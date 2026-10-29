"""Attorney Reviewer — persistence, run service, and API (plan 2026-10-27).

pgserver + full migration sweep, mirroring test_family_draft_documents.
Covers: the flag registration, snapshot recording, the run lifecycle
(disabled → 409; enabled → findings), scope rules, acknowledgment and
its carry-over across regeneration, and fixed-by-regeneration
dispositions.

All fixture data is synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

ADMIN = "a1111111-1111-1111-1111-111111111111"     # firm_admin
CLIENT = "a3333333-3333-3333-3333-333333333333"    # household primary_client
DEPENDENT = "a5555555-5555-5555-5555-555555555555"  # household dependent (read-only)
OUTSIDER = "a8888888-8888-8888-8888-888888888888"   # no access

USERS = {
    ADMIN: "admin@example.test",
    CLIENT: "client@example.test",
    DEPENDENT: "dependent@example.test",
    OUTSIDER: "outsider@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"
FINGERPRINT = "fp-test-1"

TRUNCATE_TABLES = (
    "attorney_review_findings, attorney_review_runs, attorney_review_snapshots,"
    " attorney_review_sources,"
    " document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " feature_flags, attorney_applications, review_packets, approval_gates,"
    " comments, clarification_requests,"
    " asset_flags, assets, liabilities, ai_traces,"
    " auth.users"
)

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

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
API_CONTENT_PACKS_DIR = Path(__file__).resolve().parents[1] / "content-packs"


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-attorney-review")))
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
    storage_dir = tmp_path / "documents"
    storage_dir.mkdir()
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(storage_dir))
    scratch_packs = tmp_path / "content-packs"
    shutil.copytree(API_CONTENT_PACKS_DIR, scratch_packs)
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(scratch_packs))
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("LF_FLAG_ATTORNEY_REVIEWER", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _one(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, email=USERS[sub])}"}


CONTEXT = {
    "testator_name": "Arjun Sharma",
    "spouse_name": "Neha Kapoor",
    "executor_name": "Amit Sharma",
    "alternate_executor_name": "Neha Kapoor",
    "children_list": "Kabir Sharma",
    "residuary_beneficiaries": "my spouse, Neha Kapoor",
    "today": "October 27, 2026",
}

WILL_BODY = (
    "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
    "I am married to Neha Kapoor. I appoint Amit Sharma as Executor.\n"
    "Signature of Testator: ____________________\n"
    "Witness signature: ____________________\n"
    "Witness signature: ____________________\n"
    "SELF-PROVING AFFIDAVIT under Va. Code § 64.2-403.\n"
    "Notary Public signature: ____________________"
)
POA_BODY_TYPO = (
    "DURABLE POWER OF ATTORNEY\n"
    "I, Arjun Sharma, appoint Neha Kapur as my agent."
)


class World:
    firm_id: str
    household_id: str
    matter_id: str
    person_ids: dict[str, str]
    package_ids: dict[str, str]


def build_world(db) -> World:
    w = World()
    for sub, email in USERS.items():
        _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
        _exec(
            db,
            "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (sub, email, email.split("@")[0]),
        )
    w.firm_id = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')",
          (w.firm_id, "Synthetic Firm", "Synthetic Firm"))
    _exec(db, "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
              " VALUES (%s, %s, 'firm_admin', 'active')", (w.firm_id, ADMIN))
    w.household_id = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (w.household_id, w.firm_id, "Sharma Household", CLIENT))
    people = [
        ("Arjun Sharma", "adult", "1985-04-12"),
        ("Neha Kapoor", "adult", "1987-09-03"),
        ("Amit Sharma", "adult", "1989-01-25"),
        ("Kabir Sharma", "minor", "2018-05-01"),
    ]
    w.person_ids = {}
    for name, kind, dob in people:
        pid = str(uuid4())
        w.person_ids[name] = pid
        _exec(db, "INSERT INTO persons (id, household_id, full_name, kind, date_of_birth)"
                  " VALUES (%s, %s, %s, %s, %s)",
              (pid, w.household_id, name, kind, dob))
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, %s, 'primary_client', 'active')",
          (w.household_id, CLIENT, w.person_ids["Arjun Sharma"]))
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, %s, 'dependent', 'active')",
          (w.household_id, DEPENDENT, w.person_ids["Kabir Sharma"]))
    w.matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 100)",
        (w.matter_id, w.firm_id, w.household_id, US_VA_JURISDICTION_ID,
         US_VA_PACK_ID, "Sharma Estate Plan", snapshot),
    )
    w.package_ids = {}
    for template_type in ("will", "poa"):
        template_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO document_templates"
            " (id, firm_id, name, template_type, pack_id, version, content, created_by_user_id)"
            " VALUES (%s, %s, %s, %s, %s, '1.0.0', %s::jsonb, %s)",
            (template_id, w.firm_id, f"{template_type} template", template_type,
             "us-va", json.dumps({"body": f"body of {template_type}"}), ADMIN),
        )
        package_id = str(uuid4())
        w.package_ids[template_type] = package_id
        _exec(
            db,
            "INSERT INTO document_packages"
            " (id, firm_id, matter_id, template_id, status, source_fingerprint, created_by_user_id)"
            " VALUES (%s, %s, %s, %s, 'demo_released', %s, %s)",
            (package_id, w.firm_id, w.matter_id, template_id, FINGERPRINT, CLIENT),
        )
    return w


def enable_flag(db, firm_id: str) -> None:
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled, created_by_user_id)"
        " VALUES ('attorney_reviewer', 'firm', %s, true, %s)",
        (firm_id, ADMIN),
    )
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()


def insert_snapshot(db, w: World, template_type: str, body: str, *,
                    context: dict | None = None, fingerprint: str = FINGERPRINT,
                    template_body: str = "", version: int = 2) -> None:
    _exec(
        db,
        "INSERT INTO attorney_review_snapshots"
        " (matter_id, firm_id, package_id, package_version, source_fingerprint,"
        "  template_type, template_body, body_text, context)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (w.matter_id, w.firm_id, w.package_ids[template_type], version, fingerprint,
         template_type, template_body, body,
         json.dumps(CONTEXT if context is None else context)),
    )


def insert_source(db, w: World, *, fingerprint: str = FINGERPRINT) -> None:
    payload = {
        "persons": [
            {"id": w.person_ids["Arjun Sharma"], "full_name": "Arjun Sharma",
             "kind": "adult", "date_of_birth": "1985-04-12"},
            {"id": w.person_ids["Neha Kapoor"], "full_name": "Neha Kapoor",
             "kind": "adult", "date_of_birth": "1987-09-03"},
            {"id": w.person_ids["Amit Sharma"], "full_name": "Amit Sharma",
             "kind": "adult", "date_of_birth": "1989-01-25"},
            {"id": w.person_ids["Kabir Sharma"], "full_name": "Kabir Sharma",
             "kind": "minor", "date_of_birth": "2018-05-01"},
        ],
        "nominations": [
            {"nomination_type": "executor", "person_id": w.person_ids["Amit Sharma"],
             "priority": 1, "status": "confirmed"},
        ],
        "answers": {"has_minor_children": True, "testator_full_name": "Arjun Sharma"},
        "expected_set": ["will", "poa"],
        "question_keys": ["testator_full_name", "has_minor_children"],
    }
    _exec(
        db,
        "INSERT INTO attorney_review_sources (matter_id, firm_id, source_fingerprint, payload)"
        " VALUES (%s, %s, %s, %s::jsonb)",
        (w.matter_id, w.firm_id, fingerprint, json.dumps(payload)),
    )


def seed_reviewable_world(db) -> World:
    w = build_world(db)
    insert_snapshot(db, w, "will", WILL_BODY)
    insert_snapshot(db, w, "poa", POA_BODY_TYPO)
    insert_source(db, w)
    return w


# ---------------------------------------------------------------------------
# Flag registration
# ---------------------------------------------------------------------------


def test_attorney_reviewer_flag_registered_default_off() -> None:
    from app.domains.feature_flags.schemas import FeatureFlagKey
    from app.domains.feature_flags.service import KNOWN_FLAG_DEFAULTS

    assert KNOWN_FLAG_DEFAULTS["attorney_reviewer"] is False
    assert FeatureFlagKey.ATTORNEY_REVIEWER.value == "attorney_reviewer"


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


def test_run_rejected_when_flag_off(api_client, db) -> None:
    w = seed_reviewable_world(db)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 409
    assert _all(db, "SELECT * FROM attorney_review_runs") == []


def test_run_produces_findings_and_family_can_read_them(api_client, db) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["run"]["status"] == "completed"
    checks = {f["check"] for f in body["findings"]}
    assert "name_consistency" in checks  # the planted Neha Kapur typo

    resp = api_client.get(
        f"/v1/matters/{w.matter_id}/attorney-review/findings", headers=_headers(CLIENT)
    )
    assert resp.status_code == 200
    assert resp.json()["findings"], "family read must return the run's findings"

    resp = api_client.get(
        f"/v1/matters/{w.matter_id}/attorney-review/findings", headers=_headers(OUTSIDER)
    )
    assert resp.status_code == 404


def test_dependent_cannot_run_or_acknowledge(api_client, db) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(DEPENDENT)
    )
    assert resp.status_code == 403

    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    finding_id = resp.json()["findings"][0]["id"]
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{finding_id}/acknowledge",
        headers=_headers(DEPENDENT), json={"note": "ok"},
    )
    assert resp.status_code == 403


def test_attorney_acknowledgment_is_recorded(api_client, db) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    finding_id = resp.json()["findings"][0]["id"]
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{finding_id}/acknowledge",
        headers=_headers(ADMIN), json={"note": "Spelling verified with the client."},
    )
    assert resp.status_code == 200, resp.text
    row = _one(db, "SELECT * FROM attorney_review_findings WHERE id = %s", (finding_id,))
    assert row["disposition"] == "acknowledged"
    assert row["acknowledgment_note"] == "Spelling verified with the client."
    assert str(row["acknowledged_by_user_id"]) == ADMIN


def test_regeneration_dispositions_fixed_and_ack_stays_within_fingerprint(api_client, db) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    # A second, persistent defect: successor executor == executor in context.
    ctx = dict(CONTEXT)
    ctx["alternate_executor_name"] = "Amit Sharma"
    _exec(db, "UPDATE attorney_review_snapshots SET context = %s::jsonb"
              " WHERE matter_id = %s",
          (json.dumps(ctx), w.matter_id))
    # Align the nomination so only the planted defects fire.
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    first = {f["check"]: f for f in resp.json()["findings"]}
    assert "name_consistency" in first and "role_consistency" in first
    # Attorney acknowledges the role finding (it will persist after regen).
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{first['role_consistency']['id']}/acknowledge",
        headers=_headers(ADMIN), json={"note": "Client confirmed both roles."},
    )
    assert resp.status_code == 200

    # Regeneration: fingerprint 2 — name typo fixed, role defect persists.
    fp2 = "fp-test-2"
    _exec(db, "UPDATE document_packages SET source_fingerprint = %s WHERE matter_id = %s",
          (fp2, w.matter_id))
    insert_snapshot(db, w, "will", WILL_BODY, context=ctx, fingerprint=fp2, version=4)
    insert_snapshot(
        db, w, "poa",
        "DURABLE POWER OF ATTORNEY\nI, Arjun Sharma, appoint Neha Kapoor as my agent.",
        context=ctx, fingerprint=fp2, version=4,
    )
    insert_source(db, w, fingerprint=fp2)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    second = {f["check"]: f for f in resp.json()["findings"]}
    assert "name_consistency" not in second  # fixed by the regeneration
    # FUNC-024 (Batch C3): acknowledgment carry-over is restricted to
    # previous runs with the SAME source fingerprint — under fp2 the
    # identical role finding is born OPEN, not acknowledged. (The
    # same-fingerprint carry case is covered by
    # test_ack_carryover_within_same_fingerprint.)
    assert second["role_consistency"]["disposition"] == "open"
    assert second["role_consistency"]["acknowledgment_note"] is None
    old_name = _one(
        db, "SELECT disposition FROM attorney_review_findings WHERE id = %s",
        (first["name_consistency"]["id"],),
    )
    assert old_name["disposition"] == "fixed_by_regeneration"


def test_model_findings_are_persisted(api_client, db, monkeypatch) -> None:
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review.checks import Finding

    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)

    def fake_review(*, snapshots, source, model):
        return [Finding(
            layer="model", check="free_text_contradiction", severity="should_fix",
            document="will", field="specific_bequests",
            excerpt="I appoint Amit Sharma as Executor.",
            explanation="A planted model finding for the persistence test.",
            suggested_fix="For the test.", package_id=None,
        )], {"prompt_tokens": 3, "completion_tokens": 2}

    monkeypatch.setattr(model_review, "review_via_model", fake_review)
    from app.core.config import get_settings

    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    layers = {f["layer"] for f in resp.json()["findings"]}
    assert "model" in layers
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def test_recorder_persists_snapshot_and_source(db) -> None:
    from app.domains.attorney_review import service as review_service

    w = build_world(db)
    _exec(db, "INSERT INTO intake_answers (matter_id, question_key, answer)"
              " VALUES (%s, 'has_minor_children', 'true'::jsonb)", (w.matter_id,))
    _exec(db, "INSERT INTO intake_answers (matter_id, question_key, answer)"
              " VALUES (%s, 'wants_revocable_trust', 'true'::jsonb)", (w.matter_id,))
    _exec(db, "INSERT INTO nominations (matter_id, nomination_type, person_id, priority, status)"
              " VALUES (%s, 'executor', %s, 1, 'confirmed')",
          (w.matter_id, w.person_ids["Amit Sharma"]))
    package_id = w.package_ids["will"]
    review_service.record_render_snapshot(
        db,
        matter={
            "id": w.matter_id, "firm_id": w.firm_id, "household_id": w.household_id,
            "jurisdiction_snapshot": {
                "pack_id": "us-va", "jurisdiction_code": "US-VA",
                "semantic_version": "2.0.0",
            },
        },
        package_id=package_id,
        package_version=4,
        source_fingerprint=FINGERPRINT,
        template_type="will",
        template_body="Body {{executor_name}}",
        body_text=WILL_BODY,
        context=CONTEXT,
    )
    db.commit()
    snap = _one(db, "SELECT * FROM attorney_review_snapshots WHERE package_id = %s",
                (package_id,))
    assert snap is not None
    assert snap["package_version"] == 4
    assert snap["source_fingerprint"] == FINGERPRINT
    source = _one(db, "SELECT * FROM attorney_review_sources WHERE matter_id = %s",
                  (w.matter_id,))
    assert source is not None
    payload = source["payload"]
    assert payload["expected_set"] == ["will", "poa", "advance_directive",
                                       "guardianship", "trust"]
    assert {p["full_name"] for p in payload["persons"]} >= {"Arjun Sharma", "Kabir Sharma"}
    assert payload["answers"]["has_minor_children"] is True
    assert "testator_full_name" in payload["question_keys"]


# ---------------------------------------------------------------------------
# Run idempotency, force gating, auto-run split (review fixes H2/M1/M2/L6)
# ---------------------------------------------------------------------------


def test_run_is_idempotent_and_force_is_firm_only(api_client, db, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review.checks import Finding

    def fake_review(*, snapshots, source, model):
        return [Finding(
            layer="model", check="missing_context", severity="question_for_attorney",
            document="will", field=None, excerpt=None,
            explanation="A planted model finding.", suggested_fix=None,
            package_id=None,
        )], {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(model_review, "review_via_model", fake_review)
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()

    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    url = f"/v1/matters/{w.matter_id}/attorney-review/run"
    first = api_client.post(url, headers=_headers(CLIENT))
    assert first.status_code == 201, first.text
    run_id = first.json()["run"]["id"]
    # Repeat without force -> the same completed run, no new model spend.
    again = api_client.post(url, headers=_headers(CLIENT))
    assert again.status_code == 201
    assert again.json()["run"]["id"] == run_id
    # A family caller cannot force a re-run (force degrades silently).
    forced_family = api_client.post(url, headers=_headers(CLIENT), json={"force": True})
    assert forced_family.json()["run"]["id"] == run_id
    # A firm-side actor can.
    forced_firm = api_client.post(url, headers=_headers(ADMIN), json={"force": True})
    assert forced_firm.status_code == 201, forced_firm.text
    assert forced_firm.json()["run"]["id"] != run_id


def test_auto_run_deterministic_only_then_full_upgrade(api_client, db, monkeypatch) -> None:
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review import service as review_service
    from app.domains.attorney_review.checks import Finding

    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    db.commit()
    matter = {
        "id": w.matter_id, "firm_id": w.firm_id, "household_id": w.household_id,
        "jurisdiction_snapshot": {
            "pack_id": "us-va", "jurisdiction_code": "US-VA",
            "semantic_version": "2.0.0",
        },
    }

    def fake_review(*, snapshots, source, model):
        return [Finding(
            layer="model", check="missing_context", severity="question_for_attorney",
            document="will", field=None, excerpt=None,
            explanation="A planted model finding.", suggested_fix=None,
            package_id=None,
        )], {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(model_review, "review_via_model", fake_review)
    from app.core.config import get_settings

    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()

    # Generate-time auto-run: deterministic only, even with a model
    # configured — no model findings, sentinel model_id.
    review_service.run_review_for_matter(
        matter=matter, actor_id=None, deterministic_only=True
    )
    run = _one(db, "SELECT * FROM attorney_review_runs WHERE matter_id = %s"
                   " ORDER BY created_at DESC LIMIT 1", (w.matter_id,))
    assert run["model_id"] == "deterministic_only"
    layers = {r["layer"] for r in _all(
        db, "SELECT layer FROM attorney_review_findings WHERE run_id = %s", (run["id"],))}
    assert layers == {"deterministic"}

    # Submit-time auto-run upgrades the same fingerprint to a full run.
    review_service.run_review_for_matter(
        matter=matter, actor_id=None, deterministic_only=False
    )
    run2 = _one(db, "SELECT * FROM attorney_review_runs WHERE matter_id = %s"
                    " ORDER BY created_at DESC LIMIT 1", (w.matter_id,))
    assert run2["id"] != run["id"]
    assert run2["model_id"] != "deterministic_only"
    layers2 = {r["layer"] for r in _all(
        db, "SELECT layer FROM attorney_review_findings WHERE run_id = %s", (run2["id"],))}
    assert "model" in layers2

    # A third auto-run is idempotent — no new run is created.
    review_service.run_review_for_matter(
        matter=matter, actor_id=None, deterministic_only=False
    )
    count = _one(db, "SELECT count(*) AS n FROM attorney_review_runs WHERE matter_id = %s",
                 (w.matter_id,))
    assert count["n"] == 2
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Review pass 2 — H2 / H3 / H4 / L1
# ---------------------------------------------------------------------------


def test_orphaned_package_does_not_silence_the_reviewer(api_client, db) -> None:
    """A package outside the answer-defined draft set (here a trust
    stamped with an old fingerprint) must not poison the reviewer."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    template_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO document_templates"
        " (id, firm_id, name, template_type, pack_id, version, content, created_by_user_id)"
        " VALUES (%s, %s, 'trust template', 'trust', 'us-va', '1.0.0', %s::jsonb, %s)",
        (template_id, w.firm_id, json.dumps({"body": "trust body"}), ADMIN),
    )
    _exec(
        db,
        "INSERT INTO document_packages"
        " (id, firm_id, matter_id, template_id, status, source_fingerprint, created_by_user_id)"
        " VALUES (%s, %s, %s, %s, 'demo_released', 'fp-orphan', %s)",
        (str(uuid4()), w.firm_id, w.matter_id, template_id, CLIENT),
    )
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text


def test_run_analyses_latest_snapshot_version_only(api_client, db) -> None:
    """A bad older snapshot superseded by a fixed re-render (same
    fingerprint, higher package_version) must not produce findings."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    bad_body = "\n".join(
        line for line in WILL_BODY.split("\n") if "witness" not in line.lower()
    )
    _exec(
        db,
        "UPDATE attorney_review_snapshots SET body_text = %s"
        " WHERE package_id = %s",
        (bad_body, w.package_ids["will"]),
    )
    insert_snapshot(db, w, "will", WILL_BODY, version=99)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    checks = {f["check"] for f in resp.json()["findings"]}
    assert "va_execution_requirements" not in checks


def test_failed_run_is_recorded(api_client, db, monkeypatch) -> None:
    from app.domains.attorney_review import service as review_service

    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)

    def boom(**kwargs):
        raise RuntimeError("checks exploded")

    monkeypatch.setattr(review_service, "run_deterministic_checks", boom)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 500
    failed = _all(
        db,
        "SELECT * FROM attorney_review_runs WHERE matter_id = %s AND status = 'failed'",
        (w.matter_id,),
    )
    assert len(failed) == 1


def test_acknowledge_requires_substantive_note_and_cannot_double_ack(
    api_client, db
) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    finding_id = resp.json()["findings"][0]["id"]
    url = f"/v1/matters/{w.matter_id}/attorney-review/findings/{finding_id}/acknowledge"
    empty = api_client.post(url, headers=_headers(ADMIN), json={"note": "   "})
    assert empty.status_code == 422
    ok = api_client.post(url, headers=_headers(ADMIN), json={"note": "Verified with client."})
    assert ok.status_code == 200, ok.text
    assert ok.json()["acknowledged_by_actor"] == "firm"
    again = api_client.post(url, headers=_headers(ADMIN), json={"note": "Twice."})
    assert again.status_code == 409


def test_source_payload_recorded_once_per_fingerprint(db) -> None:
    """The payload freezes at a fingerprint's first render, by design:
    the fingerprint hashes exactly the payload's user-editable inputs
    (answers, persons, nominations, titles), so a same-fingerprint
    re-render consumed the same inputs and shares the first view."""
    from app.domains.attorney_review import service as review_service

    w = build_world(db)
    package_id = w.package_ids["will"]
    matter = {
        "id": w.matter_id, "firm_id": w.firm_id, "household_id": w.household_id,
        "jurisdiction_snapshot": {
            "pack_id": "us-va", "jurisdiction_code": "US-VA",
            "semantic_version": "2.0.0",
        },
    }
    review_service.record_render_snapshot(
        db, matter=matter, package_id=package_id, package_version=4,
        source_fingerprint=FINGERPRINT, template_type="will",
        template_body="Body {{executor_name}}", body_text=WILL_BODY, context=CONTEXT,
    )
    db.commit()
    _exec(db, "INSERT INTO intake_answers (matter_id, question_key, answer)"
              " VALUES (%s, 'has_minor_children', 'true'::jsonb)", (w.matter_id,))
    review_service.record_render_snapshot(
        db, matter=matter, package_id=package_id, package_version=5,
        source_fingerprint=FINGERPRINT, template_type="will",
        template_body="Body {{executor_name}}", body_text=WILL_BODY, context=CONTEXT,
    )
    db.commit()
    source = _one(db, "SELECT * FROM attorney_review_sources WHERE matter_id = %s",
                  (w.matter_id,))
    assert "has_minor_children" not in source["payload"]["answers"]


# ---------------------------------------------------------------------------
# Review pass 3 — N1 (pack pin) / N3 / N4 / N5 coverage
# ---------------------------------------------------------------------------


def test_content_pack_files_version_pin_matches_manifest(tmp_path, monkeypatch) -> None:
    """Review N1: version dirs are v0/v1/... — the pin must resolve
    through each dir's manifest semantic_version, not the dir name."""
    from app.domains.documents import service as documents_service

    pack = tmp_path / "content-packs" / "us-va"
    for dirname, semver, marker in (("v0", "0.1.0", "A"), ("v1", "2.0.0", "B")):
        d = pack / dirname
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(f"semantic_version: {semver}\n")
        (d / "execution-profiles.yaml").write_text(f"marker: {marker}\n")
        (d / "funding-playbooks.yaml").write_text("playbooks: []\n")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path / "content-packs"))
    pinned_a = documents_service._content_pack_files("us-va", "0.1.0")
    pinned_b = documents_service._content_pack_files("us-va", "2.0.0")
    newest = documents_service._content_pack_files("us-va")
    assert pinned_a is not None and pinned_a[0]["marker"] == "A"
    assert pinned_b is not None and pinned_b[0]["marker"] == "B"
    assert newest is not None and newest[0]["marker"] == "B"


def test_mixed_stamps_within_draft_set_fail_closed(api_client, db) -> None:
    """H3 second half: a newer package of an included type stamped
    with a different fingerprint (regeneration mid-flight) → 409."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    poa_pkg = _one(
        db, "SELECT template_id::text AS tid FROM document_packages WHERE id = %s",
        (w.package_ids["poa"],),
    )
    _exec(
        db,
        "INSERT INTO document_packages"
        " (id, firm_id, matter_id, template_id, status, source_fingerprint, created_by_user_id)"
        " VALUES (%s, %s, %s, %s, 'demo_released', 'fp-mixed', %s)",
        (str(uuid4()), w.firm_id, w.matter_id, poa_pkg["tid"], CLIENT),
    )
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 409, resp.text


def test_acknowledge_rejected_when_flag_disabled(api_client, db) -> None:
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    finding_id = resp.json()["findings"][0]["id"]
    _exec(db, "UPDATE feature_flags SET is_enabled = false WHERE scope_ref = %s"
              " AND key = 'attorney_reviewer'", (w.firm_id,))
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()
    ack = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{finding_id}/acknowledge",
        headers=_headers(ADMIN), json={"note": "Verified."},
    )
    assert ack.status_code == 409, ack.text


def test_review_date_uses_frozen_packet_date(api_client, db) -> None:
    """L5 (date half): ages are judged as of the packet's recorded
    date — Kabir (DOB 2002-06-01, recorded as a minor) is 17 on the
    packet date 2020-01-01 (consistent) but 24 today (would flag)."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    source = _one(db, "SELECT * FROM attorney_review_sources WHERE matter_id = %s",
                  (w.matter_id,))
    payload = source["payload"]
    for person in payload["persons"]:
        if person["full_name"] == "Kabir Sharma":
            person["date_of_birth"] = "2002-06-01"
    _exec(
        db,
        "UPDATE attorney_review_sources SET payload = %s::jsonb,"
        " created_at = '2020-01-01T00:00:00Z'"
        " WHERE matter_id = %s",
        (json.dumps(payload), w.matter_id),
    )
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    checks = {f["check"] for f in resp.json()["findings"]}
    assert "date_age_arithmetic" not in checks


def test_payload_slot_keys_cover_identity_slots(db) -> None:
    """M3 integration: the recorded payload's slot sources include
    the relationship-derived identity slots."""
    from app.domains.attorney_review import service as review_service

    w = build_world(db)
    review_service.record_render_snapshot(
        db,
        matter={
            "id": w.matter_id, "firm_id": w.firm_id, "household_id": w.household_id,
            "jurisdiction_snapshot": {
                "pack_id": "us-va", "jurisdiction_code": "US-VA",
                "semantic_version": "2.0.0",
            },
        },
        package_id=w.package_ids["will"], package_version=4,
        source_fingerprint=FINGERPRINT, template_type="will",
        template_body="Body {{spouse_name}}", body_text=WILL_BODY, context=CONTEXT,
    )
    db.commit()
    source = _one(db, "SELECT * FROM attorney_review_sources WHERE matter_id = %s",
                  (w.matter_id,))
    keys = source["payload"]["slot_answer_keys"]
    assert "spouse_full_name" in keys["spouse_name"]
    assert "has_minor_children" in keys["children_list"]


# ---------------------------------------------------------------------------
# Batch C3: FUNC-024 — ack carry-over is fingerprint- and evidence-scoped
# ---------------------------------------------------------------------------


def _seed_persistent_role_defect(db, w) -> dict:
    ctx = dict(CONTEXT)
    ctx["alternate_executor_name"] = "Amit Sharma"
    _exec(db, "UPDATE attorney_review_snapshots SET context = %s::jsonb"
              " WHERE matter_id = %s",
          (json.dumps(ctx), w.matter_id))
    return ctx


def test_ack_carryover_within_same_fingerprint(api_client, db) -> None:
    """An identical finding in a same-fingerprint re-run DOES inherit
    the acknowledgment."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    _seed_persistent_role_defect(db, w)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    first = {f["check"]: f for f in resp.json()["findings"]}
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{first['role_consistency']['id']}/acknowledge",
        headers=_headers(ADMIN), json={"note": "Client confirmed both roles."},
    )
    assert resp.status_code == 200

    # Forced re-run under the SAME source fingerprint.
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run",
        headers=_headers(ADMIN), json={"force": True},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["run"]["id"] != first["role_consistency"]["id"]
    second = {f["check"]: f for f in resp.json()["findings"]}
    assert second["role_consistency"]["disposition"] == "acknowledged"
    assert second["role_consistency"]["acknowledgment_note"] == "Client confirmed both roles."


def test_ack_not_carried_when_evidence_changes(api_client, db) -> None:
    """A finding whose explanation changes (witness count 1 -> 0 of 2
    required) must NOT inherit the ack — its hash covers the evidence."""
    w = seed_reviewable_world(db)
    enable_flag(db, w.firm_id)
    one_witness = WILL_BODY.replace(
        "Witness signature: ____________________\nWitness signature:",
        "Witness signature:",
    )
    assert one_witness != WILL_BODY
    _exec(db, "UPDATE attorney_review_snapshots SET body_text = %s"
              " WHERE matter_id = %s AND template_type = 'will'",
          (one_witness, w.matter_id))
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run", headers=_headers(CLIENT)
    )
    assert resp.status_code == 201, resp.text
    first = {f["check"]: f for f in resp.json()["findings"]}
    assert "va_execution_requirements" in first, first.keys()
    assert "shows 1 witness" in first["va_execution_requirements"]["explanation"]
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/findings/{first['va_execution_requirements']['id']}/acknowledge",
        headers=_headers(ADMIN), json={"note": "Attorney verified the witness block."},
    )
    assert resp.status_code == 200, resp.text

    # Same fingerprint, but the draft now shows ZERO witness lines: the
    # material evidence changed, so the finding is born open.
    zero_witness = one_witness.replace(
        "Witness signature: ____________________\n", ""
    )
    insert_snapshot(db, w, "will", zero_witness, fingerprint=FINGERPRINT, version=4)
    resp = api_client.post(
        f"/v1/matters/{w.matter_id}/attorney-review/run",
        headers=_headers(ADMIN), json={"force": True},
    )
    assert resp.status_code == 201, resp.text
    second = {f["check"]: f for f in resp.json()["findings"]}
    assert "va_execution_requirements" in second
    assert "shows 0 witness" in second["va_execution_requirements"]["explanation"]
    assert second["va_execution_requirements"]["disposition"] == "open"
