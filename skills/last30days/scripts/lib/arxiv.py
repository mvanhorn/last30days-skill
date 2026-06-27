"""arXiv research-paper source for last30days.

Shells out to ``arxiv-pp-cli`` (open Atom API, no auth) to surface recent
research papers relevant to a topic. arXiv carries no engagement signal, so
ranking leans on relevance (the CLI's own relevance sort plus token overlap)
and recency.

Activation gate: this source is only available when ``arxiv-pp-cli`` is on
PATH. ``pipeline.available_sources`` checks ``shutil.which`` before including
``arxiv``. The functions below also detect the missing-binary case defensively.

Default-on safety (two gates, both required):
  1. Query construction. arXiv is queried with a *quoted* phrase and
     ``--sort-by relevance``. Sorting by submitted-date instead returns the
     newest cs.* papers regardless of topic -- topic-blind noise.
  2. Recency cutoff. Entries older than ``RECENCY_DAYS`` are dropped. Research
     does not trend on a 30-day clock, so this window is wider than the social
     sources' 30 days; it keeps arXiv current while dropping stale keyword
     matches (e.g. a 2017 sports-statistics paper that an off-topic query like
     "Golden State Warriors" would otherwise surface).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "arxiv-pp-cli"

# Per-depth result counts.
DEPTH_CONFIG = {
    "quick": 5,
    "default": 10,
    "deep": 20,
}

# Recency window for arXiv specifically. Papers do not trend daily; a year keeps
# the source current (the off-topic 2017 paper still drops) without discarding
# the genuinely-relevant work from the last few months.
RECENCY_DAYS = 365

SEARCH_TIMEOUT = 30


def _log(msg: str) -> None:
    log.source_log("arXiv", msg, tty_only=False)


def _is_available() -> bool:
    """True when the arxiv-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _build_search_query(topic: str) -> str:
    """Quote the topic so arXiv treats it as a phrase across all fields.

    Inner double-quotes are stripped (arXiv has no phrase-escaping); the outer
    quotes plus ``all:`` give a phrase-scoped relevance search.
    """
    cleaned = " ".join(topic.replace('"', " ").split())
    return f'all:"{cleaned}"'


def _build_search_args(topic: str, limit: int) -> List[str]:
    return [
        CLI_BIN,
        "query",
        "--search-query",
        _build_search_query(topic),
        "--sort-by",
        "relevance",
        "--max-results",
        str(limit),
        "--agent",
    ]


def _run_cli(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Invoke arxiv-pp-cli and parse the JSON envelope.

    arXiv returns ``{"meta": ..., "results": {"entries": [...]}}``. This
    normalizes to ``{"results": [...entries...]}`` so the parse step sees a
    flat list, matching the other sources' shape. Never raises.
    """
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

    return {"results": _extract_entries(data)}


def _extract_entries(data: Any) -> List[Dict[str, Any]]:
    """Pull the entries list out of arXiv's nested envelope.

    Tolerates ``{"results": {"entries": [...]}}`` (current shape),
    ``{"entries": [...]}``, and a bare list.
    """
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, dict):
            entries = results.get("entries")
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict)]
        if isinstance(results, list):
            return [e for e in results if isinstance(e, dict)]
        entries = data.get("entries")
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
    return []


def search_arxiv(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search arXiv via arxiv-pp-cli using a quoted, relevance-sorted query.

    Returns a dict with a flat ``results`` list of entry dicts. On failure,
    ``results`` is empty and an ``error`` key carries a one-line description.
    """
    if not topic or not topic.strip():
        return {"results": []}
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    cmd = _build_search_args(topic, limit)
    _log(f"query '{topic}' (relevance, max={limit})")
    response = _run_cli(cmd, timeout=SEARCH_TIMEOUT)
    _log(f"found {len(response.get('results') or [])} entries")
    return response


def _parse_published(published: Optional[str]) -> Optional[datetime]:
    """Parse an arXiv ``published`` timestamp (ISO 8601, e.g.
    '2026-06-25T17:59:48Z') into an aware datetime. Returns None on failure."""
    if not published or not isinstance(published, str):
        return None
    text = published.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _alternate_url(entry: Dict[str, Any]) -> str:
    """Return the human-facing abstract URL (rel=alternate), not the PDF."""
    links = entry.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("rel") == "alternate":
                href = str(link.get("href") or "").strip()
                if href:
                    return href
    # Fall back to the abstract URL derived from the entry id.
    entry_id = str(entry.get("id") or "").strip()
    if entry_id.startswith("http"):
        return entry_id
    return ""


def _author_names(entry: Dict[str, Any]) -> List[str]:
    authors = entry.get("authors")
    out: List[str] = []
    if isinstance(authors, list):
        for a in authors:
            if isinstance(a, dict):
                name = str(a.get("name") or "").strip()
                if name:
                    out.append(name)
    return out


def parse_arxiv_response(
    response: Dict[str, Any],
    query: str = "",
    today: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Parse an arXiv envelope into normalized item dicts.

    Applies the recency cutoff (drops entries older than ``RECENCY_DAYS`` and
    entries with an unparseable date) and computes a token-overlap relevance
    hint. Returns dicts ready for ``normalize._normalize_arxiv``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []

    now = today or _today()
    items: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        title = " ".join(str(entry.get("title") or "").split()).strip()
        if not title:
            continue
        published = _parse_published(entry.get("published") or entry.get("updated"))
        if published is None:
            # No usable date -> cannot honor the recency contract; drop.
            continue
        age_days = (now - published).days
        if age_days > RECENCY_DAYS or age_days < 0:
            continue

        summary = " ".join(str(entry.get("summary") or "").split()).strip()
        authors = _author_names(entry)
        url = _alternate_url(entry)

        rank_decay = max(0.3, 1.0 - (i * 0.03))
        if query:
            content_score = token_overlap_relevance(query, f"{title} {summary}".strip())
        else:
            content_score = 0.5
        relevance = min(1.0, 0.6 * rank_decay + 0.4 * content_score)

        primary_author = authors[0] if authors else ""
        author_label = primary_author
        if len(authors) > 1:
            author_label = f"{primary_author} et al."

        items.append(
            {
                "id": str(entry.get("id") or url or f"AX{i + 1}"),
                "title": title,
                "url": url,
                "summary": summary,
                "author": author_label,
                "authors": authors,
                "date": published.date().isoformat(),
                "engagement": {},
                "relevance": round(relevance, 2),
                "why_relevant": (
                    f"arXiv paper ({primary_author}, {published.date().isoformat()})"
                    if primary_author
                    else f"arXiv paper ({published.date().isoformat()})"
                ),
            }
        )

    return items
