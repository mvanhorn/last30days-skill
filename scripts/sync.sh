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

  # Verify imports — dynamically checks every module in lib/
  if (cd "$t/scripts" && python3 - <<'PYEOF'
import importlib, pathlib, sys
failed = []
for p in sorted(pathlib.Path("lib").glob("*.py")):
    if p.stem == "__init__":
        continue
    try:
        importlib.import_module(f"lib.{p.stem}")
    except Exception as e:
        failed.append(f"{p.stem}: {e}")
if failed:
    print("  Import check FAILED:")
    for f in failed:
        print(f"    {f}")
    sys.exit(1)
else:
    print(f"  Import check: OK ({len(list(pathlib.Path('lib').glob('*.py'))) - 1} modules)")
PYEOF
  ); then
    true
  else
    echo "  Import check FAILED"
  fi
done

echo ""
echo "Sync complete."
