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
    # Split WITH separators retained: the borrow below may only cross
    # a comma inside the same sentence, never a sentence boundary.
    parts = re.split(r"([.!?;,]|\bbut\b|\band\b)", user_message.lower())
    clauses = parts[0::2]
    separators = parts[1::2]  # separators[i] precedes clauses[i + 1]
    topic = [
        (i, c)
        for i, c in enumerate(clauses)
        if sum(1 for w in label_words if w in c) >= 2
    ]
    if len(topic) > 1:
        # Compound labels spread one topic over several clauses ("no
        # prior marriages, and no children outside..."): propose only
        # when every topic clause that parses agrees (unanimous);
        # mixed polarity is a genuinely bundled answer — abstain.
        parsed = set()
        for _i, clause in topic:
            clause_value = _parse_yes_no(clause)
            if clause_value is not None:
                parsed.add(clause_value)
        if len(parsed) != 1:
            return None
        value = parsed.pop()
    elif len(topic) == 1:
        idx, topic_clause = topic[0]
        value = _parse_yes_no(topic_clause)
        # The separator chain between the polarity fragment and the
        # topic clause must be commas/semicolons/"and" only (the
        # natural "Yes, and ..." shape produces an empty fragment
        # between "," and "and" — walk back over empties). A period
        # or other sentence boundary anywhere in the chain blocks
        # the borrow.
        k = idx - 1
        while k >= 0 and not clauses[k].strip():
            k -= 1
        chain_ok = (
            k >= 0
            and all(s in {",", ";", "and"} for s in separators[k:idx])
            and len(separators[k:idx]) >= 1
        )
        if value is None and chain_ok:
            # "Yes, I expect some disagreement." — the comma split
            # strands the polarity word in the preceding fragment.
            # Borrow it ONLY when that fragment is genuinely
            # polarity-only (nothing left after removing polarity /
            # pronoun / aux tokens): "no business interests" carries
            # another topic's noun and must never lend its polarity
            # across topics (fallback review I-1).
            prev = clauses[k].strip()
            remainder = [
                t
                for t in prev.split()
                if t
                not in {
                    "yes", "no", "yeah", "nope", "yep", "i", "we",
                    "do", "don't", "not", "never",
                }
            ]
            if prev and not remainder and _parse_yes_no(prev) is not None:
                value = _parse_yes_no(f"{prev} {topic_clause}")
        if value is None:
            return None
    else:
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
    allow_bundled: bool = False,
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
    if not from_assistant and not allow_bundled and len(message.split()) > 25:
        # Channel 2 (the message names the topic) with a LONG message is
        # a bundled v2 paragraph, not a single drifted answer: storing
        # the whole paragraph as one field's value is garbage (Part 3
        # live run: poa_agent_address = the entire identity paragraph).
        # The model owns bundled extraction; the fallback abstains.
        # allow_bundled is set ONLY by the G3 completeness sweep, which
        # runs scoped to a single focus field the server itself asked
        # about and the model proposed nothing for — the one case where
        # a paragraph value at 0.80 beats silently losing the answer.
        return None

    return {
        "question_key": candidates[0],
        "value": message[:2000],
        # Below the 0.9 auto-confirm threshold ON PURPOSE: human-reviewed.
        "confidence": 0.80,
        "source_snippet": message[:500],
    }


def _is_whole_message_copy(
    *,
    question_type: str,
    value: Any,
    user_message: str,
    last_assistant_text: str,
    question_label: str,
    prior_assistant_text: str = "",
    asked_keys: frozenset[str] | set[str] = frozenset(),
    question_key: str = "",
    focus_authoritative: bool = False,
) -> bool:
    """True when a text/notes proposal merely copies the user's whole
    (long, multi-topic) message into a field the assistant did not just
    ask about — the model's laziest failure mode, and a poison card: the
    open proposal blocks its key for the rest of the interview (A/B
    run 1: the guardian paragraph became trust_marital_share). A genuine
    single-topic answer the model faithfully copies is kept: its key WAS
    the asked question.

    With focus_authoritative (v2 turns), the asked-test is the server's
    persisted focus window and NOTHING else: no label-word archaeology
    and no length exemption. The A/B showed why — run 4's marital-share
    copy was saved by label "drift" against a summary reply that merely
    mentioned spouse/trust/share, and the confirmation run's
    distribution-age copy was a 24-word message that escaped through
    the <=25-word cap below (extraction guards G2, 2026-10-24)."""
    import re

    if question_type not in {"text", "notes"}:
        return False
    if not isinstance(value, str):
        return False

    def _norm(s: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    msg_norm = _norm(user_message)
    val_norm = _norm(value)
    if not val_norm or len(val_norm) < 0.9 * len(msg_norm):
        return False
    if val_norm not in msg_norm and msg_norm not in val_norm:
        # Near-copies escape strict containment with a single edited
        # word (trusted -> dependable); token similarity catches the
        # same poison shape (guard review I-4).
        import difflib

        if difflib.SequenceMatcher(None, val_norm, msg_norm).ratio() < 0.88:
            return False
    if focus_authoritative:
        # The value IS (nearly) the whole message. On a v2 turn the
        # only question is whether the server asked about this key in
        # the last two turns (the persisted focus window).
        if question_key and question_key in asked_keys:
            return False
        # A 1-2 word message is a direct answer to SOMETHING; dropping
        # it risks more than the paragraph-scale copies this guard
        # exists for.
        return len(msg_norm.split()) >= 3
    if len(user_message.split()) <= 25:
        return False
    # The server KNOWS what it asked last turn (the persisted focus):
    # membership beats prose archaeology (guard review I-3).
    if question_key and question_key in asked_keys:
        return False
    # Distinct label words only (a label repeating "treatment" must
    # not count one word twice), matched on TOKEN boundaries (share
    # must not match "shares") — parity with the deterministic
    # fallbacks' attribution bar (guard review I-2).
    label_words = {
        w
        for w in re.findall(r"\w+", question_label.lower())
        if len(w) > 3
        and w not in {
            "what", "does", "your", "with", "have", "this", "that",
            "should", "would", "could", "their", "about", "under",
            "until", "since", "while", "being", "after", "before",
            "which", "there", "these", "those", "other",
        }
    }

    def _matched(text: str) -> set[str]:
        tokens = set(re.findall(r"\w+", text.lower()))
        return label_words & tokens

    matched = _matched(last_assistant_text)
    # "Asked" needs real topical overlap: at least two DISTINCT label
    # words covering at least half the label's content words — the
    # same >=2-and->=50% bar the fallbacks use, so an acknowledgment
    # sentence about the previous topic cannot fake an ask.
    asked = len(matched) >= 2 and len(matched) >= len(label_words) * 0.5
    if not asked and prior_assistant_text:
        # Drift answers: the user answers the question from TWO turns
        # ago with a long faithful text while the last assistant
        # message already moved on. Treat the key as asked only on
        # STRONG evidence in the older message — two distinctive
        # (6+ letters) distinct label words.
        prior_matched = {
            w for w in _matched(prior_assistant_text) if len(w) >= 6
        }
        asked = len(prior_matched) >= 2
    return not asked


# --- Deterministic extraction guards (Interview v2, A/B follow-up) --------
# The 2026-10-24 A/B (runs 1-7) pinned the remaining failure classes to
# model extraction variance, not the deterministic machinery:
#   * successor slots filled with the PRIMARY person's name, copied from
#     context (run 4: both successors = "Priya Sharma" at a turn whose
#     message named nobody; confirmation run: successor_trustee =
#     "Priya Sharma" from the executor paragraph);
#   * short values asserted for facts the current message never states
#     (run 4: trust_initial_property = "All assets" from the money
#     paragraph);
#   * focus fields the model simply skipped, forcing verbatim re-sends
#     (run 4 turns 4 and 11 stored zero proposals).
# G1/G1c ground model proposals in the CURRENT user message — prior
# turns are context, not evidence — and G3 re-runs the existing
# fallbacks scoped to one focus field. Bundle-verdict fills are
# server-generated (source "bundle", inserted directly by post_turn)
# and never pass through these guards: the user's YES is their
# grounding.


def _norm_tokens(text: str) -> list[str]:
    import re

    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _person_name_grounded(value: str, user_message: str) -> bool:
    """Every name token of the proposed value appears in the current
    user message (order-free, so "Sharma, Priya" and parenthetical
    re-statements still ground)."""
    name_tokens = [t for t in _norm_tokens(value) if len(t) >= 2]
    if not name_tokens:
        return False
    message_tokens = set(_norm_tokens(user_message))
    return all(t in message_tokens for t in name_tokens)


#: Person fields that name a BACKUP for another role. Reuse across
#: roles is a confirmation (the bundle verdict), never an inference —
#: a model proposal for one of these keys needs the current message to
#: pair the name with successor wording (or be a bare-name answer).
SUCCESSOR_PERSON_KEYS: frozenset[str] = frozenset({
    "backup_guardian_person",
    "backup_executor_person",
    "successor_trustee_person",
    "successor_poa_agent_person",
    "successor_healthcare_agent_person",
})
_SUCCESSOR_SIGNAL_TOKENS: frozenset[str] = frozenset({
    "successor", "backup", "alternate", "alternative", "secondary",
    "contingent", "contingency", "replacement",
})
_SUCCESSOR_TAKEOVER_PHRASES: tuple[str, ...] = (
    "take over", "step in", "step up", "fill in",
)


def _clause_has_successor_signal(clause: str) -> bool:
    clause_tokens = set(_norm_tokens(clause))
    if clause_tokens & _SUCCESSOR_SIGNAL_TOKENS:
        return True
    normalized_clause = " ".join(_norm_tokens(clause))
    return any(p in normalized_clause for p in _SUCCESSOR_TAKEOVER_PHRASES)


def _split_clauses(sentence: str) -> list[str]:
    """Clause boundaries for pairing discipline: commas, semicolons,
    colons, dashes, and the coordinating conjunctions. Colons and
    dashes segment role lists ("Roles: Maya Lee is my healthcare
    agent: David Lee is the successor …") exactly like commas do
    (review round 2, I-3)."""
    import re

    return re.split(r"[,;:—–]|\s+-\s+|\band\b|\bbut\b", sentence)


#: A successor condition stated about the TESTATOR ("… should manage
#: the trust if I cannot …") pairs the name with successor semantics
#: as surely as the word "successor" does — the mandate's own
#: "if … cannot/unable" pattern. Restricted to first-person subjects:
#: a conditional about the PRIMARY ("if she can't serve") is the
#: incumbent's clause, not the proposed name's licence.
_FIRST_PERSON_CONDITIONAL = (
    r"\bif\s+(i|we)\s+(am\s+|become\s+)?"
    r"(can\s+t|cannot|can\s+not|unable|incapacitated|no\s+longer)\b"
    r"|\bwhen\s+(i|we)\s+can\s+no\s+longer\b"
)


def _sentences(text: str) -> list[str]:
    """Sentence split that does not amputate a clause at a middle
    initial ("Priya K. Sharma" is one name, not two sentences —
    review round 3, I-C)."""
    import re

    protected = re.sub(r"\b([A-Z])\.", r"\1", text)
    return re.split(r"[.!?]+", protected)


#: Finite-verb tokens: an appositive FRAGMENT has none. A clause
#: carrying one is a predication about somebody, not a parenthetical
#: about the name next to it.
_FRAGMENT_VERBS: frozenset[str] = frozenset({
    "is", "was", "are", "were", "be", "been", "has", "have", "had",
    "should", "would", "will", "shall", "must",
})
_NAME_FILLER_TOKENS: frozenset[str] = frozenset({
    "my", "the", "a", "an", "as", "is",
})
#: Lead-ins that may precede the name in its clause without making
#: the clause a predication about someone else ("I want Neha
#: Kapoor, as the backup guardian, …" — review round 4, C-B minor).
_NAME_LEADIN_TOKENS: frozenset[str] = frozenset({
    "i", "want", "wish", "hope", "choose", "chose", "pick", "picked",
    "select",
})


def _appositive_signal_present(sentence: str, name_seq: list[str]) -> bool:
    """Successor wording in an APPOSITIVE fragment adjacent to the
    name's clause ("Neha Kapoor, as the backup guardian, …"; "My
    backup, Amit Sharma, …"; "As backup guardian, Neha Kapoor …").
    A fragment licenses only when it is a parenthetical, not a
    predication: it carries successor signal, NO finite verb, and NO
    other person's name span. Direction discipline: a FOLLOWING
    fragment licenses only a bare-name clause; a PRECEDING fragment
    licenses only a clause that starts with the name. Anything else
    is two clauses about two people — the word-window version of
    this check crossed exactly those boundaries (semicolons, colons,
    spaced hyphens, inverted comma order) and re-licensed the
    incumbent (review round 3, C-B)."""
    name_tokens = set(name_seq)
    if not name_tokens:
        return False
    clauses = _split_clauses(sentence)
    clause_tokens = [set(_norm_tokens(c)) for c in clauses]
    for idx, tokens in enumerate(clause_tokens):
        if not name_tokens <= tokens:
            continue
        remainder = (
            tokens - name_tokens - _NAME_FILLER_TOKENS - _NAME_LEADIN_TOKENS
        )
        # The name's position: everything before it in the clause
        # must be filler or a lead-in ("I want Neha Kapoor …").
        clause_seq = _norm_tokens(clauses[idx])
        try:
            start = next(
                i
                for i in range(len(clause_seq) - len(name_seq) + 1)
                if clause_seq[i : i + len(name_seq)] == name_seq
            )
        except StopIteration:
            continue
        prefix_ok = all(
            t in _NAME_FILLER_TOKENS or t in _NAME_LEADIN_TOKENS
            for t in clause_seq[:start]
        )
        for neighbor_idx, following in ((idx - 1, False), (idx + 1, True)):
            if not (0 <= neighbor_idx < len(clauses)):
                continue
            fragment = clauses[neighbor_idx]
            if not _clause_has_successor_signal(fragment):
                continue
            if clause_tokens[neighbor_idx] & _FRAGMENT_VERBS:
                continue
            if _clause_has_name_span(fragment):
                continue
            if following and remainder:
                continue
            if not following and not (prefix_ok or not remainder):
                continue
            return True
    return False


def _successor_signal_present(
    value: str,
    user_message: str,
    *,
    first_person_conditional_ok: bool = False,
) -> bool:
    """A CLAUSE containing the proposed name also carries successor
    wording — the pairing discipline the deterministic person fallback
    uses for conditional nominations. Clause scope (split on commas,
    semicolons, colons, dashes, "and"/"but") is load-bearing: a
    sentence naming BOTH the incumbent and the successor ("my agent is
    Maya Lee and the successor agent is David Lee") must license
    DAVID only — the sentence-level version licensed a successor copy
    of the incumbent, whose rejection made the key decided, starved
    the bundle fill, and deadlocked a live A/B run (set 1, Pruned run
    1). Pronoun pairings don't count either: "If she can't serve,
    Amit is the backup" pairs the signal with AMIT's clause, not the
    incumbent. Widenings, each bounded: an APPOSITIVE fragment
    adjacent to the name's clause licenses (review round 2, I-2; the
    clause-adjacency form replaced a word window that leaked across
    list punctuation — round 3, C-B); and a first-person conditional
    in the name's clause ("… manage the trust if I cannot") is
    successor wording ONLY where the caller says the testator is the
    incumbent (the trustee) — for agent roles, incapacity wording
    defines the PRIMARY's activation, not a successor's (round 3,
    I-A)."""
    name_tokens = {t for t in _norm_tokens(value) if len(t) >= 2}
    if not name_tokens:
        return False
    import re

    name_seq = [t for t in _norm_tokens(value) if len(t) >= 2]
    for sentence in _sentences(user_message):
        for clause in _split_clauses(sentence):
            if not name_tokens <= set(_norm_tokens(clause)):
                continue
            if _clause_has_successor_signal(clause):
                return True
            if first_person_conditional_ok and re.search(
                _FIRST_PERSON_CONDITIONAL, " ".join(_norm_tokens(clause))
            ):
                return True
        if _appositive_signal_present(sentence, name_seq):
            return True
    return False


#: Layperson vocabulary for each role word in a person key (review
#: round 2, I-1): users say "medical decisions", "power of attorney",
#: "settle my estate", "manage the trust", "backup guardians" —
#: whole-token equality with the key's parts dropped every one of
#: those genuine nominations. Evidence is keyed by the role PART and
#: TIERED (round 4, I-E): STRONG words speak a role on their own;
#: WEAK words are ambiguous in isolation ("wishes", "little",
#: "doctors", "money" appear incidentally in other roles'
#: paragraphs) and count only in pairs or as part of a PHRASE
#: ("little ones", "my will", "set aside", "treatment decisions").
#: Unknown parts speak for themselves (strong).
_ROLE_EVIDENCE_STRONG: dict[str, frozenset[str]] = {
    "guardian": frozenset({
        "guardian", "guardians", "guardianship", "children", "kids",
        "child",
    }),
    "executor": frozenset({
        "executor", "executors", "executrix", "estate",
    }),
    "trustee": frozenset({"trustee", "trustees", "trust"}),
    "healthcare": frozenset({"healthcare", "health", "medical"}),
    "poa": frozenset({
        "poa", "power", "attorney", "financial", "finances",
    }),
    "agent": frozenset({"agent", "agents"}),
}
_ROLE_EVIDENCE_WEAK: dict[str, frozenset[str]] = {
    "guardian": frozenset({"little"}),
    "executor": frozenset({"wishes"}),
    "trustee": frozenset({"money"}),
    "healthcare": frozenset({"doctors", "treatment"}),
    "poa": frozenset({"money", "bills"}),
    "agent": frozenset(),
}
_ROLE_EVIDENCE_PHRASES: dict[str, tuple[str, ...]] = {
    "guardian": ("little ones",),
    "executor": ("my will",),
    "trustee": ("set aside",),
    "healthcare": ("treatment decisions", "medical decisions"),
    "poa": (),
    "agent": (),
}
_ROLE_PARTS: frozenset[str] = frozenset({
    "guardian", "executor", "trustee", "healthcare", "poa", "agent",
})


def _role_part_present(
    part: str, tokens: set[str], normalized: str
) -> bool:
    """Does the text speak this role part? Strong word, weak phrase,
    or two distinct weak words — a single weak word is scenery."""
    strong = _ROLE_EVIDENCE_STRONG.get(part)
    if strong is None:
        return part in tokens
    if strong & tokens:
        return True
    padded = f" {normalized} "
    if any(
        f" {phrase} " in padded
        for phrase in _ROLE_EVIDENCE_PHRASES.get(part, ())
    ):
        # Token boundaries are load-bearing: "my will" hides inside
        # "my willingness", "set aside" inside "set asides", and
        # "little ones" inside "little onesie" — a raw substring
        # match re-licenses exactly the incidental-mention smears
        # the tier exists to stop (final re-review).
        return True
    return len(_ROLE_EVIDENCE_WEAK.get(part, frozenset()) & tokens) >= 2


def _key_parts(question_key: str) -> set[str]:
    return set(question_key.split("_")) - {
        "primary", "backup", "successor", "person",
    }


def _key_role_present(
    question_key: str, tokens: set[str], normalized: str
) -> bool:
    return any(
        _role_part_present(part, tokens, normalized)
        for part in _key_parts(question_key)
    )


def _any_role_present(tokens: set[str], normalized: str) -> bool:
    return any(
        _role_part_present(part, tokens, normalized)
        for part in _ROLE_PARTS
    )


def _assistant_tail(assistant_context: str) -> str:
    """Only the TAIL of the assistant's last message may supply role
    context — the question it ends with. Assistant messages recap
    every role before asking; consulting the whole message would
    let one generic answer license every role the recap mentioned
    (review round 4, I-B residual). Lines are respected before
    sentences: a recap joined to its question by a bare newline has
    no sentence boundary at all (final re-review, Minor 1)."""
    lines = [
        line.strip() for line in assistant_context.splitlines()
        if line.strip()
    ]
    if not lines:
        return ""
    segments = [s.strip() for s in _sentences(lines[-1]) if s.strip()]
    return segments[-1] if segments else lines[-1]


def _person_role_mismatch(
    question_key: str, user_message: str, assistant_context: str = ""
) -> bool:
    """A fat paragraph that never speaks the key's role — in ANY of
    the words a layperson would use for it — cannot ground a
    nomination for it: the model is smearing a name across roles
    (A/B set 1, Sharma run 2: the guardian paragraph's nominee filed
    under primary_executor_person; the guardian paragraph contains
    no executor/estate word, and the executor paragraph no
    guardian/children word, so the smear still drops). Short
    messages are exempt: a direct answer's role comes from the
    question, not the answer. The same applies when the message
    speaks NO role family at all and the caller supplies the
    assistant's last message: the asked question supplies the role —
    but only THIS key's evidence is consulted there, so a smear into
    a role the question never named still drops (review round 3,
    I-B/I-E). A message that speaks a DIFFERENT role family is a
    smear regardless of context."""
    message_tokens = _norm_tokens(user_message)
    if len(message_tokens) <= 14:
        return False
    if not _key_parts(question_key):
        return False
    token_set = set(message_tokens)
    normalized = " ".join(message_tokens)
    if _key_role_present(question_key, token_set, normalized):
        return False
    if assistant_context and not _any_role_present(token_set, normalized):
        tail_tokens_list = _norm_tokens(_assistant_tail(assistant_context))
        return not _key_role_present(
            question_key,
            set(tail_tokens_list),
            " ".join(tail_tokens_list),
        )
    return True


_STREET_TOKENS: frozenset[str] = frozenset({
    "street", "avenue", "road", "drive", "lane", "boulevard", "court",
    "highway", "terrace", "circle", "parkway", "trail", "crossing",
    "place", "way", "alley", "plaza", "square", "loop",
    "st", "ave", "rd", "dr", "ln", "blvd", "ct", "hwy", "pl", "aly",
})


#: Words that break a capitalized pair's claim to be a PERSON span:
#: function words, street/place/institution vocabulary ("Wells
#: Fargo", "Main Street", "New York" are not nominees — review
#: round 2, I-6).
_SPAN_BLOCKED: frozenset[str] = frozenset({
    "the", "if", "my", "yes", "for", "and", "but", "she", "he",
    "we", "our", "their", "his", "her",
    "york", "jersey", "mexico", "carolina", "dakota", "hampshire",
    "island", "columbia", "wells", "fargo", "bank", "main", "north",
    "south", "east", "west", "united", "states", "america", "city",
    "town", "county", "beach",
    # Financial institutions an estate interview mentions as scenery
    # (review round 3, I-D — belt-and-braces behind the marker rule).
    "goldman", "sachs", "schwab", "chase", "vanguard", "fidelity",
    "merrill", "lynch", "stanley", "morgan",
}) | _STREET_TOKENS


def _clause_has_name_span(raw_clause: str) -> bool:
    """A plausible two-word person span. Middle initials ("Priya K.
    Sharma") and hyphenated names ("Mary-Kate Olsen") count (review
    round 2, I-5); place and institution pairs do not (I-6)."""
    names = 0
    for word in raw_clause.split():
        cleaned = word.strip(".,;:!?()\"“”'")
        parts = cleaned.split("-")
        if (
            len(cleaned) >= 2
            and all(
                p and p[0].isupper() and p.isalpha() for p in parts
            )
            and cleaned.lower() not in _SPAN_BLOCKED
            and all(p.lower() not in _SPAN_BLOCKED for p in parts)
        ):
            names += 1
            if names >= 2:
                return True
        elif len(cleaned) == 1 and cleaned.isupper():
            continue  # a middle initial neither makes nor breaks a span
        else:
            names = 0
    return False


#: A clause names another nominee only when it carries nomination
#: language (a role word or a serving verb) AND identifies someone —
#: a name span, or a RELATION ("my wife should be the executor …"
#: names the primary without naming her — review round 2, I-5).
#: A bare capitalized pair in a non-nominating clause ("my accounts
#: are at Wells Fargo bank") is scenery, not a nominee (I-6).
_NOMINATION_MARKERS: frozenset[str] = frozenset({
    "guardian", "guardians", "executor", "executors", "trustee",
    "trustees", "agent", "agents", "serve", "serves",
    "handle", "handles", "take", "takes", "care",
})
_RELATION_WORDS: frozenset[str] = frozenset({
    "wife", "husband", "spouse", "brother", "sister", "mother",
    "father", "friend", "partner", "son", "daughter", "mom", "dad",
})


#: Scenery vocabulary: a clause about accounts/investments naming a
#: firm is not a nomination, whatever firm it names — a denylist of
#: institutions loses to the next name (review round 4, I-D). A
#: clause carrying an actual role word is exempt from the exclusion.
_SCENERY_TOKENS: frozenset[str] = frozenset({
    "account", "accounts", "investment", "investments", "brokerage",
    "portfolio", "fund", "funds", "bank",
})
_NOMINEE_ROLE_WORDS: frozenset[str] = frozenset({
    "guardian", "guardians", "executor", "executors", "trustee",
    "trustees", "agent", "agents",
})


def _clause_names_another_nominee(clause: str) -> bool:
    tokens = set(_norm_tokens(clause))
    if not (tokens & _NOMINATION_MARKERS):
        return False
    if (tokens & _SCENERY_TOKENS) and not (tokens & _NOMINEE_ROLE_WORDS):
        return False
    return _clause_has_name_span(clause) or bool(tokens & _RELATION_WORDS)


def _primary_role_swapped(value: str, user_message: str) -> bool:
    """The proposed PRIMARY nominee is named only inside
    successor/backup clauses while another nominee is named in a
    signal-free clause — the model swapped primary and backup (A/B
    set 1, Sharma run 2: primary_executor = the backup's name, from
    the executor paragraph itself)."""
    name_tokens = {t for t in _norm_tokens(value) if len(t) >= 2}
    if not name_tokens:
        return False
    import re

    clauses: list[str] = []
    for sentence in _sentences(user_message):
        clauses.extend(_split_clauses(sentence))
    name_clauses = [
        c for c in clauses if name_tokens <= set(_norm_tokens(c))
    ]
    if not name_clauses:
        return False
    if any(not _clause_has_successor_signal(c) for c in name_clauses):
        return False
    for clause in clauses:
        if name_tokens <= set(_norm_tokens(clause)):
            continue
        if _clause_has_successor_signal(clause):
            continue
        if _clause_names_another_nominee(clause):
            return True
    return False


def _canonical_proposal_value(value: Any) -> str:
    """Canonical form for (key, value) pair comparisons — insensitive
    to case, punctuation, list order, and numeric formatting, so a
    fixation cannot escape the rejected-pair block by reformatting
    (review round 2, M-2)."""
    import re

    if isinstance(value, str):
        stripped = value.strip()
        numeric_form = stripped.lstrip("$").replace(",", "")
        if re.fullmatch(r"-?(\d+(\.\d+)?|\.\d+)", numeric_form):
            # A decimal string is a NUMBER, not a digit string:
            # "25.5" must canonicalize like 25.5, never as "255"
            # (review round 3, M-2); currency/thousands formatting
            # is the same number (round 4, Minor).
            as_float = float(numeric_form)
            return str(int(as_float)) if as_float.is_integer() else repr(as_float)
        tokens = _norm_tokens(value)
        if tokens and all(t.isdigit() for t in tokens):
            # Digit strings compare digit-for-digit: "512-555-0100"
            # and "5125550100" are the same phone number.
            return "".join(tokens)
        return " ".join(tokens)
    if isinstance(value, bool):
        return "true" if value else "false"
    from decimal import Decimal

    if isinstance(value, (int, float, Decimal)):
        as_float = float(value)
        return str(int(as_float)) if as_float.is_integer() else repr(as_float)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(
            sorted(_canonical_proposal_value(v) for v in value)
        ) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{k}:{_canonical_proposal_value(value[k])}"
            for k in sorted(value, key=str)
        ) + "}"
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _rejected_pair_restated(
    value: Any, user_message: str, prior_user_messages: list[str]
) -> bool:
    """A previously REJECTED (key, value) pair may be proposed again
    when the user THEMSELVES restates the value in a NEW message —
    a reversal ("Actually, use Priya Sharma as executor after all")
    is not model fixation, and blocking it would make the value
    un-recordable for the rest of the session (review round 2, I-4).
    A RE-SENT paragraph does not lift the block: the Sharma run-2
    fixation value was a fragment of a paragraph the user re-sent,
    and novelty is what separates the two shapes. Novelty is
    CONTAINMENT, not symmetric similarity: a re-send with one
    appended sentence ("the page seemed to freeze…") still contains
    ~all of the prior message's tokens, which defeated a set-Jaccard
    threshold (review round 3, I-F)."""
    if isinstance(value, str):
        stated = _person_name_grounded(
            value, user_message
        ) or _short_value_grounded(value, user_message)
    elif isinstance(value, bool):
        stated = (
            len(_norm_tokens(user_message)) <= 12
            and _parse_yes_no(user_message) == value
        )
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        stated = (
            len(_norm_tokens(user_message)) <= 25
            and _canonical_proposal_value(value)
            in set(_norm_tokens(user_message))
        )
    elif isinstance(value, (list, tuple)):
        normalized = " ".join(_norm_tokens(user_message))
        stated = len(_norm_tokens(user_message)) <= 40 and all(
            _canonical_proposal_value(item) in normalized for item in value
        )
    else:
        stated = False
    if not stated:
        return False
    message_tokens = set(_norm_tokens(user_message))
    if not message_tokens:
        return False
    for prior in prior_user_messages:
        prior_tokens = set(_norm_tokens(prior))
        if not prior_tokens:
            continue
        if len(prior_tokens) >= 15:
            containment = len(message_tokens & prior_tokens) / len(prior_tokens)
            if containment >= 0.9:
                return False
        elif message_tokens == prior_tokens:
            return False
    return True


def _is_bare_name_answer(value: str, user_message: str) -> bool:
    """The message is (nearly) just the proposed name — a direct answer
    to the pending successor question ("Amit Sharma", "Yes, Amit
    Sharma"). A nomination sentence for another role is NOT bare: its
    extra role words keep it from licensing a successor copy."""
    tokens = _norm_tokens(user_message)
    if not tokens or len(tokens) > 6:
        return False
    name_tokens = set(_norm_tokens(value))
    filler = {"yes", "please", "ok", "okay", "sure", "the", "a", "an"}
    return name_tokens <= set(tokens) and all(
        t in name_tokens or t in filler for t in tokens
    )


_SHORT_VALUE_GUARD_TYPES: frozenset[str] = frozenset({
    "text", "notes", "single_select",
})

#: Token equivalences for grounding (guard review I-2): the model
#: normalizes the user's words — spouse for "my wife", a semantic
#: "None" for "I don't want any …", a written-out date for digits.
#: A value token counts as stated when any equivalent appears.
_TOKEN_EQUIVALENCE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"wife", "husband", "spouse"}),
    frozenset({"everything", "all", "entire", "whole"}),
    frozenset({"none", "no", "not", "never", "nothing", "neither", "nobody"}),
)
#: Apostrophe stems of negations ("don't" tokenizes to "don" + "t").
_NEGATION_STEMS: frozenset[str] = frozenset({
    "don", "doesn", "didn", "isn", "aren", "wasn", "weren", "won",
    "haven", "hasn", "hadn", "couldn", "wouldn", "shouldn", "mustn",
})
_MONTH_NUMBERS: dict[str, str] = {
    "january": "1", "february": "2", "march": "3", "april": "4",
    "may": "5", "june": "6", "july": "7", "august": "8",
    "september": "9", "october": "10", "november": "11", "december": "12",
}


def _expand_equivalents(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for group in _TOKEN_EQUIVALENCE_GROUPS:
        if expanded & group:
            expanded |= group
    if expanded & _NEGATION_STEMS:
        expanded |= {"none", "no", "not", "never", "nothing", "neither", "nobody"}
    for name, number in _MONTH_NUMBERS.items():
        if name in expanded:
            expanded.add(number)
        if number in expanded:
            expanded.add(name)
    return expanded


def _short_value_grounded(value: str, user_message: str) -> bool:
    """A short text/notes/select value must be stated in the current
    message (the strict form for <=5 judgeable tokens; longer values
    need >=80% present — review I-2's uniform fraction in place of a
    hard cliff in both directions). Long faithful notes are the copy
    guard's domain, values with no judgeable tokens pass, and
    code-shaped select values (underscores) are pack data, not prose,
    and are not judged at all (review M-1)."""
    if "_" in value:
        return True
    content_tokens = [
        t for t in _norm_tokens(value) if len(t) > 3 or any(c.isdigit() for c in t)
    ]
    if not content_tokens:
        return True
    message_tokens = set(_norm_tokens(user_message))
    expanded_message = _expand_equivalents(message_tokens)
    present = sum(1 for t in content_tokens if t in expanded_message)
    if len(content_tokens) <= 5:
        if present == len(content_tokens):
            return True
    elif present >= 0.8 * len(content_tokens):
        return True
    if any(c.isdigit() for c in value):
        # Phone/ID-shaped values: punctuation variants of the same
        # digits ("5125550100" for "512-555-0100") are grounded.
        import re

        squashed_value = re.sub(r"[^a-z0-9]", "", value.lower())
        squashed_message = re.sub(r"[^a-z0-9]", "", user_message.lower())
        if len(squashed_value) >= 4 and squashed_value in squashed_message:
            return True
    return False


#: The primary role each successor/backup person field backs up.
PRIMARY_COUNTERPART: dict[str, str] = {
    "backup_guardian_person": "primary_guardian_person",
    "backup_executor_person": "primary_executor_person",
    "successor_poa_agent_person": "poa_agent_person",
    "successor_healthcare_agent_person": "healthcare_agent_person",
    # successor_trustee_person has no primary counterpart field: the
    # testator is the initial trustee.
}

#: Role-address fields whose natural value is the address bundle's
#: composed anchor ("123 Main Street, Austin, Texas").
_ADDRESS_ROLE_KEYS: frozenset[str] = frozenset({
    "primary_executor_address",
    "poa_agent_address",
    "primary_guardian_address",
    "healthcare_agent_address",
    "successor_healthcare_agent_address",
})


def _successor_value_licensed(
    value: str,
    user_message: str,
    *,
    primary_covered: bool,
    question_key: str | None = None,
) -> bool:
    """A successor/backup slot may take `value` from this message only
    when the message pairs the name with successor wording, or when it
    is a bare-name answer AND the primary role is already settled —
    otherwise the bare name most plausibly answers the PRIMARY
    question (guard review I-3). The first-person conditional ("…
    if I cannot …") counts as successor wording only for the
    successor TRUSTEE: there the testator is the incumbent, while
    for agent roles the same wording defines the primary's own
    activation (review round 3, I-A)."""
    if _successor_signal_present(
        value,
        user_message,
        first_person_conditional_ok=(
            question_key == "successor_trustee_person"
        ),
    ):
        return True
    return primary_covered and _is_bare_name_answer(value, user_message)


def _grounding_drop_reason(
    *,
    question_key: str,
    value: Any,
    question_type: str,
    user_message: str,
    primary_covered: bool = False,
    anchor_address: str | None = None,
    assistant_context: str = "",
) -> str | None:
    """Why a MODEL proposal is ungrounded in the current user message,
    or None when it stands. Reasons: "person_name_absent",
    "successor_unsignalled", "value_not_in_message". Applied in
    post_turn's validation loop; dropped keys join the turn's covered
    set so the deterministic fallbacks cannot re-create them (the
    022fddc Critical). Deterministic fallback proposals themselves are
    extracted FROM the message and are not re-judged here."""
    if question_type == "person":
        if not isinstance(value, str) or not value.strip():
            return None
        if not _person_name_grounded(value, user_message):
            return "person_name_absent"
        role_mismatch = _person_role_mismatch(
            question_key, user_message, assistant_context=assistant_context
        )
        if role_mismatch and question_key in SUCCESSOR_PERSON_KEYS:
            # A successor nomination whose message speaks NO role
            # family at all ("My backup, Amit Sharma, should serve
            # if Priya cannot …") takes its role from the question,
            # like a short answer — provided the name is genuinely
            # successor-paired. A message (or, for a role-free
            # message, the question it answers) that DOES speak a
            # different role ("… is the backup guardian") still
            # mismatches a successor proposal for another role:
            # without that check one generic backup sentence would
            # license every successor key at once (review round 3,
            # I-B).
            key_parts = _key_parts(question_key)
            message_tokens = set(_norm_tokens(user_message))
            normalized = " ".join(_norm_tokens(user_message))
            context_tokens = message_tokens
            context_normalized = normalized
            if assistant_context and not _any_role_present(
                message_tokens, normalized
            ):
                tail_tokens_list = _norm_tokens(
                    _assistant_tail(assistant_context)
                )
                context_tokens = message_tokens | set(tail_tokens_list)
                context_normalized = (
                    normalized + " " + " ".join(tail_tokens_list)
                )
            speaks_other_role = any(
                _role_part_present(part, context_tokens, context_normalized)
                for part in _ROLE_PARTS
                if part not in key_parts
            )
            if not speaks_other_role and _successor_value_licensed(
                value,
                user_message,
                primary_covered=primary_covered,
                question_key=question_key,
            ):
                role_mismatch = False
        if role_mismatch:
            return "person_role_mismatch"
        if question_key in SUCCESSOR_PERSON_KEYS:
            if not _successor_value_licensed(
                value,
                user_message,
                primary_covered=primary_covered,
                question_key=question_key,
            ):
                return "successor_unsignalled"
        elif _primary_role_swapped(value, user_message):
            return "primary_role_swapped"
        return None
    if question_type in _SHORT_VALUE_GUARD_TYPES and isinstance(value, str):
        if (
            anchor_address
            and question_key in _ADDRESS_ROLE_KEYS
            and " ".join(_norm_tokens(value)) == " ".join(_norm_tokens(anchor_address))
        ):
            # The value IS the server-composed bundle anchor; a user
            # pasting it with abbreviations ("St.", "TX") stated it
            # even though the expanded tokens never literally appear
            # (guard review C-1).
            return None
        if question_key in _ADDRESS_ROLE_KEYS and not _address_shaped(value):
            # A bare person name is not an address, even when the name
            # appears in the message (A/B set 1, Pruned run 1:
            # poa_agent_address = "David Lee").
            return "value_not_in_message"
        if not _short_value_grounded(value, user_message):
            return "value_not_in_message"
    return None


def _address_shaped(value: str) -> bool:
    """An address value carries a house number or a street token.
    (Composed anchors always do; "David Lee" never does.) A bare
    two-word capitalized pair is a person name even when its second
    word doubles as a street token — "David Lane" and "James Street"
    are people, not addresses (review round 2, M-1); genuine address
    values carry a number or a city/state tail, so they never trip
    this exclusion."""
    if any(c.isdigit() for c in value):
        return True
    if not (set(_norm_tokens(value)) & _STREET_TOKENS):
        return False
    raw_words = [w.strip(",.") for w in value.split() if w.strip(",.")]
    if len(raw_words) == 2 and all(
        w[:1].isupper() and w.isalpha() for w in raw_words
    ):
        return False
    if (
        len(raw_words) <= 3
        and "," not in value
        and all(w.isalpha() for w in raw_words)
    ):
        # "Anna Maria Lane" is a person (or a bare street fragment):
        # a genuine address value carries a number or a city/state
        # tail after a comma (review round 3, M-1 residual).
        return False
    return True


def _question_sets_scoped(
    question_sets: Any, keys: set[str]
) -> dict[str, Any]:
    """The question sets restricted to `keys`, structure preserved, so
    a fallback's exactly-one-candidate discipline operates within a
    single-field universe."""
    scoped: dict[str, Any] = {}
    if not isinstance(question_sets, dict):
        return scoped
    for set_id, qset in question_sets.items():
        sections = []
        for section in (qset or {}).get("sections") or []:
            kept = [
                q
                for q in section.get("questions") or []
                if str(q.get("key")) in keys
            ]
            if kept:
                sections.append({**section, "questions": kept})
        if sections:
            scoped[set_id] = {**qset, "sections": sections}
    return scoped


def _focus_completeness_proposals(
    *,
    focus_keys: list[str],
    covered_keys: set[str],
    question_types: dict[str, str],
    user_message: str,
    assistant_reply: str,
    question_sets: Any,
    answered_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """G3: for each FOCUS field still uncovered after the model and the
    standard fallbacks, run the existing deterministic fallback scoped
    to that one field. The unscoped pass abstains exactly where G3 must
    not: when several undecided questions compete for one message.
    Scoped to a single asked field, the same machinery is safe — and a
    captured value at 0.80 beats a verbatim re-send next turn (A/B
    run 4 turns 4 and 11 stored zero proposals)."""
    fallbacks = {
        "boolean": _deterministic_boolean_proposal,
        "person": _deterministic_person_proposal,
        "text": _deterministic_text_proposal,
        "notes": _deterministic_text_proposal,
    }
    covered = set(covered_keys)
    answered = set(answered_keys or set())
    proposals: list[dict[str, Any]] = []
    for key in focus_keys:
        if key in covered:
            continue
        fallback_fn = fallbacks.get(question_types.get(key) or "")
        if fallback_fn is None:
            continue
        scoped = _question_sets_scoped(question_sets, {key})
        if not scoped:
            continue
        kwargs: dict[str, Any] = {
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "question_sets": scoped,
            "existing_proposal_keys": set(),
        }
        if fallback_fn is _deterministic_text_proposal:
            kwargs["allow_bundled"] = True
        fallback = fallback_fn(**kwargs)
        if fallback is None or fallback.get("question_key") != key:
            continue
        if fallback_fn is _deterministic_boolean_proposal:
            # Scoping removes the exactly-one-candidate competition
            # that disciplines the unscoped pass: the assistant channel
            # matches trivially (the server DID ask about this key),
            # and a sibling clause carrying two label words could mint
            # a polarity. Accept the capture only when the USER's own
            # words independently clear the candidate bar — a second
            # call with no assistant text must agree (review I-1).
            corroborated = _deterministic_boolean_proposal(
                user_message=user_message,
                assistant_reply="",
                question_sets=scoped,
                existing_proposal_keys=set(),
            )
            if (
                corroborated is None
                or corroborated.get("question_key") != key
                or corroborated.get("value") != fallback.get("value")
            ):
                logger.info(
                    "Completeness fallback skipped uncorroborated boolean %r",
                    key,
                )
                continue
        primary_key = PRIMARY_COUNTERPART.get(key)
        if key in SUCCESSOR_PERSON_KEYS and not _successor_value_licensed(
            str(fallback["value"]),
            user_message,
            primary_covered=primary_key is not None and primary_key in answered,
            question_key=key,
        ):
            # The person fallback extracts the message's main-clause
            # nominee — for a successor key that is usually the
            # PRIMARY's name (the guardian paragraph yields Amit, not
            # the backup Neha). G1's pairing discipline applies to
            # fallback output too before it may fill a successor slot.
            logger.info(
                "Completeness fallback skipped unsignalled successor %r",
                key,
            )
            continue
        if (
            fallback_fn is _deterministic_person_proposal
            and key not in SUCCESSOR_PERSON_KEYS
            and (
                _person_role_mismatch(
                    key, user_message, assistant_context=assistant_reply
                )
                or _primary_role_swapped(str(fallback["value"]), user_message)
            )
        ):
            # Review round 2, C-1: post_turn's covered-set subtraction
            # un-covers a key whose MODEL proposal was just dropped as
            # a cross-role smear — without the same discipline here,
            # G3 re-mints that exact smear at 0.80 in the same turn
            # (the guardian paragraph yields Amit, and a scoped
            # executor capture would file him as executor). A scoped
            # capture must clear the same role bar as a model proposal.
            logger.info(
                "Completeness fallback skipped cross-role person %r",
                key,
            )
            continue
        logger.info("Completeness fallback captured focus field %r", key)
        proposals.append(fallback)
        covered.add(key)
    return proposals


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
    question_sets: Any,
    answers: dict[str, Any],
    pending_proposal_keys: frozenset[str] | set[str] = frozenset(),
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
        if key in pending_proposal_keys:
            # Awaiting confirmation is not a gap: the focus must never
            # instruct re-asking a key with an open proposal (Part 2
            # review I-1).
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
    applicable, _pending, pruned = qs.partition_questions(question_sets, answers)
    applicable_keys = {str(q.get("key")) for _s, _sec, q in applicable}
    pruned_keys = {str(q.get("key")) for _s, _sec, q in pruned}

    for bundle_name, members in _ROLE_BUNDLES:
        # Pruned members are out of the plan entirely: an answered but
        # pruned member can neither anchor an offer nor count toward
        # the exactly-one rule (Part 3 review I-1).
        answered_members = [
            k
            for k in members
            if k not in pruned_keys and qs.is_answered(answers.get(k))
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


def _bundle_fill_keys(
    offer: dict[str, Any],
    *,
    pending: set[str],
    open_now: set[str],
    decided: set[str],
    answers: dict[str, Any],
) -> list[str]:
    """The fill keys a consumed bundle YES may actually fill.

    Skips keys with an open proposal (pending/open_now), keys already
    answered, and keys the user already DECIDED on a card — confirmed,
    corrected, or rejected: a rejected fill must never be resurrected
    by a later bundle verdict (batch review I-1; the fallbacks hold
    the same guard since the E2E rejected-fabrication incident).
    """
    return [
        k
        for k in offer["fill_keys"]
        if k not in pending
        and k not in open_now
        and k not in decided
        and not qs.is_answered(answers.get(k))
    ]


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
        # Fail closed WITHOUT handing the caller a fresh dict: the
        # caller persists the returned state, and persisting {} over
        # the stored state would erase offered/declined bookkeeping
        # (batch review I-1). The sentinel tells post_turn to skip
        # its state write entirely this turn.
        logger.warning("bundle state read failed", exc_info=True)
        return None, {"_state_unavailable": True}
    declined = set(state.get("bundles_declined") or [])
    offer = pending_bundle_offer(pack.question_sets, answers, declined=declined)
    if offer is None:
        return None, state
    offered = dict(state.get("bundles_offered") or {})
    try:
        presentations = int(offered.get(offer["decline_key"], 0))
    except (TypeError, ValueError):
        # Corrupt bookkeeping must never kill a turn (batch review
        # M-2): treat an unreadable count as never presented.
        presentations = 0
    if presentations >= MAX_BUNDLE_OFFERS:
        # Cap reached: never PRESENTED again, but the answer to the
        # final presentation is still owed a verdict read (gating
        # review I-1) — on exactly the next turn boundary, proven by
        # turn-count freshness: count_turns counts ROWS and every
        # completed turn (including an advice refusal) stores a user +
        # assistant pair, so the very next turn's count is
        # presented_at + 2. A failed turn stores nothing, so a retry
        # of the same answer still reads fresh — correctly, since no
        # visible question intervened.
        # Any other count means another visible exchange intervened
        # (an advice refusal, a failed turn, a deferral cycle): the
        # offer is retired, never left consumable against a question
        # the user is no longer answering (batch reviews C-1 + I-1).
        presented_map = state.get("bundles_last_presented") or {}
        presented_at = presented_map.get(offer["decline_key"])
        try:
            turns_now = repo.count_turns(conn, session_id)
        except Exception:
            turns_now = None
        if (
            presented_at is not None
            and turns_now is not None
            and turns_now == presented_at + 2
        ):
            offer["presented_before"] = True
            offer["verdict_only"] = True
            offer["fill_labels"] = [
                question_labels.get(k, k) for k in offer["fill_keys"]
            ]
            return offer, state
        return None, state
    # A yes/no verdict may only be consumed once the offer was presented
    # in an EARLIER turn: on its first presentation the user's message is
    # still answering the previous visible question (live Part 3 drive:
    # an address bundle filled on a "Yes" that answered the children
    # question, because the visible question had diverged from the
    # prompt's intent).
    offer["presented_before"] = presentations >= 1
    offered[offer["decline_key"]] = presentations + 1
    state["bundles_offered"] = offered
    if presentations + 1 >= MAX_BUNDLE_OFFERS:
        # Record WHEN the final presentation happens: the verdict
        # window at the cap is exactly the next turn boundary, and
        # only turn-count freshness can prove no other visible
        # question (a refusal, a failed turn) intervened (batch
        # re-review I-1).
        try:
            last_presented = dict(state.get("bundles_last_presented") or {})
            last_presented[offer["decline_key"]] = repo.count_turns(
                conn, session_id
            )
            state["bundles_last_presented"] = last_presented
        except Exception:
            logger.warning("bundle presentation stamp failed", exc_info=True)
    offer["fill_labels"] = [
        question_labels.get(k, k) for k in offer["fill_keys"]
    ]
    return offer, state


#: Short role names for composing the visible bundle question.
_BUNDLE_ROLE_NAMES: dict[str, str] = {
    "primary_executor_person": "your executor",
    "poa_agent_person": "your financial power of attorney agent",
    "healthcare_agent_person": "your healthcare agent",
    "backup_executor_person": "your backup executor",
    "successor_poa_agent_person": "your successor financial agent",
    "successor_healthcare_agent_person": "your successor healthcare agent",
    "successor_trustee_person": "your successor trustee",
    "primary_executor_address": "your executor",
    "poa_agent_address": "your financial agent",
    "primary_guardian_address": "your children's guardian",
    "healthcare_agent_address": "your healthcare agent",
    "successor_healthcare_agent_address": "your successor healthcare agent",
}


#: Wording that marks a clause as speaking about the bundle offer
#: itself ("all of those roles", "same person for all", "also
#: serve"). In a turn where an offer is awaiting its verdict, such a
#: clause can carry the verdict — but only the clause (review
#: round 3, C-A: deciding by the whole SENTENCE accepted negated
#: bundle sentences as YES and declined the offer over a details
#: sentence that merely mentioned the roles).
_BUNDLE_REF_PATTERN = (
    r"\b(all of those|those roles|all of them|all the roles|"
    r"those positions|for all of|same person|also serve|"
    r"each of those)\b"
)
_BUNDLE_ACTION_PATTERN = r"\b(serve|serves|serving|use|using|handle|handles)\b"
_BUNDLE_NEGATION_PATTERN = r"\b(no|not|never|don'?t|doesn'?t|wouldn'?t|won'?t)\b"
_BUNDLE_UNCERTAINTY_PATTERN = (
    r"\b(not sure|not certain|unsure|maybe|perhaps|let me think|"
    r"need to think|don'?t know)\b"
)
_BUNDLE_AFFIRMATIVE_PATTERN = (
    r"\b(yes|yeah|sure|absolutely|definitely|please do)\b"
    r"|\bsame person for all\b"
)
#: Details-state vocabulary: sentences reporting the STATE of the
#: roles' details ("unchanged", "details", "phone", "address",
#: "contact") are fact reports, not bundle verdicts, unless they
#: also carry bundle action wording.
_DETAILS_STATE_TOKENS: frozenset[str] = frozenset({
    "unchanged", "details", "phone", "contact", "address",
})


def _bundle_verdict(message: str) -> bool | None:
    """The yes/no verdict for a pending bundle offer. Two shapes
    decide (review round 2, I-7; clause discipline tightened in
    round 3, C-A):

    1. A head-block sentence containing a bundle-ref CLAUSE that is
       itself verdict-shaped: the clause parses as yes/no directly,
       or it carries bundle action wording ("… also serve in all of
       those roles") / is short enough to BE the reference ("for all
       of those roles") while the sentence carries an affirmative.
       This saves the verdict when a terse "No …" about ANOTHER
       topic leads ("No additional powers. Yes, use him for all of
       those roles please") and when a clear verdict runs long. A
       negated bundle-ref clause is a DECLINE ("Not the same person
       for all of those roles …"); an uncertain one defers. A
       sentence that merely mentions the roles inside a substantive
       clause (contact details "for all of those roles … are
       unchanged") decides nothing.
    2. A short standalone head (<=12 words — the whole opening block,
       or its first line when a ride-along follows on the next line).

    A substantive paragraph that merely CONTAINS an early "no …"
    about another topic is not a verdict — consuming it as one
    recorded a decline for an offer the user never declined and
    deadlocked both Pruned A/B runs in set 1 (their details
    paragraph answered the offer's turn). Anything else defers."""
    import re

    text = message.strip()
    if not text:
        return None
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    for sentence in sentences:
        clauses = _split_clauses(sentence)
        ref_clauses = [
            c for c in clauses if re.search(_BUNDLE_REF_PATTERN, c.lower())
        ]
        if not ref_clauses:
            continue
        lowered_sentence = sentence.lower()
        if any(
            re.search(_BUNDLE_UNCERTAINTY_PATTERN, c.lower())
            for c in clauses
        ):
            # An uncertain sentence is not a verdict in either
            # direction ("Maybe yes, use him …" / "I am not sure …").
            continue
        sentence_tokens = set(_norm_tokens(sentence))
        if (sentence_tokens & _DETAILS_STATE_TOKENS) and not (
            re.search(_BUNDLE_ACTION_PATTERN, lowered_sentence)
            or "same person" in lowered_sentence
        ):
            # A details-state sentence ("my address for all of those
            # roles is unchanged") reports facts ABOUT the roles; it
            # does not decide the bundle — at any length (review
            # round 4, C-A residual: length was the only thing saving
            # the long forms).
            continue
        for clause in ref_clauses:
            parsed = _parse_yes_no(clause)
            if parsed is not None:
                return parsed
        for clause in ref_clauses:
            lowered = clause.lower()
            if re.search(_BUNDLE_UNCERTAINTY_PATTERN, lowered):
                continue
            if re.search(_BUNDLE_NEGATION_PATTERN, lowered):
                return False
            verdict_shaped = (
                re.search(_BUNDLE_ACTION_PATTERN, lowered)
                or "same person" in lowered
                or len(clause.split()) <= 10
            )
            if not verdict_shaped:
                continue
            if re.search(_BUNDLE_AFFIRMATIVE_PATTERN, lowered):
                return True
            if any(
                re.search(_BUNDLE_AFFIRMATIVE_PATTERN, c.lower())
                and not re.search(_BUNDLE_NEGATION_PATTERN, c.lower())
                for c in clauses
            ):
                return True
    if len(block.split()) <= 12:
        head = block.split("\n", 1)[0]
        head_tokens = set(_norm_tokens(head))
        if (head_tokens & _DETAILS_STATE_TOKENS) and not (
            re.search(_BUNDLE_ACTION_PATTERN, head.lower())
            or "same person" in head.lower()
        ):
            # The short-head fallback obeys the same details-state
            # exclusion as step 1.
            return None
        return _parse_yes_no(head)
    return None


def _bundle_question_text(offer: dict[str, Any]) -> str:
    """The grouped yes/no question for an offer, in plain language. Used
    verbatim by the provider's no-text fallback so the VISIBLE question
    always matches the offer the server will interpret the reply
    against."""
    roles = ", ".join(
        _BUNDLE_ROLE_NAMES.get(k, k) for k in offer["fill_keys"]
    )
    if offer["bundle"] == "address":
        return (
            f"Quick question: should {roles} also use your address "
            f"({offer['anchor_name']})?"
        )
    return (
        f"Quick question: would you like {offer['anchor_name']} to also "
        f"serve as {roles}?"
    )


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
    # Interview v2 (flag on): a pending bundle offer outranks the cluster
    # gap; otherwise the prompt focuses on ONE cluster's gap. Flag off:
    # the v5 path runs byte-for-byte (including its trace label). The
    # focus is computed BEFORE the provider context so the provider's
    # no-text fallback asks the same question the prompt intends.
    cluster_gap = None
    bundle_offer = None
    verdict_offer = None
    bundle_state: dict[str, Any] = {}
    signed_in_name = None
    # The previous turn's server-side focus (persisted in session
    # state): the copy guard treats a proposal for one of these keys
    # as answering the asked question — membership beats guessing
    # from prose (guard review I-3).
    prev_focus_keys: frozenset[str] = frozenset()
    # The two-turn focus window (this turn's user message may answer
    # the question from the turn before last — drift): the G2
    # focus-authoritative asked-test uses the window, and only it.
    window_focus_keys: frozenset[str] = frozenset()
    v2_on = _v2_enabled(conn, firm_id=firm_id, pack=pack)
    if v2_on:
        bundle_offer, bundle_state = _bundle_offer_for_turn(
            conn,
            session_id=session_id,
            pack=pack,
            answers=answers,
            question_labels=question_labels,
        )
        state_unavailable = bool(bundle_state.pop("_state_unavailable", False))
        prev_focus_keys = frozenset(bundle_state.get("last_focus_keys") or [])
        window_focus_keys = prev_focus_keys | frozenset(
            bundle_state.get("prev_focus_keys") or []
        )
        # Shift the window: this turn's incoming last_focus becomes
        # next turn's prev_focus (persisted with the state write at
        # the end of this block).
        bundle_state["prev_focus_keys"] = sorted(prev_focus_keys)
        if bundle_offer is not None and bundle_offer.get("verdict_only"):
            # Verdict-only: never presented, never the prompt focus —
            # it exists solely so the verdict block can consume the
            # answer to the final presentation.
            verdict_offer = bundle_offer
            bundle_offer = None
        if bundle_offer is None:
            cluster_gap = next_gap_cluster(
                pack.question_sets,
                answers,
                pending_proposal_keys=frozenset(pending_keys),
            )
            if cluster_gap is not None and cluster_gap["cluster"] == "identity":
                signed_in_name = _signed_in_name(
                    conn, scope.matter.get("household_id")
                )
        if bundle_offer is not None:
            # Fill keys only: the anchor is already answered, and
            # keeping it in the guard's asked-set would preserve
            # redundant copy proposals for it (batch review M-5).
            bundle_state["last_focus_keys"] = sorted(bundle_offer["fill_keys"])
        elif cluster_gap is not None:
            bundle_state["last_focus_keys"] = sorted(
                str(q["key"]) for q in cluster_gap["questions"]
            )
        else:
            bundle_state["last_focus_keys"] = []
        if not state_unavailable:
            try:
                repo.update_session_state(conn, session_id, bundle_state)
            except Exception:
                logger.warning("bundle state persist failed", exc_info=True)
    focus_keys: list[str] = []
    focus_question_text: str | None = None
    if bundle_offer is not None:
        focus_keys = list(bundle_offer["fill_keys"])
        focus_question_text = _bundle_question_text(bundle_offer)
    elif cluster_gap is not None:
        focus_keys = [str(q["key"]) for q in cluster_gap["questions"]]
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
        focus_keys=focus_keys,
        focus_question_text=focus_question_text,
    )
    history = _history_messages(conn, session_id)
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
    question_types = {
        str(question.get("key")): str(question.get("type") or "")
        for _, _, question in qs.iter_questions(pack.question_sets)
    }
    # The address bundle's composed anchor (same composition as
    # pending_bundle_offer): role-address proposals equal to it are
    # grounded by the server's own composition (guard review C-1).
    _addr_parts = (
        answers.get("testator_street_address"),
        answers.get("testator_city"),
        answers.get("primary_residence_state"),
    )
    _anchor_address = (
        f"{_addr_parts[0].strip()}, {_addr_parts[1].strip()}, {_addr_parts[2].strip()}"
        if all(isinstance(v, str) and v.strip() for v in _addr_parts)
        else None
    )
    _assistant_texts = [m.content for m in history if m.role == "assistant"]
    last_assistant_text = _assistant_texts[-1] if _assistant_texts else ""
    prior_assistant_text = _assistant_texts[-2] if len(_assistant_texts) >= 2 else ""
    valid_proposals = []
    dropped_copy_keys: set[str] = set()
    dropped_grounding_keys: set[str] = set()
    dropped_rejected_keys: set[str] = set()
    # (key, value) pairs the decider already REJECTED in this session:
    # re-proposing the identical pair is fixation, not extraction.
    # Fail-open — a read error must never break the turn.
    try:
        rejected_pairs = {
            (key, _canonical_proposal_value(value))
            for key, value in repo.rejected_proposal_values(conn, session_id)
        }
    except Exception:
        logger.warning("rejected-proposal read failed", exc_info=True)
        rejected_pairs = set()
    _prior_user_messages = [m.content for m in history if m.role == "user"]
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
        if (
            draft.question_key,
            _canonical_proposal_value(draft.value),
        ) in rejected_pairs and not _rejected_pair_restated(
            draft.value, message, _prior_user_messages
        ):
            logger.warning(
                "Dropping re-proposal of a previously_rejected value for %r (session %s)",
                draft.question_key,
                session_id,
            )
            dropped_rejected_keys.add(draft.question_key)
            continue
        if _is_whole_message_copy(
            question_type=question_types.get(draft.question_key, ""),
            value=draft.value,
            user_message=message,
            last_assistant_text=last_assistant_text,
            question_label=question_labels.get(draft.question_key, ""),
            prior_assistant_text=prior_assistant_text,
            asked_keys=window_focus_keys if v2_on else prev_focus_keys,
            question_key=draft.question_key,
            focus_authoritative=v2_on,
        ):
            logger.warning(
                "Dropping whole-message copy proposal for %r (session %s)",
                draft.question_key,
                session_id,
            )
            # A dropped key stays covered for THIS turn: the
            # deterministic fallbacks run next and must not re-create
            # the same poison card at 0.80 (guard review Critical #1).
            dropped_copy_keys.add(draft.question_key)
            continue
        # G1/G1c grounding: a model proposal must be grounded in the
        # CURRENT user message — prior turns are context, not evidence
        # (successor copies of the primary's name; "All assets"
        # asserted from a paragraph about bank accounts). Same
        # covered-set discipline as the copy guard above.
        grounding_reason = _grounding_drop_reason(
            question_key=draft.question_key,
            value=draft.value,
            question_type=question_types.get(draft.question_key, ""),
            user_message=message,
            primary_covered=(
                PRIMARY_COUNTERPART.get(draft.question_key) in answers
            ),
            anchor_address=_anchor_address,
            assistant_context=last_assistant_text,
        )
        if grounding_reason is not None:
            logger.warning(
                "Dropping ungrounded proposal for %r (%s) (session %s)",
                draft.question_key,
                grounding_reason,
                session_id,
            )
            dropped_grounding_keys.add(draft.question_key)
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
        all_covered = existing_keys | proposed_keys | set(answers.keys()) | decided_keys | dropped_copy_keys | dropped_grounding_keys | dropped_rejected_keys

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

        # G3 completeness sweep: on a pure cluster turn (no bundle
        # offer/verdict in play — the bundle machinery owns those
        # turns, and its fill keys must never be raced), any focus
        # field still uncovered gets one scoped deterministic capture
        # so the answer is recorded THIS turn instead of forcing a
        # verbatim re-send (A/B run 4 turns 4 and 11).
        if v2_on and bundle_offer is None and verdict_offer is None:
            from app.domains.ai.provider import FieldProposalDraft

            for fb in _focus_completeness_proposals(
                focus_keys=focus_keys,
                # NOT covered: keys whose MODEL proposal was just
                # guard-dropped. The drops judge the model's assertion
                # (an unasked copy, an unsignalled successor); G3's
                # scoped capture for a field the server itself asked
                # about is a different, sanctioned act — inheriting
                # the dropped keys cost a full re-send turn in A/B
                # set 1 (Sharma run 1, turns 19-20). Pending, answered,
                # decided and model-proposed keys still cover.
                covered_keys=all_covered - dropped_copy_keys - dropped_grounding_keys,
                question_types=question_types,
                user_message=message,
                assistant_reply=last_assistant_msg,
                question_sets=pack.question_sets,
                answered_keys=set(answers.keys()),
            ):
                valid_proposals.append(
                    FieldProposalDraft(
                        question_key=fb["question_key"],
                        value=fb["value"],
                        confidence=fb["confidence"],
                        source_snippet=fb["source_snippet"],
                    )
                )
                all_covered.add(fb["question_key"])

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
        model_id=settings.OPENAI_INTERVIEW_MODEL if provider.name == "openai" else provider.name,
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
            model_id=settings.OPENAI_INTERVIEW_MODEL if provider.name == "openai" else provider.name,
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
    # The verdict is only consumed when the offer was presented in an
    # earlier turn (presented_before) — on first presentation this
    # message still answers the previous visible question.
    active_offer = verdict_offer or bundle_offer
    if active_offer is not None and active_offer.get("presented_before"):
        bundle_offer = active_offer
        verdict = _bundle_verdict(message)
        if verdict is True:
            # Fresh read at fill time (review I-1): never stack a bundle
            # proposal on top of an already-open proposal for the same
            # key — the turn-start snapshot in pending_key_set can miss
            # proposals stored by other paths since.
            open_now = repo.open_proposal_keys(conn, session_id)
            decided_now = repo.decided_proposal_keys(conn, session_id)
            for fill_key in _bundle_fill_keys(
                bundle_offer,
                pending=pending_key_set,
                open_now=open_now,
                decided=decided_now,
                answers=answers,
            ):
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
