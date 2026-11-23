"""Uniform error format: {code, message, details} + request id."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Raise anywhere to return a structured error."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _body(request: Request, code: str, message: str, details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": getattr(request.state, "request_id", None),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers so every error uses one shape."""

    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        d = exc.detail
        if isinstance(d, dict) and "code" in d:
            body = _body(request, d["code"], d.get("message", ""), d.get("details"))
        else:
            body = _body(request, f"http_{exc.status_code}", str(d), None)
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = jsonable_encoder(exc.errors(), custom_encoder={Exception: str})
        return JSONResponse(_body(request, "validation_error", "Request validation failed", details), status_code=422)
