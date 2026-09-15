from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.config.settings import Settings


SECRET_VALUES = {
    "gateway_api_keys": "gateway-secret-value",
    "database_url": "postgresql+psycopg://user:password@db.example/gateway",
    "redis_url": "redis://user:password@redis.example/0",
    "openai_api_key": "openai-secret-value",
    "metrics_operator_token": "operator-secret-value",
}


def test_secret_configuration_values_are_redacted_from_representation():
    settings = Settings(_env_file=None, **SECRET_VALUES)
    rendered = f"{settings!r} {settings!s}"
    for secret in SECRET_VALUES.values():
        assert secret not in rendered


def production_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "production",
        "default_provider_id": "openai",
        "gateway_auth_enabled": True,
        "gateway_api_keys": "gateway-production-key",
        "openai_api_key": "openai-production-key",
        "redis_url": "redis://redis.example:6379/0",
        "database_url": "postgresql+psycopg://db.example/gateway",
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_production_configuration_is_accepted():
    assert production_settings().app_env == "production"


def test_production_requires_gateway_authentication_and_keys():
    with pytest.raises(ValidationError, match="GATEWAY_AUTH_ENABLED"):
        Settings(_env_file=None, app_env="production")
    with pytest.raises(ValidationError, match="GATEWAY_API_KEYS"):
        production_settings(gateway_api_keys="")


def test_production_rejects_mock_provider():
    with pytest.raises(ValidationError, match="phase3-mock"):
        production_settings(default_provider_id="phase3-mock")


@pytest.mark.parametrize(
    ("provider", "field"),
    [("openai", "openai_api_key"), ("anthropic", "anthropic_api_key"), ("gemini", "gemini_api_key")],
)
def test_selected_external_provider_requires_credential(provider, field):
    with pytest.raises(ValidationError, match="API_KEY"):
        production_settings(default_provider_id=provider, **{field: None})


def test_selected_ollama_requires_safe_url_and_model():
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        production_settings(default_provider_id="ollama", ollama_base_url=None)
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        production_settings(default_provider_id="ollama", ollama_base_url="http://user:password@ollama.example")
    assert production_settings(default_provider_id="ollama", ollama_base_url="http://ollama.example").default_provider_id == "ollama"


def test_production_rejects_local_dependency_placeholders():
    with pytest.raises(ValidationError, match="REDIS_URL"):
        production_settings(redis_url="redis://localhost:6379/0", rate_limit_enabled=True)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url="postgresql+psycopg://gateway:gateway@localhost:5432/gateway", usage_tracking_enabled=True)


def test_production_management_token_mode_requires_operator_token():
    with pytest.raises(ValidationError, match="METRICS_OPERATOR_TOKEN"):
        production_settings(metrics_enabled=True, metrics_management_enabled=True, metrics_auth_mode="token")
    with pytest.raises(ValidationError, match="mTLS"):
        production_settings(metrics_enabled=True, metrics_management_enabled=True, metrics_auth_mode="mtls")
    assert production_settings(
        metrics_enabled=True,
        metrics_management_enabled=True,
        metrics_auth_mode="token",
        metrics_operator_token="operator-token",
    ).metrics_enabled is True


def test_development_defaults_remain_compatible():
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.default_provider_id == "phase3-mock"
    assert settings.gateway_auth_enabled is False
    assert settings.gateway_api_keys.get_secret_value() == ""


def test_configuration_error_does_not_include_secret_values():
    secret = "production-gateway-secret"
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, app_env="production", gateway_api_keys=secret)
    assert secret not in str(raised.value)


def test_env_examples_are_placeholder_only_and_include_metrics_settings():
    root = Path(__file__).parents[2] / ".env.example"
    backend = Path(__file__).parents[1] / ".env.example"
    for path in (root, backend):
        content = path.read_text()
        assert "METRICS_ENABLED" in content
        assert "METRICS_MANAGEMENT_ENABLED" in content
        assert "gateway:gateway" not in content
        assert "OPENAI_API_KEY=" in content
        assert "METRICS_OPERATOR_TOKEN=" in content
