"""Xueqiu (雪球) — Chinese financial social source for last30days.

Fetches hot posts / statuses from Xueqiu's v4 public timeline endpoint and
filters them against the research topic via token-overlap relevance. Uses a
Cookie (``XUEQIU_COOKIE`` in the config) because the timeline endpoint
returns empty/redirect without a logged-in session; the cookie is read-only
and never sent anywhere except xueqiu.com.

Activation gate: this source is only available when a Xueqiu cookie is
configured AND the topic trips the financial gate (``is_financial_topic``,
the same chokepoint used for StockTwits). Non-financial topics never get
Xueqiu registered, so the planner cannot assign it. See
``pipeline.available_sources``.

Search model: the v4 statuses timeline has NO keyword-search endpoint in
the public surface (agent-reach uses the same endpoint for hot posts only).
This adapter therefore follows the listing pattern: pull the public
timeline stream (plus hot-stock context when the topic resolves to a
symbol) and keep only statuses whose text shares informative tokens with
the topic. Empty results are the normal quiet state for off-topic topics.

Endpoints used:
- GET /v4/statuses/public_timeline_by_category.json  -> public timeline
- GET /stock/search.json                              -> symbol resolution
- GET /v5/stock/hot_stock/list.json                   -> hot-stock context
"""

from __future__ import annotations

import http.cookiejar as _py_http_cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import http, log
from .relevance import token_overlap_relevance

# Reuse the same finance gate as StockTwits so a single chokepoint decides
# whether financial platforms are eligible.
from .stocktwits import is_financial_topic

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_REFERER = "https://xueqiu.com/"
_XUEQIU_HOME = "https://xueqiu.com"
_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB safety cap

# Config key that carries the Cookie string ("name=value; name2=value2").
COOKIE_CONFIG_KEY = "XUEQIU_COOKIE"

# Per-depth result counts.
DEPTH_CONFIG = {
    "quick": 8,
    "default": 15,
    "deep": 25,
}

# Default node/category used for the public timeline. -1 = all categories.
_DEFAULT_CATEGORY = -1

_cookie_jar = _py_http_cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
)
_cookies_loaded = False


def _log(msg: str) -> None:
    log.source_log("Xueqiu", msg, tty_only=False)


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _load_cookie_from_config(config: Optional[Dict[str, Any]]) -> bool:
    """Load the cookie string from config into the shared jar. Idempotent."""
    global _cookies_loaded
    if _cookies_loaded:
        return True
    cookie_str = (config or {}).get(COOKIE_CONFIG_KEY) or ""
    if not cookie_str:
        return False
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        try:
            _cookie_jar.set_cookie(
                _py_http_cookiejar.Cookie(
                    version=0,
                    name=name.strip(),
                    value=value.strip(),
                    port=None,
                    port_specified=False,
                    domain=".xueqiu.com",
                    domain_specified=True,
                    domain_initial_dot=True,
                    path="/",
                    path_specified=True,
                    secure=True,
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={},
                )
            )
        except Exception as exc:  # malformed single cookie: skip, keep others
            _log(f"skip malformed cookie pair {name!r}: {exc}")
    _cookies_loaded = True
    return True


def _get_json(url: str, config: Optional[Dict[str, Any]] = None) -> Any:
    """Fetch JSON with cookie-aware opener. Raises HTTPError on failure."""
    _load_cookie_from_config(config)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": _REFERER})
    try:
        with _opener.open(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise http.HTTPError(f"Xueqiu HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise http.HTTPError(f"Xueqiu request failed: {exc.reason}") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise http.HTTPError("Xueqiu response exceeds the 2 MiB safety limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise http.HTTPError(f"Xueqiu JSON decode failed: {exc}") from exc


def _strip_html(text: str) -> str:
    """Remove HTML tags/entities from Xueqiu status text."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _ts_to_date_ms(ts: Any) -> Optional[str]:
    """Convert a millisecond Unix timestamp to YYYY-MM-DD or None."""
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _in_window(date_str: Optional[str], from_date: str, to_date: str) -> bool:
    if not date_str:
        return True
    if from_date and date_str < from_date:
        return False
    if to_date and date_str > to_date:
        return False
    return True


def _status_overlap(topic: str, text: str, title: str = "") -> float:
    text = f"{title} {text}".strip()
    if not text:
        return 0.0
    return token_overlap_relevance(topic, text)


def _fetch_timeline(category: int = _DEFAULT_CATEGORY, count: int = 50, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fetch the public timeline; returns raw status items (list may be empty)."""
    url = (
        f"https://xueqiu.com/v4/statuses/public_timeline_by_category.json"
        f"?since_id=-1&max_id=-1&count={count}&category={category}"
    )
    data = _get_json(url, config=config)
    if not isinstance(data, dict):
        raise http.HTTPError(f"Xueqiu timeline returned unexpected shape: {type(data).__name__}")
    items = data.get("list") or []
    if not isinstance(items, list):
        return []
    return items


def _fetch_hot_stocks(limit: int = 10, stock_type: int = 10, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fetch hot-stock ranking (best-effort context for symbol resolution)."""
    url = (
        f"https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
        f"?size={limit}&type={stock_type}"
    )
    data = _get_json(url, config=config)
    if not isinstance(data, dict):
        return []
    items = ((data.get("data") or {}).get("items")) or []
    if not isinstance(items, list):
        return []
    return items


def _normalize_status(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a raw status list item to the web-item shape.

    Each timeline list item carries a JSON-encoded ``data`` field holding the
    real post payload (text, user, like_count, target). Returns None when the
    item cannot be decoded.
    """
    try:
        post = (
            json.loads(raw["data"])
            if isinstance(raw.get("data"), str)
            else {}
        )
    except (json.JSONDecodeError, KeyError):
        return None
    if not isinstance(post, dict) or not post:
        return None

    user = post.get("user") or {}
    text = _strip_html(post.get("text") or post.get("description") or "")
    title = str(post.get("title") or "").strip()
    target = post.get("target", "")
    status_id = post.get("id")
    created = _ts_to_date_ms(post.get("created_at"))

    likes = int(post.get("like_count") or 0)
    comments = int(post.get("reply_count") or 0)
    retweets = int(post.get("retweet_count") or 0)

    return {
        "id": str(status_id or ""),
        "title": (title[:200] if title else (text[:60] or "雪球讨论")),
        "url": f"https://xueqiu.com{target}" if target else "",
        "source_domain": "xueqiu.com",
        "snippet": text[:300],
        "body": text,
        "date": created,
        "date_confidence": "high" if created else "low",
        "relevance": 0.0,
        "why_relevant": "",
        "engagement": {
            "likes": likes,
            "comments": comments,
            "retweets": retweets,
        },
        "author": user.get("screen_name") or user.get("username") or "",
        "symbols": (post.get("symbols") or []) if isinstance(post.get("symbols"), list) else [],
        "status_id": status_id,
    }


def search_xueqiu(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search Xueqiu for statuses related to ``topic``.

    Returns ``{"results": [...]}`` with normalized web-item dicts. On
    transport/parse failure returns ``{"results": [], "error": "..."}``.

    Only meaningful for financial topics; callers should have already gated
    on ``is_financial_topic(topic)`` (pipeline sets ``_financial_topic``).
    """
    if not topic or not topic.strip():
        return {"results": []}

    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    # A missing cookie is a configuration error, not a quiet empty state.
    if not (config or {}).get(COOKIE_CONFIG_KEY):
        return {"results": [], "error": f"{COOKIE_CONFIG_KEY} not configured"}

    raw_items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _absorb(items: List[Dict[str, Any]]) -> None:
        for item in items:
            status = _normalize_status(item)
            if not status:
                continue
            key = status["id"] or f"{status['url']}-{status['title']}"
            if key in seen:
                continue
            seen.add(key)
            raw_items.append(status)

    try:
        _absorb(_fetch_timeline(count=50, config=config))
        # Hot-stock context can surface symbols the topic mentions; fetch as a
        # best-effort second stream so financial topics have more candidates.
        try:
            hot = _fetch_hot_stocks(limit=10, config=config)
            for stock in hot:
                if not isinstance(stock, dict):
                    continue
                sym = stock.get("symbol") or ""
                name = stock.get("name") or ""
                if not sym:
                    continue
                # Inject a lightweight "status-like" candidate so a topic that
                # mentions a hot stock has a direct hit even without a matching
                # timeline post. Only kept when relevance passes later.
                raw_items.append({
                    "id": f"hotstock:{sym}",
                    "title": f"{name}（{sym}）热度讨论",
                    "url": f"https://xueqiu.com/S/{urllib.parse.quote(sym)}",
                    "source_domain": "xueqiu.com",
                    "snippet": f"{name}（{sym}）出现在雪球热门股票榜。",
                    "body": f"{name} {sym}",
                    "date": None,
                    "date_confidence": "low",
                    "relevance": 0.0,
                    "why_relevant": "",
                    "engagement": {"likes": 0, "comments": 0, "retweets": 0},
                    "author": "",
                    "symbols": [sym],
                    "status_id": None,
                })
        except Exception as exc:
            _log(f"hot-stock context fetch failed: {exc}")
    except Exception as exc:
        _log(f"Xueqiu search failed: {exc}")
        return {"results": [], "error": str(exc)}

    results: List[Dict[str, Any]] = []
    for status in raw_items:
        if not status["title"] and not status["body"]:
            continue
        if not _in_window(status["date"], from_date, to_date):
            continue
        score = _status_overlap(topic, status["body"], status["title"])
        # A symbol mention in the status is strong topical signal even when
        # the raw text overlap is weak (e.g. topic is a ticker symbol).
        if score <= 0 and status.get("symbols"):
            sym_text = " ".join(str(s) for s in status["symbols"]).lower()
            if any(term in sym_text for term in re.findall(r"[A-Za-z0-9.]+", topic.lower())):
                score = 0.4
        if score <= 0:
            continue
        status["relevance"] = score
        status["why_relevant"] = (
            f"Xueqiu status token overlap with '{topic}' (score {score:.2f})"
        )
        results.append(status)
        if len(results) >= limit:
            break

    results.sort(
        key=lambda it: (
            it["relevance"],
            it["engagement"].get("likes", 0),
        ),
        reverse=True,
    )
    _log(f"query '{topic}' -> {len(results)} relevant statuses (from {len(raw_items)} raw)")
    return {"results": results[:limit]}


def parse_xueqiu_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a ``search_xueqiu`` envelope into the pipeline's item shape."""
    if not isinstance(result, dict):
        return []
    results = result.get("results") or []
    if not isinstance(results, list):
        return []
    parsed: List[Dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        parsed.append({
            "id": item.get("id") or "",
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "author": item.get("author") or "",
            "snippet": item.get("snippet") or "",
            "body": item.get("body") or "",
            "date": item.get("date"),
            "date_confidence": item.get("date_confidence", "low"),
            "relevance": item.get("relevance", 0.0),
            "why_relevant": item.get("why_relevant") or "",
            "engagement": item.get("engagement") or {},
            "container": "雪球",
            "metadata": {
                "symbols": item.get("symbols") or [],
                "status_id": item.get("status_id"),
            },
        })
    return parsed
