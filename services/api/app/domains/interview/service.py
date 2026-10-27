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
import re
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
    ParkedItemOut,
    ParkItemCreate,
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


#: Label words too generic to license a deterministic capture on their
#: own — they appear in many questions' labels, so a match proves
#: nothing about which field the user (or the previous reply) meant.
#: The 2026-10-25 set-6 anticipation was licensed by exactly two of
#: these ("should" + "handle") inside the EXECUTOR question and minted
#: a POA-agent proposal nobody had stated.
_GENERIC_LABEL_WORDS = {
    "should", "handle", "serve", "first", "choice", "cannot", "would",
    "could", "become", "unable", "yourself", "person", "there", "their",
    "about", "which", "after", "before", "other", "while", "optional",
}

# Each role's NOMINATION vocabulary: the noun itself plus the words
# that define the role in its label (the guardian label speaks of
# children, never of "guardians"; the POA label speaks of financial
# affairs). "estate" stays out: it is scenery that appears in bequest
# sentences. Shared by the person-fallback licensing gate and the
# topicality family path for content-thin labels.
_ROLE_FAMILY_WORDS = {
    "guardian": {"guardian", "guardians", "children", "kids", "child"},
    "executor": {"executor", "executors", "executrix"},
    "trustee": {"trustee", "trustees", "trust"},
    "agent": {"agent", "agents"},
    "healthcare": {"healthcare", "medical", "agent", "agents"},
    "poa": {"agent", "agents", "attorney", "financial", "finances", "power"},
}

_PHONE_SPAN_RE = re.compile(
    r"(?<!\d)(?:\+?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}|\d[\d\s().\-]{5,18}\d)(?!\d)"
)
_DATE_SPAN_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
_ZIP4_SPAN_RE = re.compile(r"^\d{5}-\d{4}$")


def _extract_phone_value(text: Any) -> str | None:
    """The phone-shaped span inside `text`, or None.

    A phone field's value must BE a phone number — never the sentence
    that carried it (set-6 Pruned: the trust sentence "No, I don't want
    a revocable living trust." was stored as healthcare_agent_phone).
    Non-phone digit runs are rejected (review round 2, I-3): ISO dates,
    ZIP+4 codes, and contiguous runs whose length is not a plausible
    phone length (7-digit local, 10-digit national, 11 with country
    code) — separated forms keep the 7-15 digit window. The pattern is
    digit-boundary anchored (review round 3, C-1): a match may not be
    a substring of a longer digit run, so a 16-digit card number can
    never truncate into a "phone" and an 11-digit number survives
    whole.
    """
    if not isinstance(text, str) or not text:
        return None
    for match in _PHONE_SPAN_RE.finditer(text):
        span = match.group(0).strip()
        if _DATE_SPAN_RE.match(span) or _ZIP4_SPAN_RE.match(span):
            continue
        digits = re.sub(r"\D", "", span)
        if not re.search(r"[\s().\-]", span):
            if len(digits) not in {7, 10, 11}:
                continue
        elif not 7 <= len(digits) <= 15:
            continue
        return span
    return None


def _is_phone_key(question_key: str) -> bool:
    return "phone" in str(question_key)


def _label_words(label: str) -> list[str]:
    return [
        w
        for w in re.findall(r"\w+", label.lower())
        if len(w) > 3
        and w not in {"what", "does", "your", "with", "have", "this", "that"}
    ]


def _message_names_topic(
    question_key: str,
    user_message: str,
    question_sets: dict[str, dict[str, Any]],
    *,
    multi_ask: bool = False,
) -> bool:
    """Whether the user's message itself names the candidate's topic.

    The deterministic text fallback selects its field from the PREVIOUS
    reply's wording; without a message-side check, any message that
    happens to follow a multi-ask reply gets stamped onto whichever
    field's label the reply contained (the set-6 phone bleed). A
    distinctive label word in the message — or the field's own value
    shape, for addresses — is the minimum evidence the message is an
    answer to that field at all.

    A key whose label has NO distinctive words (guardian_reason: "Why
    is this person a good fit?") passes unchecked in a single-ask
    regime — the message is the answer to the one question asked. In
    a MULTI-ASK regime that free pass is the set-7 stamp: the trust
    paragraph was filed as guardian_reason in both Sharma runs. There
    the ordinary matched-word / bare-value evidence is required.
    """
    from app.domains.intake import question_sets as qs

    message_lower = user_message.lower()
    for _, _, question in qs.iter_questions(question_sets):
        if str(question.get("key")) != question_key:
            continue
        words = _label_words(str(question.get("label") or ""))
        distinctive = [
            w for w in words if len(w) >= 5 and w not in _GENERIC_LABEL_WORDS
        ]
        if not distinctive and not multi_ask:
            return True  # nothing distinctive to check against
        matched = [
            w
            for w in words
            if len(w) >= 4 and w in message_lower and w not in _GENERIC_LABEL_WORDS
        ]
        # Section-common words cannot establish topicality (set 8):
        # "healthcare"/"agent" sit in the limitations label AND in
        # every agent label, so the agents paragraph "named the
        # topic" and was stamped onto healthcare_agent_limitations in
        # both Pruned runs. A label word counts only when it is
        # specific to this field's neighbourhood (in <3 other
        # labels): the distinctive shortcut always uses specific
        # words; the matched-words paths use them in multi-ask
        # regimes, where fat paragraphs make coincidental matches
        # likely.
        label_word_counts: dict[str, int] = {}
        for _, _, other_q in qs.iter_questions(question_sets):
            if str(other_q.get("key")) == question_key:
                continue
            for w in set(_label_words(str(other_q.get("label") or ""))):
                label_word_counts[w] = label_word_counts.get(w, 0) + 1

        def _specific(word: str) -> bool:
            return label_word_counts.get(word, 0) < 3

        if distinctive and any(
            w in message_lower for w in distinctive if _specific(w)
        ):
            return True
        counted = [w for w in matched if _specific(w)] if multi_ask else matched
        # Terse restatements of the label's own words are genuine
        # answers too ("The rest goes to my brother" for the residuary
        # question): two label content words, or one in a short reply.
        if len(counted) >= 2:
            return True
        if counted and len(user_message.split()) <= 12:
            return True
        if not distinctive:
            # Content-thin labels ("Why is this person a good fit?")
            # cannot be matched literally — the field's role family
            # IS the topic (guardian_reason's answer talks about the
            # guardian and the children). Two distinct family words
            # required, word-boundary matched: one shared word is
            # scenery ("children" alone in a trust paragraph does
            # not make it a guardian answer).
            family_words: set[str] = set()
            for token in question_key.split("_"):
                family_words |= _ROLE_FAMILY_WORDS.get(token, set())
            hits = {
                w
                for w in family_words
                if re.search(rf"\b{re.escape(w)}\b", message_lower)
            }
            if question_key.endswith("_reason") and len(hits) >= 2:
                # A *_reason answer must carry REASON language, not
                # just family scenery (review: "guardian of the
                # estate paperwork" + "children" in a trust
                # paragraph satisfied the two-hit rule without any
                # guardian-nomination content). The genuine answer
                # explains WHY: trusted / close / values / ...
                reason_markers = (
                    "trusted", "close", "values", "best", "responsible",
                    "dependable", "reliable", "caring", "patient",
                    "loves", "loved", "knows", "bond", "experienced",
                )
                if not any(
                    re.search(rf"\b{m}\b", message_lower)
                    for m in reason_markers
                ):
                    hits = set()
            if len(hits) >= 2:
                return True
        if question_key.endswith("_address") and _address_shaped(user_message):
            return True
        # Bare-value answers (review round 2, C-1): canonical answers
        # to several fields contain NO label words at all ("Richmond,
        # Virginia" for a city/state field, "Reyansh, 7 and Haasini,
        # 13" for the children list, "50%" for a share). A short reply
        # with no negation, no question, and NO CLAUSE VERB is a
        # value, not a paragraph — the verb requirement is what keeps
        # short off-topic clauses out (review round 3, I-2: "Skip the
        # trust entirely", "Everything goes to charity", "I changed
        # my mind" are bleed shapes, not values).
        tokens = user_message.split()
        if (
            len(tokens) <= 6
            and "?" not in user_message
            and not re.search(
                r"\b(no|not|never|none|don'?t|doesn'?t|isn'?t|aren'?t|won'?t|can'?t)\b",
                message_lower,
            )
            and not re.search(
                r"\b(is|are|was|were|go|goes|going|want|wants|skip|skipped|"
                r"decline|declined|change|changed|have|has|had|will|would|"
                r"can|could|should|do|does|did|get|gets|take|takes|put|puts|"
                r"give|gives|keep|keeps|leave|leaves|make|makes|use|uses|"
                r"need|needs|like|likes|prefer|prefers)\b",
                message_lower,
            )
        ):
            return True
        return False
    return True


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
            matched_label_words = [w for w in label_words if w in ctx]
            label_matches = len(matched_label_words)
            # Label licensing of a PRIMARY key needs a DISTINCTIVE
            # matched word, not just any two: generic words ("should",
            # "handle") appear in sibling questions and licensed the
            # set-6 fabrication where the executor question minted a
            # POA-agent proposal. Qualified (successor / backup) keys
            # are exempt: their labels are generic BY DESIGN ("Who
            # should serve if your first choice cannot?") and their
            # discipline is the qualification machinery below (plus,
            # on the G3 path, successor licensing against the answered
            # primary) — requiring a distinctive word there would kill
            # the sanctioned bare-name successor capture.
            distinctive_matches = sum(
                1
                for w in matched_label_words
                if len(w) >= 5 and w not in _GENERIC_LABEL_WORDS
            )
            # Role-word licensing (set-7/8 turn 4): a PRIMARY key's
            # distinctive match must be one of the key's own role
            # NOUNS, not scenery from its label. The residuary
            # sentence ("...the residue of my estate — should go to
            # my wife, Priya Sharma") matched "should" + "estate" and
            # minted the executor from a bequest, twice — "estate" is
            # role EVIDENCE vocabulary, but it is not a nomination
            # word. Nomination nouns only: executor / guardian /
            # trustee / agent.
            role_words: set[str] = set()
            for part in _key_parts(key):
                role_words |= _ROLE_FAMILY_WORDS.get(part, set())
            role_matches = sum(
                1 for w in matched_label_words if w in role_words
            )
            qualified_key = any(
                q in key for q in ("backup", "alternate", "successor", "secondary")
            )
            key_roles = {r for r in roles if r in key}
            role_match = bool(key_roles & ctx_roles)
            if role_match or (
                label_matches >= 2 and (qualified_key or role_matches >= 1)
            ):
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

    # Role parity (set 7): the G3 scoped path and the model path both
    # apply role discipline to person captures; the unscoped fallback
    # did not, so an executor paragraph minted backup_guardian_person
    # from the previous reply's guardian wording (Sharma #2 turn 6).
    # A message that speaks a different role family — or swaps the
    # primary into a successor clause — cannot ground this nomination.
    # Successor keys keep their own discipline (review I-1): a
    # role-FREE successor phrasing ("...should be the one to step in
    # and take over if I cannot serve...") is exactly what successor
    # licensing exists to judge, so for those keys only the hard
    # smear case is blocked here — the message nominates with a
    # DIFFERENT role NOUN (executor / guardian / trustee / agent are
    # nomination words; scenery like "health reasons" or "my estate"
    # is not).
    if asked_key in SUCCESSOR_PERSON_KEYS:
        _nouns = {
            "guardian": {"guardian", "guardians"},
            "executor": {"executor", "executors", "executrix"},
            "trustee": {"trustee", "trustees"},
            "agent": {"agent", "agents"},
            "healthcare": {"agent", "agents"},
            "poa": {"agent", "agents"},
        }
        _token_set = set(_norm_tokens(user_message))
        _spoken = set().union(*_nouns.values()) & _token_set
        _own = set().union(
            *(_nouns.get(part, set()) for part in _key_parts(asked_key))
        )
        if _spoken and not (_spoken & _own):
            return None
    elif _person_role_mismatch(
        asked_key, user_message, assistant_context=assistant_reply
    ) or _primary_role_swapped(name, user_message):
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
    server_asked: bool = False,
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

    key = candidates[0]
    if _is_phone_key(key):
        # A phone field's value must be a phone number, never the
        # sentence that carried it (set-6 Pruned turn 8: the trust
        # sentence became healthcare_agent_phone because the previous
        # reply contained the phone label verbatim). Extract the
        # number itself; no number in the message, no proposal.
        span = _extract_phone_value(message)
        if span is None:
            return None
        return {
            "question_key": key,
            "value": span,
            # Below the 0.9 auto-confirm threshold ON PURPOSE: human-reviewed.
            "confidence": 0.80,
            "source_snippet": message[:500],
        }
    if not server_asked and not _message_names_topic(
        key,
        message,
        question_sets,
        multi_ask=(
            assistant_reply.count("?") >= 2
            or len(re.findall(r"(?m)^\s*\d+[).]", assistant_reply)) >= 2
        ),
    ):
        # The candidate was selected from the previous reply's wording;
        # the message itself never names this field's topic, so stamping
        # the whole message onto it is the set-6 bleed shape. The G3
        # sweep passes server_asked=True: its field is in the server's
        # own focus, which establishes topicality on its own.
        return None

    return {
        "question_key": key,
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
    sibling_draft_count: int = 0,
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
            # The window exemption presumes a single-topic message
            # answering THIS key. Under v7's merged focus the window
            # holds many keys and the user answers only some of them;
            # when the same message grounded >=2 OTHER proposals it
            # is demonstrably multi-topic, and a whole-message copy
            # for an unanswered window key is the poison shape, not
            # a faithful answer (fix iteration 1: A/B set 3 Pruned
            # run 2 — the agents paragraph copied verbatim into
            # healthcare_agent_phone / _limitations /
            # poa_additional_powers, all window keys).
            if sibling_draft_count < 2:
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


def _value_shape_ok(question_type: str, value: Any) -> bool:
    """Whether a proposal's value shape can faithfully fill a field
    of this pack type. A boolean for a text/notes/person field (the
    model answering a label's yes/no half — 'Are these up to
    date?' — instead of the descriptive fact) and a string for a
    boolean field are never faithful extractions; in A/B set 4 a
    boolean True PERSISTED as the final answer for both
    *_beneficiaries_current text fields. Select types carry pack
    option codes / lists and are deliberately ungated."""
    if question_type == "boolean":
        return isinstance(value, bool)
    if question_type in {"text", "notes", "person"}:
        return isinstance(value, str)
    return True


def _focus_completeness_proposals(
    *,
    focus_keys: list[str],
    covered_keys: set[str],
    question_types: dict[str, str],
    user_message: str,
    assistant_reply: str,
    question_sets: Any,
    answered_keys: set[str] | None = None,
    prior_proposal_count: int = 0,
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
            # The scoped field is in the server's own focus — topicality
            # is established by the focus itself, so the text fallback's
            # message-topic gate does not apply here (its shape rules,
            # e.g. phone extraction, still do).
            kwargs["server_asked"] = True
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
        if (
            fallback_fn is _deterministic_text_proposal
            and len(user_message.split()) > 25
            and not _message_names_topic(
                key, user_message, question_sets, multi_ask=True
            )
        ):
            # A fat paragraph that never names this field's topic is
            # not this field's answer, even scoped (set 8: the
            # re-sent agents paragraph stamped onto living_will /
            # limitations through the sweep, whose server_asked pass
            # skips the topic gate). The sanctioned sweep capture —
            # the focus field's own paragraph — names its topic and
            # is unaffected.
            logger.info(
                "Completeness fallback skipped off-topic text capture %r",
                key,
            )
            continue
        if (
            fallback_fn is _deterministic_text_proposal
            and prior_proposal_count + len(proposals) >= 2
        ):
            # The text capture's value is the WHOLE message by
            # construction. It is sanctioned only as a lapse-catcher
            # (the model proposed ~nothing and the paragraph is this
            # field's answer — A/B run 4). When this turn already
            # produced >=2 proposals, the message is multi-topic and
            # the model demonstrably engaged it: stamping the whole
            # paragraph into every uncovered notes field of a merged
            # focus is the set-4 poison (guardian_reason /
            # living_will_directives / organ_donation_wishes /
            # poa_additional_powers all = the agents paragraph).
            # Bundled extraction is the model's job; abstain.
            logger.info(
                "Completeness fallback skipped multi-topic text capture %r",
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


# --- Parked items (Phase 4, P4: "don't know yet") ---------------------------
# A parked item is a first-class answer state: the user deferred the
# question, so the interview never asks or nags about it again, progress
# displays it distinctly (neither answered nor missing), and document
# GENERATION stays gated — the item is unanswered, so the documents
# domain's completeness gate (which reads the raw partition) keeps
# refusing until the family resolves it via the Review screen edit
# path or by returning to the interview. Markers live in
# interview_parked_items (matter-level); an ANSWER always wins over a
# marker (effective_parked_keys), so resolving an item anywhere —
# intake form, proposal confirm, review-screen edit — un-parks it
# everywhere with no extra write.


def effective_parked_keys(
    parked_keys: frozenset[str] | set[str], answers: dict[str, Any]
) -> set[str]:
    """Park markers that still bind: a marker for an answered key is
    inert (the answer superseded it)."""
    return {k for k in parked_keys if not qs.is_answered(answers.get(k))}


def _parked_keys(conn: Any, matter_id: UUID) -> set[str]:
    """Raw park markers for the matter. Fail-open like the bundle
    state reads: a parked-items read error must never break a turn —
    worst case a parked item is asked once more."""
    try:
        return set(repo.list_parked_keys(conn, matter_id))
    except Exception:
        logger.warning("parked-items read failed", exc_info=True)
        return set()


def _display_progress(
    pack: LoadedPack,
    answers: dict[str, Any],
    parked_keys: frozenset[str] | set[str],
) -> tuple[float, list[str], int, int, list[str]]:
    """The interview surface's progress view.

    Returns (completeness_pct, missing, total_required,
    answered_required, parked_required). Parked-required items are
    counted NEITHER answered NOR missing: they leave the display
    denominator entirely, exactly as pruned items do in the raw
    partition. The RAW partition (compute_progress) is unchanged —
    the documents gate keeps seeing parked items as missing.
    """
    completeness, missing, total_required, answered_required = _progress(
        pack, answers
    )
    parked_required = [k for k in missing if k in parked_keys]
    if not parked_required:
        return completeness, missing, total_required, answered_required, []
    display_missing = [k for k in missing if k not in parked_keys]
    display_total = total_required - len(parked_required)
    display_pct = (
        round(100.0 * answered_required / display_total, 2)
        if display_total
        else 100.0
    )
    return display_pct, display_missing, display_total, answered_required, parked_required


def _display_progress_for_matter(
    conn: Any, pack: LoadedPack, matter_id: UUID
) -> tuple[float, list[str], int, int, list[str]]:
    """_display_progress with answers + park markers loaded for a matter."""
    answers = _answers_map(conn, matter_id)
    parked = effective_parked_keys(_parked_keys(conn, matter_id), answers)
    return _display_progress(pack, answers, parked)


#: Bare deferrals that park the just-asked focus (Phase 4 P4). The
#: match is on the WHOLE normalized message: a reply that states any
#: fact ("I don't know his phone, but his email is ...") never parks —
#: it is an answer with a gap, not a deferral.
_PARK_EXACT: frozenset[str] = frozenset(
    {
        "i don't know",
        "i dont know",
        "don't know",
        "dont know",
        "i don't know yet",
        "i dont know yet",
        "don't know yet",
        "not sure",
        "i'm not sure",
        "im not sure",
        "i am not sure",
        "no idea",
        "i have no idea",
        "skip",
        "skip it",
        "skip this",
        "skip this one",
        "skip that",
        "pass",
        "i'll have to check",
        "ill have to check",
        "i have to check",
        "i need to check",
        "i need to check on that",
        "i'll check later",
        "ill check later",
    }
)
_PARK_FILLER_PREFIXES: tuple[str, ...] = (
    "hmm",
    "um",
    "uh",
    "well",
    "ok",
    "okay",
    "so",
)


def _is_park_request(message: str) -> bool:
    """True only for a bare "don't know yet" / skip deferral."""
    text = re.sub(r"\s+", " ", message.strip().lower()).rstrip(".!?").strip()
    for prefix in _PARK_FILLER_PREFIXES:
        if text.startswith(prefix + ", ") or text.startswith(prefix + " "):
            text = text[len(prefix) :].lstrip(", ").strip()
            break
    return text in _PARK_EXACT


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


def _gaps_by_cluster(
    question_sets: Any,
    answers: dict[str, Any],
    pending_proposal_keys: frozenset[str] | set[str] = frozenset(),
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> dict[str, list[dict[str, str]]]:
    """Applicable-missing REQUIRED questions (plus unanswered gate
    questions that pending content waits on), grouped by cluster.

    `question_sets` is the LoadedPack mapping (set id -> set dict), as
    accepted by partition_questions / iter_questions. Pruned questions
    never appear; keys with an open proposal never appear (awaiting
    confirmation is not a gap); parked keys never appear (the user
    said "don't know yet" — the interview never nags, Phase 4 P4).
    """
    applicable, pending, _pruned = qs.partition_questions(question_sets, answers)
    by_cluster: dict[str, list[dict[str, str]]] = {}
    added: set[str] = set()

    def _add(question: dict[str, Any]) -> None:
        key = str(question.get("key"))
        cluster = str(question.get("cluster") or "")
        if cluster not in CLUSTER_ORDER or key in added:
            return
        if key in parked_keys:
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
    return by_cluster


def next_gap_cluster(
    question_sets: Any,
    answers: dict[str, Any],
    pending_proposal_keys: frozenset[str] | set[str] = frozenset(),
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """The first cluster (in CLUSTER_ORDER) with unanswered work.

    Work = applicable-missing REQUIRED questions, plus unanswered gate
    questions that pending content waits on (the document-choice gates —
    without them the POA/healthcare sections can never become applicable).
    Pruned questions never appear. Returns
    {"cluster": name, "questions": [{"key", "label", "type"}]} or None when
    every applicable required question is answered.
    """
    by_cluster = _gaps_by_cluster(
        question_sets, answers, pending_proposal_keys, parked_keys
    )
    for cluster in CLUSTER_ORDER:
        if by_cluster.get(cluster):
            return {"cluster": cluster, "questions": by_cluster[cluster]}
    return None


#: Turn-count economics (A/B follow-up iteration): when the first gap
#: cluster is small, the NEXT cluster rides along in the same ask — one
#: message, several fields answered per reply (L1/L3). Phase 4 (brief
#: 2026-10-26, P2) caps how much of that ride-along one turn may carry
#: (FOCUS_MAX_QUESTIONS below); the merge now tops the cap up from the
#: following clusters in order instead of admitting whole clusters.
MERGE_FOCUS_MAX_QUESTIONS = 10
#: When this few applicable required fields remain in total, the focus
#: becomes one consolidated sweep of all of them (L4) instead of
#: walking the remaining clusters one per turn.
SWEEP_MAX_REMAINING = 6
#: Phase 4 presentation cap (brief P2): a turn never ASKS more than
#: this many pieces. Larger themes split across consecutive turns.
FOCUS_MAX_QUESTIONS = 4


def next_gap_focus(
    question_sets: Any,
    answers: dict[str, Any],
    pending_proposal_keys: frozenset[str] | set[str] = frozenset(),
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """The turn's question focus: the head of the gap queue, capped.

    Returns {"cluster": first cluster name, "clusters": [names],
    "questions": [...], optionally "sweep": True}:

    - The questions are the first FOCUS_MAX_QUESTIONS gaps in cluster
      order (Phase 4 P2 cap): a large cluster contributes its head and
      continues next turn; a small cluster is topped up from the next
      clusters in order.
    - SWEEP: when <= SWEEP_MAX_REMAINING applicable required fields
      remain overall AND they all fit under the cap, the focus is ALL
      of them across clusters (the end-of-interview backstop, L4).
      With 5-6 remaining the tail is asked as a capped focus instead —
      a sweep may only claim a turn it fully covers.
    """
    by_cluster = _gaps_by_cluster(
        question_sets, answers, pending_proposal_keys, parked_keys
    )
    gaps = [
        {"cluster": cluster, "questions": by_cluster[cluster]}
        for cluster in CLUSTER_ORDER
        if by_cluster.get(cluster)
    ]
    if not gaps:
        return None
    total = sum(len(g["questions"]) for g in gaps)
    ordered_pairs = [
        (g["cluster"], q) for g in gaps for q in g["questions"]
    ]
    capped = ordered_pairs[:FOCUS_MAX_QUESTIONS]
    questions = [q for _cluster, q in capped]
    clusters: list[str] = []
    for cluster, _q in capped:
        if cluster not in clusters:
            clusters.append(cluster)
    result: dict[str, Any] = {
        "cluster": gaps[0]["cluster"],
        "clusters": clusters,
        "questions": questions,
    }
    if total <= SWEEP_MAX_REMAINING and len(questions) == total:
        result["sweep"] = True
    return result


def _focus_without_sweep_on_verdict_turns(
    focus: dict[str, Any] | None,
    verdict_offers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Suppress the completeness-sweep framing while bundle verdicts
    are in play (review I-1 on the Phase 4 branch).

    The sweep line tells the model the focus is the whole remainder
    of the interview and to wrap up. On a turn where offers sit in
    their verdict window, the bundle machinery owns the framing —
    the same principle the G3 capture applies ("the bundle machinery
    owns those turns"). The focus QUESTIONS are unchanged (a sweep
    only fires when the focus already covers the remainder); only
    the wrap-up presentation is dropped, so the pending verdicts are
    never raced by a finish-up ask.
    """
    if focus is None or not verdict_offers or not focus.get("sweep"):
        return focus
    return {k: v for k, v in focus.items() if k != "sweep"}


def _gap_without_keys(
    gap: dict[str, Any] | None, excluded_keys: set[str]
) -> dict[str, Any] | None:
    """A focus gap minus `excluded_keys` (None when nothing remains).

    Used for the verdict turn's "then ask" set: a bundle fill key is
    unanswered and applicable, so it is a legitimate gap question —
    but it is being resolved by THIS turn's verdict, and asking it in
    the same reply would visibly re-ask a just-confirmed field
    (review I-2)."""
    if gap is None:
        return None
    questions = [
        q for q in gap["questions"] if str(q["key"]) not in excluded_keys
    ]
    if not questions:
        return None
    return {**gap, "questions": questions}


def shadow_questions_for(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
    focus_questions: list[dict[str, Any]],
    *,
    covered_keys: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Gate-shadow fields for a cluster-focus turn (v8, 2026-10-25).

    A question whose applicability is PENDING only because a boolean
    gate in this turn's focus is unanswered — and which becomes
    applicable when that gate flips true — rides along as a shadow
    field: the prompt may record it when (and only when) the user
    answers the gate yes and states the value in the same message.
    Without this, a user who states the gate and its gated facts in
    one breath ("Yes, I want a trust. Amit is the successor trustee.
    Put everything into the trust...") has only the gate recorded and
    must repeat the rest next turn — the set-6 trust paragraph took
    three sends, and the assets beneficiaries (gated on
    has_retirement_accounts / has_life_insurance) took until turn 10.
    Applicability is computed with the pack's own partition (the
    single source of truth), differenced against the gate-true trial.
    """
    from app.domains.intake import question_sets as qs

    focus_keys = {str(q["key"]) for q in focus_questions}
    gate_labels = {
        str(q["key"]): str(q.get("label") or "")
        for q in focus_questions
        if str(q.get("type") or "") == "boolean"
    }
    gates = list(gate_labels)
    if not gates:
        return []
    _applicable, pending, _pruned = qs.partition_questions(question_sets, answers)
    shadows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in gates:
        trial = dict(answers)
        trial[gate] = True
        applicable_trial, _p, _pr = qs.partition_questions(question_sets, trial)
        trial_keys = {str(q.get("key")) for _, _, q in applicable_trial}
        for _set_id, _section_id, question in pending:
            key = str(question.get("key"))
            if (
                key not in trial_keys
                or key in focus_keys
                or key in covered_keys
                or key in answers
                or key in seen
                or key == gate
            ):
                continue
            seen.add(key)
            shadows.append(
                {
                    "key": key,
                    "label": str(question.get("label") or ""),
                    "type": str(question.get("type") or ""),
                    "gate_key": gate,
                    "gate_label": gate_labels.get(gate, ""),
                }
            )
    return shadows


def other_questions_for(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
    *,
    exclude: frozenset[str] | set[str],
) -> list[dict[str, Any]]:
    """The Lever E reference inventory (prompt v9): every question the
    pack's partition counts as applicable or pending under the current
    answers, minus the turn's focus / shadow / offer keys. The model
    can only extract a volunteered off-focus paragraph against keys
    and labels it can actually see (set 7: the details, agents and
    trust paragraphs each extracted ~zero when they arrived outside
    the focus, and had to be re-sent)."""
    from app.domains.intake import question_sets as qs

    applicable, pending, _pruned = qs.partition_questions(question_sets, answers)
    out: list[dict[str, Any]] = []
    seen = set(exclude) | set(answers.keys())
    for _set_id, _section_id, question in [*applicable, *pending]:
        key = str(question.get("key"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {"key": key, "label": str(question.get("label") or key)}
        )
    return out


def _governing_expressions(
    question_sets: dict[str, dict[str, Any]], key: str
) -> list[str]:
    """The applies_if expressions governing a question (its section's
    and its own), for gate-mode inspection."""
    exprs: list[str] = []
    for question_set in question_sets.values():
        for section in question_set.get("sections", []):
            for question in section.get("questions", []):
                if str(question.get("key")) != key:
                    continue
                if section.get("applies_if"):
                    exprs.append(str(section["applies_if"]))
                if question.get("applies_if"):
                    exprs.append(str(question["applies_if"]))
    return exprs


def _gate_mode(
    question_sets: dict[str, dict[str, Any]], key: str, gate: str
) -> str:
    """"opt_out" when the gate governs the key through a `!= false`
    expression (the field applies UNLESS the gate is false — the POA
    and healthcare sections), else "opt_in" (the field applies only
    when the gate is true — the trust / guardian / beneficiary gates).
    """
    for expr in _governing_expressions(question_sets, key):
        normalized = re.sub(r"\s+", " ", expr)
        if f"{gate} != false" in normalized or f"{gate} != False" in normalized:
            return "opt_out"
    return "opt_in"


def gate_dependents_map(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> dict[str, tuple[tuple[str, str], ...]]:
    """{dependent key: ((gate, mode), ...)} for every question whose
    applicability turns on a boolean gate.

    Candidates are questions PENDING on an unanswered gate and
    questions PRUNED by an answered gate (review round 2, I-1: a
    pruned dependent was previously absent from the map, so a proposal
    for it was never dropped even with its gate recorded False).
    Attribution is by the pack's own partition, differenced against
    the gate-true trial. Licensing (see _drop_unlicensed_gate_proposals):
    opt-in gates must be true; opt-out gates must not be false.
    """
    from app.domains.intake import question_sets as qs

    applicable, pending, pruned = qs.partition_questions(question_sets, answers)
    applicable_now = {str(q.get("key")) for _, _, q in applicable}
    candidate_keys: list[str] = []
    for _set_id, _section_id, question in [*pending, *pruned]:
        key = str(question.get("key"))
        if key not in candidate_keys:
            candidate_keys.append(key)
    if not candidate_keys:
        return {}
    boolean_keys = [
        str(q.get("key"))
        for _, _, q in qs.iter_questions(question_sets)
        if str(q.get("type") or "") == "boolean"
    ]
    result: dict[str, list[tuple[str, str]]] = {}
    for gate in boolean_keys:
        if gate in answers:
            # An answered gate still controls its PRUNED dependents
            # (they must stay dropped-able); only the false case adds
            # map entries, trialed below at True. Falseness uses the
            # gate-value vocabulary, not identity (review round 3,
            # M-1): "false" / "no" / 0 count as answered-false too.
            if not _gate_value_false(answers.get(gate)):
                continue
        trial = dict(answers)
        trial[gate] = True
        applicable_trial, _p, _pr = qs.partition_questions(question_sets, trial)
        trial_keys = {str(q.get("key")) for _, _, q in applicable_trial}
        for key in candidate_keys:
            if key in trial_keys and key not in applicable_now and key != gate:
                pair = (gate, _gate_mode(question_sets, key, gate))
                if pair not in result.get(key, []):
                    result.setdefault(key, []).append(pair)
    return {key: tuple(pairs) for key, pairs in result.items()}


def _gate_value_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "yes"}:
        return True
    return False


def _gate_value_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no"}:
        return True
    return False


def _drop_unlicensed_gate_proposals(
    drafts: list[Any],
    answers: dict[str, Any],
    dependents: dict[str, Any],
) -> list[Any]:
    """Drop proposals for gate-dependent keys whose gate doesn't license them.

    A gated field recorded while its opt-in gate is false or
    unanswered — or its opt-out gate is false — is a wrong-source
    artifact (a trust field for a user who declined the trust). The
    prompt licenses shadow captures on a yes only; this is the
    deterministic backstop, judged against the answers plus this
    turn's own accepted proposals. Licensing precedence: this turn's
    drafts outrank the base map (recorded answers merged with pending
    proposal values); among duplicate drafts of one key the first
    wins, matching the storage loop (review round 2 M-1, round 3
    I-1). Dependents entries may be a gate name, a tuple of gate
    names (opt-in), or (gate, mode) pairs from gate_dependents_map.
    """
    if not dependents:
        return list(drafts)
    truth = dict(answers)
    drafted: set[str] = set()
    for draft in drafts:
        # This turn's statement outranks the base (recorded answers
        # merged with stale PENDING values) in both directions —
        # review round 3, I-1. Among duplicate drafts of one key the
        # FIRST still wins, matching the storage loop (round 2, M-1).
        if draft.question_key not in drafted:
            truth[draft.question_key] = draft.value
            drafted.add(draft.question_key)

    def _licensed(key: str) -> bool:
        entries = dependents.get(key)
        if not entries:
            return True
        if isinstance(entries, str):
            entries = ((entries, "opt_in"),)
        pairs: list[tuple[str, str]] = []
        for entry in entries:
            if isinstance(entry, str):
                pairs.append((entry, "opt_in"))
            elif isinstance(entry, (tuple, list)) and entry:
                pairs.append(
                    (str(entry[0]), str(entry[1]) if len(entry) > 1 else "opt_in")
                )
            # Malformed entries are ignored (fail open on the entry,
            # never crash the turn) — review round 3, M-4.
        for gate, mode in pairs:
            value = truth.get(gate)
            if mode == "opt_out":
                if not _gate_value_false(value):
                    return True
            elif _gate_value_true(value):
                return True
        return False

    return [draft for draft in drafts if _licensed(draft.question_key)]


def _copy_guard_asked_keys(
    *,
    v2_on: bool,
    window_focus_keys: frozenset[str] | set[str],
    prev_focus_keys: frozenset[str] | set[str],
    focus_keys: list[str],
) -> frozenset[str]:
    """The copy guard's asked-set for this turn.

    v2: the two-turn window PLUS the current turn's focus keys — a
    faithful copy for a field the server is asking about RIGHT NOW is
    an answer, not a fabrication (set-6 Sharma #1 lost whole turns
    when the current cluster's paragraph was dropped because the
    window still held the previous cluster's keys). The caller passes
    an EMPTY focus list on first-presentation bundle turns, whose
    fill keys are offered rather than asked (review round 2, M-5).
    v1: unchanged.
    """
    if v2_on:
        return frozenset(window_focus_keys) | frozenset(focus_keys)
    return frozenset(prev_focus_keys)


def _numeric_text_value(question_type: str, value: Any) -> str | None:
    """String form of a numeric scalar proposed for a text/notes field.

    Models emit JSON-native numbers for numeric content (the trust
    distribution age as int 25); discarding them costs a re-ask turn
    for a faithful value. Booleans are NOT coerced: a bool for a text
    field is a polarity answer to the label's yes/no half, the exact
    fabrication shape the type-shape gate exists to drop (bool is an
    int subclass in Python — excluded explicitly).
    """
    if question_type not in {"text", "notes"}:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return str(value)


def _is_duplicate_answer(
    question_key: str,
    value: Any,
    answers: dict[str, Any],
    question_type: str = "",
) -> bool:
    """A proposal restating the recorded answer verbatim is noise, not
    extraction — in the set-6 stall the model re-proposed answered
    trust fields for six straight turns. A DIFFERENT value is a
    genuine correction signal and is kept for human review.

    Person fields compare EXACTLY (review round 2, M-6): legal names
    are casing-sensitive, so a casing-only change is a correction,
    not a duplicate.
    """
    if question_key not in answers:
        return False
    if question_type == "person":
        return isinstance(value, str) and value == answers[question_key]
    return _canonical_proposal_value(value) == _canonical_proposal_value(
        answers[question_key]
    )


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
    conn: Any,
    *,
    firm_id: UUID,
    pack: LoadedPack,
    answers: dict[str, Any],
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """Cluster gap for this turn when the interview_v2 flag is on for the
    matter's firm + pack; None (the v5 path) otherwise."""
    if not _v2_enabled(conn, firm_id=firm_id, pack=pack):
        return None
    return next_gap_cluster(pack.question_sets, answers, parked_keys=parked_keys)


def _focus_for_turn(
    conn: Any,
    *,
    firm_id: UUID,
    pack: LoadedPack,
    answers: dict[str, Any],
    pending_proposal_keys: frozenset[str] | set[str] = frozenset(),
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """The dense question focus for this turn when the interview_v2
    flag is on for the matter's firm + pack; None (the v5 path)
    otherwise. Same fail-safe posture as _cluster_gap_for_turn."""
    if not _v2_enabled(conn, firm_id=firm_id, pack=pack):
        return None
    return next_gap_focus(
        pack.question_sets,
        answers,
        pending_proposal_keys=pending_proposal_keys,
        parked_keys=parked_keys,
    )


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


def pending_bundle_offers(
    question_sets: Any,
    answers: dict[str, Any],
    *,
    declined: frozenset[str] | set[str] = frozenset(),
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """ALL pending bundle offers for this answer state, in priority
    order (primary, backup, address).

    Role bundles: exactly one member answered (the anchor) and at least
    one member unanswered AND applicable — pruned members are never fill
    targets. Address bundle: the testator's own address trio is answered
    and at least one role address is missing and applicable. Declined offers (by decline_key) never reappear. Decline keys are
    "<bundle>:<anchor name lower>" for role bundles and
    "address:<composed address lower>" for the address bundle.

    Turn-count economics: the offers are independent — presenting only
    the first cost one presentation + verdict exchange PER offer in
    the A/B (up to three extra turns). post_turn presents all of them
    in one message and resolves them from one reply.
    """
    applicable, _pending, pruned = qs.partition_questions(question_sets, answers)
    applicable_keys = {str(q.get("key")) for _s, _sec, q in applicable}
    pruned_keys = {str(q.get("key")) for _s, _sec, q in pruned}
    offers: list[dict[str, Any]] = []

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
            and k not in parked_keys
        ]
        if fill_keys:
            offers.append(
                {
                    "bundle": bundle_name,
                    "anchor_key": anchor_key,
                    "anchor_name": anchor_name,
                    "fill_keys": fill_keys,
                    "fill_value": anchor_name,
                    "decline_key": decline_key,
                }
            )

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
                if not qs.is_answered(answers.get(k))
                and k in applicable_keys
                and k not in parked_keys
            ]
            if fill_keys:
                offers.append(
                    {
                        "bundle": "address",
                        "anchor_key": "testator_street_address",
                        "anchor_name": composed,
                        "fill_keys": fill_keys,
                        "fill_value": composed,
                        "decline_key": decline_key,
                    }
                )
    return offers


def pending_bundle_offer(
    question_sets: Any,
    answers: dict[str, Any],
    *,
    declined: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any] | None:
    """The first pending bundle offer, or None (the single-offer view
    of pending_bundle_offers, kept for the original callers/tests)."""
    offers = pending_bundle_offers(question_sets, answers, declined=declined)
    return offers[0] if offers else None


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


def _bundle_offers_for_turn(
    conn: Any,
    *,
    session_id: UUID,
    pack: LoadedPack,
    answers: dict[str, Any],
    question_labels: dict[str, str],
    limit: int | None = None,
    parked_keys: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The offers to present this turn + the verdict-only offers + the
    session state (mutated when an offer is presented, so the caller
    can persist it). Returns (presentable, verdict_only, state).

    Per-offer bookkeeping (the single-offer rules, applied to each
    pending offer independently): declined offers never return; an
    offer already presented MAX_BUNDLE_OFFERS times without resolution
    stops being offered (the cluster flow then asks the roles one by
    one) — except that the answer to its final presentation is still
    owed a verdict read on exactly the next turn boundary, proven by
    turn-count freshness (count_turns counts ROWS and every completed
    turn stores a user + assistant pair, so the very next turn's count
    is presented_at + 2; any other count means another visible
    exchange intervened and the offer is retired — batch reviews
    C-1 + I-1). `limit` caps how many pending offers are considered
    (the legacy single-offer seam passes 1).
    """
    try:
        state = repo.get_session_state(conn, session_id)
    except Exception:
        # Fail closed WITHOUT handing the caller a fresh dict: the
        # caller persists the returned state, and persisting {} over
        # the stored state would erase offered/declined bookkeeping
        # (batch review I-1). The sentinel tells post_turn to skip
        # its state write entirely this turn.
        logger.warning("bundle state read failed", exc_info=True)
        return [], [], {"_state_unavailable": True}
    declined = set(state.get("bundles_declined") or [])
    offers = pending_bundle_offers(
        pack.question_sets, answers, declined=declined, parked_keys=parked_keys
    )
    if limit is not None:
        offers = offers[:limit]
    if not offers:
        return [], [], state
    # Corrupt bookkeeping must never kill a turn: a non-dict stored
    # value reads as empty rather than raising out of dict().
    _raw_offered = state.get("bundles_offered")
    offered = dict(_raw_offered) if isinstance(_raw_offered, dict) else {}
    _raw_presented = state.get("bundles_last_presented")
    presented_map = (
        dict(_raw_presented) if isinstance(_raw_presented, dict) else {}
    )
    turns_now: int | None = None

    def _turns_now() -> int | None:
        # Cache successes only: a transient count failure is retried
        # at the next call site rather than suppressing every
        # freshness check and stamp for the rest of the turn.
        nonlocal turns_now
        if turns_now is None:
            try:
                turns_now = repo.count_turns(conn, session_id)
            except Exception:
                return None
        return turns_now

    presentable: list[dict[str, Any]] = []
    verdict_only: list[dict[str, Any]] = []
    state_dirty = False
    stamped = False
    for offer in offers:
        try:
            presentations = int(offered.get(offer["decline_key"], 0))
        except (TypeError, ValueError):
            # Corrupt bookkeeping must never kill a turn (batch review
            # M-2): treat an unreadable count as never presented.
            presentations = 0
        if presentations >= MAX_BUNDLE_OFFERS:
            _raw_at = presented_map.get(offer["decline_key"])
            try:
                presented_at = int(_raw_at) if _raw_at is not None else None
            except (TypeError, ValueError):
                # A corrupt stored stamp reads as never presented
                # rather than raising out of the freshness comparison.
                presented_at = None
            now = _turns_now()
            if (
                presented_at is not None
                and now is not None
                and now == presented_at + 2
            ):
                offer["presented_before"] = True
                offer["verdict_only"] = True
                offer["fill_labels"] = [
                    question_labels.get(k, k) for k in offer["fill_keys"]
                ]
                verdict_only.append(offer)
            continue
        # A yes/no verdict may only be consumed once the offer was
        # presented in an EARLIER turn: on its first presentation the
        # user's message is still answering the previous visible
        # question (live Part 3 drive: an address bundle filled on a
        # "Yes" that answered the children question, because the
        # visible question had diverged from the prompt's intent).
        offer["presented_before"] = presentations >= 1
        offered[offer["decline_key"]] = presentations + 1
        state_dirty = True
        if presentations + 1 >= MAX_BUNDLE_OFFERS:
            # Record WHEN the final presentation happens: the verdict
            # window at the cap is exactly the next turn boundary, and
            # only turn-count freshness can prove no other visible
            # question (a refusal, a failed turn) intervened (batch
            # re-review I-1).
            now = _turns_now()
            if now is not None:
                presented_map[offer["decline_key"]] = now
                stamped = True
            else:
                logger.warning("bundle presentation stamp failed", exc_info=True)
        offer["fill_labels"] = [
            question_labels.get(k, k) for k in offer["fill_keys"]
        ]
        presentable.append(offer)
    if state_dirty:
        state["bundles_offered"] = offered
        if stamped:
            state["bundles_last_presented"] = presented_map
    return presentable, verdict_only, state


def _bundle_offer_for_turn(
    conn: Any,
    *,
    session_id: UUID,
    pack: LoadedPack,
    answers: dict[str, Any],
    question_labels: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The single-offer seam over _bundle_offers_for_turn (limit=1):
    the first pending offer only, with its exact original bookkeeping
    semantics. post_turn uses the multi-offer version; this wrapper
    remains for the original callers/tests."""
    presentable, verdict_only, state = _bundle_offers_for_turn(
        conn,
        session_id=session_id,
        pack=pack,
        answers=answers,
        question_labels=question_labels,
        limit=1,
    )
    if presentable:
        return presentable[0], state
    if verdict_only:
        return verdict_only[0], state
    return None, state


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


def _bundle_questions_text(offers: list[dict[str, Any]]) -> str:
    """Every pending offer's question, verbatim, composed as flowing
    sentences in one message — the merged presentation text for the
    provider's no-text fallback (the model path gets the same texts
    verbatim via the prompt's offers block). Phase 4 (P1): sentences
    in one paragraph, never one-per-line or numbered — the verdict
    parser reads the user's REPLY, not this text, so the composition
    is purely presentational."""
    return " ".join(_bundle_question_text(offer) for offer in offers)


#: Given names that are also common English words: a bare "will" or
#: "may" in a sentence is far more likely the verb than the person,
#: so these anchors are referenced by full name only.
_COMMON_WORD_GIVEN_NAMES: frozenset[str] = frozenset({
    "will", "may", "mark", "art", "bill", "grant", "chase", "reed",
    "hope", "faith", "grace",
})


def _offer_reference_markers(
    offer: dict[str, Any], all_offers: list[dict[str, Any]]
) -> list[str]:
    """Reference markers for an offer's anchor, matched as normalized
    token subsequences (case/punctuation-insensitive: "Will K Smith"
    matches the anchor "Will K. Smith").

    Deliberately anchor-based, not role-based: role vocabulary
    overlaps across offers ("healthcare" is a primary fill and half
    of "successor healthcare"), while the anchor distinguishes them.
    The FULL name always marks. The given name marks only when it is
    unambiguous across the offers in play — a shared given name
    (Priya Sharma vs Priya Nair) or a common-word name (Will, May)
    would otherwise let one offer's sentence decide another's
    (review I-1). The surname marks under the same uniqueness rule
    ("Yes, Lee can serve" decides when only one Lee is an anchor,
    and nobody when two are). The address offer is marked by the
    word "address" alone: street tokens ("main", "austin") appear
    in sentences about people too.
    """
    if offer.get("bundle") == "address":
        return ["address"]
    anchor = str(offer.get("anchor_name") or "").lower()
    tokens = _norm_tokens(anchor)
    if not tokens:
        return []
    markers = [anchor]
    given_names: list[str] = []
    surnames: list[str] = []
    for other in all_offers:
        if other.get("bundle") == "address":
            continue
        other_tokens = _norm_tokens(str(other.get("anchor_name") or "").lower())
        if other_tokens:
            given_names.append(other_tokens[0])
            if len(other_tokens) >= 2:
                surnames.append(other_tokens[-1])
    given = tokens[0]
    if (
        given_names.count(given) == 1
        and given not in _COMMON_WORD_GIVEN_NAMES
        and len(given) > 2
    ):
        markers.append(given)
    if len(tokens) >= 2:
        surname = tokens[-1]
        if (
            surnames.count(surname) == 1
            and surname not in _COMMON_WORD_GIVEN_NAMES
            and len(surname) > 2
        ):
            markers.append(surname)
    return markers


def _is_token_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _references_offer(
    text_lower: str, offer: dict[str, Any], all_offers: list[dict[str, Any]]
) -> bool:
    text_tokens = _norm_tokens(text_lower)
    if not text_tokens:
        return False
    return any(
        _is_token_subsequence(_norm_tokens(marker), text_tokens)
        for marker in _offer_reference_markers(offer, all_offers)
    )


#: Capitalized words that legitimately OPEN a verdict reply without
#: naming a person. A capitalized non-initial token outside this set
#: (or a non-starter opening token) is treated as a person-shaped
#: reference: the reply is then NOT undifferentiated, however the
#: name relates to the candidate anchors.
_SENTENCE_STARTERS: frozenset[str] = frozenset({
    "yes", "no", "sure", "absolutely", "definitely", "please",
    "thanks", "thank", "okay", "ok", "well", "both", "all", "use",
    "make", "have", "let", "the", "that", "this", "it", "he", "she",
    "they", "we", "keep", "put", "give", "for", "and", "but", "if",
    "when", "while", "after", "before", "also", "just", "only",
    "quick", "correct", "right", "agreed", "actually", "however",
    "instead", "anyway", "meanwhile", "finally", "honestly",
    "basically", "so", "now", "then",
    # First-person possessive determiners are never person
    # names, but at sentence start they capitalize like one
    # ("My old primary home address should not be used." was
    # skipped as person-shaped — round-15 MN15-1). ONLY the
    # first person: "His/Her/Their address…" is a third
    # person's address, not the offer (round-16 CR16-4), and
    # "Your" is the addressee's, not the speaker's.
    "my", "our",
})

#: Capitalized tokens that name places or times, not people: US
#: state names and month names. (A candidate anchor carrying one of
#: these names is still matched by the candidate markers first —
#: this set only governs NON-candidate shape detection.)
_NON_PERSON_CAPITALS: frozenset[str] = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california",
    "colorado", "connecticut", "delaware", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "hampshire", "jersey", "mexico",
    "carolina", "dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode", "island", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "wisconsin",
    "wyoming", "columbia",
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
})

_I_CONTRACTIONS: frozenset[str] = frozenset({"i", "i'm", "i'll", "i'd", "i've"})


def _has_person_shape(text: str) -> bool:
    """A person-shaped reference in `text`: a capitalized token that
    is not a known sentence starter, place/time name, or the
    pronoun I (incl. contractions). Catches names that are NOT
    candidate anchors (a third person the user names instead), bare
    surnames, and middle-name variants — all of which make a reply
    differentiated even when no candidate marker matches."""
    import re

    tokens = re.findall(r"[A-Za-z']+", text)
    for i, tok in enumerate(tokens):
        if len(tok) < 2 or not tok[0].isupper():
            continue
        lowered = tok.lower()
        if lowered in _I_CONTRACTIONS or lowered in _NON_PERSON_CAPITALS:
            continue
        if i == 0 and lowered in _SENTENCE_STARTERS:
            continue
        return True
    return False


def _clause_owner(
    clause: str, all_offers: list[dict[str, Any]]
) -> set[str]:
    """Who a clause is ABOUT: the decline_keys of the person offers
    whose anchor it references; {"other"} when it references a
    person-shaped name that is no candidate's anchor; empty when it
    references nobody (a bare "yes", a connective fragment)."""
    lowered = clause.lower()
    refs = {
        o["decline_key"]
        for o in all_offers
        if o.get("bundle") != "address"
        and _references_offer(lowered, o, all_offers)
    }
    if refs:
        return refs
    if _has_person_shape(clause):
        return {"other"}
    return set()


#: Uncertainty for the scoped reads: the shared bundle pattern plus
#: explicit non-decision phrasing. An explicit deferral ("no decision
#: on David yet") must never be persisted as a decline (review C-3).
_SCOPED_UNCERTAINTY_EXTRA = (
    r"\b(no decision|not decided|haven'?t decided|have not decided|"
    r"still deciding|not ready to decide|need more time)\b"
)


def _scoped_uncertain(text_lower: str) -> bool:
    import re

    return bool(
        re.search(_BUNDLE_UNCERTAINTY_PATTERN, text_lower)
        or re.search(_SCOPED_UNCERTAINTY_EXTRA, text_lower)
    )


_FRAGMENT_STOPWORDS: frozenset[str] = frozenset({
    "for", "the", "a", "an", "as", "to", "be", "is", "was", "are",
    "my", "your", "should", "would", "can", "could", "shall", "will",
    "also", "please", "thanks", "thank", "just",
})
_FRAGMENT_AFFIRM: frozenset[str] = frozenset({"yes", "yeah", "yep"})
_FRAGMENT_NEGATE: frozenset[str] = frozenset({"no", "not", "never"})


def _pure_fragment_verdict(
    clause: str, offer: dict[str, Any], all_offers: list[dict[str, Any]]
) -> bool | None:
    """Verdict from a PURE verdict fragment: a short clause that
    references exactly this one candidate and, once the anchor's
    name and stopwords are removed, reduces to a single verdict
    token ("David yes" -> True, "Maya no" -> False, "David should
    not" -> False). Anything with content left over defers:
    "Priya is not only executor" leaves {not, only, executor} and
    is a fact sentence, not a verdict (review C-4); a clause naming
    two anchors is neither's fragment (review I-1)."""
    if len(clause.split()) > 7:
        return None
    lowered = clause.lower()
    refs = [
        o
        for o in all_offers
        if o.get("bundle") != "address"
        and _references_offer(lowered, o, all_offers)
    ]
    if len(refs) != 1 or refs[0]["decline_key"] != offer["decline_key"]:
        return None
    tokens = set(_norm_tokens(clause))
    name_tokens = set(_norm_tokens(str(offer.get("anchor_name") or "").lower()))
    leftover = tokens - name_tokens - _FRAGMENT_STOPWORDS
    if len(leftover) != 1:
        return None
    token = next(iter(leftover))
    if token in _FRAGMENT_AFFIRM:
        return True
    if token in _FRAGMENT_NEGATE:
        return False
    return None


_BARE_VERDICT_EXTRA_TOKENS: frozenset[str] = frozenset({
    "actually", "well", "so", "now", "then", "however", "anyway",
    "instead", "hmm", "oh", "please",
})
_BARE_AFFIRM: frozenset[str] = _FRAGMENT_AFFIRM | {"sure"}


def _bare_verdict(clause: str) -> bool | None:
    """A clause that is ONLY a verdict: "Yes", "No", "Yes please",
    "Actually no". Short, and reduces (minus discourse/stopwords)
    to a single yes/no token. Anything with real content attached
    is not bare and never decides via this path."""
    if len(clause.split()) > 5:
        return None
    tokens = set(_norm_tokens(clause))
    leftover = tokens - _FRAGMENT_STOPWORDS - _BARE_VERDICT_EXTRA_TOKENS
    if len(leftover) != 1:
        return None
    token = next(iter(leftover))
    if token in _BARE_AFFIRM:
        return True
    if token in _FRAGMENT_NEGATE:
        return False
    return None


def _has_bundle_ref(lowered: str) -> bool:
    """A genuine bundle reference in a segment. "same person"
    counts only in its bundle form ("same person for all …") —
    as a bare identity statement ("Maya is the same person as the
    account holder") it is a fact, not a reference (round-4)."""
    import re

    scrubbed = re.sub(r"same person(?!\s+for\b)", "", lowered)
    return bool(re.search(_BUNDLE_REF_PATTERN, scrubbed))


_MODAL_TOKENS: frozenset[str] = frozenset({
    "should", "would", "could", "can", "shall", "must", "will",
})


def _offer_role_phrases(offer: dict[str, Any]) -> list[str]:
    """The offer's role names as PHRASES ("healthcare agent",
    "successor trustee", …) from its display names. Phrases, not
    single tokens: "agent" alone collides with "real estate
    agent" (round-4 C-R4-4)."""
    import re

    phrases: list[str] = []
    for fill_key in offer.get("fill_keys") or []:
        name = _BUNDLE_ROLE_NAMES.get(fill_key, "").lower()
        name = re.sub(r"^your\s+", "", name).strip()
        if name:
            phrases.append(name)
    return phrases


_SERVE_VERB_PATTERN = r"\b(serve|serves|serving)\b"
_SERVE_FRAME_PATTERN = _SERVE_VERB_PATTERN + r"(\s+\w+){0,2}?\s+(as|in)\b"
#: Words that may remain in a segment whose only content is the
#: anchor, a modal/negation, and the bare verb "serve".
_BARE_SERVE_RESIDUE_OK: frozenset[str] = (
    _FRAGMENT_STOPWORDS
    | _MODAL_TOKENS
    | {"no", "not", "never", "serve", "serves", "serving", "as", "in",
       "also", "both"}
)
_APPOINTMENT_MODALS: frozenset[str] = frozenset({
    "should", "would", "shall", "must", "can",
})


def _segment_bundle_scoped(
    lowered: str, offer: dict[str, Any], clause: str
) -> bool:
    """The segment ties its verb to THIS offer's bundle (round-4
    C-R4-3/4): a bundle reference, a serve-as/in frame, a
    be/serve frame carrying one of the offer's role PHRASES, or a
    bare "serve" whose residue is only the anchor + modal +
    negation (a complete decline/appointment: "David should not
    serve."). Bare action verbs with unrelated objects are NOT
    scope: "serve dinner", "serve on the PTA board", "handle the
    taxes", "use the car" all stay facts."""
    import re

    if _has_bundle_ref(lowered):
        return True
    if re.search(_SERVE_FRAME_PATTERN, lowered):
        return True
    phrases = _offer_role_phrases(offer)
    if phrases:
        alt = "|".join(re.escape(p) for p in phrases)
        if re.search(
            rf"\b(be|become|act|serve|serves|serving)\b[^.;,]{{0,40}}?\b({alt})\b",
            lowered,
        ):
            return True
    if re.search(_SERVE_VERB_PATTERN, lowered):
        tokens = set(_norm_tokens(clause))
        name_tokens = set(
            _norm_tokens(str(offer.get("anchor_name") or "").lower())
        )
        residue = tokens - name_tokens - _BARE_SERVE_RESIDUE_OK
        if not residue and (
            re.search(_BUNDLE_NEGATION_PATTERN, lowered)
            or tokens & {"should", "would", "shall", "must"}
        ):
            return True
    return False


def _modal_segment_verdict(
    clause: str, offer: dict[str, Any], all_offers: list[dict[str, Any]]
) -> bool | None:
    """A verdict from a modal segment that references exactly this
    offer AND is scoped to its bundle: "David should not serve as
    successor", "Maya should not be my healthcare agent", "David
    should serve." The scope gate is the discipline (round-4
    C-R4-3/4): bare `serve/use/handle` plus any affirm/neg token
    manufactured verdicts from factual clauses ("will definitely
    handle the taxes", "should serve dinner at seven"), so the
    verb must be tied to the bundle — and `_parse_yes_no`'s head
    patterns, which fire on those same facts, are never consulted
    here at all (round-3 C-2). Inside the gate: negation -> False;
    an explicit affirmative -> True; a plain appointment with an
    appointment modal -> True (so it registers against a stray
    bare verdict instead of losing to it — C-R4-5). Conditionals
    and "not only …" defer."""
    import re

    lowered = clause.lower()
    refs = [
        o
        for o in all_offers
        if o.get("bundle") != "address"
        and _references_offer(lowered, o, all_offers)
    ]
    if len(refs) != 1 or refs[0]["decline_key"] != offer["decline_key"]:
        return None
    if re.search(r"\bnot only\b", lowered):
        return None
    if re.search(_BUNDLE_UNCERTAINTY_PATTERN, lowered):
        return None
    if re.search(r"\bif\b", lowered):
        return None
    tokens = set(_norm_tokens(clause))
    if not (tokens & _MODAL_TOKENS):
        return None
    if not _segment_bundle_scoped(lowered, offer, clause):
        return None
    if re.search(_BUNDLE_NEGATION_PATTERN, lowered):
        return False
    if re.search(_BUNDLE_AFFIRMATIVE_PATTERN, lowered):
        return True
    if tokens & _APPOINTMENT_MODALS and not re.search(
        r"\bcan'?t\b|\bcannot\b", lowered
    ):
        if re.search(_SERVE_VERB_PATTERN, lowered) or _has_bundle_ref(lowered):
            return True
        phrases = _offer_role_phrases(offer)
        if phrases:
            alt = "|".join(re.escape(p) for p in phrases)
            if re.search(
                rf"\b(be|become|act)\b[^.;,]{{0,40}}?\b({alt})\b", lowered
            ):
                return True
    return None


_CONTRACTION_EXPANSIONS: tuple[tuple[str, str], ...] = (
    (r"\bwon'?t\b", "will not"),
    (r"\bcan'?t\b", "can not"),
    (r"\bcannot\b", "can not"),
    (r"\bshouldn'?t\b", "should not"),
    (r"\bwouldn'?t\b", "would not"),
    (r"\bcouldn'?t\b", "could not"),
    (r"\bdoesn'?t\b", "does not"),
    (r"\bdon'?t\b", "do not"),
    (r"\bdidn'?t\b", "did not"),
    (r"\bisn'?t\b", "is not"),
    (r"\baren'?t\b", "are not"),
    (r"\bwasn'?t\b", "was not"),
    (r"\bweren'?t\b", "were not"),
    (r"\bhaven'?t\b", "have not"),
    (r"\bhasn'?t\b", "has not"),
    (r"\bi'?m\b", "i am"),
    (r"\bi'?ll\b", "i will"),
    (r"\bi'?d\b", "i would"),
    (r"\bi'?ve\b", "i have"),
)


def _expand_contractions(text: str) -> str:
    """Expand contractions for the multi-offer resolver's token
    checks: "won't" must tokenize as will + not, or every
    negation-based veto is evaded by the most natural spelling
    (round-5 C5-1). Applied ONLY inside `_resolve_offer_verdicts`'
    multi-offer path — the single-offer `_bundle_verdict` input
    is unchanged."""
    import re

    out = text
    for pattern, replacement in _CONTRACTION_EXPANSIONS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


_RETRACTION_PHRASES: tuple[str, ...] = (
    "not her",
    "not him",
    "not them",
    "on second thought",
)

#: Dismissal verbs with an optional object and an optional
#: off/out particle ("Nevermind.", "Forget her.", "Ditch her.",
#: "Cross her off."): a retracting user dismisses a PERSON as
#: often as a thing, so the object slot is structural, not
#: enumerated (round-9 F3, round-10 C3 — the verb slot is the
#: dismissal register itself: forget / scratch / ditch / skip /
#: axe / nix / void / toss / drop / cross).
_RETRACTION_DISMISSAL_PATTERN = (
    r"\b(?:never\s?mind|forget\w*|scratch\w*|scrap\w*|ditch\w*|"
    r"skip\w*|axe\w*|nix\w*|void\w*|toss\w*|drop\w*|cross\w*)"
    r"(?:\s+(?:it|that|this|her|him|them))?(?:\s+(?:off|out))?\b"
)

#: Pronoun-particle dismissal idiom: <any verb> + pronoun
#: (incl. reflexives) + up to ten intervening tokens +
#: off/out ("Cut her off.", "Cut her completely and totally
#: off.", "Take her full legal name and official professional
#: title off.", "Cut herself off."). The verb slot and the
#: middle are open; the idiom's skeleton is the pronoun and
#: the particle (round-11 F3, round-12 C4, round-13 CR4,
#: round-15 CR15-6).
_PRONOUN_PARTICLE_PATTERN = (
    r"\b[a-z]+\s+(?:her|him|them|herself|himself|themselves)"
    r"(?:\s+[a-z]+){0,10}?\s+(?:off|out)\b"
)

#: Exclusion predicates: a negation governing one of these is
#: an INCLUSION ("should not be ruled out" affirms the
#: candidates — round-14 CR14-2; "overlooked" added round-15
#: CR15-2).
_EXCLUSION_PREDICATE_PATTERN = (
    r"\b(ruled?\s+out|left\s+out|passed\s+over|"
    r"excluded|exclusion|omitted|left\s+off|overlook\w*)\b"
)

#: Lexical retraction verbs, as STEMS: they carry no negation
#: token, so the pronoun+negation branch cannot see them ("She
#: declined to serve.", "He will withdraw.", "Strike her.").
#: Morphological families, not surface forms (round-7 C-R7-2,
#: round-8 C-R8-3). Used by the contamination veto and by the
#: per-offer signal for name-bearing retraction sentences —
#: both are vetoes, so breadth only ever defers.
_RETRACTION_VERB_PATTERN = (
    r"\b(declin\w*|refus\w*|withdr\w*|retract\w*|revok\w*|"
    r"resign\w*|retir\w*|quit(?:s|ting|ted)?\b|recuse\w*|renounce\w*|"
    r"abdicat\w*|bail\w*|pass\w* on|unwilling|unable|"
    r"cancel\w*|rescinded|remove\w*|"
    # Phrasal exits are a verb-family × particle product
    # (stepped down/back/aside, stood down, backed out/off,
    # walked away, gave up, bowed out — round-10 C2).
    r"(?:step|stood|stand|back|walk|bow|pull|drop|opt|bail|"
    r"giv|gave)\w*\s+(?:down|back|off|out|up|away|aside)|"
    r"want\w* out|(?:'s|is) out|strik\w*|struck|"
    r"cross\w* out|(?:take|took) [\w']+ off|"
    r"changed (?:her|his|their|my) mind|"
    r"count (?:her|him|them) out)\b"
)


def _message_contaminated(message: str, offers: list[dict[str, Any]]) -> bool:
    """A retraction-shaped sentence after the first that names NO
    candidate ("Actually no.", "She will not serve.", "Scratch
    that.", "Not her.") contaminates the whole message: every
    read defers. The check lives at MESSAGE level in
    `_resolve_offer_verdicts` so it gates the inheritance path
    too — a veto inside the scoped path alone was routed around
    by anchor-free global reads (round-5 C5-3). Over-firing is
    safe here: contamination can only ever DEFER."""
    import re

    text = message.strip()
    if not text:
        return False
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    for idx, sentence in enumerate(sentences):
        if idx == 0:
            continue
        lowered = sentence.lower()
        if any(_references_offer(lowered, o, offers) for o in offers):
            continue
        if any(
            # Token-bounded: "not her" must not match inside
            # "not here", nor "never mind" inside "never
            # minded" (round-8 M-R8-2).
            re.search(rf"\b{re.escape(phrase)}\b", lowered)
            for phrase in _RETRACTION_PHRASES
        ):
            return True
        if re.search(_RETRACTION_DISMISSAL_PATTERN, lowered):
            return True
        if re.search(_PRONOUN_PARTICLE_PATTERN, lowered):
            return True
        if re.search(_RETRACTION_VERB_PATTERN, lowered):
            return True
        # Sentence-level pronoun retraction: the pronoun and its
        # negation need not share a clause ("She changed her mind
        # and will not serve.", "She has decided not to serve.",
        # "She no longer serves.") — read the whole sentence
        # (round-6 C2).
        sentence_tokens = set(_norm_tokens(sentence))
        if (
            sentence_tokens & {"she", "he", "they", "her", "him"}
            and re.search(_BUNDLE_NEGATION_PATTERN, lowered)
        ):
            return True
        for clause in _split_clauses(sentence):
            if _bare_verdict(clause) is not None:
                return True
    # Cross-spliced pronoun clause (round-15 CR15-7): a clause
    # carrying a pronoun predication (modal or negated) inside
    # a sentence that references exactly ONE candidate names
    # nobody, yet its pronoun can only mean the OTHER candidate
    # — or the referenced one; the referent is undecidable, so
    # the whole message defers. The vetoes above all miss it:
    # contamination skips candidate-referencing sentences, the
    # spliced check requires the SAME offer's sentence, and the
    # standalone check requires every clause to be refs-free.
    person_offers = [o for o in offers if o.get("bundle") != "address"]
    if len(person_offers) >= 2:
        for sentence in sentences:
            lowered_s = sentence.lower()
            sent_refs = {
                o["decline_key"]
                for o in person_offers
                if _references_offer(lowered_s, o, offers)
            }
            if len(sent_refs) != 1:
                continue
            for clause in _split_clauses(sentence):
                cl = clause.lower()
                if any(
                    _references_offer(cl, o, offers) for o in person_offers
                ):
                    continue
                ctoks = set(_norm_tokens(clause))
                if ctoks & _SPLICED_PRONOUNS and (
                    ctoks & _MODAL_TOKENS
                    or re.search(_BUNDLE_NEGATION_PATTERN, cl)
                ):
                    return True
    return False


def _address_offer_framed(sentence: str) -> bool:
    """The sentence is ABOUT the address offer (round-6 C4): it
    carries a bundle reference, or a use-frame with the address
    as its object ("use my address", "address … be used"). A
    factual predicate about the address itself ("The address
    does not handle mail anymore.") has none of these and must
    not be fed to `_bundle_verdict`, whose short-head fallback
    would read its "does not" as a decline."""
    import re

    lowered = sentence.lower()
    if _has_bundle_ref(lowered):
        return True
    if re.search(
        r"\buse\s+(my|the|that|this|our|your)(\s+\w+){0,4}?\s+address\b",
        lowered,
    ):
        return True
    if re.search(r"\baddress\b(\s+\w+){0,5}?\s+used\b", lowered):
        return True
    return False


#: Conditional markers for an instruction: an instruction
#: qualified by any of these is not an unconditional verdict
#: (round-15 CR15-9, form class widened round-16 CR16-5 —
#: "in case you need one" is the same condition as "if you
#: need one", and the inverted "should you need one" carries
#: no conditional token at all in the old four-word list).
_CONDITIONAL_PATTERN = (
    r"\b(if|unless|when|whenever|in\s+case|as\s+needed|"
    r"as\s+long\s+as|provided|providing|should\s+(?:you|we|it|they))\b"
)

#: Verb stems for the pronoun/demonstrative reference gate
#: below: use, placement, exclusion, and removal verbs as
#: STEMS, so gerunds and inflections are the same family
#: ("removing", "excluding", "deleted").
_ADDR_REF_VERB_STEMS = (
    r"(?:us(?:e|ed|ing)|put\w*|plac\w*|go(?:es)?|enter\w*|list\w*|"
    r"writ\w*|ban\w*|barr\w*|belong\w*|off|out|leav\w*|exclud\w*|"
    r"omit\w*|remov\w*|includ\w*|add\w*|drop\w*|tak\w*|keep\w*|"
    r"delet\w*|eras\w*|pull\w*|strik\w*|skip\w*)"
)

#: Definite place descriptions that denote the address
#: offer when a reference verb governs them ("Do not use the
#: place.", "Exclude my home.", "Never use my residence."):
#: in a merged-offer reply the only place-like referent on
#: the table is the address (round-24 CR24-4).
_ADDR_PLACE_DESCRIPTION = (
    r"\b(?:the|my|our|that|this)"
    # A modifier run may sit between the determiner and the
    # noun ("the mobile home", "my studio apartment", "the
    # detached garage apartment") — adjacency defeated the
    # listed nouns in their most common compound forms
    # (round-26 CR26-1). Modifier tokens admit digits
    # (bedroom counts and ordinals are the canonical
    # descriptors — round-27 CR27-1), and the noun may join
    # the last modifier by a hyphen ("the mobile-home").
    r"(?:\s+[a-z0-9][a-z0-9-]*){0,3}?(?:\s+|-)"
    r"(?:place|home|residence|house|properties|property|"
    r"apartment|unit|"
    r"condo|townhouse|townhome|duplex|loft|cottage|dwelling|"
    r"bungalow|cabin|studio|farmhouse|trailer|penthouse|"
    r"brownstone|ranch|suite|guesthouse)s?\b"
)


def _address_clause_vetoes_borrow(
    clause: str,
    offer: dict[str, Any],
    siblings: list[str] = (),
) -> bool:
    """Whether a clause kills a provisional borrowed Yes for
    the address offer. The burden is INVERTED and the benign
    set is purely SYNTACTIC (round-21 CR21-1 → round-22
    CR22-1/2: every semantic benign class was a hole — the
    copula class was predicate-blind ("My address is a no."),
    the plainly-affirmative class was cessation-lexeme-bound
    ("Forget using my address.")): the borrow is the weakest
    read in the resolver, so it survives only alongside
    PROPERTY FACTS in one of two fact syntaxes —

    - the fact passive: "address is/was/has been (not) used
      …" (no "be" — an instruction passive is a decision,
      not a fact, and the direct reads own it); or
    - a property-verb fact: "(does|do|did) not
      handle/receive/accept/get/deliver …".

    A clause REFERENCES the offer if it says "address",
    names a distinctive anchor token (the street number or
    name, the city, the state — "Do not use 123 Main
    Street.", "Exclude the Austin one."), or points at it
    with "it" / "that" / "the other one" plus a
    use/exclusion/removal verb (round-23 CR23-1/2). An F1
    fact with an explicit exclusion tail in a sibling clause
    ("…is used for identification only, never for
    documents") is a scoped restriction, not a benign fact
    (round-23 CR23-3). Any other referencing clause that no
    direct read decided vetoes the borrow, whatever dress
    its statement wears, affirmative included: an
    undecidable sentence about the address means the message
    is no longer the simple cooperative shape the borrow
    exists for, and the offer is re-asked (the safe
    failure)."""
    import re

    cl = clause.lower()
    if "address" in cl:
        referenced = True
    else:
        anchor_tokens = {
            t
            for t in _norm_tokens(
                str(offer.get("anchor_name") or "").lower()
            )
            if len(t) >= 4 or t.isdigit()
        }
        if anchor_tokens & set(_norm_tokens(clause)):
            referenced = True
        elif re.search(r"\bit\b", cl) and re.search(
            rf"\b{_ADDR_REF_VERB_STEMS}\b", cl
        ):
            referenced = True
        elif (
            re.search(r"\b(?:that|this)\b", cl) or "other one" in cl
        ) and re.search(rf"\b{_ADDR_REF_VERB_STEMS}\b", cl):
            referenced = True
        elif re.search(
            # The v7 presentation numbers the offers and
            # invites the user to "answer each one
            # separately", so an ordinal reference ("the 2nd
            # one", "number two", "#2", "the last one") is a
            # presentation-canonical way to decline one of
            # them (round-28 CR28-1). Deliberately coarse,
            # like every veto reference: any ordinal decline
            # defers the borrowed verdict; direct verdicts
            # are untouched.
            r"(?:\b(?:\d+(?:st|nd|rd|th)|first|second|third|"
            r"fourth|fifth|last)\s+one\b"
            r"|\bnumber\s+(?:\d+|one|two|three|four|five)\b"
            r"|#\s*\d+)",
            cl,
        ) and re.search(rf"\b{_ADDR_REF_VERB_STEMS}\b", cl):
            referenced = True
        elif re.search(
            _ADDR_PLACE_DESCRIPTION, cl
        ) and re.search(rf"\b{_ADDR_REF_VERB_STEMS}\b", cl):
            referenced = True
        else:
            return False
    f1_match = re.search(
        r"\baddress\s+(?:is|was|has\s+been|had\s+been)\s+"
        r"(?:not\s+|never\s+)?used\b",
        cl,
    )
    if f1_match:
        # F1 fact passive — benign UNLESS the sentence tail
        # after the match (the rest of this clause plus every
        # sibling clause) carries an exclusion (never / not /
        # excluding / except), which makes the fact a scoped
        # restriction on exactly this fill. The scan starts
        # AFTER the matched "used": the fact's own negation
        # ("is NOT used for packages") precedes it and is the
        # benign borrow-source shape (round-23 CR23-3 →
        # round-24 CR24-2: the tail may be packaged in the
        # same clause, reordered, or elliptical).
        tail = cl[f1_match.end():] + " " + " ".join(
            s.lower() for s in siblings
        )
        return bool(
            re.search(
                r"\b(?:never|not|no|exclud\w*|except|off)\b", tail
            )
        )
    if re.search(
        r"\b(?:does|do|did)\s+not\s+"
        r"(?:handle|receive|accept|get|deliver)\b",
        cl,
    ):
        return False
    return True


def _address_scoped_verdict(
    offer: dict[str, Any],
    message: str,
    all_offers: list[dict[str, Any]],
) -> bool | None:
    """The address offer's verdict: decided only by a head-block
    sentence about the address itself that names no person
    (round-2 C-6) AND is framed as an answer to the offer
    (round-6 C4), read with the trusted `_bundle_verdict` on
    that single sentence."""
    import re

    text = message.strip()
    if not text:
        return None
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    person_offers = [o for o in all_offers if o.get("bundle") != "address"]
    # A borrowed verdict is PROVISIONAL (round-18 CR18-2): it is
    # recorded, not returned, and any direct verdict in a later
    # sentence overrides it. A negated address instruction
    # anywhere else in the message vetoes it outright
    # (round-19 CR19-1, generalized round-20 CR20-1).
    provisional: bool | None = None
    provisional_idx: int | None = None
    for s_idx, sentence in enumerate(sentences):
        if (
            "?" in sentence
            or "？" in sentence
            or sentence.lstrip().startswith("¿")
        ):
            continue  # a question is not a decision (round-8 C-R8-4)
        lowered = sentence.lower()
        if re.search(_HEDGE_FRAME_PATTERN, lowered):
            # Reported or hedged speech is not the user's
            # instruction ("He said the address should be
            # used." persisted an accept in round 16, CR16-2):
            # the fragment decider has always had this gate;
            # the address read gets it at the same scope.
            continue
        if re.search(_CONDITIONAL_PATTERN, lowered):
            # A conditional anywhere in the sentence qualifies
            # every instruction in it ("My address should be
            # used if you need it." — round-16 CR16-1): the
            # offer defers rather than persisting a conditional
            # as unconditional.
            continue
        if "address" not in lowered:
            continue
        # Clause-scoped read (round-7 C-R7-4): framing the whole
        # sentence and feeding it entire let a factual clause
        # inside it decline the offer ("Use my address, it does
        # not handle mail."). Only framed clauses are read.
        clauses = _split_clauses(sentence)
        bare = [c for c in clauses if _bare_verdict(c) is not None]
        framed_count = sum(
            1 for c in clauses if _address_offer_framed(c)
        )
        if any(
            _references_offer(lowered, p, all_offers) for p in person_offers
        ):
            continue
        if _has_person_shape(sentence):
            continue
        if _scoped_uncertain(lowered):
            continue
        for clause_idx, clause in enumerate(clauses):
            if not _address_offer_framed(clause):
                continue
            read_text = clause
            # Sever candidates (round-8 C-R8-5 → round-9 F5 →
            # round-10 C5 → round-11 F5): a subordinator or
            # relative anywhere, or a jammed second finite
            # predication — detected by its negated auxiliary,
            # whatever the subject is ("… address mail does
            # not arrive …", "It does not handle packages use
            # my address."). The sever only happens when the
            # tail carries a negation, so determiner "that"
            # ("use that address") and innocent tails read
            # whole.
            cuts: list[int] = []
            sub = re.search(
                r"\b(that|which|whose|who|though|although|but|"
                r"since|because|as|while|when|whenever|if|unless|"
                r"where|whereas)\b|[();—]",
                clause,
            )
            if sub:
                cuts.append(sub.start())
            addr_at = clause.lower().find("address")
            if addr_at >= 0:
                # The predication search covers the whole
                # clause — a jammed fact may precede the frame
                # ("It does not handle packages use my
                # address." — round-11 F5) — EXCEPT when the
                # clause head is itself the imperative frame:
                # its negation is subject-less and belongs to
                # the instruction ("Please do not use my
                # address."), so there the search starts after
                # the frame.
                head_frame = re.match(
                    r"\s*(please\s+)?(do not|don't|never)"
                    r"(\s+[a-z]+){0,3}?\s+use\s+"
                    r"(my|the|that|this|our|your)(\s+[a-z]+){0,4}?\s+"
                    r"address\b",
                    clause.lower(),
                )
                start = addr_at if head_frame else 0
                pred = re.search(
                    r"\b\w+\s+(?:does|do|did|is|are|was|were|has|"
                    r"have)\s+not\b"
                    r"|\b\w+\s+(?:cannot|can't|won't|doesn't|don't|"
                    r"didn't|isn't|aren't|wasn't|weren't|hasn't|"
                    r"haven't)\b"
                    r"|\b(it|this|there)\s+(does|do|did|is|are|was|"
                    r"were|has|have)\b",
                    clause[start:],
                )
                if pred:
                    cuts.append(start + pred.start())
            severed_at: int | None = None
            # Set when a severed PASSIVE tail deferred on a
            # double negation: the tail was instruction-shaped
            # but undecidable, and the bare-Yes borrow must not
            # resurrect it over the emptied head (round-18
            # CR18-3 — the borrow's input no longer shows the
            # tail that deferred).
            tail_deferred = False
            if cuts:
                cut = min(cuts)
                if re.search(
                    _BUNDLE_NEGATION_PATTERN, clause[cut:].lower()
                ):
                    # A negation-bearing subordinate segment
                    # inside the framed clause is a fact about
                    # the address ("Use my address that does not
                    # handle packages."), not the direction given
                    # to the offer — sever it before the read.
                    read_text = clause[:cut].strip(" ,")
                    severed_at = cut
            if severed_at is not None:
                # The severed tail may carry the clause's OWN
                # address instruction ("… do not use my
                # address." after a jammed fact): read the tail
                # from its frame before anything borrows context
                # into the emptied head — a bare "Yes" must not
                # be lent over an explicit "do not use"
                # (round-12 C6).
                frame_match = re.search(
                    r"(?:please\s+)?(?:do not\s+|don't\s+|never\s+)?"
                    r"(?:[a-z]+\s+){0,2}?use\s+"
                    r"(?:my|the|that|this|our|your)(?:\s+[a-z]+){0,4}?\s+"
                    r"address\b"
                    r"|(?:my|the|that|this|our|your)(?:\s+[a-z]+){0,4}?\s+"
                    r"address\s+(?:should|must|can|could|will|would)"
                    r"\s+(?:(?:not|never)(?:\s+[a-z]+){0,4}?\s+)?be\s+used\b",
                    clause.lower(),
                )
                if frame_match and frame_match.start() >= severed_at:
                    segment = clause[frame_match.start():]
                    if re.search(r"\bbe\s+used\b", segment):
                        # A passive instruction ("my address
                        # should not be used.") decides by its
                        # own polarity — the frozen parser does
                        # not read passive segments, and a bare
                        # "Yes" must not be borrowed over it
                        # (round-13 CR9). Two or more negation
                        # tokens in the segment are a double
                        # negation and defer, exactly as in the
                        # unsevered read (round-17 CR17-3).
                        neg_count = len(
                            re.findall(
                                r"\b(?:not|never)\b", segment.lower()
                            )
                        )
                        if neg_count > 1:
                            tail_deferred = True
                        tail_verdict = (
                            None
                            if neg_count > 1
                            else neg_count == 0
                        )
                    else:
                        tail_verdict = _bundle_verdict(segment)
                    if tail_verdict is not None:
                        return tail_verdict
            verdict: bool | None = None
            if severed_at is None and framed_count == 1:
                # An unsevered passive instruction ("My old
                # primary home address should not be used.")
                # decides by its own polarity, exactly like a
                # severed tail (round-15 MN15-1): the frozen
                # parser does not read passive segments at all,
                # so without this read the verdict is simply
                # lost. The adjective slot is {0,4} — at {0,2}
                # a three-adjective tail also let a bare "Yes"
                # borrow OVER an explicit decline (round-15
                # CR15-5). Two disciplines the first version of
                # this read lacked (round-16 CR16-3): it fires
                # only when the sentence carries a SINGLE
                # framed clause (a second framed clause — "Use
                # my address, the address should not be used
                # for packages." — makes the reads conflict in
                # scope, and combined reads defer), and its
                # polarity comes from the
                # MATCHED FRAME's own negation slot, never from
                # the clause remainder ("…should be used not
                # for mail but for identification" affirms).
                # The negation slot tolerates an adverb
                # ("should not ever be used" — MN16-1).
                passive_match = re.search(
                    r"(?:my|the|that|this|our|your)(?:\s+[a-z]+){0,4}?\s+"
                    r"address\s+(?:should|must|can|could|will|would)"
                    r"\s+(?P<neg>(?:not|never)(?:\s+[a-z]+){0,4}?\s+)?"
                    r"be\s+used\b",
                    read_text.lower(),
                )
                if passive_match:
                    neg_text = passive_match.group("neg")
                    if neg_text and len(
                        re.findall(r"\b(?:not|never)\b", neg_text)
                    ) > 1:
                        # A second negation inside the slot is
                        # a double negation ("should never not
                        # be used") — an affirmation at best,
                        # undecidable here; it must not read as
                        # a single decline (round-17 CR17-3).
                        verdict = None
                    else:
                        verdict = neg_text is None
            if verdict is None:
                verdict = _bundle_verdict(read_text)
            if verdict is None and read_text.lower().lstrip().startswith(
                "please"
            ):
                # The frozen parser's head read drops a decline
                # behind a leading courtesy ("Please never use
                # my address." reads None where "Never use my
                # address." reads False) — retry the framed
                # clause without the courtesy token.
                verdict = _bundle_verdict(
                    re.sub(
                        r"^\s*please\b", "", read_text, flags=re.IGNORECASE
                    )
                )
            if (
                verdict is None
                and severed_at is None
                and len(clauses) == 1
                and not bare
                and not re.search(
                    _BUNDLE_NEGATION_PATTERN, read_text.lower()
                )
                and not re.search(_CONDITIONAL_PATTERN, read_text.lower())
                and re.search(
                    r"^\s*(?:please\s+)?use\s+"
                    r"(?:my|the|that|this|our|your)"
                    r"(?:\s+[a-z]+){0,4}?\s+address\b"
                    r"(?=\s*(?:$|[.,;:!?)]|\s+(?:for|as|on|in|to|"
                    r"of|when|if|but|and|because|with|from)\b))",
                    read_text.lower(),
                )
            ):
                # A bare affirmative use-instruction with no
                # sever and no negation ("Use my address.",
                # "Please use my address.") is itself the
                # verdict — the frozen parser needs a verdict
                # token or a bundle ref and reads it as None
                # (round-14 MN14-2). A severed head never gets
                # this rule: its instruction may sit in the
                # severed tail, where the pinned behavior is
                # deferral unless an explicit bare "Yes"
                # borrows (round-8 C-R8-5). A CONDITIONAL
                # instruction ("Use my address if needed.",
                # "Use my address (if you need one).") is not
                # an unconditional accept and never gets this
                # rule either (round-15 CR15-9). And the clause
                # must carry an actual USE-INSTRUCTION: the
                # used-frame also frames plain facts ("My
                # address was used last year."), and a statement
                # of past usage is not consent (round-17
                # CR17-1, the round-6 C4 fact class). The frame
                # is imperative-ANCHORED at the clause start: a
                # declarative subject or modal in front of it
                # ("They use my address for packages.", "They
                # can use my address.") is the same fact class,
                # not an instruction (round-18 CR18-1).
                verdict = True
            if verdict is not None:
                return verdict
            if bare and all(_bare_verdict(c) is True for c in bare):
                # Bare context may only lend an AFFIRMATIVE, and
                # only an affirmative read is borrowed: a leading
                # "No" aimed at the person offers must not flip
                # an affirmative address instruction into a
                # decline (round-8 C-R8-6). A decline must come
                # from the framed clause itself. And the borrow
                # must not SHORT-CIRCUIT a later framed clause
                # carrying its own verdict or a negation ("Yes,
                # my address is not used for packages, do not
                # use my address." borrowed True off the severed
                # fact clause's emptied head and the explicit
                # decline was never read — round-17 CR17-2,
                # defeating round-12 C6).
                # Benign-burden guard (round-20 CR20-1 →
                # round-21 CR21-1): the borrow fires only if
                # every OTHER non-bare clause of this sentence
                # is benign for the address (a property fact
                # or a plainly affirmative instruction). The
                # borrow clause itself is excluded: its own
                # severed fact head ("…is not used for
                # packages") is a property fact, and the direct
                # reads have already had their say over it. A
                # later clause carrying its own direct verdict
                # needs no block here — the loop reaches it and
                # it decides on its own terms.
                instruction_blocked = any(
                    _address_clause_vetoes_borrow(
                        c,
                        offer,
                        [
                            s
                            for k, s in enumerate(clauses)
                            if k != j
                        ],
                    )
                    for j, c in enumerate(clauses)
                    if j != clause_idx and _bare_verdict(c) is None
                )
                if not instruction_blocked and not tail_deferred:
                    borrowed = _bundle_verdict(
                        " ".join([*bare, read_text])
                    )
                    if borrowed is True and provisional is None:
                        # Provisional (round-18 CR18-2): keep
                        # scanning — a direct verdict in any
                        # later sentence overrides the borrow.
                        provisional = True
                        provisional_idx = s_idx
                        break
    if provisional is not None:
        # Post-pass veto (round-20 CR20-1): a negated address
        # instruction in any OTHER sentence of the message —
        # including pronoun-only sentences the scoped read never
        # reaches ("It is not to be used.") — kills the
        # provisional borrow. Reported or qualified sentences
        # are skipped exactly as in the main loop, so a hedge
        # or a conditional never vetoes either.
        for o_idx, other in enumerate(sentences):
            if o_idx == provisional_idx:
                continue
            o_lowered = other.lower()
            if (
                "?" in other
                or "？" in other
                or other.lstrip().startswith("¿")
                or (
                    re.search(_HEDGE_FRAME_PATTERN, o_lowered)
                    # A first-person denial is the user's OWN
                    # speech act, not reported speech ("I never
                    # said to use my address." — round-22
                    # MN22-3): it is not skipped.
                    and not re.search(
                        r"\bi\s+(?:never\s+)?(?:said|told|asked)\b",
                        o_lowered,
                    )
                )
                or re.search(_CONDITIONAL_PATTERN, o_lowered)
            ):
                continue
            other_clauses = _split_clauses(other)
            if any(
                _address_clause_vetoes_borrow(
                    c,
                    offer,
                    [
                        s
                        for k, s in enumerate(other_clauses)
                        if k != j
                    ],
                )
                for j, c in enumerate(other_clauses)
            ):
                return None
    return provisional


#: Frames that keep a sentence from deciding a fragment:
#: wondering/whether reported questions, uncertainty markers,
#: and speech attribution — the fragments inside them are
#: reported, not decided (round-8 C-R8-4 listed two frames;
#: round-9 F4 makes it a form class).
_HEDGE_FRAME_PATTERN = (
    r"\b(wonder|wondering|wondered|whether|not sure|unsure|"
    r"uncertain|maybe|perhaps|i guess|i suppose|i think|"
    r"i believe|i hope|said|says|asked|replied|answered|told|"
    # Evidentials, seeming, and the wider attribution stems —
    # the frame class is reported speech / hedged stance, not
    # six verbs (round-10 C4).
    r"apparently|supposedly|reportedly|allegedly|evidently|"
    r"presumably|seemingly|seem\w*|curious|think\w*|thought|"
    r"claim\w*|mention\w*|note\w*|text\w*|according to|"
    r"i heard|i read|(?:look|sound|feel)\w*\s+like|"
    # Finite attribution verbs are frames in any tense
    # (round-12 C5: "Priya suggested, Maya yes, David no.").
    r"suggest\w*|decid\w*|recommend\w*|advis\w*|report\w*|"
    r"stat\w*|pick\w*|"
    r"(?:check|confirm|verify|ask|asking)\s+(?:if|whether))\b"
)

#: Auxiliary / copula tokens: a clause built on one of these is
#: a finite predication (used by the fragment frame test).
_PREDICATION_AUX: frozenset[str] = _MODAL_TOKENS | {
    "is", "are", "was", "were", "be", "been", "being", "has",
    "have", "had", "does", "do", "did", "may", "might",
}

#: Sentence-initial interrogative auxiliaries / question words:
#: an unpunctuated question ("Is Maya yes, David no") is still
#: a question (round-9 F4).
_INTERROGATIVE_OPENERS: frozenset[str] = frozenset({
    "is", "are", "was", "were", "do", "does", "did", "can",
    "could", "should", "would", "will", "what", "who", "whom",
    "when", "where", "why", "how", "may", "might", "shall",
})

def _offer_fragment_verdict(
    offer: dict[str, Any],
    message: str,
    all_offers: list[dict[str, Any]],
) -> bool | None:
    """The ONLY per-offer decider in the multi-offer path (v6
    contract): a pure verdict fragment — a clause referencing
    exactly this offer that reduces to the anchor's name plus a
    single verdict token ("Maya yes", "David no"). Conflicting
    fragments for the same offer defer."""
    import re

    text = message.strip()
    if not text:
        return None
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    offers_by_first: dict[str, list[dict[str, Any]]] = {}
    for o in all_offers:
        name_tokens = _norm_tokens(o.get("anchor_name") or o.get("label") or "")
        if name_tokens:
            offers_by_first.setdefault(name_tokens[0], []).append(o)
    def _frame_only(s: str) -> bool:
        """A sentence that is only a reporting frame: it
        matches the hedge/attribution lexicon and carries no
        fragment or bare verdict of its own. Such a sentence
        scopes over an ADJACENT verdict sentence as surely as
        over its own clauses (round-13 CR5: "Maya yes, David
        no. As Priya recommended."). First-person DELIBERATION
        is not a report, at any length: "No decision on David
        yet, let me think about the successor roles…" is the
        user thinking, and the verdict sentence next to it
        still decides (round-14 CR14-5)."""
        # Deliberation markers disqualify the frame only when
        # they are the USER's own deliberation. A marker inside
        # reported speech ("As Priya said I should choose.") is
        # the reporter's words — the attribution verb precedes
        # it — and the frame still scopes (round-15 CR15-4).
        _delib = list(
            re.finditer(
                r"\b(let me|i need|i want|i have to|i should|"
                r"no decision|not decided|my decision)\b",
                s.lower(),
            )
        )
        if _delib:
            _attr = re.search(
                r"\b(said|says|told|asked|replied|answered|"
                r"recommend\w*|suggest\w*|advis\w*|mention\w*|"
                r"claim\w*|according to)\b",
                s.lower(),
            )
            if not (
                _attr
                and all(m.start() > _attr.start() for m in _delib)
            ):
                return False
        if not re.search(_HEDGE_FRAME_PATTERN, s.lower()):
            return False
        return not any(
            any(
                _pure_fragment_verdict(c, o, all_offers) is not None
                for o in all_offers
            )
            or _bare_verdict(c) is not None
            for c in _split_clauses(s)
        )

    values: set[bool] = set()
    for s_idx, sentence in enumerate(sentences):
        if (s_idx > 0 and _frame_only(sentences[s_idx - 1])) or (
            s_idx + 1 < len(sentences)
            and _frame_only(sentences[s_idx + 1])
        ):
            continue
        opener = re.match(r"[\s¿\"'“”«]*([a-z']+)", sentence.lower())
        opener_blocked = False
        if opener and opener.group(1) in _INTERROGATIVE_OPENERS:
            word = opener.group(1)
            if word not in offers_by_first:
                opener_blocked = True
            elif not any(
                _pure_fragment_verdict(clause, o, all_offers) is not None
                for clause in _split_clauses(sentence)
                for o in offers_by_first[word]
            ):
                # The opener-as-name exception holds only when
                # this sentence actually carries a pure fragment
                # for the anchor it names ("Will K Smith yes.");
                # a question that merely starts with the name
                # ("Will David no") decides nothing (round-10 C4).
                opener_blocked = True
        if (
            "?" in sentence
            or "？" in sentence
            or sentence.lstrip().startswith("¿")
            or '"' in sentence
            or "“" in sentence
            or "”" in sentence
            or "‘" in sentence
            or "«" in sentence
            or re.search(r"['’»][.!?;:,]*$", sentence.rstrip())
            or (
                sentence.lstrip().startswith("'")
                and sentence.count("'") >= 2
            )
            or opener_blocked
            or re.search(_HEDGE_FRAME_PATTERN, sentence.lower())
        ):
            # Sentence-scoped: no fragment decides inside a
            # question sentence — in "Is Maya yes, David no?" the
            # "?" lands in the last clause, but the whole sentence
            # is interrogative (round-6 C3, round-7 C-R7-3) — nor
            # inside a hedged, attributed, or quoted frame ("I
            # was wondering whether Maya yes, David no.", "He
            # said Maya yes, David no." — round-8 C-R8-4,
            # round-9 F4). An unpunctuated question is caught by
            # its interrogative opener.
            continue
        clauses = _split_clauses(sentence)
        # Frame-clause rule (round-11 F4): a fragment decides
        # only in a sentence with no FRAME clause. A clause is
        # a frame when it is not itself a fragment, a bare
        # verdict, a bundle-reference clause, or a courtesy
        # clause, AND it either matches the hedge/attribution
        # lexicon or is a verbless clause referring to no
        # candidate ("According to Priya", "Per Priya,"). A
        # candidate predication ("Maya can serve") is a
        # companion, not a frame — the signal machinery reads
        # those; a frame scopes over the whole sentence.
        def _frame_clause(c: str) -> bool:
            if any(
                _pure_fragment_verdict(c, o, all_offers) is not None
                for o in all_offers
            ):
                return False
            if _bare_verdict(c) is not None:
                return False
            if _has_bundle_ref(c.lower()):
                return False
            ctoks = set(_norm_tokens(c))
            if ctoks <= (_NAME_CLAUSE_FUNCTION_TOKENS | {"you"}):
                return False
            if re.search(_HEDGE_FRAME_PATTERN, c.lower()):
                return True
            if any(
                _references_offer(c.lower(), o, all_offers)
                for o in all_offers
            ):
                return False  # a candidate predication is a companion
            verbish = bool(ctoks & _PREDICATION_AUX) or any(
                t.endswith(("ed", "ing")) for t in ctoks
            )
            if not verbish:
                return True
            # Verbish and candidate-free: a companion only in
            # the modal register about a person ("Carlos can
            # serve") — the signal machinery's register. A
            # finite predication about a third party or a
            # thing ("Priya proposed", "the house has a
            # garage") is prose framing the verdict sentence
            # (round-13 CR6).
            if _has_person_shape(c) and (ctoks & _MODAL_TOKENS):
                return False
            return True

        if any(_frame_clause(c) for c in clauses):
            continue
        for clause in clauses:
            if "¿" in clause:
                continue  # a mid-sentence ¿ scopes to its clause
            verdict = _pure_fragment_verdict(clause, offer, all_offers)
            if verdict is not None:
                values.add(verdict)
    if len(values) == 1:
        return values.pop()
    return None


def _offer_modal_signal(
    offer: dict[str, Any],
    message: str,
    all_offers: list[dict[str, Any]],
) -> set[bool]:
    """Decisive-looking modal language about exactly this offer,
    as a SIGNAL SET. Signals never decide (v6 contract): a signal
    that disagrees with the offer's fragment vetoes it, and any
    signal blocks the anchor-free inheritance (the message is
    differentiated). The signal sources are the v5 scope-gated
    modal read plus a broad modal+negation trigger — vetoes may
    over-fire safely, since their only effect is deferral."""
    import re

    text = message.strip()
    if not text:
        return set()
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    def _retraction_shaped_clause(clause: str, refs: list) -> bool:
        """A refs-free clause shaped like a retraction of
        somebody's service: a retraction verb / dismissal /
        pronoun-particle form, or pronoun + modal negation —
        with a pronoun subject, or subjectless and short
        (subject ellipsis: "just resigned.", "will not
        serve.")."""
        if refs:
            return False
        low = clause.lower()
        toks = set(_norm_tokens(clause))
        verb_shaped = bool(
            re.search(_RETRACTION_VERB_PATTERN, low)
            or re.search(_RETRACTION_DISMISSAL_PATTERN, low)
            or re.search(_PRONOUN_PARTICLE_PATTERN, low)
        )
        modal_neg = bool(toks & _MODAL_TOKENS) and bool(
            re.search(_BUNDLE_NEGATION_PATTERN, low)
        )
        if not (verb_shaped or modal_neg):
            return False
        if toks & _SPLICED_PRONOUNS:
            return True
        # Subject ellipsis from here: the missing subject is
        # the sentence's candidate. A bundle-referencing tail
        # ("will not serve in any of those roles") carries no
        # token cap.
        if _has_bundle_ref(low):
            return True
        # A sentence-initial capitalized VERB or modal is not
        # a person ("Resigned last week.", "Will not serve."):
        # judge person shape on the remainder when the first
        # token is a retraction stem or a modal (round-14
        # CR14-3). The head-exit check runs BEFORE the token
        # cap and covers the phrasal family initials and
        # "unable"/"retired": a time adverbial must not push a
        # stem-headed form over the cap, and a capitalized
        # phrasal head ("Backed out.", "Stands down.") is a
        # verb, not a name (round-15 CR15-3).
        ordered = _norm_tokens(clause)
        parts = clause.split(None, 1)
        if ordered and len(parts) == 2 and (
            ordered[0] in _MODAL_TOKENS
            or re.match(
                r"^(resign|withdr|step|stood|stand|declin|refus|"
                r"retract|revok|cancel|remov|quit|recuse|"
                r"renounce|abdicat|bail|back|bow|walk|pull|"
                r"gave|giv|unable|retir)",
                ordered[0],
            )
        ):
            return not _has_person_shape(parts[1])
        if len(toks) > 4:
            return False
        return not _has_person_shape(clause)

    sentence_data = []
    for sentence in sentences:
        clauses = _split_clauses(sentence)
        clause_refs = [
            [
                o
                for o in all_offers
                if o.get("bundle") != "address"
                and _references_offer(clause.lower(), o, all_offers)
            ]
            for clause in clauses
        ]
        sentence_data.append((clauses, clause_refs))
    # A standalone retraction sentence anywhere in the block —
    # including BEFORE the verdict sentence, which the
    # contamination veto's after-the-first scope never reaches
    # ("She just resigned. Maya yes, David yes.") — vetoes
    # every offer the message references (round-13 CR3).
    values: set[bool] = set()
    if any(
        o["decline_key"] == offer["decline_key"]
        for _, clause_refs in sentence_data
        for refs in clause_refs
        for o in refs
    ) and any(
        all(not refs for refs in clause_refs)
        and any(
            _retraction_shaped_clause(c, [])
            for c in clauses
        )
        for clauses, clause_refs in sentence_data
    ):
        values.add(False)
    for clauses, clause_refs in sentence_data:
        # Sentence-scoped polarity (round-15 CR15-1/CR15-2):
        # the negation governing a modal need not sit in the
        # modal's own comma-clause ("David will, in the end,
        # not serve.", "David will serve; not in those
        # roles."), so polarity is read over the whole
        # sentence. A negation is flipped (it affirms) only
        # when it governs an exclusion predicate — the
        # predicate in its own clause, or in a following
        # clause reachable across only refs-free, person-free
        # insertions ("should not be, in my view, ruled out",
        # "should not, under any circumstances, be left out"):
        # a parenthetical must not sever the flip, and a
        # genuine negation in another clause must not be
        # flipped by a stranger's exclusion.
        def _negation_flipped(idx: int) -> bool:
            if re.search(
                _EXCLUSION_PREDICATE_PATTERN, clauses[idx].lower()
            ):
                return True
            for j in range(idx + 1, len(clauses)):
                if clause_refs[j] or _has_person_shape(clauses[j]):
                    break
                if re.search(
                    _EXCLUSION_PREDICATE_PATTERN, clauses[j].lower()
                ):
                    return True
            return False

        sentence_negated = any(
            re.search(_BUNDLE_NEGATION_PATTERN, c.lower())
            and not _negation_flipped(i)
            for i, c in enumerate(clauses)
        )
        # The sentence's joint modal polarity, for name-only
        # clauses split off a joint subject by the clause
        # splitter ("Maya" out of "Maya and David should
        # serve."): the name clause shares the predication it
        # was split from (round-14 CR14-1).
        sentence_modal_polarity: bool | None = None
        for c, refs in zip(clauses, clause_refs):
            if not refs or re.search(_RETRACTION_VERB_PATTERN, c.lower()):
                continue
            if not (set(_norm_tokens(c)) & _MODAL_TOKENS):
                continue
            sentence_modal_polarity = not sentence_negated
            break
        # A pronoun retraction clause spliced into a candidate
        # sentence ("Maya yes, she just resigned.", "Maya yes,
        # she will not serve.") vetoes every offer the sentence
        # references: the contamination veto skips
        # candidate-referencing sentences by scope, and the
        # clause itself references nobody, so this is where
        # spliced retractions land (round-12 C3, round-13 CR2).
        if any(
            o["decline_key"] == offer["decline_key"]
            for refs in clause_refs
            for o in refs
        ) and any(
            _retraction_shaped_clause(clauses[i], refs)
            for i, refs in enumerate(clause_refs)
        ):
            values.add(False)
        for idx, clause in enumerate(clauses):
            lowered = clause.lower()
            refs = clause_refs[idx]
            if all(
                r["decline_key"] != offer["decline_key"] for r in refs
            ):
                # A bare verdict clause ADJACENT to a clause that
                # references exactly this offer is a signal for
                # it: "Maya no. Actually yes, Maya can serve." —
                # the "Actually yes" retracts the fragment. A
                # bare verdict next to a NON-candidate's clause
                # ("Yes, Carlos can serve, but no, Maya should
                # not.") is that person's, not this offer's.
                if not refs:
                    neighbors = []
                    if idx > 0:
                        neighbors.append(clause_refs[idx - 1])
                    if idx + 1 < len(clauses):
                        neighbors.append(clause_refs[idx + 1])
                    if any(
                        len(n) == 1
                        and n[0]["decline_key"] == offer["decline_key"]
                        for n in neighbors
                    ):
                        bare = _bare_verdict(clause)
                        if bare is not None:
                            values.add(bare)
                continue
            # A retraction that NAMES this offer ("Maya
            # withdrew.", "Please remove Maya.", "Take David
            # off.", "Drop Maya.") is a signal against it: the
            # contamination veto skips candidate-referencing
            # sentences by scope, so named retractions land here
            # (round-8 C-R8-2). A signal only vetoes.
            if re.search(
                _RETRACTION_VERB_PATTERN, lowered
            ) or re.search(_RETRACTION_DISMISSAL_PATTERN, lowered):
                values.add(False)
                continue
            verdict = _modal_segment_verdict(clause, offer, all_offers)
            if verdict is not None:
                values.add(verdict)
                continue
            clause_tokens = set(_norm_tokens(clause))
            if clause_tokens & _MODAL_TOKENS:
                # A modal clause the scoped read cannot dispose
                # still signals its own polarity (round-13 CR1,
                # generalized round-14 CR14-1): affirmative →
                # for, negated → against — EXCEPT a negation
                # governing an exclusion predicate ("should
                # not be ruled out") is an inclusion (CR14-2).
                # The polarity is the SENTENCE's (round-15
                # CR15-1/CR15-2): the governing negation may sit
                # in a sibling clause, split off by punctuation
                # or a parenthetical. This must run before the
                # closed-form False below: an affirmative modal
                # statement about the offer ("David will
                # serve.") is not prose against it.
                values.add(not sentence_negated)
                continue
            if _pure_fragment_verdict(clause, offer, all_offers) is not None:
                continue  # the decider's own clause — no signal
            # Prose gate (round-14 CR14-1): a clause reducing
            # to anchor names and grammar alone — a
            # conjunction-split name list ("Maya" out of "Maya
            # and David …") — is not prose about the offer and
            # signals nothing.
            all_anchor_tokens: set[str] = set()
            for o in all_offers:
                all_anchor_tokens |= set(
                    _norm_tokens(
                        o.get("anchor_name") or o.get("label") or ""
                    )
                )
            if not (
                clause_tokens
                - all_anchor_tokens
                - _NAME_CLAUSE_FUNCTION_TOKENS
            ):
                if sentence_modal_polarity is not None:
                    values.add(sentence_modal_polarity)
                continue
            # Closed form (round-11 F2, replacing the round-10
            # morphology backstop; round-12 C2 extends it to
            # clauses naming this offer JOINTLY with another —
            # "Maya as well as David withdrew." is prose about
            # both): ANY other clause referencing this offer —
            # a predication in any verb form ("Maya works
            # nights.", "Maya lives in Ohio."), an imperative
            # ("Exclude Maya."), a bare mention — is
            # differentiated prose about the candidate and
            # vetoes its fragment. Verb morphology is not
            # enumerated: safety is defined by the two decider
            # reads above (the modal read, and the fragment
            # decider's own purity test).
            values.add(False)
    return values


_GENDERED_PRONOUNS: frozenset[str] = frozenset({
    "she", "her", "hers", "he", "him", "his",
})

#: Pronouns that can carry a spliced / standalone retraction
#: (mirrors the contamination veto's pronoun set, which
#: includes "they").
_SPLICED_PRONOUNS: frozenset[str] = frozenset({
    "she", "he", "they", "her", "him",
})

#: Tokens that are ordinary vocabulary AND common names: in a
#: name slot they are person references regardless of case
#: (round-6 C1 — "use will for all of those roles" names Will).
_NAME_LIKE_TOKENS: frozenset[str] = _COMMON_WORD_GIVEN_NAMES | {
    "good", "fine",
}

#: Words that may surround a bundle reference in a genuinely
#: undifferentiated reply ("that works for all of those", "same
#: people for all of those roles", "I'm fine with all of those").
#: Anything left over after scrubbing the reference itself is a
#: substantive extra word — a name, an object — and the reply is
#: differentiated after all.
_GLOBAL_REF_RESIDUE_OK: frozenset[str] = _FRAGMENT_STOPWORDS | {
    "all", "of", "those", "these", "them", "both", "role", "roles",
    "yes", "yeah", "yep", "no", "not", "never", "sure", "for", "and",
    "with", "that", "same", "person", "people", "use", "using",
    "works", "work", "does", "do", "sounds", "goes", "looks",
    "seems", "applies", "i", "we", "am", "fine", "good", "great",
    "perfect", "correct", "right", "agreed", "okay", "ok",
    "absolutely", "definitely", "serve", "count",
}

#: A head block composed ONLY of these tokens is a verdict-shaped
#: reply ("Yes.", "No, not for us.", "Yes, same people.") as
#: opposed to a short factual sentence ("The address does not
#: handle mail anymore.") whose head happens to parse.
_VERDICT_SHAPED_TOKENS: frozenset[str] = _GLOBAL_REF_RESIDUE_OK | {
    "correct", "right", "agreed", "okay", "ok", "absolutely",
    "definitely", "us", "it", "neither", "nor",
}

#: Minimal grammatical vocabulary for the name-only-clause check:
#: everything here is function words, bundle vocabulary, or
#: verdict tokens — a clause reducing to a name-like token after
#: removing these is a bare person reference.
_NAME_CLAUSE_FUNCTION_TOKENS: frozenset[str] = (
    _FRAGMENT_STOPWORDS
    | {
        "for", "all", "of", "those", "these", "them", "both",
        "that", "and", "with", "same", "person", "people", "role",
        "roles", "use", "using", "yes", "yeah", "yep", "no", "not",
        "never", "sure", "please", "thanks", "thank", "it", "is",
        "does", "so", "much", "i", "we", "us", "our", "my",
        "me", "am", "everything", "everyone", "anyone", "anybody",
        "m", "t", "re", "ve", "ll", "d",
    }
) - _NAME_LIKE_TOKENS

#: Cooperative content words: assent predicates and adjectives
#: that may appear in a genuinely undifferentiated reply ("that
#: works", "sounds good", a bare "fine", "correct"). A short
#: clause whose content is NONE of these and no verb phrase is a
#: substantive reference standing alone — a name, whatever name
#: it is (round-8 C-R8-1: the isolated-clause rule is
#: structural, not a list of names).
_COOP_SINGLE_TOKENS: frozenset[str] = frozenset({
    "works", "work", "sounds", "goes", "looks", "seems",
    "applies", "fine", "good", "great", "perfect", "correct",
    "right", "agreed", "okay", "ok", "absolutely", "definitely",
    "do", "serve", "count",
})

#: Tokens after which a common-word given name reads as a modal
#: or assent word rather than a person: a clause subject or a
#: pronoun subject ("that will work", "i may go"). In subject
#: position itself — clause-initial, or following anything
#: else — the token is the subject, hence a name (round-11 F1:
#: "will works" designates Will; "that will work" does not).
_SUBJECT_PRECEDERS: frozenset[str] = frozenset({
    "that", "it", "this", "i", "we", "you", "he", "she",
    "they", "there",
})

#: Base verb forms: a modal auxiliary takes the base form, so
#: a name token followed by one of these can be a modal
#: ("that will work"); followed by an inflected form it can
#: only be a subject ("that will works" — round-12 C1).
_BASE_FORM_TOKENS: frozenset[str] = frozenset({
    "work", "sound", "go", "look", "seem", "apply", "be",
    "do", "serve", "count",
})


def _anchor_free_global_verdict(
    message: str, offers: list[dict[str, Any]]
) -> bool | None:
    """The message-level verdict for a reply that references NOBODY:
    every head-block clause must be owner-free (no candidate anchor,
    no person-shaped name). Only then is the reply genuinely
    undifferentiated ("Yes, please — that works for all of those.")
    and the shared _bundle_verdict read may speak for every offer.
    A reply naming anyone — a candidate, a third person, a bare
    surname — is differentiated by definition (review C-2/C-5);
    whatever the scoped reads could not decide from it defers.
    Two reference forms the owner check cannot see are gated
    explicitly (v6): a gendered pronoun ("use her for all of
    those" speaks about ONE person, not every bundle) and a
    bundle-reference clause carrying a substantive leftover word
    ("use priya for all of those roles" — "priya" survives the
    reference scrub; person-shape misses lowercase names)."""
    import re

    text = message.strip()
    if not text:
        return None
    block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()
    ]
    for sentence in sentences:
        if "?" in sentence or "？" in sentence or "¿" in sentence:
            return None  # a question is not a decision (round-6 C3)
        for clause in _split_clauses(sentence):
            if _clause_owner(clause, offers):
                return None
            tokens = set(_norm_tokens(clause))
            if tokens & _GENDERED_PRONOUNS:
                return None
            # A common-word given name in subject position is a
            # NAME, not a modal: "will works" designates Will
            # (round-11 F1). The modal reading ("that will
            # work") needs BOTH a subject token before it and a
            # base-form verb after it — a modal takes the base
            # form, so "that will works" is only grammatical as
            # a designation (round-12 C1).
            ordered = _norm_tokens(clause)
            for pos, tok in enumerate(ordered):
                if tok not in _COMMON_WORD_GIVEN_NAMES:
                    continue
                preceded = (
                    pos > 0 and ordered[pos - 1] in _SUBJECT_PRECEDERS
                )
                followed = (
                    pos + 1 < len(ordered)
                    and ordered[pos + 1] in _BASE_FORM_TOKENS
                )
                if not (preceded and followed):
                    return None
            # Structural settlement (round-7 C-R7-1 → round-8
            # C-R8-1 → round-9 F1 → round-10 C1): inheritance
            # requires a WHOLLY cooperative head block. A clause
            # passes only if every substantive token in it is
            # assent vocabulary or a modal auxiliary AND at
            # least one is assent vocabulary — one cooperative
            # word predicated of a third person ("that works
            # for PRIYA", "carlos works") is a designation, not
            # assent, and a lone modal ("will") is an ellipsis
            # for a name. Anything else is a reference to
            # something specific, and the reply is
            # differentiated; a re-ask is the safe failure.
            substantial = tokens - _NAME_CLAUSE_FUNCTION_TOKENS
            if substantial and not (
                substantial
                <= (_COOP_SINGLE_TOKENS | _MODAL_TOKENS | {"may", "might"})
                and (substantial & _COOP_SINGLE_TOKENS)
            ):
                return None
            lowered = clause.lower()
            if _has_bundle_ref(lowered):
                # Name-slot gate (round-6 C1): a name-like token
                # in the "use <name> for …" slot or in clause-
                # initial designation position ("will for all of
                # those") is a person reference even when the
                # token is residue-allowlisted vocabulary. The
                # initial position uses the given-name lexicon
                # only: "Fine, for all of those." is assent, and
                # the name reading of fine/good is already gated
                # in the use-slot (round-8 M-R8-1).
                slot = re.search(r"\buse\s+(\w+)\s+for\b", lowered)
                if slot and slot.group(1) in _NAME_LIKE_TOKENS:
                    return None
                initial = re.match(r"\W*(\w+)\s+for\b", lowered)
                if initial and initial.group(1) in _COMMON_WORD_GIVEN_NAMES:
                    return None
                scrubbed = re.sub(
                    _BUNDLE_REF_PATTERN,
                    " ",
                    _expand_contractions(lowered),
                )
                residue = (
                    set(_norm_tokens(scrubbed)) - _GLOBAL_REF_RESIDUE_OK
                )
                if residue:
                    return None
    if not any(
        _has_bundle_ref(clause.lower())
        for sentence in sentences
        for clause in _split_clauses(sentence)
    ):
        # No bundle reference anywhere: the global read may speak
        # only for a genuinely verdict-shaped head — otherwise a
        # short factual sentence's "does not …" would be inherited
        # as a decline by every pending offer (round-6 C4
        # fall-through).
        if not set(_norm_tokens(block)) <= _VERDICT_SHAPED_TOKENS:
            return None
    return _bundle_verdict(message)


def _resolve_offer_verdicts(
    offers: list[dict[str, Any]], message: str
) -> dict[str, bool]:
    """decline_key -> verdict for each offer the user's message decides.

    V6 CONTRACT — decide narrow, veto broad (the settlement of
    review rounds 1-5, each of which found a fresh manufactured
    fill / persisted decline in the previous round's scoped
    free-text judging):

    DECIDERS (the only reads allowed to persist a verdict):
    - a single-offer turn: `_bundle_verdict` on the whole reply
      (pre-merge semantics, unchanged);
    - the address offer: a person-free address sentence read
      with `_bundle_verdict` (`_address_scoped_verdict`);
    - a person offer: a PURE verdict fragment — a clause naming
      exactly that anchor and reducing to name + one verdict
      token ("Maya yes", "David no");
    - every offer at once: the anchor-free global read
      (`_anchor_free_global_verdict`), only when the reply is
      wholly undifferentiated — no fragments, no modal signals,
      no contamination. This is the cooperative shape the merged
      presentation is built around ("Yes, please — that works
      for all of those.").

    VETOES (can only ever DEFER, never persist):
    - a modal signal about exactly one offer (scope-gated modal
      read, or modal+negation) vetoes that offer's fragment when
      it disagrees, and any signal blocks inheritance;
    - a retraction-shaped ownerless sentence after the first
      contaminates the whole message (`_message_contaminated`).

    Everything else — appointments, designations, capability
    statements, differentiated prose — defers: the offer is
    re-presented (later verdict-only, then retired) or its roles
    are asked as ordinary cluster questions. Deferral is the
    safe failure; a wrong confidence-1.0 fill or a persisted
    decline of an accepted offer is not.
    """
    if not offers:
        return {}
    if len(offers) == 1:
        verdict = _bundle_verdict(message)
        if verdict is not None:
            return {offers[0]["decline_key"]: verdict}
        return {}
    expanded = _expand_contractions(message)
    if _message_contaminated(expanded, offers):
        return {}
    resolved: dict[str, bool] = {}
    any_signal = False
    for offer in offers:
        if offer.get("bundle") == "address":
            verdict = _address_scoped_verdict(offer, expanded, offers)
            if verdict is not None:
                resolved[offer["decline_key"]] = verdict
            continue
        fragment = _offer_fragment_verdict(offer, expanded, offers)
        signals = _offer_modal_signal(offer, expanded, offers)
        if signals:
            any_signal = True
        if fragment is not None and signals <= {fragment}:
            resolved[offer["decline_key"]] = fragment
    if resolved:
        return resolved
    if not any_signal:
        inherited = _anchor_free_global_verdict(message, offers)
        if inherited is not None:
            return {offer["decline_key"]: inherited for offer in offers}
    return {}


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
    parked = effective_parked_keys(_parked_keys(conn, matter_id), answers)
    (
        completeness,
        missing,
        _total_required,
        _answered_required,
        parked_required,
    ) = _display_progress(pack, answers, parked)
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
                parked=key in parked,
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
        parked_required_keys=parked_required,
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
        # Parked items are the exception (Phase 4 P4): the family
        # deferred them deliberately and resolves them from the Review
        # screen's open-items list — escalating them to the attorney as
        # "incomplete" would double-surface them and misstate the
        # family's intent. They keep gating document generation.
        _answers_now = _answers_map(conn, matter_id)
        _completeness, missing, _total_required, _answered_required = _progress(
            pack, _answers_now
        )
        _parked_now = effective_parked_keys(
            _parked_keys(conn, matter_id), _answers_now
        )
        for key in missing:
            if key in _parked_now:
                continue
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


def park_item(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    session_id: UUID,
    payload: ParkItemCreate,
) -> SessionOut:
    """Park one question ("don't know yet") from the interview surface.

    The item leaves the interview's focus, follow-ups and display
    missing list immediately; it still gates document generation
    until it is answered or un-parked. Parking an answered item is a
    conflict (nothing to defer); parking a pruned item is a conflict
    (it does not apply to this plan); an unknown key is a validation
    error. Idempotent for an already-parked item.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)
    session = _load_session_for_write(conn, matter_id, session_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    key = payload.question_key
    if key not in qs.all_question_keys(pack.question_sets):
        raise ValidationAppError(f"Unknown question key: {key}")
    answers = _answers_map(conn, matter_id)
    if qs.is_answered(answers.get(key)):
        raise ConflictError("That item is already answered — nothing to park.")
    applicable, pending, _pruned = qs.partition_questions(
        pack.question_sets, answers
    )
    live_keys = {
        str(q.get("key")) for _s, _sec, q in [*applicable, *pending]
    }
    if key not in live_keys:
        raise ConflictError(
            "That item does not apply to this plan — nothing to park."
        )
    repo.park_item(
        conn,
        matter_id=matter_id,
        question_key=key,
        parked_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="interview.item_parked",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=session_id,
        before_summary={},
        after_summary={"question_key": key},
    )
    return _session_out(conn, session, pack)


def unpark_item(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    session_id: UUID,
    question_key: str,
) -> SessionOut:
    """Remove a park marker; the item rejoins the interview's missing
    list and focus. Idempotent."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)
    session = _load_session_for_write(conn, matter_id, session_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    if question_key not in qs.all_question_keys(pack.question_sets):
        raise ValidationAppError(f"Unknown question key: {question_key}")
    repo.unpark_item(conn, matter_id=matter_id, question_key=question_key)
    record_audit_event(
        conn,
        action="interview.item_unparked",
        resource_type="interview_session",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=session_id,
        before_summary={"question_key": question_key},
        after_summary={},
    )
    return _session_out(conn, session, pack)


def list_parked_items(
    conn: Any, *, actor_sub: str, matter_id: UUID
) -> list[ParkedItemOut]:
    """The matter's open (parked, still unanswered) items, in pack
    order — the Review screen's open-items list (Phase 4 P4 hook).
    Resolving an item anywhere (an answer write) removes it here with
    no extra call: effective parked = markers minus answered keys.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    firm_id = UUID(str(scope.matter["firm_id"]))
    _require_flag(conn, firm_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    answers = _answers_map(conn, matter_id)
    parked = effective_parked_keys(_parked_keys(conn, matter_id), answers)
    if not parked:
        return []
    rows = {
        str(row["question_key"]): row
        for row in repo.list_parked_items(conn, matter_id)
    }
    out: list[ParkedItemOut] = []
    for _set_id, _section_id, question in qs.iter_questions(pack.question_sets):
        key = str(question["key"])
        if key in parked and key in rows:
            out.append(
                ParkedItemOut(
                    question_key=key,
                    label=str(question.get("label") or key),
                    required=bool(question.get("required", False)),
                    parked_at=rows[key]["created_at"],
                )
            )
    return out


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
    # Parked items (Phase 4 P4): the display view of progress — parked
    # items are neither missing nor counted — drives the prompt state,
    # the provider context, and the response, so nothing downstream
    # can re-ask or nag about a deferred item.
    parked = effective_parked_keys(_parked_keys(conn, matter_id), answers)
    (
        completeness,
        missing,
        total_required,
        answered_required,
        parked_required,
    ) = _display_progress(pack, answers, parked)
    # Keys with open proposals awaiting user confirmation — the model should
    # NOT re-ask about these; instead acknowledge they're pending review.
    pending_keys = sorted(repo.open_proposal_keys(conn, session_id))
    # Human labels for every pack question key, so provider fallbacks can ask
    # the real question text instead of deriving phrasing from raw key names.
    question_labels = {
        str(question.get("key")): str(question.get("label") or question.get("key"))
        for _, _, question in qs.iter_questions(pack.question_sets)
    }
    # Interview v2 (flag on): pending bundle offers outrank the
    # cluster gap; otherwise the prompt focuses on the dense cluster
    # focus (next_gap_focus). ALL pending offers are presented in one
    # message and resolved from one reply (turn-count economics: the
    # per-offer presentation + verdict exchanges cost up to three
    # extra turns in the A/B). Flag off: the v5 path runs byte-for-byte
    # (including its trace label). The focus is computed BEFORE the
    # provider context so the provider's no-text fallback asks the
    # same question the prompt intends.
    cluster_gap = None
    presented_offers: list[dict[str, Any]] = []
    verdict_offers: list[dict[str, Any]] = []
    shadow_questions: list[dict[str, Any]] = []
    next_gap_shadow_questions: list[dict[str, Any]] = []
    then_gap = None
    bundle_state: dict[str, Any] = {}
    signed_in_name = None
    # Phase 4 (P3): question keys placed into a turn's focus by any
    # EARLIER turn (persisted in session state). This turn's focus
    # partitions against it: already-asked fields are woven follow-ups,
    # never fresh questions.
    asked_before: set[str] = set()
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
        presented_offers, verdict_offers, bundle_state = _bundle_offers_for_turn(
            conn,
            session_id=session_id,
            pack=pack,
            answers=answers,
            question_labels=question_labels,
            parked_keys=parked,
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
        _raw_asked = bundle_state.get("asked_focus_keys")
        asked_before = (
            {str(k) for k in _raw_asked} if isinstance(_raw_asked, list) else set()
        )
        if (
            not presented_offers
            and not verdict_offers
            and _is_park_request(message)
        ):
            # Conversational parking (Phase 4 P4): a bare "don't know
            # yet" defers the items the previous turn actually asked
            # (the persisted last focus) that are still unanswered and
            # not awaiting confirmation. The park lands BEFORE this
            # turn's focus is chosen, so the reply moves on to fresh
            # work in the same exchange. Offers own their turns (a
            # deferral there is a verdict read, not a park), and a
            # message stating any fact never reaches this branch —
            # _is_park_request matches whole-message deferrals only.
            _park_candidates = [
                k
                for k in sorted(prev_focus_keys)
                if k not in parked
                and not qs.is_answered(answers.get(k))
                and k not in pending_keys
            ]
            for _key in _park_candidates:
                repo.park_item(
                    conn,
                    matter_id=matter_id,
                    question_key=_key,
                    parked_by_user_id=actor_id,
                )
            if _park_candidates:
                parked = parked | set(_park_candidates)
                (
                    completeness,
                    missing,
                    total_required,
                    answered_required,
                    parked_required,
                ) = _display_progress(pack, answers, parked)
        if not presented_offers:
            # Verdict-only offers never block the gap: they are never
            # presented, never the prompt focus — they exist solely so
            # the verdict block can consume the answer to the final
            # presentation.
            cluster_gap = _focus_for_turn(
                conn,
                firm_id=firm_id,
                pack=pack,
                answers=answers,
                pending_proposal_keys=frozenset(pending_keys),
                parked_keys=parked,
            )
            # While verdicts are in play the bundle machinery owns
            # the turn's framing: drop the sweep wrap-up presentation
            # (questions unchanged) so it never races a pending
            # verdict (Phase 4 review I-1).
            cluster_gap = _focus_without_sweep_on_verdict_turns(
                cluster_gap, verdict_offers
            )
            if cluster_gap is not None and "identity" in (
                cluster_gap.get("clusters") or [cluster_gap["cluster"]]
            ):
                signed_in_name = _signed_in_name(
                    conn, scope.matter.get("household_id")
                )
            if cluster_gap is not None:
                # Gate-shadow fields (v8): conditional questions whose
                # gate sits in this turn's focus ride along as
                # record-on-yes fields, so one full answer paragraph
                # lands in one turn instead of three.
                shadow_questions = shadow_questions_for(
                    pack.question_sets,
                    answers,
                    cluster_gap["questions"],
                    covered_keys=frozenset(pending_keys) | frozenset(answers.keys()),
                )
        elif all(o.get("presented_before") for o in presented_offers):
            # Pure verdict turn: every presented offer was already
            # asked in an earlier message, so this reply's job is to
            # acknowledge the verdict AND ask the next fields (the v7
            # prompt's "then ask" addendum). The gap rides along as
            # next_gap only — the offers stay the turn's focus. The
            # offers' own fill keys are excluded from it: they are
            # being resolved by this very verdict, and asking them in
            # the same reply would visibly re-ask just-confirmed
            # fields (review I-2).
            then_gap = _focus_for_turn(
                conn,
                firm_id=firm_id,
                pack=pack,
                answers=answers,
                pending_proposal_keys=frozenset(pending_keys),
                parked_keys=parked,
            )
            resolving_keys = {
                k
                for o in presented_offers + verdict_offers
                for k in o["fill_keys"]
            }
            then_gap = _gap_without_keys(then_gap, resolving_keys)
        if presented_offers:
            # Fill keys only: the anchors are already answered, and
            # keeping them in the guard's asked-set would preserve
            # redundant copy proposals for them (batch review M-5).
            # On a verdict turn the then-gap keys join the asked-set:
            # the reply asks them, so next turn's answers to them are
            # genuinely "asked" for the copy guard's window.
            focus_key_set = {
                k for o in presented_offers for k in o["fill_keys"]
            }
            if then_gap is not None:
                focus_key_set |= {
                    str(q["key"]) for q in then_gap["questions"]
                }
            bundle_state["last_focus_keys"] = sorted(focus_key_set)
        elif cluster_gap is not None:
            bundle_state["last_focus_keys"] = sorted(
                str(q["key"]) for q in cluster_gap["questions"]
            )
        else:
            bundle_state["last_focus_keys"] = []
        # Phase 4 (P3): remember every key this turn ASKS (the cluster
        # focus + a verdict turn's then-gap) so later turns can tell
        # fresh questions from woven follow-ups. Bundle fill keys are
        # deliberately absent: an offer is a confirmation, not an ask —
        # if its roles are later asked individually, that IS their
        # first ask.
        _asked_now = {
            str(q["key"]) for q in (cluster_gap or {}).get("questions", [])
        } | {str(q["key"]) for q in (then_gap or {}).get("questions", [])}
        if _asked_now:
            bundle_state["asked_focus_keys"] = sorted(asked_before | _asked_now)
        if not state_unavailable:
            try:
                repo.update_session_state(conn, session_id, bundle_state)
            except Exception:
                logger.warning("bundle state persist failed", exc_info=True)
        if then_gap is not None:
            # The verdict turn's prompt also names the then-gap fields;
            # its gates' shadow fields ride along (review C-2) so a
            # reply that answers the gate and its gated facts at once
            # is fully extracted this turn.
            next_gap_shadow_questions = shadow_questions_for(
                pack.question_sets,
                answers,
                then_gap["questions"],
                covered_keys=frozenset(pending_keys) | frozenset(answers.keys()),
            )
    focus_keys: list[str] = []
    focus_question_text: str | None = None
    if presented_offers:
        for o in presented_offers:
            o["question_text"] = _bundle_question_text(o)
        if all(o.get("presented_before") for o in presented_offers):
            # Verdict turn: the provider's no-text fallback must not
            # re-ask the offers the v7 prompt forbids repeating
            # (review I-3) — it asks the then-gap fields instead, or
            # closes the topic when nothing remains after them.
            if then_gap is not None:
                focus_keys = [str(q["key"]) for q in then_gap["questions"]]
            else:
                focus_question_text = (
                    "That's everything I needed on those — thank you."
                )
        else:
            focus_keys = [
                k for o in presented_offers for k in o["fill_keys"]
            ]
            focus_question_text = _bundle_questions_text(presented_offers)
    elif cluster_gap is not None:
        focus_keys = [str(q["key"]) for q in cluster_gap["questions"]]
    # Phase 4 (P3): the follow-up partition of this turn's question
    # focus — keys an earlier turn already asked. The prompt marks
    # them as woven follow-ups and the provider fallback renders them
    # as a "from earlier" segment.
    _focus_source = cluster_gap if cluster_gap is not None else then_gap
    follow_up_keys: set[str] = set()
    if _focus_source is not None:
        follow_up_keys = {
            str(q["key"]) for q in _focus_source["questions"]
        } & asked_before
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
        focus_follow_up_keys=sorted(follow_up_keys & set(focus_keys)),
    )
    history = _history_messages(conn, session_id)
    # Lever E inventory (v9): the remaining applicable fields ride as
    # a reference list on cluster and verdict turns so a volunteered
    # off-focus paragraph can be extracted the turn it arrives.
    other_questions: list[dict[str, Any]] | None = None
    if v2_on and (cluster_gap is not None or presented_offers):
        _exclude = (
            set(focus_keys)
            | {s["key"] for s in (shadow_questions or [])}
            | {s["key"] for s in (next_gap_shadow_questions or [])}
            | {k for o in presented_offers for k in o["fill_keys"]}
            | {k for o in verdict_offers for k in o["fill_keys"]}
            | set(pending_keys)
        )
        other_questions = other_questions_for(
            pack.question_sets, answers, exclude=_exclude
        ) or None
    # Phase 4 (P1): v2 focus/offers turns render the v10 presentation
    # (one natural question, woven follow-ups); every other turn keeps
    # its earlier version byte-for-byte — the ladder below is the
    # pre-Phase-4 selection, retained verbatim for those turns.
    natural_questions = bool(
        v2_on and (cluster_gap is not None or presented_offers)
    )
    prompt_version = (
        prompts.PROMPT_TEMPLATE_VERSION_V10
        if natural_questions
        else prompts.PROMPT_TEMPLATE_VERSION_V9
        if other_questions
        else prompts.PROMPT_TEMPLATE_VERSION_V8
        if (shadow_questions or next_gap_shadow_questions)
        else prompts.PROMPT_TEMPLATE_VERSION_V7
        if (cluster_gap is not None or presented_offers)
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
        bundle_offers=presented_offers or None,
        signed_in_name=signed_in_name,
        next_gap=then_gap,
        shadow_questions=shadow_questions or None,
        next_gap_shadow_questions=next_gap_shadow_questions or None,
        other_questions=other_questions,
        natural_questions=natural_questions,
        follow_up_keys=follow_up_keys or None,
    )

    # Stronger extractor on v2 turns only (config OPENAI_INTERVIEW_MODEL_V2,
    # benchmark 2026-10-25); flag-off turns keep the default interview
    # model exactly as before. effective_model is also what the AI trace
    # and proposal rows record — provenance must name the model that
    # actually ran (review I-2).
    effective_model = (
        settings.OPENAI_INTERVIEW_MODEL_V2
        if v2_on
        else settings.OPENAI_INTERVIEW_MODEL
    )
    provider = get_llm_provider(model=effective_model if v2_on else None)
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
    # Copy-guard focus (review round 2, M-5): the current focus counts
    # as "asked" on cluster turns and bundle VERDICT turns, but NOT on
    # a bundle's first-presentation turn — there the focus holds the
    # fill keys, which are offered for a yes/no, not asked as value
    # questions; a whole-message copy naming one must stay guarded.
    copy_guard_focus_keys = (
        focus_keys
        if (
            cluster_gap is not None
            or (presented_offers and all(o.get("presented_before") for o in presented_offers))
        )
        else []
    )
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
        _coerced = (
            _numeric_text_value(question_types.get(draft.question_key, ""), draft.value)
            if v2_on
            else None
        )
        if _coerced is not None:
            from app.domains.ai.provider import FieldProposalDraft

            draft = FieldProposalDraft(
                question_key=draft.question_key,
                value=_coerced,
                confidence=draft.confidence,
                source_snippet=draft.source_snippet,
            )
        if not _value_shape_ok(
            question_types.get(draft.question_key, ""), draft.value
        ):
            # Shape-contradicting values are never faithful (fix
            # iteration 2): a boolean True for a text field, a prose
            # string for a boolean gate. The key stays UNCOVERED so
            # the deterministic fallbacks below can still record the
            # answer in its proper shape this same turn.
            logger.warning(
                "Dropping shape-mismatched proposal for %r (session %s)",
                draft.question_key,
                session_id,
            )
            continue
        if v2_on and _is_duplicate_answer(
            draft.question_key,
            draft.value,
            answers,
            question_types.get(draft.question_key, ""),
        ):
            # A verbatim restatement of the recorded answer is noise,
            # not extraction (set-6 stall: answered trust fields
            # re-proposed for six straight turns). Different values
            # still flow through as correction signals.
            logger.info(
                "Dropping duplicate-of-answer proposal for %r (session %s)",
                draft.question_key,
                session_id,
            )
            continue
        if v2_on and _is_phone_key(draft.question_key):
            # A phone field's value must be the number itself — a
            # model draft carrying prose (or the whole message) is
            # the set-6 bleed shape at the model layer. The span is
            # read from the draft first, then from the user's message
            # (the grounding source) when the draft's own formatting
            # defeats the pattern — a correct number must not be
            # discarded over its punctuation (review I-2).
            span = (
                _extract_phone_value(draft.value)
                if isinstance(draft.value, str)
                else None
            ) or _extract_phone_value(message)
            if span is None:
                logger.warning(
                    "Dropping phone proposal without a phone-shaped value for %r (session %s)",
                    draft.question_key,
                    session_id,
                )
                continue
            if span != draft.value:
                from app.domains.ai.provider import FieldProposalDraft

                draft = FieldProposalDraft(
                    question_key=draft.question_key,
                    value=span,
                    confidence=draft.confidence,
                    source_snippet=draft.source_snippet,
                )
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
            asked_keys=_copy_guard_asked_keys(
                v2_on=v2_on,
                window_focus_keys=window_focus_keys,
                prev_focus_keys=prev_focus_keys,
                focus_keys=copy_guard_focus_keys,
            ),
            question_key=draft.question_key,
            focus_authoritative=v2_on,
            sibling_draft_count=max(0, len(response.proposals) - 1),
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
        # Engagement discipline (set 7, refined by review I-4): the
        # TEXT fallback's value is the WHOLE message by construction.
        # When the model already extracted >=2 values from a message,
        # the message is multi-topic and the model demonstrably
        # engaged it — a whole-message stamp onto a sibling field is
        # then always the bleed shape (the agents paragraph filed as
        # healthcare_agent_limitations), so the text fallback abstains
        # (the same rule G3 applies to its own text captures). The
        # PERSON fallback still runs on engaged turns: its licensing
        # is role-word based (a nomination must be tied to its role in
        # the message's or reply's own words), which is what separates
        # a stated nomination from an inference. The boolean fallback
        # is polarity-disciplined and always runs (gates steer).
        model_proposals = len(valid_proposals)
        for fallback_fn in (
            _deterministic_boolean_proposal,
            _deterministic_person_proposal,
            _deterministic_text_proposal,
        ):
            if (
                fallback_fn is _deterministic_text_proposal
                and model_proposals >= 2
            ):
                continue
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
        if v2_on and not presented_offers and not verdict_offers:
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
                prior_proposal_count=len(valid_proposals),
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

    if v2_on and valid_proposals:
        # Gate-truth licensing (v8): a proposal for a gate-dependent
        # field survives only when its gate is true in the recorded
        # answers, in this turn's accepted proposals, or in a PENDING
        # proposal from an earlier turn (the user's own stated gate
        # value awaiting their confirmation — review C-1: licensing
        # must not depend on whether the gate card was confirmed
        # before or after the dependent was stated). Shadow capture
        # licenses gated fields on a yes — this is the deterministic
        # backstop for every other path that might mint one.
        dependents = gate_dependents_map(pack.question_sets, answers)
        if dependents:
            licensing_answers = dict(answers)
            try:
                for _row in repo.list_open_proposals(conn, session_id):
                    licensing_answers.setdefault(
                        str(_row.get("question_key")), _row.get("proposed_value")
                    )
            except Exception:
                logger.warning(
                    "open-proposal read for gate licensing failed", exc_info=True
                )
            kept_proposals = _drop_unlicensed_gate_proposals(
                valid_proposals, licensing_answers, dependents
            )
            if len(kept_proposals) != len(valid_proposals):
                dropped_keys = sorted(
                    {d.question_key for d in valid_proposals}
                    - {d.question_key for d in kept_proposals}
                )
                logger.warning(
                    "Dropping gate-unlicensed proposals %s (session %s)",
                    dropped_keys,
                    session_id,
                )
            valid_proposals = kept_proposals

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
        model_id=effective_model if provider.name == "openai" else provider.name,
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
            model_id=effective_model if provider.name == "openai" else provider.name,
            prompt_version=prompt_version,
            pack_version=version,
        )
        proposal_out = _proposal_out(row)
        # Auto-confirm happens in the router AFTER the turn transaction
        # commits, in a separate transaction with a statement timeout.
        # (Doing it here hung the turn — see ADR.)
        stored_proposals.append(proposal_out)

    # --- Role bundles: the user's reply to the presented offers is
    # resolved deterministically, per offer. YES → server proposals
    # (source "bundle", confidence 1.0) fill the sibling roles through
    # the normal confirm/auto-confirm path. NO → the decline is
    # recorded and the offer never repeats for that anchor. Anything
    # else defers. A verdict is only consumed for an offer presented
    # in an earlier turn (presented_before, or a verdict-only offer at
    # its freshness window) — on first presentation this message still
    # answers the previous visible question. With several offers in
    # play, one global affirmative resolves all of them (the merged
    # presentation's whole point); a mixed reply resolves per offer
    # via _resolve_offer_verdicts' scoped reads.
    verdict_candidates = list(verdict_offers) + [
        o for o in presented_offers if o.get("presented_before")
    ]
    if verdict_candidates:
        verdicts = _resolve_offer_verdicts(verdict_candidates, message)
        if verdicts:
            # Fresh read at fill time (review I-1): never stack a bundle
            # proposal on top of an already-open proposal for the same
            # key — the turn-start snapshot in pending_key_set can miss
            # proposals stored by other paths since.
            open_now = repo.open_proposal_keys(conn, session_id)
            decided_now = repo.decided_proposal_keys(conn, session_id)
            declined_changed = False
            for offer in verdict_candidates:
                verdict = verdicts.get(offer["decline_key"])
                if verdict is True:
                    for fill_key in _bundle_fill_keys(
                        offer,
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
                            proposed_value=offer["fill_value"],
                            source="bundle",
                            confidence=1.0,
                            source_snippet=(
                                f"Confirmed via the {offer['bundle']} bundle "
                                f"offer (anchor: {offer['anchor_key']})"
                            ),
                            model_id="server_bundle",
                            prompt_version=prompt_version,
                            pack_version=version,
                        )
                        stored_proposals.append(_proposal_out(row))
                        pending_key_set.add(fill_key)
                        open_now.add(fill_key)
                elif verdict is False:
                    declined = list(bundle_state.get("bundles_declined") or [])
                    if offer["decline_key"] not in declined:
                        declined.append(offer["decline_key"])
                        declined_changed = True
                    bundle_state["bundles_declined"] = declined
            if declined_changed:
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
        parked_required_keys=parked_required,
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
    (
        completeness,
        missing,
        _total_required,
        _answered_required,
        parked_required,
    ) = _display_progress_for_matter(conn, pack, matter_id)
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
        parked_required_keys=parked_required,
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
    (
        completeness,
        missing,
        _total_required,
        _answered_required,
        parked_required,
    ) = _display_progress_for_matter(conn, pack, matter_id)
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
        parked_required_keys=parked_required,
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
