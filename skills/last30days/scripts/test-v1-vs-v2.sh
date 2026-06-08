#!/bin/bash
set -euo pipefail

# === V1 vs V2 Skill Test Harness ===
# Runs all 17 test queries through both v1 and v2 SKILL.md
# using `claude --print` to capture real end-to-end output.

SKILL_DIR="$HOME/.claude/skills/last30days"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CLAUDE="${CLAUDE:-$(command -v claude || echo claude)}"

# Safety: always restore V2 SKILL.md on exit/crash
cleanup() {
  if [ -f "$SKILL_DIR/SKILL.md.v2.bak" ]; then
    echo ""
    echo "⚠️  Restoring V2 SKILL.md from backup (script interrupted)..."
    cp "$SKILL_DIR/SKILL.md.v2.bak" "$SKILL_DIR/SKILL.md"
    rm -f "$SKILL_DIR/SKILL.md.v2.bak"
    echo "  ✅ V2 restored"
  fi
}
trap cleanup EXIT
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUT_DIR="$REPO_DIR/docs/test-results/v1-vs-v2-${TIMESTAMP}"
V1_DIR="$OUT_DIR/v1"
V2_DIR="$OUT_DIR/v2"

mkdir -p "$V1_DIR" "$V2_DIR"

echo "📁 Output directory: $OUT_DIR"
echo ""

# All 17 test queries
QUERIES=(
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

TYPES=(
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
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | cut -c1-50
}

run_version() {
  local version="$1"
  local outdir="$2"
  local total=${#QUERIES[@]}

  echo ""
  echo "=========================================="
  echo "  Running $version — $total queries"
  echo "=========================================="
  echo ""

  for i in "${!QUERIES[@]}"; do
    local query="${QUERIES[$i]}"
    local type="${TYPES[$i]}"
    local slug
    slug=$(slugify "$query")
    local num=$((i + 1))
    local outfile="$outdir/${num}-${slug}.txt"
    local errfile="$outdir/${num}-${slug}.stderr.txt"

    echo "[$version] ($num/$total) $query [$type]"

    local start_time
    start_time=$(date +%s)

    # Run claude --print with the skill invocation
    # No timeout — claude --print exits on its own; kill manually if stuck
    if "$CLAUDE" --print \
      "/last30days $query" \
      > "$outfile" 2>"$errfile"; then
      local end_time
      end_time=$(date +%s)
      local duration=$((end_time - start_time))
      local lines
      lines=$(wc -l < "$outfile")
      echo "  ✅ Done — ${lines} lines, ${duration}s"
    else
      local exit_code=$?
      echo "  ❌ Failed (exit $exit_code)" | tee -a "$outfile"
    fi

    # Brief pause between queries to avoid rate limits
    sleep 3
  done
}

# === Phase 1: Test V1 ===
echo "📦 Backing up current V2 SKILL.md..."
cp "$SKILL_DIR/SKILL.md" "$SKILL_DIR/SKILL.md.v2.bak"

echo "📥 Installing V1 SKILL.md from upstream..."
cd "$REPO_DIR"
git show upstream/main:SKILL.md | sed '/^context: fork$/d; /^agent: Explore$/d; /^disable-model-invocation: true$/d' > "$SKILL_DIR/SKILL.md"
cp "$SKILL_DIR/SKILL.md" "$OUT_DIR/v1-SKILL.md"
echo "  ✅ V1 installed (stripped: context:fork, agent:Explore, disable-model-invocation)"

run_version "V1" "$V1_DIR"

# === Phase 2: Test V2 ===
echo ""
echo "📥 Restoring V2 SKILL.md..."
cp "$SKILL_DIR/SKILL.md.v2.bak" "$SKILL_DIR/SKILL.md"
cp "$SKILL_DIR/SKILL.md" "$OUT_DIR/v2-SKILL.md"
echo "  ✅ V2 restored"

run_version "V2" "$V2_DIR"

# === Phase 3: Generate summary ===
echo ""
echo "=========================================="
echo "  Generating comparison summary"
echo "=========================================="

SUMMARY="$OUT_DIR/comparison-summary.md"

cat > "$SUMMARY" << EOF
# V1 vs V2 Comparison Results

Generated: $(date)
Output directory: $OUT_DIR

## Output Files

| # | Query | Type | V1 Lines | V2 Lines | V1 Time | V2 Time |
|---|-------|------|----------|----------|---------|---------|
EOF

for i in "${!QUERIES[@]}"; do
  query="${QUERIES[$i]}"
  type="${TYPES[$i]}"
  slug=$(slugify "$query")
  num=$((i + 1))

  v1file="$V1_DIR/${num}-${slug}.txt"
  v2file="$V2_DIR/${num}-${slug}.txt"

  v1lines=$(wc -l < "$v1file" 2>/dev/null || echo "ERR")
  v2lines=$(wc -l < "$v2file" 2>/dev/null || echo "ERR")

  echo "| $num | \`$query\` | $type | $v1lines | $v2lines | — | — |" >> "$SUMMARY"
done

cat >> "$SUMMARY" << 'EOF'

## Quick Check: Key Features

For each query, check these v2 improvements:

- [ ] Query parsing display (`🔍 **{TOPIC}** · {QUERY_TYPE}`)
- [ ] Sparse citations (not every sentence)
- [ ] Bold topic headers in summary
- [ ] Emoji stats tree (`├─ 🟠 Reddit:`)
- [ ] Quality checklist applied to prompts
- [ ] Self-check (research grounding, not generic)

## Scoring Guide

Use the full scoring rubric from:
`docs/plans/2026-02-06-test-v1-vs-v2-comparison-plan.md`

## Next Step

Have Claude read all 34 output files and generate scored comparison:
```
Read all files in docs/test-results/v1-vs-v2-*/v1/ and v2/
Score each on the 7 dimensions from the test plan
Write the final analysis to docs/test-results/v1-vs-v2-*/analysis.md
```
EOF

# Cleanup backup
rm -f "$SKILL_DIR/SKILL.md.v2.bak"

echo ""
echo "✅ All done!"
echo ""
echo "📁 Results:  $OUT_DIR"
echo "📊 Summary:  $SUMMARY"
echo "📄 V1 files: $V1_DIR/"
echo "📄 V2 files: $V2_DIR/"
echo ""
echo "To review:"
echo "  open $OUT_DIR"
