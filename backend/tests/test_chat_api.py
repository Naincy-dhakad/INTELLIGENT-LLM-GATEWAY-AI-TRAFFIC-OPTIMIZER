from fastapi.testclient import TestClient

from gateway.domain.provider import ProviderError, ProviderErrorCategory
from gateway.main import create_app


client = TestClient(create_app())
VALID_REQUEST = {"messages": [{"role": "user", "content": "Hello"}]}


def test_chat_returns_normalized_phase3_provider_response() -> None:
    response = client.post("/api/v1/chat", json=VALID_REQUEST)

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == payload["request_id"]
    assert payload["object"] == "chat.response"
    assert payload["status"] == "completed"
    assert payload["message"]["role"] == "assistant"
    assert "Mock response" in payload["message"]["content"]
    assert payload["provider"] == {
        "id": "phase3-mock",
        "model": "phase3-mock-model",
    }
    assert payload["usage"] is None
    assert isinstance(payload["latency_ms"], int)
    assert payload["latency_ms"] >= 0
    assert payload["routing"]["decision_reason"] == (
        'Default provider "phase3-mock" selected because no provider was explicitly requested.'
    )
    assert response.headers["X-Request-ID"] == payload["request_id"]


def test_chat_uses_provider_preference_as_registry_lookup() -> None:
    response = client.post(
        "/api/v1/chat", json={**VALID_REQUEST, "provider": "phase3-mock"}
    )
    assert response.status_code == 200
    assert response.json()["provider"]["id"] == "phase3-mock"


def test_chat_unknown_provider_is_unavailable_without_fallback() -> None:
    response = client.post(
        "/api/v1/chat", json={**VALID_REQUEST, "provider": "missing-provider"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "all_providers_unavailable"


def test_chat_uses_default_timeout() -> None:
    response = client.post("/api/v1/chat", json=VALID_REQUEST)
    assert response.status_code == 200


def test_chat_accepts_timeout_in_body_or_header() -> None:
    assert client.post("/api/v1/chat", json={**VALID_REQUEST, "timeout_ms": 1}).status_code == 200
    assert client.post(
        "/api/v1/chat", json=VALID_REQUEST, headers={"X-Request-Timeout-Ms": "1"}
    ).status_code == 200


def test_chat_accepts_maximum_timeout() -> None:
    response = client.post(
        "/api/v1/chat", json={**VALID_REQUEST, "timeout_ms": 120_000}
    )
    assert response.status_code == 200


def test_chat_rejects_timeout_above_maximum() -> None:
    response = client.post(
        "/api/v1/chat", json={**VALID_REQUEST, "timeout_ms": 120_001}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_chat_rejects_timeout_header_body_conflict() -> None:
    response = client.post(
        "/api/v1/chat",
        json={**VALID_REQUEST, "timeout_ms": 1000},
        headers={"X-Request-Timeout-Ms": "1000"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_chat_accepts_stream_false() -> None:
    response = client.post("/api/v1/chat", json={**VALID_REQUEST, "stream": False})
    assert response.status_code == 200


def test_chat_rejects_streaming() -> None:
    response = client.post("/api/v1/chat", json={**VALID_REQUEST, "stream": True})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "streaming_not_supported"


def test_chat_rejects_invalid_requests_with_error_envelope() -> None:
    cases = [
        {},
        {"messages": []},
        {"messages": [{"role": "user"}]},
        {"messages": [{"role": "tool", "content": "bad"}]},
        {"messages": [{"role": "user", "content": "Hello"}], "unknown": True},
        {
            "messages": [{"role": "user", "content": "Hello"}],
            "requirements": {"capabilities": ["not_a_capability"]},
        },
        {
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"": "invalid"},
        },
    ]
    for body in cases:
        response = client.post("/api/v1/chat", json=body)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "invalid_request"
        assert error["request_id"]
        assert "traceback" not in response.text.lower()


def test_request_id_is_generated_and_propagated() -> None:
    response = client.post("/api/v1/chat", json=VALID_REQUEST)
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.json()["request_id"].startswith("req_")


def test_request_id_is_propagated_when_valid() -> None:
    request_id = "client.request-123"
    response = client.post(
        "/api/v1/chat", json=VALID_REQUEST, headers={"X-Request-ID": request_id}
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id


def test_invalid_request_id_is_replaced_safely() -> None:
    response = client.post(
        "/api/v1/chat", json=VALID_REQUEST, headers={"X-Request-ID": "bad id\n"}
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad id\n"


def test_health_still_works() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]


def test_openapi_contains_versioned_chat_contract() -> None:
    openapi = client.get("/openapi.json").json()
    assert "/api/v1/chat" in openapi["paths"]
    assert "/health" in openapi["paths"]
