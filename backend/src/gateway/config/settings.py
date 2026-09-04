from decimal import Decimal
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the foundation service."""

    app_name: str = "Intelligent LLM Gateway"
    app_env: str = "development"
    log_level: str = "INFO"
    default_provider_id: str = "phase3-mock"
    phase3_mock_input_usd_per_million_tokens: Decimal = Decimal("1")
    phase3_mock_output_usd_per_million_tokens: Decimal = Decimal("2")
    phase3_mock_latency_ms: int = 100
    phase3_mock_health_score: int = 100
    openai_api_key: SecretStr | None = None
    openai_default_model: str = "gpt-4o-mini"
    anthropic_api_key: SecretStr | None = None
    anthropic_default_model: str = "claude-3-5-haiku-latest"
    gemini_api_key: SecretStr | None = None
    gemini_default_model: str = "gemini-2.0-flash"
    ollama_base_url: str | None = None
    ollama_default_model: str = "llama3.2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
