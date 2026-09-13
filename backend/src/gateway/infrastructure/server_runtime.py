"""Explicit same-process gateway/management composition boundary."""

from dataclasses import dataclass

from fastapi import FastAPI

from gateway.application.metrics import MetricsSnapshot
from gateway.application.observability import InMemoryMetrics
from gateway.config.settings import Settings, get_settings
from gateway.infrastructure.management_listener import ManagementListenerSpec, create_management_listener
from gateway.infrastructure.management_server import create_management_server
from gateway.infrastructure.observability.prometheus import PrometheusMetricsExporter
from gateway.main import create_app


class SharedMetricsState:
    """Shared event/metric sink with a read-only snapshot source."""

    def __init__(self) -> None:
        self._metrics = InMemoryMetrics()

    def emit(self, _event) -> None:
        pass

    def increment(self, metric, labels=None, value=1) -> None:
        self._metrics.increment(metric, labels, value)

    def observe(self, metric, value, labels=None) -> None:
        self._metrics.observe(metric, value, labels)

    def snapshot(self) -> MetricsSnapshot:
        return self._metrics.snapshot()


@dataclass(frozen=True)
class GatewayRuntime:
    public_app: FastAPI
    metrics_state: SharedMetricsState
    management_spec: ManagementListenerSpec | None
    management_server: object | None


def create_gateway_runtime(settings: Settings | None = None) -> GatewayRuntime:
    """Build both isolated applications without starting a listener."""
    resolved = settings or get_settings()
    metrics_state = SharedMetricsState()
    public_app = create_app(observability=metrics_state)
    management_spec = create_management_listener(
        resolved,
        metrics_state.snapshot,
        PrometheusMetricsExporter(),
    )
    management_server = create_management_server(management_spec)
    return GatewayRuntime(public_app, metrics_state, management_spec, management_server)
