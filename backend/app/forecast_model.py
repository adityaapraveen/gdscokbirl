"""Serve checksum-verified LightGBM exports without native training libraries.

Only numerical regression trees are supported. The training job verifies this
scorer against native LightGBM on held-out feature rows before exporting.
"""
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[1] / "models/procurement.json.gz"


def compact_tree(node: dict):
    if "leaf_value" in node:
        return float(node["leaf_value"])
    if node["decision_type"] != "<=":
        raise ValueError("Only numerical regression splits are supported")
    return [node["split_feature"], float(node["threshold"]), node["default_left"], node["missing_type"],
            compact_tree(node["left_child"]), compact_tree(node["right_child"])]


def score(trees: list, features: list[float | None]) -> float:
    total = 0.0
    for tree in trees:
        node = tree
        while isinstance(node, list):
            feature, threshold, default_left, missing_type, left, right = node
            value = features[feature]
            if value is None and missing_type == "NaN":
                node = left if default_left else right
            else:
                value = 0.0 if value is None else value
                if missing_type == "Zero" and abs(value) <= 1e-35:
                    node = left if default_left else right
                else:
                    node = left if value <= threshold else right
        total += node
    return total


@lru_cache(maxsize=1)
def load_models() -> dict:
    body = MODEL_PATH.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digest != MODEL_PATH.with_suffix(".sha256").read_text().strip():
        raise ValueError("Procurement model checksum mismatch")
    payload = json.loads(gzip.decompress(body))
    payload["artifact_hash"] = digest
    return payload


def predict(source: str, features: list[float | None], method: str = "selected", models: dict | None = None) -> float:
    config = (models or load_models())["sources"][source]
    adjustment = score(config["trees"], features) if method != "baseline" and config["selected"] == "lightgbm" else 0
    return max(0, features[0] + adjustment)
