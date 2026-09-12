from dataclasses import dataclass, field

from fastapi.testclient import TestClient
import pytest

from gateway.application.observability_events import ObservabilityEvent
from gateway.config.settings import get_settings
from gateway.main import create_app


@dataclass
class Collector:
    events: list[ObservabilityEvent] = field(default_factory=list)
    fail: bool = False

    def emit(self, event):
        if self.fail:
            raise RuntimeError("sink unavailable")
        self.events.append(event)

    def increment(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def route_events(collector):
    return [e for e in collector.events if e.event_type.value == "gateway_routing_decision"]


def request_body():
    return {"messages": [{"role": "user", "content": "hello"}]}


def test_successful_routing_event_uses_existing_decision(monkeypatch):
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json=request_body())
    event = route_events(collector)[0]
    routing = response.json()["routing"]
    assert response.status_code == 200
    assert event.request_id == response.headers["x-request-id"]
    assert event.attributes["outcome"] == "success"
    assert event.attributes["objective"] == "balanced"
    assert event.attributes["policy_version"] == routing["policy_version"]
    assert event.attributes["provider_id"] == routing["provider_id"]
    assert event.attributes["model_id"] == routing["model_id"]
    assert event.attributes["reason_code"] == routing["decision_reason"]
    assert event.attributes["estimated_cost_usd"] == routing["estimated_cost_usd"]
    assert event.attributes["estimated_latency_ms"] == routing["estimated_latency_ms"]
    assert event.attributes["health_score"] == routing["health_score"]


def test_routing_failure_preserves_existing_error_and_emits_safe_event(monkeypatch):
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post(
            "/api/v1/chat",
            json={**request_body(), "provider": "missing-provider"},
        )
    event = route_events(collector)[0]
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_provider"
    assert response.json()["error"]["retryable"] is False
    assert event.attributes["outcome"] == "failure"
    assert event.attributes["reason_code"] == "unknown_provider"
    assert "missing-provider" not in str(event.as_dict())


def test_routing_event_order_and_request_id_correlation():
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json=request_body())
    names = [e.event_type.value for e in collector.events]
    assert names.index("gateway_classification_completed") < names.index("gateway_routing_decision")
    assert names.index("gateway_routing_decision") < names.index("gateway_request_completed")
    request_id = response.headers["x-request-id"]
    assert all(e.request_id == request_id for e in collector.events)


def test_routing_observability_failure_does_not_change_response():
    collector = Collector(fail=True)
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json=request_body())
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
