"""Optional Adanos Market Sentiment source.

Adanos provides structured stock sentiment snapshots across Reddit, X, News,
and Polymarket. The source is intentionally opt-in and only runs for
finance-like topics so general last30days research does not burn API quota.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlencode

from . import http, log

DEFAULT_BASE_URL = "https://api.adanos.org"
PLATFORMS = {
    "news": "/news/stocks/v1",
    "reddit": "/reddit/stocks/v1",
    "x": "/x/stocks/v1",
    "polymarket": "/polymarket/stocks/v1",
}
RESULT_CAP = {
    "quick": 4,
    "default": 8,
    "deep": 12,
}

_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])\$([A-Za-z]{1,10})(?![A-Za-z0-9_])")
_UPPER_TICKER_RE = re.compile(r"\b[A-Z]{2,6}\b")
_FINANCE_TERMS = frozenset(
    {
        "stock",
        "stocks",
        "ticker",
        "tickers",
        "equity",
        "equities",
        "shares",
        "earnings",
        "portfolio",
        "watchlist",
        "invest",
        "investing",
        "investor",
        "investors",
        "trading",
        "trader",
        "traders",
        "options",
        "nasdaq",
        "nyse",
        "etf",
        "market sentiment",
        "retail sentiment",
        "wall street",
    }
)
_TRENDING_TERMS = frozenset(
    {
        "trending",
        "buzzing",
        "moving",
        "hot",
        "watchlist",
        "market sentiment",
        "retail sentiment",
    }
)
_UPPERCASE_FALSE_POSITIVES = frozenset(
    {
        "AI",
        "API",
        "CEO",
        "CFO",
        "CTO",
        "ETF",
        "GDP",
        "IPO",
        "LLM",
        "MCP",
        "USA",
        "USD",
    }
)


def _log(msg: str) -> None:
    log.source_log("Adanos", msg)


def search(
    query: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    depth: str = "default",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch Adanos stock sentiment evidence for a finance-like query."""
    api_key = config.get("ADANOS_API_KEY")
    if not api_key:
        return [], {}
    if not looks_financial(query):
        return [], {"label": "adanos", "skipped": "non_finance_query"}

    platforms = _configured_platforms(config)
    if not platforms:
        return [], {"label": "adanos", "skipped": "no_platforms"}

    from_date, to_date = date_range
    days = _days_between(from_date, to_date)
    limit = RESULT_CAP.get(depth, RESULT_CAP["default"])
    tickers = extract_tickers(query)
    base_url = (config.get("ADANOS_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for platform in platforms:
        try:
            if tickers:
                payload = _get(
                    base_url,
                    PLATFORMS[platform],
                    "compare",
                    {"tickers": ",".join(tickers), "days": days},
                    api_key,
                )
                items.extend(_items_from_compare(payload, platform, query, to_date))
            else:
                payload = _get(
                    base_url,
                    PLATFORMS[platform],
                    "search",
                    {"q": query, "days": days, "limit": limit},
                    api_key,
                )
                parsed = _items_from_search(payload, platform, query, to_date)
                if not parsed and looks_trending_query(query):
                    payload = _get(
                        base_url,
                        PLATFORMS[platform],
                        "trending",
                        {"days": days, "limit": limit},
                        api_key,
                    )
                    parsed = _items_from_trending(payload, platform, query, to_date)
                items.extend(parsed)
        except http.HTTPError as exc:
            errors[platform] = f"HTTP {exc.status_code}" if exc.status_code else str(exc)
            _log(f"{platform} request failed: {errors[platform]}")
        except Exception as exc:
            errors[platform] = f"{type(exc).__name__}: {exc}"
            _log(f"{platform} request failed: {errors[platform]}")

    artifact = {
        "label": "adanos",
        "platforms": platforms,
        "tickers": tickers,
        "resultCount": len(items),
    }
    if errors:
        artifact["errors"] = errors
    return items[: max(limit * max(1, len(platforms)), limit)], artifact


def looks_financial(query: str) -> bool:
    text = query.strip()
    if _CASHTAG_RE.search(text):
        return True
    if _plain_ticker_query(text):
        return True
    lowered = text.lower()
    if any(term in lowered for term in _FINANCE_TERMS):
        return True
    return bool(extract_tickers(text))


def looks_trending_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in _TRENDING_TERMS)


def extract_tickers(query: str) -> list[str]:
    tickers: list[str] = []
    for match in _CASHTAG_RE.finditer(query):
        _append_ticker(tickers, match.group(1))

    if not tickers and not any(term in query.lower() for term in _FINANCE_TERMS) and not _plain_ticker_query(query):
        return tickers

    for match in _UPPER_TICKER_RE.finditer(query):
        candidate = match.group(0)
        if candidate in _UPPERCASE_FALSE_POSITIVES:
            continue
        _append_ticker(tickers, candidate)
    return tickers


def _plain_ticker_query(query: str) -> bool:
    text = re.sub(r"\b(?:vs\.?|versus)\b|[,/]", " ", query.strip(), flags=re.IGNORECASE)
    tokens = [token for token in text.split() if token]
    if not tokens or len(tokens) > 10:
        return False
    return all(
        _UPPER_TICKER_RE.fullmatch(token) and token not in _UPPERCASE_FALSE_POSITIVES
        for token in tokens
    )


def _append_ticker(tickers: list[str], value: str) -> None:
    ticker = value.strip().upper().lstrip("$")
    if ticker and ticker not in tickers:
        tickers.append(ticker)


def _configured_platforms(config: dict[str, Any]) -> list[str]:
    raw = config.get("ADANOS_PLATFORMS") or ",".join(PLATFORMS)
    selected: list[str] = []
    for item in raw.split(","):
        platform = item.strip().lower()
        if platform in PLATFORMS and platform not in selected:
            selected.append(platform)
    return selected


def _days_between(from_date: str, to_date: str) -> int:
    try:
        start = date.fromisoformat(from_date[:10])
        end = date.fromisoformat(to_date[:10])
    except ValueError:
        return 30
    return max(1, min(365, (end - start).days + 1))


def _get(
    base_url: str,
    base_path: str,
    endpoint: str,
    params: dict[str, Any],
    api_key: str,
) -> dict[str, Any] | list[Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{base_url}{base_path}/{endpoint}?{query}"
    return http.get(
        url,
        headers={"X-API-Key": api_key, "Accept": "application/json"},
        timeout=15,
        retries=2,
        max_429_retries=1,
    )


def _items_from_compare(
    payload: dict[str, Any] | list[Any],
    platform: str,
    query: str,
    to_date: str,
) -> list[dict[str, Any]]:
    rows = payload.get("stocks") if isinstance(payload, dict) else payload
    return [
        _stock_item(row, platform, query, to_date, origin="compare")
        for row in _dict_rows(rows)
    ]


def _items_from_search(
    payload: dict[str, Any] | list[Any],
    platform: str,
    query: str,
    to_date: str,
) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, dict) else payload
    items: list[dict[str, Any]] = []
    for row in _dict_rows(rows):
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        merged = {**summary, **row}
        merged.pop("summary", None)
        items.append(_stock_item(merged, platform, query, to_date, origin="search"))
    return items


def _items_from_trending(
    payload: dict[str, Any] | list[Any],
    platform: str,
    query: str,
    to_date: str,
) -> list[dict[str, Any]]:
    rows = payload.get("stocks") if isinstance(payload, dict) else payload
    return [
        _stock_item(row, platform, query, to_date, origin="trending")
        for row in _dict_rows(rows)
    ]


def _dict_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _stock_item(
    row: dict[str, Any],
    platform: str,
    query: str,
    to_date: str,
    origin: str,
) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").upper()
    company = str(row.get("company_name") or row.get("name") or ticker).strip()
    platform_label = _platform_label(platform)
    buzz = row.get("buzz_score")
    sentiment = row.get("sentiment_score")
    trend = str(row.get("trend") or "").strip()
    activity = _activity_count(row, platform)
    title_parts = [ticker or company, platform_label, "market sentiment"]
    if buzz is not None:
        title_parts.append(f"buzz {buzz}")
    if sentiment is not None:
        title_parts.append(f"sentiment {sentiment}")

    body_parts = [
        f"{company or ticker} on {platform_label}",
        f"buzz_score={buzz}" if buzz is not None else "",
        f"sentiment_score={sentiment}" if sentiment is not None else "",
        f"trend={trend}" if trend else "",
        f"bullish_pct={row.get('bullish_pct')}" if row.get("bullish_pct") is not None else "",
        f"bearish_pct={row.get('bearish_pct')}" if row.get("bearish_pct") is not None else "",
        f"activity={activity}" if activity is not None else "",
    ]
    return {
        "id": f"ADANOS-{platform}-{ticker or origin}",
        "ticker": ticker,
        "company_name": company,
        "platform": platform,
        "title": " | ".join(part for part in title_parts if part),
        "text": "; ".join(part for part in body_parts if part),
        "url": "",
        "date": to_date,
        "date_confidence": "med",
        "engagement": _engagement(row, platform),
        "relevance": _relevance(row),
        "why_relevant": f"Structured Adanos {platform_label} stock sentiment for '{query}'",
        "metadata": {
            "origin": origin,
            "platform": platform,
            "ticker": ticker,
            "company_name": company,
            "trend": trend,
            "bullish_pct": row.get("bullish_pct"),
            "bearish_pct": row.get("bearish_pct"),
            "trend_history": row.get("trend_history") or [],
        },
    }


def _platform_label(platform: str) -> str:
    return {
        "news": "News",
        "reddit": "Reddit",
        "x": "X",
        "polymarket": "Polymarket",
    }.get(platform, platform.title())


def _activity_count(row: dict[str, Any], platform: str) -> Any:
    if platform == "polymarket":
        return row.get("trade_count")
    return row.get("mentions")


def _engagement(row: dict[str, Any], platform: str) -> dict[str, float | int]:
    engagement: dict[str, float | int] = {}
    for key in ("buzz_score", "mentions", "total_upvotes", "trade_count", "total_liquidity"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            engagement[key] = value
    if platform == "news" and isinstance(row.get("source_count"), (int, float)):
        engagement["source_count"] = row["source_count"]
    return engagement


def _relevance(row: dict[str, Any]) -> float:
    buzz = row.get("buzz_score")
    if isinstance(buzz, (int, float)):
        return max(0.55, min(0.95, 0.55 + (float(buzz) / 250.0)))
    return 0.7
