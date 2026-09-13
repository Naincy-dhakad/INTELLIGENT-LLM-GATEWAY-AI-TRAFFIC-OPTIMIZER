"""Standalone management application boundary.

This module intentionally contains no gateway business routes, middleware,
authentication, dependency clients, or listener startup. It only retains the
read-only metrics dependencies needed by a future management endpoint.
"""

from collections.abc import Callable

from fastapi import FastAPI
from starlette.responses import PlainTextResponse, Response

from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import MetricsExporter

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_UNAVAILABLE = "metrics temporarily unavailable"


def create_management_app(
    snapshot_source: Callable[[], MetricsSnapshot],
    exporter: MetricsExporter,
) -> FastAPI:
    """Create the isolated management application without registering routes."""
    app = FastAPI(title="Intelligent LLM Gateway Management")
    app.state.metrics_snapshot_source = snapshot_source
    app.state.metrics_exporter = exporter

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        try:
            snapshot = app.state.metrics_snapshot_source()
            rendered = app.state.metrics_exporter.export(snapshot)
            return Response(content=rendered, media_type=_PROMETHEUS_CONTENT_TYPE)
        except Exception:
            return PlainTextResponse(_UNAVAILABLE, status_code=503)

    return app
