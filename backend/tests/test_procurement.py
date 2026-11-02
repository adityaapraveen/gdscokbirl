from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import procurement, renewable_sync
from backend.app.elexon import ElexonError
from backend.app.forecast_model import load_models, score
from backend.app.main import app
from backend.app.renewables import Archive, normalize
from backend.app.storage import RenewableCheck, RenewableObservation, make_engine


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    path = tmp_path_factory.mktemp("procurement") / "sources.sqlite"
    return f"sqlite:///{path}"


@pytest.fixture
def store(database, monkeypatch):
    monkeypatch.setattr(renewable_sync, "get_database_url", lambda: database)
    monkeypatch.delenv("VERCEL", raising=False)
    renewable_sync._load_archive.cache_clear()
    procurement._forecast.cache_clear()
    engine = renewable_sync._engine()
    with engine.begin() as connection:
        connection.execute(RenewableCheck.__table__.delete())
    yield engine
    engine.dispose()
    renewable_sync._load_archive.cache_clear()
    procurement._forecast.cache_clear()


def request_body():
    return {"delivery_date": "2026-01-22", "disjoint_supply_confirmed": True,
            "periods": [{"start": f"2026-01-22T{hour:02d}:00:00Z", "demand_mwh": 100,
                         "contracted_mwh": 60, "purchased_mwh": 25} for hour in range(24)]}


def test_energy_balance_without_variable_allocations_is_exact(store):
    result = procurement.calculate(procurement.ProcurementRequest.model_validate(request_body()))
    assert result.additional_procurement_mwh == 360
    assert result.total_demand_mwh == 2400
    assert result.total_firm_supply_mwh == 2040
    assert result.total_renewable_mwh == 0
    assert result.surplus_mwh == 0
    assert result.low_generation_procurement_mwh == 360
    assert all(row.procurement_mwh == 15 for row in result.periods)


def test_allocated_generation_uses_percentage_not_total_national_output(store):
    body = request_body()
    body["allocations_pct"] = {"wind": .1}
    data = procurement.ProcurementRequest.model_validate(body)
    result = procurement.calculate(data)
    for period, forecast in zip(result.periods, result.forecast.periods):
        allocated = forecast.sources["wind"].national_mw * .001
        assert period.renewable_by_source_mwh["wind"] == pytest.approx(allocated)
        assert period.procurement_mwh == pytest.approx(max(0, 15 - allocated))
        assert period.surplus_mwh == pytest.approx(max(0, allocated - 15))


def test_surplus_in_one_period_cannot_cancel_another_periods_shortfall(store):
    body = request_body()
    body["periods"][0]["contracted_mwh"] = 200
    result = procurement.calculate(procurement.ProcurementRequest.model_validate(body))
    assert result.additional_procurement_mwh == 23 * 15
    assert result.surplus_mwh == 125


@pytest.mark.parametrize("damage", ["negative", "bool", "nan", "share", "overlap", "duplicate", "gap", "naive", "string", "extra"])
def test_invalid_or_ambiguous_portfolios_are_rejected(store, damage):
    body = request_body()
    if damage == "negative": body["periods"][0]["demand_mwh"] = -1
    elif damage == "bool": body["periods"][0]["demand_mwh"] = True
    elif damage == "nan": body["periods"][0]["demand_mwh"] = float("nan")
    elif damage == "share": body["allocations_pct"] = {"solar": 101}
    elif damage == "overlap": body["disjoint_supply_confirmed"] = False
    elif damage == "duplicate": body["periods"][1]["start"] = body["periods"][0]["start"]
    elif damage == "gap": body["periods"].pop()
    elif damage == "naive": body["periods"][0]["start"] = "2026-01-22T00:00:00"
    elif damage == "string": body["periods"][0]["demand_mwh"] = "100"
    else: body["unexpected"] = "ignored?"
    with pytest.raises(ValueError):
        procurement.calculate(procurement.ProcurementRequest.model_validate(body))


def test_daily_scenario_preserves_cross_hour_dependence_instead_of_summing_quantiles(store, monkeypatch):
    days = {str(index): {hour: {"wind": 100 if (hour + index) % 2 else -100,
                               "solar": 0, "hydro": 0, "biomass": 0} for hour in range(24)} for index in range(30)}
    monkeypatch.setattr(procurement, "_calibration", lambda *args: days)
    body = request_body()
    body["allocations_pct"] = {"wind": 100}
    for position in body["periods"]:
        position.update(demand_mwh=50000, contracted_mwh=0, purchased_mwh=0)
    result = procurement.calculate(procurement.ProcurementRequest.model_validate(body))
    assert result.low_generation_procurement_mwh == pytest.approx(result.additional_procurement_mwh)
    assert sum(row.low_generation_procurement_mwh for row in result.periods) == pytest.approx(result.additional_procurement_mwh + 2400)


def test_unpublished_calibration_outcomes_are_excluded():
    earliest = min(row["published"] for row in load_models()["calibration"])
    assert procurement._calibration("09:20", earliest - 1, "selected") == {}


def test_future_data_is_not_invented_from_old_snapshot(store, monkeypatch):
    monkeypatch.setattr(renewable_sync, "now", lambda: datetime(2030, 6, 10, 8, tzinfo=UTC))
    forecast = procurement.forecast(date(2030, 6, 11))
    assert datetime.fromisoformat(forecast.decision_cutoff) <= renewable_sync.now()
    assert all(value.national_mw is None for point in forecast.periods for value in point.sources.values())
    body = request_body()
    body["delivery_date"] = "2030-06-11"
    for row in body["periods"]: row["start"] = row["start"].replace("2026-01-22", "2030-06-10")
    # BST delivery starts at23:00 UTC the preceding calendar day.
    starts = procurement.delivery_grid(date(2030, 6, 11))
    for row, start in zip(body["periods"], starts): row["start"] = datetime.fromtimestamp(start, UTC).isoformat()
    body["allocations_pct"] = {"wind": .1}
    with pytest.raises(ValueError, match="unavailable"):
        procurement.calculate(procurement.ProcurementRequest.model_validate(body))


def test_future_horizon_and_dst_days_are_explicitly_restricted(store, monkeypatch):
    monkeypatch.setattr(renewable_sync, "now", lambda: datetime(2026, 3, 28, 12, tzinfo=UTC))
    with pytest.raises(ValueError, match="DST"):
        procurement.forecast(date(2026, 3, 29))
    with pytest.raises(ValueError, match="tomorrow"):
        procurement.forecast(date(2026, 3, 30))


def test_api_contracts_and_private_inputs_are_not_stored(store):
    with TestClient(app) as client:
        response = client.post("/api/procurement/calculate", json=request_body())
        assert response.status_code == 200
        assert response.json()["additional_procurement_mwh"] == 360
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/procurement/forecast?cutoff=13:00").status_code == 422
        assert client.get("/api/procurement/models").status_code == 200
        contract = client.get("/api/openapi.json").json()
        assert "post" in contract["paths"]["/api/procurement/calculate"]
    assert set(store.dialect.get_table_names(store.connect())).isdisjoint({"portfolios", "positions", "procurement_results"})


def test_failed_source_refresh_retains_snapshot_and_applies_cooldown(store, monkeypatch):
    calls = []
    async def failed(day):
        calls.append(day)
        raise ElexonError("upstream unavailable")
    monkeypatch.setattr(renewable_sync, "_fetch_window", failed)
    before, _ = renewable_sync.get_archive(date(2026, 1, 22))
    status = renewable_sync.refresh(date(2026, 1, 22), True)
    assert status["error"] == "upstream unavailable" and status["mode"] == "snapshot"
    after, _ = renewable_sync.get_archive(date(2026, 1, 22))
    assert after.features("wind", 1769032800, 1768987200) == before.features("wind", 1769032800, 1768987200)
    renewable_sync.refresh(date(2026, 1, 22), True)
    assert len(calls) == 1


def test_normalization_preserves_zero_and_rejects_wrong_dataset_and_conflicts():
    solar = {"dataset": "AGWS", "psrType": "Solar", "quantity": 0, "startTime": "2026-01-22T00:00:00Z", "publishTime": "2026-01-22T01:00:00Z"}
    assert normalize("AGWS", [solar])[0][-1] == 0
    with pytest.raises(ValueError): normalize("DGWS", [solar])
    with pytest.raises(ValueError): normalize("AGWS", [solar, {**solar, "quantity": 1}])
    with pytest.raises(ValueError): normalize("AGWS", [{**solar, "quantity": None}])


def test_as_of_features_exclude_future_forecasts_and_incomplete_actual_hours():
    start = int(datetime(2026, 1, 22, tzinfo=UTC).timestamp())
    cutoff = datetime(2026, 1, 21, 9, 20, tzinfo=UTC).timestamp()
    archive = Archive([["wind", "forecast", start, cutoff, 100], ["wind", "forecast", start, cutoff + 1, 999],
                       ["wind", "actual", start, cutoff - 2, 50]])
    assert archive.features("wind", start, cutoff)[0][0] == 100
    assert archive.actual("wind", start, cutoff) is None
    future_actuals = Archive([["wind", "actual", start + offset, cutoff - 1, 100] for offset in (0, 1800)])
    assert future_actuals.actual("wind", start, cutoff) is None


def test_exported_tree_missing_and_boundary_semantics():
    tree = [0, 10, False, "NaN", 1., 2.]
    assert score([tree], [10]) == 1
    assert score([tree], [11]) == 2
    assert score([tree], [None]) == 2
    assert score([[0, 10, False, "None", 1., 2.]], [None]) == 1
    assert score([[0, 10, False, "Zero", 1., 2.]], [0]) == 2


def test_model_report_has_separate_selection_calibration_and_confirmation_windows():
    models = load_models()
    report = models["report"]
    assert report["candidates_per_source"] == 12
    assert report["splits"]["untouched_test"] == "2 Feb–1 Mar 2026"
    for source in ("wind", "solar", "hydro", "biomass"):
        item = report["sources"][source]
        assert item["export_parity_max_abs_error"] < 1e-7
        assert len(item["candidates"]) == 12
        selected = item["evaluation"]["selection"]
        if item["selected"] == "lightgbm":
            assert selected["lightgbm"]["mae_mw"] < selected["baseline"]["mae_mw"] * .98
        assert item["counts"]["test"] > 1000
