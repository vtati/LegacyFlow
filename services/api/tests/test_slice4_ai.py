"""Slice 4 tests: the grounded AI gateway (``POST /v1/matters/{id}/ai/ask``).

Runs against a local pgserver instance (never ``DATABASE_URL`` or live
Supabase). Applies the Slice-1 and Slice-2 migrations plus a fallback DDL
block for the Track-A4 ``ai_interactions`` table when Track A's Slice-4
migration file is absent.

The app under test is ``create_app()`` plus the AI router mounted under
``/v1`` — exactly the wiring the coordinator will add to
``app/api/v1/router.py``.

All fixture data is synthetic. Raw prompts are never asserted on beyond
their summaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SLICE1_MIGRATION = REPO_ROOT / "supabase" / "migrations" / "20260926220000_foundation_slice1.sql"
SLICE2_MIGRATION = (
    REPO_ROOT / "supabase" / "migrations" / "20260927010000_household_intake_slice2.sql"
)
SLICE4_MIGRATION = REPO_ROOT / "supabase" / "migrations" / "20260927040000_ai_gateway_slice4.sql"

# Fallback DDL used when the Slice-4 migration file is absent (Track A
# writes it in parallel). Mirrors the contract this track builds against:
# id / firm_id / matter_id / user_id / question_summary / answer_summary /
# citations (jsonb) / trace_hash / created_at.
AI_FALLBACK_DDL = """
CREATE TABLE IF NOT EXISTS ai_interactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  question_summary text NOT NULL,
  answer_summary text NOT NULL,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  trace_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_interactions_matter_id ON ai_interactions (matter_id);
CREATE INDEX IF NOT EXISTS idx_ai_interactions_trace_hash ON ai_interactions (trace_hash);
"""

# ---------------------------------------------------------------------------
# Synthetic identities (module scope; uuid4 = synthetic by construction)
# ---------------------------------------------------------------------------

FIRM_ID = uuid.uuid4()
HOUSEHOLD_ID = uuid.uuid4()
VA_JUR_ID = UUID("11111111-1111-1111-1111-111111111111")
XZ_JUR_ID = UUID("33333333-3333-3333-3333-333333333333")
VA_PACK_ID = UUID("22222222-2222-2222-2222-222222222222")
XZ_PACK_ID = UUID("44444444-4444-4444-4444-444444444444")

ATTORNEY_SUB = str(uuid.uuid4())
PRIMARY_SUB = str(uuid.uuid4())
OUTSIDER_SUB = str(uuid.uuid4())

VA_SNAPSHOT = {
    "pack_id": "us-va",
    "jurisdiction_code": "US-VA",
    "semantic_version": "0.1.0",
}
XZ_SNAPSHOT = {
    "pack_id": "us-xz-synthetic",
    "jurisdiction_code": "US-XZ",
    "semantic_version": "1.0.0",
}

VA_MATTER_ID = uuid.uuid4()
XZ_MATTER_ID = uuid.uuid4()

GROUNDED_QUESTION = "Tell me about naming a guardian for minor children."
ADVICE_QUESTION = "Should I name my brother as guardian for my kids?"
OUT_OF_PACK_QUESTION = "What is the capital gains tax treatment of staking rewards?"

_DSN = ""


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


def _apply_schema(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS auth")
            cur.execute("CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY)")
            cur.execute(
                "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid"
                " LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$"
            )
            cur.execute(SLICE1_MIGRATION.read_text())
            cur.execute(SLICE2_MIGRATION.read_text())
            if SLICE4_MIGRATION.exists():
                cur.execute(SLICE4_MIGRATION.read_text())
            else:
                cur.execute(AI_FALLBACK_DDL)


def _seed_core() -> None:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for sub in (ATTORNEY_SUB, PRIMARY_SUB, OUTSIDER_SUB):
                cur.execute("INSERT INTO auth.users (id) VALUES (%s)", (sub,))
                cur.execute(
                    "INSERT INTO profiles (id, display_name, email) VALUES (%s, %s, %s)",
                    (sub, f"Synthetic User {sub[:8]}", f"synthetic-{sub[:8]}@example.com"),
                )
            cur.execute(
                "INSERT INTO firms (id, legal_name, display_name, status)"
                " VALUES (%s, %s, %s, 'active')",
                (str(FIRM_ID), "Synthetic Firm LLC", "Synthetic Firm"),
            )
            cur.execute(
                "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
                " VALUES (%s, %s, 'attorney', 'active')",
                (str(FIRM_ID), ATTORNEY_SUB),
            )
            cur.execute(
                "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
                " VALUES (%s, %s, %s, %s)",
                (str(HOUSEHOLD_ID), str(FIRM_ID), "Synthetic Household", ATTORNEY_SUB),
            )
            cur.execute(
                "INSERT INTO household_members"
                " (household_id, user_id, relationship_role, access_status)"
                " VALUES (%s, %s, 'primary_client', 'active')",
                (str(HOUSEHOLD_ID), PRIMARY_SUB),
            )
            for jur_id, cc, sc, name in (
                (VA_JUR_ID, "US", "VA", "Virginia"),
                (XZ_JUR_ID, "US", "XZ", "Xanadu (synthetic)"),
            ):
                cur.execute(
                    "INSERT INTO jurisdictions"
                    " (id, country_code, subdivision_code, display_name,"
                    "  jurisdiction_type, is_enabled)"
                    " VALUES (%s, %s, %s, %s, 'us_state', true)",
                    (str(jur_id), cc, sc, name),
                )
            for pack_id, jur_id, version in (
                (VA_PACK_ID, VA_JUR_ID, "0.1.0"),
                (XZ_PACK_ID, XZ_JUR_ID, "1.0.0"),
            ):
                cur.execute(
                    "INSERT INTO jurisdiction_packs"
                    " (id, jurisdiction_id, version, status, effective_from,"
                    "  manifest, source_bundle_hash)"
                    " VALUES (%s, %s, %s, 'internal_testing', '2026-09-26',"
                    "         '{}'::jsonb, 'synthetic')",
                    (str(pack_id), str(jur_id), version),
                )
            for matter_id, jur_id, pack_id, snapshot in (
                (VA_MATTER_ID, VA_JUR_ID, VA_PACK_ID, VA_SNAPSHOT),
                (XZ_MATTER_ID, XZ_JUR_ID, XZ_PACK_ID, XZ_SNAPSHOT),
            ):
                cur.execute(
                    "INSERT INTO matters"
                    " (id, firm_id, household_id, primary_jurisdiction_id,"
                    "  jurisdiction_pack_id, plan_type, status, title,"
                    "  jurisdiction_snapshot, completeness_pct)"
                    " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS',"
                    "         %s, %s::jsonb, 0)",
                    (
                        str(matter_id),
                        str(FIRM_ID),
                        str(HOUSEHOLD_ID),
                        str(jur_id),
                        str(pack_id),
                        f"Synthetic Matter {matter_id}",
                        json.dumps(snapshot),
                    ),
                )


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    """Throwaway pgserver instance with Slice-1/2 schema applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-slice4-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            _apply_schema(dsn)
            global _DSN
            _DSN = dsn
            _seed_core()
            yield dsn
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="module")
def _env(pg_dsn: str) -> Iterator[None]:
    """Point the app at the test JWT override and the pgserver DSN."""
    from app.core.config import get_settings

    old_environ = dict(os.environ)
    os.environ["TEST_JWT_ISSUER"] = TEST_ISSUER
    os.environ["TEST_JWT_SECRET"] = TEST_SECRET
    os.environ["SUPABASE_JWT_AUDIENCE"] = TEST_AUDIENCE
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
    os.environ["DATABASE_URL"] = pg_dsn
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_JWKS_URL", None)
    os.environ.pop("AI_ALLOW_SAMPLE_CONTENT", None)
    os.environ.pop("AI_PROVIDER", None)
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def test_app(_env: None) -> FastAPI:
    """App with the AI router mounted under /v1."""
    from app.domains.ai.router import router as ai_router
    from app.main import create_app

    application = create_app()
    application.include_router(ai_router, prefix="/v1")
    return application


@pytest.fixture(scope="module")
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub)}"}


def _ask(client: TestClient, matter_id: UUID, question: str, sub: str = PRIMARY_SUB) -> Any:
    return client.post(
        f"/v1/matters/{matter_id}/ai/ask",
        json={"question": question},
        headers=_headers(sub),
    )


def _db_fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def _latest_interaction(matter_id: UUID) -> dict[str, Any]:
    rows = _db_fetchall(
        "SELECT * FROM ai_interactions WHERE matter_id = %s ORDER BY created_at DESC LIMIT 1",
        (str(matter_id),),
    )
    assert rows, "expected an ai_interactions row"
    return rows[0]


def _open_escalations(matter_id: UUID, rule_id: str) -> list[dict[str, Any]]:
    return _db_fetchall(
        "SELECT * FROM escalations WHERE matter_id = %s AND rule_id = %s AND status = 'open'",
        (str(matter_id), rule_id),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_grounded_answer_has_citations_from_correct_pack(client: TestClient) -> None:
    resp = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refused"] is False
    assert body["citations"], "grounded answer must carry citations"
    assert all(cite["source"] == "us-va" for cite in body["citations"])
    assert all(cite["excerpt"] for cite in body["citations"])
    assert "guardian" in body["answer"].lower()
    assert len(body["trace_hash"]) == 64


def test_xz_pack_isolation(client: TestClient) -> None:
    """A VA-flavored question on an XZ matter must not surface VA content."""
    resp = _ask(client, XZ_MATTER_ID, GROUNDED_QUESTION)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The XZ pack ships no knowledge docs, so the question escalates rather
    # than being answered — critically, with zero VA citations.
    assert body["refused"] is False
    assert body["citations"] == []
    assert "us-va" not in body["answer"]
    assert "guardian-basics" not in body["answer"]


def test_refusal_for_should_i_questions(client: TestClient) -> None:
    resp = _ask(client, VA_MATTER_ID, ADVICE_QUESTION)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert "attorney" in body["answer"].lower()
    escalations = _open_escalations(VA_MATTER_ID, "ai_advice_request")
    assert escalations, "advice request must create an escalation record"
    assert escalations[0]["severity"] == "review"


def test_out_of_pack_question_escalates(client: TestClient) -> None:
    resp = _ask(client, VA_MATTER_ID, OUT_OF_PACK_QUESTION)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refused"] is False
    assert body["citations"] == []
    assert "attorney" in body["answer"].lower()
    escalations = _open_escalations(VA_MATTER_ID, "ai_out_of_pack")
    assert escalations, "out-of-pack question must create an escalation record"
    interaction = _latest_interaction(VA_MATTER_ID)
    assert interaction["question_summary"] == OUT_OF_PACK_QUESTION[:200]


def test_trace_hash_determinism(client: TestClient) -> None:
    first = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION).json()
    second = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION).json()
    assert first["trace_hash"] == second["trace_hash"]
    expected = hashlib.sha256(f"{GROUNDED_QUESTION}|0.1.0|guardian-basics-v1".encode()).hexdigest()
    assert first["trace_hash"] == expected


def test_interactions_store_summaries_not_raw(client: TestClient) -> None:
    long_question = GROUNDED_QUESTION + " " + "background context " * 30
    assert 200 < len(long_question) <= 1000
    resp = _ask(client, VA_MATTER_ID, long_question)
    assert resp.status_code == 200, resp.text
    answer = resp.json()["answer"]
    interaction = _latest_interaction(VA_MATTER_ID)
    assert interaction["question_summary"] == long_question[:200]
    assert interaction["answer_summary"] == answer[:200]
    assert len(interaction["question_summary"]) == 200
    assert len(interaction["answer_summary"]) <= 200
    # The raw full-length question must not be stored anywhere in the row.
    assert interaction["question_summary"] != long_question
    for value in interaction.values():
        if isinstance(value, str):
            assert long_question not in value


def test_sample_content_excluded_when_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI_ALLOW_SAMPLE_CONTENT=false removes the only VA knowledge doc."""
    monkeypatch.setenv("AI_ALLOW_SAMPLE_CONTENT", "false")
    resp = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refused"] is False
    assert body["citations"] == []
    assert "attorney" in body["answer"].lower()


def test_production_provider_returns_501(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "production")
    resp = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION)
    assert resp.status_code == 501, resp.text
    assert resp.json()["code"] == "configuration_error"


def test_question_length_validation(client: TestClient) -> None:
    resp = _ask(client, VA_MATTER_ID, "x" * 1001)
    assert resp.status_code == 422


def test_outsider_cannot_ask(client: TestClient) -> None:
    """Existence-oracle: callers with no access see 404, not 403."""
    resp = _ask(client, VA_MATTER_ID, GROUNDED_QUESTION, sub=OUTSIDER_SUB)
    assert resp.status_code == 404
