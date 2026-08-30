---
name: obsidian2date
version: "0.1.0"
description: "Research what people actually say about any topic in the last 30 days, then write a durable Obsidian run note, briefing, index entry, and related-note links into your vault. Built on the last30days research engine."
argument-hint: 'obsidian2date nvidia earnings reaction | obsidian2date AI video tools | obsidian2date what users want in react'
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

Fork of [last30days](https://github.com/mvanhorn/last30days-skill) focused on **durable Obsidian capture**.

The research engine remains upstream-compatible under `skills/last30days/`.
This skill is the vault-first entrypoint: research a topic, write notes, link related prior runs, and surface a short briefing.

**Vault contract (read when writing to brain-paul):**
`/Users/paulschwarz/Desktop/brain-paul/90_Quellen/obsidian2date/AGENT-DEKLARATION.md`

Default vault: `/Users/paulschwarz/Desktop/brain-paul`.
Writes only under `90_Quellen/obsidian2date/{runs,briefings,Index,Dashboard}` — never into `00_Prime/`, `10_Projekte/`, or `70_Archiv/` without an explicit user order.

## Default command

Resolve `SKILL_DIR` to the directory that contains **this** `SKILL.md`.
The engine still lives next door:

```bash
ENGINE="$SKILL_DIR/../last30days/scripts/last30days.py"
python3 "$ENGINE" "<topic>" --emit=obsidian --obsidian-vault "$VAULT"
```

If `OBSIDIAN2DATE_VAULT` (or `LAST30DAYS_OBSIDIAN_VAULT`) is set, `--obsidian-vault` can be omitted.
If neither is set, the engine tries `~/Desktop/brain-paul` when that directory exists.

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
3. **Vault path:** ask once if no vault can be resolved; then pin `OBSIDIAN2DATE_VAULT` for the session.
4. **Keys are optional.** Reddit / HN / Polymarket / GitHub / Web work keyless. X needs browser cookies or a backend; TikTok/IG need ScrapeCreators.
5. **Synthesis:** the Obsidian notes already contain a structured briefing and evidence index. Do **not** invent citations beyond what the engine returned. You may add a short German prose summary *after* the engine output if the user wants narrative.
6. **Upstream skill:** for doctor/setup/deep source debugging, fall back to `skills/last30days/SKILL.md`.

## Quick examples

```bash
# Vault-native research
python3 "$ENGINE" "local LLM agent frameworks" --emit=obsidian

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
