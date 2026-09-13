"""Conditional management-listener boundary.

This module decides whether a future internal server should be started. It does
not start a server as part of public gateway application creation.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gateway.api.management import create_management_app
from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import MetricsExporter
from gateway.config.settings import Settings


@dataclass(frozen=True)
class ManagementListenerSpec:
    host: str
    port: int
    app: object


def create_management_listener(
    settings: Settings,
    snapshot_source: Callable[[], MetricsSnapshot],
    exporter: MetricsExporter,
) -> ManagementListenerSpec | None:
    """Build a listener specification only when both exposure flags are enabled."""
    if not settings.metrics_enabled or not settings.metrics_management_enabled:
        return None
    return ManagementListenerSpec(
        host=settings.metrics_bind_host,
        port=settings.metrics_bind_port,
        app=create_management_app(snapshot_source, exporter),
    )
