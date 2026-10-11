"""dev.to (Forem) source for last30days.

Uses the public Forem API at dev.to (no key):

- ``/api/articles?tag=<tag>&top=<days>`` returns the most-engaged articles for
  a tag in the window. Precise, but only as good as the tag.
- ``/api/articles/search?q=<term>&top=<days>`` is free-text search sorted by
  recency. Single distinctive terms match well; multi-word queries match
  loosely, so every search hit must pass the engine's token-overlap relevance
  check before it is kept.
- ``/api/comments?a_id=<id>`` returns the comment tree for an article.

The source is off by default. Host-inferred tags (``--devto-tags``) turn it
on for a run; ``INCLUDE_SOURCES=devto`` or ``--search devto`` turn it on
without tags, in which case only the keyword search runs. An unknown tag
returns ``[]`` with HTTP 200, so a wrong tag stays quiet instead of failing.
"""

from __future__ import annotations

import datetime
import html
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from . import http, log
from .relevance import token_overlap_relevance

ARTICLES_URL = "https://dev.to/api/articles"
SEARCH_URL = "https://dev.to/api/articles/search"
COMMENTS_URL = "https://dev.to/api/comments"
_SEARCH_HEADERS = {"Accept": "application/vnd.forem.api-v1+json"}

TAG_LIMITS = {"quick": 1, "default": 2, "deep": 3}
SEARCH_PER_PAGE = {"quick": 10, "default": 20, "deep": 30}
TAG_PER_PAGE = 30
ENRICH_LIMIT = 3
MAX_COMMENTS = 5
# Search hits below this token-overlap score are dropped. Same bar as the
# keyless Reddit lane: it keeps "kubernetes" hits and drops the newest-posts
# noise a loose multi-word query ("claude code") returns.
SEARCH_RELEVANCE_FLOOR = 0.25
# Total wall-clock cap per request. The socket timeout alone bounds each
# read, so a response trickling in small chunks could otherwise run on.
REQUEST_DEADLINE_SECONDS = 20.0
ENRICH_WAIT_SECONDS = 25.0

_TAG_RE = re.compile(r"[^a-z0-9]")
# Bot comments on dev.to ("Official Platform Update ... tr.ee/dev-to") hide
# phishing behind link shorteners; real discussion rarely needs one.
_SHORTENER_RE = re.compile(
    r"\b(?:bit\.ly|tinyurl\.com|tr\.ee|shorturl\.at|t\.co|rb\.gy|cutt\.ly|is\.gd|ow\.ly|tiny\.cc)/",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    log.source_log("dev.to", msg, tty_only=False)


def normalize_tag(value: str) -> str:
    return _TAG_RE.sub("", (value or "").lower().lstrip("#"))


def resolve_tags(tags: list[str] | None, depth: str = "default") -> list[str]:
    """Normalize and dedupe host-supplied tags, capped by depth."""
    resolved: list[str] = []
    for tag in (normalize_tag(t) for t in (tags or [])):
        if tag and tag not in resolved:
            resolved.append(tag)
    return resolved[: TAG_LIMITS.get(depth, TAG_LIMITS["default"])]


def _window_days(from_date: str | None, to_date: str | None) -> int:
    try:
        start = datetime.date.fromisoformat(from_date or "")
        end = datetime.date.fromisoformat(to_date or "") if to_date else datetime.date.today()
        return max(1, (end - start).days)
    except ValueError:
        return 30


# The pipeline calls a source once per subquery, and the tag feed does not
# depend on the subquery, so identical URLs are fetched once per process.
_RESPONSE_CACHE: dict[str, list[dict[str, Any]]] = {}
_URL_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def clear_cache() -> None:
    with _LOCKS_GUARD:
        _RESPONSE_CACHE.clear()
        _URL_LOCKS.clear()


def _get_list(url: str, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    # Concurrent subquery streams can request the same URL; the per-URL lock
    # makes the second caller wait for the first fetch instead of repeating it.
    with _LOCKS_GUARD:
        lock = _URL_LOCKS.setdefault(url, threading.Lock())
    with lock:
        if url not in _RESPONSE_CACHE:
            data = http.request(
                "GET", url, headers=headers, timeout=15, retries=2,
                deadline_monotonic=time.monotonic() + REQUEST_DEADLINE_SECONDS,
                owned_get=True,
            )
            if not isinstance(data, list):
                # Forem list endpoints always return a JSON array; anything
                # else is a changed or error response, not "no matches".
                raise ValueError(f"unexpected dev.to response shape: {type(data).__name__}")
            _RESPONSE_CACHE[url] = [row for row in data if isinstance(row, dict)]
        return [dict(row) for row in _RESPONSE_CACHE[url]]


def _in_window(article: dict[str, Any], from_date: str | None, to_date: str | None) -> bool:
    published = str(article.get("published_at") or "")[:10]
    if not published:
        return True
    if from_date and published < from_date:
        return False
    if to_date and published > to_date:
        return False
    return True


def _article_text(article: dict[str, Any]) -> str:
    tags = article.get("tag_list") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return " ".join([str(article.get("title") or ""), str(article.get("description") or ""), " ".join(tags)])


def search_devto(
    topic: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    depth: str = "default",
    tags: list[str] | None = None,
    search_query: str | None = None,
) -> dict[str, Any]:
    """Fetch tag-feed and keyword-search articles for the topic.

    Returns ``{"articles": [...], "tags": [...], "errors": [...]}``. A failed
    endpoint is recorded in ``errors`` and the other endpoint still counts.
    """
    days = _window_days(from_date, to_date)
    resolved_tags = resolve_tags(tags, depth)
    articles: dict[int, dict[str, Any]] = {}
    errors: list[str] = []

    for tag in resolved_tags:
        url = f"{ARTICLES_URL}?{urllib.parse.urlencode({'tag': tag, 'top': days, 'per_page': TAG_PER_PAGE})}"
        try:
            rows = _get_list(url)
        except Exception as exc:
            errors.append(f"tag {tag}: {exc}")
            _log(f"tag feed failed for '{tag}': {exc}")
            continue
        kept = 0
        for row in rows:
            if row.get("id") is not None and _in_window(row, from_date, to_date):
                row.setdefault("_devto_match", f"tag:{tag}")
                articles.setdefault(row["id"], row)
                kept += 1
        _log(f"tag '{tag}': {kept} articles in window")

    # Planner queries quote phrases for engines with phrase syntax; Forem
    # search treats the quotes as literal characters.
    query = (search_query or topic or "").replace('"', " ").strip()
    query = " ".join(query.split())
    if query:
        params = {"q": query, "top": days, "per_page": SEARCH_PER_PAGE.get(depth, SEARCH_PER_PAGE["default"])}
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        try:
            rows = _get_list(url, headers=_SEARCH_HEADERS)
        except Exception as exc:
            errors.append(f"search: {exc}")
            _log(f"search failed for '{query}': {exc}")
            rows = []
        kept = dropped = 0
        for row in rows:
            if row.get("id") is None or not _in_window(row, from_date, to_date):
                continue
            if token_overlap_relevance(topic or query, _article_text(row)) < SEARCH_RELEVANCE_FLOOR:
                dropped += 1
                continue
            row.setdefault("_devto_match", "search")
            articles.setdefault(row["id"], row)
            kept += 1
        _log(f"search '{query}': kept {kept}, dropped {dropped} off-topic")

    result: dict[str, Any] = {"articles": list(articles.values()), "tags": resolved_tags, "errors": errors}
    attempted = len(resolved_tags) + (1 if query else 0)
    if attempted and len(errors) == attempted:
        # Every endpoint failed: surface it so the run reports a source
        # failure instead of a quiet empty result.
        result["error"] = "; ".join(errors)
    return result


def parse_devto_response(response: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Map Forem articles to engine item dicts (the HN item shape)."""
    items: list[dict[str, Any]] = []
    for article in response.get("articles") or []:
        reactions = int(article.get("public_reactions_count") or article.get("positive_reactions_count") or 0)
        comments = int(article.get("comments_count") or 0)
        user = article.get("user") or {}
        content_score = token_overlap_relevance(query, _article_text(article)) if query else 0.5
        relevance = min(1.0, 0.7 * content_score + min(0.3, (reactions + comments) / 100))
        tags = article.get("tag_list") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        items.append({
            "id": str(article.get("id")),
            "title": str(article.get("title") or "").strip(),
            "url": str(article.get("url") or ""),
            "author": str(user.get("username") or user.get("name") or ""),
            "date": str(article.get("published_at") or "")[:10] or None,
            "text": str(article.get("description") or "").strip(),
            "engagement": {"reactions": reactions, "comments": comments},
            "relevance": round(relevance, 2),
            "why_relevant": f"dev.to article ({article.get('_devto_match', 'match')})",
            "metadata": {
                "tags": tags,
                "reading_time_minutes": article.get("reading_time_minutes"),
                "ai_disclosure_level": article.get("ai_disclosure_level"),
                "match": article.get("_devto_match"),
            },
        })
    return items


def _strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p)\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _fetch_comments(article_id: str) -> list[dict[str, Any]]:
    url = f"{COMMENTS_URL}?{urllib.parse.urlencode({'a_id': article_id})}"
    try:
        rows = _get_list(url)
    except Exception as exc:
        _log(f"comments failed for article {article_id}: {exc}")
        return []
    comments = []
    for row in rows:
        body_html = str(row.get("body_html") or "")
        text = _strip_html(body_html)
        # Check the raw HTML too: a shortener can hide in an href.
        if not text or _SHORTENER_RE.search(html.unescape(body_html)):
            continue
        user = row.get("user") or {}
        comments.append({
            "author": str(user.get("username") or user.get("name") or ""),
            "text": text[:300] + "..." if len(text) > 300 else text,
            # The comments API exposes no like count; direct replies are the
            # only engagement signal it returns.
            "replies": len(row.get("children") or []),
            "ai_disclosure_level": row.get("ai_disclosure_level"),
        })
    comments.sort(key=lambda c: c["replies"], reverse=True)
    return comments[:MAX_COMMENTS]


def enrich_source_items(items: list[Any], top_k: int = ENRICH_LIMIT) -> list[Any]:
    """Attach top comments to the first ``top_k`` ranked SourceItems with comments.

    Runs from ``_finalize_items_by_source`` so the comment calls land on the
    articles that survived relevance pruning and dedupe, not on high-engagement
    tag-feed articles the ranking later drops.
    """
    from .normalize import _remap_comments

    targets = []
    for item in items:
        if len(targets) >= top_k:
            break
        if getattr(item, "source", None) != "devto":
            continue
        if (item.metadata or {}).get("top_comments"):
            continue
        if not (item.engagement or {}).get("comments"):
            continue
        targets.append(item)
    if not targets:
        return items
    executor = ThreadPoolExecutor(max_workers=len(targets))
    futures = {http.submit_with_context(executor, _fetch_comments, item.item_id): item for item in targets}
    try:
        for future in as_completed(futures, timeout=ENRICH_WAIT_SECONDS):
            item = futures[future]
            item.metadata["top_comments"] = _remap_comments(
                future.result(),
                score_keys=("replies",),
                excerpt_keys=("text", "excerpt"),
                preserve_absent_score=True,
            )
    except FuturesTimeout:
        _log("comment enrichment hit its time limit; keeping the comments that arrived")
    finally:
        # Never block report finalization on a straggling comment fetch.
        executor.shutdown(wait=False, cancel_futures=True)
    attached = sum(1 for item in targets if item.metadata.get("top_comments"))
    _log(f"attached comments to {attached} of {len(targets)} top articles")
    return items
