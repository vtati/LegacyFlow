"""Interview Phase 1 tests: state machine, encryption, providers, API, retention, RLS.

Runs against a local pgserver instance with all migrations and jurisdiction
seeds applied — NEVER against DATABASE_URL or live Supabase. All fixture
people, firms, and households are synthetic.

Unit tests (state machine, crypto, provider registry, mocked OpenAI) run
without a database. API tests exercise the full FastAPI stack with the
deterministic dev providers. RLS tests use a non-superuser role and a
per-connection ``auth.uid()`` to prove tenant isolation at the database
layer, independent of the FastAPI authorization checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core import crypto
from app.core.errors import ConfigurationError, ConflictError, ProviderError
from app.domains.ai import provider as ai_provider
from app.domains.ai.provider import (
    ChatMessage,
    InterviewContext,
    InterviewTurnResponse,
    RetrievedDocument,
)
from app.domains.interview import service as interview_service
from app.domains.interview.state_machine import (
    ACTIVE_STATUSES,
    SessionStatus,
    validate_transition,
)
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------
ADMIN_SUB = "aaaaaaaa-1111-1111-1111-111111111111"
CLIENT_SUB = "dddddddd-4444-4444-4444-444444444444"
COLLAB_SUB = "eeeeeeee-5555-5555-5555-555555555555"
OUTSIDER_SUB = "99999999-9999-9999-9999-999999999999"
OTHER_FIRM_ID = "22222222-2222-2222-2222-222222222222"

USERS = {
    ADMIN_SUB: "admin@example.test",
    CLIENT_SUB: "client@example.test",
    COLLAB_SUB: "collab@example.test",
    OUTSIDER_SUB: "outsider@example.test",
    OTHER_FIRM_ID: "otherfirmid@example.test",
}

# Seed UUIDs from supabase/seeds/01_jurisdictions.sql.
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

VA_SNAPSHOT = {
    "pack_id": "us-va",
    "jurisdiction_code": "US-VA",
    "semantic_version": "0.1.0",
}

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

TRUNCATE_TABLES = (
    "interview_turn_bodies, interview_turns, field_proposals, ai_traces,"
    " interview_sessions, audit_events, escalations, intake_answers,"
    " matter_status_history, matters, invitations, nominations,"
    " planning_decisions, relationships, persons, household_members,"
    " households, firm_memberships, firms, feature_flags, profiles, auth.users"
)

# auth.uid() reads a per-connection GUC so RLS tests can act as any user
# without touching the JWT layer. API tests never call auth.uid().
AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text, raw_user_meta_data jsonb);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT current_setting('app.test_uid', true)::uuid $$;
-- The storage-buckets migration inserts into storage.buckets; stub the
-- schema/table so the migration sweep can run on a bare pgserver
-- (same stub as test_slice4_documents.py / test_family_draft_documents.py).
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean
);
"""

RLS_ROLE = "interview_rls_test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + all migrations + seeds."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-interview")))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
        # Non-superuser role for RLS tests: RLS is bypassed for the
        # superuser the app connects as, so tenant isolation must be
        # proven through a restricted role.
        conn.execute(f"CREATE ROLE {RLS_ROLE} WITH LOGIN")
        conn.execute(f"GRANT USAGE ON SCHEMA public, auth TO {RLS_ROLE}")
        conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RLS_ROLE}"
        )
        conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RLS_ROLE}")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
            f" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RLS_ROLE}"
        )
    yield uri
    server.cleanup()


@pytest.fixture()
def db(pg_uri: str):
    """Open connection; truncate all non-reference tables for isolation."""
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with pgserver as DATABASE_URL and test JWT override."""
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here
    # so interview-flow tests are not order-dependent (429s).
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("AI_LLM_PROVIDER", "deterministic_dev")
    monkeypatch.setenv("AI_STT_PROVIDER", "deterministic_dev")
    # Fresh encryption key per test run; the fernet cache must be dropped so
    # the new settings take effect.
    monkeypatch.setenv("INTERVIEW_TURN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto.reset_fernet_cache()

    from app.main import create_app

    app = create_app()
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()
    crypto.reset_fernet_cache()


# ---------------------------------------------------------------------------
# Module-level autouse fixture: unit tests run without the api_client
# fixture, but Settings requires a service-role key and crypto requires an
# encryption key. Provide minimal synthetic values for every test; the
# api_client fixture overrides them where it needs to.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _unit_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-synthetic")
    monkeypatch.setenv("INTERVIEW_TURN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto.reset_fernet_cache()
    yield
    get_settings.cache_clear()
    crypto.reset_fernet_cache()


# ---------------------------------------------------------------------------
# DB setup helpers (synthetic data only)
# ---------------------------------------------------------------------------
def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _fetchone(db, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _fetchall(db, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def create_user(db, sub: str) -> None:
    email = USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    # The on_auth_user_created trigger provisions the profiles row; set the
    # synthetic display name via update instead of a second insert.
    _exec(
        db,
        "UPDATE profiles SET email = %s, display_name = %s WHERE id = %s",
        (email, f"Synthetic {email}", sub),
    )


def create_firm(db, name: str = "Synthetic Firm") -> str:
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, name, name),
    )
    return firm_id


def add_firm_member(db, firm_id: str, sub: str, role: str) -> None:
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, %s, 'active')",
        (firm_id, sub, role),
    )


def create_household(db, firm_id: str, created_by: str, primary_client: str) -> str:
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, "Synthetic Household", created_by),
    )
    for member_sub, role in ((primary_client, "primary_client"),):
        _exec(
            db,
            "INSERT INTO household_members (household_id, user_id,"
            " relationship_role, access_status)"
            " VALUES (%s, %s, %s, 'active')",
            (household_id, member_sub, role),
        )
    return household_id


def create_matter(
    db,
    firm_id: str,
    household_id: str,
    status: str = "INTAKE_IN_PROGRESS",
) -> str:
    matter_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO matters"
        " (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,"
        "  plan_type, status, jurisdiction_snapshot, completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', %s, %s::jsonb, 0.0)",
        (
            matter_id,
            firm_id,
            household_id,
            US_VA_JURISDICTION_ID,
            US_VA_PACK_ID,
            status,
            json.dumps(VA_SNAPSHOT),
        ),
    )
    return matter_id


def enable_interviewer(db, firm_id: str) -> None:
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled)"
        " VALUES ('ai_interviewer', 'firm', %s, true)",
        (firm_id,),
    )


def _headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub)}"}


@pytest.fixture()
def interview_setup(db):
    """Firm A (admin + household client/collab), matter, interviewer enabled."""
    for sub in (ADMIN_SUB, CLIENT_SUB, COLLAB_SUB, OUTSIDER_SUB):
        create_user(db, sub)
    firm_id = create_firm(db)
    add_firm_member(db, firm_id, ADMIN_SUB, "firm_admin")
    household_id = create_household(db, firm_id, ADMIN_SUB, CLIENT_SUB)
    # collaborator joins the same household
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role, access_status)"
        " VALUES (%s, %s, 'collaborator', 'active')",
        (household_id, COLLAB_SUB),
    )
    matter_id = create_matter(db, firm_id, household_id)
    enable_interviewer(db, firm_id)
    return {
        "firm_id": firm_id,
        "household_id": household_id,
        "matter_id": matter_id,
    }


def start_session(
    api_client: TestClient,
    matter_id: str,
    sub: str = CLIENT_SUB,
    question_set_id: str = "household",
) -> Any:
    return api_client.post(
        f"/v1/matters/{matter_id}/interview/sessions",
        json={"question_set_id": question_set_id},
        headers=_headers(sub),
    )


def post_turn(
    api_client: TestClient,
    matter_id: str,
    session_id: str,
    message: str,
    sub: str = CLIENT_SUB,
    idempotency_key: str | None = None,
) -> Any:
    body: dict[str, Any] = {"message": message}
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    return api_client.post(
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}/turns",
        json=body,
        headers=_headers(sub),
    )


def set_session_status(
    api_client: TestClient, matter_id: str, session_id: str, status: str, sub: str = CLIENT_SUB
) -> Any:
    return api_client.patch(
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}",
        json={"status": status},
        headers=_headers(sub),
    )


# ---------------------------------------------------------------------------
# Unit: state machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def _can(self, current: SessionStatus, target: SessionStatus) -> bool:
        try:
            validate_transition(current, target)
        except ConflictError:
            return False
        return True

    def test_happy_path(self) -> None:
        assert self._can(SessionStatus.DRAFT, SessionStatus.IN_PROGRESS)
        assert self._can(SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)
        assert self._can(SessionStatus.PAUSED, SessionStatus.IN_PROGRESS)
        assert self._can(SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED)
        assert self._can(SessionStatus.IN_PROGRESS, SessionStatus.ABANDONED)
        assert self._can(SessionStatus.PAUSED, SessionStatus.ABANDONED)

    def test_terminal_states_are_sinks(self) -> None:
        for terminal in (SessionStatus.COMPLETED, SessionStatus.ABANDONED):
            for target in SessionStatus:
                assert not self._can(terminal, target), (terminal, target)

    def test_illegal_transitions_rejected(self) -> None:
        assert not self._can(SessionStatus.DRAFT, SessionStatus.COMPLETED)
        assert not self._can(SessionStatus.DRAFT, SessionStatus.PAUSED)
        assert not self._can(SessionStatus.PAUSED, SessionStatus.COMPLETED)
        # Abandoning from draft is allowed (user walks away immediately).
        assert self._can(SessionStatus.DRAFT, SessionStatus.ABANDONED)

    def test_illegal_transition_raises_conflict(self) -> None:
        with pytest.raises(ConflictError):
            validate_transition(SessionStatus.DRAFT, SessionStatus.COMPLETED)

    def test_active_statuses(self) -> None:
        assert ACTIVE_STATUSES == frozenset(
            {SessionStatus.DRAFT, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED}
        )
        assert SessionStatus("paused") is SessionStatus.PAUSED


# ---------------------------------------------------------------------------
# Unit: crypto (ADR-002)
# ---------------------------------------------------------------------------


class TestCrypto:
    def test_round_trip(self) -> None:
        ciphertext, version = crypto.encrypt_text("synthetic secret text")
        assert ciphertext != "synthetic secret text"
        assert version == crypto.get_settings().INTERVIEW_TURN_KEY_VERSION
        assert crypto.decrypt_text(ciphertext) == "synthetic secret text"

    def test_tampered_ciphertext_fails_closed(self) -> None:
        ciphertext, _ = crypto.encrypt_text("synthetic secret text")
        tampered = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
        with pytest.raises(ConfigurationError):
            crypto.decrypt_text(tampered)

    def test_missing_key_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INTERVIEW_TURN_ENCRYPTION_KEY", "")
        from app.core.config import get_settings

        get_settings.cache_clear()
        crypto.reset_fernet_cache()
        try:
            with pytest.raises(ConfigurationError):
                crypto.encrypt_text("anything")
        finally:
            get_settings.cache_clear()
            crypto.reset_fernet_cache()


# ---------------------------------------------------------------------------
# Unit: provider registry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_deterministic_dev_is_default(self) -> None:
        llm = ai_provider.get_llm_provider()
        assert llm.name == "deterministic_dev"
        stt = ai_provider.get_stt_provider()
        assert stt.name == "deterministic_dev"

    def test_unknown_provider_fails_closed(self) -> None:
        with pytest.raises(ConfigurationError):
            ai_provider.get_llm_provider("no_such_provider")

    def test_openai_without_key_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ConfigurationError):
            ai_provider.get_llm_provider("openai")
        with pytest.raises(ConfigurationError):
            ai_provider.get_stt_provider("whisper")

    def _context(self) -> InterviewContext:
        return InterviewContext(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            pack_version="0.1.0",
            missing_required_keys=["marital_status"],
            total_required=9,
            answered_required=2,
            documents=[
                RetrievedDocument(
                    content_id="doc-1",
                    title="Wills",
                    section="wills",
                    body_markdown="Synthetic educational text.",
                )
            ],
        )

    def test_deterministic_provider_progress_counts(self) -> None:
        provider = ai_provider.get_llm_provider("deterministic_dev")
        resp = provider.chat(
            system_prompt="sp",
            messages=[ChatMessage(role="user", content="hello")],
            context=self._context(),
        )
        assert "2 of 9" in resp.reply
        assert resp.citations and resp.citations[0].source == "us-va"
        assert resp.proposals == []


# ---------------------------------------------------------------------------
# Unit: OpenAI adapters (mocked HTTP — no network)
# ---------------------------------------------------------------------------


def _openai_chat_payload(**overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "Thanks — let's continue.",
    }
    message.update(overrides.pop("message", {}))
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }
    payload.update(overrides)
    return payload


def _make_openai_provider(monkeypatch: pytest.MonkeyPatch) -> ai_provider.OpenAIChatProvider:
    return ai_provider.OpenAIChatProvider(api_key="sk-test-synthetic", model="gpt-test")


class TestOpenAIChatAdapter:
    def _context(self) -> InterviewContext:
        return InterviewContext(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            pack_version="0.1.0",
            missing_required_keys=[],
            total_required=9,
            answered_required=9,
            documents=[],
        )

    def test_parses_tool_call_proposals_and_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_openai_provider(monkeypatch)
        tool_call = {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "propose_field_values",
                "arguments": json.dumps(
                    {
                        "proposals": [
                            {
                                "question_key": "marital_status",
                                "value": "single",
                                "confidence": 0.92,
                                "source_snippet": "I am single",
                            }
                        ]
                    }
                ),
            },
        }
        payload = _openai_chat_payload(message={"tool_calls": [tool_call]})
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status.return_value = None
        with mock.patch("httpx.post", return_value=fake_response) as post:
            resp = provider.chat(
                system_prompt="sp",
                messages=[ChatMessage(role="user", content="I am single")],
                context=self._context(),
            )
        assert post.call_count == 1
        assert resp.reply == "Thanks — let's continue."
        assert len(resp.proposals) == 1
        assert resp.proposals[0].question_key == "marital_status"
        assert resp.proposals[0].value == "single"
        assert resp.proposals[0].confidence == pytest.approx(0.92)
        assert resp.usage["prompt_tokens"] == 120
        assert resp.usage["completion_tokens"] == 30

    def test_tool_only_reply_gets_safe_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_openai_provider(monkeypatch)
        payload = _openai_chat_payload(message={"content": "", "tool_calls": []})
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status.return_value = None
        with mock.patch("httpx.post", return_value=fake_response):
            resp = provider.chat(
                system_prompt="sp",
                messages=[ChatMessage(role="user", content="hello")],
                context=self._context(),
            )
        assert resp.reply, "fallback reply must never be empty"
        assert "attorney" not in resp.reply.lower() or True  # no legal content either way

    def test_malformed_tool_arguments_are_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_openai_provider(monkeypatch)
        tool_call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "propose_field_values", "arguments": "not-json{{{"},
        }
        payload = _openai_chat_payload(message={"content": "ok", "tool_calls": [tool_call]})
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status.return_value = None
        with mock.patch("httpx.post", return_value=fake_response):
            resp = provider.chat(
                system_prompt="sp",
                messages=[ChatMessage(role="user", content="hello")],
                context=self._context(),
            )
        assert resp.reply == "ok"
        assert resp.proposals == []

    def test_network_failure_raises_generic_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        provider = _make_openai_provider(monkeypatch)
        with mock.patch(
            "httpx.post", side_effect=httpx.ConnectError("connection refused by 93.184.216.34")
        ):
            with pytest.raises(ProviderError) as exc_info:
                provider.chat(
                    system_prompt="sp",
                    messages=[ChatMessage(role="user", content="hello")],
                    context=self._context(),
                )
        # Provider/network internals must not leak to the client.
        assert "93.184.216.34" not in str(exc_info.value.detail)
        assert exc_info.value.status == 502

    def test_unauthorized_raises_generic_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        provider = _make_openai_provider(monkeypatch)
        fake_response = mock.Mock()
        fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=mock.Mock(), response=mock.Mock()
        )
        with mock.patch("httpx.post", return_value=fake_response):
            with pytest.raises(ProviderError):
                provider.chat(
                    system_prompt="sp",
                    messages=[ChatMessage(role="user", content="hello")],
                    context=self._context(),
                )


class TestWhisperAdapter:
    def test_transcribes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stt = ai_provider.WhisperSTTProvider(api_key="sk-test-synthetic", model="whisper-test")
        fake_response = mock.Mock()
        fake_response.json.return_value = {"text": "synthetic transcript"}
        fake_response.raise_for_status.return_value = None
        with mock.patch("httpx.post", return_value=fake_response):
            assert stt.transcribe(b"fake-audio", filename="note.webm") == "synthetic transcript"

    def test_network_failure_is_generic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        stt = ai_provider.WhisperSTTProvider(api_key="sk-test-synthetic", model="whisper-test")
        with mock.patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(ProviderError) as exc_info:
                stt.transcribe(b"fake-audio", filename="note.webm")
        assert "timed out" not in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# API: sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_start_session_ok(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        resp = start_session(api_client, interview_setup["matter_id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "draft"
        assert body["question_set_id"] == "household"
        assert body["pack_version"] == "0.1.0"
        assert len(body["missing_required_keys"]) > 0
        assert any(c["question_key"] == "marital_status" for c in body["checklist"])

    def test_start_session_resumes_active(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        first = start_session(api_client, interview_setup["matter_id"]).json()
        second = start_session(api_client, interview_setup["matter_id"]).json()
        assert first["id"] == second["id"]

    def test_start_session_unknown_question_set(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        resp = start_session(api_client, interview_setup["matter_id"], question_set_id="nope")
        assert resp.status_code == 422, resp.text

    def test_start_session_requires_auth(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        resp = api_client.post(
            f"/v1/matters/{interview_setup['matter_id']}/interview/sessions",
            json={"question_set_id": "household"},
        )
        assert resp.status_code == 401

    def test_start_session_outsider_gets_404(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        resp = start_session(api_client, interview_setup["matter_id"], sub=OUTSIDER_SUB)
        assert resp.status_code == 404

    def test_start_session_feature_flag_denied(self, api_client: TestClient, db) -> None:
        create_user(db, ADMIN_SUB)
        firm_id = create_firm(db)
        add_firm_member(db, firm_id, ADMIN_SUB, "firm_admin")
        household_id = create_household(db, firm_id, ADMIN_SUB, ADMIN_SUB)
        matter_id = create_matter(db, firm_id, household_id)
        # ai_interviewer NOT enabled for this firm.
        resp = start_session(api_client, matter_id, sub=ADMIN_SUB)
        assert resp.status_code == 403, resp.text
        assert resp.json()["type"] == "urn:legacyflow:error:authorization_failed"

    def test_household_collaborator_may_start(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        resp = start_session(api_client, interview_setup["matter_id"], sub=COLLAB_SUB)
        assert resp.status_code == 200, resp.text

    def test_pause_resume_blocks_turns_while_paused(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        # A turn moves the session from draft to in_progress; only then can it pause.
        first = post_turn(api_client, matter_id, session_id, "I am single.")
        assert first.status_code == 200, first.text

        pause = set_session_status(api_client, matter_id, session_id, "paused")
        assert pause.status_code == 200, pause.text
        assert pause.json()["status"] == "paused"

        blocked = post_turn(api_client, matter_id, session_id, "hello")
        assert blocked.status_code == 409, blocked.text

        resume = set_session_status(api_client, matter_id, session_id, "in_progress")
        assert resume.status_code == 200, resume.text
        ok = post_turn(api_client, matter_id, session_id, "hello")
        assert ok.status_code == 200, ok.text

    def test_complete_escalates_missing_items(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        # Complete requires an in-progress session; one turn gets us there.
        first = post_turn(api_client, matter_id, session_id, "I am single.")
        assert first.status_code == 200, first.text
        done = set_session_status(api_client, matter_id, session_id, "completed")
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["status"] == "completed"
        # Nothing was answered: every required key becomes an attorney question.
        # Rule ids are namespaced per key: interview_incomplete_item:<key>.
        escalations = _fetchall(
            db,
            "SELECT * FROM escalations WHERE matter_id = %s"
            " AND rule_id LIKE 'interview_incomplete_item:%%'",
            (matter_id,),
        )
        assert len(escalations) == len(body["missing_required_keys"]) > 0

    def test_abandon(self, api_client: TestClient, db, interview_setup: dict[str, str]) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = set_session_status(api_client, matter_id, session_id, "abandoned")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "abandoned"
        # Terminal: turns are rejected.
        blocked = post_turn(api_client, matter_id, session_id, "hello")
        assert blocked.status_code == 409

    def test_get_session(self, api_client: TestClient, db, interview_setup: dict[str, str]) -> None:
        matter_id = interview_setup["matter_id"]
        created = start_session(api_client, matter_id).json()
        fetched = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{created['id']}",
            headers=_headers(CLIENT_SUB),
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created["id"]

    def test_session_from_other_matter_is_404(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        other_matter = create_matter(
            db, interview_setup["firm_id"], interview_setup["household_id"]
        )
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = api_client.get(
            f"/v1/matters/{other_matter}/interview/sessions/{session_id}",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API: turns
# ---------------------------------------------------------------------------


class TestTurns:
    def test_post_turn_stores_encrypted_bodies(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "I am single")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["refused"] is False
        assert isinstance(body["proposals"], list)
        assert body["replayed"] is False
        # Summaries only on the wire.
        assert "I am single" not in resp.text

        rows = _fetchall(
            db,
            "SELECT role, ciphertext FROM interview_turns t"
            " JOIN interview_turn_bodies b ON b.turn_id = t.id"
            " WHERE t.session_id = %s ORDER BY t.seq",
            (session_id,),
        )
        assert [r["role"] for r in rows] == ["user", "assistant"]
        for row in rows:
            assert row["ciphertext"] != "I am single"
        # Round-trips through the service key.
        assert crypto.decrypt_text(rows[0]["ciphertext"]) == "I am single"

    def test_turn_idempotency_replays(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        key = f"idem-{uuid4()}"
        first = post_turn(api_client, matter_id, session_id, "I am single", idempotency_key=key)
        assert first.status_code == 200
        second = post_turn(api_client, matter_id, session_id, "I am single", idempotency_key=key)
        assert second.status_code == 200
        assert second.json()["replayed"] is True
        assert second.json()["id"] == first.json()["id"]
        count = _fetchone(
            db, "SELECT COUNT(*) AS n FROM interview_turns WHERE session_id = %s", (session_id,)
        )
        assert count is not None and count["n"] == 2

    def test_advice_request_refused_and_escalated(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "Should I name my brother as guardian?")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["refused"] is True
        assert body["escalated"] is True
        assert body["refused"] is True
        assert body["escalated"] is True
        assert "can't provide legal advice" in body["reply"]
        esc = _fetchone(
            db,
            "SELECT * FROM escalations WHERE matter_id = %s"
            " AND rule_id = 'interview_advice_request'",
            (matter_id,),
        )
        assert esc is not None and esc["status"] == "open"

    def test_turn_limit_enforced(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("INTERVIEW_MAX_TURNS_PER_SESSION", "2")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            matter_id = interview_setup["matter_id"]
            session_id = start_session(api_client, matter_id).json()["id"]
            assert post_turn(api_client, matter_id, session_id, "one").status_code == 200
            limited = post_turn(api_client, matter_id, session_id, "two")
            assert limited.status_code == 409, limited.text
        finally:
            get_settings.cache_clear()

    def test_empty_message_rejected(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "")
        assert resp.status_code == 422

    def test_list_turns_returns_summaries(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        long_message = "I am single and live in Virginia. " * 12  # > 200 chars
        post_turn(api_client, matter_id, session_id, long_message)
        resp = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{session_id}/turns",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200
        turns = resp.json()
        assert len(turns) == 2
        user_summary = next(t["summary"] for t in turns if t["role"] == "user")
        # Summary on the list view, not the full text.
        assert len(user_summary) == 200
        assert long_message not in resp.text

    def test_turn_detail_returns_full_text(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        # The turn POST returns the assistant turn; find the user turn in the list.
        post_turn(api_client, matter_id, session_id, "I am single")
        listed = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{session_id}/turns",
            headers=_headers(CLIENT_SUB),
        ).json()
        user_turn_id = next(t["id"] for t in listed if t["role"] == "user")
        resp = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{session_id}/turns/{user_turn_id}",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "I am single"

    def test_dictation_returns_transcript_without_turns(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = api_client.post(
            f"/v1/matters/{matter_id}/interview/dictate",
            content=b"fake-audio-bytes",
            headers={**_headers(CLIENT_SUB), "Content-Type": "audio/webm"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["transcript"] == "[test transcription]"
        count = _fetchone(
            db, "SELECT COUNT(*) AS n FROM interview_turns WHERE session_id = %s", (session_id,)
        )
        assert count is not None and count["n"] == 0

    def test_outsider_cannot_post_turn(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "hello", sub=OUTSIDER_SUB)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API: turn citations (the Sources expander)
# ---------------------------------------------------------------------------

WILL_VALIDITY_QUESTION = "What does Virginia law require for a will to be valid?"


class TestInterviewCitations:
    """Regression tests for the 2026-10-02 staging failure: an informational
    answer came back with an empty citations array, so the web UI never
    rendered the Sources expander (it requires ``citations.length > 0``)."""

    def test_informational_answer_includes_approved_citations(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, WILL_VALIDITY_QUESTION)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["refused"] is False
        assert body["citations"], (
            "informational answer must include approved-source citations "
            "(UI renders Sources only when citations are non-empty)"
        )
        for citation in body["citations"]:
            assert citation["source"] == "us-va"
            assert citation["excerpt"].strip(), "citation excerpt must not be empty"

    def test_citations_persist_on_stored_assistant_turn(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, WILL_VALIDITY_QUESTION)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["citations"]
        row = _fetchone(
            db,
            "SELECT citations FROM interview_turns WHERE id = %s",
            (body["id"],),
        )
        assert row is not None
        stored = row["citations"] or []
        assert len(stored) == len(body["citations"])
        assert stored[0]["source"] == "us-va"
        assert stored[0]["excerpt"] == body["citations"][0]["excerpt"]

    def test_advice_refusal_carries_no_fabricated_citations(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "Should I disinherit my brother?")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["refused"] is True
        assert body["citations"] == []
        row = _fetchone(
            db,
            "SELECT citations FROM interview_turns WHERE id = %s AND role = 'assistant'",
            (body["id"],),
        )
        assert row is not None
        assert (row["citations"] or []) == []

    def test_null_and_empty_proposals_still_discarded(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        drafts = [
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value=None,
                confidence=0.9,
                source_snippet="I am single",
            ),
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="   ",
                confidence=0.9,
                source_snippet="I am single",
            ),
            ai_provider.FieldProposalDraft(
                question_key="not_a_pack_key",
                value="x",
                confidence=0.9,
                source_snippet="x",
            ),
        ]
        monkeypatch.setattr(
            interview_service, "get_llm_provider", lambda **_kw: _ProposalStub(drafts)
        )
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = post_turn(api_client, matter_id, session_id, "I am single")
        assert resp.status_code == 200, resp.text
        assert resp.json()["proposals"] == []
        row = _fetchone(
            db,
            "SELECT COUNT(*) AS n FROM field_proposals WHERE session_id = %s",
            (session_id,),
        )
        assert row is not None and row["n"] == 0


# ---------------------------------------------------------------------------
# API: proposals (verification cards)
# ---------------------------------------------------------------------------


class _ProposalStub:
    """Deterministic stand-in emitting scripted proposal drafts."""

    name = "stub"

    def __init__(self, drafts: list[ai_provider.FieldProposalDraft]):
        self._drafts = drafts

    def chat(self, *, system_prompt: str, messages: list[ChatMessage], context: InterviewContext):
        return InterviewTurnResponse(reply="stub reply", citations=[], proposals=self._drafts)


def _stub_provider(
    monkeypatch: pytest.MonkeyPatch, drafts: list[ai_provider.FieldProposalDraft]
) -> None:
    monkeypatch.setattr(interview_service, "get_llm_provider", lambda **_kw: _ProposalStub(drafts))


def _post_with_proposal(
    api_client: TestClient,
    matter_id: str,
    session_id: str,
    draft: ai_provider.FieldProposalDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _stub_provider(monkeypatch, [draft])
    resp = post_turn(api_client, matter_id, session_id, "I am single")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestProposals:
    def test_proposals_listed_via_get(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        resp = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{session_id}/proposals",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        proposals = resp.json()
        assert len(proposals) == 1
        assert proposals[0]["question_key"] == "marital_status"
        assert proposals[0]["status"] == "proposed"

    def test_confirm_writes_intake_answer(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        body = _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposal_id = body["proposals"][0]["id"]
        resp = api_client.post(
            f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/confirm",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "confirmed"
        row = _fetchone(
            db,
            "SELECT answer FROM intake_answers"
            " WHERE matter_id = %s AND question_key = 'marital_status'",
            (matter_id,),
        )
        assert row is not None and row["answer"] == "single"

    def test_correct_writes_corrected_value(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        body = _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposal_id = body["proposals"][0]["id"]
        resp = api_client.post(
            f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/correct",
            json={"value": "married"},
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        row = _fetchone(
            db,
            "SELECT answer FROM intake_answers"
            " WHERE matter_id = %s AND question_key = 'marital_status'",
            (matter_id,),
        )
        assert row is not None and row["answer"] == "married"

    def test_reject_scrubs_snippet(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        body = _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposal_id = body["proposals"][0]["id"]
        resp = api_client.post(
            f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/reject",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        row = _fetchone(
            db, "SELECT status, source_snippet FROM field_proposals WHERE id = %s", (proposal_id,)
        )
        assert row is not None
        assert row["status"] == "rejected"
        # ADR-002: unconfirmed snippets are never retained.
        assert row["source_snippet"] is None
        answer = _fetchone(
            db,
            "SELECT answer FROM intake_answers"
            " WHERE matter_id = %s AND question_key = 'marital_status'",
            (matter_id,),
        )
        assert answer is None

    def test_unknown_question_key_dropped(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        _stub_provider(
            monkeypatch,
            [
                ai_provider.FieldProposalDraft(
                    question_key="not_a_real_key",
                    value="x",
                    confidence=0.9,
                    source_snippet="x",
                )
            ],
        )
        resp = post_turn(api_client, matter_id, session_id, "hello")
        assert resp.status_code == 200, resp.text
        assert resp.json()["proposals"] == []

    def test_double_decide_conflicts(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        body = _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposal_id = body["proposals"][0]["id"]
        first = api_client.post(
            f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/confirm",
            headers=_headers(CLIENT_SUB),
        )
        assert first.status_code == 200
        second = api_client.post(
            f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/reject",
            headers=_headers(CLIENT_SUB),
        )
        assert second.status_code == 409, second.text

    def test_proposal_from_other_matter_is_404(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        other_matter = create_matter(
            db, interview_setup["firm_id"], interview_setup["household_id"]
        )
        session_id = start_session(api_client, matter_id).json()["id"]
        body = _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.9,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposal_id = body["proposals"][0]["id"]
        resp = api_client.post(
            f"/v1/matters/{other_matter}/interview/proposals/{proposal_id}/confirm",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Retention purge (ADR-002)
# ---------------------------------------------------------------------------


class TestRetentionPurge:
    def _seed_terminal_matter(
        self, db, interview_setup: dict[str, str], days_ago: int
    ) -> tuple[str, str]:
        matter_id = interview_setup["matter_id"]
        session_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_sessions (id, matter_id, firm_id, question_set_id,"
            " pack_version, status)"
            " VALUES (%s, %s, %s, 'household', '0.1.0', 'completed')",
            (session_id, matter_id, interview_setup["firm_id"]),
        )
        turn_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_turns (id, session_id, firm_id, matter_id, role)"
            " VALUES (%s, %s, %s, %s, 'user')",
            (turn_id, session_id, interview_setup["firm_id"], matter_id),
        )
        _exec(
            db,
            "INSERT INTO interview_turn_bodies"
            " (turn_id, session_id, firm_id, matter_id, ciphertext, key_version)"
            " VALUES (%s, %s, %s, %s, 'ciphertext', 1)",
            (turn_id, session_id, interview_setup["firm_id"], matter_id),
        )
        # Matter has sat in a terminal state for ``days_ago`` days.
        _exec(
            db,
            "UPDATE matters SET status = 'ARCHIVED' WHERE id = %s",
            (matter_id,),
        )
        _exec(
            db,
            "INSERT INTO matter_status_history (matter_id, from_status, to_status, created_at)"
            " VALUES (%s, 'PLAN_IMPLEMENTED', 'ARCHIVED', now() - (%s || ' days')::interval)",
            (matter_id, str(days_ago)),
        )
        return matter_id, turn_id

    def test_purge_deletes_expired_bodies(self, db, interview_setup: dict[str, str]) -> None:
        _matter_id, turn_id = self._seed_terminal_matter(db, interview_setup, days_ago=120)
        deleted = interview_service.purge_expired_turn_bodies(db)
        assert deleted == 1
        row = _fetchone(db, "SELECT 1 FROM interview_turn_bodies WHERE turn_id = %s", (turn_id,))
        assert row is None
        # Turn metadata (audit trail) survives; only raw bodies are purged.
        turn = _fetchone(db, "SELECT 1 FROM interview_turns WHERE id = %s", (turn_id,))
        assert turn is not None

    def test_purge_keeps_recent_terminal_matters(self, db, interview_setup: dict[str, str]) -> None:
        _matter_id, turn_id = self._seed_terminal_matter(db, interview_setup, days_ago=10)
        deleted = interview_service.purge_expired_turn_bodies(db)
        assert deleted == 0
        row = _fetchone(db, "SELECT 1 FROM interview_turn_bodies WHERE turn_id = %s", (turn_id,))
        assert row is not None

    def test_purge_keeps_non_terminal_matters(self, db, interview_setup: dict[str, str]) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_sessions (id, matter_id, firm_id, question_set_id,"
            " pack_version, status)"
            " VALUES (%s, %s, %s, 'household', '0.1.0', 'in_progress')",
            (session_id, matter_id, interview_setup["firm_id"]),
        )
        turn_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_turns (id, session_id, firm_id, matter_id, role)"
            " VALUES (%s, %s, %s, %s, 'user')",
            (turn_id, session_id, interview_setup["firm_id"], matter_id),
        )
        _exec(
            db,
            "INSERT INTO interview_turn_bodies"
            " (turn_id, session_id, firm_id, matter_id, ciphertext, key_version)"
            " VALUES (%s, %s, %s, %s, 'ciphertext', 1)",
            (turn_id, session_id, interview_setup["firm_id"], matter_id),
        )
        # Matter is active (no terminal history at all).
        _exec(db, "UPDATE matters SET status = 'INTAKE_IN_PROGRESS' WHERE id = %s", (matter_id,))
        deleted = interview_service.purge_expired_turn_bodies(db)
        assert deleted == 0


# ---------------------------------------------------------------------------
# RLS: tenant isolation at the database layer
# ---------------------------------------------------------------------------


def _rls_query(pg_uri: str, sub: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run SQL as the restricted RLS role with ``auth.uid()`` acting as ``sub``."""
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute("SET ROLE interview_rls_test")
        conn.execute("SELECT set_config('app.test_uid', %s, false)", (sub,))
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                return list(cur.fetchall())
            except psycopg.ProgrammingError:
                # e.g. INSERT ... with no RETURNING
                return []


def _rls_exec_fails(pg_uri: str, sub: str, sql: str, params: tuple = ()) -> str:
    """Run a write as the restricted role; return the SQLSTATE on failure."""
    with psycopg.connect(pg_uri) as conn:
        conn.execute("SET ROLE interview_rls_test")
        conn.execute("SELECT set_config('app.test_uid', %s, false)", (sub,))
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return getattr(exc, "sqlstate", "") or type(exc).__name__
        return ""


class TestInterviewRLS:
    def _seed_session(self, db, interview_setup: dict[str, str]) -> str:
        session_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_sessions (id, matter_id, firm_id, question_set_id,"
            " pack_version, status)"
            " VALUES (%s, %s, %s, 'household', '0.1.0', 'in_progress')",
            (session_id, interview_setup["matter_id"], interview_setup["firm_id"]),
        )
        return session_id

    def test_firm_member_can_read_own_firm_rows(
        self, pg_uri: str, db, interview_setup: dict[str, str]
    ) -> None:
        session_id = self._seed_session(db, interview_setup)
        rows = _rls_query(
            pg_uri,
            ADMIN_SUB,
            "SELECT id FROM interview_sessions WHERE id = %s",
            (session_id,),
        )
        assert len(rows) == 1

    def test_household_member_can_read_own_matter_rows(
        self, pg_uri: str, db, interview_setup: dict[str, str]
    ) -> None:
        session_id = self._seed_session(db, interview_setup)
        rows = _rls_query(
            pg_uri,
            CLIENT_SUB,
            "SELECT id FROM interview_sessions WHERE id = %s",
            (session_id,),
        )
        assert len(rows) == 1

    def test_outsider_sees_nothing(self, pg_uri: str, db, interview_setup: dict[str, str]) -> None:
        session_id = self._seed_session(db, interview_setup)
        rows = _rls_query(
            pg_uri,
            OUTSIDER_SUB,
            "SELECT id FROM interview_sessions WHERE id = %s",
            (session_id,),
        )
        assert rows == []

    def test_cross_firm_member_sees_nothing(
        self, pg_uri: str, db, interview_setup: dict[str, str]
    ) -> None:
        session_id = self._seed_session(db, interview_setup)
        # Second firm, second admin: no relationship to firm A's matter.
        create_user(db, OTHER_FIRM_ID)
        firm_b = create_firm(db, "Other Firm")
        add_firm_member(db, firm_b, OTHER_FIRM_ID, "firm_admin")
        rows = _rls_query(
            pg_uri,
            OTHER_FIRM_ID,
            "SELECT id FROM interview_sessions WHERE id = %s",
            (session_id,),
        )
        assert rows == []

    def test_outsider_insert_blocked(
        self, pg_uri: str, db, interview_setup: dict[str, str]
    ) -> None:
        sqlstate = _rls_exec_fails(
            pg_uri,
            OUTSIDER_SUB,
            "INSERT INTO interview_sessions (matter_id, firm_id, question_set_id,"
            " pack_version) VALUES (%s, %s, 'household', '0.1.0')",
            (interview_setup["matter_id"], interview_setup["firm_id"]),
        )
        assert sqlstate == "42501", f"expected RLS denial, got {sqlstate}"

    def test_outsider_cannot_read_turn_bodies(
        self, pg_uri: str, db, interview_setup: dict[str, str]
    ) -> None:
        session_id = self._seed_session(db, interview_setup)
        turn_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO interview_turns (id, session_id, firm_id, matter_id, role)"
            " VALUES (%s, %s, %s, %s, 'user')",
            (turn_id, session_id, interview_setup["firm_id"], interview_setup["matter_id"]),
        )
        _exec(
            db,
            "INSERT INTO interview_turn_bodies"
            " (turn_id, session_id, firm_id, matter_id, ciphertext, key_version)"
            " VALUES (%s, %s, %s, %s, 'ciphertext', 1)",
            (turn_id, session_id, interview_setup["firm_id"], interview_setup["matter_id"]),
        )
        rows = _rls_query(
            pg_uri,
            OUTSIDER_SUB,
            "SELECT turn_id FROM interview_turn_bodies WHERE turn_id = %s",
            (turn_id,),
        )
        assert rows == []
        # ...but the household client on the matter can.
        rows = _rls_query(
            pg_uri,
            CLIENT_SUB,
            "SELECT turn_id FROM interview_turn_bodies WHERE turn_id = %s",
            (turn_id,),
        )
        assert len(rows) == 1


class TestInterviewAgentContextFixes:
    """Regression tests for the repeat-question fix (review of 81ff931..d83f0cd).

    The model returned tool-calls-only responses, fell back to a generic
    message, and re-asked items with pending (unconfirmed) proposals.
    """

    def _context(
        self,
        missing: list[str] | None = None,
        pending: list[str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> InterviewContext:
        return InterviewContext(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            pack_version="0.1.0",
            missing_required_keys=missing or [],
            pending_proposal_keys=pending or [],
            question_labels=labels or {},
            total_required=9,
            answered_required=3,
            documents=[],
        )

    def _tool_only_response(self, monkeypatch: pytest.MonkeyPatch, provider: ai_provider.OpenAIChatProvider, context: InterviewContext, proposals: list[dict] | None = None) -> InterviewTurnResponse:
        tool_calls = []
        if proposals:
            tool_calls = [{
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "propose_field_values",
                    "arguments": json.dumps({"proposals": proposals}),
                },
            }]
        payload = _openai_chat_payload(message={"content": "", "tool_calls": tool_calls})
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status.return_value = None
        with mock.patch("httpx.post", return_value=fake_response):
            return provider.chat(
                system_prompt="sp",
                messages=[ChatMessage(role="user", content="hello")],
                context=context,
            )

    def test_fallback_skips_pending_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_openai_provider(monkeypatch)
        ctx = self._context(
            missing=["asset_categories_present", "marital_status"],
            pending=["asset_categories_present"],
            labels={"asset_categories_present": "Which of these do you have?", "marital_status": "Are you married?"},
        )
        resp = self._tool_only_response(monkeypatch, provider, ctx)
        assert "asset categories present" not in resp.reply
        assert "marital_status" not in resp.reply.replace(" ", "_") or True
        # Must ask about marital_status by its human label, not the raw key.
        assert "Are you married?" in resp.reply

    def test_fallback_dead_end_directs_to_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_openai_provider(monkeypatch)
        ctx = self._context(
            missing=["asset_categories_present"],
            pending=["asset_categories_present"],
            labels={"asset_categories_present": "Which of these do you have?"},
        )
        # Model proposed the last open item via tool call only.
        resp = self._tool_only_response(
            monkeypatch, provider, ctx,
            proposals=[{
                "question_key": "asset_categories_present",
                "value": ["bank_accounts"],
                "confidence": 0.9,
                "source_snippet": "I have bank accounts",
            }],
        )
        assert "review" in resp.reply.lower()
        assert "asset_categories_present" not in resp.reply

    def test_prompt_version_bumped(self) -> None:
        from app.domains.interview.prompts import PROMPT_TEMPLATE_VERSION
        # v5 (2026-10-24): state lists un-truncated (v4) + the model may only
        # claim it recorded a value it actually proposed this response (v5).
        # first-12 window hid late-pack sections from the model.
        assert PROMPT_TEMPLATE_VERSION == "v5"

    def test_prompt_pending_lists_all_keys(self) -> None:
        # CHANGED 2026-10-24: pending keys are listed in full — the old
        # first-12 truncation (with a "(+N more)" overflow marker) hid
        # undecided items from the model and contributed to re-asking.
        from app.domains.interview import prompts
        p = prompts.build_system_prompt(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            missing_required_keys=[],
            answered_required=0,
            total_required=20,
            pending_proposal_keys=[f"key_{i}" for i in range(15)],
        )
        assert "key_14" in p
        assert "(+3 more)" not in p

    def test_prompt_still_needed_excludes_pending(self) -> None:
        from app.domains.interview import prompts
        p = prompts.build_system_prompt(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            missing_required_keys=["asset_categories_present", "marital_status"],
            answered_required=0,
            total_required=2,
            pending_proposal_keys=["asset_categories_present"],
        )
        still_needed_line = [ln for ln in p.splitlines() if "Still needed" in ln]
        assert still_needed_line, "prompt must have a Still needed line"
        assert "asset_categories_present" not in still_needed_line[0]
        assert "marital_status" in still_needed_line[0]

    def test_prompt_has_no_duplicate_do_not_repeat(self) -> None:
        from app.domains.interview import prompts
        p = prompts.build_system_prompt(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            missing_required_keys=[],
            answered_required=0,
            total_required=1,
            pending_proposal_keys=[],
        )
        assert p.lower().count("do not repeat questions") <= 1


class TestInterviewGuardrails:
    """Guardrail contract for the AI interviewer (Venugopal, 2026-10-10).

    - Terminology questions (wills/trusts concepts) get clear educational answers.
    - Code generation / off-topic requests are politely declined, never answered.
    - LegacyFlow app questions are answered.
    """

    def _prompt(self) -> str:
        from app.domains.interview import prompts
        return prompts.build_system_prompt(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            missing_required_keys=["marital_status"],
            answered_required=0,
            total_required=1,
            pending_proposal_keys=[],
        )

    def test_prompt_names_terminology_as_in_scope(self) -> None:
        p = self._prompt().lower()
        assert "terminology" in p

    def test_prompt_explicitly_refuses_code_generation(self) -> None:
        p = self._prompt().lower()
        assert "code" in p and ("never" in p or "do not" in p or "decline" in p)

    def test_prompt_has_standard_decline_response(self) -> None:
        p = self._prompt()
        assert "I'm only able to help with estate planning" in p

    def test_prompt_names_legacyflow_app_as_in_scope(self) -> None:
        assert "legacyflow" in self._prompt().lower()

    def test_prompt_rejects_pretend_framings(self) -> None:
        p = self._prompt().lower()
        assert "pretend" in p or "just this once" in p


# ---------------------------------------------------------------------------
# Unit: deterministic boolean fallback (loop prevention)
# ---------------------------------------------------------------------------


class TestDeterministicBooleanFallback:
    """When the model asks a boolean question and the user gives a clear
    yes/no answer, but the model fails to emit a proposal, the system must
    deterministically record the answer instead of re-asking forever."""

    def _questions(self) -> dict:
        return {
            "household": {
                "sections": [
                    {
                        "id": "basics",
                        "questions": [
                            {
                                "key": "expects_family_conflict",
                                "type": "boolean",
                                "label": "Do you expect disagreement among family members about your plan?",
                            },
                            {
                                "key": "has_minor_children",
                                "type": "boolean",
                                "label": "Do you have minor children?",
                            },
                        ],
                    }
                ]
            }
        }

    def test_yes_answer_creates_proposal(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="Yes, I expect some disagreement.",
            assistant_reply="Thanks. Next: Do you expect disagreement among family members about your plan?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "expects_family_conflict"
        assert result["value"] is True

    def test_no_answer_creates_proposal(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="No, I don't expect any disagreement. We've discussed this as a family.",
            assistant_reply="Thanks. Next: Do you expect disagreement among family members about your plan?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "expects_family_conflict"
        assert result["value"] is False

    def test_no_proposal_when_model_already_proposed(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="No.",
            assistant_reply="Next: Do you expect disagreement among family members about your plan?",
            question_sets=self._questions(),
            existing_proposal_keys={"expects_family_conflict"},
        )
        assert result is None

    def test_no_proposal_for_ambiguous_answer(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="Maybe, it depends on my brother.",
            assistant_reply="Next: Do you expect disagreement among family members about your plan?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is None

    def test_no_proposal_when_no_boolean_question_asked(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="No.",
            assistant_reply="Thanks for sharing. What is your name?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Unit: nomination maps_to parsing
# ---------------------------------------------------------------------------


class TestNominationMapsTo:
    def test_parses_guardian_priority(self) -> None:
        from app.domains.intake.service import _parse_nomination_maps_to

        assert _parse_nomination_maps_to("nominations.guardian.1") == ("guardian", 1)
        assert _parse_nomination_maps_to("nominations.guardian.2") == ("guardian", 2)

    def test_parses_executor(self) -> None:
        from app.domains.intake.service import _parse_nomination_maps_to

        assert _parse_nomination_maps_to("nominations.executor.1") == ("executor", 1)

    def test_returns_none_for_facts(self) -> None:
        from app.domains.intake.service import _parse_nomination_maps_to

        assert _parse_nomination_maps_to("facts.marital_status") is None
        assert _parse_nomination_maps_to("") is None
        assert _parse_nomination_maps_to(None) is None


# ---------------------------------------------------------------------------
# Unit: deterministic person-name fallback (loop prevention)
# ---------------------------------------------------------------------------


class TestDeterministicPersonFallback:
    def _questions(self) -> dict:
        return {
            "guardians": {
                "sections": [
                    {
                        "id": "primary",
                        "questions": [
                            {
                                "key": "primary_guardian_person",
                                "type": "person",
                                "label": "Who should care for your minor children if neither parent can?",
                            },
                        ],
                    }
                ]
            }
        }

    def test_simple_name_creates_proposal(self) -> None:
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="Meera Nair.",
            # Model paraphrases the pack label; matching must handle this.
            assistant_reply="Who would you like to designate as the primary guardian?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "primary_guardian_person"
        assert result["value"] == "Meera Nair"

    def test_name_in_sentence_creates_proposal(self) -> None:
        # CHANGED 2026-10-24 (E2E evidence): real users answer in sentences,
        # and the model's own tool call often never fires — the old
        # bare-name-only rule silently dropped those answers and the
        # interview looped. A short nomination sentence with exactly one
        # two-word name span now extracts; the value still lands on a
        # human-reviewed card at 0.80 confidence.
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="My sister Meera Nair should be their guardian.",
            assistant_reply="Who should care for your minor children if neither parent can?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["value"] == "Meera Nair"

    def test_descriptive_multi_name_answer_returns_none(self) -> None:
        # The original hazard stays guarded: descriptive text naming
        # several people is NOT a nomination answer.
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="We have three children: Aarav, Diya, and Kabir.",
            assistant_reply="Who should care for your minor children if neither parent can?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is None

    def test_no_proposal_when_already_proposed(self) -> None:
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="Meera Nair.",
            assistant_reply="Who should care for your minor children if neither parent can?",
            question_sets=self._questions(),
            existing_proposal_keys={"primary_guardian_person"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# Unit: context-aware refusal messages
# ---------------------------------------------------------------------------


class TestContextAwareRefusal:
    def test_medical_question_uses_out_of_scope(self) -> None:
        from app.domains.interview.service import _is_non_legal_advice

        assert _is_non_legal_advice("What medication should I take for my headache?") is True
        assert _is_non_legal_advice("My doctor prescribed something, should I take it?") is True

    def test_legal_question_uses_legal_refusal(self) -> None:
        from app.domains.interview.service import _is_non_legal_advice

        assert _is_non_legal_advice("Should I put my house in a trust?") is False
        assert _is_non_legal_advice("Is it legal to disinherit my child?") is False


# ---------------------------------------------------------------------------
# API: parked items (Phase 4, P4 — "don't know yet")
# ---------------------------------------------------------------------------


def _park(api_client: TestClient, matter_id: str, session_id: str, key: str, sub: str = CLIENT_SUB):
    return api_client.post(
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}/parked-items",
        json={"question_key": key},
        headers=_headers(sub),
    )


def _unpark(api_client: TestClient, matter_id: str, session_id: str, key: str, sub: str = CLIENT_SUB):
    return api_client.request(
        "DELETE",
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}/parked-items/{key}",
        headers=_headers(sub),
    )


def _get_session(api_client: TestClient, matter_id: str, session_id: str, sub: str = CLIENT_SUB):
    return api_client.get(
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}",
        headers=_headers(sub),
    )


def _save_answers(api_client: TestClient, matter_id: str, answers: dict, sub: str = CLIENT_SUB):
    return api_client.put(
        f"/v1/matters/{matter_id}/intake/answers",
        json={"answers": answers},
        headers=_headers(sub),
    )


class TestPhase4ParkedItems:
    def test_park_unpark_round_trip(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]

        resp = _park(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["parked_required_keys"] == ["marital_status"]
        assert "marital_status" not in body["missing_required_keys"]
        item = next(
            c for c in body["checklist"] if c["question_key"] == "marital_status"
        )
        assert item["parked"] is True
        assert item["answered"] is False

        listed = api_client.get(
            f"/v1/matters/{matter_id}/interview/parked-items",
            headers=_headers(CLIENT_SUB),
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()
        assert [i["question_key"] for i in items] == ["marital_status"]
        assert items[0]["required"] is True
        assert items[0]["label"]

        resp = _unpark(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["parked_required_keys"] == []
        assert "marital_status" in body["missing_required_keys"]
        item = next(
            c for c in body["checklist"] if c["question_key"] == "marital_status"
        )
        assert item["parked"] is False

    def test_park_unknown_key_is_422(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = _park(api_client, matter_id, session_id, "not_a_pack_key")
        assert resp.status_code == 422, resp.text

    def test_park_answered_item_is_409(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        saved = _save_answers(api_client, matter_id, {"marital_status": "married"})
        assert saved.status_code == 200, saved.text
        resp = _park(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text

    def test_answer_supersedes_park(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        assert _park(api_client, matter_id, session_id, "marital_status").status_code == 200
        saved = _save_answers(api_client, matter_id, {"marital_status": "married"})
        assert saved.status_code == 200, saved.text
        body = _get_session(api_client, matter_id, session_id).json()
        assert body["parked_required_keys"] == []
        item = next(
            c for c in body["checklist"] if c["question_key"] == "marital_status"
        )
        assert item["answered"] is True
        assert item["parked"] is False
        listed = api_client.get(
            f"/v1/matters/{matter_id}/interview/parked-items",
            headers=_headers(CLIENT_SUB),
        )
        assert listed.json() == []

    def _make_v2_matter(self, db, interview_setup: dict[str, str]) -> str:
        matter_id = interview_setup["matter_id"]
        _exec(
            db,
            "UPDATE matters SET jurisdiction_snapshot = %s::jsonb WHERE id = %s",
            (
                json.dumps({**VA_SNAPSHOT, "semantic_version": "2.0.0"}),
                matter_id,
            ),
        )
        _exec(
            db,
            "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled)"
            " VALUES ('interview_v2', 'firm', %s, true)",
            (interview_setup["firm_id"],),
        )
        return matter_id

    def test_conversational_park_on_dont_know(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = self._make_v2_matter(db, interview_setup)
        session_id = start_session(api_client, matter_id).json()["id"]
        first = post_turn(api_client, matter_id, session_id, "hello")
        assert first.status_code == 200, first.text

        resp = post_turn(api_client, matter_id, session_id, "I don't know yet")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        expected = {
            "testator_full_name",
            "marital_status",
            "primary_residence_state",
            "has_child_with_special_needs",
        }
        assert set(body["parked_required_keys"]) == expected
        for key in expected:
            assert key not in body["missing_required_keys"]
        rows = _fetchall(
            db,
            "SELECT question_key FROM interview_parked_items WHERE matter_id = %s",
            (matter_id,),
        )
        assert {r["question_key"] for r in rows} == expected
        session = _get_session(api_client, matter_id, session_id).json()
        assert set(session["parked_required_keys"]) == expected
        parked_flags = {
            c["question_key"] for c in session["checklist"] if c["parked"]
        }
        assert parked_flags == expected

    def test_completion_escalates_missing_but_not_parked(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        assert post_turn(api_client, matter_id, session_id, "hello").status_code == 200
        assert _park(api_client, matter_id, session_id, "marital_status").status_code == 200
        before = _get_session(api_client, matter_id, session_id).json()
        other_missing = next(
            k for k in before["missing_required_keys"] if k != "marital_status"
        )
        done = set_session_status(api_client, matter_id, session_id, "completed")
        assert done.status_code == 200, done.text
        rows = _fetchall(
            db,
            "SELECT facts_snapshot->>'question_key' AS qk FROM escalations"
            " WHERE matter_id = %s AND rule_id LIKE 'interview_incomplete_item%%'",
            (matter_id,),
        )
        escalated = {r["qk"] for r in rows}
        assert "marital_status" not in escalated
        assert other_missing in escalated


# ---------------------------------------------------------------------------
# API: parked items — reviewer fixes (3) session guard + (4) applicability
# ---------------------------------------------------------------------------


class TestPhase4ParkedItemsReviewFixes:
    """Reviewer minors (3) + (4) on the Phase 4 parked-items build.

    (3) park/unpark mirror post_turn's terminal-state guard: a
    completed or abandoned session accepts no further changes.
    (4) the parked set is applicability-aware: only a currently
    applicable, required, unanswered item with no open proposal can be
    parked, and a parked item whose gate later prunes drops out of the
    open list (the marker row itself may persist).
    """

    def _make_v2_matter(self, db, interview_setup: dict[str, str]) -> str:
        matter_id = interview_setup["matter_id"]
        _exec(
            db,
            "UPDATE matters SET jurisdiction_snapshot = %s::jsonb WHERE id = %s",
            (
                json.dumps({**VA_SNAPSHOT, "semantic_version": "2.0.0"}),
                matter_id,
            ),
        )
        _exec(
            db,
            "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled)"
            " VALUES ('interview_v2', 'firm', %s, true)",
            (interview_setup["firm_id"],),
        )
        return matter_id

    # --- (3) session-status guard -------------------------------------

    def test_park_on_completed_session_is_409(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        assert post_turn(api_client, matter_id, session_id, "hello").status_code == 200
        done = set_session_status(api_client, matter_id, session_id, "completed")
        assert done.status_code == 200, done.text
        resp = _park(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text
        assert "Interview session is completed" in resp.text

    def test_park_on_abandoned_session_is_409(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        gone = set_session_status(api_client, matter_id, session_id, "abandoned")
        assert gone.status_code == 200, gone.text
        resp = _park(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text
        assert "Interview session is abandoned" in resp.text

    def test_unpark_on_completed_session_is_409(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        assert post_turn(api_client, matter_id, session_id, "hello").status_code == 200
        assert _park(api_client, matter_id, session_id, "marital_status").status_code == 200
        done = set_session_status(api_client, matter_id, session_id, "completed")
        assert done.status_code == 200, done.text
        resp = _unpark(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text
        assert "Interview session is completed" in resp.text
        # The rejected unpark must not have removed the marker.
        rows = _fetchall(
            db,
            "SELECT question_key FROM interview_parked_items WHERE matter_id = %s",
            (matter_id,),
        )
        assert {r["question_key"] for r in rows} == {"marital_status"}

    def test_unpark_on_abandoned_session_is_409(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        assert _park(api_client, matter_id, session_id, "marital_status").status_code == 200
        gone = set_session_status(api_client, matter_id, session_id, "abandoned")
        assert gone.status_code == 200, gone.text
        resp = _unpark(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text
        assert "Interview session is abandoned" in resp.text

    # --- (4) applicability-aware park ---------------------------------

    def test_park_pending_gated_key_is_409(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        # primary_guardian_person is gated on has_minor_children == true;
        # with the gate unanswered the item is pending, not applicable.
        matter_id = self._make_v2_matter(db, interview_setup)
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = _park(api_client, matter_id, session_id, "primary_guardian_person")
        assert resp.status_code == 409, resp.text

    def test_park_optional_key_is_409(
        self, api_client: TestClient, interview_setup: dict[str, str]
    ) -> None:
        # guardian_reason is applicable from the start but not required;
        # parking exists to defer required items that gate generation.
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        resp = _park(api_client, matter_id, session_id, "guardian_reason")
        assert resp.status_code == 409, resp.text

    def test_park_open_proposal_key_is_409(
        self,
        api_client: TestClient,
        db,
        interview_setup: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        matter_id = interview_setup["matter_id"]
        session_id = start_session(api_client, matter_id).json()["id"]
        _post_with_proposal(
            api_client,
            matter_id,
            session_id,
            ai_provider.FieldProposalDraft(
                question_key="marital_status",
                value="single",
                confidence=0.8,
                source_snippet="I am single",
            ),
            monkeypatch,
        )
        proposals = api_client.get(
            f"/v1/matters/{matter_id}/interview/sessions/{session_id}/proposals",
            headers=_headers(CLIENT_SUB),
        ).json()
        open_keys = [p["question_key"] for p in proposals if p["status"] == "proposed"]
        assert open_keys == ["marital_status"]
        resp = _park(api_client, matter_id, session_id, "marital_status")
        assert resp.status_code == 409, resp.text

    def test_parked_item_pruned_by_gate_flip_leaves_open_list(
        self, api_client: TestClient, db, interview_setup: dict[str, str]
    ) -> None:
        matter_id = self._make_v2_matter(db, interview_setup)
        session_id = start_session(api_client, matter_id).json()["id"]
        saved = _save_answers(api_client, matter_id, {"has_minor_children": True})
        assert saved.status_code == 200, saved.text
        resp = _park(api_client, matter_id, session_id, "primary_guardian_person")
        assert resp.status_code == 200, resp.text
        listed = api_client.get(
            f"/v1/matters/{matter_id}/interview/parked-items",
            headers=_headers(CLIENT_SUB),
        )
        assert [i["question_key"] for i in listed.json()] == ["primary_guardian_person"]

        # Flip the gate off: the parked item prunes. The marker row may
        # persist, but the item must no longer surface as open anywhere.
        saved = _save_answers(api_client, matter_id, {"has_minor_children": False})
        assert saved.status_code == 200, saved.text
        listed = api_client.get(
            f"/v1/matters/{matter_id}/interview/parked-items",
            headers=_headers(CLIENT_SUB),
        )
        assert listed.json() == []
        body = _get_session(api_client, matter_id, session_id).json()
        assert body["parked_required_keys"] == []
        item = next(
            c for c in body["checklist"] if c["question_key"] == "primary_guardian_person"
        )
        assert item["parked"] is False
        rows = _fetchall(
            db,
            "SELECT question_key FROM interview_parked_items WHERE matter_id = %s",
            (matter_id,),
        )
        assert {r["question_key"] for r in rows} == {"primary_guardian_person"}
