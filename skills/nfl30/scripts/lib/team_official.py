"""Official team updates for nfl30: team-site news and press-conference video.

Two keyless sub-fetches share the ``team_official`` source slug:

1. **News** — the team CMS RSS feed (``https://www.<domain>/rss/news``, RSS
   2.0; not every team exposes one, so ``Team.rss`` may be null) windowed to
   the run's date range. When the feed is missing or fails, the lane falls
   back to a ``site:<domain>`` web search through the configured grounding
   backend (brave/serper/keyless all pass ``site:`` through verbatim).
2. **Pressers** — the team's official YouTube channel listing via
   ``yt-dlp --flat-playlist`` (newest uploads first), filtered to press
   conferences / postgame / media availability by title, then run through
   the existing transcript fetcher so the report can quote what the coach or
   quarterback actually said. Team sites post pressers as video pages, not
   transcripts, so YouTube captions are the only keyless text source.

Both halves are gated on a resolved team (``config["_nfl"]["team"]``); the
pipeline records ``SKIPPED_UNCONFIGURED`` when there is none.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from . import http, log, subproc
from .relevance import token_overlap_relevance

DEPTH_CONFIG: Dict[str, Dict[str, int]] = {
    # news: RSS items kept; videos: channel uploads scanned; pressers: videos transcribed
    "quick": {"news": 8, "videos": 12, "pressers": 2},
    "default": {"news": 20, "videos": 30, "pressers": 4},
    "deep": {"news": 40, "videos": 60, "pressers": 8},
}

PRESSER_RE = re.compile(
    r"press conference|postgame|post-game|post game|media availability|"
    r"speaks (?:to|with) the media|addresses the media|talks (?:to|with) the media|"
    r"\bpresser\b|meets the media|locker room|full interview",
    re.IGNORECASE,
)

RSS_TIMEOUT = 15
CHANNEL_TIMEOUT = 60
_ATOM = "{http://www.w3.org/2005/Atom}"
_MIN_RELEVANCE = {"news": 0.55, "presser": 0.65}


def _log(msg: str) -> None:
    log.source_log("TEAM", msg, tty_only=False)


# ---------------------------------------------------------------------------
# News (RSS + site: fallback)
# ---------------------------------------------------------------------------


def _parse_date(value: str | None) -> Optional[str]:
    """RFC-822 (RSS) or ISO (Atom) date → ``YYYY-MM-DD``; None when unparseable."""
    if not value:
        return None
    text = value.strip()
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_feed(xml_text: str) -> List[Dict[str, Any]]:
    """Parse an RSS 2.0 or Atom feed into ``{title, url, date, text}`` rows. Never raises."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        _log(f"feed parse error: {exc}")
        return []
    rows: List[Dict[str, Any]] = []
    for item in root.iter("item"):  # RSS 2.0
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not url:
            continue
        rows.append({
            "title": title,
            "url": url,
            "date": _parse_date(item.findtext("pubDate")),
            "text": _strip_html(item.findtext("description") or ""),
        })
    if not rows:
        for entry in root.iter(f"{_ATOM}entry"):  # Atom
            title = (entry.findtext(f"{_ATOM}title") or "").strip()
            link_el = entry.find(f"{_ATOM}link")
            url = link_el.get("href", "").strip() if link_el is not None else ""
            if not title or not url:
                continue
            rows.append({
                "title": title,
                "url": url,
                "date": _parse_date(entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")),
                "text": _strip_html(entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content") or ""),
            })
    return rows


def fetch_rss(url: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch and parse a team feed; None when the fetch failed (fallback trigger)."""
    text = http.get_text(url, timeout=RSS_TIMEOUT, accept="application/rss+xml, application/xml, text/xml, */*")
    if text is None:
        return None
    rows = parse_feed(text)
    if not rows and "<rss" not in text and "<feed" not in text:
        return None
    return rows


def _in_window(date: Optional[str], from_date: str, to_date: str) -> bool:
    if not date:
        return False
    return from_date <= date <= to_date


def _web_fallback(
    team: Dict[str, Any], topic: str, date_range: tuple[str, str],
    config: Optional[Dict[str, Any]], web_backend: str, cap: int,
) -> List[Dict[str, Any]]:
    if config is None:
        return []
    from . import grounding  # local import: grounding pulls optional backends

    query = f"site:{team['domain']} {topic}".strip()
    try:
        results, _artifact = grounding.web_search(query, date_range, config, backend=web_backend)
    except Exception as exc:  # the lane degrades, never raises
        _log(f"site: fallback failed for {team['domain']}: {exc}")
        return []
    rows: List[Dict[str, Any]] = []
    for row in results or []:
        url = str(row.get("url") or "")
        if team["domain"] not in url:
            continue
        rows.append({
            "title": str(row.get("title") or ""),
            "url": url,
            "date": row.get("date"),
            "text": str(row.get("snippet") or row.get("description") or ""),
        })
    return rows[:cap]


# ---------------------------------------------------------------------------
# Pressers (official YouTube channel)
# ---------------------------------------------------------------------------


def clean_transcript(text: str) -> str:
    """Un-escape entities and drop the ``>>`` speaker-change markers YouTube captions carry."""
    text = html.unescape(text or "")
    text = re.sub(r"\s*>>\s*", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def is_ytdlp_installed() -> bool:
    return shutil.which("yt-dlp") is not None


def channel_videos_url(youtube_handle: str) -> str:
    handle = youtube_handle.strip().lstrip("@")
    return f"https://www.youtube.com/@{handle}/videos"


def list_channel_videos(youtube_handle: str, count: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Newest ``count`` uploads from a channel via ``yt-dlp --flat-playlist``.

    Returns ``(rows, error)``. Rows carry ``id, title, url, date, views``;
    ``date`` is yt-dlp's approximate upload date (``youtubetab:approximate_date``)
    and may be None.
    """
    from . import youtube_yt  # local import: keeps SSH/player-client wrapping in one place

    cmd = youtube_yt._wrap_ytdlp_cmd([
        "yt-dlp", "--ignore-config", "--flat-playlist", "--dump-json",
        "--playlist-end", str(max(1, count)),
        "--extractor-args", "youtubetab:approximate_date",
        channel_videos_url(youtube_handle),
    ])
    try:
        result = subproc.run_with_timeout(cmd, timeout=CHANNEL_TIMEOUT)
    except subproc.SubprocTimeout:
        return [], "channel listing timed out"
    except (FileNotFoundError, OSError) as exc:
        return [], f"yt-dlp unavailable: {exc}"
    rows: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = str(entry.get("id") or "")
        if not vid:
            continue
        upload = entry.get("upload_date")
        date = None
        if isinstance(upload, str) and len(upload) == 8 and upload.isdigit():
            date = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
        elif entry.get("timestamp"):
            try:
                date = datetime.fromtimestamp(int(entry["timestamp"]), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                date = None
        rows.append({
            "id": vid,
            "title": str(entry.get("title") or ""),
            "url": entry.get("url") if str(entry.get("url") or "").startswith("http") else f"https://www.youtube.com/watch?v={vid}",
            "date": date,
            "views": entry.get("view_count") or 0,
            "duration": entry.get("duration") or 0,
        })
    if not rows and result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return [], (detail[-1] if detail else f"yt-dlp exit {result.returncode}")
    return rows, None


def speaker_from_title(title: str) -> str:
    """``"Andy Reid Postgame Press Conference | Week 3"`` → ``"Andy Reid"``."""
    head = re.split(r"[:|\-–—(]", title, maxsplit=1)[0]
    head = PRESSER_RE.split(head)[0]
    head = re.sub(r"\b(HC|QB|Coach|Head Coach|OC|DC|GM)\b\.?", "", head, flags=re.IGNORECASE)
    head = re.sub(r"(['\u2019]s)\b", "", head)  # "Andy Reid's Locker Room Speech" -> "Andy Reid"
    head = re.sub(r"\s+", " ", head).strip(" :-|")
    return head[:60]


# Team channels almost never write "press conference" in a title. Live titles
# (Packers, 2026-09) look like "Matt LaFleur: 'He's a pro's pro'", "Jordan Love
# after loss to the Vikings: 'We'll get back to work'", "Matt LaFleur speaks
# about loss to the Vikings on the road". So a clip is recognized by SHAPE: a
# person's name leading a colon or a speaking verb, minus the team's own shows.
SHOW_RE = re.compile(
    r"mic'?d up|trailer|final thoughts|three things|rock report|memorable moments|"
    r"highlights?\b|hype\b|unscripted|\bdaily\b|top \d+|best (?:of|plays|moments)|"
    r"all-access|inside the|day in the life|behind the scenes|throwback|rewind|"
    r"schedule release|draft (?:class|recap|pick)|hall of fame|countdown|podcast|"
    r"game ?day|preview\b|recap\b|cheerleader|full game|\bvs\.? .* \|",
    re.IGNORECASE,
)
_NAME = r"[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){0,3}"
QUOTE_RES = (
    # "Name: ..." / "Name on X: ..." / "Name after loss to Y: ..." / "Name 1-on-1: ..."
    re.compile(rf"^(?P<who>{_NAME})(?:\s+1-on-1)?(?:\s+(?:on|after|about)\b[^:]{{0,70}})?:\s*\S"),
    # "Name speaks about ...", "Name says ..."
    re.compile(rf"^(?P<who>{_NAME})\s+(?:says|speaks|talks|addresses|discusses|explains|reacts|"
               rf"previews|breaks down|meets)\b"),
    # "Name is 'excited' for ..."
    re.compile(rf"^(?P<who>{_NAME})\s+(?:is|are|was|were)\s+['‘\"“]"),
)


def _mentions_team(text: str, team: Optional[Dict[str, Any]]) -> bool:
    if not team:
        return False
    low = text.lower()
    return any(
        str(team.get(k) or "").lower() and str(team.get(k)).lower() in low
        for k in ("nickname", "city", "name")
    )


def classify_title(title: str, team: Optional[Dict[str, Any]] = None) -> Optional[tuple[str, str]]:
    """``("presser" | "clip", speaker)`` for a team-channel title, else None."""
    title = (title or "").strip()
    if PRESSER_RE.search(title):
        return "presser", speaker_from_title(title)
    if not title or SHOW_RE.search(title):
        return None
    for rx in QUOTE_RES:
        match = rx.match(title)
        if not match:
            continue
        who = match.group("who").strip()
        if _mentions_team(who, team):
            return None  # "Packers Daily:", "Total Packers:" are the team's own shows
        return "clip", who
    return None


def fetch_upload_dates(video_ids: List[str], *, workers: int = 6) -> Dict[str, Optional[str]]:
    """Real ``YYYY-MM-DD`` upload dates for videos the flat listing left undated."""
    from concurrent.futures import ThreadPoolExecutor

    from . import youtube_yt

    def _one(vid: str) -> tuple[str, Optional[str]]:
        cmd = youtube_yt._wrap_ytdlp_cmd([
            "yt-dlp", "--ignore-config", "--skip-download", "--no-warnings",
            "--print", "%(upload_date)s", f"https://www.youtube.com/watch?v={vid}",
        ])
        try:
            out = subproc.run_with_timeout(cmd, timeout=40).stdout.strip().splitlines()
        except (subproc.SubprocTimeout, FileNotFoundError, OSError):
            return vid, None
        raw = out[-1].strip() if out else ""
        if len(raw) == 8 and raw.isdigit():
            return vid, f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        return vid, None

    if not video_ids:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(video_ids)))) as executor:
        futures = [http.submit_with_context(executor, _one, vid) for vid in video_ids]
        return dict(f.result() for f in futures)


def _in_window_loose(date: Optional[str], from_date: str, to_date: str) -> bool:
    """Window check tolerant of approximate dates; an unknown date is kept."""
    if not date:
        return True
    if date > to_date:
        return False
    if date < from_date:
        return (datetime.fromisoformat(from_date) - datetime.fromisoformat(date)).days <= 2
    return True


def select_pressers(
    rows: List[Dict[str, Any]],
    from_date: str,
    to_date: str,
    cap: int,
    team: Optional[Dict[str, Any]] = None,
    resolve_dates: Optional[Callable[[List[str]], Dict[str, Optional[str]]]] = None,
) -> List[Dict[str, Any]]:
    """Pick press conferences and player/coach clips from a channel listing.

    Explicit press conferences rank ahead of quote-style clips; within a tier the
    channel's newest-first order is kept. Candidates the listing left undated get
    real dates from ``resolve_dates`` before the window check, so an old clip is
    not mistaken for this week's.
    """
    ranked: List[tuple[int, int, Dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        hit = classify_title(row.get("title") or "", team)
        if not hit:
            continue
        tier, speaker = hit
        ranked.append((0 if tier == "presser" else 1, idx, {**row, "kind": tier, "speaker": speaker}))
    ranked.sort(key=lambda t: (t[0], t[1]))
    candidates = [r for _t, _i, r in ranked[: max(cap * 3, cap)]]
    if resolve_dates:
        missing = [r for r in candidates if not r.get("date")]
        if missing:
            found = resolve_dates([r["id"] for r in missing])
            for r in missing:
                r["date"] = found.get(r["id"])
    kept = [r for r in candidates if _in_window_loose(r.get("date"), from_date, to_date)]
    return kept[:cap]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _teams_for(entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    teams = []
    for key in ("team", "opponent"):
        team = entity.get(key)
        if isinstance(team, dict) and team.get("domain"):
            teams.append(team)
    return teams


def search_team_official(
    entity: Dict[str, Any],
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    *,
    config: Optional[Dict[str, Any]] = None,
    web_backend: str = "auto",
    fetch_transcripts: bool = True,
) -> Dict[str, Any]:
    """Fetch official news and pressers for the resolved team(s). Never raises."""
    caps = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    teams = _teams_for(entity or {})
    result: Dict[str, Any] = {"news": [], "pressers": [], "news_source": {}, "errors": []}
    if not teams:
        result["error"] = "no team resolved"
        return result
    per_team = max(1, len(teams))
    news_cap = max(2, caps["news"] // per_team)
    presser_cap = max(1, caps["pressers"] // per_team)

    for team in teams:
        abbr = team.get("abbr", "?")
        # --- news
        rows: Optional[List[Dict[str, Any]]] = None
        if team.get("rss"):
            rows = fetch_rss(team["rss"])
            if rows is None:
                _log(f"{abbr}: RSS unavailable at {team['rss']}; trying site: search")
        if rows is None:
            rows = _web_fallback(team, topic, (from_date, to_date), config, web_backend, news_cap)
            result["news_source"][abbr] = "web" if rows else "none"
        else:
            result["news_source"][abbr] = "rss"
            rows = [r for r in rows if _in_window(r.get("date"), from_date, to_date)]
        for row in rows[:news_cap]:
            result["news"].append({**row, "team": abbr, "team_name": team.get("name", abbr)})
        _log(f"{abbr}: {len(rows[:news_cap])} official news items ({result['news_source'][abbr]})")

        # --- pressers
        if not team.get("youtube_handle"):
            continue
        if not is_ytdlp_installed():
            result["errors"].append(f"{abbr}: yt-dlp not on PATH; pressers skipped")
            continue
        videos, err = list_channel_videos(team["youtube_handle"], caps["videos"])
        if err:
            result["errors"].append(f"{abbr}: {err}")
            _log(f"{abbr}: channel listing failed: {err}")
            continue
        chosen = select_pressers(
            videos, from_date, to_date, presser_cap, team=team, resolve_dates=fetch_upload_dates,
        )
        _log(f"{abbr}: {len(videos)} channel uploads scanned, {len(chosen)} pressers/clips kept")
        for row in chosen:
            result["pressers"].append({**row, "team": abbr, "team_name": team.get("name", abbr)})

    if result["pressers"] and fetch_transcripts:
        from . import youtube_yt

        ids = [p["id"] for p in result["pressers"]]
        token = (config or {}).get("SCRAPECREATORS_API_KEY") if config else None
        try:
            transcripts = youtube_yt.fetch_transcripts_parallel(ids, token=token)
        except Exception as exc:  # transcript failure must not sink the lane
            _log(f"transcript fetch failed: {exc}")
            transcripts = {}
        for row in result["pressers"]:
            text = transcripts.get(row["id"])
            if text:
                text = clean_transcript(text)
                row["transcript"] = text
                row["transcript_highlights"] = youtube_yt.extract_transcript_highlights(text, topic, limit=5)
    if not result["news"] and not result["pressers"] and result["errors"]:
        result["error"] = "; ".join(result["errors"])
    return result


def parse_team_official_response(result: Dict[str, Any], query: str = "") -> List[Dict[str, Any]]:
    """Flatten news + pressers into engine item dicts."""
    items: List[Dict[str, Any]] = []
    for row in result.get("news") or []:
        text = f"{row.get('title', '')} {row.get('text', '')}".strip()
        rel = max(_MIN_RELEVANCE["news"], token_overlap_relevance(query, text) if query else 0.0)
        items.append({
            "id": f"TO-{row.get('team', 'NFL')}-{abs(hash(row.get('url', ''))) % 10**8}",
            "kind": "news",
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "date": row.get("date"),
            "text": row.get("text", ""),
            "team": row.get("team"),
            "team_name": row.get("team_name"),
            "author": f"{row.get('team_name', 'Team')} (official)",
            "engagement": {},
            "relevance": rel,
            "why_relevant": "Official team site news",
        })
    for row in result.get("pressers") or []:
        transcript = str(row.get("transcript") or "")
        text = f"{row.get('title', '')} {transcript[:2000]}".strip()
        rel = max(_MIN_RELEVANCE["presser"], token_overlap_relevance(query, text) if query else 0.0)
        speaker = str(row.get("speaker") or "") or speaker_from_title(row.get("title", ""))
        items.append({
            "id": f"TO-YT-{row.get('id', '')}",
            "kind": row.get("kind") or "presser",
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "date": row.get("date"),
            "text": transcript[:5000],
            "transcript_snippet": transcript[:5000],
            "transcript_highlights": row.get("transcript_highlights") or [],
            "speaker": speaker,
            "team": row.get("team"),
            "team_name": row.get("team_name"),
            "author": speaker or f"{row.get('team_name', 'Team')} (official)",
            "engagement": {"views": int(row.get("views") or 0)},
            "relevance": rel,
            "why_relevant": "Official team press conference or player/coach clip",
        })
    return items


def format_summary_line(items: List[Dict[str, Any]] | List[Any]) -> Optional[str]:
    """Footer line: ``🏟️ Team official: N news │ M pressers (K transcribed)``."""
    news = pressers = transcribed = 0
    for item in items:
        meta = getattr(item, "metadata", None)
        kind = (meta or {}).get("kind") if meta is not None else item.get("kind")
        if kind in ("presser", "clip"):
            pressers += 1
            has_text = bool((meta or {}).get("transcript_snippet")) if meta is not None else bool(item.get("transcript_snippet"))
            transcribed += 1 if has_text else 0
        else:
            news += 1
    if not (news or pressers):
        return None
    line = f"🏟️ Team official: {news} news"
    if pressers:
        line += f" │ {pressers} presser/clip{'s' if pressers != 1 else ''} ({transcribed} transcribed)"
    return line
