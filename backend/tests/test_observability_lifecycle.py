from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from gateway.application.observability_events import ObservabilityEvent
from gateway.config.settings import get_settings
from gateway.main import create_app


@dataclass
class EventCollector:
    events: list[ObservabilityEvent] = field(default_factory=list)
    fail: bool = False

    def emit(self, event: ObservabilityEvent) -> None:
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


def make_client(collector):
    get_settings.cache_clear()
    return TestClient(create_app(collector))


def test_success_emits_correlated_start_and_one_completion():
    collector = EventCollector()
    with make_client(collector) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    started = [event for event in collector.events if event.event_type.value == "gateway_request_started"]
    completed = [event for event in collector.events if event.event_type.value == "gateway_request_completed"]
    assert len(started) == 1
    assert len(completed) == 1
    assert started[0].request_id == completed[0].request_id == response.headers["x-request-id"]
    assert completed[0].attributes["status_code"] == 200
    assert completed[0].attributes["outcome"] == "success"
    assert completed[0].attributes["latency_ms"] >= 0


def test_start_event_has_only_safe_lifecycle_fields():
    collector = EventCollector()
    with make_client(collector) as client:
        client.post(
            "/api/v1/chat",
            headers={"X-API-Key": "not-used-when-auth-disabled", "Authorization": "Bearer secret"},
            json={"messages": [{"role": "user", "content": "private prompt"}]},
        )
    started = collector.events[0].as_dict()
    assert "private prompt" not in str(started)
    assert "not-used-when-auth-disabled" not in str(started)
    assert "Authorization" not in str(started)
    assert set(started) <= {"event", "request_id", "route", "method", "environment"}


def test_sink_failure_does_not_change_gateway_behavior():
    collector = EventCollector(fail=True)
    with make_client(collector) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_completion_is_not_duplicated_when_sink_is_healthy():
    collector = EventCollector()
    with make_client(collector) as client:
        response = client.get("/health")
    assert response.status_code == 200
    completed = [event for event in collector.events if event.event_type.value == "gateway_request_completed"]
    assert len(completed) == 1
