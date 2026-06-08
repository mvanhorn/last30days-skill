# Hermes Setup Guide for last30days

This guide covers installing last30days on Hermes AI Agent.

## Prerequisites

1. **Hermes installed** - See https://github.com/mercurial-tf/hermes
2. **Python 3.12+** - `brew install python@3.12` or similar
3. **yt-dlp** (optional, for YouTube) - `brew install yt-dlp`

## Installation

```bash
hermes skills install mvanhorn/last30days-skill --force
```

This pulls the latest release from GitHub and deploys to `~/.hermes/skills/research/last30days/`. `--force` reinstalls over any existing copy.

### Developer / live-edit alternative

If you're hacking on the skill locally and want edits to propagate to Hermes without re-installing, symlink your working tree:

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

```bash
hermes skills install mvanhorn/last30days-skill --force
```

If you symlinked your working tree (developer alternative above), just `git pull` in the repo — edits propagate live, no re-install step.

## Support

- Original repo: https://github.com/mvanhorn/last30days-skill
- Hermes: https://github.com/mercurial-tf/hermes
- Issues: Please report in the original repo
