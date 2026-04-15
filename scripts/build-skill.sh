#!/usr/bin/env bash
# build-skill.sh - package this repo as a claude.ai-upload-ready .skill file
# Usage: bash scripts/build-skill.sh  (run from repo root)
#
# Produces dist/last30days.skill, a zip with a single top-level `last30days/`
# directory containing SKILL.md and the scripts/ runtime. See
# docs/plans/2026-04-14-001-fix-skill-upload-200-file-limit-plan.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: working tree is dirty; commit or stash before building" >&2
  exit 1
fi

mkdir -p dist
OUT="dist/last30days.skill"
git archive --format=zip --prefix=last30days/ --output="$OUT" HEAD

# claude.ai's .skill bundle only needs the root SKILL.md + scripts/ runtime.
# Claude Code needs skills/ and .claude-plugin/ in the git archive
# (that's why they're NOT in .gitattributes export-ignore), but the .skill
# bundle must strip them to keep a single canonical SKILL.md and stay under
# the 200-file cap.
zip -d "$OUT" "last30days/skills/*" "last30days/.claude-plugin/*" > /dev/null 2>&1 || true

COUNT=$(unzip -l "$OUT" | tail -1 | awk '{print $2}')
SIZE=$(du -h "$OUT" | cut -f1)

if [ "$COUNT" -gt 200 ]; then
  echo "error: $COUNT files in zip, claude.ai's cap is 200" >&2
  echo "       check .gitattributes export-ignore entries and this script's zip -d excludes" >&2
  exit 1
fi

SKILL_MD_COUNT=$(unzip -l "$OUT" | grep -c "SKILL.md" || true)
if [ "$SKILL_MD_COUNT" -ne 1 ]; then
  echo "error: expected exactly one SKILL.md, found $SKILL_MD_COUNT" >&2
  exit 1
fi

echo "built $OUT ($COUNT files, $SIZE)"
echo "upload via the claude.ai skill UI"
