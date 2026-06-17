"""Keyless Reddit comment enrichment via shreddit /svc endpoints.

Reddit's ``{thread}.json`` endpoint now returns HTTP 403. The shreddit partial
endpoint ``/svc/shreddit/comments/r/{sub}/t3_{id}`` still serves HTTP 200 HTML
with no API key, embedding each comment as a ``<shreddit-comment>`` custom
element whose start-tag attributes carry ``score`` / ``author`` / ``created`` /
``permalink``, and whose body lives in a ``<div id="{thingId}-post-rtjson-content">``
block. This module parses that markup into top comments, matching the
``top_comments`` / ``comment_insights`` shape produced by ``reddit_enrich`` so
the renderer is unaffected.

Limitation: the comments endpoint carries the real comment count
(``total-comments``) but not the post's upvote score, so post-level ``score``
cannot be recovered keylessly here (ScrapeCreators backup still provides it).
"""

import html as _html
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import http
from . import reddit_enrich

# Up to N posts enriched per run, by depth (mirrors reddit_public.ENRICH_LIMITS).
ENRICH_LIMITS = {
    "quick": 3,
    "default": 5,
    "deep": 8,
}

# Max comments returned per post (independent of how many posts get enriched).
MAX_COMMENTS = 10

SVC_TIMEOUT = 12

# Match the exact <shreddit-comment> element start tag, not <shreddit-comment-tree>
# or <shreddit-comment-tree-stats> (lookahead requires whitespace or '>').
_COMMENT_START = re.compile(r"<shreddit-comment(?=[\s>])[^>]*>")
_TOTAL_COMMENTS = re.compile(r'total-comments="(\d+)"')
_PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NEXT_RTJSON = re.compile(r'id="t1_[A-Za-z0-9]+-(?:comment|post)-rtjson-content"')


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditShreddit] {msg}\n")
    sys.stderr.flush()


def extract_post_ref(url: str) -> Optional[tuple]:
    """Return (subreddit, post_id) from a Reddit thread URL, or None."""
    m = re.search(r"/r/([^/]+)/comments/([A-Za-z0-9]+)", url or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def _svc_url(subreddit: str, post_id: str) -> str:
    # sort=top guarantees Reddit front-loads the highest-scored comments on the
    # first page, so the true top comments are captured even on huge threads
    # (we still re-sort by score locally as a backstop).
    return (
        f"https://www.reddit.com/svc/shreddit/comments/r/{subreddit}/t3_{post_id}"
        f"?sort=top"
    )


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return _html.unescape(m.group(1)) if m else ""


def _iso_to_date(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date().isoformat()
    except (ValueError, TypeError):
        return None


def _body_for(html_text: str, thing_id: str) -> str:
    """Extract a comment's text body, anchored on its unique thingId.

    The body div id embeds the comment's thingId, so this assigns body→comment
    correctly even for nested replies. The slice is bounded by the next
    comment's rtjson anchor to avoid swallowing child-comment text.
    """
    if not thing_id:
        return ""
    anchor = f'id="{thing_id}-post-rtjson-content"'
    idx = html_text.find(anchor)
    if idx == -1:
        return ""
    window = html_text[idx + len(anchor): idx + len(anchor) + 8000]
    nxt = _NEXT_RTJSON.search(window)
    if nxt:
        window = window[: nxt.start()]
    paras = _PARA.findall(window)
    if not paras:
        return ""
    text = " ".join(_TAG.sub("", p) for p in paras)
    return _WS.sub(" ", _html.unescape(text)).strip()


def parse_comments(html_text: str, limit: int = MAX_COMMENTS) -> List[Dict[str, Any]]:
    """Parse <shreddit-comment> elements into scored comment dicts (sorted desc)."""
    comments: List[Dict[str, Any]] = []
    for m in _COMMENT_START.finditer(html_text or ""):
        tag = m.group(0)
        author = _attr(tag, "author") or "[deleted]"
        if author in ("[deleted]", "[removed]"):
            continue
        thing_id = _attr(tag, "thingId")
        body = _body_for(html_text, thing_id)
        if not body or body in ("[deleted]", "[removed]"):
            continue
        try:
            score = int(_attr(tag, "score") or 0)
        except ValueError:
            score = 0
        permalink = _attr(tag, "permalink")
        comments.append({
            "score": score,
            "author": author,
            "body": body[:300],
            "excerpt": body[:200],
            "permalink": permalink,
            "date": _iso_to_date(_attr(tag, "created")),
            "url": f"https://reddit.com{permalink}" if permalink else "",
        })

    comments.sort(key=lambda c: c.get("score", 0), reverse=True)
    return comments[:limit]


def _total_comments(html_text: str) -> Optional[int]:
    m = _TOTAL_COMMENTS.search(html_text or "")
    return int(m.group(1)) if m else None


def fetch_comments(
    post_url: str,
    timeout: int = SVC_TIMEOUT,
) -> Dict[str, Any]:
    """Fetch and parse top comments for a Reddit post via the shreddit endpoint.

    Args:
        post_url: Reddit thread URL (…/r/{sub}/comments/{id}/…)
        timeout: HTTP timeout in seconds

    Returns:
        Dict with 'top_comments' (list, reddit_enrich shape), 'comment_insights'
        (list[str]), and 'num_comments' (int or None). Empty/None on any
        failure — never raises, so the caller can fall through to SC backup.
    """
    ref = extract_post_ref(post_url)
    if not ref:
        return {"top_comments": [], "comment_insights": [], "num_comments": None}
    sub, post_id = ref

    html_text = http.get_text(_svc_url(sub, post_id), timeout=timeout, accept="text/html")
    if not html_text:
        return {"top_comments": [], "comment_insights": [], "num_comments": None}

    comments = parse_comments(html_text, limit=MAX_COMMENTS)
    insights = reddit_enrich.extract_comment_insights(comments)
    return {
        "top_comments": [
            {
                "score": c["score"],
                "date": c["date"],
                "author": c["author"],
                "excerpt": c["excerpt"],
                "url": c["url"],
            }
            for c in comments
        ],
        "comment_insights": insights,
        "num_comments": _total_comments(html_text),
    }
