"""Repository for payouts and case requests (raw SQL, psycopg)."""

from typing import Any
from uuid import UUID


def insert_payout(
    conn: Any,
    *,
    firm_id: UUID,
    attorney_user_id: UUID,
    matter_id: UUID,
    amount_cents: int,
    tier: str,
) -> dict[str, Any]:
    """Insert a payout; return the row. Idempotent on (matter_id)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payouts (firm_id, attorney_user_id, matter_id, amount_cents, tier)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (matter_id) DO NOTHING
            RETURNING *
            """,
            (str(firm_id), str(attorney_user_id), str(matter_id), amount_cents, tier),
        )
        row = cur.fetchone()
        if row is None:
            # Already existed — fetch the existing row.
            cur.execute("SELECT * FROM payouts WHERE matter_id = %s", (str(matter_id),))
            row = cur.fetchone()
        return dict(row)


def _payout_select() -> str:
    return """
        SELECT p.*,
               prof.display_name AS attorney_name,
               m.title AS matter_title
        FROM payouts p
        LEFT JOIN profiles prof ON prof.id = p.attorney_user_id
        LEFT JOIN matters m ON m.id = p.matter_id
    """


def list_payouts_for_firm(
    conn: Any, *, firm_id: UUID, status: str | None = None
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if status:
            cur.execute(
                _payout_select()
                + " WHERE p.firm_id = %s AND p.status = %s ORDER BY p.created_at DESC",
                (str(firm_id), status),
            )
        else:
            cur.execute(
                _payout_select() + " WHERE p.firm_id = %s ORDER BY p.created_at DESC",
                (str(firm_id),),
            )
        return [dict(r) for r in cur.fetchall()]


def list_payouts_for_attorney(
    conn: Any, *, firm_id: UUID, attorney_user_id: UUID
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            _payout_select()
            + " WHERE p.firm_id = %s AND p.attorney_user_id = %s ORDER BY p.created_at DESC",
            (str(firm_id), str(attorney_user_id)),
        )
        return [dict(r) for r in cur.fetchall()]


def get_payout(conn: Any, *, payout_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_payout_select() + " WHERE p.id = %s", (str(payout_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def set_payout_status(
    conn: Any,
    *,
    payout_id: UUID,
    status: str,
    approved_by_user_id: UUID | None = None,
    stripe_payout_id: str | None = None,
    dispute_reason: str | None = None,
) -> dict[str, Any]:
    """Transition a payout's status; stamps the matching timestamp."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE payouts
            SET status = %s,
                approved_at = CASE WHEN %s = 'approved' THEN now() ELSE approved_at END,
                approved_by_user_id = COALESCE(%s, approved_by_user_id),
                paid_at = CASE WHEN %s = 'paid' THEN now() ELSE paid_at END,
                stripe_payout_id = COALESCE(%s, stripe_payout_id),
                dispute_reason = COALESCE(%s, dispute_reason)
            WHERE id = %s
            RETURNING *
            """,
            (
                status,
                status,
                str(approved_by_user_id) if approved_by_user_id else None,
                status,
                stripe_payout_id,
                dispute_reason,
                str(payout_id),
            ),
        )
        return dict(cur.fetchone())


def payout_summary_for_attorney(
    conn: Any, *, firm_id: UUID, attorney_user_id: UUID
) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COALESCE(SUM(amount_cents), 0) AS total,
              COALESCE(SUM(amount_cents) FILTER (WHERE status = 'pending'), 0) AS pending,
              COALESCE(SUM(amount_cents) FILTER (WHERE status = 'approved'), 0) AS approved,
              COALESCE(SUM(amount_cents) FILTER (WHERE status = 'paid'), 0) AS paid,
              COUNT(*) AS count
            FROM payouts
            WHERE firm_id = %s AND attorney_user_id = %s AND status != 'disputed'
            """,
            (str(firm_id), str(attorney_user_id)),
        )
        row = cur.fetchone()
        return {
            "total_earned_cents": int(row["total"]),
            "pending_cents": int(row["pending"]),
            "approved_cents": int(row["approved"]),
            "paid_cents": int(row["paid"]),
            "payout_count": int(row["count"]),
        }


def insert_case_request(
    conn: Any, *, firm_id: UUID, attorney_user_id: UUID, matter_id: UUID
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO case_requests (firm_id, attorney_user_id, matter_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (attorney_user_id, matter_id) DO NOTHING
            RETURNING *
            """,
            (str(firm_id), str(attorney_user_id), str(matter_id)),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT * FROM case_requests WHERE attorney_user_id = %s AND matter_id = %s",
                (str(attorney_user_id), str(matter_id)),
            )
            row = cur.fetchone()
        return dict(row)


def _request_select() -> str:
    return """
        SELECT r.*,
               prof.display_name AS attorney_name,
               m.title AS matter_title
        FROM case_requests r
        LEFT JOIN profiles prof ON prof.id = r.attorney_user_id
        LEFT JOIN matters m ON m.id = r.matter_id
    """


def list_case_requests_for_firm(
    conn: Any, *, firm_id: UUID, status: str | None = None
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if status:
            cur.execute(
                _request_select()
                + " WHERE r.firm_id = %s AND r.status = %s ORDER BY r.created_at DESC",
                (str(firm_id), status),
            )
        else:
            cur.execute(
                _request_select() + " WHERE r.firm_id = %s ORDER BY r.created_at DESC",
                (str(firm_id),),
            )
        return [dict(r) for r in cur.fetchall()]


def get_case_request(conn: Any, *, request_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_request_select() + " WHERE r.id = %s", (str(request_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def set_case_request_status(
    conn: Any, *, request_id: UUID, status: str, decided_by_user_id: UUID
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE case_requests
            SET status = %s, decided_at = now(), decided_by_user_id = %s
            WHERE id = %s
            RETURNING *
            """,
            (status, str(decided_by_user_id), str(request_id)),
        )
        return dict(cur.fetchone())


def set_attorney_availability(
    conn: Any, *, firm_id: UUID, user_id: UUID, accepting_cases: bool
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE firm_memberships
            SET accepting_cases = %s
            WHERE firm_id = %s AND user_id = %s AND status = 'active'
            """,
            (accepting_cases, str(firm_id), str(user_id)),
        )


def get_attorney_availability(
    conn: Any, *, firm_id: UUID, user_id: UUID
) -> bool | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accepting_cases FROM firm_memberships
            WHERE firm_id = %s AND user_id = %s AND status = 'active'
            """,
            (str(firm_id), str(user_id)),
        )
        row = cur.fetchone()
        return bool(row["accepting_cases"]) if row else None


def list_active_attorneys(conn: Any, *, firm_id: UUID) -> list[dict[str, Any]]:
    """Attorneys in the firm who are accepting cases."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fm.user_id, prof.display_name, prof.email
            FROM firm_memberships fm
            JOIN profiles prof ON prof.id = fm.user_id
            WHERE fm.firm_id = %s AND fm.status = 'active'
              AND fm.role = 'attorney' AND fm.accepting_cases = true
            ORDER BY prof.display_name
            """,
            (str(firm_id),),
        )
        return [dict(r) for r in cur.fetchall()]
