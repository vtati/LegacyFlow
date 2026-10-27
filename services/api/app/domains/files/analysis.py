"""Document analysis for vault uploads (AI extraction → tagging).

Uses the configured LLM provider (``AI_LLM_PROVIDER``) with a JSON-mode
prompt. When the provider is ``deterministic_dev`` (or OpenAI is
unreachable), falls back to a rule-based classifier so the endpoint stays
functional in dev/test without network access.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.domains.ai.provider import get_json_provider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a document analyst for an estate-planning vault. \
Read the extracted text of an uploaded document and return ONLY valid JSON \
(no markdown, no commentary) with this shape:
{
  "document_type": one of ["will", "trust", "deed", "life_insurance", "financial_statement",
    "power_of_attorney", "advance_directive", "operating_agreement", "tax_return", "other"],
  "summary": "2-3 sentence plain-language summary of what this document is and why it matters for estate planning",
  "key_facts": [{"label": "...", "value": "..."}],
  "suggested_tags": ["..."],
  "confidence": 0.0-1.0
}
Key facts should capture the fields that matter for the document type: \
deed -> property address, grantor, grantee, recording info; \
life insurance -> carrier, policy number, face amount, insured, beneficiaries; \
will -> testator, executor, guardians, key bequests; \
trust -> trust name, grantor, trustee, successor trustee; \
power_of_attorney -> principal, agent, powers granted; \
advance_directive -> declarant, healthcare agent, key wishes; \
operating_agreement -> company, members, ownership split; \
tax_return -> tax year, filer, AGI if visible; \
financial_statement -> institution, account type, balance date. \
Tags: 2-6 short lowercase tags like "deed", "real-estate", "insurance", "fairfax-county"."""


def analyze_with_llm(extracted_text: str, filename: str) -> dict[str, Any]:
    """Run the LLM analysis; fall back to rules on any failure."""
    from app.core.config import get_settings

    settings = get_settings()
    provider_name = (settings.AI_LLM_PROVIDER or "deterministic_dev").strip().lower()
    if provider_name == "openai" and settings.OPENAI_API_KEY:
        try:
            return _analyze_openai(
                settings.OPENAI_CHAT_MODEL,
                extracted_text,
                filename,
            )
        except Exception as exc:  # never break the endpoint on provider errors
            logger.warning("OpenAI document analysis failed, using rules: %s", exc)
    return _analyze_rules(extracted_text, filename)


def _analyze_openai(model: str, text: str, filename: str) -> dict[str, Any]:
    """Classify via the JSON gateway; normalize the parsed result."""
    content, _usage = get_json_provider(model=model).complete_json(
        system_prompt=SYSTEM_PROMPT,
        user_content=f"Filename: {filename}\n\nExtracted text:\n{text[:12000]}",
        temperature=0.1,
        timeout=90.0,
    )
    return _normalize(json.loads(content), filename)


# ---------------------------------------------------------------------------
# Deterministic rule-based classifier (dev/test, no network)
# ---------------------------------------------------------------------------

_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("will", ["last will and testament", "i hereby revoke all wills", "executor"]),
    ("trust", ["declaration of trust", "revocable living trust", "as trustee", "trust agreement"]),
    ("deed", ["general warranty deed", "quitclaim deed", "grantor", "grantee", "legal description"]),
    ("life_insurance", ["life insurance", "policy number", "face amount", "death benefit"]),
    ("power_of_attorney", ["power of attorney", "attorney-in-fact", "principal hereby appoints"]),
    ("advance_directive", ["advance medical directive", "health care agent", "healthcare proxy"]),
    ("operating_agreement", ["operating agreement", "limited liability company", "members"]),
    ("tax_return", ["form 1040", "tax return", "adjusted gross income"]),
    ("financial_statement", ["account statement", "account balance", "statement period"]),
]

_TYPE_LABELS = {
    "will": "Will",
    "trust": "Trust",
    "deed": "Deed",
    "life_insurance": "Life insurance policy",
    "financial_statement": "Financial statement",
    "power_of_attorney": "Power of attorney",
    "advance_directive": "Advance directive",
    "operating_agreement": "Operating agreement",
    "tax_return": "Tax return",
    "other": "Other document",
}

_FACT_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "deed": [
        ("Property address", r"(?:property address|premises)[\s:]+([0-9][^\n]{4,70}?)(?=\s{2,}|,?\s*(?:grantor|grantee|legal)|\n|$)"),
        ("Grantor", r"grantor[\s:]+([A-Z][A-Za-z .'\-]{2,40}?)(?=\s{2,}|,?\s*grantee|\n|$)"),
        ("Grantee", r"grantee[\s:]+([A-Z][A-Za-z .'\-]{2,40}?)(?=\s{2,}|\n|$)"),
    ],
    "life_insurance": [
        ("Carrier", r"(?:carrier|insurance company)[\s:]+([A-Z][A-Za-z .&'\-]{2,40}?)(?=\s{2,}|[.,;]\s|\n|$)"),
        ("Policy number", r"policy(?: number| #| no\.?)[\s:]+([A-Z0-9\-]{4,30})"),
        ("Face amount", r"(?:face amount|death benefit|coverage amount)[\s:]+(\$?[0-9,]+(?:\.\d{2})?)"),
    ],
    "will": [
        ("Testator", r"\bi,?\s+([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?),?\s+of\s"),
        ("Executor", r"(?:executor|personal representative)[\s:]+(?:is\s+)?([A-Z][a-z]+ [A-Z][a-z]+)"),
    ],
    "trust": [
        ("Trust name", r"(?:known as|trust shall be known as)\s+the\s+([^\n.]{5,80})"),
        ("Grantor", r"\bi,?\s+([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?),?\s+of\s"),
        ("Successor trustee", r"successor trustee[\s:]+([A-Z][a-z]+ [A-Z][a-z]+)"),
    ],
    "power_of_attorney": [
        ("Principal", r"\bi,?\s+([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?),?\s+of\s"),
        ("Agent", r"(?:agent|attorney-in-fact)[\s:]+([A-Z][a-z]+ [A-Z][a-z]+)"),
    ],
    "operating_agreement": [
        ("Company", r"([A-Z][\w\s,&]+(?:LLC|Inc\.|Corporation))"),
    ],
    "tax_return": [
        ("Tax year", r"(?:tax year|for the year)\s+(\d{4})"),
    ],
}


def _analyze_rules(text: str, filename: str) -> dict[str, Any]:
    lowered = text.lower()
    doc_type = "other"
    for candidate, keywords in _TYPE_RULES:
        if any(kw in lowered for kw in keywords):
            doc_type = candidate
            break
    key_facts: list[dict[str, str]] = []
    for label, pattern in _FACT_PATTERNS.get(doc_type, []):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip().rstrip(".,;")
            if value:
                key_facts.append({"label": label, "value": value})
    base_tags = [doc_type.replace("_", "-")]
    for kw in ("fairfax", "arlington", "virginia"):
        if kw in lowered:
            base_tags.append(kw)
    summary = (
        f"This appears to be a {_TYPE_LABELS[doc_type].lower()} "
        f"(rule-based classification, {len(key_facts)} key facts extracted). "
        "Review the extracted facts before relying on them."
    )
    return _normalize(
        {
            "document_type": doc_type,
            "summary": summary,
            "key_facts": key_facts,
            "suggested_tags": base_tags,
            "confidence": 0.55 if doc_type != "other" else 0.3,
        },
        filename,
    )


_ALLOWED_TYPES = {
    "will",
    "trust",
    "deed",
    "life_insurance",
    "financial_statement",
    "power_of_attorney",
    "advance_directive",
    "operating_agreement",
    "tax_return",
    "other",
}


def _normalize(raw: dict[str, Any], filename: str) -> dict[str, Any]:
    """Coerce raw model/rules output into the contract shape."""
    doc_type = str(raw.get("document_type") or "other").strip().lower()
    if doc_type not in _ALLOWED_TYPES:
        doc_type = "other"
    key_facts: list[dict[str, str]] = []
    for item in raw.get("key_facts") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:80]
        value = str(item.get("value") or "").strip()[:500]
        if label and value:
            key_facts.append({"label": label, "value": value})
    tags: list[str] = []
    for tag in raw.get("suggested_tags") or []:
        t = re.sub(r"[^a-z0-9\-]", "", str(tag).strip().lower().replace(" ", "-"))[:40]
        if t and t not in tags:
            tags.append(t)
    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = min(max(confidence, 0.0), 1.0)
    summary = str(raw.get("summary") or f"Uploaded file {filename}.").strip()[:2000]
    return {
        "document_type": doc_type,
        "summary": summary,
        "key_facts": key_facts[:20],
        "suggested_tags": tags[:10],
        "confidence": confidence,
    }
