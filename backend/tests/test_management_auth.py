from dataclasses import dataclass

from fastapi.testclient import TestClient
import pytest

from gateway.api.management import create_management_app
from gateway.application.management_auth import ManagementIdentity, MetricsManagementAuthorizer
from gateway.application.metrics import MetricsSnapshot
from gateway.config.settings import Settings


@dataclass
class Exporter:
    calls: int = 0

    def export(self, snapshot):
        self.calls += 1
        return "metrics\n"


def settings(mode="token", token="operator-secret"):
    return Settings(_env_file=None, metrics_auth_mode=mode, metrics_operator_token=token)


def app(mode="token", token="operator-secret", source=None, identity_source=None):
    exporter = Exporter()
    source = source or (lambda: MetricsSnapshot())
    return create_management_app(source, exporter, settings(mode, token), identity_source), exporter


def test_token_auth_success_and_failures():
    management, exporter = app()
    with TestClient(management) as client:
        assert client.get("/metrics", headers={"Authorization": "Bearer operator-secret"}).status_code == 200
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Basic operator-secret"}).status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer"}).status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert exporter.calls == 1


def test_token_comparison_uses_constant_time_comparison(monkeypatch):
    import gateway.api.management as management_module
    calls = []
    original = management_module.hmac.compare_digest
    monkeypatch.setattr(management_module.hmac, "compare_digest", lambda left, right: calls.append((left, right)) or original(left, right))
    management, _ = app()
    with TestClient(management) as client:
        assert client.get("/metrics", headers={"Authorization": "Bearer operator-secret"}).status_code == 200
    assert calls == [("operator-secret", "operator-secret")]


def test_missing_configured_token_fails_closed_without_export():
    management, exporter = app(token=None)
    with TestClient(management) as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer operator-secret"})
    assert response.status_code == 503
    assert response.text == "management authentication unavailable"
    assert exporter.calls == 0


def test_token_never_appears_in_response_or_error():
    secret = "operator-secret"
    management, _ = app(token=secret)
    with TestClient(management) as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert secret not in response.text
    assert secret not in repr(management)


def test_mtls_uses_only_normalized_identity_boundary():
    allowed, exporter = app("mtls", None, identity_source=lambda request: ManagementIdentity(True, "metrics_reader", "mtls"))
    forbidden, _ = app("mtls", None, identity_source=lambda request: ManagementIdentity(True, "other_role", "mtls"))
    missing, _ = app("mtls", None)
    with TestClient(allowed) as client:
        assert client.get("/metrics").status_code == 200
    with TestClient(forbidden) as client:
        assert client.get("/metrics").status_code == 403
    with TestClient(missing) as client:
        assert client.get("/metrics").status_code == 401
    assert exporter.calls == 1


def test_failed_authentication_does_not_invoke_source_or_exporter():
    calls = []
    management, exporter = app(source=lambda: calls.append(True) or MetricsSnapshot())
    with TestClient(management) as client:
        response = client.get("/metrics")
    assert response.status_code == 401
    assert calls == []
    assert exporter.calls == 0


def test_authorization_policy_is_bounded_and_separate():
    authorizer = MetricsManagementAuthorizer()
    assert authorizer.authorize(ManagementIdentity(True, "metrics_reader", "token")).allowed
    assert authorizer.authorize(ManagementIdentity(True, "other_role", "mtls")).result.value == "forbidden"
    assert authorizer.authorize(None).result.value == "unauthorized"


def test_gateway_api_keys_are_not_used_for_management_auth(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEYS", "operator-secret")
    management, _ = app(token=None)
    with TestClient(management) as client:
        response = client.get("/metrics", headers={"X-API-Key": "operator-secret"})
    assert response.status_code == 503
