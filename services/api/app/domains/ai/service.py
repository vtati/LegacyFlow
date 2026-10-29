"""Grounded AI gateway application service (Slice 4).

Pipeline for ``POST /v1/matters/{matter_id}/ai/ask``:

1. **Classify** the question with deterministic rules. Questions asking
   for legal advice or strategy are refused outright.
2. **Retrieve** from the matter's jurisdiction pack ``knowledge/``
   educational content only (never other jurisdictions, never other
   pack artifacts). Sample (attorney-pending) content is excluded when
   ``AI_ALLOW_SAMPLE_CONTENT=false``.
3. **Generate** via the configured provider. ``GroundedTemplateProvider``
   (the production provider, F4) assembles template answers from
   retrieved excerpts; no LLM, no network, no synthesized legal claims.
4. **Trace + persist**: every interaction is recorded with
   ``sha256(question + pack_version + retrieved doc ids)`` and
   summaries only (first 200 chars) — raw prompts and answers are never
   stored or logged.

Refusals and out-of-pack questions each create an escalation record for
attorney review via the escalation domain.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.domains.ai import repository as repo
from app.domains.ai.provider import (
    AIContext,
    RetrievedDocument,
    get_provider,
)
from app.domains.ai.schemas import AIAnswer, AIAskRequest, Citation
from app.domains.escalation import repository as escalation_repo
from app.domains.legal_content.pack_loader import LoadedPack
from app.domains.matters import service as matters_service

# First-N characters kept for stored summaries / audit metadata.
SUMMARY_LIMIT = 200

_ADVICE_PATTERNS = (
    re.compile(r"\bshould\s+i\b", re.IGNORECASE),
    re.compile(r"\bis\s+it\s+legal\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+should\b", re.IGNORECASE),
    re.compile(r"\bdo\s+i\s+(need|have\s+to|must)\b", re.IGNORECASE),
    re.compile(r"\b(can\s+i\s+sue|recommend|recommendation|advise|advice)\b", re.IGNORECASE),
)

_REFUSAL_MESSAGE = (
    "I can't provide legal advice or strategy recommendations. Your question "
    "has been recorded for your attorney to review — an attorney can advise "
    "you on what is right for your situation."
)

_ESCALATION_MESSAGE_TEMPLATE = (
    "I couldn't find relevant educational content in the approved {pack_id} "
    "materials for this question, so I've flagged it for your attorney to "
    "review. Your attorney can answer questions that go beyond the approved "
    "educational materials."
)

# Escalation rule ids recorded by the gateway (distinct namespace).
_ADVICE_RULE_ID = "ai_advice_request"
_OUT_OF_PACK_RULE_ID = "ai_out_of_pack"

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "about",
        "what",
        "does",
        "how",
        "why",
        "when",
        "where",
        "which",
        "your",
        "you",
        "are",
        "can",
        "tell",
        "me",
        "from",
        "into",
        "that",
        "this",
        "have",
        "has",
        "had",
        "will",
        "would",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "also",
        "any",
        "all",
        "some",
        "who",
        "whom",
        "whose",
        "wh",
        "is",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "did",
        "an",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "as",
        "or",
        "it",
        "its",
        "my",
        "our",
        "not",
        "but",
    }
)


# ---------------------------------------------------------------------------
# Classification / retrieval
# ---------------------------------------------------------------------------


def _requests_advice(question: str) -> bool:
    """Deterministic intent check: does the question seek legal advice?"""
    return any(pattern.search(question) for pattern in _ADVICE_PATTERNS)


def _allow_sample_content() -> bool:
    """Whether sample (attorney-pending) knowledge may be used.

    Reads the ``AI_ALLOW_SAMPLE_CONTENT`` setting (default False, F6):
    sample content grounds answers only on an explicit opt-in.
    """
    return get_settings().AI_ALLOW_SAMPLE_CONTENT


def _is_sample_doc(doc: dict[str, Any]) -> bool:
    frontmatter = doc.get("frontmatter") or {}
    return str(doc.get("status", "")).lower() == "sample" or bool(frontmatter.get("sample"))


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z]{3,}", text.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for word in words:
        if word not in _STOPWORDS and word not in seen:
            seen.add(word)
            terms.append(word)
    return terms


def _retrieve(
    question: str, pack: LoadedPack, *, allow_sample: bool, limit: int = 3
) -> list[dict[str, Any]]:
    """Keyword-match the question against the pack's knowledge docs.

    Searches ONLY this pack's ``knowledge/`` educational content; returns
    up to ``limit`` docs with a positive score, best first.
    """
    terms = _keywords(question)
    if not terms:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in pack.knowledge_docs:
        if not allow_sample and _is_sample_doc(doc):
            continue
        haystack = f"{doc.get('title', '')}\n{doc.get('body_markdown', '')}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _score, doc in scored[:limit]]


def _trace_hash(question: str, pack_version: str, doc_ids: list[str]) -> str:
    material = f"{question}|{pack_version}|{','.join(doc_ids)}"
    return hashlib.sha256(material.encode()).hexdigest()


def _summarize(text: str) -> str:
    """Stored/audited summary: first SUMMARY_LIMIT chars, never raw."""
    return text[:SUMMARY_LIMIT]


# ---------------------------------------------------------------------------
# Side effects
# ---------------------------------------------------------------------------


def _ensure_open_escalation(
    conn: Any, *, matter_id: UUID, rule_id: str, severity: str, outcome: str
) -> None:
    """Record an open escalation idempotently per (matter, rule)."""
    if escalation_repo.open_escalation_exists(conn, matter_id, rule_id):
        return
    escalation_repo.insert_escalation_isolated(
        conn,
        matter_id=matter_id,
        rule_id=rule_id,
        severity=severity,
        outcome=outcome,
        facts_snapshot={"source": "ai_gateway", "kind": rule_id},
    )


def _record_interaction(
    conn: Any,
    *,
    actor_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    question: str,
    answer: str,
    citations: list[Citation],
    trace_hash: str,
    refused: bool,
) -> None:
    repo.insert_ai_interaction(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        user_id=actor_id,
        question_summary=_summarize(question),
        answer_summary=_summarize(answer),
        citations=[citation.model_dump() for citation in citations],
        trace_hash=trace_hash,
    )
    record_audit_event(
        conn,
        action="ai.asked",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={
            "question_summary": _summarize(question),
            "refused": refused,
            "trace_hash": trace_hash,
            "citation_count": len(citations),
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ask(conn: Any, *, actor_sub: str, matter_id: UUID, payload: AIAskRequest) -> AIAnswer:
    """Answer a question grounded in the matter's jurisdiction pack.

    Read access required (firm staff+ or any active household member).
    Never logs, stores, or echoes raw prompts/responses beyond the
    returned answer payload.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter = scope.matter
    matter_id = UUID(str(matter["id"]))
    firm_id = UUID(str(matter["firm_id"]))

    question = payload.question.strip()
    pack, _code, version = matters_service.load_matter_pack(conn, matter_id)

    if _requests_advice(question):
        trace_hash = _trace_hash(question, version, [])
        _ensure_open_escalation(
            conn,
            matter_id=matter_id,
            rule_id=_ADVICE_RULE_ID,
            severity="review",
            outcome="attorney_review",
        )
        _record_interaction(
            conn,
            actor_id=actor_id,
            firm_id=firm_id,
            matter_id=matter_id,
            question=question,
            answer=_REFUSAL_MESSAGE,
            citations=[],
            trace_hash=trace_hash,
            refused=True,
        )
        return AIAnswer(
            answer=_REFUSAL_MESSAGE,
            citations=[],
            trace_hash=trace_hash,
            refused=True,
        )

    docs = _retrieve(question, pack, allow_sample=_allow_sample_content())
    if not docs:
        message = _ESCALATION_MESSAGE_TEMPLATE.format(pack_id=pack.manifest.pack_id)
        trace_hash = _trace_hash(question, version, [])
        _ensure_open_escalation(
            conn,
            matter_id=matter_id,
            rule_id=_OUT_OF_PACK_RULE_ID,
            severity="info",
            outcome="attorney_review",
        )
        _record_interaction(
            conn,
            actor_id=actor_id,
            firm_id=firm_id,
            matter_id=matter_id,
            question=question,
            answer=message,
            citations=[],
            trace_hash=trace_hash,
            refused=False,
        )
        return AIAnswer(answer=message, citations=[], trace_hash=trace_hash, refused=False)

    retrieved = [
        RetrievedDocument(
            content_id=str(doc.get("content_id", "")),
            title=str(doc.get("title", "")),
            section=doc.get("section"),
            body_markdown=str(doc.get("body_markdown", "")),
        )
        for doc in docs
    ]
    context = AIContext(
        pack_id=pack.manifest.pack_id,
        jurisdiction_code=pack.manifest.jurisdiction_code,
        pack_version=version,
        documents=retrieved,
    )
    response = get_provider().generate(question, context)
    trace_hash = _trace_hash(question, version, [doc.content_id for doc in retrieved])
    _record_interaction(
        conn,
        actor_id=actor_id,
        firm_id=firm_id,
        matter_id=matter_id,
        question=question,
        answer=response.answer,
        citations=response.citations,
        trace_hash=trace_hash,
        refused=False,
    )
    return AIAnswer(
        answer=response.answer,
        citations=response.citations,
        trace_hash=trace_hash,
        refused=False,
    )
