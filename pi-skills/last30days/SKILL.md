---
name: last30days
description: Research what people are saying about a topic in the last 30 days using the local last30days engine from pi across Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, Digg, and optional web/social sources.
---

# last30days for pi

Use this skill when the user wants current discourse, recommendations, comparisons, trend signals, market/social sentiment, or a shareable brief from the last 30 days.

This is the pi wrapper around the upstream `mvanhorn/last30days-skill` Python engine. Prefer the pi tools exposed by the extension instead of trying to follow the Claude-specific root skill contract.

## Parse the request

Support these optional inline flags when the user includes them:

- `--quick` → fast pulse check
- `--balanced` or `--depth balanced` → normal/default depth
- `--deep` or `--depth deep` → more exhaustive research
- `--days N`, `--days=N`, `--lookback N`, `--lookback=N`, `--lookback-days N`, or `--lookback-days=N` → custom lookback window
- `--emit html|md|json|compact` → output/export format. Use `html` when the user asks for a shareable brief.
- `--search source1,source2` → source filter when the user explicitly asks for one
- `--web-backend auto|brave|exa|serper|parallel|none` → web backend preference
- `--competitors` or `--competitors N` → competitor comparison mode
- `--no-auto-resolve` → skip entity/community pre-resolution if the user wants a literal search

Treat the remaining text as the topic. Quoted phrases are allowed in `/last30days` commands.

If no topic is provided, ask the user what they want to research.

## Setup / sparse-result help

If the user asks about setup, missing sources, auth, sparse results, or why a source did not appear:

1. Call `last30days_diagnose`.
2. Explain the currently active sources.
3. Tell the user `/last30days-config` opens `~/.config/last30days/.env`.
4. Mention `/last30days-open` opens the saved report directory.
5. Common unlocks:
   - X/Twitter: log into x.com or add `XAI_API_KEY` or `AUTH_TOKEN` + `CT0`
   - Digg: install `digg-pp-cli`
   - YouTube: install `yt-dlp`
   - TikTok / Instagram / Threads / Pinterest / comments: add `SCRAPECREATORS_API_KEY`
   - Web: add `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, or `PARALLEL_API_KEY`
   - Perplexity / Deep Research: add `OPENROUTER_API_KEY`

## Research flow

1. Parse the topic and optional flags.
2. Call `last30days_research` with the parsed topic.
3. Use depth `balanced` by default.
4. Use `quick` for a simple pulse check.
5. Use `deep` when the user explicitly asks for an exhaustive pass or a richer comparison.
6. Use `emit=html` when the user asks for a shareable/slack/email/Notion-ready brief.
7. Keep `autoResolve` enabled unless the user explicitly asks for literal/no-resolve behavior.
8. If the user asks to compare a company/product against peers and does not name peers, use `competitors=2` unless they specify a different count.

## Response style

- Summarize the highest-signal findings first.
- Call out when the report is obviously source-limited.
- Be concrete about what sources were active if that matters.
- If the tool output says it was truncated, mention that full reports are saved under `LAST30DAYS_MEMORY_DIR`, which defaults to `~/Documents/Last30Days/`.
- If an HTML or markdown export was requested, include the saved file path when the engine provides it.
- End with a useful follow-up suggestion.
