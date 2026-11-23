"""Schemas for models / forecasts / jobs."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BacktestRequest(BaseModel):
    models: list[str] | None = None  # default: all baselines
    folds: int = Field(default=3, ge=1, le=5)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: str
    status: str
    progress: float
    result: dict[str, Any] | None
    error: str | None
    payload: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class JobDetail(JobOut):
    log: str | None = None  # progress messages, newest last


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    model_name: str
    version: str
    status: str
    metrics: dict[str, float | None] | None
    config: dict[str, Any] | None
    is_champion: bool
    created_at: datetime


class HistoryPoint(BaseModel):
    date: date
    units: int


class ForecastPoint(BaseModel):
    date: date
    p10: float
    p50: float
    p90: float


class ModelSeries(BaseModel):
    model_name: str
    run_id: uuid.UUID
    metrics: dict[str, float | None] | None
    points: list[ForecastPoint]


class SeriesOut(BaseModel):
    history: list[HistoryPoint]
    models: list[ModelSeries]
