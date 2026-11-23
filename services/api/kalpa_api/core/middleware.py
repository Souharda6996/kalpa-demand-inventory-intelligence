"""Request-ID, timing (-> Redis for /system), security headers."""

import re
import time
import uuid

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from kalpa_api.core.config import get_settings

log = structlog.get_logger()
LATENCY_KEY = "kalpa:metrics:latency"
LATENCY_KEEP = 5000
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach X-Request-ID, record latency, add security headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as e:  # noqa: BLE001 - last line of defence: standard error shape, no internals, still CORS-readable
            log.error(
                "unhandled_exception",
                path=request.url.path,
                method=request.method,
                error=type(e).__name__,
                request_id=rid,
                exc_info=True,
            )
            response = JSONResponse(
                {
                    "code": "internal_error",
                    "message": "Something went wrong on our side.",
                    "details": None,
                    "request_id": rid,
                },
                status_code=500,
            )
        ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers.setdefault("Cache-Control", "no-store")  # API responses are per-user data: never cached
        if get_settings().cookie_secure:  # only meaningful (and only sent) when the API is served over HTTPS
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            path = _UUID.sub("{id}", request.url.path)  # bounded metric cardinality
            try:
                await redis.lpush(
                    LATENCY_KEY, f"{int(time.time())}|{request.method}|{path}|{response.status_code}|{ms:.1f}"
                )
                await redis.ltrim(LATENCY_KEY, 0, LATENCY_KEEP - 1)
            except Exception:  # noqa: S110  (metrics must never break a request)
                pass
        return response


def install_middleware(app: FastAPI) -> None:
    """Add the request-context middleware."""
    app.add_middleware(RequestContextMiddleware)
