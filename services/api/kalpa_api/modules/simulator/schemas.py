"""Live store simulator schemas."""

from datetime import date

from pydantic import BaseModel, Field


class SimStartIn(BaseModel):
    day_seconds: int = Field(default=240, ge=20, le=3600, description="Real seconds per simulated business day")
    multiplier: float = Field(
        default=1.0, ge=0.2, le=5.0, description="Demand vs the forecast (1.0 = as forecast; 2.0 = forced drift)"
    )
    minutes: int = Field(default=60, ge=1, le=180, description="Stop automatically after this many real minutes")


class SimState(BaseModel):
    available: bool  # only the demo tenant can run it
    running: bool
    job_id: str | None = None
    sim_date: date | None = None  # the business day being simulated
    clock: str | None = None  # simulated time of day, HH:MM
    day_index: int = 0
    checkouts: int = 0
    units: int = 0
    revenue: float = 0.0
    lost_units: int = 0  # demand that found no stock (checkout refused, stock never goes negative)
    errors: int = 0
    multiplier: float = 1.0
    day_seconds: int = 240
    started_at: str | None = None
