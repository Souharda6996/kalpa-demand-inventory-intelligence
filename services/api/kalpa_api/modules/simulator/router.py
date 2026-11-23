"""Live store simulator controls (demo tenant only)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import publish_event, sim_stop_key
from kalpa_api.db.models import Job, Org
from kalpa_api.modules.simulator import service
from kalpa_api.modules.simulator.schemas import SimStartIn, SimState

router = APIRouter(prefix="/simulator", tags=["simulator"])
Viewer = Annotated[Principal, Depends(require("view"))]
Runner = Annotated[Principal, Depends(require("upload_data"))]  # owner / planner, like running backtests


async def enqueue_simulator(org_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID, body: SimStartIn) -> None:
    """Hand the simulation to the ARQ worker (job id = ARQ job id so the Jobs page can cancel it)."""
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job(
            "run_simulator_job",
            str(org_id),
            str(user_id),
            str(job_id),
            body.multiplier,
            body.day_seconds,
            body.minutes,
            _job_id=str(job_id),
        )
    finally:
        await pool.aclose()


async def _is_demo(session: Session, org_id: uuid.UUID) -> bool:
    org = await session.get(Org, org_id)
    return bool(org and org.is_demo)


@router.get("/status", response_model=SimState, operation_id="getSimulatorStatus")
async def status(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> SimState:
    """Whether the simulator is available (demo tenant) and what it is doing right now."""
    return await service.read_state(redis, p.org_id, await _is_demo(session, p.org_id))


@router.post("/start", response_model=SimState, status_code=202, operation_id="startSimulator")
async def start(body: SimStartIn, session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]) -> SimState:
    """Start the live store simulator. Needs forecasts (it follows them) and the demo tenant."""
    if not await _is_demo(session, p.org_id):
        raise ApiError(403, "demo_only", "The live simulator is only available on the demo tenant")
    if (await service.read_state(redis, p.org_id, True)).running:
        raise ApiError(409, "already_running", "The simulator is already running")
    await service.load_plan(session, p.org_id)  # 404 no_forecasts before anything is queued
    job = Job(org_id=p.org_id, type="simulator", status="queued", payload=body.model_dump())
    session.add(job)
    await session.commit()
    await publish_event(
        redis,
        p.org_id,
        {"type": "job", "id": str(job.id), "job_type": "simulator", "status": "queued", "progress": 0.0},
    )
    await enqueue_simulator(p.org_id, p.user.id, job.id, body)
    return SimState(
        available=True, running=True, job_id=str(job.id), multiplier=body.multiplier, day_seconds=body.day_seconds
    )


@router.post("/stop", response_model=SimState, operation_id="stopSimulator")
async def stop(session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]) -> SimState:
    """Ask the simulator to stop after the current tick."""
    await redis.set(sim_stop_key(p.org_id), "1", ex=60)
    return await service.read_state(redis, p.org_id, await _is_demo(session, p.org_id))
