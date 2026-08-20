---
title: Towncrier fragments + automated lockstep release PRs
date: 2026-07-24
category: docs/solutions/workflow-issues
module: ci-release-engineering
problem_type: workflow_issue
component: release_workflow
severity: medium
applies_when:
  - multiple PRs edit CHANGELOG.md ## [Unreleased] and conflict on merge
  - a release must bump the same semver across skill, pyproject, and every plugin/marketplace manifest
  - agents (not humans) author most feature PRs and need a clear changelog rule
symptoms:
  - Unreleased section merge conflicts on every release train
  - missed marketplace JSON version bumps when releasing by hand
  - agents invent release steps that drift from test_plugin_contract lockstep
root_cause: missing_workflow_step
resolution_type: workflow_change
related_components:
  - development_workflow
  - documentation
  - github_actions
tags:
  - changelog
  - towncrier
  - release-engineering
  - version-lockstep
  - agents
  - github-actions
---

# Towncrier fragments + automated lockstep release PRs

## Context

Every feature PR used to edit `CHANGELOG.md` under `## [Unreleased]`, which produced constant merge conflicts. Separately, a correct release must bump the **same** semver across skill frontmatter + H1, `pyproject.toml`, `uv.lock`, Claude/Codex/Grok/Gemini plugin manifests, and both marketplace JSON files — enforced by `tests/test_plugin_contract.py`. Hand-rolled release PRs missed files; release-please would work only with a large `extra-files` surface and conventional-commit discipline that agent traffic does not reliably provide.

## Solution

1. **towncrier** — PRs add `changelog.d/<n>.<type>.md`; `CHANGELOG.md` is written only at release time.
2. **`.github/scripts/prepare_release.py`** — runs `towncrier build` then bumps every lockstep path.
3. **Actions → Prepare release** — opens the release PR; **Tag release** creates `vX.Y.Z` on merge; existing **Release** workflow attaches artifacts.
4. **changelog-guard** — blocks non-release edits to `CHANGELOG.md` and version *strings*; requires a fragment (or `skip-changelog`) for engine/skill changes.
5. **PR template** — changelog checklist, agent disclosure (AI review + security), and relationship disclosure for contributors tied to a vendor/product they are adding.

## Agent rules (short)

- Write fragments, not `CHANGELOG.md`.
- Do not bump versions in feature PRs.
- Cut releases via Prepare release, not by editing ten files.

## See also

- `AGENTS.md` § Changelog and releases
- `changelog.d/README.md`
- `tests/test_changelog_workflow.py`
