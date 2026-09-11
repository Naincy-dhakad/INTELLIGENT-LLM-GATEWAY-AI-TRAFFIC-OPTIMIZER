from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from gateway.application.rate_limiting import RateLimitResult
from gateway.application.usage_tracking import UsageRecorder
from gateway.config.settings import get_settings
from gateway.domain.provider import ProviderError, ProviderErrorCategory
from gateway.main import create_app


BODY = {"messages": [{"role": "user", "content": "do not persist this prompt"}]}


class FakeRepository:
    def __init__(self):
        self.records = []
        self.fail = False

    def record_usage(self, record):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.records.append(record)


def make_app(monkeypatch, repository=None, *, auth_enabled=False):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", str(auth_enabled).lower())
    monkeypatch.setenv("GATEWAY_API_KEYS", "usage-key" if auth_enabled else "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/gateway")
    get_settings.cache_clear()
    app = create_app()
    repository = repository or FakeRepository()
    app.state.usage_recorder = UsageRecorder(repository, True)
    return app, repository


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def test_usage_disabled_preserves_behavior_without_database(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/chat", json=BODY).status_code == 200


def test_successful_request_creates_one_normalized_record(monkeypatch):
    app, repository = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY)
    assert response.status_code == 200
    assert len(repository.records) == 1
    record = repository.records[0]
    assert record.request_id == response.json()["request_id"]
    assert record.outcome == "success"
    assert record.provider_id == "phase3-mock"
    assert record.model_id == "phase3-mock-model"
    assert record.attempt_count == 1
    assert record.classification_category is not None
    assert record.actual_gateway_latency_ms is not None
    assert record.estimated_cost_usd is None or isinstance(record.estimated_cost_usd, Decimal)
    assert "do not persist this prompt" not in repr(record)


def test_authenticated_success_persists_only_hashed_principal(monkeypatch):
    app, repository = make_app(monkeypatch, auth_enabled=True)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "usage-key"})
    assert response.status_code == 200
    assert repository.records[0].principal_id is not None
    assert repository.records[0].principal_id != "usage-key"
    assert "usage-key" not in repr(repository.records[0])


def test_provider_failure_preserves_original_error_and_records_normalized_metadata(monkeypatch):
    app, repository = make_app(monkeypatch)

    class FailingService:
        def complete(self, _body, context):
            context.execution_metadata.update(
                attempt_count=1, provider_id="phase3-mock", model_id="phase3-mock-model"
            )
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "native provider detail")

    app.state.chat_service = FailingService()
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY)
    assert response.status_code == 504
    assert len(repository.records) == 1
    record = repository.records[0]
    assert record.outcome == "failed"
    assert record.error_code == "gateway_timeout"
    assert record.error_category == "timeout"
    assert record.attempt_count == 1
    assert record.provider_id == "phase3-mock"
    assert "native provider detail" not in repr(record)


def test_persistence_failure_does_not_change_success_response(monkeypatch):
    repository = FakeRepository()
    repository.fail = True
    app, _ = make_app(monkeypatch, repository)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY)
    assert response.status_code == 200


def test_rate_limited_request_records_zero_attempts_without_provider_fields(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", "usage-key")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("USAGE_TRACKING_ENABLED", "true")
    get_settings.cache_clear()
    repository = FakeRepository()
    app = create_app()
    app.state.usage_recorder = UsageRecorder(repository, True)

    class RejectingLimiter:
        def check_and_consume(self, *_args):
            return RateLimitResult(False, 61, 0, 3)

    app.state.rate_limiter = RejectingLimiter()
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "usage-key"})
    assert response.status_code == 429
    assert repository.records[0].outcome == "rate_limited"
    assert repository.records[0].attempt_count == 0
    assert repository.records[0].provider_id is None


def test_validation_and_auth_failures_store_no_request_content(monkeypatch):
    app, repository = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"messages": []})
    assert response.status_code == 400
    assert len(repository.records) == 1
    assert repository.records[0].outcome == "validation_failed"
    assert repository.records[0].provider_id is None
    assert "messages" not in repr(repository.records[0])


def test_usage_model_has_nullable_provider_and_token_fields():
    from gateway.infrastructure.database.models import GatewayUsageRecordModel

    columns = GatewayUsageRecordModel.__table__.columns
    assert columns["provider_id"].nullable is True
    assert columns["model_id"].nullable is True
    assert columns["input_tokens"].nullable is True
    from sqlalchemy import Numeric
    assert isinstance(columns["estimated_cost_usd"].type, Numeric)
    assert "prompt" not in {column.name for column in columns}
    assert "completion" not in {column.name for column in columns}
