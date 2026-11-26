"""Remote API client for last30days (optional hosted-backend mode).

When both LAST30DAYS_API_KEY and LAST30DAYS_API_BASE are set, the engine
submits the topic to the configured remote API, polls until the run reaches a
terminal status, streams narration progress to stderr, and renders the
server's report. No local provider keys are required in this mode. The
endpoint comes only from LAST30DAYS_API_BASE - there is no built-in default.

Contract (API v1):
  POST {base}/search   Authorization: Bearer <key>
                       {"query": ..., "depth": "quick"|"default"|"deep",
                        "register"?: "exec"|"dev"|"creator"|"eli5",
                        "clarify"?: {"angle": str} | {"run_broadly": true}}
                       -> 200 {"search_id": "<uuid>", "status": "running"}
                       -> 200 clarify payload {"needs_clarification": true, ...}
                       -> 401 {"error"} / 402 {"error","requires_credits",
                          "balance","needed"} / 429 {"error"}
  GET  {base}/search?id=<uuid>  same auth header; poll until status is
                       terminal ("complete" | "error"). Running rows carry
                       "stderr" (narration + engine lines) and "eta_ms";
                       terminal complete rows carry "synthesis_text" and
                       "raw_markdown" (stderr stripped); error rows may carry
                       "error" and/or "synthesis_text" (the failure reason).
                       Terminal rows may carry "engine_version", the engine
                       that wrote the run.

This module carries ZERO pricing, rate-card, cost, or billing logic.
Balance/credit numbers are only ever displayed verbatim from API responses.
The API key is never printed, logged, or persisted by this module.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from . import env, http, library, providers, usage
from .log import source_log

# Distinct exit code for the clarify gate so the invoking model can tell
# "re-run with a chosen angle" apart from a plain failure (1).
EXIT_CLARIFY = 3

POLL_INITIAL_DELAY = 3.0
POLL_MAX_DELAY = 10.0
POLL_TIMEOUT_SECONDS = 15 * 60
# GET is idempotent: retry a few times across network blips before giving up.
POLL_NETWORK_RETRIES = 3
# Cadence for the compact elapsed/eta progress line (seconds).
PROGRESS_LINE_INTERVAL = 15.0

NARRATE_PREFIX = "[narrate] step="
TERMINAL_STATUSES = {"complete", "error"}


_URL_USERINFO_RE = re.compile(r"(https?://)[^\s/?#]+@", re.IGNORECASE)


def _err(msg: str) -> None:
    source_log("hosted", msg, tty_only=False)


def _scrub_url_userinfo(url: str) -> str:
    """Return ``url`` with any embedded authority userinfo removed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _URL_USERINFO_RE.sub(r"\1", url)
    if parts.username is None and parts.password is None and "@" not in parts.netloc:
        return url
    hostport = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, hostport, parts.path, parts.query, parts.fragment))


def _scrub_error_text(text: str) -> str:
    """Remove embedded URL credentials and the configured API key from log text."""
    scrubbed = _URL_USERINFO_RE.sub(r"\1", text)
    raw_key = env.read_secret_env("LAST30DAYS_API_KEY") or ""
    for candidate in {raw_key, raw_key.strip()}:
        if candidate and candidate.strip():
            scrubbed = scrubbed.replace(candidate, "[REDACTED]")
    return scrubbed


def _api_base() -> str:
    # Endpoint comes only from the environment - no built-in default. Hosted
    # mode is gated on this being set (see last30days.py), so by the time this
    # is called it is populated; an empty value means "not configured".
    raw = (os.environ.get("LAST30DAYS_API_BASE") or "").strip()
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in raw):
        raise ValueError(
            "LAST30DAYS_API_BASE must not contain control characters or whitespace."
        )
    if not providers.allowed_base_url_override(raw):
        raise ValueError(
            "LAST30DAYS_API_BASE must use https:// "
            "(http:// is allowed only for loopback hosts)."
        )
    parts = urlsplit(raw)
    if "\\" in parts.netloc:
        raise ValueError(
            "LAST30DAYS_API_BASE must not contain backslashes in the authority."
        )
    try:
        _ = parts.port
    except ValueError:
        raise ValueError("LAST30DAYS_API_BASE contains an invalid port.") from None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError(
            "LAST30DAYS_API_BASE must not include embedded credentials (userinfo)."
        )
    if parts.query or parts.fragment or "?" in raw or "#" in raw:
        raise ValueError(
            "LAST30DAYS_API_BASE must not include a query string or fragment."
        )
    return raw.rstrip("/")


def _billing_url() -> str:
    """Derive a billing link from the configured base, so no URL is hardcoded.
    Convention: the base is the API-version root (e.g. ends in /api/v1); drop
    that segment and point at the account's billing page."""
    base = _scrub_url_userinfo(_api_base())
    root = re.sub(r"/api/v\d+$", "", base)
    return f"{root}/dashboard/billing"


def _auth_headers() -> dict[str, str]:
    # Key is read at call time and placed only in the request header;
    # it must never be interpolated into any log or output line.
    raw = env.read_secret_env("LAST30DAYS_API_KEY") or ""
    key = raw.strip()
    if not key:
        raise ValueError("LAST30DAYS_API_KEY is empty or missing.")
    if any(ord(ch) < 0x20 or ord(ch) >= 0x7F for ch in key):
        raise ValueError(
            "LAST30DAYS_API_KEY contains invalid characters for an HTTP header."
        )
    return {"Authorization": f"Bearer {key}"}


def _same_origin_redirect_guard(base: str):
    expected_origin = http._request_origin(base)
    return lambda newurl: http._request_origin(newurl) == expected_origin


def submit(query: str, depth: str, register: str = "default", clarify: dict | None = None) -> dict:
    """POST the search. retries=1: a blind POST retry could double-submit."""
    base = _api_base()
    headers = _auth_headers()
    payload = {"query": query, "depth": depth}
    if register != "default":
        payload["register"] = register
    if clarify:
        payload["clarify"] = clarify
    usage.begin("hosted")
    try:
        return http.post(
            f"{base}/search",
            json_data=payload,
            headers=headers,
            retries=1,
            bypass_proxy=providers.is_loopback_http_endpoint(base),
            redirect_guard=_same_origin_redirect_guard(base),
        )
    except ValueError:
        raise ValueError("Invalid hosted API request header or URL.") from None


def poll(search_id: str) -> dict:
    """GET the search row once. Callers own the retry loop (GET is idempotent)."""
    base = _api_base()
    headers = _auth_headers()
    try:
        return http.get(
            f"{base}/search",
            headers=headers,
            params={"id": search_id},
            retries=1,
            bypass_proxy=providers.is_loopback_http_endpoint(base),
            redirect_guard=_same_origin_redirect_guard(base),
        )
    except ValueError:
        raise ValueError("Invalid hosted API request header or URL.") from None


def _parse_error_body(exc: http.HTTPError) -> dict:
    if not exc.body:
        return {}
    try:
        parsed = json.loads(exc.body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _handle_http_error(exc: http.HTTPError) -> int:
    body = _parse_error_body(exc)
    if exc.status_code == 401:
        _err(
            "API key rejected: invalid or revoked. Check "
            "LAST30DAYS_API_KEY (and LAST30DAYS_API_BASE), or unset them "
            "to fall back to local sources."
        )
        return 1
    if exc.status_code == 402:
        _err(f"API: {_scrub_error_text(str(body.get('error') or 'insufficient credits.'))}")
        if body.get("balance") is not None or body.get("needed") is not None:
            _err(
                _scrub_error_text(
                    f"Balance: {body.get('balance')} credits. "
                    f"Needed for this search: {body.get('needed')} credits."
                )
            )
        _err(f"Add credits at {_billing_url()}")
        return 1
    if exc.status_code == 429:
        _err(
            f"API rate limit hit: "
            f"{_scrub_error_text(str(body.get('error') or 'too many requests.'))} "
            "Wait a minute and re-run."
        )
        return 1
    _err(f"API request failed: {_scrub_error_text(str(exc))}")
    return 1


def _angle_queries(resp: dict) -> dict[int, str]:
    """Option index -> the sharpened query the API pairs with that option."""
    queries: dict[int, str] = {}
    angles = resp.get("angles")
    if not isinstance(angles, list):
        return queries
    for index, angle in enumerate(angles, 1):
        if isinstance(angle, dict) and isinstance(angle.get("query"), str) and angle["query"].strip():
            queries[index] = angle["query"].strip()
    return queries


def _handle_clarify(resp: dict) -> int:
    question = resp.get("question") or "The API needs a clarification before searching."
    options = resp.get("options") or []
    queries = _angle_queries(resp)
    _err(f"Clarification needed before this search runs: {question}")
    for index, option in enumerate(options, 1):
        label = option if isinstance(option, str) else json.dumps(option)
        line = f"  {index}. {label}"
        if index in queries:
            line += f" (--clarify-angle {json.dumps(queries[index])})"
        sys.stderr.write(f"{line}\n")
    sys.stderr.flush()
    _err(
        "No search was started. Re-run last30days with the same topic plus "
        "--clarify-angle \"<chosen option's query>\", or --clarify-broadly to "
        "run the topic as asked."
    )
    return EXIT_CLARIFY


def _failure_reason(row: dict) -> str:
    for key in ("error", "synthesis_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown server error"


def _major(version) -> int | None:
    if not isinstance(version, str):
        return None
    match = re.match(r"\s*v?(\d+)\.", version)
    return int(match.group(1)) if match else None


def _local_engine_version() -> str | None:
    try:
        from . import render

        version = render._skill_version()
    except Exception:
        return None
    return None if not version or version == "?" else version


def _note_engine_version(row: dict) -> None:
    """One stderr line when the server engine's major differs from this one."""
    remote = row.get("engine_version")
    local = _local_engine_version()
    remote_major, local_major = _major(remote), _major(local)
    if remote_major is None or local_major is None or remote_major == local_major:
        return
    _err(
        f"This report came from engine v{remote} on the hosted backend; "
        f"this skill is v{local}. Results may differ from a local run."
    )


def _print_new_narration(stderr_blob: str, seen: set[str]) -> bool:
    """Print each '[narrate] step=' line once, verbatim. Returns True if any new."""
    printed = False
    for line in stderr_blob.splitlines():
        if line.startswith(NARRATE_PREFIX) and line not in seen:
            seen.add(line)
            sys.stderr.write(f"{line}\n")
            printed = True
    if printed:
        sys.stderr.flush()
    return printed


def _print_progress_line(elapsed: float, eta_ms) -> None:
    line = f"elapsed {int(elapsed)}s"
    if isinstance(eta_ms, (int, float)) and eta_ms > 0:
        line += f", eta ~{int(eta_ms / 1000)}s"
    _err(line)


def _poll_with_retry(search_id: str) -> dict | None:
    """Poll once, retrying transient network failures. None means give up
    (a user-facing message has already been printed)."""
    last_error: http.HTTPError | None = None
    for attempt in range(POLL_NETWORK_RETRIES):
        try:
            return poll(search_id)
        except ValueError as exc:
            _err(_scrub_error_text(str(exc)))
            return None
        except http.HTTPError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
                _handle_http_error(exc)
                return None
            # Network blip / timeout / 5xx / 429: GET is idempotent, retry.
            last_error = exc
            if attempt < POLL_NETWORK_RETRIES - 1:
                time.sleep(POLL_INITIAL_DELAY)
    _err(
        f"API unreachable while polling search {search_id} "
        f"after {POLL_NETWORK_RETRIES} attempts: {_scrub_error_text(str(last_error))}"
    )
    return None


def _slugify(value: str) -> str:
    return library.filename_slug(library.slugify(value))


def _sanitize_suffix(suffix: str) -> str:
    """Sanitize --save-suffix to a path-safe token (same policy as the local
    save path): lowercase, fold non [a-z0-9] runs to a single hyphen, strip
    edge hyphens. Unlike _slugify there is no fallback token - a suffix that
    sanitizes to empty simply drops, so a suffix can never contribute path
    separators or parent references to the saved filename."""
    return re.sub(r"[^a-z0-9]+", "-", suffix.lower()).strip("-")


def _save_output(topic: str, content: str, emit: str, save_dir: str, suffix: str):
    """Mirror local save_output() naming: <slug>-raw[-suffix].<ext>."""
    from datetime import datetime
    from pathlib import Path

    path = Path(save_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    slug = _slugify(topic)
    extension = "json" if emit == "json" else "md"
    sanitized_suffix = _sanitize_suffix(suffix or "")
    suffix_part = f"-{sanitized_suffix}" if sanitized_suffix else ""
    base = path / f"{slug}-raw{suffix_part}.{extension}"
    date_str = datetime.now().strftime('%Y-%m-%d')
    candidates = [base]
    candidates.append(path / f"{slug}-raw{suffix_part}-{date_str}.{extension}")
    for i in range(1, 100):
        candidates.append(path / f"{slug}-raw{suffix_part}-{date_str}-{i}.{extension}")
    encoded = content.encode("utf-8")
    for candidate in candidates:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
        return candidate
    # Fallback: all 101 candidates existed (extremely unlikely).
    raise RuntimeError(
        f"_save_output: could not find a unique filename after 101 attempts in {path}"
    )


def _render_complete(row: dict, topic: str, emit: str, save_dir, save_suffix: str) -> int:
    synthesis = row.get("synthesis_text") or ""
    raw_markdown = row.get("raw_markdown") or ""
    if emit == "json":
        payload = {
            key: row.get(key)
            for key in ("id", "status", "synthesis_text", "raw_markdown")
            if key in row
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        save_content = rendered
    else:
        # The server report is the content source; it already synthesized.
        # All markdown-ish emit modes print the synthesis text as-is.
        rendered = synthesis or raw_markdown
        save_content = raw_markdown or synthesis
    if save_dir:
        out_path = _save_output(topic, save_content, emit, save_dir, save_suffix)
        sys.stderr.write(f"[last30days] Saved output to {out_path}\n")
        sys.stderr.flush()
    print(rendered)
    return 0


def run_hosted(
    topic: str,
    depth: str,
    *,
    emit: str = "compact",
    save_dir=None,
    save_suffix: str = "",
    register: str = "default",
    clarify: dict | None = None,
) -> int:
    """Submit topic to the remote API, poll to terminal status, render report."""
    try:
        base = _api_base()
    except ValueError as exc:
        _err(_scrub_error_text(str(exc)))
        return 1
    _err(f"Running via last30days API ({_scrub_url_userinfo(base)}), depth={depth}")
    try:
        resp = submit(topic, depth, register=register, clarify=clarify)
    except ValueError as exc:
        _err(_scrub_error_text(str(exc)))
        return 1
    except http.HTTPError as exc:
        return _handle_http_error(exc)

    if resp.get("needs_clarification"):
        return _handle_clarify(resp)

    search_id = resp.get("search_id")
    if not search_id:
        _err(f"Unexpected API response (no search_id): {_scrub_error_text(json.dumps(resp))[:200]}")
        return 1
    _err(f"Search submitted (id: {search_id}). Polling for results...")

    started = time.monotonic()
    delay = POLL_INITIAL_DELAY
    seen_narration: set[str] = set()
    last_progress_line = 0.0
    while True:
        elapsed = time.monotonic() - started
        if elapsed > POLL_TIMEOUT_SECONDS:
            _err(
                f"Search did not finish within "
                f"{POLL_TIMEOUT_SECONDS // 60} minutes (id: {search_id}). "
                "It may still complete server-side; check the dashboard."
            )
            return 1
        time.sleep(delay)
        delay = min(delay * 2, POLL_MAX_DELAY)

        row = _poll_with_retry(search_id)
        if row is None:
            return 1

        status = row.get("status")
        narrated = _print_new_narration(row.get("stderr") or "", seen_narration)
        elapsed = time.monotonic() - started
        if status not in TERMINAL_STATUSES and (
            narrated or elapsed - last_progress_line >= PROGRESS_LINE_INTERVAL or last_progress_line == 0.0
        ):
            _print_progress_line(elapsed, row.get("eta_ms"))
            last_progress_line = elapsed

        if status in TERMINAL_STATUSES:
            _note_engine_version(row)
        if status == "error":
            _err(f"Search failed: {_scrub_error_text(_failure_reason(row))}")
            return 1
        if status == "complete":
            _err(f"Search complete in {int(elapsed)}s.")
            return _render_complete(row, topic, emit, save_dir, save_suffix)
        # pending | running -> keep polling
