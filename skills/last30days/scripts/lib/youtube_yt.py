"""YouTube search and transcript extraction via yt-dlp for the v3.0.0 pipeline.

Uses yt-dlp (https://github.com/yt-dlp/yt-dlp) for both YouTube search and
transcript extraction. No API keys needed — just have yt-dlp installed.

Inspired by Peter Steinberger's toolchain approach (yt-dlp + summarize CLI).
"""

import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Depth configurations: how many videos to search / transcribe
DEPTH_CONFIG = {
    "quick": 6,
    "default": 8,
    "deep": 40,
}

TRANSCRIPT_LIMITS = {
    "quick": 0,
    "default": 2,
    "deep": 8,
}

# Cumulative yt-dlp transcript-fetch stats for the current process. The final
# report only sees post-pruning items, so it can't distinguish "fetches failed
# (stale binary)" from "fetches succeeded but the videos were pruned later".
# quality_nudge reads these via last30days.py to suppress the stale-yt-dlp
# nudge when every attempted fetch actually succeeded. yt-dlp path only: the
# nudge diagnoses the local binary, not the ScrapeCreators API.
_TRANSCRIPT_FETCH_STATS = {"attempts": 0, "failures": 0}


def get_transcript_fetch_stats() -> Dict[str, int]:
    """Return cumulative transcript-fetch stats for this process."""
    return dict(_TRANSCRIPT_FETCH_STATS)


def reset_transcript_fetch_stats() -> None:
    """Reset cumulative transcript-fetch stats (used by tests)."""
    _TRANSCRIPT_FETCH_STATS["attempts"] = 0
    _TRANSCRIPT_FETCH_STATS["failures"] = 0

# Max words to keep from each transcript
TRANSCRIPT_MAX_WORDS = 5000

from . import dates, health, http, log, subproc
from .query import infer_query_intent

from .relevance import token_overlap_relevance as _compute_relevance

# yt-dlp transcript-fetch resilience. A non-zero yt-dlp exit means a real fetch
# error (rate-limit / bot-check / network), NOT "no captions" — yt-dlp exits 0
# with no file for a video that genuinely lacks the requested captions. So we
# capture the returncode, log a classified reason instead of failing silently,
# and retry transient errors a couple of times with a small per-video staggered
# backoff.
_TRANSCRIPT_MAX_RETRIES = 2
_TRANSCRIPT_BACKOFF_BASE = 2.0  # seconds; multiplied by (attempt + 1)
_TRANSCRIPT_TIMEOUT = 30  # seconds per yt-dlp attempt (keyless: no fallback to fail over to)
_TRANSCRIPT_FAST_TIMEOUT = 12  # seconds per attempt when a ScrapeCreators fallback exists
_SC_LOW_CREDIT_THRESHOLD = 50  # warn once ScrapeCreators credits drop below this
# Transient = worth retrying (and definitely not "no captions").
_TRANSIENT_RE = re.compile(
    r"429|too many requests|sign in to confirm|not a bot|rate.?limit"
    r"|temporarily|try again|timed out|timeout|connection|unable to (extract|download)"
    r"|failed to (extract|download)|got error|read error",
    re.IGNORECASE,
)
# A genuine no-captions signal — treat as no captions, never retry/surface.
_NO_CAPTION_RE = re.compile(
    r"no subtitles|requested (format|language)|there'?s no .*subtitles",
    re.IGNORECASE,
)


def extract_transcript_highlights(transcript: str, topic: str, limit: int = 5) -> list[str]:
    """Extract quotable highlights from a YouTube transcript.

    Filters filler (subscribe, welcome back, etc.), scores sentences by
    specificity (numbers, proper nouns, topic relevance), and returns
    the top highlights.
    """
    if not transcript:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', transcript)

    # Fallback for punctuation-free transcripts (common with auto-captions):
    # chunk into ~20-word segments so they pass the 8-50 word filter.
    if len(sentences) <= 1 and len(transcript.split()) > 50:
        words = transcript.split()
        sentences = [' '.join(words[i:i+20]) for i in range(0, len(words), 20)]

    filler = [
        r"^(hey |hi |what's up|welcome back|in today's video|don't forget to)",
        r"(subscribe|like and comment|hit the bell|check out the link|down below)",
        r"^(so |and |but |okay |alright |um |uh )",
        r"(thanks for watching|see you (next|in the)|bye)",
    ]

    topic_words = [w.lower() for w in topic.lower().split() if len(w) > 2]

    candidates = []
    for sent in sentences:
        sent = sent.strip()
        words = sent.split()
        if len(words) < 8 or len(words) > 50:
            continue
        if any(re.search(p, sent, re.IGNORECASE) for p in filler):
            continue

        score = 0
        if re.search(r'\d', sent):
            score += 2
        if re.search(r'[A-Z][a-z]+', sent):
            score += 1
        if '?' in sent:
            score += 1
        sent_lower = sent.lower()
        if any(w in sent_lower for w in topic_words):
            score += 2

        candidates.append((score, sent))

    candidates.sort(key=lambda x: -x[0])
    return [sent for _, sent in candidates[:limit]]


def _log(msg: str):
    log.source_log("YouTube", msg, tty_only=False)


def classify_run_failure(detail: str) -> str:
    """Map yt-dlp's text-only throttling and bot-gate errors."""
    text = detail.lower()
    if any(marker in text for marker in ("yt-dlp not installed", "yt-dlp not found")):
        return health.SKIPPED_UNCONFIGURED
    if any(
        marker in text
        for marker in ("http error 429", "confirm you're not a bot", "confirm you’re not a bot", "bot-gate")
    ):
        return health.RATE_LIMITED
    if any(marker in text for marker in ("sign in", "login required", "cookies are no longer valid")):
        return health.AUTH_FAILED
    return http.classify_failure(message=detail)


def is_ytdlp_installed() -> bool:
    """Check if yt-dlp is available locally, or if SSH routing is configured.

    When LAST30DAYS_YOUTUBE_SSH_HOST is set, returns True without a local check —
    yt-dlp lives on the remote host. Failures surface naturally on first use.
    """
    if _ytdlp_ssh_host():
        return True
    return shutil.which("yt-dlp") is not None


# Host aliases must be plain hostnames / SSH config aliases — no flags, no
# shell metacharacters. Rejects any value that could be reinterpreted by ssh
# (or the surrounding shell) as something other than a destination.
_SSH_HOST_ALIAS_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _ytdlp_ssh_host() -> Optional[str]:
    """Return SSH host alias if yt-dlp should be routed via SSH, else None.

    Set LAST30DAYS_YOUTUBE_SSH_HOST=<ssh-alias> (e.g. 'macmini') in the environment
    to route yt-dlp through SSH for residential IP egress. This bypasses
    YouTube's bot-wall on datacenter IPs (Hetzner, DigitalOcean, AWS, etc.)
    where ytsearch returns 0 results regardless of cookies.

    The remote host must have yt-dlp installed and reachable via the named
    SSH alias (configured in ~/.ssh/config). On macOS hosts with Homebrew,
    add brew shellenv to ~/.zshenv (not just ~/.zprofile) so non-login SSH
    shells find yt-dlp on PATH.

    Validation: host value must match ``[A-Za-z0-9._-]+``. Anything starting
    with ``-`` or containing shell/SSH metacharacters is rejected with a
    stderr warning and treated as unset, so a misconfigured or attacker-
    controlled value can't slip through as an SSH option flag or proxy command.
    The ``--`` option terminator in ``_wrap_ytdlp_cmd`` is a second line of
    defense; this regex closes the door on the env var ever reaching ssh
    in the first place.

    To use a value from ~/.config/last30days/.env, export it into the
    environment before invoking the engine, e.g. in a wrapper:
        set -a; source ~/.config/last30days/.env; set +a
        python3 last30days.py "..."
    """
    host = os.environ.get("LAST30DAYS_YOUTUBE_SSH_HOST", "").strip()
    if not host:
        return None
    if not _SSH_HOST_ALIAS_RE.match(host):
        sys.stderr.write(
            f"[youtube_yt] WARNING: LAST30DAYS_YOUTUBE_SSH_HOST={host!r} "
            "does not look like a plain hostname/alias; ignoring. "
            "Expected pattern: letters, digits, dot, underscore, hyphen.\n"
        )
        return None
    return host


def _wrap_ytdlp_cmd(cmd: List[str]) -> List[str]:
    """Wrap a yt-dlp command list with `ssh <host>` when SSH routing is set.

    Args are shell-quoted to survive the remote shell. Uses BatchMode=yes so
    a misconfigured key fails fast instead of hanging on a password prompt.
    The `--` option terminator prevents an SSH option-injection if
    LAST30DAYS_YOUTUBE_SSH_HOST were ever set to a value starting with `-`.
    """
    host = _ytdlp_ssh_host()
    if not host:
        return cmd
    remote_cmd = " ".join(shlex.quote(a) for a in cmd)
    return ["ssh", "-o", "BatchMode=yes", "--", host, remote_cmd]


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for YouTube search.

    NOTE: 'tips', 'tricks', 'tutorial', 'guide', 'review', 'reviews'
    are intentionally KEPT — they're YouTube content types that improve search.
    """
    from .query import VIRAL_NOISE, extract_core_subject
    # YouTube extends VIRAL_NOISE with temporal/meta words the planner emits
    # that don't appear in YouTube titles (months, recent year tokens, etc.).
    _YT_EXTRA = frozenset({
        'last', 'days', 'recent', 'recently', 'month', 'week',
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        '2025', '2026', '2027',
        'music', 'public', 'appearances', 'developments', 'discussions', 'coverage',
    })
    return extract_core_subject(topic, noise=VIRAL_NOISE | _YT_EXTRA)


def expand_youtube_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple YouTube search queries from a topic.

    Mirrors reddit.py's expand_reddit_queries() pattern:
    1. Extract core subject (strip noise words)
    2. Include original topic if different from core
    3. Add intent-specific OR-joined content-type variants
    4. Cap by depth: 1 for quick, 2 for default, 3 for deep

    Returns 1-3 query strings depending on depth.
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # Include cleaned original topic as variant if different from core
    original_clean = topic.strip().rstrip('?!.')
    if core.lower() != original_clean.lower() and len(original_clean.split()) <= 8:
        queries.append(original_clean)

    qtype = infer_query_intent(topic)

    # Intent-specific YouTube content-type variants
    if qtype == "opinion":
        queries.append(f"{core} review OR reaction OR breakdown")
    elif qtype == "product":
        queries.append(f"{core} review OR comparison OR unboxing")
    elif qtype == "comparison":
        queries.append(f"{core} vs OR compared OR head to head")
    elif qtype == "how_to":
        queries.append(f"{core} tutorial OR guide OR explained")
    else:
        # breaking_news / general — YouTube content types
        queries.append(f"{core} review OR reaction OR breakdown")

    # Deep depth: add full-length content variant
    if depth == "deep":
        queries.append(f"{core} full OR complete OR official")

    # Cap by depth budget
    caps = {"quick": 1, "default": 2, "deep": 3}
    cap = caps.get(depth, 2)
    return queries[:cap]


def search_youtube(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search YouTube via yt-dlp. No API key needed.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'

    Returns:
        Dict with 'items' list of video metadata dicts.
    """
    if not is_ytdlp_installed():
        return {"items": [], "error": "yt-dlp not installed"}

    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching YouTube for '{core_topic}' (since {from_date}, count={count})")

    # yt-dlp search with full metadata (no --flat-playlist so dates are real).
    # NOTE: --dateafter intentionally omitted — YouTube search returns
    # relevance-sorted results and strict date filtering returns 0 for
    # evergreen topics. Python soft filter (below) handles date filtering.
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "--no-cookies-from-browser",
        f"ytsearch{count}:{core_topic}",
        "--dump-json",
        "--no-warnings",
        "--no-download",
    ]
    cmd = _wrap_ytdlp_cmd(cmd)
    ssh_host = _ytdlp_ssh_host()

    try:
        result = subproc.run_with_timeout(cmd, timeout=120)
    except subproc.SubprocTimeout:
        _log("YouTube search timed out (120s)")
        return {"items": [], "error": "Search timed out"}
    except FileNotFoundError:
        return {"items": [], "error": "yt-dlp not found"}

    stdout = result.stdout
    if ssh_host and result.returncode != 0 and not stdout.strip():
        stderr_first = (result.stderr or "").strip().splitlines()
        first_line = stderr_first[0] if stderr_first else "(no stderr)"
        _log(
            f"YouTube search via SSH host {ssh_host!r} failed "
            f"(rc={result.returncode}): {first_line}"
        )
        return {
            "items": [],
            "error": f"SSH routing to {ssh_host!r} failed: {first_line}",
        }
    if not stdout.strip():
        _log("YouTube search returned 0 results")
        return {"items": []}

    # Parse JSON-per-line output
    items = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            video = json.loads(line)
        except json.JSONDecodeError:
            continue

        video_id = video.get("id", "")
        view_count = video.get("view_count") if video.get("view_count") is not None else 0
        like_count = video.get("like_count") if video.get("like_count") is not None else 0
        comment_count = video.get("comment_count") if video.get("comment_count") is not None else 0
        upload_date = video.get("upload_date", "")  # YYYYMMDD

        # Convert YYYYMMDD to YYYY-MM-DD
        date_str = None
        if upload_date and len(upload_date) == 8:
            date_str = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

        description = str(video.get("description", ""))[:500]
        items.append({
            "video_id": video_id,
            "title": video.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel_name": video.get("channel", video.get("uploader", "")),
            "date": date_str,
            "engagement": {
                "views": view_count,
                "likes": like_count,
                "comments": comment_count,
            },
            "duration": video.get("duration"),
            "relevance": _compute_relevance(core_topic, f"{video.get('title', '')} {description}"),
            "why_relevant": f"YouTube: {video.get('title', core_topic)[:60]}",
            "description": description,
        })

    # Soft date filter: prefer recent items but fall back to all if too few
    recent = [i for i in items if i["date"] and i["date"] >= from_date]
    if len(recent) >= 3:
        items = recent
        _log(f"Found {len(items)} videos within date range")
    else:
        _log(f"Found {len(items)} videos ({len(recent)} within date range, keeping all)")

    # Sort by views descending
    items.sort(key=lambda x: x["engagement"]["views"], reverse=True)

    return {"items": items}


def _clean_vtt(vtt_text: str) -> str:
    """Convert VTT subtitle format to clean plaintext."""
    # Strip VTT header
    text = re.sub(r'^WEBVTT.*?\n\n', '', vtt_text, flags=re.DOTALL)
    # Strip timestamps
    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*\n', '', text)
    # Strip position/alignment tags
    text = re.sub(r'<[^>]+>', '', text)
    # Strip cue numbers
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    # Deduplicate overlapping lines
    lines = text.strip().split('\n')
    seen = set()
    unique = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique.append(stripped)
    return re.sub(r'\s+', ' ', ' '.join(unique)).strip()


_YT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _fetch_transcript_direct(
    video_id: str,
    timeout: int = 30,
    status: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Fetch YouTube transcript via direct HTTP without yt-dlp.

    Scrapes the watch page HTML for the captions track URL in
    ytInitialPlayerResponse, then fetches the VTT subtitle file.

    Args:
        video_id: YouTube video ID
        timeout: HTTP request timeout in seconds
        status: Optional dict mutated to record per-video signals. Sets
            ``status["no_caption_tracks"] = True`` when the player response
            confirms the uploader has no caption tracks (vs. fetch failure).

    Returns:
        Raw VTT text, or None if captions are unavailable.
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "User-Agent": _YT_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Step 1: Fetch the watch page HTML
    req = urllib.request.Request(watch_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        _log(f"Direct transcript: failed to fetch watch page for {video_id}: {exc}")
        return None

    # Step 2: Extract captions URL from ytInitialPlayerResponse
    # YouTube embeds this as a JS variable in the page HTML
    match = re.search(
        r'ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;(?:\s*var\s|\s*<\/script>)',
        html,
    )
    if not match:
        # Fallback: try the JSON embedded in the script tag
        match = re.search(
            r'var\s+ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;',
            html,
        )
    if not match:
        _log(f"Direct transcript: no ytInitialPlayerResponse found for {video_id}")
        return None

    try:
        player_response = json.loads(match.group(1))
    except json.JSONDecodeError:
        _log(f"Direct transcript: failed to parse ytInitialPlayerResponse for {video_id}")
        return None

    # Navigate to caption tracks
    captions = player_response.get("captions", {})
    renderer = captions.get("playerCaptionsTracklistRenderer", {})
    caption_tracks = renderer.get("captionTracks", [])

    if not caption_tracks:
        _log(f"Direct transcript: no caption tracks for {video_id}")
        if status is not None:
            status["no_caption_tracks"] = True
        return None

    # Find English track (prefer exact 'en', then any en variant, then first track)
    base_url = None
    for track in caption_tracks:
        lang = track.get("languageCode", "")
        if lang == "en":
            base_url = track.get("baseUrl")
            break
    if not base_url:
        for track in caption_tracks:
            lang = track.get("languageCode", "")
            if lang.startswith("en"):
                base_url = track.get("baseUrl")
                break
    if not base_url:
        # Fall back to first available track
        base_url = caption_tracks[0].get("baseUrl")
    if not base_url:
        _log(f"Direct transcript: no baseUrl in caption tracks for {video_id}")
        return None

    # Step 3: Fetch the VTT subtitle file
    sep = "&" if "?" in base_url else "?"
    vtt_url = f"{base_url}{sep}fmt=vtt"
    vtt_req = urllib.request.Request(vtt_url, headers=headers)
    try:
        with urllib.request.urlopen(vtt_req, timeout=timeout) as resp:
            vtt_text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        _log(f"Direct transcript: failed to fetch VTT for {video_id}: {exc}")
        return None

    if not vtt_text or not vtt_text.strip():
        return None

    return vtt_text


def _fetch_transcript_ytdlp_via_ssh(video_id: str, ssh_host: str) -> Optional[str]:
    """Fetch transcript via yt-dlp on a remote SSH host (mktemp + cat pipeline)."""
    if not _SSH_HOST_ALIAS_RE.match(ssh_host):
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    quoted_url = shlex.quote(url)
    sub_langs = shlex.quote(_ytdlp_sub_langs())
    remote_script = (
        "set -e; "
        "TMPD=$(mktemp -d); "
        "yt-dlp --ignore-config --no-cookies-from-browser "
        f"--write-auto-subs --sub-lang {sub_langs} --sub-format vtt "
        "--skip-download --no-warnings "
        f'-o "$TMPD/%(id)s" {quoted_url} >/dev/null 2>&1 || true; '
        'VTT=$(find "$TMPD" -maxdepth 1 -name "*.vtt" 2>/dev/null | head -1); '
        '[ -n "$VTT" ] && cat "$VTT"; '
        'rm -rf "$TMPD"'
    )
    cmd = ["ssh", "-o", "BatchMode=yes", "--", ssh_host, remote_script]
    try:
        result = subproc.run_with_timeout(cmd, timeout=45)
    except subproc.SubprocTimeout:
        _log(f"SSH yt-dlp transcript timed out for {video_id} via {ssh_host!r}")
        return None
    except FileNotFoundError:
        _log("ssh executable not found; cannot route transcript fetch")
        return None
    out = result.stdout or ""
    if not out.strip().startswith("WEBVTT"):
        if result.returncode != 0 and result.stderr:
            first_line = result.stderr.strip().splitlines()[0]
            _log(
                f"SSH yt-dlp transcript via {ssh_host!r} failed for "
                f"{video_id} (rc={result.returncode}): {first_line}"
            )
        return None
    return out


def _ytdlp_sub_langs() -> str:
    """Caption languages to try, from LAST30DAYS_YT_SUB_LANGS (default en,es,pt)."""
    raw = os.environ.get("LAST30DAYS_YT_SUB_LANGS", "").strip()
    if not raw:
        return "en,es,pt"
    return ",".join(code.strip().lower() for code in raw.split(",") if code.strip()) or "en,es,pt"


def _pick_ytdlp_vtt(video_id: str, temp_dir: str, priority: List[str]) -> Optional[Path]:
    """Return the best on-disk VTT match for video_id, preferring priority order."""
    matches = list(Path(temp_dir).glob(f"{video_id}*.vtt"))
    if not matches:
        return None
    priority_index = {code: i for i, code in enumerate(priority)}

    def rank(p: Path) -> int:
        stem = p.stem
        suffix = stem[len(video_id) + 1:] if stem.startswith(video_id + ".") else ""
        code = suffix.split("-")[0].split(".")[0]
        return priority_index.get(code, len(priority_index))

    return sorted(matches, key=rank)[0]


def _transcript_backoff(video_id: str, attempt: int) -> float:
    """Backoff seconds before a transcript retry.

    Staggered per-video (a sub-second offset derived from the id) so parallel
    workers don't retry in lockstep and re-trip YouTube's limiter.
    """
    offset = (sum(ord(c) for c in video_id) % 1000) / 1000.0  # 0.0–1.0s
    return _TRANSCRIPT_BACKOFF_BASE * (attempt + 1) + offset


def _read_vtt(video_id: str, temp_dir: str) -> Optional[str]:
    """Return the VTT text yt-dlp wrote for ``video_id``, or None if absent."""
    vtt_path = _pick_ytdlp_vtt(video_id, temp_dir, _ytdlp_sub_langs().split(","))
    if vtt_path is None:
        return None

    try:
        return vtt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _fetch_transcript_ytdlp(
    video_id: str,
    temp_dir: str,
    status: Optional[Dict[str, Any]] = None,
    fast_fail: bool = False,
) -> Optional[str]:
    """Fetch transcript using yt-dlp (original implementation).

    Args:
        video_id: YouTube video ID
        temp_dir: Temporary directory for subtitle files
        status: Optional dict mutated to record a yt-dlp failure reason
            (``status["ytdlp_error"]``) so the caller can tell a real fetch
            error (rate-limit / bot-check / network / timeout) apart from a
            video that genuinely has no captions, and skip the misleading
            "no captions found" log + the YouTube-blocked HTTP fallback.
        fast_fail: When True, a ScrapeCreators fallback is available, so a
            transient failure (429 / bot-gate) should fail over fast rather
            than retry into the same rate limit. Collapses to a single attempt
            with a shorter per-attempt timeout. The slow thing in a run is
            yt-dlp retrying, not the fast SC fetch, so this is what keeps
            "yt-dlp first" from reintroducing the multi-minute hang.

    Returns:
        Raw VTT text, or None if no captions are available or the fetch failed.
        On a hard (non-no-caption) failure, sets ``status["ytdlp_error"]``.
    """
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "--no-cookies-from-browser",
        "--write-auto-subs",
        "--sub-lang", _ytdlp_sub_langs(),
        "--sub-format", "vtt",
        "--skip-download",
        "--no-warnings",
        "-o", f"{temp_dir}/%(id)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    timeout = _TRANSCRIPT_FAST_TIMEOUT if fast_fail else _TRANSCRIPT_TIMEOUT
    attempts = 1 if fast_fail else _TRANSCRIPT_MAX_RETRIES + 1
    last_reason: Optional[str] = None
    for attempt in range(attempts):
        try:
            result = subproc.run_with_timeout(cmd, timeout=timeout)
        except subproc.SubprocTimeout:
            last_reason = f"timed out after {timeout}s"
            _log(f"yt-dlp transcript timed out after {timeout}s for {video_id} "
                 f"(attempt {attempt + 1}/{attempts})")
            if attempt < attempts - 1:
                time.sleep(_transcript_backoff(video_id, attempt))
                continue
            break
        except FileNotFoundError:
            # yt-dlp binary missing — not transient, not retryable.
            if status is not None:
                status["ytdlp_error"] = "yt-dlp not found"
            return None

        if result.returncode == 0:
            vtt = _read_vtt(video_id, temp_dir)
            if vtt is not None:
                return vtt
            # Exit 0 with no file == the uploader has no matching captions.
            # Genuine no-captions: return quietly (caller may still try direct).
            return None

        # Non-zero exit, but yt-dlp may have written a usable VTT before the
        # failing language errored. With the default `--sub-lang en,es,pt`, an
        # English video fetches `en` fine, then `es`/`pt` hit a 429 and yt-dlp
        # exits non-zero — yet the `en` track is already on disk. A partial
        # success is still a real transcript, so salvage any VTT before
        # classifying this as an error (and, worse, retrying straight back into
        # the same rate limit). This is the root cause of the 0/N transcript
        # runs reported when every video had captions.
        partial_vtt = _read_vtt(video_id, temp_dir)
        if partial_vtt is not None:
            return partial_vtt

        # Non-zero exit == a real error worth classifying & surfacing.
        stderr = (result.stderr or "").strip()
        snippet = (stderr.splitlines()[-1][:200] if stderr
                   else f"exit {result.returncode}")
        if _NO_CAPTION_RE.search(stderr):
            # yt-dlp can exit non-zero when the requested language is absent.
            # Treat as genuine no-captions, not an error worth retrying.
            return None
        last_reason = snippet
        if _TRANSIENT_RE.search(stderr) and attempt < attempts - 1:
            _log(f"yt-dlp transcript transient failure for {video_id} "
                 f"(attempt {attempt + 1}/{attempts}): {snippet}")
            time.sleep(_transcript_backoff(video_id, attempt))
            continue
        # Non-transient, or retries exhausted — surface the real reason.
        _log(f"yt-dlp transcript failed for {video_id} "
             f"(exit {result.returncode}): {snippet}")
        break

    if status is not None and last_reason is not None:
        status["ytdlp_error"] = last_reason
    return None


def _should_try_sc_transcript(status: Optional[Dict[str, Any]]) -> bool:
    """Whether to spend a ScrapeCreators credit after the keyless cascade failed.

    Skip when the keyless path *proved* the uploader has no caption track
    (``no_caption_tracks``): SC would also return nothing, so a credit would be
    wasted. A transient hard failure (``ytdlp_error``: 429 / bot-gate / timeout)
    is a false negative, so SC is worth trying.
    """
    st = status or {}
    return not st.get("no_caption_tracks")


def fetch_transcript(
    video_id: str,
    temp_dir: str,
    status: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Optional[str]:
    """Fetch auto-generated transcript for a YouTube video.

    Uses yt-dlp when available (preferred, more robust). Falls back to
    direct HTTP transcript fetching when yt-dlp is not installed, and finally
    to the ScrapeCreators transcript endpoint when a key is present and the
    keyless cascade comes back empty.

    Args:
        video_id: YouTube video ID
        temp_dir: Temporary directory for subtitle files
        status: Optional dict mutated by the direct-HTTP path to record
            per-video signals like ``no_caption_tracks``. Used to surface a
            captions-disabled count so the quality nudge avoids false-positive
            "stale yt-dlp" flags.
        token: Optional ScrapeCreators API key. When present, yt-dlp fails over
            fast (see ``_fetch_transcript_ytdlp`` ``fast_fail``) and a true hard
            failure falls back to the SC transcript endpoint. A credit is only
            spent on a genuine yt-dlp failure, never on success and never on a
            video proven to have no captions. None preserves keyless behavior.

    Returns:
        Plaintext transcript string, or None if no captions available.
    """
    raw_vtt = None
    ssh_host = _ytdlp_ssh_host()
    if ssh_host and is_ytdlp_installed():
        raw_vtt = _fetch_transcript_ytdlp_via_ssh(video_id, ssh_host)
        if not raw_vtt:
            _log(f"SSH yt-dlp transcript failed for {video_id}, trying direct HTTP fallback")
            raw_vtt = _fetch_transcript_direct(video_id, status=status)
    elif is_ytdlp_installed():
        raw_vtt = _fetch_transcript_ytdlp(
            video_id, temp_dir, status=status, fast_fail=bool(token),
        )
        if not raw_vtt:
            ytdlp_error = (status or {}).get("ytdlp_error")
            if ytdlp_error:
                # Hard failure (429 / bot-gate / timeout). The direct-HTTP
                # fallback is also YouTube-blocked, so skip it and let the
                # ScrapeCreators fallback below handle it when a key is present.
                _log(f"Transcript fetch failed for {video_id}: {ytdlp_error}")
            else:
                _log(f"yt-dlp found no captions for {video_id}, trying direct HTTP fallback")
                raw_vtt = _fetch_transcript_direct(video_id, status=status)
    else:
        _log("yt-dlp not installed, using direct HTTP transcript fetch")
        raw_vtt = _fetch_transcript_direct(video_id, status=status)

    if raw_vtt:
        transcript = _clean_vtt(raw_vtt)
        # Truncate to max words
        words = transcript.split()
        if len(words) > TRANSCRIPT_MAX_WORDS:
            transcript = ' '.join(words[:TRANSCRIPT_MAX_WORDS]) + '...'
        return transcript if transcript else None

    # Keyless cascade produced nothing. When a ScrapeCreators key is present and
    # the video was not proven caption-less, fall back to the SC transcript
    # endpoint (fetched server-side: no 429, cookies, or PO tokens). Returns
    # already-cleaned, word-capped plaintext.
    if token and _should_try_sc_transcript(status):
        sc_transcript = _sc_fetch_transcript(video_id, token)
        if sc_transcript:
            return sc_transcript

    _log(f"No transcript available for {video_id}")
    return None


def fetch_transcripts_parallel(
    video_ids: List[str],
    max_workers: int = 5,
    out_captions_disabled: Optional[Set[str]] = None,
    token: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Fetch transcripts for multiple videos in parallel.

    Args:
        video_ids: List of YouTube video IDs
        max_workers: Max parallel fetches
        out_captions_disabled: Optional set mutated to record video_ids whose
            uploader confirmed no caption tracks (vs. transient fetch failures).
            Backward-compatible: callers that don't care can omit.
        token: Optional ScrapeCreators API key, threaded to each
            ``fetch_transcript`` so the per-video SC fallback activates on
            yt-dlp failure. None preserves keyless behavior.

    Returns:
        Dict mapping video_id to transcript text (or None).
    """
    if not video_ids:
        return {}

    _log(f"Fetching transcripts for {len(video_ids)} videos")

    results = {}
    statuses: Dict[str, Dict[str, Any]] = {vid: {} for vid in video_ids}
    with tempfile.TemporaryDirectory() as temp_dir:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                http.submit_with_context(
                    executor, fetch_transcript, vid, temp_dir, statuses[vid], token,
                ): vid
                for vid in video_ids
            }
            for future in as_completed(futures):
                vid = futures[future]
                try:
                    results[vid] = future.result()
                except OSError as exc:
                    _log(f"Transcript fetch error for {vid}: {exc}")
                    results[vid] = None
                except Exception as exc:
                    _log(f"Unexpected transcript error for {vid}: {type(exc).__name__}: {exc}")
                    results[vid] = None

    if out_captions_disabled is not None:
        for vid, st in statuses.items():
            if st.get("no_caption_tracks"):
                out_captions_disabled.add(vid)

    got = sum(1 for v in results.values() if v)
    errors = sum(1 for v in results.values() if v is None)
    _log(f"Got transcripts for {got}/{len(video_ids)} videos ({errors} failed)")
    return results


def backfill_transcripts(
    items: List[Any], topic: str = "", depth: str = "default",
    token: Optional[str] = None,
) -> None:
    """Second-pass transcript fetch for finalized items that lack one (#542).

    ``token`` is the optional ScrapeCreators key, threaded to
    ``fetch_transcripts_parallel`` so the SC fallback covers backfill survivors
    that yt-dlp can't fetch. None preserves keyless behavior.
    """
    limit = TRANSCRIPT_LIMITS.get(depth, TRANSCRIPT_LIMITS["default"])
    if limit <= 0 or not items or not is_ytdlp_installed():
        return
    have = sum(
        1 for it in items
        if it.metadata.get("transcript_highlights") or it.metadata.get("transcript_snippet")
    )
    need = limit - have
    if need <= 0:
        return
    missing = [
        it for it in items
        if it.item_id
        and not it.metadata.get("transcript_highlights")
        and not it.metadata.get("transcript_snippet")
        and not it.metadata.get("captions_disabled")
    ]
    attempts = missing[: need * 3]
    if not attempts:
        return
    _log(f"Backfilling transcripts for {len(attempts)} finalized videos (target: {need})")
    captions_disabled: Set[str] = set()
    transcripts = fetch_transcripts_parallel(
        [it.item_id for it in attempts],
        out_captions_disabled=captions_disabled,
        token=token,
    )
    for it in attempts:
        if it.item_id in captions_disabled:
            it.metadata["captions_disabled"] = True
            continue
        transcript = transcripts.get(it.item_id)
        if not transcript:
            continue
        it.metadata["transcript_snippet"] = transcript
        highlights = extract_transcript_highlights(transcript, topic)
        if highlights:
            it.metadata["transcript_highlights"] = highlights
        if not it.snippet:
            it.snippet = " ".join(transcript.split()[:80])


def _transcript_candidate_sort_key(item: dict) -> tuple:
    """Sort key for transcript candidate selection.

    Combines views with recency so that recent videos (which survive
    strict_recent freshness pruning) are prioritised over old high-view
    videos whose transcripts would be discarded downstream.
    """
    views = item.get("engagement", {}).get("views", 0) or 0
    recency = dates.recency_score(item.get("date", ""))
    return (views, recency)


def search_and_transcribe(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Full YouTube search: find videos, then fetch transcripts for top results.

    Uses expand_youtube_queries() to generate multiple search queries,
    runs yt-dlp for each, and merges/deduplicates results by video ID.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: Optional ScrapeCreators key for the per-video transcript
            fallback (threaded to fetch_transcripts_parallel).

    Returns:
        Dict with 'items' list. Each item has a 'transcript_snippet' field.
    """
    # Step 1: Multi-query search — run yt-dlp for each expanded query
    queries = expand_youtube_queries(topic, depth)
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    for q in queries:
        search_result = search_youtube(q, from_date, to_date, depth)
        for item in search_result.get("items", []):
            vid = item.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                items.append(item)

    # Sort merged results by views descending
    items.sort(key=lambda x: x.get("engagement", {}).get("views", 0), reverse=True)

    if not items:
        return search_result

    # Step 2: Fetch transcripts for top videos.
    # Sort candidates by a combination of views and recency so that recent
    # videos (which survive strict_recent pruning) are not starved of
    # transcript budget by older high-view-count outliers.
    # Try more candidates than the limit because some videos (music videos,
    # short clips) lack captions. Attempt up to 3x the limit so we have a
    # good chance of reaching the target number of successful transcripts.
    transcript_limit = TRANSCRIPT_LIMITS.get(depth, TRANSCRIPT_LIMITS["default"])
    transcripts: Dict[str, Optional[str]] = {}
    captions_disabled_ids: Set[str] = set()
    if transcript_limit > 0:
        attempt_count = min(len(items), transcript_limit * 3)
        transcript_candidates = sorted(
            items, key=_transcript_candidate_sort_key, reverse=True,
        )
        candidate_ids = [item["video_id"] for item in transcript_candidates[:attempt_count]]
        _log(f"Fetching transcripts for up to {attempt_count} videos (target: {transcript_limit}): {candidate_ids}")
        transcripts = fetch_transcripts_parallel(
            candidate_ids, out_captions_disabled=captions_disabled_ids,
            token=token,
        )
        # Record fetch outcomes (captions-disabled videos can never succeed,
        # so they don't count as failures) for the stale-yt-dlp nudge.
        _TRANSCRIPT_FETCH_STATS["attempts"] += len(candidate_ids)
        _TRANSCRIPT_FETCH_STATS["failures"] += sum(
            1 for vid in candidate_ids
            if not transcripts.get(vid) and vid not in captions_disabled_ids
        )
    else:
        _log(f"Transcript limit is 0 for depth={depth}, skipping transcript fetch")

    # Step 3: Attach transcripts and extract highlights. Mark captions_disabled
    # so quality_nudge can subtract those videos from the degraded-ratio
    # denominator (uploader-disabled captions can never produce a transcript;
    # counting them was producing false-positive stale-yt-dlp nudges).
    core_topic = _extract_core_subject(topic)
    for item in items:
        vid = item["video_id"]
        transcript = transcripts.get(vid)
        item["transcript_snippet"] = transcript or ""
        item["transcript_highlights"] = extract_transcript_highlights(
            transcript or "", core_topic,
        )
        item["captions_disabled"] = vid in captions_disabled_ids

    return {"items": items}


def parse_youtube_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse YouTube search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])


# ---------------------------------------------------------------------------
# ScrapeCreators YouTube API support
# ---------------------------------------------------------------------------

SCRAPECREATORS_YT_BASE = "https://api.scrapecreators.com/v1/youtube"


def _total_engagement(item: Dict[str, Any]) -> int:
    """Combined engagement score for ranking which videos to enrich."""
    eng = item.get("engagement", {})
    views = eng.get("views", 0) or 0
    likes = eng.get("likes", 0) or 0
    comments = eng.get("comments", 0) or 0
    return views + likes + comments


def enrich_with_comments(
    items: List[Dict[str, Any]],
    token: str,
    max_videos: int = 3,
    max_comments: int = 5,
) -> List[Dict[str, Any]]:
    """Enrich top YouTube videos with comment data from ScrapeCreators.

    For the top N videos by engagement, fetches comments via the SC API
    and attaches them as a ``top_comments`` field on each item.

    Args:
        items: YouTube items from search_and_transcribe() or search_youtube_sc()
        token: ScrapeCreators API key
        max_videos: How many videos to enrich with comments
        max_comments: Max comments to keep per video

    Returns:
        Items list (mutated in place) with top_comments added to enriched items.
    """
    if not items or not token or max_videos <= 0:
        return items

    ranked = sorted(items, key=_total_engagement, reverse=True)
    top_items = ranked[:max_videos]
    _log(f"Enriching comments for {len(top_items)} YouTube videos")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _enrich_one(item: dict) -> bool:
        video_id = item.get("video_id", "")
        if not video_id:
            return False
        try:
            comments = _fetch_video_comments(video_id, token, max_comments)
            if comments:
                item["top_comments"] = comments
                return True
        except Exception as exc:
            _log(f"Comment enrichment failed for {video_id}: {exc}")
        return False

    enriched_count = 0
    with ThreadPoolExecutor(max_workers=min(4, len(top_items))) as executor:
        futures = {http.submit_with_context(executor, _enrich_one, item): item for item in top_items}
        for future in as_completed(futures):
            if future.result():
                enriched_count += 1

    _log(f"Enriched {enriched_count}/{len(top_items)} videos with comments")
    return items


def _fetch_video_comments(
    video_id: str,
    token: str,
    max_comments: int = 5,
) -> List[Dict[str, Any]]:
    """Fetch comments for a single YouTube video via ScrapeCreators.

    Args:
        video_id: YouTube video ID
        token: ScrapeCreators API key
        max_comments: Maximum comments to return

    Returns:
        List of comment dicts with author, text, likes, date.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        data = http.get(
            f"{SCRAPECREATORS_YT_BASE}/video/comments",
            params={"url": video_url},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as exc:
        _log(f"Comment fetch error for {video_id}: {exc}")
        return []

    raw_comments = data.get("comments", data.get("data", []))
    comments = []
    for c in raw_comments[:max_comments]:
        text = c.get("text") or c.get("body") or c.get("content", "")
        if not text:
            continue

        # SC returns author as {"name": "@handle", ...}; legacy mocks may pass a string.
        author = c.get("author") or c.get("author_name", "")
        if isinstance(author, dict):
            author = author.get("name") or author.get("handle") or ""

        # SC nests likes under engagement.likes; legacy shapes used top-level keys.
        engagement = c.get("engagement") or {}
        likes = c.get("likes")
        if likes is None:
            likes = engagement.get("likes", 0) if isinstance(engagement, dict) else 0
        if not likes:
            likes = c.get("vote_count", 0)

        date = (
            c.get("date")
            or c.get("published_at")
            or c.get("publishedTime")
            or c.get("publishedTimeText", "")
        )

        comments.append({
            "author": author,
            "text": text[:400],
            "likes": likes,
            "date": date,
        })

    return comments


def search_youtube_sc(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
) -> Dict[str, Any]:
    """Search YouTube via ScrapeCreators API (fallback when yt-dlp is unavailable).

    Uses SC keyword search to find videos and SC transcript endpoint to
    fetch transcripts. Called by pipeline.py when yt-dlp fails.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key

    Returns:
        Dict with 'items' list of video metadata dicts.
    """
    if not token:
        return {"items": [], "error": "No SCRAPECREATORS_API_KEY configured"}

    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)
    _log(f"Searching YouTube via ScrapeCreators for '{core_topic}' (depth={depth})")

    # Step 1: Search
    raw_items = _sc_youtube_search(core_topic, token)
    if not raw_items:
        _log("SC YouTube search returned 0 results")
        return {"items": []}

    # Parse into normalized items
    items = []
    for i, raw in enumerate(raw_items[:count]):
        video_id = (
            raw.get("id") or raw.get("video_id") or raw.get("videoId") or ""
        )
        title = raw.get("title", "")
        channel = raw.get("channel") or raw.get("channel_name") or raw.get("uploader", "")
        description = str(raw.get("description", ""))[:500]
        view_count = raw.get("view_count") or raw.get("views", 0)
        like_count = raw.get("like_count") or raw.get("likes", 0)
        comment_count = raw.get("comment_count") or raw.get("comments", 0)

        # Date: try multiple field names
        date_str = raw.get("upload_date") or raw.get("date") or raw.get("published_at", "")
        if date_str and len(date_str) == 8 and date_str.isdigit():
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        elif date_str and "T" in date_str:
            date_str = date_str[:10]

        url = raw.get("url", "")
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"

        items.append({
            "video_id": video_id,
            "title": title,
            "url": url,
            "channel_name": channel,
            "date": date_str if date_str else None,
            "engagement": {
                "views": view_count or 0,
                "likes": like_count or 0,
                "comments": comment_count or 0,
            },
            "duration": raw.get("duration"),
            "relevance": _compute_relevance(core_topic, f"{title} {description}"),
            "why_relevant": f"YouTube: {title[:60]}" if title else f"YouTube: {core_topic}",
            "description": description,
        })

    # Soft date filter
    recent = [i for i in items if i["date"] and i["date"] >= from_date]
    if len(recent) >= 3:
        items = recent
        _log(f"Found {len(items)} videos within date range")
    else:
        _log(f"Found {len(items)} videos ({len(recent)} within date range, keeping all)")

    # Sort by views
    items.sort(key=lambda x: x["engagement"]["views"], reverse=True)

    # Step 2: Fetch transcripts for top videos
    transcript_limit = TRANSCRIPT_LIMITS.get(depth, TRANSCRIPT_LIMITS["default"])
    if transcript_limit > 0 and items:
        attempt_count = min(len(items), transcript_limit * 3)
        # Same in-window-first ordering as search_and_transcribe(): don't let
        # an out-of-window back-catalog (kept by the soft date filter above)
        # consume the transcript budget of videos the freshness scorer keeps.
        in_window = [i for i in items if i.get("date") and i["date"] >= from_date]
        out_of_window = [i for i in items if not (i.get("date") and i["date"] >= from_date)]
        _log(f"Fetching SC transcripts for up to {attempt_count} videos (target: {transcript_limit})")
        for item in (in_window + out_of_window)[:attempt_count]:
            vid = item["video_id"]
            if not vid:
                continue
            transcript = _sc_fetch_transcript(vid, token)
            item["transcript_snippet"] = transcript or ""
            item["transcript_highlights"] = extract_transcript_highlights(
                transcript or "", core_topic,
            )
    else:
        for item in items:
            item["transcript_snippet"] = ""
            item["transcript_highlights"] = []

    _log(f"SC YouTube: {len(items)} videos returned")
    return {"items": items}


def _sc_youtube_search(keyword: str, token: str) -> List[Dict[str, Any]]:
    """Call ScrapeCreators YouTube search endpoint.

    Args:
        keyword: Search keyword
        token: ScrapeCreators API key

    Returns:
        List of raw video dicts from the API.
    """
    try:
        # SC's /v1/youtube/search rejects ?keyword= with HTTP 400; the canonical
        # parameter for that endpoint is `query`. Other SC endpoints use their
        # own per-endpoint param names so this was the lone outlier.
        data = http.get(
            f"{SCRAPECREATORS_YT_BASE}/search",
            params={"query": keyword},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
        return data.get("videos", data.get("data", data.get("items", [])))
    except Exception as exc:
        _log(f"SC YouTube search error: {exc}")
        return []


def _sc_segment_text(seg: Any) -> str:
    """Extract caption text from a ScrapeCreators transcript segment.

    The transcript endpoint returns a list of segment dicts
    (``{text, startMs, endMs}``); older/simpler shapes return plain strings.
    Pull the ``text`` field for dicts so segment metadata is not stringified
    into the output (``{'text': ...}`` garbage).
    """
    if isinstance(seg, dict):
        # `or ""` (not a get default): a present-but-null `text` returns None,
        # which would stringify to the literal "None" for silent/music segments.
        return str(seg.get("text") or "")
    return str(seg)


def _warn_low_sc_credits(data: Dict[str, Any]) -> None:
    """Surface a low-credit warning from a ScrapeCreators response, if present."""
    credits = data.get("credits_remaining")
    if isinstance(credits, (int, float)) and not isinstance(credits, bool):
        if credits < _SC_LOW_CREDIT_THRESHOLD:
            _log(f"ScrapeCreators credits low: {int(credits)} remaining "
                 f"(below {_SC_LOW_CREDIT_THRESHOLD})")


def _sc_fetch_transcript(video_id: str, token: str) -> Optional[str]:
    """Fetch transcript for a YouTube video via ScrapeCreators.

    Args:
        video_id: YouTube video ID
        token: ScrapeCreators API key

    Returns:
        Plaintext transcript string, or None if unavailable.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        data = http.get(
            f"{SCRAPECREATORS_YT_BASE}/video/transcript",
            params={"url": video_url},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=1,
        )
    except Exception as exc:
        _log(f"SC transcript error for {video_id}: {exc}")
        return None

    _warn_low_sc_credits(data)

    transcript = data.get("transcript")
    if not transcript:
        return None

    if isinstance(transcript, list):
        transcript = " ".join(_sc_segment_text(seg) for seg in transcript).strip()

    # Clean VTT formatting if present
    transcript = _clean_vtt(transcript)

    # Truncate to max words
    words = transcript.split()
    if len(words) > TRANSCRIPT_MAX_WORDS:
        transcript = " ".join(words[:TRANSCRIPT_MAX_WORDS]) + "..."

    return transcript if transcript else None
