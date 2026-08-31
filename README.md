# obsidian2date

**Research any recent window. Keep the useful parts in Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` researches what people actually say about a topic across
Reddit, X, YouTube, HN, GitHub, Polymarket, and the web — over whatever window
you ask for (last week, last 7 days, last 90 days; 30 days is just the
default) — and turns each run into durable, linked Obsidian notes.

Each run produces:

- a source-backed **run note**
- a compact **briefing**
- `[[wikilinks]]` to related runs
- an updated **Index** and **Dashboard**

No tracking. MIT. Public fork of
[last30days-skill](https://github.com/mvanhorn/last30days-skill); the upstream
research engine stays mergeable. Requires Python 3.12+ and an Obsidian vault;
sources and API keys are optional — see
[CONFIGURATION.md](CONFIGURATION.md).

## Use it as a slash command (primary path)

`obsidian2date` is an Agent Skill: install the repo once, then just type
`/obsidian2date <topic>` in your agent. The skill runs the research engine,
resolves your vault, writes the notes, and reports the paths. No flags to
memorize — say "last week" or "over the last 90 days" in the request and the
skill translates it into the right engine flags.

| Host | Install | Then |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y` (or add this repo as a `.claude-plugin`) | `/obsidian2date <topic>` |
| Codex | repo ships `.codex-plugin/plugin.json` | `/obsidian2date <topic>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <topic>` |
| Gemini CLI | repo ships `gemini-extension.json` | `/obsidian2date <topic>` |
| OpenClaw / agents.md hosts | repo ships `.agents/` manifest | `/obsidian2date <topic>` |
| pi / any skills-capable agent | symlink or copy `skills/obsidian2date/` into the agent's skills dir | `/obsidian2date <topic>` |

What the skill does on each run (see
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) — the
canonical runtime spec the model reads):

1. resolve your vault (ask once, then remember for the session)
2. derive the window from your request (default 30 days)
3. run the research engine with `--emit=obsidian`
4. report briefing path, run-note path, and any partial or unavailable sources honestly

## Quick start (CLI fallback)

For scripting, cron, or dev-time engine testing, call the CLI directly. This
is the fallback path, not the primary one — the slash command above is the
product.

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

`30` days is only the default. Ask for anything:

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # last week
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # quarter sweep
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

In the slash command, just say it: "research the last 7 days of AI video
tools".

### Vault resolution

The export target is resolved in this order:

1. `--obsidian-vault PATH` (an explicit missing path is created for the export)
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. an existing `~/Desktop/brain-paul`

Environment and desktop candidates must already be directories. A present empty
or whitespace-only vault environment value intentionally disables implicit
fallbacks. If nothing resolves, the command stops with:

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

Use `~/...` or an absolute path in `.env` files; `$HOME` is not expanded there.
Existing notes are never overwritten; filename collisions get a numeric suffix.

## What gets written

Default layout under the vault root:

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

Notes never overwrite. Same-day collisions get numeric suffixes.
Related prior runs are linked via Obsidian `[[wikilinks]]` when token overlap
is detected.

## Sources & keys

Same floor as upstream:

- **Keyless by default:** Reddit, Hacker News, Polymarket, GitHub, Web
- **Optional:** X (browser cookies / backends), YouTube (`yt-dlp`), TikTok/IG
  (ScrapeCreators), plus other paid/opt-in backends

See [`CONFIGURATION.md`](CONFIGURATION.md) for the full matrix and key setup.

## Safe diagnostics

Run a permission-only check before research:

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` is safe: it runs **without reading cookies, writing files, or running research**.
For troubleshooting sources or installed backends, use the health check instead:

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## Upstream modes still work

```bash
# original compact synthesis envelope
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# agent JSON
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# production brief
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Relationship to upstream

| Concern | Policy |
| --- | --- |
| Research engine | Stay mergeable with `upstream/main` |
| Obsidian export | Additive module: `lib/obsidian_export.py` |
| Branding / skill | `obsidian2date` |
| License | MIT; keep upstream copyright notices |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## Credits

- Upstream research engine: [Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Obsidian export path + public fork packaging: [pauleschwarz](https://github.com/pauleschwarz)

## License

MIT. See [LICENSE](LICENSE).
