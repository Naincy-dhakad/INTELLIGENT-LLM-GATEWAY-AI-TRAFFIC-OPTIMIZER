from dataclasses import dataclass

from fastapi.testclient import TestClient
import pytest

from gateway.api.management import create_management_app
from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import TextMetricsExporter
from gateway.config.settings import Settings
from gateway.infrastructure.management_listener import create_management_listener
from gateway.main import create_app


@dataclass
class RecordingExporter:
    rendered: str = "metric_output\n"
    received: object | None = None
    fail: bool = False

    def export(self, snapshot):
        self.received = snapshot
        if self.fail:
            raise RuntimeError("secret exporter failure")
        return self.rendered


def registered_paths(app):
    return {path for route in app.routes if (path := getattr(route, "path", None)) is not None}


def test_management_app_is_standalone_and_retains_read_only_dependencies():
    source = lambda: MetricsSnapshot()
    exporter = TextMetricsExporter()
    management = create_management_app(source, exporter)

    public = create_app()
    assert management is not public
    assert management.state.metrics_snapshot_source is source
    assert management.state.metrics_exporter is exporter
    paths = registered_paths(management)
    assert "/api/v1/chat" not in paths
    assert "/health" not in paths
    assert "/metrics" in paths
    public_paths = registered_paths(public)
    assert "/metrics" not in public_paths


def test_management_app_has_no_gateway_business_dependencies():
    management = create_management_app(lambda: MetricsSnapshot(), TextMetricsExporter())
    state = management.state
    assert not hasattr(state, "gateway_authenticator")
    assert not hasattr(state, "rate_limiter")
    assert not hasattr(state, "chat_service")
    assert not hasattr(state, "budget_service")
    assert not hasattr(state, "usage_recorder")
    assert not hasattr(state, "provider_registry")


def test_metrics_endpoint_uses_source_and_exporter():
    snapshot = MetricsSnapshot()
    calls = []
    exporter = RecordingExporter()
    management = create_management_app(lambda: calls.append(True) or snapshot, exporter)
    with TestClient(management) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.text == "metric_output\n"
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert calls == [True]
    assert exporter.received is snapshot


@pytest.mark.parametrize("failure", ["source", "exporter"])
def test_metrics_endpoint_failures_are_safe(failure):
    exporter = RecordingExporter(fail=failure == "exporter")
    source = (lambda: (_ for _ in ()).throw(RuntimeError("secret source failure"))) if failure == "source" else lambda: MetricsSnapshot()
    management = create_management_app(source, exporter)
    with TestClient(management) as client:
        response = client.get("/metrics")
    assert response.status_code == 503
    assert response.text == "metrics temporarily unavailable"
    assert "secret" not in response.text


def test_management_listener_is_disabled_unless_both_flags_are_enabled():
    source = lambda: MetricsSnapshot()
    exporter = TextMetricsExporter()
    disabled = Settings(_env_file=None)
    assert create_management_listener(disabled, source, exporter) is None
    assert create_management_listener(disabled.model_copy(update={"metrics_enabled": True}), source, exporter) is None
    assert create_management_listener(disabled.model_copy(update={"metrics_management_enabled": True}), source, exporter) is None

    enabled = disabled.model_copy(update={
        "metrics_enabled": True,
        "metrics_management_enabled": True,
        "metrics_bind_host": "127.0.0.2",
        "metrics_bind_port": 9191,
    })
    spec = create_management_listener(enabled, source, exporter)
    assert spec is not None
    assert spec.host == "127.0.0.2"
    assert spec.port == 9191
    assert "/metrics" in registered_paths(spec.app)
