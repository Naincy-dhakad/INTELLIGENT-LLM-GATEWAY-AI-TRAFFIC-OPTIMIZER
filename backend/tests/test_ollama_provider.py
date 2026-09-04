import time

import httpx
import pytest

from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.infrastructure.providers.ollama import OllamaConfigurationError, OllamaProvider


def request(deadline: float | None = None) -> ProviderChatRequest:
    return ProviderChatRequest(
        messages=(ProviderMessage(role="user", content="Hello"),),
        model=None,
        generation=ProviderGenerationSettings(max_output_tokens=25, temperature=0.2, top_p=0.8),
        timeout_ms=60_000,
        deadline_monotonic=deadline or time.perf_counter() + 20,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_configuration_and_metadata() -> None:
    with pytest.raises(OllamaConfigurationError):
        OllamaProvider(base_url="not-a-url", default_model="llama-test")
    provider = OllamaProvider(
        base_url="http://localhost:11434/", default_model="llama-configured", client=client_for(lambda _: httpx.Response(200))
    )
    assert provider.metadata.id == "ollama"
    assert provider.metadata.model_ids == ("llama-configured",)
    assert provider.metadata.capabilities == {Capability.TEXT_GENERATION}
    assert provider.metadata.supports_streaming is False


def test_ollama_request_and_response_normalization() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://localhost:11434/api/chat"
        payload = request.read()
        assert b'"stream":false' in payload
        assert b'"num_predict":25' in payload
        return httpx.Response(
            200,
            json={
                "model": "llama-test",
                "message": {"role": "assistant", "content": "Hello back"},
                "done_reason": "stop",
                "prompt_eval_count": 5,
                "eval_count": 4,
            },
        )

    provider = OllamaProvider(
        base_url="http://localhost:11434", default_model="llama-default", client=client_for(handler)
    )
    result = provider.chat(request())
    assert result.provider_id == "ollama"
    assert result.model == "llama-test"
    assert result.message.content == "Hello back"
    assert result.usage is not None and result.usage.total_tokens == 9


def test_ollama_missing_usage_is_nullable() -> None:
    provider = OllamaProvider(
        base_url="http://localhost:11434",
        default_model="llama-default",
        client=client_for(lambda _: httpx.Response(200, json={"message": {"content": "Hello"}})),
    )
    assert provider.chat(request()).usage is None


def test_ollama_connection_and_status_errors_are_normalized() -> None:
    connection_client = client_for(lambda _: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    provider = OllamaProvider(base_url="http://localhost:11434", default_model="llama-default", client=connection_client)
    with pytest.raises(ProviderError) as raised:
        provider.chat(request())
    assert raised.value.category is ProviderErrorCategory.UNAVAILABLE

    status_provider = OllamaProvider(
        base_url="http://localhost:11434",
        default_model="llama-default",
        client=client_for(lambda _: httpx.Response(400)),
    )
    with pytest.raises(ProviderError) as raised:
        status_provider.chat(request())
    assert raised.value.category is ProviderErrorCategory.INVALID_REQUEST


def test_ollama_expired_deadline_does_not_call_client() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    provider = OllamaProvider(
        base_url="http://localhost:11434", default_model="llama-default", client=client_for(handler)
    )
    with pytest.raises(ProviderError) as raised:
        provider.chat(request(deadline=0.000001))
    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert called is False
