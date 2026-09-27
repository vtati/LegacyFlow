"""``GET /v1/me`` behavior tests (no external services).

Documented Slice-1 behavior: without ``DATABASE_URL`` the profile store is
unwired, so an authenticated ``GET /v1/me`` returns HTTP 501
``profile_store_unavailable``. Unauthenticated requests return HTTP 401.
"""

from fastapi.testclient import TestClient

from tests.conftest import mint_token


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token()}"}


def test_me_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["type"] == "urn:legacyflow:error:authentication_failed"
    assert body["status"] == 401
    assert "request_id" in body
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_me_with_bad_token_returns_401(client: TestClient) -> None:
    response = client.get("/v1/me", headers={"Authorization": "Bearer bogus"})
    assert response.status_code == 401


def test_me_without_database_returns_501(client: TestClient) -> None:
    """Authenticated, but no DATABASE_URL -> documented 501, not a fake profile."""
    response = client.get("/v1/me", headers=auth_headers())
    assert response.status_code == 501
    body = response.json()
    assert body["type"] == "urn:legacyflow:error:profile_store_unavailable"
    assert body["status"] == 501
    assert "request_id" in body
