"""TikTok / Instagram keyless-ish fallback: web discovery + yt-dlp extract.

ScrapeCreators owns keyword search. When that key is missing or 402s, this
module finds public post URLs via the already-configured web backends, then
extracts metadata with yt-dlp (already on PATH for YouTube).

Discovery never uses Google ``site:`` — Serper's free tier 400s that pattern.
Discovery asks each web backend for recent results (see ``_freshness_on``).
Post dates are decoded from TikTok ids and Instagram shortcodes so stale URLs
are dropped before any yt-dlp call; yt-dlp's own timestamp is the final
filter. Out-of-window posts are never kept: an empty window returns zero.

ponytail: no hashtag/user listing for IG (yt-dlp extractors are broken);
TikTok ``@user`` listing still works and is used for ``creators=``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from . import dates, http, log, subproc, web_search_keyless, youtube_yt
from .relevance import RELEVANCE_FLOOR
from .relevance import token_overlap_relevance as _compute_relevance

_TT_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([\w.\-]+)/video/(\d+)", re.I
)
_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p|reels)/([\w\-]+)", re.I
)
_HASHTAG_RE = re.compile(r"#(\w+)")

_DEPTH_URLS = {"quick": 8, "default": 12, "deep": 20}
_ENRICH_TIMEOUT = 180

# TikTok creator expansion: (creators listed, videos listed per creator).
_DEPTH_CREATORS = {"quick": (3, 5), "default": (5, 8), "deep": (8, 12)}
# A creator listing reads this many times the kept count, then keeps the most viewed.
_CREATOR_LIST_FACTOR = 3

# Discovery freshness. LAST30DAYS_SHORTFORM_FRESHNESS (env or config):
#   "on" (default) asks every backend for recent results,
#   "off" sends the unfiltered requests,
#   a comma list (e.g. "serper,exa") turns it on for those backends only.
# Backends: serper (tbs=qdr:m), brave (freshness=pm), exa (startPublishedDate
# = window start), searxng (time_range=month). Brave cards often lack
# page_age, so each switch exists to A/B the recall cost per backend.
FRESHNESS_ENV = "LAST30DAYS_SHORTFORM_FRESHNESS"

# TikTok creator expansion. LAST30DAYS_SHORTFORM_CREATOR_EXPANSION (env or
# config): "on" (default) or "off", so the A/B harness can measure its lift.
EXPANSION_ENV = "LAST30DAYS_SHORTFORM_CREATOR_EXPANSION"
# Keyless TikTok top comments from the public web endpoint. Same on/off shape.
COMMENTS_ENV = "LAST30DAYS_SHORTFORM_COMMENTS"

# Instagram media ids carry milliseconds since this epoch in their top bits.
_IG_EPOCH_MS = 1314220021721
_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
# Ids that decode before 2016 are not plausible post dates.
_MIN_ID_TS = datetime(2016, 1, 1, tzinfo=timezone.utc).timestamp()


def _stopped(deadline: float | None, cancel: threading.Event | None) -> bool:
    """True once the run is cancelled or past its shared monotonic deadline."""
    if cancel is not None and cancel.is_set():
        return True
    return deadline is not None and time.monotonic() >= deadline


def _bounded_timeout(base: float, deadline: float | None) -> float:
    """Per-call timeout, never longer than the deadline's remaining time."""
    if deadline is None:
        return base
    return max(1.0, min(base, deadline - time.monotonic()))


def _freshness_on(backend: str, config: Dict[str, Any] | None = None) -> bool:
    """True when discovery should ask ``backend`` for recent results."""
    raw = (config or {}).get(FRESHNESS_ENV) or os.environ.get(FRESHNESS_ENV) or "on"
    value = str(raw).strip().lower()
    if value in ("on", "1", "true", "yes"):
        return True
    if value in ("off", "0", "false", "no"):
        return False
    return backend in {part.strip() for part in value.split(",")}


def _switch_on(name: str, config: Dict[str, Any] | None = None) -> bool:
    raw = (config or {}).get(name) or os.environ.get(name) or "on"
    return str(raw).strip().lower() not in ("off", "0", "false", "no")


def _expansion_on(config: Dict[str, Any] | None = None) -> bool:
    """True unless creator expansion is switched off."""
    return _switch_on(EXPANSION_ENV, config)


def comments_on(config: Dict[str, Any] | None = None) -> bool:
    """True unless keyless TikTok top comments are switched off."""
    return _switch_on(COMMENTS_ENV, config)


def post_timestamp_from_url(kind: str, url: str) -> Optional[float]:
    """Unix seconds decoded from a post URL's id, or None when undecodable.

    TikTok: the video id's top 32 bits are the upload time in seconds.
    Instagram: the shortcode is a base64 media id whose top bits
    (``id >> 23``) are milliseconds since ``_IG_EPOCH_MS``.
    """
    if kind == "tiktok":
        m = _TT_RE.search(url or "")
        if not m:
            return None
        ts = float(int(m.group(2)) >> 32)
    elif kind == "instagram":
        m = _IG_RE.search(url or "")
        code = m.group(1) if m else ""
        if not code or len(code) > 11:
            return None
        media_id = 0
        for ch in code:
            idx = _IG_ALPHABET.find(ch)
            if idx < 0:
                return None
            media_id = media_id * 64 + idx
        ts = ((media_id >> 23) + _IG_EPOCH_MS) / 1000.0
    else:
        return None
    if ts < _MIN_ID_TS or ts > time.time() + 2 * 86400:
        return None
    return ts


def post_date_from_url(kind: str, url: str) -> Optional[str]:
    """UTC ``YYYY-MM-DD`` decoded from a post URL's id, or None."""
    ts = post_timestamp_from_url(kind, url)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _outside_window(kind: str, url: str, from_date: str, to_date: str) -> bool:
    """True only when the id decodes to a date clearly outside the window.

    One day of slack on each side covers timezone and post-vs-upload skew;
    yt-dlp's timestamp applies the exact window after hydration.
    """
    day = post_date_from_url(kind, url)
    if day is None:
        return False
    d = date.fromisoformat(day)
    return (d < date.fromisoformat(from_date) - timedelta(days=1)
            or d > date.fromisoformat(to_date) + timedelta(days=1))


def _log(kind: str, msg: str) -> None:
    log.source_log("TikTok" if kind == "tiktok" else "Instagram", msg, tty_only=False)


def normalize_post_url(kind: str, url: str) -> Optional[str]:
    """Canonicalize a TikTok video / IG reel URL, or None if not a post."""
    if not url:
        return None
    url = unquote(url.split("#")[0].split("?")[0]).replace("%40", "@")
    if kind == "tiktok":
        m = _TT_RE.search(url)
        if not m:
            return None
        return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
    m = _IG_RE.search(url)
    if not m:
        return None
    return f"https://www.instagram.com/reel/{m.group(1)}/"


def discovery_query(kind: str, topic: str) -> str:
    """Plain-language query. Do not add ``site:`` — Serper free accounts reject it."""
    topic = (topic or "").strip()
    if kind == "tiktok":
        return f"{topic} tiktok video"
    return f"{topic} instagram reel"


def _collect(kind: str, raw_urls, dest: List[str], limit: int) -> bool:
    for raw in raw_urls:
        n = normalize_post_url(kind, raw or "")
        if n and n not in dest:
            dest.append(n)
            if len(dest) >= limit:
                return True
    return False


def _serper_links(
    query: str, api_key: str, count: int, deadline: float | None = None,
    fresh: bool = False,
) -> List[str]:
    body: Dict[str, Any] = {"q": query, "num": min(20, max(count, 10))}
    if fresh:
        body["tbs"] = "qdr:m"
    data = http.request(
        "POST",
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key},
        json_data=body,
        timeout=_bounded_timeout(15.0, deadline),
        retries=1,
    )
    return [r.get("link", "") for r in (data.get("organic") or []) if isinstance(r, dict)]


def _exa_links(
    query: str, api_key: str, domain: str, count: int, deadline: float | None = None,
    start_date: str | None = None,
) -> List[str]:
    body: Dict[str, Any] = {
        "query": query,
        "numResults": count,
        "includeDomains": [domain],
        "contents": {"text": False},
    }
    if start_date:
        body["startPublishedDate"] = f"{start_date}T00:00:00.000Z"
    data = http.request(
        "POST",
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key},
        json_data=body,
        timeout=_bounded_timeout(20.0, deadline),
        retries=1,
    )
    return [r.get("url", "") for r in (data.get("results") or []) if isinstance(r, dict)]


def _brave_links(query: str, api_key: str, count: int, fresh: bool = False) -> List[str]:
    from . import grounding

    # Wide date window for the result filter: social cards often lack
    # page_age and would otherwise drop. ``fresh`` only shapes the request.
    items, _art = grounding.brave_search(
        query, ("1970-01-01", "2099-12-31"), api_key, count=count,
        freshness="pm" if fresh else None,
    )
    return [i.get("url", "") for i in items]


def discover_urls(
    kind: str,
    query: str,
    config: Dict[str, Any],
    count: int,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
    from_date: str | None = None,
) -> Tuple[List[str], str]:
    """Return (post_urls, backend_name). Never raises; stops at deadline/cancel.

    ``from_date`` is the window start Exa's freshness filter uses.
    """
    urls: List[str] = []
    used = "none"
    q = discovery_query(kind, query)

    searxng = (config.get("LAST30DAYS_SEARXNG_URL") or "").strip()
    if searxng and not _stopped(deadline, cancel):
        try:
            items = web_search_keyless._search_searxng(
                q, count, searxng,
                time_range="month" if _freshness_on("searxng", config) else None,
            )
            if _collect(kind, (i.get("url", "") for i in items), urls, count):
                return urls, "searxng"
            if urls:
                used = "searxng"
        except Exception as e:
            _log(kind, f"SearXNG discovery error: {e}")

    # Serper/Exa/Brave are paid rungs here, not the first choice: Serper's
    # free tier rejects ``site:`` filters, Exa returns 0 Instagram posts, and
    # Brave HTML 429s under load. They run only when the rungs above returned
    # nothing.

    if not urls and not _stopped(deadline, cancel) and config.get("SERPER_API_KEY"):
        try:
            if _collect(
                kind,
                _serper_links(
                    q, config["SERPER_API_KEY"], count, deadline=deadline,
                    fresh=_freshness_on("serper", config),
                ),
                urls,
                count,
            ):
                return urls, "serper"
            if urls:
                used = "serper"
        except Exception as e:
            _log(kind, f"Serper discovery error: {e}")

    if not urls and not _stopped(deadline, cancel) and kind == "tiktok" and config.get("EXA_API_KEY"):
        try:
            if _collect(
                kind,
                _exa_links(
                    q, config["EXA_API_KEY"], "tiktok.com", count, deadline=deadline,
                    start_date=from_date if _freshness_on("exa", config) else None,
                ),
                urls,
                count,
            ):
                return urls, "exa"
            if urls:
                used = "exa"
        except Exception as e:
            _log(kind, f"Exa discovery error: {e}")

    if not urls and not _stopped(deadline, cancel) and config.get("BRAVE_API_KEY"):
        try:
            if _collect(
                kind,
                _brave_links(
                    q, config["BRAVE_API_KEY"], count,
                    fresh=_freshness_on("brave", config),
                ),
                urls,
                count,
            ):
                return urls, "brave"
            if urls:
                used = "brave"
        except Exception as e:
            _log(kind, f"Brave discovery error: {e}")

    # The keyless floor follows the same policy the availability gate used:
    # when the host has native search (LAST30DAYS_NATIVE_SEARCH), the floor is
    # off, so a failed paid rung must not fall through to it either. A host
    # that passed --shortform-urls already searched; the floor (slow, usually
    # bot-blocked) would only repeat that search worse.
    if not urls and not _stopped(deadline, cancel):
        from . import shortform_envelope
        from .env import keyless_web_allowed

        if keyless_web_allowed(config) and shortform_envelope.from_config(config) is None:
            try:
                items, art = web_search_keyless.keyless_search(
                    q, ("1970-01-01", "2099-12-31"), config, count,
                )
                _collect(kind, (i.get("url", "") for i in items), urls, count)
                if urls and used == "none":
                    used = art.get("keyless_backend") or "keyless"
            except Exception as e:
                _log(kind, f"Keyless discovery error: {e}")

    return urls[:count], used if urls else "none"


def _creator_is_topic(topic: str, handle: str) -> bool:
    """True when the account is the topic itself (@sabrinacarpenter for
    "Sabrina Carpenter"): its own captions rarely name it, so its posts are
    on-topic by authorship."""
    key = re.sub(r"[^a-z0-9]", "", (topic or "").lower())
    name = re.sub(r"[^a-z0-9]", "", (handle or "").lstrip("@").lower())
    return len(key) >= 4 and key in name


def creator_urls(
    kind: str,
    handle: str,
    count: int,
    deadline: float | None = None,
    *,
    topic: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[str]:
    """Most-viewed recent TikTok posts for ``@handle``. IG user listing is
    broken, so Instagram is skipped.

    Lists the newest ``3 * count`` entries (id, title and view count, one JSON
    object per line; one listing call). With a window, ids that decode outside
    it are dropped; with ``topic``, titles below the shared relevance floor are
    dropped. The ``count`` survivors with the most views are returned, so a
    creator's month is represented by what people actually watched. All of
    this runs before hydration.
    """
    if kind != "tiktok" or not handle:
        return []
    handle = handle.lstrip("@").strip()
    if not handle:
        return []
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "--no-warnings",
        "--flat-playlist",
        "--playlist-end",
        str(count * _CREATOR_LIST_FACTOR),
        "--print",
        "%(.{id,title,view_count})j",
        f"https://www.tiktok.com/@{handle}",
    ]
    # Same routing as the YouTube source: local yt-dlp, or the SSH host when
    # LAST30DAYS_YOUTUBE_SSH_HOST is set (proxy flag folds in too).
    cmd = youtube_yt.wrap_ytdlp_cmd(cmd)
    try:
        result = subproc.run_with_timeout(cmd, timeout=_bounded_timeout(60.0, deadline))
    except (subproc.SubprocTimeout, FileNotFoundError, OSError) as e:
        _log(kind, f"creator list @{handle} failed: {e}")
        return []
    out: List[Tuple[int, str]] = []
    for line in (result.stdout or "").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("id") or "")
        if not vid.isdigit():
            continue
        url = f"https://www.tiktok.com/@{handle}/video/{vid}"
        if from_date and to_date and _outside_window(kind, url, from_date, to_date):
            continue
        title = str(entry.get("title") or "")
        if (topic and not _creator_is_topic(topic, handle)
                and _compute_relevance(topic, title) < RELEVANCE_FLOOR):
            continue
        views = entry.get("view_count")
        out.append((views if isinstance(views, int) else -1, url))
    # Stable: equal (or unknown) view counts keep the newest-first listing order.
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [url for _views, url in out[:count]]


def _post_id(kind: str, url: str) -> str:
    """The platform id in a post URL (TikTok video id, Instagram shortcode)."""
    m = (_TT_RE if kind == "tiktok" else _IG_RE).search(url or "")
    if not m:
        return ""
    return m.group(2) if kind == "tiktok" else m.group(1)


def _item_from_ytdlp(kind: str, raw: Dict[str, Any], core_topic: str) -> Optional[Dict[str, Any]]:
    video_id = str(raw.get("id") or "")
    if not video_id:
        return None
    author = raw.get("uploader") or raw.get("channel") or raw.get("creator") or ""
    text = (raw.get("description") or raw.get("title") or "").strip()
    ts = raw.get("timestamp")
    date_str = dates.timestamp_to_date(ts) if ts else None
    hashtags = _HASHTAG_RE.findall(text)
    if kind == "tiktok":
        url = (
            normalize_post_url(kind, raw.get("webpage_url") or "")
            or (f"https://www.tiktok.com/@{author}/video/{video_id}" if author else "")
        )
        label = "TikTok"
        engagement = {
            "views": raw.get("view_count") or 0,
            "likes": raw.get("like_count") or 0,
            "comments": raw.get("comment_count") or 0,
            "shares": raw.get("repost_count") or 0,
        }
    else:
        url = normalize_post_url(kind, raw.get("webpage_url") or "") or (
            f"https://www.instagram.com/reel/{video_id}/"
        )
        label = "Instagram"
        engagement = {
            "views": raw.get("view_count") or 0,
            "likes": raw.get("like_count") or 0,
            "comments": raw.get("comment_count") or 0,
        }
    if not url:
        return None
    snippet = text
    words = snippet.split()
    if len(words) > 500:
        snippet = " ".join(words[:500]) + "..."
    return {
        "video_id": video_id,
        "text": text,
        "url": url,
        "author_name": author,
        "date": date_str,
        "engagement": engagement,
        "hashtags": hashtags,
        "duration": raw.get("duration"),
        "relevance": (1.0 if _creator_is_topic(core_topic, author)
                      else _compute_relevance(core_topic, text, hashtags)),
        "why_relevant": f"{label}: {text[:60]}" if text else f"{label}: {core_topic}",
        "caption_snippet": snippet,
        "_oss_backend": "yt-dlp",
    }


def enrich_urls(
    kind: str,
    urls: List[str],
    core_topic: str,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """yt-dlp --dump-json over a URL list. Never raises; stops at deadline/cancel."""
    if not urls or _stopped(deadline, cancel):
        return []
    if not youtube_yt.is_ytdlp_installed():
        _log(kind, "yt-dlp not installed; skip OSS enrich")
        return []
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--ignore-errors",
        "--no-warnings",
        "--dump-json",
        "--socket-timeout",
        "25",
        "-a",
        "-",
    ]
    # Same routing as the YouTube source (see creator_urls).
    cmd = youtube_yt.wrap_ytdlp_cmd(cmd)
    budget = _bounded_timeout(_ENRICH_TIMEOUT, deadline)
    try:
        result = subproc.run_with_timeout(
            cmd, timeout=budget, input_text="\n".join(urls) + "\n"
        )
    except subproc.SubprocTimeout:
        _log(kind, f"yt-dlp enrich timed out ({budget:.0f}s)")
        return []
    except (FileNotFoundError, OSError) as e:
        _log(kind, f"yt-dlp enrich failed: {e}")
        return []
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = _item_from_ytdlp(kind, raw, core_topic)
        if not item:
            continue
        vid = item["video_id"]
        if vid in seen:
            continue
        seen.add(vid)
        items.append(item)
    _log(kind, f"yt-dlp enriched {len(items)}/{len(urls)} posts")
    return items


def search(
    kind: str,
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
    config: Dict[str, Any],
    creators: Optional[List[str]] = None,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """Discover + enrich. Returns the same ``{items, error}`` shape as SC search.

    Honors the run's shared monotonic deadline and cancellation event: each
    phase checks both before starting, and per-call timeouts are bounded by
    the remaining time, so a cancelled run does not keep a 180s yt-dlp
    extraction alive past exit.
    """
    if kind not in ("tiktok", "instagram"):
        return {"items": [], "error": f"unsupported kind {kind!r}"}
    if not youtube_yt.is_ytdlp_installed():
        return {"items": [], "error": "yt-dlp not installed"}
    if _stopped(deadline, cancel):
        return {"items": [], "error": "research deadline passed before OSS fallback"}

    cap = _DEPTH_URLS.get(depth, _DEPTH_URLS["default"])
    window = {"from_date": from_date, "to_date": to_date}
    urls: List[str] = []
    explicit = [h.lstrip("@").strip().lower() for h in (creators or []) if h]

    # Host-found URLs (--shortform-urls): post URLs lead, held to the same
    # id-date filter; they widen the hydration budget by up to one cap.
    from . import shortform_envelope
    host_env = shortform_envelope.from_config(config)
    host_posts = host_env.posts(kind) if host_env else []
    host_creators = host_env.creators(kind) if host_env else []
    host_urls: set[str] = set()
    host_stale = [u for u in host_posts if _outside_window(kind, u, from_date, to_date)]
    if host_stale:
        _log(kind, f"host URLs: dropped {len(host_stale)} dated outside "
                   f"{from_date}..{to_date} before extraction")
    for u in host_posts:
        if u not in host_stale and u not in urls:
            urls.append(u)
            host_urls.add(u)
    limit = cap + min(len(urls), cap)

    if kind == "tiktok" and creators:
        per = max(3, cap // max(len(creators), 1))
        for handle in creators:
            if _stopped(deadline, cancel):
                return {"items": [], "error": "research deadline passed before OSS discovery"}
            for u in creator_urls(kind, handle, per, deadline=deadline, **window):
                if u not in urls:
                    urls.append(u)

    found, backend = discover_urls(
        kind, topic, config, cap, deadline=deadline, cancel=cancel, from_date=from_date,
    )
    stale = [u for u in found if _outside_window(kind, u, from_date, to_date)]
    if stale:
        _log(kind, f"OSS dropped {len(stale)} discovered posts dated outside "
                   f"{from_date}..{to_date} before extraction")
    stale += host_stale
    for u in found:
        if u not in urls and u not in stale:
            urls.append(u)

    # Creator expansion (TikTok). Host creator profiles are listed first and
    # always (the host named them); with expansion on, every handle seen in
    # discovery or in a host post URL, stale videos included, is a lead too.
    if kind == "tiktok":
        max_creators, per_creator = _DEPTH_CREATORS.get(depth, _DEPTH_CREATORS["default"])
        leads: List[Tuple[str, bool]] = [(h, True) for h in host_creators]
        if _expansion_on(config):
            for u in [*host_posts, *found]:
                m = _TT_RE.search(u)
                if m:
                    leads.append((m.group(1), False))
        handles: List[Tuple[str, bool]] = []
        for h, from_host in leads:
            if h.lower() not in explicit and h.lower() not in {x.lower() for x, _ in handles}:
                handles.append((h, from_host))
        for handle, from_host in handles[:max_creators]:
            if _stopped(deadline, cancel) or len(urls) >= limit:
                break
            for u in creator_urls(
                kind, handle, per_creator, deadline=deadline, topic=topic, **window,
            ):
                if u not in urls:
                    urls.append(u)
                    if from_host:
                        host_urls.add(u)

    urls = urls[:limit]
    source = f"{backend}+host" if host_urls and backend != "none" else ("host" if host_urls else backend)
    _log(kind, f"OSS discovery via {source}: {len(urls)} urls (depth={depth})")
    if not urls:
        if stale:
            return {"items": [], "error": "oss discovery found only posts outside the date window"}
        from .env import keyless_web_allowed
        if host_env is None and backend == "none" and not keyless_web_allowed(config):
            # Native-search host: its own search is the discovery source.
            return {"items": [], "error": "oss discovery empty (the host passed no --shortform-urls)"}
        return {"items": [], "error": "oss discovery empty"}

    items = enrich_urls(kind, urls, topic, deadline=deadline, cancel=cancel)
    if not items:
        return {"items": [], "error": "yt-dlp extracted 0 posts"}

    in_range = [i for i in items if i.get("date") and from_date <= i["date"] <= to_date]
    dropped = len(items) - len(in_range)
    if dropped:
        _log(kind, f"OSS dropped {dropped} posts outside date range or undated")
    if not in_range:
        return {"items": [], "error": f"no posts in date range ({len(items)} extracted, "
                                      f"none dated {from_date}..{to_date})"}
    items = in_range

    if host_urls:
        host_ids = {_post_id(kind, u) for u in host_urls}
        kept: List[Dict[str, Any]] = []
        off_topic = 0
        for item in items:
            if _post_id(kind, item.get("url") or "") in host_ids or str(item.get("video_id")) in host_ids:
                # Host input is untrusted: it must clear the topic bar itself.
                if (item.get("relevance") or 0) < RELEVANCE_FLOOR:
                    off_topic += 1
                    continue
                item["_host_found"] = True
            kept.append(item)
        _log(kind, f"host URLs: kept {sum(1 for i in kept if i.get('_host_found'))} "
                   f"after hydration (off-topic {off_topic})")
        items = kept
        if not items:
            return {"items": [], "error": "host URLs and discovery kept 0 on-topic posts"}

    items.sort(key=lambda x: x.get("engagement", {}).get("views") or 0, reverse=True)
    return {"items": items, "backend": backend}


def credits_exhausted(error: Optional[str]) -> bool:
    """True when ScrapeCreators (or similar) refused the call for quota."""
    if not error:
        return False
    text = str(error).lower()
    return "402" in text or "payment required" in text or "out of credits" in text


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    tt = normalize_post_url(
        "tiktok", "https://www.tiktok.com/%40pris.cesa/video/7692631242264595743?foo=1"
    )
    assert tt == "https://www.tiktok.com/@pris.cesa/video/7692631242264595743", tt
    ig = normalize_post_url("instagram", "https://www.instagram.com/reel/Dem0getP8Js/")
    assert ig == "https://www.instagram.com/reel/Dem0getP8Js/", ig
    assert normalize_post_url("tiktok", "https://www.tiktok.com/discover/foo") is None
    item = _item_from_ytdlp(
        "tiktok",
        {
            "id": "1",
            "uploader": "u",
            "timestamp": 1728000000,
            "view_count": 10,
            "like_count": 2,
            "comment_count": 1,
            "description": "hello #cozy",
            "webpage_url": "https://www.tiktok.com/@u/video/1",
        },
        "cozy",
    )
    assert item and item["engagement"]["likes"] == 2 and item["date"]
    assert credits_exhausted("HTTPError: HTTP 402: Payment Required")
    assert not credits_exhausted(None)
    assert discovery_query("instagram", "cozy game") == "cozy game instagram reel"
    print("ok")
