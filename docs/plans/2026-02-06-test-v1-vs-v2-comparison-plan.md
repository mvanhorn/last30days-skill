---
title: "test: Compare v1 (public) vs v2 (private) last30days output quality"
type: test
date: 2026-02-06
---

# test: V1 vs V2 Comparison Test Plan

## Overview

Run the same queries through both the public v1 and private v2 of last30days, compare output quality across 7 dimensions, and determine if v2 is ready to ship as the new public version.

**This plan also includes a full feature audit** identifying everything v1 has that v2 is missing — some of those gaps need fixing before shipping.

---

## How to Run the Comparison

### Setup

**V1 (public upstream):** Check out upstream SKILL.md temporarily:
```bash
# Save current v2
cp ~/.claude/skills/last30days/SKILL.md ~/.claude/skills/last30days/SKILL.md.v2

# Install v1 from upstream
cd $HOME/last30days-skill-private
git show upstream/main:SKILL.md > ~/.claude/skills/last30days/SKILL.md
```

Run test queries in a NEW Claude Code session (one session per query to avoid context bleed). Save output.

**V2 (private current):** Restore v2:
```bash
cp ~/.claude/skills/last30days/SKILL.md.v2 ~/.claude/skills/last30days/SKILL.md
```

Run same queries in NEW sessions. Save output.

---

## ALL Test Queries

### From README Examples (13 documented use cases)

Every single example from the README, in order:

| # | Query | Type | README Section |
|---|-------|------|---------------|
| 1 | `prompting techniques for chatgpt for legal questions` | PROMPTING + TOOL | Example: Legal Prompting |
| 2 | `best clawdbot use cases` | RECOMMENDATIONS | Example: ClawdBot Use Cases |
| 3 | `how to best setup clawdbot` | HOW-TO | Example: ClawdBot Setup |
| 4 | `prompting tips for nano banana pro for ios designs` | PROMPTING + TOOL | Example: iOS App Mockup |
| 5 | `top claude code skills` | RECOMMENDATIONS | Example: Top Claude Code Skills |
| 6 | `using ChatGPT to make images of dogs` | GENERAL | Example: Dog as Human |
| 7 | `research best practices for beautiful remotion animation videos in claude code` | PROMPTING | Example: Remotion Launch Video |
| 8 | `photorealistic people in nano banana pro` | PROMPTING | Example: Photorealistic Portraits |
| 9 | `What are the best rap songs lately` | RECOMMENDATIONS | Example: Best Rap Songs |
| 10 | `what are people saying about DeepSeek R1` | NEWS | Example: DeepSeek R1 |
| 11 | `best practices for cursor rules files for Cursor` | PROMPTING | Example: Cursor Rules |
| 12 | `prompt advice for using suno to make killer songs in simple mode` | PROMPTING | Example: Suno AI Music |
| 13 | `how do I use Codex with Claude Code on same app to make it better` | HOW-TO | Example: Codex + Claude Code |

### From Plan Documents (4 additional battle-tested queries)

| # | Query | Type | Source |
|---|-------|------|--------|
| 14 | `kanye west` | NEWS | fix-v2-formatting plan, most-tested query |
| 15 | `howie.ai` | GENERAL | fix-v2-formatting plan, edge case (domain as topic) |
| 16 | `open claw` | GENERAL | fix-v2-formatting plan, X-heavy sources |
| 17 | `nano banana pro prompting` | PROMPTING | fix-v2-formatting plan |

### Follow-up Vision Tests (pick 4 from above, ask a follow-up)

These test the prompt-generation phase specifically:

| Base Query | Follow-up Vision |
|------------|-----------------|
| #4 (nano banana pro ios) | "make a mock-up of an app for moms who swim" |
| #6 (ChatGPT dog images) | "what would my dog look like as a human prompt" |
| #12 (suno music) | "Rap song about self aware AI that loves Claude Code" |
| #13 (codex + claude code) | "how do I build a review loop workflow" |

---

## FEATURE AUDIT: V1 vs V2

### Section-by-section comparison

I diffed the full v1 (upstream/main) SKILL.md against the current v2. Here's everything.

#### KEPT (in both versions) ✅

| Feature | V1 Location | V2 Location | Notes |
|---------|------------|------------|-------|
| Parse User Intent section | Lines 23-48 | Lines 12-38 | Same logic |
| QUERY_TYPE detection (4 types) | Lines 29-36 | Lines 18-22 | Same types |
| "Don't ask about tool before research" | Lines 49-51 | Lines 31-33 | Same rule |
| Store variables block | Lines 53-56 | Lines 35-38 | Same |
| Research script execution | Lines 81-86 | Lines 59-62 | Same command |
| WebSearch by QUERY_TYPE | Lines 99-127 | Lines 77-98 | Same queries |
| "Use user's exact terminology" | Lines 129-133 | Lines 100-101 | V2 shorter but same intent |
| Judge Agent synthesis | Lines 143-151 | Lines 113-124 | Same logic |
| Internalize research (ground in actual content) | Lines 159-165 | Lines 128-135 | V2 shorter |
| RECOMMENDATIONS: extract specific names | Lines 167-177 | Lines 137-145 | Same, v2 removes BAD/GOOD example |
| Prompt format matching | Lines 193-196 | Lines 149-153 | Same |
| Summary + Stats + Invitation flow | Lines 200-250 | Lines 157-236 | Same structure, different details |
| Wait for user's vision | Lines 254-258 | Lines 240-242 | Same |
| Write ONE perfect prompt | Lines 262-275 | Lines 246-266 | Same structure |
| Context memory | Lines 298-316 | Lines 278-288 | V2 shorter |
| Output summary footer | Lines 320-340 | Lines 292-302 | Different format |
| Depth options (quick/default/deep) | Lines 135-139 | Lines 106-109 | Same |

#### ADDED in V2 (improvements) ✨

| Feature | What it does | V2 Location |
|---------|-------------|------------|
| **Query parsing display** | Shows `🔍 **{TOPIC}** · {QUERY_TYPE}` before tools | Lines 40-53 |
| **Sparse citation rules** | BAD/GOOD examples, "1 per pattern, short format" | Lines 186-193 |
| **Bold topic headers** | `**{Topic 1}** — [1-2 sentences, per source]` format | Lines 195-208 |
| **Strict stats template** | "NEVER use plain text dashes", fill-in-blank | Lines 217-230 |
| **RECOMMENDATIONS source attribution** | Each item MUST have Sources: line with @handles | Lines 178-182 |
| **Reddit 0 results handling** | Explicit instruction for 0-thread line | Line 229 |
| **Bird CLI in stats** | "(via Bird/xAI)" notation | Line 223 |

#### ❌ MISSING FROM V2 — Features V1 Has That V2 Dropped

These are the regressions. Some are intentional simplifications, others are real gaps.

**1. Use Cases Block (intro section)**
- **V1 has:** 4 use case examples right after the intro: Prompting, Recommendations, News, General — with concrete examples
- **V2 has:** Nothing. Just the intro paragraph.
- **Impact:** LOW. The query type detection handles this. But it was nice onboarding.
- **Verdict:** Skip — not needed for execution quality.

**2. Setup Check Section (API key guidance)**
- **V1 has:** Full section explaining 3 modes (Full/Partial/Web-Only), first-time setup bash script, "API keys are OPTIONAL" messaging
- **V2 has:** Nothing. Script auto-detects.
- **Impact:** LOW for experienced users. HIGH for first-time users who don't have keys.
- **Verdict:** Skip for now — script handles auto-detection. Consider adding back for public release.

**3. Anti-Pattern Examples (synthesis quality guard)**
- **V1 has:** Explicit anti-pattern block: "If user asks about 'clawdbot skills' and research returns ClawdBot content (self-hosted AI agent), do NOT synthesize this as 'Claude Code skills' just because both involve 'skills'." Plus BAD/GOOD synthesis examples for RECOMMENDATIONS.
- **V2 has:** Only "Ground your synthesis in the ACTUAL research content, not your pre-existing knowledge" — no concrete examples.
- **Impact:** MEDIUM-HIGH. Without concrete anti-patterns, the agent may conflate similar-sounding things.
- **Verdict:** ⚠️ ADD BACK. At minimum, restore the BAD/GOOD RECOMMENDATIONS example and the "don't conflate" warning.

**4. Self-Check Instruction (pre-display validation)**
- **V1 has:** "SELF-CHECK before displaying: Re-read your 'What I learned' section. Does it match what the research ACTUALLY says? If the research was about ClawdBot (a self-hosted AI agent), your summary should be about ClawdBot, not Claude Code. If you catch yourself projecting your own knowledge instead of the research, rewrite it."
- **V2 has:** Nothing.
- **Impact:** MEDIUM. The self-check forces the model to validate its own output.
- **Verdict:** ⚠️ ADD BACK. One line costs nothing and catches hallucination.

**5. Quality Checklist for Prompts ⭐**
- **V1 has:** Explicit checklist before delivering a prompt:
  ```
  ### Quality Checklist:
  - [ ] FORMAT MATCHES RESEARCH - If research said JSON/structured/etc, prompt IS that format
  - [ ] Directly addresses what the user said they want to create
  - [ ] Uses specific patterns/keywords discovered in research
  - [ ] Ready to paste with zero edits (or minimal [PLACEHOLDERS] clearly marked)
  - [ ] Appropriate length and style for TARGET_TOOL
  ```
- **V2 has:** Only "If research says to use a specific prompt FORMAT, YOU MUST USE THAT FORMAT." — one line instead of 5 checks.
- **Impact:** HIGH. This is likely what the user noticed as missing — v1 prompts felt more polished because the agent ran a checklist before delivering.
- **Verdict:** ⚠️ ADD BACK. This is the "that's a great prompt" quality feel.

**6. Prompt Format Anti-Pattern**
- **V1 has:** "ANTI-PATTERN: Research says 'use JSON prompts with device specs' but you write plain prose. This defeats the entire purpose of the research."
- **V2 has:** Only the positive instruction (use the format research recommends).
- **Impact:** MEDIUM. Negative examples ("don't do this") are powerful for LLMs.
- **Verdict:** ⚠️ ADD BACK. One line.

**7. "IF USER ASKS FOR MORE OPTIONS" Section**
- **V1 has:** "Only if they ask for alternatives or more prompts, provide 2-3 variations. Don't dump a prompt pack unless requested."
- **V2 has:** Nothing about handling multi-prompt requests.
- **Impact:** LOW-MEDIUM. Without it, agent might dump multiple prompts unprompted.
- **Verdict:** ⚠️ ADD BACK. Two lines.

**8. Web-Only Mode Stats Template + Promo**
- **V1 has:** Separate stats template for web-only mode with "💡 Want engagement metrics? Add API keys..." promo
- **V2 has:** Only the full-mode template. If running web-only, agent has no guidance.
- **Impact:** MEDIUM for users without API keys.
- **Verdict:** Consider adding back for public release. Lower priority for now.

**9. TARGET_TOOL Question Template**
- **V1 has:** Explicit AskUserQuestion block with 4 options: [Most relevant tool], Nano Banana Pro, ChatGPT/Claude, Other
- **V2 has:** "run research first, then ask AFTER showing results" — but no actual question template.
- **Impact:** LOW-MEDIUM. Agent will still ask, just less structured.
- **Verdict:** Skip — not critical.

**10. Context Memory: "Don't re-search" Instructions**
- **V1 has:** Explicit "DO NOT run new WebSearches — you already have the research. Answer from what you learned. Cite the Reddit threads, X posts, and web sources."
- **V2 has:** Only "Only do new research if the user explicitly asks about a DIFFERENT topic."
- **Impact:** MEDIUM. Without the explicit ban, agent may re-search on follow-ups, wasting time.
- **Verdict:** ⚠️ ADD BACK. Three lines.

**11. Output Summary Footer (emoji + engagement counts)**
- **V1 has:** `📚 Expert in: {TOPIC} for {TARGET_TOOL}` and `📊 Based on: {n} Reddit threads ({sum} upvotes) + {n} X posts ({sum} likes) + {n} web pages`
- **V2 has:** `Expert in: {TOPIC} for {TARGET_TOOL}` and `Based on: {n} Reddit threads + {n} X posts + {n} web pages` — no emoji, no engagement counts.
- **Impact:** LOW but noticeable. The emoji + counts make the footer feel more substantial.
- **Verdict:** ⚠️ ADD BACK. Trivial fix.

---

## Priority Fix List (Before Shipping V2 as Public)

Based on the audit, these should be restored in V2 before it replaces V1:

### Must Fix (affects output quality)

| # | Missing Feature | Why | Effort |
|---|----------------|-----|--------|
| 1 | **Quality Checklist for prompts** | The "that's a great prompt" feel. V1's 5-point checklist made prompts more polished. | Add 8 lines to SKILL.md |
| 2 | **Anti-pattern examples** | BAD/GOOD synthesis examples prevent agent from conflating research. | Add 5 lines |
| 3 | **Self-check instruction** | One-line pre-display validation catches hallucination. | Add 2 lines |
| 4 | **Context Memory: don't re-search** | Prevents wasting time re-searching on follow-ups. | Add 3 lines |

### Should Fix (polish)

| # | Missing Feature | Why | Effort |
|---|----------------|-----|--------|
| 5 | **Prompt format anti-pattern** | Negative example reinforces "match the format". | Add 2 lines |
| 6 | **"IF USER ASKS FOR MORE OPTIONS"** | Prevents prompt dumping. | Add 2 lines |
| 7 | **Output footer emoji + engagement counts** | More polished footer. | Edit 3 lines |

### Skip for Now (nice-to-have for public release)

| # | Missing Feature | Why Skip |
|---|----------------|----------|
| 8 | Use cases block (intro) | Doesn't affect execution |
| 9 | Setup Check section | Script auto-detects; add back for public README |
| 10 | Web-only mode stats + promo | Lower priority, most users have keys |
| 11 | TARGET_TOOL question template | Agent handles this naturally |

---

## Scoring Dimensions (1-5 scale, 7 dimensions)

### 1. Query Parsing Display
Does the agent show what it understood before starting research?

| Score | Criteria |
|-------|----------|
| 1 | No acknowledgment, jumps straight to tools |
| 2 | Generic "I'll research this" with no specifics |
| 3 | Mentions the topic but not query type |
| 4 | Shows topic + query type clearly |
| 5 | Shows topic + query type + reformulated search terms |

### 2. Source Coverage
Did it actually use Reddit, X, AND web — or skip sources?

| Score | Criteria |
|-------|----------|
| 1 | WebSearch only, script didn't run |
| 2 | Script ran but returned 0 from one major source |
| 3 | 2 of 3 sources returned results |
| 4 | All 3 sources returned results |
| 5 | All 3 sources + good volume (10+ Reddit, 10+ X, 5+ web) |

### 3. Citation Quality
Are citations sparse and useful, or verbose and noisy?

| Score | Criteria |
|-------|----------|
| 1 | Every sentence has 3+ citations chained |
| 2 | Most sentences have multiple citations |
| 3 | 1-2 citations per insight, some over-citing |
| 4 | 1 citation per pattern, short format |
| 5 | Sparse citations that prove research is real without cluttering |

### 4. Summary Structure
Is the "What I learned" section scannable or a wall of text?

| Score | Criteria |
|-------|----------|
| 1 | Single paragraph wall of text |
| 2 | Multiple paragraphs but no structure |
| 3 | Some bold text but inconsistent |
| 4 | Bold topic headers with 1-2 sentence explanations |
| 5 | Clean topic headers + KEY PATTERNS list, easy to scan |

### 5. Stats Box Format
Does the emoji stats tree render correctly?

| Score | Criteria |
|-------|----------|
| 1 | No stats shown |
| 2 | Stats shown but plain text dashes, no emoji |
| 3 | Partial emoji format, some lines wrong |
| 4 | Correct ├─ └─ │ format with emoji, minor issues |
| 5 | Perfect emoji tree with accurate counts and top voices |

### 6. Research Grounding
Does the synthesis reflect the ACTUAL research, or generic pre-training knowledge?

| Score | Criteria |
|-------|----------|
| 1 | Entirely generic knowledge, no research content |
| 2 | Mentions some research but mostly generic |
| 3 | Mix of research and generic, some conflation |
| 4 | Clearly grounded in research, minor generic leakage |
| 5 | Every insight traceable to a specific source from the research |

### 7. Prompt Quality (follow-up tests only)
When user shares vision, is the generated prompt good?

| Score | Criteria |
|-------|----------|
| 1 | Generic prompt that ignores research |
| 2 | Mentions research topics but generic structure |
| 3 | Uses some research insights, decent prompt |
| 4 | Tailored to research, correct format for target tool |
| 5 | Uses research-recommended format, specific techniques, ready to paste, "that's a great prompt" feel |

---

## Comparison Scorecard Template

```
Query: [query text]
Version: V1 / V2
Date: YYYY-MM-DD

| Dimension            | Score (1-5) | Notes |
|---------------------|-------------|-------|
| Query Parsing       |             |       |
| Source Coverage      |             |       |
| Citation Quality     |             |       |
| Summary Structure    |             |       |
| Stats Box Format     |             |       |
| Research Grounding   |             |       |
| Prompt Quality       |             | (follow-up tests only) |
| **TOTAL**           | **/35**     |       |

Script output:
- Reddit: ___ threads / ___ upvotes / ___ comments
- X: ___ posts / ___ likes / ___ reposts
- Web: ___ pages

Observations:
[Free text notes]
```

---

## Execution Plan

### Phase 1: Fix the gaps first
Apply the 7 "Must Fix" + "Should Fix" items from the audit to V2 SKILL.md. This takes ~20 minutes since it's all small text additions.

### Phase 2: Smoke test (4 queries)
Run queries #14 (kanye west), #2 (best clawdbot use cases), #8 (photorealistic nano banana pro), #10 (DeepSeek R1) on V2 only. Verify the fixes work.

### Phase 3: Full comparison (all 17 queries)
Run all 17 queries on both V1 and V2. Fill scorecards.

### Phase 4: Follow-up vision tests (4 queries)
Run the 4 follow-up vision tests. Compare prompt quality — this is where the quality checklist fix matters most.

### Phase 5: Analysis
- Sum scores per version across all queries
- Identify any dimension where v1 consistently beats v2
- Decision: ship v2, or fix more gaps first

## Acceptance Criteria

- [x] Feature audit complete (this document)
- [x] Must-fix gaps restored in V2 SKILL.md
- [ ] All 17 queries run on V2
- [ ] At least 4 queries run on V1 for comparison
- [ ] 4 follow-up vision tests completed
- [ ] Scorecards filled for each
- [ ] Total score comparison documented
- [ ] Any V1 > V2 regressions identified with fix plan
- [ ] Go/no-go decision on shipping v2 as public

## Files

| File | Purpose |
|------|---------|
| `docs/plans/2026-02-06-test-v1-vs-v2-comparison-plan.md` | This plan |
| `SKILL.md` | Apply Must Fix + Should Fix items |
| `docs/test-results/v1-vs-v2-comparison.md` | Results (to be created) |
