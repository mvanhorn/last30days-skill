---
title: Skill NUX belongs in SKILL.md Step 0, not a SessionStart hook
date: 2026-09-17
category: docs/solutions/workflow-issues
module: skills/last30days/SKILL.md
problem_type: workflow_issue
component: development_workflow
severity: medium
symptoms:
  - Claude Code or Grok sessions print last30days status even when /last30days is not invoked
  - ~/Documents/Last30Days is created on every session start
  - Unconfigured installs see a setup-wizard pitch in every project, indefinitely
root_cause: design_error
resolution_type: code_fix
related_components:
  - hooks/hooks.json
  - hooks/scripts/check-config.sh
  - last30days.py --preflight
tags:
  - session-start
  - nux
  - preflight
  - plugin-hooks
  - onboarding
---

# Skill NUX belongs in SKILL.md Step 0, not a SessionStart hook

## Problem

The plugin installed a `SessionStart` hook (`hooks/scripts/check-config.sh`) that ran in every Claude Code and Grok session, in every project, whether or not anyone invoked `/last30days`. It printed a welcome or source-count banner, optionally upsold ScrapeCreators, created `~/Documents/Last30Days`, scanned Keychain item names, and auto-chmod'd `.env` files.

That is the wrong layer for skill onboarding. A trusted plugin hook cannot be "just for last30days users."

## Root cause

[PR #58](https://github.com/mvanhorn/last30days-skill/pull/58) (2026-03-10) added a silent-when-configured missing-keys warning. [PR #126](https://github.com/mvanhorn/last30days-skill/pull/126) (v2.9.6) rewrote it into an always-print NUX. Later PRs piled on mkdir (#476), Keychain presence, and security patches (#914 cwd-adjacent `.env` RCE, #1074 decoy `check-config.sh` from session cwd). `--preflight` in the non-modal first-run flow was a second copy of the same permission summary.

SKILL.md Step 0 already owns consent (cookies, ScrapeCreators). The engine already creates the save dir on first save. `last30days.py --preflight` is a secret-free inspector for humans, scripts, and the MCP `preflight` tool — not a required first-run beat.

## Solution

- Do not ship `hooks/hooks.json` or a SessionStart command. Plugin auto-discovery would load it.
- First-run NUX stays in SKILL.md Step 0 (modal / non-modal / Grok Bot). Detect setup from key presence; do not dump `.env`.
- `--preflight` stays as an opt-in inspector and frozen JSON contract. Do not run it as a required first-run step.
- `lib/preflight.py` (Class 1 query refuse-gate) is unrelated and stays in the engine.

## Prevention

If a future change wants "status at session start," put it behind an explicit user-installed hook or a slash-command invocation. A marketplace plugin SessionStart hook is not an opt-in.
