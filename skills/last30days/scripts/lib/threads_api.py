"""Optional official Threads API lane for /last30days.

The keyless browser search (lib/threads.py) is the default Threads lane. When
the user also holds Meta Threads API credentials, this module adds the two
read endpoints that help research:

- ``GET /keyword_search`` with a user access token (``THREADS_ACCESS_TOKEN``):
  public posts by keyword with a server-side ``since``/``until`` window.
  Until Meta approves the app for ``threads_keyword_search`` the endpoint
  only searches the token owner's own posts, so results may be sparse.
  The response carries no like/reply counts (those come from insights, which
  only cover the token owner's posts).
- ``GET /trending_topics`` with an app access token (``THREADS_APP_TOKEN``,
  ``TH|<app id>|<app secret>``): US/JP trends. A trend that matches the topic
  is reported, and its posts come from ``GET /{trend_id}/posts`` when the app
  has advanced Trending Topics access (a refusal there is not an error).

Publishing, replies, insights and webhooks are write-side or own-account
features and have no place in a read-only research run, so they are absent.
Docs: https://developers.facebook.com/documentation/threads/
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import auth_safety, http, log
from .relevance import token_overlap_relevance

API_BASE = "https://graph.threads.com/v1.0"
USER_TOKEN_ENV = "THREADS_ACCESS_TOKEN"
APP_TOKEN_ENV = "THREADS_APP_TOKEN"
TRENDS_COUNTRY_ENV = "LAST30DAYS_THREADS_TRENDS_COUNTRY"
TREND_COUNTRIES = ("US", "JP")
POST_FIELDS = "id,shortcode,text,media_type,permalink,timestamp,username,is_quote_post,is_reply"
# keyword_search rejects `since` before this (2023-07-05, Threads' launch).
_MIN_SINCE = 1688540400
_TREND_ID_RE = re.compile(r"^[0-9]+$")
# A trend must share most of the topic's words to count as a match.
TREND_MATCH_FLOOR = 0.6
_TIMEOUT = 15
_CODE_RE = re.compile(r"/post/([A-Za-z0-9_-]+)")


def _log(msg: str):
    log.source_log("Threads API", msg, tty_only=False)


def _token(config: Optional[Dict[str, Any]], name: str) -> Tuple[Optional[str], Optional[str]]:
    return auth_safety.normalize_header_token((config or {}).get(name))


def user_token_present(config: Optional[Dict[str, Any]]) -> bool:
    token, err = _token(config, USER_TOKEN_ENV)
    return bool(token) and not err


def app_token_present(config: Optional[Dict[str, Any]]) -> bool:
    token, err = _token(config, APP_TOKEN_ENV)
    return bool(token) and not err


def trends_country(config: Optional[Dict[str, Any]]) -> str:
    raw = str((config or {}).get(TRENDS_COUNTRY_ENV) or "US").strip().upper()
    return raw if raw in TREND_COUNTRIES else "US"


def _remaining(deadline: Optional[float]) -> float:
    if deadline is None:
        return float(_TIMEOUT)
    return deadline - time.monotonic()


def _get(path: str, params: Dict[str, Any], token: str, raw_token: Any,
         deadline: Optional[float]) -> Dict[str, Any]:
    """One Graph GET. Returns the JSON body, or {"error": scrubbed message}."""
    remaining = _remaining(deadline)
    if remaining < 2:
        return {"error": "research deadline reached"}
    try:
        # Bearer header, not the access_token query parameter: the token
        # stays out of URLs (proxy logs, redirects, percent-encoded forms the
        # scrubber would not recognise).
        data = http.get(
            f"{API_BASE}/{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=min(_TIMEOUT, remaining),
            retries=1,
            sensitive_url=True,
            deadline_monotonic=deadline,
        )
    except Exception as exc:  # http.HTTPError and transport failures alike
        detail = str(exc)
        body = getattr(exc, "body", None)
        if isinstance(body, str) and body:
            detail = f"{detail} {body[:300]}"
        safe = auth_safety.scrub_secrets(detail, (raw_token, token))
        return {"error": f"{type(exc).__name__}: {safe}"}
    return data if isinstance(data, dict) else {"error": "non-object JSON response"}


def _to_web_shape(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a Graph media object onto the web post shape lib/threads parses."""
    if not isinstance(post, dict):
        return None
    permalink = str(post.get("permalink") or "")
    match = _CODE_RE.search(permalink)
    taken_at = None
    stamp = str(post.get("timestamp") or "")
    if stamp:
        try:
            taken_at = int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").timestamp())
        except ValueError:
            taken_at = None
    shaped = {
        "id": str(post.get("id") or ""),
        "code": str(post.get("shortcode") or (match.group(1) if match else "")),
        "caption": {"text": str(post.get("text") or "")},
        "user": {"username": str(post.get("username") or "")},
        "permalink": permalink,
        "taken_at": taken_at,
        "_via": "threads_api",
    }
    return shaped if (shaped["id"] or shaped["code"]) else None


def _utc_day_start(day: str) -> Optional[int]:
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return int(parsed.timestamp())


def window_bounds(from_date: str, to_date: str,
                  now: Optional[float] = None) -> Tuple[Optional[int], Optional[int]]:
    """Unix ``since``/``until`` for a UTC ``YYYY-MM-DD`` window, inclusive.

    A date string would mean midnight, cutting off the window's last day, so
    ``until`` is the start of the day after ``to_date``. Graph requires
    ``since >= 1688540400``, ``until <= now`` and ``since < until``; a bound
    that cannot meet that is left out.
    """
    now = time.time() if now is None else now
    since = _utc_day_start(from_date)
    if since is not None:
        since = max(since, _MIN_SINCE)
    until = _utc_day_start(to_date)
    if until is not None:
        until += int(timedelta(days=1).total_seconds())
        if until > now or (since is not None and until <= since):
            until = None
    if since is not None and since >= now:
        since = None
    return since, until


def keyword_search(
    query: str,
    from_date: str,
    to_date: str,
    limit: int,
    config: Dict[str, Any],
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Recent then top keyword-search posts. {"posts": [...], "error"?: str}."""
    raw = (config or {}).get(USER_TOKEN_ENV)
    token, err = auth_safety.normalize_header_token(raw)
    if err:
        return {"posts": [], "error": err}
    if not token:
        return {"posts": [], "error": f"No {USER_TOKEN_ENV} configured"}
    posts: List[Dict[str, Any]] = []
    errors: List[str] = []
    since, until = window_bounds(from_date, to_date)
    for search_type in ("RECENT", "TOP"):
        params = {
            "q": query,
            "search_type": search_type,
            "fields": POST_FIELDS,
            "limit": max(1, min(100, int(limit))),
        }
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        data = _get("keyword_search", params, token, raw, deadline)
        if data.get("error"):
            errors.append(str(data["error"]))
            continue
        for post in data.get("data") or []:
            shaped = _to_web_shape(post)
            if shaped:
                posts.append(shaped)
    out: Dict[str, Any] = {"posts": posts}
    if errors and not posts:
        out["error"] = errors[0]
    _log(f"keyword_search '{query}': {len(posts)} posts")
    return out


def matching_trend(
    topic: str,
    config: Dict[str, Any],
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """The trending topic matching ``topic`` and its posts, when there is one.

    Returns {"trend": {...} or None, "posts": [...], "error"?: str}.
    """
    raw = (config or {}).get(APP_TOKEN_ENV)
    token, err = auth_safety.normalize_header_token(raw)
    if err:
        return {"trend": None, "posts": [], "error": err}
    if not token:
        return {"trend": None, "posts": []}
    country = trends_country(config)
    data = _get("trending_topics", {"country_code": country}, token, raw, deadline)
    if data.get("error"):
        return {"trend": None, "posts": [], "error": str(data["error"])}
    best, best_score = None, 0.0
    for trend in data.get("trends") or []:
        if not isinstance(trend, dict) or not _TREND_ID_RE.match(str(trend.get("trend_id") or "")):
            continue
        text = f"{trend.get('title') or ''} {trend.get('description') or ''}"
        score = token_overlap_relevance(topic, text)
        if score > best_score:
            best, best_score = trend, score
    if best is None or best_score < TREND_MATCH_FLOOR:
        return {"trend": None, "posts": []}
    trend = {
        "title": str(best.get("title") or ""),
        "description": str(best.get("description") or ""),
        "trend_id": str(best["trend_id"]),
        "country": country,
    }
    _log(f"topic is trending in {country}: {trend['title']}")
    posts_data = _get(
        f"{trend['trend_id']}/posts",
        {"trending_post_type": "top",
         "fields": "id,shortcode,text,media_type,permalink,timestamp,username"},
        token, raw, deadline,
    )
    posts = []
    if posts_data.get("error"):
        # Posts need advanced Trending Topics access; the trend still counts.
        _log("trend posts unavailable (needs advanced Trending Topics access)")
    else:
        for post in posts_data.get("data") or []:
            shaped = _to_web_shape(post)
            if shaped:
                shaped["_trend"] = trend["title"]
                posts.append(shaped)
    return {"trend": trend, "posts": posts}
