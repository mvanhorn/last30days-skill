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
from urllib.parse import quote, urlencode

from . import http
from . import reddit_arctic
from . import reddit_enrich
from . import reddit_refs
from . import snippet

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

# Match the exact <shreddit-comment> element start tag, not <shreddit-comment-tree>
# or <shreddit-comment-tree-stats> (lookahead requires whitespace or '>').
_COMMENT_START = re.compile(r"<shreddit-comment(?=[\s>])[^>]*>", re.IGNORECASE)
_TOTAL_COMMENTS = re.compile(r'(?<![A-Za-z0-9_.:-])total-comments="(\d+)"', re.IGNORECASE)
_PARA = re.compile(r"<p(?=[\s>])[^>]*?(?<!/)>(.*?)</p\s*>", re.S | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NEXT_RTJSON = re.compile(
    r'(?<![A-Za-z0-9_.:-])id\s*=\s*(?:"([A-Za-z0-9_]+)-(?:comment|post)-rtjson-content"'
    r"|\'([A-Za-z0-9_]+)-(?:comment|post)-rtjson-content\')",
    re.IGNORECASE,
)
_BODY_BOUNDARY = re.compile(
    r"<div(?=[\s>/])[^>]*>"
    r"|</div\s*>"
    r"|<shreddit-comment(?=[\s>])"
    r"|</shreddit-comment\s*>",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = re.compile(
    r"js-challenge|challenge-form|please wait for verification|"
    r"checking that your connection is secure|blocked by network security|"
    r"whoa there, pardner|captcha",
    re.IGNORECASE,
)
_OP_ATTR_NAMES = ("is-op", "is-submitter", "is_op", "is_submitter")
_TAG_HEAD = re.compile(r"^<\s*[^\s>/]+")
_TAG_ATTR = re.compile(
    r'(?<![A-Za-z0-9_.:-])([A-Za-z_:][A-Za-z0-9_.:-]*)'
    r'(?:\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'=<>`]+)))?'
)


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditShreddit] {msg}\n")
    sys.stderr.flush()


def unrecognized_comments_body(text: str) -> Optional[str]:
    """Validator for shreddit comment fetches: None if recognized, else a reason."""
    if not text or not text.strip():
        return "empty shreddit comments response"
    if _COMMENT_START.search(text) or _TOTAL_COMMENTS.search(text):
        return None
    if "<shreddit-comment-tree" in text.lower():
        return None
    if _CHALLENGE_MARKERS.search(text):
        return "challenge page instead of Reddit comments"
    return "not a Reddit shreddit comments page"


def extract_post_ref(url: str) -> Optional[tuple]:
    """Return (subreddit, post_id) from a Reddit thread URL, or None.

    ``subreddit`` is ``""`` for valid subreddit-less canonical URLs
    (``/comments/{id}/`` or ``redd.it/{id}``) so callers can still fall back
    to the archive by ``post_id``.
    """
    ref = reddit_refs.canonical_thread_url(url)
    if ref is None:
        return None
    return ref.subreddit, ref.post_id


def _svc_url(subreddit: str, post_id: str) -> str:
    # sort=top guarantees Reddit front-loads the highest-scored comments on the
    # first page, so the true top comments are captured even on huge threads
    # (we still re-sort by score locally as a backstop).
    sub = reddit_refs.normalize_subreddit(subreddit, allow_all=True)
    pid = reddit_refs.normalize_post_id(post_id)
    if not sub or not pid:
        raise ValueError(f"Invalid Reddit post reference: subreddit={subreddit!r}, post_id={post_id!r}")
    query = urlencode({"sort": "top"})
    return (
        f"https://www.reddit.com/svc/shreddit/comments/r/{quote(sub, safe='')}/t3_{quote(pid, safe='')}"
        f"?{query}"
    )


def _attr(tag: str, name: str) -> str:
    # HTML attribute names are case-insensitive: the server partial emits
    # ``thingId``, a DOM round-trip serializes it as ``thingid``. Match the exact
    # attribute name (not hyphenated suffixes such as ``data-author`` or
    # ``hide-score``) either way; the value keeps its case.
    if not tag or not name:
        return ""
    wanted = name.lower()
    body = _TAG_HEAD.sub("", tag.strip())
    if body.endswith(">"):
        body = body[:-1]
    for m in _TAG_ATTR.finditer(body):
        if m.group(1).lower() != wanted:
            continue
        raw = (
            m.group(2)
            if m.group(2) is not None
            else (m.group(3) if m.group(3) is not None else m.group(4))
        )
        if raw is not None:
            return _html.unescape(raw)
    return ""


def _bool_attr(tag: str, names: tuple[str, ...]) -> Optional[bool]:
    """Read an explicit HTML boolean/valued flag from a start tag, or None if absent."""
    wanted = {n.lower() for n in names}
    body = _TAG_HEAD.sub("", tag.strip()).rstrip(">/ \t\r\n")
    for m in _TAG_ATTR.finditer(body):
        if m.group(1).lower() not in wanted:
            continue
        raw = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4)
        )
        if raw is None or raw.strip() == "":
            return True
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return None


def _iso_to_date(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date().isoformat()
    except (ValueError, TypeError):
        return None


def _find_body_div_start(html_text: str, thing_id: str) -> Optional[int]:
    """Return the index right after the opening body ``<div>`` for ``thing_id``."""
    escaped = re.escape(thing_id)
    for suffix in ("post-rtjson-content", "comment-rtjson-content"):
        pattern = re.compile(
            rf'<div(?=[\s>])[^>]*?(?<![A-Za-z0-9_.:-])id\s*=\s*'
            rf'(?:"{escaped}-{suffix}"|\'{escaped}-{suffix}\')[^>]*>',
            re.IGNORECASE,
        )
        for m in pattern.finditer(html_text):
            if not m.group(0).rstrip().endswith("/>"):
                return m.end()
    return None


def _body_for(html_text: str, thing_id: str) -> str:
    """Extract a comment's text body, anchored on its unique thingId.

    The body div id embeds the comment's thingId (``-post-rtjson-content`` or
    ``-comment-rtjson-content``), so this assigns body→comment correctly even
    for nested replies. The slice is bounded by the matching closing ``</div>``
    (tracking nested ``<div>`` elements), ``<shreddit-comment>`` boundaries,
    and any other comment's rtjson anchor so deleted/removed child comments or
    UI footers are never concatenated into the parent body.
    """
    if not html_text or not thing_id:
        return ""
    start = _find_body_div_start(html_text, thing_id)
    if start is None:
        return ""
    window = html_text[start: start + 8000]
    thing_lower = thing_id.lower()
    depth = 1
    cut = len(window)
    for m in _BODY_BOUNDARY.finditer(window):
        tok = m.group(0)
        lower = tok.lower()
        if lower.startswith("</div"):
            depth -= 1
            if depth == 0:
                cut = m.start()
                break
        elif lower.startswith("<div"):
            rt = _NEXT_RTJSON.search(tok)
            if rt:
                other_id = (rt.group(1) or rt.group(2) or "").lower()
                if other_id != thing_lower:
                    cut = m.start()
                    break
            if not tok.rstrip().endswith("/>"):
                depth += 1
        else:
            cut = m.start()
            break
    window = window[:cut]
    for m in _NEXT_RTJSON.finditer(window):
        other_id = (m.group(1) or m.group(2) or "").lower()
        if other_id != thing_lower:
            window = window[: m.start()]
            break
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
        try:
            depth = int(_attr(tag, "depth") or 0)
        except ValueError:
            depth = 0
        permalink = _attr(tag, "permalink")
        entry: Dict[str, Any] = {
            "depth": depth,
            "score": score,
            "author": author,
            "body": body[:300],
            "excerpt": snippet.cut_quote(body),
            "permalink": permalink,
            "date": _iso_to_date(_attr(tag, "created")),
            "url": f"https://reddit.com{permalink}" if permalink else "",
        }
        is_op = _bool_attr(tag, _OP_ATTR_NAMES)
        if isinstance(is_op, bool):
            entry["is_uploader"] = is_op
        comments.append(entry)

    comments.sort(key=lambda c: c.get("score", 0), reverse=True)
    return comments[:limit]


def _total_comments(html_text: str) -> Optional[int]:
    m = _TOTAL_COMMENTS.search(html_text or "")
    return int(m.group(1)) if m else None


def _clean_post_author(post_author: Optional[str]) -> str:
    cleaned = str(post_author or "").strip().lower().removeprefix("/").removeprefix("u/")
    return "" if cleaned in ("", "[deleted]", "[removed]") else cleaned


def fetch_comments(
    post_url: str,
    timeout: int = SVC_TIMEOUT,
    deadline: Optional[float] = None,
    post_author: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch and parse top comments for a Reddit post via the shreddit endpoint.

    Args:
        post_url: Reddit thread URL (…/r/{sub}/comments/{id}/…)
        timeout: HTTP timeout in seconds
        deadline: absolute ``time.monotonic()`` bound shared by shreddit and the archive fallback
        post_author: optional OP username used to stamp ``is_uploader`` when a comment lacks one

    Returns:
        Dict with 'top_comments' (list, reddit_enrich shape), 'comment_insights'
        (list[str]), and 'num_comments' (int or None). Empty/None on any
        failure — never raises, so the caller can fall through to SC backup.
    """
    ref = extract_post_ref(post_url)
    if not ref:
        return {"top_comments": [], "comment_insights": [], "num_comments": None}
    if deadline is not None and time.monotonic() >= deadline:
        return {"top_comments": [], "comment_insights": [], "num_comments": None}
    sub, post_id = ref

    html_text: Optional[str] = None
    if sub:
        remaining = (deadline - time.monotonic()) if deadline is not None else None
        if remaining is None or remaining > 0:
            req_timeout = min(timeout, remaining) if remaining is not None else timeout
            get_kwargs: Dict[str, Any] = {
                "timeout": req_timeout,
                "retries": 1,
                "accept": "text/html",
                "validate": unrecognized_comments_body,
            }
            if deadline is not None:
                get_kwargs["deadline_monotonic"] = deadline
            html_text = http.reddit_keyless_get_text(_svc_url(sub, post_id), **get_kwargs)
        comments = parse_comments(html_text, limit=MAX_COMMENTS) if html_text else []
        total = _total_comments(html_text) if html_text else None
    else:
        comments = []
        total = None
    # Skip Arctic Shift when shreddit returned a recognized 0-comment thread page.
    skip_arctic = html_text is not None and total == 0
    if not comments and not skip_arctic:
        if deadline is not None and time.monotonic() >= deadline:
            return {"top_comments": [], "comment_insights": [], "num_comments": total}
        # The shreddit comment partials 403 on some hosts (datacenter egress).
        # Fall back to the arctic-shift archive, which serves scored comments
        # from any IP keyless (ranked by score client-side).
        try:
            comments = reddit_arctic.fetch_post_comments(
                post_id, limit=MAX_COMMENTS, skip_author=_is_bot_author,
                deadline=deadline)
        except Exception as exc:  # the fallback must never break enrichment
            _log(f"arctic comment fallback failed: {exc}")
            comments = []
    if not comments:
        return {"top_comments": [], "comment_insights": [], "num_comments": total}

    op_author = _clean_post_author(post_author)
    top_comments: List[Dict[str, Any]] = []
    for c in comments:
        entry: Dict[str, Any] = {
            "score": c["score"],
            "date": c["date"],
            "author": c["author"],
            "excerpt": c["excerpt"],
            "url": c["url"],
            "depth": c.get("depth", 0),
        }
        if isinstance(c.get("is_uploader"), bool):
            entry["is_uploader"] = c["is_uploader"]
        elif op_author:
            cname = _clean_post_author(c.get("author"))
            if cname:
                entry["is_uploader"] = cname == op_author
        top_comments.append(entry)

    insights = reddit_enrich.extract_comment_insights(comments)
    return {
        "top_comments": top_comments,
        "comment_insights": insights,
        "num_comments": total,
    }
