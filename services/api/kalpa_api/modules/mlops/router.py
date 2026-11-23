"""MLOps routes: model registry, drift monitor, retrain."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis

from kalpa_api.core.cache import cached_json
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.db.models import Job
from kalpa_api.modules.forecasts.schemas import JobOut
from kalpa_api.modules.mlops import service
from kalpa_api.modules.mlops.service import DriftCheckOut, DriftOut, RegistryOut

router = APIRouter(tags=["mlops"])
Viewer = Annotated[Principal, Depends(require("view"))]
Runner = Annotated[Principal, Depends(require("upload_data"))]  # owner / planner


@router.get("/models/registry", response_model=RegistryOut, operation_id="getRegistry")
async def get_registry(session: Session, p: Viewer) -> RegistryOut:
    """All model versions with metrics, the champion history and MLflow links."""
    return await service.registry(session, p.org_id)


@router.get("/models/drift", response_model=DriftOut, operation_id="getDrift")
async def get_drift(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> Response:
    """PSI on key features (training window vs new days) and live WAPE of the champion vs its backtest WAPE."""
    return await cached_json(redis, p.org_id, "drift", "", 30, lambda: service.compute_drift(session, redis, p.org_id))


@router.post("/models/drift/check", response_model=DriftCheckOut, operation_id="checkDrift")
async def check_drift(session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]) -> DriftCheckOut:
    """Run the same check the daily cron runs: an alert is published once per model version and starts a retrain."""
    return await service.check_drift(session, redis, p.org_id)


@router.post("/models/retrain", response_model=JobOut, status_code=202, operation_id="retrainNow")
async def retrain_now(session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]) -> Job:
    """Retrain every model of the latest runs now (same models and folds)."""
    return await service.start_retrain(session, redis, p.org_id, "manual")
