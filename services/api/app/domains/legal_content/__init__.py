"""Legal-content domain: jurisdiction packs, rules, approved content (artifact §9, §10).

Pack layout on disk (``content-packs/<pack_id>/<version>/``)::

    manifest.yaml            # pack identity, capabilities, sources (§9.3)
    escalation-rules.yaml    # declarative eligibility/escalation rules (§9.4)
    execution-profiles.yaml  # signing execution profiles (§9.5)
    funding-playbooks.yaml   # funding task playbooks per asset category
    question-sets/*.yaml     # guided-intake question schemas
    knowledge/*.md           # approved educational content (frontmatter + markdown)

No application code may branch on jurisdiction outside this loader and the
rule evaluator: state-specific behavior comes from pack data (CLAUDE.md).
"""

from app.domains.legal_content.pack_loader import (
    EscalationRule,
    LoadedPack,
    PackManifest,
    evaluate_rule_expression,
    get_pack,
    list_packs,
)

__all__ = [
    "EscalationRule",
    "LoadedPack",
    "PackManifest",
    "evaluate_rule_expression",
    "get_pack",
    "list_packs",
]
