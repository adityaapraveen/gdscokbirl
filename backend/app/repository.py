"""Database reads and the serverless adapter for the fixed historical snapshot."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import Dataset, Vintage
from .config import SNAPSHOT
from .ingest import import_payload, load_snapshot
from .storage import Actual, Forecast, Ingestion, make_engine


def read_dataset(engine) -> Dataset:
    with Session(engine) as session:
        actuals = [Vintage(row.start_ts, row.publish_ts, row.generation_mw) for row in session.scalars(select(Actual))]
        forecasts = [Vintage(row.start_ts, row.publish_ts, row.generation_mw) for row in session.scalars(select(Forecast))]
        runs = list(session.scalars(select(Ingestion).order_by(Ingestion.ingested_at)))
        metadata = {**json.loads(runs[-1].metadata_json), "ingested_at": runs[-1].ingested_at,
                    "source_hash": runs[-1].source_hash} if runs else {}
    if not actuals or not forecasts:
        raise ValueError("Dataset is empty; run the ingestion command")
    return Dataset(actuals, forecasts, metadata)


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
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
        engine = make_engine(f"sqlite:///{target}")
    else:
        engine = make_engine()
    try:
        return read_dataset(engine)
    finally:
        engine.dispose()
