#!/usr/bin/env bash
# sync.sh - Deploy last30days skill to all host locations
# Usage: bash scripts/sync.sh  (run from repo root)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
echo "Source: $SRC"

TARGETS=(
  "$HOME/.claude/skills/last30days"
  "$HOME/.agents/skills/last30days"
  "$HOME/.codex/skills/last30days"
)

for t in "${TARGETS[@]}"; do
  echo ""
  echo "--- Syncing to $t ---"
  mkdir -p "$t/scripts/lib"

  cp "$SRC/SKILL.md" "$t/"

  # Main script + lib modules (rsync handles identical files gracefully)
  rsync -a "$SRC/scripts/last30days.py" "$t/scripts/"
  rsync -a "$SRC/scripts/lib/"*.py "$t/scripts/lib/"

  # Vendor directory (bird-search CLI)
  if [ -d "$SRC/scripts/lib/vendor" ]; then
    rsync -a "$SRC/scripts/lib/vendor" "$t/scripts/lib/"
  fi

  # Fixtures
  if [ -d "$SRC/fixtures" ]; then
    mkdir -p "$t/fixtures"
    rsync -a "$SRC/fixtures/" "$t/fixtures/"
  fi

  # Count and report
  mod_count=$(ls "$t/scripts/lib/"*.py 2>/dev/null | wc -l | tr -d ' ')
  echo "  Copied $mod_count modules"

  # Verify imports (all public modules)
  if (cd "$t/scripts" && python3 -c "
from lib import (
    bird_x, bluesky, brave_search, cache, dates, dedupe,
    entity_extract, env, hackernews, http, instagram, models,
    normalize, openai_reddit, openrouter_search, parallel_search,
    polymarket, query, query_type, reddit, reddit_enrich,
    relevance, render, schema, score, scrapecreators_x, tiktok,
    truthsocial, ui, websearch, xai_x, xiaohongshu_api, youtube_yt,
)
print('  Import check: OK')
" 2>&1); then
    true
  else
    echo "  Import check FAILED"
  fi
done

echo ""
echo "Sync complete."
