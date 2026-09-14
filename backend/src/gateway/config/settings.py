from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

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
    gateway_api_keys: SecretStr = SecretStr("")
    rate_limit_enabled: bool = False
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    rate_limit_requests: int = Field(default=60, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    usage_tracking_enabled: bool = False
    database_url: SecretStr = SecretStr("postgresql+psycopg://localhost/gateway")
    metrics_enabled: bool = False
    metrics_management_enabled: bool = False
    metrics_bind_host: str = "127.0.0.1"
    metrics_bind_port: int = Field(default=9090, ge=1, le=65535)
    metrics_auth_mode: Literal["mtls", "token"] = "mtls"
    metrics_operator_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        production = self.app_env.lower() == "production"
        gateway_keys = self.gateway_api_keys.get_secret_value()
        redis_url = self.redis_url.get_secret_value()
        database_url = self.database_url.get_secret_value()

        if self.rate_limit_enabled and not self.gateway_auth_enabled:
            raise ValueError("configuration_invalid: RATE_LIMIT_ENABLED requires GATEWAY_AUTH_ENABLED")
        if self.rate_limit_enabled and not redis_url.strip():
            raise ValueError("required_secret_missing: REDIS_URL is required when rate limiting is enabled")
        if self.usage_tracking_enabled and not database_url.strip():
            raise ValueError("required_secret_missing: DATABASE_URL is required when usage tracking is enabled")

        if self.ollama_base_url:
            parsed = urlparse(self.ollama_base_url)
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                raise ValueError("insecure_configuration: OLLAMA_BASE_URL must not contain credentials")

        if not production:
            return self

        if not self.gateway_auth_enabled:
            raise ValueError("insecure_configuration: GATEWAY_AUTH_ENABLED must be true in production")
        if not any(key.strip() for key in gateway_keys.split(",")):
            raise ValueError("required_secret_missing: GATEWAY_API_KEYS is required in production")
        if self.default_provider_id == "phase3-mock":
            raise ValueError("insecure_configuration: DEFAULT_PROVIDER_ID cannot be phase3-mock in production")

        provider_keys = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }
        if self.default_provider_id in provider_keys:
            key = provider_keys[self.default_provider_id]
            if key is None or not key.get_secret_value().strip():
                raise ValueError(f"required_secret_missing: {self.default_provider_id.upper()}_API_KEY is required")
        if self.default_provider_id == "ollama":
            if not self.ollama_base_url:
                raise ValueError("required_secret_missing: OLLAMA_BASE_URL is required")
            if not self.ollama_default_model.strip():
                raise ValueError("required_secret_missing: OLLAMA_DEFAULT_MODEL is required")

        if self.rate_limit_enabled and redis_url in {"redis://localhost:6379/0", "redis://localhost:6379"}:
            raise ValueError("insecure_configuration: production REDIS_URL uses a local development placeholder")
        if self.usage_tracking_enabled:
            if database_url in {
                "postgresql+psycopg://localhost/gateway",
                "postgresql+psycopg://gateway:gateway@localhost:5432/gateway",
            } or "gateway:gateway@" in database_url:
                raise ValueError("insecure_configuration: production DATABASE_URL uses a development placeholder")

        if self.metrics_management_enabled and not self.metrics_enabled:
            raise ValueError("configuration_invalid: METRICS_MANAGEMENT_ENABLED requires METRICS_ENABLED")
        if self.metrics_enabled and self.metrics_management_enabled:
            if self.metrics_auth_mode == "token":
                if self.metrics_operator_token is None or not self.metrics_operator_token.get_secret_value().strip():
                    raise ValueError("required_secret_missing: METRICS_OPERATOR_TOKEN is required for token mode")
            else:
                raise ValueError("insecure_configuration: mTLS management identity integration is not configured")
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
