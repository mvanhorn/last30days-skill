---
title: Digg NUX must match printing-press-library install paths and agent subprocess PATH
date: 2026-06-17
category: docs/solutions/integration-issues
module: lib/setup_wizard
problem_type: integration_issue
component: development_workflow
severity: medium
symptoms:
  - Digg source silently off after first-run setup reports success on Hermes or OpenClaw
  - Users who already installed pp-digg via printing-press-library still see Digg missing from --diagnose available_sources
  - Setup wizard probed ~/go/bin while the catalog installer writes to ~/.local/bin (printing-press-library 0.1.16+)
  - OpenClaw setup --openclaw path skipped Digg install entirely
root_cause: config_error
resolution_type: code_fix
related_components:
  - lib/pipeline
  - lib/digg
  - CONFIGURATION.md
tags:
  - digg
  - setup-wizard
  - printing-press-library
  - agent-path
  - hermes
  - openclaw
  - nux
  - optional-cli-sources
---

# Digg NUX must match printing-press-library install paths and agent subprocess PATH

## Problem

First-run setup auto-install for `digg-pp-cli` could report success while the engine still omitted Digg, especially on Hermes and OpenClaw where the agent subprocess PATH often excludes `$HOME/.local/bin`. The initial PR also used the deprecated `@mvanhorn/printing-press` package and probed legacy `~/go/bin` fallbacks instead of the current Printing Press default install dir.

## Symptoms

- `--diagnose` `available_sources` lacks `digg` even though pp-digg or setup "installed" the CLI.
- Hermes/OpenClaw users with a prior `npx @mvanhorn/printing-press-library install digg --cli-only` run hit false failures or false "now active" messages depending on probe logic.
- OpenClaw `setup --openclaw` never attempted Digg install (desktop NUX only).

## What Didn't Work

- **Treating "binary exists somewhere" as installed** — `pipeline.available_sources()` and `digg._is_available()` gate on `shutil.which("digg-pp-cli")` only. Probing `~/go/bin` without PATH visibility produced false positives.
- **Assuming Hermes vs OpenClaw use different binary locations** — both harnesses use the same printing-press-library default (`$HOME/.local/bin`); only the focused pp-digg *skill* wiring differs.
- **Using `@mvanhorn/printing-press`** — superseded by `@mvanhorn/printing-press-library`; install defaults moved from `$GOPATH/bin` to `$HOME/.local/bin` in npm 0.1.16.

## Solution

Align setup wizard with the catalog installer and the engine PATH gate:

1. **Pin installer:** `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only` (`--cli-only` only — last30days embeds Digg as an engine source, not pp-digg skill).
2. **Split outcomes:** `already_installed` / `installed` only when `shutil.which` resolves; `installed_off_path` when the binary exists under known dirs (`~/.local/bin`, legacy `~/go/bin`, Windows PrintingPress bin) but is not PATH-visible; surface `digg_path` and PATH-restart guidance in status text.
3. **OpenClaw parity:** `run_openclaw_setup()` runs the same `_install_digg_cli()` and returns `digg_cli`, `digg_action`, optional `digg_path`.
4. **Docs:** CONFIGURATION.md, SKILL.md Step 0, HERMES_SETUP.md, AGENTS.md rule for CLI-gated sources.

Key helper shape in `setup_wizard.py`:

```python
def _digg_on_path() -> Optional[str]:
    return shutil.which(DIGG_CLI_BIN)  # engine gate

def _digg_off_path_binary() -> Optional[str]:
    for candidate in _digg_bin_candidate_paths():  # ~/.local/bin first
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
```

## Why This Works

The engine never reads "is pp-digg skill installed?" — every research run shells out to `digg-pp-cli` by name on PATH. Printing Press already installs to a managed user bin dir and warns when that dir is off PATH; last30days setup must mirror that contract instead of inventing a separate success definition. Detecting off-PATH binaries lets setup reuse prior pp-digg installs without lying about activation.

## Prevention

- When adding NUX auto-install for a CLI-gated source, match the upstream installer's default bin dir and pin the npm semver.
- Success messaging must use the same probe as `available_sources()` (`shutil.which`), with a separate off-PATH outcome when the binary exists on disk.
- Cover Hermes/OpenClaw in tests with redirected `HOME` and mocked PATH; add OpenClaw JSON fields when server setup should mirror desktop NUX.
- Search `docs/solutions/` for `digg`, `setup-wizard`, and `agent-path` before changing optional-source onboarding.
