"""Bounded, synchronous client used by the explicit ingestion command only."""
import logging
import time
from datetime import date, timedelta

import httpx

LOG = logging.getLogger(__name__)
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"


class ElexonError(RuntimeError):
    pass


class ElexonClient:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=45)

    def fetch(self, dataset: str, params: dict) -> list[dict]:
        for attempt in range(3):
            try:
                response = self.client.get(f"{BASE_URL}/datasets/{dataset}/stream", params=params)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    LOG.warning("elexon_retry dataset=%s status=%s attempt=%s", dataset, response.status_code, attempt + 1)
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ElexonError("Elexon response is not a JSON array")
                return rows
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 2:
                    raise ElexonError(f"Elexon network failure for {dataset}") from exc
                time.sleep(2**attempt)
            except (httpx.HTTPStatusError, ValueError) as exc:
                raise ElexonError(f"Elexon response failure for {dataset}") from exc
        raise ElexonError("Retry budget exhausted")

    def january(self) -> dict:
        actuals, forecasts = [], []
        current = date(2026, 1, 2)
        while current <= date(2026, 2, 1):
            actuals.extend(self.fetch("FUELHH", {"settlementDateFrom": current.isoformat(),
                "settlementDateTo": current.isoformat(), "fuelType": "WIND"}))
            current += timedelta(days=1)
        current = date(2025, 12, 30)
        while current <= date(2026, 2, 2):
            forecasts.extend(self.fetch("WINDFOR", {"publishDateTimeFrom": f"{current}T00:00:00Z",
                "publishDateTimeTo": f"{current + timedelta(days=1)}T00:00:00Z"}))
            current += timedelta(days=1)
        return {"actuals": actuals, "forecasts": forecasts}

    def close(self):
        self.client.close()
