"""Share-price window from yahoo-finance-pp-cli: ticker choice, numbers, footer.

No test shells out. ``FakeCli`` stands in for ``subproc.run_with_timeout`` and
answers ``chart`` and ``autocomplete`` in the shapes the real binary returned
for NVDA, 7203.T, ^GSPC, Tesla and Stripe.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import last30days as cli
from lib import company_resolve, health, http, render, schema, yahoo_finance
from tests.test_company_resolve import FakeWikidata
from tests.test_render_v3 import sample_report

NVDA_CLOSES = [
    ("2026-10-12", 226.32), ("2026-10-13", 222.00), ("2026-10-20", 210.00),
    ("2026-10-27", 200.00), ("2026-11-04", 190.00), ("2026-11-05", 172.33),
    ("2026-11-06", 175.00), ("2026-11-09", 170.00), ("2026-11-10", 167.68),
]
SP500_CLOSES = [("2026-10-12", 7807.24), ("2026-10-26", 7650.00), ("2026-11-10", 7500.91)]
RISING_CLOSES = [("2026-10-12", 100.0), ("2026-10-13", 104.0), ("2026-11-10", 110.0)]
TOYOTA_CLOSES = [("2026-10-12", 2480.0), ("2026-10-13", 2500.0), ("2026-11-10", 2610.0)]
AMD_CLOSES = [("2026-10-12", 200.0), ("2026-10-30", 190.0), ("2026-11-10", 183.8)]

NVDA_LINE = (
    "📈 Yahoo Finance: NVDA -25.9% in the L30 │ $226.32 → $167.68 │ "
    "S&P 500 -3.9% │ worst day Nov 5 -9.3%"
)
NVIDIA = {"company": "Nvidia", "ticker": "NVDA", "exchange": "Nasdaq"}
TESLA = {"company": "Tesla", "ticker_candidates": ["TSLA", "TL0", "0R0X"]}


def _chart(symbol, closes, *, currency="USD", gmtoffset=-18000, hour=14, name=None, instrument="EQUITY"):
    """A chart response; each bar is stamped at the exchange's opening hour."""
    stamps = [
        int(datetime.fromisoformat(f"{day}T{hour:02d}:30:00+00:00").timestamp())
        for day, _close in closes
    ]
    names = {"longName": name, "shortName": name} if name else {}
    return {
        "meta": {"source": "live"},
        "results": {"chart": {"result": [{
            "meta": {
                "currency": currency, "symbol": symbol, "gmtoffset": gmtoffset,
                "instrumentType": instrument, "fullExchangeName": "NasdaqGS", **names,
            },
            "timestamp": stamps,
            "indicators": {"quote": [{"close": [close for _day, close in closes]}]},
        }]}},
    }


def _matches(*rows):
    return {"meta": {"source": "live"}, "results": {"ResultSet": {"Result": [
        {"symbol": symbol, "name": symbol, "typeDisp": kind, "exchDisp": "-"}
        for symbol, kind in rows
    ]}}}


def _ok(data) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")


def _failed(message: str, code: int = 3) -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout="", stderr=f"Error: {message}\n")


class FakeCli:
    """Answers by symbol for ``chart`` and by query for ``autocomplete``."""

    def __init__(self, charts=None, matches=None):
        self.charts = charts or {}
        self.matches = matches or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        assert cmd[0] == yahoo_finance.CLI_BIN
        assert "--json" in cmd and "--agent" not in cmd
        if cmd[1] == "chart":
            answer = self.charts[cmd[2]]
        else:
            assert cmd[1:3] == ["autocomplete", "--query"]
            answer = self.matches[cmd[3]]
        return answer if isinstance(answer, SimpleNamespace) else _ok(answer)

    def charted(self) -> list[str]:
        return [cmd[2] for cmd in self.calls if cmd[1] == "chart"]


@pytest.fixture(autouse=True)
def _fresh_fetches():
    yahoo_finance.forget_fetches()


def _install(monkeypatch, *, available=True, **fake_kwargs) -> FakeCli:
    fake = FakeCli(**fake_kwargs)
    monkeypatch.setattr(yahoo_finance, "_is_available", lambda: available)
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", fake)
    return fake


def _report(range_from="2026-10-11", range_to="2026-11-10") -> schema.Report:
    report = sample_report()
    report.range_from = range_from
    report.range_to = range_to
    return report


def _attach(monkeypatch, company, *, report=None, **install_kwargs):
    fake = _install(monkeypatch, **install_kwargs)
    report = report or _report()
    yahoo_finance.attach_market_data(report, company)
    return report, fake


def _footer(report: schema.Report) -> list[str]:
    return [line for line in render._build_source_footer_lines(report) if "Yahoo Finance" in line]


NVDA_CHARTS = {"NVDA": _chart("NVDA", NVDA_CLOSES), "^GSPC": _chart("^GSPC", SP500_CLOSES)}


def test_falling_window_reads_as_one_footer_line(monkeypatch):
    report, fake = _attach(monkeypatch, NVIDIA, charts=NVDA_CHARTS)

    assert _footer(report) == [NVDA_LINE]
    assert fake.charted() == ["NVDA", "^GSPC"]
    # The window is asked for by its edges, a day wide on each side.
    nvda = fake.calls[0]
    assert nvda[nvda.index("--interval") + 1] == "1d"
    period1 = int(nvda[nvda.index("--period1") + 1])
    period2 = int(nvda[nvda.index("--period2") + 1])
    assert datetime.fromtimestamp(period1, timezone.utc).date().isoformat() == "2026-10-10"
    assert datetime.fromtimestamp(period2, timezone.utc).date().isoformat() == "2026-11-12"


def test_footer_line_sits_directly_after_polymarket(monkeypatch):
    report, _fake = _attach(monkeypatch, NVIDIA, charts=NVDA_CHARTS)
    report.items_by_source["polymarket"] = [schema.SourceItem(
        item_id="pm1", source="polymarket", title="Will Nvidia close above $200?",
        body="", url="https://polymarket.com/event/nvda",
    )]

    lines = render._build_source_footer_lines(report)

    polymarket = next(i for i, line in enumerate(lines) if line.startswith("📊 Polymarket"))
    assert lines[polymarket + 1] == NVDA_LINE


def test_rising_window_names_the_best_day(monkeypatch):
    report, _fake = _attach(
        monkeypatch, {"company": "Riser", "ticker": "RISE", "exchange": "Nasdaq"},
        charts={"RISE": _chart("RISE", RISING_CLOSES), "^GSPC": _chart("^GSPC", SP500_CLOSES)},
    )

    assert _footer(report) == [
        "📈 Yahoo Finance: RISE +10.0% in the L30 │ $100.00 → $110.00 │ "
        "S&P 500 -3.9% │ best day Nov 10 +5.8%"
    ]


def test_listing_outside_the_us_uses_its_currency_and_no_sp500(monkeypatch):
    report, fake = _attach(
        monkeypatch,
        {"company": "Toyota", "ticker": "7203", "exchange": "Tokyo Stock Exchange"},
        matches={"Toyota": _matches(("TM", "Equity"), ("7203.T", "Equity"))},
        charts={"7203.T": _chart("7203.T", TOYOTA_CLOSES, currency="JPY", gmtoffset=32400, hour=0)},
    )

    assert _footer(report) == [
        "📈 Yahoo Finance: 7203.T +5.2% in the L30 │ JPY 2,480 → JPY 2,610 │ best day Nov 10 +4.4%"
    ]
    assert fake.charted() == ["7203.T"]


def test_foreign_ticker_yahoo_cannot_confirm_gives_no_line(monkeypatch):
    report, fake = _attach(
        monkeypatch,
        {"company": "Toyota", "ticker": "7203", "exchange": "Tokyo Stock Exchange"},
        matches={"Toyota": _matches(("TM", "Equity"), ("72035.T", "Equity"))},
    )

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status
    assert fake.charted() == []


def test_window_opening_on_a_weekend_starts_at_the_first_close(monkeypatch):
    report, _fake = _attach(
        monkeypatch, NVIDIA, report=_report(range_from="2026-10-10"), charts=NVDA_CHARTS,
    )

    window = report.artifacts["yahoo_finance"]
    assert (window["start_date"], window["start_close"]) == ("2026-10-12", 226.32)
    assert (window["end_date"], window["end_close"]) == ("2026-11-10", 167.68)
    assert _footer(report) == [NVDA_LINE]


def test_bars_outside_the_window_are_left_out(monkeypatch):
    wide = [("2026-10-09", 229.28), *NVDA_CLOSES, ("2026-11-11", 180.00)]
    report, _fake = _attach(
        monkeypatch, NVIDIA,
        charts={"NVDA": _chart("NVDA", wide), "^GSPC": _chart("^GSPC", SP500_CLOSES)},
    )

    assert _footer(report) == [NVDA_LINE]


def test_ticker_reached_through_a_person_names_the_company(monkeypatch):
    report, _fake = _attach(
        monkeypatch, {**NVIDIA, "company_via_person": True}, charts=NVDA_CHARTS,
    )

    assert _footer(report) == [NVDA_LINE.replace("NVDA -25.9%", "NVDA (Nvidia) -25.9%")]


@pytest.mark.parametrize(
    "company",
    [
        {},
        {"company": "Stripe", "company_site": "https://stripe.com/"},
    ],
    ids=["no-company", "company-with-no-listing"],
)
def test_no_ticker_means_no_line_no_artifact_and_no_cli_call(monkeypatch, company):
    report, fake = _attach(monkeypatch, company)

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status
    assert fake.calls == []


def test_candidate_is_used_when_yahoo_puts_it_first(monkeypatch):
    report, fake = _attach(
        monkeypatch, TESLA,
        matches={"Tesla": _matches(
            ("STSLA=F", "Futures"), ("TSLA", "Equity"), ("TL0.DE", "Equity"),
        )},
        charts={"TSLA": _chart("TSLA", RISING_CLOSES), "^GSPC": _chart("^GSPC", SP500_CLOSES)},
    )

    assert report.artifacts["yahoo_finance"]["symbol"] == "TSLA"
    assert _footer(report)[0].startswith("📈 Yahoo Finance: TSLA +10.0% in the L30")
    assert fake.calls[0] == [yahoo_finance.CLI_BIN, "autocomplete", "--query", "Tesla", "--json"]


def test_candidate_matches_before_the_exchange_suffix(monkeypatch):
    report, _fake = _attach(
        monkeypatch, {"company": "Tesla", "ticker_candidates": ["tl0"]},
        matches={"Tesla": _matches(("TL0.DE", "Equity"))},
        charts={"TL0.DE": _chart("TL0.DE", RISING_CLOSES, currency="EUR", gmtoffset=3600, hour=8)},
    )

    assert report.artifacts["yahoo_finance"]["symbol"] == "TL0.DE"


@pytest.mark.parametrize(
    "rows",
    [
        # The first equity is another company; a later row that would match
        # is not reached for.
        [("TSLZ", "ETF"), ("RIVN", "Equity"), ("TSLA", "Equity")],
        [("STRIZZX", "Fund"), ("0P0000X8PD.F", "Fund")],
        [],
    ],
    ids=["first-equity-is-not-a-candidate", "only-funds", "no-matches"],
)
def test_candidates_yahoo_does_not_confirm_give_no_line(monkeypatch, rows):
    report, fake = _attach(monkeypatch, TESLA, matches={"Tesla": _matches(*rows)})

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status
    assert fake.charted() == []


@pytest.mark.parametrize(
    "answer,state",
    [
        (_failed("GET /v8/finance/chart/NVDA returned HTTP 500: upstream failure"), health.ERROR),
        (
            _failed("GET /v8/finance/chart/NVDA returned HTTP 429: Too Many Requests", code=7),
            health.RATE_LIMITED,
        ),
        (SimpleNamespace(returncode=0, stdout="", stderr=""), health.ERROR),
        (_chart("NVDA", []), health.ERROR),
        (_chart("NVDA", NVDA_CLOSES[:1]), health.ERROR),
        (_chart("NVDA", [("2026-10-12", None), ("2026-10-13", 222.0)]), health.ERROR),
    ],
    ids=["cli-error", "http-429", "empty-output", "empty-bars", "one-bar", "one-usable-close"],
)
def test_failed_fetch_is_a_degraded_source_and_no_line(monkeypatch, answer, state):
    report, _fake = _attach(monkeypatch, NVIDIA, charts={"NVDA": answer})

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    outcome = report.source_status["yahoo_finance"]
    assert outcome.state == state
    assert outcome.items_returned == 0
    assert "NVDA" in outcome.detail
    assert "Yahoo Finance" in "\n".join(render._render_source_outcome_note(report))


def test_cli_that_cannot_start_is_a_degraded_source(monkeypatch):
    def cannot_start(cmd, timeout):
        raise OSError("exec format error")

    monkeypatch.setattr(yahoo_finance, "_is_available", lambda: True)
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", cannot_start)
    report = _report()

    yahoo_finance.attach_market_data(report, NVIDIA)

    assert report.source_status["yahoo_finance"].state == health.ERROR
    assert "yahoo_finance" not in report.artifacts


def test_sp500_failure_keeps_the_line_without_that_segment(monkeypatch):
    report, _fake = _attach(
        monkeypatch, NVIDIA,
        charts={"NVDA": _chart("NVDA", NVDA_CLOSES), "^GSPC": _failed("HTTP 429: Too Many Requests")},
    )

    assert _footer(report) == [
        "📈 Yahoo Finance: NVDA -25.9% in the L30 │ $226.32 → $167.68 │ worst day Nov 5 -9.3%"
    ]
    assert "yahoo_finance" not in report.source_status


def test_missing_binary_is_silent(monkeypatch, capsys):
    report, fake = _attach(monkeypatch, NVIDIA, available=False)

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status
    assert fake.calls == []
    assert capsys.readouterr().err == ""


def test_item_totals_and_stats_are_unchanged_by_a_fetch(monkeypatch):
    before = _report()
    stats_before = render._render_stats(before)
    coverage_before = render._render_source_coverage(before)
    items_before = {source: list(items) for source, items in before.items_by_source.items()}

    report, _fake = _attach(monkeypatch, NVIDIA, report=before, charts=NVDA_CHARTS)

    assert "yahoo_finance" in report.artifacts
    assert report.items_by_source == items_before
    assert render._render_stats(report) == stats_before
    assert render._render_source_coverage(report) == coverage_before
    assert "yahoo_finance" not in report.source_status


def test_evidence_carries_market_data_and_no_presentation_commands(monkeypatch):
    report, _fake = _attach(
        monkeypatch, {**NVIDIA, "company_via_person": True}, charts=NVDA_CHARTS,
    )

    text = render.render_compact(report)

    start = text.index("## Market Data")
    assert text.index("<!-- EVIDENCE FOR SYNTHESIS") < start < text.index("<!-- END EVIDENCE FOR SYNTHESIS -->")
    block = text[start:text.index("\n## ", start + 1)]
    assert block.splitlines() == [
        "## Market Data",
        "",
        "- Yahoo Finance daily closes: NVDA (Nvidia), USD, 2026-10-11 to 2026-11-10",
        "- First close 226.32 (2026-10-12), last close 167.68 (2026-11-10), change -25.9%",
        "- S&P 500 over the same window: 7,807.24 to 7,500.91, change -3.9%",
        "- Largest daily moves, close to close:",
        "  - 2026-11-05: 190.00 to 172.33, -9.3%",
        "  - 2026-10-20: 222.00 to 210.00, -5.4%",
        "  - 2026-11-04: 200.00 to 190.00, -5.0%",
    ]
    for command in (
        "SYNTHESIS CONTRACT", "this contract still binds", "STOP and regenerate",
        "Pass through ONLY", "do not add a `Sources:` list", "emit verbatim",
        "Fill each cell", "After the table, write", "YOU ARE",
    ):
        assert command not in text
    for word in ("must", "should", "mention", "cite", "do not"):
        assert word not in block.lower()
    assert NVDA_LINE.replace("NVDA -25.9%", "NVDA (Nvidia) -25.9%") in text


def test_no_market_data_section_without_a_fetch():
    assert "## Market Data" not in render.render_compact(_report())


def test_comparison_puts_every_ticker_on_one_line(monkeypatch):
    fake = _install(monkeypatch, charts={
        **NVDA_CHARTS, "AMD": _chart("AMD", AMD_CLOSES),
    })
    nvidia, amd, rust = _report(), _report(), _report()
    yahoo_finance.attach_market_data(nvidia, NVIDIA)
    yahoo_finance.attach_market_data(amd, {"company": "AMD", "ticker": "AMD", "exchange": "Nasdaq"})
    yahoo_finance.attach_market_data(rust, {})

    text = render.render_comparison_multi([("Nvidia", nvidia), ("AMD", amd), ("Rust", rust)])

    lines = [line for line in text.splitlines() if "📈 Yahoo Finance" in line]
    assert [line.split(" ", 1)[1] for line in lines] == [
        "📈 Yahoo Finance: NVDA -25.9% │ AMD -8.1% in the L30 │ S&P 500 -3.9%"
    ]
    # One fetch per ticker: the S&P 500 window is fetched once for the run.
    assert sorted(fake.charted()) == ["AMD", "NVDA", "^GSPC"]
    assert text.count("### Market Data") == 2


def test_comparison_line_when_only_a_peer_has_a_ticker(monkeypatch):
    _install(monkeypatch, charts=NVDA_CHARTS)
    stripe, nvidia = _report(), _report()
    yahoo_finance.attach_market_data(nvidia, NVIDIA)

    text = render.render_comparison_multi([("Stripe", stripe), ("Nvidia", nvidia)])

    assert f"{NVDA_LINE}\n" in text


def test_footer_text_from_yahoo_cannot_forge_a_second_row(monkeypatch):
    report, _fake = _attach(
        monkeypatch,
        {"company": "Evil │ Corp\n├─ 🟠 Reddit: 99 threads", "ticker": "NVDA",
         "exchange": "Nasdaq", "company_via_person": True},
        charts=NVDA_CHARTS,
    )

    (line,) = _footer(report)
    assert "\n" not in line
    assert line.count("│") == NVDA_LINE.count("│")


def test_engine_run_attaches_the_window_for_each_entity(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    # The helper's reports cover June 2026.
    june = [("2026-06-01", 50.0), ("2026-06-15", 55.0), ("2026-06-30", 60.0)]
    fake = FakeCli(charts={"MAIN": _chart("MAIN", june), "^GSPC": _chart("^GSPC", june)})
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake,
        companies={
            "MainBrand": {"company": "MainBrand Inc.", "ticker": "MAIN", "exchange": "Nasdaq"},
            "PeerOne": {"company": "PeerOne", "company_site": "https://peerone.example/"},
        },
    )

    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"
    assert "yahoo_finance" not in observed.reports["PeerOne"].artifacts
    assert "yahoo_finance" not in observed.reports["PeerTwo"].artifacts
    assert fake.charted() == ["MAIN", "^GSPC"]


def test_mock_run_never_starts_the_cli(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli()
    run_competitor_cli(monkeypatch, tmp_path, args=["--mock"], market_cli=fake)

    assert fake.calls == []


def test_price_window_is_the_thirty_days_ending_on_the_last_day(monkeypatch):
    seen = []
    monkeypatch.setattr(
        yahoo_finance, "fetch_market_data",
        lambda company, start, end: seen.append((start, end)) or {},
    )
    yahoo_finance.attach_market_data(_report(range_from="2026-11-03", range_to="2026-11-10"), {"ticker": "NVDA"})
    assert seen == [("2026-10-11", "2026-11-10")]


@pytest.mark.parametrize("excluded", ["yahoo_finance", "reddit, Yahoo_Finance ,x"])
def test_excluded_source_makes_no_cli_call_and_leaves_nothing(monkeypatch, excluded):
    fake = _install(monkeypatch, charts=NVDA_CHARTS)
    report = _report()

    yahoo_finance.attach_market_data(report, NVIDIA, {"EXCLUDE_SOURCES": excluded})

    assert fake.calls == []
    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status


def test_other_exclusions_leave_the_price_line_on(monkeypatch):
    fake = _install(monkeypatch, charts=NVDA_CHARTS)
    report = _report()

    yahoo_finance.attach_market_data(report, NVIDIA, {"EXCLUDE_SOURCES": "reddit,jobs"})

    assert _footer(report) == [NVDA_LINE]
    assert fake.charted() == ["NVDA", "^GSPC"]


_JUNE = [("2026-06-01", 50.0), ("2026-06-15", 55.0), ("2026-06-30", 60.0)]
_LISTED = {
    "MainBrand": {"company": "MainBrand Inc.", "ticker": "MAIN", "exchange": "Nasdaq"},
    "PeerOne": {"company": "PeerOne Corp.", "ticker": "PEER", "exchange": "Nasdaq"},
}
_JUNE_CHARTS = {
    "MAIN": _chart("MAIN", _JUNE), "PEER": _chart("PEER", _JUNE), "^GSPC": _chart("^GSPC", _JUNE),
}


def test_engine_run_with_yahoo_finance_excluded_never_starts_the_cli(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=_JUNE_CHARTS)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, companies=_LISTED,
        config={"EXCLUDE_SOURCES": "yahoo_finance"},
    )

    assert fake.calls == []
    for report in observed.reports.values():
        assert "yahoo_finance" not in report.artifacts
        assert "yahoo_finance" not in report.source_status
    # Jobs can still run, so the company is still looked up.
    assert observed.reports["MainBrand"].artifacts["resolved"]["ticker"] == "MAIN"
    assert observed.reports["PeerOne"].artifacts["resolved"]["ticker"] == "PEER"


def test_engine_run_with_both_company_lanes_excluded_skips_the_company_lookup(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=_JUNE_CHARTS)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, companies=_LISTED,
        config={"EXCLUDE_SOURCES": "jobs, yahoo_finance"},
    )

    assert observed.company_resolver.call_count == 0
    assert fake.calls == []
    for report in observed.reports.values():
        assert "company" not in report.artifacts["resolved"]
        assert "ticker" not in report.artifacts["resolved"]
        assert "yahoo_finance" not in report.artifacts


def test_engine_run_with_only_jobs_excluded_still_prices_the_company(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=_JUNE_CHARTS)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, companies=_LISTED,
        config={"EXCLUDE_SOURCES": "jobs"},
    )

    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"
    assert observed.reports["PeerOne"].artifacts["yahoo_finance"]["symbol"] == "PEER"


def test_explicit_search_list_keeps_the_price_line(monkeypatch, tmp_path):
    """The price window is not a search source, so ``--search`` cannot name
    it and does not switch it off; ``EXCLUDE_SOURCES`` is the off switch."""
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=_JUNE_CHARTS)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, companies=_LISTED,
        args=["--search", "reddit"],
    )

    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"


_TOYOTA = {"company": "Toyota", "ticker": "7203", "exchange": "Tokyo Stock Exchange"}


@pytest.mark.parametrize(
    "company,answer,state",
    [
        (TESLA, _failed("GET /v6/finance/autocomplete returned HTTP 500: upstream failure"), health.ERROR),
        (
            TESLA,
            _failed("GET /v6/finance/autocomplete returned HTTP 429: Too Many Requests", code=7),
            health.RATE_LIMITED,
        ),
        (TESLA, SimpleNamespace(returncode=0, stdout="", stderr=""), health.ERROR),
        (TESLA, SimpleNamespace(returncode=0, stdout="<html>busy</html>", stderr=""), health.SCHEMA_DRIFT),
        (TESLA, {"meta": {"source": "live"}, "results": {}}, health.SCHEMA_DRIFT),
        (TESLA, {"results": {"ResultSet": {"Result": "TSLA"}}}, health.SCHEMA_DRIFT),
        (_TOYOTA, _failed("GET /v6/finance/autocomplete returned HTTP 500: upstream failure"), health.ERROR),
    ],
    ids=[
        "cli-error", "http-429", "empty-output", "not-json", "missing-result-set",
        "rows-not-a-list", "foreign-listing",
    ],
)
def test_failed_symbol_search_is_a_degraded_source_and_no_line(monkeypatch, company, answer, state):
    report, fake = _attach(monkeypatch, company, matches={company["company"]: answer})

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    outcome = report.source_status["yahoo_finance"]
    assert outcome.state == state
    assert outcome.items_returned == 0
    assert company["company"] in outcome.detail
    assert fake.charted() == []
    assert "Yahoo Finance" in "\n".join(render._render_source_outcome_note(report))


def test_symbol_search_that_times_out_is_a_degraded_source(monkeypatch):
    def times_out(cmd, timeout):
        raise yahoo_finance.subproc.SubprocTimeout(f"timed out after {timeout}s")

    monkeypatch.setattr(yahoo_finance, "_is_available", lambda: True)
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", times_out)
    report = _report()

    yahoo_finance.attach_market_data(report, TESLA)

    assert "yahoo_finance" not in report.artifacts
    assert "Tesla" in report.source_status["yahoo_finance"].detail


class _GatedCli:
    """Chart answers for any symbol; a symbol in ``held`` waits for ``release``."""

    def __init__(self, held=()):
        self.held = set(held)
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, cmd, timeout):
        symbol = cmd[2]
        with self._lock:
            self.calls.append(symbol)
        if symbol in self.held:
            self.started.set()
            assert self.release.wait(10), "the held fetch was never released"
        return _ok(_chart(symbol, RISING_CLOSES))


def _fetch_on_thread(symbol, results):
    def work():
        results.append((symbol, yahoo_finance._fetch_window(symbol, "2026-10-11", "2026-11-10")))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_a_slow_symbol_does_not_hold_up_another_symbol(monkeypatch):
    cli = _GatedCli(held={"SLOW"})
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", cli)
    results: list = []

    slow = _fetch_on_thread("SLOW", results)
    try:
        assert cli.started.wait(5)
        fast = _fetch_on_thread("FAST", results)
        fast.join(2)
        finished_while_slow_was_held = not fast.is_alive()
    finally:
        cli.release.set()
    slow.join(5)
    fast.join(5)

    assert finished_while_slow_was_held
    assert [symbol for symbol, _window in results] == ["FAST", "SLOW"]
    assert all(len(window["bars"]) == 3 for _symbol, window in results)


def test_two_threads_asking_for_one_symbol_start_one_fetch(monkeypatch):
    cli = _GatedCli(held={"NVDA"})
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", cli)
    results: list = []

    first = _fetch_on_thread("NVDA", results)
    try:
        assert cli.started.wait(5)
        second = _fetch_on_thread("NVDA", results)
        # The second request arrives while the first is still at the CLI.
        second.join(0.2)
        assert second.is_alive()
    finally:
        cli.release.set()
    first.join(5)
    second.join(5)

    assert cli.calls == ["NVDA"]
    assert len(results) == 2
    assert results[0][1] == results[1][1]
    assert len(results[0][1]["bars"]) == 3


def test_a_fetch_that_raises_is_not_remembered(monkeypatch):
    answers = [OSError("exec format error")]

    def flaky(symbol, range_from, range_to):
        if answers:
            raise answers.pop()
        return {"bars": [], "currency": "USD"}

    monkeypatch.setattr(yahoo_finance, "_fetch_window_uncached", flaky)

    with pytest.raises(OSError):
        yahoo_finance._fetch_window("NVDA", "2026-10-11", "2026-11-10")
    assert yahoo_finance._fetch_window("NVDA", "2026-10-11", "2026-11-10") == {"bars": [], "currency": "USD"}


# The backup: Yahoo's symbol search, asked for the topic itself, only when the
# Wikidata lookup failed.

RATE_LIMITED = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
TIMED_OUT = http.HTTPError("Request timed out")
BACKUP_USED = "wikidata failed; yahoo backup used"
NO_BACKUP_MATCH = "wikidata failed; no backup match"
BACKUP_NOT_RUN = "wikidata failed; backup not run"
TSLA_ROWS = [("TSLA", "Tesla, Inc.", "Equity"), ("STSLA=F", "Tesla, Inc Single Stock futures", "Futures")]


def _named(*rows):
    return {"meta": {"source": "live"}, "results": {"ResultSet": {"Result": [
        {"symbol": symbol, "name": name, "typeDisp": kind, "exchDisp": "-"}
        for symbol, name, kind in rows
    ]}}}


def _company_after(monkeypatch, topic, *, error=RATE_LIMITED, config=None, mock_run=False, **install_kwargs):
    """What the engine resolves for a topic, with Wikidata failing unless
    ``error`` is None and the price CLI answering from ``install_kwargs``."""
    fake = _install(monkeypatch, **install_kwargs)
    wikidata = FakeWikidata(error=error)
    monkeypatch.setattr(company_resolve.http, "get", wikidata)
    company = cli._resolved_company(topic, mock=mock_run, config=config or {})
    return company, fake, wikidata


@pytest.mark.parametrize("error", [RATE_LIMITED, TIMED_OUT], ids=["http-429", "timeout"])
def test_backup_gives_tesla_its_ticker_and_the_price_line(monkeypatch, error):
    company, fake, wikidata = _company_after(
        monkeypatch, "Tesla", error=error,
        matches={"Tesla": _named(*TSLA_ROWS)},
        charts={"TSLA": _chart("TSLA", RISING_CLOSES), "^GSPC": _chart("^GSPC", SP500_CLOSES)},
    )

    assert company == {
        "company": "Tesla", "ticker": "TSLA", "company_source": "yahoo-backup",
        "company_lookup": BACKUP_USED, "ticker_source": "yahoo-backup",
    }
    assert len(wikidata.calls) == 1
    report = _report()
    yahoo_finance.attach_market_data(report, company)
    assert _footer(report) == [
        "📈 Yahoo Finance: TSLA +10.0% in the L30 │ $100.00 → $110.00 │ "
        "S&P 500 -3.9% │ best day Nov 10 +5.8%"
    ]
    # One symbol search, for the topic; the accepted symbol is not searched again.
    assert [cmd[1:4] for cmd in fake.calls if cmd[1] == "autocomplete"] == [
        ["autocomplete", "--query", "Tesla"]
    ]
    assert fake.charted() == ["TSLA", "^GSPC"]


def test_backup_accepts_a_name_that_differs_by_case_and_designator(monkeypatch):
    company, _fake, _wikidata = _company_after(
        monkeypatch, "Nvidia", matches={"Nvidia": _named(("NVDA", "NVIDIA Corporation", "Equity"))},
    )

    assert (company["company"], company["ticker"]) == ("Nvidia", "NVDA")
    assert company["company_source"] == "yahoo-backup"
    assert "exchange" not in company and "company_site" not in company


@pytest.mark.parametrize(
    "topic,rows",
    [
        ("Rust", [("RUSTA.ST", "Rusta AB (publ)", "Equity")]),
        ("Stripe", [("STRIZZX", "Stripe Fund", "Fund"), ("0P0000X8PD.F", "Stripe", "Fund")]),
        (
            "Apple",
            [("APLE", "Apple Hospitality REIT, Inc.", "Equity"), ("AAPL", "Apple Inc.", "Equity")],
        ),
        ("Toyota", [("7203.T", "Toyota Motor Corporation", "Equity")]),
        ("Toyota", [("7203.T", "Toyota", "Equity"), ("TM", "Toyota", "Equity")]),
        ("Jensen Huang", []),
    ],
    ids=[
        "rust-is-not-rusta", "funds-only", "name-only-contains-the-topic",
        "foreign-listing", "foreign-listing-with-the-same-name", "no-results",
    ],
)
def test_backup_rejects_anything_but_an_exact_us_match_on_the_first_equity(monkeypatch, topic, rows):
    company, fake, _wikidata = _company_after(monkeypatch, topic, matches={topic: _named(*rows)})

    assert company == {"company_lookup": NO_BACKUP_MATCH}
    assert len(fake.calls) == 1
    report = _report()
    yahoo_finance.attach_market_data(report, company)
    assert _footer(report) == []
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "name",
    [
        "Tesla", "TESLA INC", "Tesla, Inc.", "Tesla Incorporated", "Tesla Corp.", "Tesla Corporation",
        "Tesla Co.", "Tesla Company", "Tesla Ltd.", "Tesla Limited", "Tesla PLC", "Tesla plc",
        "Tesla AG", "Tesla SA", "Tesla S.A.", "Tesla NV", "Tesla N.V.", "Tesla AB",
        "Tesla AB (publ)", "Tesla SE", "Tesla Holdings", "Tesla Holding", "Tesla Group",
        "Tesla, Inc. Class A", "Tesla Inc Class C", "Tesla Holdings Co., Ltd.", "Tesla, The",
        "  tesla,  inc. ",
    ],
)
def test_backup_name_match_ignores_case_punctuation_and_trailing_designators(name):
    assert yahoo_finance._same_company_name("Tesla", name)


@pytest.mark.parametrize(
    "topic,name",
    [
        ("Rust", "Rusta AB (publ)"),
        ("Apple", "Apple Hospitality REIT, Inc."),
        ("Tesla", "Tesla Motors, Inc."),
        ("Tesla", "Inc. Tesla"),
        ("Tesla", ""),
        ("Group", "Inc."),
    ],
)
def test_backup_name_match_refuses_any_other_name(topic, name):
    assert not yahoo_finance._same_company_name(topic, name)


def test_wikidata_answering_nothing_never_reaches_the_backup(monkeypatch):
    company, fake, wikidata = _company_after(
        monkeypatch, "Rust", error=None, matches={"Rust": _named(("RUST", "Rust", "Equity"))},
    )

    assert company == {}
    assert wikidata.calls
    assert fake.calls == []


def test_backup_without_the_binary_gives_nothing(monkeypatch):
    company, fake, _wikidata = _company_after(monkeypatch, "Tesla", available=False)

    assert company == {"company_lookup": BACKUP_NOT_RUN}
    assert fake.calls == []


@pytest.mark.parametrize("excluded", ["yahoo_finance", "reddit, Yahoo_Finance ,x"])
def test_backup_is_not_run_when_yahoo_finance_is_excluded(monkeypatch, excluded):
    company, fake, wikidata = _company_after(
        monkeypatch, "Tesla", config={"EXCLUDE_SOURCES": excluded},
        matches={"Tesla": _named(*TSLA_ROWS)},
    )

    assert company == {"company_lookup": BACKUP_NOT_RUN}
    assert len(wikidata.calls) == 1
    assert fake.calls == []


def test_a_topic_that_is_not_company_shaped_reaches_neither_wikidata_nor_the_cli(monkeypatch):
    company, fake, wikidata = _company_after(
        monkeypatch, "how to deploy on Fly.io",
        matches={"how to deploy on Fly.io": _named(("FLY", "how to deploy on Fly.io", "Equity"))},
    )

    assert company == {}
    assert wikidata.calls == []
    assert fake.calls == []


def test_mock_run_reaches_neither_wikidata_nor_the_backup(monkeypatch):
    company, fake, wikidata = _company_after(
        monkeypatch, "Tesla", mock_run=True, matches={"Tesla": _named(*TSLA_ROWS)},
    )

    assert company == {}
    assert wikidata.calls == []
    assert fake.calls == []


@pytest.mark.parametrize(
    "answer",
    [
        _failed("GET /v6/finance/autocomplete returned HTTP 429: Too Many Requests", code=7),
        {"meta": {"source": "live"}, "results": {}},
    ],
    ids=["cli-error", "unexpected-shape"],
)
def test_backup_search_that_fails_gives_nothing_and_does_not_raise(monkeypatch, answer):
    company, _fake, _wikidata = _company_after(monkeypatch, "Tesla", matches={"Tesla": answer})

    assert company == {"company_lookup": NO_BACKUP_MATCH}


def test_failed_lookup_is_logged_once_when_the_backup_runs(monkeypatch):
    logged = []
    monkeypatch.setattr(
        company_resolve.log, "source_log",
        lambda source, message, **kwargs: logged.append((source, message, kwargs)),
    )

    _company_after(monkeypatch, "Tesla", matches={"Tesla": _named(*TSLA_ROWS)})

    company_lines = [entry for entry in logged if entry[0] == "Company"]
    assert len(company_lines) == 1
    assert "Wikidata lookup failed for 'Tesla'" in company_lines[0][1]
    assert company_lines[0][2] == {"tty_only": False}


def test_engine_run_records_the_lookup_note_for_both_backup_outcomes(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(
        charts={"MAIN": _chart("MAIN", _JUNE), "^GSPC": _chart("^GSPC", _JUNE)},
        matches={
            "MainBrand": _named(("MAIN", "MainBrand, Inc.", "Equity")),
            "PeerOne": _named(("PONE.L", "PeerOne plc", "Equity")),
        },
    )
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake,
        companies={
            "MainBrand": company_resolve.FailedLookup(),
            "PeerOne": company_resolve.FailedLookup(),
        },
    )

    main = observed.reports["MainBrand"].artifacts["resolved"]
    assert (main["company"], main["ticker"]) == ("MainBrand", "MAIN")
    assert main["company_source"] == "yahoo-backup"
    assert main["company_lookup"] == BACKUP_USED
    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"
    peer_one = observed.reports["PeerOne"].artifacts["resolved"]
    assert peer_one["company_lookup"] == NO_BACKUP_MATCH
    assert not {"company", "ticker", "company_source"} & set(peer_one)
    assert "yahoo_finance" not in observed.reports["PeerOne"].artifacts
    # Wikidata answered for PeerTwo, so there is no note and no symbol search.
    assert "company_lookup" not in observed.reports["PeerTwo"].artifacts["resolved"]
    assert sorted(cmd[3] for cmd in fake.calls if cmd[1] == "autocomplete") == ["MainBrand", "PeerOne"]
    rows = render._render_resolved_entities_block(
        [(name, observed.reports[name]) for name in ("MainBrand", "PeerOne")]
    )
    assert "Ticker MAIN (Yahoo match)" in rows[2]
    assert "Ticker" not in rows[3]


# The host's ticker: ``--ticker "SYMBOL:Company"`` pairs the hosting model
# found by web search, each checked against Yahoo's chart and symbol search.

TSLA_CLOSES = [("2026-10-12", 400.0), ("2026-10-13", 410.0), ("2026-11-10", 402.0)]
SPCX_CLOSES = [("2026-10-12", 100.0), ("2026-10-13", 96.0), ("2026-11-10", 86.0)]
GOOGL_CLOSES = [("2026-10-12", 200.0), ("2026-10-13", 198.0), ("2026-11-10", 193.8)]
NOT_FOUND = _failed(
    'GET /v8/finance/chart/ANT returned HTTP 404: {"chart":{"result":null,"error":'
    '{"code":"Not Found","description":"No data found, symbol may be delisted"}}}'
)
HOST_CHARTS = {
    "SPCX": _chart("SPCX", SPCX_CLOSES, name="Space Exploration Technologies Corp."),
    "NET": _chart("NET", RISING_CLOSES, name="Cloudflare, Inc."),
    "TTWO": _chart("TTWO", RISING_CLOSES, name="Take-Two Interactive Software, Inc."),
    "NVDA": _chart("NVDA", NVDA_CLOSES, name="NVIDIA Corporation"),
    "GOOGL": _chart("GOOGL", GOOGL_CLOSES, name="Alphabet Inc."),
    "TSLA": _chart("TSLA", TSLA_CLOSES, name="Tesla, Inc."),
    "STRS": _chart("STRS", RISING_CLOSES, name="Stratus Properties Inc."),
    "ANT": NOT_FOUND,
    "SPY": _chart("SPY", RISING_CLOSES, name="State Street SPDR S&P 500 ETF Trust", instrument="ETF"),
    "^GSPC": _chart("^GSPC", SP500_CLOSES, name="S&P 500", instrument="INDEX"),
}
HOST_MATCHES = {
    "SpaceX": _named(
        ("SPCX", "Space Exploration Technologies Corp.", "Equity"),
        ("SSPCX=F", "SpaceX Single Stock futures", "Futures"),
        ("SPCF", "ProShares Ultra SpaceX", "ETF"),
    ),
    "Cloudflare": _named(("NET", "Cloudflare, Inc.", "Equity")),
    "Take-Two Interactive": _named(("TTWO", "Take-Two Interactive Software, Inc.", "Equity")),
    "Nvidia": _named(("NVDA", "NVIDIA Corporation", "Equity"), ("NVDL", "GraniteShares 2x Long NVDA", "ETF")),
    "Nvidia Corporation": _named(("NVDA", "NVIDIA Corporation", "Equity")),
    "Alphabet": _named(
        ("GOOG", "Alphabet Inc.", "Equity"), ("GOOGL", "Alphabet Inc.", "Equity"),
        ("SGOOG=F", "Alphabet Inc Single Stock futures", "Futures"),
    ),
    "Tesla": _named(*TSLA_ROWS),
    "Stripe": _named(
        ("STRIZZX", "Stripe - Company Level", "Fund"),
        ("0P0000X8PD.F", "Prosper Stars & Stripes Fund P EUR", "Fund"),
    ),
    "Anthropic": _named(("ANTH.PVT", "Anthropic PBC", "Private Company")),
    "S&P 500 ETF": _named(("SPY", "State Street SPDR S&P 500 ETF Trust", "ETF")),
}


def _host(monkeypatch, ticker, *, topic="Some Topic", config=None, **install_kwargs):
    """Parse ``--ticker``, check it and attach it, as the engine does after a
    run. Returns the report, what goes into ``resolved``, and the CLI."""
    install_kwargs.setdefault("charts", HOST_CHARTS)
    install_kwargs.setdefault("matches", HOST_MATCHES)
    fake = _install(monkeypatch, **install_kwargs)
    report = _report()
    report.topic = topic
    fields = yahoo_finance.attach_host_tickers(
        report, yahoo_finance.parse_host_tickers(ticker), config,
    )
    return report, fields, fake


@pytest.mark.parametrize(
    "ticker,symbol",
    [
        ("SPCX:SpaceX", "SPCX"),
        ("NET:Cloudflare", "NET"),
        ("TTWO:Take-Two Interactive", "TTWO"),
        ("NVDA:Nvidia", "NVDA"),
        ("nvda: Nvidia ", "NVDA"),
        ("$NVDA:Nvidia", "NVDA"),
    ],
)
def test_host_ticker_is_accepted_when_yahoo_lists_it_first_for_the_company(monkeypatch, ticker, symbol):
    report, fields, fake = _host(monkeypatch, ticker)

    assert report.artifacts["yahoo_finance"]["symbol"] == symbol
    assert fields["ticker"] == symbol
    assert fields["tickers"] == [symbol]
    assert fields["ticker_source"] == "host"
    assert "ticker_rejected" not in fields
    assert "yahoo_finance" not in report.source_status
    # One chart fetch for the symbol: the check reads the fetch the line uses.
    assert fake.charted() == [symbol, "^GSPC"]
    assert len([cmd for cmd in fake.calls if cmd[1] == "autocomplete"]) == 1


def test_host_ticker_is_accepted_when_the_first_stock_is_the_same_company(monkeypatch):
    """Yahoo lists GOOG before GOOGL for "Alphabet"; both are Alphabet Inc."""
    report, fields, _fake = _host(monkeypatch, "GOOGL:Alphabet")

    assert report.artifacts["yahoo_finance"]["symbol"] == "GOOGL"
    assert fields["tickers"] == ["GOOGL"]


@pytest.mark.parametrize(
    "ticker,reason",
    [
        ("STRS:Stripe", "Yahoo lists no stock for 'Stripe'; STRS is Stratus Properties Inc."),
        ("ANT:Anthropic", "Yahoo has no price data for ANT"),
        ("SPY:S&P 500 ETF", "SPY is not a stock on Yahoo (ETF)"),
        (
            "STRS:Tesla",
            "Yahoo's first stock for 'Tesla' is TSLA (Tesla, Inc.); STRS is Stratus Properties Inc.",
        ),
        ("NVDA", "no company name"),
        ("NVDA:", "no company name"),
        (":Nvidia", "not a ticker symbol"),
        ("--query:Nvidia", "not a ticker symbol"),
        ("^GSPC:S&P 500", "not a ticker symbol"),
    ],
    ids=[
        "stripe-is-not-stratus", "filed-but-not-trading", "not-an-equity", "another-company",
        "symbol-alone", "empty-name", "no-symbol", "flag-shaped-symbol", "index",
    ],
)
def test_host_ticker_that_fails_the_check_gives_no_line(monkeypatch, ticker, reason):
    report, fields, _fake = _host(monkeypatch, ticker)

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert "yahoo_finance" not in report.source_status
    assert fields == {
        "ticker_source": "host", "ticker_rejected": [{"pair": ticker, "reason": reason}],
    }


def test_host_ticker_with_no_company_name_never_reaches_the_cli(monkeypatch):
    _report_, _fields, fake = _host(monkeypatch, "NVDA,--help:Nvidia")

    assert fake.calls == []


def test_host_ticker_with_fewer_than_two_closes_is_rejected(monkeypatch):
    report, fields, _fake = _host(
        monkeypatch, "NVDA:Nvidia",
        charts={"NVDA": _chart("NVDA", NVDA_CLOSES[:1], name="NVIDIA Corporation")},
    )

    assert "yahoo_finance" not in report.artifacts
    assert "fewer than two daily closes" in fields["ticker_rejected"][0]["reason"]


@pytest.mark.parametrize(
    "answer,state",
    [
        (
            _failed("GET /v6/finance/autocomplete returned HTTP 429: Too Many Requests", code=7),
            health.RATE_LIMITED,
        ),
        (_failed("GET /v6/finance/autocomplete returned HTTP 500: upstream failure"), health.ERROR),
        ({"meta": {"source": "live"}, "results": {}}, health.SCHEMA_DRIFT),
    ],
    ids=["http-429", "cli-error", "unexpected-shape"],
)
def test_host_ticker_whose_symbol_search_fails_is_a_degraded_source(monkeypatch, answer, state):
    report, fields, _fake = _host(monkeypatch, "NVDA:Nvidia", matches={"Nvidia": answer})

    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert report.source_status["yahoo_finance"].state == state
    assert "Nvidia" in report.source_status["yahoo_finance"].detail
    assert "ticker" not in fields
    assert fields["ticker_rejected"][0]["pair"] == "NVDA:Nvidia"
    assert "not checked" in fields["ticker_rejected"][0]["reason"]


def test_host_ticker_whose_symbol_search_times_out_does_not_raise(monkeypatch):
    def cli(cmd, timeout):
        if cmd[1] == "autocomplete":
            raise yahoo_finance.subproc.SubprocTimeout(f"timed out after {timeout}s")
        return _ok(HOST_CHARTS[cmd[2]])

    monkeypatch.setattr(yahoo_finance, "_is_available", lambda: True)
    monkeypatch.setattr(yahoo_finance.subproc, "run_with_timeout", cli)
    report = _report()

    fields = yahoo_finance.attach_host_tickers(report, yahoo_finance.parse_host_tickers("NVDA:Nvidia"))

    assert "yahoo_finance" not in report.artifacts
    assert "Nvidia" in report.source_status["yahoo_finance"].detail
    assert "ticker" not in fields


def test_host_ticker_whose_chart_is_rate_limited_is_a_degraded_source(monkeypatch):
    report, fields, fake = _host(
        monkeypatch, "NVDA:Nvidia",
        charts={"NVDA": _failed("GET /v8/finance/chart/NVDA returned HTTP 429: Too Many Requests", code=7)},
    )

    assert report.source_status["yahoo_finance"].state == health.RATE_LIMITED
    assert "not checked" in fields["ticker_rejected"][0]["reason"]
    assert fake.calls == [fake.calls[0]]


def test_two_host_tickers_share_one_footer_line_and_both_reach_the_evidence(monkeypatch):
    report, fields, fake = _host(monkeypatch, "TSLA:Tesla,SPCX:SpaceX", topic="Elon Musk")

    assert _footer(report) == [
        "📈 Yahoo Finance: TSLA (Tesla) +0.5% │ SPCX (SpaceX) -14.0% in the L30 │ S&P 500 -3.9%"
    ]
    assert fields == {
        "ticker_source": "host", "ticker": "TSLA", "tickers": ["TSLA", "SPCX"], "company": "Tesla",
    }
    text = render.render_compact(report)
    assert text.count("## Market Data") == 1
    block = text[text.index("## Market Data"):]
    block = block[:block.index("\n## ", 1)]
    assert [line for line in block.splitlines() if line.startswith("- Yahoo Finance daily closes")] == [
        "- Yahoo Finance daily closes: TSLA (Tesla), USD, 2026-10-11 to 2026-11-10",
        "- Yahoo Finance daily closes: SPCX (SpaceX), USD, 2026-10-11 to 2026-11-10",
    ]
    assert sorted(fake.charted()) == ["SPCX", "TSLA", "^GSPC"]


def test_one_accepted_and_one_rejected_host_ticker_prints_the_accepted_one(monkeypatch):
    report, fields, _fake = _host(monkeypatch, "ANT:Anthropic,NVDA:Nvidia", topic="AI labs")

    assert _footer(report)[0].startswith("📈 Yahoo Finance: NVDA (Nvidia) -25.9% in the L30 │ $226.32")
    assert fields["tickers"] == ["NVDA"]
    assert fields["ticker_rejected"] == [
        {"pair": "ANT:Anthropic", "reason": "Yahoo has no price data for ANT"},
    ]


def test_a_third_host_ticker_is_ignored_with_a_log_line(monkeypatch):
    logged = []
    monkeypatch.setattr(yahoo_finance, "_log", logged.append)

    report, fields, fake = _host(monkeypatch, "TSLA:Tesla, SPCX:SpaceX ,NVDA:Nvidia", topic="Elon Musk")

    assert fields["tickers"] == ["TSLA", "SPCX"]
    assert "NVDA" not in fake.charted()
    assert any("NVDA:Nvidia" in line and "ignor" in line for line in logged)


def test_a_repeated_host_symbol_is_used_once(monkeypatch):
    _report_, fields, _fake = _host(monkeypatch, "NVDA:Nvidia,nvda:Nvidia")

    assert fields["tickers"] == ["NVDA"]
    assert fields["ticker_rejected"] == [{"pair": "nvda:Nvidia", "reason": "repeats NVDA"}]


@pytest.mark.parametrize(
    "topic,ticker,shown",
    [
        ("Nvidia", "NVDA:Nvidia", "NVDA -25.9%"),
        ("NVIDIA", "NVDA:Nvidia Corporation", "NVDA -25.9%"),
        ("Jensen Huang", "NVDA:Nvidia", "NVDA (Nvidia) -25.9%"),
        ("Waymo", "GOOGL:Alphabet", "GOOGL (Alphabet) -3.1%"),
    ],
    ids=["same", "same-but-for-case-and-designator", "person", "subsidiary"],
)
def test_footer_names_the_company_only_when_it_is_not_the_topic(monkeypatch, topic, ticker, shown):
    report, _fields, _fake = _host(monkeypatch, ticker, topic=topic)

    assert _footer(report)[0].startswith(f"📈 Yahoo Finance: {shown} in the L30 │ $")


@pytest.mark.parametrize(
    "config,available,why",
    [
        ({"EXCLUDE_SOURCES": "yahoo_finance"}, True, "not checked: yahoo_finance is excluded"),
        ({}, False, "not checked: yahoo-finance-pp-cli is not on PATH"),
    ],
    ids=["excluded", "binary-absent"],
)
def test_host_ticker_makes_no_cli_call_when_the_lane_is_off(monkeypatch, config, available, why):
    report, fields, fake = _host(monkeypatch, "NVDA:Nvidia", config=config, available=available)

    assert fake.calls == []
    assert _footer(report) == []
    assert "yahoo_finance" not in report.artifacts
    assert fields == {
        "ticker_source": "host", "ticker_rejected": [{"pair": "NVDA:Nvidia", "reason": why}],
    }


def test_excluded_lane_records_no_outcome_for_a_host_ticker(monkeypatch):
    report, _fields, _fake = _host(
        monkeypatch, "NVDA:Nvidia", config={"EXCLUDE_SOURCES": "yahoo_finance"},
    )

    assert "yahoo_finance" not in report.source_status


def test_missing_helper_with_a_host_ticker_says_so_once_with_the_install_command(monkeypatch, capsys):
    report, _fields, _fake = _host(monkeypatch, "NVDA:Nvidia,MSFT:Microsoft", available=False)

    outcome = report.source_status["yahoo_finance"]
    assert outcome.state == health.SKIPPED_UNCONFIGURED
    assert outcome.detail == (
        f"yahoo-finance-pp-cli is not on PATH; install: {health.pp_install_cmd('yahoo-finance')}"
    )
    assert len(outcome.detail) <= 140
    logged = [line for line in capsys.readouterr().err.splitlines() if "yahoo-finance-pp-cli" in line]
    assert len(logged) == 1 and "--ticker not checked" in logged[0]


def test_missing_helper_without_a_host_ticker_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(yahoo_finance, "_is_available", lambda: False)
    report = _report()

    yahoo_finance.attach_host_tickers(report, [])

    assert "yahoo_finance" not in report.source_status
    assert "yahoo-finance-pp-cli" not in capsys.readouterr().err


def test_resolved_row_names_the_host_search_and_keeps_the_other_wordings():
    from tests.test_company_resolve import _report as resolved_report

    rows = render._render_resolved_entities_block([
        ("Elon Musk", resolved_report("Elon Musk", {
            "company": "Tesla", "ticker": "TSLA", "tickers": ["TSLA", "SPCX"], "ticker_source": "host",
        })),
        ("Jensen Huang", resolved_report("Jensen Huang", {
            "company": "Nvidia", "ticker": "NVDA", "company_via_person": True, "ticker_source": "wikidata",
        })),
        ("Tesla", resolved_report("Tesla", {
            "company": "Tesla", "ticker": "TSLA", "company_source": "yahoo-backup",
            "ticker_source": "yahoo-backup",
        })),
        ("Stripe", resolved_report("Stripe", {
            "ticker_source": "host", "ticker_rejected": [{"pair": "STRS:Stripe", "reason": "no"}],
        })),
    ])

    assert rows[2] == "- **Elon Musk**: X - | Subs - | GitHub - | Ticker TSLA, SPCX (host search) | Context: -"
    assert "Ticker NVDA (via Nvidia)" in rows[3]
    assert "Ticker TSLA (Yahoo match)" in rows[4]
    assert "Ticker" not in rows[5]


# Resolution order in the engine.


def _wikidata_asked_for_a_listing(wikidata) -> bool:
    return any(
        "Q82059" in str((call.get("params") or {}).get("ids", "")) for call in wikidata.calls
    )


def test_ticker_source_is_recorded_for_wikidata_and_the_backup(monkeypatch):
    company, _fake, wikidata = _company_after(monkeypatch, "Nvidia", error=None)
    assert (company["ticker"], company["ticker_source"]) == ("NVDA", "wikidata")
    assert _wikidata_asked_for_a_listing(wikidata)

    backup, _fake, _wikidata = _company_after(monkeypatch, "Tesla", matches={"Tesla": _named(*TSLA_ROWS)})
    assert backup["ticker_source"] == "yahoo-backup"


def test_host_ticker_keeps_the_wikidata_company_and_asks_it_for_no_listing(monkeypatch):
    fake = _install(monkeypatch, charts=HOST_CHARTS, matches=HOST_MATCHES)
    wikidata = FakeWikidata()
    monkeypatch.setattr(company_resolve.http, "get", wikidata)

    company = cli._resolved_company("Nvidia", mock=False, config={}, host_ticker=True)

    assert company["company"] == "Nvidia"
    assert company["company_site"]
    assert not {"ticker", "exchange", "ticker_candidates", "ticker_source"} & set(company)
    assert wikidata.calls
    assert not _wikidata_asked_for_a_listing(wikidata)
    assert fake.calls == []


def test_host_ticker_never_reaches_the_yahoo_backup_when_wikidata_fails(monkeypatch):
    fake = _install(monkeypatch, charts=HOST_CHARTS, matches=HOST_MATCHES)
    monkeypatch.setattr(company_resolve.http, "get", FakeWikidata(error=RATE_LIMITED))

    company = cli._resolved_company("Tesla", mock=False, config={}, host_ticker=True)

    assert company == {}
    assert fake.calls == []


def _host_run(monkeypatch, tmp_path, ticker, *, charts, matches=HOST_MATCHES, **kwargs):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=charts, matches=matches)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, competitors_list=None,
        expected_topics=frozenset({"MainBrand"}),
        args=["--ticker", ticker, *kwargs.pop("args", [])], **kwargs,
    )
    return observed, fake


_JUNE_HOST_CHARTS = {
    "NVDA": _chart("NVDA", _JUNE, name="NVIDIA Corporation"),
    "STRS": _chart("STRS", _JUNE, name="Stratus Properties Inc."),
    "MAIN": _chart("MAIN", _JUNE, name="MainBrand, Inc."),
    "^GSPC": _chart("^GSPC", _JUNE),
}


def test_engine_run_uses_the_host_ticker_and_not_the_wikidata_one(monkeypatch, tmp_path):
    observed, fake = _host_run(
        monkeypatch, tmp_path, "NVDA:Nvidia", charts=_JUNE_HOST_CHARTS,
        companies={"MainBrand": {
            "company": "MainBrand Inc.", "company_site": "https://mainbrand.example/",
        }},
    )

    report = observed.reports["MainBrand"]
    assert report.artifacts["yahoo_finance"]["symbol"] == "NVDA"
    resolved = report.artifacts["resolved"]
    assert (resolved["ticker"], resolved["tickers"], resolved["ticker_source"]) == ("NVDA", ["NVDA"], "host")
    # Wikidata still names the company, and is asked for no listing.
    assert resolved["company"] == "MainBrand Inc."
    assert resolved["company_site"] == "https://mainbrand.example/"
    observed.company_resolver.assert_called_once_with("MainBrand", listing=False, names=True)
    assert fake.charted() == ["NVDA", "^GSPC"]


def test_engine_run_fills_the_company_from_the_host_when_wikidata_names_none(monkeypatch, tmp_path):
    observed, _fake = _host_run(monkeypatch, tmp_path, "NVDA:Nvidia", charts=_JUNE_HOST_CHARTS)

    assert observed.reports["MainBrand"].artifacts["resolved"]["company"] == "Nvidia"


def test_engine_run_with_every_host_ticker_rejected_prints_nothing_and_falls_back_to_nothing(
    monkeypatch, tmp_path,
):
    observed, fake = _host_run(
        monkeypatch, tmp_path, "STRS:Stripe", charts=_JUNE_HOST_CHARTS,
        matches={**HOST_MATCHES, "MainBrand": _named(("MAIN", "MainBrand, Inc.", "Equity"))},
        # What Wikidata would say with its listing; the run must not use it.
        companies={"MainBrand": company_resolve.FailedLookup()},
    )

    report = observed.reports["MainBrand"]
    assert "yahoo_finance" not in report.artifacts
    assert _footer(report) == []
    resolved = report.artifacts["resolved"]
    assert not {"ticker", "tickers", "company", "company_source"} & set(resolved)
    assert resolved["ticker_source"] == "host"
    assert resolved["ticker_rejected"] == [{
        "pair": "STRS:Stripe",
        "reason": "Yahoo lists no stock for 'Stripe'; STRS is Stratus Properties Inc.",
    }]
    observed.company_resolver.assert_called_once_with("MainBrand", listing=False, names=True)
    # No symbol search for the topic: the Yahoo backup is not a fallback here.
    assert [cmd[3] for cmd in fake.calls if cmd[1] == "autocomplete"] == ["Stripe"]
    assert fake.charted() == ["STRS"]


def test_engine_run_without_the_flag_resolves_as_before(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(charts=_JUNE_CHARTS)
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, competitors_list=None,
        expected_topics=frozenset({"MainBrand"}), companies=_LISTED,
    )

    observed.company_resolver.assert_called_once_with("MainBrand", names=True)
    resolved = observed.reports["MainBrand"].artifacts["resolved"]
    assert (resolved["ticker"], resolved["ticker_source"]) == ("MAIN", "wikidata")
    assert "tickers" not in resolved and "ticker_rejected" not in resolved
    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"


def test_engine_run_with_an_empty_ticker_flag_resolves_as_before(monkeypatch, tmp_path):
    observed, _fake = _host_run(monkeypatch, tmp_path, " ", charts=_JUNE_CHARTS, companies=_LISTED)

    observed.company_resolver.assert_called_once_with("MainBrand", names=True)
    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "MAIN"


def test_mock_run_with_a_host_ticker_never_starts_the_cli(monkeypatch, tmp_path):
    observed, fake = _host_run(monkeypatch, tmp_path, "NVDA:Nvidia", charts=_JUNE_HOST_CHARTS, args=["--mock"])

    assert fake.calls == []
    assert observed.company_resolver.call_count == 0
    assert "yahoo_finance" not in observed.reports["MainBrand"].artifacts
    assert "ticker_source" not in observed.reports["MainBrand"].artifacts["resolved"]


def test_engine_run_with_yahoo_finance_excluded_checks_no_host_ticker(monkeypatch, tmp_path):
    observed, fake = _host_run(
        monkeypatch, tmp_path, "NVDA:Nvidia", charts=_JUNE_HOST_CHARTS,
        config={"EXCLUDE_SOURCES": "yahoo_finance"},
    )

    assert fake.calls == []
    report = observed.reports["MainBrand"]
    assert "yahoo_finance" not in report.artifacts and "yahoo_finance" not in report.source_status
    assert "ticker" not in report.artifacts["resolved"]


def test_engine_run_without_the_binary_checks_no_host_ticker(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    observed = run_competitor_cli(
        monkeypatch, tmp_path, competitors_list=None, expected_topics=frozenset({"MainBrand"}),
        args=["--ticker", "NVDA:Nvidia"],
    )

    report = observed.reports["MainBrand"]
    assert "yahoo_finance" not in report.artifacts
    assert report.source_status["yahoo_finance"].state == health.SKIPPED_UNCONFIGURED
    assert "install:" in report.source_status["yahoo_finance"].detail
    assert "ticker" not in report.artifacts["resolved"]


def test_comparison_gives_the_host_ticker_to_the_main_entity_only(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    fake = FakeCli(
        charts={**_JUNE_HOST_CHARTS, "PEER": _chart("PEER", _JUNE)}, matches=HOST_MATCHES,
    )
    observed = run_competitor_cli(
        monkeypatch, tmp_path, market_cli=fake, companies=_LISTED, args=["--ticker", "NVDA:Nvidia"],
    )

    assert observed.reports["MainBrand"].artifacts["yahoo_finance"]["symbol"] == "NVDA"
    assert observed.reports["PeerOne"].artifacts["yahoo_finance"]["symbol"] == "PEER"
    assert observed.reports["PeerOne"].artifacts["resolved"]["ticker_source"] == "wikidata"


# The host's instruction and the byte budgets it has to fit.


def test_runbook_tells_the_host_to_find_the_ticker_through_the_isolated_search():
    from tests.skill_contract import SKILL_ROOT, reference_text

    runbook = reference_text("research-runbook")
    section = runbook.split("### Section B: Resolve the Stock Ticker", 1)[1].split("\n### ", 1)[0]
    assert '--ticker="SYMBOL:Company"' in section
    assert "isolated web search" in section.lower()
    assert "listed and trading" in section
    assert "pass nothing" in section
    # Next to the X handle section, in the checklist and in the engine's flag list.
    assert runbook.index("### Section A: Resolve X Handles") < runbook.index("### Section B: Resolve the Stock Ticker")
    assert runbook.index("### Section B: Resolve the Stock Ticker") < runbook.index("### Step 0.5b")
    checklist = runbook.split("| Flag | Resolved in | Applies when |", 1)[1].split("**Checkpoint", 1)[0]
    assert "`--ticker=" in checklist
    flags = runbook.split("Add these targeting flags to that engine command", 1)[1].split("**If you skipped", 1)[0]
    assert "`--ticker=" in flags

    root = (SKILL_ROOT / "SKILL.md").read_bytes()
    assert len(root) <= 32_000
    names = ("runtime", "setup-wizard", "research-runbook", "synthesis")
    total = len(root) + sum(len(reference_text(name).encode("utf-8")) for name in names)
    assert total <= 190_500
