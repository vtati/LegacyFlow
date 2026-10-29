"""Slice 2 Track C tests: guided intake, decisions, escalation, submission.

Runs against a local pgserver instance (never ``DATABASE_URL`` or live
Supabase). Applies the FULL migration chain (every file in
``supabase/migrations``, sorted) — the post-F1 sibling pattern. The
Slice-1+Slice-2 subset this file originally applied predates the
assets table (Slice 3), and the submit flow now materializes assets
from intake answers, so the subset schema 500'd every submit test
with ``relation "assets" does not exist`` (Phase-3 final sweep).

The app under test is ``create_app()`` plus the four Track C routers
mounted under ``/v1`` — exactly the wiring the coordinator will add to
``app/api/v1/router.py``.

All fixture data is synthetic. Nothing sensitive is printed.
"""

from __future__ import annotations

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

from tests.conftest import (
    STORAGE_BUCKETS_STUB,
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_SECRET,
    mint_token,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# Canonical auth stub (F1 sweep): email + raw_user_meta_data /
# raw_app_meta_data mirror the real auth.users shape — the
# handle_new_user trigger migration (20261001130000) reads them.
# STORAGE_BUCKETS_STUB (tests.conftest) covers the storage-buckets
# migration's INSERT into storage.buckets on bare pgserver.
AUTH_STUB = (
    """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;
"""
    + STORAGE_BUCKETS_STUB
)

# ---------------------------------------------------------------------------
# Synthetic identities (module scope; uuid4 = synthetic by construction)
# ---------------------------------------------------------------------------

FIRM_ID = uuid.uuid4()
HOUSEHOLD_ID = uuid.uuid4()
# Canonical IDs from supabase/seeds/01_jurisdictions.sql so the Slice 2
# migration's pack-manifest UPDATEs (and any sibling fixtures) line up.
VA_JUR_ID = UUID("11111111-1111-1111-1111-111111111111")
XZ_JUR_ID = UUID("33333333-3333-3333-3333-333333333333")
VA_PACK_ID = UUID("22222222-2222-2222-2222-222222222222")
XZ_PACK_ID = UUID("44444444-4444-4444-4444-444444444444")

ATTORNEY_SUB = str(uuid.uuid4())
STAFF_SUB = str(uuid.uuid4())
AUDITOR_SUB = str(uuid.uuid4())
PRIMARY_SUB = str(uuid.uuid4())
COLLAB_SUB = str(uuid.uuid4())
DEPENDENT_SUB = str(uuid.uuid4())
TRUSTED_SUB = str(uuid.uuid4())
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

#: All required VA answers with no triage fact set (nothing escalates).
FULL_ANSWERS: dict[str, Any] = {
    "marital_status": "married",
    "primary_residence_state": "Virginia",
    "has_minor_children": False,
    "has_child_with_special_needs": False,
    "has_prior_marriage_or_children_outside_current_relationship": False,
    "owns_real_estate_outside_primary_jurisdiction": False,
    "owns_or_controls_business_interest": False,
    "expects_family_conflict": False,
    "asset_categories_present": ["bank"],
    "has_debts": False,
    "primary_guardian_person": {"full_name": "Synthetic Guardian"},
}

_DSN = ""


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


def _apply_schema(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(AUTH_STUB)
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(migration.read_text())


def _seed_core() -> None:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            subs = [
                ATTORNEY_SUB,
                STAFF_SUB,
                AUDITOR_SUB,
                PRIMARY_SUB,
                COLLAB_SUB,
                DEPENDENT_SUB,
                TRUSTED_SUB,
                OUTSIDER_SUB,
            ]
            for sub in subs:
                cur.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                    (sub, f"synthetic-{sub[:8]}@example.test"),
                )
                # The handle_new_user trigger already created the
                # profile row on the auth.users insert; upsert the
                # synthetic display name onto it (canonical seed
                # pattern, cf. test_slice2_access.create_user).
                cur.execute(
                    "INSERT INTO profiles (id, display_name) VALUES (%s, %s)"
                    " ON CONFLICT (id) DO UPDATE"
                    " SET display_name = EXCLUDED.display_name",
                    (sub, f"Synthetic User {sub[:8]}"),
                )
            cur.execute(
                "INSERT INTO firms (id, legal_name, display_name, status)"
                " VALUES (%s, %s, %s, 'active')",
                (str(FIRM_ID), "Synthetic Firm LLC", "Synthetic Firm"),
            )
            for sub, role in (
                (ATTORNEY_SUB, "attorney"),
                (STAFF_SUB, "staff"),
                (AUDITOR_SUB, "auditor"),
            ):
                cur.execute(
                    "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
                    " VALUES (%s, %s, %s, 'active')",
                    (str(FIRM_ID), sub, role),
                )
            cur.execute(
                "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
                " VALUES (%s, %s, %s, %s)",
                (str(HOUSEHOLD_ID), str(FIRM_ID), "Synthetic Household", ATTORNEY_SUB),
            )
            for sub, role in (
                (PRIMARY_SUB, "primary_client"),
                (COLLAB_SUB, "collaborator"),
                (DEPENDENT_SUB, "dependent"),
                (TRUSTED_SUB, "trusted_contact"),
            ):
                cur.execute(
                    "INSERT INTO household_members"
                    " (household_id, user_id, relationship_role, access_status)"
                    " VALUES (%s, %s, %s, 'active')",
                    (str(HOUSEHOLD_ID), sub, role),
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


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    """Throwaway pgserver instance with the full migration chain applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-slice2-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            _apply_schema(dsn)
            global _DSN
            _DSN = dsn
            _seed_core()
            yield dsn
    finally:
        # pgserver stops the server but leaves PGDATA behind; remove it so
        # repeated runs (including failed startups) never fill /tmp.
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
    # Rate limiting is covered by its own dedicated tests; leave it off here
    # so these tests are not order-dependent (429s).
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def test_app(_env: None) -> FastAPI:
    """App with the Track C routers mounted exactly as the coordinator will."""
    from app.domains.decisions.router import router as decisions_router
    from app.domains.escalation.router import router as escalation_router
    from app.domains.intake.router import router as intake_router
    from app.domains.matters.router import router as matters_router
    from app.main import create_app

    application = create_app()
    application.include_router(matters_router, prefix="/v1")
    application.include_router(intake_router, prefix="/v1")
    application.include_router(decisions_router, prefix="/v1")
    application.include_router(escalation_router, prefix="/v1")
    return application


@pytest.fixture(scope="module")
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub)}"}


def _db() -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_DSN, row_factory=dict_row)


def _db_fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def _db_fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def _db_exec(sql: str, params: tuple[Any, ...] = ()) -> None:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def make_matter(
    *,
    status: str = "INTAKE_IN_PROGRESS",
    snapshot_kind: str = "va",  # "va" | "xz" | "missing" | "legacy"
) -> UUID:
    """Insert a matter; ``snapshot_kind="legacy"`` simulates a pre-snapshot row."""
    matter_id = uuid.uuid4()
    snapshots: dict[str, dict[str, Any] | None] = {
        "va": VA_SNAPSHOT,
        "xz": XZ_SNAPSHOT,
        "missing": {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": "9.9.9",
        },
        "legacy": None,
    }
    snap = snapshots[snapshot_kind]
    jur_id = XZ_JUR_ID if snapshot_kind == "xz" else VA_JUR_ID
    pack_id = XZ_PACK_ID if snapshot_kind == "xz" else VA_PACK_ID
    _db_exec(
        "INSERT INTO matters"
        " (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,"
        "  plan_type, status, jurisdiction_snapshot, completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', %s, %s::jsonb, 0.0)",
        (
            str(matter_id),
            str(FIRM_ID),
            str(HOUSEHOLD_ID),
            str(jur_id),
            str(pack_id),
            status,
            json.dumps(snap) if snap is not None else "{}",
        ),
    )
    return matter_id


def put_answers(
    client: TestClient, matter_id: UUID, answers: dict[str, Any], sub: str = PRIMARY_SUB
) -> Any:
    return client.put(
        f"/v1/matters/{matter_id}/intake/answers",
        json={"answers": answers},
        headers=_headers(sub),
    )


# ---------------------------------------------------------------------------
# Question sets
# ---------------------------------------------------------------------------


def _set_labels(question_sets: dict[str, Any], set_id: str) -> list[str]:
    labels: list[str] = []
    for section in question_sets[set_id]["sections"]:
        for question in section["questions"]:
            labels.append(str(question["label"]))
    return labels


def test_question_sets_returns_va_pack(client: TestClient) -> None:
    matter_id = make_matter()
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(ATTORNEY_SUB)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction_code"] == "US-VA"
    assert body["pack_version"] == "0.1.0"
    assert set(body["question_sets"]) == {"household", "assets", "guardians"}
    labels = _set_labels(body["question_sets"], "household")
    assert "What best describes your household?" in labels


def test_question_sets_xz_differs_without_code_changes(client: TestClient) -> None:
    """FR-JUR-005: the XZ pack drives visibly different UI from pack data alone."""
    matter_id = make_matter(snapshot_kind="xz")
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(ATTORNEY_SUB)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction_code"] == "US-XZ"
    assert body["pack_version"] == "1.0.0"
    assert set(body["question_sets"]) == {"household"}
    labels = _set_labels(body["question_sets"], "household")
    assert "XZ: household composition? (synthetic wording)" in labels
    assert "What best describes your household?" not in labels


def test_question_sets_legacy_row_uses_fk_fallback(client: TestClient) -> None:
    matter_id = make_matter(snapshot_kind="legacy")
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(ATTORNEY_SUB)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction_code"] == "US-VA"
    assert body["pack_version"] == "0.1.0"
    assert "household" in body["question_sets"]


def test_question_sets_missing_pack_version_404(client: TestClient) -> None:
    matter_id = make_matter(snapshot_kind="missing")
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(ATTORNEY_SUB)
    )
    assert response.status_code == 404
    assert response.json()["type"] == "urn:legacyflow:error:pack_not_found"


def test_question_sets_outsider_404(client: TestClient) -> None:
    matter_id = make_matter()
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(OUTSIDER_SUB)
    )
    assert response.status_code == 404


def test_question_sets_auditor_403(client: TestClient) -> None:
    """Auditors are firm members but not staff+: read access is denied."""
    matter_id = make_matter()
    response = client.get(
        f"/v1/matters/{matter_id}/intake/question-sets", headers=_headers(AUDITOR_SUB)
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Answers autosave
# ---------------------------------------------------------------------------


def test_answers_roundtrip(client: TestClient) -> None:
    matter_id = make_matter()
    answers = {"marital_status": "married", "has_minor_children": True}
    put = put_answers(client, matter_id, answers)
    assert put.status_code == 200
    assert put.json()["answers"] == answers
    assert put.json()["updated_at"] is not None

    got = client.get(f"/v1/matters/{matter_id}/intake/answers", headers=_headers(PRIMARY_SUB))
    assert got.status_code == 200
    assert got.json()["answers"] == answers


def test_answers_upsert_replaces_value(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, {"marital_status": "single"}).status_code == 200
    assert put_answers(client, matter_id, {"marital_status": "married"}).status_code == 200
    got = client.get(f"/v1/matters/{matter_id}/intake/answers", headers=_headers(PRIMARY_SUB))
    assert got.json()["answers"] == {"marital_status": "married"}


def test_answers_unknown_key_422(client: TestClient) -> None:
    matter_id = make_matter()
    response = put_answers(client, matter_id, {"not_a_real_question": "x"})
    assert response.status_code == 422


def test_answers_dependent_forbidden_but_readable(client: TestClient) -> None:
    matter_id = make_matter()
    put = put_answers(client, matter_id, {"marital_status": "single"}, sub=DEPENDENT_SUB)
    assert put.status_code == 403
    got = client.get(f"/v1/matters/{matter_id}/intake/answers", headers=_headers(DEPENDENT_SUB))
    assert got.status_code == 200


def test_answers_trusted_contact_forbidden(client: TestClient) -> None:
    matter_id = make_matter()
    put = put_answers(client, matter_id, {"marital_status": "single"}, sub=TRUSTED_SUB)
    assert put.status_code == 403


def test_answers_collaborator_and_staff_allowed(client: TestClient) -> None:
    matter_id = make_matter()
    assert (
        put_answers(client, matter_id, {"marital_status": "single"}, sub=COLLAB_SUB).status_code
        == 200
    )
    assert (
        put_answers(client, matter_id, {"marital_status": "married"}, sub=STAFF_SUB).status_code
        == 200
    )


def test_answers_outsider_404(client: TestClient) -> None:
    matter_id = make_matter()
    put = put_answers(client, matter_id, {"marital_status": "single"}, sub=OUTSIDER_SUB)
    assert put.status_code == 404


def test_answers_write_after_submit_409(client: TestClient) -> None:
    matter_id = make_matter()
    _db_exec("UPDATE matters SET status = 'INTAKE_SUBMITTED' WHERE id = %s", (str(matter_id),))
    response = put_answers(client, matter_id, {"marital_status": "single"})
    assert response.status_code == 409


def test_answers_write_during_clarification_allowed(client: TestClient) -> None:
    matter_id = make_matter()
    _db_exec(
        "UPDATE matters SET status = 'CLARIFICATION_REQUESTED' WHERE id = %s",
        (str(matter_id),),
    )
    assert put_answers(client, matter_id, {"marital_status": "single"}).status_code == 200


def test_answers_audit_records_counts_not_values(client: TestClient) -> None:
    matter_id = make_matter()
    answers = {"marital_status": "married", "primary_residence_state": "Virginia"}
    assert put_answers(client, matter_id, answers).status_code == 200
    row = _db_fetchone(
        "SELECT after_summary FROM audit_events"
        " WHERE matter_id = %s AND action = 'intake.answers_saved'"
        " ORDER BY created_at DESC LIMIT 1",
        (str(matter_id),),
    )
    assert row is not None
    summary = row["after_summary"]
    assert summary["saved_count"] == 2
    # Answer values must never reach the audit trail.
    assert "married" not in json.dumps(summary)
    assert "Virginia" not in json.dumps(summary)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_math_on_partially_answered_set(client: TestClient) -> None:
    matter_id = make_matter()
    answers = {
        "marital_status": "married",
        "primary_residence_state": "Virginia",
        "has_minor_children": False,
        "has_child_with_special_needs": False,
    }
    assert put_answers(client, matter_id, answers).status_code == 200
    response = client.get(f"/v1/matters/{matter_id}/intake/progress", headers=_headers(PRIMARY_SUB))
    assert response.status_code == 200
    body = response.json()
    household = body["sets"]["household"]
    assert household["total_questions"] == 8
    assert household["answered"] == 4
    assert household["required_total"] == 8
    assert household["required_answered"] == 4
    assert household["percent_complete"] == 50.0
    assets = body["sets"]["assets"]
    assert assets["total_questions"] == 3
    assert assets["answered"] == 0
    # VA pack: 11 required questions total; 4 answered -> 36.36% -> the
    # integer ``matters.completeness_pct`` column stores 36.
    assert body["completeness_pct"] == 36.0
    row = _db_fetchone("SELECT completeness_pct FROM matters WHERE id = %s", (str(matter_id),))
    assert row is not None
    assert int(row["completeness_pct"]) == 36


def test_progress_false_and_zero_count_as_answered(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, {"has_minor_children": False}).status_code == 200
    response = client.get(f"/v1/matters/{matter_id}/intake/progress", headers=_headers(PRIMARY_SUB))
    household = response.json()["sets"]["household"]
    assert household["answered"] == 1
    assert household["required_answered"] == 1


def test_progress_empty_answers_is_zero(client: TestClient) -> None:
    matter_id = make_matter()
    response = client.get(f"/v1/matters/{matter_id}/intake/progress", headers=_headers(PRIMARY_SUB))
    assert response.status_code == 200
    assert response.json()["completeness_pct"] == 0.0


# ---------------------------------------------------------------------------
# Planning decisions
# ---------------------------------------------------------------------------


def test_decisions_upsert_roundtrip(client: TestClient) -> None:
    matter_id = make_matter()
    payload = {"key": "executor_choice", "value": {"full_name": "Synthetic Executor"}}
    put = client.put(
        f"/v1/matters/{matter_id}/decisions", json=payload, headers=_headers(PRIMARY_SUB)
    )
    assert put.status_code == 200
    body = put.json()
    assert body["key"] == "executor_choice"
    assert body["value"] == {"full_name": "Synthetic Executor"}
    assert body["decided_by"] == PRIMARY_SUB

    # Upsert replaces the value and records the new decider.
    put2 = client.put(
        f"/v1/matters/{matter_id}/decisions",
        json={"key": "executor_choice", "value": {"full_name": "Synthetic Executor Two"}},
        headers=_headers(STAFF_SUB),
    )
    assert put2.status_code == 200
    assert put2.json()["value"] == {"full_name": "Synthetic Executor Two"}
    assert put2.json()["decided_by"] == STAFF_SUB

    got = client.get(f"/v1/matters/{matter_id}/decisions", headers=_headers(PRIMARY_SUB))
    assert got.status_code == 200
    items = got.json()
    assert len(items) == 1
    assert items[0]["key"] == "executor_choice"
    assert items[0]["value"] == {"full_name": "Synthetic Executor Two"}


def test_decisions_dependent_forbidden(client: TestClient) -> None:
    matter_id = make_matter()
    put = client.put(
        f"/v1/matters/{matter_id}/decisions",
        json={"key": "k", "value": "v"},
        headers=_headers(DEPENDENT_SUB),
    )
    assert put.status_code == 403


def test_decisions_audit_records_key_not_value(client: TestClient) -> None:
    matter_id = make_matter()
    secret_value = "Synthetic Executor REDACTME-123"
    put = client.put(
        f"/v1/matters/{matter_id}/decisions",
        json={"key": "executor_choice", "value": secret_value},
        headers=_headers(PRIMARY_SUB),
    )
    assert put.status_code == 200
    row = _db_fetchone(
        "SELECT after_summary FROM audit_events"
        " WHERE matter_id = %s AND action = 'decisions.saved'"
        " ORDER BY created_at DESC LIMIT 1",
        (str(matter_id),),
    )
    assert row is not None
    summary = row["after_summary"]
    assert summary["decision_key"] == "executor_choice"
    assert "REDACTME-123" not in json.dumps(summary)


# ---------------------------------------------------------------------------
# Escalation evaluation
# ---------------------------------------------------------------------------


def _evaluate(client: TestClient, matter_id: UUID, sub: str = PRIMARY_SUB) -> Any:
    return client.post(f"/v1/matters/{matter_id}/intake/evaluate", headers=_headers(sub))


def test_evaluate_blocking_rule_escalates_matter(client: TestClient) -> None:
    matter_id = make_matter()
    answers = {
        "marital_status": "single",
        "primary_residence_state": "Virginia",
        "has_child_with_special_needs": True,
    }
    assert put_answers(client, matter_id, answers).status_code == 200
    response = _evaluate(client, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert body["has_blocking"] is True
    triggered = body["triggered"]
    assert [t["rule_id"] for t in triggered] == ["VA-ESC-001"]
    assert triggered[0]["severity"] == "blocking"
    assert triggered[0]["outcome"] == "attorney_review_required"
    assert triggered[0]["user_message"]

    row = _db_fetchone("SELECT status FROM matters WHERE id = %s", (str(matter_id),))
    assert row is not None and row["status"] == "ESCALATED"

    esc = _db_fetchone(
        "SELECT rule_id, severity, outcome, facts_snapshot, status FROM escalations"
        " WHERE matter_id = %s",
        (str(matter_id),),
    )
    assert esc is not None
    assert esc["rule_id"] == "VA-ESC-001"
    assert esc["severity"] == "blocking"
    assert esc["status"] == "open"
    # Snapshot keeps triage booleans; free-text answers are never snapshotted.
    assert esc["facts_snapshot"] == {"has_child_with_special_needs": True}

    history = _db_fetchone(
        "SELECT from_status, to_status FROM matter_status_history"
        " WHERE matter_id = %s ORDER BY created_at DESC LIMIT 1",
        (str(matter_id),),
    )
    assert history is not None
    assert history["from_status"] == "INTAKE_IN_PROGRESS"
    assert history["to_status"] == "ESCALATED"

    audit = _db_fetchone(
        "SELECT id FROM audit_events WHERE matter_id = %s AND action = 'escalations.triggered'",
        (str(matter_id),),
    )
    assert audit is not None


def test_evaluate_review_rule_records_without_transition(client: TestClient) -> None:
    matter_id = make_matter()
    assert (
        put_answers(client, matter_id, {"owns_or_controls_business_interest": True}).status_code
        == 200
    )
    response = _evaluate(client, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert body["has_blocking"] is False
    assert [t["rule_id"] for t in body["triggered"]] == ["VA-ESC-005"]
    assert body["triggered"][0]["severity"] == "review"

    row = _db_fetchone("SELECT status FROM matters WHERE id = %s", (str(matter_id),))
    assert row is not None and row["status"] == "INTAKE_IN_PROGRESS"
    esc = _db_fetchone(
        "SELECT severity, status FROM escalations WHERE matter_id = %s", (str(matter_id),)
    )
    assert esc is not None and esc["severity"] == "review" and esc["status"] == "open"


def test_evaluate_xz_rule_fires_on_synthetic_pack(client: TestClient) -> None:
    matter_id = make_matter(snapshot_kind="xz")
    answers = {
        "marital_status": "married",
        "has_three_or_more_minor_children": True,
    }
    assert put_answers(client, matter_id, answers).status_code == 200
    response = _evaluate(client, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert [t["rule_id"] for t in body["triggered"]] == ["XZ-ESC-001"]
    row = _db_fetchone("SELECT status FROM matters WHERE id = %s", (str(matter_id),))
    assert row is not None and row["status"] == "INTAKE_IN_PROGRESS"


def test_evaluate_no_facts_triggers_nothing(client: TestClient) -> None:
    matter_id = make_matter()
    response = _evaluate(client, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] == []
    assert body["has_blocking"] is False
    rows = _db_fetchall("SELECT id FROM escalations WHERE matter_id = %s", (str(matter_id),))
    assert rows == []
    row = _db_fetchone("SELECT status FROM matters WHERE id = %s", (str(matter_id),))
    assert row is not None and row["status"] == "INTAKE_IN_PROGRESS"


def test_evaluate_is_idempotent_per_open_rule(client: TestClient) -> None:
    matter_id = make_matter()
    assert (
        put_answers(client, matter_id, {"owns_or_controls_business_interest": True}).status_code
        == 200
    )
    assert _evaluate(client, matter_id).status_code == 200
    assert _evaluate(client, matter_id).status_code == 200
    rows = _db_fetchall(
        "SELECT id FROM escalations WHERE matter_id = %s AND status = 'open'",
        (str(matter_id),),
    )
    assert len(rows) == 1


def test_evaluate_dependent_403_and_outsider_404(client: TestClient) -> None:
    matter_id = make_matter()
    assert _evaluate(client, matter_id, sub=DEPENDENT_SUB).status_code == 403
    assert _evaluate(client, matter_id, sub=OUTSIDER_SUB).status_code == 404


def test_evaluate_after_escalation_409(client: TestClient) -> None:
    matter_id = make_matter()
    _db_exec("UPDATE matters SET status = 'ESCALATED' WHERE id = %s", (str(matter_id),))
    assert _evaluate(client, matter_id).status_code == 409


def test_list_escalations(client: TestClient) -> None:
    matter_id = make_matter()
    assert (
        put_answers(client, matter_id, {"owns_or_controls_business_interest": True}).status_code
        == 200
    )
    assert _evaluate(client, matter_id).status_code == 200
    response = client.get(f"/v1/matters/{matter_id}/escalations", headers=_headers(PRIMARY_SUB))
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["rule_id"] == "VA-ESC-005"
    assert items[0]["status"] == "open"

    filtered = client.get(
        f"/v1/matters/{matter_id}/escalations?status=open", headers=_headers(PRIMARY_SUB)
    )
    assert len(filtered.json()) == 1
    resolved = client.get(
        f"/v1/matters/{matter_id}/escalations?status=resolved", headers=_headers(PRIMARY_SUB)
    )
    assert resolved.json() == []
    bad = client.get(
        f"/v1/matters/{matter_id}/escalations?status=bogus", headers=_headers(PRIMARY_SUB)
    )
    assert bad.status_code == 422
    outsider = client.get(f"/v1/matters/{matter_id}/escalations", headers=_headers(OUTSIDER_SUB))
    assert outsider.status_code == 404


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _submit(client: TestClient, matter_id: UUID, sub: str = PRIMARY_SUB) -> Any:
    return client.post(f"/v1/matters/{matter_id}/submit", headers=_headers(sub))


def test_submit_incomplete_409_with_missing_keys(client: TestClient) -> None:
    matter_id = make_matter()
    # Only the household set is fully answered; assets + guardians missing.
    household_answers = {
        "marital_status": "married",
        "primary_residence_state": "Virginia",
        "has_minor_children": False,
        "has_child_with_special_needs": False,
        "has_prior_marriage_or_children_outside_current_relationship": False,
        "owns_real_estate_outside_primary_jurisdiction": False,
        "owns_or_controls_business_interest": False,
        "expects_family_conflict": False,
    }
    assert put_answers(client, matter_id, household_answers).status_code == 200
    response = _submit(client, matter_id)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "intake_incomplete"
    assert set(body["missing"]) == {
        "asset_categories_present",
        "has_debts",
        "primary_guardian_person",
    }


def test_submit_blocking_escalation_open_409(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, FULL_ANSWERS).status_code == 200
    _db_exec(
        "INSERT INTO escalations (matter_id, rule_id, severity, outcome, facts_snapshot, status)"
        " VALUES (%s, 'VA-ESC-001', 'blocking', 'attorney_review_required',"
        "         '{\"has_child_with_special_needs\": true}'::jsonb, 'open')",
        (str(matter_id),),
    )
    response = _submit(client, matter_id)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "blocking_escalations_open"
    assert body["rules"] == ["VA-ESC-001"]


def test_submit_happy_path(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, FULL_ANSWERS).status_code == 200
    response = _submit(client, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(matter_id)
    assert body["status"] == "INTAKE_SUBMITTED"
    assert body["submitted_at"] is not None
    assert body["completeness_pct"] == 100.0

    row = _db_fetchone(
        "SELECT status, submitted_at, completeness_pct FROM matters WHERE id = %s",
        (str(matter_id),),
    )
    assert row is not None
    assert row["status"] == "INTAKE_SUBMITTED"
    assert row["submitted_at"] is not None
    assert float(row["completeness_pct"]) == 100.0

    history = _db_fetchone(
        "SELECT from_status, to_status FROM matter_status_history"
        " WHERE matter_id = %s ORDER BY created_at DESC LIMIT 1",
        (str(matter_id),),
    )
    assert history is not None
    assert history["from_status"] == "INTAKE_IN_PROGRESS"
    assert history["to_status"] == "INTAKE_SUBMITTED"

    audit = _db_fetchone(
        "SELECT id FROM audit_events WHERE matter_id = %s AND action = 'matters.submitted'",
        (str(matter_id),),
    )
    assert audit is not None


def test_submit_review_escalation_does_not_block(client: TestClient) -> None:
    """A review-severity escalation is recorded but does not block submission."""
    matter_id = make_matter()
    answers = dict(FULL_ANSWERS)
    answers["owns_or_controls_business_interest"] = True
    assert put_answers(client, matter_id, answers).status_code == 200
    assert _evaluate(client, matter_id).status_code == 200
    response = _submit(client, matter_id)
    assert response.status_code == 200
    assert response.json()["status"] == "INTAKE_SUBMITTED"


def test_submit_wrong_status_409(client: TestClient) -> None:
    matter_id = make_matter(status="DRAFT")
    response = _submit(client, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_matter_status"


def test_submit_twice_409(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, FULL_ANSWERS).status_code == 200
    assert _submit(client, matter_id).status_code == 200
    second = _submit(client, matter_id)
    assert second.status_code == 409
    assert second.json()["code"] == "invalid_matter_status"


def test_submit_collaborator_allowed_dependent_forbidden(client: TestClient) -> None:
    matter_id = make_matter()
    assert put_answers(client, matter_id, FULL_ANSWERS, sub=COLLAB_SUB).status_code == 200
    assert _submit(client, matter_id, sub=COLLAB_SUB).status_code == 200

    matter_id2 = make_matter()
    assert _submit(client, matter_id2, sub=DEPENDENT_SUB).status_code == 403
    assert _submit(client, matter_id2, sub=OUTSIDER_SUB).status_code == 404
