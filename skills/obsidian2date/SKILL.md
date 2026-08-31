---
name: obsidian2date
version: "0.1.0"
description: "Research what people actually say about any topic over any recent window (default 30 days, --days N for anything else), then write a durable Obsidian run note, briefing, index entry, and related-note links into your vault. Built on the last30days research engine."
argument-hint: 'obsidian2date nvidia earnings reaction | obsidian2date last 7 days of AI video tools | obsidian2date what users want in react over the last 90 days'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
homepage: https://github.com/pauleschwarz/obsidian2date
repository: https://github.com/pauleschwarz/obsidian2date
author: pauleschwarz
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "🗂"
    requires:
      env: []
      optionalEnv:
        - OBSIDIAN2DATE_VAULT
        - LAST30DAYS_OBSIDIAN_VAULT
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
      bins:
        - node
        - python3
    primaryEnv: OBSIDIAN2DATE_VAULT
    files:
      - "../last30days/scripts/*"
    homepage: https://github.com/pauleschwarz/obsidian2date
    tags:
      - research
      - obsidian
      - vault
      - briefing
      - multi-source
      - reddit
      - youtube
      - hackernews
      - github
---

# obsidian2date

Research what people actually said about a topic over any recent window and keep the useful evidence in Obsidian. The window follows the request: say "last week", "the last 7 days", or "over the last 90 days" — the default is 30 days.
This is the vault-first entrypoint for the public
[obsidian2date](https://github.com/pauleschwarz/obsidian2date) fork of
[last30days](https://github.com/mvanhorn/last30days-skill).

The upstream research engine remains under `skills/last30days/`.

## Run

1. Get the topic from the user's request.
2. Resolve the vault from an explicit `--obsidian-vault` path, then
   `OBSIDIAN2DATE_VAULT`, then `LAST30DAYS_OBSIDIAN_VAULT`, then an existing
   `~/Desktop/brain-paul`. Environment and desktop candidates must already be
   directories; an explicit missing path is the requested export target. If no
   path resolves, ask the user for one before writing (the CLI otherwise raises
   `No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.`).
3. Derive the time window from the user's request (default 30 days; "last
   week" or "last 7 days" -> `--days 7`, "last 90 days" -> `--days 90`). The
   engine accepts `--days N` and `--as-of YYYY-MM-DD`; 30 is only the default.
4. Run the existing research engine with `--emit=obsidian`, the resolved
   vault, and the requested window, preserving the engine's normal source and
   fallback behavior.
5. Report the briefing path, run-note path, and any partial or unavailable
   sources honestly.

Example from the repository root:

```bash
python3 skills/last30days/scripts/last30days.py "topic" \
  --emit=obsidian \
  --obsidian-vault /path/to/your/vault
```

## Vault contract

Write only under `90_Quellen/obsidian2date/`:

- `runs/` contains source-backed research notes.
- `briefings/` contains compact briefings for daily review.
- `Index.md` and `Dashboard.md` are maintained by the exporter.

Never overwrite an existing note. The exporter adds a numeric suffix on
filename collisions and links related prior runs with Obsidian `[[wikilinks]]`.
Do not write API keys or other sensitive values into notes.

## Upstream mode

Use `skills/last30days/SKILL.md` for the original non-Obsidian workflow. Keep
changes to the research engine additive and upstream-compatible.

## What gets written

Under the vault root (defaults shown):

| Path | Purpose |
| --- | --- |
| `90_Quellen/obsidian2date/runs/YYYY-MM-DD-<slug>.md` | Durable research run note with frontmatter, briefing bullets, evidence index |
| `90_Quellen/obsidian2date/briefings/YYYY-MM-DD-<slug>-briefing.md` | Short daily skim note linking back to the run |
| `90_Quellen/obsidian2date/Index.md` | Newest-first run index |
| `90_Quellen/obsidian2date/Dashboard.md` | Newest-first briefing dashboard |

Notes never overwrite each other. Same-day collisions get `-1`, `-2`, … suffixes.
Related prior runs are linked with Obsidian `[[wikilinks]]` when token overlap is found.

## Agent contract

1. **Do not improvise research.** Run the engine. Pass its stdout through.
2. **Prefer `--emit=obsidian`** for this skill. Use upstream modes (`compact`, `json`, `brief`, …) only when the user explicitly asks.
3. **Vault path:** ask once if no vault can be resolved; then pin `OBSIDIAN2DATE_VAULT` for the session. An empty or whitespace-only vault environment value deliberately disables implicit fallback.
4. **Keys are optional.** Reddit / HN / Polymarket / GitHub / Web work keyless. X needs browser cookies or a backend; TikTok/IG need ScrapeCreators.
5. **Synthesis:** the Obsidian notes already contain a structured briefing and evidence index. Do **not** invent citations beyond what the engine returned. You may add a short German prose summary *after* the engine output if the user wants narrative.
6. **Upstream skill:** for doctor/setup/deep source debugging, fall back to `skills/last30days/SKILL.md`.

## Quick examples

```bash
# Vault-native research, default 30-day window
python3 "$ENGINE" "local LLM agent frameworks" --emit=obsidian

# Any window: last week, or a 90-day sweep
python3 "$ENGINE" "AI video tools" --emit=obsidian --days 7
python3 "$ENGINE" "rust async runtimes" --emit=obsidian --days 90

# Explicit vault
python3 "$ENGINE" "obsidian plugins 2026" --emit=obsidian \
  --obsidian-vault "$HOME/Desktop/brain-paul"

# Keyless free sources only
python3 "$ENGINE" "rust async runtime" --emit=obsidian --search reddit,hackernews,github,web

# Upstream compact (no vault write)
python3 "$ENGINE" "topic" --emit=compact
```

## Setup notes

- Python ≥ 3.12 recommended (3.13 works).
- Optional: copy upstream `.env` patterns from `CONFIGURATION.md`.
- MIT license. Upstream copyright retained in `LICENSE`. This fork adds the Obsidian export path and branding.
