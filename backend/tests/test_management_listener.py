from fastapi import FastAPI

from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import TextMetricsExporter
from gateway.config.settings import Settings
from gateway.infrastructure.management_listener import create_management_listener
from gateway.infrastructure.management_server import create_management_server
from gateway.infrastructure.server_runtime import create_gateway_runtime


def settings(metrics=False, management=False, host="127.0.0.1", port=9090):
    return Settings(
        _env_file=None,
        metrics_enabled=metrics,
        metrics_management_enabled=management,
        metrics_bind_host=host,
        metrics_bind_port=port,
    )


def test_listener_truth_table_and_defaults():
    source = lambda: MetricsSnapshot()
    exporter = TextMetricsExporter()
    assert create_management_listener(settings(), source, exporter) is None
    assert create_management_listener(settings(True, False), source, exporter) is None
    assert create_management_listener(settings(False, True), source, exporter) is None
    spec = create_management_listener(settings(True, True), source, exporter)
    assert spec is not None
    assert (spec.host, spec.port) == ("127.0.0.1", 9090)


def test_listener_uses_configured_bind_values():
    spec = create_management_listener(settings(True, True, "127.0.0.2", 9191), lambda: MetricsSnapshot(), TextMetricsExporter())
    assert spec is not None
    assert spec.host == "127.0.0.2"
    assert spec.port == 9191


def test_management_server_construction_is_injected_and_does_not_bind():
    app = FastAPI()
    spec = type("Spec", (), {"app": app, "host": "127.0.0.1", "port": 9090})()
    configs = []

    def factory(config):
        configs.append(config)
        return "server"

    assert create_management_server(spec, server_factory=factory) == "server"
    assert configs[0].host == "127.0.0.1"
    assert configs[0].port == 9090
    assert create_management_server(None, server_factory=factory) is None


def test_runtime_shares_metrics_state_and_keeps_public_app_separate():
    runtime = create_gateway_runtime(settings())
    assert runtime.management_spec is None
    assert "/metrics" not in {getattr(route, "path", None) for route in runtime.public_app.routes}
    runtime.metrics_state.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    assert runtime.metrics_state.snapshot().counters[0].value == 1


def test_runtime_builds_management_server_only_when_enabled():
    runtime = create_gateway_runtime(settings(True, True))
    assert runtime.management_spec is not None
    assert runtime.management_server is not None
    assert runtime.management_spec.host == "127.0.0.1"
    assert runtime.management_spec.port == 9090
