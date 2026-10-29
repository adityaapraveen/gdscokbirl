"""Database reads and the serverless adapter for the fixed historical snapshot."""
from functools import lru_cache
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import Dataset, Vintage
from .ingest import import_payload, load_snapshot
from .storage import Actual, Forecast, Ingestion, SourceSync, make_engine


def read_dataset(engine) -> Dataset:
    SourceSync.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        actuals = [Vintage(row.start_ts, row.publish_ts, row.generation_mw) for row in session.scalars(select(Actual))]
        forecasts = [Vintage(row.start_ts, row.publish_ts, row.generation_mw) for row in session.scalars(select(Forecast))]
        runs = list(session.scalars(select(Ingestion).order_by(Ingestion.ingested_at)))
        metadata = {**json.loads(runs[-1].metadata_json), "ingested_at": runs[-1].ingested_at,
                    "source_hash": runs[-1].source_hash} if runs else {}
        sync = session.get(SourceSync, 1)
        if sync and sync.last_success_at:
            age = (datetime.now(UTC) - datetime.fromisoformat(sync.last_success_at)).total_seconds()
            metadata.update(source_mode="live" if not sync.error and age < 3600 else "cached",
                            retrieved_at=sync.last_success_at)
        else:
            metadata["source_mode"] = "cached" if metadata.get("source") == "Elexon live API" else "snapshot"
    if not actuals or not forecasts:
        raise ValueError("Dataset is empty; run the ingestion command")
    return Dataset(actuals, forecasts, metadata)


def get_database_url() -> str:
    """Resolve persistent local storage or seed Vercel's disposable database cache."""
    if os.getenv("VERCEL") and not os.getenv("DATABASE_URL"):
        # /tmp is a disposable materialisation, never the authoritative data store.
        # Atomic replacement avoids exposing a partially imported DB to another worker.
        payload, digest = load_snapshot()
        target = Path(tempfile.gettempdir()) / f"windlens-{digest}.sqlite"
        if not target.exists():
            handle, filename = tempfile.mkstemp(prefix="windlens-", suffix=".sqlite")
            os.close(handle)
            temporary = make_engine(f"sqlite:///{filename}")
            try:
                import_payload(temporary, payload, digest)
                temporary.dispose()
                os.replace(filename, target)
            finally:
                temporary.dispose()
                Path(filename).unlink(missing_ok=True)
        return f"sqlite:///{target}"
    from .config import DATABASE_URL
    return DATABASE_URL


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    engine = make_engine(get_database_url())
    try:
        return read_dataset(engine)
    finally:
        engine.dispose()
