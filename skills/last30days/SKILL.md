---
name: "last30days"
description: "Research recent discussion and evidence from the last 30 days across Reddit, X/Twitter, YouTube, TikTok, Instagram, Hacker News, Polymarket, GitHub, Bluesky, Perplexity, and web search. Use when the user asks what people are saying now, wants recent social research, wants source-backed recommendations or comparisons, asks for current trends, or invokes last30days."
---

# Last 30 Days

Use `last30days` to research a topic with recent social, market, code, and web evidence. Run the bundled Python CLI, then synthesize the strongest findings rather than returning raw search output.

## Resolve Runtime

Set these once per task before running commands:

```bash
for dir in \
  "${SKILL_ROOT:-}" \
  "${CODEX_HOME:-$HOME/.codex}/skills/last30days" \
  "$HOME/.agents/skills/last30days" \
  "$HOME/.claude/skills/last30days" \
  "$HOME/.openclaw/skills/last30days" \
  "$PWD"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
  echo "ERROR: Could not find the last30days skill root with scripts/last30days.py" >&2
  exit 1
fi

for py in python3.14 python3.13 python3.12 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || continue
  LAST30DAYS_PYTHON="$py"
  break
done

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  echo "ERROR: last30days requires Python 3.12+. Install python3.12 or newer and rerun." >&2
  exit 1
fi
```

Prefer `uv` when available so the `requests` dependency is available without mutating the user's Python environment:

```bash
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py --diagnose
```

If `uv` is not available, verify `requests` before running the CLI:

```bash
"$LAST30DAYS_PYTHON" -c 'import requests' || "$LAST30DAYS_PYTHON" -m pip install --user 'requests>=2.32,<3'
```

## Default Command

Use the user's topic as positional arguments:

```bash
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --emit=compact
```

Without `uv`:

```bash
"$LAST30DAYS_PYTHON" "$SKILL_ROOT/scripts/last30days.py" "TOPIC" --emit=compact
```

## First Run

Configuration lives at `~/.config/last30days/.env`.

Before researching, run `--diagnose` if the config file is missing or the user asks about setup. Do not require setup for every task: Reddit public JSON, Hacker News, Polymarket, and GitHub can work with little or no configuration.

Useful setup commands:

```bash
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py setup --openclaw
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py setup --github
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py setup
```

Before running any setup path that scans browser cookies for X/Twitter, get explicit user consent. Cookies are read live and are not saved to disk by the skill, but the action is still sensitive.

If the user provides keys, write them to `~/.config/last30days/.env`:

```bash
mkdir -p "$HOME/.config/last30days"
printf '%s\n' 'SCRAPECREATORS_API_KEY=...' >> "$HOME/.config/last30days/.env"
printf '%s\n' 'SETUP_COMPLETE=true' >> "$HOME/.config/last30days/.env"
```

## Source Configuration

The CLI degrades gracefully based on available credentials:

- No key: Reddit public JSON, Hacker News, Polymarket, and GitHub via `gh` when available.
- X/Twitter: `FROM_BROWSER=auto`, or `XAI_API_KEY`, or `AUTH_TOKEN` plus `CT0`.
- YouTube: `yt-dlp` installed locally.
- TikTok, Instagram, Threads, Pinterest, YouTube comments, Reddit backup: `SCRAPECREATORS_API_KEY`; opt into extra sources with `INCLUDE_SOURCES=tiktok,instagram`.
- Bluesky: `BSKY_HANDLE` plus `BSKY_APP_PASSWORD`.
- Web search: `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, or `PARALLEL_API_KEY`.
- Perplexity Sonar via OpenRouter: `OPENROUTER_API_KEY` and `INCLUDE_SOURCES=perplexity`; add `--deep-research` only when the user wants exhaustive research.
- Planning and reranking: `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, or deterministic local fallback.

## Useful Commands

```bash
# Fast iteration
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --quick

# Full JSON for downstream processing
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --emit=json

# Restrict sources
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --search=reddit,x,grounding

# Higher recall
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --deep

# Save complete output to disk
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --save-dir "$HOME/Documents/Last30Days"

# Persist ranked findings to the local SQLite store
cd "$SKILL_ROOT" && uv run --with 'requests>=2.32,<3' --python "$LAST30DAYS_PYTHON" python scripts/last30days.py "TOPIC" --store
```

## Query Guidance

- For "X vs Y" comparisons, run one query with both entities instead of separate runs unless the user asks for exhaustive side-by-side research.
- For people, brands, products, or companies, resolve likely X handles and GitHub users when possible, then pass `--x-handle=HANDLE` or `--github-user=USER`.
- For source-specific requests, use `--search=` rather than filtering after the fact.
- Use `--quick` when the user wants a fast answer or when iterating on query wording.
- Use `--deep` or `--deep-research` only when the user asks for high recall, serious investigation, or broad citations.

## Synthesis Guidance

Synthesize across sources. Do not answer with a source-by-source dump.

Lead with the pattern that best answers the user. Ground claims in the actual results, especially exact product names, handles, subreddit names, quote fragments, odds, engagement counts, and dates.

Source weighting, highest signal first:

1. Cross-source corroboration.
2. Reddit top comments and high-upvote threads.
3. YouTube transcript highlights.
4. X/Twitter posts from relevant handles.
5. Polymarket odds and movement.
6. TikTok and Instagram creator signal.
7. Hacker News discussion.
8. Web search and editorial sources.

For recommendations or "best X" questions, extract specific names and rank by repeated mentions plus engagement. For comparison queries, include a quick verdict, entity-specific strengths and weaknesses, a compact table when useful, and a bottom line.

If results are thin, stale, contradictory, or low quality, say that clearly instead of filling gaps from memory.

## Security

This skill reads public/platform data, calls configured provider APIs, optionally reads browser cookies only after consent for X/Twitter search, and can write local config plus optional local research outputs. It does not post, like, follow, modify platform content, or intentionally expose API keys in output.
