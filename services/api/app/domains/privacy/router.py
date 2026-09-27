"""Privacy endpoints.

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.privacy.router import router
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.privacy import service
from app.domains.privacy.schemas import (
    DataExportOut,
    DeletionRequestCreate,
    DeletionRequestOut,
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
    ReminderDispatchOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Notification preferences (own only)
# ---------------------------------------------------------------------------


@router.get("/me/notification-preferences", response_model=NotificationPreferencesOut)
def read_notification_preferences(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> NotificationPreferencesOut:
    """Return the caller's notification preferences (defaults when unset)."""
    with db.transaction() as conn:
        return service.get_notification_preferences(conn, actor_sub=claims.sub)


@router.put("/me/notification-preferences", response_model=NotificationPreferencesOut)
def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> NotificationPreferencesOut:
    """Patch the caller's notification preferences (upsert)."""
    with db.transaction() as conn:
        return service.update_notification_preferences(conn, actor_sub=claims.sub, payload=payload)


@router.post("/me/unsubscribe", response_model=NotificationPreferencesOut)
def unsubscribe_me(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> NotificationPreferencesOut:
    """Record the caller's unsubscribe from notifications (idempotent)."""
    with db.transaction() as conn:
        return service.unsubscribe(conn, actor_sub=claims.sub)


# ---------------------------------------------------------------------------
# Data export (own data only)
# ---------------------------------------------------------------------------


@router.get("/me/export", response_model=DataExportOut)
def export_my_data(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> DataExportOut:
    """Export the caller's own data as JSON (read-only assembly)."""
    with db.transaction() as conn:
        return service.export_my_data(conn, actor_sub=claims.sub)


# ---------------------------------------------------------------------------
# Deletion requests (own only)
# ---------------------------------------------------------------------------


@router.post(
    "/me/deletion-requests",
    status_code=status.HTTP_201_CREATED,
    response_model=DeletionRequestOut,
)
def create_deletion_request(
    payload: DeletionRequestCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> DeletionRequestOut:
    """File a data deletion request for the caller (audited)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_deletion_request(
            conn,
            actor_sub=claims.sub,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.get("/me/deletion-requests", response_model=list[DeletionRequestOut])
def list_my_deletion_requests(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[DeletionRequestOut]:
    """List the caller's own deletion requests, newest first."""
    with db.transaction() as conn:
        return service.list_deletion_requests(conn, actor_sub=claims.sub)


# ---------------------------------------------------------------------------
# Admin: reminder dispatch
# ---------------------------------------------------------------------------


@router.post(
    "/admin/reminders/dispatch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReminderDispatchOut,
)
def dispatch_reminders(
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> ReminderDispatchOut:
    """Enqueue one ``send_due_reminders`` worker run (firm_admin only).

    Deployment schedule: the worker's ``send_due_reminders`` actor is meant
    to run every 15 minutes via the deployment cron (e.g.
    ``*/15 * * * *`` invoking the worker's scheduler entrypoint, which
    sends ``send_due_reminders`` on the shared Dramatiq broker). This
    endpoint only enqueues a single on-demand run through the same broker
    — it does not replace the cron schedule.
    """
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.dispatch_due_reminders(
            conn, actor_sub=claims.sub, correlation_id=correlation_id
        )
