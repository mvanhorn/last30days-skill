"""Direct X API v2 backend (``xapi``) and the shared v2 response parser.

``xapi`` searches X through ``api.x.com/2`` with an app-only bearer token
(``X_BEARER_TOKEN``). Full-archive search is tried first; when the developer
project is not enrolled for it (HTTP 403 with an enrollment marker), the
search retries once against recent search with the window clamped to the
last seven days and the truncation named in the result (KTD4).

This module also owns the one X API v2 parser (``parse_v2_response``) that
``xurl_x`` delegates to, plus the snowflake, handle-grammar, and
generated-sequence helpers that ``grok_x`` imports back (KTD3).

Security contract (R8, R8a): the bearer travels only in the Authorization
header; every failure becomes an engine-authored fixed string chosen by
status code plus a marker match on the body, and the response body, reason
phrase, and headers never enter an error string, a log line, or an
exception. Topic and handle text is sanitized before it enters a query, and
the research window is always carried by request parameters.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import health, http, log
from .relevance import token_overlap_relevance as _compute_relevance

_API_BASE = "https://api.x.com/2"
_SEARCH_ALL_URL = f"{_API_BASE}/tweets/search/all"
_SEARCH_RECENT_URL = f"{_API_BASE}/tweets/search/recent"

# Depth configurations: number of posts to collect per query (shared with
# xurl_x, which re-imports this table). The API minimum per page is 10.
DEPTH_CONFIG = {
    "quick": 10,
    "default": 30,
    "deep": 60,
}

# X API v2 caps a search query at 512 characters (Basic) and pages at 10..100.
MAX_QUERY_CHARS = 512
MIN_PAGE_RESULTS = 10
MAX_PAGE_RESULTS = 100
# Recent search reaches back seven days; end_time must be at least ten
# seconds before now, so a "today" window keeps a small safety margin.
RECENT_WINDOW_DAYS = 7
END_TIME_SAFETY_SECONDS = 30
TIMEOUT_SECONDS = 30
RETRIES = 2

TRUNCATION_DETAIL = "window truncated to 7 days"

# Engine-authored fixed error strings. Each carries a marker that
# http.classify_failure recognizes, because the X retrieval branch classifies
# by message text only (KTD6).
ERR_PAYMENT_REQUIRED = "xapi: payment required (X API credits exhausted)"
ERR_UNAUTHORIZED = "xapi: unauthorized (bearer token rejected)"
ERR_FORBIDDEN = "xapi: forbidden (bearer token lacks access)"
ERR_RATE_LIMITED = "xapi: rate limit exceeded (X API)"
ERR_TIMED_OUT = "xapi: timed out"
ERR_UNREACHABLE = "xapi: connection error (X API unreachable)"
ERR_NO_TOKEN = "No X_BEARER_TOKEN configured"
ERR_EMPTY_QUERY = "xapi: empty query after sanitizing"

# Failures that end every remaining lane for the run (the key itself is the
# problem, not this request).
_FATAL_ERRORS = frozenset({ERR_PAYMENT_REQUIRED, ERR_UNAUTHORIZED, ERR_FORBIDDEN})

# A 403 whose body carries one of these is "this project is not enrolled for
# full-archive search", which recent search can still serve.
_ENROLLMENT_MARKERS = (
    "client-not-enrolled",
    "not enrolled",
    "enrolled",
    "access level",
    "subset of",
)
# Body markers for credit exhaustion, matched on any status: X reports a
# depleted pay-per-use balance under more than one code.
_CREDIT_MARKERS = (
    "payment required",
    "insufficient credits",
    "does not have any credits",
    "out of credits",
    "creditsdepleted",
    "credits depleted",
)

# Twitter/X snowflake epoch (2010-11-04T01:42:54.657Z) in milliseconds.
_SNOWFLAKE_EPOCH_MS = 1288834974657

# X's real handle grammar. Handles are interpolated into post URLs and into
# search operators (from:, @), so anything outside this charset is rejected
# rather than passed through. entity_extract applies the same rule to
# @mentions.
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")

# Grouping syntax that carries no lexical meaning in a topic, mirroring
# bird_x. Double quotes are handled separately: xapi strips them all and
# wraps the core in exactly one phrase pair, so a planner quote can never
# close the phrase early.
_GROUPING_CHARS = "“”()[]{}"
_QUOTE_CHARS = "\"„‟"
_APOSTROPHES = "'‘’"


def _log(msg: str) -> None:
    log.source_log("xapi", msg, tty_only=False)


def _utcnow() -> datetime:
    """Current UTC time; a seam tests patch to pin the window arithmetic."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared helpers (moved here from grok_x; grok_x imports them back)
# ---------------------------------------------------------------------------


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
    is, because a model interpolates a plausible-looking id sequence. Four
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


def _clean_handle(value: str) -> str:
    """Return a grammar-valid handle, or '' when the value is not one."""
    candidate = str(value or "").strip().lstrip("@")
    return candidate if _HANDLE_RE.fullmatch(candidate) else ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_v2_response(
    response: Dict[str, Any],
    topic: str = "",
    window: Optional[tuple[str, str]] = None,
    *,
    id_prefix: str = "XAPI",
    index_offset: int = 0,
    seen_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Parse an X API v2 search response into the engine's X item shape.

    Output matches the XItem schema used by xai_x and bird_x (id, text, url,
    author_handle, date, engagement, mentioned_handles, why_relevant,
    relevance) plus ``post_id`` (the numeric snowflake as a string). The
    citation URL is rebuilt from the id and username; URL fields in the
    response are never used. A post whose author is not in
    ``includes.users`` (or whose username fails the handle grammar) keeps
    the ``https://x.com/i/status/<id>`` form with an empty handle.

    ``window`` is an optional ``(from_date, to_date)`` pair of YYYY-MM-DD
    strings; dated posts outside it are dropped. ``seen_ids`` and
    ``index_offset`` let several calls share one accumulator with unique ids.
    """
    items: List[Dict[str, Any]] = []
    if not isinstance(response, dict):
        return items
    if "error" in response:
        _log("error in response; no items parsed")
        return items

    data = response.get("data") or []
    if not isinstance(data, list) or not data:
        return items

    authors: Dict[str, str] = {}
    for user in (response.get("includes") or {}).get("users") or []:
        if isinstance(user, dict) and user.get("id") is not None:
            authors[str(user["id"])] = _clean_handle(user.get("username", ""))

    from .query import leading_mentions

    window_from, window_to = (window or (None, None))
    seen = seen_ids if seen_ids is not None else set()

    for tweet in data:
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id") or "").strip()
        if not tweet_id.isdigit():
            continue
        if tweet_id in seen:
            continue

        username = authors.get(str(tweet.get("author_id") or ""), "")
        if username:
            url = f"https://x.com/{username}/status/{tweet_id}"
        else:
            url = f"https://x.com/i/status/{tweet_id}"

        note = tweet.get("note_tweet")
        text = ""
        if isinstance(note, dict) and note.get("text"):
            text = str(note["text"])
        else:
            text = str(tweet.get("text") or "")
        text = text.strip()

        engagement: Optional[Dict[str, Any]] = None
        metrics = tweet.get("public_metrics") or {}
        if isinstance(metrics, dict) and metrics:
            engagement = {
                "likes": metrics.get("like_count", 0),
                "reposts": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "quotes": metrics.get("quote_count", 0),
            }
            bookmarks = _safe_int(metrics.get("bookmark_count"))
            if bookmarks is not None:
                engagement["bookmarks"] = bookmarks
            views = _safe_int(metrics.get("impression_count"))
            if views is not None:
                engagement["views"] = views

        date: Optional[str] = None
        created = str(tweet.get("created_at") or "")
        if created:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", created)
            if m:
                date = m.group(1)
        if date and window_from and date < window_from:
            continue
        if date and window_to and date > window_to:
            continue

        seen.add(tweet_id)
        items.append({
            "id": f"{id_prefix}{index_offset + len(items) + 1}",
            "text": text[:500],
            "url": url,
            "author_handle": username,
            "date": date,
            "engagement": engagement,
            "mentioned_handles": leading_mentions(text),
            "why_relevant": "",
            "relevance": _compute_relevance(topic, text) if topic else 0.5,
            "post_id": tweet_id,
        })

    return items


# ---------------------------------------------------------------------------
# Query compilation (R8a)
# ---------------------------------------------------------------------------


def _topic_tokens(topic: str) -> List[str]:
    """Sanitized topic tokens: no quotes, grouping, operators, or negation."""
    separators = str.maketrans({char: " " for char in _GROUPING_CHARS + _QUOTE_CHARS})
    cleaned = str(topic or "").translate(separators)
    tokens: List[str] = []
    for token in cleaned.split():
        clean = token.strip(_APOSTROPHES)
        if not clean:
            continue
        if ":" in clean or clean.startswith("-"):
            continue
        if clean.upper() in ("OR", "AND"):
            continue
        tokens.append(clean)
    return tokens


def _compile(tokens: List[str]) -> str:
    return f'"{" ".join(tokens)}" -is:retweet'


def build_query(topic: str) -> str:
    """Compile a topic into one quoted phrase plus ``-is:retweet``.

    Mirrors ``bird_x.build_topic_query`` (grouping characters become spaces,
    quotes are balanced) and additionally drops every operator-shaped token
    (anything with a colon, anything starting with ``-``, bare OR/AND) so a
    topic can never smuggle ``from:``, ``since:``, or a phrase break into the
    request. The window is never part of the query (see ``_window``). The
    result stays under ``MAX_QUERY_CHARS``, cut at a token boundary. Returns
    "" when nothing lexical survives.
    """
    tokens = _topic_tokens(topic)
    if not tokens:
        return ""
    compiled = _compile(tokens)
    while len(compiled) > MAX_QUERY_CHARS and len(tokens) > 1:
        tokens.pop()
        compiled = _compile(tokens)
    if len(compiled) > MAX_QUERY_CHARS:
        # One token longer than the whole budget: keep as much of it as fits.
        overhead = len(_compile([""]))
        tokens = [tokens[0][: MAX_QUERY_CHARS - overhead]]
        compiled = _compile(tokens)
    return compiled


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


def _parse_day(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(from_date: str, to_date: str) -> tuple[str, str]:
    """``(start_time, end_time)`` for the request, ISO 8601 with Z.

    ``start_time`` is midnight UTC of ``from_date``; ``end_time`` is the end
    of ``to_date`` or, when that would be now or later ("today"), now minus a
    safety margin.
    """
    now = _utcnow()
    start = _parse_day(from_date) or (now - timedelta(days=30))
    end_day = _parse_day(to_date)
    latest = now - timedelta(seconds=END_TIME_SAFETY_SECONDS)
    if end_day is None:
        end = latest
    else:
        end = min(end_day + timedelta(hours=23, minutes=59, seconds=59), latest)
    if start >= end:
        start = end - timedelta(days=1)
    return _iso(start), _iso(end)


def _recent_floor() -> str:
    """The earliest ``start_time`` recent search accepts, with a margin."""
    return _iso(
        _utcnow() - timedelta(days=RECENT_WINDOW_DAYS) + timedelta(seconds=END_TIME_SAFETY_SECONDS)
    )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class _XApiFailure(Exception):
    """A request failure already reduced to its fixed string.

    ``enrollment`` marks the one 403 that recent search can still serve.
    The message is always one of the ERR_* literals (or ``xapi: http <n>``),
    never response text.
    """

    def __init__(self, message: str, *, enrollment: bool = False):
        super().__init__(message)
        self.enrollment = enrollment


def _failure_for(exc: http.HTTPError) -> _XApiFailure:
    status = getattr(exc, "status_code", None)
    body = str(getattr(exc, "body", "") or "").lower()
    if status == 402 or any(marker in body for marker in _CREDIT_MARKERS):
        return _XApiFailure(ERR_PAYMENT_REQUIRED)
    if status == 401:
        return _XApiFailure(ERR_UNAUTHORIZED)
    if status == 403:
        enrolled = any(marker in body for marker in _ENROLLMENT_MARKERS)
        return _XApiFailure(ERR_FORBIDDEN, enrollment=enrolled)
    if status == 429:
        return _XApiFailure(ERR_RATE_LIMITED)
    state = getattr(exc, "outcome_state", None)
    if status == 408 or state == health.TIMEOUT:
        return _XApiFailure(ERR_TIMED_OUT)
    if status:
        return _XApiFailure(f"xapi: http {status}")
    if state == health.UNREACHABLE:
        return _XApiFailure(ERR_UNREACHABLE)
    return _XApiFailure("xapi: request failed (HTTPError)")


def _get(token: str, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """One authenticated GET; every failure surfaces as ``_XApiFailure``."""
    try:
        response = http.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=TIMEOUT_SECONDS,
            retries=RETRIES,
        )
    except http.HTTPError as exc:
        raise _failure_for(exc) from None
    except TimeoutError:
        raise _XApiFailure(ERR_TIMED_OUT) from None
    except Exception as exc:  # noqa: BLE001 - the message must never leak
        raise _XApiFailure(f"xapi: request failed ({type(exc).__name__})") from None
    return response if isinstance(response, dict) else {}


def _page_size(count: int) -> int:
    return max(MIN_PAGE_RESULTS, min(MAX_PAGE_RESULTS, int(count)))


def _search_pages(token: str, url: str, params: Dict[str, Any], count: int) -> Dict[str, Any]:
    """Follow ``next_token`` until ``count`` posts are collected."""
    data: List[Dict[str, Any]] = []
    users: Dict[str, Dict[str, Any]] = {}
    page_params = dict(params)
    page_params["max_results"] = _page_size(count)
    # Bound the walk even when every page carries a next_token.
    max_pages = max(1, -(-count // MIN_PAGE_RESULTS))
    for _ in range(max_pages):
        # A fresh dict per page: the transport must never see a later
        # page's next_token on an earlier request.
        response = _get(token, url, dict(page_params))
        page = response.get("data") or []
        if isinstance(page, list):
            data.extend(t for t in page if isinstance(t, dict))
        for user in (response.get("includes") or {}).get("users") or []:
            if isinstance(user, dict) and user.get("id") is not None:
                users[str(user["id"])] = user
        next_token = (response.get("meta") or {}).get("next_token")
        if not next_token or len(data) >= count:
            break
        page_params["next_token"] = next_token
    return {"data": data[:count], "includes": {"users": list(users.values())}}


def _run_search(
    token: str,
    query: str,
    from_date: str,
    to_date: str,
    count: int,
    *,
    topic: str,
    id_prefix: str,
    label: str,
    index_offset: int = 0,
    seen_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Full-archive search with the recent-search fallback (KTD4).

    Returns ``{"items": [...]}`` (plus ``"warning"`` when the window was
    truncated) or ``{"items": [], "error": <fixed string>}``.
    """
    start, end = _window(from_date, to_date)
    params = {
        "query": query,
        "start_time": start,
        "end_time": end,
        "sort_order": "recency",
        "expansions": "author_id",
        "tweet.fields": "created_at,public_metrics,note_tweet,entities",
        "user.fields": "username",
    }
    _log(f"Searching: {label}")
    warning = None
    try:
        response = _search_pages(token, _SEARCH_ALL_URL, params, count)
    except _XApiFailure as exc:
        if not exc.enrollment:
            _log(f"{label}: {exc}")
            return {"items": [], "error": str(exc)}
        _log(f"{label}: full-archive search not enrolled; retrying recent search ({TRUNCATION_DETAIL})")
        params["start_time"] = max(start, _recent_floor())
        try:
            response = _search_pages(token, _SEARCH_RECENT_URL, params, count)
        except _XApiFailure as exc2:
            _log(f"{label}: {exc2}")
            return {"items": [], "error": str(exc2)}
        warning = TRUNCATION_DETAIL
    items = parse_v2_response(
        response, topic, (from_date, to_date),
        id_prefix=id_prefix, index_offset=index_offset, seen_ids=seen_ids,
    )
    result: Dict[str, Any] = {"items": items}
    if warning:
        result["warning"] = warning
    return result


# ---------------------------------------------------------------------------
# Public search entry points
# ---------------------------------------------------------------------------


def search_x(
    token: str,
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Topic search via X API v2.

    Returns ``{"items": [...]}`` or ``{"items": [], "error": "..."}`` (the
    xquik shape); ``"warning"`` carries the truncation detail after the
    recent-search fallback.
    """
    if not token:
        return {"items": [], "error": ERR_NO_TOKEN}
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    compiled = build_query(query)
    if not compiled:
        _log("topic compiled to an empty query after sanitizing")
        return {"items": [], "error": ERR_EMPTY_QUERY}
    return _run_search(
        token, compiled, from_date, to_date, count,
        topic=query, id_prefix="XAPI", label="topic",
    )


def _is_own(url: str, handle: str) -> bool:
    """True when a post URL is authored by ``handle`` (their own post)."""
    from .xquik import _is_own as _xquik_is_own
    return _xquik_is_own(url, handle)


def _lane_handle(raw: str, lane: str) -> str:
    handle = _clean_handle(raw)
    if not handle:
        _log(f"skipping {lane} lane for a handle outside the X handle grammar")
    return handle


def search_handles(
    handles: List[str],
    topic: str,
    from_date: str,
    to_date: str,
    *,
    count_per: int = 8,
    token: str = "",
) -> List[Dict[str, Any]]:
    """FROM lane: posts authored BY each handle (their own timeline).

    The topic is NOT AND'd into the query; it ranks relevance only (mirrors
    ``xquik.search_handles``). A handle outside the X grammar skips its lane
    with a receipt line.
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in handles:
        handle = _lane_handle(raw, "from:")
        if not handle:
            continue
        result = _run_search(
            token, f"from:{handle} -is:retweet", from_date, to_date, count_per,
            topic=topic, id_prefix="XF", label=f"from:{handle}",
            index_offset=len(items), seen_ids=seen_ids,
        )
        if result.get("error") in _FATAL_ERRORS:
            break  # the key itself failed; keep what we have
        items.extend(result.get("items", []))
    return items


def search_mentions(
    handles: List[str],
    from_date: str,
    to_date: str,
    *,
    topic: str = "",
    count_per: int = 5,
    token: str = "",
) -> List[Dict[str, Any]]:
    """ABOUT lane: posts mentioning each handle, authored by OTHERS.

    The query is ``@handle -from:handle`` and the handle's own posts are
    dropped again client-side (``_is_own``) so only third-party mentions
    remain.
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in handles:
        handle = _lane_handle(raw, "mention")
        if not handle:
            continue
        result = _run_search(
            token, f"@{handle} -from:{handle} -is:retweet", from_date, to_date, count_per,
            topic=topic, id_prefix="XA", label=f"@{handle}",
            index_offset=len(items), seen_ids=seen_ids,
        )
        if result.get("error") in _FATAL_ERRORS:
            break
        items.extend(
            it for it in result.get("items", []) if not _is_own(it.get("url", ""), handle)
        )
    return items
