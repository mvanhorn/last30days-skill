"""Next30Days: the forecast ledger validates, grades once, settles, and scores."""

import datetime as dt
import io
import json
import sys
from unittest import mock

import pytest

import last30days as cli
import store
from lib import forecast
from tests.skill_contract import reference_text

TODAY = dt.date(2026, 11, 9)
EVENT_SLUG = "widget-co-acquired-in-2026"
MARKET_SLUG = "will-widget-co-be-acquired-in-2026"
MARKET_URL = f"https://polymarket.com/event/{EVENT_SLUG}/{MARKET_SLUG}"


def _prediction(**overrides):
    entry = {
        "claim": "Widget Co publishes a stable 2.0 release on GitHub",
        "probability": 0.7,
        "resolve_by": "2026-12-09",
        "resolution": "A non-prerelease v2.0.0 tag is on the releases page",
        "basis": ["https://example.com/thread/1"],
    }
    entry.update(overrides)
    return entry


def _payload(*predictions):
    return {"topic": "Widget Co", "predictions": list(predictions or [_prediction()])}


def _event(*, closed=True, prices=("0", "1"), created="2026-07-01T00:00:00Z",
           closed_time="2026-12-01 10:00:00+00", slug=EVENT_SLUG, extra_markets=0):
    markets = [{
        "slug": MARKET_SLUG, "question": "Will Widget Co be acquired in 2026?",
        "closed": closed, "outcomes": '["Yes", "No"]',
        "outcomePrices": json.dumps(list(prices)), "closedTime": closed_time,
    }]
    markets += [
        {"slug": f"other-{i}", "question": "Other", "closed": True,
         "outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]'}
        for i in range(extra_markets)
    ]
    return [{"slug": slug, "createdAt": created, "markets": markets}]


def _market_row(**overrides):
    row = {
        "id": 1, "created_on": "2026-11-09", "created_at": "2026-11-09T12:00:00+00:00",
        "resolve_by": "2026-12-31",
        "market_url": MARKET_URL, "market_outcome": "No", "verdict": None,
    }
    row.update(overrides)
    return row


# --- validation ---------------------------------------------------------------


def test_valid_prediction_becomes_a_ledger_row():
    [row] = forecast.validate_predictions(_payload(), TODAY)
    assert row["topic"] == "Widget Co"
    assert row["created_on"] == "2026-11-09"
    assert forecast._parse_instant(row["created_at"]) is not None
    stamped = forecast.validate_predictions(
        _payload(), TODAY, now=dt.datetime(2026, 11, 9, 7, 30, tzinfo=dt.timezone(dt.timedelta(hours=-8))),
    )[0]
    assert stamped["created_at"] == "2026-11-09T15:30:00+00:00"
    assert row["resolve_by"] == "2026-12-09"
    assert row["market_url"] is None


def test_resolve_by_defaults_to_thirty_days():
    entry = _prediction()
    del entry["resolve_by"]
    [row] = forecast.validate_predictions(_payload(entry), TODAY)
    assert row["resolve_by"] == "2026-12-09"


@pytest.mark.parametrize("overrides,message", [
    ({"probability": 0.4}, "between 0.5 and 0.99"),
    ({"probability": 1.0}, "between 0.5 and 0.99"),
    ({"probability": True}, "must be a number"),
    ({"probability": "0.7"}, "must be a number"),
    ({"resolve_by": "2026-11-09"}, "must be after today"),
    ({"resolve_by": "2027-06-01"}, "more than 90 days"),
    ({"resolve_by": "next month"}, "YYYY-MM-DD"),
    ({"claim": "Up"}, "claim is too short"),
    ({"claim": "x" * 400}, "claim is too long"),
    ({"resolution": ""}, "resolution is too short"),
    ({"basis": []}, "at least one evidence URL"),
    ({"basis": ["not a url"]}, "http(s) URL"),
    ({"basis": ["javascript:alert(1)"]}, "http(s) URL"),
    ({"basis": ["https://[invalid"]}, "http(s) URL"),
    ({"basis": ["https://example.com/a"] * 7}, "at most 6"),
    ({"market": {"url": "https://example.com/event/x", "outcome": "Yes"}}, "Polymarket event URL"),
    ({"market": {"url": MARKET_URL}}, "market.outcome"),
    ({"market": {"url": MARKET_URL, "outcome": "No", "price": 1.4}}, "market.price"),
])
def test_unfalsifiable_or_ungrounded_predictions_are_refused(overrides, message):
    with pytest.raises(forecast.ForecastError, match=message.replace("(", r"\(").replace(")", r"\)")):
        forecast.validate_predictions(_payload(_prediction(**overrides)), TODAY)


def test_file_level_refusals():
    with pytest.raises(forecast.ForecastError, match="non-empty list"):
        forecast.validate_predictions({"topic": "Widget Co", "predictions": []}, TODAY)
    with pytest.raises(forecast.ForecastError, match="at most 5"):
        forecast.validate_predictions(
            _payload(*[_prediction(claim=f"Widget Co ships release number {i}") for i in range(6)]),
            TODAY,
        )
    with pytest.raises(forecast.ForecastError, match="duplicates"):
        forecast.validate_predictions(_payload(_prediction(), _prediction()), TODAY)
    with pytest.raises(forecast.ForecastError, match="topic"):
        forecast.validate_predictions({"predictions": [_prediction()]}, TODAY)
    with pytest.raises(forecast.ForecastError, match="not valid JSON"):
        forecast.parse_payload("{nope")
    with pytest.raises(forecast.ForecastError, match="JSON object"):
        forecast.parse_payload("[]")
    with pytest.raises(forecast.ForecastError, match="exceeds"):
        forecast.parse_payload(" " * (forecast.MAX_PAYLOAD_BYTES + 1))


def test_market_url_parsing():
    assert forecast.parse_market_url(MARKET_URL) == (EVENT_SLUG, MARKET_SLUG)
    assert forecast.parse_market_url(f"https://www.polymarket.com/event/{EVENT_SLUG}/") == (EVENT_SLUG, None)
    with pytest.raises(forecast.ForecastError):
        forecast.parse_market_url("https://polymarket.com.evil.test/event/x")


def test_grade_validation():
    grades = forecast.validate_grades({"grades": [
        {"id": 1, "verdict": "hit", "evidence_url": "https://example.com/proof"},
        {"id": 2, "verdict": "void", "note": "Product was renamed"},
    ]})
    assert [g["verdict"] for g in grades] == ["hit", "void"]
    for bad, message in [
        ({"id": 1, "verdict": "hit"}, "evidence_url"),
        ({"id": 1, "verdict": "miss", "evidence_url": "ftp://x"}, "evidence_url"),
        ({"id": 1, "verdict": "void"}, "needs a note"),
        ({"id": 1, "verdict": "maybe"}, "verdict must be"),
        ({"id": "1", "verdict": "hit", "evidence_url": "https://e.com"}, "id must be"),
        ({"id": True, "verdict": "hit", "evidence_url": "https://e.com"}, "id must be"),
    ]:
        with pytest.raises(forecast.ForecastError, match=message):
            forecast.validate_grades({"grades": [bad]})
    with pytest.raises(forecast.ForecastError, match="graded twice"):
        forecast.validate_grades({"grades": [
            {"id": 1, "verdict": "hit", "evidence_url": "https://e.com"},
            {"id": 1, "verdict": "miss", "evidence_url": "https://e.com"},
        ]})
    with pytest.raises(forecast.ForecastError, match="non-empty list"):
        forecast.validate_grades({})


def test_grade_refusal_rules():
    row = {"id": 3, "resolve_by": "2026-12-09", "verdict": None}
    miss = {"id": 3, "verdict": "miss"}
    hit = {"id": 3, "verdict": "hit"}
    assert "no prediction #3" in forecast.grade_refusal(None, miss, TODAY)
    assert "before its deadline" in forecast.grade_refusal(row, miss, TODAY)
    assert forecast.grade_refusal(row, hit, TODAY) is None
    assert forecast.grade_refusal(row, miss, dt.date(2026, 12, 9)) is None
    graded = {**row, "verdict": "hit", "graded_on": "2026-11-20"}
    assert "written once" in forecast.grade_refusal(graded, miss, dt.date(2026, 12, 10))


# --- market settlement ----------------------------------------------------------


def test_resolved_market_settles_hit_and_miss():
    assert forecast.settle_from_event(_market_row(), _event())[0] == "hit"
    verdict, note = forecast.settle_from_event(_market_row(market_outcome="yes"), _event())
    assert verdict == "miss" and "to No" in note
    assert forecast.settle_from_event(_market_row(), {"events": _event()})[0] == "hit"


def test_refunded_market_is_void_not_a_miss():
    assert forecast.settle_from_event(_market_row(), _event(prices=("0.5", "0.5")))[0] == "void"


def test_market_already_closed_when_logged_is_void():
    verdict, note = forecast.settle_from_event(
        _market_row(), _event(closed_time="2026-08-08 20:31:58+00"),
    )
    assert verdict == "void" and "already closed" in note


@pytest.mark.parametrize("closed_time,row_overrides,expected", [
    # Same day, but the market closed four hours before the call was logged.
    ("2026-11-09 08:00:00+00", {}, "void"),
    ("2026-11-09T12:00:00Z", {}, "void"),
    # Same day, closed after logging: a real forecast.
    ("2026-11-09 18:00:00+00", {}, "hit"),
    # Order within the day unknowable: treat a same-day close as known.
    ("2026-11-09", {}, "void"),
    ("2026-11-09 18:00:00+00", {"created_at": None}, "void"),
    ("2026-11-10 00:00:01+00", {"created_at": None}, "hit"),
    ("", {}, "hit"),
])
def test_same_day_known_results_do_not_count(closed_time, row_overrides, expected):
    result = forecast.settle_from_event(
        _market_row(**row_overrides), _event(closed_time=closed_time),
    )
    assert result[0] == expected


def test_market_miss_is_final_before_the_deadline():
    row = _market_row(market_outcome="Yes", resolve_by="2026-12-31")
    verdict, _ = forecast.settle_from_event(row, _event(closed_time="2026-11-20 10:00:00+00"))
    assert verdict == "miss"


@pytest.mark.parametrize("row,events", [
    (_market_row(), _event(closed=False)),
    (_market_row(), _event(slug="some-other-event")),
    (_market_row(), _event(created="2026-11-20T00:00:00Z")),
    (_market_row(market_outcome="Maybe"), _event()),
    (_market_row(), _event(prices=("0", "oops"))),
    (_market_row(), _event(prices=("1",))),
    (_market_row(market_url=f"https://polymarket.com/event/{EVENT_SLUG}"), _event(extra_markets=2)),
    (_market_row(market_url=f"https://polymarket.com/event/{EVENT_SLUG}/missing"), _event()),
    (_market_row(), []),
    (_market_row(), "garbage"),
])
def test_market_that_cannot_be_trusted_leaves_the_prediction_open(row, events):
    assert forecast.settle_from_event(row, events) is None


def test_single_market_event_settles_without_a_market_slug():
    row = _market_row(market_url=f"https://polymarket.com/event/{EVENT_SLUG}")
    assert forecast.settle_from_event(row, _event())[0] == "hit"


def test_settle_reports_fetch_failures_instead_of_calling_them_open():
    def fetch(slug):
        raise RuntimeError("gamma down")

    rows = [_market_row(), _market_row(id=2, verdict="hit"), {"id": 3, "verdict": None, "market_url": None}]
    settled, unreachable = forecast.settle_open_markets(rows, fetch)
    assert settled == []
    assert [(row["id"], reason) for row, reason in unreachable] == [(1, "RuntimeError: gamma down")]


# --- scorecard --------------------------------------------------------------------


def _scored(probability, verdict, **extra):
    return {"probability": probability, "verdict": verdict, "resolve_by": "2026-10-01", **extra}


def test_scorecard_math():
    rows = [
        _scored(0.9, "hit", market_price=0.6),
        _scored(0.9, "miss", market_price=0.6),
        _scored(0.6, "hit"),
        _scored(0.7, "void"),
        _scored(0.7, None),
        {"probability": 0.8, "verdict": None, "resolve_by": "2026-12-25"},
    ]
    card = forecast.scorecard(rows, TODAY)
    assert (card["total"], card["open"], card["overdue"], card["graded"]) == (6, 2, 1, 3)
    assert (card["hits"], card["misses"], card["voids"]) == (2, 1, 1)
    assert card["hit_rate"] == pytest.approx(2 / 3)
    assert card["brier"] == pytest.approx((0.01 + 0.81 + 0.16) / 3)
    assert card["small_sample"] is True
    assert card["market_count"] == 2
    assert card["brier_vs_market_ours"] == pytest.approx(0.41)
    assert card["brier_vs_market_theirs"] == pytest.approx(0.26)
    by_low = {bucket["low"]: bucket for bucket in card["calibration"]}
    assert by_low[0.9]["count"] == 2 and by_low[0.9]["actual"] == 0.5
    text = forecast.render_scorecard(card, TODAY)
    assert "Hit rate: 67% (2 of 3)" in text
    assert "Overdue and ungraded: 1" in text
    assert "Small sample" in text
    assert "worse than the market" in text
    assert "—" not in text and "–" not in text


def test_scorecard_never_invents_a_record():
    empty = forecast.render_scorecard(forecast.scorecard([], TODAY), TODAY)
    assert "No predictions logged yet" in empty and "Hit rate" not in empty
    ungraded = forecast.render_scorecard(forecast.scorecard([_scored(0.7, None)], TODAY), TODAY)
    assert "Nothing graded yet" in ungraded and "Hit rate" not in ungraded
    voided = forecast.scorecard([_scored(0.7, "void")], TODAY)
    assert voided["hit_rate"] is None and voided["brier"] is None


def test_market_comparison_wording():
    better = forecast.scorecard([_scored(0.9, "hit", market_price=0.6)], TODAY)
    assert "better than the market" in forecast.render_scorecard(better, TODAY)
    level = forecast.scorecard([_scored(0.9, "hit", market_price=0.9)], TODAY)
    assert "level with the market" in forecast.render_scorecard(level, TODAY)


# --- store: append-only ledger ------------------------------------------------------


@pytest.fixture
def ledger(tmp_path):
    with store.scoped_db(tmp_path / "research.db"):
        yield tmp_path / "research.db"


def test_ledger_logs_once_and_grades_once(ledger):
    [row] = forecast.validate_predictions(_payload(), TODAY)
    stored, created = store.add_forecast(row)
    assert created and stored["id"] == 1 and stored["basis"] == ["https://example.com/thread/1"]

    # A retry with a rewritten probability must not replace the original call.
    again, created_again = store.add_forecast({**row, "probability": 0.95, "claim": row["claim"].upper() + "!"})
    assert not created_again and again["id"] == 1 and again["probability"] == 0.7

    assert store.list_forecasts("widget co!")[0]["id"] == 1
    assert store.list_forecasts("someone else") == []
    assert store.get_forecast(99) is None

    [graded] = store.grade_forecasts(
        [{"id": 1, "verdict": "hit", "evidence_url": "https://example.com/proof"}],
        "2026-11-20", "host",
    )
    assert graded["verdict"] == "hit" and graded["graded_by"] == "host"
    with pytest.raises(store.ForecastGradeConflict):
        store.grade_forecasts([{"id": 1, "verdict": "miss"}], "2026-12-10", "host")
    assert store.get_forecast(1)["verdict"] == "hit"
    with pytest.raises(store.ForecastGradeConflict):
        store.grade_forecasts([{"id": 99, "verdict": "hit"}], "2026-11-20", "host")


def test_grade_batch_is_all_or_nothing(ledger):
    for claim in ("Widget Co ships release one", "Widget Co ships release two", "Widget Co ships release three"):
        [row] = forecast.validate_predictions(_payload(_prediction(claim=claim)), TODAY)
        store.add_forecast(row)
    # Prediction 2 gets settled by another run after the caller's checks.
    store.grade_forecasts([{"id": 2, "verdict": "void", "note": "settled elsewhere"}], "2026-11-15", "market")
    batch = [{"id": n, "verdict": "hit", "evidence_url": "https://example.com/p"} for n in (1, 2, 3)]
    with pytest.raises(store.ForecastGradeConflict) as conflict:
        store.grade_forecasts(batch, "2026-11-20", "host")
    assert conflict.value.forecast_id == 2
    assert store.get_forecast(1)["verdict"] is None
    assert store.get_forecast(3)["verdict"] is None


def test_ledger_has_no_edit_or_delete_surface():
    names = {name for name in dir(store) if "forecast" in name and not name.startswith("_")}
    assert names == {"add_forecast", "get_forecast", "list_forecasts", "grade_forecasts"}


# --- engine commands ------------------------------------------------------------------


def _run(monkeypatch, capsys, argv, stdin=None):
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(cli.pipeline, "run", mock.Mock(side_effect=AssertionError("research ran")))
    try:
        rc = cli.main()
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    return rc, out.out, out.err


def _future(days):
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _live_payload():
    return json.dumps(_payload(
        _prediction(resolve_by=_future(30)),
        _prediction(
            claim="The Widget Co acquisition market resolves No",
            probability=0.9, resolve_by=_future(40),
            resolution="The linked Polymarket market resolves No",
            market={"url": MARKET_URL, "outcome": "No", "price": 0.8},
        ),
    ))


def test_read_commands_on_an_empty_ledger_create_nothing(monkeypatch, capsys, tmp_path):
    for command, expected in [
        ("scorecard", "No predictions logged yet"),
        ("due", "Due for grading: none."),
        ("list", "Forecast ledger: none."),
    ]:
        rc, out, _ = _run(monkeypatch, capsys, ["forecast", command, "--save-dir", str(tmp_path)])
        assert rc == 0 and expected in out
    assert not (tmp_path / "research.db").exists()


def test_add_settle_grade_scorecard_round_trip(monkeypatch, capsys, tmp_path):
    save = ["--save-dir", str(tmp_path)]
    rc, out, err = _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save], stdin=_live_payload())
    assert rc == 0, err
    assert "Logged to the forecast ledger (2)" in out and "#1 [open" in out and "#2 [open" in out
    assert (tmp_path / "research.db").stat().st_mode & 0o077 == 0

    rc, out, _ = _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save], stdin=_live_payload())
    assert rc == 0 and "Already in the ledger" in out and "Logged to" not in out

    today = dt.date.today().isoformat()
    events = _event(closed_time=f"{_future(5)} 10:00:00+00", created="2026-01-01T00:00:00Z")
    with mock.patch.object(cli, "_fetch_polymarket_events", return_value=events) as fetch:
        rc, out, _ = _run(monkeypatch, capsys, ["forecast", "settle", *save])
    fetch.assert_called_once_with(EVENT_SLUG)
    assert rc == 0 and "1 settled, 0 unreachable, 0 still open" in out
    assert f"#2 [HIT (due {_future(40)}, graded {today} by market)]" in out

    # A miss before the deadline is refused and writes nothing.
    grade_file = tmp_path / "grades.json"
    grade_file.write_text(json.dumps({"grades": [
        {"id": 1, "verdict": "miss", "evidence_url": "https://example.com/no"},
    ]}))
    rc, out, err = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", str(grade_file), *save])
    assert rc == 2 and "before its deadline" in err and "nothing was written" in err

    grade_file.write_text(json.dumps({"grades": [
        {"id": 1, "verdict": "hit", "evidence_url": "https://example.com/release"},
    ]}))
    rc, out, _ = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", str(grade_file), *save])
    assert rc == 0 and "Hit rate: 100% (2 of 2)" in out and "Small sample" in out

    rc, _, err = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", str(grade_file), *save])
    assert rc == 2 and "written once" in err

    # A concurrent settle between the checks and the write rolls the file back.
    with mock.patch.object(forecast, "grade_refusal", return_value=None):
        rc, _, err = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", str(grade_file), *save])
    assert rc == 2 and "unknown or already graded; nothing was written" in err

    rc, out, _ = _run(monkeypatch, capsys, ["forecast", "list", "Widget", "Co", *save])
    assert rc == 0 and "Forecast ledger for 'Widget Co' (2)" in out
    rc, out, _ = _run(monkeypatch, capsys, ["forecast", "scorecard", *save])
    assert rc == 0 and "Against the market" in out


def test_settle_outage_is_reported_and_leaves_the_call_open(monkeypatch, capsys, tmp_path):
    save = ["--save-dir", str(tmp_path)]
    _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save], stdin=_live_payload())
    with mock.patch.object(cli, "_fetch_polymarket_events", side_effect=RuntimeError("gamma down")):
        rc, out, err = _run(monkeypatch, capsys, ["forecast", "settle", *save])
    assert rc == 0 and "0 settled, 1 unreachable, 0 still open" in out
    assert "could not check prediction #2" in err


def test_bad_input_exits_two_without_writing(monkeypatch, capsys, tmp_path):
    save = ["--save-dir", str(tmp_path)]
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "add", *save])
    assert rc == 2 and "requires --forecast-file" in err
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", str(tmp_path / "missing.json"), *save])
    assert rc == 2 and "forecast add:" in err
    bad = json.dumps(_payload(_prediction(probability=0.2)))
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save], stdin=bad)
    assert rc == 2 and "between 0.5 and 0.99" in err
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", "-", *save], stdin="{}")
    assert rc == 2 and "non-empty list" in err
    assert not (tmp_path / "research.db").exists()


@pytest.mark.parametrize("topic,expected", [
    ("forecast scorecard", ("scorecard", 18)),
    ("forecast list widget co", ("list", 13)),
    ("forecast add", ("add", 12)),
    ("forecast", None),
    ("forecast the election", None),
    ("forecast add ons for shopify", None),
    ("weather forecast list", None),
])
def test_only_exact_forecast_commands_are_reserved(topic, expected):
    assert cli._forecast_command(topic) == expected


@pytest.mark.parametrize("argv", [
    ["widget co", "--mock"],
    ["forecast", "list"],
    ["forecast", "due"],
    ["forecast", "settle"],
    ["forecast", "scorecard"],
    ["queue", "list"],
    ["library", "search", "widget"],
    ["doctor"],
])
def test_forecast_file_is_never_accepted_and_ignored(monkeypatch, capsys, tmp_path, argv):
    with mock.patch.object(cli, "_fetch_polymarket_events", side_effect=AssertionError("network")):
        rc, out, err = _run(monkeypatch, capsys, [*argv, "--forecast-file", "-", "--save-dir", str(tmp_path)])
    assert rc == 2 and "only applies to 'forecast add'" in err and not out
    assert not (tmp_path / "research.db").exists()


def test_forecast_commands_never_read_browser_cookies():
    parser = cli.build_parser()
    args = parser.parse_args(["forecast", "settle"])
    assert cli._config_policy_for_args(args, "forecast settle", []).browser_cookies == "plan_only"


# --- skill contract ---------------------------------------------------------------------


def test_reference_teaches_every_command_and_the_honesty_rules():
    text = reference_text("forecast")
    for command in cli.FORECAST_COMMANDS:
        assert f"forecast {command}" in text
    assert "--forecast-file -" in text
    for rule in (
        "Relay it verbatim",
        "A verdict is written once",
        "A wrong prediction is a `miss`",
        "Never anchor to a market that has already resolved",
        "not a recommendation",
    ):
        assert rule in text
    assert "—" not in text and "–" not in text
    for block in text.split("<<'")[1:]:
        body = block.split("\n", 1)[1].split("_EOF", 1)[0].rsplit("\n", 1)[0]
        json.loads(body)
