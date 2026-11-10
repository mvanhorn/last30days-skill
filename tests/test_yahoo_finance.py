"""Share-price window from yahoo-finance-pp-cli: ticker choice, numbers, footer.

No test shells out. ``FakeCli`` stands in for ``subproc.run_with_timeout`` and
answers ``chart`` and ``autocomplete`` in the shapes the real binary returned
for NVDA, 7203.T, ^GSPC, Tesla and Stripe.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lib import health, render, schema, yahoo_finance
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


def _chart(symbol, closes, *, currency="USD", gmtoffset=-18000, hour=14):
    """A chart response; each bar is stamped at the exchange's opening hour."""
    stamps = [
        int(datetime.fromisoformat(f"{day}T{hour:02d}:30:00+00:00").timestamp())
        for day, _close in closes
    ]
    return {
        "meta": {"source": "live"},
        "results": {"chart": {"result": [{
            "meta": {
                "currency": currency, "symbol": symbol, "gmtoffset": gmtoffset,
                "instrumentType": "EQUITY", "fullExchangeName": "NasdaqGS",
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
