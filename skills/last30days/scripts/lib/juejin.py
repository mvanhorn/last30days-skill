"""Juejin (掘金) source for last30days.

Juejin (juejin.cn) is a large Chinese-language developer article platform.
Its web client calls two keyless JSON endpoints, both POST:

- ``api.juejin.cn/search_api/v1/search`` searches articles. It takes no date
  filter and returns 20 rows a page whatever ``limit`` says. ``sort_type=1``
  favors recent articles, but pages are not in date order: every page of a
  busy term mixes dates from the whole month. ``sort_type=0`` and ``2``
  return mostly articles from months or years ago. So the source pages
  through ``sort_type=1``, keeps the in-window rows, and ranks them locally.
- ``api.juejin.cn/interact_api/v1/comment/list`` returns an article's
  top-level comments with like counts.

Most recent Juejin articles have no likes (a 2026-11 sample of 467 in-window
articles had a median of 0 likes and 35 views), so views count toward
engagement.

The source is off by default: ``INCLUDE_SOURCES=juejin`` or
``--search juejin`` turns it on.
"""

from __future__ import annotations

import datetime
import html
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from . import http, log, signals
from .relevance import token_overlap_relevance

# aid=2608 is the Juejin web client's app id; the API rejects calls without it.
SEARCH_URL = "https://api.juejin.cn/search_api/v1/search?aid=2608&spider=0"
COMMENTS_URL = "https://api.juejin.cn/interact_api/v1/comment/list?aid=2608"
ARTICLE_URL = "https://juejin.cn/post/{id}"
_HEADERS = {"Content-Type": "application/json"}

SEARCH_PAGES = {"quick": 1, "default": 3, "deep": 5}
KEEP_LIMIT = {"quick": 10, "default": 20, "deep": 30}
ENRICH_LIMIT = 3
MAX_COMMENTS = 5
# Same bar as dev.to and V2EX search hits: Juejin search also matches a term
# buried in an unrelated article.
SEARCH_RELEVANCE_FLOOR = 0.25
REQUEST_DEADLINE_SECONDS = 20.0
SEARCH_BUDGET_SECONDS = 30.0
ENRICH_WAIT_SECONDS = 25.0
TEXT_LIMIT = 500


def _log(msg: str) -> None:
    log.source_log("Juejin", msg, tty_only=False)


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


def _post(url: str, body: dict[str, Any], deadline: float | None = None) -> dict[str, Any]:
    data = http.request(
        "POST", url, headers=_HEADERS, json_data=body, timeout=15, retries=2,
        deadline_monotonic=min(
            time.monotonic() + REQUEST_DEADLINE_SECONDS,
            deadline if deadline is not None else float("inf"),
        ),
    )
    if not isinstance(data, dict) or data.get("err_no") != 0:
        detail = (data.get("err_msg") or data.get("err_no")) if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"unexpected Juejin response: {detail}")
    return data


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean(text: Any) -> str:
    return " ".join(html.unescape(str(text or "")).split())


def _tags(row: dict[str, Any]) -> list[str]:
    return [str(tag.get("tag_name")) for tag in row.get("tags") or [] if isinstance(tag, dict) and tag.get("tag_name")]


def _article_text(row: dict[str, Any]) -> str:
    info = row.get("article_info") or {}
    return " ".join([str(info.get("title") or ""), str(info.get("brief_content") or ""), " ".join(_tags(row))])


def _engagement(row: dict[str, Any]) -> dict[str, int]:
    info = row.get("article_info") or {}
    return {
        "likes": _int(info.get("digg_count")),
        "comments": _int(info.get("comment_count")),
        "saves": _int(info.get("collect_count")),
        "views": _int(info.get("view_count")),
    }


def _engagement_mix(row: dict[str, Any]) -> float:
    """The weighted log mix signals scores Juejin engagement on.

    Ranking on the same mix keeps the articles the engine will score highest;
    likes first would let one like outrank thousands of views.
    """
    engagement = _engagement(row)
    return sum(
        weight * math.log1p(engagement.get(field, 0))
        for field, weight in signals.ENGAGEMENT_WEIGHTS["juejin"]
    )


def search_juejin(
    topic: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    depth: str = "default",
    search_query: str | None = None,
) -> dict[str, Any]:
    """Page through recent Juejin articles and keep the in-window, on-topic ones.

    Returns ``{"articles": [...]}``, plus ``"error"`` when the first page
    failed so the run reports a source failure instead of a quiet empty result.
    A later page failing keeps the pages already fetched.
    """
    # Planner queries quote phrases for engines with phrase syntax; Juejin
    # treats the quotes as literal characters.
    query = " ".join((search_query or topic or "").replace('"', " ").split())
    if not query:
        return {"articles": []}
    start = _epoch(from_date)
    end = _epoch(to_date, end_of_day=True)
    deadline = time.monotonic() + SEARCH_BUDGET_SECONDS
    pages = SEARCH_PAGES.get(depth, SEARCH_PAGES["default"])
    rows: list[dict[str, Any]] = []
    cursor = "0"
    for page in range(1, pages + 1):
        if page > 1 and time.monotonic() >= deadline:
            _log(f"search '{query}': stopped after {page - 1} pages at the time budget")
            break
        body = {
            "key_word": query, "id_type": 2, "cursor": cursor, "limit": 20,
            "search_type": 0, "sort_type": 1, "version": 1,
        }
        try:
            data = _post(SEARCH_URL, body, deadline=deadline if page > 1 else None)
            if not isinstance(data.get("data"), list):
                # A changed reply shape is a failure, not "no matches".
                raise ValueError(f"unexpected Juejin search data: {type(data.get('data')).__name__}")
        except Exception as exc:
            if page == 1:
                _log(f"search failed for '{query}': {exc}")
                return {"articles": [], "error": f"search: {exc}"}
            _log(f"search '{query}': page {page} failed ({exc}); keeping the {page - 1} pages fetched")
            break
        batch = [
            r["result_model"] for r in data["data"]
            if isinstance(r, dict) and isinstance(r.get("result_model"), dict)
            and isinstance(r["result_model"].get("article_info"), dict)
        ]
        rows.extend(batch)
        created = [_int(row["article_info"].get("ctime")) for row in batch]
        # Pages are only loosely date-ordered, so stop early only once a whole
        # page predates the window.
        if not data.get("has_more") or not data.get("cursor") or (
            start is not None and created and max(created) < start
        ):
            break
        cursor = str(data["cursor"])

    articles: dict[str, dict[str, Any]] = {}
    dropped = 0
    for row in rows:
        article_id = str(row.get("article_id") or row["article_info"].get("article_id") or "")
        created = _int(row["article_info"].get("ctime"))
        if not article_id or article_id in articles or not created:
            continue
        if (start is not None and created < start) or (end is not None and created > end):
            continue
        if token_overlap_relevance(topic or query, _article_text(row)) < SEARCH_RELEVANCE_FLOOR:
            dropped += 1
            continue
        articles[article_id] = row
    kept = sorted(articles.values(), key=_engagement_mix, reverse=True)
    kept = kept[: KEEP_LIMIT.get(depth, KEEP_LIMIT["default"])]
    _log(f"search '{query}': kept {len(kept)} of {len(rows)} rows, dropped {dropped} off-topic")
    return {"articles": kept}


def parse_juejin_response(response: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Map Juejin articles to engine item dicts (the HN item shape)."""
    items: list[dict[str, Any]] = []
    for row in response.get("articles") or []:
        info = row.get("article_info") or {}
        author = row.get("author_user_info") or {}
        article_id = str(row.get("article_id") or info.get("article_id"))
        engagement = _engagement(row)
        likes, comments = engagement["likes"], engagement["comments"]
        content_score = token_overlap_relevance(query, _article_text(row)) if query else 0.5
        relevance = min(1.0, 0.7 * content_score + min(0.3, (likes + comments) / 100))
        created = datetime.datetime.fromtimestamp(_int(info.get("ctime")), datetime.timezone.utc)
        text = _clean(info.get("brief_content"))
        items.append({
            "id": article_id,
            "title": _clean(info.get("title")),
            "url": ARTICLE_URL.format(id=article_id),
            "author": str(author.get("user_name") or ""),
            "date": created.date().isoformat(),
            "text": text[:TEXT_LIMIT] + "..." if len(text) > TEXT_LIMIT else text,
            "engagement": engagement,
            "relevance": round(relevance, 2),
            "why_relevant": "Juejin article (search)",
            "metadata": {
                "tags": _tags(row),
                "category": str((row.get("category") or {}).get("category_name") or ""),
                "author_id": str(author.get("user_id") or info.get("user_id") or ""),
            },
        })
    return items


def _comment_date(created: Any) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(created), datetime.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _fetch_comments(article_id: str, author_id: str, deadline: float | None = None) -> list[dict[str, Any]]:
    """Return an article's most-liked community comments.

    Bot comments (``bot_id`` set) and the article author's own comments are
    never community comments.
    """
    if deadline is not None and time.monotonic() >= deadline:
        return []
    body = {"client_type": 2608, "cursor": "0", "item_id": article_id, "item_type": 2, "limit": 20}
    try:
        data = _post(COMMENTS_URL, body, deadline=deadline)
    except Exception as exc:
        _log(f"comments failed for article {article_id}: {exc}")
        return []
    comments = []
    for row in data.get("data") or []:
        if not isinstance(row, dict):
            continue
        info = row.get("comment_info") or {}
        user = row.get("user_info") or {}
        text = _clean(info.get("comment_content"))
        if not text or _int(info.get("bot_id")):
            continue
        if author_id and str(info.get("user_id") or user.get("user_id") or "") == author_id:
            continue
        comments.append({
            "author": str(user.get("user_name") or ""),
            "text": text[:300] + "..." if len(text) > 300 else text,
            "likes": _int(info.get("digg_count")),
            "date": _comment_date(info.get("ctime")),
        })
    comments.sort(key=lambda c: c["likes"], reverse=True)
    return comments[:MAX_COMMENTS]


def enrich_source_items(
    items: list[Any],
    top_k: int = ENRICH_LIMIT,
    deadline: float | None = None,
) -> list[Any]:
    """Attach top comments to the first ``top_k`` ranked SourceItems with comments.

    Runs from ``_finalize_items_by_source`` so the comment calls land on the
    articles that survived relevance pruning and dedupe.
    """
    from .normalize import _remap_comments

    targets = [
        item for item in items
        if getattr(item, "source", None) == "juejin"
        and not (item.metadata or {}).get("top_comments")
        and (item.engagement or {}).get("comments")
    ][:top_k]
    if not targets:
        return items
    wait_budget = ENRICH_WAIT_SECONDS
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log("comment enrichment skipped: no time left")
            return items
        wait_budget = min(wait_budget, remaining)
    executor = ThreadPoolExecutor(max_workers=len(targets))
    futures = {
        (
            http.submit_with_context(
                executor, _fetch_comments, item.item_id, (item.metadata or {}).get("author_id", ""), deadline,
            )
            if deadline is not None
            else http.submit_with_context(
                executor, _fetch_comments, item.item_id, (item.metadata or {}).get("author_id", ""),
            )
        ): item
        for item in targets
    }
    try:
        for future in as_completed(futures, timeout=wait_budget):
            item = futures[future]
            item.metadata["top_comments"] = _remap_comments(
                future.result(),
                score_keys=("likes",),
                excerpt_keys=("text", "excerpt"),
            )
    except FuturesTimeout:
        _log("comment enrichment hit its time limit; keeping the comments that arrived")
    finally:
        # Never block report finalization on a straggling comment fetch.
        executor.shutdown(wait=False, cancel_futures=True)
    attached = sum(1 for item in targets if item.metadata.get("top_comments"))
    _log(f"attached comments to {attached} of {len(targets)} top articles")
    return items
