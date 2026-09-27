"""RLS allow/deny integration tests for LegacyFlow Slice 3 (assets & review).

Covers the eight new tables (assets, liabilities, asset_flags,
approval_gates, review_packets, comments, clarification_requests,
notification_outbox) plus the Slice 3 invariants: one pending approval gate
per (matter, gate_type), immutable review packets, append-only comments, and
household response-only updates on clarification requests.

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
USER_F = uuid.UUID("92000000-0000-0000-0000-000000000000")  # firm A staff
USER_G = uuid.UUID("93000000-0000-0000-0000-000000000000")  # firm B staff
USER_H = uuid.UUID("94000000-0000-0000-0000-000000000000")  # no memberships

ASSET_A1 = uuid.UUID("95000000-0000-0000-0000-000000000000")  # firm A, matter A
ASSET_A2 = uuid.UUID("95100000-0000-0000-0000-000000000000")  # firm A, matter null
ASSET_B1 = uuid.UUID("95200000-0000-0000-0000-000000000000")  # firm B

LIAB_A1 = uuid.UUID("95300000-0000-0000-0000-000000000000")
LIAB_B1 = uuid.UUID("95400000-0000-0000-0000-000000000000")

FLAG_A1 = uuid.UUID("95500000-0000-0000-0000-000000000000")  # on ASSET_A1
FLAG_B1 = uuid.UUID("95600000-0000-0000-0000-000000000000")  # on ASSET_B1

GATE_A1 = uuid.UUID("95700000-0000-0000-0000-000000000000")  # A intake_approval pending
GATE_A2 = uuid.UUID("95800000-0000-0000-0000-000000000000")  # A document_release pending
GATE_B1 = uuid.UUID("95900000-0000-0000-0000-000000000000")  # B intake_approval pending

PACKET_A1 = uuid.UUID("95a00000-0000-0000-0000-000000000000")
PACKET_B1 = uuid.UUID("95b00000-0000-0000-0000-000000000000")

COMMENT_A1 = uuid.UUID("95c00000-0000-0000-0000-000000000000")
COMMENT_B1 = uuid.UUID("95d00000-0000-0000-0000-000000000000")

CLAR_A1 = uuid.UUID("95e00000-0000-0000-0000-000000000000")  # created by firm (USER_A)
CLAR_A2 = uuid.UUID("95f00000-0000-0000-0000-000000000000")  # created by client (USER_C)
CLAR_B1 = uuid.UUID("96000000-0000-0000-0000-000000000000")

OUTBOX_A1 = uuid.UUID("96100000-0000-0000-0000-000000000000")
OUTBOX_B1 = uuid.UUID("96200000-0000-0000-0000-000000000000")

NEW_TABLES = (
    "assets",
    "liabilities",
    "asset_flags",
    "approval_gates",
    "review_packets",
    "comments",
    "clarification_requests",
    "notification_outbox",
)


@pytest.fixture(scope="session")
def slice3(db):
    """Insert Slice 3 synthetic fixtures as owner (RLS bypassed)."""
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s), (%s)",
            (USER_F, USER_G, USER_H),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Staff F', 'staff-f@example.test'), "
            "(%s, 'Synthetic Staff G', 'staff-g@example.test'), "
            "(%s, 'Synthetic Outsider H', 'outsider-h@example.test')",
            (USER_F, USER_G, USER_H),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'staff', 'active'), "
            "(%s, %s, 'staff', 'active')",
            (FIRM_A, USER_F, FIRM_B, USER_G),
        )
        cur.execute(
            "insert into assets (id, firm_id, household_id, matter_id, asset_type, label, "
            "estimated_value_cents, ownership, beneficiary_designation, created_by_user_id) values "
            "(%s, %s, %s, %s, 'bank_account', 'Synthetic Checking', 1500000, 'joint', "
            "'{\"primary\": [\"Synthetic Person A1\"]}', %s), "
            "(%s, %s, %s, null, 'vehicle', 'Synthetic Sedan', 2200000, 'sole', '{}', %s), "
            "(%s, %s, %s, %s, 'investment', 'Synthetic Brokerage', 5000000, 'sole', '{}', %s)",
            (
                ASSET_A1, FIRM_A, HOUSEHOLD_A, MATTER_A, USER_A,
                ASSET_A2, FIRM_A, HOUSEHOLD_A, USER_A,
                ASSET_B1, FIRM_B, HOUSEHOLD_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into liabilities (id, firm_id, household_id, matter_id, liability_type, "
            "label, balance_cents, monthly_payment_cents, created_by_user_id) values "
            "(%s, %s, %s, %s, 'mortgage', 'Synthetic Mortgage', 28000000, 145000, %s), "
            "(%s, %s, %s, %s, 'credit_card', 'Synthetic Card', 320000, 5000, %s)",
            (
                LIAB_A1, FIRM_A, HOUSEHOLD_A, MATTER_A, USER_A,
                LIAB_B1, FIRM_B, HOUSEHOLD_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into asset_flags (id, firm_id, asset_id, flag_type, severity, message) values "
            "(%s, %s, %s, 'unknown_beneficiary', 'review', 'Synthetic flag A'), "
            "(%s, %s, %s, 'info', 'info', 'Synthetic flag B')",
            (FLAG_A1, FIRM_A, ASSET_A1, FLAG_B1, FIRM_B, ASSET_B1),
        )
        cur.execute(
            "insert into approval_gates (id, firm_id, matter_id, gate_type, status) values "
            "(%s, %s, %s, 'intake_approval', 'pending'), "
            "(%s, %s, %s, 'document_release', 'pending'), "
            "(%s, %s, %s, 'intake_approval', 'pending')",
            (
                GATE_A1, FIRM_A, MATTER_A,
                GATE_A2, FIRM_A, MATTER_A,
                GATE_B1, FIRM_B, MATTER_B,
            ),
        )
        cur.execute(
            "insert into review_packets (id, firm_id, matter_id, version, snapshot, created_by_user_id) values "
            "(%s, %s, %s, 1, '{\"synthetic\": true}', %s), "
            "(%s, %s, %s, 1, '{\"synthetic\": true}', %s)",
            (PACKET_A1, FIRM_A, MATTER_A, USER_A, PACKET_B1, FIRM_B, MATTER_B, USER_B),
        )
        cur.execute(
            "insert into comments (id, firm_id, matter_id, author_user_id, body) values "
            "(%s, %s, %s, %s, 'Synthetic comment A'), "
            "(%s, %s, %s, %s, 'Synthetic comment B')",
            (
                COMMENT_A1, FIRM_A, MATTER_A, USER_A,
                COMMENT_B1, FIRM_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into clarification_requests (id, firm_id, matter_id, question, status, created_by_user_id) values "
            "(%s, %s, %s, 'Synthetic question A1?', 'open', %s), "
            "(%s, %s, %s, 'Synthetic question A2?', 'open', %s), "
            "(%s, %s, %s, 'Synthetic question B1?', 'open', %s)",
            (
                CLAR_A1, FIRM_A, MATTER_A, USER_A,
                CLAR_A2, FIRM_A, MATTER_A, USER_C,
                CLAR_B1, FIRM_B, MATTER_B, USER_B,
            ),
        )
        cur.execute(
            "insert into notification_outbox (id, firm_id, matter_id, recipient_user_id, "
            "template_key, payload, status) values "
            "(%s, %s, %s, %s, 'gate.pending', '{\"synthetic\": true}', 'pending'), "
            "(%s, %s, %s, %s, 'gate.pending', '{\"synthetic\": true}', 'pending')",
            (OUTBOX_A1, FIRM_A, MATTER_A, USER_A, OUTBOX_B1, FIRM_B, MATTER_B, USER_B),
        )
    db.commit()
    return db


def _ids(cur, table):
    cur.execute(f"select id from {table} order by id")
    return {row[0] for row in cur.fetchall()}


# --- firm isolation --------------------------------------------------------

def test_firm_a_attorney_sees_own_slice3_rows(db, slice3):
    """USER_A (attorney, firm A) sees firm A's slice-3 rows, never firm B's."""
    with as_user(db, USER_A) as cur:
        assert _ids(cur, "assets") == {ASSET_A1, ASSET_A2}
        assert _ids(cur, "liabilities") == {LIAB_A1}
        assert _ids(cur, "asset_flags") == {FLAG_A1}
        assert _ids(cur, "approval_gates") == {GATE_A1, GATE_A2}
        assert _ids(cur, "review_packets") == {PACKET_A1}
        assert _ids(cur, "comments") == {COMMENT_A1}
        assert _ids(cur, "clarification_requests") == {CLAR_A1, CLAR_A2}
        assert _ids(cur, "notification_outbox") == {OUTBOX_A1}


def test_firm_b_staff_sees_only_firm_b_rows(db, slice3):
    """USER_G (staff, firm B) sees only firm B's slice-3 rows."""
    with as_user(db, USER_G) as cur:
        assert _ids(cur, "assets") == {ASSET_B1}
        assert _ids(cur, "liabilities") == {LIAB_B1}
        assert _ids(cur, "asset_flags") == {FLAG_B1}
        assert _ids(cur, "approval_gates") == {GATE_B1}
        assert _ids(cur, "review_packets") == {PACKET_B1}
        assert _ids(cur, "comments") == {COMMENT_B1}
        assert _ids(cur, "clarification_requests") == {CLAR_B1}
        assert _ids(cur, "notification_outbox") == {OUTBOX_B1}


def test_firm_a_staff_full_write_access(db, slice3):
    """USER_F (staff, firm A) has full access: INSERT/UPDATE/DELETE on assets,
    INSERT on review packets and clarification requests."""
    with as_user(db, USER_F) as cur:
        cur.execute(
            "insert into assets (firm_id, household_id, matter_id, asset_type, label, "
            "estimated_value_cents, created_by_user_id) "
            "values (%s, %s, %s, 'real_estate', 'Synthetic Cottage', 35000000, %s) returning id",
            (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_F),
        )
        new_id = cur.fetchone()[0]
        cur.execute("update assets set label = 'Synthetic Cottage Updated' where id = %s", (new_id,))
        assert cur.rowcount == 1
        cur.execute("delete from assets where id = %s", (new_id,))
        assert cur.rowcount == 1
        cur.execute(
            "insert into review_packets (firm_id, matter_id, version, snapshot, created_by_user_id) "
            "values (%s, %s, 2, '{}', %s)",
            (FIRM_A, MATTER_A, USER_F),
        )
        cur.execute(
            "insert into clarification_requests (firm_id, matter_id, question, created_by_user_id) "
            "values (%s, %s, 'Synthetic question A3?', %s)",
            (FIRM_A, MATTER_A, USER_F),
        )
        cur.execute("update approval_gates set status = 'approved', decided_by_user_id = %s, decided_at = now() where id = %s", (USER_F, GATE_A2))
        assert cur.rowcount == 1
    # the writes above rolled back; fixtures unchanged for the rest of the suite
    with as_user(db, USER_F) as cur:
        assert _ids(cur, "review_packets") == {PACKET_A1}


# --- household member read paths --------------------------------------------

def test_household_member_reads_own_slice3_rows(db, slice3):
    """USER_C (client, household A) can SELECT firm A slice-3 rows but sees
    nothing of firm B's — and nothing from the outbox."""
    with as_user(db, USER_C) as cur:
        assert _ids(cur, "assets") == {ASSET_A1, ASSET_A2}
        assert _ids(cur, "liabilities") == {LIAB_A1}
        assert _ids(cur, "asset_flags") == {FLAG_A1}
        assert _ids(cur, "approval_gates") == {GATE_A1, GATE_A2}
        assert _ids(cur, "review_packets") == {PACKET_A1}
        assert _ids(cur, "comments") == {COMMENT_A1}
        assert _ids(cur, "clarification_requests") == {CLAR_A1, CLAR_A2}
        assert _ids(cur, "notification_outbox") == set()


# --- household member write paths ---------------------------------------------

def test_household_member_cannot_insert_assets(db, slice3):
    """USER_C can SELECT assets but INSERT is denied (InsufficientPrivilege)."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into assets (firm_id, household_id, matter_id, asset_type, label, created_by_user_id) "
                "values (%s, %s, %s, 'other', 'Synthetic Sneak', %s)",
                (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_C),
            )


def test_household_member_cannot_write_assets_liabilities_flags(db, slice3):
    """Household writes are SELECT-only on assets, liabilities, asset_flags:
    UPDATE/DELETE touch zero rows; flag INSERT is denied."""
    with as_user(db, USER_C) as cur:
        cur.execute("update assets set label = 'x' where id = %s", (ASSET_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from liabilities where id = %s", (LIAB_A1,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into asset_flags (firm_id, asset_id, flag_type, severity, message) "
                "values (%s, %s, 'info', 'info', 'Synthetic sneak')",
                (FIRM_A, ASSET_A1),
            )


def test_household_member_can_insert_comments(db, slice3):
    """USER_C can INSERT comments on MATTER_A but not on MATTER_B."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "insert into comments (firm_id, matter_id, author_user_id, body) "
            "values (%s, %s, %s, 'Synthetic client comment') returning id",
            (FIRM_A, MATTER_A, USER_C),
        )
        new_id = cur.fetchone()[0]
        cur.execute("select author_user_id from comments where id = %s", (new_id,))
        assert cur.fetchone()[0] == USER_C
    # denied INSERT aborts its transaction: separate block for MATTER_B
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into comments (firm_id, matter_id, author_user_id, body) "
                "values (%s, %s, %s, 'Synthetic sneak')",
                (FIRM_B, MATTER_B, USER_C),
            )


def test_comments_append_only(db, slice3):
    """No UPDATE/DELETE policies on comments: both touch zero rows for firm
    members and household members alike."""
    with as_user(db, USER_A) as cur:
        cur.execute("update comments set body = 'x' where id = %s", (COMMENT_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from comments where id = %s", (COMMENT_A1,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("update comments set body = 'x' where id = %s", (COMMENT_A1,))
        assert cur.rowcount == 0


def test_household_member_cannot_insert_packets_or_gates(db, slice3):
    """review_packets and approval_gates are firm-write only."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into review_packets (firm_id, matter_id, version, snapshot, created_by_user_id) "
                "values (%s, %s, 2, '{}', %s)",
                (FIRM_A, MATTER_A, USER_C),
            )
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into approval_gates (firm_id, matter_id, gate_type, status) "
                "values (%s, %s, 'document_release', 'pending')",
                (FIRM_A, MATTER_A),
            )


def test_review_packets_immutable_for_firm_members(db, slice3):
    """No UPDATE/DELETE policies on review_packets: updates touch zero rows."""
    with as_user(db, USER_A) as cur:
        cur.execute("update review_packets set snapshot = '{\"x\": 1}' where id = %s", (PACKET_A1,))
        assert cur.rowcount == 0
        cur.execute("delete from review_packets where id = %s", (PACKET_A1,))
        assert cur.rowcount == 0


def test_household_member_can_answer_own_clarification_request(db, slice3):
    """USER_C may UPDATE response fields on CLAR_A2 (their own request) but
    may not touch CLAR_A1 (created by the firm) or rewrite the question."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update clarification_requests set status = 'answered', response = 'Synthetic answer', "
            "answered_by_user_id = %s, answered_at = now() where id = %s",
            (USER_C, CLAR_A2),
        )
        assert cur.rowcount == 1
        cur.execute(
            "select status, response from clarification_requests where id = %s",
            (CLAR_A2,),
        )
        status, response = cur.fetchone()
        assert status == "answered" and response == "Synthetic answer"
    with as_user(db, USER_C) as cur:
        # not their own request: zero rows touched
        cur.execute(
            "update clarification_requests set response = 'Synthetic sneak' where id = %s",
            (CLAR_A1,),
        )
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        # guard trigger: rewriting the question on their own request is rejected
        with pytest.raises(errors.RaiseException):
            cur.execute(
                "update clarification_requests set question = 'Changed?' where id = %s",
                (CLAR_A2,),
            )


def test_household_member_cannot_insert_clarification_requests(db, slice3):
    """clarification_requests INSERT is firm-side only."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into clarification_requests (firm_id, matter_id, question, created_by_user_id) "
                "values (%s, %s, 'Synthetic sneak?', %s)",
                (FIRM_A, MATTER_A, USER_C),
            )


def test_household_member_cannot_touch_outbox(db, slice3):
    """The outbox is firm-members only: SELECT sees nothing, INSERT/UPDATE
    are denied for the household client."""
    with as_user(db, USER_C) as cur:
        cur.execute("select count(*) from notification_outbox")
        assert cur.fetchone()[0] == 0
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into notification_outbox (firm_id, template_key, payload) "
                "values (%s, 'sneak', '{}')",
                (FIRM_A,),
            )
    with as_user(db, USER_C) as cur:
        cur.execute("update notification_outbox set status = 'sent' where id = %s", (OUTBOX_A1,))
        assert cur.rowcount == 0


def test_firm_member_outbox_roundtrip(db, slice3):
    """USER_A (attorney) can INSERT/UPDATE/DELETE outbox rows for firm A."""
    with as_user(db, USER_A) as cur:
        cur.execute(
            "insert into notification_outbox (firm_id, matter_id, recipient_user_id, "
            "template_key, payload) "
            "values (%s, %s, %s, 'gate.pending', '{}') returning id",
            (FIRM_A, MATTER_A, USER_A),
        )
        new_id = cur.fetchone()[0]
        cur.execute(
            "update notification_outbox set status = 'sent', sent_at = now(), attempts = 1 "
            "where id = %s",
            (new_id,),
        )
        assert cur.rowcount == 1
        cur.execute("delete from notification_outbox where id = %s", (new_id,))
        assert cur.rowcount == 1


# --- anonymous / non-member ----------------------------------------------------

def test_anonymous_gets_nothing_from_new_tables(db, slice3):
    """No app.user_id -> zero rows on every Slice 3 table."""
    with as_user(db, None) as cur:
        for table in NEW_TABLES:
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table


def test_non_member_gets_nothing_from_new_tables(db, slice3):
    """USER_H (no firm or household membership) sees zero rows on every
    Slice 3 table, and writes are denied."""
    with as_user(db, USER_H) as cur:
        for table in NEW_TABLES:
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table
    with as_user(db, USER_H) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into assets (firm_id, household_id, matter_id, asset_type, label, created_by_user_id) "
                "values (%s, %s, %s, 'other', 'Synthetic sneak', %s)",
                (FIRM_A, HOUSEHOLD_A, MATTER_A, USER_H),
            )
    with as_user(db, USER_H) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into comments (firm_id, matter_id, author_user_id, body) "
                "values (%s, %s, %s, 'Synthetic sneak')",
                (FIRM_A, MATTER_A, USER_H),
            )
    with as_user(db, USER_H) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into notification_outbox (firm_id, template_key, payload) "
                "values (%s, 'sneak', '{}')",
                (FIRM_A,),
            )


# --- Slice 3 invariants ----------------------------------------------------------

def test_pending_gate_unique_per_matter_gate_type(db, slice3):
    """A second pending gate for the same (matter, gate_type) is rejected."""
    with db.cursor() as cur:  # owner: RLS bypassed, constraint still enforced
        with pytest.raises(errors.UniqueViolation):
            cur.execute(
                "insert into approval_gates (firm_id, matter_id, gate_type, status) "
                "values (%s, %s, 'intake_approval', 'pending')",
                (FIRM_A, MATTER_A),
            )
        db.rollback()
        # a decided (non-pending) gate for the same pair is fine
        cur.execute(
            "insert into approval_gates (firm_id, matter_id, gate_type, status) "
            "values (%s, %s, 'intake_approval', 'approved')",
            (FIRM_A, MATTER_A),
        )
        db.rollback()


def test_comments_body_length_capped(db, slice3):
    """comment body > 5000 chars is rejected by the check constraint."""
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                "insert into comments (firm_id, matter_id, author_user_id, body) "
                "values (%s, %s, %s, %s)",
                (FIRM_A, MATTER_A, USER_A, "x" * 5001),
            )
