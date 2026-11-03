import { useEffect, useState } from "react";
import { Calculator, Download, RefreshCw, Info, ChevronDown } from "lucide-react";
import { requestApi, useApi } from "./api";
import { ApiState, Metric, Panel, number, clock } from "./common";

const sources = ["wind", "solar", "hydro", "biomass"] as const;
type Source = (typeof sources)[number];
type Estimate = {
  national_mw: number | null;
  baseline_mw: number | null;
  method: string;
  model: string;
};
type Status = {
  mode: string;
  storage: string;
  last_success_at: string | null;
  last_attempt_at: string | null;
  error: string | null;
};
type Forecast = {
  delivery_date: string;
  decision_cutoff: string;
  trained_through: string;
  model_id: string;
  periods: { start: string; sources: Record<Source, Estimate> }[];
  status: Status;
  warnings: string[];
};
type Position = {
  start: string;
  demand_mwh: number;
  contracted_mwh: number;
  purchased_mwh: number;
};
type ResultPeriod = Position & {
  renewable_mwh: number;
  procurement_mwh: number;
  surplus_mwh: number;
  low_generation_procurement_mwh: number | null;
};
type Result = {
  forecast: Forecast;
  periods: ResultPeriod[];
  total_demand_mwh: number;
  total_firm_supply_mwh: number;
  total_renewable_mwh: number;
  additional_procurement_mwh: number;
  surplus_mwh: number;
  low_generation_procurement_mwh: number | null;
  calibration_days: number;
  scenario_method: string;
  warnings: string[];
};
type Scores = {
  baseline: { mae_mw: number };
  selected: { mae_mw: number };
  lightgbm: { mae_mw: number };
};
type Report = {
  model_id: string;
  report: {
    splits: Record<string, string>;
    candidates_per_source: number;
    sources: Record<
      Source,
      {
        selected: string;
        parameters: { training_start?: string; training_samples?: number };
        counts: Record<string, number>;
        evaluation: { january_benchmark?: Scores; test: Scores };
      }
    >;
  };
};

function tomorrow() {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const day = new Date(`${today}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

export function ProcurementPage() {
  const [future, setFuture] = useState(false);
  const [historical, setHistorical] = useState("2026-01-22");
  const [cutoff, setCutoff] = useState("09:20");
  const [method, setMethod] = useState("selected");
  const day = future ? tomorrow() : historical;
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [entryMode, setEntryMode] = useState<"hourly" | "daily">("daily");
  const [profile, setProfile] = useState({
    demand_mwh: 0,
    contracted_mwh: 0,
    purchased_mwh: 0,
  });
  const [allocations, setAllocations] = useState<Record<Source, number>>({
    wind: 0,
    solar: 0,
    hydro: 0,
    biomass: 0,
  });
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourcePending, setSourcePending] = useState(true);
  const [calculating, setCalculating] = useState(false);
  const [reload, setReload] = useState(0);
  const [tick, setTick] = useState(Date.now());
  const report = useApi<Report>("/api/procurement/models");
  const query = `delivery_date=${day}&cutoff=${cutoff}&method=${method}`;
  const attempt = forecast?.status.last_attempt_at
    ? new Date(forecast.status.last_attempt_at).getTime()
    : 0;
  const cooldown = Math.max(0, Math.ceil((attempt + 60000 - tick) / 1000));

  useEffect(() => {
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    setForecast(null);
    setResult(null);
    setError(null);
    setSourceError(null);
    setSourcePending(true);
    const load = async () => {
      try {
        const cached = await requestApi<Forecast>(
          `/api/procurement/forecast?${query}`,
        );
        if (!active) return;
        setForecast(cached);
        setPositions(
          cached.periods.map((point) => ({
            start: point.start,
            demand_mwh: profile.demand_mwh / (entryMode === "daily" ? 24 : 1),
            contracted_mwh:
              profile.contracted_mwh / (entryMode === "daily" ? 24 : 1),
            purchased_mwh:
              profile.purchased_mwh / (entryMode === "daily" ? 24 : 1),
          })),
        );
        try {
          const status = await requestApi<Status>(
            `/api/procurement/refresh?delivery_date=${day}`,
            { method: "POST" },
          );
          if (!active) return;
          setSourceError(status.error);
          const refreshed = await requestApi<Forecast>(
            `/api/procurement/forecast?${query}`,
          );
          if (active) setForecast(refreshed);
        } catch (e) {
          if (active)
            setSourceError(
              e instanceof Error ? e.message : "Source refresh failed",
            );
        }
      } catch (e) {
        if (active)
          setError(e instanceof Error ? e.message : "Forecast unavailable");
      } finally {
        if (active) setSourcePending(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
    // A new date/cutoff resets the hourly profile; editing inputs never starts network acquisition.
  }, [query, reload]);

  async function refresh() {
    setSourcePending(true);
    setResult(null);
    setSourceError(null);
    try {
      const status = await requestApi<Status>(
        `/api/procurement/refresh?delivery_date=${day}&force=true`,
        { method: "POST" },
      );
      setSourceError(status.error);
      setForecast(
        await requestApi<Forecast>(`/api/procurement/forecast?${query}`),
      );
    } catch (e) {
      setSourceError(e instanceof Error ? e.message : "Source refresh failed");
    } finally {
      setSourcePending(false);
    }
  }

  async function calculate(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setCalculating(true);
    try {
      const response = await requestApi<Result>("/api/procurement/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          delivery_date: day,
          cutoff,
          method,
          allocations_pct: allocations,
          periods: positions,
          disjoint_supply_confirmed: confirmed,
        }),
      });
      setResult(response);
      setForecast(response.forecast);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to calculate");
    } finally {
      setCalculating(false);
    }
  }

  function updateProfile(next: typeof profile) {
    setProfile(next);
    const divisor = entryMode === "daily" ? 24 : 1;
    setPositions((rows) =>
      rows.map((row) => ({
        start: row.start,
        demand_mwh: next.demand_mwh / divisor,
        contracted_mwh: next.contracted_mwh / divisor,
        purchased_mwh: next.purchased_mwh / divisor,
      })),
    );
    setResult(null);
  }

  function changeEntryMode(mode: "daily" | "hourly") {
    // Preserve the delivery-day energy balance when changing units.
    const factor = mode === entryMode ? 1 : mode === "daily" ? 24 : 1 / 24;
    setProfile((previous) => ({
      demand_mwh: previous.demand_mwh * factor,
      contracted_mwh: previous.contracted_mwh * factor,
      purchased_mwh: previous.purchased_mwh * factor,
    }));
    setEntryMode(mode);
    setResult(null);
  }

  function download() {
    if (!result) return;
    const rows = [
      [
        "start_utc",
        "demand_mwh",
        "contracted_mwh",
        "existing_purchases_mwh",
        "renewable_mwh",
        "additional_procurement_mwh",
        "surplus_mwh",
      ],
      ...result.periods.map((p) => [
        p.start,
        p.demand_mwh,
        p.contracted_mwh,
        p.purchased_mwh,
        p.renewable_mwh,
        p.procurement_mwh,
        p.surplus_mwh,
      ]),
    ];
    const url = URL.createObjectURL(
      new Blob([rows.map((row) => row.join(",")).join("\n")], {
        type: "text/csv",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `procurement-${day}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="procurement">
      <section
        className="controls"
        aria-label="Procurement delivery and model settings"
      >
        <label className="field">
          <span>Delivery mode</span>
          <select
            value={future ? "future" : "historical"}
            onChange={(e) => setFuture(e.target.value === "future")}
          >
            <option value="historical">January 2026 replay</option>
            <option value="future">Future · tomorrow</option>
          </select>
        </label>
        <label className="field">
          <span>Delivery date</span>
          <input
            type="date"
            value={day}
            min={future ? day : "2026-01-02"}
            max={future ? day : "2026-02-01"}
            onChange={(e) => {
              if (e.target.value && !future) setHistorical(e.target.value);
            }}
          />
        </label>
        <label className="field">
          <span>D-1 cutoff · London</span>
          <select value={cutoff} onChange={(e) => setCutoff(e.target.value)}>
            <option>09:20</option>
            <option>17:00</option>
          </select>
        </label>
        <label className="field">
          <span>Generation method</span>
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="selected">Validated selection</option>
            <option value="baseline">Baseline comparison</option>
          </select>
        </label>
      </section>
      {forecast && (
        <section className="source-status" aria-label="Renewable data status">
          <div className="source-status-copy">
            <strong>
              {sourcePending
                ? "Checking four Elexon datasets…"
                : `Renewable data · ${forecast.status.mode}`}
            </strong>
            <p>
              Last successful check:{" "}
              {forecast.status.last_success_at
                ? new Date(forecast.status.last_success_at).toLocaleString()
                : "using the audited snapshot"}
              .{" "}
              {forecast.status.storage === "ephemeral"
                ? "Server cache resets with the instance."
                : "Public source records persist in SQLite."}
            </p>
            <p>
              Wind · WINDFOR/FUELHH; solar · DGWS/AGWS; hydro · NPSHYD; biomass
              · BIOMASS.
            </p>
            {sourceError && (
              <p role="alert" className="source-status-error">
                {sourceError}
              </p>
            )}
          </div>
          <button
            type="button"
            className="button"
            disabled={sourcePending || cooldown > 0}
            onClick={() => void refresh()}
          >
            <RefreshCw size={14} />
            {sourcePending
              ? "Refreshing…"
              : cooldown
                ? `Refresh in ${cooldown}s`
                : "Refresh Elexon"}
          </button>
        </section>
      )}
      {!forecast && !error && (
        <ApiState error={null} retry={() => setReload((x) => x + 1)} />
      )}
      {error && (
        <div className="error-state" role="alert">
          <p>{error}</p>
          {!forecast && (
            <button className="button" onClick={() => setReload((x) => x + 1)}>
              Retry
            </button>
          )}
        </div>
      )}
      {forecast && (
        <>
          {forecast.warnings.map((w) => (
            <div className="context-banner" key={w}>
              <Info size={16} />
              <span>{w}</span>
            </div>
          ))}
          <form
            onSubmit={(event) => void calculate(event)}
            onInvalid={(event) => {
              const disclosure = (event.target as HTMLElement).closest(
                "details",
              );
              if (disclosure) disclosure.open = true;
            }}
          >
            <div className="two-columns">
              <Panel
                title="Your energy positions"
                subtitle="Enter your expected demand and supply for the selected delivery date"
              >
                <label className="field">
                  <span>Input quantities</span>
                  <select
                    value={entryMode}
                    onChange={(e) =>
                      changeEntryMode(e.target.value as "daily" | "hourly")
                    }
                  >
                    <option value="daily">
                      Daily totals · MWh for the whole day
                    </option>
                    <option value="hourly">
                      Hourly quantities · MWh in every hour
                    </option>
                  </select>
                </label>
                <div className="procurement-fields">
                  {(
                    ["demand_mwh", "contracted_mwh", "purchased_mwh"] as const
                  ).map((name, i) => (
                    <label className="field" key={name}>
                      <span>
                        {
                          [
                            "Expected electricity demand",
                            "Already contracted supply",
                            "Electricity already purchased",
                          ][i]
                        }{" "}
                        · {entryMode === "daily" ? "MWh/day" : "MWh/hour"}
                      </span>
                      <input
                        type="number"
                        min="0"
                        max="1000000000"
                        step="any"
                        required
                        value={
                          Number.isFinite(profile[name]) ? profile[name] : ""
                        }
                        onChange={(e) => {
                          updateProfile({
                            ...profile,
                            [name]: e.target.valueAsNumber,
                          });
                          setResult(null);
                        }}
                      />
                    </label>
                  ))}
                </div>
                <details className="input-help">
                  <summary>How to enter your supply positions</summary>
                  <p className="panel-note">
                    Demand is how much electricity you expect to use, not how
                    much you still need to buy. Contracted supply is energy
                    already committed under your contracts. Already purchased
                    includes all additional trades for this same date; enter
                    them only once.
                  </p>
                  <p className="panel-note">
                    {entryMode === "daily"
                      ? "Daily totals are spread evenly across 24 hours. Adjust the hourly profile below if your demand or deliveries vary."
                      : "These quantities are applied to every hour. Adjust individual hours below if needed."}{" "}
                    Changing a bulk input replaces the hourly profile. Enter 0
                    where you have no contracted supply or purchases.
                  </p>
                  <p className="panel-note">
                    Remaining to buy = demand − contracted supply − already
                    purchased − allocated renewables, with a minimum of zero per
                    hour. Your demand is an input; the model estimates renewable
                    supply.
                  </p>
                </details>
              </Panel>
              <Panel
                title="Your renewable allocation"
                subtitle="Percentage of each national generation series · default 0%, never assume ownership"
              >
                <div className="procurement-fields allocations">
                  {sources.map((source) => (
                    <label className="field" key={source}>
                      <span>
                        {source[0].toUpperCase() + source.slice(1)} share · %
                      </span>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="any"
                        required
                        value={
                          Number.isFinite(allocations[source])
                            ? allocations[source]
                            : ""
                        }
                        onChange={(e) => {
                          setAllocations({
                            ...allocations,
                            [source]: e.target.valueAsNumber,
                          });
                          setResult(null);
                        }}
                      />
                      <small>
                        {forecast.periods[12]?.sources[source].model ===
                        "lightgbm"
                          ? "LightGBM correction"
                          : "Baseline"}
                      </small>
                    </label>
                  ))}
                </div>
                <p className="panel-note">
                  For example, 0.1% allocates one thousandth of national output.
                  Use only a documented exposure; nameplate MW is not a
                  percentage. These national allocations are scenarios, not
                  individual asset forecasts.
                </p>
              </Panel>
            </div>
            <details className="profile-details">
              <summary>
                Edit hourly delivery profile{" "}
                <span>24 hours · MWh per hour</span>
              </summary>
              <Panel
                title="Hourly delivery profile"
                subtitle="Editable MWh for each delivery hour · these values are used in the calculation"
              >
                <div className="table-wrap">
                  <table className="position-table">
                    <thead>
                      <tr>
                        <th>Start · London</th>
                        <th>Demand</th>
                        <th>Firm contracts</th>
                        <th>Existing purchases</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((position, index) => (
                        <tr key={position.start}>
                          <td>{clock(position.start)}</td>
                          {(
                            [
                              "demand_mwh",
                              "contracted_mwh",
                              "purchased_mwh",
                            ] as const
                          ).map((field, i) => (
                            <td key={field}>
                              <input
                                aria-label={`${["Demand", "Contracted supply", "Existing purchases"][i]} ${clock(position.start)}`}
                                type="number"
                                min="0"
                                max="1000000000"
                                step="any"
                                required
                                value={
                                  Number.isFinite(position[field])
                                    ? position[field]
                                    : ""
                                }
                                onChange={(e) => {
                                  setPositions(
                                    positions.map((row, j) =>
                                      j === index
                                        ? {
                                            ...row,
                                            [field]: e.target.valueAsNumber,
                                          }
                                        : row,
                                    ),
                                  );
                                  setResult(null);
                                }}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </details>
            <div className="procurement-submit">
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  required
                  onChange={(e) => {
                    setConfirmed(e.target.checked);
                    setResult(null);
                  }}
                />{" "}
                My firm contracts, existing purchases and renewable allocations
                are separate and do not overlap.
              </label>
              <button
                className="button reveal-button"
                disabled={sourcePending || calculating || !confirmed}
              >
                <Calculator size={16} />
                {calculating ? "Calculating…" : "Calculate procurement"}
              </button>
            </div>
          </form>
        </>
      )}
      {result && (
        <section aria-label="Procurement results" aria-live="polite">
          <div className="metrics four">
            <Metric
              label="Additional procurement"
              value={number(result.additional_procurement_mwh, 2)}
              unit="MWh"
              note="Sum of positive hourly requirements"
            />
            <Metric
              label="Low-generation scenario"
              value={number(result.low_generation_procurement_mwh, 2)}
              unit="MWh"
              note={`${result.calibration_days} aligned calibration days · empirical P90`}
            />
            <Metric
              label="Allocated renewables"
              value={number(result.total_renewable_mwh, 2)}
              unit="MWh"
              note="Your shares of the generation estimates"
            />
            <Metric
              label="Surplus energy"
              value={number(result.surplus_mwh, 2)}
              unit="MWh"
              note="Not netted against other delivery hours"
            />
          </div>
          <Panel
            title="What remains to buy"
            subtitle="max(0, demand − firm contracts − existing purchases − allocated renewables)"
            aside={
              <button className="button" onClick={download}>
                <Download size={14} />
                CSV
              </button>
            }
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Start · London</th>
                    <th>Demand</th>
                    <th>Firm + purchased</th>
                    <th>Renewables</th>
                    <th>To procure</th>
                    <th>Surplus</th>
                  </tr>
                </thead>
                <tbody>
                  {result.periods.map((p) => (
                    <tr key={p.start}>
                      <td>{clock(p.start)}</td>
                      <td>{number(p.demand_mwh, 2)}</td>
                      <td>{number(p.contracted_mwh + p.purchased_mwh, 2)}</td>
                      <td>{number(p.renewable_mwh, 2)}</td>
                      <td className="strong">{number(p.procurement_mwh, 2)}</td>
                      <td>{number(p.surplus_mwh, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="panel-note">
              The daily scenario uses whole historical error trajectories across
              all sources and hours. It is not the sum of hourly P90s and does
              not guarantee future coverage.
            </p>
          </Panel>
          <details className="provenance">
            <summary>Assumptions and limitations <ChevronDown size={15} /></summary>
            <ul>
              {result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </details>
        </section>
      )}
      {report.data && (
        <details className="model-details">
          <summary>
            Model validation{" "}
            <span>Compare baseline and selected forecasts</span>
          </summary>
          <Panel
            title="Model validation"
            subtitle="National generation error in MW · lower MAE is better · two cutoff settings per delivery hour"
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Selected method</th>
                    <th>January baseline / selected</th>
                    <th>February baseline / selected</th>
                    <th>Training window</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((source) => {
                    const model = report.data!.report.sources[source];
                    const jan = model.evaluation.january_benchmark;
                    const test = model.evaluation.test;
                    return (
                      <tr key={source}>
                        <td className="strong">{source}</td>
                        <td>{model.selected}</td>
                        <td>
                          {jan
                            ? `${number(jan.baseline.mae_mw, 1)} / ${number(jan.selected.mae_mw, 1)}`
                            : "See model report"}
                        </td>
                        <td>
                          {number(test.baseline.mae_mw, 1)} /{" "}
                          {number(test.selected.mae_mw, 1)}
                        </td>
                        <td>
                          {model.parameters.training_start || "2025-01-02"} →
                          2025-08-31
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="panel-note">
              Selected on October validation, with September early stopping.
              November–December calibrates scenarios; February is a separate
              confirmation test. Solar outcomes may be estimates. Future
              accuracy may differ.
            </p>
          </Panel>
        </details>
      )}
    </div>
  );
}
