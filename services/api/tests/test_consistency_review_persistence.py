"""Consistency review persistence (2026-10-27 production 500).

Root cause pinned here: the consistency reviewer's severity vocabulary
is {review, attention} — its system prompt asks the model for exactly
those two values, and the Oct 2026 benchmark's production-prompt runs
returned "attention" for 11 of 13 findings. But the ``escalations``
table CHECK (slice-2 migration, predating the consistency feature)
only allows {info, review, blocking}. The first time a real review
found a genuine contradiction and tried to persist its escalation,
Postgres raised a CHECK violation inside the request transaction and
the endpoint returned 500 — rolling back the whole review.

Why nothing caught it: the benchmark measured raw model output (no DB
writes) and the only consistency tests were model-config tests. No
test ever ran ``run_consistency_review`` against a real schema.

The fix has two parts, both pinned below:
* a migration extends the escalations severity CHECK with 'attention'
  (the API serializes severity as a plain string and the web type is
  open-ended, so the value is safe end to end; 'blocking' keeps its
  rules-engine-only meaning), and
* a single finding's escalation insert that still fails degrades to a
  logged skip (savepoint-isolated) instead of 500ing the whole review.

All fixture data is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import STORAGE_BUCKETS_STUB

from app.domains.escalation import repository as escalation_repo
from app.domains.interview import consistency

CLIENT = "e4444444-4444-4444-4444-444444444444"
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " interview_consistency_reviews, ai_traces, field_proposals,"
    " interview_turns, interview_turn_bodies, interview_sessions,"
    " interview_parked_items, intake_answers, matter_status_history,"
    " matters, invitations, nominations, planning_decisions,"
    " relationships, persons, household_members, households,"
    " firm_memberships, firms, profiles, feature_flags,"
    " attorney_applications, review_packets, approval_gates,"
    " comments, clarification_requests, asset_flags, assets,"
    " liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
""" + STORAGE_BUCKETS_STUB

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

ATTENTION_FINDING = {
    "question": (
        "The marital status is listed as single but a spouse name was"
        " provided — could you clarify?"
    ),
    "fields": ["marital_status", "spouse_full_name"],
    "severity": "attention",
}
REVIEW_FINDING = {
    "question": "Minor children are mentioned — could you confirm how many?",
    "fields": ["has_minor_children"],
    "severity": "review",
}


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-consistency")))
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
def openai_env(monkeypatch: pytest.MonkeyPatch):
    """Make run_consistency_review take the (stubbed) provider path."""
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _all(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def build_world(db) -> tuple[str, str]:
    """Firm + household + matter + in-progress session + tense answers.

    The answer set mirrors the production failure: marital_status says
    single while spouse_full_name is answered — a genuine contradiction
    of exactly the kind the reviewer rates "attention".
    Returns (matter_id, session_id).
    """
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
          (CLIENT, "client@example.test"))
    _exec(db, "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
              " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
          (CLIENT, "client@example.test", "Synthetic Client"))
    firm_id = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')",
          (firm_id, "Synthetic Firm", "Synthetic Firm"))
    household_id = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (household_id, firm_id, "Example Household", CLIENT))
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, NULL, 'primary_client', 'active')",
          (household_id, CLIENT))
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(db, "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
              " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
              " completeness_pct)"
              " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
          (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID,
           "Example Estate Plan", snapshot))
    session_id = str(uuid4())
    _exec(db, "INSERT INTO interview_sessions (id, matter_id, firm_id, question_set_id,"
              " pack_version, status)"
              " VALUES (%s, %s, %s, 'household', '2.0.0', 'in_progress')",
          (session_id, matter_id, firm_id))
    answers = {
        "testator_full_name": "Alex Example",
        "marital_status": "single",
        "spouse_full_name": "Jamie Example",
        "has_minor_children": True,
    }
    for key, value in answers.items():
        _exec(db, "INSERT INTO intake_answers (matter_id, question_key, answer)"
                  " VALUES (%s, %s, %s::jsonb)",
              (matter_id, key, json.dumps(value)))
    return matter_id, session_id


class _StubProvider:
    def __init__(self, findings: list[dict]) -> None:
        self._findings = findings

    def complete_json(self, *, system_prompt, user_content, temperature, timeout, agent="json-completion"):
        return (
            json.dumps({"findings": self._findings}),
            {"prompt_tokens": 11, "completion_tokens": 7},
        )


def _stub_provider(monkeypatch: pytest.MonkeyPatch, findings: list[dict]) -> None:
    monkeypatch.setattr(
        consistency, "get_json_provider", lambda model: _StubProvider(findings)
    )


def test_attention_and_review_findings_persist(db, openai_env, monkeypatch):
    """The production 500: an 'attention' finding must persist, not 500."""
    matter_id, session_id = build_world(db)
    _stub_provider(monkeypatch, [ATTENTION_FINDING, REVIEW_FINDING])

    result = consistency.run_consistency_review(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )
    db.commit()

    assert result.facts_considered == 4
    assert {f.severity for f in result.findings} == {"attention", "review"}
    assert result.escalations_created == 2

    rows = _all(
        db,
        "SELECT severity FROM escalations WHERE matter_id = %s ORDER BY severity",
        (matter_id,),
    )
    assert [r["severity"] for r in rows] == ["attention", "review"]

    reviews = _all(
        db,
        "SELECT findings_count, escalations_created FROM interview_consistency_reviews"
        " WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1
    assert reviews[0]["findings_count"] == 2
    assert reviews[0]["escalations_created"] == 2

    # Re-running is idempotent: no duplicate escalations.
    again = consistency.run_consistency_review(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )
    db.commit()
    assert again.escalations_created == 0
    assert len(_all(db, "SELECT id FROM escalations WHERE matter_id = %s", (matter_id,))) == 2


class _RaisingProvider:
    def complete_json(self, *, system_prompt, user_content, temperature, timeout, agent="json-completion"):
        raise RuntimeError("provider is down")


class _GarbageProvider:
    def complete_json(self, *, system_prompt, user_content, temperature, timeout, agent="json-completion"):
        return ("this is not json at all", {"prompt_tokens": 3, "completion_tokens": 1})


def test_provider_failure_persists_degraded_review(db, openai_env, monkeypatch):
    """ERR-022: a provider failure must not 500 / roll back. Following
    the attorney-reviewer policy, the review persists degraded: facts
    considered recorded, zero findings, degraded marker set, and the
    model id does NOT claim the real model ran."""
    matter_id, session_id = build_world(db)
    monkeypatch.setattr(consistency, "get_json_provider", lambda model: _RaisingProvider())

    result = consistency.run_consistency_review(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )
    db.commit()

    assert result.degraded is True
    assert result.findings == []
    assert result.escalations_created == 0
    assert result.facts_considered == 4
    reviews = _all(
        db,
        "SELECT findings_count, facts_considered, model_id FROM"
        " interview_consistency_reviews WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1
    assert reviews[0]["findings_count"] == 0
    assert reviews[0]["facts_considered"] == 4
    assert reviews[0]["model_id"] != "gpt-4o-mini"  # never the configured model
    # M-2: a provider-failure degraded run must be distinguishable in
    # the DB from a legitimate never-configured deterministic run.
    assert reviews[0]["model_id"] == "provider_degraded"


def test_unparseable_provider_output_persists_degraded_review(db, openai_env, monkeypatch):
    """ERR-022: unparseable model output gets the same degraded
    treatment — not a silent zero-findings row wearing the real
    model id."""
    matter_id, session_id = build_world(db)
    monkeypatch.setattr(consistency, "get_json_provider", lambda model: _GarbageProvider())

    result = consistency.run_consistency_review(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )
    db.commit()

    assert result.degraded is True
    assert result.findings == []
    reviews = _all(
        db,
        "SELECT model_id FROM interview_consistency_reviews WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1
    assert reviews[0]["model_id"] == "provider_degraded"


def test_never_configured_deterministic_run_keeps_deterministic_dev(db, monkeypatch):
    """M-2 counterpart: when no provider was ever configured (the
    deterministic default), the persisted marker stays
    ``deterministic_dev`` and the run is NOT degraded — only
    provider-failure / unparseable runs wear ``provider_degraded``."""
    monkeypatch.setenv("AI_LLM_PROVIDER", "deterministic_dev")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        matter_id, session_id = build_world(db)
        result = consistency.run_consistency_review(
            db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
        )
        db.commit()
    finally:
        get_settings.cache_clear()

    assert result.degraded is False
    assert result.findings == []
    reviews = _all(
        db,
        "SELECT model_id FROM interview_consistency_reviews WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1
    assert reviews[0]["model_id"] == "deterministic_dev"


def test_phased_review_holds_no_transaction_during_provider_call(
    db, pg_uri, openai_env, monkeypatch
):
    """PERF-016: the phased path (what the router runs) must call the
    provider with NO database transaction open. Proven directly: the
    transaction factory is replaced with a tracking shim over real
    connections, and the stub provider records how many transactions
    are open at call time."""
    import contextlib

    import psycopg as _psycopg
    from psycopg.rows import dict_row as _dict_row

    from app.core import db as core_db

    matter_id, session_id = build_world(db)

    class _Tracker:
        def __init__(self) -> None:
            self.open = 0
            self.opened_total = 0

        @contextlib.contextmanager
        def transaction(self):
            with _psycopg.connect(pg_uri, row_factory=_dict_row) as conn:
                with conn.transaction():
                    self.open += 1
                    self.opened_total += 1
                    try:
                        yield conn
                    finally:
                        self.open -= 1

    tracker = _Tracker()
    monkeypatch.setattr(core_db, "transaction", tracker.transaction)

    seen: dict[str, int] = {}

    class _RecordingProvider:
        def complete_json(self, *, system_prompt, user_content, temperature, timeout, agent="json-completion"):
            seen["open_at_call"] = tracker.open
            return (
                json.dumps({"findings": [REVIEW_FINDING]}),
                {"prompt_tokens": 5, "completion_tokens": 2},
            )

    monkeypatch.setattr(
        consistency, "get_json_provider", lambda model: _RecordingProvider()
    )

    result = consistency.run_consistency_review_phased(
        actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )

    assert seen["open_at_call"] == 0, "provider called with a transaction open"
    assert tracker.opened_total == 2, "expected prepare + persist transactions only"
    assert result.facts_considered == 4
    assert len(result.findings) == 1
    reviews = _all(
        db,
        "SELECT id FROM interview_consistency_reviews WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1


def test_one_findings_insert_failure_degrades_instead_of_500(db, openai_env, monkeypatch):
    """A single escalation insert failure must not sink the whole review."""
    matter_id, session_id = build_world(db)
    _stub_provider(monkeypatch, [ATTENTION_FINDING, REVIEW_FINDING])

    original = escalation_repo.insert_escalation

    def flaky(conn, **kwargs):
        if kwargs["severity"] == "attention":
            # A genuinely failing SQL insert (invalid severity through
            # the real repository call): poisons the transaction with a
            # real CheckViolation, so this test pins the savepoint —
            # without it, the next finding's read would die with
            # InFailedSqlTransaction.
            kwargs["severity"] = "bogus_value"
        return original(conn, **kwargs)

    monkeypatch.setattr(escalation_repo, "insert_escalation", flaky)

    result = consistency.run_consistency_review(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), session_id=UUID(session_id)
    )
    db.commit()

    assert len(result.findings) == 2
    assert result.escalations_created == 1
    rows = _all(db, "SELECT severity FROM escalations WHERE matter_id = %s", (matter_id,))
    assert [r["severity"] for r in rows] == ["review"]
    reviews = _all(
        db,
        "SELECT id FROM interview_consistency_reviews WHERE session_id = %s",
        (session_id,),
    )
    assert len(reviews) == 1
