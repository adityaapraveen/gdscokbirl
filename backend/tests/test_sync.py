"""Runtime refresh boundaries: upstream is mocked, persistence and API reads are real."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import repository, sync
from backend.app.elexon import ElexonError
from backend.app.ingest import import_payload, load_snapshot
from backend.app.main import app
from backend.app.responses import clear_response_cache
from backend.app.storage import Actual, Forecast, Ingestion, SourceSync, make_engine


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """Each test owns its database, clock, dataset cache, and dependency overrides."""
    database_url = f"sqlite:///{tmp_path / 'runtime.sqlite'}"
    monkeypatch.setattr(repository, "get_database_url", lambda: database_url)
    monkeypatch.setattr(sync, "get_database_url", lambda: database_url)
    monkeypatch.delenv("VERCEL", raising=False)
    engine = make_engine(database_url)
    payload, digest = load_snapshot()
    import_payload(engine, payload, digest)
    clock = SimpleNamespace(at=datetime.now(UTC))
    monkeypatch.setattr(sync, "now", lambda: clock.at)
    live = deepcopy(payload)
    live["metadata"] = {
        "source": "Elexon live API", "retrieved_at": clock.at.isoformat(),
        "raw_actual_rows": len(live["actuals"]), "raw_forecast_rows": len(live["forecasts"]),
        "duplicate_source_rows": 0, "rejected_rows": 0,
        "requests": [{"url": "https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream"},
                     {"url": "https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream"}],
    }
    overrides = app.dependency_overrides.copy()
    app.dependency_overrides.clear()
    repository.get_dataset.cache_clear()
    clear_response_cache()
    try:
        yield SimpleNamespace(engine=engine, payload=live, digest=digest, clock=clock)
    finally:
        repository.get_dataset.cache_clear()
        clear_response_cache()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        engine.dispose()


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (Actual, Forecast, Ingestion))


def test_success_persists_new_vintage_and_api_reads_committed_provenance(runtime, monkeypatch):
    before = repository.get_dataset()
    extra = {"dataset": "WINDFOR", "startTime": "2026-01-02T00:00:00Z",
             "publishTime": "2026-01-01T09:19:59Z", "generation": 12345}
    runtime.payload["forecasts"].append(extra)
    fetch = Mock(return_value=runtime.payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)

    status = sync.refresh_source()

    assert status.mode == "live"
    assert status.last_success_at == runtime.clock.at.isoformat()
    assert status.error is None
    assert counts(runtime.engine) == (1488, 18105, 2)
    after = repository.get_dataset()
    assert after is not before
    digest = hashlib.sha256(json.dumps({key: runtime.payload[key] for key in ("actuals", "forecasts")},
                                      sort_keys=True).encode()).hexdigest()
    assert after.metadata["source_hash"] == digest
    assert after.metadata["source_mode"] == "live"
    assert after.metadata["retrieved_at"] == status.last_success_at
    with TestClient(app) as client:
        overview = client.get("/api/overview?delivery_date=2026-01-02").json()
        quality = client.get("/api/data-quality").json()
    assert overview["curve"][0]["forecast_mw"] == 12345
    assert datetime.fromisoformat(overview["curve"][0]["publish_time"]) == datetime(2026, 1, 1, 9, 19, 59, tzinfo=UTC)
    assert overview["provenance"]["source_hash"] == digest
    assert overview["provenance"]["source_mode"] == "live"
    assert quality["stored_forecast_records"] == 18105
    fetch.assert_called_once_with()


def test_unchanged_content_updates_freshness_without_duplicate_rows(runtime, monkeypatch):
    fetch = Mock(return_value=runtime.payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)
    first = sync.refresh_source()
    first_counts = counts(runtime.engine)
    first_hash = repository.get_dataset().metadata["source_hash"]
    runtime.clock.at += timedelta(seconds=sync.COOLDOWN_SECONDS)

    second = sync.refresh_source(force=True)

    assert fetch.call_count == 2
    assert second.last_success_at != first.last_success_at
    assert second.last_success_at == runtime.clock.at.isoformat()
    assert counts(runtime.engine) == first_counts == (1488, 18104, 2)
    assert repository.get_dataset().metadata["source_hash"] == first_hash
    assert repository.get_dataset().metadata["retrieved_at"] == second.last_success_at
    with Session(runtime.engine) as session:
        assert session.get(SourceSync, 1).last_success_at == second.last_success_at


def test_ttl_avoids_downloads_and_manual_refresh_respects_cooldown(runtime, monkeypatch):
    fetch = Mock(return_value=runtime.payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)
    first = sync.refresh_source()
    runtime.clock.at += timedelta(seconds=sync.COOLDOWN_SECONDS - 1)
    assert sync.refresh_source(force=True).last_success_at == first.last_success_at
    assert fetch.call_count == 1
    runtime.clock.at += timedelta(seconds=1)
    manual = sync.refresh_source(force=True)
    assert fetch.call_count == 2
    runtime.clock.at += timedelta(seconds=sync.TTL_SECONDS - 1)
    assert sync.refresh_source().last_success_at == manual.last_success_at
    assert fetch.call_count == 2
    assert sync.source_status().mode == "live"
    runtime.clock.at += timedelta(seconds=1)
    assert sync.source_status().mode == "cached"
    refreshed = sync.refresh_source()
    assert fetch.call_count == 3
    assert refreshed.mode == "live"
    assert datetime.fromisoformat(refreshed.next_refresh_at) == runtime.clock.at + timedelta(seconds=sync.TTL_SECONDS)


def test_upstream_failure_preserves_snapshot_and_throttles_retry(runtime, monkeypatch):
    before = counts(runtime.engine)
    fetch = Mock(side_effect=ElexonError("Elexon network failure for FUELHH"))
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)

    status = sync.refresh_source()

    assert status.mode == "snapshot"
    assert status.last_success_at is None
    assert status.last_attempt_at == runtime.clock.at.isoformat()
    assert status.error == "Elexon network failure for FUELHH"
    assert datetime.fromisoformat(status.next_refresh_at) == runtime.clock.at + timedelta(seconds=sync.COOLDOWN_SECONDS)
    assert counts(runtime.engine) == before
    assert repository.get_dataset().metadata["source_hash"] == runtime.digest
    assert repository.get_dataset().metadata["source_mode"] == "snapshot"
    sync.refresh_source(force=True)
    fetch.assert_called_once_with()
    runtime.clock.at += timedelta(seconds=sync.COOLDOWN_SECONDS)
    sync.refresh_source()
    assert fetch.call_count == 2


def test_failed_refresh_after_success_keeps_last_good_live_records(runtime, monkeypatch):
    fetch = Mock(return_value=runtime.payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)
    success = sync.refresh_source()
    previous = repository.get_dataset()
    before = counts(runtime.engine)
    runtime.clock.at += timedelta(seconds=sync.COOLDOWN_SECONDS)
    fetch.side_effect = ElexonError("Elexon WINDFOR returned HTTP 503")

    failed = sync.refresh_source(force=True)

    assert failed.mode == "cached"
    assert failed.last_success_at == success.last_success_at
    assert failed.last_attempt_at != success.last_attempt_at
    assert failed.error == "Elexon WINDFOR returned HTTP 503"
    assert counts(runtime.engine) == before
    assert repository.get_dataset().metadata["source_hash"] == previous.metadata["source_hash"]
    assert repository.get_dataset().metadata["source_mode"] == "cached"
    assert repository.get_dataset().metadata["retrieved_at"] == success.last_success_at


@pytest.mark.parametrize("damage", ["invalid", "conflict", "missing_actual", "missing_forecast"])
def test_bad_refresh_never_mutates_last_good_data(runtime, monkeypatch, damage):
    before = counts(runtime.engine)
    if damage == "invalid":
        runtime.payload["forecasts"][0]["generation"] = None
    elif damage == "conflict":
        # A valid new row precedes the conflict to exercise transaction rollback.
        runtime.payload["forecasts"].append({**runtime.payload["forecasts"][0],
                                            "publishTime": "2026-01-01T09:19:59Z"})
        runtime.payload["forecasts"].append({**runtime.payload["forecasts"][1], "generation": 1})
    else:
        runtime.payload["actuals" if damage == "missing_actual" else "forecasts"].pop()
    monkeypatch.setattr(sync, "fetch_live_payload", Mock(return_value=runtime.payload))

    status = sync.refresh_source()

    assert status.mode == "snapshot"
    assert status.last_success_at is None
    assert status.error
    assert counts(runtime.engine) == before
    assert repository.get_dataset().metadata["source_hash"] == runtime.digest


def test_concurrent_refreshes_share_one_upstream_attempt(runtime, monkeypatch):
    entered, release, second_started = Event(), Event(), Event()

    def fetch_payload():
        entered.set()
        assert release.wait(timeout=5)
        return runtime.payload

    fetch = Mock(side_effect=fetch_payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)

    def second_refresh():
        second_started.set()
        return sync.refresh_source(force=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sync.refresh_source, True)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(second_refresh)
            assert second_started.wait(timeout=5)
        finally:
            release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    fetch.assert_called_once_with()
    assert all(result.mode == "live" for result in results)
    assert results[0].last_success_at == results[1].last_success_at
    assert counts(runtime.engine) == (1488, 18104, 2)


def test_ready_remains_available_during_blocked_elexon_download(runtime, monkeypatch):
    entered, release = Event(), Event()

    def fetch_payload():
        entered.set()
        assert release.wait(timeout=5)
        raise ElexonError("Elexon refresh exceeded its total time budget")

    monkeypatch.setattr(sync, "fetch_live_payload", fetch_payload)
    with ThreadPoolExecutor(max_workers=2) as pool, TestClient(app) as client:
        refresh = pool.submit(sync.refresh_source)
        try:
            assert entered.wait(timeout=5)
            response = pool.submit(client.get, "/ready").result(timeout=2)
            assert response.status_code == 200
            assert response.json() == {"status": "ready", "actual_periods": 1488, "forecast_periods": 744}
        finally:
            release.set()
        assert refresh.result(timeout=5).mode == "snapshot"


def test_source_routes_expose_status_headers_and_gets_never_fetch(runtime, monkeypatch):
    fetch = Mock(return_value=runtime.payload)
    monkeypatch.setattr(sync, "fetch_live_payload", fetch)
    with TestClient(app) as client:
        for path in ("/health", "/ready", "/api/source-status", "/api/overview"):
            assert client.get(path).status_code == 200
        fetch.assert_not_called()
        initial = client.get("/api/source-status")
        assert initial.json()["mode"] == "snapshot"
        assert initial.json()["storage"] == "persistent"
        assert initial.json()["ttl_seconds"] == 3600
        assert initial.json()["cooldown_seconds"] == 60
        refreshed = client.post("/api/source/refresh?force=true")
        assert refreshed.status_code == 200
        assert refreshed.json()["mode"] == "live"
        assert client.get("/api/source/refresh").status_code == 405
        assert client.post("/api/source/refresh?force=unknown").status_code == 422
        contract = client.get("/api/openapi.json").json()
    fetch.assert_called_once_with()
    for response in (initial, refreshed):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        UUID(response.headers["x-request-id"])
    assert set(contract["paths"]["/api/source/refresh"]) == {"post"}
    assert contract["paths"]["/api/source/refresh"]["post"]["parameters"][0]["name"] == "force"


def test_vercel_source_status_labels_disposable_storage(runtime, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert sync.source_status().storage == "ephemeral"
