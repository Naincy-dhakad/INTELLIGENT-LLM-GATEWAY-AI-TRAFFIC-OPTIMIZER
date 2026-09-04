import time
from types import SimpleNamespace

import httpx
import pytest
from anthropic import AuthenticationError, BadRequestError, RateLimitError

from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.infrastructure.providers.anthropic import (
    AnthropicConfigurationError,
    AnthropicProvider,
)


class FakeAnthropicClient:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.timeout = None
        self.calls: list[dict[str, object]] = []
        self.messages = SimpleNamespace(create=self.create)

    def with_options(self, *, timeout: float) -> "FakeAnthropicClient":
        self.timeout = timeout
        return self

    def create(self, **parameters: object) -> object:
        self.calls.append(parameters)
        if self.error:
            raise self.error
        return self.response


def request(deadline: float | None = None) -> ProviderChatRequest:
    return ProviderChatRequest(
        messages=(
            ProviderMessage(role="system", content="Be precise."),
            ProviderMessage(role="user", content="Hello"),
        ),
        model=None,
        generation=ProviderGenerationSettings(max_output_tokens=40, temperature=0.1, top_p=0.8),
        timeout_ms=60_000,
        deadline_monotonic=deadline or time.perf_counter() + 20,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )


def response() -> object:
    return SimpleNamespace(
        model="claude-test",
        content=[SimpleNamespace(type="text", text="Hello back")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=4),
    )


def test_anthropic_configuration_and_metadata() -> None:
    with pytest.raises(AnthropicConfigurationError):
        AnthropicProvider(api_key="", default_model="claude-test")
    provider = AnthropicProvider(api_key="fake-key", default_model="claude-configured", client=FakeAnthropicClient())
    assert provider.metadata.id == "anthropic"
    assert provider.metadata.model_ids == ("claude-configured",)
    assert Capability.TEXT_GENERATION in provider.metadata.capabilities
    assert provider.metadata.supports_streaming is False


def test_anthropic_request_translation_separates_system_message() -> None:
    fake = FakeAnthropicClient(response=response())
    provider = AnthropicProvider(api_key="fake-key", default_model="claude-default", client=fake)
    result = provider.chat(request())
    assert result.message.content == "Hello back"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 9
    assert fake.timeout is not None and fake.timeout <= 20
    assert fake.calls == [
        {
            "model": "claude-default",
            "max_tokens": 40,
            "messages": [{"role": "user", "content": "Hello"}],
            "system": "Be precise.",
            "temperature": 0.1,
            "top_p": 0.8,
        }
    ]


def test_anthropic_usage_can_be_nullable() -> None:
    no_usage = SimpleNamespace(
        model="claude-test",
        content=[SimpleNamespace(type="text", text="Hello")],
        stop_reason="max_tokens",
        usage=None,
    )
    provider = AnthropicProvider(
        api_key="fake-key", default_model="claude-default", client=FakeAnthropicClient(response=no_usage)
    )
    result = provider.chat(request())
    assert result.finish_reason == "length"
    assert result.usage is None


def test_anthropic_expired_deadline_does_not_call_client() -> None:
    fake = FakeAnthropicClient(response=response())
    provider = AnthropicProvider(api_key="fake-key", default_model="claude-default", client=fake)
    with pytest.raises(ProviderError) as raised:
        provider.chat(request(deadline=0.000001))
    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert fake.calls == []


def test_anthropic_errors_are_normalized() -> None:
    response_401 = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
    cases = [
        (AuthenticationError("fake", response=response_401, body=None), ProviderErrorCategory.AUTHENTICATION_FAILURE),
        (RateLimitError("fake", response=response_401, body=None), ProviderErrorCategory.RATE_LIMIT),
        (BadRequestError("fake", response=response_401, body=None), ProviderErrorCategory.INVALID_REQUEST),
        (RuntimeError("native details"), ProviderErrorCategory.UNKNOWN),
    ]
    for error, category in cases:
        normalized = AnthropicProvider._translate_error(error)
        assert normalized.category is category
        assert normalized.message == "Anthropic could not complete the request."
        assert "native details" not in normalized.message
