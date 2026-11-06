Secrets and temporary files are handled more safely:
- Temp files and the X-login Chrome profile now live in private per-user (0700) directories instead of fixed `/tmp` paths.
- `write_private_text` refuses to write through a symlink.
- Chromium cookies are decrypted in-process, so the derived key no longer appears in `openssl` arguments.
- `setup-keychain.sh` passes secrets on standard input instead of in arguments.
- The Gemini key is sent in a header instead of the URL.
- The `agentcookie` sidecar reads cookies only with explicit permission (`BROWSER_CONSENT=true` or `AGENTCOOKIE=on`).
- `--publish-password` now prints a warning: use `LAST30DAYS_PUBLISH_PASSWORD` instead.
