from gateway.domain.provider import (
    Capability,
    Provider,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.domain.provider_registry import ProviderRegistry
from gateway.infrastructure.providers.mock import MockProvider, is_provider


def provider_request() -> ProviderChatRequest:
    return ProviderChatRequest(
        messages=(ProviderMessage(role="user", content="Hello"),),
        model=None,
        generation=ProviderGenerationSettings(max_output_tokens=20),
        timeout_ms=60_000,
        deadline_monotonic=123.0,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )


def test_mock_provider_satisfies_provider_protocol() -> None:
    provider = MockProvider()
    assert isinstance(provider, Provider)
    assert is_provider(provider)


def test_normalized_provider_request_contains_no_native_payload() -> None:
    request = provider_request()
    assert request.messages[0].role == "user"
    assert request.timeout_ms == 60_000
    assert request.required_capabilities == {Capability.TEXT_GENERATION}
    assert "api_key" not in request.model_dump()


def test_mock_provider_returns_normalized_response() -> None:
    response = MockProvider().chat(provider_request())
    assert isinstance(response, ProviderChatResponse)
    assert response.provider_id == "phase3-mock"
    assert response.model == "phase3-mock-model"
    assert response.message.role == "assistant"
    assert response.usage is None


def test_provider_error_represents_normalized_categories() -> None:
    error = ProviderError(
        category=ProviderErrorCategory.TIMEOUT,
        message="provider attempt timed out",
    )
    assert error.category is ProviderErrorCategory.TIMEOUT
    assert str(error) == "provider attempt timed out"


def test_provider_metadata_uses_normalized_capabilities() -> None:
    metadata = MockProvider().metadata
    assert metadata.id == "phase3-mock"
    assert metadata.supports_streaming is False
    assert metadata.capabilities == {Capability.TEXT_GENERATION}
    assert metadata.model_ids == ("phase3-mock-model",)


def test_registry_registers_retrieves_and_lists_without_routing() -> None:
    provider = MockProvider()
    registry = ProviderRegistry((provider,), default_provider_id=provider.metadata.id)
    assert registry.get("phase3-mock") is provider
    assert registry.default() is provider
    assert registry.list() == (provider,)
    assert registry.get("missing") is None


def test_registry_rejects_duplicate_provider_ids() -> None:
    provider = MockProvider()
    registry = ProviderRegistry((provider,))
    try:
        registry.register(provider)
    except ValueError as error:
        assert "already registered" in str(error)
    else:
        raise AssertionError("duplicate provider registration should fail")
