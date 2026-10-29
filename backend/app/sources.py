"""Validated source contract shared by online ingestion and the frozen snapshot."""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    dataset: Literal["FUELHH", "WINDFOR"]
    start_time: datetime = Field(alias="startTime")
    publish_time: datetime = Field(alias="publishTime")
    generation_mw: float = Field(alias="generation", ge=0, allow_inf_nan=False)
    fuel_type: str | None = Field(default=None, alias="fuelType")

    @field_validator("start_time", "publish_time")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Source timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("start_time")
    @classmethod
    def settlement_grid(cls, value: datetime) -> datetime:
        if value.minute not in (0, 30) or value.second or value.microsecond:
            raise ValueError("Delivery timestamp must lie on the half-hour grid")
        return value

    @field_validator("generation_mw", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Generation must be numeric MW")
        return value

    @field_validator("fuel_type")
    @classmethod
    def wind_only(cls, value):
        if value not in (None, "WIND"):
            raise ValueError("Only WIND actuals are supported")
        return value
