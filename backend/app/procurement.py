"""Portfolio energy balance and empirical joint-error stress scenarios.

No trading, pricing or purchase execution. Private portfolio inputs are neither
persisted nor cached. Only public national forecast features are memoized.
"""
from datetime import UTC, date, datetime, time
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .analytics import decision_cutoff, delivery_grid, iso, quantile
from .forecast_model import load_models, predict
from .renewables import Archive, SOURCES
from . import renewable_sync

Quantity = Annotated[float, Field(ge=0, le=1e9, allow_inf_nan=False, strict=True)]
Share = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)]
Cutoff = Literal["09:20", "17:00"]
Method = Literal["selected", "baseline"]


class Allocations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wind: Share = 0
    solar: Share = 0
    hydro: Share = 0
    biomass: Share = 0


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    demand_mwh: Quantity
    contracted_mwh: Quantity
    purchased_mwh: Quantity


class ProcurementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_date: date
    cutoff: Cutoff = "09:20"
    method: Method = "selected"
    allocations_pct: Allocations = Field(default_factory=Allocations)
    periods: list[Position] = Field(min_length=24, max_length=24)
    disjoint_supply_confirmed: Literal[True]


class SourceEstimate(BaseModel):
    national_mw: float | None
    baseline_mw: float | None
    method: str
    model: str


class ForecastPeriod(BaseModel):
    start: str
    duration_hours: float = 1
    sources: dict[str, SourceEstimate]


class RenewableStatus(BaseModel):
    scope: str
    mode: str
    storage: str
    last_attempt_at: str | None
    last_success_at: str | None
    error: str | None
    source_hash: str | None
    snapshot_retrieved_at: str
    ttl_seconds: int
    cooldown_seconds: int


class ProcurementForecast(BaseModel):
    delivery_date: str
    decision_cutoff: str
    requested_cutoff: str
    model_id: str
    trained_through: str
    method: str
    periods: list[ForecastPeriod]
    status: RenewableStatus
    warnings: list[str]


class ResultPeriod(BaseModel):
    start: str
    demand_mwh: float
    contracted_mwh: float
    purchased_mwh: float
    renewable_by_source_mwh: dict[str, float]
    renewable_mwh: float
    procurement_mwh: float
    surplus_mwh: float
    low_generation_procurement_mwh: float | None


class ProcurementResult(BaseModel):
    forecast: ProcurementForecast
    periods: list[ResultPeriod]
    total_demand_mwh: float
    total_firm_supply_mwh: float
    total_renewable_mwh: float
    additional_procurement_mwh: float
    surplus_mwh: float
    low_generation_procurement_mwh: float | None
    calibration_days: int
    scenario_method: str
    warnings: list[str]


def effective_cutoff(day: date, slot: str) -> tuple[float, list[str]]:
    renewable_sync.validate_day(day)
    requested = decision_cutoff(day, time.fromisoformat(slot)).timestamp()
    current = renewable_sync.now().timestamp()
    warnings = []
    if requested > current:
        requested = float(int(current // 60) * 60)
        warnings.append("The scheduled cutoff has not arrived. Only publications available now are used; this earlier cutoff is outside the two validated cutoff settings.")
    return requested, warnings


@lru_cache(maxsize=128)
def _forecast(archive: Archive, day: date, cutoff: float, method: str) -> tuple[ForecastPeriod, ...]:
    points = []
    models = load_models()
    for start in delivery_grid(day):
        values = {}
        for source in SOURCES:
            featured = archive.features(source, start, cutoff)
            if featured is None:
                values[source] = SourceEstimate(national_mw=None, baseline_mw=None, method="Source unavailable", model="unavailable")
                continue
            features, baseline_method = featured
            chosen = "lightgbm" if method == "selected" and models["sources"][source]["selected"] == "lightgbm" else "baseline"
            values[source] = SourceEstimate(national_mw=predict(source, features, method, models), baseline_mw=features[0],
                                           method=baseline_method, model=chosen)
        points.append(ForecastPeriod(start=iso(start), sources=values))
    return tuple(points)


def forecast(day: date, slot: str = "09:20", method: str = "selected") -> ProcurementForecast:
    cutoff, warnings = effective_cutoff(day, slot)
    if len(delivery_grid(day)) != 24:
        raise ValueError("This pilot accepts 24-hour delivery days. DST transition days require 23/25-period portfolio support.")
    archive, status = renewable_sync.get_archive(day)
    models = load_models()
    points = list(_forecast(archive, day, cutoff, method))
    if day > date(2026, 2, 1):
        warnings.append("Future mode uses fixed historical models. January test accuracy does not establish accuracy for this future date; monitor and retrain on newer history.")
    if any(point.sources["solar"].method == "Seven available same-hour days" for point in points):
        warnings.append("A solar day-ahead publication was not available at this cutoff. Solar uses the available-history baseline, not a later Elexon forecast.")
    return ProcurementForecast(delivery_date=str(day), decision_cutoff=iso(cutoff), requested_cutoff=slot,
        model_id=models["artifact_hash"][:16], trained_through=models["trained_through"], method=method,
        periods=points, status=RenewableStatus(**status), warnings=warnings)


def _calibration(slot: str, cutoff: float, method: str) -> dict[str, dict[int, dict]]:
    days = {}
    for row in load_models()["calibration"]:
        if row["slot"] == slot and row["published"] <= cutoff:
            dt = datetime.fromtimestamp(row["start"], UTC)
            days.setdefault(str(dt.date()), {})[dt.hour] = row[method]
    return {day: hours for day, hours in days.items() if len(hours) == 24}


def calculate(request: ProcurementRequest) -> ProcurementResult:
    data = forecast(request.delivery_date, request.cutoff, request.method)
    expected_starts = [datetime.fromisoformat(point.start).timestamp() for point in data.periods]
    positions = sorted(request.periods, key=lambda row: row.start)
    if [row.start.timestamp() for row in positions] != expected_starts:
        raise ValueError("Provide exactly one position for every returned hourly start timestamp; duplicates, gaps and shifted periods are invalid.")
    shares = {name: value / 100 for name, value in request.allocations_pct.model_dump().items()}
    for source, share in shares.items():
        if share and any(point.sources[source].national_mw is None for point in data.periods):
            raise ValueError(f"{source.title()} data is unavailable. Refresh Elexon sources or set that allocation to zero; missing data is not zero output.")
    cutoff = datetime.fromisoformat(data.decision_cutoff).timestamp()
    residual_days = _calibration(request.cutoff, cutoff, request.method)
    enough = len(residual_days) >= 30
    no_variable_supply = not any(shares.values())
    hourly = []
    scenario_totals = {day: 0.0 for day in residual_days}
    for point, position in zip(data.periods, positions):
        outputs = {source: point.sources[source].national_mw or 0 for source in SOURCES}
        allocated = {source: outputs[source] * shares[source] for source in SOURCES}
        renewable = sum(allocated.values())  # MW * exactly one physical hour = MWh.
        uncovered = position.demand_mwh - position.contracted_mwh - position.purchased_mwh
        scenarios = []
        hour = datetime.fromisoformat(point.start).hour
        for day, hours in residual_days.items():
            supply = sum(max(0, outputs[source] + hours[hour][source]) * shares[source] for source in SOURCES)
            need = max(0, uncovered - supply)
            scenarios.append(need)
            scenario_totals[day] += need
        need = max(0, uncovered - renewable)
        hourly.append(ResultPeriod(start=point.start, demand_mwh=position.demand_mwh,
            contracted_mwh=position.contracted_mwh, purchased_mwh=position.purchased_mwh,
            renewable_by_source_mwh=allocated, renewable_mwh=renewable, procurement_mwh=need,
            surplus_mwh=max(0, renewable - uncovered),
            low_generation_procurement_mwh=need if no_variable_supply else quantile(scenarios, .9) if enough else None))
    warnings = [*data.warnings,
        "Renewable allocations are explicit percentages of GB national generation. This is a portfolio scenario proxy, not an individual asset/PPA forecast.",
        "Demand is your supplied forecast. The app has not trained a demand model or verified your contracts.",
        "Firm contracted supply must exclude existing purchases and the renewable allocations entered here.",
        "No electricity prices or trading constraints are provided. This calculates an energy requirement, not a cost-optimal buying schedule."]
    if not enough and not no_variable_supply:
        warnings.append("Fewer than 30 complete prior calibration days are visible; the low-generation scenario is unavailable.")
    total_need = sum(row.procurement_mwh for row in hourly)
    return ProcurementResult(forecast=data, periods=hourly, total_demand_mwh=sum(row.demand_mwh for row in hourly),
        total_firm_supply_mwh=sum(row.contracted_mwh + row.purchased_mwh for row in hourly),
        total_renewable_mwh=sum(row.renewable_mwh for row in hourly), additional_procurement_mwh=total_need,
        surplus_mwh=sum(row.surplus_mwh for row in hourly),
        low_generation_procurement_mwh=total_need if no_variable_supply else quantile(list(scenario_totals.values()), .9) if enough else None,
        calibration_days=len(residual_days), scenario_method="Empirical P90 of daily procurement across synchronized historical daily error trajectories; preserves cross-source/hour dependence. Not guaranteed coverage.",
        warnings=warnings)
