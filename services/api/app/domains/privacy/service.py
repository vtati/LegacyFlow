"""Application services for the privacy domain."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import (
    AppError,
    AuthError,
    AuthorizationError,
    ValidationAppError,
)
from app.domains.privacy import repository as repo
from app.domains.privacy.dispatch import (
    WorkerBrokerUnavailableError,
    enqueue_reminder_dispatch,
)
from app.domains.privacy.schemas import (
    DataExportOut,
    DeletionRequestCreate,
    DeletionRequestOut,
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
    ReminderDispatchOut,
)


def _user_id_or_raise(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _preferences_out(row: dict[str, Any]) -> NotificationPreferencesOut:
    return NotificationPreferencesOut.model_validate(row)


def _default_preferences(user_id: UUID) -> NotificationPreferencesOut:
    now = datetime.now(UTC)
    return NotificationPreferencesOut(
        user_id=user_id,
        email_enabled=True,
        reminder_enabled=True,
        unsubscribed_at=None,
        created_at=now,
        updated_at=now,
    )


def get_notification_preferences(conn: Any, *, actor_sub: str) -> NotificationPreferencesOut:
    """Return the caller's preferences (defaults when never set)."""
    user_id = _user_id_or_raise(actor_sub)
    row = repo.get_preferences(conn, user_id)
    if row is None:
        return _default_preferences(user_id)
    return _preferences_out(row)


def update_notification_preferences(
    conn: Any, *, actor_sub: str, payload: NotificationPreferencesUpdate
) -> NotificationPreferencesOut:
    """Patch the caller's preferences (upsert)."""
    if payload.email_enabled is None and payload.reminder_enabled is None:
        raise ValidationAppError("At least one preference field must be provided.")
    user_id = _user_id_or_raise(actor_sub)
    row = repo.upsert_preferences(
        conn,
        user_id,
        email_enabled=payload.email_enabled,
        reminder_enabled=payload.reminder_enabled,
    )
    return _preferences_out(row)


def unsubscribe(conn: Any, *, actor_sub: str) -> NotificationPreferencesOut:
    """Record the caller's unsubscribe; idempotent."""
    user_id = _user_id_or_raise(actor_sub)
    return _preferences_out(repo.set_unsubscribed(conn, user_id))


def export_my_data(conn: Any, *, actor_sub: str) -> DataExportOut:
    """Assemble the caller's own data export (read-only)."""
    user_id = _user_id_or_raise(actor_sub)
    data = repo.export_user_data(conn, user_id)
    return DataExportOut(
        exported_at=datetime.now(UTC),
        truncated=data["truncated"],
        profile=data["profile"],
        households=data["households"],
        matters=data["matters"],
        persons=data["persons"],
        decisions=data["decisions"],
        assets=data["assets"],
    )


def create_deletion_request(
    conn: Any,
    *,
    actor_sub: str,
    payload: DeletionRequestCreate,
    correlation_id: UUID | None = None,
) -> DeletionRequestOut:
    """File an ``open`` deletion request for the caller (audited)."""
    user_id = _user_id_or_raise(actor_sub)
    try:
        firm_id: UUID | None = authz.get_user_firm_id(conn, user_id)
    except AuthorizationError:
        # Pure household clients may hold no firm membership; fall back to
        # the firm of an active household membership so the request still
        # lands in that firm's triage queue (firm_id stays nullable only
        # when the caller belongs to no firm at all).
        firm_id = repo.firm_id_via_household(conn, user_id)
    row = repo.create_deletion_request(conn, user_id=user_id, firm_id=firm_id, notes=payload.notes)
    record_audit_event(
        conn,
        action="privacy.deletion_requested",
        resource_type="data_deletion_requests",
        actor_user_id=user_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
        after_summary={"status": "open"},
    )
    return DeletionRequestOut.model_validate(row)


def list_deletion_requests(conn: Any, *, actor_sub: str) -> list[DeletionRequestOut]:
    """List the caller's own deletion requests, newest first."""
    user_id = _user_id_or_raise(actor_sub)
    return [
        DeletionRequestOut.model_validate(row) for row in repo.list_deletion_requests(conn, user_id)
    ]


def dispatch_due_reminders(
    conn: Any,
    *,
    actor_sub: str,
    correlation_id: UUID | None = None,
) -> ReminderDispatchOut:
    """Enqueue one ``send_due_reminders`` worker run (firm_admin only).

    Deployment schedule: the worker's ``send_due_reminders`` actor runs
    every 15 minutes via the deployment cron; this endpoint only enqueues a
    single on-demand run through the shared Dramatiq broker.
    """
    user_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, user_id)
    authz.require_firm_role(conn, user_id, firm_id, {"firm_admin"})
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise WorkerBrokerUnavailableError()
    try:
        enqueue_reminder_dispatch(redis_url)
    except WorkerBrokerUnavailableError:
        raise
    except Exception as exc:
        # ERR-007 belt-and-braces: the dispatch helper already translates
        # broker failures; any other enqueue failure still maps to the
        # documented 501, never a raw 500. REDIS_URL is set at this
        # point, so the detail is the queue-unavailable wording
        # (Batch-D M-6), not the not-configured one.
        raise WorkerBrokerUnavailableError.queue_unavailable() from exc
    record_audit_event(
        conn,
        action="reminders.dispatch_requested",
        resource_type="reminders",
        actor_user_id=user_id,
        actor_type="user",
        firm_id=firm_id,
        correlation_id=correlation_id,
        after_summary={"actor": "send_due_reminders"},
    )
    return ReminderDispatchOut(
        dispatched=True,
        actor="send_due_reminders",
        schedule="every 15 minutes",
    )
