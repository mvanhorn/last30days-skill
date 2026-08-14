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
import time
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

# Upper bound on calls per topic search. Each call is an LLM subprocess of
# roughly 15-45s, so depth must not translate into unbounded wall time.
_MAX_FANOUT_CALLS = 4

# Wall-clock ceiling for ALL grok Phase 2 lanes combined. Without it, three
# lanes over three handles is up to 14 sequential LLM subprocess calls bounded
# only by per-call timeouts -- tens of minutes of foreground time for a source
# that now runs by default. Lanes stop issuing queries once this passes and
# return whatever they have.
LANE_BUDGET_SECONDS = 150.0

# Below this, a call cannot plausibly complete (measured calls run 15-45s), so
# the budget is spent rather than overrun. Skipping is strictly better than
# starting a call guaranteed to be killed mid-flight.
_MIN_USEFUL_CALL_SECONDS = 15


def _fanout_queries(topic: str, from_date: str, to_date: str, calls: int) -> List[str]:
    """Distinct query formulations for one topic, widest signal first.

    Each returns at most 10 posts, and the formulations surface different
    sets -- Top vs Latest ordering, and an engagement-floored variant -- so
    fanning out adds coverage rather than repeating one result set.
    """
    window = f"since:{from_date} until:{to_date}"
    variants = [
        f"{topic} {window}",
        f"{topic} {window} min_faves:5",
        f'"{topic}" {window}' if " " in topic else f"{topic} {window} filter:links",
        f"{topic} {window} -filter:replies",
    ]
    return variants[:calls]

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


def _subprocess_env(home: str) -> Dict[str, str]:
    """Minimal environment for the `grok` child process, rooted at a throwaway HOME.

    The child runs with tool permissions bypassed (non-interactivity requires
    it) while its context is filled with retrieved X post text, which is
    attacker-controlled. Stripping credential env vars is necessary but not
    sufficient: this engine writes XAI_API_KEY / AUTH_TOKEN / CT0 /
    SCRAPECREATORS_API_KEY to ``$HOME/.config/last30days/.env``, and ``~/.ssh``
    and ``~/.aws`` sit alongside it. An empty cwd is not a boundary for a
    filesystem-capable agent -- cwd constrains relative paths, not ``$HOME/...``
    reads -- so the child gets its own HOME containing only the Grok credential
    store it actually needs.
    """
    keep = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SystemRoot")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env["HOME"] = home
    if os.name == "nt":
        env["USERPROFILE"] = home
    return env


def _stage_child_home(workdir: str) -> str:
    """Create the child's throwaway HOME holding only the credential file.

    Copies ``auth.json`` alone, never the ``~/.grok`` tree: that directory is
    ~1.6 GB (marketplace cache, bundled runtime, session history), and copying
    it per invocation made a single search take minutes. The child needs the
    credential to authenticate and nothing else -- session history and caches
    are state we specifically do not want a permission-bypassed child to read
    or mutate.

    Copied rather than symlinked so the child cannot follow a link back to the
    real store, and copied rather than shared so it cannot rewrite the user's
    credentials.
    """
    home = os.path.join(workdir, "home")
    store = token_store_path()
    child_store_dir = os.path.join(home, store.parent.name)
    os.makedirs(child_store_dir, mode=0o700, exist_ok=True)
    try:
        if store.is_file():
            shutil.copyfile(store, os.path.join(child_store_dir, store.name))
            os.chmod(os.path.join(child_store_dir, store.name), 0o600)
    except OSError as exc:
        _log(f"could not stage Grok credentials for the child: {exc}")
    return home


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
    is, because the model interpolates a plausible-looking id sequence. Four
    ids is the minimum used here: three gaps are needed before a near-uniform
    step reads as generated rather than coincidental.
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


# Why the most recent parse returned nothing. Lets _run_query distinguish a
# clean empty window (common, and not worth a second LLM call) from a suspect
# response (fabricated ids, a generated sequence, a self-reported
# non-execution), which is the only case retrying can actually fix.
_LAST_REJECTION = {"reason": ""}

_RETRYABLE_REJECTIONS = (
    "failed provenance validation",
    "near-uniform sequence",
    "unparsable date window",
)

_PLACEHOLDER_HANDLES = {"unknown", "n/a", "none", "null", "example", "user", ""}

# X's real handle grammar. Model-reported handles are interpolated into post
# URLs and into the NEXT child prompt, so anything outside this charset is
# rejected rather than passed through: a poisoned post that steers the child
# into emitting a crafted handle line would otherwise reach a prompt slot it
# can close. entity_extract applies the same rule to @mentions.
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")


def _clean_handle(value: str) -> str:
    """Return a grammar-valid handle, or '' when the value is not one."""
    candidate = str(value or "").strip().lstrip("@")
    return candidate if _HANDLE_RE.fullmatch(candidate) else ""

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
        # Fail closed. Skipping the window check would silently disable the
        # module's central provenance guarantee for the whole response.
        return [], "unparsable date window"

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
        if not (lo <= created <= hi.replace(hour=23, minute=59, second=59)):
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
# Engagement counts arrive either literal ("1,462") or display-abbreviated
# ("39K", "1.2M"). Parsing only the leading digits turns 1.2M into 1, which
# does not merely lose precision -- it inverts ranking, placing a viral post
# below one with 500 literal likes.
_INT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*([KMB])?", re.I)
_SUFFIX_MULTIPLIER = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _as_int(value: str) -> Optional[int]:
    match = _INT_RE.search(value or "")
    if not match:
        return None
    number, suffix = match.group(1), match.group(2)
    try:
        parsed = float(number.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        parsed *= _SUFFIX_MULTIPLIER[suffix.lower()]
    return int(parsed)


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
        if match or current:
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
        _LAST_REJECTION["reason"] = reason
        return []
    _LAST_REJECTION["reason"] = ""

    items: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, fields in enumerate(kept, start=1):
        post_id = str(fields.get("post_id") or "").strip()
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        handle = _clean_handle(fields.get("author_handle"))
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
            child_home = _stage_child_home(workdir)
            result = subprocess.run(
                [binary, "-p", prompt, "--permission-mode", "bypassPermissions"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                env=_subprocess_env(child_home),
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
    relevance_topic: str = "",
    deadline: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Run one query, retrying only when the response looks fabricated.

    A clean empty result is NOT retried: an empty window is a common, correct
    outcome (especially for the mention lane on a low-profile handle and for
    the name lane's engagement floor), and re-issuing a byte-identical prompt
    doubles latency and Grok-plan spend to get the same answer.
    """
    timeout = _TIMEOUT_SECONDS.get(depth, _TIMEOUT_SECONDS["default"])
    prompt = _PROMPT.format(tool=tool, query=query, limit=min(limit, _MAX_LIMIT_PER_CALL))
    last_error = ""
    for attempt in range(1, attempts + 1):
        if deadline is not None:
            remaining = deadline - time.monotonic()
            # Never start a call that cannot finish inside the shared budget.
            # A floor here (max(15, ...)) would let the last call overrun the
            # documented ceiling by up to that floor, since the lanes run
            # synchronously with no outer timeout to catch it.
            if remaining < _MIN_USEFUL_CALL_SECONDS:
                return [], last_error or "X lane budget exhausted"
            timeout = min(timeout, int(remaining))
        _log(f"searching: {query}" + (f" (attempt {attempt})" if attempt > 1 else ""))
        response = _invoke(prompt, timeout)
        if response.get("error"):
            last_error = response["error"]
            continue
        items = parse_x_response(
            response,
            topic=relevance_topic or query,
            from_date=from_date,
            to_date=to_date,
        )
        if items:
            return items, ""
        reason = _LAST_REJECTION.get("reason", "")
        if not any(marker in reason for marker in _RETRYABLE_REJECTIONS):
            # Clean empty result: the search ran and found nothing.
            return [], ""
        last_error = reason or "no verified in-window posts returned"
    return [], last_error


def search_x(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search X for a topic, fanning out to reach the depth's target count.

    The underlying tool caps each call at 10 posts, so depth is achieved across
    calls. Without this, grok returned 10 posts at every depth while sitting
    ahead of bird in the chain -- silently downgrading a `--deep` run from 60
    posts to 10.

    Returns {'items': [...]}; 'error' is set only for an actual invocation
    failure. A completed run that found nothing returns an empty list with no
    error, matching bird and xquik -- reporting "no results" as a hard failure
    would make an empty window look like a broken backend.
    """
    target = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    calls = max(1, min(_MAX_FANOUT_CALLS, -(-target // _MAX_LIMIT_PER_CALL)))
    collected: List[Dict[str, Any]] = []
    seen: set = set()
    last_error = ""
    invocation_failed = False
    for mode_query in _fanout_queries(topic, from_date, to_date, calls):
        items, error = _run_query(mode_query, from_date, to_date, depth=depth,
                                  relevance_topic=topic)
        if error and not items:
            last_error = error
            # Distinguish "the CLI failed" from "the search found nothing".
            if "not found" in error or "timed out" in error or "exited" in error:
                invocation_failed = True
        for item in items:
            key = item["url"]
            if key not in seen:
                seen.add(key)
                collected.append(item)
        if len(collected) >= target:
            break
    for index, item in enumerate(collected, start=1):
        item["id"] = f"GK{index}"
    if collected:
        return {"items": collected[:target]}
    if invocation_failed:
        return {"items": [], "error": last_error}
    return {"items": []}


def search_handles(
    handles: List[str],
    topic: str,
    from_date: str,
    to_date: str,
    *,
    count_per: int = 8,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """BY lane: posts authored by each handle.

    ``topic`` is used for relevance ranking only and is never ANDed into the
    query -- doing so was a prior defect that emptied the lane.
    """
    collected: List[Dict[str, Any]] = []
    for handle in handles:
        if deadline is not None and time.monotonic() >= deadline:
            _log("lane budget exhausted; skipping remaining handles")
            break
        clean = _clean_handle(handle)
        if not clean:
            continue
        items, _ = _run_query(
            f"from:{clean} since:{from_date} until:{to_date}",
            from_date, to_date, limit=count_per, relevance_topic=topic,
            attempts=1, deadline=deadline,
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
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """ABOUT lane (mention form): posts @-mentioning each handle."""
    collected: List[Dict[str, Any]] = []
    for handle in handles:
        if deadline is not None and time.monotonic() >= deadline:
            _log("lane budget exhausted; skipping remaining handles")
            break
        clean = _clean_handle(handle)
        if not clean:
            continue
        items, _ = _run_query(
            f"@{clean} -from:{clean} since:{from_date} until:{to_date}",
            from_date, to_date, limit=count_per, relevance_topic=topic,
            attempts=1, deadline=deadline,
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
    deadline: Optional[float] = None,
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
    # Mirror bird_x.build_topic_query's guard: an unbalanced quote reads as an
    # unterminated phrase and matches nothing.
    if name.count('"') % 2:
        name = name.replace('"', " ").strip()
    phrase = f'"{name}"' if " " in name else name
    excludes = " ".join(
        f"-from:{clean}"
        for clean in (_clean_handle(h) for h in (exclude_handles or []))
        if clean
    )
    query = " ".join(
        part for part in
        [phrase, excludes, f"min_faves:{min_faves}", f"since:{from_date}", f"until:{to_date}"]
        if part
    )
    items, _ = _run_query(
        query, from_date, to_date, limit=count_per, attempts=1, deadline=deadline,
    )
    blocked = {c.lower() for c in (_clean_handle(h) for h in (exclude_handles or [])) if c}
    return [i for i in items if i["author_handle"].lower() not in blocked]
