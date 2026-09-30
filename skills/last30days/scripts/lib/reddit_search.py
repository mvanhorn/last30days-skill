"""Keyless Reddit discovery via Reddit's own site search fragment.

``/svc/shreddit/search/?q=...&type=posts`` is the server-rendered HTML the
reddit.com search page loads. It serves HTTP 200 with no API key, and
``/svc/shreddit/r/{sub}/search/`` restricts the same search to one subreddit.
Each post result unit carries:

- a ``<search-telemetry-tracker>`` whose JSON tracking context has
  ``action_info.type == "post"``, the post id, title, and subreddit,
- the post-title link (the canonical permalink),
- a ``<faceplate-timeago ts=...>`` post date,
- a counter row of ``<faceplate-number>`` votes and comments.

So every discovered post arrives dated and scored, with no extra listing fetch.
The next page is a lazy ``<faceplate-partial>`` whose ``src`` carries a
``cursor``. Output dicts match ``reddit_listing.parse_cards`` so downstream
code is unaffected.
"""

import html as _html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from . import http, reddit_listing
from .relevance import token_overlap_relevance

BASE = "https://www.reddit.com"

# Post caps per run by depth.
DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}
# Global search pages per depth (about 7 posts per page).
GLOBAL_PAGE_CAPS = {"quick": 2, "default": 4, "deep": 8}
# Targeted subreddits get one page each.
SUB_PAGE_CAP = 1
MAX_WORKERS = 4
SEARCH_TIMEOUT = 15

# A recognizable page has post result units or Reddit's explicit no-results
# unit. Anything else (challenge page, changed markup) is a lane failure.
RESULTS_MARKER = 'view-events="search/view/post"'
NO_RESULTS_MARKER = 'view-events="search/view/no_results"'

_TRACKER = re.compile(
    r'<search-telemetry-tracker\b[^>]*?\bdata-faceplate-tracking-context="([^"]*)"'
)
_TITLE_HREF = re.compile(
    r'<a\b[^>]*?data-testid="post-title"[^>]*?\bhref="([^"]*)"'
    r'|<a\b[^>]*?\bhref="([^"]*)"[^>]*?data-testid="post-title"'
)
_TIMEAGO = re.compile(r'<faceplate-timeago\b[^>]*?\bts="([^"]+)"')
_COUNTER = re.compile(
    r'<faceplate-number\b[^>]*?\bnumber="(\d+)"[^>]*>\s*</faceplate-number>\s*(vote|comment)'
)
_NEXT_PAGE = re.compile(
    r'<faceplate-partial\b[^>]*?\bsrc="([^"]*/svc/shreddit/[^"]*search/[^"]*)"'
)


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditSearch] {msg}\n")
    sys.stderr.flush()


def unrecognized_body(text: str) -> Optional[str]:
    """Validator for the fetch hook: None for a search page, else a reason."""
    if NO_RESULTS_MARKER in text:
        return None
    if RESULTS_MARKER in text:
        # Marker kept but tracking context changed shape: a clean zero-result
        # parse would hide the drift, so treat it as a lane failure.
        if _post_units(text):
            return None
        return "Reddit search result units no longer parse"
    return "not a Reddit search results page"


def search_url(
    query: str,
    time_filter: str = "month",
    subreddit: Optional[str] = None,
    cursor: Optional[str] = None,
) -> str:
    """Build a deterministic search URL so concurrent streams share the memo."""
    if subreddit:
        sub = subreddit.strip().removeprefix("r/").strip("/")
        path = f"/svc/shreddit/r/{quote(sub, safe='')}/search/"
    else:
        path = "/svc/shreddit/search/"
    params = [("q", query), ("type", "posts"), ("t", time_filter), ("sort", "relevance")]
    if cursor:
        params.append(("cursor", cursor))
    return f"{BASE}{path}?{urlencode(params)}"


def _post_units(html_text: str) -> List[Tuple[int, Dict[str, Any]]]:
    """(offset, tracking context) for each post-title tracker, in page order."""
    units = []
    for m in _TRACKER.finditer(html_text):
        try:
            ctx = json.loads(_html.unescape(m.group(1)))
        except (ValueError, TypeError):
            continue
        if not isinstance(ctx, dict):
            continue
        action = ctx.get("action_info") or {}
        post = ctx.get("post") or {}
        if action.get("type") == "post" and post.get("id"):
            units.append((m.start(), ctx))
    return units


def _next_cursor(html_text: str) -> Optional[str]:
    for m in _NEXT_PAGE.finditer(html_text):
        query = urlsplit(_html.unescape(m.group(1))).query
        cursor = (parse_qs(query).get("cursor") or [None])[0]
        if cursor:
            return cursor
    return None


def parse_page(html_text: str, query: str = "") -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse one search fragment into (normalized posts, next-page cursor)."""
    html_text = html_text or ""
    units = _post_units(html_text)
    posts: List[Dict[str, Any]] = []
    seen: set = set()
    for i, (start, ctx) in enumerate(units):
        post_id = str(ctx["post"]["id"]).removeprefix("t3_")
        if post_id in seen:
            continue
        seen.add(post_id)
        end = units[i + 1][0] if i + 1 < len(units) else len(html_text)
        chunk = html_text[start:end]

        subreddit = str((ctx.get("subreddit") or {}).get("name") or "")
        title = str(ctx["post"].get("title") or "")
        href_match = _TITLE_HREF.search(chunk)
        href = _html.unescape((href_match.group(1) or href_match.group(2))) if href_match else ""
        href = href.split("?", 1)[0]
        if f"/comments/{post_id}/" in href and href.startswith("/r/"):
            url = f"{BASE}{href}"
        else:
            url = f"{BASE}/r/{subreddit}/comments/{post_id}/"

        ts_match = _TIMEAGO.search(chunk)
        ts = ts_match.group(1) if ts_match else None
        counts = {"vote": 0, "comment": 0}
        for number, label in _COUNTER.findall(chunk):
            counts[label] = int(number)
        score, num_comments = counts["vote"], counts["comment"]

        posts.append({
            "id": "",
            "title": title,
            "url": url,
            "score": score,
            "num_comments": num_comments,
            "subreddit": subreddit,
            "created_utc": reddit_listing._to_epoch(ts),
            "author": "",
            "selftext": "",
            "date": reddit_listing._to_date(ts),
            "engagement": {
                "score": score,
                "num_comments": num_comments,
                "upvote_ratio": None,
            },
            "relevance": round(token_overlap_relevance(query, title), 3) if query else 0.0,
            "why_relevant": "Reddit search",
            "metadata": {"post_id": post_id},
        })
    return posts, _next_cursor(html_text)


def _fetch_page(url: str) -> Tuple[Optional[str], Optional[str]]:
    """(body, error). Shared limiter, run memo, one 429 retry, failure sink."""
    try:
        return http.reddit_keyless_get_text_retry_429(
            url,
            timeout=SEARCH_TIMEOUT,
            accept="text/html",
            validate=unrecognized_body,
        )
    except Exception as e:  # defensive: one bad page must not sink the run
        _log(f"search fetch failed for {url}: {e}")
        return None, str(e)


def _search_stream(
    query: str,
    time_filter: str,
    max_pages: int,
    subreddit: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Page one search sequentially. Keeps earlier pages when a later one fails."""
    label = f"r/{subreddit}" if subreddit else "global"
    posts: List[Dict[str, Any]] = []
    seen_ids: set = set()
    used_cursors: set = set()
    cursor: Optional[str] = None
    for page in range(1, max_pages + 1):
        text, error = _fetch_page(search_url(query, time_filter, subreddit, cursor))
        if text is None:
            _log(f"{label} page {page} failed: {error or 'no response'}")
            break
        page_posts, next_cursor = parse_page(text, query)
        new = [p for p in page_posts if p["metadata"]["post_id"] not in seen_ids]
        for p in new:
            seen_ids.add(p["metadata"]["post_id"])
        posts.extend(new)
        if not new or not next_cursor or next_cursor in used_cursors:
            break
        used_cursors.add(next_cursor)
        cursor = next_cursor
    return posts


def _result_timeout(batch_size: int, pages: int = 1) -> float:
    """Per-future wait: each sequential page's own timeout plus the bucket's queue."""
    return pages * (SEARCH_TIMEOUT + 5) + http.reddit_keyless_wait_allowance(batch_size)


def _time_filter(from_date: Optional[str], to_date: Optional[str]) -> str:
    # Same mapping as the ScrapeCreators path; falls back to "month".
    from .reddit import _window_to_time_filter
    return _window_to_time_filter(from_date or "", to_date or "")


def search(
    query: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Discover Reddit posts for a query via the site search fragment.

    Runs a paged global search plus one page of per-subreddit search for each
    targeted subreddit. Returns normalized, dated, scored posts, deduped by URL
    and capped by depth, with targeted-subreddit posts first so the cap cannot
    crowd them out. Returns ``[]`` on total failure and never raises; failures
    land in the pipeline's failure sink.
    """
    try:
        limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
        global_pages = GLOBAL_PAGE_CAPS.get(depth, GLOBAL_PAGE_CAPS["default"])
        time_filter = _time_filter(from_date, to_date)
        subs = []
        for raw in subreddits or []:
            sub = (raw or "").strip().removeprefix("r/").strip("/")
            if sub and sub.lower() not in {s.lower() for s in subs}:
                subs.append(sub)

        jobs = [(sub, SUB_PAGE_CAP) for sub in subs] + [(None, global_pages)]
        batch = sum(pages for _sub, pages in jobs)
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as executor:
            # submit_with_context, not executor.submit: a plain submit starts the
            # worker with an empty context, dropping the pipeline's
            # capture_failures() sink so a 429/403 or a challenge page is
            # silently discarded (issue #899).
            futures = [
                (http.submit_with_context(executor, _search_stream, query, time_filter, pages, sub), sub, pages)
                for sub, pages in jobs
            ]
            for future, sub, pages in futures:
                try:
                    results.extend(future.result(timeout=_result_timeout(batch, pages)))
                except (Exception, FuturesTimeoutError) as e:
                    _log(f"{'r/' + sub if sub else 'global'} search future failed: {e}")

        seen: set = set()
        unique: List[Dict[str, Any]] = []
        for post in results:
            if post["url"] not in seen:
                seen.add(post["url"])
                unique.append(post)
        unique = unique[:limit]
        for i, post in enumerate(unique):
            post["id"] = f"R{i + 1}"
        _log(f"{len(unique)} posts (t={time_filter}, {len(subs)} targeted subs)")
        return unique
    except Exception as e:  # defensive: discovery must never raise into the pipeline
        _log(f"search failed: {e}")
        return []
