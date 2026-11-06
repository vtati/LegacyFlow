"""Knowledge store: sync planning, chunk loading, OpenAI embedder, ranking.

DB access is faked with the same cursor protocol the repositories use
(``with conn.cursor() as cur`` + dict rows), so these tests pin behavior
without a live database. Live-DB verification of the migration and the
vector round-trip is recorded in the RAG program evidence.
"""

from __future__ import annotations

import contextlib

import pytest

from app.core.errors import ProviderError
from app.domains.ai.knowledge_store import (
    chunk_fingerprint,
    load_chunks,
    plan_sync,
    rank_documents,
)
from app.domains.ai.retrieval import FakeEmbedder, KnowledgeChunk, OpenAIEmbedder

MODEL = "text-embedding-3-small"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self._rows: list[dict] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.executed.append((sql, params))
        if sql.strip().upper().startswith("SELECT"):
            self._rows = self.conn.select_rows
        else:
            self._rows = []

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, select_rows: list[dict] | None = None) -> None:
        self.select_rows = select_rows or []
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @contextlib.contextmanager
    def transaction(self):
        yield self


def _chunk(content_id: str, seq: int, text: str, status: str = "approved") -> KnowledgeChunk:
    return KnowledgeChunk(
        content_id=content_id,
        title=content_id,
        section=None,
        seq=seq,
        text=text,
        char_start=0,
        char_end=len(text),
        status=status,
    )


# ---------------------------------------------------------------------------
# plan_sync
# ---------------------------------------------------------------------------


def test_plan_sync_upserts_new_and_changed_only():
    desired = [
        _chunk("doc-a", 0, "unchanged text"),
        _chunk("doc-a", 1, "edited text"),
        _chunk("doc-b", 0, "brand new"),
    ]
    existing = {
        ("doc-a", 0): chunk_fingerprint(desired[0], MODEL),
        ("doc-a", 1): chunk_fingerprint(_chunk("doc-a", 1, "original text"), MODEL),
    }
    upserts, deletes = plan_sync(desired, existing, embed_model=MODEL)
    assert [(c.content_id, c.seq) for c in upserts] == [("doc-a", 1), ("doc-b", 0)]
    assert deletes == []


def test_plan_sync_deletes_stale_chunks():
    desired = [_chunk("doc-a", 0, "kept")]
    existing = {
        ("doc-a", 0): chunk_fingerprint(desired[0], MODEL),
        ("doc-old", 0): chunk_fingerprint(_chunk("doc-old", 0, "gone"), MODEL),
    }
    upserts, deletes = plan_sync(desired, existing, embed_model=MODEL)
    assert upserts == []
    assert deletes == [("doc-old", 0)]


def test_plan_sync_status_flip_reindexes_even_with_same_text():
    chunk = _chunk("doc-a", 0, "same text", status="approved")
    existing = {("doc-a", 0): chunk_fingerprint(chunk, MODEL)}
    flipped = _chunk("doc-a", 0, "same text", status="sample")
    upserts, _ = plan_sync([flipped], existing, embed_model=MODEL)
    assert upserts == [flipped]


def test_plan_sync_model_change_reindexes():
    chunk = _chunk("doc-a", 0, "same text")
    existing = {("doc-a", 0): chunk_fingerprint(chunk, "an-older-model")}
    upserts, _ = plan_sync([chunk], existing, embed_model=MODEL)
    assert upserts == [chunk]


# ---------------------------------------------------------------------------
# load_chunks
# ---------------------------------------------------------------------------


def _row(content_id: str, seq: int, text: str, status: str, embedding,
         model: str = MODEL) -> dict:
    return {
        "content_id": content_id,
        "title": content_id,
        "section": None,
        "seq": seq,
        "chunk_text": text,
        "status": status,
        "embedding": embedding,
        "embedding_model": model,
        "char_start": 0,
        "char_end": len(text),
    }


def test_load_chunks_parses_string_embeddings_and_filters_sample():
    conn = FakeConn(
        select_rows=[
            _row("doc-a", 0, "approved text", "approved", "[0.1, 0.2, 0.3]"),
            _row("doc-b", 0, "sample text", "sample", [0.4, 0.5, 0.6]),
        ]
    )
    chunks, vectors, models = load_chunks(conn, "us-va", "2.0.0", allow_sample=False)
    assert [c.content_id for c in chunks] == ["doc-a"]
    assert vectors["doc-a#0"] == [0.1, 0.2, 0.3]
    assert models["doc-a#0"] == MODEL

    chunks2, _, _ = load_chunks(conn, "us-va", "2.0.0", allow_sample=True)
    assert {c.content_id for c in chunks2} == {"doc-a", "doc-b"}


# ---------------------------------------------------------------------------
# OpenAIEmbedder
# ---------------------------------------------------------------------------


def test_openai_embedder_posts_and_parses():
    calls: list[dict] = []

    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2]}]}

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return Resp()

    emb = OpenAIEmbedder(api_key="sk-test", post=fake_post, expected_dim=2)
    assert emb.embed("hello") == [0.1, 0.2]
    assert calls[0]["url"].endswith("/v1/embeddings")
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert calls[0]["json"]["model"] == "text-embedding-3-small"
    assert calls[0]["json"]["input"] == "hello"


def test_openai_embedder_raises_provider_error_on_failure():
    class Resp:
        status_code = 500
        text = "boom"

    emb = OpenAIEmbedder(api_key="sk-test", post=lambda *a, **k: Resp())
    with pytest.raises(ProviderError):
        emb.embed("hello")


def test_openai_embedder_rejects_malformed_payload_and_wrong_dimension():
    class BadShape:
        status_code = 200

        def json(self):
            return {"unexpected": True}

    emb = OpenAIEmbedder(api_key="sk-test", post=lambda *a, **k: BadShape())
    with pytest.raises(ProviderError):
        emb.embed("hello")

    class WrongDim:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    emb = OpenAIEmbedder(api_key="sk-test", post=lambda *a, **k: WrongDim())
    with pytest.raises(ProviderError):
        emb.embed("hello")


# ---------------------------------------------------------------------------
# rank_documents: chunk ranking grouped back to documents
# ---------------------------------------------------------------------------


class _Pack:
    def __init__(self, docs: list[dict]) -> None:
        self.knowledge_docs = docs
        self.manifest = type("M", (), {"pack_id": "us-va"})()


def test_rank_documents_groups_chunks_to_docs_best_first():
    emb = FakeEmbedder(dim=64)
    docs = [
        {"content_id": "will-basics", "title": "Will Basics", "section": "Wills",
         "status": "approved", "body_markdown": "A will must be signed."},
        {"content_id": "guardian-basics", "title": "Choosing Guardians", "section": "Backups",
         "status": "approved", "body_markdown": "Naming a guardian for your children."},
    ]
    rows = []
    for doc in docs:
        vec = emb.embed(f"{doc['title']}\n{doc['body_markdown']}")
        rows.append(
            _row(doc["content_id"], 0, doc["body_markdown"], "approved",
                 "[" + ", ".join(str(x) for x in vec) + "]")
        )
    conn = FakeConn(select_rows=rows)
    ranked = rank_documents(
        conn,
        pack=_Pack(docs),
        pack_version="2.0.0",
        question="who takes care of my children guardian",
        embedder=emb,
        embed_model=MODEL,
        allow_sample=False,
    )
    assert ranked[0]["content_id"] == "guardian-basics"


def test_rank_documents_empty_when_no_chunks_indexed():
    conn = FakeConn(select_rows=[])
    ranked = rank_documents(
        conn,
        pack=_Pack([]),
        pack_version="2.0.0",
        question="anything at all",
        embedder=FakeEmbedder(),
        embed_model=MODEL,
        allow_sample=False,
    )
    assert ranked == []


def test_rank_documents_ignores_chunks_from_another_embedding_model():
    emb = FakeEmbedder(dim=64)
    vec = emb.embed("Choosing Guardians\nNaming a guardian for your children.")
    rows = [
        _row("guardian-basics", 0, "Naming a guardian for your children.",
             "approved", "[" + ", ".join(str(x) for x in vec) + "]",
             model="an-older-model")
    ]
    docs = [
        {"content_id": "guardian-basics", "title": "Choosing Guardians",
         "section": "Backups", "status": "approved",
         "body_markdown": "Naming a guardian for your children."}
    ]
    ranked = rank_documents(
        FakeConn(select_rows=rows),
        pack=_Pack(docs),
        pack_version="2.0.0",
        question="guardian children",
        embedder=emb,
        embed_model=MODEL,
        allow_sample=False,
    )
    assert ranked == []


def test_rank_documents_returns_matched_chunk_text_not_doc_opening():
    emb = FakeEmbedder(dim=64)
    passage = "The backup guardian passage: name a successor guardian too."
    vec = emb.embed(f"Long Guide\n{passage}")
    rows = [_row("long-guide", 3, passage, "approved",
                 "[" + ", ".join(str(x) for x in vec) + "]")]
    long_body = ("filler opening text. " * 60) + "\n\n" + passage
    docs = [
        {"content_id": "long-guide", "title": "Long Guide", "section": "Guides",
         "status": "approved", "body_markdown": long_body}
    ]
    ranked = rank_documents(
        FakeConn(select_rows=rows),
        pack=_Pack(docs),
        pack_version="2.0.0",
        question="backup guardian successor guardian",
        embedder=emb,
        embed_model=MODEL,
        allow_sample=False,
    )
    assert ranked[0]["body_markdown"] == passage


def test_rank_documents_blocks_frontmatter_sample_doc():
    emb = FakeEmbedder(dim=64)
    vec = emb.embed("Sample Doc\nsecret sample guardian text")
    rows = [_row("sample-doc", 0, "secret sample guardian text", "approved",
                 "[" + ", ".join(str(x) for x in vec) + "]")]
    docs = [
        {"content_id": "sample-doc", "title": "Sample Doc", "section": None,
         "status": "approved", "frontmatter": {"sample": True},
         "body_markdown": "secret sample guardian text"}
    ]
    ranked = rank_documents(
        FakeConn(select_rows=rows),
        pack=_Pack(docs),
        pack_version="2.0.0",
        question="guardian",
        embedder=emb,
        embed_model=MODEL,
        allow_sample=False,
    )
    assert ranked == []


# ---------------------------------------------------------------------------
# service._retrieve_docs: mode selection + fallback
# ---------------------------------------------------------------------------


def _service_pack() -> _Pack:
    return _Pack(
        [
            {"content_id": "will-basics", "title": "Will Basics", "section": "Wills",
             "status": "approved",
             "body_markdown": "guardian guardian guardian will signing witnesses"},
            {"content_id": "guardian-basics", "title": "Choosing Guardians",
             "section": "Backups", "status": "approved",
             "body_markdown": "Naming a guardian for your children."},
        ]
    )


def _hybrid_settings(monkeypatch):
    from types import SimpleNamespace

    from app.domains.ai import service

    fake_settings = SimpleNamespace(
        AI_RETRIEVAL_MODE="hybrid",
        OPENAI_API_KEY="sk-test",
        AI_EMBEDDING_MODEL="text-embedding-3-small",
    )
    monkeypatch.setattr(service, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        service, "OpenAIEmbedder", lambda key, model=None: FakeEmbedder(dim=64)
    )
    return service


def test_retrieve_docs_falls_back_to_lexical_when_unindexed(monkeypatch):
    service = _hybrid_settings(monkeypatch)
    docs = service._retrieve_docs(
        FakeConn(select_rows=[]), "guardian", _service_pack(), "2.0.0",
        allow_sample=False,
    )
    # Lexical term-frequency ranks will-basics first (guardian x3 + title).
    assert docs[0]["content_id"] == "will-basics"


def test_retrieve_docs_uses_hybrid_result_when_indexed(monkeypatch):
    service = _hybrid_settings(monkeypatch)
    emb = FakeEmbedder(dim=64)
    vec = emb.embed("Choosing Guardians\nNaming a guardian for your children.")
    rows = [
        _row("guardian-basics", 0, "Naming a guardian for your children.",
             "approved", "[" + ", ".join(str(x) for x in vec) + "]")
    ]
    docs = service._retrieve_docs(
        FakeConn(select_rows=rows), "guardian", _service_pack(), "2.0.0",
        allow_sample=False,
    )
    # Hybrid winner first; the unindexed lexical winner is merged in
    # after it — a partial index must never suppress lexical results.
    assert [d["content_id"] for d in docs] == ["guardian-basics", "will-basics"]
