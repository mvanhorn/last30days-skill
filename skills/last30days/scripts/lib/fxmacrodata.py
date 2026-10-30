"""FXMacroData source for last30days: official macro releases for a currency.

For a topic about an economy, a currency or a central bank ("Fed rate cut",
"UK inflation", "USD/JPY"), this source returns the official data releases
published in the window (CPI, payrolls, policy-rate decisions, GDP, ...) and
the top-tier releases scheduled for the next two weeks. Each release carries
its value, the previous value, the publisher and the official source link.

API (https://api.fxmacrodata.com/v1):

- ``/announcements/{currency}/latest`` returns the latest and previous value
  of every indicator for a currency, each with ``announcement_datetime``
  (Unix seconds), the time the value was published.
- ``/calendar/{currency}?start_date=&end_date=`` returns scheduled release
  times from official release calendars, with a ``market_tier`` (1 = policy
  rates, CPI, GDP, labour market, retail sales).

USD needs no key. Other currencies need ``FXMACRODATA_API_KEY``, sent in the
``X-API-Key`` header; without it a non-USD currency is skipped and nothing is
requested for it. Keyless USD responses are held back by 15 minutes.

The source is off by default (the #791 policy for third-party APIs):
``INCLUDE_SOURCES=fxmacrodata`` or ``--search fxmacrodata`` turns it on. Once
on, it stays quiet for topics that name no currency, central bank or economy,
and sends no request in that case.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import threading
import urllib.parse
from typing import Any

from . import http, log
from .relevance import token_overlap_relevance

API_BASE = "https://api.fxmacrodata.com/v1"
DOCS_BASE = "https://fxmacrodata.com/api-data-docs"
_UA = "last30days fxmacrodata source"

SUPPORTED_CURRENCIES = frozenset({
    "AUD", "BRL", "CAD", "CHF", "CNH", "CNY", "DKK", "EUR", "GBP", "HUF", "ILS",
    "JPY", "KRW", "MYR", "NGN", "NOK", "NZD", "PEN", "SEK", "THB", "TWD", "USD",
})
KEYLESS_CURRENCIES = frozenset({"USD"})
MAX_CURRENCIES = 3
RELEASE_LIMITS = {"quick": 5, "default": 8, "deep": 15}
UPCOMING_LIMITS = {"quick": 3, "default": 5, "deep": 8}
UPCOMING_DAYS = 14

# Names that identify a currency on their own: central banks, their heads,
# and currency names. Matched as whole words, case-insensitively.
_CURRENCY_TERMS: dict[str, tuple[str, ...]] = {
    "USD": ("federal reserve", "fomc", "the fed", "fed funds", "powell", "us dollar",
            "u.s. dollar", "greenback", "nonfarm payrolls", "non-farm payrolls", "nfp"),
    "EUR": ("ecb", "european central bank", "lagarde", "eurozone", "euro area", "euro"),
    "GBP": ("bank of england", "boe", "sterling", "british pound"),
    "JPY": ("bank of japan", "boj", "japanese yen", "yen"),
    "AUD": ("reserve bank of australia", "rba", "australian dollar", "aussie dollar"),
    "NZD": ("reserve bank of new zealand", "rbnz", "new zealand dollar", "kiwi dollar"),
    "CAD": ("bank of canada", "canadian dollar", "loonie"),
    "CHF": ("swiss national bank", "snb", "swiss franc"),
    "CNH": ("offshore yuan",),
    "CNY": ("people's bank of china", "pboc", "renminbi", "yuan"),
    "SEK": ("riksbank", "swedish krona"),
    "NOK": ("norges bank", "norwegian krone"),
    "DKK": ("danmarks nationalbank", "danish krone"),
    "BRL": ("banco central do brasil", "copom", "brazilian real"),
    "HUF": ("magyar nemzeti bank", "forint"),
    "ILS": ("bank of israel", "shekel"),
    "KRW": ("bank of korea", "korean won"),
    "MYR": ("bank negara", "ringgit"),
    "NGN": ("central bank of nigeria", "naira"),
    "PEN": ("central reserve bank of peru", "peruvian sol"),
    "THB": ("bank of thailand", "baht"),
    "TWD": ("taiwan dollar",),
}

# Country names only count alongside a macro term, so "Japan travel" stays
# quiet while "Japan inflation" resolves to JPY.
_COUNTRY_TERMS: dict[str, tuple[str, ...]] = {
    "USD": ("united states", "u.s.", "american"),
    "EUR": ("europe", "european", "germany", "german", "france", "french",
            "italy", "italian", "spain", "spanish"),
    "GBP": ("united kingdom", "britain", "british", "england"),
    "JPY": ("japan", "japanese"),
    "AUD": ("australia", "australian"),
    "NZD": ("new zealand",),
    "CAD": ("canada", "canadian"),
    "CHF": ("switzerland", "swiss"),
    "CNY": ("china", "chinese"),
    "SEK": ("sweden", "swedish"),
    "NOK": ("norway", "norwegian"),
    "DKK": ("denmark", "danish"),
    "BRL": ("brazil", "brazilian"),
    "HUF": ("hungary", "hungarian"),
    "ILS": ("israel", "israeli"),
    "KRW": ("south korea", "korea", "korean"),
    "MYR": ("malaysia", "malaysian"),
    "NGN": ("nigeria", "nigerian"),
    "PEN": ("peru", "peruvian"),
    "THB": ("thailand", "thai"),
    "TWD": ("taiwan", "taiwanese"),
}
# Abbreviations that are ordinary words in lower case ("us", "fed"), so they
# are matched case-sensitively. "Fed" names USD on its own; the country
# abbreviations need a macro term like the country names above.
_STRONG_ABBREVIATIONS = {"Fed": "USD"}
_COUNTRY_ABBREVIATIONS = {"US": "USD", "USA": "USD", "UK": "GBP"}

# Topic words that point at specific indicators, mapped to indicator slug
# prefixes (a trailing "_" matches a family such as every gov_bond_ tenor).
_INDICATOR_TERMS: dict[str, tuple[str, ...]] = {
    "inflation": ("inflation", "core_inflation", "pce", "core_pce", "ppi"),
    "cpi": ("inflation", "core_inflation"),
    "pce": ("pce", "core_pce"),
    "ppi": ("ppi",),
    "prices": ("inflation", "core_inflation", "ppi"),
    "jobs": ("non_farm_payrolls", "unemployment", "employment", "initial_jobless_claims"),
    "payrolls": ("non_farm_payrolls",),
    "nfp": ("non_farm_payrolls",),
    "employment": ("employment", "unemployment", "non_farm_payrolls"),
    "unemployment": ("unemployment",),
    "jobless": ("initial_jobless_claims", "unemployment"),
    "labor": ("non_farm_payrolls", "unemployment", "employment", "wages", "average_hourly_earnings"),
    "labour": ("unemployment", "employment", "wages"),
    "wages": ("wages", "average_hourly_earnings"),
    "rate": ("policy_rate",),
    "rates": ("policy_rate",),
    "monetary": ("policy_rate",),
    "gdp": ("gdp",),
    "growth": ("gdp",),
    "recession": ("gdp", "unemployment"),
    "retail": ("retail_sales",),
    "consumer": ("retail_sales", "consumer_confidence"),
    "housing": ("housing_starts", "building_permits", "house_price_index"),
    "trade": ("trade_balance", "current_account"),
    "yields": ("gov_bond_",),
    "bonds": ("gov_bond_",),
    "treasury": ("gov_bond_",),
    "treasuries": ("gov_bond_",),
    "gilts": ("gov_bond_",),
}
_MACRO_TERMS = tuple(_INDICATOR_TERMS) + (
    "economy", "economic", "macro", "central bank", "interest rate", "rate cut",
    "rate hike", "rate decision", "deficit", "pmi",
)
# Central banks and their heads: a topic naming one is about the policy rate.
_POLICY_TERMS = (
    "central bank", "federal reserve", "fomc", "the fed", "fed funds", "powell", "ecb",
    "european central bank", "lagarde", "bank of england", "boe", "bank of japan", "boj",
    "reserve bank of australia", "rba", "reserve bank of new zealand", "rbnz",
    "bank of canada", "swiss national bank", "snb", "people's bank of china", "pboc",
    "riksbank", "norges bank", "danmarks nationalbank", "banco central do brasil", "copom",
    "magyar nemzeti bank", "bank of israel", "bank of korea", "bank negara",
    "central bank of nigeria", "central reserve bank of peru", "bank of thailand",
)
# Headline releases, used when the calendar carries no tier for an indicator.
_HEADLINE_INDICATORS = frozenset({
    "policy_rate", "inflation", "core_inflation", "gdp", "unemployment",
    "non_farm_payrolls", "retail_sales", "pce", "core_pce",
})
_PAIR_RE = re.compile(r"\b([A-Za-z]{3})\s*[/\-]?\s*([A-Za-z]{3})\b")
_CODE_RE = re.compile(r"\b[A-Z]{3}\b")


def _log(msg: str) -> None:
    log.source_log("FXMacroData", msg, tty_only=False)


def _term_re(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w.])" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


_CURRENCY_PATTERNS = {ccy: [_term_re(t) for t in terms] for ccy, terms in _CURRENCY_TERMS.items()}
_COUNTRY_PATTERNS = {ccy: [_term_re(t) for t in terms] for ccy, terms in _COUNTRY_TERMS.items()}
_MACRO_PATTERNS = [_term_re(t) for t in _MACRO_TERMS]
_POLICY_PATTERNS = [_term_re(t) for t in _POLICY_TERMS]


def _first_match(text: str, patterns: list[re.Pattern[str]]) -> int | None:
    positions = [m.start() for p in patterns if (m := p.search(text))]
    return min(positions) if positions else None


def _exact_word(text: str, word: str) -> int | None:
    match = re.search(r"(?<![\w.])" + re.escape(word) + r"(?!\w)", text)
    return match.start() if match else None


def resolve_currencies(topic: str) -> list[str]:
    """Currencies a topic is about, in order of appearance, capped at 3.

    Codes ("USD"), pairs ("EUR/USD", "usdjpy"), central banks ("FOMC") and
    currency names ("yen") count on their own. Country names count only next
    to a macro term ("Japan inflation"). Returns ``[]`` for other topics.
    """
    text = topic or ""
    found: dict[str, int] = {}

    def add(ccy: str, pos: int | None) -> None:
        if pos is None or ccy not in SUPPORTED_CURRENCIES:
            return
        if ccy not in found or pos < found[ccy]:
            found[ccy] = pos

    for match in _PAIR_RE.finditer(text):
        base, quote = match.group(1).upper(), match.group(2).upper()
        if base in SUPPORTED_CURRENCIES and quote in SUPPORTED_CURRENCIES and base != quote:
            add(base, match.start(1))
            add(quote, match.start(2))
    for match in _CODE_RE.finditer(text):
        add(match.group(0), match.start())
    for ccy, patterns in _CURRENCY_PATTERNS.items():
        add(ccy, _first_match(text, patterns))
    for abbr, ccy in _STRONG_ABBREVIATIONS.items():
        add(ccy, _exact_word(text, abbr))
    if _first_match(text, _MACRO_PATTERNS) is not None:
        for ccy, patterns in _COUNTRY_PATTERNS.items():
            add(ccy, _first_match(text, patterns))
        for abbr, ccy in _COUNTRY_ABBREVIATIONS.items():
            add(ccy, _exact_word(text, abbr))
    # CNH is the offshore quote of CNY; a topic naming both wants one series.
    if "CNH" in found:
        found.pop("CNY", None)
    # "dollar" on its own means USD unless another currency was named.
    if not found and _term_re("dollar").search(text):
        found["USD"] = 0
    return [ccy for ccy, _ in sorted(found.items(), key=lambda kv: kv[1])][:MAX_CURRENCIES]


def topic_indicator_prefixes(topic: str) -> tuple[str, ...]:
    """Indicator slug prefixes the topic asks about (``()`` when none)."""
    text = topic or ""
    prefixes: list[str] = []
    for term, slugs in _INDICATOR_TERMS.items():
        if _term_re(term).search(text):
            prefixes.extend(s for s in slugs if s not in prefixes)
    # A central-bank topic is about the policy rate even without "rate".
    if "policy_rate" not in prefixes and (
        _first_match(text, _POLICY_PATTERNS) is not None or _exact_word(text, "Fed") is not None
    ):
        prefixes.append("policy_rate")
    return tuple(prefixes)


def _matches(slug: str, prefixes: tuple[str, ...]) -> bool:
    """``inflation`` matches ``inflation`` and ``inflation_mom``, not ``core_inflation``."""
    return any(
        slug.startswith(p) if p.endswith("_") else (slug == p or slug.startswith(p + "_"))
        for p in prefixes
    )


def _utc(epoch: Any) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromtimestamp(int(epoch), tz=datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _utc_date(epoch: Any) -> str | None:
    moment = _utc(epoch)
    return moment.date().isoformat() if moment else None


def _utc_minute(epoch: Any) -> str | None:
    moment = _utc(epoch)
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else None


# The pipeline calls a source once per subquery and these payloads do not
# depend on the subquery, so identical URLs are fetched once per process.
_RESPONSE_CACHE: dict[tuple[str, bytes | None], dict[str, Any]] = {}
_URL_LOCKS: dict[tuple[str, bytes | None], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def clear_cache() -> None:
    with _LOCKS_GUARD:
        _RESPONSE_CACHE.clear()
        _URL_LOCKS.clear()


def _get_json(url: str, api_key: str | None) -> dict[str, Any]:
    token = (api_key or "").strip()
    if any(char.isspace() or not char.isprintable() for char in token):
        raise ValueError("FXMacroData API key contains whitespace or control characters")
    # A paid response must never satisfy a different credential or keyless
    # request. Keep the credential itself out of cache/lock dictionary keys.
    cache_key = (url, hashlib.sha256(token.encode("utf-8")).digest() if token else None)
    with _LOCKS_GUARD:
        lock = _URL_LOCKS.setdefault(cache_key, threading.Lock())
    with lock:
        if cache_key not in _RESPONSE_CACHE:
            headers = {"User-Agent": _UA, "Accept": "application/json"}
            if token:
                headers["X-API-Key"] = token
            try:
                data = http.get(url, headers=headers, timeout=20, retries=2)
            except Exception:
                raise ValueError("FXMacroData request failed") from None
            # Both endpoints wrap their rows in a ``data`` list. Anything else
            # (an error object, a changed schema) must surface as a source
            # error rather than parse as zero rows, and is never cached.
            if not isinstance(data, dict):
                raise ValueError(f"unexpected FXMacroData response shape: {type(data).__name__}")
            if not isinstance(data.get("data"), list):
                raise ValueError(f"unexpected FXMacroData response shape: data is {type(data.get('data')).__name__}")
            if data.get("error") or data.get("detail") or not all(isinstance(row, dict) for row in data["data"]):
                raise ValueError("unexpected FXMacroData response shape")
            _RESPONSE_CACHE[cache_key] = data
        return _RESPONSE_CACHE[cache_key]


def search_fxmacrodata(
    topic: str,
    from_date: str,
    to_date: str,
    *,
    depth: str = "default",
    api_key: str | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Fetch releases and the release calendar for each currency the topic names.

    Returns ``{"currencies", "releases", "upcoming", "skipped", "errors",
    "depth"}``. ``releases`` are ``/latest`` rows whose publication date falls
    in the window. ``upcoming`` are calendar rows scheduled after ``now``,
    collected only when the window reaches today. A currency that needs a key
    is listed in ``skipped`` and not requested.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    now_epoch = int(now.timestamp())
    currencies = resolve_currencies(topic)
    result: dict[str, Any] = {
        "currencies": currencies, "releases": [], "upcoming": [], "skipped": [],
        "errors": [], "depth": depth,
    }
    if not currencies:
        _log("topic names no currency, central bank or economy; skipped")
        return result

    want_upcoming = to_date >= now.date().isoformat()
    calendar_end = (
        (datetime.date.fromisoformat(to_date) + datetime.timedelta(days=UPCOMING_DAYS)).isoformat()
        if want_upcoming else to_date
    )
    attempted = failed = 0
    for ccy in currencies:
        if ccy not in KEYLESS_CURRENCIES and not api_key:
            result["skipped"].append(ccy)
            _log(f"{ccy} needs FXMACRODATA_API_KEY; skipped (USD works without a key)")
            continue
        attempted += 1
        code = ccy.lower()
        try:
            latest = _get_json(f"{API_BASE}/announcements/{code}/latest", api_key)
        except Exception as exc:
            failed += 1
            result["errors"].append(f"{ccy} latest: {exc}")
            _log(f"{ccy} latest failed: {exc}")
            continue
        calendar_rows: list[dict[str, Any]] = []
        params = urllib.parse.urlencode({"start_date": from_date, "end_date": calendar_end})
        try:
            calendar = _get_json(f"{API_BASE}/calendar/{code}?{params}", api_key)
            calendar_rows = [r for r in calendar.get("data") or [] if isinstance(r, dict)]
        except Exception as exc:
            # Releases stand without the calendar; only tiers and the
            # upcoming list are lost.
            result["errors"].append(f"{ccy} calendar: {exc}")
            _log(f"{ccy} calendar failed: {exc}")

        tiers: dict[str, int] = {}
        next_release: dict[str, dict[str, Any]] = {}
        for row in calendar_rows:
            slug = str(row.get("release") or "")
            tier = 1 if row.get("top_tier_for_currency") is True else row.get("market_tier")
            if slug and type(tier) is int and tier >= 1:
                tiers[slug] = min(tier, tiers.get(slug, tier))
            when = row.get("announcement_datetime")
            if want_upcoming and slug and isinstance(when, int) and when > now_epoch:
                if slug not in next_release or when < next_release[slug]["announcement_datetime"]:
                    next_release[slug] = row

        withheld = (latest.get("freemium_delay") or {}).get("withheld_count")
        if withheld:
            _log(f"{ccy}: {withheld} release(s) from the last 15 minutes withheld without a key")

        kept = 0
        for row in latest.get("data") or []:
            current = row.get("latest") if isinstance(row, dict) else None
            if not isinstance(current, dict) or current.get("val") is None:
                continue
            published = _utc_date(current.get("announcement_datetime"))
            if not published or published < from_date or published > to_date:
                continue
            slug = str(row.get("indicator") or "")
            result["releases"].append({
                **row,
                "_currency": ccy,
                "_published_date": published,
                "_tier": tiers.get(slug),
                "_next_release": next_release.get(slug),
            })
            kept += 1
        for slug, row in next_release.items():
            result["upcoming"].append({**row, "_currency": ccy, "_tier": tiers.get(slug)})
        _log(f"{ccy}: {kept} releases in window, {len(next_release)} scheduled")

    if attempted and failed == attempted:
        # Every currency failed: report a source failure, not a quiet empty run.
        result["error"] = "; ".join(result["errors"])
    return result


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _is_headline(slug: str, tier: Any) -> bool:
    return tier == 1 if type(tier) is int else slug in _HEADLINE_INDICATORS


def _schedule_text(row: dict[str, Any]) -> str:
    if row.get("time_announced") is False:
        day = row.get("announcement_date") or _utc_date(row.get("announcement_datetime"))
        return f"{day} (time not announced yet)"
    return str(row.get("announcement_datetime_local") or _utc_minute(row.get("announcement_datetime")) or "")


def event_key(ccy: str, slug: str, kind: str, when: Any) -> str:
    """Identity of one release event, independent of its citation URL.

    Publishers reuse one URL for many events (a calendar page lists every
    scheduled release; a series page serves each new print), so fusion keys
    FXMacroData items on this instead of the URL.
    """
    return f"{ccy}:{slug}:{kind}:{when}"


def _docs_url(ccy: str, slug: str) -> str:
    return f"{DOCS_BASE}/{ccy.lower()}/{slug}"


def _release_item(row: dict[str, Any], score: float) -> dict[str, Any]:
    ccy = row["_currency"]
    slug = str(row.get("indicator") or "")
    name = str(row.get("name") or slug)
    current = row.get("latest") or {}
    previous = row.get("previous") or {}
    unit = str(row.get("unit") or "").strip()
    publisher = str(current.get("source") or row.get("source") or "").strip()
    value = _fmt(current.get("val")) + (f" {unit}" if unit else "")
    parts = [f"{name} ({ccy}) for {current.get('date')}: {value}."]
    if previous.get("val") is not None:
        parts.append(f"Previous: {_fmt(previous.get('val'))} for {previous.get('date')}.")
    # Only a confirmed publication time is stated to the minute.
    if current.get("publication_time_status") == "confirmed":
        released = _utc_minute(current.get("announcement_datetime"))
    else:
        released = row["_published_date"]
    parts.append(f"Released {released}" + (f" by {publisher}." if publisher else "."))
    next_row = row.get("_next_release")
    if next_row:
        parts.append(f"Next release: {_schedule_text(next_row)}.")
    return {
        "id": f"FXMD-{ccy}-{slug}-{current.get('date')}",
        "title": f"{ccy} {name}: {value} ({current.get('date')})",
        "url": str(current.get("source_url") or row.get("source_url") or "") or _docs_url(ccy, slug),
        "author": publisher or None,
        "date": row["_published_date"],
        "text": " ".join(parts),
        "engagement": {},
        "relevance": round(score, 2),
        "why_relevant": f"Official {ccy} release published in the window",
        "metadata": {
            "event_key": event_key(ccy, slug, "release", row["_published_date"]),
            "kind": "release",
            "currency": ccy,
            "indicator": slug,
            "unit": unit or None,
            "frequency": row.get("frequency"),
            "period": current.get("date"),
            "value": current.get("val"),
            "previous_period": previous.get("date"),
            "previous_value": previous.get("val"),
            "announcement_datetime": current.get("announcement_datetime"),
            "publication_time_status": current.get("publication_time_status"),
            "market_tier": row.get("_tier"),
            "next_release": (next_row or {}).get("announcement_datetime_utc"),
            "fxmacrodata_url": _docs_url(ccy, slug),
        },
    }


def _scheduled_item(row: dict[str, Any], score: float) -> dict[str, Any]:
    ccy = row["_currency"]
    slug = str(row.get("release") or "")
    name = str(row.get("name") or slug)
    when = _schedule_text(row)
    period = row.get("reference_period") or row.get("date")
    return {
        "id": str(row.get("calendar_event_id") or f"FXMD-{ccy}-{slug}-scheduled"),
        "title": f"Scheduled: {ccy} {name}, {when}",
        "url": str(row.get("schedule_url") or row.get("source_url") or "") or _docs_url(ccy, slug),
        "author": str(row.get("source") or "").strip() or None,
        # Not published yet, so there is no publication date to report.
        "date": None,
        "text": f"{name} ({ccy}) is scheduled for {when}" + (f", covering {period}." if period else "."),
        "engagement": {},
        "relevance": round(score, 2),
        "why_relevant": f"Scheduled {ccy} release in the next {UPCOMING_DAYS} days",
        "metadata": {
            "event_key": event_key(
                ccy, slug, "scheduled",
                row.get("announcement_datetime_utc") or row.get("announcement_datetime"),
            ),
            "kind": "scheduled",
            "currency": ccy,
            "indicator": slug,
            "scheduled_at": row.get("announcement_datetime_utc"),
            "time_announced": row.get("time_announced"),
            "market_tier": row.get("_tier"),
        },
    }


def parse_fxmacrodata_response(response: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Map releases and scheduled releases to engine item dicts.

    Releases rank by topic match ("inflation" picks CPI and PCE), then
    headline status, then recency. Daily market series (yields, fixing rates)
    are kept only when the topic asks for them. Scheduled releases are kept
    when they are headline or topic-matched, as undated items.
    """
    prefixes = topic_indicator_prefixes(query)
    depth = response.get("depth") or "default"

    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for row in response.get("releases") or []:
        slug = str(row.get("indicator") or "")
        matched = _matches(slug, prefixes)
        if str(row.get("frequency") or "").lower() == "daily" and not matched:
            continue
        name = str(row.get("name") or slug)
        overlap = token_overlap_relevance(query, f"{name} {slug.replace('_', ' ')}") if query else 0.0
        score = 0.45 + (0.3 if matched else 0.0) + (0.15 if _is_headline(slug, row.get("_tier")) else 0.0)
        score = min(1.0, score + 0.1 * overlap)
        ranked.append((score, int((row.get("latest") or {}).get("announcement_datetime") or 0), row))
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    items = [
        _release_item(row, score)
        for score, _, row in ranked[: RELEASE_LIMITS.get(depth, RELEASE_LIMITS["default"])]
    ]

    upcoming: list[tuple[float, int, dict[str, Any]]] = []
    for row in response.get("upcoming") or []:
        slug = str(row.get("release") or "")
        matched = _matches(slug, prefixes)
        if not (matched or _is_headline(slug, row.get("_tier"))):
            continue
        upcoming.append((0.8 if matched else 0.5, -int(row.get("announcement_datetime") or 0), row))
    upcoming.sort(key=lambda t: (t[0], t[1]), reverse=True)
    items.extend(
        _scheduled_item(row, score)
        for score, _, row in upcoming[: UPCOMING_LIMITS.get(depth, UPCOMING_LIMITS["default"])]
    )
    return items
