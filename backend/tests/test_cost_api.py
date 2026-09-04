from fastapi.testclient import TestClient

from gateway.main import create_app


client = TestClient(create_app())


def test_cost_objective_returns_estimate_and_policy_metadata():
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Write code"}],
            "routing": {"objective": "cost"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"]["id"] == "phase3-mock"
    assert body["routing"]["policy_version"] == "classification-cost-v1"
    assert body["routing"]["estimated_cost_usd"] is not None
    assert "lowest estimated cost" in body["routing"]["decision_reason"]


def test_cost_ceiling_returns_safe_error_when_mock_is_over_budget():
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Write code"}],
            "routing": {"objective": "cost", "max_cost_usd": 0},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cost_limit_exceeded"


def test_invalid_cost_ceiling_uses_existing_validation_envelope():
    for value in [-1, "not-a-number", "NaN", "Infinity"]:
        response = client.post(
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "routing": {"max_cost_usd": value},
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"
