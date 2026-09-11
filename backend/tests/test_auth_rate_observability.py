from dataclasses import dataclass, field

from fastapi.testclient import TestClient
import pytest

from gateway.application.rate_limiting import RateLimitResult, RateLimitUnavailable
from gateway.application.observability_events import ObservabilityEvent
from gateway.config.settings import get_settings
from gateway.main import create_app


@dataclass
class Collector:
    events: list[ObservabilityEvent] = field(default_factory=list)
    fail: bool = False

    def emit(self, event):
        if self.fail:
            raise RuntimeError("sink failed")
        self.events.append(event)

    def increment(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


class FakeLimiter:
    def __init__(self, result):
        self.result = result

    def check_and_consume(self, *_args):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def make_app(monkeypatch, collector, *, auth=False, rate=False):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", str(auth).lower())
    monkeypatch.setenv("GATEWAY_API_KEYS", "safe-test-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", str(rate).lower())
    get_settings.cache_clear()
    return create_app(collector)


def chat(client, headers=None):
    return client.post(
        "/api/v1/chat",
        headers=headers or {},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )


def events(collector, event_name):
    return [event for event in collector.events if event.event_type.value == event_name]


def test_authentication_success_event_is_safe_and_correlated(monkeypatch):
    collector = Collector()
    app = make_app(monkeypatch, collector, auth=True)
    with TestClient(app) as client:
        response = chat(client, {"X-API-Key": "safe-test-key"})
    auth_events = events(collector, "gateway_authentication_result")
    assert response.status_code == 200
    assert len(auth_events) == 1
    assert auth_events[0].attributes["outcome"] == "success"
    assert auth_events[0].request_id == response.headers["x-request-id"]
    assert "safe-test-key" not in str(auth_events[0].as_dict())
    assert "principal_id" not in auth_events[0].attributes


def test_authentication_failure_event_does_not_leak_secret(monkeypatch):
    collector = Collector()
    app = make_app(monkeypatch, collector, auth=True)
    with TestClient(app) as client:
        response = chat(client, {"X-API-Key": "wrong-secret"})
    auth_events = events(collector, "gateway_authentication_result")
    assert response.status_code == 401
    assert auth_events[0].attributes["outcome"] == "failure"
    assert auth_events[0].attributes["status_code"] == 401
    assert "wrong-secret" not in str(auth_events[0].as_dict())
    assert not events(collector, "gateway_rate_limit_result")


def test_disabled_auth_and_rate_limit_emit_bounded_outcomes(monkeypatch):
    collector = Collector()
    app = make_app(monkeypatch, collector, auth=False, rate=False)
    with TestClient(app) as client:
        response = chat(client)
    assert response.status_code == 200
    assert events(collector, "gateway_authentication_result")[0].attributes["outcome"] == "disabled"
    assert events(collector, "gateway_rate_limit_result")[0].attributes["outcome"] == "disabled"


def test_rate_limit_allowed_rejected_and_unavailable_events(monkeypatch):
    for result, expected, status in [
        (RateLimitResult(True, 1, 1), "allowed", 200),
        (RateLimitResult(False, 2, 0, 9), "rejected", 429),
        (RateLimitUnavailable(), "unavailable", 503),
    ]:
        collector = Collector()
        app = make_app(monkeypatch, collector, auth=True, rate=True)
        app.state.rate_limiter = FakeLimiter(result)
        with TestClient(app) as client:
            response = chat(client, {"X-API-Key": "safe-test-key"})
        rate_events = events(collector, "gateway_rate_limit_result")
        assert response.status_code == status
        assert rate_events[0].attributes["outcome"] == expected
        assert "safe-test-key" not in str(rate_events[0].as_dict())
        assert "redis" not in str(rate_events[0].as_dict()).lower()
        if expected == "rejected":
            assert rate_events[0].attributes["retry_after_seconds"] == 9


def test_observability_failure_does_not_change_auth_or_rate_behavior(monkeypatch):
    collector = Collector(fail=True)
    app = make_app(monkeypatch, collector, auth=True, rate=False)
    with TestClient(app) as client:
        response = chat(client, {"X-API-Key": "safe-test-key"})
    assert response.status_code == 200
