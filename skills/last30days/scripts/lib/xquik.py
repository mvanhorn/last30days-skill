"""Xquik X search source for the v3.0.0 last30days pipeline.

Uses the Xquik REST API (https://xquik.com/api/v1) to search X/Twitter
with full engagement metrics (likes, retweets, replies, quotes, views,
bookmarks). Requires an API key from xquik.com.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import health, http, log
from .dates import x_date_filters
from .relevance import token_overlap_relevance as _compute_relevance
from .x_api import _clean_handle, is_own_post

# Per-process probe cache: (state, reason). state is "unset" until probed, then
# True (funded/working) | False (auth/payment failure) | None (inconclusive).
_probe_cache: tuple = ("unset", "")

# Depth configurations: number of results to request per query
DEPTH_CONFIG = {
    "quick": {"limit": 10, "queries": 1},
    "default": {"limit": 20, "queries": 2},
    "deep": {"limit": 40, "queries": 3},
}

_BASE_URL = "https://xquik.com/api/v1"


def _log(msg: str):
    log.source_log("Xquik", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject for X search queries."""
    from .query import extract_core_subject
    return extract_core_subject(topic, max_words=5, strip_suffixes=True)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_TWEET_ID_DIGITS = 20


def _clean_tweet_id(value: Any) -> str:
    """Return a positive ASCII numeric tweet id, or '' when invalid."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        if value <= 0 or value.bit_length() > 67:
            return ""
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        return ""
    if len(candidate) > _MAX_TWEET_ID_DIGITS or not (candidate.isascii() and candidate.isdigit()):
        return ""
    return candidate if candidate.lstrip("0") else ""


def _sanitize_topic_query(query_text: str) -> str:
    """Sanitize a topic query before appending engine-owned date filters.

    Neutralizes backslashes, balances quotes and parentheses, drops unquoted X
    search operators (colon-containing tokens and leading-``-`` negations,
    preserving trailing-colon subject punctuation), drops empty groups or
    dangling ``OR`` operators, and parenthesizes top-level ``OR`` expressions
    so trailing date filters apply to every branch.
    """
    if not isinstance(query_text, str):
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(
        " ",
        query_text.replace("\\", " ").replace("“", '"').replace("”", '"'),
    ).strip()
    if not cleaned:
        return ""

    chars = list(cleaned)
    in_quote = False
    open_quote_idx = -1
    for i, ch in enumerate(chars):
        if ch == '"':
            if in_quote:
                in_quote = False
                open_quote_idx = -1
            else:
                in_quote = True
                open_quote_idx = i
    if in_quote and open_quote_idx >= 0:
        chars[open_quote_idx] = " "

    in_quote = False
    paren_stack: List[int] = []
    for i, ch in enumerate(chars):
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                paren_stack.append(i)
            elif ch == ")":
                if paren_stack:
                    paren_stack.pop()
                else:
                    chars[i] = " "
    for idx in paren_stack:
        chars[idx] = " "

    balanced = "".join(chars)
    tokens: List[str] = []
    i = 0
    n = len(balanced)
    while i < n:
        ch = balanced[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = balanced.find('"', i + 1)
            if end == -1:
                break
            inner = " ".join(balanced[i + 1:end].split())
            if inner:
                tokens.append(f'"{inner}"')
            i = end + 1
            continue
        if ch in ("(", ")"):
            tokens.append(ch)
            i += 1
            continue
        start = i
        while i < n and not balanced[i].isspace() and balanced[i] not in ('"', "(", ")"):
            i += 1
        word = balanced[start:i]
        if word.endswith(":") and word.count(":") == 1:
            word = word[:-1]
        if not word or ":" in word or word.startswith("-"):
            continue
        tokens.append(word)

    changed = True
    while changed and tokens:
        changed = False
        pruned: List[str] = []
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            if tok == "(" and idx + 1 < len(tokens) and tokens[idx + 1] == ")":
                idx += 2
                changed = True
                continue
            if tok == "OR":
                prev = pruned[-1] if pruned else None
                nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
                if prev in (None, "(", "OR") or nxt in (None, ")", "OR"):
                    idx += 1
                    changed = True
                    continue
            pruned.append(tok)
            idx += 1
        tokens = pruned

    if not tokens:
        return ""

    depth = 0
    has_top_level_or = False
    parts: List[str] = []
    for tok in tokens:
        if tok == "(":
            depth += 1
            if parts and parts[-1] != "(":
                parts.append(" ")
            parts.append("(")
        elif tok == ")":
            depth = max(0, depth - 1)
            parts.append(")")
        else:
            if tok == "OR" and depth == 0:
                has_top_level_or = True
            if parts and parts[-1] != "(":
                parts.append(" ")
            parts.append(tok)

    reconstructed = "".join(parts).strip()
    if not reconstructed:
        return ""
    if has_top_level_or:
        return f"({reconstructed})"
    return reconstructed


def expand_xquik_queries(topic: str, depth: str) -> List[str]:
    """Generate query variants based on depth.

    Args:
        topic: Research topic
        depth: "quick", "default", or "deep"

    Returns:
        List of query strings (1 for quick, 2 for default, 3 for deep).
    """
    core = _extract_core_subject(topic)
    # Anti-bare-generic guard (#607): never let the core collapse to a single
    # bare token when the topic carries more — a lone generic word floods X with
    # off-topic collisions. Fall back to the full multi-word topic as the anchor.
    topic_clean = topic.strip()
    if len(core.split()) <= 1 and len(topic_clean.split()) > 1 and core.lower() != topic_clean.lower():
        core = topic_clean
    queries = [core]

    # Add original topic if meaningfully different
    if topic.lower().strip() != core.lower().strip():
        queries.append(topic.strip())

    # Add compound term variant for deep searches
    if len(queries) < 3:
        from .query import extract_compound_terms
        compounds = extract_compound_terms(topic)
        clean_compounds = [t.replace('"', "").strip() for t in compounds[:3] if t.replace('"', "").strip()]
        if clean_compounds:
            or_parts = " OR ".join(f'"{t}"' for t in clean_compounds)
            queries.append(f"({or_parts})")

    cap = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])["queries"]
    return queries[:cap]


def search_xquik(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
    deadline: float | None = None,
    cancel: Any = None,
) -> Dict[str, Any]:
    """Search X via Xquik REST API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: Inclusive end date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        token: Xquik API key
        deadline: Optional shared wall-clock deadline (``time.monotonic()``
            instant) from the X backend chain. Queries past the deadline are
            never started; the per-request timeout/retry pair still bounds
            each call (mirrors ``x_api``'s TIMEOUT_SECONDS/RETRIES pattern).

    Returns:
        Dict with "items" list and optional "error" string.
    """
    if not token:
        return {"items": [], "error": "No XQUIK_API_KEY configured"}

    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    queries = expand_xquik_queries(topic, depth)
    all_items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    failures: List[str] = []
    failure_states: List[tuple[str, str]] = []
    stop_reasons: List[str] = []
    window = (from_date, to_date)
    date_filters = x_date_filters(from_date, to_date)

    for query_text in queries:
        if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
            _log("chain deadline reached; skipping remaining queries")
            return {"items": all_items, "error": "Xquik research cancelled or timed out"}
        safe_query = _sanitize_topic_query(query_text)
        if not safe_query:
            continue
        q = f"{safe_query} {date_filters}"
        items, auth_error = _execute_search(
            q, cfg["limit"], token,
            label=safe_query, id_prefix="XQ",
            seen_ids=seen_ids, relevance_query=query_text,
            index_offset=len(all_items),
            deadline_monotonic=deadline,
            failure_out=failures,
            failure_states_out=failure_states,
            stop_reasons=stop_reasons,
            max_429_retries=1,
            include_label_in_failure=False,
            cancel=cancel,
            window=window,
        )
        if auth_error:
            # Auth/payment failure is fatal for the whole source (e.g. 401/403,
            # and 402-unpaid surfaced via U5 diagnose) — return it so the caller
            # settles honestly instead of silently empty.
            return {"items": [], "error": auth_error}
        all_items.extend(items)
        if stop_reasons:
            if "rate_limited" in stop_reasons:
                _log("rate limited (HTTP 429); skipping remaining queries")
            break

    if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
        return {"items": all_items, "error": "Xquik research cancelled or timed out"}
    if not all_items and (failure_states or failures):
        if failure_states:
            _, winning_error = min(
                failure_states,
                key=lambda pair: health.FAILURE_SPECIFICITY.get(pair[0], 9),
            )
        else:
            winning_error = failures[0]
        return {"items": [], "error": winning_error}
    return {"items": all_items}


def _execute_search(
    q: str,
    limit: int,
    token: str,
    *,
    label: str,
    id_prefix: str,
    seen_ids: set[str],
    relevance_query: str,
    index_offset: int = 0,
    deadline_monotonic: float | None = None,
    failure_out: Optional[List[str]] = None,
    failure_states_out: Optional[List[tuple[str, str]]] = None,
    stop_reasons: Optional[List[str]] = None,
    max_429_retries: Optional[int] = None,
    include_label_in_failure: bool = True,
    cancel: Any = None,
    window: Optional[tuple[str, str]] = None,
) -> tuple[List[Dict[str, Any]], str | None]:
    """Run one Xquik search call and parse its tweets.

    Returns ``(items, auth_error)``. ``auth_error`` is a non-empty string only
    on a fatal auth/payment failure (401/403); transient/HTTP errors log and
    return ``([], None)`` so one bad lane never discards another's results.

    Non-fatal failures (429, 5xx, network error, unparseable payload) append a
    reason to ``failure_out`` instead. They stay out of the return value
    because the caller must keep going to the next handle, but a caller that
    stopped there would report the lane as empty rather than as failed.
    ``relevance_query`` (the topic) is what items are scored against — for the
    handle lanes that differs from the search query (``from:handle``).
    ``index_offset`` keeps item ids unique across multiple calls that share an
    accumulator (multi-query topic search, per-handle lanes).
    ``deadline_monotonic`` bounds the request including retries at the
    transport (mirrors ``x_api._get``).
    """
    label_suffix = f" for '{label}'" if include_label_in_failure else ""

    def _record_failure(state: str, msg: str) -> None:
        if failure_out is not None:
            failure_out.append(msg)
        if failure_states_out is not None:
            failure_states_out.append((state, msg))

    full_url = f"{_BASE_URL}/x/tweets/search?q={_url_encode(q)}&queryType=Top&limit={limit}"
    _log(f"Searching: {label}")
    try:
        request_headers = {"X-Api-Key": token}
        get_kwargs: Dict[str, Any] = {
            "headers": request_headers,
            "timeout": 30,
            "retries": 2,
            "deadline_monotonic": deadline_monotonic,
            "cancel": cancel,
        }
        if max_429_retries is not None:
            get_kwargs["max_429_retries"] = max_429_retries
        response = http.get(full_url, **get_kwargs)
    except http.HTTPError as exc:
        status = getattr(exc, "status_code", None)
        if status == 402:
            # Unpaid key — fatal for the source, and surfaced on the real search
            # path (not just --diagnose) so a live run reports it instead of
            # settling silently empty. The X retrieval branch classifies by
            # message text only, so the detail carries the "payment required"
            # marker that http.classify_failure maps to PAYMENT_REQUIRED.
            return [], "Xquik key unpaid: payment required (402)"
        if status in (401, 403):
            return [], f"Xquik auth failed ({status})"
        _log(f"HTTP error for '{label}': {exc}")
        if status == 429:
            exc_text = str(exc)
            lowered = exc_text.lower()
            has_rate_limit_marker = any(
                marker in lowered
                for marker in ("http 429", "status 429", "rate limit", "too many requests")
            )
            msg = (
                f"Xquik HTTP error{label_suffix}: {exc_text}"
                if has_rate_limit_marker
                else f"Xquik HTTP 429 rate limit{label_suffix}: {exc_text}"
            )
            _record_failure(health.RATE_LIMITED, msg)
            if stop_reasons is not None:
                stop_reasons.append("rate_limited")
            return [], None
        if isinstance(exc, http.DeadlineExceeded):
            _record_failure(health.TIMEOUT, f"Xquik timed out{label_suffix}: {exc}")
            if stop_reasons is not None:
                stop_reasons.append("deadline")
            return [], None
        exc_state = getattr(exc, "outcome_state", None)
        if exc_state == health.TIMEOUT:
            exc_text = str(exc)
            lowered = exc_text.lower()
            msg = (
                f"Xquik HTTP error{label_suffix}: {exc_text}"
                if "timed out" in lowered or "timeout" in lowered
                else f"Xquik timed out{label_suffix}: {exc_text}"
            )
            _record_failure(health.TIMEOUT, msg)
            return [], None
        state = exc_state or http.classify_failure(status_code=status, message=str(exc))
        _record_failure(state, f"Xquik HTTP error{label_suffix}: {exc}")
        return [], None
    except Exception as exc:
        _log(f"Error for '{label}': {exc}")
        exc_text = str(exc)
        lowered = exc_text.lower()
        if isinstance(exc, TimeoutError):
            msg = (
                f"Xquik request failed{label_suffix}: {exc_text}"
                if "timed out" in lowered or "timeout" in lowered
                else f"Xquik timed out{label_suffix}: {exc_text or 'TimeoutError'}"
            )
            _record_failure(health.TIMEOUT, msg)
        elif isinstance(exc, OSError):
            _record_failure(health.UNREACHABLE, f"Xquik request failed{label_suffix}: {exc}")
        else:
            _record_failure(
                http.classify_failure(message=exc_text),
                f"Xquik request failed{label_suffix}: {exc}",
            )
        return [], None

    if not isinstance(response, dict):
        _log(f"Unexpected response payload for '{label}' (got {type(response).__name__})")
        _record_failure(
            health.SCHEMA_DRIFT,
            f"Xquik returned unexpected payload{label_suffix} "
            f"(got {type(response).__name__})",
        )
        return [], None

    tweets = response.get("tweets", [])
    if not isinstance(tweets, list):
        _record_failure(
            health.SCHEMA_DRIFT,
            f"Xquik returned no tweets array{label_suffix} "
            f"(got {type(tweets).__name__})",
        )
        return [], None
    items: List[Dict[str, Any]] = []
    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue
        tweet_id = _clean_tweet_id(tweet.get("id"))
        if not tweet_id or tweet_id in seen_ids:
            continue
        item = _parse_tweet(
            tweet,
            index_offset + len(items),
            relevance_query,
            id_prefix=id_prefix,
            window=window,
        )
        if item:
            seen_ids.add(tweet_id)
            items.append(item)
    return items, None


# True when a tweet URL is authored by ``handle`` (their own post). Used by
# the ABOUT lane to drop the subject's own tweets so only mentions *by
# others* remain; the implementation is ``x_api.is_own_post``.
_is_own = is_own_post


def _validated_handle(raw: Any, lane: str) -> str:
    """Validate an X handle against X's handle grammar before query interpolation."""
    if not isinstance(raw, str):
        _log(f"Skipping invalid non-string handle in {lane} lane")
        return ""
    handle = _clean_handle(raw)
    if not handle:
        _log(f"Skipping invalid handle in {lane} lane")
    return handle


def search_handles(
    handles: List[str],
    topic: str,
    from_date: str,
    to_date: str,
    *,
    count_per: int = 8,
    token: str = "",
    failure_out: Optional[List[str]] = None,
    deadline: float | None = None,
    cancel: Any = None,
) -> List[Dict[str, Any]]:
    """FROM lane: tweets authored BY each handle (their own timeline).

    The topic is NOT AND'd into the query (that was the from:-AND bug, #610) —
    we pull the raw timeline and use ``topic`` for relevance ranking only.
    Returns a flat list of item dicts (mirrors ``bird_x.search_handles``).

    When ``failure_out`` is provided, any failure reason is appended — the
    fatal auth/payment one that stops the lane, and the per-handle transient
    ones (429, 5xx, network, unparseable payload) that do not — so the caller
    can distinguish a failed request from a handle that posted nothing.
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_handles: set[str] = set()
    window = (from_date, to_date)
    date_filters = x_date_filters(from_date, to_date)
    for raw in handles:
        if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
            if failure_out is not None:
                failure_out.append("Xquik research cancelled or timed out")
            break
        handle = _validated_handle(raw, "from:")
        if not handle:
            continue
        handle_key = handle.lower()
        if handle_key in seen_handles:
            continue
        seen_handles.add(handle_key)
        q = f"from:{handle} {date_filters}"
        got, auth_error = _execute_search(
            q, count_per, token,
            label=f"from:{handle}", id_prefix="XF",
            seen_ids=seen_ids, relevance_query=topic,
            index_offset=len(items),
            failure_out=failure_out,
            deadline_monotonic=deadline,
            cancel=cancel,
            window=window,
        )
        if auth_error:
            # Fatal auth/payment failure — stop, keep what we have. Surface the
            # reason: an empty FROM lane is otherwise indistinguishable from a
            # subject who simply did not post.
            if failure_out is not None:
                failure_out.append(auth_error)
            break
        items.extend(got)
    return items


def search_mentions(
    handles: List[str],
    from_date: str,
    to_date: str,
    *,
    topic: str = "",
    count_per: int = 5,
    token: str = "",
    failure_out: Optional[List[str]] = None,
    deadline: float | None = None,
    cancel: Any = None,
) -> List[Dict[str, Any]]:
    """ABOUT lane: tweets mentioning each handle, authored by OTHERS.

    Queries ``@handle -from:handle`` then drops the handle's own tweets
    (``_is_own``) so only third-party mentions remain. Returns a flat list of
    item dicts.

    When ``failure_out`` is provided, any failure reason is appended — the
    fatal auth/payment one that stops the lane, and the per-handle transient
    ones (429, 5xx, network, unparseable payload) that do not — so the caller
    can distinguish a failed request from a handle nobody mentioned.
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_handles: set[str] = set()
    window = (from_date, to_date)
    date_filters = x_date_filters(from_date, to_date)
    for raw in handles:
        if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
            if failure_out is not None:
                failure_out.append("Xquik research cancelled or timed out")
            break
        handle = _validated_handle(raw, "@")
        if not handle:
            continue
        handle_key = handle.lower()
        if handle_key in seen_handles:
            continue
        seen_handles.add(handle_key)
        q = f"@{handle} -from:{handle} {date_filters}"
        got, auth_error = _execute_search(
            q, count_per, token,
            label=f"@{handle}", id_prefix="XA",
            seen_ids=seen_ids, relevance_query=topic,
            index_offset=len(items),
            failure_out=failure_out,
            deadline_monotonic=deadline,
            cancel=cancel,
            window=window,
        )
        if auth_error:
            if failure_out is not None:
                failure_out.append(auth_error)
            break
        items.extend(it for it in got if not _is_own(it.get("url", ""), handle))
    return items


def probe_works(token: str, timeout: int = 8) -> Optional[bool]:
    """Cheap runtime check that the xquik key actually returns data.

    Mirrors ``bird_x.probe_works`` for the key-based X path so ``--diagnose``
    reflects reality instead of static key presence. Returns True
    (funded/working), False (a clear auth/payment failure — 401/403, or 402
    when the key is configured but unpaid), or None (inconclusive: timeout /
    transient HTTP) so callers fail open.
    The human-readable reason is available via ``probe_reason()``. Cached per
    process so repeated diagnose calls don't re-probe.
    """
    global _probe_cache
    if _probe_cache[0] != "unset":
        return _probe_cache[0]
    if not token:
        _probe_cache = (False, "no XQUIK_API_KEY configured")
        return False
    from datetime import timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    # @x (the platform's own account) posts frequently, so a no-error response
    # means the key works even if this particular window is quiet.
    q = f"from:x since:{since}"
    full_url = f"{_BASE_URL}/x/tweets/search?q={_url_encode(q)}&queryType=Top&limit=1"
    try:
        request_headers = {"X-Api-Key": token}
        http.get(full_url, headers=request_headers, timeout=timeout, retries=0)
    except http.HTTPError as exc:
        status = getattr(exc, "status_code", None)
        if status == 402:
            _probe_cache = (False, "xquik key unpaid: payment required (402)")
        elif status in (401, 403):
            _probe_cache = (False, f"xquik auth failed ({status})")
        else:
            # 5xx / unexpected status — inconclusive, don't report a false-down.
            _probe_cache = (None, f"xquik probe inconclusive ({status})")
        return _probe_cache[0]
    except Exception as exc:
        _probe_cache = (None, f"xquik probe inconclusive ({type(exc).__name__})")
        return None
    _probe_cache = (True, "ok")
    return True


def probe_reason() -> str:
    """Human-readable reason for the last ``probe_works`` result (or '')."""
    return _probe_cache[1]


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
) -> Dict[str, Any]:
    """Search X via Xquik and return results.

    Xquik API returns full engagement data by default, so no separate
    enrichment step is needed.
    """
    return search_xquik(topic, from_date, to_date, depth=depth, token=token)


def parse_xquik_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract items from search response.

    Args:
        response: Response dict from search_xquik()

    Returns:
        List of normalized item dicts.
    """
    if not isinstance(response, dict):
        return []
    items = response.get("items", [])
    return items if isinstance(items, list) else []


def _parse_tweet(
    tweet: Dict[str, Any],
    index: int,
    query: str,
    id_prefix: str = "XQ",
    window: Optional[tuple[str, str]] = None,
) -> Dict[str, Any] | None:
    """Parse a single tweet from the API response into the standard item format."""
    if not isinstance(tweet, dict):
        return None
    author = tweet.get("author")
    if not isinstance(author, dict):
        return None
    raw_username = author.get("username")
    username = _clean_handle(raw_username) if isinstance(raw_username, str) else ""
    tweet_id = _clean_tweet_id(tweet.get("id"))
    if not username or not tweet_id:
        return None
    url = f"https://x.com/{username}/status/{tweet_id}"

    # Parse date
    date = None
    created_at = tweet.get("createdAt")
    if isinstance(created_at, str) and created_at:
        try:
            if len(created_at) > 10 and created_at[10] == "T":
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            date = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    if window is not None and date:
        window_from, window_to = window
        if window_from and date < window_from:
            return None
        if window_to and date > window_to:
            return None

    text = str(tweet.get("text") or "").strip()[:500]

    # Leading-run @mentions = who the post is directed at (reply target). Shared
    # parser with bird so the first-party interaction signal fires for xquik too.
    from .query import leading_mentions
    mentioned_handles = leading_mentions(text)

    # Build engagement dict with full metrics
    engagement = {
        "likes": _safe_int(tweet.get("likeCount")),
        "reposts": _safe_int(tweet.get("retweetCount")),
        "replies": _safe_int(tweet.get("replyCount")),
        "quotes": _safe_int(tweet.get("quoteCount")),
        "views": _safe_int(tweet.get("viewCount")),
        "bookmarks": _safe_int(tweet.get("bookmarkCount")),
    }

    return {
        "id": f"{id_prefix}{index + 1}",
        "text": text,
        "url": url,
        "author_handle": username,
        "date": date,
        "engagement": engagement,
        "mentioned_handles": mentioned_handles,
        "relevance": _compute_relevance(query, text) if query else 0.7,
        "why_relevant": "",
    }


def _safe_int(value: Any) -> int | None:
    """Convert value to int, returning None on failure."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _url_encode(text: str) -> str:
    """URL-encode a string using stdlib."""
    from urllib.parse import quote
    return quote(text, safe="")
