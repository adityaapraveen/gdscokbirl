import { useState } from "react";
import {
  ArrowUpRight,
  ChartNoAxesCombined,
  Database,
  Eye,
  EyeOff,
  History,
  LayoutDashboard,
  Wind,
  Calculator,
} from "lucide-react";
import {
  AccuracyPage,
  EvolutionPage,
  OverviewPage,
  QualityPage,
} from "./pages";
import { dayLabel } from "./common";
import type { SourceStatus } from "./api";
import { SourceStatusBar, sourceLabel } from "./source";
import { ProcurementPage } from "./procurement";

const navigation = [
  {
    id: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    title: "Wind outlook",
    description:
      "National wind forecasts available at your selected decision cutoff.",
  },
  {
    id: "accuracy",
    label: "Accuracy",
    icon: ChartNoAxesCombined,
    title: "Forecast accuracy",
    description:
      "Evaluate the forecasts available when the procurement decision was made.",
  },
  {
    id: "evolution",
    label: "Forecast Evolution",
    icon: History,
    title: "Forecast history",
    description: "Published forecast versions for a single delivery hour.",
  },
  {
    id: "quality",
    label: "Data Quality",
    icon: Database,
    title: "Data coverage",
    description:
      "Inspect coverage, timestamps and the records behind every calculation.",
  },
  {
    id: "procurement",
    label: "Procurement",
    icon: Calculator,
    title: "Procurement estimate",
    description:
      "Combine your demand and supply positions with four renewable generation estimates.",
  },
] as const;
type Page = (typeof navigation)[number]["id"];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [source, setSource] = useState<SourceStatus | null>(null);
  const [delivery, setDelivery] = useState("2026-01-22");
  const [cutoff, setCutoff] = useState("09:20");
  const [hour, setHour] = useState("18");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const replayKey = `${delivery}|${cutoff}|${hour}`;
  const reveal = revealedKey === replayKey;
  const current = navigation.find((item) => item.id === page)!;
  const decisionPage = page === "overview" || page === "evolution";
  function navigate(next: Page) {
    setPage(next);
    window.scrollTo(0, 0);
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to analysis
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            navigate("overview");
          }}
        >
          <span className="brand-symbol">
            <Wind size={25} />
          </span>
          <span>
            WindLens<small>Wind analysis</small>
          </span>
        </a>

        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              onClick={() => navigate(item.id)}
              className={`${page === item.id ? "active" : ""} ${item.id === "procurement" ? "nav-extension" : ""}`}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={18} />
              <span className="nav-label">
                {item.label}
                {item.id === "procurement" && <small>Extension</small>}
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <strong>January 2026 study</strong>
          <p>
            Purchase decisions
            <br />
            1–31 January · Great Britain
          </p>
        </div>
        <div className="sidebar-bottom">
          <div className="source-dot" />
          <div>
            {page === "procurement"
              ? "Elexon renewable sources"
              : sourceLabel(source?.mode)}
            <small>
              {page === "procurement"
                ? "Wind · solar · hydro · biomass"
                : "GB operationally metered wind"}
            </small>
          </div>
          <a
            href="https://github.com/adityaapraveen/gdscokbirl"
            target="_blank"
            rel="noreferrer"
            aria-label="Open source repository"
          >
            <ArrowUpRight size={15} />
          </a>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            Analysis <span>/</span>
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-meta">
            <span className="pilot-dot" />
            {page === "procurement"
              ? "Procurement scenarios"
              : "Historical analysis"}
            <span className="topbar-separator" />
            {page === "procurement" ? "JAN 2026 + DAY-AHEAD" : "JAN 2026"}
          </div>
        </header>
        <main id="main-content">
          <div className="page-intro">
            <div>
              <h1>{current.title}</h1>
              <p>{current.description}</p>
            </div>
          </div>
          {page !== "procurement" && (
            <section className="controls" aria-label="Analysis controls">
              <div className="control-fields">
                {decisionPage && (
                  <label className="field">
                    <span>Delivery date</span>
                    <input
                      type="date"
                      min="2026-01-02"
                      max="2026-02-01"
                      value={delivery}
                      onInput={(event) => {
                        if (event.currentTarget.value) {
                          setDelivery(event.currentTarget.value);
                          setRevealedKey(null);
                        }
                      }}
                    />
                  </label>
                )}
                <label className="field">
                  <span title="Publications after this London time on D-1 are excluded.">
                    Decision cutoff · D-1
                  </span>
                  <input
                    type="time"
                    value={cutoff}
                    onInput={(event) => {
                      if (event.currentTarget.value) {
                        setCutoff(event.currentTarget.value);
                        setRevealedKey(null);
                      }
                    }}
                  />
                </label>
                <div className="control-context">
                  <span>
                    {decisionPage ? "Delivery day" : "Decision dates"}
                  </span>
                  <strong>
                    {decisionPage ? dayLabel(delivery) : "1 – 31 January 2026"}
                  </strong>
                  <small>Europe/London · GMT</small>
                </div>
              </div>
              {decisionPage ? (
                <button
                  className={`button reveal-button ${reveal ? "revealed" : ""}`}
                  onClick={() => setRevealedKey(reveal ? null : replayKey)}
                >
                  {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                  <span>
                    {reveal ? "Reset replay" : "Reveal actual outcome"}
                  </span>
                </button>
              ) : (
                <span className="small-badge">
                  {cutoff} D-1 · NATIVE TIMESTAMPS
                </span>
              )}
            </section>
          )}
          {page !== "procurement" && <SourceStatusBar onStatus={setSource} />}
          {page === "procurement" && <ProcurementPage />}
          {page === "overview" && (
            <OverviewPage
              delivery={delivery}
              cutoff={cutoff}
              reveal={reveal}
              onEvolution={(value) => {
                setHour(value);
                setRevealedKey(null);
                navigate("evolution");
              }}
            />
          )}
          {page === "evolution" && (
            <EvolutionPage
              delivery={delivery}
              cutoff={cutoff}
              reveal={reveal}
              hour={hour}
              setHour={(value) => {
                setHour(value);
                setRevealedKey(null);
              }}
            />
          )}
          {page === "accuracy" && <AccuracyPage cutoff={cutoff} />}
          {page === "quality" && <QualityPage cutoff={cutoff} />}
          <footer className="footer">
            <span>
              WindLens <i /> Day-Ahead Wind Procurement Intelligence
            </span>
            <span>MW measures power. MWh measures energy.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
