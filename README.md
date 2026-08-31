# obsidian2date

**Research the last 30 days. Keep the useful parts in Obsidian.**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` researches Reddit, X, YouTube, HN, GitHub, Polymarket, and the
web, then turns each run into durable, linked Obsidian notes. It is a public,
MIT-licensed fork of [last30days-skill](https://github.com/mvanhorn/last30days-skill);
the upstream research engine stays intact.

Each Obsidian run produces:

- a source-backed **run note**
- a compact **briefing**
- `[[wikilinks]]` to related runs
- an updated **Index** and **Dashboard**

No tracking.

## Quick start

Requires Python 3.12+.

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

## Use it in an agent

Load [`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) in your
Agent Skills-compatible host. The upstream skill remains available at
[`skills/last30days/SKILL.md`](skills/last30days/SKILL.md) for the original
workflow and setup.

For a direct scripted run, use the CLI below. For key setup and all available
backends, see [`CONFIGURATION.md`](CONFIGURATION.md).

## Upstream modes still work

```bash
# original compact synthesis envelope
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# agent JSON
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# production brief
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## Sources & keys

Same floor as upstream:

- **Keyless by default:** Reddit, Hacker News, Polymarket, GitHub, Web
- **Optional:** X (browser cookies / backends), YouTube (`yt-dlp`), TikTok/IG
  (ScrapeCreators), plus other paid/opt-in backends

See upstream [`CONFIGURATION.md`](CONFIGURATION.md) for the full matrix.

## Python

Requires Python ≥ 3.12 (3.13 verified for the Obsidian export tests).

```bash
PYTHONPATH=skills/last30days/scripts python3 -m unittest tests.test_obsidian_export -v
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
