from datetime import UTC, date, datetime, time
import json

import pytest
from pydantic import ValidationError

from backend.app.analytics import Dataset, Vintage
from backend.app.ingest import load_snapshot
from backend.app.responses import clear_response_cache, render_view
from backend.app import services


@pytest.fixture(autouse=True)
def reset_caches():
    clear_response_cache()
    yield
    clear_response_cache()


@pytest.fixture(scope="module")
def dataset():
    payload, digest = load_snapshot()

    def rows(name):
        return [Vintage(int(datetime.fromisoformat(row["startTime"].replace("Z", "+00:00")).timestamp()),
                        datetime.fromisoformat(row["publishTime"].replace("Z", "+00:00")).timestamp(),
                        row["generation"]) for row in payload[name]]

    return Dataset(rows("actuals"), rows("forecasts"), {**payload["metadata"], "source_hash": digest})


def test_repeat_view_avoids_recalculation_and_cached_output_is_immutable(dataset, monkeypatch):
    calls = []
    original = services.overview

    def counted(*parameters):
        calls.append(parameters)
        return original(*parameters)

    monkeypatch.setattr(services, "overview", counted)
    arguments = ("overview", dataset, date(2026, 1, 22), time(9, 20), False)
    first = render_view(*arguments)
    decoded = json.loads(first)
    decoded["curve"][0]["forecast_mw"] = -1
    second = render_view(*arguments)
    assert first is second
    assert len(calls) == 1
    assert json.loads(second)["curve"][0]["forecast_mw"] >= 0


def test_reveal_date_and_cutoff_have_distinct_cache_entries(dataset):
    hidden = json.loads(render_view("overview", dataset, date(2026, 1, 22), time(9, 20), False))
    revealed = json.loads(render_view("overview", dataset, date(2026, 1, 22), time(9, 20), True))
    different_date = json.loads(render_view("overview", dataset, date(2026, 1, 23), time(9, 20), False))
    different_cutoff = json.loads(render_view("overview", dataset, date(2026, 1, 22), time(0, 0), False))
    assert all(point["actual_mw"] is None for point in hidden["curve"])
    assert all(point["actual_mw"] is not None for point in revealed["curve"])
    assert different_date["delivery_date"] == "2026-01-23"
    assert different_cutoff["decision_cutoff"] != hidden["decision_cutoff"]
    assert different_cutoff["latest_eligible_publication"] != hidden["latest_eligible_publication"]
    assert render_view.cache_info().currsize == 4


def test_new_dataset_does_not_reuse_older_generation_response(dataset):
    actuals = [row for versions in dataset.actuals.values() for row in versions]
    forecasts = [Vintage(row.delivery_ts, row.publish_ts, row.generation_mw + 100)
                 for versions in dataset.forecasts.values() for row in versions]
    updated = Dataset(actuals, forecasts, {**dataset.metadata, "source_hash": "new-source", "source_mode": "live",
                                          "retrieved_at": datetime.now(UTC).isoformat()})
    before = json.loads(render_view("overview", dataset, date(2026, 1, 22), time(9, 20), False))
    after = json.loads(render_view("overview", updated, date(2026, 1, 22), time(9, 20), False))
    assert after["expected_wind_mw"] == pytest.approx(before["expected_wind_mw"] + 100)
    assert after["provenance"]["source_hash"] == "new-source"
    assert after["provenance"]["source_mode"] == "live"


def test_freshness_change_rolls_over_cached_response_without_replacing_records(dataset, monkeypatch):
    original = services.provenance
    mode = ["live"]
    monkeypatch.setattr(services, "provenance", lambda data: {**original(data), "source_mode": mode[0]})
    arguments = ("overview", dataset, date(2026, 1, 22), time(9, 20), False)
    fresh = render_view(*arguments)
    assert json.loads(fresh)["provenance"]["source_mode"] == "live"
    mode[0] = "cached"
    expired = render_view(*arguments)
    assert json.loads(expired)["provenance"]["source_mode"] == "cached"
    assert render_view(*arguments) is expired
    assert render_view.cache_info().currsize == 2


def test_invalid_service_result_is_not_cached(dataset, monkeypatch):
    original = services.overview
    arguments = ("overview", dataset, date(2026, 1, 22), time(9, 20), False)
    monkeypatch.setattr(services, "overview", lambda *args: {"delivery_date": "2026-01-22"})
    with pytest.raises(ValidationError):
        render_view(*arguments)
    assert render_view.cache_info().currsize == 0
    monkeypatch.setattr(services, "overview", original)
    assert json.loads(render_view(*arguments))["delivery_date"] == "2026-01-22"


def test_quality_and_evolution_views_use_explicit_response_contracts(dataset):
    quality = json.loads(render_view("data-quality", dataset, time(9, 20)))
    assert quality["actual_half_hours"] == 1488
    assert quality["stored_forecast_records"] == 18104
    evolution = json.loads(render_view("evolution", dataset, datetime(2026, 1, 22, 18, tzinfo=UTC), time(9, 20), False))
    assert evolution["actual_mw"] is None
    assert all(version["eligible"] for version in evolution["versions"])


def test_refresh_invalidation_releases_response_and_domain_caches(dataset):
    render_view("accuracy", dataset, date(2026, 1, 1), date(2026, 1, 31), time(9, 20))
    assert render_view.cache_info().currsize == 1
    assert Dataset.calibration.cache_info().currsize == 31
    assert Dataset.historical_pool.cache_info().currsize > 0
    assert Dataset.evaluations.cache_info().currsize > 0
    clear_response_cache()
    for cached in (render_view, Dataset.calibration, Dataset.historical_pool, Dataset.evaluations):
        assert cached.cache_info().currsize == 0


def test_revisions_include_equal_cutoff_and_exclude_later_versions():
    target = int(datetime(2026, 1, 22, 18, tzinfo=UTC).timestamp())
    publications = [datetime(2026, 1, 21, hour, minute, tzinfo=UTC).timestamp()
                    for hour, minute in [(7, 30), (9, 20), (10, 30)]]
    data = Dataset([], [Vintage(target, timestamp, value)
                        for timestamp, value in zip(publications, [7000, 6800, 3000])])
    cutoff = datetime(2026, 1, 21, 9, 20, tzinfo=UTC)
    assert data.revisions(target, cutoff) == {"latest_revision_mw": -200, "change_from_first_mw": -200}
    assert data.revisions(target, datetime(2026, 1, 21, 7, tzinfo=UTC)) == {
        "latest_revision_mw": None, "change_from_first_mw": None}
    assert data.revisions(target + 3600, cutoff) == {"latest_revision_mw": None, "change_from_first_mw": None}


def test_naive_revision_cutoff_is_rejected():
    with pytest.raises(ValueError, match="Timezone-aware"):
        Dataset([], []).revisions(0, datetime(2026, 1, 21, 9, 20))
