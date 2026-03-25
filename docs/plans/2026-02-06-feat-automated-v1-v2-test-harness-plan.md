---
title: "feat: Automated v1 vs v2 test harness using claude --print"
type: feat
date: 2026-02-06
---

# feat: Automated V1 vs V2 Test Harness

## Overview

Build a bash script that swaps SKILL.md between v1 (upstream) and v2 (current), runs `claude --print "/last30days [query]"` for all 17 test queries on each version, captures output to files, then generates a comparison doc with analysis.

## How It Works

```
┌─────────────────────────────────────────────────┐
│ test-v1-vs-v2.sh                                │
│                                                 │
│  1. Save current SKILL.md as .v2 backup         │
│  2. Install v1 SKILL.md from upstream           │
│  3. Loop 17 queries → claude --print → v1/*.txt │
│  4. Restore v2 SKILL.md                         │
│  5. Loop 17 queries → claude --print → v2/*.txt │
│  6. Generate comparison doc                     │
└─────────────────────────────────────────────────┘
```

Each `claude --print` call:
- Invokes the /last30days skill exactly as a user would
- Runs the Python script (real API calls to OpenAI + xAI)
- Runs WebSearch
- Applies SKILL.md presentation instructions
- Returns the full formatted output
- Exits (no interactive session)

## Implementation

### File: `scripts/test-v1-vs-v2.sh`

```bash
#!/bin/bash
set -euo pipefail

# === Config ===
SKILL_DIR="$HOME/.claude/skills/last30days"
REPO_DIR="$HOME/last30days-skill-private"
OUT_DIR="$REPO_DIR/docs/test-results/v1-vs-v2-$(date +%Y%m%d-%H%M%S)"
V1_DIR="$OUT_DIR/v1"
V2_DIR="$OUT_DIR/v2"

mkdir -p "$V1_DIR" "$V2_DIR"

# All 17 test queries (from README + plans)
declare -a QUERIES=(
  "prompting techniques for chatgpt for legal questions"
  "best clawdbot use cases"
  "how to best setup clawdbot"
  "prompting tips for nano banana pro for ios designs"
  "top claude code skills"
  "using ChatGPT to make images of dogs"
  "research best practices for beautiful remotion animation videos in claude code"
  "photorealistic people in nano banana pro"
  "What are the best rap songs lately"
  "what are people saying about DeepSeek R1"
  "best practices for cursor rules files for Cursor"
  "prompt advice for using suno to make killer songs in simple mode"
  "how do I use Codex with Claude Code on same app to make it better"
  "kanye west"
  "howie.ai"
  "open claw"
  "nano banana pro prompting"
)

declare -a TYPES=(
  "PROMPTING+TOOL"
  "RECOMMENDATIONS"
  "HOW-TO"
  "PROMPTING+TOOL"
  "RECOMMENDATIONS"
  "GENERAL"
  "PROMPTING"
  "PROMPTING"
  "RECOMMENDATIONS"
  "NEWS"
  "PROMPTING"
  "PROMPTING"
  "HOW-TO"
  "NEWS"
  "GENERAL"
  "GENERAL"
  "PROMPTING"
)

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | head -c 60
}

run_version() {
  local version="$1"
  local outdir="$2"

  echo ""
  echo "=========================================="
  echo "  Running $version — ${#QUERIES[@]} queries"
  echo "=========================================="

  for i in "${!QUERIES[@]}"; do
    local query="${QUERIES[$i]}"
    local type="${TYPES[$i]}"
    local slug=$(slugify "$query")
    local num=$((i + 1))
    local outfile="$outdir/${num}-${slug}.txt"

    echo ""
    echo "[$version] ($num/${#QUERIES[@]}) $query [$type]"
    echo "  → $outfile"

    # Run claude --print with the skill invocation
    # --no-session-persistence: don't save to session history
    # Timeout after 5 minutes per query (generous for slow API calls)
    if timeout 300 claude --print \
      "/last30days $query" \
      > "$outfile" 2>"$outdir/${num}-${slug}.stderr.txt"; then
      echo "  ✅ Done ($(wc -l < "$outfile") lines)"
    else
      echo "  ❌ Failed or timed out"
      echo "FAILED: timeout or error" >> "$outfile"
    fi

    # Brief pause between queries to avoid rate limits
    sleep 2
  done
}

# === Phase 1: Test V1 ===
echo "📦 Backing up current SKILL.md..."
cp "$SKILL_DIR/SKILL.md" "$SKILL_DIR/SKILL.md.v2.bak"

echo "📥 Installing V1 SKILL.md from upstream..."
cd "$REPO_DIR"
git show upstream/main:SKILL.md > "$SKILL_DIR/SKILL.md"

# Also save a copy for reference
cp "$SKILL_DIR/SKILL.md" "$OUT_DIR/v1-SKILL.md"

run_version "V1" "$V1_DIR"

# === Phase 2: Test V2 ===
echo ""
echo "📥 Restoring V2 SKILL.md..."
cp "$SKILL_DIR/SKILL.md.v2.bak" "$SKILL_DIR/SKILL.md"

# Also save a copy for reference
cp "$SKILL_DIR/SKILL.md" "$OUT_DIR/v2-SKILL.md"

run_version "V2" "$V2_DIR"

# === Phase 3: Generate summary ===
echo ""
echo "=========================================="
echo "  Generating comparison summary"
echo "=========================================="

SUMMARY="$OUT_DIR/comparison-summary.md"

cat > "$SUMMARY" << 'HEADER'
# V1 vs V2 Comparison Results

Generated: $(date)

## Output Files

| # | Query | Type | V1 Lines | V2 Lines |
|---|-------|------|----------|----------|
HEADER

# Replace the date placeholder
sed -i '' "s/\$(date)/$(date)/" "$SUMMARY"

for i in "${!QUERIES[@]}"; do
  local query="${QUERIES[$i]}"
  local type="${TYPES[$i]}"
  local slug=$(slugify "$query")
  local num=$((i + 1))

  local v1file="$V1_DIR/${num}-${slug}.txt"
  local v2file="$V2_DIR/${num}-${slug}.txt"

  local v1lines=$(wc -l < "$v1file" 2>/dev/null || echo "0")
  local v2lines=$(wc -l < "$v2file" 2>/dev/null || echo "0")

  echo "| $num | \`$query\` | $type | $v1lines | $v2lines |" >> "$SUMMARY"
done

cat >> "$SUMMARY" << 'FOOTER'

## Scorecard Template

For each query, score both versions on:

| Dimension | V1 | V2 | Notes |
|-----------|----|----|-------|
| Query Parsing Display (1-5) | | | |
| Source Coverage (1-5) | | | |
| Citation Quality (1-5) | | | |
| Summary Structure (1-5) | | | |
| Stats Box Format (1-5) | | | |
| Research Grounding (1-5) | | | |

## Next Step

Read each pair of output files and score them using the test plan at:
`docs/plans/2026-02-06-test-v1-vs-v2-comparison-plan.md`
FOOTER

echo ""
echo "✅ All done!"
echo "📁 Results: $OUT_DIR"
echo "📊 Summary: $SUMMARY"
echo ""
echo "V1 outputs: $V1_DIR/"
echo "V2 outputs: $V2_DIR/"
echo ""
echo "To review, run:"
echo "  open $OUT_DIR"

# Cleanup backup
rm -f "$SKILL_DIR/SKILL.md.v2.bak"
```

## Acceptance Criteria

- [ ] Script runs all 17 queries on v1 SKILL.md
- [ ] Script runs all 17 queries on v2 SKILL.md
- [ ] Each query output saved to a separate .txt file
- [ ] Comparison summary generated with line counts
- [ ] SKILL.md restored to v2 after testing
- [ ] Both SKILL.md versions saved in output dir for reference
- [ ] Script handles timeouts gracefully (5 min per query)
- [ ] Brief pause between queries to avoid rate limits

## Cost Estimate

- 34 total `claude --print` invocations
- Each invocation: ~1 Python script run (OpenAI + xAI API) + 2-3 WebSearches + Claude response
- Estimated: ~$0.10-0.30 per invocation for API calls
- **Total estimate: $3-10 for the full run**

## Time Estimate

- Each query: ~1-3 minutes (script + WebSearch + synthesis)
- 17 queries × 2 versions = 34 runs
- **Total: ~45-90 minutes** (could run in background)

## How to Run

```bash
cd $HOME/last30days-skill-private
chmod +x scripts/test-v1-vs-v2.sh
./scripts/test-v1-vs-v2.sh
```

Or run in background:
```bash
nohup ./scripts/test-v1-vs-v2.sh > test-run.log 2>&1 &
tail -f test-run.log
```

## After the Run

Once all outputs are captured, Claude can read every file pair and generate the scored comparison doc with analysis — that's the part where I score each dimension 1-5 and write the final report.

## Files

| File | Purpose |
|------|---------|
| `scripts/test-v1-vs-v2.sh` | The test harness script |
| `docs/test-results/v1-vs-v2-*/` | Output directory (timestamped) |
| `docs/test-results/v1-vs-v2-*/v1/*.txt` | V1 outputs |
| `docs/test-results/v1-vs-v2-*/v2/*.txt` | V2 outputs |
| `docs/test-results/v1-vs-v2-*/comparison-summary.md` | Auto-generated summary |
