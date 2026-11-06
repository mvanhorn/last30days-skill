---
title: Hermes security review — download-and-run removal, full scan scope, secret and temp-file hardening
date: 2026-11-06
category: docs/solutions/security-issues
module: lib/setup_wizard, lib/vendor/bird-search, lib/chrome_cookies, lib/agentcookie
problem_type: security_issue
component: install_and_runtime
severity: high
tags: [security, hermes, supply-chain, secrets, temp-files, cookies]
root_cause: design_gap
resolution_type: code_fix
---

# Hermes security review (November 2026)

The Hermes reviewer recommended keeping the skill uninstalled. Each finding below was checked against the code. The table gives the finding, whether it was real, and the fix.

| # | Finding | Verdict | Resolution |
|---|---|---|---|
| 1 | `HERMES_SETUP.md` told users to copy files manually to get past the installer block | Real | Removed. The guide now uses the Hermes installer (which runs the scan) or a manually reviewed checkout pinned to an exact SHA. Updates are review-then-pin, never a bare `git pull`. |
| 2 | The guide installed from upstream, unpinned | Real | The guide now installs from this fork at a reviewed 40-character SHA and checks that the SHA matches. |
| 3 | Setup ran `npx -y @mvanhorn/printing-press-library …` | Real. The package version was pinned, but `npx -y` still downloaded and ran code, and that installer fetched a live upstream registry and ran `go install` on upstream module paths. | Setup now only *detects* Printing Press CLIs. arXiv and Techmeme are native stdlib clients. For Digg, the user runs `scripts/build-pp-cli.sh`, which builds from `missterr/printing-press-library` at a pinned commit with `GOTOOLCHAIN=local`, `-mod=readonly`, and checksum-verified dependencies, and refuses to build if the fetched HEAD doesn't match the pin. `tests/test_no_runtime_installs.py` enforces this. |
| 4 | `.skillignore` excluded the vendored executable client from scans | Real | No executable file is excluded any more. The vendored client is environment-only and search-only, with a sha256 manifest (`VENDORED.md`, checked by `tests/test_vendor_integrity.py`). |
| 5 | `/tmp/last30days-synthesis-…` was written with a clobbering redirect | Real | The file moved to a private 0700 per-user directory. |
| 6 | `setup-keychain.sh` passed the secret with `-w "$value"` | Real | The secret is now fed to `security -i` on stdin. |

## Additional findings (own review)

| Finding | Fix |
|---|---|
| X-login Chrome profile at a fixed `/tmp/last30days-x-chrome` path | Moved to a private 0700 directory under the config dir. Launch is refused if the path is a symlink or owned by another user. |
| GitHub device-handle file fell back to a fixed shared-`/tmp` name | Now uses a private per-user 0700 temp directory, owner-checked. |
| `write_private_text` followed symlinks | Now opens with `O_NOFOLLOW`. |
| Chromium Safe Storage key passed in `openssl -K` arguments | Decryption now runs in-process (`lib/aes_cbc.py`, checked against NIST vectors). |
| Gemini key sent as `?key=` in the URL | Now sent in the `x-goog-api-key` header. |
| `agentcookie` sidecar read X cookies on Linux/Mac mini hosts without consent | Now requires `BROWSER_CONSENT=true` or `AGENTCOOKIE=on`. |
| `curl … \| bash` grok install advice | Replaced with a link to xAI's official instructions. |
| User topics in CLI argv with no `--` separator | `--` added for Digg, Trustpilot, and the watchlist re-invocation. |
| `--publish-password` exposes the password in argv | Still supported, but now prints a warning recommending `LAST30DAYS_PUBLISH_PASSWORD`. |

## Reviewed and accepted (by design)

- **Trustpilot WAF harvest.** `trustpilot-pp-cli` drives its own throwaway headless browser (`agent-browser`) to collect trustpilot.com anti-bot cookies. It never reads the user's browser profile. The source is opt-in, and `LAST30DAYS_TRUSTPILOT_NO_BROWSER=1` disables it.
- **ScrapeCreators device auth** sends a GitHub OAuth token to `api.scrapecreators.com`. This is how the user-initiated signup flow mints the key.
- **`r.jina.ai`** (keyless web fetch) receives the URLs being fetched, but no credentials.
- **The `grok` CLI** runs with `bypassPermissions` on prompts containing untrusted topic text. It is mitigated by a per-build audit (`lib/grok_audit.py`: a fake endpoint, a refusing proxy, and canary checks) and a throwaway `HOME`. It is opt-in.
- **CDP debug port 18800** listens on localhost during the consented extras-host login, so any local user can attach to it while it is open. Close the login Chrome after signing in.
- **`xurl search <query>`** has no `--`. The query is built by the engine, and `xurl`'s handling of `--` hasn't been verified, so it is left as-is.

## Credential, network, and publishing

- **Credential scope.** Each module that sends a credential names only its provider's hosts. `tests/test_credential_host_scope.py` pins these, so a new host fails CI until it is reviewed. `http.py` strips auth headers on redirects that cross origins.
- **Publishing.** `html_publish.py` uploads only on `--publish-html` or `library feed --publish`. The payload is the rendered HTML plus an optional shared password, and never includes an API key. The reference doc requires an explicit user choice before uploading.
- **Scheduling.** The code creates no cron, launchd, schtasks, or systemd entries. `watchlist.py` is designed to be scheduled by the user and runs with `--no-browser-cookies`.
