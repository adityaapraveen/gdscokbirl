"""Versioned renewable observations and decision-time features, in native MW.

FUELHH is operationally metered WIND, non-pumped hydro and biomass. AGWS solar
is actual OR estimated output. WINDFOR is an hourly sample; DGWS solar is
half-hourly. A full-hour target averages both actual half-hours.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from math import cos, sin, pi, isfinite
from statistics import mean

from .analytics import LONDON

SOURCES = ("wind", "solar", "hydro", "biomass")
FUELS = {"WIND": "wind", "NPSHYD": "hydro", "BIOMASS": "biomass"}
FEATURES = ("baseline_mw", "provider_available", "recent_same_hour", "week_same_hour",
            "same_hour_mean", "latest_actual", "hour_sin", "hour_cos", "season_sin",
            "season_cos", "weekday", "cutoff_hour")


def timestamp(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Source timestamps must include a timezone")
    return dt.timestamp()


def normalize(dataset: str, rows: list[dict]) -> list[list]:
    result = {}
    for raw in rows:
        if raw.get("dataset") != dataset:
            raise ValueError(f"Wrong dataset in {dataset} response")
        if dataset == "FUELHH":
            source = FUELS.get(raw.get("fuelType"))
            kind, value = "actual", raw.get("generation")
        elif dataset == "WINDFOR":
            source, kind, value = "wind", "forecast", raw.get("generation")
        elif dataset in ("AGWS", "DGWS"):
            source = "solar" if raw.get("psrType") == "Solar" else None
            if dataset == "DGWS" and raw.get("processType") != "Day ahead":
                continue
            kind, value = ("actual" if dataset == "AGWS" else "forecast"), raw.get("quantity")
        else:
            raise ValueError("Unsupported renewable dataset")
        if source is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError(f"Invalid generation in {dataset}; never replace missing values with zero")
        start, publish = timestamp(raw["startTime"]), timestamp(raw["publishTime"])
        if start % (3600 if dataset == "WINDFOR" else 1800):
            raise ValueError(f"Unexpected timestamp resolution in {dataset}")
        key = (source, kind, int(start), publish)
        if key in result and result[key] != value:
            raise ValueError(f"Conflicting publication key in {dataset}")
        result[key] = float(value)
    return [[*key, value] for key, value in sorted(result.items())]


@dataclass(frozen=True)
class Observation:
    publish: float
    mw: float


class Archive:
    def __init__(self, rows: list[list], metadata: dict | None = None):
        self.index: dict[tuple, list[Observation]] = {}
        for source, kind, start, publish, value in rows:
            self.index.setdefault((source, kind, int(start)), []).append(Observation(publish, value))
        for values in self.index.values():
            values.sort(key=lambda row: row.publish)
        self.metadata = metadata or {}

    def observation(self, source: str, kind: str, start: int, cutoff: float) -> Observation | None:
        values = self.index.get((source, kind, start), ())
        position = bisect_right(values, cutoff, key=lambda row: row.publish)
        return values[position - 1] if position else None

    @lru_cache(maxsize=100000)
    def actual(self, source: str, start: int, cutoff: float) -> tuple[float, float] | None:
        if start + 3600 > cutoff:
            return None
        rows = [self.observation(source, "actual", start + offset, cutoff) for offset in (0, 1800)]
        if any(row is None for row in rows):
            return None
        return mean(row.mw for row in rows), max(row.publish for row in rows)

    def provider(self, source: str, start: int, cutoff: float) -> float | None:
        offsets = (0,) if source == "wind" else (0, 1800)
        rows = [self.observation(source, "forecast", start + offset, cutoff) for offset in offsets]
        return mean(row.mw for row in rows) if all(row is not None for row in rows) else None

    @lru_cache(maxsize=4000)
    def latest_actual(self, source: str, cutoff: float) -> float | None:
        start = int(cutoff // 3600) * 3600 - 3600
        for offset in range(72):
            value = self.actual(source, start - offset * 3600, cutoff)
            if value is not None:
                return value[0]
        return None

    def features(self, source: str, start: int, cutoff: float) -> tuple[list[float | None], str] | None:
        history = []
        for days in range(1, 36):
            value = self.actual(source, start - days * 86400, cutoff)
            if value is not None:
                history.append(value[0])
                if len(history) == 7:
                    break
        provider = self.provider(source, start, cutoff) if source in ("wind", "solar") else None
        if provider is None and not history:
            return None
        baseline = provider if provider is not None else (mean(history) if source == "solar" else history[0])
        target = datetime.fromtimestamp(start, LONDON)
        decision = datetime.fromtimestamp(cutoff, LONDON)
        angle = 2 * pi * target.hour / 24
        season = 2 * pi * target.timetuple().tm_yday / 365.25
        features = [baseline, float(provider is not None), history[0] if history else None,
                    history[-1] if history else None, mean(history) if history else None,
                    self.latest_actual(source, cutoff), sin(angle), cos(angle), sin(season), cos(season),
                    float(target.weekday()), decision.hour + decision.minute / 60]
        method = ("Elexon WINDFOR hourly proxy" if source == "wind" else "Elexon DGWS day-ahead") if provider is not None else (
            "Seven available same-hour days" if source == "solar" else "Latest available same-hour output")
        return features, method
