"""Health and OpenAPI smoke tests."""

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "legacyflow-api"
    assert body["version"] == "0.1.0"
    assert "X-Request-ID" in response.headers


def test_request_id_echoed_when_client_supplied(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "test-request-123"})
    assert response.headers["X-Request-ID"] == "test-request-123"


def test_root_returns_service_info(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "legacyflow-api"


def test_openapi_generates_and_includes_v1_me() -> None:
    from app.main import app

    schema = app.openapi()
    assert schema["info"]["title"] == "LegacyFlow API"
    assert "/v1/me" in schema["paths"]
