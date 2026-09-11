import pytest
from fastapi.testclient import TestClient

from gateway.config.settings import get_settings
from gateway.main import create_app


BODY = {"messages": [{"role": "user", "content": "hello"}]}


@pytest.fixture(autouse=True)
def clear_settings_cache():
    yield
    get_settings.cache_clear()


def make_client(monkeypatch, *, enabled: bool, keys: str = ""):
    monkeypatch.setenv("GATEWAY_AUTH_ENABLED", str(enabled).lower())
    monkeypatch.setenv("GATEWAY_API_KEYS", keys)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_authentication_disabled_allows_existing_request(monkeypatch):
    with make_client(monkeypatch, enabled=False) as client:
        response = client.post("/api/v1/chat", json=BODY)
    assert response.status_code == 200


def test_missing_and_invalid_keys_use_safe_401_envelope(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="local-development-key") as client:
        missing = client.post("/api/v1/chat", json=BODY)
        invalid = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "wrong"})
    for response in (missing, invalid):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
        assert response.json()["error"]["retryable"] is False
        assert response.json()["error"]["details"] is None
        assert response.headers["x-request-id"].startswith("req_")
        assert "local-development-key" not in response.text


def test_valid_key_succeeds_without_echoing_or_forwarding_key(monkeypatch):
    key = "local-development-key"
    with make_client(monkeypatch, enabled=True, keys=key) as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": key})
    assert response.status_code == 200
    assert key not in response.text
    assert "X-API-Key" not in response.text


def test_multiple_keys_and_empty_key_handling(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="first-key, second-key") as client:
        first = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "first-key"})
        second = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "second-key"})
        empty = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": ""})
    assert first.status_code == second.status_code == 200
    assert empty.status_code == 401


def test_health_remains_public_when_authentication_is_enabled(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="health-key") as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_invalid_key_never_reaches_chat_service(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="real-key") as client:
        called = False

        class SpyService:
            def complete(self, *_args, **_kwargs):
                nonlocal called
                called = True
                raise AssertionError("provider execution must not be reached")

        client.app.state.chat_service = SpyService()
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert called is False


def test_authentication_precedes_provider_and_streaming(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="stream-key") as client:
        unauthenticated = client.post("/api/v1/chat", json={**BODY, "stream": True})
        authenticated = client.post(
            "/api/v1/chat", json={**BODY, "stream": True}, headers={"X-API-Key": "stream-key"}
        )
    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 501
    assert authenticated.json()["error"]["code"] == "streaming_not_supported"


def test_no_configured_key_cannot_authenticate(monkeypatch):
    with make_client(monkeypatch, enabled=True, keys="") as client:
        response = client.post("/api/v1/chat", json=BODY, headers={"X-API-Key": "anything"})
    assert response.status_code == 401
