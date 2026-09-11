from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from gateway.application.rate_limiting import RateLimitResult, RateLimitUnavailable
from gateway.config.settings import get_settings
from gateway.main import create_app


BODY = {"messages": [{"role": "user", "content": "hello"}]}


class FakeRateLimiter:
    def __init__(self, limit: int = 2):
        self.limit = limit
        self.calls = []
        self._counts = {}
        self._lock = threading.Lock()
        self.unavailable = False

    def check_and_consume(self, principal_id, limit, window_seconds):
        self.calls.append((principal_id, limit, window_seconds))
        if self.unavailable:
            raise RateLimitUnavailable
        with self._lock:
            count = self._counts.get(principal_id, 0) + 1
            self._counts[principal_id] = count
        allowed = count <= self.limit
        return RateLimitResult(
            allowed=allowed,
            count=count,
            remaining=max(0, self.limit - count),
            retry_after_seconds=7 if not allowed else None,
        )


def make_app(monkeypatch, *, keys="first-key,second-key", limit=2):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_API_KEYS", keys)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", str(limit))
    get_settings.cache_clear()
    app = create_app()
    app.state.rate_limiter = FakeRateLimiter(limit)
    return app


@pytest.fixture(autouse=True)
def clear_settings():
    yield
    get_settings.cache_clear()


def test_disabled_rate_limiting_does_not_require_redis(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/chat", json=BODY).status_code == 200


def test_rate_limiting_requires_authentication(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="requires GATEWAY_AUTH_ENABLED"):
        create_app()


def test_authenticated_requests_consume_quota_and_reject_after_limit(monkeypatch):
    app = make_app(monkeypatch, limit=2)
    with TestClient(app) as client:
        headers = {"X-API-Key": "first-key"}
        assert client.post("/api/v1/chat", json=BODY, headers=headers).status_code == 200
        assert client.post("/api/v1/chat", json=BODY, headers=headers).status_code == 200
        response = client.post("/api/v1/chat", json=BODY, headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.json()["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "7"


def test_different_principals_have_independent_quotas(monkeypatch):
    app = make_app(monkeypatch, limit=1)
    with TestClient(app) as client:
        first = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "first-key"})
        second = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "second-key"})
    assert first.status_code == second.status_code == 200
    assert app.state.rate_limiter.calls[0][0] != app.state.rate_limiter.calls[1][0]
    assert "first-key" not in app.state.rate_limiter.calls[0][0]


def test_authentication_failure_does_not_call_rate_limiter(monkeypatch):
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert app.state.rate_limiter.calls == []


def test_rate_limited_request_does_not_reach_service(monkeypatch):
    app = make_app(monkeypatch, limit=1)
    app.state.rate_limiter = FakeRateLimiter(0)
    called = False

    class SpyService:
        def complete(self, *_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("rate-limited requests must not execute")

    app.state.chat_service = SpyService()
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "first-key"})
    assert response.status_code == 429
    assert called is False


def test_redis_unavailable_fails_closed_and_does_not_reach_service(monkeypatch):
    app = make_app(monkeypatch)
    app.state.rate_limiter.unavailable = True
    called = False

    class SpyService:
        def complete(self, *_args, **_kwargs):
            nonlocal called
            called = True

    app.state.chat_service = SpyService()
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "first-key"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rate_limit_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "redis" not in response.text.lower()
    assert called is False


def test_health_is_public_and_does_not_consume_quota(monkeypatch):
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert app.state.rate_limiter.calls == []


def test_fixed_window_fake_is_atomic_under_concurrency(monkeypatch):
    app = make_app(monkeypatch, limit=3)
    with TestClient(app) as client:
        def send(_):
            return client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "first-key"}).status_code

        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(send, range(10)))
    assert statuses.count(200) == 3
    assert statuses.count(429) == 7
