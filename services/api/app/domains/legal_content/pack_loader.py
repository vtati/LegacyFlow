"""Jurisdiction-pack loader and declarative rule-expression evaluator.

Packs live under ``content-packs/<pack_id>/<semantic_version>/`` (repo root
by default, overridable with ``CONTENT_PACKS_DIR``). The loader parses the
manifest, escalation rules, execution profiles, funding playbooks, question
sets, and knowledge files into typed structures.

Rule expressions (artifact §9.4, e.g. ``has_child_with_special_needs == true``)
use a tiny safe grammar — no ``eval()``::

    expr     := or_expr
    or_expr  := and_expr ("or" and_expr)*
    and_expr := not_expr ("and" not_expr)*
    not_expr := "not" not_expr | comparison
    comparison := operand (("==" | "!=") operand)?
    operand  := identifier | boolean | number | string | "(" or_expr ")"

Identifiers resolve against the facts dict using dotted lookup
(``assets.real_property_outside_jurisdiction``); missing facts evaluate to
``None`` (never equal to a concrete value, so rules fail closed).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Manifest / pack structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackSource:
    source_id: str
    authority_type: str
    citation: str | None
    source_url: str | None
    retrieved_on: str | None


@dataclass(frozen=True)
class PackManifest:
    pack_id: str
    country_code: str
    subdivision_code: str
    display_name: str
    semantic_version: str
    status: str
    effective_from: str | None
    capabilities: dict[str, Any]
    requirements: dict[str, Any]
    sources: tuple[PackSource, ...]

    @property
    def jurisdiction_code(self) -> str:
        return f"{self.country_code}-{self.subdivision_code}"


@dataclass(frozen=True)
class EscalationRule:
    id: str
    version: str
    when: str
    severity: str  # info | review | blocking
    outcome: str
    user_message: str | None = None


@dataclass
class LoadedPack:
    """Everything the application needs from one pack version."""

    manifest: PackManifest
    escalation_rules: list[EscalationRule] = field(default_factory=list)
    execution_profiles: list[dict[str, Any]] = field(default_factory=list)
    funding_playbooks: list[dict[str, Any]] = field(default_factory=list)
    question_sets: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge_docs: list[dict[str, Any]] = field(default_factory=list)
    raw_dir: Path | None = None


def _packs_root() -> Path:
    override = os.environ.get("CONTENT_PACKS_DIR")
    if override:
        return Path(override)
    # services/api/app/domains/legal_content/pack_loader.py -> repo root is
    # five levels up.
    return Path(__file__).resolve().parents[5] / "content-packs"


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _parse_manifest(data: dict[str, Any]) -> PackManifest:
    pack = data.get("pack", {})
    sources = tuple(
        PackSource(
            source_id=s.get("source_id", ""),
            authority_type=s.get("authority_type", "other"),
            citation=s.get("citation"),
            source_url=s.get("source_url"),
            retrieved_on=s.get("retrieved_on"),
        )
        for s in data.get("sources", [])
    )
    return PackManifest(
        pack_id=str(pack.get("id", "")),
        country_code=str(pack.get("country_code", "")),
        subdivision_code=str(pack.get("subdivision_code", "")),
        display_name=str(pack.get("display_name", "")),
        semantic_version=str(pack.get("semantic_version", "")),
        status=str(pack.get("status", "draft")),
        effective_from=pack.get("effective_from"),
        capabilities=dict(data.get("capabilities", {})),
        requirements=dict(data.get("requirements", {})),
        sources=sources,
    )


@lru_cache(maxsize=32)
def _list_packs_cached(packs_root: str) -> tuple[PackManifest, ...]:
    root = Path(packs_root)
    manifests: list[PackManifest] = []
    if not root.is_dir():
        return tuple(manifests)
    for pack_dir in sorted(root.iterdir()):
        if not pack_dir.is_dir():
            continue
        for version_dir in sorted(pack_dir.iterdir()):
            manifest_path = version_dir / "manifest.yaml"
            if version_dir.is_dir() and manifest_path.is_file():
                manifests.append(_parse_manifest(_read_yaml(manifest_path)))
    return tuple(manifests)


def list_packs() -> list[PackManifest]:
    """List every pack version found under the packs root.

    Results are cached in-process, keyed by the packs root (so
    ``CONTENT_PACKS_DIR`` overrides are honored); see
    :func:`clear_pack_cache`.
    """
    return list(_list_packs_cached(str(_packs_root())))


@lru_cache(maxsize=128)
def _get_pack_cached(jurisdiction_code: str, version: str | None, packs_root: str) -> LoadedPack:
    root = Path(packs_root)
    candidates: list[tuple[str, Path]] = []
    for pack_dir in root.iterdir():
        if not pack_dir.is_dir():
            continue
        for version_dir in pack_dir.iterdir():
            manifest_path = version_dir / "manifest.yaml"
            if not (version_dir.is_dir() and manifest_path.is_file()):
                continue
            manifest = _parse_manifest(_read_yaml(manifest_path))
            if manifest.jurisdiction_code == jurisdiction_code:
                candidates.append((manifest.semantic_version, version_dir))
    if not candidates:
        raise FileNotFoundError(f"No pack found for jurisdiction {jurisdiction_code}")
    if version is not None:
        matches = [d for v, d in candidates if v == version]
        if not matches:
            raise FileNotFoundError(
                f"No pack version {version} for jurisdiction {jurisdiction_code}"
            )
        version_dir = matches[0]
    else:
        # Highest semantic version (numeric tuple comparison).
        def _key(item: tuple[str, Path]) -> tuple[int, ...]:
            return tuple(int(p) if p.isdigit() else 0 for p in item[0].split("."))

        version_dir = sorted(candidates, key=_key)[-1][1]

    manifest = _parse_manifest(_read_yaml(version_dir / "manifest.yaml"))

    rules: list[EscalationRule] = []
    rules_path = version_dir / "escalation-rules.yaml"
    if rules_path.is_file():
        for item in _read_yaml(rules_path).get("rules", []):
            rules.append(
                EscalationRule(
                    id=str(item.get("id", "")),
                    version=str(item.get("version", "")),
                    when=str(item.get("when", "")),
                    severity=str(item.get("severity", "review")),
                    outcome=str(item.get("outcome", "")),
                    user_message=item.get("user_message"),
                )
            )

    profiles: list[dict[str, Any]] = []
    profiles_path = version_dir / "execution-profiles.yaml"
    if profiles_path.is_file():
        data = _read_yaml(profiles_path)
        raw_profiles = data.get("execution_profiles") or data.get("execution_profile")
        if isinstance(raw_profiles, dict):
            raw_profiles = [raw_profiles]
        profiles = [dict(p) for p in (raw_profiles or [])]

    playbooks: list[dict[str, Any]] = []
    playbooks_path = version_dir / "funding-playbooks.yaml"
    if playbooks_path.is_file():
        playbooks = [dict(p) for p in _read_yaml(playbooks_path).get("playbooks", [])]

    question_sets: dict[str, dict[str, Any]] = {}
    qs_dir = version_dir / "question-sets"
    if qs_dir.is_dir():
        for qs_file in sorted(qs_dir.glob("*.yaml")):
            question_sets[qs_file.stem] = _read_yaml(qs_file)

    knowledge_docs: list[dict[str, Any]] = []
    knowledge_dir = version_dir / "knowledge"
    if knowledge_dir.is_dir():
        for md_file in sorted(knowledge_dir.glob("*.md")):
            knowledge_docs.append(_parse_knowledge_doc(md_file))

    return LoadedPack(
        manifest=manifest,
        escalation_rules=rules,
        execution_profiles=profiles,
        funding_playbooks=playbooks,
        question_sets=question_sets,
        knowledge_docs=knowledge_docs,
        raw_dir=version_dir,
    )


def get_pack(jurisdiction_code: str, version: str | None = None) -> LoadedPack:
    """Load a pack by jurisdiction code (e.g. ``US-VA``).

    When ``version`` is omitted the highest semantic version wins. Raises
    ``FileNotFoundError`` when the pack (or version) does not exist.

    Results are cached in-process, keyed by jurisdiction, version, and the
    packs root (so ``CONTENT_PACKS_DIR`` overrides are honored and pack
    metadata is not re-parsed from disk per request); treat the returned
    pack as read-only. Use :func:`clear_pack_cache` to drop the cache
    (tests, pack re-import).
    """
    return _get_pack_cached(jurisdiction_code, version, str(_packs_root()))


def clear_pack_cache() -> None:
    """Drop all cached pack data (tests, pack re-import)."""
    _get_pack_cached.cache_clear()
    _list_packs_cached.cache_clear()


def _parse_knowledge_doc(path: Path) -> dict[str, Any]:
    """Parse a knowledge markdown file with ``---`` frontmatter."""
    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            frontmatter = yaml.safe_load(text[3:end]) or {}
            body = text[end + 3 :].lstrip("\n")
    return {
        "path": str(path),
        "content_id": str(frontmatter.get("content_id", path.stem)),
        "title": str(frontmatter.get("title", path.stem)),
        "version": str(frontmatter.get("version", "0.0.0")),
        "section": frontmatter.get("section"),
        "status": str(frontmatter.get("status", "draft")),
        "body_markdown": body,
        "frontmatter": frontmatter,
    }


# ---------------------------------------------------------------------------
# Rule expression evaluator
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<op>==|!=|\(|\))
    | (?P<bool>true|false)
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    """,
    re.VERBOSE,
)

_KEYWORDS = {"and", "or", "not"}


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expression):
        match = _TOKEN_RE.match(expression, pos)
        if not match or match.end() == pos:
            raise ValueError(
                f"Cannot tokenize rule expression near: {expression[pos : pos + 20]!r}"
            )
        kind = match.lastgroup or "ws"
        text = match.group()
        pos = match.end()
        if kind == "ws":
            continue
        tokens.append((kind, text))
    return tokens


class _Parser:
    """Recursive-descent parser producing an AST of nested tuples."""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Any:
        node = self._parse_or()
        if self._pos != len(self._tokens):
            raise ValueError("Unexpected trailing tokens in rule expression.")
        return node

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of rule expression.")
        self._pos += 1
        return token

    def _parse_or(self) -> Any:
        node = self._parse_and()
        while self._peek() == ("word", "or"):
            self._next()
            node = ("or", node, self._parse_and())
        return node

    def _parse_and(self) -> Any:
        node = self._parse_not()
        while self._peek() == ("word", "and"):
            self._next()
            node = ("and", node, self._parse_not())
        return node

    def _parse_not(self) -> Any:
        if self._peek() == ("word", "not"):
            self._next()
            return ("not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_operand()
        peek = self._peek()
        if peek is not None and peek[0] == "op" and peek[1] in ("==", "!="):
            self._next()
            right = self._parse_operand()
            return (peek[1], left, right)
        return left

    def _parse_operand(self) -> Any:
        kind, text = self._next()
        if kind == "op" and text == "(":
            node = self._parse_or()
            closing_kind, closing_text = self._next()
            if (closing_kind, closing_text) != ("op", ")"):
                raise ValueError("Missing closing parenthesis in rule expression.")
            return node
        if kind == "bool":
            return ("lit", text == "true")
        if kind == "number":
            return ("lit", float(text) if "." in text else int(text))
        if kind == "string":
            return ("lit", text[1:-1])
        if kind == "word":
            if text in _KEYWORDS:
                raise ValueError(f"Unexpected keyword {text!r} in rule expression.")
            return ("fact", text)
        raise ValueError(f"Unexpected token {text!r} in rule expression.")


def _resolve_fact(name: str, facts: dict[str, Any]) -> Any:
    current: Any = facts
    for part in name.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _eval(node: Any, facts: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "fact":
        return _resolve_fact(node[1], facts)
    if kind == "not":
        return not _truthy(_eval(node[1], facts))
    if kind == "and":
        return _truthy(_eval(node[1], facts)) and _truthy(_eval(node[2], facts))
    if kind == "or":
        return _truthy(_eval(node[1], facts)) or _truthy(_eval(node[2], facts))
    if kind == "==":
        return _eval(node[1], facts) == _eval(node[2], facts)
    if kind == "!=":
        return _eval(node[1], facts) != _eval(node[2], facts)
    raise ValueError(f"Unknown AST node {kind!r}.")


def _truthy(value: Any) -> bool:
    return value is not None and value is not False and value != 0 and value != ""


def evaluate_rule_expression(expression: str, facts: dict[str, Any]) -> bool:
    """Evaluate a rule ``when`` expression against intake facts.

    Missing facts evaluate to ``None`` so comparisons against concrete
    values fail closed (rule does not fire). Raises ``ValueError`` on
    malformed expressions.
    """
    tokens = _tokenize(expression)
    if not tokens:
        raise ValueError("Empty rule expression.")
    ast = _Parser(tokens).parse()
    return bool(_truthy(_eval(ast, facts)))
