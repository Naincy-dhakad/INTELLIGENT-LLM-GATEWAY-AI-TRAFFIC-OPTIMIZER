from gateway.config.settings import Settings
from gateway.domain.provider import Provider
from gateway.domain.provider_registry import ProviderRegistry
from gateway.infrastructure.providers.anthropic import AnthropicProvider
from gateway.infrastructure.providers.gemini import GeminiProvider
from gateway.infrastructure.providers.ollama import OllamaProvider
from gateway.infrastructure.providers.openai import OpenAIProvider


def test_all_configured_provider_adapters_share_protocol() -> None:
    providers = (
        OpenAIProvider(api_key="fake-openai-key", default_model="gpt-test", client=object()),
        AnthropicProvider(api_key="fake-anthropic-key", default_model="claude-test", client=object()),
        GeminiProvider(api_key="fake-gemini-key", default_model="gemini-test", client=object()),
        OllamaProvider(base_url="http://localhost:11434", default_model="llama-test", client=object()),
    )
    assert all(isinstance(provider, Provider) for provider in providers)
    registry = ProviderRegistry(providers=providers, default_provider_id="openai")
    assert {provider.metadata.id for provider in registry.list()} == {
        "openai", "anthropic", "gemini", "ollama"
    }
    assert registry.get("anthropic") is providers[1]
    assert registry.get("missing") is None


def test_provider_credentials_are_optional_in_settings_and_not_required_by_test_suite() -> None:
    settings = Settings(
        openai_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        ollama_base_url=None,
    )
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.gemini_api_key is None
    assert settings.ollama_base_url is None
