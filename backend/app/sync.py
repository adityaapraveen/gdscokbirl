"""Bounded Elexon refreshes with atomic persistence and a last-good fallback.

The lock and cooldown apply to one API instance. Vercel's database is disposable;
Docker's database volume persists both source records and freshness state.
"""
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
from threading import Lock

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .elexon import ElexonError
from .ingest import import_payload, load_snapshot
from .live_elexon import fetch_live_payload
from .repository import get_database_url, get_dataset, read_dataset
from .responses import clear_response_cache
from .schemas import SourceStatus
from .sources import SourceRow
from .storage import SourceSync, make_engine

LOG = logging.getLogger("windlens")
TTL_SECONDS = 3600
COOLDOWN_SECONDS = 60
_refresh_lock = Lock()


def now() -> datetime:
    return datetime.now(UTC)


def _state(engine) -> dict:
    SourceSync.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        row = session.get(SourceSync, 1)
        return {"last_attempt_at": row.last_attempt_at, "last_success_at": row.last_success_at,
                "error": row.error} if row else {
                    "last_attempt_at": None, "last_success_at": None, "error": None}


def _save_state(engine, state: dict):
    with Session(engine) as session, session.begin():
        session.merge(SourceSync(id=1, **state))


def _status(state: dict) -> SourceStatus:
    last_attempt = datetime.fromisoformat(state["last_attempt_at"]) if state["last_attempt_at"] else None
    last_success = datetime.fromisoformat(state["last_success_at"]) if state["last_success_at"] else None
    fresh = last_success is not None and (now() - last_success).total_seconds() < TTL_SECONDS
    next_refresh = (last_attempt + timedelta(seconds=COOLDOWN_SECONDS) if state["error"] and last_attempt
                    else last_success + timedelta(seconds=TTL_SECONDS) if last_success else None)
    return SourceStatus(mode=("live" if fresh and not state["error"] else "cached") if last_success else "snapshot",
                        storage="ephemeral" if os.getenv("VERCEL") and not os.getenv("DATABASE_URL") else "persistent",
                        next_refresh_at=next_refresh.isoformat() if next_refresh else None,
                        **state)


def source_status() -> SourceStatus:
    # Ensure status describes a usable dataset, without contacting Elexon.
    get_dataset()
    engine = make_engine(get_database_url())
    try:
        return _status(_state(engine))
    finally:
        engine.dispose()


def _require_archive_coverage(payload: dict):
    """An apparently valid but truncated archive must not become a successful refresh."""
    baseline, _ = load_snapshot()
    for name in ("actuals", "forecasts"):
        def keys(rows):
            return {(row.start_time, row.publish_time) for raw in rows
                    for row in [SourceRow.model_validate(raw)]}
        if not keys(baseline[name]).issubset(keys(payload[name])):
            raise ElexonError(f"Elexon {name} archive is incomplete; retaining stored data.")


def refresh_source(force: bool = False) -> SourceStatus:
    # A GET never starts upstream downloads. The app explicitly POSTs a cached
    # refresh on opening; manual requests can bypass TTL, but not the cooldown.
    with _refresh_lock:
        get_dataset()
        engine = make_engine(get_database_url())
        try:
            state = _state(engine)
            current = now()
            if state["last_attempt_at"]:
                age = (current - datetime.fromisoformat(state["last_attempt_at"])).total_seconds()
                if age < COOLDOWN_SECONDS:
                    return _status(state)
            if not force and state["last_success_at"] and not state["error"]:
                age = (current - datetime.fromisoformat(state["last_success_at"])).total_seconds()
                if age < TTL_SECONDS:
                    return _status(state)
            state["last_attempt_at"] = current.isoformat()
            _save_state(engine, state)
            try:
                payload = fetch_live_payload()
                _require_archive_coverage(payload)
                content = {name: payload[name] for name in ("actuals", "forecasts")}
                digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
                counts = import_payload(engine, payload, digest)
                # Read back committed records before advertising a successful check.
                read_dataset(engine)
                state.update(last_success_at=now().isoformat(), error=None)
                _save_state(engine, state)
                LOG.info("elexon_refresh_succeeded source_hash=%s inserted=%s duplicates=%s",
                         digest, counts["inserted"], counts["duplicates"])
            except (ElexonError, ValueError, SQLAlchemyError, OSError) as exc:
                LOG.warning("elexon_refresh_retained_data reason=%s", exc)
                state["error"] = str(exc)[:240] if isinstance(exc, ElexonError) else (
                    "Elexon data failed validation; retaining the last good dataset.")
                _save_state(engine, state)
            clear_response_cache()
            get_dataset.cache_clear()
            return _status(state)
        finally:
            engine.dispose()
