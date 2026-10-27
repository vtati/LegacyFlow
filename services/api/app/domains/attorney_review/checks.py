"""Attorney Reviewer — Layer 1 deterministic checks (plan 2026-10-27, §9.2).

Pure functions over the FROZEN snapshot set + frozen source payload.
No DB, no provider, no live answers: everything a check may look at was
persisted at generation time. Every check is written to the §9.8
false-positive budget first — the clean Sharma-shaped set must produce
zero findings — and to catch its seeded defect class second.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

LAYER = "deterministic"
SHOULD_FIX = "should_fix"
QUESTION = "question_for_attorney"

#: Person kinds that mean "minor" (mirrors documents build_template_context).
_MINOR_KINDS = frozenset({"minor", "child", "minor_child", "son", "daughter"})

#: Context slots produced from answers/persons by the documents slot table
#: (documents/service._ANSWER_SLOT_TABLE targets + identity slots). A
#: template token in this set is family-required when unfilled; any other
#: unfilled token (witness names, signing dates, notary fields) is
#: attorney-fill. Guarded against drift by a test that compares this set
#: with the slot table itself.
_FAMILY_SLOTS = frozenset({
    "testator_name", "testator_full_name", "spouse_name",
    "grantor_name", "trustee_name", "declarant_name", "principal_name",
    "parent_name", "trust_name", "children_list",
    "executor_name", "executor_address", "alternate_executor_name",
    "guardian_name", "guardian_address", "alternate_guardian_name",
    "guardian_relationship",
    "healthcare_agent_name", "healthcare_agent_phone",
    "successor_healthcare_agent_name",
    "agent_name", "agent_address", "successor_agent_name",
    "successor_trustee_name",
    "residuary_beneficiaries", "specific_bequests",
    "living_will_directives", "healthcare_agent_limitations",
    "anatomical_gift_directions", "additional_powers",
    "initial_property", "distribution_age", "marital_share",
    "testator_address", "declarant_address", "principal_address",
    "grantor_address", "parent_address",
    "testator_city", "declarant_city", "principal_city",
    "grantor_city", "parent_city",
    "upbringing_wishes", "education_wishes", "religious_wishes",
    "financial_provisions", "extended_family",
})

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_NAME_SEQ_RE = re.compile(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+")
_WITNESS_SIG_RE = re.compile(r"witness[^\n]{0,30}signature|signature[^\n]{0,30}witness", re.I)
_TESTATOR_SIG_RE = re.compile(r"signature[^\n]{0,40}testator|testator[^\n]{0,40}signature", re.I)
#: The v2 templates lay signature blocks out as a rule line followed by
#: a role-labeled name line ("____\nArjun Sharma, Testator") and witness
#: blocks headed "Witness 1:" / "Witness 2:" with the word "Signature" on
#: its own line — neither matches the same-line patterns above, so the
#: structural shapes count too (first production run: clean will read
#: as 0 witness signatures).
_TESTATOR_BLOCK_RE = re.compile(
    r"_{8,}[^\n]*\n(?:[^\n]*\n){0,2}[^\n]{0,80}(testator|declarant|principal|grantor)",
    re.I,
)
_WITNESS_HEADER_RE = re.compile(r"witness\s*\d+\s*:", re.I)
_CODE_RE = re.compile(r"\d+(?:\.\d+)*-\d+")


@dataclass
class Finding:
    layer: str
    check: str
    severity: str
    document: str | None
    field: str | None
    excerpt: str | None
    explanation: str
    suggested_fix: str | None
    package_id: str | None = None


def finding_hash(finding: Finding) -> str:
    """Content hash (layer + check + location + normalised evidence)."""
    payload = {
        "layer": finding.layer,
        "check": finding.check,
        "document": finding.document,
        "field": finding.field,
        "excerpt": (finding.excerpt or "").strip().lower(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (0 if ca == cb else 1),
            ))
        previous = current
    return previous[-1]


def _name_sequences(text: str) -> list[str]:
    """Capitalised name-like sequences (2+ words, each Capitalised)."""
    return _NAME_SEQ_RE.findall(text or "")


def _windows(sequence: str, size: int) -> list[str]:
    tokens = sequence.split()
    if len(tokens) < size:
        return []
    return [" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1)]


def _canonical_names(source: dict[str, Any], context: dict[str, str]) -> list[str]:
    names: list[str] = []
    for person in source.get("persons") or []:
        name = str(person.get("full_name") or "").strip()
        if name:
            names.append(name)
    for key in ("testator_name", "testator_full_name", "spouse_name"):
        value = str(context.get(key) or "").strip()
        if value:
            names.append(value)
    # Role slot values are person names too (whitelisted slots only —
    # trust_name / household_name are not people).
    for key in (
        "executor_name", "alternate_executor_name", "agent_name",
        "successor_agent_name", "healthcare_agent_name",
        "successor_healthcare_agent_name", "guardian_name",
        "alternate_guardian_name", "trustee_name", "successor_trustee_name",
    ):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _primary_context(snapshots: list[dict[str, Any]]) -> dict[str, str]:
    for snap in snapshots:
        if snap.get("template_type") == "will":
            return dict(snap.get("context") or {})
    return dict(snapshots[0].get("context") or {}) if snapshots else {}


# ---------------------------------------------------------------------------
# name_consistency
# ---------------------------------------------------------------------------


def _check_name_consistency(
    snapshots: list[dict[str, Any]], source: dict[str, Any]
) -> list[Finding]:
    context = _primary_context(snapshots)
    canonical = _canonical_names(source, context)
    canonical_set = set(canonical)
    findings: list[Finding] = []
    emitted: set[tuple[str, str, str]] = set()
    for snap in snapshots:
        body = str(snap.get("body_text") or "")
        sequences = _name_sequences(body)
        for name in canonical:
            size = len(name.split())
            if size < 2:
                continue
            for sequence in sequences:
                for window in _windows(sequence, size):
                    if window == name:
                        continue
                    # A window that exactly matches a DIFFERENT canonical
                    # person is that person's correct name, not a
                    # misspelling — families share surnames, so similar
                    # names (spouse Priya, daughter Diya) sit within
                    # edit distance 2 of each other legitimately.
                    if window in canonical_set:
                        continue
                    if _levenshtein(window.lower(), name.lower()) <= 2:
                        key = (str(snap.get("template_type")), name, window)
                        if key in emitted:
                            continue
                        emitted.add(key)
                        findings.append(Finding(
                            layer=LAYER,
                            check="name_consistency",
                            severity=SHOULD_FIX,
                            document=str(snap.get("template_type")),
                            field=None,
                            excerpt=window,
                            explanation=(
                                f"The name \"{window}\" in this document differs "
                                f"from \"{name}\" used elsewhere in the plan. "
                                "Names in legal documents should match exactly."
                            ),
                            suggested_fix=f"Make the spelling match: \"{name}\".",
                            package_id=snap.get("package_id"),
                        ))
    return findings


# ---------------------------------------------------------------------------
# role_consistency
# ---------------------------------------------------------------------------

#: (family label, primary slot, successor slot, document that carries it)
_ROLE_FAMILIES: tuple[tuple[str, str, str, str], ...] = (
    ("executor", "executor_name", "alternate_executor_name", "will"),
    ("agent", "agent_name", "successor_agent_name", "poa"),
    ("healthcare agent", "healthcare_agent_name",
     "successor_healthcare_agent_name", "advance_directive"),
    ("trustee", "trustee_name", "successor_trustee_name", "trust"),
    ("guardian", "guardian_name", "alternate_guardian_name", "guardianship"),
)

#: (nomination_type, priority) -> context slot.
#: ("trustee", 1) is deliberately absent: the renderer sets trustee_name
#: to the grantor by design (the grantor is the initial trustee of the
#: revocable trust), so a priority-1 trustee nomination is not
#: comparable to that slot — cross-checking it flagged every trust.
_NOMINATION_SLOTS: dict[tuple[str, int], str] = {
    ("executor", 1): "executor_name",
    ("executor", 2): "alternate_executor_name",
    ("guardian", 1): "guardian_name",
    ("guardian", 2): "alternate_guardian_name",
    ("healthcare_agent", 1): "healthcare_agent_name",
    ("healthcare_agent", 2): "successor_healthcare_agent_name",
    ("financial_agent", 1): "agent_name",
    ("financial_agent", 2): "successor_agent_name",
    ("trustee", 2): "successor_trustee_name",
}


def _check_role_consistency(
    snapshots: list[dict[str, Any]], source: dict[str, Any]
) -> list[Finding]:
    context = _primary_context(snapshots)
    findings: list[Finding] = []
    for label, primary_slot, successor_slot, document in _ROLE_FAMILIES:
        primary = str(context.get(primary_slot) or "").strip()
        successor = str(context.get(successor_slot) or "").strip()
        if primary and successor and primary.lower() == successor.lower():
            findings.append(Finding(
                layer=LAYER,
                check="role_consistency",
                severity=SHOULD_FIX,
                document=document,
                field=successor_slot,
                excerpt=successor,
                explanation=(
                    f"{primary} is named as both the {label} and the successor "
                    f"{label}. A successor is the backup if the first choice "
                    "cannot serve, so the two roles need different people."
                ),
                suggested_fix=f"Name a different person as the successor {label}.",
            ))
        elif successor and not primary:
            findings.append(Finding(
                layer=LAYER,
                check="role_consistency",
                severity=SHOULD_FIX,
                document=document,
                field=successor_slot,
                excerpt=successor,
                explanation=(
                    f"A successor {label} ({successor}) is named, but no "
                    f"primary {label} is recorded."
                ),
                suggested_fix=f"Name a primary {label}, or remove the successor.",
            ))
    persons_by_id = {
        str(p.get("id")): str(p.get("full_name") or "").strip()
        for p in source.get("persons") or []
    }
    for nomination in source.get("nominations") or []:
        # Withdrawn nominations are dead; proposed/confirmed are live
        # (the nominations table's status vocabulary).
        if str(nomination.get("status") or "proposed") == "withdrawn":
            continue
        slot = _NOMINATION_SLOTS.get(
            (str(nomination.get("nomination_type")), int(nomination.get("priority") or 0))
        )
        if slot is None:
            continue
        nominated = persons_by_id.get(str(nomination.get("person_id")), "")
        rendered = str(context.get(slot) or "").strip()
        if nominated and rendered and nominated.lower() != rendered.lower():
            findings.append(Finding(
                layer=LAYER,
                check="role_consistency",
                severity=SHOULD_FIX,
                document=None,
                field=slot,
                excerpt=rendered,
                explanation=(
                    f"The nominations record names {nominated} for this role, "
                    f"but the documents name {rendered}. The two records "
                    "should agree."
                ),
                suggested_fix=(
                    f"Decide which person holds this role and make the "
                    f"records match ({nominated} vs {rendered})."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# date_age_arithmetic
# ---------------------------------------------------------------------------


def _parse_dob(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _age_on(dob: date, today: date) -> int:
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def _check_date_age(
    snapshots: list[dict[str, Any]], source: dict[str, Any], today: date
) -> list[Finding]:
    findings: list[Finding] = []
    minors: list[dict[str, Any]] = []
    for person in source.get("persons") or []:
        name = str(person.get("full_name") or "").strip()
        kind = str(person.get("kind") or "").strip().lower()
        dob = _parse_dob(person.get("date_of_birth"))
        if dob is None or not name:
            continue
        if dob > today:
            findings.append(Finding(
                layer=LAYER,
                check="date_age_arithmetic",
                severity=SHOULD_FIX,
                document=None,
                field="date_of_birth",
                excerpt=str(person.get("date_of_birth")),
                explanation=(
                    f"{name}'s date of birth ({person.get('date_of_birth')}) "
                    "is in the future, which cannot be right."
                ),
                suggested_fix=f"Correct {name}'s date of birth.",
            ))
            continue
        age = _age_on(dob, today)
        if kind in _MINOR_KINDS:
            minors.append(person)
            if age >= 18:
                findings.append(Finding(
                    layer=LAYER,
                    check="date_age_arithmetic",
                    severity=SHOULD_FIX,
                    document="guardianship",
                    field="date_of_birth",
                    excerpt=str(person.get("date_of_birth")),
                    explanation=(
                        f"{name} is recorded as a minor child, but their date "
                        f"of birth ({person.get('date_of_birth')}) makes them "
                        f"{age}. Guardianship designations apply to minors."
                    ),
                    suggested_fix=(
                        f"Correct {name}'s date of birth, or update their "
                        "record if they are now an adult."
                    ),
                ))
        elif age < 18:
            findings.append(Finding(
                layer=LAYER,
                check="date_age_arithmetic",
                severity=SHOULD_FIX,
                document=None,
                field="date_of_birth",
                excerpt=str(person.get("date_of_birth")),
                explanation=(
                    f"{name} is recorded as an adult, but their date of "
                    f"birth ({person.get('date_of_birth')}) makes them {age}."
                ),
                suggested_fix=f"Correct {name}'s date of birth or record type.",
            ))
    context = _primary_context(snapshots)
    answers = source.get("answers") or {}
    gate = answers.get("has_minor_children")
    children_list = str(context.get("children_list") or "").strip()
    has_child_content = bool(children_list) and children_list != "___"
    if gate is True and not minors and not has_child_content:
        findings.append(Finding(
            layer=LAYER,
            check="date_age_arithmetic",
            severity=SHOULD_FIX,
            document="guardianship",
            field="has_minor_children",
            excerpt=None,
            explanation=(
                "The plan says there are minor children, but no minor child "
                "is recorded in the household and the documents list no "
                "children. The guardianship designation has nobody to cover."
            ),
            suggested_fix="Add the minor children to the household records.",
        ))
    elif gate is False and minors:
        names = ", ".join(str(p.get("full_name")) for p in minors)
        findings.append(Finding(
            layer=LAYER,
            check="date_age_arithmetic",
            severity=SHOULD_FIX,
            document="guardianship",
            field="has_minor_children",
            excerpt=None,
            explanation=(
                f"The plan says there are no minor children, but {names} "
                "is recorded as a minor. Guardianship coverage may be missing."
            ),
            suggested_fix="Correct the minor-children answer or the records.",
        ))
    return findings


# ---------------------------------------------------------------------------
# pack_completeness
# ---------------------------------------------------------------------------


def _check_pack_completeness(
    snapshots: list[dict[str, Any]], source: dict[str, Any]
) -> list[Finding]:
    expected = [str(t) for t in source.get("expected_set") or []]
    present = [str(s.get("template_type")) for s in snapshots]
    findings: list[Finding] = []
    for missing in [t for t in expected if t not in present]:
        findings.append(Finding(
            layer=LAYER,
            check="pack_completeness",
            severity=SHOULD_FIX,
            document=missing,
            field=None,
            excerpt=None,
            explanation=(
                f"The plan's document set should include a {missing}, but "
                "no generated draft of it is in the package."
            ),
            suggested_fix=f"Regenerate the drafts so the {missing} is included.",
            package_id=None,
        ))
    for extra in [t for t in present if t not in expected]:
        findings.append(Finding(
            layer=LAYER,
            check="pack_completeness",
            severity=SHOULD_FIX,
            document=extra,
            field=None,
            excerpt=None,
            explanation=(
                f"The package includes a {extra}, which is not part of "
                "this plan's document set."
            ),
            suggested_fix=f"Remove the {extra} from the package or correct the plan.",
            package_id=None,
        ))
    return findings


# ---------------------------------------------------------------------------
# unfilled_placeholders
# ---------------------------------------------------------------------------


def _check_unfilled_placeholders(
    snapshots: list[dict[str, Any]], source: dict[str, Any]
) -> list[Finding]:
    # A blank family slot is a defect only when the family actually
    # supplied — or was required to supply — the underlying answer and
    # it still rendered blank (the answered-but-dropped defect class).
    # An OPTIONAL question the family skipped leaves the slot unset by
    # design; flagging it would cry wolf on every packet (the first
    # production run flagged 7 such blanks on a complete packet).
    answered = {str(k) for k in (source.get("answers") or {})}
    required_q = {str(k) for k in source.get("required_question_keys") or []}
    slot_sources: dict[str, Any] = source.get("slot_answer_keys") or {}
    findings: list[Finding] = []
    for snap in snapshots:
        template_body = str(snap.get("template_body") or "")
        context = dict(snap.get("context") or {})
        for token in sorted(set(_TOKEN_RE.findall(template_body))):
            if token in context:
                continue
            if token not in _FAMILY_SLOTS and token not in slot_sources:
                continue  # attorney-fill (witness/notary/date blanks)
            sources = [str(k) for k in slot_sources.get(token) or [token]]
            if not any(k in answered or k in required_q for k in sources):
                continue  # optional and unanswered — blank by design
            findings.append(Finding(
                layer=LAYER,
                check="unfilled_placeholders",
                severity=SHOULD_FIX,
                document=str(snap.get("template_type")),
                field=token,
                excerpt="{{" + token + "}}",
                explanation=(
                    f"The {snap.get('template_type')} draft has a blank where "
                    f"\"{token}\" should appear — that information comes from "
                    "the family's answers and is missing."
                ),
                suggested_fix=f"Provide the missing \"{token}\" answer, then regenerate.",
                package_id=snap.get("package_id"),
            ))
    return findings


# ---------------------------------------------------------------------------
# va_execution_requirements
# ---------------------------------------------------------------------------


def _check_execution_requirements(
    snapshots: list[dict[str, Any]], profiles: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    for snap in snapshots:
        doc_type = str(snap.get("template_type"))
        profile = profiles.get(doc_type)
        if not profile:
            continue
        body = str(snap.get("body_text") or "")
        requirements = profile.get("requirements") or {}
        if requirements.get("testator_signature_required"):
            if not (_TESTATOR_SIG_RE.search(body) or _TESTATOR_BLOCK_RE.search(body)):
                findings.append(Finding(
                    layer=LAYER,
                    check="va_execution_requirements",
                    severity=SHOULD_FIX,
                    document=doc_type,
                    field="signature_block",
                    excerpt=None,
                    explanation=(
                        f"The {doc_type} draft has no signature block for the "
                        "person making the plan, which its execution profile "
                        "requires."
                    ),
                    suggested_fix="Restore the signature block in the template.",
                    package_id=snap.get("package_id"),
                ))
        witnesses = requirements.get("witnesses") or {}
        required_count = int(witnesses.get("required_count") or 0)
        if required_count:
            found = max(
                len(_WITNESS_SIG_RE.findall(body)),
                len(_WITNESS_HEADER_RE.findall(body)),
            )
            if found < required_count:
                findings.append(Finding(
                    layer=LAYER,
                    check="va_execution_requirements",
                    severity=SHOULD_FIX,
                    document=doc_type,
                    field="witness_block",
                    excerpt=None,
                    explanation=(
                        f"The {doc_type} draft's execution profile requires "
                        f"{required_count} witness signatures, but the draft "
                        f"shows {found} witness signature line(s)."
                    ),
                    suggested_fix="Restore the witness signature block in the template.",
                    package_id=snap.get("package_id"),
                ))
        notary = requirements.get("notary") or {}
        if notary.get("recommended_self_proving_affidavit"):
            if "affidavit" not in body.lower():
                findings.append(Finding(
                    layer=LAYER,
                    check="va_execution_requirements",
                    severity=QUESTION,
                    document=doc_type,
                    field="self_proving_affidavit",
                    excerpt=None,
                    explanation=(
                        f"The {doc_type} draft has no self-proving affidavit. "
                        "Virginia does not require one, but the execution "
                        "profile recommends it — worth asking the attorney "
                        "whether to include it."
                    ),
                    suggested_fix="Ask the attorney about adding a self-proving affidavit.",
                    package_id=snap.get("package_id"),
                ))
        citation = str((profile.get("source") or {}).get("citation") or "")
        code_match = _CODE_RE.search(citation)
        if code_match and code_match.group(0) not in body:
            findings.append(Finding(
                layer=LAYER,
                check="va_execution_requirements",
                severity=SHOULD_FIX,
                document=doc_type,
                field="statutory_citation",
                excerpt=None,
                explanation=(
                    f"The {doc_type} draft does not cite {citation}, the "
                    "statute its execution profile is based on."
                ),
                suggested_fix=f"Restore the citation to {citation} in the template.",
                package_id=snap.get("package_id"),
            ))
    return findings


# ---------------------------------------------------------------------------
# reference_integrity
# ---------------------------------------------------------------------------


def _check_reference_integrity(
    snapshots: list[dict[str, Any]], source: dict[str, Any]
) -> list[Finding]:
    context = _primary_context(snapshots)
    canonical = _canonical_names(source, context)
    findings: list[Finding] = []
    residuary = str(context.get("residuary_beneficiaries") or "")
    for sequence in _name_sequences(residuary):
        for name in canonical:
            size = len(name.split())
            for window in _windows(sequence, size):
                if window == name or _levenshtein(window.lower(), name.lower()) <= 2:
                    break
            else:
                continue
            break
        else:
            findings.append(Finding(
                layer=LAYER,
                check="reference_integrity",
                severity=SHOULD_FIX,
                document="will",
                field="residuary_beneficiaries",
                excerpt=sequence,
                explanation=(
                    f"The will names {sequence} as a residuary beneficiary, "
                    "but no person with that name is in the household records."
                ),
                suggested_fix=(
                    f"Add {sequence} to the household records, or correct "
                    "the residuary beneficiary answer."
                ),
            ))
    trust_snap = next(
        (s for s in snapshots if s.get("template_type") == "trust"), None
    )
    if trust_snap is not None:
        trust_context = dict(trust_snap.get("context") or {})
        initial = str(trust_context.get("initial_property") or "").strip()
        if not initial or initial == "___":
            findings.append(Finding(
                layer=LAYER,
                check="reference_integrity",
                severity=SHOULD_FIX,
                document="trust",
                field="initial_property",
                excerpt=None,
                explanation=(
                    "The trust draft does not describe any property being "
                    "transferred into the trust, so the trust would start "
                    "out holding nothing."
                ),
                suggested_fix="Describe the initial trust property, then regenerate.",
                package_id=trust_snap.get("package_id"),
            ))
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_deterministic_checks(
    *,
    snapshots: list[dict[str, Any]],
    source: dict[str, Any],
    execution_profiles: dict[str, Any],
    today: date,
) -> list[Finding]:
    """Run every Layer 1 check over the frozen snapshot set."""
    findings: list[Finding] = []
    findings.extend(_check_name_consistency(snapshots, source))
    findings.extend(_check_role_consistency(snapshots, source))
    findings.extend(_check_date_age(snapshots, source, today))
    findings.extend(_check_pack_completeness(snapshots, source))
    findings.extend(_check_unfilled_placeholders(snapshots, source))
    findings.extend(_check_execution_requirements(snapshots, execution_profiles))
    findings.extend(_check_reference_integrity(snapshots, source))
    # Idempotent by content hash: identical findings collapse.
    seen: dict[str, Finding] = {}
    for finding in findings:
        seen.setdefault(finding_hash(finding), finding)
    return list(seen.values())
