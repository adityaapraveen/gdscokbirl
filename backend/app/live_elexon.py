"""Bounded runtime refresh of the fixed historical challenge window.

The synchronous entry point runs in FastAPI's worker thread. Its two independent
HTTP requests run concurrently under one absolute deadline, including retries.
"""
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging

import httpx
from pydantic import ValidationError

from .elexon import BASE_URL, ElexonError
from .sources import SourceRow

LOG = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 20
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
QUERIES = {
    "FUELHH": {"settlementDateFrom": "2026-01-02", "settlementDateTo": "2026-02-01", "fuelType": "WIND"},
    "WINDFOR": {"publishDateTimeFrom": "2025-12-30T00:00:00Z", "publishDateTimeTo": "2026-02-03T00:00:00Z"},
}
FIRST_DELIVERY = datetime(2026, 1, 2, tzinfo=UTC)
END_DELIVERY = datetime(2026, 2, 2, tzinfo=UTC)


async def _fetch(client: httpx.AsyncClient, dataset: str) -> tuple[list, dict]:
    for attempt in range(2):
        try:
            async with client.stream("GET", f"{BASE_URL}/datasets/{dataset}/stream", params=QUERIES[dataset]) as response:
                if response.status_code in TRANSIENT_STATUSES and attempt == 0:
                    LOG.warning("live_elexon_retry dataset=%s status=%s", dataset, response.status_code)
                else:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ElexonError(f"Elexon {dataset} response exceeded the size limit")
                        body.extend(chunk)
                    try:
                        rows = json.loads(body)
                    except (ValueError, UnicodeError) as exc:
                        raise ElexonError(f"Elexon {dataset} returned invalid JSON") from exc
                    if not isinstance(rows, list) or not rows:
                        raise ElexonError(f"Elexon {dataset} returned an empty or invalid dataset")
                    return rows, {"url": str(response.url), "retrieved_at": datetime.now(UTC).isoformat(),
                                  "sha256": hashlib.sha256(body).hexdigest(), "row_count": len(rows)}
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == 1:
                raise ElexonError(f"Elexon network failure for {dataset}") from exc
            LOG.warning("live_elexon_retry dataset=%s reason=network", dataset)
        except httpx.HTTPStatusError as exc:
            raise ElexonError(f"Elexon {dataset} returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ElexonError(f"Elexon transport failure for {dataset}") from exc
        await asyncio.sleep(0.2)
    raise ElexonError(f"Elexon retry budget exhausted for {dataset}")


def _validate(actuals: list, forecasts: list) -> int:
    """Reject a partial month and conflicting source versions before any writes."""
    duplicates = 0
    delivery_times = {"FUELHH": set(), "WINDFOR": set()}
    eligible_forecast_times = set()
    seen = {}
    for expected_dataset, rows in (("FUELHH", actuals), ("WINDFOR", forecasts)):
        for raw in rows:
            try:
                row = SourceRow.model_validate(raw)
            except (ValidationError, TypeError) as exc:
                raise ElexonError(f"Elexon {expected_dataset} contained an invalid source record") from exc
            if row.dataset != expected_dataset or (expected_dataset == "FUELHH" and row.fuel_type != "WIND"):
                raise ElexonError(f"Elexon {expected_dataset} returned an unexpected dataset or fuel type")
            if expected_dataset == "WINDFOR" and row.start_time.minute:
                raise ElexonError("Elexon WINDFOR timestamps must be native whole hours")
            key = (row.dataset, row.start_time, row.publish_time)
            if key in seen:
                if seen[key] != row.generation_mw:
                    raise ElexonError(f"Elexon {expected_dataset} contained conflicting source versions")
                duplicates += 1
            seen[key] = row.generation_mw
            if FIRST_DELIVERY <= row.start_time < END_DELIVERY:
                delivery_times[expected_dataset].add(row.start_time)
                cutoff = (row.start_time - timedelta(days=1)).replace(hour=9, minute=20)
                if expected_dataset == "WINDFOR" and row.publish_time <= cutoff:
                    eligible_forecast_times.add(row.start_time)
    half_hours = {FIRST_DELIVERY + timedelta(minutes=30 * index) for index in range(1488)}
    hours = {FIRST_DELIVERY + timedelta(hours=index) for index in range(744)}
    if delivery_times["FUELHH"] != half_hours:
        raise ElexonError("Elexon FUELHH response did not cover all 1,488 challenge half-hours")
    if delivery_times["WINDFOR"] != hours or eligible_forecast_times != hours:
        raise ElexonError("Elexon WINDFOR response did not cover all 744 eligible challenge hours")
    return duplicates


async def _fetch_live_payload(transport: httpx.AsyncBaseTransport | None) -> dict:
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=httpx.Timeout(8), transport=transport,
                                         headers={"Accept": "application/json"}, follow_redirects=False) as client:
                (actuals, actual_request), (forecasts, forecast_request) = await asyncio.gather(
                    _fetch(client, "FUELHH"), _fetch(client, "WINDFOR"))
    except TimeoutError as exc:
        raise ElexonError("Elexon refresh exceeded its total time budget") from exc
    duplicates = _validate(actuals, forecasts)
    return {"actuals": actuals, "forecasts": forecasts, "metadata": {
        "source": "Elexon live API", "retrieved_at": datetime.now(UTC).isoformat(),
        "raw_actual_rows": len(actuals), "raw_forecast_rows": len(forecasts),
        "duplicate_source_rows": duplicates, "rejected_rows": 0,
        "requests": [actual_request, forecast_request],
    }}


def fetch_live_payload(*, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Fetch both sources or raise, without touching the database.

    ``transport`` permits deterministic network boundary tests. Production uses
    the fixed Elexon host and fixed date filters; callers cannot proxy other URLs.
    """
    return asyncio.run(_fetch_live_payload(transport))
