"""Persistence for Ask knowledge chunks (pgvector table knowledge_chunks).

Sync is two-phase so the (per-pack, transaction-scoped) advisory lock is
never held across embedding HTTP calls:

1. Read stored fingerprints, diff against the desired chunks, and embed
   every new/changed chunk — no lock held.
2. Inside one transaction: take ``pg_try_advisory_xact_lock`` for this
   pack, re-diff (normally a no-op), and apply upserts/deletes. The lock
   dies with the transaction, so a failed sync can never leak it.

The sync fingerprint covers title, section, effective status, embedding
model, and text — a status flip or a model change re-indexes even when
the body text is unchanged. Ranking runs in-process over the pack's
chunks (hundreds at most) so the exact code path covered by unit tests
is the one production executes; rows written by a different embedding
model are ignored until re-synced. Repository style follows the
codebase: functions take an open connection, services own transactions.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.domains.ai.retrieval import (
    Embedder,
    KnowledgeChunk,
    chunk_knowledge_doc,
    doc_is_sample,
    hybrid_rank,
    text_hash,
)

logger = logging.getLogger(__name__)


def chunk_fingerprint(chunk: KnowledgeChunk, embed_model: str) -> str:
    """Canonical sync fingerprint: content + metadata + embedding model."""
    material = "\n".join(
        [
            chunk.title,
            chunk.section or "",
            chunk.status,
            embed_model,
            chunk.text,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def plan_sync(
    desired: list[KnowledgeChunk],
    existing: dict[tuple[str, int], str],
    *,
    embed_model: str,
) -> tuple[list[KnowledgeChunk], list[tuple[str, int]]]:
    """Diff desired chunks against stored (content_id, seq) -> fingerprint."""
    desired_keys = {(c.content_id, c.seq) for c in desired}
    upserts = [
        c
        for c in desired
        if existing.get((c.content_id, c.seq)) != chunk_fingerprint(c, embed_model)
    ]
    deletes = [key for key in existing if key not in desired_keys]
    return upserts, deletes


def _format_vector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _parse_embedding(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    text = str(value).strip().strip("[]")
    if not text:
        return []
    return [float(x) for x in text.split(",")]


def _read_fingerprints(
    conn: Any, pack_id: str, pack_version: str
) -> dict[tuple[str, int], str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_id, seq, fingerprint FROM knowledge_chunks "
            "WHERE pack_id = %s AND pack_version = %s",
            (pack_id, pack_version),
        )
        return {
            (str(r["content_id"]), int(r["seq"])): str(r["fingerprint"])
            for r in cur.fetchall()
        }


def _embed_text(embedder: Embedder, chunk: KnowledgeChunk) -> str:
    return f"{chunk.title}\n{chunk.text}"


def sync_pack(
    conn: Any,
    *,
    pack_id: str,
    pack_version: str,
    docs: list[dict[str, Any]],
    embedder: Embedder,
    embed_model: str,
) -> dict[str, int]:
    """Chunk + embed a pack's knowledge docs into knowledge_chunks."""
    desired: list[KnowledgeChunk] = []
    for doc in docs:
        desired.extend(chunk_knowledge_doc(doc))

    # Phase 1 (no lock): diff + embed.
    existing = _read_fingerprints(conn, pack_id, pack_version)
    upserts, _deletes = plan_sync(desired, existing, embed_model=embed_model)
    vectors: dict[str, list[float]] = {
        chunk.key: embedder.embed(_embed_text(embedder, chunk)) for chunk in upserts
    }

    # Phase 2: per-pack transaction lock, re-diff, apply.
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"knowledge-sync:{pack_id}:{pack_version}",),
            )
            row = cur.fetchone()
            if not row or not row.get("locked"):
                return {
                    "upserted": 0,
                    "deleted": 0,
                    "total": len(desired),
                    "skipped": 1,
                }
            existing_now = _read_fingerprints(conn, pack_id, pack_version)
            upserts_now, deletes_now = plan_sync(
                desired, existing_now, embed_model=embed_model
            )
            for chunk in upserts_now:
                vec = vectors.get(chunk.key)
                if vec is None:  # became stale between phases (rare)
                    vec = embedder.embed(_embed_text(embedder, chunk))
                cur.execute(
                    """
                    INSERT INTO knowledge_chunks
                      (pack_id, pack_version, content_id, section, seq, title,
                       chunk_text, text_hash, status, embedding,
                       embedding_model, char_start, char_end, fingerprint)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector,
                            %s, %s, %s, %s)
                    ON CONFLICT (pack_id, pack_version, content_id, seq)
                    DO UPDATE SET section = EXCLUDED.section,
                      title = EXCLUDED.title, chunk_text = EXCLUDED.chunk_text,
                      text_hash = EXCLUDED.text_hash, status = EXCLUDED.status,
                      embedding = EXCLUDED.embedding,
                      embedding_model = EXCLUDED.embedding_model,
                      char_start = EXCLUDED.char_start,
                      char_end = EXCLUDED.char_end,
                      fingerprint = EXCLUDED.fingerprint, updated_at = now()
                    """,
                    (
                        pack_id,
                        pack_version,
                        chunk.content_id,
                        chunk.section,
                        chunk.seq,
                        chunk.title,
                        chunk.text,
                        text_hash(chunk.text),
                        chunk.status,
                        _format_vector(vec),
                        embed_model,
                        chunk.char_start,
                        chunk.char_end,
                        chunk_fingerprint(chunk, embed_model),
                    ),
                )
            for content_id, seq in deletes_now:
                cur.execute(
                    "DELETE FROM knowledge_chunks WHERE pack_id = %s AND "
                    "pack_version = %s AND content_id = %s AND seq = %s",
                    (pack_id, pack_version, content_id, seq),
                )
    return {
        "upserted": len(upserts_now),
        "deleted": len(deletes_now),
        "total": len(desired),
    }


def load_chunks(
    conn: Any,
    pack_id: str,
    pack_version: str,
    *,
    allow_sample: bool,
) -> tuple[list[KnowledgeChunk], dict[str, list[float]], dict[str, str | None]]:
    """Load a pack's indexed chunks, their embeddings, and their models."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_id, title, section, seq, chunk_text, status, "
            "embedding, embedding_model, char_start, char_end "
            "FROM knowledge_chunks WHERE pack_id = %s AND pack_version = %s "
            "ORDER BY content_id, seq",
            (pack_id, pack_version),
        )
        rows = cur.fetchall()
    chunks: list[KnowledgeChunk] = []
    vectors: dict[str, list[float]] = {}
    models: dict[str, str | None] = {}
    for row in rows:
        status = str(row["status"])
        if not allow_sample and status.lower() == "sample":
            continue
        text = str(row["chunk_text"])
        chunk = KnowledgeChunk(
            content_id=str(row["content_id"]),
            title=str(row["title"]),
            section=row["section"],
            seq=int(row["seq"]),
            text=text,
            char_start=int(row["char_start"]) if row.get("char_start") is not None else 0,
            char_end=int(row["char_end"])
            if row.get("char_end") is not None
            else len(text),
            status=status,
        )
        chunks.append(chunk)
        vec = _parse_embedding(row["embedding"])
        if vec:
            vectors[chunk.key] = vec
        models[chunk.key] = row.get("embedding_model")
    return chunks, vectors, models


def rank_documents(
    conn: Any,
    *,
    pack: Any,
    pack_version: str,
    question: str,
    embedder: Embedder,
    embed_model: str,
    allow_sample: bool,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Hybrid-rank indexed chunks and group them back to their documents.

    Returns up to ``limit`` document dicts in best-first order, or [] when
    nothing is indexed or no chunk carries positive evidence. Each
    returned doc is a shallow copy whose ``body_markdown`` is the matched
    chunks' text (best chunks first) so the provider's excerpt — and the
    citation built from it — contains the passage that actually matched,
    not just the document's opening lines. The current pack document's
    effective sample status is re-checked here as a second gate.
    """
    pack_id = str(pack.manifest.pack_id)
    chunks, vectors, models = load_chunks(
        conn, pack_id, pack_version, allow_sample=allow_sample
    )
    chunks = [c for c in chunks if models.get(c.key) == embed_model]
    if not chunks:
        return []
    ranked_chunks = hybrid_rank(question, chunks, embedder, chunk_vectors=vectors)
    if not ranked_chunks:
        return []
    docs_by_id = {str(d.get("content_id")): d for d in pack.knowledge_docs}
    chunks_by_doc: dict[str, list[KnowledgeChunk]] = {}
    for chunk in ranked_chunks:
        chunks_by_doc.setdefault(chunk.content_id, []).append(chunk)
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in ranked_chunks:
        if chunk.content_id in seen:
            continue
        seen.add(chunk.content_id)
        doc = docs_by_id.get(chunk.content_id)
        if doc is None:
            continue
        if not allow_sample and doc_is_sample(doc):
            continue
        top_chunks = chunks_by_doc[chunk.content_id][:2]
        merged = dict(doc)
        merged["body_markdown"] = "\n\n".join(c.text for c in top_chunks)
        if top_chunks[0].section:
            merged["section"] = top_chunks[0].section
        ordered.append(merged)
        if len(ordered) >= limit:
            break
    return ordered
