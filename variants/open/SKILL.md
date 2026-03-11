---
name: "open"
description: "Research topics, manage watchlists, get briefings, query history. Also triggered by 'last30'. Sources: Reddit, X, YouTube, web."
allowed-tools: "Bash, Read, Write, AskUserQuestion, WebSearch"
metadata:
  version: "2.1-open"
  argument-hint: "last30 AI video tools, last30 watch my competitor every week, last30 give me my briefing"
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
| `PARALLEL_API_KEY` | Optional | Web search via Parallel AI |
| `BRAVE_API_KEY` | Optional | Web search via Brave Search |
| `OPENROUTER_API_KEY` | Optional | Web search via Perplexity Sonar Pro |

Bird CLI provides free X search if installed. YouTube search uses yt-dlp (free).

Run `python3 "${SKILL_ROOT}/scripts/last30days.py" --diagnose` to check source availability.

## Routing Logic

After determining the mode, **read the corresponding reference file** using the Read tool:

```
Read: ${SKILL_ROOT}/variants/open/references/{mode}.md
```

Then follow the instructions in that reference file exactly.
