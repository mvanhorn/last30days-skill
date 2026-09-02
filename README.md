# obsidian2date

**Research any recent window. Keep the useful parts in Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` turns a topic + time window into **durable, linked Obsidian
notes** — sourced from what people actually say across Reddit, X, YouTube, HN,
GitHub, Polymarket, and the open web.

- **Window is yours:** last week, 7 days, 90 days, or a dated slice. **30 days
  is only the default**, not the product name.
- **Primary UX:** a multi-harness **Agent Skill** — type
  `/obsidian2date <topic>` and get paths back. CLI is the fallback for cron and
  debugging.
- **Fork with a mergeable engine:** public fork of
  [last30days-skill](https://github.com/mvanhorn/last30days-skill); upstream
  research engine stays mergeable. Obsidian export and vault UX are the
  opinionated layer on top.

Requires **Python 3.12+** and an **Obsidian vault**. API keys are optional —
runs degrade cleanly when a source is unavailable. Full knobs:
[CONFIGURATION.md](CONFIGURATION.md). Vocabulary: [CONCEPTS.md](CONCEPTS.md).

## What you get each run

| Artifact | What it is |
| --- | --- |
| **Run note** | Source-backed note for this topic + window |
| **Briefing** | Compact synthesis you can read in one sitting |
| **Wikilinks** | `[[links]]` to related prior runs |
| **Index + Dashboard** | Updated navigation over the research corpus |

No tracking. MIT.

## When to use / not use

| Use when | Skip when |
| --- | --- |
| You want a **citable** sweep into a vault you own | You need a live chat answer with no files |
| You re-research topics and want **compounding** notes | You only want a one-off web search |
| An agent should run research **without you babysitting flags** | You need guaranteed coverage of paywalled sources |

## Use it as a slash command (primary path)

Install the skill once, then type `/obsidian2date <topic>`. Say the window in
natural language ("last week", "over the last 90 days"); the skill maps that to
engine flags.

| Host | Install | Then |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (or add this repo as a `.claude-plugin`) | `/obsidian2date <topic>` |
| Codex | repo ships `.codex-plugin/plugin.json` | `/obsidian2date <topic>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <topic>` |
| Gemini CLI | repo ships `gemini-extension.json` | `/obsidian2date <topic>` |
| OpenClaw / agents.md hosts | repo ships `.agents/` manifest | `/obsidian2date <topic>` |
| pi / skills-capable agents | symlink or copy `skills/obsidian2date/` into the agent's skills dir | `/obsidian2date <topic>` |

Each run the skill (canonical contract:
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md)):

1. resolves your vault (ask once, remember for the session)
2. derives the window (default **30** days)
3. runs the research engine with `--emit=obsidian`
4. reports briefing path, run-note path, and any partial / unavailable sources
   honestly

## Quick start (CLI fallback)

For scripting, cron, or engine debugging — not the main product path:

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /path/to/your/vault
```

Or configure the vault once:

```bash
export OBSIDIAN2DATE_VAULT=/path/to/your/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### Time window

```bash
# last week
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7
# quarter sweep
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90
# fixed end date
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

In the slash command, just say it: `research the last 7 days of AI video tools`.

### Vault resolution (order)

1. `--obsidian-vault PATH` (explicit missing path may be created for export)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. existing `~/Desktop/brain-paul` (legacy convenience)

Environment and desktop candidates must already exist unless you pass an
explicit `--obsidian-vault` you intend to create. Details:
[CONFIGURATION.md](CONFIGURATION.md).

## Sources at a glance

The engine fans out across public and optional authenticated sources. **No key
is required to install or to get a partial run.**

| Source family | Typical need | If missing |
| --- | --- | --- |
| HN, web fallbacks, public feeds | often none | reduced coverage |
| Reddit / X / YouTube / GitHub / Polymarket / search APIs | optional tokens per [CONFIGURATION.md](CONFIGURATION.md) | source marked unavailable; other sources still write |

Exact env vars, rate limits, and degrade behavior live in
[CONFIGURATION.md](CONFIGURATION.md) — keep secrets out of the vault and out
of git.

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| Skill can't find a vault | Set `OBSIDIAN2DATE_VAULT` or pass `--obsidian-vault`; confirm the folder exists |
| Empty / thin briefing | Widen `--days`, check which sources reported unavailable, add optional keys only if you need that source |
| Rate limits / blocks | Re-run later; reduce concurrency in config; don't hammer a single backend |
| Notes landed in the wrong vault | Unset stale `LAST30DAYS_OBSIDIAN_VAULT` / desktop default; pass an explicit path once |
| Python errors on 3.11 | Use **3.12+** |

## Docs map

| Doc | Contents |
| --- | --- |
| [CONCEPTS.md](CONCEPTS.md) | Skill vs engine vs harness vocabulary |
| [CONFIGURATION.md](CONFIGURATION.md) | Env, keys, flags, vault behavior |
| [docs/](docs/README.md) | Search quality, reference, plans, releases |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup and PR expectations |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [HERMES_SETUP.md](HERMES_SETUP.md) | Hermes-oriented setup notes |
| [Plan: docs usability](docs/plans/2026-09-02-docs-usability.md) | Why this README looks like this |

## Locales

**English `README.md` is canonical.** Localized files (`README.de.md`,
`README.ja.md`, …) may lag; prefer EN when they conflict, then open a PR to
sync.

## Related tools

| Tool | Layer |
| --- | --- |
| **obsidian2date** (this) | Research window → vault notes |
| [pi-verity](https://github.com/pauleschwarz/pi-verity) | Deterministic proof that agent code changes match evidence |
| [visual-qa](https://github.com/pauleschwarz/visual-qa) | Autonomous explore/fix/prove on a running web app |

## License

MIT — [LICENSE](LICENSE).
