"""Run explicitly: python -m backend.app.ingest [--live]."""
import argparse
from datetime import UTC, datetime
import gzip
import hashlib
import json
import logging

from .config import DATABASE_URL, SNAPSHOT
from .elexon import ElexonClient
from .sources import SourceRow
from .storage import make_engine, persist

LOG = logging.getLogger(__name__)


def load_snapshot() -> tuple[dict, str]:
    body = SNAPSHOT.read_bytes()
    expected = SNAPSHOT.with_suffix(".sha256").read_text().strip()
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected:
        raise ValueError("Snapshot checksum mismatch")
    return json.loads(gzip.decompress(body)), digest


def import_payload(engine, payload: dict, digest: str):
    # Validate all input before opening the write transaction. No partial imports.
    rows = [SourceRow.model_validate(row) for name in ("actuals", "forecasts") for row in payload[name]]
    rows = [row for row in rows if datetime(2026, 1, 2, tzinfo=UTC) <= row.start_time
            < datetime(2026, 2, 2, tzinfo=UTC)]
    metadata = {**payload.get("metadata", {}), "actual_records": sum(row.dataset == "FUELHH" for row in rows),
                "forecast_records": sum(row.dataset == "WINDFOR" for row in rows)}
    return persist(engine, rows, digest, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Fetch Elexon explicitly; default uses bundled snapshot")
    parser.add_argument("--database-url", default=DATABASE_URL)
    args = parser.parse_args()
    if args.live:
        client = ElexonClient()
        try:
            payload = client.january()
        finally:
            client.close()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        payload["metadata"] = {"source": "Elexon live API", "retrieved_at": datetime.now(UTC).isoformat(),
            "raw_actual_rows": len(payload["actuals"]), "raw_forecast_rows": len(payload["forecasts"])}
    else:
        payload, digest = load_snapshot()
    engine = make_engine(args.database_url)
    try:
        result = import_payload(engine, payload, digest)
        LOG.info(json.dumps({"event": "ingestion_complete", "source_hash": digest, **result}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
