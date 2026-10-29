"""Explicit response contracts; analytics do not leak through untyped route dictionaries."""
from pydantic import BaseModel
from typing import Literal


class SourceStatus(BaseModel):
    mode: Literal["live", "cached", "snapshot"]
    storage: Literal["persistent", "ephemeral"]
    last_attempt_at: str | None
    last_success_at: str | None
    next_refresh_at: str | None
    error: str | None
    ttl_seconds: int = 3600
    cooldown_seconds: int = 60


class Metrics(BaseModel):
    sample_count: int
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None
    p90_absolute_error_mw: float | None


class Calibration(BaseModel):
    available: bool
    sample_count: int
    day_count: int
    date_from: str | None
    date_to: str | None
    p10_error_mw: float | None
    p50_error_mw: float | None
    p90_error_mw: float | None
    metrics: Metrics
    method: str
    reason: str | None


class Provenance(BaseModel):
    source_actuals: str = "Elexon FUELHH • WIND"
    source_forecasts: str = "Elexon WINDFOR"
    population: str = "GB operationally metered wind"
    timezone: str = "Europe/London"
    units: str = "MW; native hourly forecast timestamps matched to half-hour actuals at the same start time"
    error_convention: str = "actual − forecast"
    selection: str = "Latest source publishTime <= decision cutoff"
    uncertainty: str = "Historical empirical P10–P90 error range; no guaranteed future coverage"
    decision_scope: str = "1–31 January 2026; delivery 2 January–1 February"
    source_retrieved_at: str | None = None
    source_hash: str | None = None
    source_mode: Literal["live", "cached", "snapshot"] = "snapshot"


class Point(BaseModel):
    delivery_time: str
    publish_time: str | None
    forecast_mw: float | None
    actual_mw: float | None = None
    error_mw: float | None = None
    lower_mw: float | None = None
    median_mw: float | None = None
    upper_mw: float | None = None
    downside_mw: float | None = None
    latest_revision_mw: float | None = None
    change_from_first_mw: float | None = None


class Overview(BaseModel):
    delivery_date: str
    decision_date: str
    decision_cutoff: str
    latest_eligible_publication: str | None
    expected_wind_mw: float | None
    latest_revision_mw: float | None
    change_from_first_mw: float | None
    mean_downside_mw: float | None
    stability: str
    stability_method: str
    calibration: Calibration
    curve: list[Point]
    expected_native_periods: int
    eligible_native_periods: int
    high_risk_periods: list[Point]
    highest_exposure_tie_count: int
    brief: str
    reveal: bool
    provenance: Provenance


class Version(BaseModel):
    publish_time: str
    forecast_mw: float
    eligible: bool
    selected: bool


class Evolution(BaseModel):
    delivery_time: str
    decision_cutoff: str
    forecast_mw: float | None
    selected_publish_time: str | None
    actual_mw: float | None
    error_mw: float | None
    inside_range: bool | None
    lower_mw: float | None
    upper_mw: float | None
    versions: list[Version]
    calibration: Calibration
    reveal: bool
    provenance: Provenance


class Bucket(BaseModel):
    label: str
    metrics: Metrics


class HistogramBin(BaseModel):
    lower_mw: float
    upper_mw: float
    count: int


class Accuracy(BaseModel):
    decision_date_from: str
    decision_date_to: str
    cutoff_local: str
    metrics: Metrics
    expected_native_periods: int
    matched_periods: int
    join_coverage: float
    curve: list[Point]
    time_of_day: list[Bucket]
    lead_time: list[Bucket]
    histogram: list[HistogramBin]
    interval_evaluation_count: int
    interval_coverage: float | None
    mean_interval_width_mw: float | None
    provenance: Provenance


class DataQuality(BaseModel):
    raw_actual_rows: int
    raw_forecast_rows: int
    stored_actual_records: int
    stored_forecast_records: int
    expected_half_hours: int
    actual_half_hours: int
    missing_actual_periods: list[str]
    expected_native_forecasts: int
    eligible_native_forecasts: int
    actuals_without_native_forecast: int
    native_forecasts_without_actuals: int
    half_hours_without_native_forecast: int
    join_coverage: float
    duplicate_source_rows: int | None
    rejected_source_rows: int | None
    earliest_delivery: str | None
    latest_delivery: str | None
    last_ingestion_time: str | None
    source_retrieved_at: str | None
    cutoff_local: str
    provenance: Provenance
