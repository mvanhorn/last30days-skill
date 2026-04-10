# One-Shot Research Mode (v3)

Research ANY topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, and the web. Surface what people are actually discussing, recommending, betting on, and debating right now.

---

## 1. Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about
2. **TARGET TOOL** (if specified): Where they'll use the prompts
3. **QUERY TYPE**:
   - **PROMPTING** - "X prompts", "prompting for X" -> copy-paste prompts
   - **RECOMMENDATIONS** - "best X", "top X" -> list of specific things
   - **NEWS** - "what's happening with X" -> current events
   - **COMPARISON** - "X vs Y", "X versus Y", "compare X and Y" -> side-by-side comparison
   - **GENERAL** - anything else -> broad understanding

Common patterns:
- `[topic] for [tool]` -> TOOL IS SPECIFIED
- `[topic] prompts for [tool]` -> TOOL IS SPECIFIED
- Just `[topic]` -> TOOL NOT SPECIFIED, that's OK
- "best [topic]" or "top [topic]" -> QUERY_TYPE = RECOMMENDATIONS
- "X vs Y" or "X versus Y" -> QUERY_TYPE = COMPARISON, TOPIC_A = X, TOPIC_B = Y

**Do NOT ask about target tool before research.** Run research first, ask after.

**Store these variables:**
- `TOPIC = [extracted topic]`
- `TARGET_TOOL = [extracted tool, or "unknown" if not specified]`
- `QUERY_TYPE = [PROMPTING | RECOMMENDATIONS | NEWS | COMPARISON | GENERAL]`
- `TOPIC_A = [first item]` (only if COMPARISON)
- `TOPIC_B = [second item]` (only if COMPARISON)

---

## 2. Confirm Topic

Display a branded one-liner before starting research. Build ACTIVE_SOURCES_LIST by checking what's configured in .env (Reddit, HN, Polymarket are always active; add X, YouTube, TikTok, Instagram, GitHub, Perplexity based on configured keys/tools).

For GENERAL / NEWS / RECOMMENDATIONS / PROMPTING queries:
```
/last30days - searching {ACTIVE_SOURCES_LIST} for what people are saying about {TOPIC}.
```

For COMPARISON queries:
```
/last30days - comparing {TOPIC_A} vs {TOPIC_B} across {ACTIVE_SOURCES_LIST}.
```

Do NOT show a multi-line "Parsed intent" block with TOPIC=, TARGET_TOOL=, QUERY_TYPE= variables. Do NOT promise a specific time. Do NOT list sources that aren't configured.

Then proceed immediately to research execution.

---

## 3. Handle / GitHub Resolution

**OpenClaw does not have WebSearch.** Skip manual handle resolution (Steps 0.5, 0.55, 0.75 from the main skill). Instead, add `--auto-resolve` to the research command. The engine will use configured web search backends (Brave, Exa, Serper) to discover subreddits, X handles, and context before planning.

If the user manually provides handles or community names, pass them through as CLI flags (see the flags list in Research Execution below). But do NOT attempt WebSearch-based resolution yourself.

---

## 4. Agent Mode (--agent flag)

If `--agent` appears in ARGUMENTS (e.g., `/last30days plaud granola --agent`):

1. **Skip** the intro display block
2. **Skip** any `AskUserQuestion` calls - use `TARGET_TOOL = "unknown"` if not specified
3. **Run** the research script exactly as normal
4. **Skip** the follow-up invitation
5. **Output** the complete research report and stop - do not wait for further input

Agent mode saves raw research data to `~/Documents/Last30Days/` automatically via `--save-dir`.

Agent mode report format:

```
## Research Report: {TOPIC}
Generated: {date} | Sources: Reddit, X, YouTube, TikTok, Instagram, HN, Polymarket, Web

### Key Findings
[3-5 bullet points, highest-signal insights with citations]

### What I learned
{The full "What I learned" synthesis from normal output}

### Stats
{The standard stats block}
```

---

## 5. Comparison Mode (QUERY_TYPE = COMPARISON)

When the user asks "X vs Y", run ONE research pass with a comparison-optimized query that covers both entities AND their rivalry.

**Single pass with entity-aware subqueries:**
```bash
python3 "${SKILL_ROOT}/scripts/last30days.py" "{TOPIC_A} vs {TOPIC_B}" --auto-resolve --emit=compact --save-dir=~/Documents/Last30Days --save-suffix=v3 --store 2>&1
```

If the user provided manual handles or subreddits, include those flags too.

Then skip the normal Research Execution below - go directly to the comparison synthesis format (see Synthesis section).

**Comparison output format:**

```
# {TOPIC_A} vs {TOPIC_B}: What the Community Says (Last 30 Days)

## Quick Verdict
[1-2 sentence data-driven summary: which one the community prefers and why, with source counts]

## {TOPIC_A}
Community Sentiment: [Positive/Mixed/Negative] ({N} mentions across {sources})

Strengths (what people love)
- [Point 1 with source attribution]
- [Point 2]

Weaknesses (common complaints)
- [Point 1 with source attribution]
- [Point 2]

## {TOPIC_B}
Community Sentiment: [Positive/Mixed/Negative] ({N} mentions across {sources})

Strengths (what people love)
- [Point 1 with source attribution]
- [Point 2]

Weaknesses (common complaints)
- [Point 1 with source attribution]
- [Point 2]

## Head-to-Head
[Synthesis from the combined search - what people say when directly comparing]

| Dimension | {TOPIC_A} | {TOPIC_B} |
|-----------|-----------|-----------|
| [Key dimension 1] | [A's position] | [B's position] |
| [Key dimension 2] | [A's position] | [B's position] |
| [Key dimension 3] | [A's position] | [B's position] |

## The Bottom Line
Choose {TOPIC_A} if... Choose {TOPIC_B} if... (based on actual community data, not assumptions)
```

Then show combined stats and the standard invitation section.

---

## 6. Research Execution

**Run the research script in the FOREGROUND with a 5-minute timeout.**

```bash
python3 "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --auto-resolve --emit=compact --save-dir=~/Documents/Last30Days --save-suffix=v3 --store 2>&1
```

Use a **timeout of 300000** (5 minutes). The `--store` flag persists findings for watchlist/briefing integration.

**Always include `--auto-resolve`** since OpenClaw has no WebSearch. The engine will use configured web search backends (Brave, Exa, Serper) to discover subreddits, X handles, and current events context before planning.

**Available flags** (pass through if the user provides them manually):
- `--x-handle={handle}` - primary X/Twitter handle (without @)
- `--x-related={handle1},{handle2}` - related X handles (comma-separated, without @)
- `--subreddits={sub1},{sub2}` - target subreddits (comma-separated, no r/ prefix)
- `--tiktok-hashtags={tag1},{tag2}` - TikTok hashtags to search
- `--tiktok-creators={creator1},{creator2}` - TikTok creator handles
- `--ig-creators={creator1},{creator2}` - Instagram creator handles
- `--github-user={username}` - GitHub username for person-mode search
- `--github-repo={owner/repo}` - GitHub repo for project-mode search (comma-separated for multiple)
- `--deep-research` - Perplexity Deep Research mode (exhaustive 50+ citation reports, ~$0.90/query, requires OPENROUTER_API_KEY + `INCLUDE_SOURCES=perplexity`)
- `--days=N` - look back N days instead of 30
- `--quick` - faster, fewer sources (8-12 each)
- `--deep` - comprehensive (50-70 Reddit, 40-60 X)

**Read the ENTIRE output.** It contains data sections for: Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, and Web. If you miss sections, you will produce incomplete stats.

---

## 7. WebSearch Supplemental

**If your platform supports WebSearch**, use it after the script finishes to supplement with blogs, tutorials, and news.

Choose search queries based on QUERY_TYPE:

- **RECOMMENDATIONS**: `best {TOPIC} recommendations`, `{TOPIC} list examples`
- **NEWS**: `{TOPIC} news 2026`, `{TOPIC} announcement update`
- **PROMPTING**: `{TOPIC} prompts examples 2026`, `{TOPIC} techniques tips`
- **GENERAL**: `{TOPIC} 2026`, `{TOPIC} discussion`

Rules:
- **USE THE USER'S EXACT TERMINOLOGY**
- EXCLUDE reddit.com, x.com, twitter.com (covered by script)
- Do NOT output a separate "Sources:" block

**If your platform does NOT support WebSearch**, the `--auto-resolve` flag already provides web context via the engine's configured backends. Skip this step.

---

## 8. Synthesis / Judge Agent

### v3 Cluster-First Output

v3 returns results grouped by STORY/THEME (clusters), not by source. Each cluster represents one narrative thread found across multiple platforms.

**How to read v3 output:**
- `### 1. Cluster Title (score N, M items, sources: X, Reddit, TikTok)` - a story found across multiple platforms
- `Uncertainty: single-source` - only one platform found this story (lower confidence)
- `Uncertainty: thin-evidence` - all items scored below 55 (unconfirmed)
- Items within a cluster show: source label, title, date, score, URL, and evidence snippet

**Synthesis strategy for cluster-first output:**
1. **Synthesize per-cluster first.** Each cluster = one story. Summarize what each story is about.
2. **Multi-source clusters are highest confidence.** A cluster with items from Reddit + X + YouTube is much stronger than single-source.
