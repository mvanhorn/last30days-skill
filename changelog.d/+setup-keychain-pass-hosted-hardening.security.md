Hardened hosted API client, GitHub device-auth setup wizard, and Keychain/pass helper scripts:
- Bypass ambient `HTTP_PROXY` for loopback `http://` `LAST30DAYS_API_BASE` requests, reject embedded URL userinfo/query/fragment and control characters in `LAST30DAYS_API_BASE` and `LAST30DAYS_API_KEY`, and scrub credentials from hosted error output.
- Strictly validate GitHub device-auth `verification_uri` values (HTTPS on `github.com` / `*.github.com` under `/login/device`, no userinfo or control characters) before emitting or launching `webbrowser.open()`.
- Route device-auth HTTP requests in `setup_wizard.py` through `http.open_request()` with redirects refused so GitHub OAuth `access_token` values cannot leak across 3xx redirects, and redact secrets from HTTP error details before logging or returning them.
- Pin `/usr/bin/security` and pass hex-encoded secrets over stdin via `security -i` in `setup-keychain.sh` instead of passing plaintext secrets on process `argv`.
- Validate `LAST30DAYS_PASS_PREFIX` and key names against `ALL_KEYS`, and pass `--` end-of-options markers to every `pass` invocation in `setup-pass.sh`.
