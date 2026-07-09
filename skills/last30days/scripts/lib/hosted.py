"""Remote API client for last30days (optional hosted-backend mode).

When both LAST30DAYS_API_KEY and LAST30DAYS_API_BASE are set, the engine
submits the topic to the configured remote API, polls until the run reaches a
terminal status, streams narration progress to stderr, and renders the
server's report. No local provider keys are required in this mode. The
endpoint comes only from LAST30DAYS_API_BASE - there is no built-in default.

Contract (API v1):
  POST {base}/search   Authorization: Bearer <key>
                       {"query": ..., "depth": "quick"|"default"|"deep"}
                       -> 200 {"search_id": "<uuid>", "status": "running"}
                       -> 200 clarify payload {"needs_clarification": true, ...}
                       -> 401 {"error"} / 402 {"error","requires_credits",
                          "balance","needed"} / 429 {"error"}
  GET  {base}/search?id=<uuid>  same auth header; poll until status is
                       terminal ("complete" | "error"). Running rows carry
                       "stderr" (narration + engine lines) and "eta_ms";
                       terminal complete rows carry "synthesis_text" and
                       "raw_markdown" (stderr stripped).

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

from . import env, http
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


def _err(msg: str) -> None:
    source_log("hosted", msg, tty_only=False)


def _api_base() -> str:
    # Endpoint comes only from the environment - no built-in default. Hosted
    # mode is gated on this being set (see last30days.py), so by the time this
    # is called it is populated; an empty value means "not configured".
    return (os.environ.get("LAST30DAYS_API_BASE") or "").rstrip("/")


def _billing_url() -> str:
    """Derive a billing link from the configured base, so no URL is hardcoded.
    Convention: the base is the API-version root (e.g. ends in /api/v1); drop
    that segment and point at the account's billing page."""
    base = _api_base()
    root = re.sub(r"/api/v\d+$", "", base)
    return f"{root}/dashboard/billing"


def _auth_headers() -> dict[str, str]:
    # Key is read at call time and placed only in the request header;
    # it must never be interpolated into any log or output line.
    key = env.read_secret_env("LAST30DAYS_API_KEY") or ""
    return {"Authorization": f"Bearer {key}"}


def submit(query: str, depth: str) -> dict:
    """POST the search. retries=1: a blind POST retry could double-submit."""
    return http.post(
        f"{_api_base()}/search",
        json_data={"query": query, "depth": depth},
        headers=_auth_headers(),
        retries=1,
    )


def poll(search_id: str) -> dict:
    """GET the search row once. Callers own the retry loop (GET is idempotent)."""
    return http.get(
        f"{_api_base()}/search",
        headers=_auth_headers(),
        params={"id": search_id},
        retries=1,
    )


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
        _err(f"API: {body.get('error') or 'insufficient credits.'}")
        if body.get("balance") is not None or body.get("needed") is not None:
            _err(
                f"Balance: {body.get('balance')} credits. "
                f"Needed for this search: {body.get('needed')} credits."
            )
        _err(f"Add credits at {_billing_url()}")
        return 1
    if exc.status_code == 429:
        _err(
            f"API rate limit hit: "
            f"{body.get('error') or 'too many requests.'} "
            "Wait a minute and re-run."
        )
        return 1
    _err(f"API request failed: {exc}")
    return 1


def _handle_clarify(resp: dict) -> int:
    question = resp.get("question") or "The API needs a clarification before searching."
    options = resp.get("options") or []
    _err(f"Clarification needed before this search runs: {question}")
    for index, option in enumerate(options, 1):
        label = option if isinstance(option, str) else json.dumps(option)
        sys.stderr.write(f"  {index}. {label}\n")
    sys.stderr.flush()
    _err(
        "No search was started. Re-run last30days with the chosen angle "
        "folded into the topic text."
    )
    return EXIT_CLARIFY


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
        f"after {POLL_NETWORK_RETRIES} attempts: {last_error}"
    )
    return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "last30days"


def _save_output(topic: str, content: str, emit: str, save_dir: str, suffix: str):
    """Mirror local save_output() naming: <slug>-raw[-suffix].<ext>."""
    from datetime import datetime
    from pathlib import Path

    path = Path(save_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    slug = _slugify(topic)
    extension = "json" if emit == "json" else "md"
    suffix_part = f"-{suffix}" if suffix else ""
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


def run_hosted(topic: str, depth: str, *, emit: str = "compact",
               save_dir=None, save_suffix: str = "") -> int:
    """Submit topic to the remote API, poll to terminal status, render report."""
    _err(f"Running via last30days API ({_api_base()}), depth={depth}")
    try:
        resp = submit(topic, depth)
    except http.HTTPError as exc:
        return _handle_http_error(exc)

    if resp.get("needs_clarification"):
        return _handle_clarify(resp)

    search_id = resp.get("search_id")
    if not search_id:
        _err(f"Unexpected API response (no search_id): {json.dumps(resp)[:200]}")
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

        if status == "error":
            _err(f"Search failed: {row.get('error') or 'unknown server error'}")
            return 1
        if status == "complete":
            _err(f"Search complete in {int(elapsed)}s.")
            return _render_complete(row, topic, emit, save_dir, save_suffix)
        # pending | running -> keep polling
