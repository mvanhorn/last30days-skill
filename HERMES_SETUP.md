# Hermes Setup Guide for last30days

This guide covers installing last30days on Hermes AI Agent.

## Prerequisites

1. **Hermes installed** - See https://github.com/NousResearch/hermes-agent
2. **Python 3.12+** - `brew install python@3.12` or similar
3. **yt-dlp** (optional, for YouTube) - `brew install yt-dlp`

## Installation

```bash
hermes skills install mvanhorn/last30days-skill/skills/last30days --force
```

Hermes's install-time scanner rates this skill `caution`, not `dangerous`: it flags ordinary things a research tool does, such as reading its own config from environment variables and running `yt-dlp` with `subprocess`. Hermes blocks a `caution` community skill unless you pass `--force`. Run `hermes skills list` to confirm it shows up. A session that is already open needs `/reload-skills` (or a new session) to pick it up.

Use the full `owner/repo/skills/last30days` path shown above. Do not install from a raw `SKILL.md` URL: that route copies only the files `SKILL.md` names directly and leaves out most of `scripts/lib/`, so research runs fail. The shorter `hermes skills install mvanhorn/last30days-skill` goes through the skills.sh index, which has served stale snapshots of this repo before ([vercel-labs/skills#1602](https://github.com/vercel-labs/skills/issues/1602)).

If the install is ever blocked again (`dangerous` verdict, "--force does not override a dangerous verdict"), please open an issue with the scan output, and copy the skill in by hand meanwhile:

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
mkdir -p ~/.hermes/skills/research
cp -r last30days-skill/skills/last30days ~/.hermes/skills/research/
```

That deploys the skill straight from this repo's default branch to `~/.hermes/skills/research/last30days` (with named profiles the skills root is `~/.hermes/profiles/<name>/skills/`).

### Developer / live-edit alternative

If you're hacking on the skill locally and want edits to propagate to Hermes without re-copying, symlink your working tree instead of `cp`:

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
mkdir -p ~/.hermes/skills/research
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.hermes/skills/research/last30days
```

## Usage

In Hermes, invoke with:

```
last30days "your research topic"
```

Or with options:
```
last30days "best mechanical keyboards 2025" --search=reddit,youtube
last30days "AI news" --days=7 --deep
```

## First Run Setup

On first run, the skill will guide you through setup:

1. **Auto setup** (~30 seconds)
   - Scans browser cookies for X/Twitter
   - Checks/installs yt-dlp for YouTube
   - Best-effort install of `digg-pp-cli` for Digg story clusters (via `@mvanhorn/printing-press-library`; binary lands in `$HOME/.local/bin` — ensure your Hermes gateway PATH includes it, or Digg stays off even after install)
   - Configures free sources (Reddit, HN, Polymarket)

2. **Optional: ScrapeCreators**
   - Adds TikTok, Instagram, Reddit backup
   - 100 free credits (no expiration)
   - Sign up at scrapecreators.com

3. **Optional: API Keys**
   - XAI_API_KEY for X/Twitter (alternative to browser cookies)
   - BRAVE_API_KEY for web search

## Available Sources

### Free (No API Key)
- **Reddit** - Public discussions and comments
- **Hacker News** - Tech discussions via Algolia
- **Polymarket** - Prediction markets
- **YouTube** - Search and transcripts (requires yt-dlp)
- **Digg** - story clusters from every Digg topic, ranked by Digg Score (requires `digg-pp-cli` on the agent PATH; auto-installed to `$HOME/.local/bin` during setup when `npx` is available)

### Requires API Key
- **X/Twitter** - xAI API key or browser cookies
- **TikTok** - ScrapeCreators API
- **Instagram** - ScrapeCreators API
- **Web Search** - Brave Search API

## Troubleshooting

### `rich.errors.MarkupError` during install

Hermes prints the skill scan report through `rich` with markup on, and that report quotes scanned
source lines. When a quoted line looks like a rich tag — `[/?#\s)\]` in
`skills/last30days/scripts/lib/resolve.py`'s `_PROSE_SEG_END` regex does — the install aborts
right after `Running security scan...`:

```
rich.errors.MarkupError: closing tag '[/?#\s)\]' at position 9778 doesn't match any open tag
```

The scan itself is fine; only the report failed to print, so no verdict ever appears. This is a
Hermes CLI bug (`hermes_cli/skills_hub.py` prints that report with `markup` left on), not a
problem with this skill. Use the hand-copy or symlink path above in the meantime, and report the
traceback to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues) if
your Hermes still crashes after an update.

### Python not found
```bash
# Find Python 3.12+
which python3.12 python3.13 python3.14

# If not installed
brew install python@3.12
```

### yt-dlp not found
```bash
brew install yt-dlp
# or
pip install yt-dlp
```

### Check what's configured
```bash
cd ~/.hermes/skills/research/last30days
python3.12 scripts/last30days.py --diagnose
```

## Updating

Re-run the install command. `--force` replaces the installed copy with the current version:

```bash
hermes skills install mvanhorn/last30days-skill/skills/last30days --force
```

`hermes skills update last30days` reports "No updates available" for a copy that was put in by hand (`git clone` + `cp`), and it reinstalls without `--force`, so Hermes blocks the `caution` verdict. Use the install command instead. If an older copy was put in by hand, remove that folder first (for example `~/.hermes/skills/research/last30days`). To keep copying by hand instead:

```bash
cd last30days-skill && git pull
cp -r skills/last30days ~/.hermes/skills/research/
```

If you symlinked your working tree (developer alternative above), just `git pull` in the repo; edits propagate live.

## Support

- Original repo: https://github.com/mvanhorn/last30days-skill
- Hermes: https://github.com/NousResearch/hermes-agent
- Issues: Please report in the original repo
