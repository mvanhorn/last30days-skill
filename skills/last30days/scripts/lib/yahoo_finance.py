"""Share-price window for a listed company, through ``yahoo-finance-pp-cli``.

Not a search source: it returns no items and the planner never schedules it.
For a topic the company resolver tied to a listed company it fetches the daily
closes of the research window once and stores the numbers as the report's
``yahoo_finance`` artifact, which the footer and the evidence block read.

Activation gate: the ``yahoo-finance-pp-cli`` binary on PATH and a company
with a ticker. Without either, nothing happens and nothing is logged.
``EXCLUDE_SOURCES=yahoo_finance`` switches it off the same way. ``--search``
picks search sources and this is not one, so it does not switch it off.

Which symbol is charted:

- The resolver's certain ``ticker`` on a US exchange is used as it is.
- A certain ``ticker`` on any other exchange has a different Yahoo symbol
  (Tokyo ``7203`` is ``7203.T``), so ``autocomplete`` is asked for the company
  name and the first equity whose symbol is the ticker, or the ticker followed
  by ``.`` and an exchange suffix, is used.
- With only ``ticker_candidates``, the first equity ``autocomplete`` returns
  for the company name is used when its symbol, or the part of it before the
  first ``.``, is one of the candidates. Only that first equity is looked at.

All comparisons ignore case. ``autocomplete`` only ever confirms a symbol the
resolver supplied: on its own it returned a fund for "Stripe" and a Swedish
retailer for "Rust". A symbol that cannot be confirmed gives no line.

The one exception is ``backup_company``, for a topic whose Wikidata lookup
failed (not one Wikidata answered with nothing). ``autocomplete`` is asked for
the topic itself and only its first equity is looked at. It is taken when its
symbol has no exchange suffix and its name is the topic once case, punctuation
and trailing corporate designators ("Tesla, Inc.", "NVIDIA Corporation") are
set aside. That result carries ``company_source`` of ``yahoo-backup`` and its
ticker is charted as it is.

The host's ticker: ``--ticker "SYMBOL:Company"`` carries what the hosting
model found by web search, and then the resolver's ticker is not used at all.
A host claim is never taken alone. Yahoo has to agree twice: ``chart`` for the
symbol returns an equity with two daily closes (the same fetch the line is
built from), and the first equity ``autocomplete`` lists for the company name
is that symbol, or carries the same company name as the chart does ("GOOGL"
when "Alphabet" lists "GOOG" first). A pair that fails gives no line and no
other source is tried in its place: see ``attach_host_tickers``.

Surface choice: ``--json``, never ``--agent``. ``--agent`` implies
``--compact``, which empties records on older Printing Press binaries (see
``lib/techmeme.py``).

Window: bars are requested from a day before ``range_from`` to a day after
``range_to`` and then kept by the exchange's own calendar date, so a market
that opens before midnight UTC keeps its first day. The window's first close
is the first trading day inside it and its last close is the last one. Daily
moves are close to close between consecutive bars inside the window.

A fetch that fails, is rate limited, or returns fewer than two closes records
a degraded ``yahoo_finance`` source outcome and no artifact. So does an
``autocomplete`` call that fails or answers in another shape: that is not the
same as Yahoo listing no matching equity, which records nothing. Nothing
raises.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from concurrent.futures import Future
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import env, http, log, schema, subproc

CLI_BIN = "yahoo-finance-pp-cli"
SOURCE = "yahoo_finance"
TIMEOUT_SECONDS = 20

BENCHMARK_SYMBOL = "^GSPC"
BENCHMARK_NAME = "S&P 500"
TOP_MOVES = 3

# Exchange labels the company resolver returns for US listings, case-folded.
_US_EXCHANGES = frozenset({
    "nasdaq", "new york stock exchange", "nyse american", "nyse arca",
})

# ``company_source`` of a company found by ``backup_company``.
BACKUP_SOURCE = "yahoo-backup"

# ``ticker_source`` of a ticker the host passed with ``--ticker``.
HOST_SOURCE = "host"
MAX_HOST_TICKERS = 2
# The symbol becomes a CLI argument, so it has to look like one: a letter or
# digit first, never a dash.
_HOST_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}\Z")
_HOST_TEXT_LIMIT = 80

# Words dropped from the end of a company name before it is compared with the
# topic. "publ" is what is left of "AB (publ)" and "the" of ", The".
_DESIGNATORS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "ag", "sa", "nv", "ab", "publ", "se", "holdings",
    "holding", "group", "the",
})
_SHARE_CLASSES = frozenset({"a", "b", "c"})

# One fetch per symbol and window for the life of the process: the entities of
# a comparison run on threads and each asks for the same S&P 500 window. The
# lock guards the dict only. The thread that adds a key runs the CLI outside
# the lock and the others wait on that key's future, so different symbols are
# fetched at the same time.
_fetch_lock = threading.Lock()
_fetched: dict[tuple[str, str, str], Future] = {}


class SymbolSearchFailed(Exception):
    """``autocomplete`` gave no usable answer, so the symbol is unknown."""


def _log(msg: str) -> None:
    log.source_log("Yahoo Finance", msg, tty_only=False)


def _is_available() -> bool:
    """True when the yahoo-finance-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def forget_fetches() -> None:
    """Drop the fetched windows, so the next request reaches the CLI again."""
    with _fetch_lock:
        _fetched.clear()


def _run_cli(args: list[str]) -> dict[str, Any]:
    """Run the CLI and return ``{"data": parsed}`` or ``{"error": text}``."""
    cmd = [CLI_BIN, *args, "--json"]
    try:
        result = subproc.run_with_timeout(cmd, timeout=TIMEOUT_SECONDS)
    except (subproc.SubprocTimeout, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        return {"error": lines[0] if lines else f"exit {result.returncode}"}
    stdout = (result.stdout or "").strip()
    if not stdout:
        return {"error": "empty output"}
    try:
        return {"data": json.loads(stdout)}
    except json.JSONDecodeError as exc:
        return {"error": f"json decode: {exc}"}


def _equities(query: str) -> list[tuple[str, str]]:
    """The ``(symbol, name)`` of each equity ``autocomplete`` lists for the
    query, in its order. Raises ``SymbolSearchFailed`` when the call failed or
    its answer could not be read."""
    response = _run_cli(["autocomplete", "--query", query])
    if "error" in response:
        raise SymbolSearchFailed(f"symbol search for {query!r} failed: {response['error']}")
    try:
        rows = response["data"]["results"]["ResultSet"]["Result"]
        if not isinstance(rows, list):
            raise TypeError(f"Result is {type(rows).__name__}, not a list")
        return [
            (str(row.get("symbol") or "").strip(), str(row.get("name") or ""))
            for row in rows
            if str(row.get("typeDisp") or "").casefold() == "equity"
        ]
    except (AttributeError, KeyError, TypeError) as exc:
        raise SymbolSearchFailed(
            f"symbol search for {query!r}: schema: unexpected autocomplete response ({exc!r})"
        ) from exc


def _first_equity(company: str, accept) -> str | None:
    """The first equity ``autocomplete`` lists for the company that ``accept``
    takes, or None when it lists none. ``accept`` gets the symbol in upper
    case. Raises ``SymbolSearchFailed`` when the call failed or its answer
    could not be read."""
    for symbol, _name in _equities(company):
        if accept(symbol.upper()):
            return symbol
    return None


def _name_key(name: str) -> str:
    """A company name for comparison: case-folded, without punctuation, and
    without the corporate designators and share class at its end. The last
    word left is never dropped."""
    text = re.sub(r"[.'\u2019]", "", name.casefold())
    words = re.sub(r"[^\w\s]|_", " ", text).split()
    while len(words) > 1:
        if len(words) > 2 and words[-2] == "class" and words[-1] in _SHARE_CLASSES:
            del words[-2:]
        elif words[-1] in _DESIGNATORS:
            del words[-1]
        else:
            break
    return " ".join(words)


def _same_company_name(topic: str, name: str) -> bool:
    key = _name_key(name)
    return bool(key) and key == _name_key(topic)


def backup_company(topic: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The company a topic names, from Yahoo's symbol search alone. For a
    topic whose Wikidata lookup failed; the caller decides that.

    None when the search was not run (``EXCLUDE_SOURCES`` names this source,
    or the binary is absent). ``{}`` when it ran and gave no certain match,
    which includes a search that failed. Otherwise ``company`` (the topic as
    typed), ``ticker`` and ``company_source``. Never raises.
    """
    if is_excluded(config) or not _is_available():
        return None
    topic = " ".join(topic.split())
    try:
        equities = _equities(topic)
    except SymbolSearchFailed as exc:
        _log(f"backup {exc}")
        return {}
    except Exception as exc:
        _log(f"backup symbol search for {topic!r} failed: {type(exc).__name__}: {exc}")
        return {}
    if not equities:
        return {}
    # Only the first equity: a later row is a guess among companies.
    symbol, name = equities[0]
    if not symbol or "." in symbol or not _same_company_name(topic, name):
        return {}
    return {"company": topic, "ticker": symbol.upper(), "company_source": BACKUP_SOURCE}


def _confirmed_symbol(company: dict[str, Any]) -> str | None:
    """The Yahoo symbol for a resolved company, or None when not certain.
    Raises ``SymbolSearchFailed`` when the symbol search itself failed."""
    name = str(company.get("company") or "").strip()
    ticker = str(company.get("ticker") or "").strip().upper()
    if ticker:
        if str(company.get("exchange") or "").casefold() in _US_EXCHANGES:
            return ticker
        # The backup took this symbol from Yahoo itself and only without an
        # exchange suffix, so it is already Yahoo's symbol.
        if company.get("company_source") == BACKUP_SOURCE:
            return ticker
        if not name:
            return None
        return _first_equity(
            name, lambda symbol: symbol == ticker or symbol.startswith(ticker + "."),
        )
    candidates = {
        str(candidate).strip().upper()
        for candidate in company.get("ticker_candidates") or []
        if str(candidate).strip()
    }
    if not candidates or not name:
        return None
    seen: list[str] = []

    def first_only(symbol: str) -> bool:
        seen.append(symbol)
        return True

    symbol = _first_equity(name, first_only)
    if symbol and (seen[0] in candidates or seen[0].split(".", 1)[0] in candidates):
        return symbol
    return None


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _fetch_window(symbol: str, range_from: str, range_to: str) -> dict[str, Any]:
    """``{"bars": [(iso date, close)], "currency": str}`` for the window, or
    ``{"error": text}``. Fetched once per symbol and window."""
    key = (symbol, range_from, range_to)
    with _fetch_lock:
        pending = _fetched.get(key)
        fetching = pending is None
        if fetching:
            pending = _fetched[key] = Future()
    if fetching:
        try:
            pending.set_result(_fetch_window_uncached(symbol, range_from, range_to))
        except BaseException as exc:
            # Whoever is waiting gets the error; the next request tries again.
            with _fetch_lock:
                if _fetched.get(key) is pending:
                    del _fetched[key]
            pending.set_exception(exc)
    return pending.result()


def _fetch_window_uncached(symbol: str, range_from: str, range_to: str) -> dict[str, Any]:
    first = date.fromisoformat(range_from)
    last = date.fromisoformat(range_to)
    _log(f"chart {symbol} {range_from}..{range_to}")
    response = _run_cli([
        "chart", symbol,
        "--period1", str(_epoch(first - timedelta(days=1))),
        "--period2", str(_epoch(last + timedelta(days=2))),
        "--interval", "1d",
    ])
    if "error" in response:
        return response
    try:
        chart = response["data"]["results"]["chart"]
        results = chart.get("result") or []
        if not results:
            reason = (chart.get("error") or {}).get("description") or "no chart data"
            return {"error": str(reason)}
        meta = results[0].get("meta") or {}
        offset = int(meta.get("gmtoffset") or 0)
        stamps = results[0].get("timestamp") or []
        closes = results[0]["indicators"]["quote"][0].get("close") or [] if stamps else []
        bars = []
        for stamp, close in zip(stamps, closes):
            if not isinstance(close, (int, float)) or isinstance(close, bool):
                continue
            day = datetime.fromtimestamp(stamp + offset, timezone.utc).date().isoformat()
            if range_from <= day <= range_to:
                bars.append((day, round(float(close), 4)))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        return {"error": f"schema: unexpected chart response ({exc!r})"}
    return {
        "bars": bars,
        "currency": str(meta.get("currency") or "").upper(),
        "instrument": str(meta.get("instrumentType") or "").upper(),
        "name": str(meta.get("longName") or meta.get("shortName") or "").strip(),
    }


def _percent(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0


def _summary(bars: list[tuple[str, float]]) -> dict[str, Any]:
    """First and last close, the change between them, and the daily moves
    ordered largest first. Needs two bars."""
    moves = [
        {
            "date": day,
            "previous_close": previous,
            "close": close,
            "change_pct": _percent(previous, close),
        }
        for (_previous_day, previous), (day, close) in zip(bars, bars[1:])
        if previous
    ]
    moves.sort(key=lambda move: abs(move["change_pct"]), reverse=True)
    (start_date, start_close), (end_date, end_close) = bars[0], bars[-1]
    return {
        "start_date": start_date,
        "start_close": start_close,
        "end_date": end_date,
        "end_close": end_close,
        "change_pct": _percent(start_close, end_close),
        "top_moves": moves[:TOP_MOVES],
    }


def fetch_market_data(company: dict[str, Any], range_from: str, range_to: str) -> dict[str, Any]:
    """The price window for a resolved company.

    Returns ``{}`` when there is nothing to fetch (binary absent, no ticker,
    or a symbol Yahoo does not confirm), ``{"artifact": {...}}`` when the
    window came back, and ``{"error": text}`` when the symbol search or the
    fetch for a confirmed symbol failed. Never raises.
    """
    if not (company.get("ticker") or company.get("ticker_candidates")):
        return {}
    if not _is_available():
        return {}
    try:
        try:
            symbol = _confirmed_symbol(company)
        except SymbolSearchFailed as exc:
            _log(str(exc))
            return {"error": str(exc)}
        if not symbol:
            return {}
        window = _fetch_window(symbol, range_from, range_to)
        if "error" not in window and len(window["bars"]) < 2:
            window = {"error": f"fewer than two daily closes in {range_from}..{range_to}"}
        if "error" in window:
            _log(f"{symbol}: {window['error']}")
            return {"error": f"{symbol}: {window['error']}"}
        if not window["bars"][0][1]:
            return {"error": f"{symbol}: first close is zero"}
        return {"artifact": _artifact(
            symbol, window, range_from, range_to,
            company=str(company.get("company") or ""),
            via_person=bool(company.get("company_via_person")),
        )}
    except Exception as exc:
        _log(f"failed for {company.get('company')!r}: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}


def _artifact(
    symbol: str, window: dict[str, Any], range_from: str, range_to: str, **labels: Any,
) -> dict[str, Any]:
    """The price window as the report stores it, with the S&P 500 beside a US
    listing. ``window`` holds at least two bars."""
    artifact: dict[str, Any] = {
        "symbol": symbol,
        **labels,
        "currency": window["currency"],
        "range_from": range_from,
        "range_to": range_to,
        **_summary(window["bars"]),
    }
    # A US listing: quoted in dollars under a symbol with no exchange suffix.
    if window["currency"] == "USD" and "." not in symbol:
        benchmark = _fetch_window(BENCHMARK_SYMBOL, range_from, range_to)
        if "error" in benchmark or len(benchmark["bars"]) < 2 or not benchmark["bars"][0][1]:
            _log(f"{BENCHMARK_SYMBOL}: {benchmark.get('error') or 'fewer than two daily closes'}")
        else:
            summary = _summary(benchmark["bars"])
            artifact["benchmark"] = {
                "symbol": BENCHMARK_SYMBOL,
                "name": BENCHMARK_NAME,
                "start_close": summary["start_close"],
                "end_close": summary["end_close"],
                "change_pct": summary["change_pct"],
            }
    return artifact


def parse_host_tickers(raw: str) -> list[dict[str, str]]:
    """The pairs of a ``--ticker`` value, ``SYMBOL:Company name`` separated by
    commas, so a company name cannot hold a comma. The symbol is upper-cased
    and a leading ``$`` dropped; the name is everything after the first colon.
    Only the first ``MAX_HOST_TICKERS`` pairs are read. Each is ``pair`` (as
    typed), ``symbol`` and ``company``, with a ``reason`` when it cannot be
    used: no company name, a symbol that is not one, or a repeated symbol."""
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if len(entries) > MAX_HOST_TICKERS:
        ignored = ", ".join(entry[:_HOST_TEXT_LIMIT] for entry in entries[MAX_HOST_TICKERS:])
        _log(f"--ticker takes {MAX_HOST_TICKERS} pairs; ignoring {ignored}")
    pairs: list[dict[str, str]] = []
    for entry in entries[:MAX_HOST_TICKERS]:
        symbol, _colon, company = entry.partition(":")
        symbol = symbol.strip().lstrip("$").upper()
        pair = {
            "pair": entry[:_HOST_TEXT_LIMIT],
            "symbol": symbol,
            "company": " ".join(company.split())[:_HOST_TEXT_LIMIT],
        }
        if not _HOST_SYMBOL.match(symbol):
            pair["reason"] = "not a ticker symbol"
        elif not pair["company"]:
            pair["reason"] = "no company name"
        elif any(symbol == earlier["symbol"] for earlier in pairs):
            pair["reason"] = f"repeats {symbol}"
        pairs.append(pair)
    return pairs


def _check_host_ticker(symbol: str, company: str, range_from: str, range_to: str) -> dict[str, Any]:
    """Whether Yahoo agrees that ``symbol`` is the stock of ``company``.

    ``{"window": ...}`` when it does. Otherwise ``{"reason": text}``, with
    ``"error"`` as well when Yahoo could not be asked, which says nothing
    about the pair. Raises nothing the caller does not catch."""
    window = _fetch_window(symbol, range_from, range_to)
    if "error" in window:
        failure = window["error"].casefold()
        if "http 404" in failure or "no data found" in failure:
            return {"reason": f"Yahoo has no price data for {symbol}"}
        return {"reason": "not checked: price fetch failed", "error": f"{symbol}: {window['error']}"}
    if window["instrument"] != "EQUITY":
        return {"reason": f"{symbol} is not a stock on Yahoo ({window['instrument'] or 'no type'})"}
    if len(window["bars"]) < 2 or not window["bars"][0][1]:
        return {"reason": f"{symbol} has fewer than two daily closes in {range_from}..{range_to}"}
    try:
        equities = _equities(company)
    except SymbolSearchFailed as exc:
        return {"reason": "not checked: symbol search failed", "error": str(exc)}
    listed_as = f"{symbol} is {window['name'] or 'unnamed'}"
    if not equities:
        return {"reason": f"Yahoo lists no stock for {company!r}; {listed_as}"}
    # Only the first equity, as everywhere else: a later row is a guess.
    first_symbol, first_name = equities[0]
    same_name = _name_key(first_name) and _name_key(first_name) == _name_key(window["name"])
    if first_symbol.upper() == symbol or same_name:
        return {"window": window}
    return {
        "reason": f"Yahoo's first stock for {company!r} is {first_symbol} ({first_name}); {listed_as}"
    }


PRICE_WINDOW_DAYS = 30


def _thirty_days_before(range_to: str) -> str:
    """The footer says "in the L30", so the price window is always the 30 days
    ending on the report's last day, whatever the research window was."""
    return (date.fromisoformat(range_to) - timedelta(days=PRICE_WINDOW_DAYS)).isoformat()


def is_excluded(config: dict[str, Any] | None) -> bool:
    """True when ``EXCLUDE_SOURCES`` names this source."""
    return SOURCE in env.exclude_sources(config or {})


def attach_market_data(
    report: schema.Report, company: dict[str, Any], config: dict[str, Any] | None = None,
) -> None:
    """Fetch the report's window for the company and record it on the report:
    the ``yahoo_finance`` artifact on success, a degraded source outcome on a
    failed fetch, and nothing at all when there was nothing to fetch or the
    run's ``config`` excludes the source."""
    if is_excluded(config):
        return
    outcome = fetch_market_data(company or {}, _thirty_days_before(report.range_to), report.range_to)
    if "artifact" in outcome:
        report.artifacts[SOURCE] = outcome["artifact"]
    elif "error" in outcome:
        _record_failure(report, outcome["error"])


def _record_failure(report: schema.Report, error: str) -> None:
    report.source_status[SOURCE] = schema.SourceOutcome(
        source=SOURCE,
        state=http.classify_failure(message=error),
        detail=error,
        fix_hint="doctor",
    )


def attach_host_tickers(
    report: schema.Report, pairs: list[dict[str, str]], config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check the host's ``--ticker`` pairs and record the accepted ones on the
    report, in place of ``attach_market_data``: there is no other ticker to
    fall back to, because a line for another company is worse than none.

    The ``yahoo_finance`` artifact is the first accepted window, and a second
    one sits in its ``more`` list. A check Yahoo could not answer records a
    degraded source outcome. Returns the fields for the ``resolved`` artifact:
    ``ticker_source``, then ``ticker``, ``tickers`` and ``company`` (the first
    accepted pair's) when a pair was accepted, and ``ticker_rejected`` (each
    ``pair`` and ``reason``) when one was not. With the source excluded or the
    binary absent nothing is run and every pair is listed as not checked.
    Never raises."""
    range_from = _thirty_days_before(report.range_to)
    topic_key = _name_key(report.topic or "")
    not_run = None
    if is_excluded(config):
        not_run = f"not checked: {SOURCE} is excluded"
    elif not _is_available():
        not_run = f"not checked: {CLI_BIN} is not on PATH"
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    errors: list[str] = []
    for pair in pairs:
        symbol, company = pair["symbol"], pair["company"]
        outcome: dict[str, Any] = {"reason": pair.get("reason") or not_run}
        if not outcome["reason"]:
            try:
                outcome = _check_host_ticker(symbol, company, range_from, report.range_to)
                if "window" in outcome:
                    accepted.append(_artifact(
                        symbol, outcome["window"], range_from, report.range_to,
                        company=company, via_person=False,
                        # Beside the ticker when the topic is not the company:
                        # the person who leads it, a product, a subsidiary.
                        name_company=_name_key(company) != topic_key,
                    ))
                    continue
            except Exception as exc:
                error = f"{symbol}: {type(exc).__name__}: {exc}"
                outcome = {"reason": "not checked: the check failed", "error": error}
        if "error" in outcome:
            errors.append(outcome["error"])
        if not not_run:
            _log(f"--ticker {pair['pair']} not used: {outcome['reason']}")
        rejected.append({"pair": pair["pair"], "reason": outcome["reason"]})
    fields: dict[str, Any] = {"ticker_source": HOST_SOURCE}
    if accepted:
        report.artifacts[SOURCE] = {**accepted[0], "more": accepted[1:]} if accepted[1:] else accepted[0]
        fields["ticker"] = accepted[0]["symbol"]
        fields["tickers"] = [window["symbol"] for window in accepted]
        fields["company"] = accepted[0]["company"]
    if rejected:
        fields["ticker_rejected"] = rejected
    if errors:
        _record_failure(report, "; ".join(errors))
    return fields
