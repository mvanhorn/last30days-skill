# last30days Skill

Agent Skills package for researching any topic across Reddit, X, YouTube, and web. Installable across Claude Code (most common host), Codex, Cursor, GitHub Copilot, Gemini CLI, and 50+ other [Agent Skills](https://agentskills.io) hosts. Python scripts with multi-source search aggregation.

## Structure
- `skills/last30days/SKILL.md` — canonical skill definition / runtime spec the model reads when the slash command fires
- `skills/last30days/scripts/last30days.py` — main research engine
- `skills/last30days/scripts/lib/` — search, enrichment, rendering modules
- `skills/last30days/scripts/lib/vendor/bird-search/` — vendored X search client
- `docs/solutions/` — documented solutions to past problems (bugs, best practices, workflow patterns), organized by category with YAML frontmatter (`module`, `tags`, `problem_type`)
- `CONCEPTS.md` — shared domain vocabulary (Skill, Engine, Harness, Beta channel) — relevant when orienting to the codebase or discussing project terminology
- `CONFIGURATION.md` — user-facing knobs (env vars, flags, per-host install patterns); keep in sync per the rules below
- `CHANGELOG.md` — structured release history (launch copy lives in GitHub Releases)
- `HERMES_SETUP.md` — install instructions for the Hermes harness specifically

## Orientation
- This is an Agent Skills package, not a CLI tool. The product is the slash-command-invoked skill (`/last30days <topic>` in most harnesses); `scripts/last30days.py` is implementation. Claude Code is the most common host but not the only one — features must work across every harness the skill installs into.
- Feature design starts from the slash-command UX. A new engine flag with no SKILL.md integration is incomplete — the model invoking the skill won't know the flag exists.
- README and PR examples show `/last30days <topic>` first. Direct CLI invocation (`python3 scripts/last30days.py ...`) is a fallback for scripting, cron, and dev-time engine testing; label it as such, never as the primary path.
- Slash commands don't pass shell mechanics through. `/last30days OpenClaw --emit=html | pbcopy` is invalid in any harness — either use the slash form (no flags or pipes; let the model translate user intent into engine flags) or use the direct CLI form (full `python3 ...` with explicit flags and a real shell).

## Commands
```bash
# Dev/fallback: direct engine invocation (scripting, cron, or engine testing only).
# Saves to $LAST30DAYS_MEMORY_DIR when set in shell or ~/.config/last30days/.env;
# add --save-dir <path> for a one-off override. Mirrors LAST30DAYS_STORE convention.
python3 skills/last30days/scripts/last30days.py "test query" --emit=compact
npx skills add . -g -y   # copies skill into ~/.agents/skills/<name>/ (frozen at install time); re-run to sync working-tree edits — see Rules below

# Tests (pytest, ~89 files under tests/, configured in pyproject.toml)
uv run pytest                              # full suite
uv run pytest tests/test_dedupe_v3.py      # single file
uv run pytest tests/test_dedupe_v3.py -k some_case   # single case
uv run pytest --cov                        # with coverage (skips lib/vendor/)
```

Python 3.12+ required. Use `uv` for the env; the venv lives at `.venv/`.

## Rules
- `lib/__init__.py` must be bare package marker (comment only, NO eager imports)
- One-time setup: `npx skills add . -g -y` copies the skill into `~/.agents/skills/<name>/` (real directory) and, for harnesses that support symlinked skill dirs, drops a per-host symlink pointing at that copy. **Working-tree edits do NOT propagate automatically** — the `~/.agents/skills/<name>/` copy is frozen at install time. To sync after edits, re-run `npx skills add . -g -y`. For live-edit on a dev machine, replace the install copy with a symlink to the working tree: `ln -sfn "$PWD/skills/last30days" ~/.agents/skills/last30days` (run from the repo root).
- Git remote: origin = public (`mvanhorn/last30days-skill`)
- Do not reduce `fail_under` in `pyproject.toml` (`[tool.coverage.report]`) without documenting why in the PR. The coverage gate is a floor meant to rise over time, not to be relaxed when new code is under-tested.
- Every `lib/*.py` call to `log.source_log(...)` must pass `tty_only=False`. The default is `True`, which silently drops every line when stderr isn't a TTY (Claude Code, Codex, CI, captured output) — turning source observability into invisible failure. Enforced by `tests/test_source_log_visibility.py`.
- **CLI-gated optional sources** (Digg via `digg-pp-cli`, YouTube via `yt-dlp`) activate only when `shutil.which` resolves the binary on the **agent subprocess PATH** — not merely when the file exists on disk. First-run setup installs Digg through `@mvanhorn/printing-press-library` (default `$HOME/.local/bin`); Hermes/OpenClaw gateways often need that directory on PATH. Setup must distinguish PATH-visible installs from off-PATH binaries and must not claim "now active" unless the engine gate would pass. See `docs/solutions/integration-issues/digg-cli-agent-path-setup-wizard.md`.
- **First-run onboarding is consent-driven, model-led, and host-split.** The setup subprocess does only mechanical work (cookie reads, tool installs, GitHub device-auth) — it cannot prompt, so consent lives in `SKILL.md` Step 0. Step 0 has TWO branches: a **Claude Code Modal Flow** (the restored v3.0.0 `AskUserQuestion`-driven NUX — welcome, Auto/Manual/Skip, cookie consent, ScrapeCreators offer, `INCLUDE_SOURCES` opt-in, first-topic picker) for hosts with modals, and a **Non-Modal Prose Flow** for hosts without (OpenClaw, Codex, Cursor, Gemini CLI). Both ask before reading cookies, surface the macOS Full Disk Access fix on permission-denied, and offer the ScrapeCreators GitHub signup (10,000 free calls) on every first run. A successful `setup --github` persists `SCRAPECREATORS_API_KEY` automatically (via `setup_wizard.write_api_key`, 0o600) and masks the key in stdout. Do NOT collapse the modal flow back into a bare silent `setup` call or flatten it to prose-only — the guided modals are the feature (they eroded once and were restored). The onboarding contract is locked by `tests/test_onboarding_contract.py`. Threads/Pinterest are intentionally not surfaced in onboarding (power-user `INCLUDE_SOURCES` only).

## Security hygiene
- Never commit real API keys, browser cookies, auth tokens, app passwords, access tokens, or `.env` contents.
- Use the env-based auth patterns in `skills/last30days/scripts/lib/env.py`; tests and fixtures must use obvious dummy values only.
- Keep examples safe by redacting secrets and avoiding copy/pasteable live credentials in docs, fixtures, and test data.
- Do not weaken or disable the advisory security workflow (`.github/workflows/security.yml`) without explaining why in the PR description or review thread.

## Maintaining CONFIGURATION.md

`CONFIGURATION.md` is the user-facing configuration reference — save paths, per-source API keys, web-search backend priority, trend-monitoring stack, per-client install patterns. Distinct from `SKILL.md` (the canonical runtime spec).

Update `CONFIGURATION.md` when:

- adding a new env var (e.g. `LAST30DAYS_*`, `BSKY_*`, `*_API_KEY`)
- adding a new CLI flag that affects configuration (e.g. `--store`, `--web-backend`)
- adding a new per-client install pattern (Claude Code, Gemini, Codex, Cursor, Hermes…)
- adding a new optional source that requires its own credential
- changing the priority order of config layers (per-run flag > env > `.env` file > defaults)

Keep the existing structure organized by how often each layer is touched: per-run flags → env vars / `.env` → optional trend-monitoring stack → per-client patterns. Add new content into the right section rather than appending at the end.

When a new config concept lands in `SKILL.md` or `AGENTS.md`, mirror the user-facing knob in `CONFIGURATION.md` so non-agent readers can configure the skill without reverse-engineering it from the runtime spec.

## Beta channel

Experimental changes get tested on `mvanhorn/last30days-skill-private`, which installs as a parallel `/last30days-beta` slash command. Beta-only changes never ship to public without a review PR here. Workflow guide lives at `BETA.md` in the private repo. Plan that established this setup: `docs/plans/2026-04-17-005-feat-beta-skill-from-private-repo-plan.md`.
