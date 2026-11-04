"""AI consistency review for the interviewer (Phase 3).

Reads the matter's *verified* facts (confirmed interview answers and
confirmed document extractions, each with provenance) and asks the model to
find contradictions or gaps between them. Every finding is phrased as a
question for the attorney — never a verdict — and recorded as an escalation
so it lands in the attorney pre-review package.

Mechanical guardrails:
  * Findings must end with "?" — anything else is discarded server-side.
  * ``fields`` may only name known question keys; unknown keys are dropped.
  * Rule ids are content-hashed, so re-running a review is idempotent: an
    already-open identical finding is not duplicated.
  * Like extraction, only verified fact text is sent to the provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg.errors

from app.core import db
from app.core.config import get_settings
from app.core.errors import (
    AuthorizationError,
    ConfigurationError,
    ProviderError,
)
from app.domains.ai.provider import get_json_provider
from app.domains.escalation import repository as escalation_repo
from app.domains.feature_flags import service as feature_flags
from app.domains.intake import question_sets as qs
from app.domains.intake import repository as intake_repo
from app.domains.interview import repository as repo
from app.domains.matters import service as matters_service

logger = logging.getLogger(__name__)

FLAG_KEY = "ai_interviewer"
#: Persisted model_id for a provider-failure / unparseable degraded
#: run (M-2). Distinct from ``deterministic_dev`` — a legitimate
#: never-configured deterministic run — so the two are
#: distinguishable in interview_consistency_reviews / ai_traces.
#: Nothing may claim the real model ran for a degraded run. The one
#: in-repo switch on the exact ``deterministic_dev`` string (the
#: ai_trace provider derivation in _persist_consistency_review)
#: treats this marker the same way, so trace consumers keep working.
DEGRADED_MODEL_ID = "provider_degraded"
CONSISTENCY_PROMPT_VERSION = "consistency-v1"
CONSISTENCY_RULE_PREFIX = "interview_consistency"

_SYSTEM_PROMPT = """\
You are a careful reviewer for an estate-planning intake product. You receive
a list of VERIFIED facts the user already confirmed (each with its source:
"interview" for answers given in conversation, "document" for values extracted
from an uploaded document and confirmed by the user).

Your job: spot contradictions, tensions, or meaningful gaps BETWEEN these
facts that an attorney should clarify. Examples of the kind of thing to flag:
a marital status that conflicts with spouse information provided, a child
count that does not match the children named, an asset mentioned in one place
but not another.

HARD RULES:
- Output QUESTIONS ONLY. Every finding must be a single question ending with
  "?". Never state a conclusion, recommendation, or legal interpretation.
  Bad: "The marital status is inconsistent." Good: "The marital status is
  listed as single but a spouse name was provided — could you clarify?"
- Only flag genuine tensions between the given facts. Do not invent facts,
  do not speculate beyond what is listed, and do not flag missing
  information (incompleteness is handled separately).
- "fields" lists the fact keys involved, using exactly the keys given.
- severity is "review" for routine clarifications, "attention" when the
  tension could materially affect the plan.

Respond with a single JSON object only, exactly:
{"findings": [{"question": str, "fields": [str], "severity": str}, ...]}
An empty list is valid when the facts are mutually consistent.\
"""


@dataclass
class ConsistencyFinding:
    question: str
    fields: list[str]
    severity: str


@dataclass
class ConsistencyReviewResult:
    review_id: UUID
    findings: list[ConsistencyFinding] = field(default_factory=list)
    escalations_created: int = 0
    facts_considered: int = 0
    # ERR-022: the provider failed (or returned unparseable output) and
    # the review was persisted degraded — zero findings, no model claim.
    degraded: bool = False


@dataclass
class _PreparedConsistencyReview:
    """Frozen inputs for one review, assembled in a short read
    transaction (PERF-016, mirroring attorney_review's pattern)."""

    actor_id: UUID
    firm_id: UUID
    matter_id: UUID
    session_id: UUID
    pack_version: str
    facts: list[dict[str, Any]]
    known_keys: set[str]


@dataclass
class _ComputedConsistencyReview:
    findings: list[ConsistencyFinding] = field(default_factory=list)
    model_id: str = "deterministic_dev"
    usage: dict[str, int] = field(default_factory=dict)
    degraded: bool = False


def _require_flag(conn: Any, firm_id: UUID) -> None:
    if not feature_flags.is_enabled(conn, FLAG_KEY, firm_id=firm_id):
        raise AuthorizationError("The AI interviewer is not enabled for this firm.")


def _verified_facts(conn: Any, matter_id: UUID, session_id: UUID) -> list[dict[str, Any]]:
    """Confirmed facts with provenance, oldest first."""
    facts: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT question_key, proposed_value, corrected_value, source,
                   source_document_id, status
            FROM field_proposals
            WHERE session_id = %s AND status IN ('confirmed', 'corrected')
            ORDER BY decided_at ASC NULLS LAST, created_at ASC
            """,
            (str(session_id),),
        )
        for row in cur.fetchall():
            value = (
                row["corrected_value"] if row["status"] == "corrected" else row["proposed_value"]
            )
            facts.append(
                {
                    "key": str(row["question_key"]),
                    "value": value,
                    "provenance": "document"
                    if row["source"] == "document_extracted"
                    else "interview",
                }
            )
    void_keys = {f["key"] for f in facts}
    for row in intake_repo.list_answers(conn, matter_id):
        key = str(row["question_key"])
        if key in void_keys or not qs.is_answered(row["answer"]):
            continue
        facts.append({"key": key, "value": row["answer"], "provenance": "interview"})
    return facts


def _review_via_openai(
    *, model: str, facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Call the JSON gateway; return (raw finding items, usage)."""
    fact_lines = "\n".join(
        f"- {f['key']} = {json.dumps(f['value'])[:300]} (source: {f['provenance']})" for f in facts
    )
    started = time.monotonic()
    content, usage = get_json_provider(model=model).complete_json(
        system_prompt=_SYSTEM_PROMPT,
        user_content="VERIFIED FACTS:\n" + fact_lines,
        temperature=0.0,
        timeout=90.0,
        agent="consistency-review",
    )
    logger.info("consistency provider call latency_ms=%d", int((time.monotonic() - started) * 1000))
    try:
        parsed = json.loads(content)
    except (ValueError, AttributeError) as exc:
        # ERR-022: unparseable output is a provider failure, not a
        # silent zero-findings success — the caller degrades the
        # review (and must not record the real model id for it).
        raise ProviderError("Consistency provider returned unparseable output.") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        raise ProviderError("Consistency provider returned unparseable output.")
    return parsed["findings"], usage


def _rule_id(question: str, fields: list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps({"q": question, "f": sorted(fields)}, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{CONSISTENCY_RULE_PREFIX}:{digest}"


def _prepare_consistency_review(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> _PreparedConsistencyReview:
    """Read phase (short transaction): scope, flag, session, facts."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    session = repo.get_session(conn, session_id)
    if session is None or UUID(str(session["matter_id"])) != matter_id:
        raise AuthorizationError("Interview session not found for this matter.")
    pack_version = str(session["pack_version"])

    facts = _verified_facts(conn, matter_id, session_id)
    return _PreparedConsistencyReview(
        actor_id=actor_id,
        firm_id=firm_id,
        matter_id=matter_id,
        session_id=session_id,
        pack_version=pack_version,
        facts=facts,
        known_keys={f["key"] for f in facts},
    )


def _compute_consistency_findings(
    prepared: _PreparedConsistencyReview,
) -> _ComputedConsistencyReview:
    """Compute phase: the provider call. NO database access — this
    runs with no transaction open (PERF-016).

    ERR-022: a provider failure (transport error or unparseable
    output) degrades instead of propagating — the caller persists a
    review record with the facts considered, zero findings, the
    no-model marker, and ``degraded=True`` (the attorney-reviewer's
    policy for advisory AI calls).
    """
    settings = get_settings()
    computed = _ComputedConsistencyReview()
    facts = prepared.facts
    if not facts or settings.AI_LLM_PROVIDER.strip().lower() != "openai":
        return computed
    if not settings.OPENAI_API_KEY:
        raise ConfigurationError("AI_LLM_PROVIDER=openai requires OPENAI_API_KEY to be set.")
    try:
        raw_findings, usage = _review_via_openai(
            model=settings.OPENAI_CONSISTENCY_MODEL,
            facts=facts,
        )
    except Exception:
        logger.exception(
            "consistency provider call failed matter_id=%s; "
            "persisting a degraded review with zero findings",
            prepared.matter_id,
        )
        computed.degraded = True
        computed.model_id = DEGRADED_MODEL_ID
        return computed
    computed.model_id = settings.OPENAI_CONSISTENCY_MODEL
    computed.usage = usage
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        # Questions only: anything not phrased as a question is discarded.
        if not question.endswith("?"):
            continue
        fields = [f for f in (item.get("fields") or []) if str(f) in prepared.known_keys]
        if not fields:
            continue
        severity = str(item.get("severity", "review"))
        if severity not in ("review", "attention"):
            severity = "review"
        computed.findings.append(
            ConsistencyFinding(
                question=question[:500], fields=[str(f) for f in fields], severity=severity
            )
        )
    return computed


def _persist_consistency_review(
    conn: Any,
    prepared: _PreparedConsistencyReview,
    computed: _ComputedConsistencyReview,
) -> ConsistencyReviewResult:
    """Write phase (short transaction): escalations, trace, review row."""
    matter_id = prepared.matter_id
    session_id = prepared.session_id
    firm_id = prepared.firm_id
    findings = computed.findings
    model_id = computed.model_id
    usage = computed.usage

    escalations_created = 0
    for finding in findings:
        rule_id = _rule_id(finding.question, finding.fields)
        if escalation_repo.open_escalation_exists(conn, matter_id, rule_id):
            continue
        # Savepoint-isolated: one finding's escalation write must never
        # sink the whole review. A failed insert rolls back to the
        # savepoint, is logged, and the remaining findings (and the
        # review record itself) still persist — the endpoint degrades
        # instead of returning 500 in the family's face. (2026-10-27:
        # an 'attention' severity the escalations CHECK did not know
        # took down the entire review; the CHECK is fixed by migration
        # 20261027170000, this guard covers the next surprise.)
        try:
            with conn.transaction():
                escalation_repo.insert_escalation(
                    conn,
                    matter_id=matter_id,
                    rule_id=rule_id,
                    severity=finding.severity,
                    outcome="attorney_review",
                    facts_snapshot={
                        "source": "interview",
                        "kind": "consistency_finding",
                        "question": finding.question,
                        "fields": finding.fields,
                        "session_id": str(session_id),
                    },
                )
        except psycopg.errors.Error:
            # DB failures only: a programming error in the insert path
            # should still fail loudly, not silently drop a finding.
            logger.exception(
                "consistency escalation insert failed matter_id=%s rule_id=%s severity=%s",
                matter_id,
                rule_id,
                finding.severity,
            )
            continue
        escalations_created += 1

    repo.insert_ai_trace(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        turn_id=None,
        # Both non-model markers (never-configured deterministic and
        # provider-degraded) mean no real provider produced this run.
        provider=(
            "openai"
            if model_id not in ("deterministic_dev", DEGRADED_MODEL_ID)
            else "deterministic_dev"
        ),
        model_id=model_id,
        model_version=None,
        prompt_template="interview_consistency_review",
        prompt_version=CONSISTENCY_PROMPT_VERSION,
        pack_version=prepared.pack_version,
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
        latency_ms=None,
        retrieved_doc_ids=[],
    )
    review = repo.insert_consistency_review(
        conn,
        session_id=session_id,
        firm_id=firm_id,
        matter_id=matter_id,
        findings_count=len(findings),
        escalations_created=escalations_created,
        facts_considered=len(prepared.facts),
        model_id=model_id,
        prompt_version=CONSISTENCY_PROMPT_VERSION,
        created_by_user_id=prepared.actor_id,
    )
    return ConsistencyReviewResult(
        review_id=UUID(str(review["id"])),
        findings=findings,
        escalations_created=escalations_created,
        facts_considered=len(prepared.facts),
        degraded=computed.degraded,
    )


def run_consistency_review(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> ConsistencyReviewResult:
    """Single-connection composition of the three phases (direct
    callers / tests). The request path uses the phased variant so the
    provider call never sits inside a transaction (PERF-016)."""
    prepared = _prepare_consistency_review(
        conn, actor_sub=actor_sub, matter_id=matter_id, session_id=session_id
    )
    computed = _compute_consistency_findings(prepared)
    return _persist_consistency_review(conn, prepared, computed)


def run_consistency_review_phased(
    *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> ConsistencyReviewResult:
    """Request-path entry: manages its own short transactions —
    read/prepare, provider call with NO transaction open, persist —
    mirroring attorney_review.run_review (PERF-016)."""
    with db.transaction() as conn:
        prepared = _prepare_consistency_review(
            conn, actor_sub=actor_sub, matter_id=matter_id, session_id=session_id
        )
    computed = _compute_consistency_findings(prepared)
    with db.transaction() as conn:
        return _persist_consistency_review(conn, prepared, computed)
