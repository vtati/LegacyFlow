"""Retrieval golden-suite runner: lexical vs hybrid over the VA pack.

Scores the cases in ``evals/golden/retrieval.jsonl`` against the live
pack knowledge documents (sample content included — this is a synthetic
evaluation posture, the same one pack docs declare; production gating
is a separate concern):

- **lexical**: the production doc-level path (``service._retrieve``).
- **hybrid**: chunk-level ``hybrid_rank`` with the chosen embedder.

Metrics per mode: top-1 accuracy and recall@3 over cases with a gold
document, plus fail-closed correctness on the no-match case (gold null
means success = empty result).

Usage:
    python -m evals.retrieval_eval --embedder fake   # mechanics smoke only
    python -m evals.retrieval_eval --embedder openai # real comparison;
        needs OPENAI_API_KEY in the environment (run server-side).

The fake embedder has NO semantic quality (hashing); never quote its
hybrid numbers as evidence. The openai run is the decision-grade one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domains.ai import service as ai_service  # noqa: E402
from app.domains.ai.retrieval import (  # noqa: E402
    FakeEmbedder,
    OpenAIEmbedder,
    chunk_knowledge_doc,
    hybrid_rank,
)
from app.domains.legal_content.pack_loader import get_pack  # noqa: E402

CASES_PATH = Path(__file__).parent / "golden" / "retrieval.jsonl"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _hit(ranked_ids: list[str], gold: dict[str, Any], depth: int) -> bool:
    return any(g in ranked_ids[:depth] for g in gold.get("acceptable", []))


def score_cases(cases: list[dict[str, Any]], ranker) -> dict[str, Any]:
    """ranker(question) -> ordered content_id list (may be empty)."""
    per_case: list[dict[str, Any]] = []
    scored = [c for c in cases if c["gold"].get("content_id")]
    top1 = recall3 = 0
    for case in scored:
        ranked = ranker(case["input"]["question"])
        hit1 = _hit(ranked, case["gold"], 1)
        hit3 = _hit(ranked, case["gold"], 3)
        top1 += hit1
        recall3 += hit3
        per_case.append(
            {"id": case["id"], "top1": hit1, "recall3": hit3, "ranked": ranked[:3]}
        )
    no_match = [c for c in cases if not c["gold"].get("content_id")]
    fail_closed = sum(1 for c in no_match if ranker(c["input"]["question"]) == [])
    return {
        "cases": len(scored),
        "top1": top1 / len(scored) if scored else 0.0,
        "recall@3": recall3 / len(scored) if scored else 0.0,
        "fail_closed": f"{fail_closed}/{len(no_match)}",
        "per_case": per_case,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["fake", "openai"], default="fake")
    parser.add_argument("--out", default=None, help="write JSON results to this path")
    args = parser.parse_args(argv)

    pack = get_pack("US-VA", "2.0.0")
    cases = load_cases()

    def lexical_ranker(question: str) -> list[str]:
        docs = ai_service._retrieve(question, pack, allow_sample=True)
        return [str(d.get("content_id")) for d in docs]

    chunks = []
    for doc in pack.knowledge_docs:
        chunks.extend(chunk_knowledge_doc(doc))

    if args.embedder == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            print("OPENAI_API_KEY not set; cannot run the openai embedder.")
            return 1
        embedder: Any = OpenAIEmbedder(key)
    else:
        embedder = FakeEmbedder(dim=256)

    def hybrid_ranker(question: str) -> list[str]:
        ranked = hybrid_rank(question, chunks, embedder)
        seen: list[str] = []
        for chunk in ranked:
            if chunk.content_id not in seen:
                seen.append(chunk.content_id)
        return seen

    results = {
        "embedder": args.embedder,
        "lexical": score_cases(cases, lexical_ranker),
        "hybrid": score_cases(cases, hybrid_ranker),
    }
    print(json.dumps({k: v for k, v in results.items() if k != "per_case"}, indent=2))
    print("lexical per-case:", json.dumps(results["lexical"]["per_case"], indent=2))
    print("hybrid per-case:", json.dumps(results["hybrid"]["per_case"], indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
