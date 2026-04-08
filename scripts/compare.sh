#!/bin/bash
# A/B/C test runner for last30days skill variants
# Usage: bash scripts/compare.sh "Kanye West"
#
# Runs all 3 skills sequentially (30s gap for rate limits),
# saves raw results with unique suffixes, then prints file paths
# for comparison.

set -e

# Join all args as the topic (so "bash compare.sh Kevin Rose" works without quotes)
if [ $# -eq 0 ]; then
  echo "Usage: bash scripts/compare.sh <topic>"
  echo "  Example: bash scripts/compare.sh Kevin Rose"
  exit 1
fi
TOPIC="$*"
SLUG=$(echo "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')
DIR="$HOME/Documents/Last30Days"
DATE=$(date +%Y-%m-%d)

echo "=============================================="
echo " A/B/C Test: $TOPIC"
echo " Date: $DATE"
echo "=============================================="
echo ""

# Run 1: v2.9 production
echo "[1/3] Running v2.9 (production /last30days)..."
echo "  This takes 2-4 minutes..."
claude -p --dangerously-skip-permissions "/last30days $TOPIC" > /dev/null 2>&1 || true
V2_FILE="$DIR/${SLUG}-raw.md"
[ -f "$V2_FILE" ] && echo "  ✓ Done → $V2_FILE" || echo "  ✗ FAILED — no output file"
echo ""

echo "  Waiting 30s for API rate limits..."
sleep 30

# Run 2: v3 Gemini
echo "[2/3] Running v3 (/last30days-3)..."
echo "  This takes 2-4 minutes..."
claude -p --dangerously-skip-permissions "/last30days-3:last30days-skill-private $TOPIC" > /dev/null 2>&1 || true
V3GEM_FILE="$DIR/${SLUG}-raw-v3.md"
[ -f "$V3GEM_FILE" ] && echo "  ✓ Done → $V3GEM_FILE" || echo "  ✗ FAILED — no output file"
echo ""

echo ""

echo "=============================================="
echo " Both complete. Raw files:"
echo "=============================================="
echo ""
ls -la "$DIR/${SLUG}-raw"*.md 2>/dev/null || echo "  (no files found — check if skills saved correctly)"
echo ""
echo "To compare, run in Claude Code:"
echo "  Read and compare these raw research files, produce a detailed report:"
echo "  $DIR/${SLUG}-raw.md"
echo "  $DIR/${SLUG}-raw-v3.md"
echo ""
