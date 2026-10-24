"""Guided-intake application services (Slice 2, Track C).

Question sets are served from the jurisdiction pack snapshotted on the
matter (never the live pack); answers autosave with pack key validation;
progress persists the answer-driven completeness percentage.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, ValidationAppError
from app.domains.intake import question_sets as qs
from app.domains.intake import repository as repo
from app.domains.intake.schemas import (
    AnswersOut,
    IntakeProgressOut,
    QuestionSetsOut,
)
from app.domains.matters import repository as matters_repo
from app.domains.matters import service as matters_service
from app.domains.matters.state_machine import MatterStatus

# Intake writes are only meaningful while the household is actively
# answering (or responding to attorney clarification).
WRITABLE_STATUSES = frozenset(
    {MatterStatus.INTAKE_IN_PROGRESS.value, MatterStatus.CLARIFICATION_REQUESTED.value}
)


def _require_writable_status(matter: dict[str, Any]) -> None:
    if str(matter["status"]) not in WRITABLE_STATUSES:
        raise ConflictError(
            "Intake answers can only be saved while intake is in progress "
            "or clarification has been requested."
        )


def _answers_out(conn: Any, matter_id: UUID) -> AnswersOut:
    rows = repo.list_answers(conn, matter_id)
    answers = {str(row["question_key"]): row["answer"] for row in rows}
    updated = [row["updated_at"] for row in rows if row.get("updated_at")]
    latest: datetime | None = max(updated) if updated else None
    return AnswersOut(answers=answers, updated_at=latest)


def _parse_nomination_maps_to(maps_to: str | None) -> tuple[str, int] | None:
    """Parse ``nominations.{type}.{priority}`` maps_to bindings.

    Returns (nomination_type, priority) or None if not a nomination binding.
    """
    if not maps_to or not isinstance(maps_to, str):
        return None
    parts = maps_to.split(".")
    if len(parts) != 3 or parts[0] != "nominations":
        return None
    try:
        priority = int(parts[2])
    except ValueError:
        return None
    return (parts[1], priority)


def _ensure_person_for_name(
    conn: Any, household_id: UUID, full_name: str
) -> dict[str, Any] | None:
    """Find a household person by name (case-insensitive), or create one."""
    from app.domains.household import repository as household_repo

    name = full_name.strip()
    if not name:
        return None
    for person in household_repo.list_persons_for_household(conn, household_id):
        if str(person.get("full_name", "")).strip().lower() == name.lower():
            return person
    # Create a new person record for the named individual.
    return household_repo.insert_person(
        conn,
        household_id=household_id,
        full_name=name,
        kind="adult",
    )


def _stale_auto_nominations(
    existing: list[dict[str, Any]],
    *,
    source_key: str,
    target: tuple[str, int, str],
) -> list[dict[str, Any]]:
    """Auto-created nominations sourced from ``source_key`` that no longer
    match the key's current target (type, priority, person_id).

    When an answer changes to a different person, the earlier auto-created
    row is superseded — leaving it produces duplicate Priority-1 role
    holders (E2E #3: two primary guardians). Only rows still in "proposed"
    status are stale: a nomination a human confirmed is a decision and is
    never swept, and rows sourced from other keys or created manually are
    untouched.
    """
    note = f"From interview answer: {source_key}"
    stale: list[dict[str, Any]] = []
    for n in existing:
        if str(n.get("notes") or "") != note:
            continue
        if str(n.get("status") or "") != "proposed":
            continue
        try:
            triple = (
                str(n.get("nomination_type")),
                int(n.get("priority")),
                str(n.get("person_id")),
            )
        except (TypeError, ValueError):
            continue
        if triple != target:
            stale.append(n)
    return stale


def _sync_nominations_from_answers(
    conn: Any,
    *,
    matter_id: UUID,
    household_id: UUID,
    firm_id: UUID,
    actor_id: UUID,
    answers: dict[str, Any],
    pack: Any,
) -> None:
    """Auto-create nominations from answers whose question maps_to a
    nominations.{type}.{priority} binding.

    This keeps the My Plan "Key roles" section in sync with interview answers,
    so users don't see "Not named yet" after naming someone in the interview.
    Best-effort: never raises, never fails the answer save.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        from app.domains.nominations import repository as nom_repo

        for _set_id, _section_id, question in qs.iter_questions(pack.question_sets):
            key = str(question.get("key", ""))
            if key not in answers:
                continue
            target = _parse_nomination_maps_to(question.get("maps_to"))
            if target is None:
                continue
            nomination_type, priority = target
            value = answers[key]
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                # A SAVEPOINT per answer: a failing insert (e.g. a pack
                # binding whose nomination type violates the table CHECK)
                # otherwise poisons the whole transaction — Postgres
                # aborts it, the Python-level "best-effort" catch cannot
                # save the answer write, and the save dies with a 500.
                # That is exactly how the poa/trustee bindings broke
                # confirms in the 2026-10-24 proof re-run.
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT lf_nom_sync")
                person = _ensure_person_for_name(conn, household_id, value)
                if person is None:
                    with conn.cursor() as cur:
                        cur.execute("RELEASE SAVEPOINT lf_nom_sync")
                    continue
                person_id = UUID(str(person["id"]))
                # Retire auto-created nominations this key produced for a
                # different person/priority (the answer changed) before the
                # dedupe check, so a superseded row never lingers as a
                # duplicate role holder.
                existing = nom_repo.list_nominations(conn, matter_id)
                stale_rows = _stale_auto_nominations(
                    existing,
                    source_key=key,
                    target=(nomination_type, priority, str(person_id)),
                )
                stale_ids = {str(s["id"]) for s in stale_rows}
                for stale in stale_rows:
                    nom_repo.delete_nomination(conn, UUID(str(stale["id"])))
                    logger.info(
                        "Retired superseded %s nomination (matter %s, key %s)",
                        nomination_type,
                        matter_id,
                        key,
                    )
                existing = [n for n in existing if str(n.get("id")) not in stale_ids]
                if any(
                    str(n.get("nomination_type")) == nomination_type
                    and str(n.get("person_id")) == str(person_id)
                    for n in existing
                ):
                    with conn.cursor() as cur:
                        cur.execute("RELEASE SAVEPOINT lf_nom_sync")
                    continue
                nom_repo.insert_nomination(
                    conn,
                    matter_id=matter_id,
                    nomination_type=nomination_type,
                    person_id=person_id,
                    priority=priority,
                    notes=f"From interview answer: {key}",
                )
                logger.info(
                    "Auto-created %s nomination for %s (matter %s)",
                    nomination_type,
                    value,
                    matter_id,
                )
                with conn.cursor() as cur:
                    cur.execute("RELEASE SAVEPOINT lf_nom_sync")
            except Exception as e:
                # Per-answer failures are best-effort; don't fail the save.
                try:
                    with conn.cursor() as cur:
                        cur.execute("ROLLBACK TO SAVEPOINT lf_nom_sync")
                        cur.execute("RELEASE SAVEPOINT lf_nom_sync")
                except Exception:
                    pass
                logger.error("Failed to auto-create nomination for %s: %s", key, e)
    except Exception as e:
        # Sync is best-effort; never fail the answer save.
        logger.warning("Nomination sync failed: %s", e)


# ---------------------------------------------------------------------------
# Answer materialization: minors + assets
#
# The Family & Guardians and Assets pages read household persons and asset
# rows, but interview answers only ever created nominations — E2E #3 showed
# "No minors recorded" / "No assets yet" after a complete interview. These
# syncs close that gap, create-only and best-effort, mirroring the
# nominations sync contract (never fail the answer save, never delete or
# overwrite records a human may have enriched).
# ---------------------------------------------------------------------------

_MINOR_KINDS = {"minor"}  # persons.kind CHECK allows only adult/minor
_NAME_PATTERN = r"[A-ZÀ-Þ][A-Za-zÀ-ÿ.'-]+(?:\s+(?:[A-ZÀ-Þ][A-Za-zÀ-ÿ.'-]+|de|van|von)){0,3}"
_AGE_PAREN_RE = re.compile(rf"({_NAME_PATTERN})\s*\(\s*(?:age\s*)?(\d{{1,2}})\s*\)")
_AGE_WORD_RE = re.compile(rf"({_NAME_PATTERN})\s*,?\s+(?:age|Age)\s+(\d{{1,2}})\b")
_NAME_ONLY_RE = re.compile(rf"^{_NAME_PATTERN}$")
#: Leading words that are descriptors/honorifics, not part of a child's name
#: ("Son Aarav Sharma (12)" must yield "Aarav Sharma", never "Son Aarav…").
#: Stripped repeatedly, so multi-word descriptors ("Step Daughter",
#: "Foster Son", "First Child", "Twin Brother") collapse token by token.
_NAME_LEAD_INS = {
    "son", "sons", "daughter", "daughters", "child", "children", "kid",
    "kids", "twin", "twins", "stepdaughter", "stepson", "stepchild",
    "step", "half", "foster", "adopted", "biological", "grandson",
    "granddaughter", "grandchild", "grandchildren", "nephew", "niece",
    "brother", "sister", "first", "second", "third", "only", "eldest",
    "elder", "middle", "young", "oldest", "youngest", "older", "younger",
    "big", "little", "special", "needs", "baby", "infant", "toddler",
    "newborn", "minor", "boy", "girl", "born", "godson", "goddaughter",
    "godchild", "godchildren", "god", "surviving", "adoptive", "our",
    "my", "his", "her", "their", "the",
    "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
}
#: Whole-answer negatives that must never become a person.
_NAME_NEGATIVES = {"no", "none", "n/a", "na", "nil"}


def _is_lead_in(token: str) -> bool:
    """True when a token is a descriptor lead-in (hyphenated forms like
    "Step-son" count when every part is a lead-in)."""
    lowered = token.lower()
    if lowered in _NAME_LEAD_INS:
        return True
    if "-" in lowered:
        return all(part in _NAME_LEAD_INS for part in lowered.split("-") if part)
    return False


def _clean_person_name(raw: str) -> str | None:
    """Strip descriptor lead-ins from an extracted name; None if nothing
    name-like remains (or the text is a negative like "None").

    When descriptors were stripped, the remaining run is capped at the
    last 3 words — descriptor contexts make longer runs suspect, and a
    child's name in this field is at most first/middle/last. A run with
    no descriptors keeps the regex's 4-word allowance.
    """
    words = " ".join(raw.split()).strip(" .").split(" ")
    if words and words[0].lower() in _NAME_NEGATIVES:
        return None
    stripped = False
    while words and _is_lead_in(words[0]):
        words = words[1:]
        stripped = True
    if not words:
        return None
    if stripped and len(words) > 3:
        words = words[-3:]
    name = " ".join(words)
    if name.lower() in _NAME_NEGATIVES or len(name) > 40:
        return None
    return name


def _parse_minor_children(details: str) -> list[tuple[str, int | None]]:
    """Extract (name, age) pairs from a minor_children_details answer.

    Conservative by design: ages come only from explicit "(12)" / "age 12"
    forms and must be under 18; a bare name list ("Aarav Sharma, Diya
    Sharma and Kabir Sharma") is accepted segment by segment, each segment
    required to look like a plain capitalized name, and merged with the
    aged matches so an age-less sibling is not dropped. Prose yields
    nothing — an unparsed answer is safer than an invented person.
    """
    found: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    adults: set[str] = set()  # names explicitly stated with an age >= 18
    for pattern in (_AGE_PAREN_RE, _AGE_WORD_RE):
        for match in pattern.finditer(details):
            name = _clean_person_name(match.group(1))
            if name is None:
                continue
            age = int(match.group(2))
            if age > 17:
                adults.add(name.lower())
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            found.append((name, age))
    for segment in re.split(r"[,;\n]|\band\b", details):
        candidate = " ".join(segment.split()).strip(" .")
        if not candidate:
            continue
        if not _NAME_ONLY_RE.match(candidate):
            # A lowercase lead-in ("Our sons Aarav Sharma") breaks the
            # whole-segment match — fall back to a trailing name run of
            # at least two words at the segment's end.
            trailing = re.search(rf"({_NAME_PATTERN})$", candidate)
            if trailing is None or " " not in trailing.group(1):
                continue
            candidate = trailing.group(1)
        name = _clean_person_name(candidate)
        if name is None or name.lower() in seen or name.lower() in adults:
            continue
        seen.add(name.lower())
        found.append((name, None))
        if len(found) >= 8:
            break
    return found


def _sync_minors_from_answers(
    conn: Any, *, household_id: UUID, answers: dict[str, Any]
) -> None:
    """Create minor persons for children named in the interview (create-only)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        if answers.get("has_minor_children") is False:
            return
        details = answers.get("minor_children_details")
        if not isinstance(details, str) or not details.strip():
            return
        from app.domains.household import repository as household_repo

        existing = household_repo.list_persons_for_household(conn, household_id)
        known = {
            str(p.get("full_name", "")).strip().lower()
            for p in existing
            if str(p.get("kind") or "").lower() in _MINOR_KINDS
        }
        # SAVEPOINT armor, same as the nominations sync: a DB-level failure
        # here must not poison the request transaction and lose the save.
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT lf_minor_sync")
        try:
            for name, age in _parse_minor_children(details):
                if name.lower() in known:
                    continue
                household_repo.insert_person(
                    conn,
                    household_id=household_id,
                    full_name=name,
                    kind="minor",
                    notes=(
                        f"Age {age} per the interview"
                        if age is not None
                        else "Named in the interview as a minor child"
                    ),
                )
                known.add(name.lower())
                logger.info("Materialized minor person %s (household %s)", name, household_id)
        except Exception:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT lf_minor_sync")
                cur.execute("RELEASE SAVEPOINT lf_minor_sync")
            raise
        with conn.cursor() as cur:
            cur.execute("RELEASE SAVEPOINT lf_minor_sync")
    except Exception as e:  # best-effort: never fail the answer save
        logger.warning("Minor materialization failed: %s", e)


_CATEGORY_ASSET_TYPES = {
    "bank": "bank_account",
    "brokerage": "investment",
    "retirement": "retirement",
    "insurance": "insurance",
    "real_property": "real_estate",
    "business": "business",
    "vehicle": "vehicle",
    "529": "other",
    "digital": "other",
}


def _asset_specs_for_categories(
    question: dict[str, Any] | None, selected: list[str]
) -> list[tuple[str, str]]:
    """Map selected asset-category values to (asset_type, label) rows.

    Labels come from the pack question's own options so the Assets page
    shows the wording the user picked; unknown values are skipped.
    """
    labels = {}
    if question:
        for option in question.get("options") or []:
            if isinstance(option, dict) and option.get("value") is not None:
                labels[str(option["value"])] = str(option.get("label") or option["value"])
    specs: list[tuple[str, str]] = []
    for value in selected:
        asset_type = _CATEGORY_ASSET_TYPES.get(value)
        if asset_type is None:
            continue
        specs.append((asset_type, labels.get(value, value)))
    return specs


def _sync_assets_from_answers(
    conn: Any,
    *,
    firm_id: UUID,
    household_id: UUID,
    matter_id: UUID,
    actor_id: UUID,
    answers: dict[str, Any],
    pack: Any,
) -> None:
    """Create one asset row per selected asset category (create-only)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        categories = answers.get("asset_categories_present")
        if not isinstance(categories, list) or not categories:
            return
        question = None
        for _set_id, _section_id, q in qs.iter_questions(pack.question_sets):
            if str(q.get("key")) == "asset_categories_present":
                question = q
                break
        specs = _asset_specs_for_categories(question, [str(c) for c in categories])
        if not specs:
            return
        from app.domains.assets import repository as assets_repo

        rows, _total = assets_repo.list_assets_for_household(
            conn, household_id, page=1, page_size=200
        )
        have = {(str(a.get("asset_type")), str(a.get("label"))) for a in rows}
        # SAVEPOINT armor, same as the nominations sync (see minors sync).
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT lf_asset_sync")
        try:
            for asset_type, label in specs:
                if (asset_type, label) in have:
                    continue
                assets_repo.insert_asset(
                    conn,
                    firm_id=firm_id,
                    household_id=household_id,
                    matter_id=matter_id,
                    asset_type=asset_type,
                    label=label,
                    estimated_value_cents=None,
                    ownership=None,
                    beneficiary_designation={},
                    notes="Added from the interview asset categories",
                    created_by_user_id=actor_id,
                )
                have.add((asset_type, label))
                logger.info("Materialized asset %s/%s (matter %s)", asset_type, label, matter_id)
        except Exception:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT lf_asset_sync")
                cur.execute("RELEASE SAVEPOINT lf_asset_sync")
            raise
        with conn.cursor() as cur:
            cur.execute("RELEASE SAVEPOINT lf_asset_sync")
    except Exception as e:  # best-effort: never fail the answer save
        logger.warning("Asset materialization failed: %s", e)


def get_question_sets(conn: Any, *, actor_sub: str, matter_id: UUID) -> QuestionSetsOut:
    """Serve the matter's snapshotted pack question sets (read access)."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    pack, code, version = matters_service.load_matter_pack(conn, matter_id)
    return QuestionSetsOut(
        jurisdiction_code=code,
        pack_version=version,
        question_sets=pack.question_sets,
    )


_TRUE_STRINGS = {"yes", "true", "y"}
_FALSE_STRINGS = {"no", "false", "n"}


def _coerce_answer_value(question: dict[str, Any], value: Any) -> Any:
    """Coerce an answer to its pack question type.

    The interview Edit-save path submits booleans as display strings
    ("No"); storing them raw left string-typed booleans in
    intake_answers (2026-10-24 E2E: three corrected booleans stored as
    the string "No", which downstream rules read as truthy). Boolean
    keys accept only bools or clear yes/no strings — anything else is
    a validation error, never a silent store.
    """
    if value is None:
        return None
    if question.get("type") == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
        raise ValidationAppError(
            f"Answer for {question.get('key')!r} must be yes or no."
        )
    return value


def _coerce_answers_map(
    questions_by_key: dict[str, dict[str, Any]],
    answers: dict[str, Any],
    *,
    strict: bool,
) -> dict[str, Any]:
    """Coerce a whole answers map for save_answers.

    Batch saves (the intake wizard autosaves the FULL map on every
    change) must never be bricked by one legacy row: an unvalidated
    value stored before coercion existed would otherwise fail every
    subsequent save for the matter. In batch mode an uncoercible value
    is dropped with an error log; single-key saves (proposal decisions,
    auto-confirm) stay strict because the caller is acting on exactly
    that value.
    """
    import logging

    logger = logging.getLogger(__name__)
    out: dict[str, Any] = {}
    for key, value in answers.items():
        question = questions_by_key.get(key)
        if question is None:
            out[key] = value
            continue
        try:
            out[key] = _coerce_answer_value(question, value)
        except ValidationAppError:
            if strict:
                raise
            logger.error(
                "Dropping uncoercible legacy answer for %r (value type %s)",
                key,
                type(value).__name__,
            )
    return out


def save_answers(
    conn: Any, *, actor_sub: str, matter_id: UUID, answers: dict[str, Any]
) -> AnswersOut:
    """Autosave answers: validate keys against the pack, then upsert.

    Audit records summary counts only — never answer values (contract
    rule 9).
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    _require_writable_status(scope.matter)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    known_keys = qs.all_question_keys(pack.question_sets)
    unknown = sorted(key for key in answers if key not in known_keys)
    if unknown:
        raise ValidationAppError(f"Unknown question key(s): {', '.join(unknown)}.")
    questions_by_key = {
        str(q.get("key")): q for _, _, q in qs.iter_questions(pack.question_sets)
    }
    answers = _coerce_answers_map(
        questions_by_key, answers, strict=len(answers) <= 1
    )
    repo.upsert_answers(conn, matter_id, answers)
    # Auto-create nominations from person-name answers (e.g. guardian names
    # from the interview) so My Plan key roles stay in sync.
    _sync_nominations_from_answers(
        conn,
        matter_id=matter_id,
        household_id=UUID(str(scope.matter["household_id"])),
        firm_id=UUID(str(scope.matter["firm_id"])),
        actor_id=actor_id,
        answers=answers,
        pack=pack,
    )
    _sync_minors_from_answers(
        conn,
        household_id=UUID(str(scope.matter["household_id"])),
        answers=answers,
    )
    _sync_assets_from_answers(
        conn,
        firm_id=UUID(str(scope.matter["firm_id"])),
        household_id=UUID(str(scope.matter["household_id"])),
        matter_id=matter_id,
        actor_id=actor_id,
        answers=answers,
        pack=pack,
    )
    record_audit_event(
        conn,
        action="intake.answers_saved",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={"saved_count": len(answers)},
    )
    return _answers_out(conn, matter_id)


def get_answers(conn: Any, *, actor_sub: str, matter_id: UUID) -> AnswersOut:
    """Return the matter's stored answers (read access)."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    return _answers_out(conn, matter_id)


def get_progress(conn: Any, *, actor_sub: str, matter_id: UUID) -> IntakeProgressOut:
    """Return per-set progress and persist the overall completeness.

    The overall percentage is the required-answered share across all
    question sets; it is written back to ``matters.completeness_pct`` so
    submission and review see the answer-driven value.
    """
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    answers = {
        str(row["question_key"]): row["answer"] for row in repo.list_answers(conn, matter_id)
    }
    per_set, overall, _missing, _pruned = qs.compute_progress(pack.question_sets, answers)
    # ``matters.completeness_pct`` is an integer column (Track A Slice 2
    # migration): round once here so the persisted value and the response agree.
    overall = float(int(round(overall)))
    matters_repo.update_completeness(conn, matter_id, overall)
    return IntakeProgressOut(
        sets=per_set,
        completeness_pct=overall,
        overall=qs.build_overall_progress(per_set, overall),
    )
