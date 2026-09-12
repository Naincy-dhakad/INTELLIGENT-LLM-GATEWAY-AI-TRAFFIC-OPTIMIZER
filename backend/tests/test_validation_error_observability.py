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


def event(collector, name):
    return [e for e in collector.events if e.event_type.value == name]


def test_valid_request_emits_successful_validation_event():
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    validation = event(collector, "gateway_request_validation_result")[0]
    assert response.status_code == 200
    assert validation.attributes == {"outcome": "success", "status_code": 200}
    assert validation.request_id == response.headers["x-request-id"]
    assert "error_code" not in validation.attributes


def test_invalid_request_emits_safe_rejection_and_completion_code():
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "private prompt"}], "unknown": True},
        )
    validation = event(collector, "gateway_request_validation_result")[0]
    completed = event(collector, "gateway_request_completed")[0]
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert validation.attributes == {"outcome": "rejected", "error_code": "invalid_request", "status_code": 400}
    assert completed.attributes["error_code"] == "invalid_request"
    assert validation.request_id == completed.request_id == response.headers["x-request-id"]
    assert "private prompt" not in str(validation.as_dict())
    assert "unknown" not in str(validation.as_dict())


def test_validation_precedes_downstream_events():
    collector = Collector()
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    names = [e.event_type.value for e in collector.events]
    assert names.index("gateway_request_validation_result") < names.index("gateway_classification_completed")
    assert names.index("gateway_request_validation_result") < names.index("gateway_request_completed")


def test_validation_observability_failure_does_not_change_response():
    collector = Collector(fail=True)
    get_settings.cache_clear()
    with TestClient(create_app(collector)) as client:
        response = client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
