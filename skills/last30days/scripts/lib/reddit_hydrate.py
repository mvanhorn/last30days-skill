"""Turn discovered Reddit thread refs into Reddit items, one chain for every path.

Each discovery path (web search, a host's thread list, a logged-in session)
hands over :class:`reddit_refs.ThreadRef` values. :func:`hydrate` fills them
in best-first:

1. An injected session hydrator, when the caller has one. It returns
   Reddit's live data for the refs it could read.
2. Arctic Shift ``/posts/ids`` for the rest, in batches of at most 100 ids,
   one request at a time through the shared keyless limiter. A post under
   ``reddit_refs.PENDING_SECONDS`` old has no settled archive score yet, so
   its score is withheld (``None``) and labeled ``arctic-pending``.
3. A ref nothing filled keeps the search result's title, URL and date with an
   unknown score, so an archive outage leaves Reddit thin, not empty.

Comments for the most-discussed kept threads come from Arctic Shift
``/comments/tree`` within the lane's comment budget, each with its ``depth``
(0 for a top-level comment); a fresh archive comment is pending like a fresh
post. When the caller passes a ScrapeCreators fetch (it has a key) and fewer
threads than the floor are on topic, the fetch runs and its threads merge in
by thread id. A pending thread counts as on topic when its title names the
topic.

The items have the shape the Reddit lane hands the pipeline (title, url,
subreddit, date, engagement, top_comments, metadata.post_id), plus
``engagement_source`` and ``comments_source`` naming where the score and the
comments came from, and ``provenance``, the discovery paths that found the
thread. ``normalize`` carries both labels into ``SourceItem.metadata``.
Never raises.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from . import env, reddit_arctic, reddit_shreddit
from .reddit_refs import (
    ARCTIC,
    ARCTIC_PENDING,
    LIVE,
    PENDING_SECONDS,
    UNKNOWN,
    ThreadRef,
    canonical_thread,
    score_rank,
    union_comments,
)

SessionHydrator = Callable[[list[ThreadRef]], Mapping[str, dict]]

COMMENTS_PER_THREAD = 10
# Order the served-path detail names its paths in.
_PATH_ORDER = ("session", "host", "web", "listing", "arctic", "sc")


@dataclass
class Hydration:
    """What :func:`hydrate` produced.

    ``arctic_state`` is ``"ok"``, ``"throttled"``, ``"down"``, or ``"unused"``
    when no ref needed the archive. ``detail`` is a short served-path note
    such as ``web+arctic served 14`` or ``web served 3; arctic down``.
    """

    items: list[dict]
    detail: str
    arctic_state: str
    # Archive comment-tree requests this call: trees attached, and requests
    # that failed (an empty tree is neither).
    comment_fills: int = 0
    comment_failures: int = 0
    comment_throttles: int = 0  # failures that were a 422/429 slow-down


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditHydrate] {msg}\n")
    sys.stderr.flush()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _age_seconds(created_utc: Any, now: float) -> Optional[float]:
    try:
        return now - float(created_utc)
    except (TypeError, ValueError):
        return None


def _fresh(created_utc: Any, now: float) -> bool:
    age = _age_seconds(created_utc, now)
    return age is not None and age < PENDING_SECONDS


def _merge_refs(refs: Iterable[ThreadRef]) -> dict[str, ThreadRef]:
    """One ref per thread id, in first-seen order, provenance unioned."""
    by_id: dict[str, ThreadRef] = {}
    for ref in refs:
        if not isinstance(ref, ThreadRef) or not ref.post_id:
            continue
        current = by_id.get(ref.post_id)
        if current is None:
            by_id[ref.post_id] = ThreadRef(
                post_id=ref.post_id, url=ref.url, subreddit=ref.subreddit,
                provenance=set(ref.provenance), matched_comment=ref.matched_comment,
                title=ref.title, date=ref.date,
            )
            continue
        current.provenance |= ref.provenance
        current.subreddit = current.subreddit or ref.subreddit
        current.title = current.title or ref.title
        current.date = current.date or ref.date
        current.matched_comment = current.matched_comment or ref.matched_comment
        if ref.subreddit and "/r/" not in current.url:
            current.url = ref.url
    return by_id


def label_archive_post(post: dict, now: float) -> None:
    """Label an archive row's score: pending under 36 h old, else archive."""
    if _fresh(post.get("created_utc"), now):
        post["score"] = None
        post.setdefault("engagement", {})["score"] = None
        post["engagement_source"] = ARCTIC_PENDING
    else:
        post["engagement_source"] = ARCTIC


def _from_search_result(ref: ThreadRef) -> dict:
    return {
        "id": "",
        "title": ref.title,
        "url": ref.url,
        "score": None,
        "num_comments": None,
        "subreddit": ref.subreddit,
        "created_utc": None,
        "author": "",
        "selftext": "",
        "date": ref.date,
        "engagement": {"score": None, "num_comments": None, "upvote_ratio": None},
        "relevance": 0.0,
        "why_relevant": "Reddit thread (search result)",
        "metadata": {"post_id": ref.post_id},
        "engagement_source": UNKNOWN,
    }


def _stamp_depth(comments: Any) -> list[dict]:
    """Comments with an int ``depth``; a path that reports none lists top-level comments."""
    out: list[dict] = []
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        try:
            comment["depth"] = int(comment.get("depth") or 0)
        except (TypeError, ValueError):
            comment["depth"] = 0
        out.append(comment)
    return out


def _stamp_uploader(post: dict) -> None:
    """Mark the original poster's own comments so they never quote as community.

    Set while the post author is still on the item; the normalized item drops it.
    """
    author = str(post.get("author") or "").strip().lower()
    if not author or author in ("[deleted]", "[removed]"):
        return
    for comment in post.get("top_comments") or []:
        if isinstance(comment, dict) and not isinstance(comment.get("is_uploader"), bool):
            name = str(comment.get("author") or "").strip().lower()
            if name:
                comment["is_uploader"] = name == author


def _finish(post: dict, ref: ThreadRef, topic: str) -> dict:
    """Canonical URL, subreddit, provenance and topic relevance on one item."""
    # The subreddit Reddit or the archive reported is authoritative; a ref's
    # URL (a host-supplied one included) can name the wrong community.
    subreddit = str(post.get("subreddit") or "") or ref.subreddit
    canon = canonical_thread(
        f"https://www.reddit.com/r/{subreddit}/comments/{ref.post_id}/" if subreddit else ref.post_id
    )
    post["url"] = canon.url if canon else ref.url
    post["subreddit"] = subreddit
    post.setdefault("metadata", {})["post_id"] = ref.post_id
    post["provenance"] = sorted(ref.provenance)
    matched = ref.matched_comment
    if matched is not None and matched.created_utc:
        from . import dates

        post["matched_comment"] = {
            "id": matched.comment_id,
            "date": dates.timestamp_to_date(matched.created_utc),
            "url": matched.permalink,
        }
    engagement = post.setdefault("engagement", {})
    engagement.setdefault("score", post.get("score"))
    engagement.setdefault("num_comments", post.get("num_comments"))
    post.setdefault("engagement_source", LIVE)
    if post.get("top_comments"):
        post["top_comments"] = _stamp_depth(post["top_comments"])
        _stamp_uploader(post)
    if topic and post.get("title"):
        from .relevance import token_overlap_relevance

        post["relevance"] = round(token_overlap_relevance(topic, post["title"]), 3)
    return post


def _day_in_window(day: Optional[str], from_date: Optional[str], to_date: Optional[str]) -> bool:
    return bool(day) and (not from_date or day >= from_date) and (not to_date or day <= to_date)


def _in_window(item: dict, from_date: Optional[str], to_date: Optional[str]) -> bool:
    """A thread is in the window by its own date, or by the date of the
    comment a comment search matched in it (an older thread with a fresh
    matched comment stays)."""
    day = item.get("date")
    if not day:
        return True
    if _day_in_window(day, from_date, to_date):
        return True
    matched = item.get("matched_comment") or {}
    return _day_in_window(matched.get("date"), from_date, to_date)


def _discussion(item: dict) -> float:
    engagement = item.get("engagement") or {}
    return sum(
        float(engagement.get(key)) for key in ("num_comments", "score")
        if _is_number(engagement.get(key))
    )


def _attach_comments(
    items: list[dict], depth: str, deadline: Optional[float], now: float,
) -> tuple[int, int, int]:
    """Archive comment trees for the most-discussed hydrated threads that lack
    comments, within the lane's comment budget. Returns (filled, failed,
    throttled), where throttled counts the failures that were the archive
    asking to slow down."""
    from . import reddit_keyless

    limit = reddit_shreddit.ENRICH_LIMITS.get(depth, reddit_shreddit.ENRICH_LIMITS["default"])
    targets = sorted(
        (
            item for item in items
            if item.get("engagement_source") != UNKNOWN
            and not item.get("top_comments")
            and (item.get("engagement") or {}).get("num_comments") != 0
        ),
        key=_discussion, reverse=True,
    )[:limit]
    if not targets:
        return 0, 0, 0
    end = time.monotonic() + reddit_keyless.FINAL_ENRICH_BUDGET
    if deadline is not None:
        end = min(end, deadline)
    filled = failed = throttled = 0
    for item in targets:
        if end - time.monotonic() < 1:
            _log("comment budget spent; remaining threads keep no comments")
            break
        rows = reddit_arctic.fetch_comment_tree(item["metadata"]["post_id"], deadline=end)
        if rows is None:
            failed += 1
            throttled += bool(getattr(reddit_arctic, "last_tree_throttled", False))
            if failed >= 2 and not filled:
                _log("archive comments failing; remaining threads keep no comments")
                break
            continue
        for row in rows:
            if _fresh(row.get("created_utc"), now):
                row["score"] = None
                row["score_pending"] = True
        kept = union_comments([], rows)[:COMMENTS_PER_THREAD]
        if kept:
            item["top_comments"] = kept
            item["comments_source"] = ARCTIC
            _stamp_uploader(item)
            filled += 1
    return filled, failed, throttled


def in_window(item: dict, from_date: Optional[str], to_date: Optional[str]) -> bool:
    """Whether a hydrated thread is in the window (see ``_in_window``)."""
    return _in_window(item, from_date, to_date)


def attach_comments(
    items: list[dict], depth: str = "default", deadline: Optional[float] = None,
    now: Optional[float] = None,
) -> int:
    """Archive comments for ``items`` hydrated with ``comments=False``, within
    the lane's comment budget. Returns how many threads gained comments."""
    filled, _failed, _throttled = _attach_comments(items, depth, deadline, time.time() if now is None else now)
    return filled


def on_topic_count(items: Iterable[dict], topic: str) -> int:
    """Threads whose title names the topic. A pending score does not matter."""
    items = list(items)
    if not topic:
        return len(items)
    from .reddit_discover import names_topic

    return sum(names_topic(str(item.get("title") or ""), topic) for item in items)


def merge_post(existing: dict, incoming: dict) -> None:
    """Fold another layer's copy of a thread into ``existing``, field by field."""
    mine = existing.setdefault("engagement", {})
    theirs = incoming.get("engagement") or {}
    if score_rank(incoming.get("engagement_source")) > score_rank(existing.get("engagement_source")):
        mine["score"] = theirs.get("score")
        existing["score"] = theirs.get("score")
        existing["engagement_source"] = incoming.get("engagement_source") or LIVE
    count = theirs.get("num_comments")
    if _is_number(count) and not (_is_number(mine.get("num_comments")) and mine["num_comments"] >= count):
        mine["num_comments"] = count
        existing["num_comments"] = count
    if incoming.get("top_comments"):
        sources = {existing.get("comments_source"), incoming.get("comments_source")} - {None}
        existing["top_comments"] = union_comments(
            existing.get("top_comments") or [], incoming["top_comments"],
        )[:COMMENTS_PER_THREAD]
        existing["comments_source"] = "+".join(sorted(sources)) or LIVE
    for key in ("title", "date", "selftext", "author", "matched_comment"):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]
    existing["provenance"] = sorted(set(existing.get("provenance") or []) | set(incoming.get("provenance") or []))


def _merge_sc(
    items: list[dict], sc_items: Iterable[dict], topic: str,
    from_date: Optional[str], to_date: Optional[str],
) -> int:
    by_id = {item["metadata"]["post_id"]: item for item in items}
    added = 0
    for raw in sc_items or []:
        if not isinstance(raw, dict):
            continue
        ref = canonical_thread(str(raw.get("url") or ""))
        if ref is None:
            continue
        ref.provenance.add("sc")
        post = dict(raw)
        post.setdefault("engagement_source", LIVE)
        if post.get("top_comments"):
            post.setdefault("comments_source", "sc")
        post = _finish(post, ref, topic)
        if not _in_window(post, from_date, to_date):
            continue
        if ref.post_id in by_id:
            merge_post(by_id[ref.post_id], post)
        else:
            by_id[ref.post_id] = post
            items.append(post)
        added += 1
    return added


def _detail(items: list[dict], served: set[str], arctic_state: str) -> str:
    paths = set(served)
    for item in items:
        paths.update(p for p in item.get("provenance") or [] if p in _PATH_ORDER)
    named = "+".join(p for p in _PATH_ORDER if p in paths) or "reddit"
    detail = f"{named} served {len(items)}"
    if arctic_state in ("down", "throttled"):
        detail += f"; arctic {arctic_state}"
    return detail


def hydrate(
    refs: Iterable[ThreadRef],
    *,
    topic: str = "",
    depth: str = "default",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    comments: bool = True,
    session_hydrator: Optional[SessionHydrator] = None,
    sc_fetch: Optional[Callable[[], Iterable[dict]]] = None,
    sc_min_items: int = env.REDDIT_SC_MIN_ITEMS_DEFAULT,
    deadline: Optional[float] = None,
    now: Optional[float] = None,
) -> Hydration:
    """Hydrate ``refs`` through the chain described in the module docstring.

    ``from_date``/``to_date`` (YYYY-MM-DD) keep threads whose post date is in
    the window; a thread with no known date stays. ``sc_fetch`` is passed only
    by a caller that holds a ScrapeCreators key, and returns Reddit lane items.
    ``deadline`` is a ``time.monotonic()`` bound; ``now`` is the epoch clock
    used for the pending-score age.
    """
    now = time.time() if now is None else now
    by_id = _merge_refs(refs)
    posts: dict[str, dict] = {}
    served: set[str] = set()
    dropped = 0

    if session_hydrator is not None and by_id:
        try:
            got = session_hydrator(list(by_id.values())) or {}
        except Exception as exc:  # a session failure must not stop the free chain
            _log(f"session hydration failed: {type(exc).__name__}: {exc}")
            got = {}
        for pid, post in got.items():
            if pid in by_id and isinstance(post, dict) and post.get("_drop"):
                # The session's privacy rules or a thread 403 exclude it:
                # no other hydrator may bring it back.
                del by_id[pid]
                dropped += 1
                continue
            if pid in by_id and isinstance(post, dict):
                post = dict(post)
                post.setdefault("engagement_source", LIVE)
                if post.get("top_comments"):
                    post.setdefault("comments_source", "session")
                posts[pid] = post
        if posts:
            served.add("session")

    missing = [pid for pid in by_id if pid not in posts]
    arctic_state = "unused"
    # An archive already throttled or down this run is not asked again.
    from . import reddit_health

    skipped = reddit_health.skip_reason("arctic") if missing else None
    if skipped:
        arctic_state = "throttled" if skipped == reddit_health.THROTTLED else "down"
        _log(f"arctic skipped ({skipped}); {len(missing)} threads keep search-result data")
    elif missing:
        found, arctic_state = reddit_arctic.fetch_posts_by_ids(missing, deadline=deadline)
        for pid, post in found.items():
            if pid in by_id:
                label_archive_post(post, now)
                posts[pid] = post
        if found:
            served.add("arctic")
        unfilled = len(missing) - len(found)
        if unfilled:
            _log(f"{unfilled} of {len(missing)} threads kept search-result data only (arctic {arctic_state})")

    # A thread only the logged-in session found, that neither the session's
    # thread JSON nor the public archive filled, may be from a community a
    # logged-out reader cannot see: it is not shown on search-result data.
    session_only = [
        pid for pid, ref in by_id.items() if pid not in posts and ref.provenance == {"session"}
    ]
    for pid in session_only:
        del by_id[pid]
    if session_only:
        _log(f"{len(session_only)} session-only threads had no public copy; left out")
    if dropped:
        _log(f"{dropped} threads left out by the session's privacy rules or a thread refusal")
    items = [
        _finish(posts.get(pid) or _from_search_result(ref), ref, topic)
        for pid, ref in by_id.items()
    ]
    items = [item for item in items if _in_window(item, from_date, to_date)]

    filled = failed = throttled = 0
    if comments and items and not skipped and arctic_state not in ("down", "throttled"):
        filled, failed, throttled = _attach_comments(items, depth, deadline, now)
        if filled:
            served.add("arctic")

    if sc_fetch is not None:
        on_topic = on_topic_count(items, topic)
        if on_topic < max(sc_min_items, 1):
            try:
                sc_items = list(sc_fetch() or [])
            except Exception as exc:  # the backup must never break the free result
                _log(f"ScrapeCreators backup failed: {type(exc).__name__}: {exc}")
                sc_items = []
            if _merge_sc(items, sc_items, topic, from_date, to_date):
                served.add("sc")

    return Hydration(
        items=items, detail=_detail(items, served, arctic_state), arctic_state=arctic_state,
        comment_fills=filled, comment_failures=failed, comment_throttles=throttled,
    )
