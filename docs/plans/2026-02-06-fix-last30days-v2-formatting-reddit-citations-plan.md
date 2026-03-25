---
title: "fix: last30days v2 formatting, Reddit results, and citation verbosity"
type: fix
date: 2026-02-06
---

# fix: last30days v2 Formatting, Reddit Results, and Citation Verbosity

## Overview

Four bugs found during v2 testing across 4 queries (kanye west, howie.ai, nano banana pro prompting, open claw). The skill IS executing (the agent:Explore removal worked) but output quality has regressed from v1.

## Problem Statement

| # | Bug | Severity | Where |
|---|-----|----------|-------|
| 1 | Stats emoji tree format ignored 3/4 times - agent renders plain text dashes instead | High | `SKILL.md` |
| 2 | Reddit returns 0 results for popular topics (kanye west, howie.ai) | High | `scripts/lib/openai_reddit.py` |
| 3 | Citations too verbose - every sentence has `(per @x, @y, @z; r/sub)` making summary unreadable | Medium | `SKILL.md` |
| 4 | Kanye summary is wall of text - no bold headers or paragraph breaks like nano banana pro got | Medium | `SKILL.md` |

## Proposed Fixes

### Fix 1: Stats Emoji Format Enforcement

**Root cause:** The agent ignores the emoji tree template even with BAD/GOOD examples. The template uses box-drawing characters (├─ └─) that the agent treats as decorative, not mandatory.

**Approach:** Instead of relying on the agent to copy box-drawing characters, provide the template as a **literal fill-in-the-blank** with placeholders that are impossible to misinterpret.

**File:** `SKILL.md` (stats section, currently around line 190)

**Change:** Replace the current template + BAD/GOOD examples with a single, strict fill-in format:

```
Copy this EXACTLY, replacing only the {placeholders}:

---
✅ All agents reported back!
├─ 🟠 Reddit: {N} threads │ {N} upvotes │ {N} comments
├─ 🔵 X: {N} posts │ {N} likes │ {N} reposts (via Bird/xAI)
├─ 🌐 Web: {N} pages │ {domain1}, {domain2}, {domain3}
└─ 🗣️ Top voices: @{handle1} ({N} likes), @{handle2} │ r/{sub1}, r/{sub2}
---

If Reddit returned 0 threads, write: "├─ 🟠 Reddit: 0 threads (no results this cycle)"
NEVER use plain text dashes (-) or pipe (|). ALWAYS use ├─ └─ │ and the emoji.
```

Remove the separate BAD/GOOD section (it adds length without helping).

### Fix 2: Reddit Returning 0 Results

**Root cause (from code analysis):**

1. `openai_reddit.py:53-93` - The `REDDIT_SEARCH_PROMPT` instructs the OpenAI model to strip noise words before searching. For "kanye west" this isn't the issue (no noise words), but for "howie.ai" it might strip "ai".

2. `openai_reddit.py:160-166` - The search is restricted to `allowed_domains: ["reddit.com"]` which depends on OpenAI's web_search indexing of Reddit.

3. `last30days.py:474-490` - Post-retrieval filtering: `normalize.filter_by_date_range()` + `score.score_reddit_items()` + `dedupe.dedupe_reddit()` can discard all results if date confidence is low.

4. `score.py:151-157` - Items with no engagement metrics get `-10` penalty, low date confidence gets `-10`. Combined that's `-20` which may push score below threshold.

**Approach (multi-layered):**

**A. Add subreddit-targeted search fallback** in `openai_reddit.py`:
- When the first search returns < 3 results, add a second search prompt that explicitly queries: `"r/{topic} site:reddit.com"` and `"{topic} subreddit site:reddit.com"`
- This catches cases where OpenAI's web_search doesn't find the obvious subreddit

**B. Soften post-retrieval scoring** in `score.py`:
- Change the no-engagement penalty from `-10` to `-3` (missing metrics ≠ irrelevant)
- Change low date confidence penalty from `-10` to `-5`

**C. Add minimum result guarantee** in `last30days.py`:
- If scoring filters out ALL results, keep the top 3 by raw relevance regardless of score
- Log a warning: "All Reddit results scored below threshold, keeping top 3 by relevance"

**Files to change:**
- `scripts/lib/openai_reddit.py` - Add subreddit fallback search (lines ~160-180)
- `scripts/lib/score.py` - Soften penalties (lines ~151-157)
- `scripts/last30days.py` - Add minimum result guarantee (lines ~474-490)

### Fix 3: Citations Too Verbose

**Root cause:** The SKILL.md instruction says "Every insight MUST cite at least one source" with a GOOD example showing `(per @XXX, 15 likes; r/kanye thread with 200 upvotes)` - this is too much detail per citation and the agent over-applies it.

**Approach:** Dial back to "cite 1-2 sources per KEY PATTERN, not per sentence. Use short format."

**File:** `SKILL.md` (citation section, currently around line 158)

**Change the citation rule to:**

```
CITATION RULE: Cite sources sparingly to prove research is real.
- In the "What I learned" intro: cite 1-2 top sources total, not every sentence
- In KEY PATTERNS: cite 1 source per pattern, short format: "per @handle" or "per r/sub"
- Do NOT include engagement metrics in citations (likes, upvotes) - save those for stats box
- Do NOT chain multiple citations: "per @x, @y, @z" is too much. Pick the strongest one.

BAD: "His album is set for March 20 (per @cocoabutterbf; Rolling Stone; HotNewHipHop; Complex)."
GOOD: "His album BULLY is set for March 20 via Gamma, per Rolling Stone."
```

### Fix 4: Summary Formatting (Wall of Text vs Structured)

**Root cause:** The SKILL.md template for PROMPTING/NEWS/GENERAL shows:
```
What I learned:
[2-4 sentences synthesizing...]
```

This gives the agent permission to write a dense paragraph. The nano banana pro test got good formatting because PROMPTING queries naturally produce structured patterns. NEWS queries (kanye) produce narratives that become walls of text.

**Approach:** Add explicit structure to the NEWS/GENERAL format with bold topic headers.

**File:** `SKILL.md` (summary display section, around line 158)

**Change the PROMPTING/NEWS/GENERAL template to:**

```
What I learned:

**{Topic 1}** — [1-2 sentences about this storyline, per source]

**{Topic 2}** — [1-2 sentences, per source]

**{Topic 3}** — [1-2 sentences, per source]

KEY PATTERNS from the research:
1. [Pattern] — per @handle
2. [Pattern] — per r/sub
3. [Pattern] — per source
```

The bold topic headers force structure. Each topic gets its own paragraph with a line break. No more wall-of-text narratives.

## Acceptance Criteria

- [ ] **Fix 1:** Stats box uses emoji tree format ├─ 🟠 🔵 🌐 └─ 🗣️ in 4/4 test queries
- [ ] **Fix 2:** "kanye west" returns >0 Reddit threads (r/kanye exists and is active)
- [ ] **Fix 3:** Summary citations are 1 per insight, short format, no engagement metrics inline
- [ ] **Fix 4:** NEWS/GENERAL summaries use bold topic headers with paragraph breaks, not wall of text

## Test Plan

Re-run the same 4 queries after fixes:
1. `/last30days kanye west` — NEWS: should get Reddit results, structured summary, emoji stats
2. `/last30days howie.ai` — GENERAL: should get Reddit if available, citations not verbose
3. `/last30days nano banana pro prompting` — PROMPTING: should maintain current good quality, reduce citation density
4. `/last30days open claw` — GENERAL: should cite @handles in summary, emoji stats

## Files to Modify

| File | Fix | Change |
|------|-----|--------|
| `SKILL.md` | 1, 3, 4 | Stats template, citation rules, summary structure |
| `scripts/lib/openai_reddit.py` | 2 | Add subreddit fallback search |
| `scripts/lib/score.py` | 2 | Soften scoring penalties |
| `scripts/last30days.py` | 2 | Add minimum result guarantee |

## References

- Current SKILL.md: `~/.claude/skills/last30days/SKILL.md`
- Private repo: `$HOME/last30days-skill-private/`
- Old working SKILL.md: `~/.claude/skills/last30days.backup-v1/SKILL.md`
- Reddit search module: `scripts/lib/openai_reddit.py:53-93` (prompt), `:160-166` (API call)
- Scoring module: `scripts/lib/score.py:151-157` (penalties)
- Main pipeline: `scripts/last30days.py:474-490` (filtering)
