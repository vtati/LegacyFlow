"""Hybrid retrieval for the Ask gateway: chunker, embedder seam, fusion.

Pure-function tests (no DB, no network). The semantic-quality proof lives
in the retrieval golden suite under ``evals/``; these tests pin the
mechanics: chunk spans, deterministic test embeddings, RRF fusion, and
that the vector signal can outrank a purely lexical winner.
"""

from __future__ import annotations

import math

from app.domains.ai.retrieval import (
    FakeEmbedder,
    KnowledgeChunk,
    chunk_knowledge_doc,
    hybrid_rank,
    lexical_rank,
    rrf_fuse,
)


def _doc(body: str, **overrides):
    doc = {
        "content_id": "guardian-basics-v1",
        "title": "Choosing Guardians",
        "section": "Backups",
        "status": "approved",
        "body_markdown": body,
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def test_chunker_splits_on_headings_and_preserves_spans():
    body = (
        "# Choosing guardians\n\nNaming a guardian tells the court your wishes.\n\n"
        "## Backups\n\nA backup guardian matters because circumstances change.\n"
    )
    chunks = chunk_knowledge_doc(_doc(body))
    assert len(chunks) == 2
    assert [c.seq for c in chunks] == [0, 1]
    for chunk in chunks:
        assert body[chunk.char_start : chunk.char_end] == chunk.text
    assert chunks[0].content_id == "guardian-basics-v1"
    assert chunks[0].status == "approved"
    assert "Naming a guardian" in chunks[0].text
    assert "backup guardian" in chunks[1].text


def test_chunker_single_chunk_without_headings():
    body = "A will must be in writing and signed by the testator."
    chunks = chunk_knowledge_doc(_doc(body))
    assert len(chunks) == 1
    assert chunks[0].text == body


def test_chunker_empty_body_yields_no_chunks():
    assert chunk_knowledge_doc(_doc("")) == []
    assert chunk_knowledge_doc(_doc("   \n\n")) == []


def test_chunker_carries_doc_section_when_no_heading():
    chunks = chunk_knowledge_doc(_doc("Plain text with no heading."))
    assert chunks[0].section == "Backups"


def test_chunker_frontmatter_sample_marks_chunk_sample():
    doc = _doc("Some pending text.", status="approved")
    doc["frontmatter"] = {"sample": True}
    chunks = chunk_knowledge_doc(doc)
    assert chunks[0].status == "sample"


def test_chunker_splits_oversized_sections_with_exact_spans():
    paragraph = "guardian trustee executor beneficiary probate " * 20  # ~920 chars
    body = "# Big section\n\n" + "\n\n".join(paragraph for _ in range(10))
    chunks = chunk_knowledge_doc(_doc(body))
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 4000
        assert body[chunk.char_start : chunk.char_end] == chunk.text
    assert [c.seq for c in chunks] == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# FakeEmbedder (test seam — hashing, lexical overlap only, never semantic)
# ---------------------------------------------------------------------------


def test_fake_embedder_is_deterministic_and_normalized():
    emb = FakeEmbedder(dim=32)
    a = emb.embed("choosing a guardian for my children")
    b = emb.embed("choosing a guardian for my children")
    assert a == b
    assert len(a) == 32
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)


def test_fake_embedder_differs_for_different_text():
    emb = FakeEmbedder(dim=32)
    assert emb.embed("guardian children") != emb.embed("probate taxes")


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def test_rrf_double_winner_ranks_first():
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]], k=60)
    assert fused[0][0] == "a"
    scores = dict(fused)
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_single_list_presence_scores_lower():
    fused = dict(rrf_fuse([["a", "b"], ["a"]], k=60))
    assert fused["a"] > fused["b"]


# ---------------------------------------------------------------------------
# Hybrid ranking: vector signal can beat the lexical winner
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Hand-set vectors: query sits next to chunk A, far from chunk B."""

    def embed(self, text: str) -> list[float]:
        if text.startswith("QUERY"):
            return [0.95, 0.05]
        if "alpha" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


def _chunk(seq: int, text: str, content_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        content_id=content_id,
        title=content_id,
        section=None,
        seq=seq,
        text=text,
        char_start=0,
        char_end=len(text),
        status="approved",
    )


def test_hybrid_rank_lets_vector_signal_win():
    # Lexically the query matches chunk B ("beta beta beta"); semantically
    # (by the stub vectors) it belongs with chunk A.
    chunks = [
        _chunk(0, "alpha guardianship nominations explained", "doc-a"),
        _chunk(1, "beta beta beta executor trustee probate", "doc-b"),
    ]
    query = "QUERY beta"
    lexical = lexical_rank(query, chunks)
    assert lexical[0].content_id == "doc-b"  # lexical alone picks B
    ranked = hybrid_rank(query, chunks, _StubEmbedder())
    assert ranked[0].content_id == "doc-a"  # hybrid fusion picks A


def test_hybrid_rank_returns_only_positive_evidence():
    chunks = [_chunk(0, "alpha content", "doc-a")]
    ranked = hybrid_rank("zzz qqq unrelated", chunks, _StubEmbedder())
    assert ranked == []
