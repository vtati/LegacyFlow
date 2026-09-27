"""``GET /v1/me`` 200-path integration test (requires a Postgres database).

Boots user-space Postgres via pgserver when ``DATABASE_URL`` is unset,
applies the Slice-1 migration, inserts a synthetic profile, and asserts the
API returns it. Skips gracefully when no database is available.

All fixture data is synthetic. Nothing sensitive is printed.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    TEST_AUDIENCE,
    TEST_EMAIL,
    TEST_ISSUER,
    TEST_SECRET,
    TEST_SUB,
    mint_token,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
MIGRATION_FILE = REPO_ROOT / "supabase" / "migrations" / "20260926220000_foundation_slice1.sql"


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    """Connection string: DATABASE_URL (CI) or a throwaway pgserver instance."""
    url = os.environ.get("DATABASE_URL")
    if url:
        yield url
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("no database available (set DATABASE_URL or install pgserver)")
    server = pgserver.get_server(tempfile.mkdtemp(prefix="lf-api-test-"))
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture(scope="module")
def seeded_db(db_url: str) -> str:
    """Fresh database with the Slice-1 migration and one synthetic profile."""
    import psycopg

    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("drop schema if exists public cascade")
            cur.execute("create schema public")
            cur.execute("drop schema if exists auth cascade")
            cur.execute("create schema auth")
            # profiles.id references auth.users(id) per §7.2 DDL; RLS policies
            # call auth.uid(), shimmed here like the supabase RLS suite does.
            cur.execute("create table auth.users (id uuid primary key)")
            cur.execute(
                """
                create or replace function auth.uid() returns uuid
                language sql stable
                as $$ select nullif(current_setting('app.user_id', true), '')::uuid $$;
                """
            )
            cur.execute(MIGRATION_FILE.read_text())
            cur.execute("insert into auth.users (id) values (%s)", (TEST_SUB,))
            cur.execute(
                "insert into profiles (id, display_name, email) values (%s, %s, %s)",
                (TEST_SUB, "Synthetic API User", TEST_EMAIL),
            )
    return db_url


@pytest.fixture()
def db_client(seeded_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient wired to the test JWT override AND the seeded database."""
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    # Service role key is required-but-empty in Slice 1 (see config.py).
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("DATABASE_URL", seeded_db)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


def test_me_returns_profile_from_database(db_client: TestClient) -> None:
    response = db_client.get("/v1/me", headers={"Authorization": f"Bearer {mint_token()}"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == TEST_SUB
    assert body["email"] == TEST_EMAIL
    assert body["display_name"] == "Synthetic API User"
    assert "created_at" in body


def test_me_unknown_user_returns_404(db_client: TestClient) -> None:
    token = mint_token(sub="99999999-9999-9999-9999-999999999999", email="nobody@example.test")
    response = db_client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert response.json()["type"] == "urn:legacyflow:error:profile_not_found"
