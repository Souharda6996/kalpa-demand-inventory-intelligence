"""FastAPI app factory."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from kalpa_api.core.cache import invalidate_cache_after_write
from kalpa_api.core.config import assert_safe_secrets, get_settings
from kalpa_api.core.errors import install_error_handlers
from kalpa_api.core.middleware import install_middleware
from kalpa_api.db.session import dispose_engine
from kalpa_api.modules.audit.router import router as audit_router
from kalpa_api.modules.auth.router import router as auth_router
from kalpa_api.modules.copilot.router import router as copilot_router
from kalpa_api.modules.crud.inventory import router as inventory_router
from kalpa_api.modules.crud.routers import crud_routers
from kalpa_api.modules.data.router import router as data_router
from kalpa_api.modules.explain.router import router as explain_router
from kalpa_api.modules.forecasts.router import router as forecasts_router
from kalpa_api.modules.mlops.router import router as mlops_router
from kalpa_api.modules.orgs.invites import auth_router as invite_auth_router
from kalpa_api.modules.orgs.invites import org_router as invites_router
from kalpa_api.modules.orgs.router import router as org_router
from kalpa_api.modules.policy.router import router as policy_router
from kalpa_api.modules.pos.router import router as pos_router
from kalpa_api.modules.public.router import router as public_router
from kalpa_api.modules.purchase_orders.router import router as po_router
from kalpa_api.modules.scenarios.router import router as scenarios_router
from kalpa_api.modules.simulator.router import router as simulator_router
from kalpa_api.modules.stream.router import router as stream_router
from kalpa_api.modules.system.router import router as system_router


def _safe_console() -> None:
    """A log line must never break a request: on Windows consoles (cp1252) a rupee sign would raise on print."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Close the shared database pool on shutdown."""
    yield
    await dispose_engine()


def create_app(redis_client: Redis | None = None) -> FastAPI:
    """Build the FastAPI application (a Redis client can be injected for tests)."""
    settings = get_settings()
    _safe_console()
    assert_safe_secrets(settings)
    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(invalidate_cache_after_write)],
        title="Kalpa API",
        version=settings.app_version,
        description="Demand & Inventory Intelligence platform API. Auth: HttpOnly cookie JWT + X-CSRF-Token header.",
    )
    app.state.redis = redis_client or Redis.from_url(settings.redis_url)
    install_error_handlers(app)
    install_middleware(app)  # added first -> runs inside CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    api = "/api/v1"
    for r in (
        system_router,
        auth_router,
        org_router,
        invites_router,
        invite_auth_router,
        data_router,
        inventory_router,
        pos_router,
        audit_router,
        forecasts_router,
        policy_router,
        po_router,
        scenarios_router,
        explain_router,
        copilot_router,
        stream_router,
        simulator_router,
        mlops_router,
        public_router,
        *crud_routers,
    ):
        app.include_router(r, prefix=api)
    return app


app = create_app()
