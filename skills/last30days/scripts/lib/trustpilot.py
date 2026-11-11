"""Trustpilot brand-sentiment source for last30days.

Shells out to ``trustpilot-pp-cli`` to surface a company's TrustScore and
Trustpilot's own AI review summary for brand/company topics. Trustpilot has no
API key, but it sits behind AWS WAF: the CLI replays an ``aws-waf-token`` that
only a Chrome launch can harvest (``trustpilot-pp-cli auth login``) and that
stays replayable for about four minutes.

A research run never launches a browser on the user's machine. The CLI has no
no-browser mode (every fetch command harvests by itself when its session is
missing or stale), so this module asks ``auth status`` first, which is a local
read, and calls a fetch command only while the saved session is fresh.
Otherwise it reads the CLI's cached company record (``info <domain> --local``,
a local read that needs no session) and labels the data with its cache age.
With no usable cached record the lane is skipped quietly: one stderr line with
the sign-in command, and a not-attempted "no results" outcome carrying
``SESSION_NEEDED``, never a failed or degraded source. ``can_run_now`` tells
``--diagnose`` and doctor which of these a run would get, so the host does not
resolve a domain for a lane that would skip.

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

Session pre-flight: ``ensure_session_ready`` runs ``auth status`` before each
fetch command and never ``auth login``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import dates, log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "trustpilot-pp-cli"

SEARCH_TIMEOUT = 75  # generous: a cold run may harvest a WAF cookie (~10s).

AUTH_STATUS_TIMEOUT = 20  # auth status is a local SQLite read; fast.

# `info <domain> --local` and `doctor-tp` read the CLI's local store only.
LOCAL_READ_TIMEOUT = 10

# A cached company record older than the research window is not evidence
# about the last 30 days, so it is not used.
CACHE_MAX_AGE_DAYS = 30

NO_BROWSER_ENV = "LAST30DAYS_TRUSTPILOT_NO_BROWSER"

# The source outcome when the saved session cannot be replayed.
SESSION_NEEDED = (
    "Trustpilot needs a browser session, and a research run never opens a browser: "
    "run `trustpilot-pp-cli auth login`, then run again within 3 minutes"
)
# The CLI replays a session for 4 minutes from its harvest and launches Chrome
# after that. A session this close to the limit could lapse during the call.
CLI_SESSION_SECONDS = 240
SESSION_MARGIN_SECONDS = 45

# Among name-matching search hits, the top hit must have this many times the
# runner-up's review volume to win automatically. Lookalike/squatter pages have
# tiny volume (ThriftBooks: 2.8M vs 130); comparable volume means genuine
# ambiguity, where falling back beats silently picking the wrong company.
DOMAIN_DOMINANCE_FACTOR = 50

# Domain-like token, e.g. "chownow.com", "nothing.tech".
_DOMAIN_RE = re.compile(
    r"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|io|co|net|org|app|ai|dev|gg|tech|shop|store)"
    r"(?:\.[a-z]{2,63})*(?![\w-]|\.[\w-])"
)

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


def _reset_state_for_tests() -> None:
    """Clear module-level caches/flags (tests only)."""
    with _domain_cache_lock:
        _domain_cache.clear()


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


def _search_domain(
    topic: str, session_ready: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """Resolve a company name to its Trustpilot domain via the CLI's search.

    ``session_ready`` is asked right before the CLI runs (a cached answer
    needs no session); when it says no, nothing runs and None is returned.

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
    if session_ready is not None and not session_ready():
        return None
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


def _session_about_to_lapse(status: Dict[str, Any]) -> bool:
    """True when the payload dates the harvest within the margin of the CLI's
    replay limit. A payload with no readable harvest time is not judged."""
    containers = [status, status.get("session")] if isinstance(status, dict) else []
    for container in containers:
        if not isinstance(container, dict) or not container.get("harvestedAt"):
            continue
        try:
            harvested = datetime.fromisoformat(str(container["harvestedAt"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if harvested.tzinfo is None:
            harvested = harvested.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - harvested).total_seconds()
        return age > CLI_SESSION_SECONDS - SESSION_MARGIN_SECONDS
    return False


def ensure_session_ready(
    topic: str,
    config: Optional[Dict[str, Any]] = None,
    has_domain: bool = False,
) -> bool:
    """Whether the CLI's saved WAF session can be replayed right now.

    Checks only: ``auth status`` is a local read. It never runs ``auth
    login``, which launches Chrome, and a False answer means the caller must
    not run a fetch command either, since each of those harvests by itself
    when the session is missing or stale. There is no memo: the session lasts
    four minutes, so every fetch asks again. Logs only structured status
    strings; the raw CLI payload carries live WAF-token prefixes and must
    never be logged. Never raises.
    """
    if not _is_available():
        return False
    if not _harvest_allowed(config):
        return False
    if not has_domain and not is_brand_shaped(topic):
        return False
    with _warmup_lock:
        status = _run_cli(
            [CLI_BIN, "auth", "status", "--agent"], timeout=AUTH_STATUS_TIMEOUT
        )
    if _is_session_fresh(status) and not _session_about_to_lapse(status):
        _log("session: fresh")
        return True
    _log("session: missing or stale; not launching Chrome")
    return False


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
        # A --local miss is the normal "nothing cached" answer, and the CLI's
        # message for it suggests a command that opens a browser.
        if "--local" not in cmd:
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
    opt-out is set, or the CLI fails. With no replayable session nothing is
    fetched and the result carries ``needs_session`` and ``SESSION_NEEDED``
    under ``skipped``: an expected skip, not an error.
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
    # Asked before every fetch command (all gates above have passed, so this
    # never fires for topics the source would not fetch).
    def session_ready() -> bool:
        return ensure_session_ready(topic, config=config, has_domain=bool(explicit_domain))

    def without_session(identifier: Optional[str]) -> Dict[str, Any]:
        """The cached record for a known domain, else the quiet skip."""
        cached = _cached_info(identifier) if identifier else None
        if cached:
            return {"results": [cached]}
        _log(f"skipped: {SESSION_NEEDED}")
        # No "error" key: a skipped lane is not a failed source.
        return {"results": [], "needs_session": True, "skipped": SESSION_NEEDED}

    # Cached records are keyed by domain; a bare name cannot be looked up
    # without the session its name -> domain search needs.
    known_domain = explicit_domain or (
        _company_identifier(topic) if _DOMAIN_RE.search(topic.lower()) else None
    )
    if not session_ready():
        return without_session(known_domain)
    # Retry-budget timer starts AFTER the session check, so it measures the
    # info call itself.
    started = time.monotonic()
    if explicit_domain:
        identifier = explicit_domain
    else:
        identifier = _company_identifier(topic)
        if not _DOMAIN_RE.search(topic.lower()):
            # No domain token in the topic: Trustpilot pages are keyed by
            # domain, so resolve name -> domain before the info lookup.
            resolved_domain = _search_domain(topic, session_ready)
            identifier = resolved_domain or identifier
            if not session_ready():
                return without_session(resolved_domain)
    _log(f"info '{identifier}'")
    data = _run_cli(_build_info_args(identifier), timeout=SEARCH_TIMEOUT)
    if ("error" in data or not data) and explicit_domain and domain_is_hint:
        # The auto-resolved hint missed. Only user-set flags are
        # verbatim-final; a hint falls through to the search resolution.
        # Skip the retry chain when the first lookup already consumed a full
        # single-call budget (hung CLI) -- one stream must not chain three
        # sequential SEARCH_TIMEOUT-bound subprocesses.
        if time.monotonic() - started < SEARCH_TIMEOUT:
            resolved = _search_domain(topic, session_ready)
            if resolved and resolved != identifier and session_ready():
                _log(f"hint '{identifier}' missed; retrying via search as '{resolved}'")
                data = _run_cli(_build_info_args(resolved), timeout=SEARCH_TIMEOUT)
    if "error" in data or not data:
        return {"results": []}
    return {"results": [data]}


def _cache_age(info: Dict[str, Any]) -> Optional[tuple[str, Optional[str]]]:
    """``("cached 3 days ago", "YYYY-MM-DD")`` for a cached record: its age
    label and the UTC date it was fetched. None when it is too old to use. A
    record with no readable fetch time is used, says so, and has no date."""
    raw = info.get("fetchedAt") or info.get("updatedAt") or info.get("updated_at")
    try:
        fetched = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "cached, age unknown", None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    fetched = fetched.astimezone(timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - fetched).days)
    if days > CACHE_MAX_AGE_DAYS:
        return None
    fetched_date = fetched.date().isoformat()
    if days == 0:
        return "cached today", fetched_date
    return f"cached {days} day{'s' if days != 1 else ''} ago", fetched_date


def _cached_info(identifier: str) -> Optional[Dict[str, Any]]:
    """The CLI's cached company record for a domain, labelled with its age.

    ``info <domain> --local`` reads the local store and needs no session, so
    it never opens a browser. None when nothing usable is cached.
    """
    data = _run_cli(
        [CLI_BIN, "info", identifier, "--local", "--agent"], timeout=LOCAL_READ_TIMEOUT
    )
    if not isinstance(data, dict) or not data or "error" in data:
        return None
    age = _cache_age(data)
    if age is None:
        _log(f"cached record for '{identifier}' is over {CACHE_MAX_AGE_DAYS} days old; not used")
        return None
    label, fetched_date = age
    _log(f"no fresh session; using the cached record for '{identifier}' ({label})")
    return {**data, "_cache_label": label, "_cache_date": fetched_date}


def can_run_now(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Whether a run started now could get Trustpilot data without a browser.

    True with a replayable session, or with at least one cached company
    record (``doctor-tp`` counts them; whether it covers a given company is
    known only once its domain is). Local reads only; never raises.
    """
    answer = {"can_run": False, "session_fresh": False, "cached_companies": 0}
    if not _is_available() or not _harvest_allowed(config):
        return answer
    status = _run_cli([CLI_BIN, "auth", "status", "--agent"], timeout=LOCAL_READ_TIMEOUT)
    answer["session_fresh"] = _is_session_fresh(status) and not _session_about_to_lapse(status)
    if not answer["session_fresh"]:
        cache = _run_cli([CLI_BIN, "doctor-tp", "--agent"], timeout=LOCAL_READ_TIMEOUT)
        answer["cached_companies"] = _coerce_int(
            cache.get("companies") if isinstance(cache, dict) else None
        ) or 0
    answer["can_run"] = bool(answer["session_fresh"] or answer["cached_companies"])
    return answer


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
    # A cached record (no fresh session this run) says so wherever it is read.
    cache_label = str(info.get("_cache_label") or "").strip()
    if cache_label:
        title = f"{title} ({cache_label})"
        why = f"{why} ({cache_label})"

    return [
        {
            "id": domain or name or "trustpilot",
            "title": title,
            "url": url,
            "summary": ai_summary,
            "name": name,
            "trustScore": trust_score,
            "reviewCount": review_count,
            # A live TrustScore is a fact about now. A cached record is dated
            # by its fetch, and undated when the fetch time is unreadable.
            "date": info.get("_cache_date") if cache_label else dates.get_date_range(1)[0],
            "engagement": engagement,
            "relevance": round(min(1.0, max(0.4, relevance)), 2),
            "why_relevant": why,
        }
    ]
