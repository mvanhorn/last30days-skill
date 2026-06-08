---
title: Release-time consistency tests cause cascade CI failures across all open PRs
date: 2026-05-16
category: docs/solutions/workflow-issues
module: ci-release-engineering
problem_type: workflow_issue
component: testing_framework
severity: high
applies_when:
  - a test asserts consistency between two release-time artifacts (e.g., SKILL.md version and a hardcoded pin in a shell script)
  - one artifact is updated as part of a version bump and the other requires a manual lockstep update
  - multiple long-lived PRs are open simultaneously against the same base branch
symptoms:
  - every open PR's CI fails after a version bump even though the PRs are unrelated to versioning
  - the failing test references a stale hardcoded value that was not updated alongside the bumped version
  - PR authors must rebase and manually fix an artifact they did not touch
root_cause: missing_workflow_step
resolution_type: code_fix
related_components:
  - development_workflow
  - documentation
tags:
  - ci
  - release-engineering
  - consistency-test
  - version-pin
  - cascade-failure
  - test-design
  - workflow
---

# Release-time consistency tests cause cascade CI failures across all open PRs

## Context

A `tests/test_version_consistency.py::test_sync_cache_path_uses_skill_version` test was added to enforce that the version string embedded in `skills/last30days/scripts/sync.sh` (a hardcoded plugin-cache path segment) matched the version frontmatter in `skills/last30days/SKILL.md`. The intention was sound: the cache path had to stay in lockstep with the skill version or the sync would silently pull stale files.

The test worked as designed until a release shipped. At that point it turned into a cascade-failure machine:

1. A release PR bumps `SKILL.md` version (e.g., 3.2.0 → 3.2.1) **and** bumps the `sync.sh` pin. That PR's CI is green.
2. The release PR merges to `main`.
3. Every PR that was open at merge time was branched from pre-release `main`. Those PRs have `SKILL.md` 3.2.1 (inherited via merge-base with `main`) but their branch never touched `sync.sh`.
4. CI for those PRs runs the consistency test against the new `main` — `SKILL.md` says 3.2.1, `sync.sh` still says 3.2.0 — and fails.
5. All open PRs are now red simultaneously, with a failure that has nothing to do with their changes.

This affected at least five PRs during the 2026-05-13 to 2026-05-15 window: PR #400 (caught during rebase, required a manual pin bump), PRs #390 and #392 (OpenClaw `SCRAPECREATORS_API_KEY` fix, both stalled for the same stale-pin reason), and at least two others. A follow-up hotfix PR (#397 — `fix(sync): bump cache target to 3.2.1 to match SKILL.md`) was required just to unblock the queue.

The permanent fix was PR #405: delete `sync.sh` entirely (the install workflow made it redundant) and drop `test_sync_cache_path_uses_skill_version`. Once both were gone, no version-consistency cascade was possible.

## Guidance

### 1. Don't write consistency tests that read two files and assert one matches a substring derived from the other

This pattern looks safe but is not:

```python
def test_sync_cache_path_uses_skill_version(self) -> None:
    sync_text = (SKILL_ROOT / "scripts" / "sync.sh").read_text(encoding="utf-8")
    version = _skill_version()          # reads SKILL.md
    self.assertIn(
        f'last30days-skill/last30days/{version}"',
        sync_text,                      # asserts sync.sh contains that string
    )
```

It encodes the assumption that both files are always updated together, in the same commit, on the same branch. That assumption breaks the moment two files have independent lifecycle owners — a versioned manifest and a deployment script are archetypal examples.

### 2. If the values genuinely need to stay in sync, derive one from the other at runtime

Remove the hardcoded pin from `sync.sh` and compute it:

```bash
# sync.sh — derive version from SKILL.md at runtime, no pin to maintain
SKILL_VERSION=$(grep -m1 '^version:' "$(dirname "$0")/../SKILL.md" \
    | sed 's/version:[[:space:]]*"\([^"]*\)"/\1/')
CACHE_PATH="last30days-skill/last30days/${SKILL_VERSION}"
```

Now there is only one source of truth (`SKILL.md`). The test that asserted they matched becomes vacuous and should be deleted. If `SKILL.md` is wrong, the sync itself will fail loudly — which is better feedback than a CI gate on a different PR.

### 3. If two values must stay independent for legitimate reasons, update them together and make the test self-skip if either source is missing

If separate versioning is genuinely required (e.g., SKILL.md versions for harness consumers, sync.sh versions a private artifact store with its own cadence), update both in the same PR — never staggered — and write the test to self-skip rather than error when either file is absent:

```python
def test_sync_cache_path_uses_skill_version(self) -> None:
    sync_sh = SKILL_ROOT / "scripts" / "sync.sh"
    if not sync_sh.exists():
        self.skipTest("sync.sh not present; skipping pin consistency check")
    sync_text = sync_sh.read_text(encoding="utf-8")
    version = _skill_version()
    self.assertIn(
        f'last30days-skill/last30days/{version}"',
        sync_text,
    )
```

Self-skipping means deleting the file is a non-event in CI — no cascading red, no hotfix PR to the queue.

### 4. Run consistency tests against the merge-base diff, not main

If you keep a two-file consistency test, scope it so it only fails when the PR itself modifies one of the two files but not the other. A GitHub Actions step can do this:

```yaml
- name: Check sync.sh version pin consistency
  run: |
    BASE=$(git merge-base HEAD origin/main)
    SKILL_CHANGED=$(git diff --name-only "$BASE" HEAD | grep -c 'SKILL\.md' || true)
    SYNC_CHANGED=$(git diff --name-only "$BASE" HEAD | grep -c 'sync\.sh' || true)
    if [ "$SKILL_CHANGED" -gt 0 ] && [ "$SYNC_CHANGED" -eq 0 ]; then
      echo "SKILL.md version bumped but sync.sh pin was not updated"
      exit 1
    fi
```

This only fires when your PR touched `SKILL.md` and left `sync.sh` alone — never because a release merged to `main` after you branched.

### 5. Ask whether you actually need this test

If the values are wrong, downstream tooling will fail loudly: the sync will fetch the wrong artifact, installs will break, or the harness will reject the version. A test that exists only to catch a human-bookkeeping error at release time adds cascade-fail risk without offering a meaningfully earlier signal. Weigh that cost before adding any two-file consistency gate.

## Why This Matters

The damage from a stale-pin consistency test is asymmetric. It:

- Fails on every open PR simultaneously the moment a release lands on `main` — not just the PR that forgot to update the pin.
- Produces a failure message that points at a line in a test file with no obvious relationship to the PR's actual changes.
- Requires either a hotfix PR (touching a file the failing PRs have no business touching) or a manual rebase of every affected branch.
- Blocks work that has already been reviewed and approved.

In this repo the effect was measurable: at least five PRs stalled across a two-day window, one hotfix PR was shipped just to unblock the queue, and multiple authors spent time debugging a failure completely unrelated to their changes.

The broader principle is that tests which gate on *bookkeeping consistency between files* impose their maintenance cost on every contributor, every time, even when those contributors did nothing wrong. That cost compounds with team size and release cadence.

## When to Apply

Apply this guidance whenever you find yourself:

- Writing a test that reads two files and asserts that a string in one matches a value derived from the other.
- Adding a CI step labeled "consistency check," "sync check," or "pin check" where the check compares a hardcoded value against a computed one from a separate file.
- Working in a repo where a versioned manifest (e.g., `SKILL.md`, `package.json`, `pyproject.toml`) and a deployment artifact (e.g., a shell script, a Dockerfile, a Helm values file) are both maintained by hand.
- Reviewing a PR that touches only one of two "paired" files and fails a consistency test for the other.

It does *not* apply to tests that read a single source of truth and validate its internal structure (e.g., asserting that `SKILL.md`'s frontmatter version is double-quoted, or that `package.json`'s `version` field is a valid semver string). Those tests have one file and one assertion; they cannot cascade across branches.

## Examples

### Before — the pattern that caused the cascade

Original `tests/test_version_consistency.py` (deleted in commit `9fb19ea`):

```python
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "last30days"


def _skill_version() -> str:
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'^version:\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if not match:
        raise AssertionError("SKILL.md version frontmatter not found")
    return match.group(1)


class TestVersionConsistency(unittest.TestCase):
    def test_sync_cache_path_uses_skill_version(self) -> None:
        sync_text = (SKILL_ROOT / "scripts" / "sync.sh").read_text(encoding="utf-8")
        version = _skill_version()          # source 1: SKILL.md frontmatter
        self.assertIn(                      # assertion: sync.sh must contain
            f'last30days-skill/last30days/{version}"',
            sync_text,                      # source 2: hardcoded string in sync.sh
        )
```

`sync.sh` contained a line like:

```bash
PLUGIN_CACHE="$HOME/.cache/last30days-skill/last30days/3.2.0"
```

When SKILL.md bumped to `3.2.1` in a release PR, `sync.sh` was updated in the same PR and CI stayed green. But every PR branched before that release still had `sync.sh` at `3.2.0`. Their CI failed immediately, with an assertion error pointing at the test, not at the release PR.

### After — what we did: delete both

PR #405 deleted `sync.sh` (the install workflow replaced it) and dropped `test_sync_cache_path_uses_skill_version` in the same change. No consistency gate, no pin to maintain, no cascade possible.

### After — what we could have done instead: derive at runtime

If `sync.sh` had still been needed, the right fix would have been to remove the hardcoded version from the script and derive it from `SKILL.md`:

```bash
#!/usr/bin/env bash
# sync.sh — no hardcoded version; reads SKILL.md as single source of truth
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_VERSION=$(grep -m1 '^version:' "${SCRIPT_DIR}/../SKILL.md" \
    | sed 's/version:[[:space:]]*"\([^"]*\)"/\1/')

if [ -z "$SKILL_VERSION" ]; then
    echo "error: could not parse version from SKILL.md" >&2
    exit 1
fi

PLUGIN_CACHE="$HOME/.cache/last30days-skill/last30days/${SKILL_VERSION}"
# ... rest of sync logic
```

With this in place, `test_sync_cache_path_uses_skill_version` has no reason to exist — there is nothing to assert. Delete it. If the version parsing breaks, `sync.sh` itself exits non-zero with a clear message.

## Related

- **PR #397** (merged) — `fix(sync): bump cache target to 3.2.1 to match SKILL.md`. The hotfix that unblocked the cascade temporarily by bumping the pin.
- **PR #400** (merged) — caught the same cascade during rebase; had to bump the pin to clear CI.
- **PR #390** (closed) and **PR #392** (rebased + merged) — OpenClaw `SCRAPECREATORS_API_KEY` fix; both blocked by the cascade until rebased onto post-#405 main.
- **PR #405** (merged) — the permanent fix: deleted `sync.sh` + `test_sync_cache_path_uses_skill_version` together.
- **PR #412** (merged) — adjacent work that consolidated SKILL.md version parsing into `lib/skill_meta.py`, reducing future drift risk by giving the version field one canonical reader.
