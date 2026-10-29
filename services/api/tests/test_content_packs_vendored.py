"""Guard against drift between the canonical content packs (repo root
``content-packs/``) and the vendored copy under ``services/api/content-packs/``.

The Railway API service builds with root directory ``services/api``, so the
repo-root packs are outside the image build context; the deployed pack loader
reads the vendored copy. After changing any pack, re-run
``services/api/sync-content-packs.sh``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
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


def test_sync_guard_aborts_on_vendored_only_file_with_spaces(tmp_path: Path) -> None:
    """Batch C1 M-7: the vendored-only guard must be space-safe.

    Discriminating case: the vendored tree holds ``orphan file.txt``
    while the root tree holds the two files the OLD word-splitting loop
    would have checked instead (``orphan`` and ``file.txt``). The old
    guard therefore passed and silently kept the orphan; the new
    ``find -print0`` guard must abort and name the real file.
    """
    api_dir = tmp_path / "services" / "api"
    api_dir.mkdir(parents=True)
    script_src = Path(__file__).resolve().parents[1] / "sync-content-packs.sh"
    script = api_dir / "sync-content-packs.sh"
    shutil.copy(script_src, script)

    src = tmp_path / "content-packs"
    src.mkdir()
    (src / "orphan").write_text("fragment one")
    (src / "file.txt").write_text("fragment two")

    dst = api_dir / "content-packs"
    dst.mkdir()
    (dst / "orphan file.txt").write_text("vendored only")

    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "orphan file.txt" in result.stderr
    assert "ABORTED" in result.stderr
    # Nothing was synced over the orphan.
    assert (dst / "orphan file.txt").read_text() == "vendored only"
    assert not (dst / "orphan").exists()
