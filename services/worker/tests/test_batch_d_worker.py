"""Batch D worker tests: pooling (PERF-021), savepoint isolation
(ERR-021), reminder failure ledger (ERR-002), actor retry config +
dead letters (ERR-003), scan storage failures (ERR-004), atomic
sandbox writes (ERR-011), JSON logging (ERR-012).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# PERF-021: module-level connection pool
# ---------------------------------------------------------------------------


def test_pool_module_reuses_pool_per_dsn() -> None:
    from worker import db as worker_db

    assert hasattr(worker_db, "get_pool")
    assert hasattr(worker_db, "get_connection")
    assert worker_db.POOL_MIN_SIZE == 1
    assert worker_db.POOL_MAX_SIZE == 5


def test_worker_deps_declare_psycopg_pool() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    requirements = (root / "requirements.txt").read_text()
    assert "pool" in pyproject.split("psycopg")[1].split("]")[0]
    assert "pool" in requirements.split("psycopg")[1].split("]")[0]


# ---------------------------------------------------------------------------
# ERR-021: per-row savepoints — a Postgres-level row failure must not
# roll back rows already marked sent/scanned in the same batch.
# ---------------------------------------------------------------------------

DDL_021 = """
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
CREATE TABLE IF NOT EXISTS file_uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL,
  household_id uuid NOT NULL,
  matter_id uuid,
  uploader_user_id uuid NOT NULL,
  original_filename text NOT NULL,
  stored_path text NOT NULL,
  mime_type text,
  size_bytes bigint,
  scan_status text NOT NULL DEFAULT 'pending'
    CHECK (scan_status IN ('pending', 'clean', 'quarantined')),
  scan_detail text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
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
CREATE TABLE IF NOT EXISTS worker_dead_letters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_name text NOT NULL,
  message_id text,
  args_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_class text,
  retries integer,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION fail_row_update() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'synthetic row failure';
END;
$$ LANGUAGE plpgsql;
"""


@pytest.fixture(scope="module")
def batch_dsn() -> Iterator[str]:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-worker-batchd-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            import psycopg

            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(DDL_021)
            yield dsn
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _use_dsn(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    from worker.db import reset_pool
    from worker.settings import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", dsn)
    get_worker_settings.cache_clear()
    reset_pool()


def _clear_dsn() -> None:
    from worker.db import reset_pool
    from worker.settings import get_worker_settings

    get_worker_settings.cache_clear()
    reset_pool()


def test_drain_outbox_db_error_row_does_not_roll_back_sent_row(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ERR-021: the poison row's UPDATE raises a real Postgres error.
    Without a per-row savepoint the recovery UPDATE dies with
    InFailedSqlTransaction and the good row's sent marking rolls back."""
    import psycopg

    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("NOTIFICATION_SANDBOX_DIR", str(tmp_path / "mail"))
    _use_dsn(monkeypatch, batch_dsn)
    try:
        good_id, poison_id = uuid.uuid4(), uuid.uuid4()
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE notification_outbox")
                cur.execute("DROP TRIGGER IF EXISTS trg_fail ON notification_outbox")
                for rid, mins in ((good_id, 2), (poison_id, 1)):
                    cur.execute(
                        "INSERT INTO notification_outbox"
                        " (id, firm_id, recipient_email, template_key, created_at)"
                        " VALUES (%s, %s, 's@example.com', 'gate.decided', now() - %s::interval)",
                        (str(rid), str(uuid.uuid4()), f"{mins} minutes"),
                    )
                cur.execute(
                    "CREATE TRIGGER trg_fail BEFORE UPDATE ON notification_outbox"
                    f" FOR EACH ROW WHEN (OLD.id = '{poison_id}' AND NEW.status = 'sent')"
                    " EXECUTE FUNCTION fail_row_update()"
                )
        ledger = actors.drain_outbox(batch_size=25)
        assert ledger == {"sent": 1, "failed": 1}
        with psycopg.connect(batch_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, attempts FROM notification_outbox WHERE id=%s", (str(good_id),))
                assert cur.fetchone() == ("sent", 1)
                cur.execute("SELECT status, attempts FROM notification_outbox WHERE id=%s", (str(poison_id),))
                assert cur.fetchone() == ("pending", 1)
    finally:
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TRIGGER IF EXISTS trg_fail ON notification_outbox")
        get_worker_settings.cache_clear()
        _clear_dsn()


def test_scan_pending_db_error_row_does_not_roll_back_scanned_row(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ERR-021 (scan path): same savepoint shape in scan_pending_uploads."""
    import psycopg

    from worker.scanning import scan_pending_uploads

    _use_dsn(monkeypatch, batch_dsn)
    try:
        good_id, poison_id = uuid.uuid4(), uuid.uuid4()
        pdf = b"%PDF-1.4\n%synthetic\n%%EOF"
        for rid in (good_id, poison_id):
            p = tmp_path / f"{rid}.pdf"
            p.write_bytes(pdf)
            with psycopg.connect(batch_dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO file_uploads (id, firm_id, household_id,"
                        " uploader_user_id, original_filename, stored_path, mime_type)"
                        " VALUES (%s, %s, %s, %s, 'x.pdf', %s, 'application/pdf')"
                        " ON CONFLICT (id) DO NOTHING",
                        (str(rid), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(p)),
                    )
                    cur.execute("UPDATE file_uploads SET scan_status='pending' WHERE id=%s", (str(rid),))
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TRIGGER IF EXISTS trg_fail_scan ON file_uploads")
                cur.execute(
                    "CREATE TRIGGER trg_fail_scan BEFORE UPDATE ON file_uploads"
                    f" FOR EACH ROW WHEN (OLD.id = '{poison_id}') EXECUTE FUNCTION fail_row_update()"
                )
        ledger = scan_pending_uploads(batch_size=25)
        assert ledger["scanned"] == 1 and ledger["clean"] == 1
        with psycopg.connect(batch_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT scan_status FROM file_uploads WHERE id=%s", (str(good_id),))
                assert cur.fetchone()[0] == "clean"
                cur.execute("SELECT scan_status FROM file_uploads WHERE id=%s", (str(poison_id),))
                assert cur.fetchone()[0] == "pending"
    finally:
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TRIGGER IF EXISTS trg_fail_scan ON file_uploads")
        _clear_dsn()


# ---------------------------------------------------------------------------
# ERR-002 + PERF-007: dispatch failure ledger/logging; folded count
# ---------------------------------------------------------------------------


def _insert_due_reminder(dsn: str, user_id: uuid.UUID) -> uuid.UUID:
    import psycopg

    rid = uuid.uuid4()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reminders (id, firm_id, user_id, reminder_type, scheduled_for)"
                " VALUES (%s, %s, %s, 'signing_checklist_due', now() - interval '1 hour')",
                (str(rid), str(uuid.uuid4()), str(user_id)),
            )
    return rid


def test_dispatch_failure_logs_exception_and_counts_failed(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ERR-002: a failing reminder produces logger.exception with the
    reminder id and is counted in the returned ledger's ``failed``."""
    import logging

    import psycopg

    from worker import actors

    _use_dsn(monkeypatch, batch_dsn)
    try:
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "TRUNCATE reminders, notification_preferences, notification_outbox, audit_events"
                )
        user = uuid.uuid4()
        good_id = _insert_due_reminder(batch_dsn, user)
        bad_id = _insert_due_reminder(batch_dsn, user)
        real_enqueue = actors._enqueue_reminder_notification

        def flaky(cur: Any, reminder: dict[str, Any]) -> None:
            if str(reminder["id"]) == str(bad_id):
                raise RuntimeError("outbox unavailable (synthetic)")
            return real_enqueue(cur, reminder)

        monkeypatch.setattr(actors, "_enqueue_reminder_notification", flaky)
        with caplog.at_level(logging.ERROR, logger="worker.actors"):
            ledger = actors.dispatch_due_reminders(batch_size=100)
        assert ledger == {"processed": 2, "queued": 1, "skipped": 0, "failed": 1}
        failures = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and str(bad_id) in r.getMessage()
        ]
        assert failures, "expected an ERROR log naming the failed reminder id"
        assert failures[0].exc_info is not None, "must be logger.exception (exc_info set)"
        with psycopg.connect(batch_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sent_at FROM reminders WHERE id=%s", (str(good_id),))
                assert cur.fetchone()[0] is not None
                cur.execute("SELECT sent_at FROM reminders WHERE id=%s", (str(bad_id),))
                assert cur.fetchone()[0] is None
    finally:
        _clear_dsn()


def test_dispatch_suppressed_count_folded_into_selection(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PERF-007: when deliverable rows are selected, the suppressed
    count comes from that same query — no separate COUNT round-trip."""
    import psycopg

    from worker import actors

    _use_dsn(monkeypatch, batch_dsn)
    try:
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "TRUNCATE reminders, notification_preferences, notification_outbox, audit_events"
                )
        _insert_due_reminder(batch_dsn, uuid.uuid4())
        opted_out = uuid.uuid4()
        _insert_due_reminder(batch_dsn, opted_out)
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notification_preferences (user_id, reminder_enabled)"
                    " VALUES (%s, false)",
                    (str(opted_out),),
                )

        def _boom(cur: Any) -> int:
            raise AssertionError("separate suppressed COUNT ran on the hot path")

        monkeypatch.setattr(actors, "_count_suppressed_reminders", _boom)
        ledger = actors.dispatch_due_reminders(batch_size=100)
        assert ledger["queued"] == 1
        assert ledger["skipped"] == 1
    finally:
        _clear_dsn()


# ---------------------------------------------------------------------------
# ERR-003: explicit actor retry config, broker config, dead letters
# ---------------------------------------------------------------------------


def test_actor_retry_options_are_explicit() -> None:
    from worker import actors

    for actor in (actors.send_notifications, actors.send_due_reminders, actors.scan_file):
        assert actor.options.get("on_retry_exhausted") == "record_dead_letter", actor.actor_name
        assert actor.options.get("max_retries") is not None, actor.actor_name
        assert actor.options.get("min_backoff") is not None, actor.actor_name
        assert actor.options.get("max_backoff") is not None, actor.actor_name
        assert actor.options.get("max_age") is not None, actor.actor_name
    # Scans tolerate more retries than dispatch/notification drains.
    assert actors.scan_file.options["max_retries"] > actors.send_due_reminders.options["max_retries"]
    # Reminders run every 15 minutes: a message older than that is stale.
    assert actors.send_due_reminders.options["max_age"] == 15 * 60 * 1000
    # The dead-letter sink itself never retries (no exhaustion loop).
    assert actors.record_dead_letter.options.get("max_retries") == 0


def test_broker_retries_middleware_is_explicit() -> None:
    from dramatiq.middleware import Retries

    from worker.broker import get_broker

    broker = get_broker()
    retries = [m for m in broker.middleware if isinstance(m, Retries)]
    assert len(retries) == 1
    assert retries[0].max_retries == 3


def test_dead_letter_handler_writes_row_without_pii(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import psycopg

    from worker.dead_letter import write_dead_letter

    _use_dsn(monkeypatch, batch_dsn)
    try:
        with psycopg.connect(batch_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE worker_dead_letters")
        upload_id = str(uuid.uuid4())
        message = {
            "actor_name": "scan_file",
            "message_id": "msg-123",
            "args": [upload_id, "Jane Q Public"],
            "kwargs": {"note": "sensitive free text"},
            "options": {
                "traceback": "Traceback...\nValueError: storage exploded",
            },
        }
        with caplog.at_level(logging.ERROR, logger="worker.dead_letter"):
            assert write_dead_letter(message, {"retries": 5, "max_retries": 5}) is True
        with psycopg.connect(batch_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT actor_name, message_id, args_summary, error_class, retries"
                    " FROM worker_dead_letters"
                )
                row = cur.fetchone()
        assert row[0] == "scan_file"
        assert row[1] == "msg-123"
        assert row[3] == "ValueError"
        assert row[4] == 5
        import json as _json

        summary = row[2] if isinstance(row[2], dict) else _json.loads(row[2])
        summary_text = _json.dumps(summary)
        assert upload_id in summary_text  # ids are allowed
        assert "Jane Q Public" not in summary_text  # PII is not
        assert "sensitive free text" not in summary_text
        assert any(r.levelno >= logging.ERROR for r in caplog.records)
    finally:
        _clear_dsn()


def test_migrations_dead_letters_and_partial_indexes() -> None:
    root = Path(__file__).resolve().parents[3]
    dead = (root / "supabase/migrations/20261029143000_worker_dead_letters.sql").read_text()
    assert "create table worker_dead_letters" in dead
    assert "alter table worker_dead_letters enable row level security" in dead
    idx = (root / "supabase/migrations/20261029150000_worker_partial_indexes.sql").read_text()
    assert "on file_uploads (created_at)" in idx and "scan_status = 'pending'" in idx
    assert "on notification_outbox (created_at)" in idx and "status = 'pending'" in idx
    assert "on reminders (scheduled_for)" in idx and "sent_at is null" in idx.lower()


# ---------------------------------------------------------------------------
# ERR-004: non-404 storage failures must not strand rows in 'pending'
# ---------------------------------------------------------------------------


def test_perform_file_scan_storage_error_is_terminal(
    batch_dsn: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import types
    import urllib.error
    import urllib.request

    import psycopg
    from psycopg.rows import dict_row

    import worker.scanning as scanning
    from worker.scanning import perform_file_scan

    fake_settings = types.SimpleNamespace(
        STORAGE_BACKEND="supabase",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="svc-key",
        STORAGE_DIR="/tmp/legacyflow-uploads",
    )
    monkeypatch.setattr(scanning, "get_worker_settings", lambda: fake_settings)

    def _boom(req: object, timeout: int = 0) -> Any:
        raise urllib.error.URLError("storage unreachable (synthetic)")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    upload_id = uuid.uuid4()
    with psycopg.connect(batch_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO file_uploads (id, firm_id, household_id, uploader_user_id,"
                " original_filename, stored_path, mime_type)"
                " VALUES (%s, %s, %s, %s, 'x.pdf', 'client-uploads/docs/x.pdf',"
                " 'application/pdf') ON CONFLICT (id) DO NOTHING",
                (str(upload_id), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())),
            )
            cur.execute(
                "UPDATE file_uploads SET scan_status='pending', scan_detail=NULL WHERE id=%s",
                (str(upload_id),),
            )
    with psycopg.connect(batch_dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            with caplog.at_level(logging.ERROR, logger="worker.scanning"):
                outcome = perform_file_scan(conn, upload_id)
    assert outcome == {"scanned": 1, "status": "quarantined"}
    with psycopg.connect(batch_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scan_status, scan_detail FROM file_uploads WHERE id=%s",
                (str(upload_id),),
            )
            status, detail = cur.fetchone()
    assert status == "quarantined"  # terminal, fail-closed — not 'pending'
    assert detail and "URLError" in detail
    assert any(
        r.levelno >= logging.ERROR and r.exc_info for r in caplog.records
    ), "storage failure must be exception-logged"


# ---------------------------------------------------------------------------
# ERR-011: sandbox writes are atomic (temp file + os.replace)
# ---------------------------------------------------------------------------


def test_write_to_sandbox_is_atomic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from worker import actors
    from worker.settings import get_worker_settings

    monkeypatch.setenv("NOTIFICATION_SANDBOX_DIR", str(tmp_path))
    get_worker_settings.cache_clear()
    try:
        def _forbidden(self: Path, *a: Any, **k: Any) -> Any:
            raise AssertionError("direct write_text on the target is not atomic")

        monkeypatch.setattr(Path, "write_text", _forbidden)
        outbox_id = uuid.uuid4()
        actors._write_to_sandbox({"hello": "world"}, outbox_id)
        target = tmp_path / f"{outbox_id}.json"
        assert target.is_file()
        import json as _json

        assert _json.loads(target.read_text()) == {"hello": "world"}
        leftovers = [p for p in tmp_path.iterdir() if p != target]
        assert leftovers == [], f"temp files left behind: {leftovers}"
    finally:
        get_worker_settings.cache_clear()


# ---------------------------------------------------------------------------
# ERR-012: worker logs are JSON, same shape as the API's
# ---------------------------------------------------------------------------


def test_worker_json_formatter_matches_api_shape() -> None:
    import json as _json
    import logging

    from worker.logging import JSONFormatter

    record = logging.LogRecord(
        name="worker.actors", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="dead letter %s", args=("m1",), exc_info=None,
    )
    record.message_id = "m1"  # an extra={} field, as dramatiq code logs
    payload = _json.loads(JSONFormatter().format(record))
    assert payload["level"] == "ERROR"
    assert payload["message"] == "dead letter m1"
    assert payload["logger"] == "worker.actors"
    assert payload["service"] == "legacyflow-worker"
    assert payload["message_id"] == "m1"
    assert "timestamp" in payload
