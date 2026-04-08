#!/usr/bin/env bash
# sync.sh - Deploy last30days skill to all host locations
# Usage: bash scripts/sync.sh  (run from repo root)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
echo "Source: $SRC"

COMMON_TARGETS=(
  # Claude Code plugin cache: marketplace installs overwrite on update,
  # but local development needs the cache kept in sync with the repo.
  # Do NOT add ~/.claude/skills/last30days - it creates a duplicate
  # /last30days-3 in the slash command menu alongside the plugin version.
  "$HOME/.claude/plugins/cache/last30days-skill-private/last30days-3/3.0.0-alpha"
  "$HOME/.claude/plugins/cache/last30days-skill-private/last30days-3-nogem/3.0.0-nogem"
  "$HOME/.agents/skills/last30days"
  "$HOME/.codex/skills/last30days"
)
OPENCLAW_TARGET="$HOME/.openclaw/skills/last30days"

sync_target() {
  local target="$1"
  local skill_md="$2"

  echo ""
  echo "--- Syncing to $target ---"
  mkdir -p "$target/scripts/lib" "$target/variants/open/references"

  cp "$skill_md" "$target/SKILL.md"

  rsync -a \
    "$SRC/scripts/last30days.py" \
    "$SRC/scripts/watchlist.py" \
    "$SRC/scripts/briefing.py" \
    "$SRC/scripts/store.py" \
    "$target/scripts/"
  rsync -a "$SRC/scripts/lib/"*.py "$target/scripts/lib/"
  rsync -a "$SRC/variants/open/" "$target/variants/open/"

  if [ -d "$SRC/scripts/lib/vendor" ]; then
    rsync -a "$SRC/scripts/lib/vendor" "$target/scripts/lib/"
  fi

  if [ -d "$SRC/fixtures" ]; then
    mkdir -p "$target/fixtures"
    rsync -a "$SRC/fixtures/" "$target/fixtures/"
  fi

  mod_count=$(ls "$target/scripts/lib/"*.py 2>/dev/null | wc -l | tr -d ' ')
  echo "  Copied $mod_count modules"

  if (
    cd "$target/scripts" &&
    python3 -c "import briefing, store, watchlist; from lib import youtube_yt, bird_x, render, ui; print('  Import check: OK')"
  ); then
    true
  else
    echo "  Import check FAILED"
  fi
}

for t in "${COMMON_TARGETS[@]}"; do
  sync_target "$t" "$SRC/SKILL.md"
done

sync_target "$OPENCLAW_TARGET" "$SRC/variants/open/SKILL.md"

echo ""
echo "Sync complete."
