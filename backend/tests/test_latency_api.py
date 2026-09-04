from fastapi.testclient import TestClient

from gateway.main import create_app


client = TestClient(create_app())


def test_latency_objective_returns_configured_estimate_and_reason():
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Write code"}],
            "routing": {"objective": "latency"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["routing"]["policy_version"] == "classification-cost-latency-v1"
    assert body["routing"]["estimated_latency_ms"] == 100
    assert "lowest configured estimated latency" in body["routing"]["decision_reason"]


def test_latency_ceiling_returns_safe_error_when_no_candidate_fits():
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Write code"}],
            "routing": {"objective": "latency", "max_latency_ms": 1},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "latency_limit_exceeded"


def test_latency_validation_preserves_existing_error_envelope():
    for value in [0, -1, 120001, "invalid"]:
        response = client.post(
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "routing": {"max_latency_ms": value},
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"
