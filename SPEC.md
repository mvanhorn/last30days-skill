# last30days Skill Specification

## Overview

`last30days` is a Claude Code skill that researches a given topic across Reddit and X (Twitter) using the OpenAI Responses API and xAI Responses API respectively. It enforces a strict 30-day recency window, popularity-aware ranking, and produces actionable outputs including best practices, a prompt pack, and a reusable context snippet. OpenAI auth can come from `OPENAI_API_KEY` or Codex login credentials.

The skill operates in three modes depending on available API keys: **reddit-only** (OpenAI key), **x-only** (xAI key), or **both** (full cross-validation). It uses automatic model selection to stay current with the latest models from both providers, with optional pinning for stability.

## Architecture

The orchestrator (`last30days.py`) coordinates discovery, enrichment, normalization, scoring, deduplication, and rendering. Each concern is isolated in `scripts/lib/`:

- **env.py**: Load API keys from `~/.config/last30days/.env` and Codex auth from `~/.codex/auth.json`
- **dates.py**: Date range calculation and confidence scoring
- **cache.py**: 24-hour TTL caching keyed by topic + date range
- **http.py**: stdlib-only HTTP client with retry logic
- **models.py**: Auto-selection of OpenAI/xAI models with 7-day caching
- **openai_reddit.py**: OpenAI Responses API + web_search for Reddit
- **xai_x.py**: xAI Responses API + x_search for X
- **reddit_enrich.py**: Fetch Reddit thread JSON for real engagement metrics
- **hackernews.py**: Hacker News search via Algolia API (free, no auth)
- **polymarket.py**: Polymarket prediction market search via Gamma API (free, no auth)
- **adanos.py**: Optional Adanos Market Sentiment source for finance/ticker queries
- **normalize.py**: Convert raw API responses to canonical schema
- **score.py**: Compute popularity-aware scores (relevance + recency + engagement)
- **dedupe.py**: Near-duplicate detection via text similarity
- **render.py**: Generate markdown and JSON outputs
- **schema.py**: Type definitions and validation

## Embedding in Other Skills

Other skills can import the research context in several ways:

### Inline Context Injection
```markdown
## Recent Research Context
!python3 ~/.claude/skills/last30days/scripts/last30days.py "your topic" --emit=context
```

### Read from File
```markdown
## Research Context
!cat ~/.local/share/last30days/out/last30days.context.md
```

### Get Path for Dynamic Loading
```bash
CONTEXT_PATH=$(python3 ~/.claude/skills/last30days/scripts/last30days.py "topic" --emit=path)
cat "$CONTEXT_PATH"
```

### JSON for Programmatic Use
```bash
python3 ~/.claude/skills/last30days/scripts/last30days.py "topic" --emit=json > research.json
```

## CLI Reference

```
python3 ~/.claude/skills/last30days/scripts/last30days.py <topic> [options]

Options:
  --refresh           Bypass cache and fetch fresh data
  --mock              Use fixtures instead of real API calls
  --emit=MODE         Output mode: compact|json|md|context|path (default: compact)
  --sources=MODE      Source selection: auto|reddit|x|both (default: auto)
```

## Output Files

All outputs are written to `~/.local/share/last30days/out/`:

- `report.md` - Human-readable full report
- `report.json` - Normalized data with scores
- `last30days.context.md` - Compact reusable snippet for other skills
- `raw_openai.json` - Raw OpenAI API response
- `raw_xai.json` - Raw xAI API response
- `raw_reddit_threads_enriched.json` - Enriched Reddit thread data
