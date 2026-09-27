"""Attorney-review application services (Slice 3).

Covers review packets (point-in-time matter snapshots for attorney
review), matter comments, clarification requests, approval gates, content
pack registration, and notification-outbox writes.

Authorization summary:
  * Review packets (generate/list/get): firm staff only — packets are the
    firm's attorney work product.
  * Comments: firm staff or any active household member (auditors excluded).
  * Clarifications: created by attorney/firm_admin (matter must be in
    ATTORNEY_REVIEW); answered by the household side.
  * Gates: created by firm staff; decided by attorney/firm_admin.
  * Pack import: firm_admin only. Pack listing: any caller with a firm or
    household membership (packs are a global legal-content catalog).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg.errors
import yaml
from fastapi import UploadFile
from pydantic import BaseModel

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.decisions import repository as decisions_repo
from app.domains.escalation import repository as escalation_repo
from app.domains.household import repository as household_repo
from app.domains.intake import repository as intake_repo
from app.domains.matters import repository as matters_repo
from app.domains.matters import service as matters_service
from app.domains.matters.schemas import MatterTransition
from app.domains.matters.state_machine import MatterStatus, validate_transition
from app.domains.nominations import repository as nominations_repo
from app.domains.review import repository as repo
from app.domains.review.schemas import (
    ClarificationOut,
    CommentOut,
    GateCreate,
    GateDecide,
    GateOut,
    PackImportResponse,
    PackOut,
    PackTransitionOut,
    ReviewPacketDetailOut,
    ReviewPacketOut,
)

# Notification template keys (worker dispatches on these).
TEMPLATE_MATTER_SUBMITTED = "matter.submitted"
TEMPLATE_CLARIFICATION_CREATED = "clarification.created"
TEMPLATE_CLARIFICATION_RESPONDED = "clarification.responded"
TEMPLATE_GATE_DECIDED = "gate.decided"
TEMPLATE_INVITATION_CREATED = "invitation.created"

# The matter state a gate may be created/decided in, and where an approval lands.
GATE_FROM_STATE: dict[str, str] = {
    "intake_approval": MatterStatus.ATTORNEY_REVIEW.value,
    "document_release": MatterStatus.APPROVED_FOR_ASSEMBLY.value,
}
GATE_TO_STATE: dict[str, str] = {
    "intake_approval": MatterStatus.APPROVED_FOR_ASSEMBLY.value,
    "document_release": MatterStatus.DOCUMENTS_RELEASED.value,
}

# Pack manifest fields required for registration.
PACK_STATUSES = frozenset({"draft", "internal_testing", "attorney_review", "approved", "retired"})


def _out[M: BaseModel](row: dict[str, Any], model: type[M]) -> M:
    return model.model_validate(row)


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


def notify_recipients(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID | None,
    template_key: str,
    payload: dict[str, Any],
    recipients: list[dict[str, Any]],
) -> int:
    """Enqueue one outbox row per recipient; return the row count.

    Recipients carry ``user_id`` and ``email`` keys. Duplicates by user id
    are collapsed to a single row.
    """
    seen: set[str] = set()
    count = 0
    for recipient in recipients:
        user_id = UUID(str(recipient["user_id"])) if recipient.get("user_id") else None
        dedupe_key = str(user_id) if user_id else f"email:{recipient.get('email')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        repo.enqueue_notification(
            conn,
            firm_id=firm_id,
            matter_id=matter_id,
            template_key=template_key,
            payload=payload,
            recipient_user_id=user_id,
            recipient_email=str(recipient["email"]) if recipient.get("email") else None,
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Review packets
# ---------------------------------------------------------------------------


def _build_snapshot(conn: Any, matter: dict[str, Any]) -> dict[str, Any]:
    """Assemble the point-in-time review snapshot for a matter.

    No assets/liabilities tables exist in the Slice-2 schema, so that
    section is omitted rather than invented.
    """
    matter_id = UUID(str(matter["id"]))
    household_id = UUID(str(matter["household_id"]))
    jurisdiction_snapshot = matter.get("jurisdiction_snapshot") or {}

    def _nomination_person_name(nomination: dict[str, Any]) -> str | None:
        person_id = nomination.get("person_id")
        if person_id is None:
            return None
        person = nominations_repo.get_person(conn, UUID(str(person_id)))
        return str(person["full_name"]) if person else None

    return {
        "matter": {
            "id": str(matter["id"]),
            "title": str(matter.get("title") or ""),
            "status": str(matter["status"]),
            "plan_type": str(matter["plan_type"]),
            "jurisdiction_code": jurisdiction_snapshot.get("jurisdiction_code"),
            "pack_version": jurisdiction_snapshot.get("semantic_version"),
            "completeness_pct": matter.get("completeness_pct"),
            "assigned_attorney_id": (
                str(matter["assigned_attorney_id"]) if matter.get("assigned_attorney_id") else None
            ),
        },
        "intake_answers": {
            str(row["question_key"]): row["answer"]
            for row in intake_repo.list_answers(conn, matter_id)
        },
        "persons": [
            {
                "id": str(row["id"]),
                "full_name": str(row["full_name"]),
                "kind": row.get("kind"),
            }
            for row in household_repo.list_persons_for_household(conn, household_id)
        ],
        "relationships": [
            {
                "from_person_id": str(row["from_person_id"]),
                "to_person_id": str(row["to_person_id"]),
                "relationship_type": str(row["relationship_type"]),
            }
            for row in household_repo.list_relationships_for_household(conn, household_id)
        ],
        "nominations": [
            {
                "nomination_type": str(row["nomination_type"]),
                "priority": row.get("priority"),
                "status": str(row["status"]),
                "person_name": _nomination_person_name(row),
            }
            for row in nominations_repo.list_nominations(conn, matter_id)
        ],
        "decisions": [
            {"decision_key": str(row["decision_key"]), "value": row["value"]}
            for row in decisions_repo.list_decisions(conn, matter_id)
        ],
        "escalations": [
            {
                "rule_id": str(row["rule_id"]),
                "severity": str(row["severity"]),
                "outcome": row.get("outcome"),
                "status": str(row["status"]),
            }
            for row in escalation_repo.list_escalations(conn, matter_id)
        ],
    }


def generate_packet(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    notes: str | None = None,
) -> ReviewPacketDetailOut:
    """Generate a new review packet snapshot (firm staff only)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    matter_id = UUID(str(matter["id"]))

    snapshot = _build_snapshot(conn, matter)
    if notes:
        snapshot["notes"] = notes
    version = repo.next_packet_version(conn, matter_id)
    row = repo.insert_packet(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        version=version,
        snapshot=snapshot,
        created_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="review.packets.generated",
        resource_type="review_packet",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        after_summary={"version": version},
    )
    return _out(row, ReviewPacketDetailOut)


def list_packets(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[ReviewPacketOut]:
    """List a matter's review packets, newest version first (firm staff only)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter_id = UUID(str(scope.matter["id"]))
    return [_out(row, ReviewPacketOut) for row in repo.list_packets(conn, matter_id)]


def get_packet(
    conn: Any, *, actor_sub: str, matter_id: UUID, packet_id: UUID
) -> ReviewPacketDetailOut:
    """Return one review packet snapshot (firm staff only)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter_id = UUID(str(scope.matter["id"]))
    row = repo.get_packet(conn, packet_id)
    if row is None or UUID(str(row["matter_id"])) != matter_id:
        # Existence-oracle: a packet from another matter reads as not found.
        raise NotFoundError("Review packet not found.")
    return _out(row, ReviewPacketDetailOut)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def create_comment(conn: Any, *, actor_sub: str, matter_id: UUID, body: str) -> CommentOut:
    """Post a matter comment (firm staff or any active household member)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter = scope.matter
    row = repo.insert_comment(
        conn,
        firm_id=UUID(str(matter["firm_id"])),
        matter_id=UUID(str(matter["id"])),
        author_user_id=actor_id,
        body=body,
    )
    record_audit_event(
        conn,
        action="review.comments.created",
        resource_type="comment",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(matter["firm_id"])),
        matter_id=UUID(str(matter["id"])),
        resource_id=UUID(str(row["id"])),
    )
    return _out(row, CommentOut)


def list_comments(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[CommentOut]:
    """List a matter's comments in chronological order (read scope)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter_id = UUID(str(scope.matter["id"]))
    return [_out(row, CommentOut) for row in repo.list_comments(conn, matter_id)]


# ---------------------------------------------------------------------------
# Clarification requests
# ---------------------------------------------------------------------------


def create_clarification(
    conn: Any, *, actor_sub: str, matter_id: UUID, question: str
) -> ClarificationOut:
    """Ask the household a clarification question (attorney/firm_admin).

    Moves the matter ATTORNEY_REVIEW -> CLARIFICATION_REQUESTED and queues
    a ``clarification.created`` notification for the household's primary
    client(s) and collaborators.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.ATTORNEY_ROLES)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    matter_id = UUID(str(matter["id"]))
    household_id = UUID(str(matter["household_id"]))
    from_status = str(matter["status"])
    if from_status != MatterStatus.ATTORNEY_REVIEW.value:
        raise ConflictError(
            "Clarifications can only be created while the matter is in ATTORNEY_REVIEW."
        )

    row = repo.insert_clarification(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        question=question,
        created_by_user_id=actor_id,
    )
    to_status = MatterStatus.CLARIFICATION_REQUESTED.value
    validate_transition(from_status, to_status)
    matters_repo.update_matter_status(
        conn,
        matter_id,
        to_status=to_status,
        completeness_pct=matters_service.completeness_for(to_status),
        submitted_at=None,
    )
    matters_repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=from_status,
        to_status=to_status,
        reason_code="clarification_requested",
        actor_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="review.clarifications.created",
        resource_type="clarification",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        before_summary={"status": from_status},
        after_summary={"status": to_status},
    )
    recipients = repo.household_member_recipients(
        conn, household_id, {"primary_client", "collaborator"}
    )
    notify_recipients(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        template_key=TEMPLATE_CLARIFICATION_CREATED,
        payload={"clarification_id": str(row["id"]), "question": question},
        recipients=recipients,
    )
    return _out(row, ClarificationOut)


def respond_clarification(
    conn: Any, *, actor_sub: str, clarification_id: UUID, response: str
) -> ClarificationOut:
    """Answer an open clarification (household side).

    Moves the matter CLARIFICATION_REQUESTED -> ATTORNEY_REVIEW and queues
    a ``clarification.responded`` notification for the asking attorney.
    """
    actor_id = matters_service.caller_id(actor_sub)
    row = repo.get_clarification(conn, clarification_id)
    if row is None:
        raise NotFoundError("Clarification not found.")
    if str(row["status"]) != "open":
        raise ConflictError("This clarification has already been answered.")
    matter_id = UUID(str(row["matter_id"]))
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    from_status = str(matter["status"])
    if from_status != MatterStatus.CLARIFICATION_REQUESTED.value:
        raise ConflictError(
            "A clarification can only be answered while its matter is in CLARIFICATION_REQUESTED."
        )

    answered = repo.answer_clarification(
        conn, clarification_id, response=response, answered_by_user_id=actor_id
    )
    if answered is None:
        raise ConflictError("This clarification has already been answered.")
    to_status = MatterStatus.ATTORNEY_REVIEW.value
    validate_transition(from_status, to_status)
    matters_repo.update_matter_status(
        conn,
        matter_id,
        to_status=to_status,
        completeness_pct=matters_service.completeness_for(to_status),
        submitted_at=None,
    )
    matters_repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=from_status,
        to_status=to_status,
        reason_code="clarification_answered",
        actor_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="review.clarifications.answered",
        resource_type="clarification",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=clarification_id,
        before_summary={"status": from_status},
        after_summary={"status": to_status},
    )
    recipients = repo.firm_staff_recipients(conn, firm_id)
    asker_id = row.get("created_by_user_id")
    if asker_id and not any(
        str(recipient.get("user_id")) == str(asker_id) for recipient in recipients
    ):
        recipients.append({"user_id": asker_id, "email": None})
    notify_recipients(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        template_key=TEMPLATE_CLARIFICATION_RESPONDED,
        payload={"clarification_id": str(clarification_id)},
        recipients=recipients,
    )
    return _out(answered, ClarificationOut)


def list_clarifications(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[ClarificationOut]:
    """List a matter's clarification requests, oldest first (read scope)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter_id = UUID(str(scope.matter["id"]))
    return [_out(row, ClarificationOut) for row in repo.list_clarifications(conn, matter_id)]


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


def create_gate(conn: Any, *, actor_sub: str, matter_id: UUID, payload: GateCreate) -> GateOut:
    """Create a pending approval gate (firm staff only).

    The matter must be in the gate's expected from-state; only one pending
    gate per (matter, gate_type) may exist.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    matter_id = UUID(str(matter["id"]))
    from_status = str(matter["status"])
    if from_status != GATE_FROM_STATE[payload.gate_type]:
        raise ConflictError(
            f"A '{payload.gate_type}' gate requires the matter to be in "
            f"{GATE_FROM_STATE[payload.gate_type]}; it is in {from_status}."
        )
    existing = repo.latest_gate_by_status(conn, matter_id, payload.gate_type, "pending")
    if existing is not None:
        raise ConflictError(f"A pending '{payload.gate_type}' gate already exists for this matter.")
    row = repo.insert_gate(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        gate_type=payload.gate_type,
        notes=payload.notes,
        created_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="review.gates.created",
        resource_type="approval_gate",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        after_summary={"gate_type": payload.gate_type},
    )
    return _out(row, GateOut)


def decide_gate(conn: Any, *, actor_sub: str, gate_id: UUID, payload: GateDecide) -> GateOut:
    """Decide an approval gate (attorney/firm_admin only).

    An approval transitions the matter through the gate's target state
    (intake_approval -> APPROVED_FOR_ASSEMBLY, document_release ->
    DOCUMENTS_RELEASED); a rejection leaves the matter where it is. Both
    queue a ``gate.decided`` notification for the firm staff and the
    household's primary clients.
    """
    actor_id = matters_service.caller_id(actor_sub)
    gate = repo.get_gate(conn, gate_id)
    if gate is None:
        raise NotFoundError("Approval gate not found.")
    if str(gate["status"]) != "pending":
        raise ConflictError("This approval gate has already been decided.")
    matter_id = UUID(str(gate["matter_id"]))
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.ATTORNEY_ROLES)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    household_id = UUID(str(matter["household_id"]))
    gate_type = str(gate["gate_type"])
    from_status = str(matter["status"])
    if from_status != GATE_FROM_STATE[gate_type]:
        raise ConflictError(
            f"Deciding a '{gate_type}' gate requires the matter to be in "
            f"{GATE_FROM_STATE[gate_type]}; it is in {from_status}."
        )

    try:
        decided = repo.decide_gate(
            conn,
            gate_id,
            decision=payload.decision,
            decided_by_user_id=actor_id,
            notes=payload.notes,
        )
    except LookupError as exc:
        # A concurrent decision landed first; the pending guard above
        # already ran, so surface it as a conflict rather than a 500.
        raise ConflictError("This approval gate has already been decided.") from exc
    record_audit_event(
        conn,
        action="review.gates.decided",
        resource_type="approval_gate",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=gate_id,
        after_summary={"decision": payload.decision, "gate_type": gate_type},
    )

    if payload.decision == "approved":
        # The gate is now recorded as approved, so the shared transition
        # path (which enforces the gate requirement) proceeds.
        matters_service.transition_matter(
            conn,
            actor_sub=actor_sub,
            matter_id=matter_id,
            payload=MatterTransition(
                to_status=GATE_TO_STATE[gate_type], reason=f"gate_{gate_type}_approved"
            ),
        )

    recipients = repo.firm_staff_recipients(conn, firm_id)
    recipients += repo.household_member_recipients(conn, household_id, {"primary_client"})
    notify_recipients(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        template_key=TEMPLATE_GATE_DECIDED,
        payload={
            "gate_id": str(gate_id),
            "gate_type": gate_type,
            "decision": payload.decision,
        },
        recipients=recipients,
    )
    return _out(decided, GateOut)


def list_gates(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[GateOut]:
    """List a matter's approval gates, newest first (read scope)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter_id = UUID(str(scope.matter["id"]))
    return [_out(row, GateOut) for row in repo.list_gates(conn, matter_id)]


# ---------------------------------------------------------------------------
# Content packs
# ---------------------------------------------------------------------------


def _bundle_hash(pack_dir: Path) -> str:
    """Hash a pack directory: sorted relative paths + file bytes (sha256)."""
    digest = hashlib.sha256()
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(pack_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate a pack manifest; return the normalized pack section."""
    pack = manifest.get("pack") or {}
    missing = [
        field
        for field in ("id", "country_code", "subdivision_code", "semantic_version")
        if not pack.get(field)
    ]
    if missing:
        raise ValidationAppError(
            "The pack manifest is missing required pack fields: " + ", ".join(missing) + "."
        )
    status = str(pack.get("status", "draft"))
    if status not in PACK_STATUSES:
        raise ValidationAppError(
            f"The pack status {status!r} is not one of: {', '.join(sorted(PACK_STATUSES))}."
        )
    return {
        "pack_id": str(pack["id"]),
        "country_code": str(pack["country_code"]).upper(),
        "subdivision_code": str(pack["subdivision_code"]).upper(),
        "display_name": str(pack.get("display_name") or pack["id"]),
        "semantic_version": str(pack["semantic_version"]),
        "status": status,
        "effective_from": pack.get("effective_from") or "2026-09-26",
    }


def import_pack(
    conn: Any,
    *,
    actor_sub: str,
    pack_dir: str | None = None,
    bundle: UploadFile | None = None,
    expected_bundle_hash: str | None = None,
) -> PackImportResponse:
    """Import a content pack from a directory or an uploaded zip bundle.

    Validates the manifest, computes the bundle hash (verified against
    ``expected_bundle_hash`` when supplied), and registers the version in
    ``jurisdiction_packs``. Firm_admin only.
    """
    actor_id = matters_service.caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin"})

    if bundle is not None:
        filename = (bundle.filename or "").lower()
        if not filename.endswith(".zip"):
            raise ValidationAppError("The uploaded bundle must be a .zip archive.")
        with tempfile.TemporaryDirectory(prefix="legacyflow-pack-") as tmp:
            archive = Path(tmp) / "bundle.zip"
            archive.write_bytes(bundle.file.read())
            try:
                with zipfile.ZipFile(archive) as archive_file:
                    archive_file.extractall(tmp)
            except zipfile.BadZipFile as exc:
                raise ValidationAppError("The uploaded bundle is not a valid zip archive.") from exc
            return _import_pack_dir(
                conn, actor_id, Path(tmp), expected_bundle_hash=expected_bundle_hash
            )
    if pack_dir:
        source = Path(pack_dir)
        if not source.is_dir():
            raise ValidationAppError(f"The pack directory {pack_dir!r} does not exist.")
        return _import_pack_dir(conn, actor_id, source, expected_bundle_hash=expected_bundle_hash)
    raise ValidationAppError("Either a pack directory path or an uploaded bundle is required.")


def _import_pack_dir(
    conn: Any,
    actor_id: UUID,
    pack_dir: Path,
    *,
    expected_bundle_hash: str | None,
) -> PackImportResponse:
    manifest_path = pack_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise ValidationAppError("The pack is missing manifest.yaml.")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise ValidationAppError("The pack manifest.yaml is not a mapping.")
    normalized = _validate_manifest(manifest)

    computed_hash = _bundle_hash(pack_dir)
    if expected_bundle_hash and expected_bundle_hash != computed_hash:
        raise ValidationAppError(
            "The bundle hash does not match the expected hash; the pack was not imported."
        )

    jurisdiction = repo.find_jurisdiction(
        conn, normalized["country_code"], normalized["subdivision_code"]
    )
    if jurisdiction is None:
        jurisdiction_type = "us_state" if normalized["country_code"] == "US" else "country"
        jurisdiction = repo.insert_jurisdiction(
            conn,
            country_code=normalized["country_code"],
            subdivision_code=normalized["subdivision_code"],
            display_name=normalized["display_name"],
            jurisdiction_type=jurisdiction_type,
        )
    try:
        row = repo.insert_jurisdiction_pack(
            conn,
            jurisdiction_id=UUID(str(jurisdiction["id"])),
            version=normalized["semantic_version"],
            status=normalized["status"],
            effective_from=str(normalized["effective_from"]),
            manifest=manifest,
            source_bundle_hash=computed_hash,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError(
            f"Pack version {normalized['semantic_version']} is already registered for "
            f"{normalized['country_code']}-{normalized['subdivision_code']}."
        ) from exc

    record_audit_event(
        conn,
        action="packs.imported",
        resource_type="jurisdiction_pack",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=None,
        resource_id=UUID(str(row["id"])),
        after_summary={
            "pack_id": normalized["pack_id"],
            "version": normalized["semantic_version"],
            "bundle_hash": computed_hash,
        },
    )
    return PackImportResponse(
        pack_id=normalized["pack_id"],
        jurisdiction_code=(f"{normalized['country_code']}-{normalized['subdivision_code']}"),
        version=normalized["semantic_version"],
        status=normalized["status"],
        source_bundle_hash=computed_hash,
        jurisdiction_id=UUID(str(jurisdiction["id"])),
    )


def list_packs(conn: Any, *, actor_sub: str) -> list[PackOut]:
    """List registered content packs with versions (any firm/household member)."""
    actor_id = matters_service.caller_id(actor_sub)
    try:
        authz.get_user_firm_id(conn, actor_id)
    except AuthorizationError:
        if not matters_repo.active_household_ids_for_user(conn, actor_id):
            raise
    items = []
    for row in repo.list_packs(conn):
        effective_from = row.get("effective_from")
        items.append(
            PackOut(
                jurisdiction_code=f"{row['country_code']}-{row['subdivision_code']}",
                pack_id=str(row.get("pack_id") or row["id"]),
                display_name=str(row["display_name"]),
                version=str(row["version"]),
                status=str(row["status"]),
                effective_from=str(effective_from) if effective_from else None,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Content-pack lifecycle
# ---------------------------------------------------------------------------

#: Allowed pack lifecycle transitions. Forward motion is
#: draft -> internal_testing -> attorney_review -> approved; retirement is
#: terminal-ish: internal_testing, attorney_review, and approved may all
#: move to retired, but draft may not retire directly and retired never
#: moves forward again.
PACK_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"internal_testing"}),
    "internal_testing": frozenset({"attorney_review", "retired"}),
    "attorney_review": frozenset({"approved", "retired"}),
    "approved": frozenset({"retired"}),
    "retired": frozenset(),
}


def _pack_out_id(row: dict[str, Any]) -> str:
    """Prefer the manifest pack id; fall back to the row id."""
    manifest = row.get("manifest") or {}
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except ValueError:
            manifest = {}
    pack = manifest.get("pack") or {}
    return str(pack.get("id") or row["id"])


def transition_pack(
    conn: Any, *, actor_sub: str, pack_ref: str, to_status: str
) -> PackTransitionOut:
    """Move a content pack through its lifecycle.

    ``pack_ref`` is a jurisdiction_pack UUID or a manifest pack id
    (e.g. ``us-va``). Ordering is enforced by ``PACK_TRANSITIONS``;
    the attorney_review -> approved step additionally requires the
    attorney or firm_admin role, and every transition is audited as
    ``packs.status_changed``. All other transitions require firm_admin.
    """
    actor_id = matters_service.caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    row = repo.get_jurisdiction_pack_by_ref(conn, pack_ref)
    if row is None:
        raise NotFoundError("Content pack not found.")
    from_status = str(row["status"])
    to_status = str(to_status)
    if to_status not in PACK_STATUSES:
        raise ValidationAppError(f"Unknown pack status {to_status!r}.")
    allowed = PACK_TRANSITIONS.get(from_status)
    if allowed is None or to_status not in allowed:
        raise ConflictError(f"A pack cannot transition from {from_status!r} to {to_status!r}.")
    if to_status == "approved":
        # Attorney approval gate: attorney or firm_admin (never staff).
        authz.require_firm_role(conn, actor_id, firm_id, authz.ATTORNEY_ROLES)
    else:
        authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin"})
    updated = repo.update_jurisdiction_pack_status(
        conn,
        UUID(str(row["id"])),
        status=to_status,
        approved_by_user_id=actor_id if to_status == "approved" else None,
    )
    record_audit_event(
        conn,
        action="packs.status_changed",
        resource_type="jurisdiction_pack",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=UUID(str(row["id"])),
        before_summary={"status": from_status},
        after_summary={"status": to_status},
    )
    return PackTransitionOut(
        id=UUID(str(updated["id"])),
        pack_id=_pack_out_id(updated),
        version=str(updated["version"]),
        from_status=from_status,
        to_status=to_status,
    )
