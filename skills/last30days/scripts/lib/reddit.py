"""Reddit search via ScrapeCreators API for the v3 pipeline.

Uses ScrapeCreators REST API to search Reddit globally, discover relevant
subreddits, run targeted subreddit searches, and fetch comment trees.

Requires SCRAPECREATORS_API_KEY in config (same key as TikTok + Instagram).
API docs: https://scrapecreators.com/docs
"""

import contextlib
import contextvars
import copy
import math
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait as futures_wait
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Set

def _first_of(*values, default=None):
    """Return first value that is not None."""
    for v in values:
        if v is not None:
            return v
    return default


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _extract_dict_list(
    data: Any,
    primary_key: str,
    *,
    context: str = "",
) -> List[Dict[str, Any]]:
    label = f"ScrapeCreators Reddit {context}" if context else "ScrapeCreators Reddit"
    if not isinstance(data, dict):
        http._record_failure(http.HTTPError(
            f"{label} returned non-object JSON ({type(data).__name__}) (schema drift)",
            outcome_state=health.SCHEMA_DRIFT,
        ))
        return []
    primary = data.get(primary_key)
    raw: Any = None
    drift_reason: Optional[str] = None
    if isinstance(primary, list):
        raw = primary
    else:
        fallback = data.get("data")
        if isinstance(fallback, list):
            raw = fallback
            if primary is not None:
                drift_reason = (
                    f"{label} returned non-list '{primary_key}' ({type(primary).__name__}) "
                    "(schema drift)"
                )
        else:
            if primary_key in data:
                drift_reason = (
                    f"{label} returned non-list '{primary_key}' ({type(primary).__name__}) "
                    "without usable 'data' list (schema drift)"
                )
            elif "data" in data:
                drift_reason = (
                    f"{label} returned non-list 'data' ({type(fallback).__name__}) (schema drift)"
                )
            else:
                drift_reason = (
                    f"{label} response missing '{primary_key}'/'data' list (schema drift)"
                )
    if raw is None:
        http._record_failure(http.HTTPError(
            drift_reason or f"{label} returned unexpected payload shape (schema drift)",
            outcome_state=health.SCHEMA_DRIFT,
        ))
        return []
    valid = [item for item in raw if isinstance(item, dict)]
    if drift_reason is not None:
        http._record_failure(http.HTTPError(
            drift_reason,
            outcome_state=health.SCHEMA_DRIFT,
        ))
    elif len(valid) < len(raw):
        dropped = len(raw) - len(valid)
        http._record_failure(http.HTTPError(
            f"{label} dropped {dropped} non-object entries in '{primary_key}' (schema drift)",
            outcome_state=health.SCHEMA_DRIFT,
        ))
    return valid


def _is_finite_int(value: int) -> bool:
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _coerce_int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if _is_finite_int(value) else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError:
            return None
        return parsed if _is_finite_int(parsed) else None
    return None


def _coerce_int(value: Any, default: int = 0) -> int:
    coerced = _coerce_int_or_none(value)
    return default if coerced is None else coerced


def _first_int(*values: Any, default: int = 0) -> int:
    for v in values:
        coerced = _coerce_int_or_none(v)
        if coerced is not None:
            return coerced
    return default


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            num = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return num if math.isfinite(num) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
        except (ValueError, OverflowError):
            return None
        return num if math.isfinite(num) else None
    return None


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return value if isinstance(value, str) else str(value)

from . import dates, health, http, log, snippet

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/reddit"


def _sc_base() -> str:
    if SCRAPECREATORS_BASE != "https://api.scrapecreators.com/v1/reddit":
        return SCRAPECREATORS_BASE
    from . import env
    return f"{env.get_scrapecreators_base_url()}/v1/reddit"

# Reddit's highest-upvote content (relationship drama, AITA, viral news) often
# has near-zero topic overlap. Engagement-only ranking floats it above on-topic
# posts, especially on bare global searches where no subreddits were resolved
# upstream. A relevance floor + relevance-first ranking (see _relevance_rank_key
# below and RELEVANCE_FLOOR / MIN_ON_TOPIC in relevance.py) keeps an off-topic
# viral post from ever outranking an on-topic one.

# Depth configurations: how many API calls per phase
DEPTH_CONFIG = {
    "quick": {
        "global_searches": 1,
        "subreddit_searches": 2,
        "comment_enrichments": 3,
    },
    "default": {
        "global_searches": 2,
        "subreddit_searches": 3,
        "comment_enrichments": 5,
    },
    "deep": {
        "global_searches": 3,
        "subreddit_searches": 5,
        "comment_enrichments": 8,
    },
}

from .query import extract_core_subject as _query_extract, infer_query_intent
from .relevance import token_overlap_relevance, RELEVANCE_FLOOR, MIN_ON_TOPIC


# Reddit-specific noise words (preserves original smaller set)
NOISE_WORDS = frozenset({
    'best', 'top', 'good', 'great', 'awesome', 'killer',
    'latest', 'new', 'news', 'update', 'updates',
    'trending', 'hottest', 'popular',
    'practices', 'features', 'tips',
    'recommendations', 'advice',
    'prompt', 'prompts', 'prompting',
    'methods', 'strategies', 'approaches',
    'how', 'to', 'the', 'a', 'an', 'for', 'with',
    'of', 'in', 'on', 'is', 'are', 'what', 'which',
    'guide', 'tutorial', 'using',
})


def _log(msg: str):
    log.source_log("Reddit", msg, tty_only=False)


# Run-scoped spend bound for ScrapeCreators Reddit calls. Pool workers inherit
# it via http.submit_with_context so an abandoned backfill thread stops before
# starting additional paid subreddit/global searches or comment fetches.
@dataclass(frozen=True)
class _SCSpendLimit:
    deadline: Optional[float] = None
    cancels: tuple[threading.Event, ...] = ()


_sc_spend_limit: contextvars.ContextVar[Optional[_SCSpendLimit]] = (
    contextvars.ContextVar("reddit_sc_spend_limit", default=None)
)

# Shared mutable [bool] flag owned by search_and_enrich_memo and inherited by
# pool workers via http.submit_with_context. Flipped to True right before any
# paid ScrapeCreators HTTP request so mid-spend timeouts are cached and never
# paid for twice, while pre-spend cancellations stay uncached.
_sc_paid_started: contextvars.ContextVar[Optional[list[bool]]] = (
    contextvars.ContextVar("reddit_sc_paid_started", default=None)
)

_SC_STOPPED_MSG = "ScrapeCreators Reddit backfill cancelled or timed out"
_SC_COMMENTS_INCOMPLETE_NOTE = "Reddit comments incomplete (deadline reached)"
_SC_SEARCH_INCOMPLETE_NOTE = "Reddit subreddit drill-down incomplete (deadline reached)"


def _mark_sc_paid_started() -> None:
    flag = _sc_paid_started.get()
    if flag is not None:
        flag[0] = True


@contextlib.contextmanager
def sc_spend_until(
    *,
    deadline: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> Iterator[None]:
    """Refuse ScrapeCreators Reddit HTTP calls once ``cancel`` or ``deadline`` fires.

    Nested contexts tighten the deadline and accumulate cancel events so both
    external run cancellation and backfill-local abandonment stop paid spend.
    """
    outer = _sc_spend_limit.get()
    merged_deadline = deadline
    cancels: list[threading.Event] = []
    if outer is not None:
        if outer.deadline is not None:
            merged_deadline = (
                outer.deadline
                if merged_deadline is None
                else min(outer.deadline, merged_deadline)
            )
        cancels.extend(outer.cancels)
    if cancel is not None and cancel not in cancels:
        cancels.append(cancel)
    token = _sc_spend_limit.set(_SCSpendLimit(deadline=merged_deadline, cancels=tuple(cancels)))
    try:
        yield
    finally:
        _sc_spend_limit.reset(token)


def _effective_spend_limit(
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> tuple[float | None, tuple[threading.Event, ...]]:
    limit = _sc_spend_limit.get()
    eff_deadline = deadline_monotonic
    cancels: list[threading.Event] = []
    if limit is not None:
        if limit.deadline is not None:
            eff_deadline = (
                limit.deadline
                if eff_deadline is None
                else min(eff_deadline, limit.deadline)
            )
        cancels.extend(limit.cancels)
    if cancel is not None and cancel not in cancels:
        cancels.append(cancel)
    return eff_deadline, tuple(cancels)


def _sc_cancelled(cancel: threading.Event | None = None) -> bool:
    _, cancels = _effective_spend_limit(None, cancel)
    return any(ev.is_set() for ev in cancels)


def _sc_spend_stopped(
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    eff_deadline, cancels = _effective_spend_limit(deadline_monotonic, cancel)
    if any(ev.is_set() for ev in cancels):
        return True
    return eff_deadline is not None and time.monotonic() >= eff_deadline


def _check_sc_spend(
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> tuple[float | None, threading.Event | None]:
    if _sc_spend_stopped(deadline_monotonic, cancel):
        raise TimeoutError(_SC_STOPPED_MSG)
    eff_deadline, cancels = _effective_spend_limit(deadline_monotonic, cancel)
    eff_cancel = cancel if cancel is not None else (cancels[0] if cancels else None)
    return eff_deadline, eff_cancel


def _search_batch_deadline(eff_deadline: float | None) -> float | None:
    """Reserve a short return margin before ``eff_deadline`` for batch collection."""
    if eff_deadline is None:
        return None
    rem = max(0.0, eff_deadline - time.monotonic())
    return eff_deadline - min(1.5, rem * 0.1)


def _collect_bounded(
    executor: ThreadPoolExecutor,
    futures: dict,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> tuple[list[tuple[Any, Any]], bool]:
    """Collect finished ``(tag, value)`` pairs and whether any sibling timed out.

    Fatal auth/payment HTTP errors still propagate immediately.
    """
    eff_deadline, cancels = _effective_spend_limit(deadline_monotonic, cancel)
    if eff_deadline is None and not cancels:
        results: list[tuple[Any, Any]] = []
        incomplete = False
        for future in as_completed(futures):
            try:
                results.append((futures[future], future.result()))
            except TimeoutError:
                incomplete = True
        return results, incomplete
    pending = set(futures)
    results = []
    incomplete = False
    try:
        while pending:
            # Always harvest already-finished futures before checking the
            # cutoff so sibling posts that arrived in time are never discarded.
            finished = {f for f in pending if f.done() and not f.cancelled()}
            if finished:
                pending -= finished
                for future in finished:
                    try:
                        results.append((futures[future], future.result(timeout=0)))
                    except TimeoutError:
                        incomplete = True
                continue
            if any(ev.is_set() for ev in cancels) or (
                eff_deadline is not None and time.monotonic() >= eff_deadline
            ):
                incomplete = True
                for future in pending:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break
            timeout = 0.1
            if eff_deadline is not None:
                timeout = max(0.0, min(0.1, eff_deadline - time.monotonic()))
            done, pending = futures_wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    results.append((futures[future], future.result(timeout=0)))
                except TimeoutError:
                    incomplete = True
        return results, incomplete
    except BaseException:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise


def classify_run_failure(detail: str) -> str:
    """Map Reddit auth and anti-bot responses that do not carry HTTP status."""
    text = detail.lower()
    if any(marker in text for marker in ("interstitial", "blocked by reddit", "too many requests")):
        return health.RATE_LIMITED
    if any(marker in text for marker in ("login required", "invalid token", "expired token")):
        return health.AUTH_FAILED
    return http.classify_failure(message=detail)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query.

    Strips meta/research words to keep only the core product/concept name.
    """
    return _query_extract(topic, noise=NOISE_WORDS)


def expand_reddit_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple Reddit search queries from a topic.

    Uses local logic (no LLM call needed):
    1. Extract core subject (strip noise words)
    2. Include original topic if different from core
    3. For default/deep: add casual/review variant
    4. For deep: add problem/issues variant

    Returns 1-4 query strings depending on depth.
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # Broader variant: include more context from original topic
    original_clean = topic.strip().rstrip('?!.')
    if core.lower() != original_clean.lower() and len(original_clean.split()) <= 8:
        queries.append(original_clean)

    qtype = infer_query_intent(topic)

    # Product queries: always include review-oriented variant to bias toward
    # review communities instead of keyword-matching unrelated subreddits.
    if qtype == "product":
        queries.append(f"{core} review OR recommendation OR best")

    # Comparison queries: include head-to-head discussion variant.
    if qtype == "comparison":
        queries.append(f"{core} worth it OR vs OR compared")

    # Opinion/review variants for default/deep depth.
    if depth in ("default", "deep") and qtype in ("product", "opinion"):
        queries.append(f"{core} worth it OR thoughts OR review")

    # Problem/bug variants are useful for tool workflows, not generic news.
    if depth == "deep" and qtype in ("product", "opinion", "how_to"):
        queries.append(f"{core} issues OR problems OR bug OR broken")

    return queries


# Known utility/meta subreddits that match queries but aren't discussion subs.
# These get a 0.3x penalty (not banned) in subreddit discovery scoring.
UTILITY_SUBS = frozenset({
    'namethatsong', 'findthatsong', 'tipofmytongue',
    'whatisthissong', 'helpmefind', 'whatisthisthing',
    'whatsthissong', 'findareddit', 'subredditdrama',
})


def discover_subreddits(
    results: List[Dict[str, Any]],
    topic: str = "",
    max_subs: int = 5,
) -> List[str]:
    """Extract top subreddits from global search results with relevance weighting.

    Uses frequency + topic-word matching + utility-sub penalties + engagement
    bonus to find discussion subs rather than utility/meta subs.

    Args:
        results: List of post dicts from global search
        topic: Original search topic (for relevance matching)
        max_subs: Maximum subreddits to return

    Returns:
        Top subreddit names sorted by weighted score
    """
    core = _extract_core_subject(topic) if topic else ""
    core_words = set(core.lower().split()) if core else set()

    scores = Counter()
    for post in results:
        if not isinstance(post, dict):
            continue
        sub = _extract_subreddit_name(post.get("subreddit", ""))
        if not sub:
            continue

        # Base: frequency count
        base = 1.0

        # Bonus: subreddit name contains a core topic word
        sub_lower = sub.lower()
        if core_words and any(w in sub_lower for w in core_words if len(w) > 2):
            base += 2.0

        # Penalty: known utility/meta subreddits
        if sub_lower in UTILITY_SUBS:
            base *= 0.3

        # Bonus: post engagement (high-engagement posts = better sub)
        ups = _first_int(post.get("ups"), post.get("score"), post.get("votes"), default=0)
        if ups > 100:
            base += 0.5

        scores[sub] += base

    return [sub for sub, _ in scores.most_common(max_subs)]


def _parse_date(value) -> Optional[str]:
    """Convert Unix timestamp or ISO-8601 string to YYYY-MM-DD.

    Global search returns ``created_at`` as an ISO string
    (e.g. "2018-05-03T01:09:17.620000+0000"); subreddit search returns
    ``created_utc`` as a Unix timestamp. dates.parse_date() handles both,
    plus edge cases like Z suffix and +0000 (no colon) offset.

    Falsy inputs (None, "", 0) return None, matching the original behavior
    where a Unix timestamp of 0 meant "no date" rather than epoch 0.
    """
    if not value or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        dt = dates.parse_date(str(value))
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    return dt.strftime("%Y-%m-%d") if dt else None


def _extract_subreddit_name(value: Any) -> str:
    """Extract subreddit name from string or API object dict."""
    if isinstance(value, dict):
        for key in ("name", "display_name"):
            text = _coerce_text(value.get(key)).strip()
            if text:
                return text
        return ""
    return _coerce_text(value).strip()


def _extract_score(post: Dict[str, Any]) -> int:
    """Extract post score from either API schema.

    Global search uses ``votes``; subreddit search uses ``ups``/``score``.
    """
    return _first_int(post.get("ups"), post.get("score"), post.get("votes"), default=0)


def _extract_date(post: Dict[str, Any]) -> Optional[str]:
    """Extract date from either API schema.

    Global search uses ``created_at`` (ISO); subreddit search uses ``created_utc`` (Unix).
    """
    for key in ("created_utc", "created_at", "created_at_iso"):
        parsed = _parse_date(post.get(key))
        if parsed is not None:
            return parsed
    return None


def _normalize_reddit_id(raw_id: str) -> str:
    """Strip Reddit fullname prefix (t3_) for consistent dedup."""
    s = _coerce_text(raw_id)
    return s[3:] if s.startswith("t3_") else s


def _total_engagement(item: Dict[str, Any]) -> int:
    """Combined engagement score: upvotes + comment count.

    Used for selecting which threads to enrich with comments.
    Threads with lots of comments are high-value even if upvote score is low.
    """
    eng = item.get("engagement")
    if not isinstance(eng, dict):
        return 0
    score = _coerce_int(eng.get("score"), default=0)
    num_comments = _coerce_int(eng.get("num_comments"), default=0)
    return score + num_comments


def _relevance_rank_key(item: Dict[str, Any]) -> float:
    """Rank by relevance first, with a bounded engagement bonus as tiebreaker.

    The log-scaled bonus is capped at 0.25 so it orders similarly-relevant posts
    by discussion volume but is too small to lift an off-topic post (relevance
    ~0) above an on-topic one (relevance >= RELEVANCE_FLOOR).
    """
    rel = item.get("relevance") or 0.0
    eng_bonus = min(0.25, math.log10(max(0, _total_engagement(item)) + 1) / 20.0)
    return rel + eng_bonus


def _normalize_post(post: Dict[str, Any], idx: int, source_label: str = "global", query: str = "") -> Dict[str, Any]:
    """Normalize a ScrapeCreators Reddit post to our internal format.

    Handles both the global-search schema (``votes``, ``created_at``,
    ``subreddit`` as dict) and the subreddit-search schema (``ups``/``score``,
    ``created_utc``, ``subreddit`` as string).
    """
    raw_permalink = post.get("permalink")
    permalink = raw_permalink if isinstance(raw_permalink, str) else ""
    raw_url = post.get("url")
    url = f"https://www.reddit.com{permalink}" if permalink else (raw_url if isinstance(raw_url, str) else "")

    # Ensure URL looks like a Reddit thread
    if url and "reddit.com" not in url:
        url = ""

    title = _coerce_text(post.get("title")).strip()
    selftext = _coerce_text(post.get("selftext"))

    # Score the title first, then let the body provide limited support.
    # This keeps long selftexts from overpowering the visible topic signal.
    relevance = _compute_post_relevance(query, title, selftext) if query else 0.7

    return {
        "id": f"R{idx}",
        "reddit_id": _normalize_reddit_id(post.get("id", "")),
        "title": title,
        "url": url,
        "subreddit": _extract_subreddit_name(post.get("subreddit", "")),
        "date": _extract_date(post),
        "engagement": {
            "score": _extract_score(post),
            "num_comments": _coerce_int(post.get("num_comments"), default=0),
            "upvote_ratio": _coerce_float(post.get("upvote_ratio")),
        },
        "relevance": relevance,
        "why_relevant": f"Reddit {source_label} search",
        "selftext": selftext[:500],
    }


def _compute_post_relevance(query: str, title: str, selftext: str) -> float:
    """Compute Reddit relevance with title-first weighting.

    Title should carry most of the weight because it is the visible summary the
    user sees. Selftext can lift a marginal match, but it should not rescue a
    weak or ambiguous title into the top ranks.
    """
    title_score = token_overlap_relevance(query, title)
    if not selftext.strip():
        return title_score

    body_score = token_overlap_relevance(query, selftext)
    support_score = max(title_score, body_score)
    return round(0.75 * title_score + 0.25 * support_score, 2)


def _global_search(
    query: str,
    token: str,
    sort: str = "relevance",
    timeframe: str = "month",
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Search across all of Reddit via ScrapeCreators global search.

    Args:
        query: Search query
        token: ScrapeCreators API key
        sort: Sort order (relevance, hot, top, new)
        timeframe: Time filter (hour, day, week, month, year, all)

    Returns:
        List of post dicts
    """
    eff_deadline, eff_cancel = _check_sc_spend(deadline_monotonic, cancel)
    extra: Dict[str, Any] = {}
    if eff_deadline is not None:
        extra["deadline_monotonic"] = eff_deadline
        extra["owned_get"] = True
    if eff_cancel is not None:
        extra["cancel"] = eff_cancel
    try:
        with http.observe_dispatch(_mark_sc_paid_started):
            data = http.get(
                f"{_sc_base()}/search",
                headers=http.scrapecreators_headers(token),
                params={"query": query, "sort": sort, "timeframe": timeframe},
                timeout=30,
                retries=2,
                **extra,
            )
        _mark_sc_paid_started()
        return _extract_dict_list(data, "posts", context="global search")
    except http.DeadlineExceeded as e:
        if getattr(e, "dispatched", False):
            _mark_sc_paid_started()
        raise TimeoutError(_SC_STOPPED_MSG) from None
    except http.HTTPError as e:
        _mark_sc_paid_started()
        if e.status_code in (401, 402, 403):
            raise
        _log(f"Global search error: {e}")
        return []
    except Exception as e:
        _mark_sc_paid_started()
        if _sc_spend_stopped(deadline_monotonic, cancel):
            raise TimeoutError(_SC_STOPPED_MSG) from None
        _log(f"Global search error: {e}")
        return []


def _subreddit_search(
    subreddit: str,
    query: str,
    token: str,
    sort: str = "relevance",
    timeframe: str = "month",
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Search within a specific subreddit via ScrapeCreators.

    Args:
        subreddit: Subreddit name (without r/)
        query: Search query
        token: ScrapeCreators API key
        sort: Sort order
        timeframe: Time filter

    Returns:
        List of post dicts
    """
    eff_deadline, eff_cancel = _check_sc_spend(deadline_monotonic, cancel)
    extra: Dict[str, Any] = {}
    if eff_deadline is not None:
        extra["deadline_monotonic"] = eff_deadline
        extra["owned_get"] = True
    if eff_cancel is not None:
        extra["cancel"] = eff_cancel
    try:
        with http.observe_dispatch(_mark_sc_paid_started):
            data = http.get(
                f"{_sc_base()}/subreddit/search",
                headers=http.scrapecreators_headers(token),
                params={
                    "subreddit": subreddit,
                    "query": query,
                    "sort": sort,
                    "timeframe": timeframe,
                },
                timeout=30,
                retries=2,
                **extra,
            )
        _mark_sc_paid_started()
        return _extract_dict_list(data, "posts", context=f"subreddit search r/{subreddit}")
    except http.DeadlineExceeded as e:
        if getattr(e, "dispatched", False):
            _mark_sc_paid_started()
        raise TimeoutError(_SC_STOPPED_MSG) from None
    except http.HTTPError as e:
        _mark_sc_paid_started()
        if e.status_code in (401, 402, 403):
            raise
        _log(f"Subreddit search error for r/{subreddit}: {e}")
        return []
    except Exception as e:
        _mark_sc_paid_started()
        if _sc_spend_stopped(deadline_monotonic, cancel):
            raise TimeoutError(_SC_STOPPED_MSG) from None
        _log(f"Subreddit search error for r/{subreddit}: {e}")
        return []


def fetch_post_comments(
    url: str,
    token: str,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Fetch comments for a Reddit post via ScrapeCreators.

    Args:
        url: Reddit post URL or permalink
        token: ScrapeCreators API key

    Returns:
        List of comment dicts with score, author, body, etc.
    """
    eff_deadline, eff_cancel = _check_sc_spend(deadline_monotonic, cancel)
    try:
        with http.observe_dispatch(_mark_sc_paid_started):
            data = http.get(
                f"{_sc_base()}/post/comments",
                headers=http.scrapecreators_headers(token),
                params={"url": url},
                timeout=30,
                retries=2,
                deadline_monotonic=eff_deadline,
                cancel=eff_cancel,
                owned_get=eff_deadline is not None,
            )
        _mark_sc_paid_started()
        return _extract_dict_list(data, "comments", context="comment fetch")
    except http.DeadlineExceeded as e:
        if getattr(e, "dispatched", False):
            _mark_sc_paid_started()
        raise TimeoutError(_SC_STOPPED_MSG) from None
    except http.HTTPError as e:
        _mark_sc_paid_started()
        if e.status_code in (401, 402, 403):
            raise
        _log(f"Comment fetch error: {e}")
        return []
    except Exception as e:
        _mark_sc_paid_started()
        if _sc_spend_stopped(deadline_monotonic, cancel):
            raise TimeoutError(_SC_STOPPED_MSG) from None
        _log(f"Comment fetch error: {e}")
        return []


def _dedupe_posts(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate posts by reddit_id, keeping first occurrence."""
    seen_ids = set()
    seen_urls = set()
    unique = []
    for post in posts:
        rid = post.get("reddit_id", "")
        url = post.get("url", "")
        if rid and rid in seen_ids:
            continue
        if url and url in seen_urls:
            continue
        if rid:
            seen_ids.add(rid)
        if url:
            seen_urls.add(url)
        unique.append(post)
    return unique


def _days_to_reddit_bucket(days: float) -> str:
    """Map a day count onto the smallest Reddit rolling bucket that covers it.

    Adds one day of slack so calendar windows that cross a day boundary still
    fit inside Reddit's rolling ``t=`` buckets (a yesterday→today request needs
    ``week``, not ``day``).
    """
    covered = days + 1
    if covered <= 1:
        return "day"
    if covered <= 7:
        return "week"
    if covered <= 31:
        return "month"
    if covered <= 366:
        return "year"
    return "all"


def _window_to_time_filter(from_date: str, to_date: str) -> str:
    """Map a requested YYYY-MM-DD window onto Reddit's coarse `t` param.

    Reddit's ``t=day|week|month`` buckets are rolling windows ending *now*, not
    calendar spans and not anchored to ``to_date``. Coverage therefore needs:

    1. Span — a yesterday→today request needs more than rolling ``t=day``.
    2. Historical reach — a one-day request ending two weeks ago still needs a
       bucket that reaches ``from_date``; span-alone would pick ``week`` and
       the API would omit the entire requested range.

    Take the wider of the two, independently of retrieval depth.
    Phase 5 still trims to ``from_date``/``to_date``. Falls back to ``month``
    if the dates don't parse.
    """
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except (ValueError, TypeError):
        return "month"
    span_days = max(0, (end - start).days)
    # Age of from_date relative to "today" — Reddit always anchors to now.
    age_days = max(0, (datetime.now(timezone.utc).date() - start).days)
    return _days_to_reddit_bucket(max(span_days, age_days))


def search_reddit(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    subreddits: List[str] | None = None,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """Full Reddit search: multi-query global discovery + subreddit drill-down.

    This is the main v3 Reddit entry point.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        subreddits: Optional list of subreddit names to search first (pre-resolved)

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    if not token:
        return {"items": [], "error": "No SCRAPECREATORS_API_KEY configured"}

    spend_ctx = (
        sc_spend_until(deadline=deadline_monotonic, cancel=cancel)
        if (deadline_monotonic is not None or cancel is not None)
        else contextlib.nullcontext()
    )
    with spend_ctx:
        _check_sc_spend()
        config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
        timeframe = _window_to_time_filter(from_date, to_date)
        intent = infer_query_intent(topic)

        # === Phase 1: Query Expansion ===
        queries = expand_reddit_queries(topic, depth)
        _log(f"Expanded '{topic}' into {len(queries)} queries: {queries}")

        core = _extract_core_subject(topic)

        outer_deadline, _ = _effective_spend_limit()

        # === Phase 1.5: Pre-resolved subreddit search (high-signal) ===
        all_raw_posts: List[Dict[str, Any]] = []
        all_items: List[Dict[str, Any]] = []
        search_incomplete = False
        collected_any = False
        if subreddits:
            _check_sc_spend()
            _log(f"Searching pre-resolved subreddits: {subreddits}")
            batch_deadline = _search_batch_deadline(outer_deadline)
            batch_ctx = (
                sc_spend_until(deadline=batch_deadline)
                if batch_deadline is not None
                else contextlib.nullcontext()
            )
            with batch_ctx:
                executor = ThreadPoolExecutor(max_workers=min(5, len(subreddits)))
                completed_cleanly = False
                try:
                    futures = {}
                    for sub in subreddits:
                        if _sc_spend_stopped():
                            search_incomplete = True
                            break
                        futures[http.submit_with_context(
                            executor, _subreddit_search, sub, core, token, "relevance", timeframe,
                        )] = sub
                    batch_results, batch_incomplete = _collect_bounded(executor, futures)
                    for sub, sub_posts in batch_results:
                        sub_posts = _as_dict_list(sub_posts)
                        _log(f"  -> {len(sub_posts)} results from pre-resolved r/{sub}")
                        for j, post in enumerate(sub_posts):
                            item = _normalize_post(post, len(all_items) + j + 1, f"r/{sub}", query=core)
                            all_items.append(item)
                            collected_any = True
                    if batch_incomplete:
                        search_incomplete = True
                    completed_cleanly = not search_incomplete
                finally:
                    executor.shutdown(wait=completed_cleanly, cancel_futures=not completed_cleanly)

        if _sc_cancelled():
            raise TimeoutError(_SC_STOPPED_MSG)

        # === Phase 2: Global Discovery ===
        if not search_incomplete and _sc_spend_stopped():
            search_incomplete = True
        if not search_incomplete:
            max_global = config["global_searches"]
            batch_deadline = _search_batch_deadline(outer_deadline)
            batch_ctx = (
                sc_spend_until(deadline=batch_deadline)
                if batch_deadline is not None
                else contextlib.nullcontext()
            )
            with batch_ctx:
                executor = ThreadPoolExecutor(max_workers=max_global or 1)
                completed_cleanly = False
                try:
                    futures = {}
                    for i, query in enumerate(queries[:max_global]):
                        if _sc_spend_stopped():
                            search_incomplete = True
                            break
                        # Product/comparison queries: sort=top surfaces high-engagement posts
                        # from relevant communities instead of keyword-matched noise.
                        sort = "top" if intent in ("product", "comparison") else ("relevance" if i == 0 else "top")
                        _log(f"Global search {i+1}/{max_global}: '{query}' (sort={sort})")
                        futures[http.submit_with_context(
                            executor, _global_search, query, token, sort, timeframe,
                        )] = query
                    batch_results, batch_incomplete = _collect_bounded(executor, futures)
                    for query, posts in batch_results:
                        posts = _as_dict_list(posts)
                        _log(f"  -> {len(posts)} results for '{query}'")
                        all_raw_posts.extend(posts)
                        if posts:
                            collected_any = True
                    if batch_incomplete:
                        search_incomplete = True
                    completed_cleanly = not search_incomplete
                finally:
                    executor.shutdown(wait=completed_cleanly, cancel_futures=not completed_cleanly)

            # Normalize all posts (with query for relevance scoring)
            for i, post in enumerate(all_raw_posts):
                item = _normalize_post(post, i + 1, "global", query=core)
                all_items.append(item)

        if _sc_cancelled():
            raise TimeoutError(_SC_STOPPED_MSG)

        # === Phase 3: Subreddit Discovery + Targeted Search ===
        subreddit_budget = 0 if intent == "how_to" else config["subreddit_searches"]
        discovered_subs = discover_subreddits(all_raw_posts, topic=topic, max_subs=subreddit_budget)
        _log(f"Discovered subreddits: {discovered_subs}")

        subreddit_limit = subreddit_budget
        if subreddit_limit > 0 and discovered_subs:
            if search_incomplete or _sc_spend_stopped():
                search_incomplete = True
                _log("Subreddit drill-down skipped at deadline; keeping already-fetched posts")
            else:
                batch_deadline = _search_batch_deadline(outer_deadline)
                batch_ctx = (
                    sc_spend_until(deadline=batch_deadline)
                    if batch_deadline is not None
                    else contextlib.nullcontext()
                )
                with batch_ctx:
                    executor = ThreadPoolExecutor(max_workers=subreddit_limit)
                    completed_cleanly = False
                    try:
                        futures = {}
                        for sub in discovered_subs[:subreddit_limit]:
                            if _sc_spend_stopped():
                                search_incomplete = True
                                break
                            _log(f"Subreddit search: r/{sub} for '{core}'")
                            futures[http.submit_with_context(
                                executor, _subreddit_search, sub, core, token, "relevance", timeframe,
                            )] = sub
                        batch_results, batch_incomplete = _collect_bounded(executor, futures)
                        for sub, sub_posts in batch_results:
                            sub_posts = _as_dict_list(sub_posts)
                            _log(f"  -> {len(sub_posts)} results from r/{sub}")
                            for j, post in enumerate(sub_posts):
                                item = _normalize_post(post, len(all_items) + j + 1, f"r/{sub}", query=core)
                                all_items.append(item)
                                collected_any = True
                        if batch_incomplete:
                            search_incomplete = True
                        completed_cleanly = not search_incomplete
                    finally:
                        executor.shutdown(wait=completed_cleanly, cancel_futures=not completed_cleanly)

        if _sc_cancelled():
            raise TimeoutError(_SC_STOPPED_MSG)
        if search_incomplete and not collected_any:
            raise TimeoutError(_SC_STOPPED_MSG)

        # === Phase 4: Deduplicate ===
        all_items = _dedupe_posts(all_items)
        _log(f"After dedup: {len(all_items)} unique posts")

        # === Phase 5: Date filter ===
        in_range = []
        out_of_range = 0
        for item in all_items:
            if item["date"] and from_date <= item["date"] <= to_date:
                in_range.append(item)
            elif item["date"] is None:
                in_range.append(item)  # Keep unknown dates
            else:
                out_of_range += 1

        all_items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} posts outside date range")

        # === Phase 6: Relevance floor + relevance-weighted ranking ===
        # Drop the off-topic tail when enough on-topic posts remain. When too few clear
        # the soft floor, still strip zero-overlap posts (relevance exactly 0 = no
        # title/body token match at all, never on-topic) whenever anything relevant
        # remains, so viral high-upvote junk can't fill the section. Then rank by
        # relevance with a bounded engagement bonus (see RELEVANCE_FLOOR note above).
        before = len(all_items)
        on_topic = [it for it in all_items if (it.get("relevance") or 0) >= RELEVANCE_FLOOR]
        if len(on_topic) >= MIN_ON_TOPIC:
            all_items = on_topic
        else:
            nonzero = [it for it in all_items if (it.get("relevance") or 0) > 0]
            if nonzero:
                all_items = nonzero
        if len(all_items) < before:
            _log(f"Relevance floor dropped {before - len(all_items)} off-topic posts")
        all_items.sort(key=_relevance_rank_key, reverse=True)

        # Re-index IDs
        for i, item in enumerate(all_items):
            item["id"] = f"R{i+1}"

        _log(f"Final: {len(all_items)} Reddit posts")
        out: Dict[str, Any] = {"items": all_items}
        if search_incomplete:
            out["incomplete"] = True
            out["incomplete_note"] = _SC_SEARCH_INCOMPLETE_NOTE
        return out


def enrich_with_comments(
    items: List[Dict[str, Any]],
    token: str,
    depth: str = "default",
    budget_seconds: int = 60,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
    _incomplete_out: Optional[List[bool]] = None,
) -> List[Dict[str, Any]]:
    """Enrich top items with comment data from ScrapeCreators.

    Args:
        items: Reddit items from search_reddit()
        token: ScrapeCreators API key
        depth: Depth for comment limit
        budget_seconds: Maximum total time for enrichment. If exceeded,
            returns items with whatever enrichment completed. Never discards items.

    Returns:
        Items with top_comments and comment_insights added.
    """
    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_comments = config["comment_enrichments"]

    if not items or not token or max_comments <= 0 or budget_seconds <= 0:
        return items

    spend_ctx = (
        sc_spend_until(deadline=deadline_monotonic, cancel=cancel)
        if (deadline_monotonic is not None or cancel is not None)
        else contextlib.nullcontext()
    )
    with spend_ctx:
        outer_deadline, outer_cancels = _effective_spend_limit()
        if _sc_spend_stopped():
            if _incomplete_out is not None:
                _incomplete_out.append(True)
            _log("Comment enrichment skipped: deadline reached or cancelled")
            return items

        # Select the top threads by total engagement (upvotes + comment count),
        # not by list position. This ensures high-comment threads like [FRESH ALBUM]
        # always get enriched even if their upvote score is low.
        ranked = sorted(items, key=_total_engagement, reverse=True)
        top_items = ranked[:max_comments]
        _log(f"Enriching comments for {len(top_items)} posts (by total engagement)")

        start = time.monotonic()
        deadline = start + budget_seconds
        if outer_deadline is not None:
            deadline = min(deadline, outer_deadline)
        local_cancel = threading.Event()

        def fetch(item):
            if local_cancel.is_set() or _sc_spend_stopped(deadline, local_cancel):
                return None
            comments = fetch_post_comments(
                item.get("url", ""), token,
                deadline_monotonic=deadline, cancel=local_cancel,
            )
            return comments if not local_cancel.is_set() and time.monotonic() < deadline else None

        executor = ThreadPoolExecutor(max_workers=min(4, len(top_items)))
        abandoned = False
        not_done: set = set()
        try:
            with ExitStack() as cleanup:
                cleanup.callback(local_cancel.set)
                futures = {}
                for item in top_items:
                    if not item.get("url"):
                        continue
                    if local_cancel.is_set() or _sc_spend_stopped(deadline, local_cancel):
                        abandoned = True
                        break
                    futures[http.submit_with_context(executor, fetch, item)] = item

                done = set()
                not_done = set(futures)
                if not abandoned:
                    if outer_deadline is None and not outer_cancels:
                        remaining = max(0, budget_seconds - (time.monotonic() - start))
                        done, not_done = futures_wait(futures, timeout=remaining)
                    else:
                        while not_done:
                            if _sc_spend_stopped():
                                abandoned = True
                                break
                            rem = deadline - time.monotonic()
                            if rem <= 0:
                                if outer_deadline is not None and time.monotonic() >= outer_deadline:
                                    abandoned = True
                                break
                            completed, not_done = futures_wait(
                                not_done, timeout=min(0.1, rem), return_when=FIRST_COMPLETED,
                            )
                            done.update(completed)

                if not_done or abandoned:
                    local_cancel.set()
                    for future in not_done:
                        future.cancel()
                    if _incomplete_out is not None:
                        _incomplete_out.append(True)

                enriched_count = 0
                for future in done:
                    item = futures[future]
                    try:
                        raw_comments = future.result(timeout=0)
                    except Exception:
                        continue
                    raw_comments = _as_dict_list(raw_comments)
                    if not raw_comments:
                        continue

                    top_comments = []
                    insights = []

                    for ci, c in enumerate(raw_comments[:10]):
                        body = _coerce_text(c.get("body"))
                        if not body or body in ("[deleted]", "[removed]"):
                            continue

                        score = _first_int(c.get("ups"), c.get("score"), default=0)
                        author = _coerce_text(c.get("author")) or "[deleted]"
                        raw_permalink = c.get("permalink")
                        permalink = raw_permalink if isinstance(raw_permalink, str) else ""
                        comment_url = f"https://reddit.com{permalink}" if permalink else ""

                        top_comments.append({
                            "score": score,
                            "date": _parse_date(c.get("created_utc")),
                            "author": author,
                            "excerpt": snippet.cut_quote(body),
                            "url": comment_url,
                            # The thread's own poster, when the payload says so.
                            **(
                                {"is_uploader": c["is_submitter"]}
                                if isinstance(c.get("is_submitter"), bool)
                                else {}
                            ),
                        })

                        if len(body) >= 30 and author not in ("[deleted]", "[removed]", "AutoModerator"):
                            insight = body[:150]
                            if len(body) > 150:
                                for i, char in enumerate(insight):
                                    if char in '.!?' and i > 50:
                                        insight = insight[:i+1]
                                        break
                                else:
                                    insight = insight.rstrip() + "..."
                            insights.append(insight)

                    top_comments.sort(key=lambda c: _coerce_int(c.get("score"), default=0), reverse=True)
                    item["top_comments"] = top_comments[:10]
                    item["comment_insights"] = insights[:10]
                    enriched_count += 1

                if not_done or abandoned:
                    _log(f"Enrichment budget hit ({budget_seconds}s): {enriched_count}/{len(futures)} posts enriched, {len(not_done)} skipped")
                else:
                    elapsed = time.monotonic() - start
                    _log(f"Enriched {enriched_count}/{len(futures)} posts in {elapsed:.1f}s")
        finally:
            executor.shutdown(wait=not abandoned and not bool(not_done), cancel_futures=abandoned or bool(not_done))

        return items


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    subreddits: List[str] | None = None,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """Full Reddit pipeline: search + comment enrichment.

    This is the convenience function that does everything.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        subreddits: Optional list of subreddit names to search first (pre-resolved)

    Returns:
        Dict with 'items' list. Items include top_comments and comment_insights.
    """
    spend_ctx = (
        sc_spend_until(deadline=deadline_monotonic, cancel=cancel)
        if (deadline_monotonic is not None or cancel is not None)
        else contextlib.nullcontext()
    )
    with spend_ctx:
        _check_sc_spend()
        result = search_reddit(topic, from_date, to_date, depth, token, subreddits=subreddits)
        items = result.get("items", [])

        if items and token:
            eff_deadline, cancels = _effective_spend_limit()
            comment_deadline: float | None = None
            skip_comments = bool(result.get("incomplete")) or _sc_spend_stopped()
            if not skip_comments and eff_deadline is not None:
                # Reserve a return margin so already-paid posts make it back
                # before the outer backfill wait gives up on the worker.
                rem = eff_deadline - time.monotonic()
                reserve = min(2.0, max(0.0, rem * 0.2))
                comment_deadline = eff_deadline - reserve
                if comment_deadline <= time.monotonic():
                    skip_comments = True
            if skip_comments:
                result["comments_incomplete"] = True
                result["comments_note"] = _SC_COMMENTS_INCOMPLETE_NOTE
            else:
                incomplete_flag: list[bool] = []
                try:
                    if eff_deadline is not None or cancels:
                        items = enrich_with_comments(
                            items,
                            token,
                            depth,
                            deadline_monotonic=comment_deadline,
                            _incomplete_out=incomplete_flag,
                        )
                    else:
                        items = enrich_with_comments(items, token, depth)
                    result["items"] = items
                    if incomplete_flag:
                        result["comments_incomplete"] = True
                        result["comments_note"] = _SC_COMMENTS_INCOMPLETE_NOTE
                except TimeoutError:
                    result["items"] = items
                    result["comments_incomplete"] = True
                    result["comments_note"] = _SC_COMMENTS_INCOMPLETE_NOTE

        return result


# Run-scoped memo for the paid ScrapeCreators Reddit call (R9). All subquery
# streams share the raw topic, and the thin-source retry repeats it, so without
# this one run could pay for the same query five times. Concurrent callers for
# one key wait on the first call; results AND failures are kept until the
# per-command reset, so a failed backfill is never retried within the run.
_SC_MEMO: Dict[tuple, tuple] = {}
_SC_INFLIGHT: Dict[tuple, threading.Event] = {}
_SC_MEMO_LOCK = threading.Lock()


def reset_scrapecreators_memo() -> None:
    """Forget memoized ScrapeCreators Reddit calls. Called once per command, and by tests."""
    with _SC_MEMO_LOCK:
        _SC_MEMO.clear()
        _SC_INFLIGHT.clear()


def _sc_memo_outcome(entry: tuple, replay_failures: bool = True) -> Dict[str, Any]:
    ok, value, failures = entry
    if replay_failures:
        # search_and_enrich swallows ScrapeCreators HTTP errors into the
        # caller's failure sink; a later caller must see them too, or an
        # empty cached result reads as a clean no-results.
        for failure in failures:
            http._record_failure(failure)
    if not ok:
        raise value
    # Each caller gets its own copy so one stream cannot mutate another's items.
    return copy.deepcopy(value)


def search_and_enrich_memo(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    subreddits: List[str] | None = None,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """:func:`search_and_enrich`, at most once per key per command.

    Keyed by query, date window, depth, and sorted subreddits.
    """
    spend_ctx = (
        sc_spend_until(deadline=deadline_monotonic, cancel=cancel)
        if (deadline_monotonic is not None or cancel is not None)
        else contextlib.nullcontext()
    )
    with spend_ctx:
        eff_deadline, cancels = _effective_spend_limit()
        key = (topic, from_date, to_date, depth, tuple(sorted(subreddits or ())))
        while True:
            with _SC_MEMO_LOCK:
                entry = _SC_MEMO.get(key)
                if entry is None:
                    if _sc_spend_stopped():
                        raise TimeoutError(_SC_STOPPED_MSG)
                    gate = _SC_INFLIGHT.get(key)
                    owner = gate is None
                    if owner:
                        gate = threading.Event()
                        _SC_INFLIGHT[key] = gate
            if entry is not None:
                return _sc_memo_outcome(entry)
            if owner:
                break
            if eff_deadline is None and not cancels:
                gate.wait()
            else:
                while not gate.wait(timeout=0.1):
                    _check_sc_spend()
            # Loop: read the owner's cached outcome, or re-elect if it was
            # interrupted before caching one (e.g. KeyboardInterrupt or cancel).
        paid_flag = [False]
        paid_token = _sc_paid_started.set(paid_flag)
        cancelled_before_spend = False
        try:
            with http.tee_failures() as recorded:
                try:
                    result = search_and_enrich(
                        topic, from_date, to_date, depth=depth, token=token,
                        subreddits=subreddits,
                    )
                    outcome = (True, result)
                except TimeoutError as exc:
                    if not paid_flag[0]:
                        cancelled_before_spend = True
                    outcome = (False, exc)
                except Exception as exc:
                    if _sc_spend_stopped() and not paid_flag[0]:
                        cancelled_before_spend = True
                    outcome = (False, exc)
            entry = (*outcome, list(recorded))
            if not cancelled_before_spend:
                with _SC_MEMO_LOCK:
                    _SC_MEMO[key] = entry
        finally:
            _sc_paid_started.reset(paid_token)
            with _SC_MEMO_LOCK:
                _SC_INFLIGHT.pop(key, None)
            gate.set()
        # The owner's failures already reached its own sink through tee_failures.
        return _sc_memo_outcome(entry, replay_failures=False)


def parse_reddit_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse ScrapeCreators response to item list.

    Parse raw Reddit search output into the generic item shape.
    """
    if not isinstance(response, dict):
        return []
    return _as_dict_list(response.get("items"))
