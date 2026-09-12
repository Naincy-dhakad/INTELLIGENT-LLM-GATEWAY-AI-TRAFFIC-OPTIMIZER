"""Standalone management application boundary.

This module intentionally contains no gateway business routes, middleware,
authentication, dependency clients, or listener startup. It only retains the
read-only metrics dependencies needed by a future management endpoint.
"""

from collections.abc import Callable

from fastapi import FastAPI

from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import MetricsExporter


def create_management_app(
    snapshot_source: Callable[[], MetricsSnapshot],
    exporter: MetricsExporter,
) -> FastAPI:
    """Create the isolated management application without registering routes."""
    app = FastAPI(title="Intelligent LLM Gateway Management")
    app.state.metrics_snapshot_source = snapshot_source
    app.state.metrics_exporter = exporter
    return app
