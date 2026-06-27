"""Trustpilot brand-sentiment source for last30days.

Shells out to ``trustpilot-pp-cli`` to surface a company's TrustScore and
Trustpilot's own AI review summary for brand/company topics. Trustpilot has no
API key, but it sits behind AWS WAF: the CLI harvests an ``aws-waf-token`` via
a one-time headless Chrome launch (~10s), then replays it over plain HTTP until
it expires.

Activation gate: only available when ``trustpilot-pp-cli`` is on PATH.
``pipeline.available_sources`` checks ``shutil.which`` before including
``trustpilot``.

Default-on safety (three gates):
  1. Brand-shape gate. The CLI is invoked only when the topic resolves to a
     company/brand -- a domain-like token, or a short (<=2-word) capitalized
     proper noun. Generic phrases ("AI coding agents", "agent memory") and
     longer multi-word phrases never call the CLI, so Trustpilot stays quiet --
     and never harvests Chrome -- on non-company topics.
  2. Browser opt-out. Automated contexts (cron, CI, the eval harness) can set
     ``LAST30DAYS_TRUSTPILOT_NO_BROWSER`` to disable the source entirely, so a
     headless run never spawns the cookie harvest.
  3. Graceful degradation. Any CLI failure (no Chrome, expired cookie that
     cannot re-harvest, timeout) degrades to empty results, never an error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from . import dates, log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "trustpilot-pp-cli"

SEARCH_TIMEOUT = 75  # generous: a cold run may harvest a WAF cookie (~10s).

NO_BROWSER_ENV = "LAST30DAYS_TRUSTPILOT_NO_BROWSER"

# Domain-like token, e.g. "chownow.com", "nothing.tech".
_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(com|io|co|net|org|app|ai|dev|gg|tech|shop|store)\b")

# Generic tokens that disqualify a short capitalized phrase from being a brand.
_GENERIC_TOKENS = {
    "ai", "best", "top", "vs", "review", "reviews", "guide", "tutorial",
    "how", "what", "why", "agents", "agent", "memory", "tips", "news",
}


def _log(msg: str) -> None:
    log.source_log("Trustpilot", msg, tty_only=False)


def _is_available() -> bool:
    """True when the trustpilot-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _harvest_allowed(config: Optional[Dict[str, Any]]) -> bool:
    """False when the browser opt-out is set (automated/headless contexts).

    Reads the opt-out from the merged config AND directly from the process
    environment. The env fallback is load-bearing: ``config`` is assembled from
    an allowlist in ``env.get_config``, so a fallback here guarantees the
    documented kill-switch works even when the key is not propagated into
    config (e.g. a bare ``LAST30DAYS_TRUSTPILOT_NO_BROWSER=1`` in cron/CI).
    """
    if config and _truthy(config.get(NO_BROWSER_ENV)):
        return False
    if _truthy(os.environ.get(NO_BROWSER_ENV)):
        return False
    return True


def is_brand_shaped(topic: str) -> bool:
    """True when the topic looks like a company/brand Trustpilot can resolve.

    A domain-like token always qualifies. Otherwise the topic must be a short
    (<=2-word) capitalized proper noun with no generic tokens -- this lets
    "ChowNow", "Nothing Phone", and "OpenAI" through while keeping "AI coding
    agents", "agent memory", and "Golden State Warriors" out.
    """
    if not topic or not topic.strip():
        return False
    text = topic.strip()
    if _DOMAIN_RE.search(text.lower()):
        return True
    words = text.split()
    if len(words) > 2:
        return False
    if any(w.lower() in _GENERIC_TOKENS for w in words):
        return False
    # At least one token must look like a proper noun (leading capital).
    return any(w[:1].isupper() for w in words)


def _company_identifier(topic: str) -> str:
    """Pick the identifier to hand the CLI: a domain token if present, else the
    cleaned topic string."""
    m = _DOMAIN_RE.search(topic.lower())
    if m:
        return m.group(0)
    return topic.strip()


def _build_info_args(identifier: str) -> List[str]:
    return [CLI_BIN, "info", identifier, "--agent"]


def _run_cli(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Invoke trustpilot-pp-cli and parse the JSON object. Never raises."""
    if not _is_available():
        return {"error": f"{CLI_BIN} not on PATH"}
    try:
        result = subproc.run_with_timeout(cmd, timeout=timeout)
    except subproc.SubprocTimeout as exc:
        _log(f"Timeout: {exc}")
        return {"error": str(exc)}
    except FileNotFoundError as exc:
        _log(f"Binary missing: {exc}")
        return {"error": str(exc)}
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return {"error": str(exc)}

    if result.returncode != 0:
        snippet = (result.stderr or "").strip().splitlines()[:1]
        first = snippet[0] if snippet else f"exit {result.returncode}"
        _log(f"CLI exit {result.returncode}: {first}")
        return {"error": first}

    stdout = result.stdout or ""
    if not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log(f"JSON decode failed: {exc}")
        return {"error": f"json decode: {exc}"}
    return data if isinstance(data, dict) else {}


def search_trustpilot(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Look up a company's Trustpilot sentiment, gated on a brand-shaped topic.

    Returns ``{"results": [info_dict]}`` for a resolved company, or
    ``{"results": []}`` when the topic is not brand-shaped, the browser opt-out
    is set, or the CLI fails.
    """
    if not is_brand_shaped(topic):
        return {"results": []}
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    if not _harvest_allowed(config):
        _log("skipped: browser opt-out set")
        return {"results": []}
    identifier = _company_identifier(topic)
    _log(f"info '{identifier}'")
    data = _run_cli(_build_info_args(identifier), timeout=SEARCH_TIMEOUT)
    if "error" in data or not data:
        return {"results": []}
    return {"results": [data]}


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_trustpilot_response(
    response: Dict[str, Any],
    query: str = "",
) -> List[Dict[str, Any]]:
    """Parse a Trustpilot ``info`` envelope into a single normalized item.

    The AI summary is the body (it already balances positive and negative
    sentiment). TrustScore and review count feed engagement and metadata.
    Returns dicts ready for ``normalize._normalize_trustpilot``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list) or not raw:
        return []
    info = raw[0]
    if not isinstance(info, dict):
        return []

    resolved_name = str(info.get("name") or info.get("displayName") or "").strip()
    ai_summary = str(info.get("aiSummary") or info.get("summary") or "").strip()
    trust_score = _coerce_float(info.get("trustScore") or info.get("score"))
    review_count = _coerce_int(
        info.get("reviewCount") or info.get("numberOfReviews") or info.get("total")
    )
    url = str(info.get("url") or "").strip()
    domain = str(info.get("domain") or info.get("identifyingName") or "").strip()
    if not url and domain:
        url = f"https://www.trustpilot.com/review/{domain}"

    # Require substantive content from the company record itself; do not
    # fabricate an item from the query alone when the CLI returned nothing.
    if not resolved_name and not ai_summary and trust_score is None and review_count is None:
        return []

    name = resolved_name or query.strip()

    title = f"{name} on Trustpilot" if name else "Trustpilot reviews"
    if trust_score is not None:
        title = f"{name}: TrustScore {trust_score}" if name else title

    engagement: Dict[str, float | int] = {}
    if review_count is not None:
        engagement["reviews"] = review_count
    if trust_score is not None:
        engagement["trustScore"] = trust_score

    relevance = token_overlap_relevance(query, name) if (query and name) else 0.7

    why = "Trustpilot brand sentiment"
    if trust_score is not None and review_count is not None:
        why = f"Trustpilot: TrustScore {trust_score} across {review_count} reviews"
    elif trust_score is not None:
        why = f"Trustpilot: TrustScore {trust_score}"

    return [
        {
            "id": domain or name or "trustpilot",
            "title": title,
            "url": url,
            "summary": ai_summary,
            "name": name,
            "trustScore": trust_score,
            "reviewCount": review_count,
            "date": dates.get_date_range(1)[0],
            "engagement": engagement,
            "relevance": round(min(1.0, max(0.4, relevance)), 2),
            "why_relevant": why,
        }
    ]
