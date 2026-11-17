#!/usr/bin/env bash
# build-skill.sh - package this repo as a claude.ai-upload-ready .skill file
# Usage: bash skills/last30days/scripts/build-skill.sh  (run from repo root)
#
# Produces dist/last30days.skill, a zip with a single top-level `last30days/`
# directory containing SKILL.md and the scripts/ runtime from skills/last30days.
# See
# docs/plans/2026-04-14-001-fix-skill-upload-200-file-limit-plan.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: working tree is dirty; commit or stash before building" >&2
  exit 1
fi

mkdir -p dist
OUT="dist/last30days.skill"
STAGING=$(mktemp -d "dist/.last30days-skill.XXXXXXXX")
ARCHIVE="$STAGING/last30days.skill"
trap 'rm -f -- "$ARCHIVE"; rmdir -- "$STAGING"' EXIT
git archive --format=zip --prefix=last30days/ --output="$ARCHIVE" HEAD:skills/last30days \
  ':(exclude)assets' \
  ':(exclude)scripts/compare.sh' \
  ':(exclude)scripts/verify_v3.py' \
  ':(exclude,glob)scripts/test[-_]*'

ENTRIES=$(unzip -Z1 "$ARCHIVE")
COUNT=$(awk '!/\/$/ { count++ } END { print count + 0 }' <<<"$ENTRIES")

if [ "$COUNT" -gt 200 ]; then
  echo "error: $COUNT files in zip, claude.ai's cap is 200" >&2
  exit 1
fi

SKILL_MD_COUNT=$(awk '/(^|\/)SKILL\.md$/ { count++ } END { print count + 0 }' <<<"$ENTRIES")
if [ "$SKILL_MD_COUNT" -ne 1 ] || ! grep -Fxq 'last30days/SKILL.md' <<<"$ENTRIES"; then
  echo "error: expected exactly one SKILL.md at last30days/SKILL.md" >&2
  exit 1
fi

SIZE=$(du -h "$ARCHIVE" | cut -f1)
python3 -I - "$ARCHIVE" "$OUT" <<'PY'
import os
import sys

source, output = sys.argv[1:]
if os.path.isdir(output):
    sys.exit(f"error: cannot replace {output}: destination is a directory")
try:
    os.replace(source, output)
except OSError as exc:
    sys.exit(f"error: cannot replace {output}: {exc.strerror}")
PY
echo "built $OUT ($COUNT files, $SIZE)"
echo "upload via the claude.ai skill UI"
