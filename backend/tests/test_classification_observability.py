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
            raise RuntimeError("classification telemetry unavailable")
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


def classification_events(collector):
    return [event for event in collector.events if event.event_type.value == "gateway_classification_completed"]


def test_classification_event_contains_normalized_result_and_correlates():
    collector = Collector()
    with make_client(collector) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "Write code in Python"}]},
        )
    event = classification_events(collector)[0]
    assert event.request_id == response.headers["x-request-id"]
    assert event.attributes["category"] == response.json()["routing"]["category"]
    assert event.attributes["complexity_level"] == response.json()["routing"]["complexity_level"]
    assert event.attributes["complexity_score"] == response.json()["routing"]["complexity_score"]
    assert event.attributes["policy_version"] == "classification-v1"


def test_classification_event_is_ordered_after_auth_and_rate_limit():
    collector = Collector()
    with make_client(collector) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    names = [event.event_type.value for event in collector.events]
    assert names.index("gateway_authentication_result") < names.index("gateway_rate_limit_result")
    assert names.index("gateway_rate_limit_result") < names.index("gateway_classification_completed")
    assert names.index("gateway_classification_completed") < names.index("gateway_request_completed")


def test_classification_event_has_no_prompt_or_sensitive_data():
    collector = Collector()
    prompt = "private prompt with secret-token"
    with make_client(collector) as client:
        client.post("/api/v1/chat", json={"messages": [{"role": "user", "content": prompt}]})
    payload = classification_events(collector)[0].as_dict()
    assert set(payload) == {"event", "request_id", "category", "complexity_level", "complexity_score", "policy_version"}
    assert prompt not in str(payload)
    assert "token" not in payload
    assert "authorization" not in payload


def test_classification_observability_failure_does_not_change_response():
    collector = Collector(fail=True)
    with make_client(collector) as client:
        response = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
