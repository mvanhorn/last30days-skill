---
name: last30days
version: "3.0.0-open"
description: "Research any topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, GitHub, Perplexity, and more. AI agent scores by upvotes, likes, and real money. Also triggered by 'last30'."
argument-hint: 'last30 AI video tools, last30 watch my competitor every week, last30 give me my briefing'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
disable-model-invocation: true
---

# last30days (open variant): Research + Watchlist + Briefings

Multi-mode research skill with persistent knowledge accumulation.

## Command Routing

Parse the user's first argument to determine the mode:

| First word | Mode | Reference |
|---|---|---|
| `watch` | Watchlist management | `references/watchlist.md` |
| `briefing` | Morning briefing | `references/briefing.md` |
| `history` | Query accumulated knowledge | `references/history.md` |
| *(anything else)* | One-shot research | `references/research.md` |

## Setup: Find Skill Root

```bash
for dir in \
  "." \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "${GEMINI_EXTENSION_DIR:-}" \
  "$HOME/.gemini/extensions/last30days-skill" \
  "$HOME/.gemini/extensions/last30days" \
  "$HOME/.claude/skills/last30days" \
  "$HOME/.agents/skills/last30days" \
  "$HOME/.codex/skills/last30days"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
  echo "ERROR: Could not find scripts/last30days.py" >&2
  exit 1
fi
```

Use `$SKILL_ROOT` for all script and reference file paths.

## Load Context

At session start, read `${SKILL_ROOT}/variants/open/context.md` for user preferences and source quality notes. Update it after interactions.

## Shared Configuration

- **Database**: `~/.local/share/last30days/research.db` (SQLite, WAL mode)
- **Briefings**: `~/.local/share/last30days/briefs/`
- **API keys**: `~/.config/last30days/.env` or environment variables
- **Key priority**: env vars > config file

### API Keys

| Key | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | For Reddit | Reddit search via OpenAI responses API |
| `XAI_API_KEY` | For X (fallback) | X search via xAI Grok API |
| `OPENROUTER_API_KEY` | Optional | Perplexity Sonar Pro search + AI reasoning (planning/reranking). Add `INCLUDE_SOURCES=perplexity` to enable. Use `--deep-research` for exhaustive reports. |
| `PARALLEL_API_KEY` | Optional | Web search via Parallel AI |
| `BRAVE_API_KEY` | Optional | Web search via Brave Search |

Bird CLI provides free X search if installed. YouTube search uses yt-dlp (free).

Run `python3 "${SKILL_ROOT}/scripts/last30days.py" --diagnose` to check source availability.

## Routing Logic

After determining the mode, **read the corresponding reference file** using the Read tool:

```
Read: ${SKILL_ROOT}/variants/open/references/{mode}.md
```

Then follow the instructions in that reference file exactly.
