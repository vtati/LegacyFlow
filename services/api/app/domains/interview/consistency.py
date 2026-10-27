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

from app.core.config import get_settings
from app.core.errors import (
    AuthorizationError,
    ConfigurationError,
    ProviderError,
)
from app.domains.escalation import repository as escalation_repo
from app.domains.feature_flags import service as feature_flags
from app.domains.intake import question_sets as qs
from app.domains.intake import repository as intake_repo
from app.domains.interview import repository as repo
from app.domains.matters import service as matters_service

logger = logging.getLogger(__name__)

FLAG_KEY = "ai_interviewer"
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
    *, api_key: str, model: str, facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    import httpx

    fact_lines = "\n".join(
        f"- {f['key']} = {json.dumps(f['value'])[:300]} (source: {f['provenance']})" for f in facts
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "VERIFIED FACTS:\n" + fact_lines,
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
    started = time.monotonic()
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=90.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("OpenAI consistency request failed: %s", exc)
        raise ProviderError() from exc
    logger.info("consistency provider call latency_ms=%d", int((time.monotonic() - started) * 1000))
    data = response.json()
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            usage[key] = int((data.get("usage") or {}).get(key, 0))
        except (TypeError, ValueError):
            pass
    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
        parsed = json.loads(content)
    except (ValueError, AttributeError):
        parsed = {}
    findings = parsed.get("findings")
    return (findings if isinstance(findings, list) else []), usage


def _rule_id(question: str, fields: list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps({"q": question, "f": sorted(fields)}, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{CONSISTENCY_RULE_PREFIX}:{digest}"


def run_consistency_review(
    conn: Any, *, actor_sub: str, matter_id: UUID, session_id: UUID
) -> ConsistencyReviewResult:
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)

    session = repo.get_session(conn, session_id)
    if session is None or UUID(str(session["matter_id"])) != matter_id:
        raise AuthorizationError("Interview session not found for this matter.")
    pack_version = str(session["pack_version"])

    facts = _verified_facts(conn, matter_id, session_id)
    known_keys = {f["key"] for f in facts}

    settings = get_settings()
    findings: list[ConsistencyFinding] = []
    model_id = "deterministic_dev"
    usage: dict[str, int] = {}
    if facts and settings.AI_LLM_PROVIDER.strip().lower() == "openai":
        if not settings.OPENAI_API_KEY:
            raise ConfigurationError("AI_LLM_PROVIDER=openai requires OPENAI_API_KEY to be set.")
        raw_findings, usage = _review_via_openai(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_CONSISTENCY_MODEL,
            facts=facts,
        )
        model_id = settings.OPENAI_CONSISTENCY_MODEL
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            # Questions only: anything not phrased as a question is discarded.
            if not question.endswith("?"):
                continue
            fields = [f for f in (item.get("fields") or []) if str(f) in known_keys]
            if not fields:
                continue
            severity = str(item.get("severity", "review"))
            if severity not in ("review", "attention"):
                severity = "review"
            findings.append(
                ConsistencyFinding(
                    question=question[:500], fields=[str(f) for f in fields], severity=severity
                )
            )

    escalations_created = 0
    for finding in findings:
        rule_id = _rule_id(finding.question, finding.fields)
        if escalation_repo.open_escalation_exists(conn, matter_id, rule_id):
            continue
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
        escalations_created += 1

    repo.insert_ai_trace(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        turn_id=None,
        provider="openai" if model_id != "deterministic_dev" else "deterministic_dev",
        model_id=model_id,
        model_version=None,
        prompt_template="interview_consistency_review",
        prompt_version=CONSISTENCY_PROMPT_VERSION,
        pack_version=pack_version,
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
        facts_considered=len(facts),
        model_id=model_id,
        prompt_version=CONSISTENCY_PROMPT_VERSION,
        created_by_user_id=actor_id,
    )
    return ConsistencyReviewResult(
        review_id=UUID(str(review["id"])),
        findings=findings,
        escalations_created=escalations_created,
        facts_considered=len(facts),
    )
