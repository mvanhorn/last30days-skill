# obsidian2date

**Research the last 30 days. Land it cleanly in Obsidian.**

`obsidian2date` is a public fork of
[mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill).
The upstream multi-source research engine is kept intact. This fork adds a
vault-native export path so every run becomes:

- a durable **run note** with YAML frontmatter and an evidence index
- a short **briefing** for daily skimming
- **wikilinks** to related prior runs
- an auto-updated **Index** + **Dashboard**

MIT licensed. Upstream copyright retained. No tracking.

## Why this fork

last30days is excellent at *finding* what people are saying across Reddit, X,
YouTube, HN, GitHub, Polymarket, and more. `obsidian2date` is opinionated about
what happens next: the research should become **linked knowledge in your vault**,
not a one-off chat dump.

## Quick start

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

# optional: point at your vault
export OBSIDIAN2DATE_VAULT="$HOME/Desktop/brain-paul"

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian
```

If `OBSIDIAN2DATE_VAULT` / `LAST30DAYS_OBSIDIAN_VAULT` is unset, the exporter
tries `~/Desktop/brain-paul` when that directory exists. Override anytime:

```bash
python3 skills/last30days/scripts/last30days.py "topic" \
  --emit=obsidian \
  --obsidian-vault /path/to/vault
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

## Agent skill entrypoints

| Path | Role |
| --- | --- |
| [`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) | Vault-first skill (this fork's default) |
| [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md) | Full upstream research skill / doctor / setup |

Install whichever skill your harness loads (Claude Code marketplace, Codex,
OpenClaw, plain checkout, etc.). For vault work, load `obsidian2date`.

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
