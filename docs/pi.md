# Pi package

This repo includes a pi-native bridge around the upstream `last30days` Python research engine.

## What it adds

- pi tool: `last30days_research`
- pi tool: `last30days_diagnose`
- command: `/last30days <topic> [--quick|--balanced|--deep] [--days N] [--emit html]`
- command: `/last30days-doctor`
- command: `/last30days-config`
- command: `/last30days-open`
- pi skill: `/skill:last30days`

The pi integration does **not** ask the model to follow the Claude-specific 1400-line runtime contract in `skills/last30days/SKILL.md`. It keeps the useful part for pi: call the local Python engine through typed tools and concise slash commands.

## Install in pi

From a local checkout:

```bash
pi install /absolute/path/to/last30days-skill
```

Or from a git repo/fork:

```bash
pi install https://github.com/<you>/last30days-skill
```

Then reload pi:

```text
/reload
```

## Typical usage

```text
/last30days OpenAI --quick
/last30days "Claude Code vs OpenClaw" --deep --days 14
/last30days Cursor IDE --emit html
/last30days OpenAI --competitors 2
/last30days-doctor
/last30days-config
/last30days-open
/skill:last30days cursor vs windsurf
```

Supported command flags:

- `--quick`, `--balanced`, `--deep`
- `--depth quick|balanced|deep`
- `--days N` / `--days=N`
- `--lookback N` / `--lookback=N`
- `--emit compact|json|md|html`
- `--search reddit,youtube,github`
- `--web-backend auto|brave|exa|serper|parallel|none`
- `--competitors` / `--competitors N`
- `--no-auto-resolve` for literal searches

## X / Twitter setup

Fastest path:

1. Log into `x.com` in a local browser.
2. Open `~/.config/last30days/.env` with `/last30days-config`.
3. Add:

```env
FROM_BROWSER=auto
SETUP_COMPLETE=true
```

On macOS, Chrome may ask for Keychain permission the first time cookies are read.

Alternative auth methods:

```env
XAI_API_KEY=...
# or
AUTH_TOKEN=...
CT0=...
```

## Optional source unlocks

```env
# TikTok, Instagram, Threads, Pinterest, YouTube/TikTok comments
SCRAPECREATORS_API_KEY=...
INCLUDE_SOURCES=tiktok,instagram,threads

# Web backends
BRAVE_API_KEY=...
EXA_API_KEY=...
SERPER_API_KEY=...
PARALLEL_API_KEY=...

# Perplexity / Deep Research through OpenRouter
OPENROUTER_API_KEY=...

# Bluesky
BSKY_HANDLE=you.bsky.social
BSKY_APP_PASSWORD=...
```

Digg AI-1000 clusters unlock when `digg-pp-cli` is on `PATH`.

## Developer checks

```bash
npm run verify:pi
pi -e ./extensions/index.ts --skill ./pi-skills/last30days --mode json '/last30days-doctor'
uv run pytest -q
```

## Notes

- Python 3.12+ is required. If no `python3.12+` binary is found, the pi extension falls back to `uv run --project <repo> python`.
- Reports are saved to `~/Documents/Last30Days/` by default.
- Tool output is capped to pi's default tool-output budget; full report files remain in the save directory.
- If coverage is sparse, run `/last30days-doctor` and configure more sources.
