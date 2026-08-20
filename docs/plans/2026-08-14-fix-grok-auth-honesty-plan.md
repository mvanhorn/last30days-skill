# Plan: Fix Grok Auth Honesty

**Date:** 2026-08-14  
**Status:** Implemented  
**PR:** fix(grok): treat expired sessions as degraded, not ok

## Problem (measured 2026-08-14 Peter Steinberger run on the user's Mac)

- grok binary on PATH. Doctor cached grok status ok / will use grok because `~/.grok/auth.json` existed with token markers.
- `stored_auth_status()` substring-scans for `refresh_token`/`access_token`/`auth_mode`. It never parses `expires_at`.
- The file had `expires_at 2026-08-14T01:26:53Z`, hours dead.
- A prior run at 07:47:26 UTC had `run_outcome` ok (2 items). Session was real.
- At 08:43 grok loaded auth, `is_expired` true, OIDC refresh → `invalid_grant` "Refresh token has been revoked". grok deleted auth.json.
- Engine exit 1 "Not signed in", fell back to bird (30 items via Safari cookies), lane flagged PARTIAL.
- Host told the user "Grok CLI is not signed in" as if it never was.

## Three states to distinguish

1. **No grok CLI** — silent fallback. Fine. Do not waste the user's time. Do not nag install on every research run.
2. **CLI installed, never logged in** — silent fallback. Fine.
3. **CLI installed, WAS logged in, session dead** — currently reports ok then partial. **This is the bug.**

## What was built

1. **`stored_auth_status` parses `expires_at` locally** (no network, no subprocess). Added `AUTH_EXPIRED` distinct from `AUTH_OK` / `AUTH_MISSING` / `AUTH_ERROR`. Never echoes token values. Finds `expires_at` anywhere in the vendor-keyed JSON object via recursive search.

2. **Doctor / `_probe_grok` does NOT map `AUTH_EXPIRED` to `health.OK`** or "will use: grok". Reports `DEGRADED`/warn + expiry timestamp + "refresh happens at run; if refresh was revoked, `grok login --device-auth`".

3. **Research-time `is_available` STILL attempts grok when a `refresh_token` marker is present** even if `access` `expires_at` is past. Expiry of the access token is not proof refresh is dead. Does not skip a refresh that might work.

4. **Auth revocation detection**: If grok exits "Not signed in" / RefreshTokenRejected / auth.json vanished mid-run: does not retry grok in that run. Falls back once. Typed outcome `auth-failed` (via `is_auth_revoked_error()` and `classify_run_failure()`), not a generic PARTIAL that reads as "the product half-worked."

5. **Host-facing copy for case 3**: SKILL.md updated with guidance: "X used <fallback> after the Grok session expired" + login hint. Not "Grok CLI is not signed in" when `run_outcome` shows it worked earlier.

6. **Doctor --probe still does not call xAI or grok.** Whole-doctor-path test patches `subprocess.run` to raise and still passes. `active_backend` stays a prediction; when `run_outcome.at` is stale or not ok, doctor says "will use grok, unverified since <time>".

7. **Tests**: Fixture stores (missing file, future `expires_at`, past `expires_at`, unparseable JSON). No network.

8. **SKILL.md**: Host reads `sources.x.run_outcome` and grok expiry warn; does not treat `active_backend` as verified; does not spend a turn installing grok unless the user asked for first-party X.

9. **Changelog fragment**: `changelog.d/+grok-auth-expired.fixed.md`. Tests pass with `uv run pytest`.

## Scope boundaries (NOT in this PR)

- X query construction, fanout, `search_name`, retrieve-judge-retry, and handle promotion are unchanged. That is a separate PR.

## Success criteria (all met)

- Past `expires_at` fixture → not grok ok.
- Future `expires_at` → still ok (not live-verified).
- No grok binary → no extra user-facing failure.
- Simulated "Not signed in" after prior ok `run_outcome` → typed `auth-failed` / fallback copy, not "never signed in."
- No-subprocess doctor test still passes.

## Files changed

- `skills/last30days/scripts/lib/grok_x.py` — `AUTH_EXPIRED`, `stored_auth_status()` returns 3-tuple, `is_auth_revoked_error()`, `classify_run_failure()`, `_invoke()` sets `auth_revoked`, `_run_query()` returns 3-tuple, `search_x()` propagates `auth_revoked`
- `skills/last30days/scripts/lib/backends.py` — `_probe_grok()` handles `AUTH_EXPIRED` as `DEGRADED`
- `skills/last30days/scripts/lib/pipeline.py` — `_fetch_x_backend()` propagates `auth_revoked`, `_classify_source_failure()` recognizes grok markers
- `skills/last30days/SKILL.md` — Grok session expiry handling guidance
- `tests/test_grok_x.py` — expires_at and auth revocation tests
- `tests/test_backend_descriptors.py` — grok expiry state tests
- `changelog.d/+grok-auth-expired.fixed.md` — release notes fragment
