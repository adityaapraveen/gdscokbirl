import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json

import httpx
import pytest

from backend.app.elexon import ElexonError
from backend.app.ingest import load_snapshot
from backend.app.live_elexon import fetch_live_payload


@pytest.fixture(scope="module")
def source_payload():
    return load_snapshot()[0]


def response(request, payload):
    return httpx.Response(200, json=payload["actuals" if "FUELHH" in request.url.path else "forecasts"])


def test_fetches_both_fixed_sources_concurrently_and_preserves_vintages(source_payload):
    requests = []
    both_started = asyncio.Event()

    async def handler(request):
        requests.append(request)
        if len(requests) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        return response(request, source_payload)

    payload = fetch_live_payload(transport=httpx.MockTransport(handler))
    assert len(payload["actuals"]) == 1488
    assert len(payload["forecasts"]) == 18104
    assert payload["forecasts"] == source_payload["forecasts"]
    by_dataset = {request.url.path.split("/")[-2]: request for request in requests}
    assert dict(by_dataset["FUELHH"].url.params) == {
        "settlementDateFrom": "2026-01-02", "settlementDateTo": "2026-02-01", "fuelType": "WIND"}
    assert dict(by_dataset["WINDFOR"].url.params) == {
        "publishDateTimeFrom": "2025-12-30T00:00:00Z", "publishDateTimeTo": "2026-02-03T00:00:00Z"}
    metadata = payload["metadata"]
    assert metadata["source"] == "Elexon live API"
    assert datetime.fromisoformat(metadata["retrieved_at"]).utcoffset().total_seconds() == 0
    assert metadata["raw_actual_rows"] == 1488
    assert metadata["raw_forecast_rows"] == 18104
    assert metadata["duplicate_source_rows"] == metadata["rejected_rows"] == 0
    for request, evidence in zip(requests, metadata["requests"]):
        raw = response(request, source_payload).content
        assert evidence["url"] == str(request.url)
        assert evidence["sha256"] == hashlib.sha256(raw).hexdigest()
        assert evidence["row_count"] == len(json.loads(raw))


def test_retries_only_transient_response_and_network_failures(source_payload, monkeypatch):
    calls = Counter()

    async def handler(request):
        dataset = request.url.path.split("/")[-2]
        calls[dataset] += 1
        if calls[dataset] == 1:
            if dataset == "FUELHH":
                return httpx.Response(429)
            raise httpx.ReadTimeout("timeout", request=request)
        return response(request, source_payload)

    payload = fetch_live_payload(transport=httpx.MockTransport(handler))
    assert payload["metadata"]["raw_actual_rows"] == 1488
    assert calls == {"FUELHH": 2, "WINDFOR": 2}


@pytest.mark.parametrize("status,expected_calls", [(400, 1), (429, 2), (503, 2)])
def test_failed_http_refresh_is_bounded_and_reports_failure(source_payload, status, expected_calls):
    calls = Counter()

    def handler(request):
        dataset = request.url.path.split("/")[-2]
        calls[dataset] += 1
        return httpx.Response(status) if dataset == "FUELHH" else response(request, source_payload)

    with pytest.raises(ElexonError, match=f"HTTP {status}"):
        fetch_live_payload(transport=httpx.MockTransport(handler))
    assert calls["FUELHH"] == expected_calls


@pytest.mark.parametrize("body", [b"not JSON", b"{}", b"[]"])
def test_invalid_or_empty_json_response_fails_whole_refresh(source_payload, body):
    def handler(request):
        return httpx.Response(200, content=body) if "FUELHH" in request.url.path else response(request, source_payload)

    with pytest.raises(ElexonError, match="invalid|empty"):
        fetch_live_payload(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("changes", [
    {"dataset": "WINDFOR"}, {"fuelType": "CCGT"}, {"fuelType": None},
    {"generation": None}, {"publishTime": "2026-01-03T00:00:00"},
])
def test_invalid_source_identity_or_values_rejected_before_persistence(source_payload, changes):
    modified = deepcopy(source_payload)
    modified["actuals"][0].update(changes)
    with pytest.raises(ElexonError, match="invalid|unexpected"):
        fetch_live_payload(transport=httpx.MockTransport(lambda request: response(request, modified)))


@pytest.mark.parametrize("missing_source", ["actuals", "forecasts", "eligible_forecasts"])
def test_partial_month_is_rejected(source_payload, missing_source):
    modified = deepcopy(source_payload)
    if missing_source == "actuals":
        modified["actuals"].pop()
    else:
        delivery = "2026-01-15T18:00:00Z"
        modified["forecasts"] = [row for row in modified["forecasts"] if row["startTime"] != delivery
                                 or (missing_source == "eligible_forecasts" and row["publishTime"] > "2026-01-14T09:20:00Z")]
    with pytest.raises(ElexonError, match="cover all"):
        fetch_live_payload(transport=httpx.MockTransport(lambda request: response(request, modified)))


def test_repeated_identical_source_rows_are_counted(source_payload):
    modified = deepcopy(source_payload)
    modified["actuals"].append(deepcopy(modified["actuals"][0]))
    payload = fetch_live_payload(transport=httpx.MockTransport(lambda request: response(request, modified)))
    assert payload["metadata"]["duplicate_source_rows"] == 1
    assert payload["metadata"]["raw_actual_rows"] == 1489


def test_conflicting_versions_are_rejected(source_payload):
    modified = deepcopy(source_payload)
    conflicting = {**modified["forecasts"][0], "generation": modified["forecasts"][0]["generation"] + 1}
    modified["forecasts"].append(conflicting)
    with pytest.raises(ElexonError, match="conflicting"):
        fetch_live_payload(transport=httpx.MockTransport(lambda request: response(request, modified)))


def test_response_size_is_capped_before_json_parsing(source_payload, monkeypatch):
    monkeypatch.setattr("backend.app.live_elexon.MAX_RESPONSE_BYTES", 100)
    with pytest.raises(ElexonError, match="size limit"):
        fetch_live_payload(transport=httpx.MockTransport(lambda request: response(request, source_payload)))


def test_total_deadline_cancels_both_slow_requests(monkeypatch):
    monkeypatch.setattr("backend.app.live_elexon.TOTAL_TIMEOUT_SECONDS", 0.01)
    cancelled = []

    async def handler(request):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(request.url.path)

    with pytest.raises(ElexonError, match="total time budget"):
        fetch_live_payload(transport=httpx.MockTransport(handler))
    assert len(cancelled) == 2
