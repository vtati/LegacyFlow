"""Slice 3 tests: attorney review (packets, comments, clarifications, gates,
notifications), matter search/filter/pagination, and content-pack import.

Runs against a local pgserver instance (never ``DATABASE_URL`` or live
Supabase). Applies the Slice-1 and Slice-2 migrations plus a fallback DDL
block for the five Track-A3 tables (approval_gates, review_packets,
comments, clarification_requests, notification_outbox) when Track A's
Slice-3 migration file is absent.

The app under test is ``create_app()`` plus the matters, review, and
invitations routers mounted under ``/v1`` — exactly the wiring the
coordinator will add to ``app/api/v1/router.py``.

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

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SLICE1_MIGRATION = REPO_ROOT / "supabase" / "migrations" / "20260926220000_foundation_slice1.sql"
SLICE2_MIGRATION = (
    REPO_ROOT / "supabase" / "migrations" / "20260927010000_household_intake_slice2.sql"
)
SLICE3_MIGRATION = (
    REPO_ROOT / "supabase" / "migrations" / "20260927030000_attorney_review_slice3.sql"
)

# Fallback DDL used when the Slice-3 migration file is absent (Track A
# writes it in parallel). Mirrors the contract this track builds against:
# approval_gates / review_packets / comments / clarification_requests /
# notification_outbox, per the task's column list.
SLICE3_FALLBACK_DDL = """
CREATE TABLE IF NOT EXISTS approval_gates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  gate_type text NOT NULL CHECK (gate_type IN ('intake_approval', 'document_release')),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  decided_by_user_id uuid REFERENCES profiles(id),
  decided_at timestamptz,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_approval_gates_matter_id ON approval_gates (matter_id);

CREATE TABLE IF NOT EXISTS review_packets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  version integer NOT NULL,
  snapshot jsonb NOT NULL,
  created_by_user_id uuid NOT NULL REFERENCES profiles(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (matter_id, version)
);
CREATE INDEX IF NOT EXISTS idx_review_packets_matter_id ON review_packets (matter_id);

CREATE TABLE IF NOT EXISTS comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  author_user_id uuid NOT NULL REFERENCES profiles(id),
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comments_matter_id ON comments (matter_id);

CREATE TABLE IF NOT EXISTS clarification_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
  question text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
  created_by_user_id uuid NOT NULL REFERENCES profiles(id),
  answered_by_user_id uuid REFERENCES profiles(id),
  answered_at timestamptz,
  response text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_clarifications_matter_id ON clarification_requests (matter_id);

CREATE TABLE IF NOT EXISTS notification_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  matter_id uuid REFERENCES matters(id) ON DELETE CASCADE,
  recipient_user_id uuid REFERENCES profiles(id),
  recipient_email text,
  template_key text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent', 'failed')),
  attempts integer NOT NULL DEFAULT 0,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notification_outbox_status ON notification_outbox (status);
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

FIRM_ADMIN_SUB = str(uuid.uuid4())
ATTORNEY_SUB = str(uuid.uuid4())
STAFF_SUB = str(uuid.uuid4())
AUDITOR_SUB = str(uuid.uuid4())
PRIMARY_SUB = str(uuid.uuid4())
COLLAB_SUB = str(uuid.uuid4())
DEPENDENT_SUB = str(uuid.uuid4())
OUTSIDER_SUB = str(uuid.uuid4())

VA_SNAPSHOT = {
    "pack_id": "us-va",
    "jurisdiction_code": "US-VA",
    "semantic_version": "0.1.0",
}

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
            if SLICE3_MIGRATION.exists():
                cur.execute(SLICE3_MIGRATION.read_text())
            else:
                cur.execute(SLICE3_FALLBACK_DDL)


def _seed_core() -> None:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            subs = [
                FIRM_ADMIN_SUB,
                ATTORNEY_SUB,
                STAFF_SUB,
                AUDITOR_SUB,
                PRIMARY_SUB,
                COLLAB_SUB,
                DEPENDENT_SUB,
                OUTSIDER_SUB,
            ]
            for sub in subs:
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
            for sub, role in (
                (FIRM_ADMIN_SUB, "firm_admin"),
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
    """Throwaway pgserver instance with Slice-1..3 schema applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-slice3-")
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
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def test_app(_env: None) -> FastAPI:
    """App with the matters + review + invitations routers mounted under /v1."""
    from app.domains.invitations.router import router as invitations_router
    from app.domains.matters.router import router as matters_router
    from app.domains.review.router import router as review_router
    from app.main import create_app

    application = create_app()
    application.include_router(matters_router, prefix="/v1")
    application.include_router(review_router, prefix="/v1")
    application.include_router(invitations_router, prefix="/v1")
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
    status: str = "ATTORNEY_REVIEW",
    title: str = "Synthetic Matter",
    assigned_attorney_id: str | None = None,
    household_id: UUID = HOUSEHOLD_ID,
    jur_id: UUID = VA_JUR_ID,
    pack_id: UUID = VA_PACK_ID,
    snapshot: dict[str, Any] | None = None,
) -> UUID:
    """Insert a matter directly; returns its id."""
    matter_id = uuid.uuid4()
    snap = snapshot if snapshot is not None else dict(VA_SNAPSHOT)
    _db_exec(
        "INSERT INTO matters"
        " (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,"
        "  plan_type, status, title, jurisdiction_snapshot, completeness_pct,"
        "  assigned_attorney_id)"
        " VALUES (%s, %s, %s, %s, %s, 'will', %s, %s, %s::jsonb, 0.0, %s)",
        (
            str(matter_id),
            str(FIRM_ID),
            str(household_id),
            str(jur_id),
            str(pack_id),
            status,
            title,
            json.dumps(snap) if snap else "{}",
            assigned_attorney_id,
        ),
    )
    return matter_id


def _outbox_rows(template_key: str, matter_id: UUID | None = None) -> list[dict[str, Any]]:
    if matter_id is None:
        return _db_fetchall(
            "SELECT * FROM notification_outbox WHERE template_key = %s", (template_key,)
        )
    return _db_fetchall(
        "SELECT * FROM notification_outbox WHERE template_key = %s AND matter_id = %s",
        (template_key, str(matter_id)),
    )


# ---------------------------------------------------------------------------
# Review packets
# ---------------------------------------------------------------------------


def test_packet_generate_versioning_and_get(client: TestClient) -> None:
    matter_id = make_matter()
    _db_exec(
        "INSERT INTO intake_answers (matter_id, question_key, answer)"
        " VALUES (%s, 'marital_status', '\"married\"'::jsonb)",
        (str(matter_id),),
    )
    person_id = uuid.uuid4()
    _db_exec(
        "INSERT INTO persons (id, household_id, full_name, kind)"
        " VALUES (%s, %s, 'Synthetic Person', 'adult')",
        (str(person_id), str(HOUSEHOLD_ID)),
    )

    first = client.post(
        f"/v1/matters/{matter_id}/review-packets",
        json={"notes": "first packet"},
        headers=_headers(STAFF_SUB),
    )
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1
    snapshot = first.json()["snapshot"]
    assert snapshot["matter"]["title"] == "Synthetic Matter"
    assert snapshot["intake_answers"]["marital_status"] == "married"
    assert snapshot["persons"][0]["full_name"] == "Synthetic Person"
    assert snapshot["notes"] == "first packet"
    for section in (
        "relationships",
        "nominations",
        "decisions",
        "escalations",
    ):
        assert section in snapshot

    second = client.post(f"/v1/matters/{matter_id}/review-packets", headers=_headers(STAFF_SUB))
    assert second.status_code == 201
    assert second.json()["version"] == 2

    listed = client.get(f"/v1/matters/{matter_id}/review-packets", headers=_headers(STAFF_SUB))
    assert listed.status_code == 200
    versions = [item["version"] for item in listed.json()]
    assert versions == [2, 1]

    detail = client.get(
        f"/v1/matters/{matter_id}/review-packets/{first.json()['id']}",
        headers=_headers(STAFF_SUB),
    )
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["matter"]["status"] == "ATTORNEY_REVIEW"


def test_packet_authz(client: TestClient) -> None:
    matter_id = make_matter()
    # Household primary client cannot generate review packets (staff only).
    resp = client.post(f"/v1/matters/{matter_id}/review-packets", headers=_headers(PRIMARY_SUB))
    assert resp.status_code == 403
    # Auditors are not staff.
    resp = client.post(f"/v1/matters/{matter_id}/review-packets", headers=_headers(AUDITOR_SUB))
    assert resp.status_code == 403
    # Cross-firm callers see 404 (existence oracle).
    resp = client.post(f"/v1/matters/{matter_id}/review-packets", headers=_headers(OUTSIDER_SUB))
    assert resp.status_code == 404
    # A packet id from another matter reads as not found.
    other_matter = make_matter()
    packet = client.post(
        f"/v1/matters/{other_matter}/review-packets", headers=_headers(STAFF_SUB)
    ).json()
    resp = client.get(
        f"/v1/matters/{matter_id}/review-packets/{packet['id']}",
        headers=_headers(STAFF_SUB),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def test_comments_crud_and_authz(client: TestClient) -> None:
    matter_id = make_matter()
    staff_comment = client.post(
        f"/v1/matters/{matter_id}/comments",
        json={"body": "Staff note"},
        headers=_headers(STAFF_SUB),
    )
    assert staff_comment.status_code == 201, staff_comment.text
    primary_comment = client.post(
        f"/v1/matters/{matter_id}/comments",
        json={"body": "Client note"},
        headers=_headers(PRIMARY_SUB),
    )
    assert primary_comment.status_code == 201
    dependent_comment = client.post(
        f"/v1/matters/{matter_id}/comments",
        json={"body": "Dependent note"},
        headers=_headers(DEPENDENT_SUB),
    )
    assert dependent_comment.status_code == 201
    assert staff_comment.json()["author_user_id"] == STAFF_SUB

    # Body over 5000 chars is rejected.
    too_long = client.post(
        f"/v1/matters/{matter_id}/comments",
        json={"body": "x" * 5001},
        headers=_headers(STAFF_SUB),
    )
    assert too_long.status_code == 422

    # Outsider cannot comment (404); auditor cannot comment (403).
    assert (
        client.post(
            f"/v1/matters/{matter_id}/comments",
            json={"body": "hi"},
            headers=_headers(OUTSIDER_SUB),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/matters/{matter_id}/comments",
            json={"body": "hi"},
            headers=_headers(AUDITOR_SUB),
        ).status_code
        == 403
    )

    listed = client.get(f"/v1/matters/{matter_id}/comments", headers=_headers(STAFF_SUB))
    assert listed.status_code == 200
    bodies = [item["body"] for item in listed.json()]
    assert bodies == ["Staff note", "Client note", "Dependent note"]
    # Household members can read the comment thread too.
    listed_household = client.get(
        f"/v1/matters/{matter_id}/comments", headers=_headers(PRIMARY_SUB)
    )
    assert listed_household.status_code == 200
    assert len(listed_household.json()) == 3


# ---------------------------------------------------------------------------
# Clarifications
# ---------------------------------------------------------------------------


def test_clarification_flow_with_state_transitions(client: TestClient) -> None:
    matter_id = make_matter(status="ATTORNEY_REVIEW")

    # Staff (non-attorney) cannot create clarifications.
    forbidden = client.post(
        f"/v1/matters/{matter_id}/clarifications",
        json={"question": "Staff asks?"},
        headers=_headers(STAFF_SUB),
    )
    assert forbidden.status_code == 403

    created = client.post(
        f"/v1/matters/{matter_id}/clarifications",
        json={"question": "Who is the backup guardian?"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert created.status_code == 201, created.text
    clarification_id = created.json()["id"]
    assert created.json()["status"] == "open"

    matter = client.get(f"/v1/matters/{matter_id}", headers=_headers(ATTORNEY_SUB))
    assert matter.json()["matter"]["status"] == "CLARIFICATION_REQUESTED"

    # A clarification-created notification was queued for the household side.
    rows = _outbox_rows("clarification.created", matter_id)
    assert len(rows) >= 1
    assert rows[0]["template_key"] == "clarification.created"

    # Wrong-status creation is a conflict.
    wrong_state = make_matter(status="INTAKE_IN_PROGRESS")
    resp = client.post(
        f"/v1/matters/{wrong_state}/clarifications",
        json={"question": "Too early?"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert resp.status_code == 409

    # The primary client answers; the matter returns to attorney review.
    answered = client.post(
        f"/v1/clarifications/{clarification_id}/respond",
        json={"response": "My sister, Synthetic Guardian."},
        headers=_headers(PRIMARY_SUB),
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "answered"
    assert answered.json()["answered_by_user_id"] == PRIMARY_SUB

    matter = client.get(f"/v1/matters/{matter_id}", headers=_headers(ATTORNEY_SUB))
    assert matter.json()["matter"]["status"] == "ATTORNEY_REVIEW"

    responded_rows = _outbox_rows("clarification.responded", matter_id)
    assert len(responded_rows) >= 1

    # Answering twice is a conflict.
    again = client.post(
        f"/v1/clarifications/{clarification_id}/respond",
        json={"response": "Once more."},
        headers=_headers(PRIMARY_SUB),
    )
    assert again.status_code == 409

    # List view shows the clarification.
    listed = client.get(f"/v1/matters/{matter_id}/clarifications", headers=_headers(ATTORNEY_SUB))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [clarification_id]

    # Unknown clarification ids read as 404.
    missing = client.post(
        f"/v1/clarifications/{uuid.uuid4()}/respond",
        json={"response": "Nobody asked."},
        headers=_headers(PRIMARY_SUB),
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


def test_gate_approval_transitions_matter(client: TestClient) -> None:
    matter_id = make_matter(status="ATTORNEY_REVIEW")

    # No gate yet: the manual transition fails closed with 409.
    rejected = client.post(
        f"/v1/matters/{matter_id}/transition",
        json={"to_status": "APPROVED_FOR_ASSEMBLY"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "approval_gate_required"

    created = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "intake_approval", "notes": "Intake looks complete."},
        headers=_headers(STAFF_SUB),
    )
    assert created.status_code == 201, created.text
    gate_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    # Duplicate pending gate for the same type is a conflict.
    dup = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "intake_approval"},
        headers=_headers(STAFF_SUB),
    )
    assert dup.status_code == 409

    # Staff cannot decide gates (attorney/firm_admin only).
    staff_decide = client.post(
        f"/v1/gates/{gate_id}/decide",
        json={"decision": "approved"},
        headers=_headers(STAFF_SUB),
    )
    assert staff_decide.status_code == 403

    decided = client.post(
        f"/v1/gates/{gate_id}/decide",
        json={"decision": "approved", "notes": "Approved."},
        headers=_headers(ATTORNEY_SUB),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"
    assert decided.json()["decided_by_user_id"] == ATTORNEY_SUB

    matter = client.get(f"/v1/matters/{matter_id}", headers=_headers(ATTORNEY_SUB))
    assert matter.json()["matter"]["status"] == "APPROVED_FOR_ASSEMBLY"

    gate_rows = _outbox_rows("gate.decided", matter_id)
    assert len(gate_rows) >= 1
    assert gate_rows[0]["payload"]["decision"] == "approved"

    # Deciding twice is a conflict.
    again = client.post(
        f"/v1/gates/{gate_id}/decide",
        json={"decision": "rejected"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert again.status_code == 409

    listed = client.get(f"/v1/matters/{matter_id}/gates", headers=_headers(ATTORNEY_SUB))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [gate_id]


def test_gate_rejection_leaves_matter_in_place(client: TestClient) -> None:
    matter_id = make_matter(status="ATTORNEY_REVIEW")
    gate_id = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "intake_approval"},
        headers=_headers(ATTORNEY_SUB),
    ).json()["id"]
    decided = client.post(
        f"/v1/gates/{gate_id}/decide",
        json={"decision": "rejected", "notes": "Needs more info."},
        headers=_headers(ATTORNEY_SUB),
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "rejected"
    matter = client.get(f"/v1/matters/{matter_id}", headers=_headers(ATTORNEY_SUB))
    assert matter.json()["matter"]["status"] == "ATTORNEY_REVIEW"


def test_document_release_gate(client: TestClient) -> None:
    matter_id = make_matter(status="APPROVED_FOR_ASSEMBLY")
    gate_id = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "document_release"},
        headers=_headers(STAFF_SUB),
    ).json()["id"]
    decided = client.post(
        f"/v1/gates/{gate_id}/decide",
        json={"decision": "approved"},
        headers=_headers(FIRM_ADMIN_SUB),
    )
    assert decided.status_code == 200
    matter = client.get(f"/v1/matters/{matter_id}", headers=_headers(ATTORNEY_SUB))
    assert matter.json()["matter"]["status"] == "DOCUMENTS_RELEASED"


def test_approved_gate_unblocks_manual_transition(client: TestClient) -> None:
    matter_id = make_matter(status="ATTORNEY_REVIEW")
    gate_id = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "intake_approval"},
        headers=_headers(STAFF_SUB),
    ).json()["id"]
    # Record the approval directly (bypassing the decide endpoint's own
    # transition) so the manual transition path does the work.
    _db_exec(
        "UPDATE approval_gates SET status = 'approved', decided_by_user_id = %s,"
        " decided_at = now(), updated_at = now() WHERE id = %s",
        (ATTORNEY_SUB, gate_id),
    )
    transitioned = client.post(
        f"/v1/matters/{matter_id}/transition",
        json={"to_status": "APPROVED_FOR_ASSEMBLY"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert transitioned.status_code == 200, transitioned.text
    assert transitioned.json()["status"] == "APPROVED_FOR_ASSEMBLY"


def test_gate_create_wrong_state_409(client: TestClient) -> None:
    matter_id = make_matter(status="INTAKE_IN_PROGRESS")
    resp = client.post(
        f"/v1/matters/{matter_id}/gates",
        json={"gate_type": "intake_approval"},
        headers=_headers(STAFF_SUB),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Matter search / filter / pagination
# ---------------------------------------------------------------------------


def test_matter_search_filter_pagination(client: TestClient) -> None:
    household_b = uuid.uuid4()
    _db_exec(
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (str(household_b), str(FIRM_ID), "Beacon Family", ATTORNEY_SUB),
    )
    alpha = make_matter(title="Alpha Will", assigned_attorney_id=ATTORNEY_SUB)
    beta = make_matter(title="Beta Trust", household_id=household_b)
    gamma = make_matter(
        title="Gamma Will",
        status="ESCALATED",
        jur_id=XZ_JUR_ID,
        pack_id=XZ_PACK_ID,
        snapshot={
            "pack_id": "us-xz-synthetic",
            "jurisdiction_code": "US-XZ",
            "semantic_version": "1.0.0",
        },
    )

    # q matches title.
    body = client.get("/v1/matters?q=alpha", headers=_headers(ATTORNEY_SUB)).json()
    assert {item["id"] for item in body["items"]} >= {str(alpha)}

    # q matches client (household display) name.
    body = client.get("/v1/matters?q=beacon", headers=_headers(ATTORNEY_SUB)).json()
    assert {item["id"] for item in body["items"]} == {str(beta)}

    # Status filter.
    body = client.get("/v1/matters?status=ESCALATED", headers=_headers(ATTORNEY_SUB)).json()
    assert str(gamma) in {item["id"] for item in body["items"]}
    assert all(item["status"] == "ESCALATED" for item in body["items"])

    # Jurisdiction filter.
    body = client.get("/v1/matters?jurisdiction_code=US-XZ", headers=_headers(ATTORNEY_SUB)).json()
    assert str(gamma) in {item["id"] for item in body["items"]}

    # Assigned-to filter.
    body = client.get(
        f"/v1/matters?assigned_to={ATTORNEY_SUB}", headers=_headers(ATTORNEY_SUB)
    ).json()
    assert {item["id"] for item in body["items"]} >= {str(alpha)}

    # Bad jurisdiction code shape is a 422.
    resp = client.get("/v1/matters?jurisdiction_code=bogus", headers=_headers(ATTORNEY_SUB))
    assert resp.status_code == 422

    # Limit/offset pagination (defaults 20/0).
    first = client.get("/v1/matters?limit=1", headers=_headers(ATTORNEY_SUB)).json()
    assert first["page_size"] == 1
    assert first["total"] >= 3
    second = client.get("/v1/matters?limit=1&offset=1", headers=_headers(ATTORNEY_SUB)).json()
    assert second["items"][0]["id"] != first["items"][0]["id"]
    assert second["page"] == 2

    # Legacy page/page_size still works.
    legacy = client.get("/v1/matters?page=1&page_size=2", headers=_headers(ATTORNEY_SUB)).json()
    assert legacy["page"] == 1 and legacy["page_size"] == 2
    assert len(legacy["items"]) <= 2


# ---------------------------------------------------------------------------
# Notification outbox
# ---------------------------------------------------------------------------


def test_invitation_created_queues_outbox(client: TestClient) -> None:
    created = client.post(
        "/v1/firms/invitations",
        json={"email": "invitee@example.com", "role": "firm_staff"},
        headers=_headers(ATTORNEY_SUB),
    )
    assert created.status_code == 201, created.text
    rows = _db_fetchall(
        "SELECT * FROM notification_outbox WHERE template_key = 'invitation.created'"
        " AND recipient_email = 'invitee@example.com'"
    )
    assert len(rows) == 1
    assert rows[0]["matter_id"] is None
    assert rows[0]["status"] == "pending"


def test_matter_submitted_queues_outbox(client: TestClient) -> None:
    # All required US-VA answers with no triage fact set (nothing escalates).
    full_answers: dict[str, Any] = {
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
    matter_id = make_matter(status="INTAKE_IN_PROGRESS", title="Submit Outbox Matter")
    for key, value in full_answers.items():
        _db_exec(
            "INSERT INTO intake_answers (matter_id, question_key, answer)"
            " VALUES (%s, %s, %s::jsonb)",
            (str(matter_id), key, json.dumps(value)),
        )
    submitted = client.post(f"/v1/matters/{matter_id}/submit", headers=_headers(PRIMARY_SUB))
    assert submitted.status_code == 200, submitted.text
    rows = _outbox_rows("matter.submitted", matter_id)
    recipients = {str(row["recipient_user_id"]) for row in rows}
    # Firm staff (firm_admin, attorney, staff) are notified — not auditors.
    assert recipients == {FIRM_ADMIN_SUB, ATTORNEY_SUB, STAFF_SUB}
    assert all(row["payload"]["status"] == "INTAKE_SUBMITTED" for row in rows)


# ---------------------------------------------------------------------------
# Content packs
# ---------------------------------------------------------------------------


def _write_pack_dir(tmp: pathlib.Path, version: str = "2.0.0") -> pathlib.Path:
    pack_dir = tmp / "synthetic-pack"
    pack_dir.mkdir()
    (pack_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "pack:",
                "  id: us-xy-synthetic",
                "  country_code: US",
                "  subdivision_code: XY",
                "  display_name: Xylonia (synthetic)",
                "  semantic_version: " + version,
                "  status: draft",
                "  effective_from: '2026-09-26'",
                "capabilities: {}",
                "requirements: {}",
                "sources: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return pack_dir


def test_pack_import_and_list(client: TestClient) -> None:
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        pack_dir = _write_pack_dir(pathlib.Path(tmp))
        resp = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(pack_dir)},
            headers=_headers(FIRM_ADMIN_SUB),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pack_id"] == "us-xy-synthetic"
    assert body["jurisdiction_code"] == "US-XY"
    assert body["version"] == "2.0.0"
    assert len(body["source_bundle_hash"]) == 64

    listed = client.get("/v1/packs", headers=_headers(STAFF_SUB)).json()
    assert any(
        item["jurisdiction_code"] == "US-XY" and item["version"] == "2.0.0" for item in listed
    )

    # Re-importing the same version is a conflict.
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        pack_dir = _write_pack_dir(pathlib.Path(tmp))
        dup = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(pack_dir)},
            headers=_headers(FIRM_ADMIN_SUB),
        )
    assert dup.status_code == 409


def test_pack_import_authz_and_validation(client: TestClient) -> None:
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        pack_dir = _write_pack_dir(pathlib.Path(tmp))
        # Staff cannot import (firm_admin only).
        denied = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(pack_dir)},
            headers=_headers(STAFF_SUB),
        )
        assert denied.status_code == 403

    # Hash mismatch is rejected.
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        pack_dir = _write_pack_dir(pathlib.Path(tmp), version="2.0.1")
        mismatch = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(pack_dir), "expected_bundle_hash": "0" * 64},
            headers=_headers(FIRM_ADMIN_SUB),
        )
        assert mismatch.status_code == 422

    # Missing manifest is rejected.
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        empty_dir = pathlib.Path(tmp) / "empty"
        empty_dir.mkdir()
        missing = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(empty_dir)},
            headers=_headers(FIRM_ADMIN_SUB),
        )
        assert missing.status_code == 422

    # Malformed manifest (missing required pack fields) is rejected.
    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        bad_dir = pathlib.Path(tmp) / "bad"
        bad_dir.mkdir()
        (bad_dir / "manifest.yaml").write_text("pack: {}\n", encoding="utf-8")
        bad = client.post(
            "/v1/admin/packs/import",
            data={"pack_dir": str(bad_dir)},
            headers=_headers(FIRM_ADMIN_SUB),
        )
        assert bad.status_code == 422

    # Missing source (no dir, no bundle) is rejected.
    nothing = client.post("/v1/admin/packs/import", headers=_headers(FIRM_ADMIN_SUB))
    assert nothing.status_code == 422


def test_pack_import_zip_bundle(client: TestClient) -> None:
    import io
    import zipfile

    with tempfile.TemporaryDirectory(prefix="lf-pack-test-") as tmp:
        pack_dir = _write_pack_dir(pathlib.Path(tmp), version="3.0.0")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path in pack_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(pack_dir))
        buffer.seek(0)
        resp = client.post(
            "/v1/admin/packs/import",
            files={"bundle": ("pack.zip", buffer, "application/zip")},
            headers=_headers(FIRM_ADMIN_SUB),
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == "3.0.0"
    assert resp.json()["jurisdiction_code"] == "US-XY"
