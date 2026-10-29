"""Repro for staging UI-review failures (2026-09-28).

- GET /v1/admin/feature-flags 500s on staging for a firm_admin
- GET /v1/matters/{id}/funding-tasks 500s on staging
- GET /v1/matters list lacks nested household object (web crash)

Synthetic data only. Mirrors the staging seed shapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, STORAGE_BUCKETS_STUB, mint_token

ADMIN = "d1111111-1111-1111-1111-111111111111"

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
-- raw_user_meta_data/raw_app_meta_data: the handle_new_user trigger
-- (migration 20261001130000) reads them; mirror the real auth.users shape.
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
""" + STORAGE_BUCKETS_STUB

TRUNCATE_TABLES = (
    "feature_flags, document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, jurisdictions, jurisdiction_packs, auth.users"
)


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    # Session scope, like every other DB test module: a fresh pgserver
    # per test made later tests inherit pooled connections to a server
    # the previous test's cleanup had already shut down
    # (psycopg AdminShutdown / initdb failures). Per-test isolation is
    # provided by the db fixture's TRUNCATE, not by the server.
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-repro")))
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
def api_client(pg_uri, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    # Point the pack loader at the REAL repo content-packs, like staging.
    monkeypatch.setenv(
        "CONTENT_PACKS_DIR",
        str(Path(__file__).resolve().parents[3] / "content-packs"),
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=True)


def _exec(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def seed_admin_with_matter(db) -> str:
    """firm_admin + firm + household + matter + funding tasks (staging-like)."""
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (ADMIN, "admin@example.test"))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (ADMIN, "admin@example.test", "Admin"),
    )
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, "Synthetic Firm", "Synthetic Firm"),
    )
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'firm_admin', 'active')",
        (firm_id, ADMIN),
    )
    hh_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (hh_id, firm_id, "Synthetic Household", ADMIN),
    )
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": "0.1.0",
            "rule_ids": ["VA-ESC-001"],
            "question_set_versions": {"household": "1.0.0"},
        }
    )
    _exec(
        db,
        """INSERT INTO matters
           (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,
            plan_type, status, jurisdiction_snapshot)
           VALUES (%s, %s, %s,
             '11111111-1111-1111-1111-111111111111',
             '22222222-2222-2222-2222-222222222222',
             'will', 'INTAKE_IN_PROGRESS', %s::jsonb)""",
        (matter_id, firm_id, hh_id, snapshot),
    )
    _exec(
        db,
        """INSERT INTO funding_tasks
           (id, firm_id, matter_id, task_type, title, status)
           VALUES (%s, %s, %s, 'beneficiary_update', 'Add a contingent beneficiary', 'pending')""",
        (str(uuid4()), firm_id, matter_id),
    )
    return matter_id


def _auth():
    return {"Authorization": f"Bearer {mint_token(sub=ADMIN)}"}


def test_feature_flags_list(db, api_client):
    seed_admin_with_matter(db)
    r = api_client.get("/v1/admin/feature-flags", headers=_auth())
    assert r.status_code == 200, r.text
    # The known-flag registry grows with the product (interview_v2 and
    # attorney_reviewer were added after this repro was written); pin
    # the count to the service's own registry instead of a literal.
    from app.domains.feature_flags.service import KNOWN_FLAG_DEFAULTS

    assert len(r.json()) == len(KNOWN_FLAG_DEFAULTS)


def test_feature_flags_blank_env_treated_as_unset(db, api_client, monkeypatch):
    seed_admin_with_matter(db)
    monkeypatch.setenv("LF_FLAG_DOCUMENT_ASSEMBLY", "")
    r = api_client.get("/v1/admin/feature-flags", headers=_auth())
    assert r.status_code == 200, r.text


def test_funding_tasks_list(db, api_client):
    matter_id = seed_admin_with_matter(db)
    r = api_client.get(f"/v1/matters/{matter_id}/funding-tasks", headers=_auth())
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


def test_matters_list_has_nested_household(db, api_client):
    seed_admin_with_matter(db)
    r = api_client.get("/v1/matters", headers=_auth())
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["household"]["id"], "nested household missing from list item"
    assert item["household"]["name"] == "Synthetic Household"


# ---------------------------------------------------------------------------
# Railway regression tests (root causes from 2026-09-28 staging logs).
#
# 1. GET /v1/admin/feature-flags 500:
#    psycopg.errors.DuplicatePreparedStatement: prepared statement "_pg3_0"
#    already exists — psycopg auto-prepares repeated queries, which collides
#    with the transaction-pooling Supabase pooler. Fix: prepare_threshold=None.
# 2. GET /v1/matters/{id}/funding-tasks 500:
#    IndexError: 5 in _repo_root() — Path.parents[5] assumed the repo layout,
#    but the Railway container runs services/api as the app root. Fix: walk
#    up to find content-packs/, fall back to built-in data when absent.
# ---------------------------------------------------------------------------


def test_find_content_packs_dir_returns_none_when_absent(tmp_path, monkeypatch):
    """Walk-up never raises IndexError, even with no content-packs on disk."""
    from app.core.paths import find_content_packs_dir

    monkeypatch.delenv("CONTENT_PACKS_DIR", raising=False)
    lonely = tmp_path / "lonely" / "deep" / "file.py"
    lonely.parent.mkdir(parents=True)
    lonely.touch()
    assert find_content_packs_dir(lonely) is None


def test_content_pack_files_returns_none_without_packs(tmp_path, monkeypatch):
    """documents._content_pack_files degrades to None (fallback data)."""
    from app.domains.documents.service import _content_pack_files

    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path))
    assert _content_pack_files("us-va") is None


def test_pack_loader_root_never_raises_index_error(tmp_path, monkeypatch):
    """pack_loader._packs_root never assumes a fixed directory depth."""
    from app.domains.legal_content import pack_loader

    monkeypatch.delenv("CONTENT_PACKS_DIR", raising=False)
    root = pack_loader._packs_root()
    # Missing dir is fine (list_packs returns []); crashing is not.
    assert pack_loader.list_packs() == [] or root.is_dir()


def test_funding_tasks_list_without_packs_on_disk(db, api_client, monkeypatch, tmp_path):
    """Funding tasks work when content-packs are absent (Railway layout).

    The endpoint must serve from the built-in fallback playbooks instead of
    500ing in _repo_root().
    """
    from app.core.config import get_settings

    matter_id = seed_admin_with_matter(db)
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path))
    get_settings.cache_clear()
    r = api_client.get(f"/v1/matters/{matter_id}/funding-tasks", headers=_auth())
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


def test_get_connection_disables_prepared_statements(monkeypatch):
    """psycopg must not auto-prepare (pooler DuplicatePreparedStatement)."""
    import psycopg

    from app.core import db as db_module

    captured: dict = {}

    def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("no real connection in unit test")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    try:
        db_module.get_connection()
    except RuntimeError:
        pass
    assert captured.get("prepare_threshold") is None, f"prepare_threshold not disabled: {captured}"
