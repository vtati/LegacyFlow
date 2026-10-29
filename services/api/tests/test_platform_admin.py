"""SEC-008/SEC-009: platform-admin gate for firm creation + pack admin.

Semantics under test:
  * PLATFORM_ADMIN_USER_IDS non-empty -> only listed users are admins
    (in any environment).
  * Allowlist empty + non-production -> any authenticated user counts
    (preserves dev/test flows and the pre-existing suite).
  * Allowlist empty + production -> NOBODY is a platform admin.
  * Imported packs are forced to status 'draft'; effective_from must
    parse as a date and not be absurdly future (> 366 days -> 422).

API tests run against pgserver with synthetic data only.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

PLATFORM_ADMIN = "f1111111-1111-1111-1111-111111111111"
PLAIN_USER = "f2222222-2222-2222-2222-222222222222"
FIRM_ADMIN = "f3333333-3333-3333-3333-333333333333"

USERS = {
    PLATFORM_ADMIN: "platform.admin@example.test",
    PLAIN_USER: "plain@example.test",
    FIRM_ADMIN: "firm.admin@example.test",
}

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean
);
"""

TRUNCATE_TABLES = (
    "audit_events, escalations, intake_answers, matter_status_history, matters,"
    " invitations, nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles, auth.users"
)


# ---------------------------------------------------------------------------
# Unit: is_platform_admin / require_platform_admin semantics
# ---------------------------------------------------------------------------


def _fake_settings(monkeypatch, *, allowlist: str, api_env: str = "test", environment: str = "test"):
    import app.core.authz as authz

    fake = SimpleNamespace(
        PLATFORM_ADMIN_USER_IDS=allowlist, API_ENV=api_env, ENVIRONMENT=environment
    )
    monkeypatch.setattr(authz, "get_settings", lambda: fake)


def test_listed_user_is_platform_admin(monkeypatch):
    from app.core.authz import is_platform_admin

    _fake_settings(monkeypatch, allowlist=PLATFORM_ADMIN)
    assert is_platform_admin(UUID(PLATFORM_ADMIN)) is True
    assert is_platform_admin(UUID(PLAIN_USER)) is False


def test_empty_allowlist_nonproduction_any_user(monkeypatch):
    from app.core.authz import is_platform_admin

    _fake_settings(monkeypatch, allowlist="")
    assert is_platform_admin(UUID(PLAIN_USER)) is True


def test_empty_allowlist_production_nobody(monkeypatch):
    from app.core.authz import is_platform_admin, require_platform_admin
    from app.core.errors import AuthorizationError

    _fake_settings(monkeypatch, allowlist="", api_env="production")
    assert is_platform_admin(UUID(PLAIN_USER)) is False
    with pytest.raises(AuthorizationError):
        require_platform_admin(UUID(PLAIN_USER))


def test_empty_allowlist_production_via_environment(monkeypatch):
    from app.core.authz import is_platform_admin

    _fake_settings(monkeypatch, allowlist="", environment="production")
    assert is_platform_admin(UUID(PLAIN_USER)) is False


# ---------------------------------------------------------------------------
# Unit: manifest validation — forced draft + effective_from guard
# ---------------------------------------------------------------------------


def _manifest(**pack_overrides):
    pack = {
        "id": "us-va",
        "country_code": "US",
        "subdivision_code": "VA",
        "semantic_version": "9.9.9",
        "status": "approved",
        "effective_from": "2026-09-26",
    }
    pack.update(pack_overrides)
    return {"pack": pack}


def test_import_manifest_status_forced_to_draft():
    from app.domains.review.service import _validate_manifest

    normalized = _validate_manifest(_manifest())
    assert normalized["status"] == "draft"


def test_import_manifest_absurd_future_effective_from_rejected():
    from app.core.errors import ValidationAppError
    from app.domains.review.service import _validate_manifest

    with pytest.raises(ValidationAppError):
        _validate_manifest(_manifest(effective_from="2099-01-01"))


def test_import_manifest_unparseable_effective_from_rejected():
    from app.core.errors import ValidationAppError
    from app.domains.review.service import _validate_manifest

    with pytest.raises(ValidationAppError):
        _validate_manifest(_manifest(effective_from="not-a-date"))


# ---------------------------------------------------------------------------
# API: firm creation + pack endpoints under an allowlist in force
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-platform-admin")))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
    yield uri
    server.cleanup()


@pytest.fixture()
def db(pg_uri: str):
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    # Allowlist in force: only PLATFORM_ADMIN is a platform admin.
    monkeypatch.setenv("PLATFORM_ADMIN_USER_IDS", PLATFORM_ADMIN)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _create_user(db, sub: str) -> None:
    email = USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (sub, email, f"Synthetic {email}"),
    )


def _auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, email=USERS[sub])}"}


def test_plain_user_cannot_create_firm_with_allowlist_in_force(api_client, db) -> None:
    _create_user(db, PLAIN_USER)
    resp = api_client.post(
        "/v1/firms",
        json={"legal_name": "Evil LLC", "display_name": "Evil"},
        headers=_auth(PLAIN_USER),
    )
    assert resp.status_code == 403, resp.text


def test_platform_admin_can_create_firm(api_client, db) -> None:
    _create_user(db, PLATFORM_ADMIN)
    resp = api_client.post(
        "/v1/firms",
        json={"legal_name": "Platform Firm LLC", "display_name": "Platform Firm"},
        headers=_auth(PLATFORM_ADMIN),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["display_name"] == "Platform Firm"


def test_firm_admin_cannot_transition_pack_with_allowlist_in_force(api_client, db) -> None:
    _create_user(db, FIRM_ADMIN)
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, "Self Made LLC", "Self Made"),
    )
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'firm_admin', 'active')",
        (firm_id, FIRM_ADMIN),
    )
    resp = api_client.post(
        "/v1/admin/packs/us-va/transition",
        json={"to_status": "retired"},
        headers=_auth(FIRM_ADMIN),
    )
    assert resp.status_code == 403, resp.text


def test_platform_admin_import_pack_with_unquoted_effective_from(api_client, db, tmp_path) -> None:
    # M-1 regression: an UNQUOTED YAML effective_from is loaded by
    # yaml.safe_load as a datetime.date object, not a string. Import
    # must succeed (not 500 on json.dumps of the raw manifest) and the
    # stored row must carry the normalized ISO date string.
    _create_user(db, PLATFORM_ADMIN)
    pack_dir = tmp_path / "unquoted-pack"
    pack_dir.mkdir()
    (pack_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "pack:",
                "  id: us-zz-unquoted",
                "  country_code: US",
                "  subdivision_code: ZZ",
                "  display_name: Zedland (synthetic)",
                "  semantic_version: 1.0.0",
                "  status: approved",
                "  effective_from: 2026-09-26",
                "capabilities: {}",
                "requirements: {}",
                "sources: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    resp = api_client.post(
        "/v1/admin/packs/import",
        data={"pack_dir": str(pack_dir)},
        headers=_auth(PLATFORM_ADMIN),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pack_id"] == "us-zz-unquoted"
    assert body["jurisdiction_code"] == "US-ZZ"
    assert body["version"] == "1.0.0"
    assert body["status"] == "draft"
    with db.cursor() as cur:
        cur.execute(
            "SELECT jp.* FROM jurisdiction_packs jp"
            " JOIN jurisdictions j ON j.id = jp.jurisdiction_id"
            " WHERE j.country_code = 'US' AND j.subdivision_code = 'ZZ'"
        )
        row = cur.fetchone()
    assert row is not None
    assert row["status"] == "draft"
    assert str(row["effective_from"]) == "2026-09-26"
    stored_manifest = row["manifest"]
    if isinstance(stored_manifest, str):
        import json

        stored_manifest = json.loads(stored_manifest)
    assert stored_manifest["pack"]["effective_from"] == "2026-09-26"
    assert stored_manifest["pack"]["id"] == "us-zz-unquoted"


def test_firm_admin_cannot_import_pack_with_allowlist_in_force(api_client, db) -> None:
    _create_user(db, FIRM_ADMIN)
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, "Self Made LLC", "Self Made"),
    )
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'firm_admin', 'active')",
        (firm_id, FIRM_ADMIN),
    )
    resp = api_client.post(
        "/v1/admin/packs/import",
        data={"pack_dir": "/nonexistent"},
        headers=_auth(FIRM_ADMIN),
    )
    assert resp.status_code == 403, resp.text
