# feat(x): discover browser cookies for Bird (agentcookie + live Chrome CDP); make Grok a fail-closed backup after Bird

Unpinned last30days X uses **bird** when a complete `auth_token`+`ct0` pair exists, then **Grok CLI**, then **`XAI_API_KEY`** (xai), then existing **xurl/xquik** last-ditch. Doctor names the real source. The product path never writes `AUTH_TOKEN`/`CT0` to `~/.config/last30days/.env` and never prints cookie values. Fixtures use `test-auth-token` / `test-ct0` only.

## Problem
Bird already works when those two cookies are injected. last30days does not discover them. Weekly X on Grok Bot was auth-failed because grok was first and the grok session was dead. Linux Chrome SQLite cannot be decrypted. Grok Bot ships Chrome, not Firefox. `agentcookie` can deliver cookies on Linux; last30days never shelled out to it. A live Chrome login can also yield the pair via CDP `Network.getAllCookies`; last30days had no CDP client.

## User-directed walk (do not reorder)
Cookie probe, first complete pair wins, no half-pair merge:
1. Explicit `AUTH_TOKEN`+`CT0` already in env (do not overwrite).
2. `agentcookie cookies --domain .x.com --json` (soft dep; `AGENTCOOKIE=off` disables). Do not open `cookies-plain.db`. Do not import agentcookie. Do not add a Python agentcookie dependency. Independent of `FROM_BROWSER`.
3. Live Chrome CDP: scan reachable localhost debug ports, `Network.enable` + `Network.getAllCookies`. Do not hardcode 9228/9223. Usual box-chrome port is 9222 plus the display number. `FROM_BROWSER=off` skips CDP and browser extract.
4. Existing macOS cookie extract; optional Firefox if installed.

Then backends if bird still has no complete pair:
5. Grok CLI, fail-closed. Leftover `~/.grok/auth.json` must not steal first place over bird and must not block xai when grok is dead/expired.
6. xai (`XAI_API_KEY`).
7. xurl / xquik last-ditch. Keep them out of SKILL.md first-run copy.

Explicit `LAST30DAYS_X_BACKEND` / `--x-backend` pin still wins with no failover.

Inject into bird the same way env already does (`bird_x.set_credentials`). Keep `BIRD_DISABLE_BROWSER_COOKIES=1` on the Node subprocess. Do not change vendor `bird-search.mjs` unless proven necessary (it was not).

The 2026-08-14 grok-pin-only decision is superseded **only on membership**: grok is a fail-closed backup after bird, not first place and not pin-only.

## Units
- **U1** sidecar reader (`lib/agentcookie.py`).
- **U2** probe orchestration (`env.discover_x_credentials`, wired into `get_config` read mode) + backend walk (`_X_BACKEND_ORDER = bird, grok, xai, xurl, xquik`; grok fail-closed via `grok_x.is_auto_available`; descriptor `fail_closed` field in `lib/backends.py`).
- **U3** doctor honesty (`doctor._x_record`): predict bird when pending, name grok when signed in, report dead/expired grok as unconfigured (fail-closed skip), never mask a configured-but-broken non-grok backend.
- **U4** SKILL.md first-run/repair: agentcookie, then sign into x.com in Chrome and leave it open, then `grok login`, then `XAI_API_KEY`, then skip X. No marketplace X plugin. Do not tell Grok Bot users to install Firefox as the default. CONFIGURATION.md mirrors the knobs.
- **U5** tests (mock subprocess, fake CDP server, dummy cookie values only).
- **U6** Chrome CDP + localhost debug-port scan (`lib/chrome_cdp.py`, stdlib RFC 6455 websocket client).

## Done
PR open. Plan copied to `docs/plans/`. U1–U6 landed. Unit tests pass. No secrets in the diff.
