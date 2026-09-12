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
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import http
from . import reddit_enrich
from . import scrapling_fetch

# Up to N posts enriched per subquery, by depth. Raised from 3/5/8 once the
# per-command memo (http.reddit_keyless_get_text) collapsed repeat shreddit
# fetches across subqueries and the 1 req/s bucket stopped the 429s: eight
# fetches fit inside ENRICH_BUDGET at four workers, and the comments are the
# lane's headline value.
ENRICH_LIMITS = {
    "quick": 4,
    "default": 8,
    "deep": 12,
}

# Max comments returned per post (independent of how many posts get enriched).
# Twelve so a thousand-comment thread feeds more than ten candidates into the
# cross-platform Top Community Comments block.
MAX_COMMENTS = 12

SVC_TIMEOUT = 12

# Known bots whose comments carry no community signal.
BOT_AUTHORS = frozenset({
    "automoderator",
    "remindmebot",
    "repostsleuthbot",
    "sneakpeekbot",
    "savevideo",
    "videodownloadbot",
    "totesmessenger",
    "b0trank",
    "amputatorbot",
    "stabbot",
    "gifreversingbot",
    "haikubotinaction",
    "imagesofnetwork",
    "botdefense",
})

_BOT_SUFFIXES = ("-bot", "_bot")

# CamelCase catches WikiTextBot without swallowing names such as Talbot.
_CAMEL_BOT = re.compile(r"[a-z0-9]Bot\d*$")
# Browser fallback is slower than the HTTP path: a stealthy-fetch launches a real
# browser to clear Reddit's block, so it gets a wider window. When the caller
# passes a ``deadline`` (the enrichment budget in reddit_keyless._enrich), the
# effective timeout is capped to the time remaining so the browser subprocess is
# killed by the deadline instead of outliving a cancelled future.
SCRAPLING_TIMEOUT = 75

# Below this many remaining seconds the fallback is skipped outright: launching
# a stealthy browser takes several seconds by itself, so a tighter window can
# only burn the tail of the budget without returning usable markup.
SCRAPLING_MIN_BUDGET = 10

# On timeout, subproc.run_with_timeout runs SIGTERM -> wait(5) -> SIGKILL ->
# wait(5) AFTER the timeout fires, so a subprocess handed the whole remaining
# budget outlives the deadline by up to this many seconds. The cap reserves it,
# so timeout + cleanup always fits inside the enrichment budget.
SCRAPLING_CLEANUP_GRACE = 10

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
    # Case-insensitive on the attribute NAME: the server partial emits the
    # custom-element attribute as ``thingId`` (camelCase), but when the same
    # markup is fetched through a real browser (the Scrapling fallback below),
    # the DOM lowercases every attribute name to ``thingid``. HTML attribute
    # names are case-insensitive, so matching either shape keeps both fetch
    # paths feeding the one parser. Attribute values stay case-sensitive.
    m = re.search(rf'\b{name}="([^"]*)"', tag, re.IGNORECASE)
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


def _is_bot_author(author: str) -> bool:
    """Whether an author is a bot whose comments carry no community signal."""
    raw = (author or "").strip()
    if not raw:
        return False
    name = raw.lower()
    return (name in BOT_AUTHORS
            or name.endswith(_BOT_SUFFIXES)
            or bool(_CAMEL_BOT.search(raw)))


def parse_comments(html_text: str, limit: int = MAX_COMMENTS) -> List[Dict[str, Any]]:
    """Parse <shreddit-comment> elements into scored comment dicts (sorted desc).

    Deleted, removed, and bot authors are dropped: they occupy top-comment slots
    on high-traffic threads without saying anything about the topic.
    """
    comments: List[Dict[str, Any]] = []
    for m in _COMMENT_START.finditer(html_text or ""):
        tag = m.group(0)
        author = _attr(tag, "author") or "[deleted]"
        if author in ("[deleted]", "[removed]") or _is_bot_author(author):
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


def _blocked_by_reddit(failures: List[http.HTTPError]) -> bool:
    """True when the keyless GET was refused by Reddit's block wall (HTTP 403).

    That is the only miss the browser fallback exists for. Any other empty
    outcome -- 5xx, 429, a timeout, a memo election that gave up, an empty 200
    body -- is not something a stealthy browser fetch would change, so it must
    not pay for a browser launch.
    """
    return any(f.status_code == 403 for f in failures)


def _scrapling_svc_fallback(
    svc_url: str,
    deadline: Optional[float] = None,
) -> Optional[str]:
    """Browser fallback for the shreddit partial when the keyless HTTP path is
    blocked.

    Reddit now 403s the keyless GET from many contexts. When the Scrapling CLI
    is installed, a stealthy-fetch renders the same URL in a real browser, clears
    the block (Reddit's own "network security" wall, not Cloudflare, so no
    challenge-solve is needed), and returns the same shreddit markup -- with
    attribute names lowercased by the DOM, which ``_attr`` tolerates.

    ``deadline`` is a ``time.monotonic()`` instant (the caller's aggregate
    enrichment budget). The subprocess timeout is capped to the time remaining
    minus ``SCRAPLING_CLEANUP_GRACE``, so ``subproc.run_with_timeout``'s
    SIGTERM/SIGKILL cleanup of the browser's process group also completes
    before the deadline rather than outliving a cancelled future; when less
    than ``SCRAPLING_MIN_BUDGET`` would be left for the browser itself the
    fallback is skipped entirely.

    Strictly additive: the caller only invokes it after the HTTP path was
    refused with HTTP 403 (see ``_blocked_by_reddit``) -- never on a 5xx, a
    429, a timeout, or an empty body, none of which a browser would clear --
    and it no-ops to ``None`` when the CLI is absent (CI, Cowork), so behavior
    is unchanged wherever Scrapling is not installed. Returns None on any failure.
    """
    if not scrapling_fetch.is_available():
        return None
    timeout = SCRAPLING_TIMEOUT
    if deadline is not None:
        # Reserve the process-group cleanup window up front: what is left after
        # that is the most the browser may run.
        remaining = int(deadline - time.monotonic()) - SCRAPLING_CLEANUP_GRACE
        if remaining < SCRAPLING_MIN_BUDGET:
            _log(f"keyless blocked; {remaining}s left in budget, skipping browser fallback")
            return None
        timeout = min(timeout, remaining)
    _log("keyless blocked; trying Scrapling browser fallback")
    return scrapling_fetch.fetch(
        svc_url,
        mode=scrapling_fetch.MODE_STEALTHY,
        fmt="html",
        timeout=timeout,
    )


def fetch_comments(
    post_url: str,
    timeout: int = SVC_TIMEOUT,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Fetch and parse top comments for a Reddit post via the shreddit endpoint.

    Args:
        post_url: Reddit thread URL (…/r/{sub}/comments/{id}/…)
        timeout: HTTP timeout in seconds
        deadline: optional ``time.monotonic()`` instant bounding the browser
            fallback (see ``_scrapling_svc_fallback``); the HTTP path keeps
            its own short ``timeout`` regardless

    Returns:
        Dict with 'top_comments' (list, reddit_enrich shape), 'comment_insights'
        (list[str]), and 'num_comments' (int or None). Empty/None on any
        failure — never raises, so the caller can fall through to SC backup.
    """
    ref = extract_post_ref(post_url)
    if not ref:
        return {"top_comments": [], "comment_insights": [], "num_comments": None}
    sub, post_id = ref

    svc_url = _svc_url(sub, post_id)
    # tee_failures() recovers the status that get_text swallows (it returns None
    # on any failure) while still forwarding it to the pipeline's sink.
    with http.tee_failures() as misses:
        html_text = http.reddit_keyless_get_text(svc_url, timeout=timeout, accept="text/html")
    if not html_text and _blocked_by_reddit(misses):
        html_text = _scrapling_svc_fallback(svc_url, deadline=deadline)
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
