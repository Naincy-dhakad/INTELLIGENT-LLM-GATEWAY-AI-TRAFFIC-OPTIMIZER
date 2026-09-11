from decimal import Decimal
from dataclasses import dataclass, field

from fastapi.testclient import TestClient
import pytest

from gateway.application.budget import BudgetService
from gateway.application.observability_events import ObservabilityEvent
from gateway.config.settings import get_settings
from gateway.main import create_app


class SpendRepository:
    def __init__(self, spend):
        self.spend = Decimal(spend)

    def get_accumulated_spend(self, principal_id):
        assert principal_id
        return self.spend


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


def app_with_budget(monkeypatch, collector, spend):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", "budget-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app(collector)
    app.state.budget_service = BudgetService(SpendRepository(spend), True)
    return app


def body(budget="0.50"):
    return {
        "messages": [{"role": "user", "content": "private budget prompt"}],
        "routing": {"objective": "budget", "max_budget_usd": budget},
    }


def budget_events(collector):
    return [e for e in collector.events if e.event_type.value == "gateway_budget_decision"]


def test_budget_success_event_is_normalized_and_correlated(monkeypatch):
    collector = Collector()
    app = app_with_budget(monkeypatch, collector, "0")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.50"), headers={"X-API-Key": "budget-key"})
    event = budget_events(collector)[0]
    assert response.status_code == 200
    assert event.request_id == response.headers["x-request-id"]
    assert event.attributes == {
        "outcome": "success",
        "objective": "budget",
        "policy_version": "classification-cost-latency-quality-budget-v1",
    }


def test_budget_exhausted_behavior_and_event_are_unchanged(monkeypatch):
    collector = Collector()
    app = app_with_budget(monkeypatch, collector, "1")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.50"), headers={"X-API-Key": "budget-key"})
    event = budget_events(collector)[0]
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert response.json()["error"]["retryable"] is False
    assert event.attributes["outcome"] == "failure"
    assert event.attributes["error_code"] == "budget_exhausted"


def test_budget_unavailable_behavior_and_event_are_unchanged(monkeypatch):
    collector = Collector()
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", "budget-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app(collector)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body(), headers={"X-API-Key": "budget-key"})
    event = budget_events(collector)[0]
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "budget_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert event.attributes["error_code"] == "budget_unavailable"
    assert "database" not in str(event.as_dict()).lower()


def test_budget_observability_failure_does_not_change_behavior(monkeypatch):
    collector = Collector(fail=True)
    app = app_with_budget(monkeypatch, collector, "1")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.50"), headers={"X-API-Key": "budget-key"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert "private budget prompt" not in response.text
