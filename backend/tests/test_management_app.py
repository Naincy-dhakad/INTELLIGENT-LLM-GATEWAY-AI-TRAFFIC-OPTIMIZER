from gateway.api.management import create_management_app
from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import TextMetricsExporter
from gateway.main import create_app


def test_management_app_is_standalone_and_retains_read_only_dependencies():
    source = lambda: MetricsSnapshot()
    exporter = TextMetricsExporter()
    management = create_management_app(source, exporter)

    assert management is not create_app()
    assert management.state.metrics_snapshot_source is source
    assert management.state.metrics_exporter is exporter
    paths = {route.path for route in management.routes}
    assert "/api/v1/chat" not in paths
    assert "/health" not in paths
    assert "/metrics" not in paths


def test_management_app_has_no_gateway_business_dependencies():
    management = create_management_app(lambda: MetricsSnapshot(), TextMetricsExporter())
    state = vars(management.state)
    assert "gateway_authenticator" not in state
    assert "rate_limiter" not in state
    assert "chat_service" not in state
    assert "budget_service" not in state
    assert "usage_recorder" not in state
    assert "provider_registry" not in state
