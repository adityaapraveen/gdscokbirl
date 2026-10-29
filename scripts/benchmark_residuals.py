"""Exploratory chronological residual benchmark using the inspection cache.

No network calls. Does not modify or replace the published point forecast.
Candidate methods are compared on the SAME evaluation observations.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from math import sqrt
from pathlib import Path
from statistics import mean

from scripts.inspect_elexon import LONDON, audit, timestamp


def quantile(values: list[float], probability: float) -> float:
    """Linear interpolation at (n-1)*p, equivalent to NumPy's linear method."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    index = int(position)
    return ordered[index] + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index]) * (position - index)


def metrics(errors: list[float]) -> dict:
    return {"sample_count": len(errors), "mae_mw": mean(map(abs, errors)),
            "rmse_mw": sqrt(mean(error**2 for error in errors)), "bias_mw": mean(errors),
            "p90_absolute_error_mw": quantile(list(map(abs, errors)), .9)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=Path("docs/elexon-audit.json"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/elexon"))
    parser.add_argument("--output", type=Path, default=Path("docs/residual-benchmark.json"))
    args = parser.parse_args()
    source = json.loads(args.audit.read_text())
    raw = defaultdict(list)
    # Use only requests belonging to this audit, never unrelated cached downloads.
    for request in source["requests"]:
        filename = hashlib.sha256(request["url"].encode()).hexdigest() + ".json"
        body = (args.cache / filename).read_bytes()
        if hashlib.sha256(body).hexdigest() != request["sha256"]:
            raise ValueError("Cache differs from the audited snapshot")
        for row in json.loads(body):
            raw[row["dataset"]].append(row)
    _, actuals = audit(raw["FUELHH"], "FUELHH")
    _, forecasts = audit(raw["WINDFOR"], "WINDFOR")
    forecast_by_delivery = defaultdict(list)
    actual_by_delivery = defaultdict(list)
    for row in forecasts:
        forecast_by_delivery[timestamp(row["startTime"])].append(row)
    for row in actuals:
        actual_by_delivery[timestamp(row["startTime"])].append(row)
    cutoff_by_delivery_day = {day["delivery_date"]: timestamp(day["decision_cutoff"])
                              for day in source["days"]}
    pairs = []
    for delivery, versions in sorted(actual_by_delivery.items()):
        delivery_day = delivery.astimezone(LONDON).date().isoformat()
        if delivery.minute != 0 or delivery_day not in cutoff_by_delivery_day:
            continue
        cutoff = cutoff_by_delivery_day[delivery_day]
        eligible = [row for row in forecast_by_delivery[delivery]
                    if timestamp(row["publishTime"]) <= cutoff]
        if not eligible:
            continue
        forecast = max(eligible, key=lambda row: timestamp(row["publishTime"]))
        actual = max(versions, key=lambda row: timestamp(row["publishTime"]))
        pairs.append({"delivery": delivery, "cutoff": cutoff, "actual_versions": versions,
                      "forecast_mw": forecast["generation"], "actual_mw": actual["generation"],
                      "error_mw": actual["generation"] - forecast["generation"],
                      "lead_bucket": int((delivery - timestamp(forecast["publishTime"])).total_seconds() // 21600)})
    if not pairs:
        raise ValueError("No native forecast/actual matches")
    results = defaultdict(list)
    for pair in pairs:
        history = []
        for earlier in pairs:
            if earlier["delivery"].astimezone(LONDON).date() >= pair["cutoff"].astimezone(LONDON).date():
                continue
            visible = [row for row in earlier["actual_versions"]
                       if timestamp(row["publishTime"]) <= pair["cutoff"]]
            if visible:
                actual = max(visible, key=lambda row: timestamp(row["publishTime"]))
                history.append({**earlier, "error_mw": actual["generation"] - earlier["forecast_mw"]})
        if len({row["delivery"].astimezone(LONDON).date() for row in history}) < 7:
            continue
        cohorts = {"pooled": history, "lead_6h": [row for row in history
                   if row["lead_bucket"] == pair["lead_bucket"]]}
        # Minimums are declared research guardrails, not statistical guarantees.
        if any(len(rows) < 30 for rows in cohorts.values()):
            continue
        for method, rows in cohorts.items():
            errors = [row["error_mw"] for row in rows]
            lower = max(0, pair["forecast_mw"] + quantile(errors, .1))
            upper = max(0, pair["forecast_mw"] + quantile(errors, .9))
            actual = pair["actual_mw"]
            width = upper - lower
            score = width + 10 * max(0, lower - actual) + 10 * max(0, actual - upper)
            results[method].append({"delivery_time": pair["delivery"].isoformat(),
                "inside": lower <= actual <= upper, "below": actual < lower,
                "width_mw": width, "winkler80_mw": score, "training_count": len(rows)})
    output = {"scope": source["scope"], "audit_generated_at": source["generated_at"],
              "point_forecast_native_matches": metrics([row["error_mw"] for row in pairs]),
              "methodology": {"minimum_completed_history_days": 7, "minimum_cohort_rows": 30,
                  "quantile_method": "linear at (n-1)*p", "interval": "P10-P90 signed errors; lower floor zero",
                  "training": "Earlier complete delivery days; actual publishTime <= replay cutoff",
                  "evaluation": "Identical native timestamp observations across candidate methods",
                  "limitation": "Exploratory single-month comparison, not an independent final holdout"},
              "methods": {}}
    for method, rows in results.items():
        output["methods"][method] = {"evaluation_count": len(rows),
            "evaluation_first": rows[0]["delivery_time"], "evaluation_last": rows[-1]["delivery_time"],
            "coverage": mean(row["inside"] for row in rows),
            "below_p10_rate": mean(row["below"] for row in rows),
            "mean_interval_width_mw": mean(row["width_mw"] for row in rows),
            "mean_winkler80_mw": mean(row["winkler80_mw"] for row in rows),
            "training_count_min": min(row["training_count"] for row in rows),
            "training_count_max": max(row["training_count"] for row in rows)}
    if not results:
        output["limitation"] = "Not enough prior history for the candidate comparison"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
