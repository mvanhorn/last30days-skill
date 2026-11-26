---
title: "An expired x.com session in an earlier browser shadowed a live one, and bird reported it as \"Internal server error\""
date: 2026-11-10
category: integration-issues
module: lib/bird_x
problem_type: integration_issue
component: authentication
symptoms:
  - "Every X search via the bird backend failed with \"Internal server error\" while other sources worked"
  - "X had worked on earlier days with no config change"
  - "doctor showed bird authenticated (AUTH_TOKEN and CT0 present, source browser)"
root_cause: logic_error
resolution_type: code_fix
severity: high
related_components:
  - lib/env
  - lib/chrome_cookies
tags:
  - x
  - bird
  - cookies
  - from-browser
  - auth-failed
  - session-failover
  - keychain
---

# An expired x.com session in an earlier browser shadowed a live one, and bird reported it as "Internal server error"

## Problem

With `FROM_BROWSER=auto`, the engine uses the first browser that holds a complete `auth_token` + `ct0` pair for `.x.com`. An expired Safari session therefore won over a live Chrome session, and X search failed on every run. Bird surfaced X's rejection as a generic GraphQL "Internal server error", so it looked like a broken endpoint, not a bad session.

## Symptoms

- POST `SearchTimeline` returned HTTP 200 with `errors: [{"message": "Internal server error", "path": ["search_by_raw_query", "search_timeline", "timeline"]}]` and no timeline.
- With the same cookies, every authenticated endpoint returned 401 `{"code": 32, "message": "Could not authenticate you"}`: GET `SearchTimeline`, `https://x.com/i/api/1.1/account/verify_credentials.json`, `UserByScreenName`.
- Presence checks passed, because both cookies existed. Only their validity was wrong.

## What Didn't Work

- Assuming the endpoint or query ID had drifted, and looking for a "better endpoint". The vendored bird POST `SearchTimeline` works unchanged with a live session. The endpoint was never the problem.
- Suspecting the wrong Chrome profile. Both Chrome profiles (`Default`, `Profile 1`) held the same session (same token hash), so profile choice was irrelevant.
- Using the 1.1 account endpoints as an auth check. With a *valid* session, the `https://x.com/i/api/1.1/account/settings.json` and `verify_credentials` endpoints now return 404 code 34 ("page does not exist") on both `x.com/i/api` and `api.x.com`. A 401 there means rejected, but a 404 tells you nothing, so they make a poor preflight.
- A different HTTP client gave different answers for "the same" cookies. That was the clue: the probe and the engine were not using the same cookies.
- Open PR #1383 (prefer complete cookie sets) would not have helped: Safari's pair *was* complete, just expired. First-complete-wins stays first-complete-wins.

## Solution

Diagnosis that found it, without ever printing a credential:

1. Hash the engine's chosen `AUTH_TOKEN` (`sha256(...)[:8]`) after `env._discover_and_apply_x_credentials(cfg)`.
2. Hash each browser's token from `cookie_extract.extract_cookies(browser, ".x.com", ["auth_token", "ct0"])`.
3. The engine's hash matched Safari, not Chrome. Sending Chrome's pair through the unchanged bird client (`bird_x.set_credentials` + `_subprocess_env`) returned real posts.

The browser order comes from `COOKIE_BROWSER_NAMES` (`skills/last30days/scripts/lib/env.py:1085`). `auto` returns silent browsers first (Firefox, Safari), then Chromium (`skills/last30days/scripts/lib/env.py:1139`), so that Keychain prompts happen only when needed. That order is deliberate, so the fix keeps it and adds failover on rejection instead of reordering.

Fix (PR #1396, released in v3.33.2):

- When extraction picks a browser's X pair, it records a run-state dict under `X_SESSION_STATE_KEY` (`skills/last30days/scripts/lib/env.py:1253`). The dict holds the source browser, the browsers left to try, a set of rejected tokens, and the live session.
- `bird_x._run_bird_search` (`skills/last30days/scripts/lib/bird_x.py:418`) treats `_SESSION_REJECTED_MARKERS` / `_SESSION_SUSPECT_MARKERS` (`skills/last30days/scripts/lib/bird_x.py:112-113`) as a possible rejected session. It then calls `env.next_x_browser_pair` (`skills/last30days/scripts/lib/env.py:1264`), retries the same search with the next distinct session, and keeps that session for the run.
- With no usable alternate, `_annotate_session_error` (`skills/last30days/scripts/lib/bird_x.py:479`) names the browser. A 401 or code 32 classifies as auth-failed. A bare "Internal server error" gets a hint but stays a generic error, because X also returns it during real outages.
- Each later browser read goes through `_bounded_cookie_read` (`skills/last30days/scripts/lib/env.py:1320`). With a deadline, the read runs on a daemon thread, because a Chromium Keychain lookup can block for up to 10s (`skills/last30days/scripts/lib/chrome_cookies.py:78`).

## Why This Works

Presence of a cookie pair was being used as a stand-in for a valid session. The only reliable validity signal is X's own answer to a real request. So the fix acts on that answer when it arrives, instead of probing up front. A working setup pays nothing extra: no additional request and no Keychain read for later browsers. Only a rejected session costs one more read and one more search.

Two parts of the design came from review findings on PR #1396 and are not obvious from the code:

- The live session must sit in a shared nested object, not in top-level `AUTH_TOKEN`/`CT0`. `pipeline.run` works on `stream_config = dict(config)` (`skills/last30days/scripts/lib/pipeline.py:2677`), a shallow copy. A switch written to the copy's top-level keys was undone when a later phase re-injected credentials from the original `config`. A nested dict is shared by every shallow copy. `_inject_bird_credentials` (`skills/last30days/scripts/lib/env.py:1348`) injects from that state, not from the config's own keys.
- Track every rejected token for the run, not just the last one. Profiles and browsers can share a session (here, two Chrome profiles did), so remembering only the last rejected token lets an A, B, A, C order retry A.

## Prevention

- Any per-run mutable state that phases must share (a session switch, a counter, a consumed queue) goes in a nested object on the config, never in top-level keys. Top-level writes do not survive `dict(config)` copies. The same pattern appears in `docs/solutions/logic-errors/comparison-subruns-multiply-shared-ytdlp-budget.md`, where a value written to the shared config *before* fan-out is visible to every copy.
- When a credentialed source fails with an opaque server error, compare token hashes between the engine and each candidate source before changing endpoints.
- Anything that may trigger a macOS Keychain prompt during a search must be bounded by the search deadline.
- Tests: `tests/test_x_session_failover.py` covers the failover, the config-copy re-injection, the A, B, A, C order, the deadline, the cancel, and a slow Keychain read.
- Known gap, left non-blocking in review: a browser whose read timed out at the deadline is dropped for the rest of that run and not retried by a later search.

## Related Issues

- PR #1396 (fix, merged, v3.33.2)
- PR #1383 (complete-cookie-set preference, related, does not cover expired pairs)
- `docs/solutions/logic-errors/comparison-subruns-multiply-shared-ytdlp-budget.md` (shared state across `dict(config)` copies)
- `docs/solutions/logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md` (why the bounded read uses a daemon thread)
