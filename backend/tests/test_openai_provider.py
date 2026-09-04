import time
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, RateLimitError

from gateway.config.settings import Settings
from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.infrastructure.providers.openai import (
    OpenAIConfigurationError,
    OpenAIProvider,
)


class FakeClient:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.timeout: float | None = None
        self.calls: list[dict[str, object]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, *, timeout: float) -> "FakeClient":
        self.timeout = timeout
        return self

    def create(self, **parameters: object) -> object:
        self.calls.append(parameters)
        if self.error:
            raise self.error
        return self.response


def request(*, deadline: float, model: str | None = None) -> ProviderChatRequest:
    return ProviderChatRequest(
        messages=(
            ProviderMessage(role="system", content="Be concise."),
            ProviderMessage(role="user", content="Hello"),
        ),
        model=model,
        generation=ProviderGenerationSettings(
            max_output_tokens=50, temperature=0.2, top_p=0.9
        ),
        timeout_ms=60_000,
        deadline_monotonic=deadline,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )


def response() -> object:
    return SimpleNamespace(
        model="gpt-test",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Hello back"), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=4, completion_tokens=3, total_tokens=7
        ),
    )


def test_settings_reads_openai_configuration_without_exposing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    placeholder = "unit-test-placeholder"
    monkeypatch.setenv("OPENAI_API_KEY", placeholder)
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "gpt-configured")

    settings = Settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == placeholder
    assert settings.openai_default_model == "gpt-configured"
    assert placeholder not in repr(settings)


def test_missing_key_is_safe_and_does_not_enable_provider() -> None:
    assert Settings(openai_api_key=None).openai_api_key is None
    with pytest.raises(OpenAIConfigurationError, match="OPENAI_API_KEY"):
        OpenAIProvider(api_key="", default_model="gpt-test")


def test_openai_metadata_is_normalized() -> None:
    provider = OpenAIProvider(api_key="test-key", default_model="gpt-configured", client=FakeClient())

    assert provider.metadata.id == "openai"
    assert provider.metadata.name == "OpenAI"
    assert Capability.TEXT_GENERATION in provider.metadata.capabilities
    assert provider.metadata.supports_streaming is False
    assert provider.metadata.model_ids == ("gpt-configured",)


def test_request_translation_maps_normalized_fields_and_remaining_timeout() -> None:
    fake = FakeClient(response=response())
    provider = OpenAIProvider(api_key="test-key", default_model="gpt-default", client=fake)

    result = provider.chat(request(deadline=time.perf_counter() + 20, model="gpt-requested"))

    assert result.model == "gpt-test"
    assert fake.timeout is not None
    assert 0 < fake.timeout <= 20
    assert fake.calls == [
        {
            "model": "gpt-requested",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hello"},
            ],
            "max_completion_tokens": 50,
            "temperature": 0.2,
            "top_p": 0.9,
        }
    ]


def test_default_model_is_used_when_request_model_is_absent() -> None:
    fake = FakeClient(response=response())
    provider = OpenAIProvider(api_key="test-key", default_model="gpt-default", client=fake)

    provider.chat(request(deadline=time.perf_counter() + 20))

    assert fake.calls[0]["model"] == "gpt-default"


def test_response_translation_maps_message_model_finish_reason_and_usage() -> None:
    fake = FakeClient(response=response())
    provider = OpenAIProvider(api_key="test-key", default_model="gpt-default", client=fake)

    result = provider.chat(request(deadline=time.perf_counter() + 20))

    assert result.provider_id == "openai"
    assert result.message.role == "assistant"
    assert result.message.content == "Hello back"
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.total_tokens == 7


def test_missing_openai_usage_is_nullable() -> None:
    no_usage = SimpleNamespace(
        model="gpt-test",
        choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"), finish_reason=None)],
        usage=None,
    )
    provider = OpenAIProvider(
        api_key="test-key", default_model="gpt-default", client=FakeClient(response=no_usage)
    )

    assert provider.chat(request(deadline=time.perf_counter() + 20)).usage is None


def test_expired_deadline_prevents_external_call() -> None:
    fake = FakeClient(response=response())
    provider = OpenAIProvider(api_key="test-key", default_model="gpt-default", client=fake)

    with pytest.raises(ProviderError) as raised:
        provider.chat(request(deadline=0.000001))

    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert fake.calls == []


def test_openai_errors_are_normalized_without_native_details() -> None:
    http_response = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com"))
    cases = [
        (AuthenticationError("bad key", response=http_response, body=None), ProviderErrorCategory.AUTHENTICATION_FAILURE),
        (RateLimitError("slow down", response=http_response, body=None), ProviderErrorCategory.RATE_LIMIT),
        (BadRequestError("bad request", response=http_response, body=None), ProviderErrorCategory.INVALID_REQUEST),
        (RuntimeError("secret native detail"), ProviderErrorCategory.UNKNOWN),
    ]
    for native_error, category in cases:
        normalized = OpenAIProvider._translate_error(native_error)
        assert normalized.category is category
        assert "secret native detail" not in normalized.message
        assert normalized.message == "OpenAI could not complete the request."
