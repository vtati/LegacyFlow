"""Filesystem path helpers for the API service.

The versioned jurisdiction content packs live at the repo root under
``content-packs/``. In deployed containers the service may run from a
different directory layout (e.g. Railway builds ``services/api`` as the
app root, so ``content-packs/`` is not on disk at all). These helpers
locate the packs without assuming a fixed directory depth, and return
``None`` when the packs are absent so callers can fall back to their
built-in starter data instead of crashing.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_content_packs_dir(start: Path | None = None) -> Path | None:
    """Return the ``content-packs/`` directory, or ``None`` if not on disk.

    Resolution order:
    1. ``CONTENT_PACKS_DIR`` env var, when it points at an existing directory.
    2. Walk up from ``start`` (defaults to this file) looking for a
       ``content-packs/`` child directory.
    """
    override = os.environ.get("CONTENT_PACKS_DIR")
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            return candidate

    here = Path(start).resolve() if start is not None else Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "content-packs"
        if candidate.is_dir():
            return candidate
    return None
