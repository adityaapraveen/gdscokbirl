import { useEffect, useState } from "react";

export type Metrics = {
  sample_count: number;
  mae_mw: number | null;
  rmse_mw: number | null;
  bias_mw: number | null;
  p90_absolute_error_mw: number | null;
};
export type Calibration = {
  available: boolean;
  sample_count: number;
  day_count: number;
  date_from: string | null;
  date_to: string | null;
  p10_error_mw: number | null;
  p50_error_mw: number | null;
  p90_error_mw: number | null;
  metrics: Metrics;
  method: string;
  reason: string | null;
};
export type Provenance = {
  source_mode?: "live" | "cached" | "snapshot";
  source_actuals: string;
  source_forecasts: string;
  population: string;
  timezone: string;
  units: string;
  error_convention: string;
  selection: string;
  uncertainty: string;
  decision_scope: string;
  source_retrieved_at: string | null;
  source_hash: string | null;
};
export type Point = {
  delivery_time: string;
  publish_time: string | null;
  forecast_mw: number | null;
  actual_mw: number | null;
  error_mw: number | null;
  lower_mw: number | null;
  median_mw: number | null;
  upper_mw: number | null;
  downside_mw: number | null;
  latest_revision_mw: number | null;
  change_from_first_mw: number | null;
};
export type OverviewData = {
  delivery_date: string;
  decision_date: string;
  decision_cutoff: string;
  latest_eligible_publication: string | null;
  expected_wind_mw: number | null;
  latest_revision_mw: number | null;
  change_from_first_mw: number | null;
  mean_downside_mw: number | null;
  stability: string;
  stability_method: string;
  calibration: Calibration;
  curve: Point[];
  expected_native_periods: number;
  eligible_native_periods: number;
  high_risk_periods: Point[];
  highest_exposure_tie_count: number;
  brief: string;
  reveal: boolean;
  provenance: Provenance;
};
export type EvolutionData = {
  delivery_time: string;
  decision_cutoff: string;
  forecast_mw: number | null;
  selected_publish_time: string | null;
  actual_mw: number | null;
  error_mw: number | null;
  inside_range: boolean | null;
  lower_mw: number | null;
  upper_mw: number | null;
  versions: {
    publish_time: string;
    forecast_mw: number;
    eligible: boolean;
    selected: boolean;
  }[];
  calibration: Calibration;
  reveal: boolean;
  provenance: Provenance;
};
export type AccuracyData = {
  decision_date_from: string;
  decision_date_to: string;
  cutoff_local: string;
  metrics: Metrics;
  expected_native_periods: number;
  matched_periods: number;
  join_coverage: number;
  curve: Point[];
  time_of_day: { label: string; metrics: Metrics }[];
  lead_time: { label: string; metrics: Metrics }[];
  histogram: { lower_mw: number; upper_mw: number; count: number }[];
  interval_evaluation_count: number;
  interval_coverage: number | null;
  mean_interval_width_mw: number | null;
  provenance: Provenance;
};
export type QualityData = {
  raw_actual_rows: number;
  raw_forecast_rows: number;
  stored_actual_records: number;
  stored_forecast_records: number;
  expected_half_hours: number;
  actual_half_hours: number;
  missing_actual_periods: string[];
  expected_native_forecasts: number;
  eligible_native_forecasts: number;
  actuals_without_native_forecast: number;
  native_forecasts_without_actuals: number;
  half_hours_without_native_forecast: number;
  join_coverage: number;
  duplicate_source_rows: number | null;
  rejected_source_rows: number | null;
  earliest_delivery: string | null;
  latest_delivery: string | null;
  last_ingestion_time: string | null;
  source_retrieved_at: string | null;
  cutoff_local: string;
  provenance: Provenance;
};

export type SourceStatus = {
  mode: "live" | "cached" | "snapshot";
  storage: "persistent" | "ephemeral";
  last_attempt_at: string | null;
  last_success_at: string | null;
  next_refresh_at: string | null;
  error: string | null;
  ttl_seconds: number;
  cooldown_seconds: number;
};

export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "";
export const dataRefreshEvent = "windlens:data-refreshed";

export async function requestApi<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Unable to complete the request (HTTP ${response.status}).`,
    );
  }
  if (body == null) throw new Error("The API returned an empty response.");
  return body as T;
}

export function useApi<T>(path: string) {
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{
    path: string;
    data: T | null;
    error: string | null;
  }>({ path: "", data: null, error: null });
  useEffect(() => {
    const refresh = () => setRetry((value) => value + 1);
    window.addEventListener(dataRefreshEvent, refresh);
    return () => window.removeEventListener(dataRefreshEvent, refresh);
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    setState((previous) => ({
      path,
      data: previous.path === path ? previous.data : null,
      error: null,
    }));
    requestApi<T>(path, { signal: abort.signal })
      .then((body) => {
        if (abort.signal.aborted) return;
        setState({ path, data: body, error: null });
      })
      .catch((error) => {
        if (!abort.signal.aborted && error.name !== "AbortError")
          setState((previous) => ({
            path,
            data: previous.path === path ? previous.data : null,
            error: error.message || "Unable to reach the API.",
          }));
      });
    return () => abort.abort();
  }, [path, retry]);
  return {
    ...(state.path === path ? state : { data: null, error: null }),
    retry: () => setRetry((value) => value + 1),
  };
}
