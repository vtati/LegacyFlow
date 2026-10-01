"""Interview application services (Phase 1).

Orchestrates the interview turn pipeline:

1. **Classify** the message with the shared deterministic advice patterns
   (single definition lives in ``app.domains.ai.service``).
2. **Resolve context**: session state, pack question sets, current answers,
   missing required keys, recent (decrypted) history.
3. **Retrieve** pack educational content for the turn.
4. **Generate** via the configured LLM provider (tool calls → field proposals).
5. **Validate + persist**: proposals checked against pack keys; turns stored with
   summaries in the clear and bodies encrypted (ADR-002); one AI trace row per
   model call; audit events carry summaries only.

The model never writes facts. Every proposal waits for explicit user
confirmation (confirm / correct / reject) before reaching intake answers.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any
from uuid import UUID

from app.core import crypto
from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.ai import service as ai_service
from app.domains.ai.provider import (
    ChatMessage,
    InterviewContext,
    RetrievedDocument,
    get_llm_provider,
    get_stt_provider,
)
from app.domains.escalation import repository as escalation_repo
from app.domains.feature_flags import service as feature_flags
from app.domains.intake import question_sets as qs
from app.domains.intake import repository as intake_repo
from app.domains.intake import service as intake_service
from app.domains.interview import consistency, prompts
from app.domains.interview import repository as repo
from app.domains.interview.schemas import (
    ChecklistItemOut,
    CitationOut,
    ConsistencyFindingOut,
    ConsistencyReviewOut,
    DictationOut,
    ExtractionSummaryOut,
    ProposalDraftOut,
    ProposalOut,
    SessionCreate,
    SessionOut,
    SessionStatusUpdate,
    TurnCreate,
    TurnDetailOut,
    TurnOut,
    TurnSummaryOut,
)
from app.domains.interview.state_machine import SessionStatus, validate_transition
from app.domains.legal_content.pack_loader import LoadedPack
from app.domains.matters import service as matters_service

logger = logging.getLogger(__name__)

FLAG_KEY = "ai_interviewer"
_SUMMARY_LIMIT = 200
_HISTORY_TURNS = 10

_REFUSAL_MESSAGE = (
    "I can't provide legal advice or strategy recommendations. I've recorded "
    "your question for your attorney to review — an attorney can advise you on "
    "what is right for your situation. In the meantime, is there factual "
    "information about your plan you'd like to provide?"
)

# Escalation rule ids recorded by the interviewer (distinct namespace).
_INTERVIEW_ADVICE_RULE_ID = "interview_advice_request"
_INTERVIEW_INCOMPLETE_RULE_ID = "interview_incomplete_item"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_flag(conn: Any, firm_id: UUID) -> None:
    if not feature_flags.is_enabled(conn, FLAG_KEY, firm_id=firm_id):
        raise AuthorizationError("The AI interviewer is not enabled for this firm.")


def _summarize(text: str) -> str:
    return text[:_SUMMARY_LIMIT]


def _trace_hash(material: str) -> str:
    return hashlib.sha256(material.encode()).hexdigest()


def _answers_map(conn: Any, matter_id: UUID) -> dict[str, Any]:
    return {
        str(row["question_key"]): row["answer"] for row in intake_repo.list_answers(conn, matter_id)
    }


def _progress(pack: LoadedPack, answers: dict[str, Any]) -> tuple[float, list[str], int, int]:
    per_set, completeness, missing = qs.compute_progress(pack.question_sets, answers)
    total_required = sum(p.required_total for p in per_set.values())
    answered_required = sum(p.required_answered for p in per_set.values())
    return completeness, missing, total_required, answered_required


def _session_out(conn: Any, session: dict[str, Any], pack: LoadedPack) -> SessionOut:
    session_id = UUID(str(session["id"]))
    matter_id = UUID(str(session["matter_id"]))
    answers = _answers_map(conn, matter_id)
    completeness, missing, _total_required, _answered_required = _progress(pack, answers)
    open_keys = repo.open_proposal_keys(conn, session_id)
    checklist: list[ChecklistItemOut] = []
    for _set_id, _section_id, question in qs.iter_questions(pack.question_sets):
        key = str(question["key"])
        checklist.append(
            ChecklistItemOut(
                question_key=key,
                required=bool(question.get("required", False)),
                answered=qs.is_answered(answers.get(key)),
                has_open_proposal=key in open_keys,
            )
        )
    return SessionOut(
        id=session_id,
        matter_id=matter_id,
        question_set_id=str(session["question_set_id"]),
        pack_version=str(session["pack_version"]),
        status=str(session["status"]),
        completeness_pct=completeness,
        missing_required_keys=missing,
        checklist=checklist,
        open_proposal_count=repo.count_open_proposals(conn, session_id),
        turn_count=repo.count_turns(conn, session_id),
        created_at=session["created_at"],
        updated_at=session["updated_at"],
    )


def _proposal_out(row: dict[str, Any]) -> ProposalOut:
    return ProposalOut(
        id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        question_key=str(row["question_key"]),
        proposed_value=row["proposed_value"],
        corrected_value=row["corrected_value"],
        source=str(row["source"]),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        source_snippet=row["source_snippet"],
        source_page=row["source_page"],
        model_id=str(row["model_id"]),
        prompt_version=str(row["prompt_version"]),
        pack_version=str(row["pack_version"]),
        status=str(row["status"]),
        created_at=row["created_at"],
    )


def _load_session_for_write(conn: Any, matter_id: UUID, session_id: UUID) -> dict[str, Any]:
    session = repo.get_session(conn, session_id)
    if session is None or UUID(str(session["matter_id"])) != matter_id:
        raise NotFoundError("Interview session not found.")
    return session


def _history_messages(conn: Any, session_id: UUID) -> list[ChatMessage]:
    """Recent decrypted turns as chat messages (ADR-002 sanctioned use)."""
    messages: list[ChatMessage] = []
    for row in repo.list_recent_bodies(conn, session_id, _HISTORY_TURNS):
        try:
            text = crypto.decrypt_text(str(row["ciphertext"]))
        except Exception:
            logger.warning("Skipping undecryptable turn body %s", row["id"])
            continue
        role = str(row["role"])
        if role not in ("user", "assistant"):
            continue
        messages.append(ChatMessage(role=role, content=text))
    return messages


def _store_turn(
    conn: Any,
    *,
    session_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    role: str,
    text: str,
    citations: list[dict[str, Any]] | None = None,
    proposals_emitted: int = 0,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Persist a turn: summary in the clear, body encrypted (ADR-002)."""
    turn = repo.insert_turn(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        role=role,
        summary=_summarize(text),
        citations=citations or [],
        proposals_emitted=proposals_emitted,
        idempotency_key=idempotency_key,
    )
    ciphertext, key_version = crypto.encrypt_text(text)
    repo.insert_turn_body(
        conn,
        turn_id=UUID(str(turn["id"])),
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        ciphertext=ciphertext,
        key_version=key_version,
    )
    return turn


def _record_turn_audit(
    conn: Any,
    *,
    actor_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    session_id: UUID,
    user_summary: str,
    reply_summary: str,
    refused: bool,
    proposal_count: int,
    trace_hash: str,
) -> None:
    record_audit_event(
        conn,
        action="interview.turn",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=session_id,
        after_summary={
            "user_summary": user_summary,
            "reply_summary": reply_summary,
            "refused": refused,
            "proposal_count": proposal_count,
            "trace_hash": trace_hash,
        },
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def start_session(
    conn: Any, *, actor_sub: str, matter_id: UUID, payload: SessionCreate
) -> SessionOut:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    pack, _code, version = matters_service.load_matter_pack(conn, matter_id)
    if payload.question_set_id not in pack.question_sets:
        raise ValidationAppError(
            f"Unknown question set {payload.question_set_id!r} for this matter's pack."
        )

    existing = repo.get_active_session(conn, matter_id, payload.question_set_id)
    if existing is not None:
        return _session_out(conn, existing, pack)

    session = repo.insert_session(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        question_set_id=payload.question_set_id,
        pack_version=version,
        created_by_user_id=actor_id,
        status=SessionStatus.DRAFT.value,
    )
    record_audit_event(
        conn,
        action="interview.session_started",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(session["id"])),
        after_summary={
            "question_set_id": payload.question_set_id,
            "pack_version": version,
        },
    )
    return _session_out(conn, session, pack)


def get_session(conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID) -> SessionOut:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)
    session = _load_session_for_write(conn, matter_id, session_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    return _session_out(conn, session, pack)


def update_session_status(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    session_id: UUID,
    payload: SessionStatusUpdate,
) -> SessionOut:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    session = _load_session_for_write(conn, matter_id, session_id)
    current = SessionStatus(str(session["status"]))
    target = SessionStatus(payload.status)
    validate_transition(current, target)

    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    if target is SessionStatus.COMPLETED:
        # Nothing silently dropped: remaining required items become open
        # attorney-review escalations (idempotent per rule while open).
        _completeness, missing, _total_required, _answered_required = _progress(
            pack, _answers_map(conn, matter_id)
        )
        for key in missing:
            rule_id = f"{_INTERVIEW_INCOMPLETE_RULE_ID}:{key}"
            if not escalation_repo.open_escalation_exists(conn, matter_id, rule_id):
                escalation_repo.insert_escalation(
                    conn,
                    matter_id=matter_id,
                    rule_id=rule_id,
                    severity="review",
                    outcome="attorney_review",
                    facts_snapshot={
                        "source": "interview",
                        "kind": "incomplete_item",
                        "question_key": key,
                        "session_id": str(session_id),
                    },
                )

    updated = repo.update_session_status(
        conn, session_id, target.value, completed_at=target is SessionStatus.COMPLETED
    )
    record_audit_event(
        conn,
        action="interview.session_status",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=session_id,
        before_summary={"status": current.value},
        after_summary={"status": target.value},
    )
    return _session_out(conn, updated, pack)


def list_turns(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> list[TurnSummaryOut]:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    _require_flag(conn, UUID(str(scope.matter["firm_id"])))
    _load_session_for_write(conn, matter_id, session_id)
    return [
        TurnSummaryOut(
            id=UUID(str(row["id"])),
            role=str(row["role"]),
            summary=str(row["summary"]),
            created_at=row["created_at"],
        )
        for row in repo.list_turn_summaries(conn, session_id)
    ]


def list_proposals(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> list[ProposalOut]:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    _require_flag(conn, UUID(str(scope.matter["firm_id"])))
    _load_session_for_write(conn, matter_id, session_id)
    return [_proposal_out(row) for row in repo.list_open_proposals(conn, session_id)]


def list_extractions(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> list[ExtractionSummaryOut]:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    _require_flag(conn, UUID(str(scope.matter["firm_id"])))
    _load_session_for_write(conn, matter_id, session_id)
    return [
        ExtractionSummaryOut(
            id=UUID(str(row["id"])),
            filename=str(row["filename"]),
            status=str(row["status"]),
            page_count=int(row["page_count"]),
            proposals_created=int(row["proposals_created"]),
            created_at=row["created_at"],
        )
        for row in repo.list_extractions(conn, session_id)
    ]


def consistency_review(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> ConsistencyReviewOut:
    result = consistency.run_consistency_review(
        conn, actor_sub=actor_sub, matter_id=matter_id, session_id=session_id
    )
    return ConsistencyReviewOut(
        review_id=result.review_id,
        findings=[
            ConsistencyFindingOut(question=f.question, fields=f.fields, severity=f.severity)
            for f in result.findings
        ],
        escalations_created=result.escalations_created,
        facts_considered=result.facts_considered,
    )


def get_turn_detail(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID, turn_id: UUID
) -> TurnDetailOut:
    """Return the full (decrypted) text of one turn.

    Same read scope as the session; the caller is viewing their own
    conversation. Bodies are decrypted server-side and never logged.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    _require_flag(conn, UUID(str(scope.matter["firm_id"])))
    _load_session_for_write(conn, matter_id, session_id)
    turn = repo.get_turn(conn, turn_id)
    if turn is None or UUID(str(turn["session_id"])) != session_id:
        raise NotFoundError("Interview turn not found.")
    body = repo.get_turn_body_ciphertext(conn, turn_id)
    text = crypto.decrypt_text(str(body["ciphertext"])) if body else str(turn["summary"])
    citations = [
        CitationOut(
            source=str(c.get("source", "")),
            section=c.get("section"),
            excerpt=str(c.get("excerpt", "")),
        )
        for c in (turn["citations"] or [])
    ]
    return TurnDetailOut(
        id=turn_id,
        role=str(turn["role"]),
        text=text,
        citations=citations,
        created_at=turn["created_at"],
    )


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def post_turn(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    session_id: UUID,
    payload: TurnCreate,
) -> TurnOut:
    settings = get_settings()
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    session = _load_session_for_write(conn, matter_id, session_id)
    status = SessionStatus(str(session["status"]))
    if status is SessionStatus.PAUSED:
        raise ConflictError("Resume the interview session before posting turns.")
    if status in (SessionStatus.COMPLETED, SessionStatus.ABANDONED):
        raise ConflictError(f"Interview session is {status.value}; no further turns.")

    # Idempotent replay: a retried turn returns the original response.
    # The idempotency key lives on the user turn; the response is rebuilt
    # from the assistant turn that followed it (ordered by seq, because
    # created_at ties inside a single transaction).
    if payload.idempotency_key:
        existing = repo.find_turn_by_idempotency(conn, session_id, payload.idempotency_key)
        if existing is not None:
            reply_turn = repo.find_assistant_reply_turn(conn, session_id, int(existing["seq"]))
            if reply_turn is None:
                raise ConflictError("This turn is still being processed. Please retry in a moment.")
            return _replay_turn(conn, reply_turn, session, firm_id, matter_id)

    if repo.count_turns(conn, session_id) >= settings.INTERVIEW_MAX_TURNS_PER_SESSION:
        raise ConflictError(
            "This interview session has reached its turn limit. "
            "Your attorney can continue from the review package."
        )

    if status is SessionStatus.DRAFT:
        repo.update_session_status(conn, session_id, SessionStatus.IN_PROGRESS.value)

    message = payload.message.strip()
    pack, _code, version = matters_service.load_matter_pack(conn, matter_id)

    # --- Classification: advice/strategy requests are refused + escalated ---
    if ai_service._requests_advice(message):
        return _refused_turn(
            conn,
            actor_id=actor_id,
            firm_id=firm_id,
            matter_id=matter_id,
            session=session,
            pack=pack,
            version=version,
            message=message,
            idempotency_key=payload.idempotency_key,
        )

    answers = _answers_map(conn, matter_id)
    completeness, missing, total_required, answered_required = _progress(pack, answers)
    docs = ai_service._retrieve(message, pack, allow_sample=ai_service._allow_sample_content())
    retrieved = [
        RetrievedDocument(
            content_id=str(doc.get("content_id", "")),
            title=str(doc.get("title", "")),
            section=doc.get("section"),
            body_markdown=str(doc.get("body_markdown", "")),
        )
        for doc in docs
    ]
    context = InterviewContext(
        pack_id=pack.manifest.pack_id,
        jurisdiction_code=pack.manifest.jurisdiction_code,
        pack_version=version,
        documents=retrieved,
        missing_required_keys=missing,
        answered_required=answered_required,
        total_required=total_required,
    )
    history = _history_messages(conn, session_id)
    system_prompt = prompts.build_system_prompt(
        pack_id=pack.manifest.pack_id,
        jurisdiction_code=pack.manifest.jurisdiction_code,
        missing_required_keys=missing,
        answered_required=answered_required,
        total_required=total_required,
    )

    provider = get_llm_provider()
    started = time.perf_counter()
    response = provider.chat(
        system_prompt=system_prompt,
        messages=[*history, ChatMessage(role="user", content=message)],
        context=context,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    # --- Validate proposals: known pack keys only; drop the rest loudly ---
    known_keys = qs.all_question_keys(pack.question_sets)
    valid_proposals = []
    for draft in response.proposals:
        if draft.question_key not in known_keys:
            logger.warning(
                "Dropping proposal for unknown question key %r (session %s)",
                draft.question_key,
                session_id,
            )
            continue
        if draft.value is None or (isinstance(draft.value, str) and not draft.value.strip()):
            logger.warning(
                "Dropping proposal for %r with empty value (session %s)",
                draft.question_key,
                session_id,
            )
            continue
        valid_proposals.append(draft)

    trace_hash = _trace_hash(f"{message}|{version}|{','.join(d.content_id for d in retrieved)}")
    _store_turn(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        role="user",
        text=message,
        idempotency_key=payload.idempotency_key,
    )
    assistant_turn = _store_turn(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        role="assistant",
        text=response.reply,
        citations=[c.model_dump() for c in response.citations],
        proposals_emitted=len(valid_proposals),
    )
    repo.insert_ai_trace(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        turn_id=UUID(str(assistant_turn["id"])),
        provider=provider.name,
        model_id=settings.OPENAI_CHAT_MODEL if provider.name == "openai" else provider.name,
        model_version=None,
        prompt_template=prompts.PROMPT_TEMPLATE_NAME,
        prompt_version=prompts.PROMPT_TEMPLATE_VERSION,
        pack_version=version,
        tokens_in=response.usage.get("prompt_tokens"),
        tokens_out=response.usage.get("completion_tokens"),
        latency_ms=latency_ms,
        retrieved_doc_ids=[d.content_id for d in retrieved],
    )

    stored_proposals: list[ProposalOut] = []
    for draft in valid_proposals:
        # One open proposal per (session, key): skip when the user already has
        # an undecided proposal for this key.
        if draft.question_key in repo.open_proposal_keys(conn, session_id):
            continue
        row = repo.insert_proposal(
            conn,
            session_id=session_id,
            turn_id=UUID(str(assistant_turn["id"])),
            firm_id=firm_id,
            matter_id=matter_id,
            question_key=draft.question_key,
            proposed_value=draft.value,
            source="user_stated",
            confidence=draft.confidence,
            source_snippet=draft.source_snippet or None,
            model_id=provider.name,
            prompt_version=prompts.PROMPT_TEMPLATE_VERSION,
            pack_version=version,
        )
        stored_proposals.append(_proposal_out(row))

    _record_turn_audit(
        conn,
        actor_id=actor_id,
        firm_id=firm_id,
        matter_id=matter_id,
        session_id=session_id,
        user_summary=_summarize(message),
        reply_summary=_summarize(response.reply),
        refused=False,
        proposal_count=len(stored_proposals),
        trace_hash=trace_hash,
    )
    return TurnOut(
        id=UUID(str(assistant_turn["id"])),
        session_id=session_id,
        role="assistant",
        reply=response.reply,
        citations=[
            CitationOut(source=c.source, section=c.section, excerpt=c.excerpt)
            for c in response.citations
        ],
        proposals=[
            ProposalDraftOut(
                id=p.id,
                question_key=p.question_key,
                proposed_value=p.proposed_value,
                source=p.source,
                confidence=p.confidence,
                source_snippet=p.source_snippet,
            )
            for p in stored_proposals
        ],
        refused=False,
        escalated=False,
        trace_hash=trace_hash,
        completeness_pct=completeness,
        missing_required_keys=missing,
    )


def _refused_turn(
    conn: Any,
    *,
    actor_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    session: dict[str, Any],
    pack: LoadedPack,
    version: str,
    message: str,
    idempotency_key: str | None,
) -> TurnOut:
    """Advice-seeking turn: refuse, escalate, persist, and return."""
    session_id = UUID(str(session["id"]))
    trace_hash = _trace_hash(f"{message}|{version}|refused")
    ai_service._ensure_open_escalation(
        conn,
        matter_id=matter_id,
        rule_id=_INTERVIEW_ADVICE_RULE_ID,
        severity="review",
        outcome="attorney_review",
    )
    _store_turn(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        role="user",
        text=message,
        idempotency_key=idempotency_key,
    )
    assistant_turn = _store_turn(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        role="assistant",
        text=_REFUSAL_MESSAGE,
    )
    _record_turn_audit(
        conn,
        actor_id=actor_id,
        firm_id=firm_id,
        matter_id=matter_id,
        session_id=session_id,
        user_summary=_summarize(message),
        reply_summary=_summarize(_REFUSAL_MESSAGE),
        refused=True,
        proposal_count=0,
        trace_hash=trace_hash,
    )
    completeness, missing, _total_required, _answered_required = _progress(
        pack, _answers_map(conn, matter_id)
    )
    return TurnOut(
        id=UUID(str(assistant_turn["id"])),
        session_id=session_id,
        role="assistant",
        reply=_REFUSAL_MESSAGE,
        citations=[],
        proposals=[],
        refused=True,
        escalated=True,
        trace_hash=trace_hash,
        completeness_pct=completeness,
        missing_required_keys=missing,
    )


def _replay_turn(
    conn: Any,
    reply_turn: dict[str, Any],
    session: dict[str, Any],
    firm_id: UUID,
    matter_id: UUID,
) -> TurnOut:
    """Rebuild the original assistant response for an idempotent turn retry."""
    turn_id = UUID(str(reply_turn["id"]))
    body = repo.get_turn_body_ciphertext(conn, turn_id)
    reply = crypto.decrypt_text(str(body["ciphertext"])) if body else str(reply_turn["summary"])
    was_refused = reply == _REFUSAL_MESSAGE
    proposals = [
        ProposalDraftOut(
            id=UUID(str(row["id"])),
            question_key=str(row["question_key"]),
            proposed_value=row["proposed_value"],
            source=str(row["source"]),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            source_snippet=row["source_snippet"],
        )
        for row in repo.list_proposals_by_turn(conn, turn_id)
    ]
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    completeness, missing, _total_required, _answered_required = _progress(
        pack, _answers_map(conn, matter_id)
    )
    citations = [
        CitationOut(
            source=str(c.get("source", "")),
            section=c.get("section"),
            excerpt=str(c.get("excerpt", "")),
        )
        for c in (reply_turn["citations"] or [])
    ]
    return TurnOut(
        id=turn_id,
        session_id=UUID(str(session["id"])),
        role="assistant",
        reply=reply,
        citations=citations,
        proposals=proposals,
        refused=was_refused,
        escalated=was_refused,
        trace_hash=_trace_hash(f"replay|{turn_id}"),
        completeness_pct=completeness,
        missing_required_keys=missing,
        replayed=True,
    )


# ---------------------------------------------------------------------------
# Proposal decisions
# ---------------------------------------------------------------------------


def decide_proposal(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    proposal_id: UUID,
    decision: str,
    corrected_value: Any = None,
) -> ProposalOut:
    """Confirm / correct / reject a proposal.

    Confirmed and corrected values are written to intake answers with the same
    validation the guided intake uses; the audit trail records the decision
    and question key — never the value (contract rule 9).
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    proposal = repo.get_proposal(conn, proposal_id)
    if proposal is None or UUID(str(proposal["matter_id"])) != matter_id:
        raise NotFoundError("Proposal not found.")
    if str(proposal["status"]) != "proposed":
        raise ConflictError("This proposal has already been decided.")

    question_key = str(proposal["question_key"])
    if decision == "rejected":
        # ADR-002: only snippets the user explicitly confirmed are retained.
        row = repo.decide_proposal(
            conn,
            proposal_id,
            status="rejected",
            corrected_value=None,
            decided_by_user_id=actor_id,
            scrub_snippet=True,
        )
    else:
        value = proposal["proposed_value"] if decision == "confirmed" else corrected_value
        # Reuse the guided-intake write path: pack key validation, writable
        # status check, and its own audit event.
        intake_service.save_answers(
            conn, actor_sub=actor_sub, matter_id=matter_id, answers={question_key: value}
        )
        row = repo.decide_proposal(
            conn,
            proposal_id,
            status=decision,
            corrected_value=value if decision == "corrected" else None,
            decided_by_user_id=actor_id,
        )
    if not row:
        raise ConflictError("This proposal has already been decided.")

    record_audit_event(
        conn,
        action="interview.proposal_decided",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(proposal["session_id"])),
        after_summary={
            "proposal_id": str(proposal_id),
            "question_key": question_key,
            "decision": decision,
            "source": str(proposal["source"]),
        },
    )
    return _proposal_out(row)


# ---------------------------------------------------------------------------
# Retention (ADR-002)
# ---------------------------------------------------------------------------


def purge_expired_turn_bodies(conn: Any) -> int:
    """Delete raw turn bodies past the retention window.

    Matters must have been in a terminal state (``PLAN_IMPLEMENTED`` /
    ``ARCHIVED``) for longer than ``INTERVIEW_TURN_RETENTION_DAYS``. Proposals,
    traces, and audit events are permanent and untouched. Intended to run from
    a scheduled maintenance job; returns the number of bodies deleted.
    """
    settings = get_settings()
    deleted = repo.purge_expired_turn_bodies(conn, settings.INTERVIEW_TURN_RETENTION_DAYS)
    if deleted:
        logger.info("Purged %d expired interview turn bodies", deleted)
    return deleted


# ---------------------------------------------------------------------------
# Dictation
# ---------------------------------------------------------------------------


def dictate(conn: Any, *, actor_sub: str, matter_id: UUID, audio: bytes) -> DictationOut:
    """Transcribe audio; the transcript is returned for user review, never sent."""
    settings = get_settings()
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    if not audio:
        raise ValidationAppError("No audio was provided.")
    if len(audio) > settings.DICTATION_MAX_BYTES:
        raise ValidationAppError(
            f"Audio is too large ({len(audio)} bytes). Please record a shorter clip."
        )

    provider = get_stt_provider()
    transcript = provider.transcribe(audio, filename="dictation.webm").strip()
    record_audit_event(
        conn,
        action="interview.dictated",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={"provider": provider.name, "chars": len(transcript)},
    )
    return DictationOut(transcript=transcript, model=provider.name)
