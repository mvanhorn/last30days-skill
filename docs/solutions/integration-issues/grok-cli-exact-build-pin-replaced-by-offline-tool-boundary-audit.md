---
module: lib/grok_audit
date: 2026-10-25
problem_type: integration_issue
component: tooling
severity: high
symptoms:
  - "Grok X research fails with `unsupported grok CLI version` after the user updates the grok CLI"
  - "Only grok 1.0.46 (2765805b9442) is accepted while xAI ships near-daily releases"
  - "Under the throwaway child HOME, every npm grok call spends seconds decompressing a ~170 MB binary"
root_cause: design_error
resolution_type: code_fix
related_components:
  - lib/grok_x
  - lib/backends
  - lib/doctor
tags:
  - grok
  - grok-cli
  - x-backend
  - tool-boundary
  - security-gate
  - offline-fixture
  - npm-launcher
---

# grok CLI exact-build pin replaced by an offline tool-boundary audit

## Problem

The grok X backend gates which grok CLI builds may receive the user's Grok credentials, because research runs grok with `--permission-mode bypassPermissions` and depends on an agent profile to strip local tools. That gate was an exact allowlist of one build (`grok 1.0.46 (2765805b9442)`, PR #1161). xAI publishes a new grok release almost daily, so every user who updated the CLI lost Grok X research. Fixed in PR #1337 (unmerged as of this writing): stable builds at 1.0.46 or newer are accepted, and each new build gets a one-time local audit before it is trusted.

## Symptoms

- `unsupported grok CLI version` from `skills/last30days/scripts/lib/grok_x.py` for any build other than the pinned one.
- A user with grok 1.0.70 cannot use the backend, and neither can the maintainer: an interactive `~/.grok/bin/grok -> grok-1.0.44` symlink hides that last30days, which runs grok under a throwaway HOME, actually executes whatever the npm launcher resolves (1.0.70 here).

## What Didn't Work

- Bumping the allowlist to the new build. It breaks again the next day, and it asserts a security property nobody checked for the new build.
- A bare version floor with no audit. It throws away the reason for the pin: a new build could re-enable local tools under the same profile.
- Pointing a tool-boundary test at grok without a model catalog. grok silently falls back to `/v1/chat/completions` with no tools at all, so every build looks safe. That proves nothing.
- Hashing the npm `grok` launcher as the build identity. It is a small node script, identical across releases, that picks the real binary from HOME at run time. One audit of it would cover every future build.
- Counting "no canary file and no leaked secret" as a refusal. A build that crashes right after receiving the forced calls produces neither, without showing it would have refused. (Caught in review by a cross-model pass.)
- Auditing only in API-key mode. Production runs signed in, with a copied `auth.json` and no `XAI_API_KEY`, so the tools the build declares in that mode were never observed. (Caught in security review.)

## Solution

`skills/last30days/scripts/lib/grok_audit.py` runs the exact research argv and agent profile (shared with `lib/grok_x.py` through `_research_argv` and `_write_agent_profile`) against an in-process fake xAI endpoint on 127.0.0.1.

Getting grok onto a local fixture took all of these:

- Env vars `GROK_XAI_API_BASE_URL`, `GROK_CLI_CHAT_PROXY_BASE_URL`, `GROK_MODELS_BASE_URL` and `GROK_MODELS_LIST_URL` all pointing at the fixture, plus a dummy `XAI_API_KEY`. The custom models endpoint fetches its catalog with `XAI_API_KEY` (per grok's README), never the session. Without a catalog there is no Responses backend and no tools.
- `/v1/models` serving the full model-info schema, including `api_backend: "responses"` and `supports_backend_search: true` (see `_model_info`).
- `/v1/responses` answering as an SSE stream in which every event carries `sequence_number` (`grok_audit.py:324`), the response has `created_at`, and usage has `input_tokens_details` and `output_tokens_details`. Otherwise grok errors out. Session-title requests carry a `session_title` function tool and need their own answer.
- For signed-in mode, a dummy OIDC `auth.json` in the throwaway HOME, keyed `https://auth.x.ai::<client_id>` with a fake unexpired JWT (`_write_dummy_session`, `grok_audit.py:430`). The same base-URL variables redirect it offline.
- An HTTP(S) proxy on the fixture that refuses and records every connection, so any non-fixture host fails the audit.

Checks, in order, for each restricted run (signed-in, then API key):
1. No proxied hosts.
2. At least one research request reached the fixture.
3. The declared tools are exactly `[{"type": "x_search"}]` (`_EXPECTED_TOOLS`, `grok_audit.py:54`).
4. A forced `run_terminal_command` did not create a canary file.
5. A forced `read_file` did not send a secret back.
6. The CLI reported a `function_call_output` for every forced call (`_answered_call_ids`, `grok_audit.py:527`). Otherwise the run is inconclusive, not a pass.

A positive control then runs with a plain, permissive profile and must execute both forced calls. That proves the fixture can see a breach.

Verdicts: pass and fail persist in `grok-audit.json` in the config dir, keyed by native sha256, version line and `AUDIT_SCHEMA` (`grok_audit.py:44`). Inconclusive is kept in memory only, so the next run retries. Credentials are staged only after a pass. Doctor reads the cache without spawning. An explicit `doctor` run clears cached fails.

npm installs: `resolve_native` (`grok_audit.py:127`) mirrors the launcher's choice of binary. It tries `$GROK_HOME/bin/grok-<version>`, then the platform package's `bin/grok`, then decompresses `grok.br` once, via node, into the config dir (or `$GROK_HOME/bin` when there is none). The audit and the real run both use that native path.

## Why This Works

What actually matters is the boundary the build enforces, not which build it is. The audit observes that boundary directly, using the same argv, profile and auth mode as production, with no real credentials and no xAI traffic, in about 3-4 seconds per new build. Verified against real binaries: 1.0.46 and 1.0.70 pass. With 1.0.70, `injectDefaultTools: true`, an un-denied `read_file`, and allowed web search each fail, with the offending tools named.

## Prevention

- Gate an external CLI on observed behavior, not on version strings. Pin only the minimum version whose flags you rely on.
- A fixture-based security check needs both a positive control (proof it can detect a breach) and positive evidence of refusal (an answer for every forced call). The absence of a breach is not a pass.
- Audit in every auth mode production uses.
- When a CLI ships as an npm launcher, hash and execute the native binary it resolves, never the launcher.
- `tests/test_grok_audit.py` drives a fake grok script against the real fixture over HTTP, so each check runs end to end in CI without the real CLI.
- Prose edits to `skills/last30days/references/research-runbook.md` count toward the 190,000-byte cap in `tests/test_skill_progressive_disclosure.py:55`. Keep runbook additions terse.
