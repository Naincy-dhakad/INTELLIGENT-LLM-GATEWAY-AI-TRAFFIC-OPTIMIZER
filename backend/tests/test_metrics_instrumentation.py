from dataclasses import dataclass, field

from fastapi.testclient import TestClient
import pytest

from gateway.application.observability import InMemoryMetrics, record_event_metrics
from gateway.application.observability_events import EventType, ObservabilityEvent
from gateway.config.settings import get_settings
from gateway.main import create_app


@dataclass
class MetricsCollector:
    metrics: InMemoryMetrics = field(default_factory=InMemoryMetrics)
    events: list[ObservabilityEvent] = field(default_factory=list)
    fail_metrics: bool = False

    def emit(self, event):
        self.events.append(event)

    def increment(self, metric, labels=None, value=1):
        if self.fail_metrics:
            raise RuntimeError("metrics unavailable")
        self.metrics.increment(metric, labels, value)

    def observe(self, metric, value, labels=None):
        if self.fail_metrics:
            raise RuntimeError("metrics unavailable")
        self.metrics.observe(metric, value, labels)


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def counts_for(collector, metric):
    return counts_for_metrics(collector.metrics, metric)


def counts_for_metrics(metrics, metric):
    return {labels: value for (name, labels), value in metrics.counts().items() if name == metric}


def test_successful_request_instruments_lifecycle_and_application_metrics():
    collector = MetricsCollector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 200
    for metric in (
        "gateway_requests_total",
        "gateway_authentication_results_total",
        "gateway_rate_limit_results_total",
        "gateway_classification_total",
        "gateway_routing_decisions_total",
        "gateway_provider_attempts_total",
        "gateway_validation_results_total",
    ):
        assert sum(counts_for(collector, metric).values()) == 1
    assert collector.metrics.observations()
    assert any(name == "gateway_request_duration_seconds" for name, _, _ in collector.metrics.observations())
    assert any(name == "gateway_provider_attempt_duration_seconds" for name, _, _ in collector.metrics.observations())


def test_validation_and_normalized_error_metrics_are_safe():
    collector = MetricsCollector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "private prompt"}], "unknown": True})
    assert response.status_code == 400
    assert sum(counts_for(collector, "gateway_validation_results_total").values()) == 1
    assert sum(counts_for(collector, "gateway_errors_total").values()) == 1
    for labels in counts_for(collector, "gateway_errors_total"):
        assert all(key not in dict(labels) for key in ("request_id", "prompt", "api_key", "error_message"))


def test_authentication_metric_records_failure_without_sensitive_labels(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", "safe-key")
    get_settings.cache_clear()
    collector = MetricsCollector()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", headers={"X-API-Key": "wrong-secret"}, json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 401
    labels = counts_for(collector, "gateway_authentication_results_total")
    assert any(dict(item).get("outcome") == "failure" for item in labels)
    assert "wrong-secret" not in str(collector.metrics.counts())


def test_decision_retry_and_fallback_events_map_to_counters():
    metrics = InMemoryMetrics()
    record_event_metrics(metrics, EventType.BUDGET_DECISION, {"outcome": "success", "objective": "budget", "policy_version": "budget-v1"})
    record_event_metrics(metrics, EventType.RETRY_SCHEDULED, {"provider_id": "primary", "model_id": "model", "attempt_number": 1, "error_category": "timeout"})
    record_event_metrics(metrics, EventType.FALLBACK_SELECTED, {"from_provider_id": "primary", "to_provider_id": "fallback", "attempt_number": 2, "reason_code": "timeout"})
    assert sum(counts_for_metrics(metrics, "gateway_budget_decisions_total").values()) == 1
    assert sum(counts_for_metrics(metrics, "gateway_retries_scheduled_total").values()) == 1
    assert sum(counts_for_metrics(metrics, "gateway_fallbacks_selected_total").values()) == 1


def test_metrics_failure_does_not_change_request_behavior():
    collector = MetricsCollector(fail_metrics=True)
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
