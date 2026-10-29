"""Health and OpenAPI smoke tests."""

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # SEC-006: public body is exactly {"status": "ok"}; mock the DB check
    # so the ok path is deterministic without a live database.
    import contextlib

    class _Cursor:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return False

        def execute(self, *args):  # type: ignore[no-untyped-def]
            return None

        def fetchone(self):  # type: ignore[no-untyped-def]
            return (1,)

    class _Conn:
        def cursor(self):  # type: ignore[no-untyped-def]
            return _Cursor()

    @contextlib.contextmanager
    def _fake_transaction():  # type: ignore[no-untyped-def]
        yield _Conn()

    from app.core import db as db_module

    monkeypatch.setattr(db_module, "transaction", _fake_transaction)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
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
