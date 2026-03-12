# Installing last30days for Codex

Enable `last30days` in Codex via native skill discovery. Clone it into `~/.agents/skills/`, add your config, run a quick diagnose check, then restart Codex.

## Prerequisites

- OpenAI Codex CLI
- Git
- Python 3
- A `SCRAPECREATORS_API_KEY` from scrapecreators.com

## Installation

1. **Clone the repo into the Codex skills directory:**

   ```bash
   mkdir -p ~/.agents/skills
   git clone https://github.com/mvanhorn/last30days-skill.git ~/.agents/skills/last30days
   ```

2. **Create `~/.config/last30days/.env`:**

   ```bash
   mkdir -p ~/.config/last30days
   cat > ~/.config/last30days/.env <<'EOF'
   SCRAPECREATORS_API_KEY=...    # required - Reddit + TikTok + Instagram
   OPENAI_API_KEY=sk-...         # optional - not needed if you've already run `codex login`
   XAI_API_KEY=xai-...           # optional - X search API fallback
   PARALLEL_API_KEY=...          # optional - native web search
   BRAVE_API_KEY=...             # optional - native web search
   OPENROUTER_API_KEY=...        # optional - native web search
   AUTH_TOKEN=...                # optional - X cookie fallback
   CT0=...                       # optional - X cookie fallback
   BSKY_HANDLE=you.bsky.social   # optional - Bluesky search
   BSKY_APP_PASSWORD=xxxx-xxxx-xxxx  # optional - create at bsky.app/settings/app-passwords
   TRUTHSOCIAL_TOKEN=...         # optional - Truth Social search
   APIFY_API_TOKEN=...           # optional - legacy fallback
   EOF
   chmod 600 ~/.config/last30days/.env
   ```

## Verify

```bash
python3 ~/.agents/skills/last30days/scripts/last30days.py --diagnose
```

You should get JSON back showing which sources are available.

## Restart Codex

Quit and relaunch the CLI so it rescans `~/.agents/skills/`.

After restart, invoke the skill with `$last30days` or from the `/skills` menu.

## Updating

```bash
cd ~/.agents/skills/last30days && git pull
python3 ~/.agents/skills/last30days/scripts/last30days.py --diagnose
```

Restart Codex after updating if you want to force a fresh skill reload.

## Uninstalling

```bash
rm -rf ~/.agents/skills/last30days
```

If you also want to remove your local config, delete `~/.config/last30days/.env`.
