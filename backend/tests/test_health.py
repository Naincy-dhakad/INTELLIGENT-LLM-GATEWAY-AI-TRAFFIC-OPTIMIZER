from fastapi.testclient import TestClient

from gateway.main import create_app


def test_health_endpoint_returns_foundation_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Intelligent LLM Gateway",
        "environment": "development",
    }
