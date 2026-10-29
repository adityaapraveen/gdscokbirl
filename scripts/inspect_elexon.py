"""Read-only Elexon audit. Python 3.11+, standard library only.

Downloads are cached locally; the compact report can be committed as evidence.
This is a research tool, not the application's ingestion or analytics service.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
import logging
from pathlib import Path
import time as clock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
LONDON = ZoneInfo("Europe/London")
LOG = logging.getLogger(__name__)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Naive source timestamp")
    return parsed.astimezone(UTC)


def days(start: date, end: date):
    while start <= end:
        yield start
        start += timedelta(days=1)


def fetch(dataset: str, params: dict, cache: Path, refresh: bool) -> tuple[list, dict]:
    url = f"{BASE_URL}/datasets/{dataset}/stream?{urlencode(params)}"
    filename = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    metadata_file = filename.with_suffix(".meta.json")
    if filename.exists() and metadata_file.exists() and not refresh:
        body = filename.read_bytes()
        metadata = json.loads(metadata_file.read_text())
        if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
            raise ValueError(f"Cache checksum mismatch for {url}; rerun with --refresh")
    else:
        for attempt in range(3):
            try:
                request = Request(url, headers={"Accept": "application/json"})
                with urlopen(request, timeout=45) as response:
                    body = response.read()
                break
            except (HTTPError, URLError, TimeoutError) as exc:
                if isinstance(exc, HTTPError) and exc.code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                clock.sleep(2**attempt)
        payload = json.loads(body)
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON array from {url}")
        metadata = {"url": url, "retrieved_at": datetime.now(UTC).isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest()}
        filename.write_bytes(body)
        metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array from {url}")
    LOG.info("%s %s: %d rows", dataset, next(iter(params.values())), len(payload))
    return payload, {**metadata, "row_count": len(payload)}


def audit(rows: list[dict], dataset: str) -> tuple[dict, list[dict]]:
    valid, rejected = [], []
    for index, row in enumerate(rows):
        try:
            if row.get("dataset") != dataset:
                raise ValueError("Unexpected dataset")
            if dataset == "FUELHH" and row.get("fuelType") != "WIND":
                raise ValueError("Unexpected fuel type")
            timestamp(row["startTime"])
            timestamp(row["publishTime"])
            generation = row["generation"]
            if isinstance(generation, bool) or not isinstance(generation, (int, float)):
                raise ValueError("Missing or non-numeric generation")
            if not 0 <= generation < float("inf"):
                raise ValueError("Negative or non-finite wind generation")
            valid.append(row)
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            rejected.append({"row_index": index, "reason": str(exc)})
    by_key = defaultdict(list)
    for row in valid:
        by_key[(timestamp(row["startTime"]), timestamp(row["publishTime"]))].append(row)
    conflicts = [key for key, values in by_key.items()
                 if len({row["generation"] for row in values}) > 1]
    summary = {
        "retrieved_rows": len(rows), "valid_rows": len(valid), "rejected_rows": len(rejected),
        "rejection_examples": rejected[:5],
        "unique_delivery_publication_keys": len(by_key),
        "repeated_keys": sum(len(values) - 1 for values in by_key.values()),
        "conflicting_keys": len(conflicts),
        "delivery_minute_counts": dict(Counter(row["startTime"][14:16] for row in valid)),
        "example": valid[0] if valid else None,
        "earliest_delivery": min((row["startTime"] for row in valid), default=None),
        "latest_delivery": max((row["startTime"] for row in valid), default=None),
        "earliest_publication": min((row["publishTime"] for row in valid), default=None),
        "latest_publication": max((row["publishTime"] for row in valid), default=None),
    }
    if conflicts:
        raise ValueError(f"{dataset}: conflicting values for identical delivery/publication keys")
    return summary, [values[0] for values in by_key.values()]


def report(actual_rows: list, forecast_rows: list, start: date, end: date, cutoff: time) -> dict:
    actual_summary, actuals = audit(actual_rows, "FUELHH")
    forecast_summary, forecasts = audit(forecast_rows, "WINDFOR")
    actual_by_time = defaultdict(list)
    forecast_by_time = defaultdict(list)
    for row in actuals:
        actual_by_time[timestamp(row["startTime"])].append(row)
    for row in forecasts:
        forecast_by_time[timestamp(row["startTime"])].append(row)
    day_reports = []
    for decision_day in days(start, end):
        delivery_day = decision_day + timedelta(days=1)
        cutoff_at = datetime.combine(decision_day, cutoff, LONDON).astimezone(UTC)
        first = datetime.combine(delivery_day, time(), LONDON).astimezone(UTC)
        last = datetime.combine(delivery_day + timedelta(days=1), time(), LONDON).astimezone(UTC)
        grid, current = [], first
        while current < last:
            grid.append(current)
            current += timedelta(minutes=30)
        available = set(actual_by_time).intersection(grid)
        eligible = {}
        for delivery in grid:
            vintages = [row for row in forecast_by_time[delivery]
                        if timestamp(row["publishTime"]) <= cutoff_at]
            if vintages:
                eligible[delivery] = max(vintages, key=lambda row: timestamp(row["publishTime"]))
        native = {delivery for delivery in grid if delivery.minute == 0}
        day_reports.append({
            "decision_date": decision_day.isoformat(), "delivery_date": delivery_day.isoformat(),
            "decision_cutoff": cutoff_at.isoformat(), "expected_half_hours": len(grid),
            "actual_half_hours": len(available),
            "missing_actual_times": [delivery.isoformat() for delivery in grid if delivery not in available],
            "expected_native_hourly_timestamps": len(native),
            "eligible_native_forecasts": len(native.intersection(eligible)),
            "native_forecast_actual_pairs": len(native.intersection(eligible).intersection(available)),
            "unmatched_half_hours_due_to_hourly_forecast": len(set(grid) - native),
            "eligible_publications": sorted({row["publishTime"] for row in eligible.values()}),
        })
    totals = {key: sum(day[key] for day in day_reports) for key in [
        "expected_half_hours", "actual_half_hours", "expected_native_hourly_timestamps",
        "eligible_native_forecasts", "native_forecast_actual_pairs",
        "unmatched_half_hours_due_to_hourly_forecast"]}
    first_delivery = start + timedelta(days=1)
    last_delivery = end + timedelta(days=1)
    totals["forecast_rows_for_delivery_scope"] = sum(
        first_delivery <= timestamp(row["startTime"]).astimezone(LONDON).date() <= last_delivery
        for row in forecasts
    )
    return {"scope": {"decision_date_from": start.isoformat(), "decision_date_to": end.isoformat(),
                      "cutoff_local": cutoff.isoformat(), "timezone": "Europe/London",
                      "delivery_date_from": (start + timedelta(days=1)).isoformat(),
                      "delivery_date_to": (end + timedelta(days=1)).isoformat()},
            "actuals": actual_summary, "forecasts": forecast_summary, "totals": totals,
            "days": day_reports}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 1, 31))
    parser.add_argument("--cutoff", type=time.fromisoformat, default=time(9, 20))
    parser.add_argument("--cache", type=Path, default=Path(".cache/elexon"))
    parser.add_argument("--output", type=Path, default=Path("docs/elexon-audit.json"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.start > args.end or args.cutoff.tzinfo is not None:
        parser.error("Use an ordered date range and a local cutoff without timezone offset")
    args.cache.mkdir(parents=True, exist_ok=True)
    actuals, forecasts, requests = [], [], []
    for delivery_day in days(args.start + timedelta(days=1), args.end + timedelta(days=1)):
        rows, metadata = fetch("FUELHH", {"settlementDateFrom": delivery_day.isoformat(),
                                        "settlementDateTo": delivery_day.isoformat(),
                                        "fuelType": "WIND"}, args.cache, args.refresh)
        actuals.extend(rows)
        requests.append(metadata)
    # Supporting publication windows include early vintages and post-cutoff evolution.
    # Shared inclusive boundaries are deliberately preserved and counted in the audit.
    for publication_day in days(args.start - timedelta(days=2), args.end + timedelta(days=2)):
        rows, metadata = fetch("WINDFOR", {
            "publishDateTimeFrom": publication_day.isoformat() + "T00:00:00Z",
            "publishDateTimeTo": (publication_day + timedelta(days=1)).isoformat() + "T00:00:00Z",
        }, args.cache, args.refresh)
        forecasts.extend(rows)
        requests.append(metadata)
    result = report(actuals, forecasts, args.start, args.end, args.cutoff)
    result["generated_at"] = datetime.now(UTC).isoformat()
    result["requests"] = requests
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))
    print(f"Report written to {args.output}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
