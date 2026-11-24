"""The opt-in logged-in Reddit session layer.

A user who consented (``REDDIT_BROWSER_CONSENT``, recorded by
``setup --reddit-session``) lends the engine their reddit.com login cookie,
read into memory from the one browser setup recorded. With it the engine
reads, never writes:

* post search (``type=posts``, a month window) through Reddit's search
  fragment, parsed by ``reddit_search.parse_page``;
* comment search (``type=comments``): each hit names its thread and carries
  the matched comment, which stays in the thread's quotes and whose date
  can keep an older thread in the window;
* community search (``type=communities``), then post search inside the top
  three communities;
* thread JSON (``/comments/<id>.json``) as the first hydrator in
  ``reddit_hydrate``: live scores and comments.

Nothing calls ``/api/``, a write endpoint, the home feed, the inbox or saved
items. The identity check is the first search page itself: a logged-in page
carries :data:`LOGGED_IN_MARKER`; every later page is checked again, so a
session that expires mid-run is caught. A logged-out page or a 403 on a
search page means Reddit no longer honors the session: the path is skipped
for six hours under this cookie's fingerprint (a new login clears it) and the
detail says ``session session-expired`` with a re-login hint, never "no
results". A 403 on one thread drops that thread only.

Privacy (applied to every item the session returns, whichever layer found
the thread): over-18 posts are dropped, private and quarantined subreddits
are dropped unless the user named the subreddit (``r/<name>`` in the topic,
or ``--dedicated-subreddits``), and only public fields are copied, so viewer
fields (votes, saves, reports, moderation state) never enter the pipeline.

The cookie is held by :class:`Session` only, never logged, never in an error
string, and never written anywhere. Each run caps session requests at
:data:`REQUEST_CAP`; comparison entities split it. Requests are paced by the
session's own limiter (``http.REDDIT_SESSION_LIMITER``) and memoized in the
session's own namespace. Never raises into the pipeline.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import html as _html
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import quote

from . import dates, env, http, log, reddit_enrich, reddit_health, reddit_listing, reddit_search, snippet
from .reddit_refs import LIVE, MatchedComment, ThreadRef, canonical_thread

PATH = "session"
BASE = "https://www.reddit.com"
LOGGED_IN_MARKER = "user-logged-in"
REQUEST_CAP = 60
# Post-search pages per depth; comments, communities and each in-community
# search take one page.
POST_PAGES = {"quick": 1, "default": 2, "deep": 3}
# Threads hydrated through thread JSON per lane call.
HYDRATE_LIMITS = {"quick": 8, "default": 15, "deep": 25}
COMMUNITY_SEARCHES = 3
COMMENTS_PER_THREAD = 10
# Two consecutive thread 403s mean the session, not the threads, is refused.
_EXPIRED_AFTER_FORBIDDEN = 2
_DISCOVER_CACHE_MAX = 64
_LOGGED_OUT_403_MARKERS = (
    "try logging in or creating an account",
    "your request has been blocked due to a network policy",
)
_PRIVATE_403_REASONS = frozenset({"private", "quarantined", "gated", "banned"})

COMMENT_UNIT_MARKER = 'view-events="search/view/comment"'
COMMUNITY_UNIT_MARKER = 'view-events="search/view/subreddit"'
NO_RESULTS_MARKER = reddit_search.NO_RESULTS_MARKER

# Page states.
OK = "ok"
EXPIRED = "session-expired"
THROTTLED = "throttled"
ERROR = "error"

# Fields of a thread's JSON that describe the viewer, not the thread. They are
# never copied; strip_viewer_fields removes them from a raw row.
VIEWER_FIELDS = (
    "likes", "saved", "clicked", "visited", "hidden", "user_reports", "mod_reports",
    "mod_reason_by", "mod_reason_title", "mod_note", "approved_by", "approved_at_utc",
    "banned_by", "banned_at_utc", "removed_by", "num_reports", "report_reasons",
    "can_mod_post", "can_gild", "author_flair_text", "author_flair_richtext",
    "author_flair_css_class", "author_flair_template_id", "author_flair_type",
    "user_reports_dismissed", "mod_reports_dismissed", "ignore_reports",
)
# Only these subreddit_type values are readable by logged-out users; any other
# value (private, gold_only, gold_restricted, employees_only, user, or missing)
# is treated as non-public.
_PUBLIC_TYPES = frozenset({"public", "restricted", "archived"})

# The per-lane-call budget owner: the entity (topic) a lane call serves.
_entity: contextvars.ContextVar[str] = contextvars.ContextVar("last30days_reddit_session_entity", default="")
_current: contextvars.ContextVar[Optional["Session"]] = contextvars.ContextVar(
    "last30days_reddit_session", default=None,
)
_named: contextvars.ContextVar[Optional[frozenset[str]]] = contextvars.ContextVar(
    "last30days_reddit_session_named", default=None,
)

_run_lock = threading.Lock()
_run: dict[str, Any] = {"resolved": False, "session": None, "status": None}


def _log(msg: str) -> None:
    log.source_log("RedditSession", msg, tty_only=False)


def cookie_fp(cookie: str) -> str:
    """The first 16 hex characters of the cookie's sha256: names a login, reveals nothing."""
    return hashlib.sha256(str(cookie or "").encode("utf-8")).hexdigest()[:16]


def strip_viewer_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """``row`` without the fields that describe the viewer."""
    return {k: v for k, v in dict(row or {}).items() if k not in VIEWER_FIELDS}


def _sub_key(name: Any) -> str:
    return str(name or "").strip().removeprefix("r/").strip("/").lower()


def named_subreddits(topic: str, dedicated: Iterable[str] = ()) -> frozenset[str]:
    """Subreddits the user named: ``r/<name>`` in the topic, plus ``--dedicated-subreddits``."""
    named = {m.lower() for m in re.findall(r"(?<![A-Za-z0-9_])r/([A-Za-z0-9_]{2,21})", topic or "")}
    named |= {_sub_key(s) for s in dedicated or []}
    return frozenset(named - {""})


def _wait_event(
    event: threading.Event,
    deadline: Optional[float],
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Wait until ``event`` is set, returning False on deadline or cancel."""
    if event.is_set():
        return True
    if cancel is not None and cancel.is_set():
        return False
    if cancel is None:
        if deadline is None:
            return event.wait()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return event.is_set()
        return event.wait(timeout=remaining)
    while not event.is_set():
        if cancel.is_set():
            return False
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return event.is_set()
            step = min(0.05, remaining)
        else:
            step = 0.05
        if event.wait(timeout=step):
            return True
    return True


@dataclass
class _InflightFetch:
    done: threading.Event = field(default_factory=threading.Event)
    status: Optional[int] = None
    body: Optional[str] = None
    shareable: bool = False


@dataclass
class _InflightDiscovery:
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional["Discovery"] = None
    cacheable: bool = False


class Session:
    """One run's consented session. Holds the cookie; never shows it."""

    def __init__(
        self,
        cookie: str,
        *,
        browser: str = "",
        entity_count: int = 1,
        sc_floor: int = env.REDDIT_SC_MIN_ITEMS_DEFAULT,
        named: Iterable[str] = (),
        limiter: Optional[http.RateLimiter] = None,
        cap: int = REQUEST_CAP,
    ):
        self._cookie = str(cookie)
        self.fp = cookie_fp(cookie)
        self.browser = browser
        self.entity_count = max(1, int(entity_count or 1))
        self.sc_floor = sc_floor
        self.named = frozenset(_sub_key(s) for s in named) - {""}
        self.limiter = limiter
        self.cap = cap
        self.state = OK
        self.verified = False
        self.public_subs: set[str] = set()
        self._lock = threading.Lock()
        self._total = 0
        self._per_entity: dict[str, int] = {}
        self._fetch_inflight: dict[tuple[str, str], _InflightFetch] = {}
        self._discover_lock = threading.Lock()
        self._discover_cache: dict[tuple, "Discovery"] = {}
        self._discover_inflight: dict[tuple, _InflightDiscovery] = {}

    def __repr__(self) -> str:  # never the cookie
        return f"<reddit Session fp={self.fp} state={self.state}>"

    __str__ = __repr__

    @property
    def requests(self) -> int:
        with self._lock:
            return self._total

    def entity_budget(self) -> int:
        """Requests each comparison entity may spend: an equal share, at least 1."""
        return max(1, self.cap // self.entity_count)

    def _take(self, entity: str) -> bool:
        with self._lock:
            if self._total >= self.cap:
                return False
            if self._per_entity.get(entity, 0) >= self.entity_budget():
                return False
            self._total += 1
            self._per_entity[entity] = self._per_entity.get(entity, 0) + 1
            return True

    def _refund(self, entity: str) -> None:
        with self._lock:
            self._total -= 1
            self._per_entity[entity] = self._per_entity.get(entity, 1) - 1

    @property
    def alive(self) -> bool:
        return self.state == OK

    def fetch(
        self,
        url: str,
        *,
        accept: str = "text/html",
        deadline: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        """One session GET within the run's cap: ``(status, body)``.

        ``(None, None)`` when the cap, deadline, or cancel event leaves no
        room. Concurrent callers for the same ``(url, accept)`` coalesce onto
        the in-flight request at zero extra budget cost; memo hits also cost
        nothing.
        """
        key = (url, accept)
        for _round in range(2):
            if cancel is not None and cancel.is_set():
                return None, None
            if deadline is not None and deadline - time.monotonic() < 1:
                return None, None
            with self._lock:
                inflight = self._fetch_inflight.get(key)
                owner = inflight is None
                if owner:
                    inflight = _InflightFetch()
                    self._fetch_inflight[key] = inflight
            if not owner:
                if not _wait_event(inflight.done, deadline, cancel):
                    return None, None
                if inflight.shareable:
                    return inflight.status, inflight.body
                continue
            try:
                entity = _entity.get()
                if not self._take(entity):
                    return None, None
                status, body, from_memo = http.reddit_session_get(
                    url, f"reddit_session={self._cookie}", accept=accept,
                    deadline_monotonic=deadline, limiter=self.limiter,
                )
                if from_memo:
                    self._refund(entity)
                inflight.status = status
                inflight.body = body
                inflight.shareable = status in (200, 403, 429)
                return status, body
            finally:
                with self._lock:
                    if self._fetch_inflight.get(key) is inflight:
                        self._fetch_inflight.pop(key, None)
                inflight.done.set()
        return None, None

    def room(self) -> bool:
        entity = _entity.get()
        with self._lock:
            return self._total < self.cap and self._per_entity.get(entity, 0) < self.entity_budget()

    def mark_public_sub(self, subreddit: Any) -> None:
        sub_key = _sub_key(subreddit)
        if sub_key:
            with self._lock:
                self.public_subs.add(sub_key)

    def is_public_sub(self, subreddit: Any) -> bool:
        sub_key = _sub_key(subreddit)
        if not sub_key:
            return False
        with self._lock:
            return sub_key in self.public_subs


# --- run-level session ---------------------------------------------------------

def reset_run_state() -> None:
    """Forget this run's session (call once per command, and from tests)."""
    with _run_lock:
        _run.update(resolved=False, session=None, status=None)
    http.reset_reddit_session_memo()


def gate(config: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the session may be used this run (reads no cookie)."""
    return env.reddit_session_gate(dict(config or {}))


def status() -> Optional[str]:
    """Why this run has no session after a read attempt, or None."""
    with _run_lock:
        return _run.get("status")


def session_for(
    config: Mapping[str, Any], *, reader: Optional[Callable[..., tuple]] = None, topic: str = "",
) -> Optional[Session]:
    """This run's session, reading the cookie on first use. None when the gate
    refuses, the browser has no Reddit login, or a persisted skip for this
    login is in force."""
    allowed, _why = gate(config)
    if not allowed:
        return None
    with _run_lock:
        if _run["resolved"]:
            return _run["session"]
        _run["resolved"] = True
        cookie, read_status = (reader or env.read_reddit_session_cookie)(dict(config))
        if not cookie:
            _run["status"] = read_status
            browser = str(config.get(env.REDDIT_FROM_BROWSER_VAR) or "")
            hint = {
                "not-logged-in": f"no reddit.com login in {browser}; log in to reddit.com there",
                "permission-denied": f"{browser} cookie access was denied",
                "unsupported-encryption": f"{browser} cookies use an encryption this engine cannot read",
            }.get(read_status, f"could not read the reddit.com login from {browser}")
            _log(f"session off this run: {hint}")
            return None
        if "_reddit_named_subreddits" in config:
            session_named = named_subreddits("", config.get("_reddit_named_subreddits") or ())
        else:
            session_named = named_subreddits(topic, config.get("_dedicated_subreddits") or [])
        session = Session(
            cookie,
            browser=str(config.get(env.REDDIT_FROM_BROWSER_VAR) or ""),
            entity_count=int(config.get("_comparison_entity_count") or 1),
            sc_floor=env.reddit_sc_min_items(dict(config)),
            named=session_named,
        )
        skip = reddit_health.skip_reason(PATH, cookie_fp=session.fp)
        if skip:
            reason = (reddit_health.entries().get(PATH) or {}).get("reason") or ""
            session.state = EXPIRED if reason.startswith(EXPIRED) else reddit_health.SKIPPED_CACHED
            _log(f"session skipped this run ({session.state}); a new reddit.com login or doctor clears it")
        _run["session"] = session
        return session


@contextlib.contextmanager
def lane(
    session: Optional[Session],
    entity: str = "",
    *,
    named: Optional[Iterable[str]] = None,
):
    """Make ``session`` the Reddit lane's session in this context, spending
    the budget of ``entity`` (the topic the lane call serves).

    When ``named`` is given, it overrides ``session.named`` in this context so
    comparison entities sharing a session never inherit each other's
    private/quarantined subreddit allowlist.
    """
    token = _current.set(session)
    entity_token = _entity.set(entity or "")
    override = (frozenset(_sub_key(s) for s in named) - {""}) if named is not None else None
    named_token = _named.set(override)
    try:
        yield session
    finally:
        _named.reset(named_token)
        _entity.reset(entity_token)
        _current.reset(token)


def current() -> Optional[Session]:
    return _current.get()


def _effective_named(session: Session) -> frozenset[str]:
    override = _named.get()
    return session.named if override is None else override


def relogin_hint(session: Session) -> str:
    return f"log in to reddit.com in {session.browser or 'your browser'}"


# --- page parsers -----------------------------------------------------------------

def page_state(status: Optional[int], body: Optional[str]) -> str:
    """What one session search page says about the session."""
    if status == 200:
        return OK if LOGGED_IN_MARKER in (body or "") else EXPIRED
    if status == 403:
        # A valid session gets this page; a refusal with the cookie attached
        # is Reddit no longer honoring it.
        return EXPIRED
    if status == 429:
        return THROTTLED
    return ERROR


_TAG = re.compile(r"<search-telemetry-tracker\b[^>]*>")
_CONTEXT = re.compile(r'\bdata-faceplate-tracking-context="([^"]*)"')
_TIMEAGO = re.compile(r'<faceplate-timeago\b[^>]*?\bts="([^"]+)"')
_VOTES = re.compile(r'<faceplate-number\b[^>]*?\bnumber="(\d+)"[^>]*>\s*</faceplate-number>\s*votes?')
_COUNTER = re.compile(r'<faceplate-number\b[^>]*?\bnumber="(\d+)"[^>]*>\s*</faceplate-number>\s*(vote|comment)')
_HREF = re.compile(r'\bhref="([^"]*)"')
_TAGS = re.compile(r"<[^>]+>")


def _units(text: str, marker: str) -> list[tuple[int, dict]]:
    """(offset, tracking context) of each result unit carrying ``marker``."""
    out = []
    for m in _TAG.finditer(text or ""):
        tag = m.group(0)
        if marker not in tag:
            continue
        ctx_match = _CONTEXT.search(tag)
        if not ctx_match:
            continue
        try:
            ctx = json.loads(_html.unescape(ctx_match.group(1)))
        except (TypeError, ValueError):
            continue
        if isinstance(ctx, dict):
            out.append((m.start(), ctx))
    return out


def _flags(ctx: Mapping[str, Any]) -> tuple[bool, bool]:
    """(over_18, quarantined) from a tracking context."""
    post = ctx.get("post") if isinstance(ctx.get("post"), dict) else {}
    sub = ctx.get("subreddit") if isinstance(ctx.get("subreddit"), dict) else {}
    return bool(post.get("nsfw") or sub.get("nsfw")), bool(sub.get("quarantined"))


def _plain(fragment: str) -> str:
    return " ".join(_html.unescape(_TAGS.sub(" ", fragment or "")).split())


def unrecognized_comments(text: str) -> Optional[str]:
    """None for a comment-search page (with hits or Reddit's no-results unit), else why not."""
    if NO_RESULTS_MARKER in (text or ""):
        return None
    if COMMENT_UNIT_MARKER in (text or ""):
        return None if parse_comments(text) else "Reddit comment results no longer parse"
    return "not a Reddit comment search page"


def unrecognized_communities(text: str) -> Optional[str]:
    """None for a community-search page, else why not."""
    if NO_RESULTS_MARKER in (text or ""):
        return None
    if COMMUNITY_UNIT_MARKER in (text or ""):
        return None if parse_communities(text) else "Reddit community results no longer parse"
    return "not a Reddit community search page"


def parse_comments(text: str) -> list[dict[str, Any]]:
    """Comment-search hits: the comment, its date and votes, and its thread."""
    text = text or ""
    units = _units(text, COMMENT_UNIT_MARKER)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, (start, ctx) in enumerate(units):
        comment = ctx.get("comment") if isinstance(ctx.get("comment"), dict) else {}
        post = ctx.get("post") if isinstance(ctx.get("post"), dict) else {}
        sub = ctx.get("subreddit") if isinstance(ctx.get("subreddit"), dict) else {}
        comment_id = str(comment.get("id") or "").removeprefix("t1_").lower()
        post_id = str(post.get("id") or comment.get("post_id") or "").removeprefix("t3_").lower()
        if not comment_id or not post_id or comment_id in seen:
            continue
        seen.add(comment_id)
        end = units[i + 1][0] if i + 1 < len(units) else len(text)
        chunk = text[start:end]
        body_at = chunk.find(f'id="search-comment-t1_{comment_id}-post-rtjson-content"')
        if body_at < 0:
            continue
        body_end = chunk.find("</shreddit-stop-propagation>", body_at)
        body = _plain(chunk[chunk.find(">", body_at) + 1: body_end if body_end > 0 else None])
        content_at = chunk.find('data-testid="search-comment-content"')
        stamps = _TIMEAGO.findall(chunk[content_at:] if content_at >= 0 else chunk)
        comment_ts = stamps[0] if stamps else None
        post_stamps = _TIMEAGO.findall(chunk[:content_at]) if content_at > 0 else []
        counter_at = chunk.find('data-testid="search-counter-row"')
        votes = _VOTES.search(chunk, body_at, counter_at if counter_at > body_at else len(chunk))
        counts: dict[str, int] = {}
        if counter_at >= 0:
            for number, label in _COUNTER.findall(chunk[counter_at:]):
                counts.setdefault(label, int(number))
        permalink = ""
        for href in _HREF.findall(chunk):
            href = _html.unescape(href).split("?", 1)[0]
            if f"/comments/{post_id}/" in href and href.rstrip("/").endswith(comment_id):
                permalink = href if href.startswith("http") else f"{BASE}{href}"
                break
        over_18, quarantined = _flags(ctx)
        hits.append({
            "comment_id": comment_id,
            "post_id": post_id,
            "title": str(post.get("title") or ""),
            "subreddit": str(sub.get("name") or ""),
            "body": body,
            "comment_created_utc": reddit_listing._to_epoch(comment_ts),
            "comment_date": reddit_listing._to_date(comment_ts),
            "comment_score": int(votes.group(1)) if votes else None,
            "post_date": reddit_listing._to_date(post_stamps[0]) if post_stamps else None,
            "post_score": counts.get("vote"),
            "num_comments": counts.get("comment"),
            "permalink": permalink,
            "over_18": over_18,
            "quarantined": quarantined,
        })
    return hits


def parse_communities(text: str) -> list[dict[str, Any]]:
    """Community-search hits, in page order: name, over_18, quarantined."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _start, ctx in _units(text or "", COMMUNITY_UNIT_MARKER):
        sub = ctx.get("subreddit") if isinstance(ctx.get("subreddit"), dict) else {}
        name = str(sub.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "over_18": bool(sub.get("nsfw")), "quarantined": bool(sub.get("quarantined"))})
    return out


def post_flags(text: str) -> dict[str, tuple[bool, bool]]:
    """(over_18, quarantined) per post id on a post-search page."""
    out: dict[str, tuple[bool, bool]] = {}
    for _start, ctx in reddit_search._post_units(text or ""):
        out[str(ctx["post"]["id"]).removeprefix("t3_").lower()] = _flags(ctx)
    return out


# --- discovery ----------------------------------------------------------------------

@dataclass
class Discovery:
    """What one session discovery call found."""

    refs: list[ThreadRef] = field(default_factory=list)
    # Live search-hit copies, by post id, for threads thread JSON did not reach.
    hits: dict[str, dict] = field(default_factory=dict)
    # The matched comment of a comment hit, stored the way an item stores comments.
    comments: dict[str, dict] = field(default_factory=dict)
    state: Optional[str] = None
    dropped_private: int = 0
    pages: int = 0


def _record(session: Session, state: str, reason: str = "") -> None:
    """Note a session page state: a refusal persists under this login's fingerprint."""
    if state == EXPIRED:
        session.state = EXPIRED
        reddit_health.record(PATH, reddit_health.BLOCKED, f"{EXPIRED}: {reason}"[:120], cookie_fp=session.fp)
    elif state == THROTTLED:
        session.state = THROTTLED
        reddit_health.record(PATH, reddit_health.THROTTLED, reason)
    elif state == OK and not session.verified:
        session.verified = True
        reddit_health.record(PATH, reddit_health.OK, "session search", cookie_fp=session.fp)


def _search_page(
    session: Session,
    url: str,
    deadline: Optional[float],
    cancel: Optional[threading.Event] = None,
) -> tuple[str, Optional[str]]:
    """One search page: ``(state, body)``. A 429 is retried once."""
    status, body = session.fetch(url, deadline=deadline, cancel=cancel)
    if status is None and body is None:
        return ERROR, None
    state = page_state(status, body)
    if state == THROTTLED:
        status, body = session.fetch(url, deadline=deadline, cancel=cancel)
        state = page_state(status, body) if status is not None else THROTTLED
    if state == OK:
        _record(session, OK)
    elif state in (EXPIRED, THROTTLED):
        _record(session, state, f"HTTP {status}" if status != 200 else "logged-out page")
    return state, body


def _allowed(session: Session, subreddit: str, over_18: bool, quarantined: bool, private: bool = False) -> bool:
    if over_18:
        return False
    if (quarantined or private) and _sub_key(subreddit) not in _effective_named(session):
        return False
    return True


def _post_hits(session: Session, body: str, topic: str, found: Discovery) -> None:
    flags = post_flags(body)
    posts, _cursor = reddit_search.parse_page(body, topic)
    for post in posts:
        pid = post["metadata"]["post_id"]
        over_18, quarantined = flags.get(pid, (False, False))
        if not _allowed(session, post.get("subreddit", ""), over_18, quarantined):
            found.dropped_private += 1
            continue
        ref = canonical_thread(post.get("url") or pid)
        if ref is None:
            continue
        ref.provenance.add(PATH)
        ref.title = post.get("title") or ""
        ref.date = post.get("date")
        found.refs.append(ref)
        post["engagement_source"] = LIVE
        post["why_relevant"] = "Reddit search (logged-in)"
        found.hits.setdefault(pid, post)


def _comment_hits(session: Session, body: str, found: Discovery) -> None:
    for hit in parse_comments(body):
        if not _allowed(session, hit["subreddit"], hit["over_18"], hit["quarantined"]):
            found.dropped_private += 1
            continue
        url = f"{BASE}/r/{hit['subreddit']}/comments/{hit['post_id']}/" if hit["subreddit"] else hit["post_id"]
        ref = canonical_thread(url)
        if ref is None:
            continue
        ref.provenance.add(PATH)
        ref.title = hit["title"]
        ref.date = hit["post_date"]
        ref.matched_comment = MatchedComment(
            comment_id=hit["comment_id"], created_utc=hit["comment_created_utc"],
            permalink=hit["permalink"],
        )
        found.refs.append(ref)
        found.comments.setdefault(hit["post_id"], {
            "id": hit["comment_id"],
            "score": hit["comment_score"],
            "date": hit["comment_date"],
            "author": "",
            "excerpt": snippet.cut_quote(hit["body"]),
            "url": hit["permalink"],
            "depth": 0,
            "matched": True,
        })


def discover(
    session: Session,
    topic: str,
    *,
    depth: str = "default",
    deadline: Optional[float] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    cancel: Optional[threading.Event] = None,
) -> Discovery:
    """Session search for ``topic``: posts, comments, communities, then posts
    inside the top communities. Stops at the first page that says the session
    is gone, at the request cap, at ``deadline``, or on ``cancel``. Concurrent
    callers on the same session for the same ``(topic, depth, window, named)``
    share one in-flight run. Never raises."""
    if not session.alive:
        return Discovery(state=session.state)
    if cancel is not None and cancel.is_set():
        return Discovery(state=ERROR)
    try:
        window = reddit_search._time_filter(from_date, to_date)
    except Exception as exc:  # defensive: discovery must never raise
        _log(f"session search failed: {type(exc).__name__}")
        return Discovery(state=ERROR)
    key = (str(topic or ""), depth, window, _effective_named(session))
    for _round in range(2):
        if not session.alive:
            return Discovery(state=session.state)
        if cancel is not None and cancel.is_set():
            return Discovery(state=ERROR)
        with session._discover_lock:
            cached = session._discover_cache.get(key)
            if cached is not None:
                return copy.deepcopy(cached)
            slot = session._discover_inflight.get(key)
            owner = slot is None
            if owner:
                slot = _InflightDiscovery()
                session._discover_inflight[key] = slot
        if not owner:
            if not _wait_event(slot.done, deadline, cancel):
                return Discovery(state=ERROR)
            if slot.result is not None and (
                slot.cacheable or slot.result.state in (EXPIRED, THROTTLED)
            ):
                return copy.deepcopy(slot.result)
            continue
        found = Discovery()
        cancelled = False
        complete = False
        try:
            complete = _discover(session, topic, depth, deadline, found, window, cancel=cancel)
            cancelled = bool(cancel is not None and cancel.is_set())
        except Exception as exc:  # the session must never break the lane
            _log(f"session search failed: {type(exc).__name__}")
            found.state = found.state or ERROR
        finally:
            try:
                if cancelled:
                    found.state = ERROR
                elif found.state is None:
                    found.state = reddit_health.OK if found.refs else reddit_health.EMPTY
                cacheable = (
                    complete
                    and not cancelled
                    and found.pages > 0
                    and found.state in (reddit_health.OK, reddit_health.EMPTY)
                )
                snapshot = copy.deepcopy(found)
                slot.result = snapshot
                slot.cacheable = cacheable
                with session._discover_lock:
                    if cacheable and len(session._discover_cache) < _DISCOVER_CACHE_MAX:
                        session._discover_cache[key] = snapshot
            finally:
                with session._discover_lock:
                    if session._discover_inflight.get(key) is slot:
                        session._discover_inflight.pop(key, None)
                slot.done.set()
        if found.dropped_private:
            _log(f"dropped {found.dropped_private} over-18, private or quarantined results")
        return found
    return Discovery(state=ERROR)


def _discover(
    session: Session,
    topic: str,
    depth: str,
    deadline: Optional[float],
    found: Discovery,
    window: str = "month",
    *,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Run all discovery steps; return True only when every step finished cleanly."""
    cursor: Optional[str] = None
    for _page in range(POST_PAGES.get(depth, POST_PAGES["default"])):
        if cancel is not None and cancel.is_set():
            found.state = ERROR
            return False
        state, body = _search_page(
            session, reddit_search.search_url(topic, window, cursor=cursor), deadline, cancel=cancel,
        )
        if state != OK:
            found.state = state if state != ERROR or not found.refs else None
            return False
        found.pages += 1
        if reddit_search.unrecognized_body(body or ""):
            break
        _post_hits(session, body or "", topic, found)
        cursor = reddit_search.next_cursor(body or "")
        if not cursor:
            break
    if not session.room() or (cancel is not None and cancel.is_set()):
        return False
    state, body = _search_page(
        session, reddit_search.search_url(topic, window, kind="comments", sort="new"), deadline, cancel=cancel,
    )
    if state != OK:
        found.state = state if state in (EXPIRED, THROTTLED) else None
        return False
    found.pages += 1
    if not unrecognized_comments(body or ""):
        _comment_hits(session, body or "", found)
    if not session.room() or (cancel is not None and cancel.is_set()):
        return False
    state, body = _search_page(
        session, reddit_search.search_url(topic, kind="communities"), deadline, cancel=cancel,
    )
    if state != OK:
        found.state = state if state in (EXPIRED, THROTTLED) else None
        return False
    found.pages += 1
    communities = [] if unrecognized_communities(body or "") else parse_communities(body or "")
    chosen = [c["name"] for c in communities if _allowed(session, c["name"], c["over_18"], c["quarantined"])]
    for name in chosen[:COMMUNITY_SEARCHES]:
        if not session.room() or (cancel is not None and cancel.is_set()):
            return False
        state, body = _search_page(
            session, reddit_search.search_url(topic, window, subreddit=name), deadline, cancel=cancel,
        )
        if state != OK:
            found.state = state if state in (EXPIRED, THROTTLED) else None
            return False
        found.pages += 1
        if not reddit_search.unrecognized_body(body or ""):
            _post_hits(session, body or "", topic, found)
    return True


# --- thread JSON hydrator -------------------------------------------------------------

DROP = {"_drop": True}


def thread_url(post_id: str) -> str:
    return f"{BASE}/comments/{quote(post_id, safe='')}.json?limit=20&depth=1&raw_json=1&sort=top"


def thread_post(data: Any, session: Session) -> Optional[dict]:
    """A thread JSON answer as a Reddit lane item, ``DROP`` when privacy
    rules exclude it, or None when the answer is not a thread."""
    if not isinstance(data, list) or not data:
        return None
    try:
        raw = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    row = strip_viewer_fields(raw)
    subreddit = str(row.get("subreddit") or "")
    subreddit_type = str(row.get("subreddit_type") or "").strip().lower()
    is_public = subreddit_type in _PUBLIC_TYPES
    if not _allowed(session, subreddit, bool(row.get("over_18")), bool(row.get("quarantine")), private=not is_public):
        return DROP
    if is_public and not row.get("quarantine"):
        session.mark_public_sub(subreddit)
    parsed = reddit_enrich.parse_thread_data(data)
    comments = reddit_enrich.stored_top_comments(
        reddit_enrich.get_top_comments(parsed.get("comments") or [], COMMENTS_PER_THREAD)
    )
    for comment in comments:
        comment["depth"] = 0
    score = row.get("score")
    num_comments = row.get("num_comments")
    created = row.get("created_utc")
    post = {
        "id": "",
        "title": str(row.get("title") or ""),
        "url": f"{BASE}{row.get('permalink') or ''}" if row.get("permalink") else "",
        "score": score,
        "num_comments": num_comments,
        "subreddit": subreddit,
        "created_utc": created,
        "author": str(row.get("author") or ""),
        "selftext": str(row.get("selftext") or "")[:500],
        "date": dates.timestamp_to_date(created) if created else None,
        "engagement": {"score": score, "num_comments": num_comments, "upvote_ratio": row.get("upvote_ratio")},
        "relevance": 0.0,
        "why_relevant": "Reddit thread (logged-in)",
        "metadata": {"post_id": str(row.get("id")).lower()},
        "engagement_source": LIVE,
        "top_comments": comments,
    }
    if comments:
        post["comments_source"] = PATH
    return post


def _is_logged_out_403(body: Optional[str]) -> bool:
    lower = (body or "").lower()
    return any(marker in lower for marker in _LOGGED_OUT_403_MARKERS)


def _is_private_403(body: Optional[str]) -> bool:
    text = (body or "").strip()
    if not text.startswith("{"):
        return False
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return str(payload.get("reason") or "").strip().lower() in _PRIVATE_403_REASONS


def hydrator(
    session: Session,
    found: Optional[Discovery] = None,
    *,
    depth: str = "default",
    deadline: Optional[float] = None,
) -> Callable[[list[ThreadRef]], dict[str, dict]]:
    """The session hydrator ``reddit_hydrate.hydrate`` calls first.

    Thread JSON for up to ``HYDRATE_LIMITS[depth]`` refs, session search
    hits first. A thread it cannot reach keeps its live search-hit copy when
    its subreddit is known to be public this run; otherwise the free chain
    takes it. A thread 403 drops that thread only.
    """
    found = found or Discovery()
    limit = HYDRATE_LIMITS.get(depth, HYDRATE_LIMITS["default"])
    bound_named = _named.get()

    def run(refs: list[ThreadRef]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        named_token = _named.set(bound_named) if bound_named is not None and _named.get() is None else None
        try:
            if session.alive:
                ordered = sorted(refs, key=lambda r: (PATH not in r.provenance, r.matched_comment is None))
                forbidden: list[str] = []
                consecutive_403: list[str] = []
                logged_out_403: set[str] = set()
                answered = 0
                for ref in ordered[:limit]:
                    if not session.alive or not session.room():
                        break
                    status, body = session.fetch(thread_url(ref.post_id), accept="application/json", deadline=deadline)
                    if status is None and body is None:
                        break
                    if status == 403:
                        forbidden.append(ref.post_id)
                        if not _is_private_403(body):
                            consecutive_403.append(ref.post_id)
                            if _is_logged_out_403(body):
                                logged_out_403.add(ref.post_id)
                            if len(consecutive_403) >= _EXPIRED_AFTER_FORBIDDEN:
                                _record(session, EXPIRED, "thread JSON refused")
                                break
                        continue
                    if status == 429:
                        _record(session, THROTTLED, "thread JSON 429")
                        break
                    if status != 200:
                        continue
                    try:
                        post = thread_post(json.loads(body or ""), session)
                    except Exception as exc:
                        _log(f"thread JSON parse failed for {ref.post_id}: {type(exc).__name__}")
                        post = None
                    if post is None:
                        continue
                    answered += 1
                    consecutive_403.clear()
                    out[ref.post_id] = post
                if session.state == EXPIRED and consecutive_403:
                    # The session, not this trailing streak of threads, was refused:
                    # release only the trailing 403s to the free chain while preserving
                    # any earlier per-thread DROP verdicts.
                    trailing = set(consecutive_403)
                    forbidden = [pid for pid in forbidden if pid not in trailing]
                elif not answered and not session.verified and len(consecutive_403) == 1:
                    only_pid = consecutive_403[0]
                    # Only release a single 403 when Reddit positively indicated a
                    # whole-session refusal; otherwise fail closed with DROP.
                    if only_pid in logged_out_403:
                        _record(session, EXPIRED, "thread JSON refused")
                        forbidden = [pid for pid in forbidden if pid != only_pid]
                for pid in forbidden:
                    out[pid] = dict(DROP)
                for ref in refs:
                    pid = ref.post_id
                    post = out.get(pid)
                    matched = found.comments.get(pid)
                    if post is None and pid in found.hits and session.is_public_sub(found.hits[pid].get("subreddit")):
                        post = out[pid] = dict(found.hits[pid])
                    if post is None or post.get("_drop") or not matched:
                        continue
                    kept = post.setdefault("top_comments", [])
                    if not any(str(c.get("id") or "") == matched["id"] or matched["id"] in str(c.get("url") or "") for c in kept):
                        kept.append(dict(matched))
                        post["comments_source"] = post.get("comments_source") or PATH
                    else:
                        for c in kept:
                            if str(c.get("id") or "") == matched["id"] or matched["id"] in str(c.get("url") or ""):
                                c["matched"] = True
            # Omit logged-in matched_comment details on refs that thread_post
            # never verified (e.g. cap reached or session expired) before the
            # archive fallback runs, unless visibility is already confirmed or
            # the user explicitly named the subreddit.
            allowed_named = _effective_named(session)
            for ref in refs:
                post = out.get(ref.post_id)
                if post is not None and not post.get("_drop"):
                    if ref.matched_comment is not None:
                        ref.matched_comment_verified = True
                    continue
                if ref.matched_comment is not None:
                    if session.is_public_sub(ref.subreddit) or _sub_key(ref.subreddit) in allowed_named:
                        ref.matched_comment_verified = True
                    else:
                        ref.matched_comment_verified = False
                        ref.matched_comment = None
            return out
        finally:
            if named_token is not None:
                _named.reset(named_token)

    return run


# Threads that name the topic only in one matched comment fill in only while
# fewer than this many threads name it in their title or body.
COMMENT_ONLY_FILL_BELOW = 8


def topic_bar(topic: str, items: Iterable[dict]) -> list[dict]:
    """Session items that name the topic, title or body first.

    A thread whose title and body never name the topic, where only a matched
    comment does, is often an unrelated conversation with one passing
    mention. Such threads are kept only to fill a thin result: while fewer
    than ``COMMENT_ONLY_FILL_BELOW`` threads name the topic themselves.
    """
    from .reddit_discover import names_topic

    items = list(items or [])
    if not topic:
        return items
    strong: list[dict] = []
    comment_only: list[dict] = []
    for item in items:
        if any(names_topic(t, topic) for t in (str(item.get("title") or ""), str(item.get("selftext") or "")) if t):
            strong.append(item)
            continue
        matched = [str(c.get("excerpt") or "") for c in item.get("top_comments") or [] if c.get("matched")]
        if any(names_topic(t, topic) for t in matched if t):
            item["comment_only_match"] = True
            comment_only.append(item)
    room = max(COMMENT_ONLY_FILL_BELOW - len(strong), 0)
    left_out = len(comment_only) - room
    if left_out > 0:
        _log(f"{left_out} threads that name the topic only in a comment left out")
    return strong + comment_only[:room]
