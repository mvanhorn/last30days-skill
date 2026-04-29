# last30days Skill

Claude Code skill for researching any topic across Reddit, X, YouTube, and web.
Python scripts with multi-source search aggregation.

## Structure
- `skills/last30days/SKILL.md` — canonical skill definition
- `skills/last30days/scripts/last30days.py` — main research engine
- `skills/last30days/scripts/lib/` — search, enrichment, rendering modules
- `skills/last30days/scripts/lib/vendor/bird-search/` — vendored X search client

## Orientation
- This is a Claude Code skill, not a CLI tool. `/last30days <topic>` is the product; `scripts/last30days.py` is implementation.
- Feature design starts from the slash-command UX. A new engine flag with no SKILL.md integration is incomplete.
- README and PR examples show `/last30days <topic>` first. Direct CLI is a fallback for scripting/cron; label it as such.
- Slash commands don't pass shell mechanics through. `/last30days OpenClaw --emit=html | pbcopy` is invalid; either use the slash form (no flags or pipes) or the direct CLI form (full `python3 ...`).

## Commands
```bash
python3 skills/last30days/scripts/last30days.py "test query" --emit=compact
bash skills/last30days/scripts/sync.sh
```

## Rules
- `lib/__init__.py` must be bare package marker (comment only, NO eager imports)
- `bash skills/last30days/scripts/sync.sh` — local-dev deploy only. Skip if the plugin is already installed via marketplace (creates duplicate skill entries).
- Git remote: origin = public (`mvanhorn/last30days-skill`)

## Beta channel

Experimental changes get tested on `mvanhorn/last30days-skill-private`, which installs as a parallel `/last30days-beta` slash command. Beta-only changes never ship to public without a review PR here. Workflow guide lives at `BETA.md` in the private repo. Plan that established this setup: `docs/plans/2026-04-17-005-feat-beta-skill-from-private-repo-plan.md`.
