from fastapi.testclient import TestClient
import pytest

from backend.app.main import app, dataset_dependency
from backend.app.repository import read_dataset
from backend.app.ingest import import_payload, load_snapshot
from backend.app.storage import make_engine


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('api') / 'data.sqlite'}")
    payload, digest = load_snapshot()
    import_payload(engine, payload, digest)
    dataset = read_dataset(engine)
    app.dependency_overrides[dataset_dependency] = lambda: dataset
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_real_accuracy_matches_independent_research(client):
    response = client.get("/api/accuracy")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["sample_count"] == 744
    assert data["metrics"]["mae_mw"] == pytest.approx(2022.470430107527)
    assert data["metrics"]["bias_mw"] == pytest.approx(-1745.9381720430108)
    assert sum(bucket["count"] for bucket in data["histogram"]) == 744


def test_overview_replay_and_boundary_dates(client):
    response = client.get("/api/overview", params={"delivery_date": "2026-02-01"})
    assert response.status_code == 200
    data = response.json()
    assert data["decision_date"] == "2026-01-31"
    assert len(data["curve"]) == 24
    assert all(point["actual_mw"] is None for point in data["curve"])
    assert all(point["publish_time"] <= data["decision_cutoff"] for point in data["curve"])
    revealed = client.get("/api/overview?delivery_date=2026-02-01&reveal=true").json()
    assert all(point["actual_mw"] is not None for point in revealed["curve"])


@pytest.mark.parametrize("url", ["/api/overview?delivery_date=2026-01-01", "/api/overview?cutoff=25:00",
    "/api/evolution?delivery_time=2026-01-22T18:00:00", "/api/evolution?delivery_time=2026-01-22T18:30:00Z",
    "/api/accuracy?date_from=2026-01-31&date_to=2026-01-01"])
def test_invalid_scope_and_times_rejected(client, url):
    assert client.get(url).status_code == 422


def test_data_quality_distinguishes_resolution_from_missing_data(client):
    response = client.get("/api/data-quality")
    assert response.status_code == 200
    data = response.json()
    assert data["actual_half_hours"] == 1488
    assert data["missing_actual_periods"] == []
    assert data["eligible_native_forecasts"] == 744
    assert data["half_hours_without_native_forecast"] == 744
    assert data["join_coverage"] == 1


def test_cold_start_has_no_invented_band(client):
    data = client.get("/api/overview?delivery_date=2026-01-02").json()
    assert not data["calibration"]["available"]
    assert all(point["lower_mw"] is None for point in data["curve"])


def test_missing_database_is_503_not_fake_data(monkeypatch):
    previous = app.dependency_overrides.pop(dataset_dependency, None)
    monkeypatch.setattr("backend.app.main.get_dataset", lambda: (_ for _ in ()).throw(ValueError("missing")))
    try:
        with TestClient(app) as client:
            assert client.get("/ready").status_code == 503
            assert client.get("/health").status_code == 200
    finally:
        if previous is not None:
            app.dependency_overrides[dataset_dependency] = previous


def test_api_documentation_is_under_proxied_api_path(client):
    assert client.get("/api/docs").status_code == 200
    contract = client.get("/api/openapi.json").json()
    assert "/api/evolution" in contract["paths"]
