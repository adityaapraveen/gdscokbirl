"""Backtest a declared illustrative portfolio; no customer demand history is claimed."""
from datetime import UTC, date, datetime, time, timedelta
import gzip
import hashlib
import json
from pathlib import Path

from backend.app.analytics import decision_cutoff, quantile
from backend.app.forecast_model import load_models
from backend.app.procurement import _calibration, _forecast
from backend.app.renewables import Archive, SOURCES

ROOT = Path(__file__).resolve().parents[1]


def main():
    history = json.loads(gzip.decompress((ROOT / ".cache/procurement/history.json.gz").read_bytes()))
    archive = Archive(history["rows"])
    deadline = datetime(2026, 3, 2, tzinfo=UTC).timestamp()
    report = {"inputs": {"demand_mwh_per_hour": 100, "contracted_mwh_per_hour": 60,
                         "existing_purchases_mwh_per_hour": 25, "each_national_source_share_pct": .05},
              "scope": "Illustrative fixed-demand portfolio; does not measure real customer procurement accuracy.", "windows": {}}
    for name, first, end in (("january", date(2026, 1, 2), date(2026, 2, 2)),
                             ("february", date(2026, 2, 2), date(2026, 3, 2))):
        errors = {method: [] for method in ("baseline", "selected")}
        covered = {method: [] for method in errors}
        excluded = 0
        current = first
        while current < end:
            for slot in ("09:20", "17:00"):
                cutoff = decision_cutoff(current, time.fromisoformat(slot)).timestamp()
                for method in errors:
                    points = _forecast(archive, current, cutoff, method)
                    outcomes = [{source: archive.actual(source, int(datetime.fromisoformat(point.start).timestamp()), deadline)
                                 for source in SOURCES} for point in points]
                    if any(value is None for outcome in outcomes for value in outcome.values()):
                        if method == "selected": excluded += 1
                        continue
                    actual = sum(max(0, 15 - sum(outcome[source][0] * .0005 for source in SOURCES)) for outcome in outcomes)
                    estimated = sum(max(0, 15 - sum(point.sources[source].national_mw * .0005 for source in SOURCES)) for point in points)
                    errors[method].append(estimated - actual)
                    days = _calibration(slot, cutoff, method)
                    scenarios = []
                    for hours in days.values():
                        scenarios.append(sum(max(0, 15 - sum(max(0, point.sources[source].national_mw + hours[datetime.fromisoformat(point.start).hour][source]) * .0005
                                                            for source in SOURCES)) for point in points))
                    covered[method].append(actual <= quantile(scenarios, .9))
            current += timedelta(days=1)
        report["windows"][name] = {method: {"daily_evaluations": len(values), "daily_mae_mwh": sum(map(abs, values)) / len(values),
            "daily_bias_mwh": sum(values) / len(values), "empirical_p90_coverage": sum(covered[method]) / len(covered[method])}
            for method, values in errors.items()}
        report["windows"][name]["excluded_days_with_missing_outcomes"] = excluded
    path = ROOT / "backend/models/procurement.json.gz"
    model = json.loads(gzip.decompress(path.read_bytes()))
    model["report"]["normalized_history_rows"] = len(history["rows"])
    model["report"]["portfolio_backtest"] = report
    body = gzip.compress(json.dumps(model, separators=(",", ":"), allow_nan=False).encode(), mtime=0)
    path.write_bytes(body)
    path.with_suffix(".sha256").write_text(hashlib.sha256(body).hexdigest() + "\n")
    (ROOT / "docs/MODEL-EVALUATION.json").write_text(json.dumps(model["report"], indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
