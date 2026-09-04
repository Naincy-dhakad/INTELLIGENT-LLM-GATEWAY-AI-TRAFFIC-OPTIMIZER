import pytest
from fastapi.testclient import TestClient

from gateway.api.chat import get_chat_service
from gateway.api.schemas import ChatRequest
from gateway.application.context import RequestContext
from gateway.domain.provider import ProviderError, ProviderErrorCategory
from gateway.main import create_app


VALID_REQUEST = {"messages": [{"role": "user", "content": "Hello"}]}


def assert_invalid(client: TestClient, body: dict) -> None:
    response = client.post("/api/v1/chat", json=body)
    assert response.status_code == 400
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "traceback" not in response.text.lower()
    assert "backend/" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "invalid", "content": "Hello"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user"}]},
        {"messages": [{"role": "user", "content": "Hello"}], "provider": "bad id"},
        {"messages": [{"role": "user", "content": "Hello"}], "model": "bad id"},
        {"messages": [{"role": "user", "content": "Hello"}], "unknown": True},
        {"messages": [{"role": "user", "content": "Hello"}], "routing": {"objective": "fast"}},
        {"messages": [{"role": "user", "content": "Hello"}], "generation": {"temperature": 3}},
        {"messages": [{"role": "user", "content": "Hello"}], "generation": {"top_p": 0}},
        {"messages": [{"role": "user", "content": "Hello"}], "generation": {"max_output_tokens": 0}},
        {"messages": [{"role": "user", "content": "Hello"}], "metadata": {"bad key": "value"}},
        {"messages": [{"role": "user", "content": "Hello"}], "timeout_ms": 0},
        {"messages": [{"role": "user", "content": "Hello"}], "timeout_ms": 120001},
    ],
)
def test_invalid_requests_use_safe_error_envelope(body: dict) -> None:
    assert_invalid(TestClient(create_app()), body)


def test_excessive_message_count_and_content_are_rejected() -> None:
    client = TestClient(create_app())
    assert_invalid(
        client,
        {"messages": [{"role": "user", "content": "x"}] * 101},
    )
    assert_invalid(
        client,
        {"messages": [{"role": "user", "content": "x" * 1_000_001}]},
    )


def test_timeout_header_malformed_or_out_of_range_is_invalid() -> None:
    client = TestClient(create_app())
    for value in ["not-a-number", "0", "120001"]:
        response = client.post(
            "/api/v1/chat", json=VALID_REQUEST, headers={"X-Request-Timeout-Ms": value}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


def test_provider_error_mapping_is_safe_and_does_not_expose_native_message() -> None:
    app = create_app()

    class FailingService:
        def __init__(self, category: ProviderErrorCategory) -> None:
            self.category = category

        def complete(self, _: ChatRequest, __: RequestContext) -> None:
            raise ProviderError(self.category, "native secret and internal path")

    expected = {
        ProviderErrorCategory.TIMEOUT: (504, "gateway_timeout", True),
        ProviderErrorCategory.AUTHENTICATION_FAILURE: (502, "provider_error", False),
        ProviderErrorCategory.RATE_LIMIT: (429, "rate_limited", True),
        ProviderErrorCategory.INVALID_REQUEST: (400, "invalid_request", False),
        ProviderErrorCategory.UNAVAILABLE: (503, "all_providers_unavailable", True),
        ProviderErrorCategory.UNSUPPORTED_CAPABILITY: (422, "unsupported_capability", False),
        ProviderErrorCategory.PROVIDER_ERROR: (502, "provider_error", False),
        ProviderErrorCategory.UNKNOWN: (502, "provider_error", False),
    }
    for category, (status, code, retryable) in expected.items():
        app.state.chat_service = FailingService(category)
        response = TestClient(app).post("/api/v1/chat", json=VALID_REQUEST)
        assert response.status_code == status
        error = response.json()["error"]
        assert error["code"] == code
        assert error["retryable"] is retryable
        assert "native secret" not in response.text
        assert "internal path" not in response.text
        assert "traceback" not in response.text.lower()


def test_unexpected_exception_uses_internal_error_envelope() -> None:
    app = create_app()

    class BrokenService:
        def complete(self, _: ChatRequest, __: RequestContext) -> None:
            raise RuntimeError("secret value /internal/backend/path")

    app.state.chat_service = BrokenService()
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/chat", json=VALID_REQUEST
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret value" not in response.text
    assert "/internal/backend/path" not in response.text


def test_invalid_capability_is_rejected_before_provider_call() -> None:
    app = create_app()
    service = app.state.chat_service
    called = False

    class SpyProvider:
        @property
        def metadata(self):
            return service._registry.get("phase3-mock").metadata

        def chat(self, _):
            nonlocal called
            called = True
            raise AssertionError("provider must not be called")

    from gateway.domain.provider_registry import ProviderRegistry
    from gateway.application.chat_service import ChatService

    app.state.chat_service = ChatService(
        ProviderRegistry((SpyProvider(),), default_provider_id="phase3-mock")
    )
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            **VALID_REQUEST,
            "provider": "phase3-mock",
            "requirements": {"capabilities": ["structured_output"]},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_capability"
    assert called is False
