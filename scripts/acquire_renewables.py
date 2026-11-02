"""Download audited public Elexon history for offline model development.

Raw responses remain in ignored .cache/procurement/. No API request trains a model.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import time

import httpx

from backend.app.elexon import BASE_URL
from backend.app.renewables import normalize

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache/procurement"


def fetch(dataset: str, params: dict) -> tuple[list[list], dict]:
    url = str(httpx.Request("GET", f"{BASE_URL}/datasets/{dataset}/stream", params=params).url)
    key = hashlib.sha256(url.encode()).hexdigest()
    path, evidence = CACHE / f"{key}.json.gz", CACHE / f"{key}.meta.json"
    if path.exists() and evidence.exists():
        body = gzip.decompress(path.read_bytes())
        meta = json.loads(evidence.read_text())
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("Cached source checksum mismatch")
    else:
        for attempt in range(3):
            response = httpx.get(url, timeout=45)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1 + attempt)
                continue
            response.raise_for_status()
            body = response.content
            break
        meta = {"url": url, "sha256": hashlib.sha256(body).hexdigest(),
                "retrieved_at": datetime.now(UTC).isoformat(), "row_count": len(json.loads(body))}
        path.write_bytes(gzip.compress(body, mtime=0))
        evidence.write_text(json.dumps(meta))
    rows = normalize(dataset, json.loads(body))
    print(f"{dataset}: {len(rows)} relevant rows ({params})", flush=True)
    return rows, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", default="2024-12-01")
    parser.add_argument("--to-date", default="2026-02-03", help="Exclusive boundary")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    tasks = []
    current, end = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    while current < end:
        following = min((current.replace(day=28) + timedelta(days=4)).replace(day=1), end)
        for dataset in ("FUELHH", "WINDFOR", "DGWS", "AGWS"):
            params = ({"settlementDateFrom": current.isoformat(), "settlementDateTo": (following - timedelta(days=1)).isoformat(),
                       "fuelType": ["WIND", "NPSHYD", "BIOMASS"]} if dataset == "FUELHH" else {
                           "publishDateTimeFrom": f"{current}T00:00:00Z", "publishDateTimeTo": f"{following}T00:00:00Z"})
            tasks.append((dataset, params))
        current = following
    rows, requests = {}, []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for batch, meta in pool.map(lambda task: fetch(*task), tasks):
            requests.append(meta)
            for row in batch:
                key = tuple(row[:-1])
                if key in rows and rows[key] != row[-1]:
                    raise ValueError("Conflicting versions across source windows")
                rows[key] = row[-1]
    payload = {"rows": [[*key, value] for key, value in sorted(rows.items())],
               "metadata": {"requests": requests, "retrieved_at": max(x["retrieved_at"] for x in requests),
                            "from_date": args.from_date, "to_date": args.to_date}}
    target = CACHE / "history.json.gz"
    target.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    print(f"Normalized {len(rows)} rows into {target}", flush=True)


if __name__ == "__main__":
    main()
