"""Compose domain calculations into views; routes only validate inputs and serialize."""
from datetime import date, datetime, time, timedelta
from math import ceil, floor, isclose
from statistics import mean

from .analytics import Dataset, LONDON, decision_cutoff, delivery_grid, empirical_range, iso, metrics, quantile
from .schemas import Provenance


def provenance(dataset: Dataset) -> dict:
    return Provenance(source_retrieved_at=dataset.metadata.get("retrieved_at"),
                      source_hash=dataset.metadata.get("source_hash")).model_dump()


def average(values):
    present = [value for value in values if value is not None]
    return mean(present) if present else None


def overview(dataset: Dataset, delivery_day: date, cutoff_time: time, reveal: bool) -> dict:
    cutoff = decision_cutoff(delivery_day, cutoff_time)
    calibration = dataset.calibration(cutoff, cutoff_time)
    curve = []
    for target in delivery_grid(delivery_day):
        forecast = dataset.forecast(target, cutoff)
        actual = dataset.actual(target) if reveal else None
        bounds = empirical_range(forecast.generation_mw, calibration) if forecast else {}
        curve.append({"delivery_time": iso(target), "publish_time": iso(forecast.publish_ts) if forecast else None,
            "forecast_mw": forecast.generation_mw if forecast else None,
            "actual_mw": actual.generation_mw if actual else None,
            "error_mw": actual.generation_mw - forecast.generation_mw if actual and forecast else None,
            **bounds, **dataset.revisions(target, cutoff)})
    revision_magnitude = average(abs(point["latest_revision_mw"]) for point in curve if point["latest_revision_mw"] is not None)
    historical_revisions = []
    _, historical_days = dataset.historical_pool(cutoff, cutoff_time)
    for day_text in historical_days:
        day = date.fromisoformat(day_text)
        historical_cutoff = decision_cutoff(day, cutoff_time)
        magnitudes = [abs(revision) for target in delivery_grid(day)
                      if (revision := dataset.revisions(target, historical_cutoff)["latest_revision_mw"]) is not None]
        if magnitudes:
            historical_revisions.append(mean(magnitudes))
    stability = "Insufficient history"
    if len(historical_revisions) >= 7 and revision_magnitude is not None:
        stability = ("Stable" if revision_magnitude <= quantile(historical_revisions, 1/3)
                     else "Typical" if revision_magnitude <= quantile(historical_revisions, 2/3) else "Volatile")
    ranked = sorted([point for point in curve if point.get("downside_mw") is not None],
                    key=lambda point: point["downside_mw"], reverse=True)
    ties = sum(isclose(point["downside_mw"], ranked[0]["downside_mw"], abs_tol=1e-6) for point in ranked) if ranked else 0
    expected = average(point["forecast_mw"] for point in curve)
    revision = average(point["latest_revision_mw"] for point in curve)
    brief = "No forecast was available before the selected decision cutoff."
    if expected is not None:
        brief = f"Mean native wind forecast is {expected / 1000:.2f} GW. "
        if revision is not None:
            brief += f"The latest eligible revision {'reduced' if revision < 0 else 'increased'} the mean outlook by {abs(revision):,.0f} MW. "
        brief += (f"The empirical range uses {calibration['sample_count']} prior observations across {calibration['day_count']} complete days."
                  if calibration["available"] else "There is insufficient prior history to estimate an empirical range.")
    publications = [point["publish_time"] for point in curve if point["publish_time"]]
    return {"delivery_date": delivery_day.isoformat(), "decision_date": (delivery_day - timedelta(days=1)).isoformat(),
        "decision_cutoff": cutoff.isoformat(), "latest_eligible_publication": max(publications, key=datetime.fromisoformat) if publications else None,
        "expected_wind_mw": expected, "latest_revision_mw": revision,
        "change_from_first_mw": average(point["change_from_first_mw"] for point in curve),
        "mean_downside_mw": average(point.get("downside_mw") for point in curve),
        "stability": stability,
        "stability_method": "Mean absolute latest revision compared with prior complete-day 33rd/67th percentiles; minimum seven days. Descriptive, not accuracy confidence.",
        "calibration": calibration, "curve": curve, "expected_native_periods": len(curve),
        "eligible_native_periods": sum(point["forecast_mw"] is not None for point in curve),
        "high_risk_periods": ranked[:5], "highest_exposure_tie_count": ties,
        "brief": brief, "reveal": reveal, "provenance": provenance(dataset)}


def evolution(dataset: Dataset, delivery: datetime, cutoff_time: time, reveal: bool) -> dict:
    target = int(delivery.timestamp())
    cutoff = decision_cutoff(delivery.astimezone(LONDON).date(), cutoff_time)
    forecast = dataset.forecast(target, cutoff)
    actual = dataset.actual(target) if reveal else None
    calibration = dataset.calibration(cutoff, cutoff_time)
    bounds = empirical_range(forecast.generation_mw, calibration) if forecast else {"lower_mw": None, "upper_mw": None}
    lower, upper = bounds["lower_mw"], bounds["upper_mw"]
    versions = [{"publish_time": iso(row.publish_ts), "forecast_mw": row.generation_mw,
                 "eligible": row.publish_ts <= cutoff.timestamp(),
                 "selected": forecast is not None and row.publish_ts == forecast.publish_ts}
                for row in dataset.forecasts.get(target, []) if reveal or row.publish_ts <= cutoff.timestamp()]
    return {"delivery_time": iso(target), "decision_cutoff": cutoff.isoformat(),
        "forecast_mw": forecast.generation_mw if forecast else None,
        "selected_publish_time": iso(forecast.publish_ts) if forecast else None,
        "actual_mw": actual.generation_mw if actual else None,
        "error_mw": actual.generation_mw - forecast.generation_mw if actual and forecast else None,
        "inside_range": lower <= actual.generation_mw <= upper if actual and lower is not None else None,
        "lower_mw": lower, "upper_mw": upper, "versions": versions,
        "calibration": calibration, "reveal": reveal, "provenance": provenance(dataset)}


def accuracy(dataset: Dataset, start: date, end: date, cutoff_time: time) -> dict:
    selected = [row for row in dataset.evaluations(cutoff_time) if start <= row["day"] - timedelta(days=1) <= end]
    matched = [row for row in selected if row["error_mw"] is not None]
    errors = [row["error_mw"] for row in matched]
    curve = []
    interval_outcomes = []
    for row in matched:
        forecast, actual = row["forecast"], row["actual"]
        calibration = dataset.calibration(row["cutoff"], cutoff_time)
        bounds = empirical_range(forecast.generation_mw, calibration)
        curve.append({"delivery_time": iso(row["delivery_ts"]), "publish_time": iso(forecast.publish_ts),
            "forecast_mw": forecast.generation_mw, "actual_mw": actual.generation_mw,
            "error_mw": row["error_mw"], **bounds})
        if bounds["lower_mw"] is not None:
            interval_outcomes.append((bounds["lower_mw"] <= actual.generation_mw <= bounds["upper_mw"],
                                      bounds["upper_mw"] - bounds["lower_mw"]))
    tod = []
    for hour in range(0, 24, 4):
        bucket = [row["error_mw"] for row in matched
                  if hour <= datetime.fromtimestamp(row["delivery_ts"], LONDON).hour < hour + 4]
        tod.append({"label": f"{hour:02d}–{hour+4:02d}", "metrics": metrics(bucket)})
    leads = []
    for lower, upper in [(0, 6), (6, 12), (12, 18), (18, 24), (24, 36), (36, 48), (48, 1000)]:
        bucket = [row["error_mw"] for row in matched
                  if lower <= (row["delivery_ts"] - row["forecast"].publish_ts) / 3600 < upper]
        leads.append({"label": f"{lower}–{upper}h" if upper != 1000 else "48h+", "metrics": metrics(bucket)})
    histogram = []
    if errors:
        width = max(100, ceil((max(errors) - min(errors)) / 20 / 100) * 100)
        lower = floor(min(errors) / width) * width
        while lower <= max(errors):
            histogram.append({"lower_mw": lower, "upper_mw": lower + width,
                              "count": sum(lower <= error < lower + width for error in errors)})
            lower += width
    return {"decision_date_from": start.isoformat(), "decision_date_to": end.isoformat(),
        "cutoff_local": cutoff_time.isoformat(timespec="minutes"), "metrics": metrics(errors),
        "expected_native_periods": len(selected), "matched_periods": len(matched),
        "join_coverage": len(matched) / len(selected) if selected else 0,
        "curve": curve, "time_of_day": tod, "lead_time": leads, "histogram": histogram,
        "interval_evaluation_count": len(interval_outcomes),
        "interval_coverage": average(inside for inside, _ in interval_outcomes),
        "mean_interval_width_mw": average(width for _, width in interval_outcomes),
        "provenance": provenance(dataset)}


def data_quality(dataset: Dataset, cutoff_time: time) -> dict:
    expected = []
    day = date(2026, 1, 2)
    while day <= date(2026, 2, 1):
        expected.extend(delivery_grid(day, 30))
        day += timedelta(days=1)
    expected_set = set(expected)
    actual_set = set(dataset.actuals) & expected_set
    native = list(dataset.evaluations(cutoff_time))
    joined = sum(row["forecast"] is not None and row["actual"] is not None for row in native)
    return {"raw_actual_rows": dataset.metadata.get("raw_actual_rows", sum(map(len, dataset.actuals.values()))),
        "raw_forecast_rows": dataset.metadata.get("raw_forecast_rows", sum(map(len, dataset.forecasts.values()))),
        "stored_actual_records": sum(map(len, dataset.actuals.values())),
        "stored_forecast_records": sum(map(len, dataset.forecasts.values())),
        "expected_half_hours": len(expected), "actual_half_hours": len(actual_set),
        "missing_actual_periods": [iso(target) for target in expected if target not in actual_set],
        "expected_native_forecasts": len(native),
        "eligible_native_forecasts": sum(row["forecast"] is not None for row in native),
        "actuals_without_native_forecast": sum(row["actual"] is not None and row["forecast"] is None for row in native),
        "native_forecasts_without_actuals": sum(row["forecast"] is not None and row["actual"] is None for row in native),
        "half_hours_without_native_forecast": sum(target not in dataset.forecasts for target in expected),
        "join_coverage": joined / len(native) if native else 0,
        "duplicate_source_rows": dataset.metadata.get("duplicate_source_rows"),
        "rejected_source_rows": dataset.metadata.get("rejected_rows"),
        "earliest_delivery": iso(min(actual_set)) if actual_set else None,
        "latest_delivery": iso(max(actual_set)) if actual_set else None,
        "last_ingestion_time": dataset.metadata.get("ingested_at"),
        "source_retrieved_at": dataset.metadata.get("retrieved_at"),
        "cutoff_local": cutoff_time.isoformat(timespec="minutes"), "provenance": provenance(dataset)}
