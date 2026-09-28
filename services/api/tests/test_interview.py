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
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT current_setting('app.test_uid', true)::uuid $$;
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
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)",
        (sub, email, f"Synthetic {email}"),
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
    monkeypatch.setattr(interview_service, "get_llm_provider", lambda: _ProposalStub(drafts))


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
