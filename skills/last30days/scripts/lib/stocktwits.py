"""StockTwits source for last30days — ticker/crypto topics only.

StockTwits is a cashtag-native social network for traders. Every message can
carry a self-reported Bullish/Bearish tag, which makes it uniquely good at one
thing the other sources can't quantify: a sentiment *ratio* and retail *volume*
on a specific symbol.

GATING: this source is only meaningful for financial topics. `detect_symbols()`
and `is_financial_topic()` are the gate — the pipeline must NOT register
stocktwits for a non-ticker topic (see INTEGRATION.md, step 3). Treat the output
as a direction/volume signal, never as analysis: StockTwits skews retail and
promotional, sentiment tags are self-reported, and bot/pump noise is common.

API: public, no auth. Symbol stream + symbol search endpoints. Unauthenticated
quota is ~200 requests/hour and is rate-limited per IP — keep pagination small.
Respect StockTwits' API terms if this is ever shipped beyond personal use.

NOTE: uses raw urllib rather than the shared `from . import http` helper.
Every call is wrapped in try/except and degrades to a partial/empty result, but
switching to the shared helper (429 Retry-After, retry budget, backoff) is a
known follow-up to match siblings like hackernews.py.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

_UA = "Mozilla/5.0 (last30days stocktwits source)"
_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_SEARCH_URL = "https://api.stocktwits.com/api/2/search/symbols.json"

# Topic must look financial before we even try to resolve a symbol. This is the
# coarse gate; symbol resolution is the fine gate.
_FINANCE_HINTS = re.compile(
    # Unambiguous finance vocabulary only. Bare "share/token/coin/bull/bear"
    # were removed: they misfire on general topics ("share files", "token
    # limits", "coin collecting", "bear attacks") and would inject stock chatter
    # into non-financial runs.
    r"\b(stock|stocks|ticker|cashtag|equit(?:y|ies)|price target|"
    r"earnings|premarket|pre-?market|after\s?hours|dividend|valuation|"
    r"crypto|altcoin|defi|market cap|bullish|bearish|"
    # Unambiguous crypto names so "bitcoin price" gates without a cashtag.
    # Short aliases (eth, sol, ada, doge, ripple) stay OUT of the gate: they
    # collide with everyday topics (ETH Zurich, ADA compliance, doge memes);
    # they still resolve via _CRYPTO_ALIASES once the gate fires another way.
    r"bitcoin|btc|ethereum|solana|dogecoin|cardano|xrp|"
    r"\$[A-Za-z]{1,5}(?:\.[A-Z])?)\b",
    re.IGNORECASE,
)
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Z])?)\b")

# A small built-in crypto map so we don't burn a symbol-search call on the
# obvious ones. StockTwits uses the `.X` suffix for crypto symbols.
_CRYPTO_ALIASES = {
    "bitcoin": "BTC.X", "btc": "BTC.X",
    "ethereum": "ETH.X", "eth": "ETH.X",
    "solana": "SOL.X", "sol": "SOL.X",
    "dogecoin": "DOGE.X", "doge": "DOGE.X",
    "ripple": "XRP.X", "xrp": "XRP.X",
    "cardano": "ADA.X", "ada": "ADA.X",
}


def _log(msg: str) -> None:
    try:
        from . import log as _enginelog
        _enginelog.source_log("StockTwits", msg, tty_only=False)
    except Exception:  # standalone / outside package
        print(f"[StockTwits] {msg}", file=sys.stderr)


def _get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# --------------------------------------------------------------------------- #
# Gating + symbol resolution                                                   #
# --------------------------------------------------------------------------- #

def is_financial_topic(topic: str) -> bool:
    """Coarse gate: does the topic look like it's about a tradeable asset?"""
    return bool(_CASHTAG.search(topic) or _FINANCE_HINTS.search(topic))


def detect_symbols(topic: str, *, resolve: bool = True, max_symbols: int = 2) -> list[str]:
    """Resolve a topic to StockTwits symbols. Returns [] for non-financial topics.

    Order of resolution:
      1. Explicit cashtags in the topic ($NOW, $BTC.X) — trusted as-is.
      2. Crypto name aliases (bitcoin -> BTC.X).
      3. StockTwits symbol-search API for a company/product name, but ONLY if the
         topic also tripped the finance gate (so "Apple pie" never resolves AAPL).

    `resolve=False` skips the network call (useful for the cheap gate check).
    """
    found: list[str] = []

    for m in _CASHTAG.finditer(topic):
        sym = m.group(1).upper()
        if sym not in found:
            found.append(sym)

    lowered = topic.lower()
    for alias, sym in _CRYPTO_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered) and sym not in found:
            found.append(sym)

    if found:
        return found[:max_symbols]

    # No explicit symbol. Only hit the network if the topic looks financial.
    if not resolve or not is_financial_topic(topic):
        return []

    # Strip finance noise words so the search query is just the entity name.
    name = re.sub(
        r"\b(stock|stocks|shares?|price|ticker|earnings|forecast|crypto|"
        r"token|coin|news|today|now)\b",
        "", topic, flags=re.IGNORECASE,
    ).strip()
    if not name:
        return []
    try:
        url = _SEARCH_URL + "?" + urllib.parse.urlencode({"q": name})
        data = _get_json(url)
        for result in data.get("results", []):
            sym = (result.get("symbol") or "").upper()
            if sym and sym not in found:
                found.append(sym)
            if len(found) >= max_symbols:
                break
        if found:
            _log(f"Resolved '{name}' -> {found}")
    except Exception as e:  # noqa: BLE001 — network/parse, degrade gracefully
        _log(f"symbol search failed for '{name}': {e}")
    return found[:max_symbols]


# --------------------------------------------------------------------------- #
# Fetch + parse                                                                #
# --------------------------------------------------------------------------- #

_DEPTH = {"quick": 30, "default": 60, "deep": 120}


def search_stocktwits(
    topic_or_symbol: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    depth: str = "default",
) -> dict[str, Any]:
    """Fetch the symbol stream for the topic. Paginates by cursor up to `depth`.

    Accepts either a raw topic (resolved via detect_symbols) or an explicit
    symbol. Returns {"messages": [...], "symbols": [...], "watchlist": int}.
    """
    symbols = (
        [topic_or_symbol.lstrip("$").upper()]
        if _CASHTAG.fullmatch("$" + topic_or_symbol.lstrip("$"))
        else detect_symbols(topic_or_symbol)
    )
    if not symbols:
        return {"messages": [], "symbols": [], "error": "no symbol resolved"}

    symbol = symbols[0]  # primary symbol drives the stream
    target = _DEPTH.get(depth, 60)
    messages: list[dict[str, Any]] = []
    watchlist = None
    cursor_max = None
    try:
        while len(messages) < target:
            url = _STREAM_URL.format(symbol=urllib.parse.quote(symbol))
            if cursor_max:
                url += f"?max={cursor_max}"
            data = _get_json(url)
            if watchlist is None:
                watchlist = (data.get("symbol") or {}).get("watchlist_count")
            batch = data.get("messages", [])
            if not batch:
                break
            messages.extend(batch)
            cursor = data.get("cursor", {})
            if not cursor.get("more") or not cursor.get("max"):
                break
            cursor_max = cursor["max"]
            time.sleep(0.8)  # be polite to the unauth quota
    except Exception as e:  # noqa: BLE001
        _log(f"stream fetch failed for {symbol}: {e}")
        return {"messages": messages, "symbols": symbols, "error": str(e)}

    messages = _filter_by_date(messages, from_date, to_date)
    _log(f"{symbol}: {len(messages)} messages (watchlist {watchlist})")
    return {"messages": messages, "symbols": symbols, "watchlist": watchlist}


def _filter_by_date(messages: list[dict], from_date: str | None, to_date: str | None) -> list[dict]:
    if not (from_date or to_date):
        return messages
    out = []
    for m in messages:
        d = (m.get("created_at") or "")[:10]
        if from_date and d and d < from_date:
            continue
        if to_date and d and d > to_date:
            continue
        out.append(m)
    return out


def parse_stocktwits_response(response: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Normalize the stream into engine-style item dicts (same keys as HN/Reddit).

    Each item carries metadata.sentiment in {"Bullish","Bearish",None} and the
    symbol-level bull/bear aggregate so synthesis can cite the ratio.
    """
    messages = response.get("messages", [])
    symbols = response.get("symbols", [])
    agg = aggregate_sentiment(messages)
    items: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        user = m.get("user") or {}
        username = user.get("username") or "unknown"
        body = (m.get("body") or "").strip()
        sentiment = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        likes = (m.get("likes") or {}).get("total", 0) or 0
        reshares = (m.get("reshares") or {}).get("reshared_count", 0) or 0
        followers = user.get("followers", 0) or 0
        # Relevance: cashtag-native source, so on-symbol is a near-given. Nudge
        # by author reach + a tagged-sentiment bonus (tagged posts are higher
        # intent than chatter).
        relevance = min(1.0, 0.7 + (0.1 if sentiment else 0.0) + min(0.2, followers / 50000))
        items.append({
            "id": str(m.get("id") or f"ST{i+1}"),
            "title": body[:120] or f"${symbols[0] if symbols else ''} post",
            "url": f"https://stocktwits.com/{username}/message/{m.get('id')}",
            "author": username,
            "date": (m.get("created_at") or "")[:10] or None,
            "engagement": {"likes": likes, "reshares": reshares, "followers": followers},
            "relevance": round(relevance, 2),
            "why_relevant": f"StockTwits ${symbols[0] if symbols else ''} post"
                            + (f" tagged {sentiment}" if sentiment else ""),
            "snippet": body[:400],
            "metadata": {
                "sentiment": sentiment,
                "symbol": symbols[0] if symbols else None,
                "sentiment_aggregate": agg,   # same dict on every item; cheap, lets synthesis cite it
                "watchlist": response.get("watchlist"),
            },
        })
    return items


def aggregate_sentiment(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Bull/bear counts + ratio over the sentiment-tagged subset."""
    bull = bear = 0
    for m in messages:
        s = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        if s == "Bullish":
            bull += 1
        elif s == "Bearish":
            bear += 1
    tagged = bull + bear
    return {
        "bullish": bull,
        "bearish": bear,
        "untagged": len(messages) - tagged,
        "pct_bullish": round(100 * bull / tagged) if tagged else None,
        "sample": len(messages),
    }


# --------------------------------------------------------------------------- #
# Standalone CLI (ad-hoc use today, before any engine wiring)                  #
#   python3 stocktwits.py "ServiceNow stock"                                   #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "$NOW"
    if not is_financial_topic(topic) and not detect_symbols(topic, resolve=False):
        print(f"Not a ticker/crypto topic — skipping StockTwits: {topic!r}")
        raise SystemExit(0)
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=30)).isoformat()
    resp = search_stocktwits(topic, from_date=since, depth="default")
    if resp.get("error") and not resp.get("messages"):
        print("error:", resp["error"]); raise SystemExit(1)
    items = parse_stocktwits_response(resp, query=topic)
    agg = aggregate_sentiment(resp["messages"])
    print(f"symbol(s): {resp.get('symbols')} | watchlist {resp.get('watchlist')}")
    print(f"sentiment: {agg['bullish']} bull / {agg['bearish']} bear "
          f"({agg['pct_bullish']}% bullish of tagged) over {agg['sample']} msgs")
    for it in sorted(items, key=lambda x: x["engagement"]["likes"], reverse=True)[:8]:
        s = it["metadata"]["sentiment"] or "-"
        print(f"  [{it['engagement']['likes']}♥ {s}] @{it['author']}: {it['snippet'][:120]}")
