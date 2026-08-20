"""Techmeme tech-news source for last30days.

Shells out to ``techmeme-pp-cli`` (no auth). The CLI's ``search`` command hits
Techmeme's live archive search endpoint (results back to ~2005) -- it never
reads the locally synced headline cache, so the adapter performs no ``sync``.

Activation gate: only available when ``techmeme-pp-cli`` is on PATH.
``pipeline.available_sources`` checks ``shutil.which`` before including
``techmeme``. The functions below also detect the missing-binary case.

Surface choice: ``search "<topic>" --json`` (NOT ``--agent``). ``--agent``
implies ``--compact``, which on older binaries stripped headline records to
``{}`` (fixed upstream in printing-press-library PR #1383); ``--json`` without
``--compact`` returns the populated record shape on every binary version, so
the adapter is robust regardless of the installed build.

Dates: current binaries emit ``{num, source, headline, link, date}`` where
``date`` is ISO ``YYYY-MM-DD`` (or ``""`` when Techmeme's markup was
unparseable). The adapter windows records to the research range
(``from_date <= date <= to_date``) so archive hits from years past never
masquerade as current news. Records with no usable date -- old binaries emit
no ``date`` key at all -- are kept but flow downstream with no date, so
``normalize._normalize_techmeme`` assigns ``date_confidence: low``. Headlines
are never stamped with today's date. (This deliberately diverges from
``lib/arxiv.py``, which drops entries with unparseable dates -- arXiv's feed
reliably carries dates, so an unparseable one is anomalous; Techmeme's old
binaries emit no ``date`` key at all, so dropping would zero out the source
for every user on an old binary.) Old binaries also print prose
(``No results for "q"``) to stdout on zero hits; that parses as an empty
result set, not a decode failure. Publication-name header rows (very short
``headline`` values) are dropped; ranking is topic relevance plus rank decay.
"""

from __future__ import annotations

import json
import re
import shutil
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

# Old binaries print this prose to stdout (exit 0) on zero hits, even in JSON
# mode. It is a zero-result response, not malformed output.
_NO_RESULTS_PREFIX = "No results"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _log(msg: str) -> None:
    log.source_log("Techmeme", msg, tty_only=False)


def _is_available() -> bool:
    """True when the techmeme-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _build_search_args(topic: str) -> List[str]:
    # --json (not --agent) avoids --compact, which blanks headline records on
    # pre-PR-1383 binaries. Techmeme's `search` has no result-limit flag, so the
    # depth cap is applied client-side after windowing.
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


def _record_iso_date(rec: Dict[str, Any]) -> str | None:
    """The record's ``date`` as a valid ISO YYYY-MM-DD string, else None.

    Old binaries emit no ``date`` key; current binaries emit ``""`` when
    Techmeme's markup was unparseable. Anything that isn't a clean ISO date is
    treated as absent."""
    value = rec.get("date")
    if isinstance(value, str) and _ISO_DATE_RE.match(value.strip()):
        return value.strip()
    return None


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
    # Old binaries print `No results for "q"` prose (exit 0) even in JSON
    # mode: a legitimate zero-hit response, not a decode failure.
    if stdout.strip().startswith(_NO_RESULTS_PREFIX):
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
    """Search Techmeme's live archive via techmeme-pp-cli.

    Windows records to ``from_date..to_date`` on each record's own ISO date
    (lexicographic compare is exact for ISO strings). Records with no usable
    date are kept -- their recency is resolved downstream as low confidence.
    There is deliberately no keep-all fallback when nothing is in-window:
    Techmeme's archive reaches back decades, so zero in-window records means
    zero results, not "serve stale news". Returns a dict with a ``results``
    list of raw records; on failure ``results`` is empty.
    """
    if not topic or not topic.strip():
        return {"results": []}
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    cmd = _build_search_args(topic)
    _log(f"search '{topic}' (cap={limit})")
    response = _run_cli(cmd, timeout=SEARCH_TIMEOUT)
    records = response.get("results") or []
    if isinstance(records, list):
        # Hard date window: drop records whose date falls outside the research
        # range; keep undated records (old binaries / unparseable markup).
        dated_in_window = []
        undated = []
        dropped = 0
        for rec in records:
            iso = _record_iso_date(rec)
            if iso is None:
                undated.append(rec)
            elif from_date <= iso <= to_date:
                dated_in_window.append(rec)
            else:
                dropped += 1
        if dropped:
            _log(f"dropped {dropped} records outside {from_date}..{to_date}")
        if records and not dated_in_window and not dropped:
            # Every record lacks a usable date: old techmeme-pp-cli (no date
            # key) or a Techmeme markup change upstream. Windowing is inactive.
            _log(
                "no records carry usable dates; date windowing inactive "
                "(old techmeme-pp-cli binary or upstream markup change; upgrade "
                "via `npx -y @mvanhorn/printing-press-library install techmeme "
                "--cli-only`)"
            )
        # Techmeme returns all matches; apply the depth cap after windowing.
        # Dated in-window records take cap slots first so undated archive
        # hits can never evict confirmed-fresh stories; undated records fill
        # whatever slots remain.
        response["results"] = (dated_in_window + undated)[:limit]
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

    Drops publication-name header rows and records missing a link. Each item
    carries the record's own ISO date, or None when the record has no usable
    date (never today's date -- undated items get ``date_confidence: low``
    downstream). Computes a token-overlap relevance hint. Returns dicts ready
    for ``normalize._normalize_techmeme``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []

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
                "date": _record_iso_date(rec),
                "engagement": {},
                "relevance": round(relevance, 2),
                "why_relevant": (
                    f"Techmeme headline ({source_name})" if source_name else "Techmeme headline"
                ),
            }
        )

    return items
