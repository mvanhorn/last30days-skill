# Hermes Setup Guide for last30days

This guide covers installing last30days on Hermes AI Agent.

## Prerequisites

1. **Hermes installed** - See https://github.com/NousResearch/hermes-agent
2. **Python 3.12+** - `brew install python@3.12` or similar
3. **yt-dlp** (optional, for YouTube) - `brew install yt-dlp`

## Installation

Install a specific commit you have reviewed, not whatever the default branch points at right now.

### Option A: the Hermes installer (runs the Hermes security scan)

```bash
hermes skills install missterr/last30days-skill/skills/last30days
```

If the scanner reports findings, read them before you go further. If it blocks the install, don't work around the block: open an issue with the findings. Every executable file in the skill, including the vendored X client in `scripts/lib/vendor/`, is inside the scan scope. `.skillignore` excludes only images and agent metadata.

### Option B: a pinned, reviewed checkout

```bash
REVIEWED_SHA=<40-character commit SHA you reviewed>
git clone https://github.com/missterr/last30days-skill.git
git -C last30days-skill checkout --detach "$REVIEWED_SHA"
test "$(git -C last30days-skill rev-parse HEAD)" = "$REVIEWED_SHA" || { echo "SHA mismatch"; exit 1; }

# Optional: run the repo's own security contract tests (Python 3.12+, pytest)
(cd last30days-skill && python3 -m pytest -q tests/hermes tests/test_no_runtime_installs.py tests/test_vendor_integrity.py)

mkdir -p ~/.hermes/skills/research
cp -R last30days-skill/skills/last30days ~/.hermes/skills/research/
```

This copies the skill to `~/.hermes/skills/research/last30days`. With named profiles, the skills root is `~/.hermes/profiles/<name>/skills/` instead. Run `hermes skills list` to confirm the skill shows up. A session that is already open needs `/reload-skills`, or a new session, to pick it up.

### What runs on your machine

- **Setup installs one thing:** `yt-dlp` through Homebrew, on macOS, when it's missing. Setup never runs `npx`/`npm`, `curl | sh`, `git clone`, or `go install`, and never downloads or builds third-party CLIs. `tests/test_no_runtime_installs.py` enforces this.
- **Built in, no install needed:** Reddit, Hacker News, Polymarket, GitHub, arXiv, Techmeme, and web search.
- **Optional external binaries.** The skill runs these only if you installed them yourself and they're on `PATH`: `yt-dlp` (YouTube), `node` (runs the vendored, search-only X client; it reads credentials from its environment only), `digg-pp-cli` (Digg), `gh`, `grok`, `xurl`, `brightdata`.
- **Digg CLI (optional):** build it from a pinned, reviewed source commit with `bash ~/.hermes/skills/research/last30days/scripts/build-pp-cli.sh digg`. This needs Go and writes to `$HOME/.local/bin`. Your Hermes gateway's `PATH` must include that directory, or Digg stays off.
- **Browser cookies:** read only after you consent during setup. Set `BROWSER_CONSENT=false` to turn reads off.

### Developer / live-edit alternative

If you're hacking on the skill locally and want edits to reach Hermes without re-copying, symlink your working tree instead of copying it:

```bash
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

On first run, the skill walks you through setup:

1. **Auto setup** (~30 seconds)
   - Reads browser cookies for X/Twitter, only if you consent
   - Checks for yt-dlp and installs it with Homebrew if it's missing
   - Detects `digg-pp-cli` and never installs it. If it's missing, setup prints the `build-pp-cli.sh digg` command for you to run yourself.
   - Configures the free sources (Reddit, HN, Polymarket, arXiv, Techmeme)

2. **Optional: ScrapeCreators**
   - Adds TikTok, Instagram, and backup Reddit search
   - Sign up at scrapecreators.com

3. **Optional: API keys**
   - `XAI_API_KEY` for X/Twitter (an alternative to browser cookies)
   - `BRAVE_API_KEY` for web search

## Available Sources

### Free (no API key)
- **Reddit**: public discussions and comments
- **Hacker News**: tech discussions, via Algolia
- **Polymarket**: prediction markets
- **arXiv**: research papers (built in)
- **Techmeme**: tech headlines (built in)
- **YouTube**: search and transcripts (needs yt-dlp)
- **Digg**: AI-news story clusters (needs `digg-pp-cli` on the agent `PATH`; build it yourself with `scripts/build-pp-cli.sh digg`)

### Requires an API key
- **X/Twitter**: xAI API key or browser cookies
- **TikTok**: ScrapeCreators API
- **Instagram**: ScrapeCreators API
- **Web search**: Brave Search API

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

Don't `git pull` straight into an install. Review the change, then pin the new commit:

```bash
cd last30days-skill
git fetch origin
git log --stat HEAD..origin/main -- skills/        # review what changed
git diff HEAD..origin/main -- skills/
NEW_SHA=<the 40-character commit SHA you reviewed>
git checkout --detach "$NEW_SHA"
cp -R skills/last30days ~/.hermes/skills/research/
```

If you used the developer symlink, the working tree is the install, so review a change before you check it out.

## Support

- This fork: https://github.com/missterr/last30days-skill
- Upstream: https://github.com/mvanhorn/last30days-skill
- Hermes: https://github.com/NousResearch/hermes-agent
