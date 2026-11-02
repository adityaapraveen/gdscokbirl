"""Deterministic temporal training, tuning, calibration and untouched January evaluation.

Run acquisition first. Training dependencies are isolated from API requirements.
"""
from datetime import UTC, date, datetime, time, timedelta
import gzip
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from backend.app.analytics import decision_cutoff, delivery_grid, metrics
from backend.app.forecast_model import compact_tree, predict, score
from backend.app.renewables import Archive, FEATURES, SOURCES

ROOT = Path(__file__).resolve().parents[1]
CUTOFFS = (time(9, 20), time(17))
BOUNDARIES = {"train": "2025-09-01", "early_stop": "2025-10-01", "selection": "2025-11-01",
              "calibration": "2026-01-02", "january_benchmark": "2026-02-02", "test": "2026-03-02"}


def group(start: int):
    day = datetime.fromtimestamp(start, UTC).date().isoformat()
    for name, end in BOUNDARIES.items():
        if day < end:
            return name
    return None


def main():
    raw = (ROOT / ".cache/procurement/history.json.gz").read_bytes()
    payload = json.loads(gzip.decompress(raw))
    archive = Archive(payload["rows"], payload["metadata"])
    records = {source: [] for source in SOURCES}
    current = date(2023, 1, 2)
    while current <= date(2026, 3, 1):
        for cutoff_time in CUTOFFS:
            cutoff = decision_cutoff(current, cutoff_time).timestamp()
            for start in delivery_grid(current):
                partition = group(start)
                available_after = {"early_stop": "2025-09-01", "selection": "2025-10-01",
                                   "calibration": "2025-11-01", "test": "2026-01-02"}.get(partition)
                if partition != "test" and available_after and cutoff < datetime.fromisoformat(available_after).replace(tzinfo=UTC).timestamp():
                    continue
                # Outcomes revised after a partition's boundary are not labels
                # available when the next phase begins.
                label_deadline = datetime.fromisoformat(BOUNDARIES[partition]).replace(tzinfo=UTC).timestamp()
                for source in SOURCES:
                    actual = archive.actual(source, start, label_deadline)
                    featured = archive.features(source, start, cutoff)
                    if actual is not None and featured is not None:
                        features, _ = featured
                        records[source].append({"start": start, "cutoff": cutoff, "slot": cutoff_time.isoformat(timespec="minutes"),
                            "partition": partition, "features": features, "actual": actual[0], "published": actual[1]})
        current += timedelta(days=1)
    models = {"schema_version": 1, "library": f"LightGBM {lgb.__version__}", "features": list(FEATURES),
              "trained_through": "2025-08-31", "selected_by": "2025-10-31", "sources": {}}
    report = {"created_at": datetime.now(UTC).isoformat(), "history_sha256": hashlib.sha256(raw).hexdigest(),
              "normalized_history_rows": len(payload["rows"]),
              "splits": {"train": "2023 or 2025 start through 31 Aug 2025", "early_stop": "September 2025", "selection": "October 2025",
                         "calibration": "2 Nov 2025–1 Jan 2026", "january_benchmark": "2 Jan–1 Feb 2026 (previously inspected)",
                         "untouched_test": "2 Feb–1 Mar 2026"},
              "cutoffs": [x.isoformat(timespec="minutes") for x in CUTOFFS],
              "selection_rule": "At least 2% lower October MAE than the source-specific baseline; never select on January.",
              "candidates_per_source": 12, "sources": {}, "limitations": [
                  "The first delivery day of each tuning/selection/calibration phase is purged so prior-phase labels are available at decision time.",
                  "National generation allocation is a scenario proxy, not an asset/PPA forecast.",
                  "Solar AGWS outcomes may be estimates; WINDFOR is an hourly power proxy.",
                  "No external weather, plant availability or fuel-price features.",
                  "A single final winter test window does not establish year-round reliability.",
                  "Future-mode models are fixed historical models and are not automatically retrained."]}
    for source in SOURCES:
        entries = records[source]
        parts = {name: [row for row in entries if row["partition"] == name] for name in BOUNDARIES}
        arrays = {name: np.array([[np.nan if x is None else x for x in row["features"]] for row in rows], dtype=float)
                  for name, rows in parts.items()}
        labels = {name: np.array([row["actual"] - row["features"][0] for row in rows]) for name, rows in parts.items()}
        if min(len(parts[name]) for name in BOUNDARIES) < 100:
            raise ValueError(f"Insufficient chronological coverage for {source}")
        best = None
        candidates = []
        for training_start in ("2023-01-02", "2025-01-02"):
            first = datetime.fromisoformat(training_start).replace(tzinfo=UTC).timestamp()
            mask = np.array([row["start"] >= first for row in parts["train"]])
            for leaves in (7, 15, 31):
                for minimum in (50, 150):
                    params = {"objective": "regression_l1", "metric": "l1", "learning_rate": .04,
                              "num_leaves": leaves, "max_depth": 6, "min_data_in_leaf": minimum,
                              "lambda_l2": 2, "feature_fraction": .9, "verbosity": -1, "num_threads": 2,
                              "seed": 42, "deterministic": True, "force_col_wise": True}
                    train = lgb.Dataset(arrays["train"][mask], label=labels["train"][mask], feature_name=list(FEATURES))
                    stop = lgb.Dataset(arrays["early_stop"], label=labels["early_stop"], reference=train)
                    booster = lgb.train(params, train, num_boost_round=400, valid_sets=[stop],
                                        callbacks=[lgb.early_stopping(30, verbose=False)])
                    estimates = np.maximum(0, arrays["selection"][:, 0] + booster.predict(arrays["selection"]))
                    actual = np.array([row["actual"] for row in parts["selection"]])
                    mae = float(np.mean(np.abs(actual - estimates)))
                    trial = {"training_start": training_start, "training_samples": int(mask.sum()),
                             "num_leaves": leaves, "min_data_in_leaf": minimum, "best_iteration": booster.best_iteration,
                             "selection_mae_mw": mae}
                    candidates.append(trial)
                    if best is None or mae < best[0]:
                        best = (mae, booster, trial)
        mae, booster, trial = best
        baseline_mae = float(np.mean(np.abs(labels["selection"])))
        selected = "lightgbm" if mae < baseline_mae * .98 else "baseline"
        trees = [compact_tree(tree["tree_structure"]) for tree in booster.dump_model()["tree_info"]]
        parity_rows = np.concatenate([arrays["selection"][::4], arrays["test"][::4]])
        native = booster.predict(parity_rows)
        exported = np.array([score(trees, [None if np.isnan(x) else float(x) for x in row]) for row in parity_rows])
        parity = float(np.max(np.abs(native - exported)))
        if parity > 1e-7:
            raise ValueError(f"Exported inference differs from native LightGBM for {source}: {parity}")
        models["sources"][source] = {"selected": selected, "trees": trees, "parameters": trial}
        evaluations = {}
        for name in ("selection", "january_benchmark", "test"):
            actual = np.array([row["actual"] for row in parts[name]])
            baseline = arrays[name][:, 0]
            candidate = np.maximum(0, baseline + booster.predict(arrays[name]))
            chosen = candidate if selected == "lightgbm" else baseline
            evaluations[name] = {"baseline": metrics((actual - baseline).tolist()),
                                 "lightgbm": metrics((actual - candidate).tolist()),
                                 "selected": metrics((actual - chosen).tolist())}
        report["sources"][source] = {"selected": selected, "counts": {k: len(v) for k, v in parts.items()},
            "candidates": candidates, "parameters": trial, "export_parity_max_abs_error": parity,
            "evaluation": evaluations}
        print(source, selected, "October baseline/ML", baseline_mae, mae,
              "January baseline/selected", evaluations["january_benchmark"]["baseline"]["mae_mw"],
              evaluations["january_benchmark"]["selected"]["mae_mw"],
              "February baseline/selected", evaluations["test"]["baseline"]["mae_mw"],
              evaluations["test"]["selected"]["mae_mw"], flush=True)
    # Preserve synchronized residual vectors and their publication times. They
    # can be scaled to a portfolio without assuming independent fuel errors.
    aligned = {}
    for source in SOURCES:
        for row in records[source]:
            if row["partition"] != "calibration":
                continue
            key = (row["start"], row["slot"])
            entry = aligned.setdefault(key, {"start": row["start"], "slot": row["slot"], "published": 0,
                                             "selected": {}, "baseline": {}})
            entry["published"] = max(entry["published"], row["published"])
            for method in ("selected", "baseline"):
                entry[method][source] = row["actual"] - predict(source, row["features"], method, models)
    models["calibration"] = [row for row in aligned.values() if len(row["selected"]) == 4]
    models["report"] = report
    destination = ROOT / "backend/models/procurement.json.gz"
    blob = gzip.compress(json.dumps(models, separators=(",", ":"), allow_nan=False).encode(), mtime=0)
    destination.write_bytes(blob)
    destination.with_suffix(".sha256").write_text(hashlib.sha256(blob).hexdigest() + "\n")
    (ROOT / "docs/MODEL-EVALUATION.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    # Bundle only the history used by the January calculator and lag features.
    boundary = datetime(2025, 10, 1, tzinfo=UTC).timestamp()
    end = datetime(2026, 2, 2, tzinfo=UTC).timestamp()
    snapshot = {"rows": [row for row in payload["rows"] if boundary <= row[2] < end], "metadata": payload["metadata"]}
    target = ROOT / "backend/data/renewables-january-2026.json.gz"
    blob = gzip.compress(json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode(), mtime=0)
    target.write_bytes(blob)
    target.with_suffix(".sha256").write_text(hashlib.sha256(blob).hexdigest() + "\n")
    (ROOT / "docs/renewables-source-audit.json").write_text(json.dumps(payload["metadata"], indent=2) + "\n")
    print("Exported", destination, "and renewable snapshot", flush=True)


if __name__ == "__main__":
    main()
