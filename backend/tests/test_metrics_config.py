import pytest
from pydantic import ValidationError

from gateway.config.settings import Settings


def test_metrics_configuration_defaults_are_disabled_and_local():
    settings = Settings(_env_file=None)
    assert settings.metrics_enabled is False
    assert settings.metrics_management_enabled is False
    assert settings.metrics_bind_host == "127.0.0.1"
    assert settings.metrics_bind_port == 9090
    assert settings.metrics_auth_mode == "mtls"
    assert settings.metrics_operator_token is None


def test_metrics_environment_overrides(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_MANAGEMENT_ENABLED", "true")
    monkeypatch.setenv("METRICS_BIND_HOST", "127.0.0.2")
    monkeypatch.setenv("METRICS_BIND_PORT", "9191")
    monkeypatch.setenv("METRICS_AUTH_MODE", "token")
    monkeypatch.setenv("METRICS_OPERATOR_TOKEN", "do-not-leak")
    settings = Settings(_env_file=None)
    assert settings.metrics_enabled is True
    assert settings.metrics_management_enabled is True
    assert settings.metrics_bind_host == "127.0.0.2"
    assert settings.metrics_bind_port == 9191
    assert settings.metrics_auth_mode == "token"
    assert settings.metrics_operator_token.get_secret_value() == "do-not-leak"


def test_metrics_port_must_be_valid_tcp_port():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, metrics_bind_port=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, metrics_bind_port=65536)


def test_metrics_auth_mode_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, metrics_auth_mode="basic")


def test_operator_token_is_not_exposed_by_repr_or_validation_error():
    token = "super-secret-operator-token"
    settings = Settings(_env_file=None, metrics_operator_token=token)
    assert token not in repr(settings)
    assert token not in str(settings)
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, metrics_operator_token=token, metrics_bind_port=70000)
    assert token not in str(raised.value)


def test_existing_configuration_remains_compatible():
    settings = Settings(_env_file=None, app_name="existing", gateway_auth_enabled=True)
    assert settings.app_name == "existing"
    assert settings.gateway_auth_enabled is True
    assert settings.metrics_enabled is False
