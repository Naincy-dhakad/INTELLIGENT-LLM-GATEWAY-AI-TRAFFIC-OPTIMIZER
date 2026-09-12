from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway.application.observability_events import EventType, make_event
from gateway.application.usage_tracking import record_error


def _set_error_code(request: Request, code: str) -> None:
    request.state.error_code = code


def _emit_validation_result(request: Request, outcome: str, status_code: int, error_code: str | None = None) -> None:
    try:
        attributes = {"outcome": outcome, "status_code": status_code}
        if error_code is not None:
            attributes["error_code"] = error_code
        request.app.state.observability.emit(
            make_event(EventType.REQUEST_VALIDATION_RESULT, request.state.request_id, **attributes)
        )
    except Exception:
        pass


@dataclass
class GatewayAPIError(Exception):
    code: str
    message: str
    status_code: int
    retryable: bool = False
    details: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    internal_error_category: str | None = None


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
    _set_error_code(request, exc.code)
    record_error(
        request, exc.code, exc.status_code, exc.message, exc.internal_error_category
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            request,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        ),
        headers={
            "X-Request-ID": request.state.request_id,
            **(exc.headers or {}),
        },
    )


def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe envelope without serializing the unexpected exception."""
    _ = exc
    _set_error_code(request, "internal_error")
    record_error(request, "internal_error", 500, "The gateway encountered an internal error.")
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
    _set_error_code(request, "invalid_request")
    _emit_validation_result(request, "rejected", 400, "invalid_request")
    record_error(request, "invalid_request", 400, "The request body or headers are invalid.")
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
