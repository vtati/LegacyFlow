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

# Rate limits for interview turns (per user, to bound AI API costs).
INTERVIEW_TURNS_PER_MINUTE = 10
INTERVIEW_TURNS_PER_DAY = 200


def _check_turn_rate_limit(conn: Any, actor_id: UUID) -> None:
    """Enforce per-user interview turn rate limits. Raises RateLimitError."""
    from app.core.errors import RateLimitError

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n
              FROM interview_turns t
              JOIN interview_sessions s ON s.id = t.session_id
             WHERE s.created_by_user_id = %s
               AND t.created_at > NOW() - INTERVAL '1 minute'
               AND t.role = 'user'
            """,
            (str(actor_id),),
        )
        per_minute = cur.fetchone()["n"]
        cur.execute(
            """
            SELECT COUNT(*) AS n
              FROM interview_turns t
              JOIN interview_sessions s ON s.id = t.session_id
             WHERE s.created_by_user_id = %s
               AND t.created_at > NOW() - INTERVAL '1 day'
               AND t.role = 'user'
            """,
            (str(actor_id),),
        )
        per_day = cur.fetchone()["n"]
    if per_minute >= INTERVIEW_TURNS_PER_MINUTE:
        logger.warning("interview rate limit hit (per-minute) user=%s", actor_id)
        raise RateLimitError(
            "You're sending messages too quickly. Please wait a moment and try again."
        )
    if per_day >= INTERVIEW_TURNS_PER_DAY:
        logger.warning("interview rate limit hit (per-day) user=%s", actor_id)
        raise RateLimitError(
            "You've reached today's interview limit. Please continue tomorrow."
        )

_REFUSAL_MESSAGE = (
    "I can't provide legal advice or strategy recommendations. I've recorded "
    "your question for your attorney to review — an attorney can advise you on "
    "what is right for your situation. In the meantime, is there factual "
    "information about your plan you'd like to provide?"
)

_OUT_OF_SCOPE_REFUSAL = (
    "I'm only able to help with estate planning and the LegacyFlow interview. "
    "Let's get back to your estate plan — is there factual information you'd "
    "like to provide?"
)

#: Terms indicating a non-legal advice request (medical, etc.).
_NON_LEGAL_TERMS = frozenset(
    {
        "medication", "medicine", "doctor", "headache", "symptom", "diagnosis",
        "treatment", "prescription", "pill", "dose", "sick", "illness",
    }
)


def _is_non_legal_advice(message: str) -> bool:
    """Check if an advice-seeking message is clearly not about legal matters."""
    import re

    lowered = message.lower()
    return any(term in lowered for term in _NON_LEGAL_TERMS)

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


#: Patterns for deterministic yes/no parsing (loop prevention fallback).
_YES_PATTERNS = (
    r"^\s*(yes|yeah|yep|yup|sure|correct|right|absolutely|definitely)\b",
    r"\bi do\b",
    r"\bwe do\b",
)
_NO_PATTERNS = (
    r"^\s*(no|nope|nah|never|not really)\b",
    r"\bi don'?t\b",
    r"\bwe don'?t\b",
    r"\bdo not expect\b",
    r"\bdon'?t expect\b",
    # General negation forms. These MUST be checked before the YES
    # patterns: "\bi do\b" / "\bwe do\b" match inside "I do not..." /
    # "We do not...", which inverted three "No" answers to Yes @0.95 in
    # the 2026-10-24 E2E run.
    r"\bdo not\b",
    r"\bdon'?t\b",
    r"\bdoes not\b",
    r"\bdo not have\b",
    r"\bnot expect\b",
)


def _parse_yes_no(text: str) -> bool | None:
    """Parse a clear yes/no answer. Returns True/False or None if ambiguous.

    Scoped to the FIRST clause: real answers are compound ("Yes, I do own
    one, and I don't expect any conflicts."), so a negation in a trailing
    clause about another topic must not flip a leading explicit Yes —
    and a leading "No, we do not..." must not be flipped by the "we do"
    inside it. Within one clause, negation wins over affirmation
    ("I do not have one." is a No).
    """
    import re

    lowered = text.lower().strip()

    def _scan(segment: str) -> bool | None:
        if any(re.search(pat, segment) for pat in _NO_PATTERNS):
            return False
        if any(re.search(pat, segment) for pat in _YES_PATTERNS):
            return True
        return None

    first_clause = re.split(r"[.!?;,]|\bbut\b|\band\b", lowered, maxsplit=1)[0].strip()
    scoped = _scan(first_clause)
    if scoped is not None:
        return scoped
    return _scan(lowered)


def _deterministic_boolean_proposal(
    *,
    user_message: str,
    assistant_reply: str,
    question_sets: dict[str, dict[str, Any]],
    existing_proposal_keys: set[str],
) -> dict[str, Any] | None:
    """Deterministic fallback: if the model asked a boolean question and the
    user gave a clear yes/no answer, but no proposal was emitted, create one.

    This prevents the interview from looping forever on yes/no questions when
    the model acknowledges without recording (e.g. "Thanks — I've noted that"
    with no tool call).
    """
    import re

    from app.domains.intake import question_sets as qs

    # Find which boolean question was asked last by matching the assistant's
    # reply against question labels. The previous assistant message usually
    # acknowledges the last topic AND asks the next one, so several labels
    # can match: the fallback fires only when exactly ONE undecided boolean
    # question matches — attribution by coin flip fabricated values in the
    # 2026-10-24 E2E run.
    label_words_by_key: dict[str, list[str]] = {}

    def _candidates(context_lower: str) -> list[str]:
        found: list[str] = []
        for _, _, question in qs.iter_questions(question_sets):
            if question.get("type") != "boolean":
                continue
            key = str(question["key"])
            if key in existing_proposal_keys:
                continue
            label = str(question.get("label") or "")
            # Match on significant words from the label (skip stopwords).
            label_words = [
                w for w in re.findall(r"\w+", label.lower())
                if len(w) > 3 and w not in {"what", "does", "your", "with", "have", "this", "that"}
            ]
            if not label_words:
                continue
            label_words_by_key[key] = label_words
            # Require at least 2 significant words to match (avoid false positives).
            matches = sum(1 for w in label_words if w in context_lower)
            if matches >= 2 and matches >= len(label_words) * 0.5:
                found.append(key)
        return found

    candidates = _candidates(assistant_reply.lower())
    if len(candidates) != 1:
        # Second channel: the user's own words name the topic ("No, we do
        # not have any specific bequests") — vital when turns drift out of
        # alignment and the last assistant message was about another item.
        candidates = _candidates(user_message.lower())
    if len(candidates) != 1:
        return None
    asked_key = candidates[0]

    # The VALUE comes from the clause(s) about THIS topic, never from the
    # whole message: a bundled v2 answer covers many topics with mixed
    # polarity ("Yes to the POA, no business interests, no conflicts…"),
    # and whole-message polarity fabricated has_minor_children=False out
    # of an identity paragraph in the 2026-10-24 Part 3 live run.
    label_words = label_words_by_key.get(asked_key, [])
    clauses = re.split(r"[.!?;,]|\bbut\b|\band\b", user_message.lower())
    topic = [
        (i, c)
        for i, c in enumerate(clauses)
        if sum(1 for w in label_words if w in c) >= 2
    ]
    if len(topic) != 1:
        return None
    idx, topic_clause = topic[0]
    value = _parse_yes_no(topic_clause)
    if value is None and idx > 0:
        # "Yes, I expect some disagreement." — the comma split strands the
        # polarity word in the preceding fragment. Borrow it when that
        # fragment is a short polarity-only lead-in.
        prev = clauses[idx - 1].strip()
        if (
            prev
            and len(prev.split()) <= 3
            and _parse_yes_no(prev) is not None
        ):
            value = _parse_yes_no(f"{prev} {topic_clause}")
    if value is None:
        return None

    return {
        "question_key": asked_key,
        "value": value,
        # Below the 0.9 auto-confirm threshold ON PURPOSE: a deterministic
        # guess is always human-reviewed, never silently written.
        "confidence": 0.80,
        "source_snippet": user_message.strip()[:500],
    }


def _extract_person_name(text: str) -> str | None:
    """Extract a person name from a user message. Returns the name or None.

    Two accepted shapes:
    - a bare name ("Priya Sharma", "Amit Sharma"), or
    - a short nomination sentence carrying exactly ONE two-word
      capitalized name span ("My wife Priya Sharma should be executor.").
      Real users answer in sentences; the 2026-10-24 E2E run showed the
      old bare-name-only rule silently dropped backup guardian/executor
      answers whenever the model also skipped its tool call.

    Rejected: place names (US states, address words), descriptive
    multi-name text ("We have three children: Aarav, Diya..."), and any
    span containing a non-name word. Every extraction lands on a
    human-reviewed proposal card at 0.80 confidence, so a rare miss is
    visible and correctable — a dropped answer is neither.
    """
    import re

    stripped = text.strip().rstrip(".")
    if not stripped:
        return None

    non_name_words = {
        "we", "i", "my", "the", "a", "an", "no", "yes", "not", "have",
        "has", "had", "are", "is", "was", "were", "be", "been",
    }
    # Places are not people: "Texas." became Primary Executor @0.90 in
    # the 2026-10-24 E2E run because any capitalized word passed.
    place_words = {
        "texas", "california", "florida", "virginia", "austin", "houston",
        "dallas", "street", "avenue", "road", "drive", "lane", "city",
        "state", "states", "america", "english",
    }
    us_states = {
        "alabama", "alaska", "arizona", "arkansas", "california",
        "colorado", "connecticut", "delaware", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
        "kentucky", "louisiana", "maine", "maryland", "massachusetts",
        "michigan", "minnesota", "mississippi", "missouri", "montana",
        "nebraska", "nevada", "hampshire", "jersey", "mexico", "carolina",
        "dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode",
        "island", "tennessee", "texas", "utah", "vermont", "virginia",
        "washington", "wisconsin", "wyoming", "york", "columbia",
    }
    blocked = non_name_words | place_words | us_states

    def _clean(word: str) -> str:
        return re.sub(r"[^A-Za-z]", "", word)

    def _is_name_word(word: str) -> bool:
        return bool(word) and word[0].isupper() and word.lower() not in blocked

    words = stripped.split()

    # Bare-name shape: 1-4 words, all capitalized name words. A SINGLE
    # token gets a wider blocklist (major cities, greetings, meta words):
    # "Denver." or "Hello." is not a person, and single tokens carry no
    # corroborating second word.
    single_token_blocked = blocked | {
        "denver", "chicago", "boston", "phoenix", "seattle", "portland",
        "atlanta", "miami", "york", "angeles", "vegas", "orleans",
        "hello", "thanks", "okay", "sure", "right", "correct", "morning",
        "afternoon", "evening", "today", "tomorrow", "none", "nobody",
        "nothing", "everything", "something", "someone", "anyone",
    }
    if len(words) <= 4 and all(_is_name_word(_clean(w)) for w in words if _clean(w)):
        cleaned = [_clean(w) for w in words if _clean(w)]
        if 1 <= len(cleaned) <= 4:
            if len(cleaned) == 1 and cleaned[0].lower() in single_token_blocked:
                return None
            return " ".join(cleaned)

    # Sentence shape: exactly one run of exactly two capitalized name
    # words, and the text must read like a nomination (a relation word,
    # a role word, or a naming verb) — otherwise it is descriptive prose
    # for the model's own extraction, not the fallback.
    lowered = stripped.lower()
    nomination_hint = any(
        hint in lowered
        for hint in (
            "my wife", "my husband", "my brother", "my sister", "my mother",
            "my father", "my friend", "my son", "my daughter", "guardian",
            "executor", "trustee", "agent", "should be", "will be",
        )
    )
    if nomination_hint and re.search(r"\sif\s", stripped, flags=re.IGNORECASE):
        # "Amit Sharma, my brother, should serve as backup executor if
        # Priya Sharma cannot." names two people; the nominee is the
        # name span in the main clause, before the conditional. This
        # runs before the comma guard below on purpose: the appositive
        # comma in such answers is not a descriptive list.
        head = re.split(r"\s+if\s+", stripped, maxsplit=1, flags=re.IGNORECASE)[0]
        head_spans: list[str] = []
        run = []
        for w in head.split():
            cw = _clean(w)
            if _is_name_word(cw):
                run.append(cw)
            else:
                if 2 <= len(run) <= 3:
                    head_spans.append(" ".join(run))
                run = []
        if 2 <= len(run) <= 3:
            head_spans.append(" ".join(run))
        if len(head_spans) == 1:
            return head_spans[0]
    if not nomination_hint or "," in stripped or ":" in stripped:
        return None
    spans: list[str] = []
    run: list[str] = []
    for w in words:
        cw = _clean(w)
        if _is_name_word(cw):
            run.append(cw)
        else:
            if 2 <= len(run) <= 3:
                spans.append(" ".join(run))
            run = []
    if 2 <= len(run) <= 3:
        spans.append(" ".join(run))
    if len(spans) == 1:
        return spans[0]
    return None


def _next_adaptive_question(
    *,
    answered_keys: set[str],
    pending_keys: set[str],
    section: str,
) -> str | None:
    """Adaptive interrogation: given what's answered, return the next question
    key for a section, or None if the section is complete.

    This makes the interviewer "interrogate" each section thoroughly rather
    than asking a fixed list — follow-ups are driven by what the user already
    provided.
    """
    done = answered_keys | pending_keys

    if section == "executors":
        # Primary executor first, then backup, then address.
        if "primary_executor_person" not in done:
            return "primary_executor_person"
        if "backup_executor_person" not in done:
            return "backup_executor_person"
        if "primary_executor_address" not in done:
            return "primary_executor_address"
        return None

    if section == "bequests":
        # Ask if they have specific bequests; if yes, get details.
        # Residuary is always required.
        if "has_specific_bequests" not in done:
            return "has_specific_bequests"
        if "specific_bequests" not in done:
            # Only ask for details if they said yes (True). If they said no,
            # the details question is skipped — but we can't know the value
            # here, so the caller checks answered_keys for the boolean.
            return "specific_bequests"
        if "residuary_beneficiaries" not in done:
            return "residuary_beneficiaries"
        return None

    if section == "addresses":
        if "testator_street_address" not in done:
            return "testator_street_address"
        if "testator_city" not in done:
            return "testator_city"
        if "primary_guardian_address" not in done:
            return "primary_guardian_address"
        return None

    if section == "healthcare":
        if "healthcare_agent_person" not in done:
            return "healthcare_agent_person"
        if "healthcare_agent_phone" not in done:
            return "healthcare_agent_phone"
        if "successor_healthcare_agent_person" not in done:
            return "successor_healthcare_agent_person"
        if "living_will_directives" not in done:
            return "living_will_directives"
        if "organ_donation_wishes" not in done:
            return "organ_donation_wishes"
        return None

    if section == "poa":
        if "poa_agent_person" not in done:
            return "poa_agent_person"
        if "poa_agent_address" not in done:
            return "poa_agent_address"
        if "successor_poa_agent_person" not in done:
            return "successor_poa_agent_person"
        return None

    if section == "trust":
        if "wants_revocable_trust" not in done:
            return "wants_revocable_trust"
        if "successor_trustee_person" not in done:
            return "successor_trustee_person"
        if "trust_initial_property" not in done:
            return "trust_initial_property"
        if "trust_distribution_age" not in done:
            return "trust_distribution_age"
        if "trust_marital_share" not in done:
            return "trust_marital_share"
        return None

    if section == "beneficiaries":
        if "retirement_beneficiaries_current" not in done:
            return "retirement_beneficiaries_current"
        if "life_insurance_beneficiaries_current" not in done:
            return "life_insurance_beneficiaries_current"
        return None

    if section == "final_wishes":
        if "funeral_wishes" not in done:
            return "funeral_wishes"
        if "digital_asset_wishes" not in done:
            return "digital_asset_wishes"
        if "guardian_relationship" not in done:
            return "guardian_relationship"
        if "children_upbringing_wishes" not in done:
            return "children_upbringing_wishes"
        if "children_education_wishes" not in done:
            return "children_education_wishes"
        return None

    return None


def _deterministic_person_proposal(
    *,
    user_message: str,
    assistant_reply: str,
    question_sets: dict[str, dict[str, Any]],
    existing_proposal_keys: set[str],
) -> dict[str, Any] | None:
    """Deterministic fallback: if the model asked a person-name question and
    the user gave a name, but no proposal was emitted, create one.

    Discipline (hardened after the 2026-10-24 E2E fabrication, where
    "Texas." became Primary Executor @0.90): the role named in the
    assistant's message must match the question's role; "backup /
    alternate / successor" qualifiers in the message select the
    qualified key (and their absence selects the primary key); and the
    fallback fires only when exactly ONE undecided person question
    matches — otherwise it abstains and lets the model/checklist drive.
    """
    import re

    from app.domains.intake import question_sets as qs

    roles = ("guardian", "executor", "trustee", "agent")

    def _candidates_for(context_text: str) -> list[str]:
        ctx = context_text.lower()
        sentences = [seg for seg in re.split(r"[.!?]+", ctx) if seg.strip()]
        tail = sentences[-1] if sentences else ctx
        tail_roles = {r for r in roles if r in tail}
        if tail_roles:
            ctx_roles = tail_roles
            qualified_ctx = any(
                q in tail for q in ("backup", "alternate", "successor", "secondary")
            )
        else:
            ctx_roles = {r for r in roles if r in ctx}
            qualified_ctx = any(
                q in ctx for q in ("backup", "alternate", "successor", "secondary")
            )
        found: list[str] = []
        for _, _, question in qs.iter_questions(question_sets):
            if question.get("type") != "person":
                continue
            key = str(question.get("key", ""))
            if key in existing_proposal_keys:
                continue
            label = str(question.get("label") or "")
            key_words = [w for w in key.split("_") if len(w) > 3]
            label_words = [
                w for w in re.findall(r"\w+", label.lower())
                if len(w) > 3 and w not in {"what", "does", "your", "with", "have", "this", "that", "who"}
            ]
            key_matches = sum(1 for w in key_words if w in ctx)
            label_matches = sum(1 for w in label_words if w in ctx)
            key_roles = {r for r in roles if r in key}
            role_match = bool(key_roles & ctx_roles)
            if role_match or label_matches >= 2 or (key_matches >= 1 and label_matches >= 2):
                found.append(key)
        qualified = [
            k for k in found
            if any(q in k for q in ("backup", "alternate", "successor", "secondary"))
        ]
        primary = [k for k in found if k not in qualified]
        if qualified_ctx and qualified:
            found = qualified
        elif not qualified_ctx and primary:
            found = primary
        return found

    candidates = _candidates_for(assistant_reply)
    if len(candidates) != 1:
        # Second channel: the user's own words name the role — vital when
        # turns drift out of alignment and the last assistant message was
        # about another item entirely.
        candidates = _candidates_for(user_message)
    if len(candidates) != 1:
        return None
    asked_key = candidates[0]

    name = _extract_person_name(user_message)
    if name is None:
        return None

    return {
        "question_key": asked_key,
        "value": name,
        # Below the 0.9 auto-confirm threshold ON PURPOSE: a deterministic
        # guess is always human-reviewed, never silently written.
        "confidence": 0.80,
        "source_snippet": user_message.strip()[:500],
    }


def _deterministic_text_proposal(
    *,
    user_message: str,
    assistant_reply: str,
    question_sets: dict[str, dict[str, Any]],
    existing_proposal_keys: set[str],
) -> dict[str, Any] | None:
    """Deterministic fallback for text/notes questions: for these
    types the user's answer IS the value (an address, a residuary note).
    Select types are deliberately excluded — their values are pack
    option codes, and raw prose stored as a select answer corrupts the
    facts model while looking "answered".
    They previously had no fallback at all — when the model skipped its
    tool call, the answer vanished and progress froze (2026-10-24 E2E).
    Fires only for exactly one undecided matching question, and never
    for a question back, a greeting, or a one-word reply.
    """
    import re

    from app.domains.intake import question_sets as qs

    message = user_message.strip()
    if len(message) < 3 or message.endswith("?"):
        return None
    if message.lower().rstrip(".!") in {
        "hello", "hi", "hey", "thanks", "thank you", "ok", "okay", "yes", "no",
    }:
        return None

    def _candidates(context_lower: str) -> list[str]:
        found: list[str] = []
        for _, _, question in qs.iter_questions(question_sets):
            if question.get("type") not in {"text", "notes"}:
                continue
            key = str(question["key"])
            if key in existing_proposal_keys:
                continue
            label = str(question.get("label") or "")
            label_words = [
                w for w in re.findall(r"\w+", label.lower())
                if len(w) > 3 and w not in {"what", "does", "your", "with", "have", "this", "that"}
            ]
            if not label_words:
                continue
            matches = sum(1 for w in label_words if w in context_lower)
            if matches >= 2 and matches >= len(label_words) * 0.5:
                found.append(key)
        return found

    candidates = _candidates(assistant_reply.lower())
    from_assistant = len(candidates) == 1
    if not from_assistant:
        candidates = _candidates(user_message.lower())
    if len(candidates) != 1:
        return None
    if not from_assistant and len(message.split()) > 40:
        # Channel 2 (the message names the topic) with a LONG message is
        # a bundled v2 paragraph, not a single drifted answer: storing
        # the whole paragraph as one field's value is garbage (Part 3
        # live run: poa_agent_address = the entire identity paragraph).
        # The model owns bundled extraction; the fallback abstains.
        return None

    return {
        "question_key": candidates[0],
        "value": message[:2000],
        # Below the 0.9 auto-confirm threshold ON PURPOSE: human-reviewed.
        "confidence": 0.80,
        "source_snippet": message[:500],
    }


def _answers_map(conn: Any, matter_id: UUID) -> dict[str, Any]:
    return {
        str(row["question_key"]): row["answer"] for row in intake_repo.list_answers(conn, matter_id)
    }


def _progress(pack: LoadedPack, answers: dict[str, Any]) -> tuple[float, list[str], int, int]:
    per_set, completeness, missing, _pruned = qs.compute_progress(pack.question_sets, answers)
    total_required = sum(p.required_total for p in per_set.values())
    answered_required = sum(p.required_answered for p in per_set.values())
    return completeness, missing, total_required, answered_required


# Interview v2 cluster order (spec §Interview flow): the interview walks
# these in order; within a cluster the model asks ONE bundled question.
CLUSTER_ORDER: tuple[str, ...] = (
    "identity", "children", "money", "wishes", "people", "agents", "trust", "final",
)


def _gate_keys_for_pending(
    question_sets: Any, pending_keys: set[str]
) -> set[str]:
    """Question keys referenced by the applies_if expressions that keep
    `pending_keys` pending (their gates)."""
    exprs: list[str] = []
    sets = (
        question_sets.values()
        if isinstance(question_sets, dict)
        else question_sets
    )
    for question_set in sets:
        for section in question_set.get("sections") or []:
            section_expr = section.get("applies_if")
            for question in section.get("questions") or []:
                if str(question.get("key")) in pending_keys:
                    if isinstance(section_expr, str):
                        exprs.append(section_expr)
                    question_expr = question.get("applies_if")
                    if isinstance(question_expr, str):
                        exprs.append(question_expr)
    gates: set[str] = set()
    for expr in exprs:
        for kind, text in qs._tokenize_applies(expr):
            if kind == "word" and text not in qs._APPLIES_KEYWORDS:
                gates.add(text)
    return gates


def next_gap_cluster(
    question_sets: Any, answers: dict[str, Any]
) -> dict[str, Any] | None:
    """The first cluster (in CLUSTER_ORDER) with unanswered work.

    `question_sets` is the LoadedPack mapping (set id -> set dict), as
    accepted by partition_questions / iter_questions.

    Work = applicable-missing REQUIRED questions, plus unanswered gate
    questions that pending content waits on (the document-choice gates —
    without them the POA/healthcare sections can never become applicable).
    Pruned questions never appear. Returns
    {"cluster": name, "questions": [{"key", "label", "type"}]} or None when
    every applicable required question is answered.
    """
    applicable, pending, _pruned = qs.partition_questions(question_sets, answers)
    by_cluster: dict[str, list[dict[str, str]]] = {}
    added: set[str] = set()

    def _add(question: dict[str, Any]) -> None:
        key = str(question.get("key"))
        cluster = str(question.get("cluster") or "")
        if cluster not in CLUSTER_ORDER or key in added:
            return
        if qs.is_answered(answers.get(key)):
            return
        added.add(key)
        by_cluster.setdefault(cluster, []).append(
            {
                "key": key,
                "label": str(question.get("label") or key),
                "type": str(question.get("type") or "text"),
            }
        )

    for _set_id, _section_id, question in applicable:
        if question.get("required"):
            _add(question)
    if pending:
        pending_keys = {str(q.get("key")) for _s, _sec, q in pending}
        applicable_keys = {str(q.get("key")) for _s, _sec, q in applicable}
        by_key = {
            str(q.get("key")): q for _s, _sec, q in qs.iter_questions(question_sets)
        }
        for gate_key in _gate_keys_for_pending(question_sets, pending_keys):
            gate_question = by_key.get(gate_key)
            if gate_question is not None and gate_key in applicable_keys:
                _add(gate_question)
    for cluster in CLUSTER_ORDER:
        if by_cluster.get(cluster):
            return {"cluster": cluster, "questions": by_cluster[cluster]}
    return None


def _v2_enabled(conn: Any, *, firm_id: UUID, pack: LoadedPack) -> bool:
    """The interview_v2 flag for this firm + pack. Fail-safe: a flag-read
    error must never break the turn — the interview falls back to the
    proven v5 path (same best-effort posture as the router's
    auto-confirm)."""
    try:
        return bool(
            feature_flags.is_enabled(
                conn, "interview_v2", firm_id=firm_id, pack_id=pack.manifest.pack_id
            )
        )
    except Exception:
        logger.warning(
            "interview_v2 flag read failed; falling back to the v5 prompt path",
            exc_info=True,
        )
        return False


def _cluster_gap_for_turn(
    conn: Any, *, firm_id: UUID, pack: LoadedPack, answers: dict[str, Any]
) -> dict[str, Any] | None:
    """Cluster gap for this turn when the interview_v2 flag is on for the
    matter's firm + pack; None (the v5 path) otherwise."""
    if not _v2_enabled(conn, firm_id=firm_id, pack=pack):
        return None
    return next_gap_cluster(pack.question_sets, answers)


# --- Role bundles (Interview v2, Part 3) -------------------------------------
# One person named for one role is offered for the sibling roles as a
# single grouped yes/no. The fill happens through server-created proposals
# (source "bundle", confidence 1.0) on a deterministic YES — the model
# never guesses a nomination (spec: reuse is a confirmation, never an
# assumption).
PRIMARY_BUNDLE: tuple[str, ...] = (
    "primary_executor_person",
    "poa_agent_person",
    "healthcare_agent_person",
)
BACKUP_BUNDLE: tuple[str, ...] = (
    "backup_executor_person",
    "successor_poa_agent_person",
    "successor_healthcare_agent_person",
    "successor_trustee_person",
)
_ROLE_BUNDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("primary", PRIMARY_BUNDLE),
    ("backup", BACKUP_BUNDLE),
)
ADDRESS_BUNDLE_KEYS: tuple[str, ...] = (
    "primary_executor_address",
    "poa_agent_address",
    "primary_guardian_address",
)
#: An offer is presented at most twice; after that the cluster flow asks
#: the remaining roles individually (a defer is not a decline). A
#: "presentation" is counted when an offer is placed into a turn's prompt;
#: if that turn's model call then fails, the count is still spent (the
#: cap is an approximation, biased toward asking individually sooner).
MAX_BUNDLE_OFFERS = 2


def pending_bundle_offer(
    question_sets: Any,
    answers: dict[str, Any],
    *,
    declined: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """The pending bundle offer for this answer state, or None.

    Role bundles: exactly one member answered (the anchor) and at least
    one member unanswered AND applicable — pruned members are never fill
    targets. Address bundle: the testator's own address trio is answered
    and at least one role address is missing and applicable. Declined offers (by decline_key) never reappear. Decline keys are
    "<bundle>:<anchor name lower>" for role bundles and
    "address:<composed address lower>" for the address bundle.
    """
    applicable, _pending, _pruned = qs.partition_questions(question_sets, answers)
    applicable_keys = {str(q.get("key")) for _s, _sec, q in applicable}

    for bundle_name, members in _ROLE_BUNDLES:
        answered_members = [
            k for k in members if qs.is_answered(answers.get(k))
        ]
        if len(answered_members) != 1:
            continue
        anchor_key = answered_members[0]
        anchor_name = answers.get(anchor_key)
        if not isinstance(anchor_name, str) or not anchor_name.strip():
            continue
        anchor_name = anchor_name.strip()
        decline_key = f"{bundle_name}:{anchor_name.lower()}"
        if decline_key in declined:
            continue
        fill_keys = [
            k
            for k in members
            if k != anchor_key
            and not qs.is_answered(answers.get(k))
            and k in applicable_keys
        ]
        if fill_keys:
            return {
                "bundle": bundle_name,
                "anchor_key": anchor_key,
                "anchor_name": anchor_name,
                "fill_keys": fill_keys,
                "fill_value": anchor_name,
                "decline_key": decline_key,
            }

    street = answers.get("testator_street_address")
    city = answers.get("testator_city")
    state = answers.get("primary_residence_state")
    if all(isinstance(v, str) and v.strip() for v in (street, city, state)):
        composed = f"{street.strip()}, {city.strip()}, {state.strip()}"
        decline_key = f"address:{composed.lower()}"
        if decline_key not in declined:
            fill_keys = [
                k
                for k in ADDRESS_BUNDLE_KEYS
                if not qs.is_answered(answers.get(k)) and k in applicable_keys
            ]
            if fill_keys:
                return {
                    "bundle": "address",
                    "anchor_key": "testator_street_address",
                    "anchor_name": composed,
                    "fill_keys": fill_keys,
                    "fill_value": composed,
                    "decline_key": decline_key,
                }
    return None


def _bundle_offer_for_turn(
    conn: Any,
    *,
    session_id: UUID,
    pack: LoadedPack,
    answers: dict[str, Any],
    question_labels: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The offer to present this turn + the session state (mutated when an
    offer is presented, so the caller can persist it).

    Suppression: declined offers never return; an offer already presented
    MAX_BUNDLE_OFFERS times without resolution stops being offered (the
    cluster flow then asks the roles one by one)."""
    try:
        state = repo.get_session_state(conn, session_id)
    except Exception:
        logger.warning("bundle state read failed", exc_info=True)
        state = {}
    declined = set(state.get("bundles_declined") or [])
    offer = pending_bundle_offer(pack.question_sets, answers, declined=declined)
    if offer is None:
        return None, state
    offered = dict(state.get("bundles_offered") or {})
    if int(offered.get(offer["decline_key"], 0)) >= MAX_BUNDLE_OFFERS:
        return None, state
    offered[offer["decline_key"]] = int(offered.get(offer["decline_key"], 0)) + 1
    state["bundles_offered"] = offered
    offer["fill_labels"] = [
        question_labels.get(k, k) for k in offer["fill_keys"]
    ]
    return offer, state


def _signed_in_name(conn: Any, household_id: Any) -> str | None:
    """Full name of the household's primary client (their profile name),
    for the identity confirmation opener. Best-effort."""
    if household_id is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.display_name, p.first_name, p.last_name
                FROM household_members hm
                JOIN profiles p ON p.id = hm.user_id
                WHERE hm.household_id = %s
                  AND hm.relationship_role = 'primary_client'
                LIMIT 1
                """,
                (str(household_id),),
            )
            row = cur.fetchone()
        if row:
            display = str(row.get("display_name") or "").strip()
            if display:
                return display
            composed = " ".join(
                part
                for part in (
                    str(row.get("first_name") or "").strip(),
                    str(row.get("last_name") or "").strip(),
                )
                if part
            )
            if composed:
                return composed
    except Exception:
        logger.warning("signed-in name lookup failed", exc_info=True)
    return None


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
    _check_turn_rate_limit(conn, actor_id)

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
    # Keys with open proposals awaiting user confirmation — the model should
    # NOT re-ask about these; instead acknowledge they're pending review.
    pending_keys = sorted(repo.open_proposal_keys(conn, session_id))
    # Human labels for every pack question key, so provider fallbacks can ask
    # the real question text instead of deriving phrasing from raw key names.
    question_labels = {
        str(question.get("key")): str(question.get("label") or question.get("key"))
        for _, _, question in qs.iter_questions(pack.question_sets)
    }
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
        pending_proposal_keys=pending_keys,
        question_labels=question_labels,
    )
    history = _history_messages(conn, session_id)
    # Interview v2 (flag on): a pending bundle offer outranks the cluster
    # gap; otherwise the prompt focuses on ONE cluster's gap. Flag off:
    # the v5 path runs byte-for-byte (including its trace label).
    cluster_gap = None
    bundle_offer = None
    bundle_state: dict[str, Any] = {}
    signed_in_name = None
    if _v2_enabled(conn, firm_id=firm_id, pack=pack):
        bundle_offer, bundle_state = _bundle_offer_for_turn(
            conn,
            session_id=session_id,
            pack=pack,
            answers=answers,
            question_labels=question_labels,
        )
        if bundle_offer is not None:
            try:
                repo.update_session_state(conn, session_id, bundle_state)
            except Exception:
                logger.warning("bundle state persist failed", exc_info=True)
        else:
            cluster_gap = next_gap_cluster(pack.question_sets, answers)
            if cluster_gap is not None and cluster_gap["cluster"] == "identity":
                signed_in_name = _signed_in_name(
                    conn, scope.matter.get("household_id")
                )
    prompt_version = (
        prompts.PROMPT_TEMPLATE_VERSION
        if (cluster_gap is not None or bundle_offer is not None)
        else prompts.PROMPT_TEMPLATE_VERSION_V5
    )
    system_prompt = prompts.build_system_prompt(
        pack_id=pack.manifest.pack_id,
        jurisdiction_code=pack.manifest.jurisdiction_code,
        missing_required_keys=missing,
        answered_required=answered_required,
        total_required=total_required,
        pending_proposal_keys=pending_keys,
        answered_keys=sorted(answers.keys()),
        cluster_gap=cluster_gap,
        bundle_offer=bundle_offer,
        signed_in_name=signed_in_name,
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

    # Deterministic fallbacks (loop prevention): if the model asked a
    # boolean or person-name question and the user gave a clear answer, but
    # the model emitted no proposal for that specific key, record it
    # deterministically instead of re-asking. We look at the PREVIOUS
    # assistant message (history[-1]) to find what was asked, since
    # response.reply is the current turn's acknowledgement.
    if history:
        last_assistant_msg = next(
            (m.content for m in reversed(history) if m.role == "assistant"),
            "",
        )
        existing_keys = set(pending_keys)
        # Also include keys from valid_proposals (they'll become pending).
        proposed_keys = {p.question_key for p in valid_proposals}
        # Answered and already-decided keys are covered too: re-proposing
        # them is what made the interview loop and resurrected rejected
        # fabrications in the 2026-10-24 E2E run.
        decided_keys = repo.decided_proposal_keys(conn, session_id)
        all_covered = existing_keys | proposed_keys | set(answers.keys()) | decided_keys

        # Try boolean fallback first, then person-name, then text/notes.
        for fallback_fn in (
            _deterministic_boolean_proposal,
            _deterministic_person_proposal,
            _deterministic_text_proposal,
        ):
            fallback = fallback_fn(
                user_message=message,
                assistant_reply=last_assistant_msg,
                question_sets=pack.question_sets,
                existing_proposal_keys=all_covered,
            )
            if fallback is not None:
                from app.domains.ai.provider import FieldProposalDraft

                valid_proposals.append(
                    FieldProposalDraft(
                        question_key=fallback["question_key"],
                        value=fallback["value"],
                        confidence=fallback["confidence"],
                        source_snippet=fallback["source_snippet"],
                    )
                )
                logger.info(
                    "Deterministic fallback: %r=%r (session %s)",
                    fallback["question_key"],
                    fallback["value"],
                    session_id,
                )
                # A single message can carry several facts ("No, and my
                # brother Amit Sharma is the backup executor") — let the
                # remaining fallbacks run for OTHER keys instead of
                # stopping at the first hit.
                all_covered.add(fallback["question_key"])

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
        prompt_version=prompt_version,
        pack_version=version,
        tokens_in=response.usage.get("prompt_tokens"),
        tokens_out=response.usage.get("completion_tokens"),
        latency_ms=latency_ms,
        retrieved_doc_ids=[d.content_id for d in retrieved],
    )

    stored_proposals: list[ProposalOut] = []
    # Reuse the pending keys already fetched at the start of post_turn
    # instead of re-querying inside the loop (N+1).
    pending_key_set = set(pending_keys)
    for draft in valid_proposals:
        # One open proposal per (session, key): skip when the user already has
        # an undecided proposal for this key.
        if draft.question_key in pending_key_set:
            continue
        pending_key_set.add(draft.question_key)
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
            prompt_version=prompt_version,
            pack_version=version,
        )
        proposal_out = _proposal_out(row)
        # Auto-confirm happens in the router AFTER the turn transaction
        # commits, in a separate transaction with a statement timeout.
        # (Doing it here hung the turn — see ADR.)
        stored_proposals.append(proposal_out)

    # --- Role bundles: the user's reply to a pending offer is resolved
    # deterministically. YES → server proposals (source "bundle",
    # confidence 1.0) fill the sibling roles through the normal
    # confirm/auto-confirm path. NO → the decline is recorded and the
    # offer never repeats for that anchor. Anything else defers.
    if bundle_offer is not None:
        verdict = _parse_yes_no(message)
        if verdict is True:
            # Fresh read at fill time (review I-1): never stack a bundle
            # proposal on top of an already-open proposal for the same
            # key — the turn-start snapshot in pending_key_set can miss
            # proposals stored by other paths since.
            open_now = repo.open_proposal_keys(conn, session_id)
            for fill_key in bundle_offer["fill_keys"]:
                if (
                    fill_key in pending_key_set
                    or fill_key in open_now
                    or qs.is_answered(answers.get(fill_key))
                ):
                    continue
                row = repo.insert_proposal(
                    conn,
                    session_id=session_id,
                    turn_id=UUID(str(assistant_turn["id"])),
                    firm_id=firm_id,
                    matter_id=matter_id,
                    question_key=fill_key,
                    proposed_value=bundle_offer["fill_value"],
                    source="bundle",
                    confidence=1.0,
                    source_snippet=(
                        f"Confirmed via the {bundle_offer['bundle']} bundle "
                        f"offer (anchor: {bundle_offer['anchor_key']})"
                    ),
                    model_id="server_bundle",
                    prompt_version=prompt_version,
                    pack_version=version,
                )
                stored_proposals.append(_proposal_out(row))
                pending_key_set.add(fill_key)
        elif verdict is False:
            declined = list(bundle_state.get("bundles_declined") or [])
            if bundle_offer["decline_key"] not in declined:
                declined.append(bundle_offer["decline_key"])
            bundle_state["bundles_declined"] = declined
            try:
                repo.update_session_state(conn, session_id, bundle_state)
            except Exception:
                logger.warning("bundle decline persist failed", exc_info=True)

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
    # Use context-aware refusal: medical/other non-legal questions get the
    # out-of-scope message, not the legal-advice one.
    refusal_message = (
        _OUT_OF_SCOPE_REFUSAL if _is_non_legal_advice(message) else _REFUSAL_MESSAGE
    )
    # Only escalate to attorney for actual legal advice requests.
    if not _is_non_legal_advice(message):
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
        text=refusal_message,
    )
    _record_turn_audit(
        conn,
        actor_id=actor_id,
        firm_id=firm_id,
        matter_id=matter_id,
        session_id=session_id,
        user_summary=_summarize(message),
        reply_summary=_summarize(refusal_message),
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
        reply=refusal_message,
        citations=[],
        proposals=[],
        refused=True,
        escalated=not _is_non_legal_advice(message),
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
    was_refused = reply in (_REFUSAL_MESSAGE, _OUT_OF_SCOPE_REFUSAL)
    proposals = [
        ProposalDraftOut(
            id=UUID(str(row["id"])),
            question_key=str(row["question_key"]),
            proposed_value=row["proposed_value"],
            source=str(row["source"]),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            source_snippet=row["source_snippet"],
            # Carry the row's REAL status: defaulting to "proposed" made
            # the router's auto-confirm resurrect rejected/corrected
            # values into intake answers on idempotent replays.
            status=str(row["status"]),
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
