"""Hybrid retrieval for the Ask gateway's approved knowledge content.

Replaces the doc-level term-frequency scan in ``service._retrieve`` (kept
as the lexical fallback behind ``AI_RETRIEVAL_MODE``) with chunk-level
hybrid retrieval:

1. **Chunking** — pack knowledge documents are split on Markdown headings
   into chunks that keep their exact character span in the source body,
   so citations stay precise.
2. **Candidates** — lexical term-frequency ranking UNION cosine-similarity
   ranking over stored embeddings (pgvector column; ranking itself runs
   in-process at this corpus scale so tests and production share one
   implementation).
3. **Fusion** — Reciprocal Rank Fusion (k=60) plus a small bonus scaled
   by the chunk's cosine similarity, so a strong semantic match can
   outrank a purely lexical winner without swamping a chunk that wins
   both lists.

The grounding contract is unchanged and enforced by the caller: only
approved pack content (sample only on the existing explicit opt-in),
jurisdiction pack is a hard boundary, and no positive evidence means the
caller fails closed into the attorney-escalation path.

``FakeEmbedder`` is a test/dev seam only: SHA-256 feature hashing gives
deterministic lexical-overlap vectors. It has NO semantic quality and
must never be used to judge retrieval quality — that is what the
retrieval golden suite with the real embedder is for.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

# Mirrors service._STOPWORDS / service._keywords semantics exactly; the
# wiring stage unifies the two tokenizers.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "about", "what", "does", "how", "why",
        "when", "where", "which", "your", "you", "are", "can", "tell", "me",
        "from", "into", "that", "this", "have", "has", "had", "will",
        "would", "there", "their", "they", "them", "then", "than", "also",
        "any", "all", "some", "who", "whom", "whose", "wh", "is", "was",
        "were", "be", "been", "being", "do", "did", "an", "of", "to", "in",
        "on", "at", "by", "as", "or", "it", "its", "my", "our", "not", "but",
    }
)

_TOKEN_RE = re.compile(r"[a-z]{3,}")
_HEADING_RE = re.compile(r"^#{1,6}\s")

RRF_K = 60
VECTOR_BONUS = 0.01
VECTOR_MIN_COSINE = 0.25


def doc_is_sample(doc: dict[str, Any]) -> bool:
    """Effective sample status: frontmatter flag OR status field.

    Single definition shared by lexical retrieval, chunking, and ranking
    so no path can treat attorney-pending content as approved.
    """
    frontmatter = doc.get("frontmatter") or {}
    return str(doc.get("status", "")).lower() == "sample" or bool(
        frontmatter.get("sample")
    )


def _terms(text: str) -> list[str]:
    words = _TOKEN_RE.findall(text.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for word in words:
        if word not in _STOPWORDS and word not in seen:
            seen.add(word)
            terms.append(word)
    return terms


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeChunk:
    content_id: str
    title: str
    section: str | None
    seq: int
    text: str
    char_start: int
    char_end: int
    status: str

    @property
    def key(self) -> str:
        return f"{self.content_id}#{self.seq}"


MAX_CHUNK_CHARS = 4000


def _split_spans(text: str, cap: int) -> list[tuple[int, int]]:
    """Split ``text`` into spans of at most ``cap`` chars.

    Prefers paragraph boundaries; a single paragraph longer than the cap
    is hard-split. Spans are contiguous and cover the whole text.
    """
    if len(text) <= cap:
        return [(0, len(text))]
    cuts = [m.end() for m in re.finditer(r"\n\n+", text)]
    spans: list[tuple[int, int]] = []
    start = 0
    last_good = -1
    for cut in cuts:
        if cut - start <= cap:
            last_good = cut
            continue
        if last_good > start:
            spans.append((start, last_good))
            start = last_good
        while cut - start > cap:
            spans.append((start, start + cap))
            start += cap
        last_good = cut
    while len(text) - start > cap:
        spans.append((start, start + cap))
        start += cap
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def chunk_knowledge_doc(doc: dict[str, Any]) -> list[KnowledgeChunk]:
    """Split a pack knowledge doc into heading-delimited chunks.

    Oversized heading sections are split further at paragraph boundaries
    (cap ``MAX_CHUNK_CHARS``) so no chunk can exceed the embedding model's
    input limits. Chunk text is an exact slice of the body
    (``body[start:end] == text``) so a citation can always be traced back
    to its source span. The stored status is the EFFECTIVE status
    (frontmatter sample flag included).
    """
    body = str(doc.get("body_markdown", ""))
    if not body.strip():
        return []
    status = "sample" if doc_is_sample(doc) else str(doc.get("status", "draft"))
    boundaries: list[int] = [0]
    offset = 0
    for line in body.splitlines(keepends=True):
        if offset > 0 and _HEADING_RE.match(line):
            boundaries.append(offset)
        offset += len(line)
    boundaries.append(len(body))
    chunks: list[KnowledgeChunk] = []
    for seg_start, seg_end in zip(boundaries, boundaries[1:]):
        segment = body[seg_start:seg_end]
        for local_start, local_end in _split_spans(segment, MAX_CHUNK_CHARS):
            text = segment[local_start:local_end]
            if not text.strip():
                continue
            chunks.append(
                KnowledgeChunk(
                    content_id=str(doc.get("content_id", "")),
                    title=str(doc.get("title", "")),
                    section=doc.get("section"),
                    seq=len(chunks),
                    text=text,
                    char_start=seg_start + local_start,
                    char_end=seg_start + local_end,
                    status=status,
                )
            )
    return chunks


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class FakeEmbedder:
    """Deterministic hashing embedder for tests (lexical overlap only)."""

    model = "fake-embedder"

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vec[bucket] += 1.0
        return _normalize(vec)


class OpenAIEmbedder:
    """Embeddings via the OpenAI API (text-embedding-3-small by default).

    ``post`` is injectable for tests; the default uses httpx directly,
    matching the codebase's no-SDK provider style.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        post: Any = None,
        expected_dim: int = 1536,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self.model = model
        self._post = post
        self._expected_dim = expected_dim

    def embed(self, text: str) -> list[float]:
        from app.core.errors import ProviderError

        post = self._post
        if post is None:
            import httpx

            post = httpx.post
        try:
            resp = post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "input": text},
                timeout=30.0,
            )
        except Exception as exc:  # transport failure -> provider error
            raise ProviderError(f"embedding request failed: {exc}") from exc
        if getattr(resp, "status_code", 0) != 200:
            raise ProviderError(
                f"embedding request failed with status {getattr(resp, 'status_code', '?')}"
            )
        try:
            raw = resp.json()["data"][0]["embedding"]
            vec = [float(x) for x in raw]
        except Exception as exc:
            raise ProviderError(f"malformed embedding response: {exc}") from exc
        if len(vec) != self._expected_dim:
            raise ProviderError(
                f"embedding dimension {len(vec)} != expected {self._expected_dim} "
                f"for model {self._model}"
            )
        return vec


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def lexical_rank(query: str, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
    """Term-frequency ranking at chunk level (positive scores only)."""
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[int, KnowledgeChunk]] = []
    for chunk in chunks:
        haystack = f"{chunk.title}\n{chunk.text}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _score, chunk in scored]


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over chunk keys, best first."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def hybrid_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    embedder: Embedder,
    *,
    chunk_vectors: dict[str, list[float]] | None = None,
    limit: int | None = None,
) -> list[KnowledgeChunk]:
    """Fuse lexical and vector rankings; positive evidence only.

    ``chunk_vectors`` carries stored embeddings in production; when it is
    None the chunk texts are embedded on the fly (tests, tiny corpora).
    """
    by_key = {chunk.key: chunk for chunk in chunks}
    lexical = lexical_rank(query, chunks)
    query_vec = embedder.embed(query)
    cosine_by_key: dict[str, float] = {}
    vector_scored: list[tuple[float, KnowledgeChunk]] = []
    for chunk in chunks:
        vec = (chunk_vectors or {}).get(chunk.key)
        if vec is None:
            vec = embedder.embed(chunk.text)
        sim = _cosine(query_vec, vec)
        if sim >= VECTOR_MIN_COSINE:
            cosine_by_key[chunk.key] = sim
            vector_scored.append((sim, chunk))
    vector_scored.sort(key=lambda item: item[0], reverse=True)
    vector_ranking = [chunk.key for _sim, chunk in vector_scored]

    fused = rrf_fuse([[c.key for c in lexical], vector_ranking])
    final: list[tuple[float, KnowledgeChunk]] = []
    for key, score in fused:
        bonus = VECTOR_BONUS * cosine_by_key.get(key, 0.0)
        final.append((score + bonus, by_key[key]))
    final.sort(key=lambda item: item[0], reverse=True)
    ranked = [chunk for _score, chunk in final]
    return ranked[:limit] if limit is not None else ranked
