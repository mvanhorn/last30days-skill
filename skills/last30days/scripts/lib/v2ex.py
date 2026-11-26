"""V2EX source for last30days.

V2EX (v2ex.com) is the largest Chinese-language developer community. Its own
public API (v1) has no search, so retrieval goes through two keyless
endpoints:

- SOV2EX (``www.sov2ex.com/api/search``), the community-run full-text index
  of V2EX topics. It takes ``gte``/``lte`` epoch-second bounds, so past
  windows are filtered server-side. ``operator=and`` requires every query
  term; the default OR matching returns topics that share one common word.
  ``size`` is capped at 50.
- V2EX API v1 (``www.v2ex.com/api/replies/show.json``) returns a topic's
  replies in thread order. Replies carry no votes. The API allows about 120
  requests an hour per IP, so only the top-ranked topics are enriched.

SOV2EX's ``replies`` count is a snapshot from indexing time (a new topic
reads 0), so enrichment refreshes it from the replies it fetches.

The source is off by default: ``INCLUDE_SOURCES=v2ex`` or ``--search v2ex``
turns it on.
"""

from __future__ import annotations

import datetime
import html
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from . import http, log, signals
from .relevance import token_overlap_relevance

SEARCH_URL = "https://www.sov2ex.com/api/search"
REPLIES_URL = "https://www.v2ex.com/api/replies/show.json"
TOPIC_URL = "https://www.v2ex.com/t/{id}"

SEARCH_SIZE = {"quick": 10, "default": 20, "deep": 40}
ENRICH_LIMIT = 3
MAX_COMMENTS = 5
# Same bar as dev.to search hits: SOV2EX matches every term somewhere in the
# topic, but a term buried in a long job post is not a topic about it.
SEARCH_RELEVANCE_FLOOR = 0.25
REQUEST_DEADLINE_SECONDS = 20.0
ENRICH_WAIT_SECONDS = 25.0
TEXT_LIMIT = 500


def _log(msg: str) -> None:
    log.source_log("V2EX", msg, tty_only=False)


def _epoch(day: str | None, *, end_of_day: bool = False) -> int | None:
    """UTC epoch seconds for the start (or end) of an ISO date."""
    try:
        date = datetime.date.fromisoformat(day or "")
    except ValueError:
        return None
    moment = datetime.datetime.combine(
        date,
        datetime.time.max if end_of_day else datetime.time.min,
        tzinfo=datetime.timezone.utc,
    )
    return int(moment.timestamp())


def _get(url: str, deadline: float | None = None) -> Any:
    return http.request(
        "GET", url, timeout=15, retries=2,
        deadline_monotonic=min(
            time.monotonic() + REQUEST_DEADLINE_SECONDS,
            deadline if deadline is not None else float("inf"),
        ),
        owned_get=True,
    )


def _topic_text(topic: dict[str, Any]) -> str:
    return " ".join([str(topic.get("title") or ""), str(topic.get("content") or "")])


def search_v2ex(
    topic: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    depth: str = "default",
    search_query: str | None = None,
) -> dict[str, Any]:
    """Search V2EX topics created in the window.

    Returns ``{"topics": [...]}``, plus ``"error"`` when the search failed so
    the run reports a source failure instead of a quiet empty result.
    """
    # Planner queries quote phrases for engines with phrase syntax; SOV2EX
    # treats the quotes as literal characters.
    query = " ".join((search_query or topic or "").replace('"', " ").split())
    if not query:
        return {"topics": []}
    params: dict[str, Any] = {
        "q": query,
        "size": SEARCH_SIZE.get(depth, SEARCH_SIZE["default"]),
        "sort": "sumup",
        "operator": "and",
    }
    gte = _epoch(from_date)
    lte = _epoch(to_date, end_of_day=True)
    if gte is not None:
        params["gte"] = gte
    if lte is not None:
        params["lte"] = lte
    try:
        data = _get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            # SOV2EX answers a rejected query with {"message": ...}, not hits.
            detail = data.get("message") if isinstance(data, dict) else type(data).__name__
            raise ValueError(f"unexpected SOV2EX response: {detail}")
    except Exception as exc:
        _log(f"search failed for '{query}': {exc}")
        return {"topics": [], "error": f"search: {exc}"}

    topics: list[dict[str, Any]] = []
    seen: set[int] = set()
    dropped = 0
    for hit in data["hits"]:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict) or source.get("id") is None:
            continue
        if source["id"] in seen:
            continue
        if token_overlap_relevance(topic or query, _topic_text(source)) < SEARCH_RELEVANCE_FLOOR:
            dropped += 1
            continue
        seen.add(source["id"])
        topics.append(source)
    _log(f"search '{query}': kept {len(topics)}, dropped {dropped} off-topic")
    return {"topics": topics}


def _clean(text: Any) -> str:
    return " ".join(html.unescape(str(text or "")).split())


def parse_v2ex_response(response: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Map SOV2EX topics to engine item dicts (the HN item shape)."""
    items: list[dict[str, Any]] = []
    for topic in response.get("topics") or []:
        replies = int(topic.get("replies") or 0)
        content_score = token_overlap_relevance(query, _topic_text(topic)) if query else 0.5
        relevance = min(1.0, 0.7 * content_score + min(0.3, replies / 100))
        content = _clean(topic.get("content"))
        items.append({
            "id": str(topic.get("id")),
            "title": _clean(topic.get("title")),
            "url": TOPIC_URL.format(id=topic.get("id")),
            "author": str(topic.get("member") or ""),
            # SOV2EX timestamps are UTC without an offset.
            "date": str(topic.get("created") or "")[:10] or None,
            "text": content[:TEXT_LIMIT] + "..." if len(content) > TEXT_LIMIT else content,
            "engagement": {"replies": replies},
            "relevance": round(relevance, 2),
            "why_relevant": "V2EX topic (search)",
            "metadata": {"node_id": topic.get("node")},
        })
    return items


_LEADING_MENTIONS = re.compile(r"^(?:@\S+\s*)+")


def _fetch_replies(topic_id: str, op: str, deadline: float | None = None) -> tuple[int, list[dict[str, Any]]] | None:
    """Return the topic's reply count and its leading community replies.

    Replies arrive in thread order with no votes. Replies that open with an
    ``@handle`` answer another reply, so replies to the topic itself lead and
    keep their thread order. The topic's own author is never a community
    comment.
    """
    if deadline is not None and time.monotonic() >= deadline:
        return None
    url = f"{REPLIES_URL}?{urllib.parse.urlencode({'topic_id': topic_id})}"
    try:
        rows = _get(url, deadline=deadline)
        if not isinstance(rows, list):
            raise ValueError(f"unexpected V2EX response shape: {type(rows).__name__}")
    except Exception as exc:
        _log(f"replies failed for topic {topic_id}: {exc}")
        return None
    direct: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        member = row.get("member") or {}
        author = str(member.get("username") or "")
        raw_text = _clean(row.get("content"))
        text = _LEADING_MENTIONS.sub("", raw_text)
        if not text or (op and author == op):
            continue
        (mentions if text != raw_text else direct).append({
            "author": author,
            "text": text[:300] + "..." if len(text) > 300 else text,
            "date": _reply_date(row.get("created")),
        })
    return len(rows), (direct + mentions)[:MAX_COMMENTS]


def _reply_date(created: Any) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(created), datetime.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def enrich_source_items(
    items: list[Any],
    top_k: int = ENRICH_LIMIT,
    deadline: float | None = None,
) -> list[Any]:
    """Attach replies to the first ``top_k`` ranked SourceItems.

    Runs from ``_finalize_items_by_source`` so the reply calls land on topics
    that survived relevance pruning and dedupe. Every target is fetched, not
    only topics with replies, because SOV2EX's reply count can be stale.
    """
    from .normalize import _remap_comments

    targets = [
        item for item in items
        if getattr(item, "source", None) == "v2ex"
        and not (item.metadata or {}).get("top_comments")
    ][:top_k]
    if not targets:
        return items
    wait_budget = ENRICH_WAIT_SECONDS
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log("reply enrichment skipped: no time left")
            return items
        wait_budget = min(wait_budget, remaining)
    executor = ThreadPoolExecutor(max_workers=len(targets))
    futures = {
        (
            http.submit_with_context(executor, _fetch_replies, item.item_id, item.author, deadline)
            if deadline is not None
            else http.submit_with_context(executor, _fetch_replies, item.item_id, item.author)
        ): item
        for item in targets
    }
    try:
        for future in as_completed(futures, timeout=wait_budget):
            result = future.result()
            if result is None:
                continue
            item = futures[future]
            count, comments = result
            item.engagement = {**(item.engagement or {}), "replies": max(count, int((item.engagement or {}).get("replies") or 0))}
            # Scores were set before enrichment; fusion ranks on this one, so
            # the refreshed count must reach it (as fusion's own merge does).
            item.engagement_score = signals.engagement_score(item)
            item.metadata["top_comments"] = _remap_comments(
                comments,
                # V2EX replies carry no votes; the absent score stays None.
                score_keys=("score",),
                excerpt_keys=("text", "excerpt"),
                preserve_absent_score=True,
            )
    except FuturesTimeout:
        _log("reply enrichment hit its time limit; keeping the replies that arrived")
    finally:
        # Never block report finalization on a straggling reply fetch.
        executor.shutdown(wait=False, cancel_futures=True)
    attached = sum(1 for item in targets if item.metadata.get("top_comments"))
    _log(f"attached replies to {attached} of {len(targets)} top topics")
    return items
