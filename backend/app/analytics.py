"""Temporal selection and empirical analytics, independent of HTTP and storage."""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from math import sqrt
from statistics import mean
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def decision_cutoff(delivery_day: date, local_cutoff: time) -> datetime:
    if local_cutoff.tzinfo is not None:
        raise ValueError("Cutoff must be a London wall-clock time without offset")
    return datetime.combine(delivery_day - timedelta(days=1), local_cutoff, LONDON).astimezone(UTC)


def delivery_grid(day: date, minutes: int = 60) -> list[int]:
    first = datetime.combine(day, time(), LONDON).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), LONDON).astimezone(UTC)
    result = []
    while first < end:
        result.append(int(first.timestamp()))
        first += timedelta(minutes=minutes)
    return result


def quantile(values: list[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ValueError("Quantiles require samples and a probability between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    index = int(position)
    return ordered[index] + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index]) * (position - index)


def metrics(errors: list[float]) -> dict:
    return {"sample_count": len(errors),
            "mae_mw": mean(map(abs, errors)) if errors else None,
            "rmse_mw": sqrt(mean(error**2 for error in errors)) if errors else None,
            "bias_mw": mean(errors) if errors else None,
            "p90_absolute_error_mw": quantile(list(map(abs, errors)), .9) if errors else None}


@dataclass(frozen=True)
class Vintage:
    delivery_ts: int
    publish_ts: float
    generation_mw: float


def get_as_of_forecast(vintages: list[Vintage], cutoff: datetime) -> Vintage | None:
    """Latest eligible publication, including equality; never substitute a future row."""
    eligible = [vintage for vintage in vintages if vintage.publish_ts <= aware(cutoff).timestamp()]
    return max(eligible, key=lambda vintage: vintage.publish_ts) if eligible else None


class Dataset:
    """Small immutable snapshot indexed by delivery and publication time."""
    def __init__(self, actuals: list[Vintage], forecasts: list[Vintage], metadata: dict | None = None):
        self.actuals = self._index(actuals)
        self.forecasts = self._index(forecasts)
        self.metadata = metadata or {}

    @staticmethod
    def _index(rows):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row.delivery_ts].append(row)
        return {target: sorted(versions, key=lambda row: row.publish_ts) for target, versions in grouped.items()}

    def forecast(self, delivery_ts: int, cutoff: datetime) -> Vintage | None:
        versions = self.forecasts.get(delivery_ts, [])
        index = bisect_right(versions, aware(cutoff).timestamp(), key=lambda row: row.publish_ts) - 1
        return versions[index] if index >= 0 else None

    def actual(self, delivery_ts: int, cutoff: datetime | None = None) -> Vintage | None:
        versions = self.actuals.get(delivery_ts, [])
        if cutoff is None:
            return versions[-1] if versions else None
        index = bisect_right(versions, aware(cutoff).timestamp(), key=lambda row: row.publish_ts) - 1
        return versions[index] if index >= 0 else None

    @lru_cache(maxsize=128)
    def evaluations(self, local_cutoff: time) -> tuple[dict, ...]:
        evaluations = []
        day = date(2026, 1, 2)
        while day <= date(2026, 2, 1):
            cutoff = decision_cutoff(day, local_cutoff)
            for target in delivery_grid(day):
                forecast = self.forecast(target, cutoff)
                actual = self.actual(target)
                evaluations.append({"delivery_ts": target, "day": day, "cutoff": cutoff,
                    "forecast": forecast, "actual": actual,
                    "error_mw": actual.generation_mw - forecast.generation_mw if actual and forecast else None})
            day += timedelta(days=1)
        return tuple(evaluations)

    @lru_cache(maxsize=512)
    def historical_pool(self, cutoff: datetime, local_cutoff: time) -> tuple[tuple[float, ...], tuple[str, ...]]:
        """Only complete earlier delivery days with actuals visible by this cutoff."""
        cutoff = aware(cutoff)
        decision_day = cutoff.astimezone(LONDON).date()
        grouped = defaultdict(list)
        for row in self.evaluations(local_cutoff):
            if row["day"] >= decision_day or row["forecast"] is None:
                continue
            actual = self.actual(row["delivery_ts"], cutoff)
            if actual is not None:
                grouped[row["day"]].append(actual.generation_mw - row["forecast"].generation_mw)
        complete = {day: errors for day, errors in grouped.items() if len(errors) == len(delivery_grid(day))}
        return tuple(error for errors in complete.values() for error in errors), tuple(day.isoformat() for day in sorted(complete))

    def calibration(self, cutoff: datetime, local_cutoff: time) -> dict:
        errors, dates = self.historical_pool(cutoff, local_cutoff)
        enough = len(dates) >= 7 and len(errors) >= 30
        values = list(errors)
        return {"available": enough, "sample_count": len(errors), "day_count": len(dates),
            "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
            "p10_error_mw": quantile(values, .1) if enough else None,
            "p50_error_mw": quantile(values, .5) if enough else None,
            "p90_error_mw": quantile(values, .9) if enough else None,
            "metrics": metrics(values),
            "method": "Expanding pooled errors from complete earlier delivery days; actual publication <= cutoff",
            "reason": None if enough else "At least seven complete historical days and 30 matched observations are required."}

    def revisions(self, target: int, cutoff: datetime) -> dict:
        eligible = [row for row in self.forecasts.get(target, []) if row.publish_ts <= aware(cutoff).timestamp()]
        return {"latest_revision_mw": eligible[-1].generation_mw - eligible[-2].generation_mw if len(eligible) > 1 else None,
                "change_from_first_mw": eligible[-1].generation_mw - eligible[0].generation_mw if len(eligible) > 1 else None}


def empirical_range(forecast_mw: float, calibration: dict) -> dict:
    if not calibration["available"]:
        return {"lower_mw": None, "median_mw": None, "upper_mw": None, "downside_mw": None}
    lower = max(0, forecast_mw + calibration["p10_error_mw"])
    return {"lower_mw": lower, "median_mw": max(0, forecast_mw + calibration["p50_error_mw"]),
            "upper_mw": max(0, forecast_mw + calibration["p90_error_mw"]),
            "downside_mw": max(0, forecast_mw - lower)}
