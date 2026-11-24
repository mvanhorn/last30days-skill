"""Reddit thread enrichment with real engagement metrics.

Supports two backends:
1. ScrapeCreators API (preferred) - no rate limits, 1 credit/call
2. reddit.com/.json (fallback) - free but 429-prone
"""

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

from . import http, dates, reddit_refs, snippet

_SAFE_SLUG_SEG = re.compile(r"^[A-Za-z0-9_.~%-]{1,200}\Z")


def _coerce_int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _coerce_int(value: Any, default: Optional[int] = 0) -> Optional[int]:
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
        num = float(value)
        return num if math.isfinite(num) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            return None
        return num if math.isfinite(num) else None
    return None


def _coerce_timestamp(value: Any) -> Optional[float | int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        ts: float | int = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            ts = float(text)
        except ValueError:
            return None
        if not math.isfinite(ts):
            return None
    else:
        return None
    try:
        datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    return ts


def _safe_timestamp_to_date(value: Any) -> Optional[str]:
    ts = _coerce_timestamp(value)
    if ts is None:
        return None
    return dates.timestamp_to_date(ts)


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return value if isinstance(value, str) else str(value)


def _coerce_author(value: Any, default: str = "[deleted]") -> str:
    text = _coerce_text(value)
    return text or default


def extract_reddit_path(url: str) -> Optional[str]:
    """Extract a validated thread path from a Reddit URL.

    Args:
        url: Reddit thread URL

    Returns:
        Path component or None
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or "://" not in text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except (ValueError, AttributeError):
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != default_port:
        return None
    host = (parsed.hostname or "").lower()
    if host not in reddit_refs._HOSTS:
        return None
    ref = reddit_refs.canonical_thread_url(text)
    if ref is None:
        return None
    raw_path = parsed.path or ""
    if not raw_path.startswith("/") or raw_path.startswith("//") or "\\" in raw_path:
        return None
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in raw_path):
        return None
    segments = [seg for seg in raw_path.split("/") if seg]
    if not segments:
        return None
    for seg in segments:
        if not _SAFE_SLUG_SEG.match(seg):
            return None
        if seg in (".", ".."):
            return None
        decoded = unquote(seg)
        decoded_twice = unquote(decoded)
        for candidate in (decoded, decoded_twice):
            if (
                candidate in (".", "..")
                or any(ch in candidate for ch in ("/", "\\", "?", "#"))
                or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in candidate)
            ):
                return None
    # Tail segments after /r/<sub>/comments/<id> or /comments/<id> are at most slug + comment_id.
    tail_start = 4 if ref.subreddit else 2
    if len(segments) < tail_start or len(segments) > tail_start + 2:
        return None
    return raw_path


class RedditRateLimitError(Exception):
    """Raised when Reddit returns HTTP 429 (rate limited)."""
    pass


def fetch_thread_data(
    url: str,
    mock_data: Optional[Dict] = None,
    timeout: int = 30,
    retries: int = 3,
) -> Optional[Dict[str, Any]]:
    """Fetch Reddit thread JSON data.

    Args:
        url: Reddit thread URL
        mock_data: Mock data for testing
        timeout: HTTP timeout per attempt in seconds
        retries: Number of retries on failure

    Returns:
        Thread data dict or None on failure

    Raises:
        RedditRateLimitError: When Reddit returns 429 (caller should bail)
    """
    if mock_data is not None:
        return mock_data

    path = extract_reddit_path(url)
    if not path:
        return None

    try:
        data = http.get_reddit_json(path, timeout=timeout, retries=retries)
        return data
    except http.HTTPError as e:
        if e.status_code == 429:
            raise RedditRateLimitError(f"Reddit rate limited (429) fetching {url}") from e
        return None


def parse_thread_data(data: Any) -> Dict[str, Any]:
    """Parse Reddit thread JSON into structured data.

    Args:
        data: Raw Reddit JSON response

    Returns:
        Dict with submission and comments data
    """
    result = {
        "submission": None,
        "comments": [],
    }

    if not isinstance(data, list) or len(data) < 1:
        return result

    # First element is submission listing
    submission_listing = data[0]
    if isinstance(submission_listing, dict):
        listing_data = submission_listing.get("data")
        children = listing_data.get("children") if isinstance(listing_data, dict) else None
        if isinstance(children, list) and children:
            first_child = children[0]
            sub_data = first_child.get("data") if isinstance(first_child, dict) else None
            if isinstance(sub_data, dict):
                raw_permalink = sub_data.get("permalink")
                raw_title = sub_data.get("title")
                title = (
                    raw_title
                    if isinstance(raw_title, str)
                    else (None if raw_title is None or isinstance(raw_title, bool) else str(raw_title))
                )
                result["submission"] = {
                    "score": _coerce_int(sub_data.get("score"), default=None),
                    "num_comments": _coerce_int(sub_data.get("num_comments"), default=None),
                    "upvote_ratio": _coerce_float(sub_data.get("upvote_ratio")),
                    "created_utc": _coerce_timestamp(sub_data.get("created_utc")),
                    "permalink": raw_permalink if isinstance(raw_permalink, str) else None,
                    "title": title,
                    "selftext": _coerce_text(sub_data.get("selftext"))[:500],  # Truncate
                }

    # Second element is comments listing
    if len(data) >= 2:
        comments_listing = data[1]
        if isinstance(comments_listing, dict):
            comments_data = comments_listing.get("data")
            children = comments_data.get("children") if isinstance(comments_data, dict) else None
            if isinstance(children, list):
                for child in children:
                    if not isinstance(child, dict) or child.get("kind") != "t1":  # t1 = comment
                        continue
                    c_data = child.get("data")
                    if not isinstance(c_data, dict):
                        continue
                    body = _coerce_text(c_data.get("body"))
                    if not body:
                        continue

                    raw_permalink = c_data.get("permalink")
                    comment = {
                        "score": _coerce_int(c_data.get("score"), default=0),
                        "created_utc": _coerce_timestamp(c_data.get("created_utc")),
                        "author": _coerce_author(c_data.get("author")),
                        "body": body[:600],  # Truncate; the stored excerpt is cut cleanly from this
                        "permalink": raw_permalink if isinstance(raw_permalink, str) else None,
                    }
                    # Reddit says whether the thread's own poster wrote this.
                    if isinstance(c_data.get("is_submitter"), bool):
                        comment["is_submitter"] = c_data["is_submitter"]
                    result["comments"].append(comment)

    return result


def stored_top_comments(comments: List[Dict]) -> List[Dict[str, Any]]:
    """Parsed comments in the shape an item stores them. ``is_uploader`` is
    Reddit's ``is_submitter`` (the thread's own poster wrote the comment), and
    is left out when the thread data did not say."""
    if not isinstance(comments, list):
        return []
    stored = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        raw_permalink = c.get("permalink")
        permalink = raw_permalink if isinstance(raw_permalink, str) else ""
        entry = {
            "score": _coerce_int(c.get("score"), default=0),
            "date": _safe_timestamp_to_date(c.get("created_utc")),
            "author": _coerce_text(c.get("author")),
            "excerpt": snippet.cut_quote(_coerce_text(c.get("body"))),
            "url": f"https://reddit.com{permalink}" if permalink else "",
        }
        if isinstance(c.get("is_submitter"), bool):
            entry["is_uploader"] = c["is_submitter"]
        stored.append(entry)
    return stored


def get_top_comments(comments: List[Dict], limit: int = 10) -> List[Dict[str, Any]]:
    """Get top comments sorted by score.

    Args:
        comments: List of comment dicts
        limit: Maximum number to return

    Returns:
        Top comments sorted by score
    """
    if not isinstance(comments, list):
        return []

    # Filter out non-dicts and deleted/removed
    valid = [
        c for c in comments
        if isinstance(c, dict) and c.get("author") not in ("[deleted]", "[removed]")
    ]

    # Sort by score descending
    sorted_comments = sorted(
        valid,
        key=lambda c: _coerce_int(c.get("score"), default=0),
        reverse=True,
    )

    return sorted_comments[:limit]


def extract_comment_insights(comments: List[Dict], limit: int = 7) -> List[str]:
    """Extract key insights from top comments.

    Uses simple heuristics to identify valuable comments:
    - Has substantive text
    - Contains actionable information
    - Not just agreement/disagreement

    Args:
        comments: Top comments
        limit: Max insights to extract

    Returns:
        List of insight strings
    """
    if not isinstance(comments, list):
        return []
    insights = []

    for comment in comments[:limit * 2]:  # Look at more comments than we need
        if not isinstance(comment, dict):
            continue
        body = _coerce_text(comment.get("body")).strip()
        if not body or len(body) < 30:
            continue

        # Skip low-value patterns
        skip_patterns = [
            r'^(this|same|agreed|exactly|yep|nope|yes|no|thanks|thank you)\.?$',
            r'^lol|lmao|haha',
            r'^\[deleted\]',
            r'^\[removed\]',
        ]
        if any(re.match(p, body.lower()) for p in skip_patterns):
            continue

        # Truncate to first meaningful sentence or ~150 chars
        insight = body[:150]
        if len(body) > 150:
            # Try to find a sentence boundary
            for i, char in enumerate(insight):
                if char in '.!?' and i > 50:
                    insight = insight[:i+1]
                    break
            else:
                insight = insight.rstrip() + "..."

        insights.append(insight)
        if len(insights) >= limit:
            break

    return insights


def enrich_reddit_item(
    item: Dict[str, Any],
    mock_thread_data: Optional[Dict] = None,
    timeout: int = 10,
    retries: int = 1,
) -> Dict[str, Any]:
    """Enrich a Reddit item with real engagement data.

    Args:
        item: Reddit item dict
        mock_thread_data: Mock data for testing
        timeout: HTTP timeout per attempt (default 10s for enrichment)
        retries: Number of retries (default 1 — fail fast for enrichment)

    Returns:
        Enriched item dict

    Raises:
        RedditRateLimitError: Propagated so caller can bail on remaining items
    """
    url = item.get("url", "")

    # Fetch thread data (RedditRateLimitError propagates to caller)
    thread_data = fetch_thread_data(url, mock_thread_data, timeout=timeout, retries=retries)
    if not thread_data:
        return item

    parsed = parse_thread_data(thread_data)
    submission = parsed.get("submission")
    comments = parsed.get("comments", [])

    # Update engagement metrics
    if submission:
        item["engagement"] = {
            "score": submission.get("score"),
            "num_comments": submission.get("num_comments"),
            "upvote_ratio": submission.get("upvote_ratio"),
        }

        # Update date from actual data
        created_utc = submission.get("created_utc")
        if created_utc:
            item["date"] = _safe_timestamp_to_date(created_utc)

    # Get top comments
    top_comments = get_top_comments(comments)
    item["top_comments"] = stored_top_comments(top_comments)

    # Extract insights
    item["comment_insights"] = extract_comment_insights(top_comments)

    return item


def enrich_reddit_item_sc(
    item: Dict[str, Any],
    token: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Enrich a Reddit item using ScrapeCreators comment API.

    No rate limit risk. Uses 1 credit per call.

    Args:
        item: Reddit item dict (already has engagement from search)
        token: ScrapeCreators API key
        timeout: HTTP timeout

    Returns:
        Enriched item with top_comments and comment_insights
    """
    from . import reddit as reddit_mod

    url = item.get("url", "")
    if not url:
        return item

    raw_comments = reddit_mod.fetch_post_comments(url, token)
    if not isinstance(raw_comments, list) or not raw_comments:
        return item

    dict_comments = [c for c in raw_comments if isinstance(c, dict)]
    if not dict_comments:
        return item

    top_comments = []
    for c in dict_comments[:10]:
        body = _coerce_text(c.get("body"))
        if not body or body in ("[deleted]", "[removed]"):
            continue

        score = _first_int(c.get("ups"), c.get("score"), default=0)
        author = _coerce_author(c.get("author"))
        raw_permalink = c.get("permalink")
        permalink = raw_permalink if isinstance(raw_permalink, str) else ""
        comment_url = f"https://reddit.com{permalink}" if permalink else ""

        top_comments.append({
            "score": score,
            "date": _safe_timestamp_to_date(c.get("created_utc")) if c.get("created_utc") else None,
            "author": author,
            "body": body[:300],
            "excerpt": snippet.cut_quote(body),
            "url": comment_url,
        })

    top_comments.sort(key=lambda c: _coerce_int(c.get("score"), default=0), reverse=True)

    item["top_comments"] = []
    for c in top_comments:
        item["top_comments"].append({
            "score": c.get("score", 0),
            "date": c.get("date"),
            "author": c.get("author", ""),
            "excerpt": c.get("excerpt", ""),
            "url": c.get("url", ""),
        })

    item["comment_insights"] = extract_comment_insights(top_comments)

    return item
