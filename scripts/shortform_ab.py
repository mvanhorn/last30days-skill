#!/usr/bin/env python3
"""A/B parity harness: ScrapeCreators vs keyless TikTok and Instagram.

Runs the research engine once per topic per arm and compares what each arm
returns for TikTok and Instagram. Arms of the same topic run concurrently so
they hit the same moment on the platforms.

Arms:
  A          ScrapeCreators (the user's normal config).
  B          Keyless, engine discovery only, creator expansion off, freshness on.
  B-nofresh  B with the web-backend freshness parameters off.
  C          Keyless, creator expansion on.
  D          C plus a host-discovered URL envelope (--shortform-urls). Runs only
             when <fixtures-dir>/<topic-slug>.json exists; skipped otherwise.

A host run also passes the TikTok hashtags and creators it infers (runbook
step 3). When <fixtures-dir>/<topic-slug>.host.json exists ({"tiktok_creators":
[...], "tiktok_hashtags": [...]}), arms A, C and D get those flags, so the paid
and keyless lanes start from the same host input.

Keyless arms read a private copy of the user's .env with the ScrapeCreators
key lines removed (LAST30DAYS_CONFIG_DIR points at it). The copy is deleted
on exit. Config values are never printed.

Dev tool, run from the repo root with Python 3.12+:
  uv run python scripts/shortform_ab.py --topics "Labubu,Claude Code" --arms A,B
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"

PLATFORMS = ("tiktok", "instagram")
ALL_ARMS = ("A", "B", "B-nofresh", "C", "D")
RUN_TIMEOUT_SECONDS = 180
HEAD_SIZE = 10
SC_KEY_NAMES = ("SCRAPECREATORS_API_KEY", "SCRAPE_CREATORS_API_KEY")

# Parity bar.
MIN_IN_WINDOW_RATIO = 0.5
MIN_HEAD_RECALL = 0.4
WALL_SLACK_SECONDS = 30.0
WALL_CEILING_SECONDS = 180.0

# 24 fixed topics, 4 per category, chosen to stay busy on TikTok year-round.
TOPICS: dict[str, list[str]] = {
    "product/tech": ["Claude Code", "iPhone 17", "Nintendo Switch 2", "Meta Ray-Ban glasses"],
    "celebrity/music": ["Sabrina Carpenter", "Taylor Swift", "Bad Bunny", "Charli XCX"],
    "consumer trend": ["Labubu", "Stanley cup", "matcha", "Dubai chocolate"],
    "food/how-to": ["air fryer recipes", "sourdough", "cottage cheese recipes", "meal prep"],
    "news event": ["NFL", "Formula 1", "stock market", "Premier League"],
    "niche hobby": ["crochet", "mechanical keyboards", "pickleball", "aquascaping"],
}

FIELDS = ("date", "creator", "views", "likes", "comments", "caption")

_TIKTOK_ID = re.compile(r"/(?:video|photo)/(\d+)")
_IG_CODE = re.compile(r"/(?:reels?|p|tv)/([A-Za-z0-9_-]+)")
_PLATFORM_ERROR = {
    p: re.compile(rf"(?i)\b{p}\b[^\n]*\b(error|failed|exception|429|timed out)\b") for p in PLATFORMS
}


# ---------------------------------------------------------------------------
# Pure metric functions (unit-tested in tests/test_shortform_ab.py)
# ---------------------------------------------------------------------------


def video_id(platform: str, url: str) -> str | None:
    """Return the TikTok numeric video id or the Instagram shortcode in ``url``."""
    if not url:
        return None
    pattern = _TIKTOK_ID if platform == "tiktok" else _IG_CODE
    match = pattern.search(url)
    return match.group(1) if match else None


def item_ids(platform: str, items: list[dict[str, Any]]) -> set[str]:
    return {vid for vid in (video_id(platform, str(i.get("url") or "")) for i in items) if vid}


def parse_day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def window_counts(items: list[dict[str, Any]], range_from: str, range_to: str) -> dict[str, int]:
    """Count items inside the window, outside it (stale leaks), and undated."""
    start, end = parse_day(range_from), parse_day(range_to)
    counts = {"in_window": 0, "stale": 0, "undated": 0}
    for item in items:
        day = parse_day(item.get("published_at"))
        if day is None or start is None or end is None:
            counts["undated"] += 1
        elif start <= day <= end:
            counts["in_window"] += 1
        else:
            counts["stale"] += 1
    return counts


def recall(baseline_ids: set[str], arm_ids: set[str]) -> float | None:
    """Share of the baseline's ids the arm also returned; None when the baseline is empty."""
    if not baseline_ids:
        return None
    return len(baseline_ids & arm_ids) / len(baseline_ids)


def _metric(item: dict[str, Any], key: str) -> float:
    value = (item.get("engagement") or {}).get(key)
    try:
        return float(value) if value is not None else -1.0
    except (TypeError, ValueError):
        return -1.0


def head_ids(platform: str, items: list[dict[str, Any]], size: int = HEAD_SIZE) -> list[str]:
    """Ids of the top ``size`` items by views (TikTok) or likes (Instagram)."""
    key = "views" if platform == "tiktok" else "likes"
    ranked = sorted(items, key=lambda i: _metric(i, key), reverse=True)
    out: list[str] = []
    for item in ranked:
        vid = video_id(platform, str(item.get("url") or ""))
        if vid and vid not in out:
            out.append(vid)
        if len(out) == size:
            break
    return out


def head_recall(platform: str, baseline_items: list[dict[str, Any]], arm_ids: set[str]) -> float | None:
    head = head_ids(platform, baseline_items)
    if not head:
        return None
    return sum(1 for vid in head if vid in arm_ids) / len(head)


def completeness(items: list[dict[str, Any]]) -> dict[str, float | None]:
    """Per-field share of items carrying date, creator, views, likes, comments, caption."""
    if not items:
        return {field: None for field in FIELDS}
    present = {field: 0 for field in FIELDS}
    for item in items:
        eng = item.get("engagement") or {}
        present["date"] += bool(item.get("published_at"))
        present["creator"] += bool(item.get("author"))
        for key in ("views", "likes", "comments"):
            present[key] += eng.get(key) is not None
        present["caption"] += bool(str(item.get("body") or item.get("snippet") or "").strip())
    return {field: present[field] / len(items) for field in FIELDS}


def median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def p90(values: list[float | None]) -> float | None:
    """Nearest-rank 90th percentile."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    rank = max(1, math.ceil(0.9 * len(clean)))
    return clean[rank - 1]


def parity_verdict(
    *,
    arm_in_window: list[float],
    a_in_window: list[float],
    arm_head_recall: list[float | None],
    arm_stale: list[int],
    arm_wall: list[float],
    a_wall: list[float],
) -> dict[str, Any]:
    """Check one keyless arm on one platform against the parity bar."""
    checks: dict[str, bool] = {}
    a_med = median(a_in_window)
    arm_med = median(arm_in_window)
    checks["in_window"] = (
        arm_med is not None and a_med is not None and arm_med >= MIN_IN_WINDOW_RATIO * a_med
    )
    hr = median(arm_head_recall)
    checks["head_recall"] = hr is not None and hr >= MIN_HEAD_RECALL
    checks["stale"] = sum(arm_stale) == 0
    arm_p90, a_p90 = p90(arm_wall), p90(a_wall)
    checks["wall"] = (
        arm_p90 is not None
        and a_p90 is not None
        and arm_p90 <= a_p90 + WALL_SLACK_SECONDS
        and arm_p90 < WALL_CEILING_SECONDS
    )
    return {"pass": all(checks.values()), "checks": checks}


def slugify(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")


def strip_sc_keys(env_text: str) -> str:
    """Drop ScrapeCreators key lines (plain or ``export``-prefixed) from .env text."""
    kept = []
    for line in env_text.splitlines():
        key = line.strip()
        if key.startswith("export "):
            key = key[len("export "):].lstrip()
        key = key.split("=", 1)[0].strip()
        if key in SC_KEY_NAMES:
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")


# ---------------------------------------------------------------------------
# Running arms
# ---------------------------------------------------------------------------

_PRIVATE_DIRS: list[str] = []


def _cleanup_private_dirs() -> None:
    while _PRIVATE_DIRS:
        shutil.rmtree(_PRIVATE_DIRS.pop(), ignore_errors=True)


def _source_config_file() -> Path:
    override = os.environ.get("LAST30DAYS_CONFIG_DIR")
    if override:
        return Path(override) / ".env"
    return Path.home() / ".config" / "last30days" / ".env"


def make_keyless_config_dir() -> str:
    """Private temp config dir holding the user's .env minus ScrapeCreators keys."""
    path = tempfile.mkdtemp(prefix="l30d-ab-")
    _PRIVATE_DIRS.append(path)
    os.chmod(path, 0o700)
    source = _source_config_file()
    text = source.read_text(encoding="utf-8-sig") if source.exists() else ""
    target = Path(path) / ".env"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(strip_sc_keys(text))
    return path


def keychain_has_sc_key() -> bool:
    """True when the macOS Keychain holds the ScrapeCreators key (value never read)."""
    if sys.platform != "darwin" or not shutil.which("security"):
        return False
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "last30days-SCRAPECREATORS_API_KEY"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def arm_env(arm: str, keyless_dir: str | None, skip_keychain: bool) -> dict[str, str]:
    env = dict(os.environ)
    if arm == "A":
        return env
    for key in SC_KEY_NAMES:
        env.pop(key, None)
    env["LAST30DAYS_CONFIG_DIR"] = keyless_dir or ""
    if skip_keychain:
        env["LAST30DAYS_SKIP_KEYCHAIN"] = "1"
    env["LAST30DAYS_SHORTFORM_FRESHNESS"] = "off" if arm == "B-nofresh" else "on"
    env["LAST30DAYS_SHORTFORM_CREATOR_EXPANSION"] = "off" if arm in ("B", "B-nofresh") else "on"
    return env


def host_flags(fixtures_dir: Path | None, topic: str) -> list[str]:
    """--tiktok-creators/--tiktok-hashtags from the topic's .host.json sidecar."""
    if fixtures_dir is None:
        return []
    path = fixtures_dir / f"{slugify(topic)}.host.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    flags: list[str] = []
    for key, flag in (("tiktok_creators", "--tiktok-creators"), ("tiktok_hashtags", "--tiktok-hashtags")):
        values = [str(v).strip().lstrip("@#") for v in data.get(key) or [] if str(v).strip()]
        if values:
            flags.append(f"{flag}={','.join(values)}")
    return flags


def arm_command(topic: str, arm: str, fixture: Path | None,
                extra: list[str] | None = None) -> list[str]:
    cmd = [
        sys.executable,
        str(ENGINE),
        topic,
        "--emit=json",
        "--json-profile=raw",
        "--auto-resolve",
        "--save-dir=",
        "--search=tiktok,instagram",
    ]
    if arm == "D" and fixture is not None:
        cmd += ["--shortform-urls", str(fixture)]
    if arm in ("A", "C", "D"):
        cmd += list(extra or [])
    return cmd


def _platform_items(payload: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    items = (payload.get("items_by_source") or {}).get(platform) or []
    return [i for i in items if isinstance(i, dict)]


def run_arm(topic: str, arm: str, *, keyless_dir: str | None, skip_keychain: bool,
            fixtures_dir: Path | None, workdir: str) -> dict[str, Any]:
    fixture = None
    if arm == "D":
        candidate = fixtures_dir / f"{slugify(topic)}.json" if fixtures_dir else None
        if candidate is None or not candidate.exists():
            return {"arm": arm, "skipped": "no shortform-urls fixture for this topic"}
        fixture = candidate
    cmd = arm_command(topic, arm, fixture, host_flags(fixtures_dir, topic))
    started = time.monotonic()
    result: dict[str, Any] = {"arm": arm, "skipped": None, "timed_out": False, "exit_code": None}
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, env=arm_env(arm, keyless_dir, skip_keychain),
            capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, check=False,
        )
        stdout, stderr, result["exit_code"] = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result["timed_out"] = True
    result["wall"] = round(time.monotonic() - started, 2)
    payload: dict[str, Any] = {}
    try:
        start = stdout.find("{")
        if start >= 0:
            payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        payload = {}
    result["parse_ok"] = bool(payload)
    result["range_from"] = payload.get("range_from")
    result["range_to"] = payload.get("range_to")
    run_failed = result["timed_out"] or result["exit_code"] != 0 or not payload
    errors = payload.get("errors_by_source") or {}
    result["platforms"] = {}
    for platform in PLATFORMS:
        items = _platform_items(payload, platform)
        stderr_match = _PLATFORM_ERROR[platform].search(stderr or "")
        platform_error = errors.get(platform) or (stderr_match.group(0) if stderr_match else None)
        result["platforms"][platform] = {
            "items": items,
            "failed": bool(run_failed or (not items and platform_error)),
            "error": (str(platform_error)[:200] if platform_error else None),
        }
    if run_failed:
        result["stderr_tail"] = (stderr or "")[-400:]
    return result


def score_topic(topic: str, runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn raw arm runs for one topic into per-arm, per-platform metrics."""
    baseline = runs.get("A")
    scored: dict[str, Any] = {}
    for arm, run in runs.items():
        if run.get("skipped"):
            scored[arm] = {"skipped": run["skipped"]}
            continue
        entry: dict[str, Any] = {"wall": run["wall"], "exit_code": run["exit_code"],
                                 "timed_out": run["timed_out"], "platforms": {}}
        if run.get("stderr_tail"):
            entry["stderr_tail"] = run["stderr_tail"]
        for platform in PLATFORMS:
            pdata = run["platforms"][platform]
            items = pdata["items"]
            ids = item_ids(platform, items)
            counts = window_counts(items, run.get("range_from") or "", run.get("range_to") or "")
            metrics: dict[str, Any] = {
                "items": len(items),
                **counts,
                "failed": pdata["failed"],
                "error": pdata["error"],
                "completeness": completeness(items),
                "ids": sorted(ids),
            }
            if baseline and not baseline.get("skipped") and arm != "A":
                base_items = baseline["platforms"][platform]["items"]
                metrics["recall"] = recall(item_ids(platform, base_items), ids)
                metrics["head_recall"] = head_recall(platform, base_items, ids)
            entry["platforms"][platform] = metrics
        scored[arm] = entry
    return {"topic": topic, "arms": scored}


def summarize(results: list[dict[str, Any]], arms: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    def series(arm: str, platform: str, key: str) -> list[Any]:
        out = []
        for res in results:
            entry = res["arms"].get(arm) or {}
            if entry.get("skipped") or not entry:
                continue
            out.append(entry["platforms"][platform].get(key))
        return out

    def walls(arm: str) -> list[float]:
        return [r["arms"][arm]["wall"] for r in results
                if arm in r["arms"] and not r["arms"][arm].get("skipped")]

    for arm in arms:
        runs = walls(arm)
        arm_sum: dict[str, Any] = {"runs": len(runs), "p90_wall": p90(runs),
                                   "median_wall": median(runs), "platforms": {}}
        for platform in PLATFORMS:
            failed = series(arm, platform, "failed")
            comp = [c for c in series(arm, platform, "completeness") if c]
            comp_mean = {
                f: median([c[f] for c in comp if c[f] is not None]) for f in FIELDS
            } if comp else {f: None for f in FIELDS}
            plat: dict[str, Any] = {
                "median_in_window": median(series(arm, platform, "in_window")),
                "stale_total": sum(series(arm, platform, "stale")),
                "undated_total": sum(series(arm, platform, "undated")),
                "median_recall": median(series(arm, platform, "recall")),
                "median_head_recall": median(series(arm, platform, "head_recall")),
                "failure_rate": (sum(bool(f) for f in failed) / len(failed)) if failed else None,
                "median_completeness": comp_mean,
            }
            if arm != "A" and runs and "A" in arms:
                # Compare on the topics where both arms ran.
                paired = [r for r in results if arm in r["arms"] and not r["arms"][arm].get("skipped")
                          and "A" in r["arms"] and not r["arms"]["A"].get("skipped")]
                plat["parity"] = parity_verdict(
                    arm_in_window=[r["arms"][arm]["platforms"][platform]["in_window"] for r in paired],
                    a_in_window=[r["arms"]["A"]["platforms"][platform]["in_window"] for r in paired],
                    arm_head_recall=[r["arms"][arm]["platforms"][platform].get("head_recall") for r in paired],
                    arm_stale=[r["arms"][arm]["platforms"][platform]["stale"] for r in paired],
                    arm_wall=[r["arms"][arm]["wall"] for r in paired],
                    a_wall=[r["arms"]["A"]["wall"] for r in paired],
                )
            arm_sum["platforms"][platform] = plat
        summary[arm] = arm_sum
    return summary


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: Any, pct: bool = False) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value * 100:.0f}%"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _comp_avg(comp: dict[str, float | None]) -> float | None:
    vals = [v for v in comp.values() if v is not None]
    return sum(vals) / len(vals) if vals else None


def render_markdown(meta: dict[str, Any], results: list[dict[str, Any]], summary: dict[str, Any],
                    arms: list[str]) -> str:
    lines = [
        "# Short-form A/B parity run",
        "",
        f"- Started: {meta['started']}",
        f"- Engine commit: {meta['commit']}",
        f"- Arms: {', '.join(arms)}",
        f"- Topics: {len(results)}",
        f"- Keyless config: ScrapeCreators key removed"
        + ("; Keychain skipped for keyless arms" if meta.get("skip_keychain") else ""),
        "",
        "Parity bar (per keyless arm, per platform): median in-window >= 50% of A, "
        "median head recall >= 40%, stale leaks 0, p90 wall <= A p90 + 30s and < 180s.",
        "",
        "## Per-arm summary",
        "",
        "| Arm | Platform | Median in-window | Stale | Undated | Recall | Head recall "
        "| Completeness | Fail rate | p90 wall | Parity |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    one_liners = []
    for arm in arms:
        s = summary[arm]
        for platform in PLATFORMS:
            p = s["platforms"][platform]
            parity = p.get("parity")
            verdict = "-"
            if parity:
                failed = [k for k, ok in parity["checks"].items() if not ok]
                verdict = "PASS" if parity["pass"] else "FAIL (" + ", ".join(failed) + ")"
            lines.append(
                f"| {arm} | {platform} | {_fmt(p['median_in_window'])} | {p['stale_total']} "
                f"| {p['undated_total']} | {_fmt(p['median_recall'], True)} "
                f"| {_fmt(p['median_head_recall'], True)} | {_fmt(_comp_avg(p['median_completeness']), True)} "
                f"| {_fmt(p['failure_rate'], True)} | {_fmt(s['p90_wall'])}s | {verdict} |"
            )
        tk, ig = s["platforms"]["tiktok"], s["platforms"]["instagram"]
        one_liners.append(
            f"- {arm}: TikTok median {_fmt(tk['median_in_window'])} in-window, head recall "
            f"{_fmt(tk['median_head_recall'], True)}, {tk['stale_total']} stale; Instagram median "
            f"{_fmt(ig['median_in_window'])}, {ig['stale_total']} stale; p90 wall {_fmt(s['p90_wall'])}s"
            + (f"; TikTok parity {'PASS' if tk['parity']['pass'] else 'FAIL'}" if tk.get("parity") else "")
        )
    lines += ["", "## One line per arm", "", *one_liners, "", "## Per topic", ""]
    lines += [
        "| Topic | Arm | Wall | TikTok in/stale/undated | TT recall | TT head | "
        "IG in/stale/undated | IG recall | IG head | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for res in results:
        for arm in arms:
            entry = res["arms"].get(arm)
            if not entry:
                continue
            if entry.get("skipped"):
                lines.append(f"| {res['topic']} | {arm} | - | - | - | - | - | - | - | skipped: {entry['skipped']} |")
                continue
            tk, ig = entry["platforms"]["tiktok"], entry["platforms"]["instagram"]
            notes = []
            if entry["timed_out"]:
                notes.append("timeout")
            elif entry["exit_code"] != 0:
                notes.append(f"exit {entry['exit_code']}")
            for name, p in (("TT", tk), ("IG", ig)):
                if p["failed"]:
                    notes.append(f"{name} failed" + (f": {p['error'][:60]}" if p["error"] else ""))
            lines.append(
                f"| {res['topic']} | {arm} | {_fmt(entry['wall'])}s "
                f"| {tk['in_window']}/{tk['stale']}/{tk['undated']} | {_fmt(tk.get('recall'), True)} "
                f"| {_fmt(tk.get('head_recall'), True)} "
                f"| {ig['in_window']}/{ig['stale']}/{ig['undated']} | {_fmt(ig.get('recall'), True)} "
                f"| {_fmt(ig.get('head_recall'), True)} | {'; '.join(notes).replace('|', '/')} |"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _split(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topics", help="Comma-separated topics; replaces the fixed 24-topic set")
    parser.add_argument("--rotating", help="Comma-separated extra topics appended to the set (e.g. this week's trends)")
    parser.add_argument("--arms", default=",".join(ALL_ARMS), help=f"Comma-separated arms (default: {','.join(ALL_ARMS)})")
    parser.add_argument("--fixtures-dir", type=Path, help="Directory of <topic-slug>.json shortform-urls envelopes for arm D")
    parser.add_argument("--out", type=Path, help="Markdown report path (JSON written next to it)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 12):
        print("shortform_ab.py needs Python 3.12+ (try: uv run python scripts/shortform_ab.py)", file=sys.stderr)
        return 2
    arms = _split(args.arms)
    unknown = [a for a in arms if a not in ALL_ARMS]
    if unknown:
        print(f"Unknown arms: {', '.join(unknown)}", file=sys.stderr)
        return 2
    topics = _split(args.topics) or [t for group in TOPICS.values() for t in group]
    topics += _split(args.rotating)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_md = args.out or Path(tempfile.gettempdir()) / "last30days-shortform-ab" / f"ab-{stamp}.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json = out_md.with_suffix(".json")

    atexit.register(_cleanup_private_dirs)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    keyless = any(a != "A" for a in arms)
    keyless_dir = make_keyless_config_dir() if keyless else None
    skip_keychain = keyless and keychain_has_sc_key()
    workdir = tempfile.mkdtemp(prefix="l30d-ab-cwd-")
    _PRIVATE_DIRS.append(workdir)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=False).stdout.strip() or "unknown"
    meta = {"started": datetime.now().isoformat(timespec="seconds"), "commit": commit,
            "skip_keychain": skip_keychain, "python": sys.version.split()[0]}

    results = []
    try:
        for index, topic in enumerate(topics, 1):
            print(f"[{index}/{len(topics)}] {topic}: running {', '.join(arms)}", file=sys.stderr, flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(arms)) as pool:
                futures = {
                    arm: pool.submit(run_arm, topic, arm, keyless_dir=keyless_dir, skip_keychain=skip_keychain,
                                     fixtures_dir=args.fixtures_dir, workdir=workdir)
                    for arm in arms
                }
                runs = {arm: fut.result() for arm, fut in futures.items()}
            results.append(score_topic(topic, runs))
    finally:
        _cleanup_private_dirs()

    summary = summarize(results, arms)
    out_md.write_text(render_markdown(meta, results, summary, arms), encoding="utf-8")
    out_json.write_text(json.dumps({"meta": meta, "arms": arms, "summary": summary, "topics": results},
                                   indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_md}\nWrote {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
