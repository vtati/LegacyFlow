"""SEC-004 / SEC-006: production docs disabled; minimal /healthz body."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _prod_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("API_ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TEST_JWT_ISSUER", raising=False)
    monkeypatch.delenv("TEST_JWT_SECRET", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import create_app

        return TestClient(create_app(), raise_server_exceptions=False)
    finally:
        get_settings.cache_clear()


def test_production_app_has_no_docs_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    prod_client = _prod_client(monkeypatch)
    paths = {getattr(route, "path", "") for route in prod_client.app.routes}  # type: ignore[attr-defined]
    assert "/docs" not in paths
    assert "/openapi.json" not in paths
    assert "/redoc" not in paths
    assert prod_client.get("/docs").status_code == 404
    assert prod_client.get("/openapi.json").status_code == 404


def test_production_root_has_no_docs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    prod_client = _prod_client(monkeypatch)
    body = prod_client.get("/").json()
    assert "docs" not in body
    assert body["service"] == "legacyflow-api"


def test_healthz_body_is_minimal(client: TestClient) -> None:
    response = client.get("/healthz")
    body = response.json()
    # Public contract: only the status key; detail stays in server logs.
    assert set(body.keys()) == {"status"}
    assert body["status"] in ("ok", "degraded")
    assert response.status_code == (200 if body["status"] == "ok" else 503)
