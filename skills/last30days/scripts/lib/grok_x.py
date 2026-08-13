"""X (Twitter) search via the Grok CLI — no X credential of any kind.

The `grok` CLI (https://x.ai/cli) exposes X search tools natively
(`x_keyword_search`, `x_semantic_search`, `x_thread_fetch`, `x_user_search`).
Reaching X through it needs no X account, no browser cookies, and no
`XAI_API_KEY` — only an installed and signed-in `grok`.

Install: curl -fsSL https://x.ai/cli/install.sh | bash   (or npm i -g @xai-official/grok)
Auth:    grok login

Two invocation constraints, both measured, both load-bearing:

* **Never pass `--json-schema`.** Constrained decoding competes with tool use:
  the search silently does not run and the model fills the schema's required
  fields from training data instead. Measured with an interleaved A/B
  controlling for time: plain output returned verified in-window posts on 4 of
  4 calls, `--json-schema` on 1 of 4.
* **Never pass `--tools`.** Two runs produced no output in 7 minutes and were
  killed; the identical prompts without it completed normally.

Because retrieval is performed by a language model rather than an API client,
its output can be *confidently wrong* in a way no other backend's can. Every
returned post is therefore validated against the requested window via its
snowflake timestamp before it is allowed into the item flow — see
`_validate_items`. Author matching and schema shape are not sufficient: a
fabricated post carries a plausible handle and a numeric id by construction.
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import log
from .relevance import token_overlap_relevance as _compute_relevance


def _log(msg: str) -> None:
    log.source_log("Grok", msg, tty_only=False)


# Posts requested per call. The tool caps `limit` at 10, so depth is achieved
# by fanning out across queries rather than by raising a single call's limit.
_MAX_LIMIT_PER_CALL = 10

DEPTH_CONFIG = {
    "quick": 10,
    "default": 30,
    "deep": 60,
}

# Wall-clock ceiling for one `grok` invocation. A run that blocks on an
# unexpected interactive prompt would otherwise hang indefinitely, and a
# non-daemon worker can outlive a wall-clock budget.
_TIMEOUT_SECONDS = {"quick": 120, "default": 240, "deep": 360}

# Twitter/X snowflake epoch (2010-11-04T01:42:54.657Z) in milliseconds.
_SNOWFLAKE_EPOCH_MS = 1288834974657

_AUTH_STORE = Path.home() / ".grok" / "auth.json"

# Substrings that indicate stored credentials. Deliberately format-agnostic:
# the observed store is a JSON object keyed by issuer and principal, but the
# shape is the vendor's to change. Mirrors xurl_x's marker scan.
_TOKEN_STORE_MARKERS = ("refresh_token", "access_token", "auth_mode", '"key"')

AUTH_OK = "ok"            # token store present with stored credentials
AUTH_MISSING = "missing"  # no token store, or no credentials stored in it
AUTH_ERROR = "error"      # token store exists but could not be read

_availability_cache: Optional[bool] = None


def clear_availability_cache() -> None:
    """Reset the memoized is_available() result (tests, or a re-check after login)."""
    global _availability_cache
    _availability_cache = None


def binary_path() -> Optional[str]:
    """Resolved `grok` path, or None when it is not on PATH.

    PATH resolution is the gate, not file existence: a binary present on disk
    but off the agent subprocess PATH is not installed as far as the engine is
    concerned.
    """
    return shutil.which("grok")


def token_store_path() -> Path:
    return _AUTH_STORE


def stored_auth_status() -> Tuple[str, str]:
    """Local-only auth check: filesystem read, no subprocess, no network.

    This is the doctor / --diagnose / --preflight surface. It must never spawn
    a process: the whole-doctor-path test patches ``subprocess.run`` to raise,
    and shelling out to `grok` here would fail it.
    """
    path = token_store_path()
    try:
        if not path.exists():
            return AUTH_MISSING, f"no Grok credential store at {path}"
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return AUTH_ERROR, f"{type(exc).__name__}: {exc}"
    if any(marker in raw for marker in _TOKEN_STORE_MARKERS):
        # Deliberately reports the path only. Never echo store contents: the
        # file holds an access key and refresh token, and doctor output is
        # routinely pasted into issue reports.
        return AUTH_OK, f"stored Grok credentials found in {path}"
    return AUTH_MISSING, f"Grok credential store at {path} has no stored credentials"


def has_stored_auth() -> bool:
    return binary_path() is not None and stored_auth_status()[0] == AUTH_OK


def is_available() -> bool:
    """Research-time availability. May spawn a subprocess; memoized per process."""
    global _availability_cache
    if _availability_cache is None:
        _availability_cache = _is_available_uncached()
    return _availability_cache


def _is_available_uncached() -> bool:
    if binary_path() is None:
        return False
    return stored_auth_status()[0] == AUTH_OK


def _subprocess_env() -> Dict[str, str]:
    """Minimal environment for the `grok` child process.

    The child runs with tool permissions bypassed (non-interactivity requires
    it) while its context is filled with retrieved X post text, which is
    attacker-controlled. Handing it the full parent environment would expose
    every credential the engine holds to a prompt-injection payload in a
    search result. Pass only what the CLI needs to locate itself and its
    credentials.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USERPROFILE", "SystemRoot")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


def _decode_snowflake(post_id: str) -> Optional[datetime]:
    """Recover a post's creation time from its id, with no network call."""
    try:
        value = int(str(post_id).strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(
            ((value >> 22) + _SNOWFLAKE_EPOCH_MS) / 1000, tz=timezone.utc
        )
    except (OverflowError, OSError, ValueError):
        return None


def _looks_generated(ids: List[str]) -> bool:
    """True when ids form a near-uniform arithmetic run.

    Real ranked results are not evenly spaced in time. A fabricated set often
    is, because the model interpolates a plausible-looking id sequence. Three
    ids is the minimum that can show a consistent step.
    """
    numeric = []
    for pid in ids:
        try:
            numeric.append(int(pid))
        except (TypeError, ValueError):
            return False
    if len(numeric) < 4:
        return False
    numeric.sort()
    gaps = [b - a for a, b in zip(numeric, numeric[1:])]
    if any(g <= 0 for g in gaps):
        return False
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return False
    # Every gap within 5% of the mean is not something real timelines do.
    return all(abs(g - mean) / mean < 0.05 for g in gaps)


_PLACEHOLDER_HANDLES = {"unknown", "n/a", "none", "null", "example", "user", ""}

_NON_EXECUTION_MARKERS = (
    "was not executed",
    "not executed in this turn",
    "unable to search",
    "could not search",
    "no tool call",
    "tool not available",
)


def _validate_items(
    items: List[Dict[str, Any]],
    from_date: str,
    to_date: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """Drop anything that did not come from a real in-window post.

    Returns (kept, reason). A non-empty reason means the response should be
    treated as a non-execution to retry rather than as a thin result.
    """
    if not items:
        return [], "no items parsed"

    try:
        lo = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        hi = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        lo = hi = None

    kept: List[Dict[str, Any]] = []
    for item in items:
        text = str(item.get("text") or "").lower()
        if any(marker in text for marker in _NON_EXECUTION_MARKERS):
            continue
        handle = str(item.get("author_handle") or "").strip().lstrip("@").lower()
        if handle in _PLACEHOLDER_HANDLES:
            continue
        created = _decode_snowflake(item.get("post_id"))
        if created is None:
            continue
        if lo is not None and not (lo <= created <= hi.replace(hour=23, minute=59, second=59)):
            continue
        kept.append(item)

    if not kept:
        return [], "every item failed provenance validation (window/handle/id)"
    if _looks_generated([str(i.get("post_id")) for i in kept]):
        return [], "post ids form a near-uniform sequence (generated, not retrieved)"
    return kept, ""


# --- prose parsing ---------------------------------------------------------

_FIELD_ALIASES = {
    "id": "post_id",
    "post id": "post_id",
    "conversation id": "conversation_id",
    "author": "author",
    "handle": "author_handle",
    "text": "text",
    "content": "text",
    "created_at": "created_at",
    "timestamp": "created_at",
    "likes": "likes",
    "reposts": "reposts",
    "retweets": "reposts",
    "replies": "replies",
    "quotes": "quotes",
    "bookmarks": "bookmarks",
    "views": "views",
}

_FIELD_LINE = re.compile(
    r"^[\s\-*>]*\**\s*([A-Za-z][A-Za-z _]{1,20}?)\**\s*[:=]\s*(.+?)\s*$"
)
_INT_RE = re.compile(r"-?\d[\d,]*")


def _as_int(value: str) -> Optional[int]:
    match = _INT_RE.search(value or "")
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_date(value: str) -> Optional[str]:
    value = (value or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a %b %d %H:%M:%S %z %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _split_blocks(text: str) -> List[str]:
    """Split the model's prose into per-post blocks.

    Keyed on the post-id field starting a new record rather than on any
    heading style, because the narration around the blocks varies run to run.
    """
    blocks: List[str] = []
    current: List[str] = []
    for line in (text or "").splitlines():
        match = _FIELD_LINE.match(line)
        key = _FIELD_ALIASES.get(match.group(1).strip().lower()) if match else None
        if key == "post_id" and current:
            blocks.append("\n".join(current))
            current = []
        if match:
            current.append(line)
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def parse_x_response(
    response: Dict[str, Any],
    topic: str = "",
    from_date: str = "",
    to_date: str = "",
) -> List[Dict[str, Any]]:
    """Parse a grok response into normalized X item dicts.

    Total: returns [] on error rather than raising.
    """
    if not isinstance(response, dict):
        return []
    if response.get("error"):
        _log(f"error: {response['error']}")
        return []

    raw: List[Dict[str, Any]] = []
    for block in _split_blocks(response.get("text") or ""):
        fields: Dict[str, Any] = {}
        for line in block.splitlines():
            match = _FIELD_LINE.match(line)
            if not match:
                continue
            key = _FIELD_ALIASES.get(match.group(1).strip().lower())
            if key and key not in fields:
                value = match.group(2).strip()
                # Field values arrive with varying markdown decoration
                # (`- **id:** 123`), so strip emphasis and code marks.
                value = value.strip("*").strip().strip("`").strip()
                fields[key] = value
        if fields.get("post_id"):
            raw.append(fields)

    kept, reason = _validate_items(raw, from_date, to_date) if from_date else (raw, "")
    if reason:
        _log(f"rejected response: {reason}")
        return []

    items: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, fields in enumerate(kept, start=1):
        post_id = str(fields.get("post_id") or "").strip()
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        handle = str(fields.get("author_handle") or "").strip().lstrip("@")
        if not handle:
            author = str(fields.get("author") or "")
            match = re.search(r"@([A-Za-z0-9_]{1,15})", author)
            handle = match.group(1) if match else ""
        if not handle:
            continue
        text = str(fields.get("text") or "").strip()[:500]
        engagement = {
            "likes": _as_int(str(fields.get("likes", ""))),
            "reposts": _as_int(str(fields.get("reposts", ""))),
            "replies": _as_int(str(fields.get("replies", ""))),
            "quotes": _as_int(str(fields.get("quotes", ""))),
        }
        items.append({
            "id": f"GK{index}",
            "text": text,
            "url": f"https://x.com/{handle}/status/{post_id}",
            "author_handle": handle,
            "date": _parse_date(str(fields.get("created_at", ""))),
            "engagement": engagement if any(v is not None for v in engagement.values()) else None,
            "why_relevant": "",
            "relevance": _compute_relevance(topic, text) if topic else 0.7,
        })
    return items


# --- invocation ------------------------------------------------------------

_PROMPT = """Use {tool} with query '{query}', mode Top, limit {limit}.

Report every post the tool returned, one block per post, using exactly these
field labels on their own lines:

id: <numeric post id>
handle: <author handle without @>
created_at: <post timestamp>
likes: <number>
reposts: <number>
replies: <number>
quotes: <number>
text: <full post text on one line>

Report only posts the tool actually returned. If the tool returned nothing or
could not run, say so plainly and report no post blocks. Do not supply posts
from your own knowledge."""


def _invoke(prompt: str, timeout: int) -> Dict[str, Any]:
    """Run `grok` once. Never raises; every failure returns {'error': str}."""
    binary = binary_path()
    if binary is None:
        return {"error": "grok CLI not found on PATH"}
    # Isolated working directory: the child has tool permissions bypassed and
    # its context carries untrusted post text, so give it an empty directory
    # rather than the user's repository to act in.
    try:
        with tempfile.TemporaryDirectory(prefix="last30days-grok-") as workdir:
            result = subprocess.run(
                [binary, "-p", prompt, "--permission-mode", "bypassPermissions"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                env=_subprocess_env(),
            )
    except FileNotFoundError:
        return {"error": "grok CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"grok CLI timed out after {timeout}s"}
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - search_x must never raise
        return {"error": f"{type(exc).__name__}: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        return {"error": f"grok CLI exited {result.returncode}: {detail}"}
    return {"text": result.stdout or ""}


def _run_query(
    query: str,
    from_date: str,
    to_date: str,
    *,
    tool: str = "x_keyword_search",
    limit: int = _MAX_LIMIT_PER_CALL,
    depth: str = "default",
    attempts: int = 2,
) -> Tuple[List[Dict[str, Any]], str]:
    """Run one query, retrying once when the response fails provenance."""
    timeout = _TIMEOUT_SECONDS.get(depth, _TIMEOUT_SECONDS["default"])
    prompt = _PROMPT.format(tool=tool, query=query, limit=min(limit, _MAX_LIMIT_PER_CALL))
    last_error = ""
    for attempt in range(1, attempts + 1):
        _log(f"searching: {query}" + (f" (attempt {attempt})" if attempt > 1 else ""))
        response = _invoke(prompt, timeout)
        if response.get("error"):
            last_error = response["error"]
            continue
        items = parse_x_response(response, topic=query, from_date=from_date, to_date=to_date)
        if items:
            return items, ""
        last_error = "no verified in-window posts returned"
    return [], last_error


def search_x(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search X for a topic. Returns {'items': [...]} or {'error': str}."""
    query = f"{topic} since:{from_date} until:{to_date}"
    items, error = _run_query(query, from_date, to_date, depth=depth)
    if items:
        return {"items": items}
    return {"items": [], "error": error or "no results"}


def search_handles(
    handles: List[str],
    topic: str,
    from_date: str,
    to_date: str,
    *,
    count_per: int = 8,
) -> List[Dict[str, Any]]:
    """BY lane: posts authored by each handle.

    ``topic`` is used for relevance ranking only and is never ANDed into the
    query -- doing so was a prior defect that emptied the lane.
    """
    collected: List[Dict[str, Any]] = []
    for handle in handles:
        clean = handle.strip().lstrip("@")
        if not clean:
            continue
        items, _ = _run_query(
            f"from:{clean} since:{from_date} until:{to_date}",
            from_date, to_date, limit=count_per,
        )
        # Enforce the author constraint client-side: operator fidelity is not
        # guaranteed. A measured `from:` query returned a different account.
        collected.extend(
            i for i in items
            if i["author_handle"].lower() == clean.lower()
        )
    return collected


def search_mentions(
    handles: List[str],
    from_date: str,
    to_date: str,
    *,
    topic: str = "",
    count_per: int = 5,
) -> List[Dict[str, Any]]:
    """ABOUT lane (mention form): posts @-mentioning each handle."""
    collected: List[Dict[str, Any]] = []
    for handle in handles:
        clean = handle.strip().lstrip("@")
        if not clean:
            continue
        items, _ = _run_query(
            f"@{clean} -from:{clean} since:{from_date} until:{to_date}",
            from_date, to_date, limit=count_per,
        )
        # Enforce the exclusion client-side too: a measured run carrying
        # `-from:X` still returned a post authored by X.
        collected.extend(
            i for i in items
            if i["author_handle"].lower() != clean.lower()
        )
    return collected


def search_name(
    name: str,
    from_date: str,
    to_date: str,
    *,
    exclude_handles: Optional[List[str]] = None,
    count_per: int = 8,
    min_faves: int = 2,
) -> List[Dict[str, Any]]:
    """ABOUT lane (name form): posts naming the subject in plain text.

    Not redundant with the mention lane and not a fallback for it. Most talk
    about a person or company never @-mentions them -- people write "Bentgo
    lunch box from Costco", not "@Bentgo lunch box from Costco". A modest
    engagement floor applies here only, because a bare name query is the
    widest and noisiest of the three lanes.
    """
    name = (name or "").strip()
    if not name:
        return []
    phrase = f'"{name}"' if " " in name else name
    excludes = " ".join(
        f"-from:{h.strip().lstrip('@')}"
        for h in (exclude_handles or [])
        if h and h.strip()
    )
    query = " ".join(
        part for part in
        [phrase, excludes, f"min_faves:{min_faves}", f"since:{from_date}", f"until:{to_date}"]
        if part
    )
    items, _ = _run_query(query, from_date, to_date, limit=count_per)
    blocked = {h.strip().lstrip("@").lower() for h in (exclude_handles or []) if h}
    return [i for i in items if i["author_handle"].lower() not in blocked]
