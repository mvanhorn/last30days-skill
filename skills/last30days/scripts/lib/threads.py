"""Threads keyword search for /last30days through a headless browser.

Keyless: no ScrapeCreators (or any other) API key. The search runs
``lib/threads_browser.py`` as a child process; that helper drives
Playwright against the public, logged-out ``threads.com/search`` page and
returns the posts the page renders. Running it out of process keeps
Playwright's event loop off the pipeline's worker threads, and lets the
research deadline kill a stuck browser through ``subproc``.

Interpreter, first match wins:
  1. ``LAST30DAYS_THREADS_PYTHON`` - a Python that has Playwright installed.
  2. The running interpreter, when it can import ``playwright``.
  3. ``uv run --with playwright`` - no install step beyond having uv.

The browser is Playwright's Chromium (``python -m playwright install
chromium``) with an installed Google Chrome as the fallback.
``LAST30DAYS_THREADS_BROWSER=off`` turns the browser lane off. Opt-in via
INCLUDE_SOURCES=threads or ``--search threads``.

Optional official API (lib/threads_api.py): ``THREADS_ACCESS_TOKEN`` adds
Graph ``keyword_search`` results, and ``THREADS_APP_TOKEN`` reports a
matching US/JP trending topic (and its posts with advanced access). Either
lane alone is enough; with both, browser copies win on duplicates because
they carry the like/reply/repost counts the API does not return.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import math
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import dates, log, subproc, threads_api
from .relevance import token_overlap_relevance as _compute_relevance

BROWSER_ENV = "LAST30DAYS_THREADS_BROWSER"
PYTHON_ENV = "LAST30DAYS_THREADS_PYTHON"
# Run by path (it never imports lib), so it ships inside lib/ and every
# bundle that vendors lib/ (the MCP .mcpb) carries it.
HELPER = Path(__file__).resolve().parent / "threads_browser.py"
PLAYWRIGHT_REQ = "playwright>=1.49,<2"

# results: posts kept; modes: helper search tabs; timeout: helper budget (s).
DEPTH_CONFIG = {
    "quick":   {"results": 10, "modes": "recent", "timeout": 40, "scrolls": 0},
    "default": {"results": 20, "modes": "recent,top", "timeout": 60, "scrolls": 1},
    "deep":    {"results": 40, "modes": "recent,top", "timeout": 90, "scrolls": 2},
}
# Extra time on top of the helper budget for `uv run` to resolve Playwright
# (first run downloads it) and for the browser to start.
_LAUNCH_GRACE = 45
_CAPTURE_LIMIT_BYTES = 4 * 1024 * 1024
# Whole budget for the official API calls (search + trends), which run beside
# the browser.
_API_BUDGET = 25
_OFF_VALUES = {"0", "off", "false", "no", "disable", "disabled"}
# The child gets no API keys: only what Python, uv and Playwright need.
_CHILD_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP", "LANG",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA",
    "USERPROFILE", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
}
_CHILD_ENV_PREFIXES = ("LC_", "PLAYWRIGHT_", "UV_", "XDG_")


def _log(msg: str):
    log.source_log("Threads", msg, tty_only=False)


def browser_enabled(config: Optional[Dict[str, Any]]) -> bool:
    """False when LAST30DAYS_THREADS_BROWSER is set to an off value."""
    raw = (config or {}).get(BROWSER_ENV)
    if raw is None:
        raw = os.environ.get(BROWSER_ENV)
    return str(raw or "").strip().lower() not in _OFF_VALUES


def _uv_path() -> Optional[str]:
    return shutil.which("uv")


def _find_browser() -> bool:
    """True when a browser the helper can launch is on disk.

    The helper tries Playwright's own Chromium, then ``channel="chrome"``
    (an installed Google Chrome). ``uv run --with playwright`` installs the
    Python package only, never a browser, so uv on PATH alone is not enough.
    """
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if custom == "0":
        return True  # browsers live inside the playwright package; trust it
    home = Path.home()
    caches = [Path(custom)] if custom else [
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
        Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ms-playwright",
    ]
    for cache in caches:
        try:
            if any(d.name.startswith("chromium") for d in cache.iterdir() if d.is_dir()):
                return True
        except OSError:
            continue
    chrome = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        Path("/opt/google/chrome/chrome"),
    ]
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if root:
            chrome.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    if any(path.is_file() for path in chrome):
        return True
    return any(shutil.which(name) for name in ("google-chrome", "google-chrome-stable"))


@functools.lru_cache(maxsize=1)
def browser_installed() -> bool:
    return _find_browser()


def browser_command(config: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Command prefix that runs the helper with Playwright, or None."""
    if not browser_enabled(config) or not HELPER.is_file() or not browser_installed():
        return None
    override = str((config or {}).get(PYTHON_ENV) or os.environ.get(PYTHON_ENV) or "").strip()
    if override:
        # A bare name resolves through PATH; a path must be an executable.
        resolved = shutil.which(override) or (override if os.access(override, os.X_OK) else None)
        return [resolved, str(HELPER)] if resolved else None
    if importlib.util.find_spec("playwright") is not None:
        return [sys.executable, str(HELPER)]
    uv = _uv_path()
    if uv:
        return [uv, "run", "--quiet", "--no-project", "--with", PLAYWRIGHT_REQ,
                "python", str(HELPER)]
    return None


def _describe_browser(config: Optional[Dict[str, Any]]) -> str:
    if not browser_enabled(config):
        return f"browser off ({BROWSER_ENV})"
    cmd = browser_command(config)
    if cmd is None:
        if HELPER.is_file() and not browser_installed():
            return "needs a browser: python -m playwright install chromium (or Google Chrome)"
        return "needs Playwright (pip install playwright) or uv on PATH"
    if len(cmd) > 1 and cmd[1] == "run":
        return "Playwright via uv run (keyless)"
    return f"Playwright via {Path(cmd[0]).name} (keyless)"


def describe_backend(config: Optional[Dict[str, Any]]) -> str:
    """One-line human description of how Threads would run here."""
    parts = [_describe_browser(config)]
    if threads_api.user_token_present(config):
        parts.append("official API keyword search")
    if threads_api.app_token_present(config):
        parts.append(f"trending topics ({threads_api.trends_country(config)})")
    return " + ".join(parts)


def is_available(config: Optional[Dict[str, Any]]) -> bool:
    """True when the browser lane or the API keyword-search lane can run."""
    return browser_command(config) is not None or threads_api.user_token_present(config)


def _child_env() -> Dict[str, str]:
    env = {
        k: v for k, v in os.environ.items()
        if k.upper() in _CHILD_ENV_ALLOWLIST or k.upper().startswith(_CHILD_ENV_PREFIXES)
    }
    # A pipe on Windows defaults to the ANSI code page; keep stdio UTF-8.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _extract_core_subject(topic: str) -> str:
    """Extract the core subject from a verbose query for Threads search.

    Threads' own search answers "No results." to long multi-word queries
    (four words already fail, three usually work) and to leaked boolean
    operators (the planner emits "A OR B"). Strip operators and noise, cap
    to the three most salient words.
    """
    from .query import SOCIAL_NOISE, extract_core_subject
    core = extract_core_subject(topic, noise=SOCIAL_NOISE, max_words=3)
    return " ".join(core.rstrip("?!.").split()[:3])


def _query_ladder(core: str) -> List[str]:
    """The query, then shorter prefixes, for Threads' long-query blind spot.

    A lone word is too broad for a multi-word topic, so the ladder stops at
    two words.
    """
    words = core.split()
    if len(words) <= 1:
        return [core] if core else []
    return [" ".join(words[:n]) for n in range(len(words), 1, -1)]


def _parse_date(item: Dict[str, Any]) -> Optional[str]:
    """Parse a Threads post date (unix ``taken_at``) to YYYY-MM-DD."""
    for key in ("taken_at", "create_time", "created_at", "published_at", "date"):
        val = item.get(key)
        if val is None:
            continue
        dt = dates.parse_date(str(val))
        if dt:
            return dt.strftime("%Y-%m-%d")
    return None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_items(raw_items: List[Dict[str, Any]], core_topic: str) -> List[Dict[str, Any]]:
    """Parse raw Threads post objects (web shape) into normalized dicts."""
    items = []
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        post_id = str(raw.get("pk") or raw.get("id") or raw.get("code") or f"TH{i + 1}")
        caption = raw.get("caption")
        if isinstance(caption, dict):
            text = caption.get("text") or ""
        else:
            text = caption or raw.get("text") or ""
        text = str(text)

        user = raw.get("user") or {}
        if isinstance(user, dict):
            handle = str(user.get("username") or "")
            display_name = str(user.get("full_name") or handle)
        else:
            handle = display_name = str(user)

        info = raw.get("text_post_app_info") or {}
        if not isinstance(info, dict):
            info = {}
        likes = _count(raw.get("like_count"))
        replies = _count(info.get("direct_reply_count"))
        reposts = _count(info.get("repost_count"))
        quotes = _count(info.get("quote_count"))

        code = str(raw.get("code") or "")
        url = str(raw.get("permalink") or raw.get("canonical_url") or "")
        if not url.startswith(("https://www.threads.com/", "https://www.threads.net/")):
            url = ""
        if not url and code and handle:
            url = f"https://www.threads.com/@{handle}/post/{code}"
        elif not url and code:
            url = f"https://www.threads.com/post/{code}"

        rank_score = max(0.3, 1.0 - (i * 0.02))
        engagement_boost = min(0.2, math.log1p(likes + reposts) / 40)
        text_relevance = _compute_relevance(core_topic, text)
        relevance = min(1.0, text_relevance * 0.5 + rank_score * 0.3 + engagement_boost + 0.1)

        why = f"Threads: @{handle}: {text[:60]}" if text else f"Threads: {handle}"
        if raw.get("_trend"):
            why = f"Threads trending ({raw['_trend']}): @{handle}: {text[:60]}"
        items.append({
            "id": post_id,
            "handle": handle,
            "display_name": display_name,
            "text": text,
            "url": url,
            "date": _parse_date(raw),
            "engagement": {
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "quotes": quotes,
            },
            "relevance": round(relevance, 2),
            "why_relevant": why,
        })
    return items


def _run_helper(cmd: List[str], cfg: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Run the browser helper; return its JSON or {"error": ...}."""
    helper_budget = float(cfg["timeout"])
    deadline = config.get("_research_deadline")
    # The helper's own clock starts only after `uv run` and Playwright are up
    # (a cold uv cache takes a while), so it also gets an absolute wall-clock
    # end: it stops searching and prints what it has before the research
    # deadline kills the process group.
    stop_epoch = time.time() + helper_budget + _LAUNCH_GRACE
    if deadline is not None:
        left = deadline - time.monotonic()
        helper_budget = min(helper_budget, max(5.0, left - 5))
        stop_epoch = min(stop_epoch, time.time() + left - 5)
    full_cmd = list(cmd) + [
        "--modes", cfg["modes"],
        "--timeout", f"{helper_budget:.0f}",
        "--stop-epoch", f"{stop_epoch:.1f}",
        "--scrolls", str(cfg["scrolls"]),
        "--",
    ] + list(cfg["queries"])
    try:
        result = subproc.run_with_timeout(
            full_cmd,
            timeout=helper_budget + _LAUNCH_GRACE,
            env=_child_env(),
            deadline_monotonic=deadline,
            capture_limit_bytes=_CAPTURE_LIMIT_BYTES,
        )
    except subproc.SubprocTimeout as exc:
        return {"error": f"browser timed out: {exc}"}
    except OSError as exc:
        return {"error": f"browser could not start: {type(exc).__name__}: {exc}"}
    out = (result.stdout or "").strip()
    if result.returncode != 0 or not out:
        tail = (result.stderr or "").strip().splitlines()[-1:] or [f"exit {result.returncode}"]
        return {"error": f"browser helper failed: {tail[0][:300]}"}
    try:
        # Only "\n" ends the JSON line; splitlines() would also break on
        # U+2028 and friends if a post carried them unescaped.
        data = json.loads(out.rsplit("\n", 1)[-1])
    except ValueError:
        return {"error": "browser helper returned non-JSON output"}
    return data if isinstance(data, dict) else {"error": "browser helper returned non-object JSON"}


def search_threads(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search Threads: keyless browser, plus the official API when configured.

    Returns:
        Dict with 'items' list, optional 'error', and 'trend' when the topic
        matches a Threads trending topic (THREADS_APP_TOKEN).
    """
    config = config or {}
    cmd = browser_command(config)
    use_api = threads_api.user_token_present(config)
    if cmd is None and not use_api:
        return {"items": [], "error": f"Threads unavailable: {describe_backend(config)}"}

    cfg = dict(DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"]))
    core_topic = _extract_core_subject(topic)
    if not core_topic:
        return {"items": [], "error": "empty Threads query"}
    cfg["queries"] = _query_ladder(core_topic)
    deadline = config.get("_research_deadline")

    errors: List[str] = []
    api_posts: List[Dict[str, Any]] = []
    trend = None
    use_trends = threads_api.app_token_present(config)

    def api_lane() -> Dict[str, Any]:
        # Bounded on its own: never longer than the API lane budget, never
        # past the research deadline.
        lane_deadline = time.monotonic() + _API_BUDGET
        if deadline is not None:
            lane_deadline = min(lane_deadline, deadline)
        out: Dict[str, Any] = {}
        if use_api:
            out["search"] = threads_api.keyword_search(
                core_topic, from_date, to_date, cfg["results"], config, lane_deadline)
        if use_trends:
            out["trending"] = threads_api.matching_trend(core_topic, config, lane_deadline)
        return out

    browser_posts: List[Dict[str, Any]] = []
    # The API lane runs beside the browser instead of delaying it.
    with ThreadPoolExecutor(max_workers=1) as pool:
        api_future = pool.submit(api_lane) if (use_api or use_trends) else None
        if cmd is not None:
            _log(f"Browser search for '{core_topic}' (depth={depth}, modes={cfg['modes']})")
            data = _run_helper(cmd, cfg, config)
            posts = data.get("posts") or []
            browser_posts = posts if isinstance(posts, list) else []
            if data.get("error") and not browser_posts:
                _log(f"Browser error: {data['error']}")
                errors.append(str(data["error"]))
        api_out = api_future.result() if api_future is not None else {}

    api = api_out.get("search")
    if api:
        api_posts.extend(api.get("posts") or [])
        if api.get("error"):
            _log(f"API error: {api['error']}")
            errors.append(f"API: {api['error']}")
    trending = api_out.get("trending")
    if trending:
        trend = trending.get("trend")
        api_posts.extend(trending.get("posts") or [])
        if trending.get("error"):
            _log(f"Trending topics error: {trending['error']}")

    raw_items = _merge_posts(browser_posts, api_posts)
    if not raw_items:
        out: Dict[str, Any] = {"items": []}
        if errors:
            out["error"] = "; ".join(errors)
        else:
            _log("No Threads posts found")
        if trend:
            out["trend"] = trend
        return out

    items = _parse_items(raw_items, core_topic)

    in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
    out_of_range = len(items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} posts outside date range")
    else:
        _log(f"No posts within date range, keeping all {len(items)}")

    # API posts carry no counts, so relevance breaks the tie among them.
    items.sort(key=lambda x: (x["engagement"]["likes"], x["relevance"]), reverse=True)
    items = items[:cfg["results"]]
    _log(f"Found {len(items)} Threads posts")
    out = {"items": items}
    if trend:
        out["trend"] = trend
    return out


def _post_key(raw: Dict[str, Any]) -> str:
    return str(raw.get("code") or raw.get("pk") or raw.get("id") or "")


def _merge_posts(browser_posts: List[Any], api_posts: List[Any]) -> List[Dict[str, Any]]:
    """Browser posts first (they carry engagement), then API-only posts."""
    merged: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for raw in list(browser_posts) + list(api_posts):
        if not isinstance(raw, dict):
            continue
        key = _post_key(raw)
        if key and key in seen:
            # The browser copy wins, but keeps the API copy's trend label.
            if raw.get("_trend"):
                seen[key].setdefault("_trend", raw["_trend"])
            continue
        if key:
            seen[key] = raw
        merged.append(raw)
    return merged


def parse_threads_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Threads search response to normalized format."""
    return response.get("items", [])
