#!/bin/bash
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
# Space-safe (Batch C1 M-7): NUL-delimited find output read with
# `read -d ''`, so filenames containing spaces are checked whole
# instead of being word-split into fragments that can false-pass.
# The list goes through a temp file with the loop's stdin redirected
# from it, so `missing` is set in THIS shell (a pipeline would fork
# the loop into a subshell and lose it).
missing=0
if [ -d "$DST" ]; then
  guard_list="$(mktemp)"
  trap 'rm -f "$guard_list"' EXIT
  (cd "$DST" && find . -type f -print0 | sort -z) > "$guard_list"
  while IFS= read -r -d '' f; do
    if [ ! -f "$SRC/$f" ]; then
      echo "sync-content-packs: vendored-only file would be orphaned: services/api/content-packs/$f" >&2
      missing=1
    fi
  done < "$guard_list"
  rm -f "$guard_list"
  trap - EXIT
fi
if [ "$missing" -ne 0 ]; then
  echo "sync-content-packs: ABORTED - copy the files above UP to content-packs/ first; no files were changed." >&2
  exit 1
fi

cp -r "$SRC/." "$DST/"
echo "vendored $(find "$DST" -type f | wc -l) files"
