"""Techmeme tech-news source for last30days.

Shells out to ``techmeme-pp-cli`` (no auth) to surface current tech-news
headlines relevant to a topic. Techmeme caches headlines locally, so the
adapter ensures a one-time sync per run, then searches the cache.

Activation gate: only available when ``techmeme-pp-cli`` is on PATH.
``pipeline.available_sources`` checks ``shutil.which`` before including
``techmeme``. The functions below also detect the missing-binary case.

Surface choice: ``search "<topic>" --json`` (NOT ``--agent``). ``--agent``
implies ``--compact``, which on older binaries stripped headline records to
``{}`` (fixed upstream in printing-press-library PR #1383); ``--json`` without
``--compact`` returns the populated ``{num, source, headline, link}`` shape on
every binary version, so the adapter is robust regardless of the installed
build.

The search shape carries no per-item date or velocity, so headlines are dated
to the sync time (Techmeme's cache is the current news cycle) and ranked on
topic relevance plus source quality. Publication-name header rows (very short
``headline`` values) are dropped.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List

from . import log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "techmeme-pp-cli"

DEPTH_CONFIG = {
    "quick": 8,
    "default": 16,
    "deep": 30,
}

# A real story headline is a sentence; bare publication-name rows ("TechCrunch",
# "New York Times") are section headers in the feed, not stories. Require at
# least this many words to keep a record.
MIN_HEADLINE_WORDS = 4

SEARCH_TIMEOUT = 30
SYNC_TIMEOUT = 40

# Sync at most once per process; a last30days run is one process and the cache
# is sub-second to refresh.
_SYNCED = False


def _log(msg: str) -> None:
    log.source_log("Techmeme", msg, tty_only=False)


def _is_available() -> bool:
    """True when the techmeme-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _ensure_synced() -> None:
    """Refresh the local headline cache once per process. Best-effort: a sync
    failure is logged and ignored (search can still run on an existing cache)."""
    global _SYNCED
    if _SYNCED:
        return
    _SYNCED = True  # set first so a failure does not retry every subquery
    try:
        subproc.run_with_timeout([CLI_BIN, "sync", "--agent"], timeout=SYNC_TIMEOUT)
        _log("synced headline cache")
    except (subproc.SubprocTimeout, FileNotFoundError, OSError) as exc:
        _log(f"sync skipped: {exc}")


def _build_search_args(topic: str) -> List[str]:
    # --json (not --agent) avoids --compact, which blanks headline records on
    # pre-PR-1383 binaries. Techmeme's `search` has no result-limit flag, so the
    # depth cap is applied client-side after parsing.
    return [CLI_BIN, "search", topic, "--json"]


def _coerce_list(data: Any) -> List[Dict[str, Any]]:
    """Techmeme search returns a bare JSON array; tolerate a results-wrapped
    envelope too."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    return []


def _run_cli(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Invoke techmeme-pp-cli and return ``{"results": [...records...]}``.
    Never raises."""
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    try:
        result = subproc.run_with_timeout(cmd, timeout=timeout)
    except subproc.SubprocTimeout as exc:
        _log(f"Timeout: {exc}")
        return {"results": [], "error": str(exc)}
    except FileNotFoundError as exc:
        _log(f"Binary missing: {exc}")
        return {"results": [], "error": str(exc)}
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return {"results": [], "error": str(exc)}

    if result.returncode != 0:
        snippet = (result.stderr or "").strip().splitlines()[:1]
        first = snippet[0] if snippet else f"exit {result.returncode}"
        _log(f"CLI exit {result.returncode}: {first}")
        return {"results": [], "error": first}

    stdout = result.stdout or ""
    if not stdout.strip():
        return {"results": []}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log(f"JSON decode failed: {exc}")
        return {"results": [], "error": f"json decode: {exc}"}

    return {"results": _coerce_list(data)}


def search_techmeme(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search Techmeme headlines via techmeme-pp-cli.

    Ensures a one-time cache sync, then searches. Returns a dict with a
    ``results`` list of raw records. On failure, ``results`` is empty.
    """
    if not topic or not topic.strip():
        return {"results": []}
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    _ensure_synced()
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    cmd = _build_search_args(topic)
    _log(f"search '{topic}' (cap={limit})")
    response = _run_cli(cmd, timeout=SEARCH_TIMEOUT)
    # Techmeme returns all matches; apply the depth cap client-side.
    records = response.get("results") or []
    if isinstance(records, list) and len(records) > limit:
        response["results"] = records[:limit]
    _log(f"found {len(response.get('results') or [])} records")
    return response


def _is_story_headline(headline: str, source: str) -> bool:
    """Reject bare publication-name header rows; keep sentence-shaped stories."""
    if not headline:
        return False
    if len(headline.split()) < MIN_HEADLINE_WORDS:
        return False
    # A row whose headline is just the publication name is a header.
    if source and headline.strip().lower() == source.strip().lower():
        return False
    return True


def parse_techmeme_response(
    response: Dict[str, Any],
    query: str = "",
) -> List[Dict[str, Any]]:
    """Parse a Techmeme search envelope into normalized item dicts.

    Drops publication-name header rows and records missing a link. Dates each
    headline to today (the cache is the current news cycle) and computes a
    token-overlap relevance hint. Returns dicts ready for
    ``normalize._normalize_techmeme``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []

    today = _today_iso()
    items: List[Dict[str, Any]] = []
    for i, rec in enumerate(raw):
        if not isinstance(rec, dict):
            continue
        headline = " ".join(str(rec.get("headline") or "").split()).strip()
        source_name = str(rec.get("source") or "").strip()
        if not _is_story_headline(headline, source_name):
            continue
        link = str(rec.get("link") or "").strip()
        if not link:
            continue

        rank_decay = max(0.3, 1.0 - (i * 0.03))
        content_score = token_overlap_relevance(query, headline) if query else 0.5
        relevance = min(1.0, 0.55 * rank_decay + 0.45 * content_score)

        items.append(
            {
                "id": link,
                "title": headline,
                "url": link,
                "source_name": source_name,
                "date": today,
                "engagement": {},
                "relevance": round(relevance, 2),
                "why_relevant": (
                    f"Techmeme headline ({source_name})" if source_name else "Techmeme headline"
                ),
            }
        )

    return items
