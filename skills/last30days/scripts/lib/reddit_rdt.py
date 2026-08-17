"""Read-only Reddit search through the locally installed rdt CLI.

This adapter is deliberately small and best-effort: it uses only ``rdt search``
with structured output, never invokes mutation commands, and returns a typed
outcome alongside normalized posts. Callers can fall back to the existing
RSS/shreddit/ArcticShift keyless path when rdt is unavailable or unhealthy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import health
from .relevance import token_overlap_relevance

DEFAULT_TIMEOUT = 20
DEFAULT_LIMITS = {"quick": 10, "default": 25, "deep": 50}


def _command() -> Optional[str]:
    """Resolve the executable without reading or displaying credential files."""
    configured = os.environ.get("LAST30DAYS_RDT_CLI", "").strip()
    if configured:
        return configured
    return shutil.which("rdt") or shutil.which("rdt-cli")


def _date(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc).date().isoformat()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _subreddit(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("display_name") or value.get("name") or ""
    return str(value or "").strip().removeprefix("r/")


def _url(row: Dict[str, Any]) -> str:
    value = str(row.get("url") or row.get("permalink") or "").strip()
    if value.startswith("/"):
        return "https://www.reddit.com" + value
    return value if "reddit.com" in value else ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize(row: Dict[str, Any], index: int, query: str) -> Optional[Dict[str, Any]]:
    title = str(row.get("title") or row.get("name") or "").strip()
    url = _url(row)
    if not title or not url:
        return None
    score = _int(row.get("score") or row.get("ups") or row.get("votes"))
    comments = _int(row.get("num_comments") or row.get("comments"))
    rid = str(row.get("id") or row.get("name") or "").removeprefix("t3_")
    return {
        "id": f"R{index}",
        "reddit_id": rid,
        "title": title,
        "url": url,
        "subreddit": _subreddit(row.get("subreddit")),
        "date": _date(row.get("created_utc") or row.get("created_at")),
        "engagement": {
            "score": score,
            "num_comments": comments,
            "upvote_ratio": row.get("upvote_ratio"),
        },
        "relevance": round(token_overlap_relevance(query, title), 3) if query else 0.0,
        "why_relevant": "Reddit rdt-cli search",
        "selftext": str(row.get("selftext") or row.get("text") or "")[:500],
        "author": str(row.get("author") or "[deleted]"),
        "metadata": {"rdt": True},
    }


def _rows(payload: Any) -> Optional[Iterable[Dict[str, Any]]]:
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    data = payload.get("data", payload)
    if isinstance(data, dict):
        data = data.get("posts", data.get("results", []))
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        return None
    return data


def search(
    query: str,
    from_date: str,
    to_date: str,
    *,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[List[Dict[str, Any]], Optional[health.SourceHealth]]:
    """Run one read-only rdt search and return ``(items, outcome)``.

    ``outcome`` is ``None`` on a successful search, including a valid empty
    result. Failures are typed so the caller can report them while falling back.
    """
    command = _command()
    if not command:
        return [], health.SourceHealth("reddit-rdt", health.MISSING, "rdt CLI not found on PATH")
    args = [command, "search", query, "--json", "--compact", "--limit", str(DEFAULT_LIMITS.get(depth, 25)), "--time", "month"]
    normalized_subs = [s.removeprefix("r/").strip() for s in (subreddits or []) if s.strip()]
    if len(normalized_subs) == 1:
        args.extend(["--subreddit", normalized_subs[0]])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [], health.SourceHealth("reddit-rdt", health.TIMEOUT, f"rdt search timed out after {timeout}s")
    except (OSError, ValueError):
        return [], health.SourceHealth("reddit-rdt", health.ERROR, "rdt CLI could not execute")
    if proc.returncode != 0:
        return [], health.SourceHealth("reddit-rdt", health.ERROR, f"rdt search exited {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, json.JSONDecodeError):
        return [], health.SourceHealth("reddit-rdt", health.SCHEMA_DRIFT, "rdt returned non-JSON output")
    rows = _rows(payload)
    if rows is None:
        return [], health.SourceHealth("reddit-rdt", health.SCHEMA_DRIFT, "rdt JSON did not contain a post list")

    items: List[Dict[str, Any]] = []
    seen = set()
    allowed_subs = {s.lower() for s in normalized_subs}
    for row in rows:
        item = _normalize(row, len(items) + 1, query)
        if item is None:
            continue
        if allowed_subs and item["subreddit"].lower() not in allowed_subs:
            continue
        if item["date"] is not None and not (from_date <= item["date"] <= to_date):
            continue
        key = item["reddit_id"] or item["url"]
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    for index, item in enumerate(items, 1):
        item["id"] = f"R{index}"
    return items, None
