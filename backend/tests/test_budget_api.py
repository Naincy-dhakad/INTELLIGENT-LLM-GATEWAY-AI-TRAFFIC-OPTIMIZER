from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from gateway.application.budget import BudgetService
from gateway.config.settings import get_settings
from gateway.main import create_app


class SpendRepository:
    def __init__(self, spend=Decimal("0")):
        self.spend = Decimal(spend)

    def get_accumulated_spend(self, principal_id):
        assert principal_id
        return self.spend

    def record_usage(self, record):
        pass


def app_with_spend(monkeypatch, spend, usage_enabled=True):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", "budget-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", str(usage_enabled).lower())
    get_settings.cache_clear()
    app = create_app()
    app.state.budget_service = BudgetService(
        SpendRepository(spend) if usage_enabled else None, usage_enabled
    )
    return app


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def body(budget):
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "routing": {"objective": "budget", "max_budget_usd": str(budget)},
    }


def test_budget_route_uses_authenticated_principal_history(monkeypatch):
    app = app_with_spend(monkeypatch, "0")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.01"), headers={"X-API-Key": "budget-key"})
    assert response.status_code == 200
    assert response.json()["routing"]["policy_version"].endswith("budget-v1")


def test_exhausted_budget_is_safe_policy_error(monkeypatch):
    app = app_with_spend(monkeypatch, "1")
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.50"), headers={"X-API-Key": "budget-key"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert response.json()["error"]["retryable"] is False


def test_budget_history_unavailable_fails_safely(monkeypatch):
    app = app_with_spend(monkeypatch, "0", usage_enabled=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=body("0.01"), headers={"X-API-Key": "budget-key"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "budget_unavailable"
    assert "database" not in response.text.lower()
