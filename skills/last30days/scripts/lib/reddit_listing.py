"""Keyless Reddit listing scrape via shreddit /svc partials — with real scores.

The subreddit listing partial
``/svc/shreddit/community-more-posts/{sort}/?name={sub}[&t={range}]`` serves
HTTP 200 with no API key and **server-renders each post's upvote score**, which
neither RSS nor the comments endpoint provides. Each post is a
``<shreddit-post>`` element whose start-tag attributes carry ``score``,
``comment-count``, ``post-title``, ``permalink``, ``author``, ``subreddit-name``
and ``created-timestamp``.

This is the keyless source of post-level upvotes. It works for normal users on
ordinary connections (verified), so reddit_keyless uses it both as a scored
discovery source and to backfill scores onto RSS-discovered posts.
"""

import html as _html
import re
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional, Set

from . import http
from .relevance import token_overlap_relevance, tokenize

# Generic domain terms that are excluded from the keyword gate — matches
# pipeline._DISCOVERY_GENERIC_DOMAIN_TERMS (duplicated to avoid circular import).
_DISCOVERY_GENERIC_DOMAIN_TERMS: Set[str] = {
    "ai", "artificial", "intelligence", "tech", "technology", "trending", "trend",
}

# Listing sorts pulled per subreddit, by depth.
LISTING_SORTS = {
    "quick": ["top"],
    "default": ["top", "hot"],
    "deep": ["top", "hot", "new"],
}
DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}
TIMEFRAME = "month"
MAX_WORKERS = 4
LISTING_TIMEOUT = 15

_POST_CARD = re.compile(r"<shreddit-post(?=[\s>])[^>]*>")


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditListing] {msg}\n")
    sys.stderr.flush()


def _matches_discovery_domain(domain: str, text: str) -> bool:
    """Require a distinctive domain term, not a generic token such as ``AI``.

    Duplicated from pipeline._matches_discovery_domain to avoid circular imports.
    The rule must stay in sync: pipeline.py owns the authoritative version and
    test_reddit_listing.py verifies parity.
    """
    def terms(value: str) -> Set[str]:
        words: Set[str] = set()
        for word in tokenize(value):
            words.add(word)
            if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
                words.add(word[:-1])
        return words

    domain_terms = terms(domain)
    anchors = domain_terms - _DISCOVERY_GENERIC_DOMAIN_TERMS
    return bool((anchors or domain_terms) & terms(text))


def _attr(tag: str, name: str) -> Optional[str]:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return _html.unescape(m.group(1)) if m else None


def _to_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date().isoformat()
    except (ValueError, TypeError):
        return None


def _to_epoch(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _post_id(permalink: str) -> str:
    m = re.search(r"/comments/([A-Za-z0-9]+)", permalink or "")
    return m.group(1) if m else ""


_ERROR_PATTERN = re.compile(r"^r/(\S+)\s+(\S+):", re.IGNORECASE)


def _shreddit_error_recovered(error: str, successes: Set[tuple[str, str]]) -> bool:
    """Return True if the error's (sub, sort) pair is in the successes set.

    Error format: "r/{sub} {sort}: {message}".
    """
    m = _ERROR_PATTERN.match(error)
    if not m:
        return False
    sub, sort = m.group(1).lower(), m.group(2).lower()
    return (sub, sort) in successes


def parse_cards(html_text: str, query: str = "") -> List[Dict[str, Any]]:
    """Parse <shreddit-post> cards into normalized post dicts with real scores."""
    posts: List[Dict[str, Any]] = []
    for m in _POST_CARD.finditer(html_text or ""):
        tag = m.group(0)
        permalink = _attr(tag, "permalink") or ""
        if "/comments/" not in permalink:
            continue
        try:
            score = int(_attr(tag, "score") or 0)
        except ValueError:
            score = 0
        try:
            num_comments = int(_attr(tag, "comment-count") or 0)
        except ValueError:
            num_comments = 0
        title = _attr(tag, "post-title") or ""
        author = _attr(tag, "author") or "[deleted]"
        subreddit = _attr(tag, "subreddit-name") or ""
        created = _attr(tag, "created-timestamp")
        url = f"https://www.reddit.com{permalink}"

        posts.append({
            "id": "",
            "title": title,
            "url": url,
            "score": score,
            "num_comments": num_comments,
            "subreddit": subreddit,
            "created_utc": _to_epoch(created),
            "author": author if author not in ("[deleted]", "[removed]") else "[deleted]",
            "selftext": "",
            "date": _to_date(created),
            "engagement": {
                "score": score,
                "num_comments": num_comments,
                "upvote_ratio": None,
            },
            "relevance": round(token_overlap_relevance(query, title), 3) if query else 0.0,
            "why_relevant": "Reddit listing",
            "metadata": {"post_id": _post_id(permalink)},
        })
    return posts


def _listing_url(subreddit: str, sort: str, timeframe: str = TIMEFRAME) -> str:
    sub = subreddit.removeprefix("r/").strip()
    if sub.lower() == "all":
        url = f"https://www.reddit.com/r/all/{sort}/"
        if sort == "top":
            url += f"?t={timeframe}"
        return url
    url = f"https://www.reddit.com/svc/shreddit/community-more-posts/{sort}/?name={sub}"
    if sort == "top":
        url += f"&t={timeframe}"
    return url


def _fetch_one(
    subreddit: str,
    sort: str,
    query: str,
    timeframe: str = TIMEFRAME,
) -> List[Dict[str, Any]]:
    items, _ = _fetch_one_with_status(subreddit, sort, query, timeframe)
    return items


def _fetch_one_with_status(
    subreddit: str,
    sort: str,
    query: str,
    timeframe: str = TIMEFRAME,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        # tee_failures, not capture_failures: the latter would replace the
        # pipeline's sink and hide this failure from it. get_text launders a
        # terminal HTTP failure into None, so the tee is how this lane recovers
        # the status code it needs to report (issue #899).
        with http.tee_failures() as swallowed:
            text = http.reddit_keyless_get_text(_listing_url(subreddit, sort, timeframe), timeout=LISTING_TIMEOUT,
                                                accept="text/html")
        if text is None:
            # An empty body ("") is a real empty listing; None never is.
            return [], (str(swallowed[-1]) if swallowed else "no response")
        return parse_cards(text, query), None
    except Exception as e:
        _log(f"listing fetch failed r/{subreddit} {sort}: {e}")
        return [], str(e)


def fetch_listings(
    subreddits: List[str],
    depth: str = "default",
    query: str = "",
    sorts: Optional[List[str]] = None,
    timeframe: str = TIMEFRAME,
) -> List[Dict[str, Any]]:
    """Fetch scored post cards across subreddits × sorts.

    Returns deduped normalized posts (with real scores), unranked/unsliced —
    the caller merges these with other sources, ranks, and slices.

    ``sorts`` overrides the depth-derived sort set. Dedicated-subreddit lanes
    pass ``["top", "hot", "new"]`` so fresh threads (which the top-of-month
    listing misses) are caught with their scores regardless of depth.
    """
    if not subreddits:
        return []
    sorts = sorts or LISTING_SORTS.get(depth, LISTING_SORTS["default"])
    jobs = [(sub, sort) for sub in subreddits for sort in sorts]
    all_posts: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs)) or 1) as executor:
        # submit_with_context, not executor.submit — see the note in
        # fetch_discovery_listings below (issue #899).
        futures = {http.submit_with_context(executor, _fetch_one, sub, sort, query, timeframe): (sub, sort)
                   for sub, sort in jobs}
        for future in futures:
            try:
                all_posts.extend(future.result(timeout=LISTING_TIMEOUT + 5))
            except (Exception, FuturesTimeoutError) as e:
                _log(f"listing future failed: {e}")

    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for p in all_posts:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)
    return unique


def fetch_discovery_listings(
    subreddits: List[str],
    *,
    query: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Fetch rising/top-week listings while preserving per-feed failures.

    When shreddit fails and arctic-shift recovers, errors are cleared only for
    subreddits whose posts survive the keyword gate. If query is empty (global
    ``--discover`` with no domain), the gate is skipped and any arctic result
    counts as recovery.
    """
    if not subreddits:
        return {"items": [], "errors": []}
    jobs = [(subreddit, sort) for subreddit in subreddits for sort in ("rising", "top")]
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    # Track which (sub, sort) pairs shreddit successfully delivered posts for.
    # Used to decide which errors to clear — Arctic can supplement but cannot
    # "recover" a failed hot/top/new/rising lane (it's recency-only).
    shreddit_successes: Set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs)) or 1) as executor:
        # submit_with_context, not executor.submit: a plain submit starts the
        # worker with an empty context, dropping the pipeline's
        # capture_failures() sink so a listing's 429/403 is silently discarded
        # and the source reports a clean no-results (issue #899).
        futures = {
            http.submit_with_context(
                executor, _fetch_one_with_status, subreddit, sort, query, "week"
            ): (subreddit, sort)
            for subreddit, sort in jobs
        }
        for future, (subreddit, sort) in futures.items():
            try:
                fetched, error = future.result(timeout=LISTING_TIMEOUT + 5)
            except (Exception, FuturesTimeoutError) as exc:
                errors.append(f"r/{subreddit} {sort}: {exc}")
                continue
            items.extend(fetched)
            if error:
                errors.append(f"r/{subreddit} {sort}: {error}")
            elif fetched:
                # Shreddit succeeded for this (sub, sort) lane.
                shreddit_successes.add((subreddit.lower(), sort.lower()))

    seen: set[str] = set()
    unique = []
    for item in items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)

    # Supplement with arctic-shift for all requested subreddits. Shreddit's
    # per-sort success/failure is opaque (individual rising/top lanes can fail
    # while others succeed), so arctic provides coverage for any failed lanes.
    # Deduplication ensures no redundant posts when shreddit fully succeeded.
    from . import reddit_arctic
    arctic_items = reddit_arctic.fetch_listings(
        subreddits, depth=depth, query=query, sorts=("rising", "top")
    )
    if arctic_items:
        _log(f"discovery arctic supplement: {len(arctic_items)} posts")
        # Apply the same keyword gate that pipeline._fetch_discovery_source
        # uses downstream. When query is empty (global --discover), skip the
        # gate — there's no keyword to match, and the river feed IS the signal.
        if query:
            arctic_items = [
                item for item in arctic_items
                if _matches_discovery_domain(
                    query,
                    f"{item.get('title') or ''} {item.get('selftext') or ''}",
                )
            ]
        # Merge arctic items into unique list, deduping by URL.
        added = 0
        for item in arctic_items:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
                added += 1
        if added:
            _log(f"discovery arctic supplement added {added} new posts")

    # Clear errors only for (sub, sort) pairs where shreddit succeeded.
    # Arctic supplements recency posts but cannot "recover" a failed hot/top/
    # rising lane — it has no sort lanes. Errors for failed shreddit lanes are
    # preserved even when another sort for the same subreddit succeeded.
    if errors and shreddit_successes:
        errors = [
            e for e in errors
            if not _shreddit_error_recovered(e, shreddit_successes)
        ]
    return {"items": unique, "errors": errors}


def score_index(subreddits: List[str], depth: str = "default") -> Dict[str, Dict[str, int]]:
    """Build a {post_id: {score, num_comments}} map from subreddit listings.

    Used to backfill real scores onto posts discovered via RSS, which carries
    no engagement numbers.
    """
    index: Dict[str, Dict[str, int]] = {}
    for p in fetch_listings(subreddits, depth=depth):
        pid = p.get("metadata", {}).get("post_id") or _post_id(p["url"])
        if pid:
            index[pid] = {"score": p["score"], "num_comments": p["num_comments"]}
    return index
