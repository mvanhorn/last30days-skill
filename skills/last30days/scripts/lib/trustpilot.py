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
     and never harvests Chrome -- on non-company topics. An explicit resolved
     domain (``--trustpilot-domain`` or an auto-resolve hint) bypasses this
     gate: an explicit domain is proof of brand intent.
  2. Browser opt-out. Automated contexts (cron, CI, the eval harness) can set
     ``LAST30DAYS_TRUSTPILOT_NO_BROWSER`` to disable the source entirely, so a
     headless run never spawns the cookie harvest.
  3. Graceful degradation. Any CLI failure (no Chrome, expired cookie that
     cannot re-harvest, timeout) degrades to empty results, never an error.

Domain resolution: Trustpilot review pages are keyed by domain
(``www.thriftbooks.com``), not company name -- ``info ThriftBooks`` 404s.
Priority chain: user flag (verbatim) > auto-resolve hint (retries via search
on a miss) > domain token in the topic > CLI ``search`` name->domain lookup
(cached per topic) > cleaned topic (legacy behavior).

Session pre-flight: ``ensure_session_ready`` performs one serialized
``auth status`` / ``auth login`` before the parallel fan-out so concurrent
streams and vs-mode sub-runs never race their own Chrome harvests.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from . import dates, log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "trustpilot-pp-cli"

SEARCH_TIMEOUT = 75  # generous: a cold run may harvest a WAF cookie (~10s).

AUTH_STATUS_TIMEOUT = 20  # auth status is a local SQLite read; fast.

NO_BROWSER_ENV = "LAST30DAYS_TRUSTPILOT_NO_BROWSER"

# Among name-matching search hits, the top hit must have this many times the
# runner-up's review volume to win automatically. Lookalike/squatter pages have
# tiny volume (ThriftBooks: 2.8M vs 130); comparable volume means genuine
# ambiguity, where falling back beats silently picking the wrong company.
DOMAIN_DOMINANCE_FACTOR = 50

# Domain-like token, e.g. "chownow.com", "nothing.tech".
_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(com|io|co|net|org|app|ai|dev|gg|tech|shop|store)\b")

# Generic tokens that disqualify a short capitalized phrase from being a brand.
_GENERIC_TOKENS = {
    "ai", "best", "top", "vs", "review", "reviews", "guide", "tutorial",
    "how", "what", "why", "agents", "agent", "memory", "tips", "news",
}

# Single-word programming languages, frameworks, runtimes, OSes, and dev tools.
# A bare capitalized "Python"/"React"/"Docker" query is overwhelmingly about the
# technology, not a company's customer reviews -- letting it through would both
# trigger the Chrome harvest and risk surfacing an unrelated company that shares
# the name. A user who genuinely wants the company can use its domain
# (e.g. "docker.com"), which still passes via the domain branch.
_TECH_TOKENS = {
    "python", "javascript", "typescript", "java", "rust", "ruby", "php",
    "kotlin", "scala", "golang", "swift", "elixir", "erlang", "haskell",
    "react", "vue", "angular", "svelte", "django", "flask", "rails", "spring",
    "node", "nodejs", "deno", "bun", "express", "nextjs", "nuxt",
    "linux", "ubuntu", "debian", "fedora", "windows", "macos", "android",
    "docker", "kubernetes", "k8s", "terraform", "ansible", "nginx",
    "redis", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "kafka",
    "graphql", "webpack", "vite", "rust", "wasm",
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
    if any(w.lower() in _GENERIC_TOKENS or w.lower() in _TECH_TOKENS for w in words):
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


def _normalize_name(text: str) -> str:
    """Case/whitespace/punctuation-insensitive brand-name key."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# name->domain results, keyed by normalized topic. Per-topic (NOT a single
# process-wide slot): vs-mode resolves several entities in one process, and a
# single slot would serve entity A's domain to entity B.
_domain_cache: Dict[str, Optional[str]] = {}
_domain_cache_lock = threading.Lock()

_warmup_lock = threading.Lock()
_warmup_at: Optional[float] = None  # time.monotonic() of the last warm-up

# Warm-up freshness window, matching the CLI's ~4-minute safe replay bound for
# WAF tokens. A boolean-forever flag would leave long-lived host processes
# running stale (and never retrying a failed login); the TTL re-checks cheaply
# via `auth status` once the window lapses.
WARMUP_TTL_SECONDS = 240


def _reset_state_for_tests() -> None:
    """Clear module-level caches/flags (tests only)."""
    global _warmup_at
    with _domain_cache_lock:
        _domain_cache.clear()
    _warmup_at = None


def _warmup_fresh() -> bool:
    return _warmup_at is not None and (time.monotonic() - _warmup_at) < WARMUP_TTL_SECONDS


def _select_search_hit(topic: str, hits: List[Any]) -> Optional[str]:
    """Pick the canonical domain from search hits, or None when ambiguous.

    Name-match is mandatory: review volume must never override a name
    mismatch, or the engine attributes another company's reviews to the topic.
    Among name-matching hits the winner must dominate on review volume
    (DOMAIN_DOMINANCE_FACTOR); comparable volume is genuine ambiguity and
    falls back to legacy behavior.
    """
    want = _normalize_name(topic)
    if not want:
        return None
    matching: List[tuple[int, str]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        domain = str(hit.get("domain") or hit.get("identifyingName") or "").strip()
        name = str(hit.get("displayName") or hit.get("name") or "").strip()
        if not domain or _normalize_name(name) != want:
            continue
        try:
            count = int(hit.get("numberOfReviews") or 0)
        except (TypeError, ValueError):
            count = 0
        matching.append((count, domain))
    if not matching:
        top = next(
            (str(h.get("domain") or "").strip() for h in hits
             if isinstance(h, dict) and h.get("domain")),
            "",
        )
        if top:
            _log(
                f"no name-matching search hit; top candidate was '{top}' - "
                f"pass --trustpilot-domain to target it"
            )
        return None
    matching.sort(reverse=True)
    if len(matching) == 1:
        return matching[0][1]
    top_count, top_domain = matching[0]
    runner_count, runner_domain = matching[1]
    if top_count >= max(1, runner_count) * DOMAIN_DOMINANCE_FACTOR:
        return top_domain
    _log(
        f"ambiguous search hits ('{top_domain}' vs '{runner_domain}'); "
        f"falling back - pass --trustpilot-domain to disambiguate"
    )
    return None


def _search_domain(topic: str) -> Optional[str]:
    """Resolve a company name to its Trustpilot domain via the CLI's search.

    Cached per normalized topic (thread-safe), so repeat lookups cost one
    subprocess while vs-mode entities still resolve independently. Only
    definitive results are cached: a transient CLI failure (timeout, spawn
    error, malformed JSON) returns None WITHOUT caching, so one flaky search
    does not suppress resolution for this topic for the rest of the process.
    """
    key = _normalize_name(topic)
    if not key:
        return None
    with _domain_cache_lock:
        if key in _domain_cache:
            return _domain_cache[key]
    data = _run_cli(
        [CLI_BIN, "search", topic.strip(), "--limit", "5", "--agent"],
        timeout=SEARCH_TIMEOUT,
    )
    if not isinstance(data, dict) or "error" in data:
        return None  # transient failure: retry on the next lookup
    hits = data.get("hits")
    if not isinstance(hits, list):
        # Degenerate payload (e.g. empty stdout parses to {}): not a
        # definitive no-match -- do not cache, retry on the next lookup.
        return None
    domain = _select_search_hit(topic, hits)
    if domain:
        _log(f"resolved '{topic}' -> '{domain}' via search")
    with _domain_cache_lock:
        _domain_cache[key] = domain
    return domain


def _is_session_fresh(status: Dict[str, Any]) -> bool:
    """Read the freshness signal from an ``auth status --agent`` payload."""
    if not isinstance(status, dict) or "error" in status:
        return False
    containers: List[Dict[str, Any]] = [status]
    session = status.get("session")
    if isinstance(session, dict):
        containers.append(session)
    for container in containers:
        for key in ("isFresh", "fresh"):
            if key in container:
                return bool(container[key])
    return False


def ensure_session_ready(
    topic: str,
    config: Optional[Dict[str, Any]] = None,
    has_domain: bool = False,
) -> None:
    """Warm the CLI's WAF session, serialized, at the first Trustpilot fetch.

    Called from ``search_trustpilot`` (never from the pipeline's fan-out
    setup), so it only ever delays the one capped Trustpilot stream, never
    the other sources -- and never fires for runs whose plan fetches no
    Trustpilot at all. The module lock serializes concurrent streams (vs-mode
    fans out up to 6 entity sub-runs) so they never race their own Chrome
    harvests. Freshness is a monotonic TTL (WARMUP_TTL_SECONDS, matching the
    CLI's ~4-minute token bound), not a boolean-forever flag: long-lived host
    processes re-check via ``auth status`` after the window lapses, which
    also retries a previously failed login. ``auth login`` fires only when
    ``auth status`` reports the session missing or stale -- login always
    harvests (~10s Chrome), it has no freshness no-op. Logs only structured
    status strings; the raw CLI payload carries live WAF-token prefixes and
    must never be logged. Never raises.
    """
    global _warmup_at
    if _warmup_fresh():
        return
    if not _is_available():
        return
    if not _harvest_allowed(config):
        return
    if not has_domain and not is_brand_shaped(topic):
        return
    with _warmup_lock:
        if _warmup_fresh():
            return
        status = _run_cli(
            [CLI_BIN, "auth", "status", "--agent"], timeout=AUTH_STATUS_TIMEOUT
        )
        if _is_session_fresh(status):
            _log("warm-up: fresh")
            _warmup_at = time.monotonic()
            return
        # Missing session exits non-zero (an error dict here): that is the
        # "login needed" signal, not a warm-up failure.
        login = _run_cli([CLI_BIN, "auth", "login", "--agent"], timeout=SEARCH_TIMEOUT)
        if isinstance(login, dict) and "error" in login:
            _log("warm-up failed: auth login did not complete")
        else:
            _log("warm-up: harvested")
        # Stamp even on failure: a broken Chrome will not fix itself within
        # the TTL, per-call CLI auto-harvest remains the fallback, and the
        # TTL lapse retries the warm-up later.
        _warmup_at = time.monotonic()


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
    explicit_domain: Optional[str] = None,
    domain_is_hint: bool = False,
) -> Dict[str, Any]:
    """Look up a company's Trustpilot sentiment, gated on a brand-shaped topic.

    ``explicit_domain`` is used verbatim as the CLI identifier and bypasses
    the brand-shape gate (an explicit domain is proof of brand intent). A
    user-set domain is verbatim-final; a resolved hint
    (``domain_is_hint=True``) retries via the CLI search when the lookup
    misses, since auto-resolve can guess a plausible-but-wrong domain (the
    official site is not always Trustpilot's canonical identifyingName).

    Without an explicit domain, a bare company name resolves via the CLI's
    ``search`` (cached per topic) before falling back to the cleaned topic.

    Returns ``{"results": [info_dict]}`` for a resolved company, or
    ``{"results": []}`` when the topic is not brand-shaped, the browser
    opt-out is set, or the CLI fails.
    """
    explicit_domain = (explicit_domain or "").strip() or None
    user_domain = bool(explicit_domain) and not domain_is_hint
    # Only a USER-set domain proves brand intent and bypasses the brand-shape
    # gate. An auto-resolved hint must not widen activation beyond
    # brand-shaped topics, or a generic topic that happens to yield a hint
    # would trigger a Chrome harvest -- violating the module's documented
    # "never harvests on non-company topics" contract.
    if not user_domain and not is_brand_shaped(topic):
        return {"results": []}
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    if not _harvest_allowed(config):
        _log("skipped: browser opt-out set")
        return {"results": []}
    # Serialized session check at first source touch (all gates above have
    # passed, so this never fires for topics the source would not fetch).
    ensure_session_ready(topic, config=config, has_domain=bool(explicit_domain))
    # Retry-budget timer starts AFTER the warm-up: a slow Chrome harvest must
    # not consume the hint-retry budget when the info call itself was fast.
    started = time.monotonic()
    if explicit_domain:
        identifier = explicit_domain
    else:
        identifier = _company_identifier(topic)
        if not _DOMAIN_RE.search(topic.lower()):
            # No domain token in the topic: Trustpilot pages are keyed by
            # domain, so resolve name -> domain before the info lookup.
            identifier = _search_domain(topic) or identifier
    _log(f"info '{identifier}'")
    data = _run_cli(_build_info_args(identifier), timeout=SEARCH_TIMEOUT)
    if ("error" in data or not data) and explicit_domain and domain_is_hint:
        # The auto-resolved hint missed. Only user-set flags are
        # verbatim-final; a hint falls through to the search resolution.
        # Skip the retry chain when the first lookup already consumed a full
        # single-call budget (hung CLI) -- one stream must not chain three
        # sequential SEARCH_TIMEOUT-bound subprocesses.
        if time.monotonic() - started < SEARCH_TIMEOUT:
            resolved = _search_domain(topic)
            if resolved and resolved != identifier:
                _log(f"hint '{identifier}' missed; retrying via search as '{resolved}'")
                data = _run_cli(_build_info_args(resolved), timeout=SEARCH_TIMEOUT)
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
