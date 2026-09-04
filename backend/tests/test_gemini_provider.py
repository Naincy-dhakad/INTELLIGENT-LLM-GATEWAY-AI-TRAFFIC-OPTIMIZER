import time
from types import SimpleNamespace

import pytest

from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.infrastructure.providers.gemini import GeminiConfigurationError, GeminiProvider


class FakeGeminiClient:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **parameters: object) -> object:
        self.calls.append(parameters)
        if self.error:
            raise self.error
        return self.response


def request(deadline: float | None = None) -> ProviderChatRequest:
    return ProviderChatRequest(
        messages=(
            ProviderMessage(role="system", content="Be helpful."),
            ProviderMessage(role="user", content="Hello"),
            ProviderMessage(role="assistant", content="Hi"),
        ),
        model="gemini-requested",
        generation=ProviderGenerationSettings(max_output_tokens=30, temperature=0.3, top_p=0.7),
        timeout_ms=60_000,
        deadline_monotonic=deadline or time.perf_counter() + 20,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )


def response() -> object:
    return SimpleNamespace(
        text="Hello back",
        model_version="gemini-test",
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=SimpleNamespace(
            prompt_token_count=6, candidates_token_count=4, total_token_count=10
        ),
    )


def test_gemini_configuration_and_metadata() -> None:
    with pytest.raises(GeminiConfigurationError):
        GeminiProvider(api_key="", default_model="gemini-test")
    provider = GeminiProvider(api_key="fake-key", default_model="gemini-configured", client=FakeGeminiClient())
    assert provider.metadata.id == "gemini"
    assert provider.metadata.model_ids == ("gemini-configured",)
    assert provider.metadata.capabilities == {Capability.TEXT_GENERATION}
    assert provider.metadata.supports_streaming is False


def test_gemini_request_translation_maps_roles_and_generation() -> None:
    fake = FakeGeminiClient(response=response())
    provider = GeminiProvider(api_key="fake-key", default_model="gemini-default", client=fake)
    result = provider.chat(request())
    assert result.provider_id == "gemini"
    assert result.model == "gemini-test"
    assert result.message.content == "Hello back"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 10
    assert fake.calls[0]["model"] == "gemini-requested"
    assert len(fake.calls[0]["contents"]) == 2
    config = fake.calls[0]["config"]
    assert 0 < config.http_options.timeout <= 20_000
    assert config.max_output_tokens == 30
    assert config.temperature == 0.3
    assert config.top_p == 0.7
    assert config.system_instruction == "Be helpful."


def test_gemini_usage_can_be_nullable() -> None:
    no_usage = SimpleNamespace(
        text="Hello", model_version=None, candidates=[], usage_metadata=None
    )
    provider = GeminiProvider(
        api_key="fake-key", default_model="gemini-default", client=FakeGeminiClient(response=no_usage)
    )
    assert provider.chat(request()).usage is None


def test_gemini_expired_deadline_does_not_call_client() -> None:
    fake = FakeGeminiClient(response=response())
    provider = GeminiProvider(api_key="fake-key", default_model="gemini-default", client=fake)
    with pytest.raises(ProviderError) as raised:
        provider.chat(request(deadline=0.000001))
    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert fake.calls == []


def test_gemini_errors_are_normalized_by_status_or_type() -> None:
    class GeminiRateError(Exception):
        status_code = 429

    class GeminiTimeoutError(Exception):
        pass

    cases = [
        (SimpleNamespace(status_code=401), ProviderErrorCategory.AUTHENTICATION_FAILURE),
        (GeminiRateError(), ProviderErrorCategory.RATE_LIMIT),
        (SimpleNamespace(status_code=400), ProviderErrorCategory.INVALID_REQUEST),
        (GeminiTimeoutError(), ProviderErrorCategory.TIMEOUT),
        (SimpleNamespace(status_code=503), ProviderErrorCategory.UNAVAILABLE),
        (RuntimeError("native details"), ProviderErrorCategory.UNKNOWN),
    ]
    for error, category in cases:
        normalized = GeminiProvider._translate_error(error)
        assert normalized.category is category
        assert normalized.message == "Gemini could not complete the request."
