"""Tests for the send_notifications actor (Slice 3).

Runs against a local pgserver instance. Renders go to a temp sandbox
directory — real email is never sent.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS notification_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL,
  matter_id uuid,
  recipient_user_id uuid,
  recipient_email text,
  template_key text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent', 'failed')),
  attempts integer NOT NULL DEFAULT 0,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
"""


@pytest.fixture(scope="module")
def outbox_dsn() -> Iterator[str]:
    """Throwaway pgserver instance with the outbox table applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-worker-notify-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            import psycopg

            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(OUTBOX_DDL)
            yield dsn
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _enqueue(dsn: str, **overrides: Any) -> uuid.UUID:
    import psycopg

    row_id = uuid.uuid4()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_outbox
                  (id, firm_id, recipient_email, template_key, payload, status, attempts)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    str(row_id),
                    str(overrides.get("firm_id", uuid.uuid4())),
                    overrides.get("recipient_email", "synthetic@example.com"),
                    overrides.get("template_key", "gate.decided"),
                    json.dumps(overrides.get("payload", {"decision": "approved"})),
                    overrides.get("status", "pending"),
                    overrides.get("attempts", 0),
                ),
            )
    return row_id


def _fetch(dsn: str, row_id: uuid.UUID) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notification_outbox WHERE id = %s", (str(row_id),))
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def test_send_notifications_drains_to_sandbox(
    outbox_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    sandbox = tmp_path / "mail"
    monkeypatch.setenv("DATABASE_URL", outbox_dsn)
    monkeypatch.setenv("NOTIFICATION_SANDBOX_DIR", str(sandbox))
    get_worker_settings.cache_clear()
    try:
        pending_id = _enqueue(outbox_dsn)
        already_sent = _enqueue(outbox_dsn, status="sent")

        ledger = actors.drain_outbox(batch_size=25)

        assert ledger == {"sent": 1, "failed": 0}
        rendered = json.loads((sandbox / f"{pending_id}.json").read_text(encoding="utf-8"))
        assert rendered["to"] == "synthetic@example.com"
        assert rendered["template_key"] == "gate.decided"
        assert rendered["payload"] == {"decision": "approved"}
        assert rendered["subject"]

        row = _fetch(outbox_dsn, pending_id)
        assert row["status"] == "sent"
        assert row["sent_at"] is not None
        assert int(row["attempts"]) == 1
        # Already-sent rows are untouched.
        assert _fetch(outbox_dsn, already_sent)["status"] == "sent"
    finally:
        get_worker_settings.cache_clear()
        for var in ("DATABASE_URL", "NOTIFICATION_SANDBOX_DIR"):
            os.environ.pop(var, None)


def test_send_notifications_without_database_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_worker_settings.cache_clear()
    try:
        assert actors.drain_outbox() == {"sent": 0, "failed": 0}
    finally:
        get_worker_settings.cache_clear()
