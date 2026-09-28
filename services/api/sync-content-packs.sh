#!/bin/sh
# Re-vendor content-packs/ from the repo root into services/api/content-packs/.
# The Railway API service builds with root directory = services/api, so the
# repo-root content-packs/ is outside the image build context. The vendored
# copy is what the deployed pack loader reads. Re-run after any pack change.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
rm -rf "$ROOT/services/api/content-packs"
mkdir -p "$ROOT/services/api/content-packs"
cp -r "$ROOT/content-packs/." "$ROOT/services/api/content-packs/"
echo "vendored $(find "$ROOT/services/api/content-packs" -type f | wc -l) files"
