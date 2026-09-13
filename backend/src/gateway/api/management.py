"""Standalone management application boundary.

This module intentionally contains no gateway business routes, middleware,
authentication, dependency clients, or listener startup. It only retains the
read-only metrics dependencies needed by a future management endpoint.
"""

from collections.abc import Callable
import hmac

from fastapi import Depends, FastAPI, Request
from starlette.responses import PlainTextResponse, Response

from gateway.application.management_auth import (
    AuthorizationResult,
    ManagementIdentity,
    MetricsManagementAuthorizer,
)
from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import MetricsExporter
from gateway.config.settings import Settings, get_settings

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_UNAVAILABLE = "metrics temporarily unavailable"
_AUTH_REQUIRED = "management authentication required"
_FORBIDDEN = "management access forbidden"
_AUTH_UNAVAILABLE = "management authentication unavailable"


class ManagementAuthenticationConfigurationError(Exception):
    pass


def _token_identity(request: Request, settings: Settings) -> ManagementIdentity:
    configured = settings.metrics_operator_token
    if configured is None or not configured.get_secret_value():
        raise ManagementAuthenticationConfigurationError
    header = request.headers.get("authorization")
    if header is None:
        return ManagementIdentity(False, auth_mode="token")
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1] or any(char.isspace() for char in parts[1]):
        return ManagementIdentity(False, auth_mode="token")
    if not hmac.compare_digest(parts[1], configured.get_secret_value()):
        return ManagementIdentity(False, auth_mode="token")
    return ManagementIdentity(True, role="metrics_reader", auth_mode="token")


def create_management_app(
    snapshot_source: Callable[[], MetricsSnapshot],
    exporter: MetricsExporter,
    settings: Settings | None = None,
    identity_source: Callable[[Request], ManagementIdentity | None] | None = None,
) -> FastAPI:
    """Create the isolated management application without registering routes."""
    resolved_settings = settings or get_settings()
    app = FastAPI(title="Intelligent LLM Gateway Management")
    app.state.metrics_snapshot_source = snapshot_source
    app.state.metrics_exporter = exporter

    def authorize(request: Request) -> ManagementIdentity | None:
        try:
            if resolved_settings.metrics_auth_mode == "token":
                identity = _token_identity(request, resolved_settings)
            else:
                identity = identity_source(request) if identity_source is not None else None
            decision = MetricsManagementAuthorizer().authorize(identity)
        except ManagementAuthenticationConfigurationError:
            raise
        except Exception as exc:
            raise PermissionError from exc
        if decision.result is AuthorizationResult.UNAUTHORIZED:
            raise PermissionError(_AUTH_REQUIRED)
        if decision.result is AuthorizationResult.FORBIDDEN:
            raise PermissionError(_FORBIDDEN)
        return identity

    @app.exception_handler(ManagementAuthenticationConfigurationError)
    async def authentication_configuration_error(request: Request, exc: ManagementAuthenticationConfigurationError) -> PlainTextResponse:
        _ = request, exc
        return PlainTextResponse(_AUTH_UNAVAILABLE, status_code=503)

    @app.exception_handler(PermissionError)
    async def authorization_error(request: Request, exc: PermissionError) -> PlainTextResponse:
        _ = request
        message = str(exc)
        if message == _FORBIDDEN:
            return PlainTextResponse(_FORBIDDEN, status_code=403)
        return PlainTextResponse(_AUTH_REQUIRED, status_code=401)

    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(authorize)])
    def metrics() -> Response:
        try:
            snapshot = app.state.metrics_snapshot_source()
            rendered = app.state.metrics_exporter.export(snapshot)
            return Response(content=rendered, media_type=_PROMETHEUS_CONTENT_TYPE)
        except Exception:
            return PlainTextResponse(_UNAVAILABLE, status_code=503)

    return app
