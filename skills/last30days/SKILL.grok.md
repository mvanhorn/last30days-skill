---
name: last30days
version: "3.18.4"
description: "Research what people actually say about any topic in the last 30 days. Pulls posts and engagement from Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, and the web. Includes a doctor health check to diagnose broken or missing sources."
argument-hint: 'last30days nvidia earnings reaction | last30days AI video tools | last30days what users want in react'
allowed-tools: shell, WebSearch
homepage: https://github.com/mvanhorn/last30days-skill
repository: https://github.com/mvanhorn/last30days-skill
author: mvanhorn
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "📰"
    requires:
      env: []
      optionalEnv:
        - SCRAPECREATORS_API_KEY
        - OPENAI_API_KEY
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PERPLEXITY_API_KEY
        - PARALLEL_API_KEY
        - BRAVE_API_KEY
        - APIFY_API_TOKEN
        - AUTH_TOKEN
        - CT0
        - BSKY_HANDLE
        - BSKY_APP_PASSWORD
        - TRUTHSOCIAL_TOKEN
        - XIAOHONGSHU_API_BASE
      bins:
        - node
        - python3
    primaryEnv: SCRAPECREATORS_API_KEY
    files:
      - "scripts/*"
    homepage: https://github.com/mvanhorn/last30days-skill
    tags:
      - research
      - deep-research
      - reddit
      - x
      - twitter
      - youtube
      - tiktok
      - instagram
      - linkedin
      - hackernews
      - polymarket
      - digg
      - bluesky
      - truthsocial
      - xiaohongshu
      - rednote
      - trends
      - recency
      - news
      - citations
      - multi-source
      - social-media
      - analysis
      - web-search
      - hiring-signals
      - ai-skill
      - clawhub
---

# Grok Build (xAI) — Thin Path Entry

This is a minimal skill entry for Grok Build (xAI). It references the full SKILL.md for the complete contract but documents the Grok-specific tool mappings and recommended setup.

## Tool Mappings for Grok Build

| SKILL.md Tool | Grok Build Equivalent |
|---|---|
| `Bash` | `shell` tool |
| `WebSearch` | Built-in host web search |
| `AskUserQuestion` | Host question UI or prose prompt |

## Recommended X Backend on Grok

Use `XAI_API_KEY` as the preferred X backend on Grok (cookie-free, no browser access needed):

```bash
XAI_API_KEY=xai-...   # Get a key at https://api.x.ai
```

This is the cleanest X authentication path on Grok Build — no cookie extraction required.

## Full Contract Reference

The complete runtime contract (LAWs, setup wizard, engine invocation, synthesis rules) lives in **[SKILL.md](SKILL.md)**. This thin entry only documents Grok-specific deviations:

1. **Non-Modal Prose Flow** — Grok has no modal prompts, so it follows the Non-Modal Prose Flow in SKILL.md § Step 0.
2. **Visible-URL Citations** — Grok is a visible-URL host; LAW 8 requires plain labels (no inline Markdown links).
3. **Tool Substitution** — The allowed-tools above map to Grok's native tools as shown.
4. **Hook Resolution** — SessionStart hook resolves `GROK_PLUGIN_ROOT` first (see `hooks/hooks.json` and `hooks/scripts/check-config.sh`).

## Quick Start on Grok Build

```bash
# Install from marketplace
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days

# Or direct install (tracks HEAD)
grok plugin install mvanhorn/last30days-skill

# First run — setup wizard runs conversationally (Non-Modal Prose Flow)
/last30days "your topic"
```

The setup wizard will:
1. Show the welcome (via `--welcome`)
2. Run `--preflight` to show planned config
3. Ask for cookie consent (or recommend `XAI_API_KEY`)
4. Offer ScrapeCreators signup (GitHub OAuth for 10k free calls)
5. Let you pick source tier (TikTok/Instagram + comments, or Everything)
6. Write `SETUP_COMPLETE=true` and proceed to research

## Doctor Health Check

Run `/last30days doctor` to audit all sources. On Grok, it will show:
- Python 3.12+ availability (uv fallback if needed)
- X backend: prefers `XAI_API_KEY` when set
- Web search: uses host built-in (no `LAST30DAYS_NATIVE_SEARCH=1` needed)
- Memory dir: created by SessionStart hook via `GROK_PLUGIN_ROOT`

## Notes

- The full SKILL.md (~2255 lines) is still available for discovery, HTML rendering, and cross-host comparison.
- This thin path avoids loading the entire Claude-first modal contract on Grok.
- All LAWs (1–11) from SKILL.md still apply — this is a host adapter, not a contract change.