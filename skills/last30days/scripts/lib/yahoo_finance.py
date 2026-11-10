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


def _first_equity(company: str, accept) -> str | None:
    """The first equity ``autocomplete`` lists for the company that ``accept``
    takes, or None when it lists none. ``accept`` gets the symbol in upper
    case. Raises ``SymbolSearchFailed`` when the call failed or its answer
    could not be read."""
    response = _run_cli(["autocomplete", "--query", company])
    if "error" in response:
        raise SymbolSearchFailed(f"symbol search for {company!r} failed: {response['error']}")
    try:
        rows = response["data"]["results"]["ResultSet"]["Result"]
        if not isinstance(rows, list):
            raise TypeError(f"Result is {type(rows).__name__}, not a list")
        for row in rows:
            if str(row.get("typeDisp") or "").casefold() != "equity":
                continue
            symbol = str(row.get("symbol") or "").strip()
            if accept(symbol.upper()):
                return symbol
    except (AttributeError, KeyError, TypeError) as exc:
        raise SymbolSearchFailed(
            f"symbol search for {company!r}: schema: unexpected autocomplete response ({exc!r})"
        ) from exc
    return None


def _confirmed_symbol(company: dict[str, Any]) -> str | None:
    """The Yahoo symbol for a resolved company, or None when not certain.
    Raises ``SymbolSearchFailed`` when the symbol search itself failed."""
    name = str(company.get("company") or "").strip()
    ticker = str(company.get("ticker") or "").strip().upper()
    if ticker:
        if str(company.get("exchange") or "").casefold() in _US_EXCHANGES:
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
    return {"bars": bars, "currency": str(meta.get("currency") or "").upper()}


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
        artifact: dict[str, Any] = {
            "symbol": symbol,
            "company": str(company.get("company") or ""),
            "via_person": bool(company.get("company_via_person")),
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
        return {"artifact": artifact}
    except Exception as exc:
        _log(f"failed for {company.get('company')!r}: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}


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
        report.source_status[SOURCE] = schema.SourceOutcome(
            source=SOURCE,
            state=http.classify_failure(message=outcome["error"]),
            detail=outcome["error"],
            fix_hint="doctor",
        )
