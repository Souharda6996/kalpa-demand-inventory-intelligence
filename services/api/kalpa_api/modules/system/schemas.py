"""Schemas for the health endpoint."""

from typing import Literal

from pydantic import BaseModel

ServiceStatus = Literal["up", "down"]


class HealthResponse(BaseModel):
    """Health payload: version, git SHA and dependency status."""

    status: Literal["ok", "degraded"]
    version: str
    git_sha: str
    db: ServiceStatus
    redis: ServiceStatus
    worker: ServiceStatus
