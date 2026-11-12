"""Digg source for last30days.

Shells out to ``digg-pp-cli`` (read-only, no auth required) to surface
clustered stories from Digg. Each cluster carries a published TLDR,
Digg's own engagement numbers, and a list of X posts that can be fetched
as inline quotes.

Activation gate: this source is only available when ``digg-pp-cli`` is
on PATH. ``pipeline.available_sources`` checks ``shutil.which`` before
including ``digg`` in the source list. The functions below also detect
the missing-binary case as a defensive fallback.

Primary path: ``digg-pp-cli search <topic> --since 30d --agent --limit N``,
which searches every Digg topic live and returns stories with a canonical
``url``, a ``diggScore`` and a ``publishedAt`` date.

digg-pp-cli 2026.10.x is still supported. Its ``search`` covers only the
sections it is told to (default ``ai``) and rejects flags it does not
know, so the call is chosen up front by ``cli_searches_all_topics`` and
those installs keep the ``--topic ai,tech`` call. Their answers carry no
``url`` and take the older parsing path; see ``upgrade_note``.
Optional enrichment: ``digg-pp-cli posts <clusterUrlId> --agent --by rank
--limit M`` for the top K clusters in default/deep depth, attaching the
top-ranked X posts to each cluster's ``posts`` field.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "digg-pp-cli"

# Sections searched on digg-pp-cli 2026.10.x, whose search defaults to ai
# alone. Newer CLIs search every topic when no section is named.
SECTIONS = "ai,tech"

# Outcome note for a search answered by digg-pp-cli 2026.10.x. Kept under
# the 140-character outcome note limit in render.
UPGRADE_NOTE = (
    f"this {CLI_BIN} searches only Digg's AI and tech sections; "
    "upgrade it to search every Digg topic with engagement scores"
)

# Key set on a search answer that came from the ai,tech call.
_LEGACY_ANSWER_KEY = "legacy_cli"

# Per-depth knobs.
DEPTH_CONFIG = {
    "quick": 8,
    "default": 20,
    "deep": 50,
}

# How many top-ranked clusters get post enrichment, per depth. Quick mode
# skips enrichment to keep latency low (clusters already carry a TLDR).
ENRICH_CONFIG = {
    "quick": 0,
    "default": 3,
    "deep": 5,
}

# X posts pulled per enriched cluster. Matches the 5-comment cap used by
# Reddit/HN/YouTube/TikTok/GitHub enrichment.
POSTS_PER_CLUSTER = 5

# On digg-pp-cli 2026.10.x a stale section makes the CLI fetch up to 8 live
# feed pages per section before answering (about 8s for ai,tech), well
# inside this budget.
SEARCH_TIMEOUT = 30
POSTS_TIMEOUT = 15
HELP_TIMEOUT = 10

# Whether the installed CLI searches every topic. None until first checked;
# the answer holds for the rest of the process.
_all_topic_search: Optional[bool] = None
_all_topic_search_lock = threading.Lock()


def _log(msg: str) -> None:
    log.source_log("Digg", msg, tty_only=False)


def _is_available() -> bool:
    """True when the digg-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _parse_first_post_age(age: Optional[str], today: Optional[datetime] = None) -> Optional[str]:
    """Convert a digg firstPostAge token (e.g. '5d', '17d', '5h', '1w', '1m')
    into a YYYY-MM-DD string. Returns None when the value is outside the
    last-30-day window or cannot be parsed.

    Digg uses minutes-symbol-collision for 'months' (per agent-context:
    'Nh, Nd, Nw, Nm (e.g. 30d, 1w, 12h, 1m)'), so 'Nm' is months ~30 days.
    """
    if not age or not isinstance(age, str):
        return None
    age = age.strip().lower()
    if len(age) < 2:
        return None
    unit = age[-1]
    try:
        amount = int(age[:-1])
    except (ValueError, TypeError):
        return None
    if amount < 0:
        return None

    base = today or _today()

    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    elif unit == "m":
        delta = timedelta(days=amount * 30)
    else:
        return None

    if delta > timedelta(days=30):
        return None

    point = base - delta
    return point.date().isoformat()


def _published_date(published_at: Any) -> Optional[str]:
    """Convert a ``publishedAt`` timestamp into a UTC YYYY-MM-DD string."""
    if not isinstance(published_at, str) or not published_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(published_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


def cli_searches_all_topics() -> bool:
    """True when the installed CLI's ``search`` covers every Digg topic.

    digg-pp-cli 2026.10.x defaults ``--topic`` to ``ai`` and rejects unknown
    flags, so the call has to be chosen before it is made. The all-topic
    search shipped together with ``--sort``, so its presence in
    ``search --help`` identifies the newer CLI. The help text is read once
    per process; a help call that fails is not an answer and is retried on
    the next search.
    """
    global _all_topic_search
    with _all_topic_search_lock:
        if _all_topic_search is not None:
            return _all_topic_search
        try:
            result = subproc.run_with_timeout(
                [CLI_BIN, "search", "--help"], timeout=HELP_TIMEOUT
            )
        except (subproc.SubprocTimeout, OSError) as exc:
            _log(f"search --help failed: {exc}")
            return False
        if result.returncode != 0:
            _log(f"search --help exit {result.returncode}")
            return False
        _all_topic_search = "--sort" in (result.stdout or "")
        return _all_topic_search


def _build_search_args(query: str, limit: int, *, all_topics: bool) -> List[str]:
    """Build the search call.

    The all-topic call names no section and no sort order: the newer CLI
    defaults to every topic in relevance order, which is what the
    position-based relevance hint in ``parse_digg_response`` expects.
    """
    sections = [] if all_topics else ["--topic", SECTIONS]
    return [
        CLI_BIN,
        "search",
        query,
        *sections,
        "--since",
        "30d",
        "--agent",
        "--limit",
        str(limit),
    ]


def _build_posts_args(cluster_url_id: str, posts_per: int) -> List[str]:
    return [
        CLI_BIN,
        "posts",
        cluster_url_id,
        "--agent",
        "--by",
        "rank",
        "--limit",
        str(posts_per),
    ]


_LOCAL_FALLBACK_MARKER = "falling back to local FTS5:"
_FALLBACK_REASON_MAX = 200


def _local_fallback_reason(stderr: str) -> str:
    """Extract the live-search failure from digg-pp-cli's fallback stderr.

    The CLI prints ``falling back to local FTS5: <error>`` and the error can
    embed the full HTML body of a failed response, so the reason is cut at
    the HTTP status when present and length-bounded otherwise.
    """
    for line in (stderr or "").splitlines():
        _, marker, rest = line.partition(_LOCAL_FALLBACK_MARKER)
        if not marker:
            continue
        rest = rest.strip()
        status = re.match(r".*?\bHTTP \d{3}\b", rest)
        if status:
            return status.group(0)
        return rest[:_FALLBACK_REASON_MAX].rstrip()
    return ""


def _empty_local_fallback_error(data: Dict[str, Any], stderr: str = "") -> Optional[str]:
    """Return an error when the CLI answered from an empty local store.

    ``digg-pp-cli search`` is live by default, but it falls back to its FTS5
    store on a network error and exits 0 with ``meta.source == "local"``.
    That store only holds rows for users who ran ``digg-pp-cli sync``
    themselves, which last30days never does - so a broken live search
    reaches the user as ``Digg: 0 items (no results)`` instead of an error.
    A non-empty local store is real data and is left alone.

    CLI builds from 2026.10.1 always send ``meta.stale`` and exit non-zero
    themselves when they have no usable data, so an empty local answer from
    them is a real "no matches" and is left alone too.

    The CLI reports the live failure only on stderr; ``meta.reason`` is
    preferred if a future CLI release adds it to the envelope.
    """
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("source") != "local":
        return None
    if "stale" in meta:
        return None
    if data.get("results"):
        return None
    reason = str(meta.get("reason") or "").strip()[:_FALLBACK_REASON_MAX]
    detail = (
        reason
        or _local_fallback_reason(stderr)
        or "live search fell back to an empty local store"
    )
    return f"live-search-unavailable: {detail}"


def _invoke_cli(cmd: List[str], timeout: int) -> Tuple[Dict[str, Any], str]:
    """Invoke digg-pp-cli and return the parsed envelope with its stderr.

    The envelope is ``{"results": [...]}`` on success and
    ``{"results": [], "error": "..."}`` on failure. Never raises; the
    pipeline relies on shape consistency.
    """
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}, ""
    try:
        result = subproc.run_with_timeout(cmd, timeout=timeout)
    except subproc.SubprocTimeout as exc:
        _log(f"Timeout: {exc}")
        return {"results": [], "error": str(exc)}, ""
    except FileNotFoundError as exc:
        _log(f"Binary missing: {exc}")
        return {"results": [], "error": str(exc)}, ""
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return {"results": [], "error": str(exc)}, ""

    stderr = result.stderr or ""
    if result.returncode != 0:
        snippet = stderr.strip().splitlines()[:1]
        first = snippet[0] if snippet else f"exit {result.returncode}"
        _log(f"CLI exit {result.returncode}: {first}")
        return {"results": [], "error": first}, stderr

    stdout = result.stdout or ""
    if not stdout.strip():
        return {"results": []}, stderr
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log(f"JSON decode failed: {exc}")
        return {"results": [], "error": f"json decode: {exc}"}, stderr

    if not isinstance(data, dict):
        return {"results": []}, stderr
    results = data.get("results")
    if not isinstance(results, list):
        return {"results": []}, stderr
    return data, stderr


def _run_cli(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Invoke digg-pp-cli and return only the parsed envelope."""
    return _invoke_cli(cmd, timeout)[0]


def search_digg(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search Digg clusters via digg-pp-cli.

    Args:
        topic: search query.
        from_date: YYYY-MM-DD start (advisory; --since 30d is the actual filter).
        to_date: YYYY-MM-DD end (advisory; same).
        depth: 'quick' | 'default' | 'deep'.

    Returns:
        Dict with ``results`` list. On failure, ``results`` is empty and an
        ``error`` key carries a one-line description. An answer from the
        ai,tech call of digg-pp-cli 2026.10.x is marked for ``upgrade_note``.
    """
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    if not topic or not topic.strip():
        return {"results": []}
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    all_topics = cli_searches_all_topics()
    cmd = _build_search_args(topic, limit, all_topics=all_topics)
    scope = "all topics" if all_topics else f"sections={SECTIONS}"
    _log(f"search '{topic}' ({scope}, limit={limit}, since=30d)")
    response, stderr = _invoke_cli(cmd, timeout=SEARCH_TIMEOUT)
    fallback_error = _empty_local_fallback_error(response, stderr)
    if fallback_error:
        _log(fallback_error)
        return {"results": [], "error": fallback_error}
    n = len(response.get("results") or [])
    _log(f"found {n} clusters")
    if not all_topics and not response.get("error"):
        response[_LEGACY_ANSWER_KEY] = True
        _log(UPGRADE_NOTE)
    return response


def upgrade_note(response: Dict[str, Any]) -> Optional[str]:
    """Describe a search answered by digg-pp-cli 2026.10.x, else None.

    That CLI searches only the AI and tech sections, from a local feed copy
    that can be stale, and reports no engagement score. Upgrading fixes all
    three, so this one note stands in for the older stale-copy note.
    """
    if not isinstance(response, dict) or response.get(_LEGACY_ANSWER_KEY) is not True:
        return None
    return UPGRADE_NOTE


def is_upgrade_note(detail: Optional[str]) -> bool:
    """True when an outcome detail carries the upgrade note."""
    return bool(detail) and UPGRADE_NOTE in detail


def _build_url(cluster_url_id: str) -> str:
    return f"https://di.gg/ai/{cluster_url_id}"


def _rank_score(rank: Optional[int]) -> float:
    """Convert Digg rank (lower is better, top 50 are notable) into a
    positive engagement-style signal in [0, 50]. Anything off the top-50
    leaderboard contributes 0.
    """
    if rank is None:
        return 0.0
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.0
    if r < 1 or r > 50:
        return 0.0
    return float(51 - r)


def _position_relevance(position: int, query: str, text: str, rank_boost: float = 0.0) -> float:
    """Relevance hint from result position, query overlap and a rank boost."""
    rank_decay = max(0.3, 1.0 - (position * 0.02))
    content_score = token_overlap_relevance(query, text.strip()) if query else 0.5
    return round(min(1.0, 0.55 * rank_decay + 0.35 * content_score + rank_boost), 2)


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _parse_story(
    story: Dict[str, Any],
    position: int,
    query: str,
    from_date: str,
) -> Optional[Dict[str, Any]]:
    """Parse one result of the all-topic search, which carries its own URL.

    Returns None for a story published before ``from_date`` or whose URL is
    not a web link.
    """
    cluster_url_id = str(story["clusterUrlId"])
    url = str(story.get("url") or "").strip()
    if not _is_safe_http_url(url):
        # The URL is rendered into a link in the report, so a non-web
        # scheme from upstream is dropped the same way an unsafe xUrl is.
        _log(f"dropped story with unsafe url scheme: {url!r}")
        return None
    date_str = _published_date(story.get("publishedAt"))
    if date_str is not None and date_str < from_date:
        return None

    title = str(story.get("title") or "").strip()
    tldr = str(story.get("tldr") or "").strip()
    source_count = story.get("sourceCount")
    if source_count is None:
        source_count = story.get("postCount")
    post_count = _count(source_count)
    unique_authors = _count(story.get("uniqueAuthors"))
    engagement: Dict[str, Any] = {
        "postCount": post_count,
        "uniqueAuthors": unique_authors,
    }
    counts = f"{post_count} posts, {unique_authors} authors"
    digg_score = story.get("diggScore")
    if isinstance(digg_score, (int, float)) and not isinstance(digg_score, bool):
        engagement["digg_score"] = digg_score
        counts = f"Digg Score {digg_score:g}, {counts}"

    return {
        "id": cluster_url_id,
        "cluster_id": str(story.get("clusterId") or ""),
        "topic": str(story.get("topic") or ""),
        "title": title or f"Digg cluster {position + 1}",
        "url": url,
        "tldr": tldr,
        "author": "",
        "date": date_str,
        "engagement": engagement,
        "first_post_age": story.get("firstPostAge"),
        "posts": [],
        "relevance": _position_relevance(position, query, f"{title} {tldr}"),
        "why_relevant": f"Digg cluster ({counts})",
    }


def parse_digg_response(
    response: Dict[str, Any],
    query: str = "",
    from_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse a digg search envelope into normalized item dicts.

    A result carrying ``url`` comes from the all-topic search: its link,
    date and engagement are read from the story itself. Any other result
    is a digg-pp-cli 2026.10.x cluster and keeps the older path, with a
    ``di.gg/ai`` link and a date derived from ``firstPostAge``.

    Args:
        response: payload from ``search_digg``.
        query: original search query, used for token-overlap relevance.
        from_date: YYYY-MM-DD start of the research window. Stories with a
            ``publishedAt`` before it are dropped. Defaults to 30 days ago.

    Returns:
        List of dicts ready for ``normalize._normalize_digg``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []
    window_start = from_date or (_today() - timedelta(days=30)).date().isoformat()

    items: List[Dict[str, Any]] = []
    for i, cluster in enumerate(raw):
        if not isinstance(cluster, dict):
            continue
        cluster_url_id = cluster.get("clusterUrlId")
        if not cluster_url_id:
            continue
        if cluster.get("url"):
            story = _parse_story(cluster, i, query, window_start)
            if story is not None:
                items.append(story)
            continue

        title = str(cluster.get("title") or "").strip()
        tldr = str(cluster.get("tldr") or "").strip()
        rank = cluster.get("rank")
        post_count = cluster.get("postCount") or 0
        unique_authors = cluster.get("uniqueAuthors") or 0
        first_post_age = cluster.get("firstPostAge")
        date_str = _parse_first_post_age(first_post_age)
        if date_str is None and first_post_age:
            # firstPostAge present but outside 30d -> drop; last30days contract.
            continue

        rank_boost = min(0.2, _rank_score(rank) / 250.0)
        relevance = _position_relevance(i, query, f"{title} {tldr}", rank_boost)

        items.append(
            {
                "id": str(cluster_url_id),
                "title": title or f"Digg cluster {i + 1}",
                "url": _build_url(str(cluster_url_id)),
                "tldr": tldr,
                "author": "",
                "date": date_str,
                "engagement": {
                    "postCount": int(post_count) if isinstance(post_count, (int, float)) else 0,
                    "uniqueAuthors": int(unique_authors) if isinstance(unique_authors, (int, float)) else 0,
                    "rank": int(rank) if isinstance(rank, (int, float)) else None,
                    "rank_score": _rank_score(rank),
                },
                "first_post_age": first_post_age,
                "posts": [],
                "relevance": relevance,
                "why_relevant": (
                    f"Digg cluster (rank {rank}, {post_count} posts, {unique_authors} authors)"
                    if rank is not None
                    else f"Digg cluster ({post_count} posts, {unique_authors} authors)"
                ),
            }
        )

    return items


def _is_safe_http_url(url: str) -> bool:
    """True iff ``url`` parses with an http or https scheme.

    Used to reject upstream-supplied post URLs whose scheme would be
    dangerous in a rendered ``<a href>`` (``javascript:``, ``data:``,
    ``file:``, ``vbscript:``, ``about:``).
    """
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


def _parse_post(raw_post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reduce a digg post payload into the small dict render uses.

    We deliberately keep this minimal: an inline quote needs the author
    handle, the body, the post type, and the X URL.
    """
    if not isinstance(raw_post, dict):
        return None
    body = str(raw_post.get("body") or "").strip()
    if not body:
        return None
    author = raw_post.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    username = str(author.get("username") or "").strip()
    if not username:
        return None
    x_url = str(raw_post.get("xUrl") or "").strip()
    if not x_url:
        return None
    if not _is_safe_http_url(x_url):
        # Security-class drop: an upstream-supplied URL with a dangerous
        # scheme. Force tty_only=False so the rejection is visible in
        # non-interactive runs (Claude Code), which is the actual attack
        # surface — the default tty_only=True would suppress it there.
        log.source_log(
            "Digg",
            f"dropped post with unsafe xUrl scheme: {x_url!r}",
            tty_only=False,
        )
        return None
    return {
        "username": username,
        "display_name": str(author.get("display_name") or "").strip() or username,
        "category": str(author.get("category") or "").strip(),
        "rank": author.get("rank"),
        "body": body,
        "post_type": str(raw_post.get("post_type") or "tweet").strip(),
        "x_url": x_url,
        "posted_at": raw_post.get("posted_at"),
    }


def fetch_top_posts(cluster_url_id: str, posts_per: int = POSTS_PER_CLUSTER) -> List[Dict[str, Any]]:
    """Fetch top-ranked X posts attached to a cluster.

    Returns an empty list on any failure (timeout, missing cluster, JSON
    error). Never raises.
    """
    if posts_per <= 0:
        return []
    cmd = _build_posts_args(cluster_url_id, posts_per)
    response = _run_cli(cmd, timeout=POSTS_TIMEOUT)
    raw = response.get("results") or []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        post = _parse_post(entry)
        if post is not None:
            out.append(post)
    return out


def enrich_with_top_posts(
    items: List[Dict[str, Any]],
    top_k: int = 3,
    posts_per: int = POSTS_PER_CLUSTER,
) -> List[Dict[str, Any]]:
    """Attach top X posts to the first ``top_k`` clusters by Digg rank order.

    Mutates and returns the same list. Items that already have posts, or
    whose ``postCount`` is 0, are skipped.
    """
    if top_k <= 0 or posts_per <= 0:
        return items
    enriched = 0
    for item in items:
        if enriched >= top_k:
            break
        if item.get("posts"):
            continue
        engagement = item.get("engagement") or {}
        if not engagement.get("postCount"):
            continue
        cluster_url_id = item.get("id")
        if not cluster_url_id:
            continue
        posts = fetch_top_posts(str(cluster_url_id), posts_per=posts_per)
        item["posts"] = posts
        enriched += 1
    if enriched:
        _log(f"enriched {enriched} clusters with X posts")
    return items


def enrich_source_items(items: list, top_k: int = 3, posts_per: int = POSTS_PER_CLUSTER) -> list:
    """Attach top X posts to the first ``top_k`` SourceItems that survived dedupe.

    Reads ``metadata['clusterUrlId']`` and writes ``metadata['posts']`` in
    place. Skips items that already carry a non-empty ``metadata['posts']``,
    items whose engagement ``postCount`` is 0, and items whose source is not
    'digg'. Designed to run from `_finalize_items_by_source` so enrichment
    is spent on the items the brief actually shows.
    """
    if top_k <= 0 or posts_per <= 0:
        return items
    enriched = 0
    for item in items:
        if enriched >= top_k:
            break
        if getattr(item, "source", None) != "digg":
            continue
        metadata = getattr(item, "metadata", None) or {}
        if metadata.get("posts"):
            continue
        engagement = getattr(item, "engagement", None) or {}
        if not engagement.get("postCount"):
            continue
        cluster_url_id = metadata.get("clusterUrlId") or item.item_id
        if not cluster_url_id:
            continue
        posts = fetch_top_posts(str(cluster_url_id), posts_per=posts_per)
        if posts:
            metadata["posts"] = posts
            enriched += 1
    if enriched:
        _log(f"post-dedupe enriched {enriched} clusters with X posts")
    return items
