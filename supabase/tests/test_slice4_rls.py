"""RLS allow/deny integration tests for LegacyFlow Slice 4 (files & documents).

Covers the nine new tables (file_uploads, document_templates,
document_packages, document_versions, signing_checklists, funding_tasks,
vault_shares, ai_interactions, reminders) plus the Slice 4 invariants:
immutable document versions, unique vault share tokens, one version number
per (package, version), firm-admin-only template writes, and household
completion/evidence-only updates on signing checklists and funding tasks.

Reuses the conftest `db` fixture (all migrations applied in order) and the
`as_user` helper: each test runs as the least-privilege `lf_app` role with
auth.uid() set to the synthetic user under test, inside a transaction that
is always rolled back.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import uuid

import pytest
from psycopg import errors

from conftest import (
    FIRM_A,
    FIRM_B,
    HOUSEHOLD_A,
    HOUSEHOLD_B,
    MATTER_A,
    MATTER_B,
    USER_A,
    USER_B,
    USER_C,
    as_user,
)

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
USER_I = uuid.UUID("ac000000-0000-0000-0000-000000000000")  # firm A admin
USER_J = uuid.UUID("ad000000-0000-0000-0000-000000000000")  # firm B staff
USER_K = uuid.UUID("ae000000-0000-0000-0000-000000000000")  # no memberships

ASSET_A1 = uuid.UUID("af000000-0000-0000-0000-000000000000")  # firm A, matter A

FUP_A1 = uuid.UUID("a1000000-0000-0000-0000-000000000000")  # firm A, matter A
FUP_A2 = uuid.UUID("a2000000-0000-0000-0000-000000000000")  # firm A, matter null
FUP_B1 = uuid.UUID("a3000000-0000-0000-0000-000000000000")  # firm B

TPL_A1 = uuid.UUID("b1000000-0000-0000-0000-000000000000")  # firm A
TPL_B1 = uuid.UUID("b2000000-0000-0000-0000-000000000000")  # firm B

PKG_A1 = uuid.UUID("c1000000-0000-0000-0000-000000000000")  # firm A, matter A
PKG_B1 = uuid.UUID("c2000000-0000-0000-0000-000000000000")  # firm B, matter B

VER_A1 = uuid.UUID("d1000000-0000-0000-0000-000000000000")  # PKG_A1 v1
VER_B1 = uuid.UUID("d2000000-0000-0000-0000-000000000000")  # PKG_B1 v1

CHK_A1 = uuid.UUID("e1000000-0000-0000-0000-000000000000")  # firm A, matter A
CHK_B1 = uuid.UUID("e2000000-0000-0000-0000-000000000000")  # firm B, matter B

TASK_A1 = uuid.UUID("f1000000-0000-0000-0000-000000000000")  # firm A, matter A
TASK_B1 = uuid.UUID("f2000000-0000-0000-0000-000000000000")  # firm B, matter B

SHR_A1 = uuid.UUID("a4000000-0000-0000-0000-000000000000")  # firm A, matter A
SHR_B1 = uuid.UUID("a5000000-0000-0000-0000-000000000000")  # firm B, matter B

AI_A1 = uuid.UUID("a6000000-0000-0000-0000-000000000000")  # firm A, matter A
AI_B1 = uuid.UUID("a7000000-0000-0000-0000-000000000000")  # firm B, matter B
AI_A_NULL = uuid.UUID("a8000000-0000-0000-0000-000000000000")  # firm A, matter null

REM_A1 = uuid.UUID("a9000000-0000-0000-0000-000000000000")  # firm A, matter A
REM_B1 = uuid.UUID("aa000000-0000-0000-0000-000000000000")  # firm B, matter B
REM_A_NULL = uuid.UUID("ab000000-0000-0000-0000-000000000000")  # firm A, matter null

NEW_TABLES = (
    "file_uploads",
    "document_templates",
    "document_packages",
    "document_versions",
    "signing_checklists",
    "funding_tasks",
    "vault_shares",
    "ai_interactions",
    "reminders",
)


@pytest.fixture(scope="session")
def slice4(db):
    """Insert Slice 4 synthetic fixtures as owner (RLS bypassed)."""
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s), (%s)",
            (USER_I, USER_J, USER_K),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Admin I', 'admin-i@example.test'), "
            "(%s, 'Synthetic Staff J', 'staff-j@example.test'), "
            "(%s, 'Synthetic Outsider K', 'outsider-k@example.test')",
            (USER_I, USER_J, USER_K),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'firm_admin', 'active'), "
            "(%s, %s, 'staff', 'active')",
            (FIRM_A, USER_I, FIRM_B, USER_J),
        )
        cur.execute(
            "insert into assets (id, firm_id, household_id, matter_id, asset_type, label, "
            "created_by_user_id) values (%s, %s, %s, %s, 'bank_account', 'Synthetic Checking', %s)",
            (ASSET_A1, FIRM_A, HOUSEHOLD_A, MATTER_A, USER_A),
        )
        cur.execute(
            "insert into file_uploads (id, firm_id, household_id, matter_id, uploader_user_id, "
            "original_filename, stored_path, mime_type, size_bytes, scan_status) values "
            "(%s, %s, %s, %s, %s, 'synthetic-id.pdf', 'synthetic/id-a1.pdf', 'application/pdf', 1024, 'clean'), "
            "(%s, %s, %s, null, %s, 'synthetic-bank.pdf', 'synthetic/bank-a2.pdf', 'application/pdf', 2048, 'pending'), "
            "(%s, %s, %s, %s, %s, 'synthetic-deed.pdf', 'synthetic/deed-b1.pdf', 'application/pdf', 4096, 'clean')",
            (
                FUP_A1, FIRM_A, HOUSEHOLD_A, MATTER_A, USER_C,
                FUP_A2, FIRM_A, HOUSEHOLD_A, USER_A,
                FUP_B1, FIRM_B, HOUSEHOLD_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into document_templates (id, firm_id, name, template_type, pack_id, version, "
            "content, created_by_user_id) values "
            "(%s, %s, 'Synthetic Will Template', 'will', 'us-va', '1.0.0', '{\"synthetic\": true}', %s), "
            "(%s, %s, 'Synthetic Trust Template', 'trust', 'us-va', '1.0.0', '{\"synthetic\": true}', %s)",
            (TPL_A1, FIRM_A, USER_I, TPL_B1, FIRM_B, USER_B),
        )
        cur.execute(
            "insert into document_packages (id, firm_id, matter_id, template_id, status, "
            "created_by_user_id) values "
            "(%s, %s, %s, %s, 'draft', %s), "
            "(%s, %s, %s, %s, 'draft', %s)",
            (
                PKG_A1, FIRM_A, MATTER_A, TPL_A1, USER_A,
                PKG_B1, FIRM_B, MATTER_B, TPL_B1, USER_B,
            ),
        )
        cur.execute(
            "insert into document_versions (id, package_id, version, file_upload_id, "
            "watermark_text, created_by_user_id) values "
            "(%s, %s, 1, %s, 'DRAFT - synthetic', %s), "
            "(%s, %s, 1, %s, 'DRAFT - synthetic', %s)",
            (
                VER_A1, PKG_A1, FUP_A1, USER_A,
                VER_B1, PKG_B1, FUP_B1, USER_B,
            ),
        )
        cur.execute(
            "insert into signing_checklists (id, firm_id, matter_id, checklist) values "
            "(%s, %s, %s, '{\"steps\": [\"witness\", \"notary\"]}'), "
            "(%s, %s, %s, '{\"steps\": [\"witness\"]}')",
            (CHK_A1, FIRM_A, MATTER_A, CHK_B1, FIRM_B, MATTER_B),
        )
        cur.execute(
            "insert into funding_tasks (id, firm_id, matter_id, asset_id, task_type, title, status) values "
            "(%s, %s, %s, %s, 'retitle', 'Synthetic retitle task', 'pending'), "
            "(%s, %s, %s, null, 'beneficiary', 'Synthetic beneficiary task', 'pending')",
            (TASK_A1, FIRM_A, MATTER_A, ASSET_A1, TASK_B1, FIRM_B, MATTER_B),
        )
        cur.execute(
            "insert into vault_shares (id, firm_id, matter_id, token_hash, label, expires_at, "
            "created_by_user_id) values "
            "(%s, %s, %s, 'synthetic-hash-a1', 'Synthetic share A', now() + interval '30 days', %s), "
            "(%s, %s, %s, 'synthetic-hash-b1', 'Synthetic share B', now() + interval '30 days', %s)",
            (
                SHR_A1, FIRM_A, MATTER_A, USER_A,
                SHR_B1, FIRM_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into ai_interactions (id, firm_id, matter_id, user_id, question_summary, "
            "answer_summary, citations, trace_hash) values "
            "(%s, %s, %s, %s, 'Synthetic question summary A', 'Synthetic answer summary A', '[]', 'synthetic-trace-a1'), "
            "(%s, %s, %s, %s, 'Synthetic question summary B', 'Synthetic answer summary B', '[]', 'synthetic-trace-b1'), "
            "(%s, %s, null, %s, 'Synthetic question summary A-null', 'Synthetic answer summary A-null', '[]', 'synthetic-trace-a-null')",
            (
                AI_A1, FIRM_A, MATTER_A, USER_C,
                AI_B1, FIRM_B, MATTER_B, USER_B,
                AI_A_NULL, FIRM_A, USER_A,
            ),
        )
        cur.execute(
            "insert into reminders (id, firm_id, matter_id, user_id, reminder_type, scheduled_for, payload) values "
            "(%s, %s, %s, %s, 'signing', now() + interval '7 days', '{\"synthetic\": true}'), "
            "(%s, %s, %s, %s, 'funding', now() + interval '7 days', '{\"synthetic\": true}'), "
            "(%s, %s, null, %s, 'review', now() + interval '7 days', '{\"synthetic\": true}')",
            (
                REM_A1, FIRM_A, MATTER_A, USER_C,
                REM_B1, FIRM_B, MATTER_B, USER_B,
                REM_A_NULL, FIRM_A, USER_A,
            ),
        )
    db.commit()
    return db


def _ids(cur, table):
    cur.execute(f"select id from {table} order by id")
    return {row[0] for row in cur.fetchall()}


# --- firm isolation --------------------------------------------------------

def test_firm_a_attorney_sees_own_slice4_rows(db, slice4):
    """USER_A (attorney, firm A) sees firm A's slice-4 rows, never firm B's."""
    with as_user(db, USER_A) as cur:
        assert _ids(cur, "file_uploads") == {FUP_A1, FUP_A2}
        assert _ids(cur, "document_templates") == {TPL_A1}
        assert _ids(cur, "document_packages") == {PKG_A1}
        assert _ids(cur, "document_versions") == {VER_A1}
        assert _ids(cur, "signing_checklists") == {CHK_A1}
        assert _ids(cur, "funding_tasks") == {TASK_A1}
        assert _ids(cur, "vault_shares") == {SHR_A1}
        assert _ids(cur, "ai_interactions") == {AI_A1, AI_A_NULL}
        assert _ids(cur, "reminders") == {REM_A1, REM_A_NULL}


def test_firm_b_staff_sees_only_firm_b_rows(db, slice4):
    """USER_J (staff, firm B) sees only firm B's slice-4 rows."""
    with as_user(db, USER_J) as cur:
        assert _ids(cur, "file_uploads") == {FUP_B1}
        assert _ids(cur, "document_templates") == {TPL_B1}
        assert _ids(cur, "document_packages") == {PKG_B1}
        assert _ids(cur, "document_versions") == {VER_B1}
        assert _ids(cur, "signing_checklists") == {CHK_B1}
        assert _ids(cur, "funding_tasks") == {TASK_B1}
        assert _ids(cur, "vault_shares") == {SHR_B1}
        assert _ids(cur, "ai_interactions") == {AI_B1}
        assert _ids(cur, "reminders") == {REM_B1}


def test_firm_a_admin_full_write_access(db, slice4):
    """USER_I (firm A admin) has full access across the slice-4 tables."""
    with as_user(db, USER_I) as cur:
        # file_uploads roundtrip
        cur.execute(
            "insert into file_uploads (firm_id, household_id, matter_id, uploader_user_id, "
            "original_filename, stored_path) values (%s, %s, %s, %s, 'synthetic.pdf', 'synthetic/x.pdf') "
            "returning id",
            (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_I),
        )
        new_fup = cur.fetchone()[0]
        cur.execute("update file_uploads set scan_status = 'clean' where id = %s", (new_fup,))
        assert cur.rowcount == 1
        cur.execute("delete from file_uploads where id = %s", (new_fup,))
        assert cur.rowcount == 1
        # document_packages roundtrip
        cur.execute(
            "insert into document_packages (firm_id, matter_id, template_id, created_by_user_id) "
            "values (%s, %s, %s, %s) returning id",
            (FIRM_A, MATTER_A, TPL_A1, USER_I),
        )
        new_pkg = cur.fetchone()[0]
        cur.execute("update document_packages set status = 'demo_released' where id = %s", (new_pkg,))
        assert cur.rowcount == 1
        # document_versions insert (immutable: no update/delete policy)
        cur.execute(
            "insert into document_versions (package_id, version, watermark_text, created_by_user_id) "
            "values (%s, 2, 'DRAFT - synthetic', %s)",
            (PKG_A1, USER_I),
        )
        cur.execute("delete from document_packages where id = %s", (new_pkg,))
        assert cur.rowcount == 1
        # checklists / tasks roundtrip
        cur.execute(
            "insert into signing_checklists (firm_id, matter_id, checklist) "
            "values (%s, %s, '{\"steps\": []}') returning id",
            (FIRM_A, MATTER_A),
        )
        new_chk = cur.fetchone()[0]
        cur.execute(
            "update signing_checklists set completed_at = now(), completed_by_user_id = %s where id = %s",
            (USER_I, new_chk),
        )
        assert cur.rowcount == 1
        cur.execute("delete from signing_checklists where id = %s", (new_chk,))
        assert cur.rowcount == 1
        cur.execute(
            "insert into funding_tasks (firm_id, matter_id, task_type, title, evidence_file_id) "
            "values (%s, %s, 'retitle', 'Synthetic task', %s) returning id",
            (FIRM_A, MATTER_A, FUP_A1),
        )
        new_task = cur.fetchone()[0]
        cur.execute("update funding_tasks set status = 'completed' where id = %s", (new_task,))
        assert cur.rowcount == 1
        cur.execute("delete from funding_tasks where id = %s", (new_task,))
        assert cur.rowcount == 1
        # shares / ai / reminders roundtrip
        cur.execute(
            "insert into vault_shares (firm_id, matter_id, token_hash, expires_at, created_by_user_id) "
            "values (%s, %s, 'synthetic-hash-temp', now() + interval '1 day', %s) returning id",
            (FIRM_A, MATTER_A, USER_I),
        )
        new_shr = cur.fetchone()[0]
        cur.execute("delete from vault_shares where id = %s", (new_shr,))
        assert cur.rowcount == 1
        cur.execute(
            "insert into ai_interactions (firm_id, matter_id, user_id, question_summary, answer_summary) "
            "values (%s, %s, %s, 'q', 'a') returning id",
            (FIRM_A, MATTER_A, USER_I),
        )
        new_ai = cur.fetchone()[0]
        cur.execute("update ai_interactions set answer_summary = 'b' where id = %s", (new_ai,))
        assert cur.rowcount == 1
        cur.execute("delete from ai_interactions where id = %s", (new_ai,))
        assert cur.rowcount == 1
        cur.execute(
            "insert into reminders (firm_id, matter_id, user_id, reminder_type, scheduled_for) "
            "values (%s, %s, %s, 'signing', now() + interval '1 day') returning id",
            (FIRM_A, MATTER_A, USER_I),
        )
        new_rem = cur.fetchone()[0]
        cur.execute("delete from reminders where id = %s", (new_rem,))
        assert cur.rowcount == 1
    # the writes above rolled back; fixtures unchanged for the rest of the suite
    with as_user(db, USER_I) as cur:
        assert _ids(cur, "document_versions") == {VER_A1}


def test_document_templates_admin_only_writes(db, slice4):
    """USER_I (admin) can write templates; USER_A (attorney) can only SELECT;
    the household client sees none."""
    with as_user(db, USER_I) as cur:
        cur.execute(
            "insert into document_templates (firm_id, name, template_type, created_by_user_id) "
            "values (%s, 'Synthetic Temp', 'poa', %s) returning id",
            (FIRM_A, USER_I),
        )
        new_tpl = cur.fetchone()[0]
        cur.execute("update document_templates set version = '2.0.0' where id = %s", (new_tpl,))
        assert cur.rowcount == 1
        cur.execute("delete from document_templates where id = %s", (new_tpl,))
        assert cur.rowcount == 1
    with as_user(db, USER_A) as cur:
        assert _ids(cur, "document_templates") == {TPL_A1}
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into document_templates (firm_id, name, template_type, created_by_user_id) "
                "values (%s, 'Synthetic Sneak', 'will', %s)",
                (FIRM_A, USER_A),
            )
    with as_user(db, USER_C) as cur:
        assert _ids(cur, "document_templates") == set()


def test_document_versions_immutable_for_firm_members(db, slice4):
    """No UPDATE/DELETE policies on document_versions: updates touch zero rows."""
    with as_user(db, USER_A) as cur:
        cur.execute("update document_versions set watermark_text = 'x' where id = %s", (VER_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from document_versions where id = %s", (VER_A1,))
        assert cur.rowcount == 0


# --- household member read/write paths ---------------------------------------

def test_household_member_reads_own_slice4_rows(db, slice4):
    """USER_C (client, household A) can SELECT firm A slice-4 rows but sees
    nothing of firm B's — and nothing from templates."""
    with as_user(db, USER_C) as cur:
        assert _ids(cur, "file_uploads") == {FUP_A1, FUP_A2}
        assert _ids(cur, "document_templates") == set()
        assert _ids(cur, "document_packages") == {PKG_A1}
        assert _ids(cur, "document_versions") == {VER_A1}
        assert _ids(cur, "signing_checklists") == {CHK_A1}
        assert _ids(cur, "funding_tasks") == {TASK_A1}
        assert _ids(cur, "vault_shares") == {SHR_A1}
        assert _ids(cur, "ai_interactions") == {AI_A1}  # AI_A_NULL has no matter
        assert _ids(cur, "reminders") == {REM_A1}  # REM_A_NULL has no matter


def test_household_member_file_upload_insert(db, slice4):
    """USER_C may INSERT uploads for their own household, not for firm B's,
    and may not DELETE/UPDATE existing uploads."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "insert into file_uploads (firm_id, household_id, matter_id, uploader_user_id, "
            "original_filename, stored_path) "
            "values (%s, %s, %s, %s, 'client-doc.pdf', 'synthetic/client.pdf') returning id",
            (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_C),
        )
        new_id = cur.fetchone()[0]
        cur.execute("select uploader_user_id from file_uploads where id = %s", (new_id,))
        assert cur.fetchone()[0] == USER_C
    # denied INSERT aborts its transaction: separate block for HOUSEHOLD_B
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into file_uploads (firm_id, household_id, matter_id, uploader_user_id, "
                "original_filename, stored_path) "
                "values (%s, %s, %s, %s, 'sneak.pdf', 'synthetic/sneak.pdf')",
                (FIRM_B, HOUSEHOLD_B, MATTER_B, USER_C),
            )
    with as_user(db, USER_C) as cur:
        cur.execute("update file_uploads set scan_status = 'clean' where id = %s", (FUP_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from file_uploads where id = %s", (FUP_A1,))
        assert cur.rowcount == 0


def test_household_member_signing_checklist_completion(db, slice4):
    """USER_C may UPDATE completion fields on CHK_A1 but may not rewrite the
    checklist content or touch CHK_B1."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update signing_checklists set completed_at = now(), completed_by_user_id = %s "
            "where id = %s",
            (USER_C, CHK_A1),
        )
        assert cur.rowcount == 1
        cur.execute(
            "select completed_by_user_id from signing_checklists where id = %s",
            (CHK_A1,),
        )
        assert cur.fetchone()[0] == USER_C
    with as_user(db, USER_C) as cur:
        # guard trigger: rewriting the checklist content is rejected
        with pytest.raises(errors.RaiseException):
            cur.execute(
                "update signing_checklists set checklist = '{\"steps\": []}' where id = %s",
                (CHK_A1,),
            )
    with as_user(db, USER_C) as cur:
        # firm B's checklist: zero rows touched
        cur.execute(
            "update signing_checklists set completed_at = now() where id = %s",
            (CHK_B1,),
        )
        assert cur.rowcount == 0


def test_household_member_funding_task_evidence(db, slice4):
    """USER_C may UPDATE status/evidence on TASK_A1 but may not rewrite the
    task itself or touch TASK_B1."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update funding_tasks set status = 'in_progress', evidence_file_id = %s where id = %s",
            (FUP_A1, TASK_A1),
        )
        assert cur.rowcount == 1
        cur.execute("select status from funding_tasks where id = %s", (TASK_A1,))
        assert cur.fetchone()[0] == "in_progress"
    with as_user(db, USER_C) as cur:
        # guard trigger: rewriting the title is rejected
        with pytest.raises(errors.RaiseException):
            cur.execute(
                "update funding_tasks set title = 'Changed' where id = %s",
                (TASK_A1,),
            )
    with as_user(db, USER_C) as cur:
        # firm B's task: zero rows touched
        cur.execute("update funding_tasks set status = 'completed' where id = %s", (TASK_B1,))
        assert cur.rowcount == 0


def test_household_member_cannot_insert_other_tables(db, slice4):
    """Household writes are limited to file uploads; every other slice-4
    INSERT path is denied."""
    denied = (
        ("document_packages",
         "insert into document_packages (firm_id, matter_id, template_id, created_by_user_id) "
         "values (%s, %s, %s, %s)",
         (FIRM_A, MATTER_A, TPL_A1, USER_C)),
        ("document_versions",
         "insert into document_versions (package_id, version, watermark_text, created_by_user_id) "
         "values (%s, 2, 'x', %s)",
         (PKG_A1, USER_C)),
        ("signing_checklists",
         "insert into signing_checklists (firm_id, matter_id, checklist) "
         "values (%s, %s, '{}')",
         (FIRM_A, MATTER_A)),
        ("funding_tasks",
         "insert into funding_tasks (firm_id, matter_id, task_type, title) "
         "values (%s, %s, 'retitle', 'Synthetic sneak')",
         (FIRM_A, MATTER_A)),
        ("vault_shares",
         "insert into vault_shares (firm_id, matter_id, token_hash, expires_at, created_by_user_id) "
         "values (%s, %s, 'synthetic-hash-sneak', now() + interval '1 day', %s)",
         (FIRM_A, MATTER_A, USER_C)),
        ("ai_interactions",
         "insert into ai_interactions (firm_id, matter_id, user_id, question_summary, answer_summary) "
         "values (%s, %s, %s, 'q', 'a')",
         (FIRM_A, MATTER_A, USER_C)),
        ("reminders",
         "insert into reminders (firm_id, matter_id, user_id, reminder_type, scheduled_for) "
         "values (%s, %s, %s, 'signing', now() + interval '1 day')",
         (FIRM_A, MATTER_A, USER_C)),
        ("document_templates",
         "insert into document_templates (firm_id, name, template_type, created_by_user_id) "
         "values (%s, 'Synthetic sneak', 'will', %s)",
         (FIRM_A, USER_C)),
    )
    for table, sql, params in denied:
        with as_user(db, USER_C) as cur:
            with pytest.raises(errors.InsufficientPrivilege):
                cur.execute(sql, params)
        # noqa: each denial is asserted independently


def test_household_member_cannot_delete_firm_owned_rows(db, slice4):
    """DELETE is firm-side only: household DELETEs touch zero rows."""
    with as_user(db, USER_C) as cur:
        cur.execute("delete from document_packages where id = %s", (PKG_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from vault_shares where id = %s", (SHR_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from reminders where id = %s", (REM_A1,))
        assert cur.rowcount == 0


# --- anonymous / non-member ----------------------------------------------------

def test_anonymous_gets_nothing_from_new_tables(db, slice4):
    """No app.user_id -> zero rows on every Slice 4 table."""
    with as_user(db, None) as cur:
        for table in NEW_TABLES:
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table


def test_non_member_gets_nothing_from_new_tables(db, slice4):
    """USER_K (no firm or household membership) sees zero rows on every
    Slice 4 table, and writes are denied."""
    with as_user(db, USER_K) as cur:
        for table in NEW_TABLES:
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table
    with as_user(db, USER_K) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into file_uploads (firm_id, household_id, matter_id, uploader_user_id, "
                "original_filename, stored_path) "
                "values (%s, %s, %s, %s, 'sneak.pdf', 'synthetic/sneak.pdf')",
                (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_K),
            )
    with as_user(db, USER_K) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into vault_shares (firm_id, matter_id, token_hash, expires_at, created_by_user_id) "
                "values (%s, %s, 'synthetic-hash-sneak2', now() + interval '1 day', %s)",
                (FIRM_A, MATTER_A, USER_K),
            )
    with as_user(db, USER_K) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into reminders (firm_id, matter_id, user_id, reminder_type, scheduled_for) "
                "values (%s, %s, %s, 'signing', now() + interval '1 day')",
                (FIRM_A, MATTER_A, USER_K),
            )


# --- Slice 4 invariants ----------------------------------------------------------

def test_vault_share_token_hash_unique(db, slice4):
    """Duplicate token_hash is rejected."""
    with db.cursor() as cur:  # owner: RLS bypassed, constraint still enforced
        with pytest.raises(errors.UniqueViolation):
            cur.execute(
                "insert into vault_shares (firm_id, matter_id, token_hash, expires_at, created_by_user_id) "
                "values (%s, %s, 'synthetic-hash-a1', now() + interval '1 day', %s)",
                (FIRM_A, MATTER_A, USER_A),
            )
        db.rollback()


def test_document_version_unique_per_package(db, slice4):
    """A second version 1 for the same package is rejected."""
    with db.cursor() as cur:  # owner: RLS bypassed, constraint still enforced
        with pytest.raises(errors.UniqueViolation):
            cur.execute(
                "insert into document_versions (package_id, version, created_by_user_id) "
                "values (%s, 1, %s)",
                (PKG_A1, USER_A),
            )
        db.rollback()
        # version 2 for the same package is fine
        cur.execute(
            "insert into document_versions (package_id, version, created_by_user_id) "
            "values (%s, 2, %s)",
            (PKG_A1, USER_A),
        )
        db.rollback()


def test_scan_status_check(db, slice4):
    """file_uploads.scan_status outside the enum is rejected."""
    with as_user(db, USER_I) as cur:
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                "insert into file_uploads (firm_id, household_id, uploader_user_id, "
                "original_filename, stored_path, scan_status) "
                "values (%s, %s, %s, 'synthetic.pdf', 'synthetic/x.pdf', 'bogus')",
                (FIRM_A, HOUSEHOLD_A, USER_I),
            )


def test_document_package_status_check(db, slice4):
    """document_packages.status outside the enum is rejected."""
    with as_user(db, USER_I) as cur:
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                "insert into document_packages (firm_id, matter_id, template_id, status, created_by_user_id) "
                "values (%s, %s, %s, 'bogus', %s)",
                (FIRM_A, MATTER_A, TPL_A1, USER_I),
            )


def test_ai_interactions_has_no_raw_prompt_columns(db, slice4):
    """ai_interactions must never carry raw prompt/response columns — only
    summaries, citations and a trace hash."""
    with as_user(db, USER_I) as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'ai_interactions'"
        )
        columns = {row[0] for row in cur.fetchall()}
        assert columns == {
            "id", "firm_id", "matter_id", "user_id",
            "question_summary", "answer_summary", "citations",
            "trace_hash", "created_at",
        }
