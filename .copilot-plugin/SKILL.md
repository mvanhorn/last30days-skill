---
name: last30days
version: "3.0.1"
description: "Multi-query social search with intelligent planning. Research any topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, and the web."
argument-hint: 'last30days AI video tools, last30days best noise cancelling headphones'
allowed-tools: Bash, Read, Write, WebSearch
homepage: https://github.com/mvanhorn/last30days-skill
repository: https://github.com/mvanhorn/last30days-skill
author: mvanhorn
license: MIT
user-invocable: true
metadata:
  copilot:
    emoji: "📰"
    tags:
      - research
      - deep-research
      - reddit
      - x
      - twitter
      - youtube
      - tiktok
      - instagram
      - hackernews
      - polymarket
      - trends
      - recency
      - news
      - citations
      - multi-source
      - social-media
      - analysis
      - web-search
    requires:
      optionalEnv:
        - SCRAPECREATORS_API_KEY
        - OPENAI_API_KEY
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PARALLEL_API_KEY
        - BRAVE_API_KEY
        - APIFY_API_TOKEN
        - AUTH_TOKEN
        - CT0
        - BSKY_HANDLE
        - BSKY_APP_PASSWORD
        - TRUTHSOCIAL_TOKEN
      bins:
        - node
        - python3
    files:
      - "scripts/*"
    homepage: https://github.com/mvanhorn/last30days-skill
---

# last30days v3.0.1: Research Any Topic from the Last 30 Days

> **GitHub Copilot edition.** This SKILL.md is adapted for GitHub Copilot (VS Code). The Python engine is identical to Claude Code / OpenClaw / Hermes / Codex versions — only the agent instructions differ.

> **Permissions overview:** Reads public web/platform data and optionally saves research briefings to `~/Documents/Last30Days/` (Windows: `%USERPROFILE%\Documents\Last30Days\`). X/Twitter search uses optional user-provided tokens (AUTH_TOKEN/CT0 env vars). Bluesky search uses optional app password (BSKY_HANDLE/BSKY_APP_PASSWORD env vars - create at bsky.app/settings/app-passwords). All credential usage and data writes are documented in the [Security & Permissions](#security--permissions) section.

Research ANY topic across Reddit, X, YouTube, and other sources. Surface what people are actually discussing, recommending, betting on, and debating right now.

## Runtime Preflight

Before running any `last30days.py` command, resolve a Python 3.12+ interpreter. On Windows (PowerShell):

```powershell
$pyExe = @("python3","python") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $pyExe) { Write-Error "Python 3.12+ required"; return }
```

On macOS/Linux (bash):

```bash
for py in python3.14 python3.13 python3.12 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || continue
  LAST30DAYS_PYTHON="$py"
  break
done
```

## Step 0: First-Run Setup

To detect first run: check if `~/.config/last30days/.env` (Windows: `%USERPROFILE%\.config\last30days\.env`) exists. If it contains `SETUP_COMPLETE=true`, skip setup silently and proceed to Step 1.

If this is a first run, display:

```
Welcome to last30days!

I research any topic across Reddit, X, YouTube, and other sources —
synthesizing what people are actually saying right now.

These sources work with zero config:
- Reddit (with comments) — public JSON, no API key needed
- Hacker News — always on
- Polymarket — prediction markets with real money
- GitHub — if `gh` CLI is installed

To unlock more sources, edit ~/.config/last30days/.env:
- X/Twitter: add XAI_API_KEY (from api.x.ai) or AUTH_TOKEN+CT0 (browser cookies)
- YouTube: install yt-dlp (pip install yt-dlp)
- TikTok/Instagram: add SCRAPECREATORS_API_KEY (10,000 free calls at scrapecreators.com)
- Web search: add BRAVE_API_KEY (2,000 free queries/month at brave.com/search/api)
```

Then create the .env file with `SETUP_COMPLETE=true` and proceed to research.

**END OF FIRST-RUN SETUP.**

---

## CRITICAL: Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about
2. **TARGET TOOL** (if specified): Where they'll use the prompts
3. **QUERY TYPE**: RECOMMENDATIONS | NEWS | PROMPTING | COMPARISON | GENERAL

Display a branded confirmation:
```
/last30days — searching {ACTIVE_SOURCES_LIST} for what people are saying about {TOPIC}.
```

Then proceed to research execution.

---

## Step 0.5: Resolve X Handles (if topic could have X accounts)

If TOPIC looks like it could have its own X/Twitter account (people, creators, brands, products, tools, companies), do WebSearches to find handles:

1. Primary handle: `WebSearch("{TOPIC} X twitter handle site:x.com")`
2. Company/founder handle: `WebSearch("{TOPIC} company CEO of site:x.com")`
3. 1-2 related handles

Pass handles to the CLI: `--x-handle={handle}` and `--x-related={handle1},{handle2}`

Skip if TOPIC is a generic concept or already contains @.

---

## Step 0.55: Pre-Research Intelligence

Run 2-3 focused WebSearches to resolve platform-specific targeting:

1. Reddit communities: `WebSearch("{TOPIC} subreddit reddit community")`
2. Current events: `WebSearch("{TOPIC} news {CURRENT_MONTH} {CURRENT_YEAR}")`
3. Infer TikTok hashtags, Instagram creators, YouTube queries from topic knowledge

---

## Step 0.75: Generate Query Plan

Generate a JSON query plan for the topic:

```json
{
  "intent": "breaking_news|product|comparison|how_to|opinion|prediction|factual|concept",
  "freshness_mode": "strict_recent|balanced_recent|evergreen_ok",
  "cluster_mode": "story|debate|market|workflow|none",
  "subqueries": [
    {
      "label": "primary",
      "search_query": "keyword-heavy search terms",
      "ranking_query": "Natural language question for ranking",
      "sources": ["reddit", "x", "youtube", "tiktok", "instagram", "hackernews", "polymarket"],
      "weight": 1.0
    }
  ]
}
```

Rules:
- 1 to 4 subqueries
- Primary subquery MUST include ALL sources
- NEVER include temporal phrases in search_query
- For comparisons: per-entity subqueries at 0.8 + head-to-head at 1.0

---

## Research Execution

**Run the research script in the terminal with a 5-minute timeout.**

Find the skill root:
```
# Check these locations in order:
# 1. Current workspace (if scripts/last30days.py exists)
# 2. ~/.agents/skills/last30days/
```

Execute:
```bash
python scripts/last30days.py "{TOPIC}" --emit=compact --save-dir=~/Documents/Last30Days --save-suffix=v3 --plan '{QUERY_PLAN_JSON}' --x-handle={HANDLE} --subreddits={SUBS}
```

On Windows, use `python` instead of `python3`. Use `$env:USERPROFILE\Documents\Last30Days` for `--save-dir`. Note: `--plan` JSON may require escaping in PowerShell. If the plan fails to parse, the script falls back to its internal planner automatically — this is safe and still produces good results.

**Read the ENTIRE output.** It contains: Reddit items, X items, YouTube items, TikTok items, Instagram items, Hacker News items, Polymarket items, and WebSearch items.

---

## Step 2: WebSearch After Script Completes

After the script finishes, do WebSearch to supplement with blogs, tutorials, and news. Choose queries based on QUERY_TYPE.

---

## Synthesis

Synthesize all sources following cluster-first output:
1. Multi-source clusters are highest confidence
2. Check uncertainty tags
3. Weight Reddit/X higher (engagement signals)
4. Weight YouTube high (transcripts)
5. Quote directly from evidence snippets
6. Cite sources: prefer @handles > r/subreddits > YouTube channels > web sources

**Citation format:** "per @handle" or "per r/subreddit" — never paste raw URLs.

### If QUERY_TYPE = RECOMMENDATIONS
Extract SPECIFIC NAMES with mention counts and source attribution.

### If QUERY_TYPE = COMPARISON
Structure as side-by-side with Quick Verdict, Strengths/Weaknesses, Head-to-Head table, Bottom Line.

---

## Stats Block

After synthesis, show:
```
📊 Stats: {N} Reddit · {N} X · {N} YouTube · {N} TikTok · {N} HN · {N} Polymarket · {N} Web
```

---

## Security & Permissions

- **Reads:** Public web data via APIs (Reddit JSON, X search, YouTube via yt-dlp, HN API, Polymarket API)
- **Writes:** Research briefings to `~/Documents/Last30Days/` (Windows: `%USERPROFILE%\Documents\Last30Days\`), optional via `--save-dir`
- **Credentials:** User-provided env vars in `~/.config/last30days/.env` (Windows: `%USERPROFILE%\.config\last30days\.env`). Never transmitted except to their intended API endpoints.
- **No telemetry.** No analytics. No tracking. Research stays on your machine.
