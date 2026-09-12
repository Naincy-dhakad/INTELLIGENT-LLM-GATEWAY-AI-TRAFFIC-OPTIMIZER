from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, Field, model_validator
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
    gateway_auth_enabled: bool = False
    gateway_api_keys: str = ""
    rate_limit_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_requests: int = Field(default=60, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    usage_tracking_enabled: bool = False
    database_url: str = "postgresql+psycopg://localhost/gateway"
    metrics_enabled: bool = False
    metrics_management_enabled: bool = False
    metrics_bind_host: str = "127.0.0.1"
    metrics_bind_port: int = Field(default=9090, ge=1, le=65535)
    metrics_auth_mode: Literal["mtls", "token"] = "mtls"
    metrics_operator_token: SecretStr | None = None

    @model_validator(mode="after")
    def rate_limit_requires_authentication(self) -> "Settings":
        if self.rate_limit_enabled and not self.gateway_auth_enabled:
            raise ValueError("RATE_LIMIT_ENABLED requires GATEWAY_AUTH_ENABLED")
        if self.rate_limit_enabled and not self.redis_url.strip():
            raise ValueError("REDIS_URL is required when rate limiting is enabled")
        if self.usage_tracking_enabled and not self.database_url.strip():
            raise ValueError("DATABASE_URL is required when usage tracking is enabled")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
