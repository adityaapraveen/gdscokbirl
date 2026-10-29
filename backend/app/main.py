import logging
import os
from datetime import date, datetime, time
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from .analytics import Dataset, LONDON, aware
from .repository import get_dataset
from .schemas import Accuracy, DataQuality, Evolution, Overview
from . import services

LOG = logging.getLogger("windlens")
app = FastAPI(title="WindLens API", version="1.0.0",
              description="Decision-time analysis of GB operationally metered wind. Error = actual − forecast.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
                   allow_methods=["GET"], allow_headers=["Content-Type"])


@app.middleware("http")
async def request_context(request, call_next):
    request_id = str(uuid4())
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    LOG.info("request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f", request_id,
             request.method, request.url.path, response.status_code, (perf_counter() - started) * 1000)
    return response


def dataset_dependency() -> Dataset:
    try:
        return get_dataset()
    except (SQLAlchemyError, OSError, ValueError) as exc:
        LOG.exception("dataset_unavailable")
        raise HTTPException(503, "Dataset unavailable. Run the ingestion command and restart the API.") from exc


def cutoff_dependency(cutoff: Annotated[str, Query(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] = "09:20") -> time:
    return time.fromisoformat(cutoff)


Data = Annotated[Dataset, Depends(dataset_dependency)]
Cutoff = Annotated[time, Depends(cutoff_dependency)]


def validate_delivery(day: date):
    if not date(2026, 1, 2) <= day <= date(2026, 2, 1):
        raise HTTPException(422, "Delivery date must be 2 January–1 February 2026 (January decision dates).")


@app.get("/health")
def health():
    return {"status": "ok", "service": "windlens", "version": "1.0.0"}


@app.get("/ready")
def ready(dataset: Data):
    return {"status": "ready", "actual_periods": len(dataset.actuals), "forecast_periods": len(dataset.forecasts)}


@app.get("/api/overview", response_model=Overview)
def overview(dataset: Data, cutoff: Cutoff, delivery_date: date = date(2026, 1, 22), reveal: bool = False):
    validate_delivery(delivery_date)
    return services.overview(dataset, delivery_date, cutoff, reveal)


@app.get("/api/evolution", response_model=Evolution)
def evolution(dataset: Data, cutoff: Cutoff, delivery_time: datetime = datetime.fromisoformat("2026-01-22T18:00:00+00:00"), reveal: bool = False):
    try:
        delivery_time = aware(delivery_time)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    validate_delivery(delivery_time.astimezone(LONDON).date())
    if delivery_time.minute or delivery_time.second or delivery_time.microsecond:
        raise HTTPException(422, "WINDFOR is hourly. Select a native whole-hour delivery timestamp.")
    return services.evolution(dataset, delivery_time, cutoff, reveal)


@app.get("/api/accuracy", response_model=Accuracy)
def accuracy(dataset: Data, cutoff: Cutoff, date_from: date = date(2026, 1, 1), date_to: date = date(2026, 1, 31)):
    if not date(2026, 1, 1) <= date_from <= date_to <= date(2026, 1, 31):
        raise HTTPException(422, "Select an ordered range of decision dates within January 2026.")
    return services.accuracy(dataset, date_from, date_to, cutoff)


@app.get("/api/data-quality", response_model=DataQuality)
def data_quality(dataset: Data, cutoff: Cutoff):
    return services.data_quality(dataset, cutoff)
