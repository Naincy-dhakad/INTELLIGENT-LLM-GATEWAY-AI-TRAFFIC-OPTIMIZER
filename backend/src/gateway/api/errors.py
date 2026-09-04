from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass
class GatewayAPIError(Exception):
    code: str
    message: str
    status_code: int
    retryable: bool = False
    details: dict[str, Any] | None = None


def error_payload(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request.state.request_id,
            "retryable": retryable,
            "details": details,
        }
    }


def gateway_error_handler(request: Request, exc: GatewayAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            request,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        ),
        headers={"X-Request-ID": request.state.request_id},
    )


def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe envelope without serializing the unexpected exception."""
    _ = exc
    return JSONResponse(
        status_code=500,
        content=error_payload(
            request,
            code="internal_error",
            message="The gateway encountered an internal error.",
        ),
        headers={"X-Request-ID": request.state.request_id},
    )


def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Validation locations/types are safe and useful; raw input is intentionally omitted.
    details = {"field_count": len(exc.errors())}
    return JSONResponse(
        status_code=400,
        content=error_payload(
            request,
            code="invalid_request",
            message="The request body or headers are invalid.",
            details=details,
        ),
        headers={"X-Request-ID": request.state.request_id},
    )
