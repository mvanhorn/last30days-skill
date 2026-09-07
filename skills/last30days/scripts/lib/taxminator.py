"""Taxminator prediction-market search via the public markets API (keyless).

Taxminator (https://taxminator.uz) is an Uzbek-language prediction market
covering Central Asian football, CIS chess, esports, the Uzbek economy, and
entertainment. Every market carries uz/ru/en titles and outcome labels, so a
topic can match in any of the three languages.

**Points, not money.** Taxminator predictions are scored in points, not
currency. There is no stake, no payout, and no liquidity. The crowd split it
publishes is *the share of predictors who picked each outcome*, which is a
weaker (and differently-biased) signal than a money-backed market price. Never
present these numbers as odds backed by money.

The API (`/api/v1/markets`) has no server-side search, so the whole open list
plus the resolved tail is fetched once per process and matched client-side with
the same overlap/similarity machinery Polymarket uses — deliberately importing
those helpers rather than re-deriving them, so the two prediction-market sources
cannot drift apart on what counts as on-topic.
"""

import math
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from . import http, log, polymarket

MARKETS_API_URL = "https://taxminator.uz/api/v1/markets"
MARKET_URL_TAGS = "utm_source=last30days&utm_medium=skill"

# The API caps `limit` at 500. Depth controls how many markets we pull, not how
# many queries we run (there is only ever one fetch pair per process).
DEPTH_FETCH_LIMIT = {
    "quick": 200,
    "default": 500,
    "deep": 500,
}

# Max markets to return after matching + ranking.
RESULT_CAP = {
    "quick": 5,
    "default": 15,
    "deep": 25,
}

REQUEST_TIMEOUT = 12
REQUEST_RETRIES = 2

# Same floors as Polymarket: a prediction-market source that cannot find a
# genuinely on-topic market must stay silent rather than pad the brief.
_MIN_RELEVANCE = 0.15
_ITEM_MIN_RELEVANCE = 0.10

_LANGS = ("en", "uz", "ru")

# Per-process fetch cache. The markets list is topic-independent, so a run that
# fans out into several subqueries must not re-download it once per subquery.
#
# Two things bound its staleness. Entries expire after
# ``_FETCH_CACHE_TTL_SECONDS`` (matching the endpoint's own ``s-maxage``), and
# ``clear_cache()`` is called at every top-level run boundary. Without both, a
# long-lived host (MCP server, watchlist daemon) would serve one process's first
# snapshot forever. Values are ``(monotonic stamp, payload)``.
_FETCH_CACHE_TTL_SECONDS = 300
_FETCH_CACHE: Dict[tuple, tuple] = {}


def _log(msg: str):
    log.source_log("TX", msg, tty_only=False)


def clear_cache() -> None:
    """Drop the in-process fetch cache.

    Call at the start of each top-level research run (the pipeline does) so a
    long-lived process does not reuse one run's market snapshot in the next.
    Within a single run the cache stays hot, so a comparison fan-out still
    downloads the list once.
    """
    _FETCH_CACHE.clear()


# ---------------------------------------------------------------------------
# Topic matching (trilingual)
# ---------------------------------------------------------------------------


def _titles(market: Dict[str, Any]) -> List[str]:
    """All non-empty title strings for a market, English first."""
    title = market.get("title") or {}
    if isinstance(title, str):
        return [title]
    out = []
    for lang in _LANGS:
        value = str(title.get(lang) or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _english_title(market: Dict[str, Any]) -> str:
    titles = _titles(market)
    return titles[0] if titles else ""


def _outcome_labels(market: Dict[str, Any]) -> List[str]:
    """Every outcome label in every language, plus the group title."""
    labels: List[str] = []
    for outcome in market.get("outcomes") or []:
        label = outcome.get("label") if isinstance(outcome, dict) else None
        if isinstance(label, str):
            if label and label not in labels:
                labels.append(label)
            continue
        if not isinstance(label, dict):
            continue
        for lang in _LANGS:
            value = str(label.get(lang) or "").strip()
            if value and value not in labels:
                labels.append(value)
    group = market.get("group")
    if isinstance(group, dict):
        for value in _titles(group):
            if value not in labels:
                labels.append(value)
    return labels


def _passes_topic_filter(topic: str, market: Dict[str, Any]) -> bool:
    """Keep a market when ANY of its three titles matches the topic.

    Delegates the per-title decision to Polymarket's filter so the two
    prediction-market sources agree on what "on topic" means; the only
    difference here is that a market carries three titles instead of one.
    """
    if not topic:
        return True
    return any(
        polymarket._passes_topic_filter(topic, title) for title in _titles(market)
    )


def _compute_text_similarity(topic: str, market: Dict[str, Any]) -> float:
    """Best similarity score across the market's three titles.

    Outcome labels (all languages) are supplied as the secondary signal, the
    same role Polymarket gives its outcome names.
    """
    titles = _titles(market)
    if not titles:
        return 0.0
    outcomes = _outcome_labels(market)
    return max(
        polymarket._compute_text_similarity(topic, title, outcomes)
        for title in titles
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch(status: str, *, since: Optional[str], limit: int) -> Dict[str, Any]:
    """One GET against the markets API. Never raises."""
    params: Dict[str, Any] = {"status": status, "limit": str(limit)}
    if since:
        params["since"] = since
    try:
        response = http.request(
            "GET", MARKETS_API_URL, params=params,
            timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES,
        )
    except http.HTTPError as exc:
        _log(f"Fetch failed (status={status}): {exc}")
        return {"markets": [], "error": str(exc)}
    except Exception as exc:  # network, JSON, anything: zero items, one line
        _log(f"Fetch failed (status={status}): {exc}")
        return {"markets": [], "error": str(exc)}
    if not isinstance(response, dict):
        _log(f"Unexpected payload shape for status={status}: {type(response).__name__}")
        return {"markets": [], "error": "unexpected payload shape"}
    markets = response.get("markets")
    if not isinstance(markets, list):
        return {"markets": [], "error": "payload carried no markets list"}
    return {"markets": [m for m in markets if isinstance(m, dict)]}


def _fetch_all(from_date: str, limit: int) -> Dict[str, Any]:
    """Open markets + the resolved tail since ``from_date``, merged and cached.

    The API caps ``limit`` at 500 and orders resolved markets newest-first, and
    a 30-day resolved window already exceeds that cap. So the resolved half is
    the *freshest* 500 resolutions in the window, not necessarily all of them —
    which is the right truncation for a last-30-days brief, but it means the
    window must never be described as complete.
    """
    cache_key = (from_date, limit)
    cached = _FETCH_CACHE.get(cache_key)
    if cached is not None:
        stored_at, payload = cached
        if time.monotonic() - stored_at < _FETCH_CACHE_TTL_SECONDS:
            return payload
        del _FETCH_CACHE[cache_key]

    open_result = _fetch("open", since=None, limit=limit)
    resolved_result = _fetch("resolved", since=from_date or None, limit=limit)

    merged: Dict[str, Dict[str, Any]] = {}
    for result in (open_result, resolved_result):
        for market in result["markets"]:
            market_id = str(market.get("id") or market.get("slug") or "").strip()
            if not market_id or market_id in merged:
                continue
            merged[market_id] = market

    errors = [r["error"] for r in (open_result, resolved_result) if r.get("error")]
    payload: Dict[str, Any] = {"markets": list(merged.values())}
    if errors and not payload["markets"]:
        payload["error"] = "; ".join(errors[:2])
    _FETCH_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


# ---------------------------------------------------------------------------
# Date window
# ---------------------------------------------------------------------------


def _day(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    return text[:10]


def _reference_day(market: Dict[str, Any]) -> Optional[str]:
    """The day this market last said something: resolution, else last update."""
    return _day(market.get("resolvedAt")) or _day(market.get("updatedAt"))


def _within_window(market: Dict[str, Any], from_date: str, to_date: str) -> bool:
    """Keep markets whose evidence lands inside the research window.

    An OPEN market is a live forecast — current evidence no matter when it was
    created — so only a reference day in the *future* of the window excludes it
    (the same reasoning the jobs source uses for still-open roles). A resolved
    or closed market is a dated event and must fall inside the window.
    """
    day = _reference_day(market)
    if day is None:
        return True
    if to_date and day > to_date:
        return False
    status = str(market.get("status") or "").upper()
    if status == "OPEN":
        return True
    return not (from_date and day < from_date)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_taxminator(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Fetch the Taxminator market list and window it to the research dates.

    There is no server-side search, so this returns the whole (windowed) list
    and ``parse_taxminator_response`` does the topic matching.

    Returns a dict with a ``markets`` list, a ``_cap``, and an optional
    ``error``. Any network failure yields zero markets and one log line.
    """
    limit = DEPTH_FETCH_LIMIT.get(depth, DEPTH_FETCH_LIMIT["default"])
    cap = RESULT_CAP.get(depth, RESULT_CAP["default"])

    payload = _fetch_all(from_date, limit)
    markets = payload["markets"]
    windowed = [m for m in markets if _within_window(m, from_date, to_date)]
    dropped = len(markets) - len(windowed)
    if dropped:
        _log(f"Date window dropped {dropped} of {len(markets)} markets")
    _log(f"Fetched {len(windowed)} Taxminator markets in window for '{topic}'")

    result: Dict[str, Any] = {"markets": windowed, "_cap": cap}
    if payload.get("error"):
        result["error"] = payload["error"]
    return result


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def market_url(market: Dict[str, Any]) -> str:
    """The market's public URL with the skill's own attribution tags."""
    url = str(market.get("url") or "").strip()
    if not url:
        slug = str(market.get("slug") or "").strip()
        if not slug:
            return ""
        url = f"https://taxminator.uz/markets/{quote(slug)}"
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{MARKET_URL_TAGS}"


def _outcome_shares(market: Dict[str, Any]) -> List[tuple]:
    """(english label, share 0..1) pairs, best-supported first.

    Empty when the crowd split is withheld (below the reveal floor), which is
    a real state of the product, not an error.

    A percentage that is not a finite number in [0, 100] is dropped rather than
    rendered: the alternative is a brief that shows "150%", "-5%" or "nan%" as
    crowd evidence. A dropped outcome is simply absent, i.e. withheld for that
    outcome, and the market keeps whatever shares did validate.
    """
    if not market.get("crowdRevealed"):
        return []
    pairs = []
    invalid = 0
    for outcome in market.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        percent = outcome.get("votePercent")
        if percent is None:
            continue
        try:
            numeric_percent = float(percent)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not math.isfinite(numeric_percent) or not 0.0 <= numeric_percent <= 100.0:
            invalid += 1
            continue
        share = numeric_percent / 100.0
        label = outcome.get("label") or {}
        if isinstance(label, dict):
            name = str(
                label.get("en") or label.get("uz") or label.get("ru") or ""
            ).strip()
        else:
            name = str(label).strip()
        pairs.append((name or "Outcome", share))
    if invalid:
        market_id = str(market.get("id") or market.get("slug") or "?").strip() or "?"
        _log(
            f"Dropped {invalid} invalid vote percentage(s) on market {market_id} "
            f"(not a finite number in 0..100)"
        )
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    return pairs


def _predictors(market: Dict[str, Any]) -> int:
    try:
        return int(market.get("totalPredictions") or 0)
    except (TypeError, ValueError):
        return 0


def _crowd_note(market: Dict[str, Any]) -> str:
    """The one-line crowd descriptor shown as the item's snippet."""
    count = _predictors(market)
    noun = "predictor" if count == 1 else "predictors"
    if not market.get("crowdRevealed"):
        return f"{count} {noun}, split withheld"
    return f"{count} {noun}"


def parse_taxminator_response(
    response: Dict[str, Any],
    topic: str = "",
    *,
    include_all_outcomes: bool = False,
) -> List[Dict[str, Any]]:
    """Turn a fetched market list into normalized item dicts.

    Mirrors ``polymarket.parse_polymarket_response``: topic filter, relevance
    score, per-item and best-item relevance floors, then a depth cap.
    """
    markets = response.get("markets", [])
    items: List[Dict[str, Any]] = []
    filtered_count = 0

    for index, market in enumerate(markets):
        titles = _titles(market)
        if not titles:
            continue
        if topic and not _passes_topic_filter(topic, market):
            filtered_count += 1
            continue

        title = _english_title(market)
        shares = _outcome_shares(market)
        predictors = _predictors(market)

        text_score = _compute_text_similarity(topic, market) if topic else 0.5
        # Crowd size is the only quality signal this source has — there is no
        # money, so no volume or liquidity to lean on. ~150 predictors saturates.
        crowd_score = min(1.0, math.log1p(predictors) / 5)
        relevance = min(1.0, text_score * (0.75 + 0.25 * crowd_score))

        top_outcomes = shares if include_all_outcomes else shares[:3]
        remaining = max(0, len(shares) - 3)

        items.append({
            "market_id": str(market.get("id") or market.get("slug") or f"TX{index + 1}"),
            "slug": str(market.get("slug") or ""),
            "title": title,
            "titles": {
                lang: str((market.get("title") or {}).get(lang) or "")
                for lang in _LANGS
            } if isinstance(market.get("title"), dict) else {},
            "question": title,
            "url": market_url(market),
            "category": str(market.get("category") or ""),
            "status": str(market.get("status") or ""),
            "outcome_prices": top_outcomes,
            "outcomes_remaining": remaining,
            "crowd_revealed": bool(market.get("crowdRevealed")),
            "predictors": predictors,
            "volume": predictors,
            "crowd_note": _crowd_note(market),
            "points_for_correct": market.get("pointsForCorrect"),
            "date": _reference_day(market),
            "end_date": _day(market.get("closesAt")),
            "relevance": round(relevance, 2),
            "why_relevant": f"Taxminator prediction market: {title[:60]}",
        })

    if filtered_count:
        _log(f"Filtered {filtered_count} off-topic markets (topic: '{topic}')")

    items.sort(key=lambda item: item["relevance"], reverse=True)

    if items and items[0]["relevance"] < _MIN_RELEVANCE:
        _log(
            f"All {len(items)} Taxminator results below relevance threshold "
            f"({items[0]['relevance']:.2f} < {_MIN_RELEVANCE}), dropping all"
        )
        return []

    before_count = len(items)
    items = [item for item in items if item["relevance"] >= _ITEM_MIN_RELEVANCE]
    dropped = before_count - len(items)
    if dropped:
        _log(
            f"Dropped {dropped} Taxminator items below per-item relevance floor "
            f"({_ITEM_MIN_RELEVANCE})"
        )

    cap = response.get("_cap", len(items))
    return items[:cap]


def filter_items_against_topic(topic: str, items: List[Any]) -> List[Any]:
    """Post-merge re-validation against the full original topic.

    Comparison topics fan out into per-entity subqueries whose narrow topic
    lets tangential markets through; this re-checks survivors against the whole
    topic using the looser any-entity rule (shared with Polymarket).

    Unlike Polymarket's version this must check ALL THREE titles, not just the
    item's title: a normalized item always carries the English title, so a
    Russian or Uzbek topic that legitimately matched at retrieval time shares
    no word with it and would be dropped here.
    """
    if not topic:
        return items

    filtered = []
    for item in items:
        metadata = getattr(item, "metadata", None)
        if metadata is None and isinstance(item, dict):
            metadata = item.get("metadata")
        title = getattr(item, "title", None)
        if title is None and isinstance(item, dict):
            title = item.get("title", "")
        candidates = [title or ""]
        for value in ((metadata or {}).get("titles") or {}).values():
            if value and value not in candidates:
                candidates.append(value)
        if any(
            polymarket._passes_any_informative_word(topic, candidate)
            for candidate in candidates
        ):
            filtered.append(item)

    dropped = len(items) - len(filtered)
    if dropped:
        _log(
            f"Post-merge topic filter dropped {dropped} Taxminator items "
            f"against full topic '{topic}'"
        )
    return filtered


# ---------------------------------------------------------------------------
# Freshness re-fetch
# ---------------------------------------------------------------------------


def refetch_datum(item: Any, datum_key: str) -> dict[str, Any]:
    """Re-fetch one market datum through the replay-aware HTTP wrapper.

    ``datum_key`` is either ``end_date`` or an outcome label. Identity is
    verified against the market id (or, failing that, the slug from the item
    URL) so a verdict can never be re-derived from a different market.
    """
    metadata = getattr(item, "metadata", {}) or {}
    market_id = str(metadata.get("market_id") or "").strip()
    url = str(getattr(item, "url", "") or "")
    slug_match = re.search(r"/markets/([^/?#]+)", url)
    slug = slug_match.group(1) if slug_match else str(metadata.get("slug") or "").strip()

    if not market_id and not slug:
        raise ValueError("Taxminator item has no market id or slug")

    payload = http.request(
        "GET", MARKETS_API_URL, params={"status": "all", "limit": "500"},
        timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES,
    )
    markets = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, list):
        raise KeyError("Taxminator markets payload was malformed")

    match = None
    for entry in markets:
        if not isinstance(entry, dict):
            continue
        if market_id:
            if str(entry.get("id") or "").strip() == market_id:
                match = entry
                break
            continue
        if slug and str(entry.get("slug") or "").strip() == slug:
            match = entry
            break
    if match is None:
        raise KeyError("Taxminator market was not found")

    parsed = parse_taxminator_response({"markets": [match]}, include_all_outcomes=True)
    if not parsed:
        raise KeyError("Taxminator market is unavailable or malformed")
    refreshed = parsed[0]

    values: dict[str, Any] = {
        str(name): share for name, share in refreshed.get("outcome_prices") or []
    }
    if refreshed.get("end_date") is not None:
        values["end_date"] = refreshed["end_date"]

    if datum_key == "end_date":
        value = values.get("end_date")
    else:
        value = next(
            (
                share
                for name, share in refreshed.get("outcome_prices") or []
                if str(name).casefold() == datum_key.casefold()
            ),
            None,
        )
    if value is None:
        raise KeyError(f"Taxminator datum {datum_key!r} was not found")
    return {
        "value": value,
        "values": values,
        "url": url,
        "timestamp": match.get("resolvedAt") or match.get("updatedAt"),
    }
