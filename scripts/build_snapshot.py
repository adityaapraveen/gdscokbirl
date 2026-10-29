"""Build the distributable source snapshot from checksum-verified research downloads."""
from pathlib import Path
import gzip
import hashlib
import json


def main():
    root = Path(__file__).resolve().parents[1]
    audit = json.loads((root / "docs/elexon-audit.json").read_text())
    payload = {"actuals": [], "forecasts": [], "metadata": {
        "source": "Elexon FUELHH WIND / WINDFOR",
        "retrieved_at": max(request["retrieved_at"] for request in audit["requests"]),
        "raw_actual_rows": audit["actuals"]["retrieved_rows"],
        "raw_forecast_rows": audit["forecasts"]["retrieved_rows"],
        "rejected_rows": sum(audit[name]["rejected_rows"] for name in ("actuals", "forecasts")),
        "duplicate_source_rows": sum(audit[name]["repeated_keys"] for name in ("actuals", "forecasts")),
        "requests": audit["requests"],
    }}
    seen = set()
    for request in audit["requests"]:
        filename = hashlib.sha256(request["url"].encode()).hexdigest() + ".json"
        body = (root / ".cache/elexon" / filename).read_bytes()
        if hashlib.sha256(body).hexdigest() != request["sha256"]:
            raise ValueError("Research cache differs from audited source")
        for row in json.loads(body):
            if not "2026-01-02" <= row["startTime"][:10] <= "2026-02-01":
                continue
            key = (row["dataset"], row["startTime"], row["publishTime"])
            if key in seen:
                raise ValueError("Duplicate key; inspect source before packaging")
            seen.add(key)
            payload["actuals" if row["dataset"] == "FUELHH" else "forecasts"].append(row)
    target = root / "backend/data/elexon-january-2026.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = gzip.compress(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(), mtime=0)
    target.write_bytes(body)
    target.with_suffix(".sha256").write_text(hashlib.sha256(body).hexdigest() + "\n")
    print({"actuals": len(payload["actuals"]), "forecasts": len(payload["forecasts"]), "bytes": len(body)})


if __name__ == "__main__":
    main()
