# Hermes Setup Guide for last30days

This guide covers installing last30days on Hermes AI Agent.

## Prerequisites

1. **Hermes installed** - See https://github.com/NousResearch/hermes-agent
2. **Python 3.12+** - `brew install python@3.12` or similar
3. **yt-dlp** (optional, for YouTube) - `brew install yt-dlp`

## Installation

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
mkdir -p ~/.hermes/skills/research
cp -r last30days-skill/skills/last30days ~/.hermes/skills/research/
```

That deploys the skill straight from this repo's current default branch to `~/.hermes/skills/research/last30days` (with named profiles the skills root is `~/.hermes/profiles/<name>/skills/`). Run `hermes skills list` to confirm it shows up; a session already open needs `/reload-skills` (or a new session) to pick it up.

> **Why not `hermes skills install`?** The installer is currently blocked for this skill. Hermes's install-time security scanner flags benign patterns here — reading your own API keys from the environment (`os.environ.get("XAI_API_KEY")` etc.) and calling `subprocess` to run `yt-dlp`/`bird` — and returns a `dangerous` verdict (19 findings). `--force` only overrides a `caution` verdict; for community sources a `dangerous` verdict is a hard block that `--force` cannot bypass:
>
> ```bash
> hermes skills install mvanhorn/last30days-skill/skills/last30days --force
> # → Installation blocked: Blocked (community source + dangerous verdict, 19 findings)
> ```
>
> The `git clone` + `cp` path above sidesteps the installer and is the supported workaround until the scanner rules or this skill's flagged patterns change. (The shorter `hermes skills install mvanhorn/last30days-skill` additionally resolves through the skills.sh index, which was serving a stale snapshot — tracked in [vercel-labs/skills#1602](https://github.com/vercel-labs/skills/issues/1602).)

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
   - Best-effort install of `digg-pp-cli` for Digg AI-news clusters (via `@mvanhorn/printing-press-library`; binary lands in `$HOME/.local/bin` — ensure your Hermes gateway PATH includes it, or Digg stays off even after install)
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
- **Digg** - AI-news story clusters (requires `digg-pp-cli` on the agent PATH; auto-installed to `$HOME/.local/bin` during setup when `npx` is available)

### Requires API Key
- **X/Twitter** - xAI API key or browser cookies
- **TikTok** - ScrapeCreators API
- **Instagram** - ScrapeCreators API
- **Web Search** - Brave Search API

## Troubleshooting

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

If you symlinked your working tree (developer alternative above), just `git pull` in the repo — edits propagate live, no re-install step. With a `cp` install, pull and re-copy:

```bash
cd last30days-skill && git pull
cp -r skills/last30days ~/.hermes/skills/research/
```

`hermes skills install mvanhorn/last30days-skill --force` remains blocked by the scanner verdict above; retry it occasionally in case the flagged patterns or scanner rules change.

## Support

- Original repo: https://github.com/mvanhorn/last30days-skill
- Hermes: https://github.com/mercurial-tf/hermes
- Issues: Please report in the original repo
