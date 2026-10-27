"""Draft-set resolution for family self-serve generation (plan D2).

The generated document set is a deterministic function of the matter's
stored answers, mirroring the v2 pack's own ``applies_if`` gates
(``content-packs/us-va/v2/question-sets/``):

===================  ===================================================
template_type        rule
===================  ===================================================
will                 always included (core instrument)
poa                  unless ``wants_poa_document == false``
advance_directive    unless ``wants_healthcare_directive == false``
guardianship         iff ``has_minor_children == true``
trust                iff ``wants_revocable_trust == true``
===================  ===================================================

``codicil`` / ``trust_amendment`` are NEVER in a fresh plan's set: they
amend already-executed documents, and amendments have their own flow
(``POST /matters/{id}/amendment-requests``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ...core.errors import ValidationAppError
from . import repository as repo

#: The five generatable types, in canonical (render/report) order.
DRAFT_SET_TYPES: tuple[str, ...] = (
    "will",
    "poa",
    "advance_directive",
    "guardianship",
    "trust",
)

# (template_type, gate question key, opt-in?) — opt-in types are included
# iff the gate answer is exactly True; opt-out types are included unless
# the gate answer is exactly False (the pack's `!= false` convention).
_GATES: tuple[tuple[str, str, bool], ...] = (
    ("poa", "wants_poa_document", False),
    ("advance_directive", "wants_healthcare_directive", False),
    ("guardianship", "has_minor_children", True),
    ("trust", "wants_revocable_trust", True),
)


class DraftTemplateMissingError(ValidationAppError):
    """A selected draft-set type has no template in the firm (HTTP 422).

    Raised before anything renders: a silently skipped legal document is
    the worst outcome, so generation fails closed listing the types.
    """

    type = "urn:legacyflow:error:draft_template_missing"
    title = "Draft template missing"
    code = "draft_template_missing"

    def __init__(self, missing_types: list[str]) -> None:
        super().__init__(
            detail=(
                "No template exists in this firm for: " + ", ".join(missing_types) + "."
            )
        )
        self.missing_types = list(missing_types)

    def to_response(self) -> dict[str, Any]:
        return {**super().to_response(), "missing_types": self.missing_types}


def resolve_draft_signals(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-type inclusion + the signal that decided it (determinism made visible)."""
    signals: list[dict[str, Any]] = [
        {"template_type": "will", "included": True, "signal": "always"}
    ]
    for template_type, gate_key, opt_in in _GATES:
        value = answers.get(gate_key)
        if opt_in:
            included = value is True
            signal = f"{gate_key} == true" if included else f"{gate_key} != true"
        else:
            included = value is not False
            signal = f"{gate_key} != false" if included else f"{gate_key} == false"
        signals.append(
            {"template_type": template_type, "included": included, "signal": signal}
        )
    return signals


def resolve_draft_set(answers: dict[str, Any]) -> list[str]:
    """Return the included template types in canonical order."""
    return [
        str(entry["template_type"])
        for entry in resolve_draft_signals(answers)
        if entry["included"]
    ]


def resolve_set_templates(
    conn: Any, firm_id: UUID, pack_id: str | None, types: list[str]
) -> dict[str, dict[str, Any]]:
    """Resolve each selected type to the firm's template row (pack-preferred).

    Raises :class:`DraftTemplateMissingError` listing every type with no
    template, before the caller renders anything.
    """
    resolved: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for template_type in types:
        row = repo.latest_template_by_type(conn, firm_id, template_type, pack_id=pack_id)
        if row is None:
            missing.append(template_type)
        else:
            resolved[template_type] = row
    if missing:
        raise DraftTemplateMissingError(missing)
    return resolved
