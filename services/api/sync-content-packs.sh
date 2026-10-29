#!/bin/sh
# Re-vendor content-packs/ from the repo root into services/api/content-packs/.
# The Railway API service builds with root directory = services/api, so the
# repo-root content-packs/ is outside the image build context. The vendored
# copy is what the deployed pack loader reads. Re-run after any pack change.
#
# NON-DESTRUCTIVE (FUNC-005): this script never deletes files. If a file
# exists in the vendored tree but not in the repo-root tree, syncing would
# orphan production content — the script aborts and lists those files so a
# human can copy them UP to the root first (production truth wins).
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/content-packs"
DST="$ROOT/services/api/content-packs"

if [ ! -d "$SRC" ]; then
  echo "sync-content-packs: source $SRC not found" >&2
  exit 1
fi
mkdir -p "$DST"

# Guard: refuse if any vendored file is absent from the root tree.
missing=0
if [ -d "$DST" ]; then
  for f in $(cd "$DST" && find . -type f | sort); do
    if [ ! -f "$SRC/$f" ]; then
      echo "sync-content-packs: vendored-only file would be orphaned: services/api/content-packs/$f" >&2
      missing=1
    fi
  done
fi
if [ "$missing" -ne 0 ]; then
  echo "sync-content-packs: ABORTED - copy the files above UP to content-packs/ first; no files were changed." >&2
  exit 1
fi

cp -r "$SRC/." "$DST/"
echo "vendored $(find "$DST" -type f | wc -l) files"
