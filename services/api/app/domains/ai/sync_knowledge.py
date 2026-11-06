"""Index pack knowledge documents into knowledge_chunks.

Usage (server-side, where the production OpenAI key lives):

    python -m app.domains.ai.sync_knowledge US-VA 2.0.0

Idempotent: unchanged chunks are not re-embedded (fingerprint diff).
Also runs automatically at API startup (see sync_all_packs).
"""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

from app.core.config import get_settings
from app.core.db import get_connection
from app.domains.ai.knowledge_store import sync_pack
from app.domains.ai.retrieval import OpenAIEmbedder
from app.domains.legal_content.pack_loader import get_pack, list_packs

logger = logging.getLogger(__name__)


def _sync_one(manifest, embedder, embed_model: str) -> None:
    pack = get_pack(manifest.jurisdiction_code, manifest.semantic_version)
    if not pack.knowledge_docs:
        return
    with get_connection() as conn:
        result = sync_pack(
            conn,
            pack_id=str(pack.manifest.pack_id),
            pack_version=manifest.semantic_version,
            docs=pack.knowledge_docs,
            embedder=embedder,
            embed_model=embed_model,
        )
    logger.info(
        "knowledge sync %s %s: %s",
        pack.manifest.pack_id,
        manifest.semantic_version,
        result,
    )


def sync_all_packs() -> None:
    """Index every pack version on disk. Fail-open: never raises.

    Runs at API startup in a background thread so the knowledge_chunks
    index exists before AI_RETRIEVAL_MODE=hybrid is enabled. Fingerprint
    diffing makes restarts cheap (one SELECT per pack when unchanged).
    """
    try:
        settings = get_settings()
        if not settings.OPENAI_API_KEY:
            return
        embedder = OpenAIEmbedder(
            settings.OPENAI_API_KEY, model=settings.AI_EMBEDDING_MODEL
        )
        for manifest in list_packs():
            try:
                _sync_one(manifest, embedder, settings.AI_EMBEDDING_MODEL)
            except Exception:
                logger.exception(
                    "knowledge sync failed for pack %s", getattr(manifest, "pack_id", "?")
                )
    except Exception:
        logger.exception("knowledge autosync failed")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m app.domains.ai.sync_knowledge <JURISDICTION> <VERSION>")
        return 2
    jurisdiction, version = argv
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY is not configured; cannot embed.")
        return 1
    embedder = OpenAIEmbedder(settings.OPENAI_API_KEY, model=settings.AI_EMBEDDING_MODEL)
    manifest = SimpleNamespace(
        jurisdiction_code=jurisdiction, semantic_version=version
    )
    _sync_one(manifest, embedder, settings.AI_EMBEDDING_MODEL)
    print(f"synced {jurisdiction} {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
