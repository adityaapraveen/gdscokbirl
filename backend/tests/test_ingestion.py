from datetime import UTC, datetime
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.elexon import ElexonClient, ElexonError
from backend.app.ingest import import_payload, load_snapshot
from backend.app.sources import SourceRow
from backend.app.storage import Actual, Forecast, Ingestion, make_engine


def row(generation=7000, **changes):
    return {"dataset": "WINDFOR", "startTime": "2026-01-15T18:00:00Z",
            "publishTime": "2026-01-14T08:30:00Z", "generation": generation, **changes}


@pytest.fixture
def engine(tmp_path):
    database = make_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    yield database
    database.dispose()


def test_snapshot_integrity_and_real_counts(engine):
    payload, digest = load_snapshot()
    assert len(payload["actuals"]) == 1488
    assert len(payload["forecasts"]) == 18104
    result = import_payload(engine, payload, digest)
    assert result["inserted"] == 19592
    repeated = import_payload(engine, payload, digest)
    assert repeated == {"inserted": 0, "duplicates": 19592}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Actual)) == 1488
        assert session.scalar(select(func.count()).select_from(Forecast)) == 18104
        assert session.scalar(select(func.count()).select_from(Ingestion)) == 1


def test_conflict_rolls_back_entire_batch(engine):
    payload = {"actuals": [], "forecasts": [row(), row(6500)]}
    with pytest.raises(ValueError, match="Conflicting"):
        import_payload(engine, payload, "bad")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Forecast)) == 0
        assert session.scalar(select(func.count()).select_from(Ingestion)) == 0


def test_actual_publication_versions_preserved(engine):
    actual = row(dataset="FUELHH", fuelType="WIND", publishTime="2026-01-15T18:30:00Z")
    revised = {**actual, "publishTime": "2026-01-16T10:30:00Z", "generation": 6900}
    import_payload(engine, {"actuals": [actual, revised], "forecasts": []}, "versions")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Actual)) == 2


@pytest.mark.parametrize("value", [None, True, "7000", float("nan"), -1])
def test_invalid_generation_is_not_coerced(value):
    with pytest.raises(ValidationError):
        SourceRow.model_validate(row(value))


def test_naive_time_and_wrong_fuel_rejected():
    with pytest.raises(ValidationError):
        SourceRow.model_validate(row(publishTime="2026-01-14T08:30:00"))
    with pytest.raises(ValidationError):
        SourceRow.model_validate(row(dataset="FUELHH", fuelType="CCGT"))


def test_client_does_not_retry_bad_requests():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"error": "bad parameters"})
    client = ElexonClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ElexonError):
        client.fetch("WINDFOR", {})
    assert len(requests) == 1
    client.close()


def test_client_retries_transient_server_error(monkeypatch):
    statuses = iter([503, 200])
    monkeypatch.setattr("backend.app.elexon.time.sleep", lambda _: None)
    def handler(request):
        return httpx.Response(next(statuses), json=[row()])
    client = ElexonClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(client.fetch("WINDFOR", {})) == 1
    client.close()
