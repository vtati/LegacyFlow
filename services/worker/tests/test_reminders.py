"""Tests for the send_due_reminders actor (reminder delivery wiring).

Covers ``worker.actors.dispatch_due_reminders`` (the directly testable
core of the ``send_due_reminders`` dramatiq actor):

  * due reminders are queued to ``notification_outbox`` and marked sent
  * users who disabled reminders or unsubscribed are skipped (rows stay
    unsent so a later re-subscribe still delivers)
  * the rendered subject is generic (FR-FUND-004: no reminder details in
    the subject)
  * a second run is a no-op (idempotent ``sent_at`` update)

Runs against a local pgserver instance with minimal DDL for the
``reminders``, ``notification_preferences``, ``notification_outbox`` and
``audit_events`` tables. All data is synthetic.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

REMINDERS_DDL = """
CREATE TABLE IF NOT EXISTS reminders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL,
  matter_id uuid,
  user_id uuid NOT NULL,
  reminder_type text NOT NULL,
  scheduled_for timestamptz NOT NULL,
  sent_at timestamptz,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notification_preferences (
  user_id uuid PRIMARY KEY,
  email_enabled boolean NOT NULL DEFAULT true,
  reminder_enabled boolean NOT NULL DEFAULT true,
  unsubscribed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
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
CREATE TABLE IF NOT EXISTS audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid,
  matter_id uuid,
  actor_user_id uuid,
  actor_type text NOT NULL,
  action text NOT NULL,
  resource_type text NOT NULL,
  resource_id uuid,
  correlation_id uuid,
  before_summary jsonb,
  after_summary jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
"""


@pytest.fixture(scope="module")
def reminders_dsn() -> Iterator[str]:
    """Throwaway pgserver instance with the reminder tables applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-worker-reminders-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            import psycopg

            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(REMINDERS_DDL)
            yield dsn
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _insert_reminder(
    dsn: str,
    user_id: uuid.UUID,
    *,
    scheduled_for: datetime,
    sent_at: datetime | None = None,
    reminder_type: str = "signing_checklist_due",
) -> uuid.UUID:
    import psycopg

    reminder_id = uuid.uuid4()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reminders
                  (id, firm_id, user_id, reminder_type, scheduled_for, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(reminder_id),
                    str(uuid.uuid4()),
                    str(user_id),
                    reminder_type,
                    scheduled_for.isoformat(),
                    sent_at.isoformat() if sent_at else None,
                ),
            )
    return reminder_id


def _set_preferences(
    dsn: str,
    user_id: uuid.UUID,
    *,
    reminder_enabled: bool = True,
    unsubscribed: bool = False,
) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_preferences
                  (user_id, reminder_enabled, unsubscribed_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                  reminder_enabled = EXCLUDED.reminder_enabled,
                  unsubscribed_at = EXCLUDED.unsubscribed_at
                """,
                (
                    str(user_id),
                    reminder_enabled,
                    datetime.now(UTC).isoformat() if unsubscribed else None,
                ),
            )


def _fetch_reminder(dsn: str, reminder_id: uuid.UUID) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reminders WHERE id = %s", (str(reminder_id),))
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def _outbox_rows(dsn: str) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notification_outbox ORDER BY created_at")
            return [dict(row) for row in cur.fetchall()]


def _audit_actions(dsn: str) -> list[str]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT action FROM audit_events ORDER BY created_at")
            return [row[0] for row in cur.fetchall()]


def _truncate(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE reminders, notification_preferences, notification_outbox, audit_events"
            )


def test_dispatch_queues_due_reminders_and_marks_sent(
    reminders_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", reminders_dsn)
    get_worker_settings.cache_clear()
    try:
        _truncate(reminders_dsn)
        user = uuid.uuid4()
        due_id = _insert_reminder(
            reminders_dsn, user, scheduled_for=datetime.now(UTC) - timedelta(hours=1)
        )
        # Not due yet: untouched. Already sent: untouched.
        future_id = _insert_reminder(
            reminders_dsn, user, scheduled_for=datetime.now(UTC) + timedelta(hours=1)
        )
        sent_id = _insert_reminder(
            reminders_dsn,
            user,
            scheduled_for=datetime.now(UTC) - timedelta(hours=2),
            sent_at=datetime.now(UTC) - timedelta(hours=2),
        )

        ledger = actors.dispatch_due_reminders(batch_size=100)

        assert ledger == {"processed": 1, "queued": 1, "skipped": 0}
        rows = _outbox_rows(reminders_dsn)
        assert len(rows) == 1
        assert rows[0]["template_key"] == "reminder.due"
        assert rows[0]["status"] == "pending"
        assert str(rows[0]["recipient_user_id"]) == str(user)
        payload = rows[0]["payload"]
        assert payload["reminder_id"] == str(due_id)

        assert _fetch_reminder(reminders_dsn, due_id)["sent_at"] is not None
        assert _fetch_reminder(reminders_dsn, future_id)["sent_at"] is None
        assert _audit_actions(reminders_dsn) == ["reminder.sent"]
        # Already-sent row keeps its original timestamp.
        assert _fetch_reminder(reminders_dsn, sent_id)["sent_at"] is not None
    finally:
        get_worker_settings.cache_clear()


def test_dispatch_skips_opted_out_users(
    reminders_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", reminders_dsn)
    get_worker_settings.cache_clear()
    try:
        _truncate(reminders_dsn)
        past = datetime.now(UTC) - timedelta(minutes=30)
        disabled_user = uuid.uuid4()
        unsubscribed_user = uuid.uuid4()
        _set_preferences(reminders_dsn, disabled_user, reminder_enabled=False)
        _set_preferences(reminders_dsn, unsubscribed_user, unsubscribed=True)
        disabled_id = _insert_reminder(reminders_dsn, disabled_user, scheduled_for=past)
        unsub_id = _insert_reminder(reminders_dsn, unsubscribed_user, scheduled_for=past)

        ledger = actors.dispatch_due_reminders(batch_size=100)

        assert ledger == {"processed": 0, "queued": 0, "skipped": 2}
        assert _outbox_rows(reminders_dsn) == []
        # Skipped rows stay unsent so a later re-subscribe still delivers.
        assert _fetch_reminder(reminders_dsn, disabled_id)["sent_at"] is None
        assert _fetch_reminder(reminders_dsn, unsub_id)["sent_at"] is None
        assert _audit_actions(reminders_dsn) == []
    finally:
        get_worker_settings.cache_clear()


def test_dispatch_second_run_is_noop(reminders_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", reminders_dsn)
    get_worker_settings.cache_clear()
    try:
        _truncate(reminders_dsn)
        _insert_reminder(
            reminders_dsn,
            uuid.uuid4(),
            scheduled_for=datetime.now(UTC) - timedelta(minutes=5),
        )

        first = actors.dispatch_due_reminders(batch_size=100)
        second = actors.dispatch_due_reminders(batch_size=100)

        assert first["queued"] == 1
        assert second == {"processed": 0, "queued": 0, "skipped": 0}
        # Still exactly one outbox row: the idempotent sent_at update
        # prevents double-enqueueing.
        assert len(_outbox_rows(reminders_dsn)) == 1
    finally:
        get_worker_settings.cache_clear()


def test_reminder_subject_is_generic(reminders_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-FUND-004: reminder type/details never appear in the subject."""
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", reminders_dsn)
    get_worker_settings.cache_clear()
    try:
        _truncate(reminders_dsn)
        _insert_reminder(
            reminders_dsn,
            uuid.uuid4(),
            scheduled_for=datetime.now(UTC) - timedelta(minutes=5),
            reminder_type="funding_task_overdue",
        )
        actors.dispatch_due_reminders(batch_size=100)

        rows = _outbox_rows(reminders_dsn)
        assert len(rows) == 1
        rendered = actors._render_notification(rows[0])
        assert rendered["subject"] == "A reminder from LegacyFlow"
        assert "funding_task_overdue" not in rendered["subject"]
    finally:
        get_worker_settings.cache_clear()


def test_dispatch_without_database_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_worker_settings.cache_clear()
    try:
        assert actors.dispatch_due_reminders() == {
            "processed": 0,
            "queued": 0,
            "skipped": 0,
        }
    finally:
        get_worker_settings.cache_clear()


def test_send_due_reminders_actor_is_registered() -> None:
    from worker import actors

    assert actors.send_due_reminders.actor_name == "send_due_reminders"


def test_dispatch_failed_enqueue_counts_honestly(
    reminders_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FUNC-017 (worker copy): processed counts rows examined, queued
    counts only successful enqueues — one failing enqueue yields
    processed > queued and leaves that reminder unsent."""
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", reminders_dsn)
    get_worker_settings.cache_clear()
    try:
        _truncate(reminders_dsn)
        user = uuid.uuid4()
        good_id = _insert_reminder(
            reminders_dsn, user, scheduled_for=datetime.now(UTC) - timedelta(hours=1)
        )
        bad_id = _insert_reminder(
            reminders_dsn, user, scheduled_for=datetime.now(UTC) - timedelta(hours=1)
        )

        real_enqueue = actors._enqueue_reminder_notification

        def flaky_enqueue(cur: Any, reminder: dict[str, Any]) -> None:
            if str(reminder["id"]) == str(bad_id):
                raise RuntimeError("outbox unavailable (synthetic)")
            return real_enqueue(cur, reminder)

        monkeypatch.setattr(actors, "_enqueue_reminder_notification", flaky_enqueue)

        ledger = actors.dispatch_due_reminders(batch_size=100)

        assert ledger == {"processed": 2, "queued": 1, "skipped": 0}
        assert _fetch_reminder(reminders_dsn, good_id)["sent_at"] is not None
        assert _fetch_reminder(reminders_dsn, bad_id)["sent_at"] is None
        assert len(_outbox_rows(reminders_dsn)) == 1
    finally:
        get_worker_settings.cache_clear()
