# last30days for Codex

Use `last30days` in OpenAI Codex CLI via native skill discovery.

## Quick Install

Tell Codex:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/mvanhorn/last30days-skill/main/.codex/INSTALL.md
```

That install doc clones the repo to `~/.agents/skills/last30days`, sets up `~/.config/last30days/.env`, runs `--diagnose`, and tells you when to restart Codex.

## Manual Install

### Prerequisites

- OpenAI Codex CLI
- Git
- Python 3
- `SCRAPECREATORS_API_KEY`

### Steps

1. Clone the repo:

   ```bash
   mkdir -p ~/.agents/skills
   git clone https://github.com/mvanhorn/last30days-skill.git ~/.agents/skills/last30days
   ```

2. Create `~/.config/last30days/.env`:

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

3. Verify the install:

   ```bash
   python3 ~/.agents/skills/last30days/scripts/last30days.py --diagnose
   ```

4. Restart Codex.

## How Codex Finds the Skill

Codex scans `~/.agents/skills/` at startup. Each skill lives in its own folder, and Codex parses the frontmatter in that folder's `SKILL.md` to discover the skill.

That frontmatter gives Codex the name, description, and invocation hints it uses for native skill discovery. This repo also includes `agents/openai.yaml` for Codex-specific display metadata.

## Env Vars

### Required

- `SCRAPECREATORS_API_KEY` — the main key for this skill. It powers Reddit + TikTok + Instagram and gives the best out-of-the-box coverage.

### Optional

- `OPENAI_API_KEY` — optional if you already use `codex login`; useful as a fallback or explicit override.
- `XAI_API_KEY` — X/Twitter search API fallback.
- `AUTH_TOKEN` + `CT0` — manual X cookie fallback if browser cookie detection is flaky.
- `PARALLEL_API_KEY`, `BRAVE_API_KEY`, `OPENROUTER_API_KEY` — native web search backends.
- `BSKY_HANDLE` + `BSKY_APP_PASSWORD` — enable Bluesky search.
- `TRUTHSOCIAL_TOKEN` — enable Truth Social search.
- `APIFY_API_TOKEN` — legacy fallback for older flows.

## Invoking

After restart, you can run the skill either way:

- `$last30days AI video tools`
- Open `/skills` and pick `last30days`

## Troubleshooting

### Skill not found

- Make sure the repo exists at `~/.agents/skills/last30days`
- Make sure `~/.agents/skills/last30days/SKILL.md` exists
- Restart Codex — skill discovery happens at startup

### Diagnose config and source availability

```bash
python3 ~/.agents/skills/last30days/scripts/last30days.py --diagnose
```

If `SCRAPECREATORS_API_KEY` is missing, Reddit + TikTok + Instagram will stay limited or unavailable.

### Still on an old version?

```bash
cd ~/.agents/skills/last30days && git pull
```

Then restart Codex.

## Updating

```bash
cd ~/.agents/skills/last30days && git pull
python3 ~/.agents/skills/last30days/scripts/last30days.py --diagnose
```

Restart Codex afterward if you want to force a clean reload.

## Uninstalling

```bash
rm -rf ~/.agents/skills/last30days
```

Optionally remove `~/.config/last30days/.env` too if you do not plan to reinstall.
