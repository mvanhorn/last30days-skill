# Hermes Cron Setup for last30days

> The SessionStart hook (`hooks/hooks.json`) is Claude Code-specific and does not fire on Hermes.
> Use a Hermes cron job as the equivalent auto-start mechanism.

## What the SessionStart hook does

The Claude Code hook runs `hooks/scripts/check-config.sh` on every session start. It:
1. Creates `$LAST30DAYS_MEMORY_DIR` (defaults to `~/Documents/Last30Days`)
2. Shows configuration status (active sources, last run summary)
3. Detects first-run and shows welcome message

## Hermes equivalent: cron job

On Hermes, create a cron job that runs the check on a schedule:

```
# One-time: create the memory directory
mkdir -p ~/Documents/Last30Days

# One-time: check and report config status
bash hooks/scripts/check-config.sh
```

### Option A: Daily health check cron

```bash
hermes cron create --schedule "0 9 * * *" --prompt "Run 'bash skills/research/last30days/hooks/scripts/check-config.sh' and report the result."
```

### Option B: Manual first-run detection

The first-run detection is handled by the SKILL.md Step 0 setup wizard. The engine automatically detects `~/.config/last30days/.env` and runs the wizard on first invocation. No cron needed.

### Option C: Background directory creation

On Hermes Desktop, the agent will create `$LAST30DAYS_MEMORY_DIR` automatically when running the skill. The directory creation is handled by the engine's save logic — the hook's `mkdir -p` is a convenience, not a requirement.

## What's NOT needed on Hermes

- The permission check (`chmod 600`) — Windows uses ACLs, not POSIX permissions
- The Keychain integration — Windows uses Credential Manager (handled separately)
- The config status message — the skill reports available sources in its own output
