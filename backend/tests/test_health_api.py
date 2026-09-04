from fastapi.testclient import TestClient

from gateway.main import create_app


client = TestClient(create_app())


def test_quality_objective_returns_health_score_and_policy_version():
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Write code"}],
            "routing": {"objective": "quality"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["routing"]["policy_version"] == "classification-cost-latency-quality-v1"
    assert body["routing"]["health_score"] == 100
    assert "highest configured health score" in body["routing"]["decision_reason"]


def test_quality_without_usable_health_returns_safe_error():
    # The production mock has configured health; this verifies the public error
    # contract through a service assembled with an unknown-health provider.
    from gateway.application.chat_service import ChatService
    from gateway.domain.provider import Capability, ProviderMetadata
    from gateway.domain.provider_registry import ProviderRegistry
    from gateway.infrastructure.providers.mock import MockProvider

    unknown = MockProvider(provider_id="unknown-health")
    unknown._metadata = ProviderMetadata(
        id="unknown-health", name="Unknown Health", capabilities=frozenset({Capability.TEXT_GENERATION}), model_ids=("phase3-mock-model",)
    )
    app = create_app()
    app.state.chat_service = ChatService(ProviderRegistry((unknown,), "unknown-health"))
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"messages": [{"role": "user", "content": "Write code"}], "routing": {"objective": "quality"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quality_unavailable"
