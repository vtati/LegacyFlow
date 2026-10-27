"""Persistence for the Attorney Reviewer domain (plan 2026-10-27).

All reads/writes are over the frozen artifact tables: snapshots and
sources are written once at render time; runs and findings are the
reviewer's own record. Nothing here reads live intake answers.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def insert_snapshot(conn: Any, **fields: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attorney_review_snapshots
                (matter_id, firm_id, package_id, package_version,
                 source_fingerprint, template_type, template_body,
                 body_text, context)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                str(fields["matter_id"]), str(fields["firm_id"]),
                str(fields["package_id"]), fields["package_version"],
                fields["source_fingerprint"], fields["template_type"],
                fields["template_body"], fields["body_text"],
                json.dumps(fields["context"], default=str),
            ),
        )
        return dict(cur.fetchone())


def get_source(
    conn: Any, *, matter_id: UUID, source_fingerprint: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM attorney_review_sources"
            " WHERE matter_id = %s AND source_fingerprint = %s",
            (str(matter_id), source_fingerprint),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_source(conn: Any, **fields: Any) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attorney_review_sources
                (matter_id, firm_id, source_fingerprint, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (matter_id, source_fingerprint) DO NOTHING
            RETURNING *
            """,
            (
                str(fields["matter_id"]), str(fields["firm_id"]),
                fields["source_fingerprint"],
                json.dumps(fields["payload"], default=str),
            ),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def snapshots_for(
    conn: Any, *, matter_id: UUID, source_fingerprint: str
) -> list[dict[str, Any]]:
    """The snapshots a run analyses: the LATEST rendered version of
    each package under the fingerprint.

    A re-render under the same fingerprint (e.g. a template fix with
    unchanged answers) records a second snapshot set; analysing every
    version would re-report defects the family already regenerated
    away and would let Layer 1 and Layer 2 read different artifacts
    (review H2). One row per package — its highest package_version.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM ("
            " SELECT DISTINCT ON (package_id) * FROM attorney_review_snapshots"
            " WHERE matter_id = %s AND source_fingerprint = %s"
            " ORDER BY package_id, package_version DESC"
            ") AS latest ORDER BY template_type, package_version",
            (str(matter_id), source_fingerprint),
        )
        return [dict(row) for row in cur.fetchall()]


def insert_run(conn: Any, **fields: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attorney_review_runs
                (matter_id, firm_id, source_fingerprint, status,
                 prompt_version, created_by_user_id)
            VALUES (%s, %s, %s, 'running', %s, %s)
            RETURNING *
            """,
            (
                str(fields["matter_id"]), str(fields["firm_id"]),
                fields["source_fingerprint"], fields["prompt_version"],
                str(fields["created_by_user_id"]) if fields.get("created_by_user_id") else None,
            ),
        )
        return dict(cur.fetchone())


def complete_run(
    conn: Any, run_id: UUID, *, model_id: str | None
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE attorney_review_runs SET status = 'completed',"
            " model_id = %s, completed_at = now() WHERE id = %s RETURNING *",
            (model_id, str(run_id)),
        )
        return dict(cur.fetchone())


def fail_run(conn: Any, run_id: UUID, *, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE attorney_review_runs SET status = 'failed', error = %s,"
            " completed_at = now() WHERE id = %s",
            (error[:1000], str(run_id)),
        )


def latest_completed_run(
    conn: Any, *, matter_id: UUID, source_fingerprint: str | None = None,
    exclude_run_id: UUID | None = None,
) -> dict[str, Any] | None:
    query = (
        "SELECT * FROM attorney_review_runs"
        " WHERE matter_id = %s AND status = 'completed'"
    )
    params: list[Any] = [str(matter_id)]
    if source_fingerprint is not None:
        query += " AND source_fingerprint = %s"
        params.append(source_fingerprint)
    if exclude_run_id is not None:
        query += " AND id != %s"
        params.append(str(exclude_run_id))
    query += " ORDER BY created_at DESC LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row else None


def insert_finding(conn: Any, **fields: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attorney_review_findings
                (run_id, matter_id, package_id, finding_hash, layer,
                 check_name, severity, location, explanation, suggested_fix,
                 disposition, acknowledged_by_user_id, acknowledgment_note,
                 acknowledged_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, finding_hash) DO NOTHING
            RETURNING *
            """,
            (
                str(fields["run_id"]), str(fields["matter_id"]),
                str(fields["package_id"]) if fields.get("package_id") else None,
                fields["finding_hash"], fields["layer"], fields["check_name"],
                fields["severity"],
                json.dumps(fields["location"], default=str),
                fields["explanation"], fields.get("suggested_fix"),
                fields.get("disposition", "open"),
                str(fields["acknowledged_by_user_id"])
                if fields.get("acknowledged_by_user_id") else None,
                fields.get("acknowledgment_note"),
                fields.get("acknowledged_at"),
            ),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


def findings_for_run(conn: Any, run_id: UUID) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM attorney_review_findings WHERE run_id = %s"
            " ORDER BY (severity = 'should_fix') DESC, layer, check_name, created_at",
            (str(run_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_finding(conn: Any, finding_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM attorney_review_findings WHERE id = %s",
            (str(finding_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def set_disposition(conn: Any, finding_id: UUID, disposition: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE attorney_review_findings SET disposition = %s,"
            " updated_at = now() WHERE id = %s",
            (disposition, str(finding_id)),
        )


def acknowledge_finding(
    conn: Any, finding_id: UUID, *, actor_id: UUID, note: str
) -> dict[str, Any] | None:
    """Acknowledge an OPEN finding. The disposition guard makes the
    service's latest-run check atomic with the write: if a concurrent
    re-run already transitioned this finding (stale /
    fixed_by_regeneration / acknowledged), the update matches nothing
    and the caller conflicts instead of flipping it back (review L1).
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE attorney_review_findings SET disposition = 'acknowledged',"
            " acknowledged_by_user_id = %s, acknowledgment_note = %s,"
            " acknowledged_at = now(), updated_at = now()"
            " WHERE id = %s AND disposition = 'open' RETURNING *",
            (str(actor_id), note, str(finding_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None
