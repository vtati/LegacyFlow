"""Guard against drift between the canonical content packs (repo root
``content-packs/``) and the vendored copy under ``services/api/content-packs/``.

The Railway API service builds with root directory ``services/api``, so the
repo-root packs are outside the image build context; the deployed pack loader
reads the vendored copy. After changing any pack, re-run
``services/api/sync-content-packs.sh``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_vendored_content_packs_in_sync() -> None:
    api_root = Path(__file__).resolve().parents[1]
    vendored = api_root / "content-packs"
    canonical = api_root.parent.parent / "content-packs"
    assert vendored.is_dir(), (
        "services/api/content-packs is missing; run services/api/sync-content-packs.sh"
    )
    if not canonical.is_dir():
        # Editable installs / sdist layouts without the repo root: nothing to compare.
        return
    assert _hash_tree(vendored) == _hash_tree(canonical), (
        "services/api/content-packs has drifted from repo-root content-packs/; "
        "run services/api/sync-content-packs.sh and commit the result"
    )
