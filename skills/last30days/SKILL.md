---
name: last30days-v3-spec
version: "3.0.0"
description: "Internal architecture spec for the v3 last30days runtime pipeline. Not user-invocable."
argument-hint: "last30days codex vs claude code"
allowed-tools: Bash, Read, Write, WebSearch
homepage: https://github.com/mvanhorn/last30days-skill
repository: https://github.com/mvanhorn/last30days-skill
author: mvanhorn
license: MIT
user-invocable: false
---

# last30days v3.0.0

Use `last30days` when the user wants recent, cross-source evidence from the last 30 days.

The runtime is a single v3 pipeline:

1. plan the query
2. retrieve per `(subquery, source)`
3. normalize and dedupe
4. extract best snippets
5. fuse with weighted RRF
6. rerank with one relevance score
7. cluster evidence
8. render ranked clusters

## Setup: resolve the skill root

```bash
for dir in \
  "." \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "${GEMINI_EXTENSION_DIR:-}" \
  "$HOME/.openclaw/workspace/skills/last30days" \
  "$HOME/.openclaw/skills/last30days" \
  "$HOME/.claude/skills/last30days" \
  "$HOME/.agents/skills/last30days" \
  "$HOME/.codex/skills/last30days"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
  echo "ERROR: Could not find scripts/last30days.py" >&2
  exit 1
fi

for py in python3.14 python3.13 python3.12 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || continue
  LAST30DAYS_PYTHON="$py"
  break
done

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  echo "ERROR: last30days v3 requires Python 3.12+. Install python3.12 or python3.13 and rerun." >&2
  exit 1
fi
```

## Default command

```bash
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --emit=compact
```

## Useful commands

```bash
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --emit=json
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --quick
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --deep
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --search=reddit,x,grounding
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --store
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" --diagnose
```

## Runtime expectations

- One reasoning provider is required: `GOOGLE_API_KEY` for Gemini, `OPENAI_API_KEY` for OpenAI, or `XAI_API_KEY` for xAI.
- `BRAVE_API_KEY` enables Brave web search (recommended). `SERPER_API_KEY` is the web fallback.
- `SCRAPECREATORS_API_KEY` enables Reddit, TikTok, and Instagram.
- `XAI_API_KEY` enables xAI reasoning and X search.
- `AUTH_TOKEN` plus `CT0` enables Bird-backed X search.
- `yt-dlp` enables YouTube.
- Planning and reranking fall back gracefully: Gemini -> OpenAI -> xAI -> deterministic/local.
- Web retrieval stays within Brave/Serper dated results. Undated web hits are dropped.

## Output model

- `compact` and `md`: cluster-first markdown
- `json`: full v3 report
- `context`: short synthesis-oriented context

Important report fields:

- `provider_runtime`
- `query_plan`
- `ranked_candidates`
- `clusters`
- `items_by_source`
- `errors_by_source`

## Usage guidance for agents

- Prefer `--quick` for fast iteration.
- Prefer default mode when the user wants a balanced answer.
- Prefer `--deep` only when the user explicitly wants maximum recall or the topic is complex enough to justify extra latency.
- Prefer `--emit=json` when downstream code or evaluation will consume the result.
- Use `--search=` only when the user explicitly wants source restrictions.

## X handle resolution

If the topic could have its own X/Twitter account (people, brands, products, companies), do a quick WebSearch for their handle:
```
WebSearch("{TOPIC} X twitter handle site:x.com")
```
If you find a verified handle, pass `--x-handle={handle}` (without @). This searches their posts directly, finding content they posted that doesn't mention their own name. Skip this for generic concepts ("best headphones 2026", "how to use Docker").

## Synthesis guidance

### First: synthesize, don't summarize

Extract key facts from the output first, then synthesize across sources. Lead with patterns that appear across multiple clusters. Present a unified narrative, not a source-by-source summary.

### Ground in actual research, not pre-existing knowledge

Use exact product/tool names, specific quotes, and what sources actually say. If research mentions "ClawdBot" and "@clawdbot", that is a different product than "Claude Code" -- read what the research actually says.

**Anti-pattern to avoid:**
- BAD: User asks "best Claude Code skills" and you respond with generic advice: "Skills are powerful. Keep them under 500 lines."
- GOOD: You respond with specifics from the research: "Most mentioned: /commit (5 mentions), remotion skill (4x), git-worktree (3x). The Remotion announcement got 16K likes on X per @thedorbrothers."

### Source weighting (highest to lowest signal)

1. **Cross-cluster corroboration** -- same evidence across multiple sources is the strongest signal. Lead with it.
2. **Reddit top comments** -- often the wittiest, most insightful take. Quote directly when upvotes are high.
3. **YouTube transcript highlights** -- pre-extracted key moments. Quote and attribute to channel name.
4. **X/Twitter @handles** -- real-time community signal. Quote with engagement context.
5. **Polymarket odds** -- real money on outcomes cuts through opinion. Include specific odds AND movement.
6. **TikTok/Instagram** -- viral/creator signal. Cite @creators with views/likes.
7. **Hacker News** -- technical community perspective. Cite as "per HN."
8. **Web (Brave/Serper)** -- cite only when social sources don't cover a fact.

### Polymarket interpretation

When Polymarket returns relevant markets:
1. Prefer structural/long-term markets over near-term deadlines (championship odds > regular season, IPO > incremental update)
2. Call out the specific outcome's odds and movement, not just that a market exists
3. Weave odds into the narrative as supporting evidence, don't isolate them
4. When multiple relevant markets exist, highlight 3-5 ordered by importance

Domain importance ranking:
- **Sports:** Championship/tournament > conference title > regular season > weekly matchup
- **Geopolitics:** Regime change/structural > near-term strike deadlines > sanctions
- **Tech/Business:** IPO, major product launch > incremental updates
- **Elections:** Presidency > primary > individual state

### Citation rules

Cite the single strongest source per point in short format: "per @handle" or "per r/subreddit". Save engagement metrics for the stats section. Use the priority order from source weighting above. The tool's value is surfacing what PEOPLE are saying, not what journalists wrote.

### Comparison queries

For "X vs Y" queries, structure output as:

```
## Quick Verdict
[1-2 sentences: which one the community prefers and why, with source counts]

## [Entity A]
**Community Sentiment:** [Positive/Mixed/Negative] (N mentions across sources)
**Strengths:** [with source attribution]
**Weaknesses:** [with source attribution]

## [Entity B]
[Same structure]

## Head-to-Head
| Dimension | Entity A | Entity B |
|-----------|----------|----------|
| [Key dim] | [position] | [position] |

## Bottom Line
Choose A if... Choose B if... (based on community data)
```

### Recommendation queries

When users ask "best X" or "top X", extract SPECIFIC NAMES:

```
Most mentioned:
[Name] -- Nx mentions
  Sources: @handle1, r/subreddit, [YouTube channel]

[Name] -- Nx mentions
  Sources: @handle2, r/subreddit2

Notable mentions: [others with 1-2 mentions]
```

### Edge cases

- **Empty results from a source:** State what is missing. ("No Reddit discussion found for this topic.") Do not fill the gap with training data.
- **Sources contradict each other:** Present both sides with attribution. ("Reddit r/fitness is bullish on X, while @DrExpert on X warns about Y.")
- **All results are low-engagement or off-topic:** Acknowledge uncertainty. ("Limited recent discussion found -- these findings should be treated as preliminary.")

### Follow-up conversations

After research completes, treat yourself as an expert on this topic. Answer follow-ups from the research findings. Cite the specific threads, posts, and channels you found. Only run new research if the user asks about a DIFFERENT topic.

## Security and permissions

**What this skill does:**
- Sends search queries to ScrapeCreators API for Reddit, TikTok, Instagram search
- Sends search queries via xAI API or Bird client for X search
- Sends search queries to Algolia HN Search API (free, no auth)
- Sends search queries to Polymarket Gamma API (free, no auth)
- Runs yt-dlp locally for YouTube search and transcript extraction (no API key)
- Sends search queries to Brave Search API or Serper for web search (optional)
- Uses Gemini, OpenAI, or xAI for LLM planning and reranking
- Stores findings in local SQLite database (--store mode only)

**What this skill does NOT do:**
- Does not post, like, or modify content on any platform
- Does not access your personal accounts on any platform
- Does not share API keys between providers
- Does not log or cache API keys in output files
