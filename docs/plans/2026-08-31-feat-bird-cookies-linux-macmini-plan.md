# Bird X cookies on Linux and Mac mini - Plan

Ship path: rewrite PR 1087 in place. Same branch. Do not open a second PR.

Goal: Feed bird a complete auth_token+ct0 from env, agentcookie cookies, or live Chrome login on Linux, a Mac mini (`sysctl -n hw.model` starts with `Macmini`), or Darwin agentcookie sink role. Leave a MacBook on main. grok pin-only everywhere. Do not detect Hermes or OpenClaw by install.

R1 extra-host sidecar → bird posts.
R2 macOS extract stays as main.
R3 never write AUTH_TOKEN/CT0 to .env.
R4 never print cookie values.
R5 doctor names real bird source.
R6 unpinned chain bird→xai→xurl→xquik; grok `_X_BACKEND_OPT_IN`; pin no failover.
R7 MacBook no agentcookie/CDP unless AGENTCOOKIE=on. Linux/Mac mini/sink: agentcookie if CLI present and not AGENTCOOKIE=off.
R8 missing sidecar is not a crash.
R9 no marketplace X plugin.
R10 Linux/Mac-mini Chrome login via CDP `Network.getAllCookies`, not SQLite.
R11 extra probe: env pair, agentcookie, CDP, extract if FROM_BROWSER set. First complete pair. No half-pair merge.
R12 MacBook FROM_BROWSER unset/off = no CDP.
R13 Darwin Keychain extract still works when FROM_BROWSER=auto or chrome.
R14 CDP extras-only.
R15 diagnose/preflight/doctor plan-only.
R16 Firefox/Safari/Windows/--no-browser-cookies as main.
R17 grok pin-only every OS.
R18 MacBook doctor must not predict bird from agentcookie-on-PATH.
R19 Mac mini: extract before CDP if FROM_BROWSER already opted in.
R20 CDP ports: BROWSER_CDP_URL, 18800 if Chrome, 9222+$DISPLAY. Chrome page target. No MacBook 9222-9232 scan.
R21 MacBook never reads sidecar.
R22 leftover grok + XAI_API_KEY uses xai.

AE8 MacBook hw.model MacBookPro, FROM_BROWSER unset, agentcookie on PATH, fake CDP, grok AUTH_OK, XAI_API_KEY → xai, no CDP, no agentcookie subprocess.
AE10 Darwin hw.model=Macmini9,1 sidecar pair FROM_BROWSER unset → bird from agentcookie. MacBook + ~/.hermes still AE8.
AE7 Linux no bird cookies, grok AUTH_OK, XAI_API_KEY → xai not grok.

KTD1 spawn `agentcookie cookies --domain .x.com --json` only when extras apply. Never open cookies-plain.db.
KTD5 do not change `_X_BACKEND_ORDER` or `_X_BACKEND_OPT_IN` vs main.
KTD7 CDP extras-only, Chrome page target.
KTD8 first complete pair, no merge.

Mac mini detect: `sysctl -n hw.model` prefix `Macmini`; sysctl failure → MacBook.
Darwin agentcookie sink role still gets extras on non-mini Darwin. Source role on a MacBook does not.

## Implementation notes (as shipped)

- `env.x_extras_enabled(config)` is the single gate: True when `AGENTCOOKIE=on`, or platform is Linux, or a Darwin Mac mini (`_mac_model()` prefix `Macmini`), or a Darwin agentcookie sink (`agentcookie.role_is_sink`, a subprocess-free config-file read; parse failure = not sink). Never consults home dir, PATH, or Hermes/OpenClaw env.
- `env._discover_and_apply_x_credentials` runs in `read` mode only: env pair (never overwritten) → agentcookie (extras) → CDP (extras) → mainline browser extract; on a Mac mini with a browser opted in, the extract runs before CDP (R19). Sources apply atomically (no half-pair merge); labels `env` / `agentcookie` / `chrome cdp` / `browser`.
- `chrome_cdp` resolves `BROWSER_CDP_URL`, else `18800` (only if `/json/version` reports Chrome/Chromium), else `9222`+`$DISPLAY`; requires a Chrome page target; rejects a Node inspector. No port scan.
- `env.x_pending_browser_auth` counts the agentcookie sidecar as a pending source only on extra hosts, so a MacBook never predicts bird from an agentcookie binary on PATH (R18).
- grok, backends, and doctor are unchanged from `main` (grok pin-only; `_X_BACKEND_ORDER = bird, xai, xurl, xquik`; `_X_BACKEND_OPT_IN = grok`).

Copy this plan to `docs/plans/` in the PR. Update the PR title/body: extras on Linux and Mac mini; MacBook last30days unchanged; grok pin-only.

## Delta 2026-09-01 — the login recipe (proven live on Grok Bot)

CDP harvest alone was not enough: on a fresh Grok Bot NUX the Auto path only ran `setup --allow-browser-cookies`, which on Linux tries a sqlite extract, finds nothing, and never opens a login window. Two human logins hours apart proved the working recipe is: the agent launches a throwaway-profile Chrome via the host `box-chrome` wrapper on the last30days extras port, opens `x.com/login`, then **hands the desktop to the human to type** — driving the form itself (computerUse/overlay on the login) plus X rate-limit was the failure mode.

- **Port fact:** `18800` is NOT box-chrome's built-in default. box-chrome uses `SAND_CHROME_REMOTE_DEBUG_PORT` or `9222 + DISPLAY_NUM` (on Grok Bot `DISPLAY=:7` → `9229`). `18800` is the last30days extras NUX convention: the agent launches the throwaway with `SAND_CHROME_REMOTE_DEBUG_PORT=18800` so `chrome_cdp._BOX_CHROME_PORT` finds it. Fixed the `chrome_cdp.py` docstrings/constant comment and SKILL.md that wrongly called 18800 "the box-Chrome default."
- **Endpoint order unchanged:** `BROWSER_CDP_URL` exclusive if set, else 18800 if Chrome, else 9222+`$DISPLAY`; fall through on empty pair; pin `BROWSER_CDP_URL` after NUX as the guard against a stale session answering on 18800. No port scan.
- **SKILL.md recipe** added to BOTH the first-run Auto setup (Claude Code Modal Flow and Non-Modal Prose Flow, after "Yes — X cookies") and the "X on Linux / Grok Bot / Mac mini" repair section: `AGENTCOOKIE=off` for the harvest; launch box-chrome throwaway on 18800; do NOT fill/drive the form; hand off to the human; confirm signed in; pin `BROWSER_CDP_URL=http://127.0.0.1:18800` (never `AUTH_TOKEN`/`CT0`); run `setup --allow-browser-cookies`; stop on block/rate-limit. A MacBook skips all of this (Keychain/Firefox/Safari extract). R3 still holds — cookies are never written to `.env`.
- **Helper:** `skills/last30days/scripts/box_chrome_login.py` prints the exact host-correct launch command (or `--exec` launches it), extras-gated, no-op on a MacBook, reads no cookies and prints no cookie values. Tests lock: MacBook never spawns it; extras + box-chrome on PATH documents 18800; `candidate_endpoints` order unchanged; `FROM_BROWSER=off` still skips CDP.
- Greptile constraints preserved: same host/path/partition cookie pairing; refuse `wss://`/`https://` CDP bases.

### Delta 2026-09-01b — harvest confirmed; flag delta corrected

A live harvest with exclusive `BROWSER_CDP_URL=http://127.0.0.1:9334` returned a complete same-host `x.com` `auth_token`+`ct0` (path `/`, unpartitioned); doctor reported `bird=ok` and the `~/.config/last30days/.env` sha256 was unchanged (R3 holds — no cookie values persisted).

The working-vs-failing difference was the launcher, not the user-agent:
- **Working (9334):** launched via `box-chrome` → `--class=box-chrome`, `--enable-unsafe-swiftshader`, no `--ignore-gpu-blocklist`. The `GrokAgent` UA token was NOT in argv (this box has `/tmp/sand-ua-token-disabled`).
- **Failed (18800 the night before):** a raw Chrome with `--class=l30d-pr1087-chrome` (NOT `box-chrome`), same swiftshader, same absence of the GrokAgent UA.

So: launch via `box-chrome` (class `box-chrome`); do NOT launch raw Chrome with a custom `--class`; do NOT claim the `GrokAgent` UA is required or was present. The human types the login; the agent never drives the form. Pin `BROWSER_CDP_URL` to the debug port the Chrome actually listens on (`18800` for the convention launch, or the real port such as `9334`). `18800` remains the last30days NUX convention, not box-chrome's built-in default. SKILL.md, the helper, and CONFIGURATION.md updated accordingly; a test locks that the helper command goes through `box-chrome` with no custom `--class`.
