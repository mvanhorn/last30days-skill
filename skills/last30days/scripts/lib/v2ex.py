"""V2EX — public API source for last30days.

Fetches topics from V2EX's public HTTPS JSON API (no auth required) and
filters them against the research topic via token-overlap relevance.

Activation gate: always available when the network is reachable; the V2EX
public API has no key requirement. ``pipeline.available_sources`` includes
``v2ex`` unconditionally (subject to ``EXCLUDE_SOURCES``).

Search model: the V2EX public API has NO full-text search endpoint
(``/api/search.json`` is not exposed; agent-reach's own ``search`` method
returns an error for exactly this reason). This adapter therefore follows
the digg/listing pattern instead: pull the hot-topics stream (plus a few
high-signal default nodes) and keep only items whose title/body shares
informative tokens with the topic. Topics that have no meaningful overlap
yield an empty result — the caller's normal empty-state handling applies.

Recency: V2EX ``created`` is a Unix timestamp; the adapter drops items
outside the requested window (same hard date filter as other sources).

Endpoints used:
- GET /api/topics/hot.json            -> hot topics across the site
- GET /api/topics/show.json?node_name -> latest topics in a node
- GET /api/replies/show.json?id       -> replies for a topic (enrichment)
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import http, log
from .relevance import token_overlap_relevance

# V2EX public API base. HTTPS only.
_API_BASE = "https://www.v2ex.com"

_UA = "last30days/3.x (+https://github.com/mvanhorn/last30days-skill)"

_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MiB safety cap

# Default nodes pulled alongside the hot stream. Chosen for broad coverage of
# technology/programming discussion; node names are stable V2EX slugs.
_DEFAULT_NODES = ("tech", "programmer", "python", "nodejs")

# Per-depth stream sizes. V2EX hot.json returns a fixed 20; node listings can
# return more, so the cap only really binds on node pulls.
DEPTH_CONFIG = {
    "quick": 10,
    "default": 20,
    "deep": 40,
}

# How many top replies to attach to each topic in default/deep depth.
REPLIES_CONFIG = {
    "quick": 0,
    "default": 3,
    "deep": 5,
}

# V2EX has no engagement beyond reply count; treat replies as the signal.
_ENGAGEMENT_FIELD = "replies"


def _log(msg: str) -> None:
    log.source_log("V2EX", msg, tty_only=False)


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _v2ex_url(path: str, **params: Any) -> str:
    """Build a V2EX API URL with strict host/path validation."""
    url = f"{_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def _get_json(url: str) -> Any:
    """Fetch JSON via stdlib HTTP stack. Raises on transport errors."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in {"v2ex.com", "www.v2ex.com"}:
        raise ValueError(f"only V2EX HTTPS API is allowed, got {url}")
    if not parsed.path.startswith("/api/"):
        raise ValueError(f"only V2EX /api/ paths are allowed, got {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    except ssl.SSLError as exc:
        # Retryable TLS EOF (V2EX edge is flaky); bubble up as HTTPError so
        # the pipeline's normal classify path treats it as unreachable/retry.
        text = str(exc).casefold()
        if "unexpected_eof" in text or "eof occurred" in text:
            raise http.HTTPError(f"V2EX TLS EOF: {exc}")
        raise
    except urllib.error.URLError as exc:
        raise http.HTTPError(f"V2EX request failed: {exc.reason}") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise http.HTTPError("V2EX API response exceeds the 1 MiB safety limit")
    return json.loads(raw.decode("utf-8"))


def _ts_to_date(ts: Any) -> Optional[str]:
    """Convert a Unix timestamp (seconds) to YYYY-MM-DD or None."""
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _in_window(date_str: Optional[str], from_date: str, to_date: str) -> bool:
    """Hard date filter shared with other sources."""
    if not date_str:
        return True  # unknown date: keep (caller's relevance gate still applies)
    if from_date and date_str < from_date:
        return False
    if to_date and date_str > to_date:
        return False
    return True


def _topic_overlap(topic: str, title: str, body: str = "") -> float:
    """Token-overlap relevance against the topic (0..1)."""
    text = f"{title} {body}".strip()
    if not text:
        return 0.0
    return token_overlap_relevance(topic, text)


def _fetch_hot_topics() -> List[Dict[str, Any]]:
    """Fetch the site-wide hot-topics stream."""
    url = _v2ex_url("/api/topics/hot.json")
    data = _get_json(url)
    if not isinstance(data, list):
        raise http.HTTPError(f"V2EX hot.json returned unexpected shape: {type(data).__name__}")
    return data


def _fetch_node_topics(node_name: str, page: int = 1) -> List[Dict[str, Any]]:
    """Fetch the latest topics in a node."""
    url = _v2ex_url("/api/topics/show.json", node_name=node_name, page=page)
    data = _get_json(url)
    if not isinstance(data, list):
        raise http.HTTPError(f"V2EX node {node_name} returned unexpected shape")
    return data


def _fetch_replies(topic_id: int, limit: int) -> List[Dict[str, Any]]:
    """Fetch replies for a topic (best-effort; returns [] on failure)."""
    if limit <= 0:
        return []
    url = _v2ex_url("/api/replies/show.json", topic_id=topic_id)
    try:
        data = _get_json(url)
    except Exception as exc:  # enrichment is best-effort
        _log(f"replies fetch failed for topic {topic_id}: {exc}")
        return []
    if not isinstance(data, list):
        return []
    return data[:limit]


def _normalize_topic(raw: Dict[str, Any], node_name: Optional[str] = None) -> Dict[str, Any]:
    """Normalize a raw V2EX topic dict to the web-item shape used by other sources."""
    node = raw.get("node") or {}
    created = _ts_to_date(raw.get("created"))
    title = str(raw.get("title") or "").strip()
    content = str(raw.get("content") or "").strip()
    topic_id = raw.get("id")
    url = raw.get("url") or (f"https://www.v2ex.com/t/{topic_id}" if topic_id else "")

    snippet = content[:300] if content else title[:300]

    return {
        "id": str(raw.get("id") or ""),
        "title": title[:200],
        "url": url,
        "source_domain": "v2ex.com",
        "snippet": snippet,
        "body": content,
        "date": created,
        "date_confidence": "high" if created else "low",
        "relevance": 0.0,  # set by caller after topic comparison
        "why_relevant": "",
        "engagement": {
            _ENGAGEMENT_FIELD: int(raw.get("replies") or 0),
        },
        # Raw fields for enrichment/debugging.
        "node_name": node.get("name") or node_name,
        "node_title": node.get("title") or "",
        "author": (raw.get("member") or {}).get("username") or "",
        "replies": int(raw.get("replies") or 0),
        "topic_id": topic_id,
    }


def search_v2ex(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search V2EX for topics related to ``topic``.

    Returns ``{"results": [...]}`` with normalized web-item dicts. On
    transport/parse failure returns ``{"results": [], "error": "..."}``.
    """
    if not topic or not topic.strip():
        return {"results": []}

    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    reply_limit = REPLIES_CONFIG.get(depth, REPLIES_CONFIG["default"])

    raw_items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _absorb(items: List[Dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = item.get("id")
            if tid is None:
                continue
            key = str(tid)
            if key in seen:
                continue
            seen.add(key)
            raw_items.append(item)

    try:
        _absorb(_fetch_hot_topics())
        # Node pulls give topical coverage the hot stream may miss.
        nodes = config.get("V2EX_NODES") if config else None
        for node in (nodes or _DEFAULT_NODES):
            try:
                _absorb(_fetch_node_topics(node))
            except Exception as exc:
                _log(f"node {node} fetch failed: {exc}")
    except Exception as exc:
        _log(f"V2EX search failed: {exc}")
        return {"results": [], "error": str(exc)}

    results: List[Dict[str, Any]] = []
    for raw in raw_items:
        item = _normalize_topic(raw)
        if not item["title"]:
            continue
        # Hard date filter first.
        if not _in_window(item["date"], from_date, to_date):
            continue
        score = _topic_overlap(topic, item["title"], item.get("body", ""))
        if score <= 0:
            continue
        item["relevance"] = score
        item["why_relevant"] = (
            f"V2EX title/body token overlap with '{topic}' (score {score:.2f})"
        )
        results.append(item)
        if len(results) >= limit:
            break

    # Attach replies for the kept topics (default/deep only) — best-effort.
    if reply_limit > 0:
        for item in results[:limit]:
            tid = item.get("topic_id")
            if not isinstance(tid, int):
                continue
            replies = _fetch_replies(tid, reply_limit)
            if replies:
                item["replies_data"] = [
                    {
                        "author": (r.get("member") or {}).get("username") or "",
                        "text": str(r.get("content") or "")[:500],
                        "created": _ts_to_date(r.get("created")),
                    }
                    for r in replies
                ]

    # V2EX carries no per-item engagement beyond replies, so final ordering is
    # relevance-dominant (replies as tiebreak), matching arxiv's pattern.
    results.sort(
        key=lambda it: (it["relevance"], it["engagement"].get(_ENGAGEMENT_FIELD, 0)),
        reverse=True,
    )
    _log(f"query '{topic}' -> {len(results)} relevant topics (from {len(raw_items)} raw)")
    return {"results": results[:limit]}


def parse_v2ex_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a ``search_v2ex`` envelope into the pipeline's item shape."""
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
            "container": item.get("node_title") or "V2EX",
            "metadata": {
                "node_name": item.get("node_name") or "",
                "topic_id": item.get("topic_id"),
                "replies_data": item.get("replies_data") or [],
            },
        })
    return parsed
