#!/usr/bin/env python3
"""Live canary for the keyless Reddit floor and the Arctic Shift hydrator.

Dev and CI tool, not part of the skill runtime. The scheduled workflow
``.github/workflows/reddit-canary.yml`` runs it daily; it never gates a pull
request. It runs with no logged-in session, no ScrapeCreators key and no
config file, so it measures exactly the path every free user gets.

Each run:

1. Asks every keyless web rung (Brave, DuckDuckGo, Startpage) the floor's own
   month-filtered ``site:reddit.com <topic>`` query for each of
   :data:`TOPICS`, one rung at a time (no fall-through), and parses the page
   with the engine's own parser.
2. Hydrates each topic's discovered threads through the engine's hydration
   chain (Arctic Shift, no session, no ScrapeCreators).
3. Looks up :data:`KNOWN_THREAD_IDS` on Arctic Shift ``/posts/ids``, so the
   hydrator is checked even on a day every web rung is challenged.

Every check is classified as one of:

- ``ok``: it returned parseable results.
- ``challenged``: a 202 or 429 answer, an archive "slow down" (422), or a
  known challenge/captcha page. GitHub runners are datacenter IPs and get
  challenged more often than users do.
- ``parser-broke``: a 200 answer the engine's parser finds no results in,
  and that is not the rung's own "no results" page.
- ``down``: no answer, a 403, a 5xx, another error status, or a page the
  parser read that holds no Reddit thread (the rung serves the floor nothing).

The run fails (exit 1) when any check is ``parser-broke``, Arctic hydration is
``down``, every discovery rung check is ``down``, or every rung was kept from
serving (challenged, or challenged and down) two days running: a streak of
runs with no rung ``ok``, each within :data:`CONSECUTIVE_WINDOW_HOURS` of the
last, that began at least :data:`STREAK_FAIL_HOURS` ago. The streak start is
read from and written to ``--state-file``; the workflow carries that file
between runs through the Actions cache.

Run locally (direct CLI; there is no slash-command form for this dev tool)::

    python3 skills/last30days/scripts/reddit_canary.py --state-file tmp/reddit-canary/state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import env, http, reddit_arctic, reddit_discover, reddit_health, reddit_hydrate, web_search_keyless  # noqa: E402
from lib.reddit_refs import ARCTIC, ARCTIC_PENDING, ThreadRef, canonical_thread  # noqa: E402

# The canary measures the free floor only: no config file (so no saved key,
# Reddit consent or browser setting), no Keychain key, no session, no SC key.
CLEAN_ENV = {
    "LAST30DAYS_CONFIG_DIR": "",
    "LAST30DAYS_SKIP_KEYCHAIN": "1",
    "REDDIT_BROWSER_CONSENT": "false",
    "FROM_BROWSER": "off",
}
DROPPED_ENV = ("SCRAPECREATORS_API_KEY", env.REDDIT_SESSION_COOKIE_VAR)


def clean_environment() -> None:
    """Point this process at the free floor only (see :data:`CLEAN_ENV`)."""
    os.environ.update(CLEAN_ENV)
    for key in DROPPED_ENV:
        os.environ.pop(key, None)
    # env resolved its config dir at import; no config file from here on, so
    # the path-health cache is not read or written either.
    env.CONFIG_DIR = None
    env.CONFIG_FILE = None

TOPICS = ("Claude Code", "Steam Deck")
# Settled threads the archive has held for a long time. A lookup that finds
# none of them means the archive or its answer shape changed.
KNOWN_THREAD_IDS = (reddit_health.PROBE_THREAD_ID, "1j1y38t", "1j1w6ar", "1j1oqnu")
RUNGS = web_search_keyless.RUNGS
RESULTS_PER_PAGE = reddit_discover.RESULTS_PER_QUERY
FRESHNESS = "month"
# A run more than this long after the previous one starts a fresh streak.
CONSECUTIVE_WINDOW_HOURS = 48
# A no-rung-served streak this old spans two daily runs: the canary fails.
# Same-day reruns (workflow_dispatch) never reach it.
STREAK_FAIL_HOURS = 20
REQUEST_TIMEOUT = 20
PAUSE_SECONDS = 2.0

OK = "ok"
CHALLENGED = "challenged"
PARSER_BROKE = "parser-broke"
DOWN = "down"

# Generic captcha wording a results page has only when it is a challenge. The
# engine's per-rung challenge markers are checked first.
_GENERIC_CHALLENGE_MARKERS = ("captcha",)
# A rung's own "nothing found" page: parsed fine, just no results. DuckDuckGo
# serves it for many month-filtered site: queries.
_NO_RESULTS_MARKERS = {"ddg": ("no-results__message",)}

Fetch = Callable[[str, str], tuple[Optional[int], str]]
HydrateFn = Callable[..., Any]


@dataclass
class Check:
    name: str
    kind: str  # "rung", "hydrate" or "arctic"
    state: str
    status: Optional[int] = None
    results: int = 0
    note: str = ""
    refs: list[ThreadRef] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("refs", None)
        return out


def http_fetch(url: str, accept: str = "text/html") -> tuple[Optional[int], str]:
    """One GET with the engine's browser headers: ``(status, body)``.

    ``status`` is None when no HTTP answer came back. Never raises.
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": http.BROWSER_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception:
        return None, ""


def _is_challenge(rung: str, text: str) -> bool:
    if web_search_keyless.is_challenge_page(rung, text):
        return True
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _GENERIC_CHALLENGE_MARKERS)


def _recognized_empty(rung: str, text: str) -> bool:
    return any(marker in (text or "") for marker in _NO_RESULTS_MARKERS.get(rung, ()))


def classify_page(
    rung: str, status: Optional[int], text: str, threads: int, parsed: int = 0,
) -> str:
    """The canary state of one results page (see the module docstring).

    ``threads`` is the Reddit threads found on the page, ``parsed`` every
    result the parser read. A page the parser read (other results, or the
    engine's own "no results" page) but with no Reddit thread is ``down``
    for the floor, not a parser break.
    """
    if status is None:
        return DOWN
    if 200 <= status < 300 and threads > 0:
        return OK
    if status in (202, 429):
        return CHALLENGED
    if 200 <= status < 300 and _is_challenge(rung, text):
        return CHALLENGED
    if status == 200 and (parsed > 0 or _recognized_empty(rung, text)):
        return DOWN
    if status == 200:
        return PARSER_BROKE
    return DOWN


def check_rung(rung: str, topic: str, fetch: Fetch) -> Check:
    query = reddit_discover._queries(topic, None)[0]
    status, text = fetch(web_search_keyless.rung_url(rung, query, FRESHNESS), "text/html")
    items: list[dict] = []
    if status is not None and 200 <= status < 300:
        try:
            items = web_search_keyless.parse_rung(rung, text, RESULTS_PER_PAGE)
        except Exception as exc:  # a parser crash is a parser break
            return Check(f"{rung}: {topic}", "rung", PARSER_BROKE, status, 0, f"parser raised {type(exc).__name__}")
    refs: dict[str, ThreadRef] = {}
    for item in items:
        ref = canonical_thread(str(item.get("url") or ""))
        if ref is None or ref.post_id in refs:
            continue
        ref.provenance.add("web")
        ref.title = reddit_discover._clean_title(str(item.get("title") or ""))
        ref.date = item.get("date") or None
        refs[ref.post_id] = ref
    state = classify_page(rung, status, text, len(refs), len(items))
    note = f"{len(items)} results, {len(refs)} reddit threads"
    if status is None:
        note = "no answer"
    elif state == DOWN and status == 200:
        note += " (results page parsed)"
    return Check(f"{rung}: {topic}", "rung", state, status, len(refs), note, list(refs.values()))


def check_hydration(topic: str, refs: list[ThreadRef], hydrate: HydrateFn) -> Optional[Check]:
    """The engine's hydration chain on the topic's discovered threads, or None
    when discovery found none to hydrate."""
    if not refs:
        return None
    result = hydrate(refs, topic=topic, depth="quick", comments=True)
    filled = sum(
        1 for item in result.items if item.get("engagement_source") in (ARCTIC, ARCTIC_PENDING)
    )
    comments = sum(len(item.get("top_comments") or []) for item in result.items)
    state = {"ok": OK, "unused": OK, "throttled": CHALLENGED}.get(result.arctic_state, DOWN)
    failures = getattr(result, "comment_failures", 0)
    throttles = getattr(result, "comment_throttles", 0)
    if state == OK and failures and not getattr(result, "comment_fills", 0):
        # Post lookups worked but every comment-tree request failed. All of
        # them slow-downs is a challenge; any other failure means the comments
        # endpoint is out. An empty tree is not a failure.
        state = CHALLENGED if throttles >= failures else DOWN
    note = f"{filled} of {len(refs)} threads from arctic, {comments} comments"
    if failures:
        note += f", {failures} comment requests failed ({throttles} throttled)"
    note += f"; {result.detail}"
    return Check(f"hydrate: {topic}", "hydrate", state, None, filled, note)


def check_known_ids(fetch: Fetch, ids: Iterable[str] = KNOWN_THREAD_IDS) -> Check:
    ids = list(ids)
    status, text = fetch(f"{reddit_arctic.API}?ids={','.join(ids)}", "application/json")
    name = "arctic: known ids"
    if status is None:
        return Check(name, "arctic", DOWN, None, 0, "no answer")
    if status in (422, 429):
        return Check(name, "arctic", CHALLENGED, status, 0, "archive asked to slow down")
    if status != 200:
        return Check(name, "arctic", DOWN, status, 0, f"HTTP {status}")
    try:
        data = json.loads(text or "")
    except ValueError:
        return Check(name, "arctic", PARSER_BROKE, status, 0, "not JSON")
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        if isinstance(data, dict) and "slow down" in str(data.get("error") or "").lower():
            return Check(name, "arctic", CHALLENGED, status, 0, "archive asked to slow down")
        return Check(name, "arctic", PARSER_BROKE, status, 0, "no data list in the answer")
    found = {
        str(row.get("id")) for row in rows
        if isinstance(row, dict) and row.get("id") in ids and row.get("title")
    }
    if not found:
        return Check(name, "arctic", PARSER_BROKE, status, 0, f"0 of {len(ids)} known threads in the answer")
    return Check(name, "arctic", OK, status, len(found), f"{len(found)} of {len(ids)} known threads")


def read_state(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(path: Optional[Path], state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _parse_at(value: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _streak_start(previous: Optional[dict[str, Any]], now: datetime) -> Optional[datetime]:
    """When the no-rung-served streak the previous run was in began, or None
    when the previous run served (or is missing, unreadable or too old)."""
    if not previous or not previous.get("no_rung_served"):
        return None
    at = _parse_at(previous.get("at"))
    if at is None or not timedelta(0) <= now - at <= timedelta(hours=CONSECUTIVE_WINDOW_HOURS):
        return None
    return _parse_at(previous.get("no_serve_since")) or at


def evaluate(
    checks: list[Check], previous: Optional[dict[str, Any]], now: datetime,
) -> tuple[list[str], Optional[datetime]]:
    """``(failure reasons, start of the no-rung-served streak or None)``."""
    failures: list[str] = []
    for check in checks:
        if check.state == PARSER_BROKE:
            failures.append(f"{check.name} is parser-broke ({check.note})")
        elif check.kind in ("arctic", "hydrate") and check.state == DOWN:
            failures.append(f"{check.name}: Arctic hydration is down ({check.note})")
    rungs = [check for check in checks if check.kind == "rung"]
    no_rung_served = bool(rungs) and not any(check.state == OK for check in rungs)
    since = (_streak_start(previous, now) or now) if no_rung_served else None
    if rungs and all(check.state == DOWN for check in rungs):
        failures.append("every discovery rung is down")
    elif (
        since is not None
        and any(check.state == CHALLENGED for check in rungs)
        and now - since >= timedelta(hours=STREAK_FAIL_HOURS)
    ):
        failures.append(
            f"no discovery rung served since {since.isoformat(timespec='minutes')} "
            "(challenged two days running)"
        )
    return failures, since


def run(
    *,
    fetch: Fetch = http_fetch,
    hydrate: HydrateFn = reddit_hydrate.hydrate,
    topics: Iterable[str] = TOPICS,
    state_file: Optional[Path] = None,
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = time.sleep,
    pause: float = PAUSE_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """Run every check; returns ``(exit code, report)``."""
    now = now or datetime.now(timezone.utc)
    previous = read_state(state_file)
    reddit_health.reset_run_state()
    web_search_keyless.reset_rung_health()
    checks: list[Check] = []
    for topic in topics:
        refs: dict[str, ThreadRef] = {}
        for rung in RUNGS:
            check = check_rung(rung, topic, fetch)
            checks.append(check)
            for ref in check.refs:
                refs.setdefault(ref.post_id, ref)
            sleep(pause)
        hydration = check_hydration(topic, list(refs.values()), hydrate)
        if hydration is not None:
            checks.append(hydration)
    checks.append(check_known_ids(fetch))
    failures, since = evaluate(checks, previous, now)
    report = {
        "at": now.isoformat(timespec="seconds"),
        "no_rung_served": since is not None,
        "no_serve_since": since.isoformat(timespec="seconds") if since else None,
        "failures": failures,
        "checks": [check.to_dict() for check in checks],
    }
    write_state(state_file, report)
    return (1 if failures else 0), report


def render(report: dict[str, Any]) -> str:
    lines = [
        f"Reddit canary {report['at']}",
        "",
        "| Check | State | HTTP | Results | Note |",
        "|---|---|---:|---:|---|",
    ]
    for check in report["checks"]:
        status = check["status"] if check["status"] is not None else "-"
        lines.append(f"| {check['name']} | {check['state']} | {status} | {check['results']} | {check['note']} |")
    lines.append("")
    if report["failures"]:
        lines.append("FAIL")
        lines.extend(f"- {reason}" for reason in report["failures"])
    else:
        lines.append("PASS")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state-file", help="previous-run state, read then rewritten (JSON)")
    parser.add_argument("--report", help="also write the full report as JSON here")
    args = parser.parse_args(argv)
    clean_environment()
    state_file = Path(args.state_file) if args.state_file else None
    code, report = run(state_file=state_file)
    text = render(report)
    sys.stdout.write(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
