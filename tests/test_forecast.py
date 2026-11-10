"""Next30Days: the forecast ledger validates, grades once, settles, and scores."""

import datetime as dt
import io
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import mock

import pytest

import last30days as cli
import store
from lib import forecast, open_questions
from tests.skill_contract import reference_text

TODAY = dt.date(2026, 11, 9)
EVENT_SLUG = "widget-co-acquired-in-2026"
MARKET_SLUG = "will-widget-co-be-acquired-in-2026"
MARKET_URL = f"https://polymarket.com/event/{EVENT_SLUG}/{MARKET_SLUG}"


def _questions(market_date="2026-12-31", event_date="2026-12-09", yes=0.45):
    """A topic's stored open questions: one market, one scheduled release."""
    return [
        {
            "id": "Q1", "key": f"market:{MARKET_SLUG}", "kind": "market", "origin": "market",
            "question": "Will Widget Co be acquired in 2026?", "date": market_date,
            "url": MARKET_URL, "outcomes": [["Yes", yes], ["No", round(1 - yes, 2)]],
            "related": [],
        },
        {
            "id": "Q2", "key": f"release:{event_date}:example.com/widget/roadmap",
            "kind": "release", "origin": "host", "question": "Widget Co 2.0 stable release",
            "date": event_date, "url": "https://example.com/widget/roadmap", "related": [],
        },
    ]


def _market_call(**overrides):
    entry = {
        "question": "Q1",
        "outcome": "No",
        "claim": "The Widget Co acquisition market resolves No",
        "probability": 0.7,
        "reason": "Both named acquirers walked away this month",
    }
    entry.update(overrides)
    return entry


def _event_call(**overrides):
    entry = {
        "question": "Q2",
        "claim": "Widget Co ships no stable 2.0 release by the roadmap date",
        "probability": 0.6,
        "reason": "The release branch still has three open blockers",
        "baseline": {"status_quo": "No stable 2.0 release ships", "probability": 0.8},
        "backs": "status_quo",
        "resolution": {
            "page": "https://github.com/example/widget/releases",
            "field": "A non-prerelease v2.0.0 tag is listed",
        },
    }
    entry.update(overrides)
    return entry


def _rehearsal_call(**overrides):
    entry = {
        "claim": "The top-voted launch thread comment raises pricing",
        "probability": 0.7,
        "resolve_by": "2026-12-09",
        "resolution": "The highest-voted comment on the launch thread is about price",
        "basis": ["https://example.com/thread/1"],
    }
    entry.update(overrides)
    return entry


def _payload(*predictions, **extra):
    return {"topic": "Widget Co", "predictions": list(predictions or [_market_call()]), **extra}


def _rehearsal(*predictions):
    return _payload(*(predictions or [_rehearsal_call()]), kind="rehearsal")


def _validate(payload, questions=None, **kwargs):
    if questions is None and payload.get("kind") != "rehearsal":
        questions = _questions()
    return forecast.validate_predictions(payload, TODAY, questions=questions, **kwargs)


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


def test_market_call_takes_its_market_fields_from_the_stored_question():
    host_fields = {
        "outcome": "no",
        "market": {"url": "https://polymarket.com/event/some-other-event", "outcome": "Yes", "price": 0.1},
        "market_url": "https://polymarket.com/event/some-other-event",
        "resolve_by": "2026-11-20",
    }
    [row] = _validate(_payload(_market_call(**host_fields)))
    assert row["topic"] == "Widget Co" and row["kind"] == "question"
    assert (row["question_key"], row["question_kind"]) == (f"market:{MARKET_SLUG}", "market")
    assert (row["market_url"], row["market_outcome"], row["market_price"]) == (MARKET_URL, "No", 0.55)
    assert row["resolve_by"] == "2026-12-31"
    assert (row["baseline_kind"], row["baseline_outcome"], row["baseline_probability"]) == ("market", "No", 0.55)
    assert row["resolution_url"] == MARKET_URL
    assert row["reason"] == "Both named acquirers walked away this month"
    assert row["created_on"] == "2026-11-09"
    stamped = _validate(
        _payload(), now=dt.datetime(2026, 11, 9, 7, 30, tzinfo=dt.timezone(dt.timedelta(hours=-8))),
    )[0]
    assert stamped["created_at"] == "2026-11-09T15:30:00+00:00"


def test_market_call_within_five_points_of_the_price_is_a_restatement():
    with pytest.raises(forecast.ForecastError, match="restates"):
        _validate(_payload(_market_call(outcome="Yes", probability=0.93)), _questions(yes=0.92))
    # 0.70 against a market at 0.55 says something the price does not.
    [row] = _validate(_payload(_market_call(probability=0.7)))
    assert row["baseline_probability"] == 0.55


@pytest.mark.parametrize("probability,status_quo,backs,accepted", [
    # A scheduled event "happening as listed", shaded from 92% to 80%: the same call.
    (0.80, 0.92, "status_quo", False),
    # Fifteen points under a near-certain baseline says something.
    (0.80, 0.95, "status_quo", True),
    # Backing the change at 65% when the status quo was given 20% (so 80% on the claim).
    (0.65, 0.20, "change", True),
    (0.75, 0.20, "change", False),
    # The other side of even odds from the baseline always departs.
    (0.55, 0.60, "change", True),
])
def test_a_non_market_call_must_depart_from_the_baseline_it_states(probability, status_quo, backs, accepted):
    call = _event_call(
        probability=probability, backs=backs,
        baseline={"status_quo": "The summit is held on its listed dates", "probability": status_quo},
    )
    if accepted:
        [row] = _validate(_payload(call))
        assert forecast.departs_from_baseline(row) is True
    else:
        with pytest.raises(forecast.ForecastError, match="does not depart from"):
            _validate(_payload(call))


def test_non_market_call_states_a_status_quo_baseline():
    with pytest.raises(forecast.ForecastError, match="restates"):
        _validate(_payload(_event_call(probability=0.9)))
    [row] = _validate(_payload(_event_call(probability=0.6)))
    assert (row["question_key"], row["question_kind"]) == (
        "release:2026-12-09:example.com/widget/roadmap", "release",
    )
    assert (row["baseline_kind"], row["baseline_outcome"], row["baseline_probability"]) == (
        "status_quo", "No stable 2.0 release ships", 0.8,
    )
    assert row["resolve_by"] == "2026-12-09"
    assert row["resolution_url"] == "https://github.com/example/widget/releases"
    assert row["resolution"] == "A non-prerelease v2.0.0 tag is listed"
    assert row["market_url"] is None
    # A call against the status quo is measured against the remainder.
    [against] = _validate(_payload(_event_call(probability=0.9, backs="change")))
    assert against["baseline_probability"] == pytest.approx(0.2)


@pytest.mark.parametrize("call,message", [
    (_market_call(probability=0.4), "between 0.5 and 0.99"),
    (_market_call(probability=True), "must be a number"),
    (_market_call(claim="Up"), "claim is too short"),
    (_market_call(reason=None), "reason must be a string"),
    (_market_call(question="Q9"), "not in this topic's open questions"),
    (_market_call(question=None), "question must name an open question id"),
    (_market_call(outcome=None), "outcome must be one of"),
    (_market_call(outcome="Maybe"), "outcome must be one of"),
    (_event_call(resolution=None), "resolution must give"),
    (_event_call(resolution={"page": "ftp://example.com/x", "field": "A v2.0.0 tag is listed"}), "http(s) URL"),
    (_event_call(resolution={"page": "https://example.com/x"}), "resolution.field"),
    (_event_call(baseline=None), "baseline must state"),
    (_event_call(baseline={"status_quo": "No stable 2.0 release ships", "probability": 1.2}), "baseline.probability"),
    (_event_call(backs="neither"), "backs must be"),
])
def test_question_calls_missing_a_required_part_are_refused(call, message):
    with pytest.raises(forecast.ForecastError, match=re.escape(message)):
        _validate(_payload(call))


def test_a_question_dated_today_or_too_far_out_cannot_be_called():
    with pytest.raises(forecast.ForecastError, match="must be after today"):
        _validate(_payload(), _questions(market_date="2026-11-09"))
    with pytest.raises(forecast.ForecastError, match="more than 90 days"):
        _validate(_payload(), _questions(market_date="2027-06-01"))


def test_rehearsal_call_needs_basis_links_and_no_question():
    [row] = _validate(_rehearsal())
    assert row["kind"] == "rehearsal" and row["question_key"] is None
    assert row["basis"] == ["https://example.com/thread/1"]
    assert row["baseline_kind"] is None and row["resolve_by"] == "2026-12-09"
    entry = _rehearsal_call()
    del entry["resolve_by"]
    assert _validate(_rehearsal(entry))[0]["resolve_by"] == "2026-12-09"


@pytest.mark.parametrize("overrides,message", [
    ({"basis": []}, "at least one evidence URL"),
    ({"basis": None}, "at least one evidence URL"),
    ({"basis": ["not a url"]}, "http(s) URL"),
    ({"basis": ["javascript:alert(1)"]}, "http(s) URL"),
    ({"basis": ["https://[invalid"]}, "http(s) URL"),
    ({"basis": ["https://example.com/a"] * 7}, "at most 6"),
    ({"resolve_by": "2026-11-09"}, "must be after today"),
    ({"resolve_by": "2027-06-01"}, "more than 90 days"),
    ({"resolve_by": "next month"}, "YYYY-MM-DD"),
    ({"resolution": ""}, "resolution is too short"),
    ({"probability": 1.0}, "between 0.5 and 0.99"),
])
def test_ungrounded_or_undated_rehearsal_calls_are_refused(overrides, message):
    with pytest.raises(forecast.ForecastError, match=re.escape(message)):
        _validate(_rehearsal(_rehearsal_call(**overrides)))


def test_file_level_refusals():
    with pytest.raises(forecast.ForecastError, match="non-empty list"):
        _validate({"topic": "Widget Co", "predictions": []})
    with pytest.raises(forecast.ForecastError, match="at most 2"):
        _validate(_rehearsal(*[
            _rehearsal_call(claim=f"Launch thread comment number {i} raises pricing") for i in range(3)
        ]))
    with pytest.raises(forecast.ForecastError, match="duplicates"):
        _validate(_payload(_market_call(), _market_call(claim="The acquisition market ends on No")))
    with pytest.raises(forecast.ForecastError, match="duplicates"):
        _validate(_rehearsal(_rehearsal_call(), _rehearsal_call()))
    with pytest.raises(forecast.ForecastError, match="topic"):
        _validate({"predictions": [_market_call()]})
    with pytest.raises(forecast.ForecastError, match="kind must be"):
        _validate(_payload(kind="hunch"))
    with pytest.raises(forecast.ForecastError, match="missing or has expired"):
        forecast.validate_predictions(_payload(), TODAY, questions=None)
    with pytest.raises(forecast.ForecastError, match="not valid JSON"):
        forecast.parse_payload("{nope")
    with pytest.raises(forecast.ForecastError, match="JSON object"):
        forecast.parse_payload("[]")
    with pytest.raises(forecast.ForecastError, match="exceeds"):
        forecast.parse_payload(" " * (forecast.MAX_PAYLOAD_BYTES + 1))


@pytest.mark.parametrize("probability,baseline,expected", [
    (0.70, 0.55, True),    # fifteen points away
    (0.62, 0.55, False),   # same side, close
    (0.55, 0.45, True),    # other side of even odds
    (0.60, 0.80, True),
    (0.60, 0.50, False),
    (0.70, None, None),    # no baseline recorded
])
def test_a_call_departs_from_its_baseline_across_even_odds_or_fifteen_points(probability, baseline, expected):
    row = {"probability": probability, "baseline_probability": baseline}
    assert forecast.departs_from_baseline(row) is expected


def test_feature_is_on_unless_the_setting_says_off():
    assert forecast.enabled({}) and forecast.enabled({forecast.CONFIG_KEY: "on"})
    assert not forecast.enabled({forecast.CONFIG_KEY: " OFF "})


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
    settled, unreachable, unchecked = forecast.settle_open_markets(rows, fetch)
    assert settled == [] and unchecked == []
    assert [(row["id"], reason) for row, reason in unreachable] == [(1, "RuntimeError: gamma down")]


def test_settle_asks_for_each_market_event_once():
    fetch = mock.Mock(return_value=_event())
    rows = [_market_row(), _market_row(id=2, market_outcome="Yes")]
    settled, unreachable, unchecked = forecast.settle_open_markets(rows, fetch)
    fetch.assert_called_once_with(EVENT_SLUG)
    assert [(row["id"], verdict) for row, verdict, _ in settled] == [(1, "hit"), (2, "miss")]

    down = mock.Mock(side_effect=RuntimeError("gamma down"))
    _, unreachable, _ = forecast.settle_open_markets(rows, down)
    down.assert_called_once_with(EVENT_SLUG)
    assert [row["id"] for row, _ in unreachable] == [1, 2]


def test_settle_stops_at_its_deadline_and_returns_what_it_did_not_check():
    ticks = iter([0.0, 6.0, 12.0])
    fetch = mock.Mock(return_value=_event())
    rows = [
        _market_row(),
        _market_row(id=2, market_url="https://polymarket.com/event/second-event"),
        _market_row(id=3, market_url="https://polymarket.com/event/third-event"),
    ]
    settled, unreachable, unchecked = forecast.settle_open_markets(
        rows, fetch, deadline=10.0, clock=lambda: next(ticks),
    )
    assert [call.args[0] for call in fetch.call_args_list] == [EVENT_SLUG, "second-event"]
    assert [row["id"] for row, _, _ in settled] == [1]
    assert unreachable == [] and [row["id"] for row in unchecked] == [3]


# --- scorecard --------------------------------------------------------------------


def _scored(probability, verdict, **extra):
    return {"probability": probability, "verdict": verdict, "resolve_by": "2026-10-01", **extra}


def _against(probability, verdict, baseline, baseline_kind="market", **extra):
    """A graded question call with a stored baseline on the call's own outcome."""
    return _scored(
        probability, verdict, kind="question", baseline_kind=baseline_kind,
        baseline_probability=baseline, **extra,
    )


DEPARTED = [_against(0.7, "hit", 0.4), _against(0.8, "hit", 0.5), _against(0.7, "miss", 0.45)]
DEPARTED_SKILL = 1 - ((0.09 + 0.04 + 0.49) / 3) / ((0.36 + 0.25 + 0.2025) / 3)


def _card_text(rows):
    return forecast.render_scorecard(forecast.scorecard(rows, TODAY), TODAY)


def test_scorecard_math():
    rows = [
        _scored(0.9, "hit"),
        _scored(0.9, "miss"),
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
    by_low = {bucket["low"]: bucket for bucket in card["calibration"]}
    assert by_low[0.9]["count"] == 2 and by_low[0.9]["actual"] == 0.5
    text = forecast.render_scorecard(card, TODAY)
    assert "Hit rate: 67% (2 of 3)" in text
    assert "Overdue and ungraded: 1" in text
    assert "Small sample" in text
    assert "—" not in text and "–" not in text


def test_scorecard_never_invents_a_record():
    empty = forecast.render_scorecard(forecast.scorecard([], TODAY), TODAY)
    assert "No predictions logged yet" in empty and "Hit rate" not in empty
    ungraded = forecast.render_scorecard(forecast.scorecard([_scored(0.7, None)], TODAY), TODAY)
    assert "Nothing graded yet" in ungraded and "Hit rate" not in ungraded
    voided = forecast.scorecard([_scored(0.7, "void")], TODAY)
    assert voided["hit_rate"] is None and voided["brier"] is None


def test_scorecard_leads_with_the_calls_that_departed_from_the_market():
    card = forecast.scorecard(DEPARTED, TODAY)
    assert card["departed_market"] == {
        "graded": 3, "hits": 2, "ours": pytest.approx(0.62 / 3),
        "baseline": pytest.approx(0.8125 / 3), "skill": pytest.approx(DEPARTED_SKILL),
    }
    text = forecast.render_scorecard(card, TODAY)
    record = text.index("Calls that departed from a market baseline: 2 of 3 right")
    skill = text.index("Skill against the market: +0.24 (ours 0.207, market 0.271 on Brier")
    assert record < skill < text.index("Hit rate: 67% (2 of 3)")
    assert "The market was closer" not in text and "—" not in text and "–" not in text


def test_calls_that_agreed_with_the_baseline_are_reported_apart_from_the_headline():
    agreeing = [_against(0.6, "hit", 0.55), _against(0.6, "miss", 0.55, "status_quo")]
    card = forecast.scorecard(DEPARTED + agreeing, TODAY)
    assert card["departed_market"]["skill"] == pytest.approx(DEPARTED_SKILL)
    assert card["agreed"] == {"graded": 2, "hits": 1}
    text = forecast.render_scorecard(card, TODAY)
    assert "Calls that departed from a market baseline: 2 of 3 right" in text
    assert "Skill against the market: +0.24" in text
    assert text.index("Skill against the market") < text.index(
        "Calls that agreed with their baseline: 1 of 2 right"
    )


def test_a_call_against_a_host_stated_baseline_stays_out_of_the_headline_skill():
    card = forecast.scorecard(DEPARTED + [_against(0.6, "hit", 0.2, "status_quo")], TODAY)
    assert card["departed_market"]["graded"] == 3
    assert card["departed_market"]["skill"] == pytest.approx(DEPARTED_SKILL)
    assert card["departed_status_quo"]["skill"] == pytest.approx(1 - 0.16 / 0.64)
    text = forecast.render_scorecard(card, TODAY)
    assert "Skill against the market: +0.24" in text
    assert (
        "Calls that departed from a host-stated baseline: 1 of 1 right, skill +0.75 "
        "(ours 0.160, baseline 0.640 on Brier)"
    ) in text
    assert "not part of the market figure" in text


def test_legacy_and_rehearsal_calls_are_counted_on_their_own_lines_without_a_skill_figure():
    legacy = [_scored(0.9, "hit", kind="legacy", market_price=0.6), _scored(0.7, "miss", kind="legacy")]
    text = _card_text(legacy)
    assert "Legacy calls (logged before baselines were recorded): 2 logged, 1 of 2 graded right. No skill figure." in text
    assert "Skill against" not in text and "departed" not in text and "Rehearsal" not in text
    assert "Hit rate: 50% (1 of 2)" in text

    mixed = _card_text(legacy + [_scored(0.7, "hit", kind="rehearsal"), _scored(0.7, None, kind="rehearsal")])
    assert "Rehearsal calls: 2 logged, 1 of 1 graded right. No skill figure." in mixed
    assert "Skill against" not in mixed


def test_negative_skill_says_the_market_was_closer():
    card = forecast.scorecard([_against(0.9, "miss", 0.6), _against(0.9, "hit", 0.6)], TODAY)
    assert card["departed_market"]["skill"] == pytest.approx(1 - 0.41 / 0.26)
    text = forecast.render_scorecard(card, TODAY)
    assert "Skill against the market: -0.58 (ours 0.410, market 0.260 on Brier" in text
    assert "The market was closer than these calls." in text


def test_skill_is_not_computed_against_a_baseline_that_scored_zero():
    card = forecast.scorecard([_against(0.6, "miss", 0.0)], TODAY)
    assert card["departed_market"]["skill"] is None
    assert "Skill against the market: not defined" in forecast.render_scorecard(card, TODAY)


# --- store: append-only ledger ------------------------------------------------------


@pytest.fixture
def ledger(tmp_path):
    with store.scoped_db(tmp_path / "research.db"):
        yield tmp_path / "research.db"


def _open_rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM forecasts WHERE verdict IS NULL").fetchone()[0]
    finally:
        conn.close()


def test_ledger_logs_once_and_grades_once(ledger):
    [row] = _validate(_payload())
    stored, created = store.add_forecast(row)
    assert created and stored["id"] == 1 and stored["kind"] == "question"
    assert stored["baseline_probability"] == 0.55 and stored["question_key"] == f"market:{MARKET_SLUG}"

    # A second call on the same question must not replace or join the first.
    again, created_again = store.add_forecast({**row, "probability": 0.95, "claim": "The market ends on No!"})
    assert not created_again and again["id"] == 1 and again["probability"] == 0.7
    assert _open_rows(ledger) == 1

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


def test_open_call_is_found_under_a_renamed_topic_but_not_an_unrelated_one(ledger):
    [row] = _validate({**_payload(), "topic": "OpenClaw"})
    first, _ = store.add_forecast(row)
    renamed, created = store.add_forecast({**row, "topic": "OpenClaw agent", "claim": "OpenClaw is not acquired"})
    assert not created and renamed["id"] == first["id"] and renamed["topic"] == "OpenClaw"
    other, created = store.add_forecast({**row, "topic": "Widget Co"})
    assert created and other["id"] != first["id"]


def test_open_calls_are_read_back_under_a_renamed_topic(ledger):
    [row] = _validate({**_payload(), "topic": "OpenClaw"})
    first, _ = store.add_forecast(row)
    other, _ = store.add_forecast({**row, "topic": "Widget Co"})
    assert [r["id"] for r in store.list_open_forecasts("OpenClaw agent")] == [first["id"]]
    assert [r["id"] for r in store.list_open_forecasts("openclaw")] == [first["id"]]
    assert [r["id"] for r in store.list_open_forecasts()] == [first["id"], other["id"]]
    assert store.list_open_forecasts("Something Else Entirely") == []
    # An exact-topic read keeps its meaning.
    assert store.list_forecasts("OpenClaw agent") == []

    store.grade_forecasts(
        [{"id": first["id"], "verdict": "hit", "evidence_url": "https://example.com/proof"}],
        "2026-11-20", "host",
    )
    assert store.list_open_forecasts("OpenClaw agent") == []


def test_rehearsal_retry_returns_the_first_logged_call(ledger):
    [row] = _validate(_rehearsal())
    stored, created = store.add_forecast(row)
    assert created and stored["kind"] == "rehearsal" and stored["basis"] == ["https://example.com/thread/1"]
    again, created_again = store.add_forecast({**row, "probability": 0.95})
    assert not created_again and again["probability"] == 0.7


def test_concurrent_adds_on_one_question_log_one_row(ledger):
    [row] = _validate({**_payload(), "topic": "OpenClaw"})
    store.init_db()
    barrier = threading.Barrier(8)

    def add(index):
        barrier.wait(timeout=5)
        topic = "OpenClaw" if index % 2 else "OpenClaw agent"
        return store.add_forecast({**row, "topic": topic, "claim": f"{row['claim']} (attempt {index})"})

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(add, range(8)))
    assert sum(created for _, created in results) == 1
    assert {stored["id"] for stored, _ in results} == {1}
    assert _open_rows(ledger) == 1


def test_grade_batch_is_all_or_nothing(ledger):
    for claim in ("Launch thread comment one", "Launch thread comment two", "Launch thread comment three"):
        [row] = _validate(_rehearsal(_rehearsal_call(claim=claim)))
        store.add_forecast(row)
    # Prediction 2 gets settled by another run after the caller's checks.
    store.grade_forecasts([{"id": 2, "verdict": "void", "note": "settled elsewhere"}], "2026-11-15", "market")
    batch = [{"id": n, "verdict": "hit", "evidence_url": "https://example.com/p"} for n in (1, 2, 3)]
    with pytest.raises(store.ForecastGradeConflict) as conflict:
        store.grade_forecasts(batch, "2026-11-20", "host")
    assert conflict.value.forecast_id == 2
    assert store.get_forecast(1)["verdict"] is None
    assert store.get_forecast(3)["verdict"] is None


def test_list_view_shows_the_baseline_and_the_reason(ledger):
    for call in (_market_call(), _event_call()):
        [row] = _validate(_payload(call))
        store.add_forecast(row)
    text = forecast.render_list(store.list_forecasts(), TODAY, "Forecast ledger")
    assert "market priced it at 55% when logged" in text
    assert 'baseline for this claim: 80% (the status quo is: No stable 2.0 release ships)' in text
    assert "resolution page: https://github.com/example/widget/releases" in text
    assert "why: The release branch still has three open blockers" in text
    assert "legacy" not in text


def test_ledger_has_no_edit_or_delete_surface():
    names = {name for name in dir(store) if "forecast" in name and not name.startswith("_")}
    assert names == {
        "add_forecast", "get_forecast", "list_forecasts", "list_open_forecasts", "grade_forecasts",
    }


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


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    path = tmp_path / "config"
    monkeypatch.setattr(cli.env, "CONFIG_DIR", path)
    return path


def _live_questions():
    return _questions(market_date=_future(40), event_date=_future(30), yes=0.2)


def _live_payload(*predictions, **extra):
    calls = predictions or (_event_call(), _market_call(probability=0.9))
    return json.dumps(_payload(*calls, **extra))


def _add(monkeypatch, capsys, save_dir, stdin, extra=()):
    save = [] if save_dir is None else ["--save-dir", str(save_dir)]
    return _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save, *extra], stdin=stdin)


def test_read_commands_on_an_empty_ledger_create_nothing(monkeypatch, capsys, tmp_path):
    for command, expected in [
        ("scorecard", "No predictions logged yet"),
        ("due", "Due for grading: none."),
        ("list", "Forecast ledger: none."),
    ]:
        rc, out, _ = _run(monkeypatch, capsys, ["forecast", command, "--save-dir", str(tmp_path)])
        assert rc == 0 and expected in out
    assert not (tmp_path / "research.db").exists()


def test_add_settle_grade_scorecard_round_trip(monkeypatch, capsys, tmp_path, config_dir):
    save = ["--save-dir", str(tmp_path)]
    open_questions.store("Widget Co", _live_questions())
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload())
    assert rc == 0, err
    assert "Logged to the forecast ledger (2)" in out and "#1 [open" in out and "#2 [open" in out
    assert (tmp_path / "research.db").stat().st_mode & 0o077 == 0

    # A second call on a question that already has an open one logs nothing.
    reworded = _live_payload(
        _event_call(claim="Widget Co misses its own 2.0 roadmap date", probability=0.65),
        _market_call(claim="Nobody acquires Widget Co this year", probability=0.95),
    )
    rc, out, _ = _add(monkeypatch, capsys, tmp_path, reworded)
    assert rc == 0 and "Already in the ledger" in out and "Logged to" not in out
    assert "Widget Co ships no stable 2.0 release" in out and "misses its own" not in out
    assert _open_rows(tmp_path / "research.db") == 2

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
    assert rc == 0
    # The release call left its stated status quo; the market call stayed near the price.
    assert "Calls that departed from a host-stated baseline: 1 of 1 right" in out
    assert "Calls that agreed with their baseline: 1 of 1 right" in out
    assert "Skill against the market" not in out


def test_settle_outage_is_reported_and_leaves_the_call_open(monkeypatch, capsys, tmp_path, config_dir):
    save = ["--save-dir", str(tmp_path)]
    open_questions.store("Widget Co", _live_questions())
    _add(monkeypatch, capsys, tmp_path, _live_payload())
    with mock.patch.object(cli, "_fetch_polymarket_events", side_effect=RuntimeError("gamma down")):
        rc, out, err = _run(monkeypatch, capsys, ["forecast", "settle", *save])
    assert rc == 0 and "0 settled, 1 unreachable, 0 still open" in out
    assert "could not check prediction #2" in err


def test_bad_input_exits_two_without_writing(monkeypatch, capsys, tmp_path, config_dir):
    save = ["--save-dir", str(tmp_path)]
    open_questions.store("Widget Co", _live_questions())
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "add", *save])
    assert rc == 2 and "requires --forecast-file" in err
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "add", "--forecast-file", str(tmp_path / "missing.json"), *save])
    assert rc == 2 and "forecast add:" in err
    for call, message in [
        (_market_call(probability=0.2), "between 0.5 and 0.99"),
        (_market_call(question="Q9"), "'Q9' is not in this topic's open questions"),
        (_event_call(resolution=None), "resolution must give"),
    ]:
        rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload(call))
        assert rc == 2 and message in err and not out, (message, err)
    three = _live_payload(*[_event_call(claim=f"Widget Co slips release date number {i}") for i in range(3)])
    rc, _, err = _add(monkeypatch, capsys, tmp_path, three)
    assert rc == 2 and "at most 2" in err
    rc, _, err = _run(monkeypatch, capsys, ["forecast", "grade", "--forecast-file", "-", *save], stdin="{}")
    assert rc == 2 and "non-empty list" in err
    assert not (tmp_path / "research.db").exists()


def test_restatement_of_the_market_price_is_refused_from_the_command_line(monkeypatch, capsys, tmp_path, config_dir):
    open_questions.store("Widget Co", _questions(market_date=_future(40), yes=0.92))
    call = _market_call(outcome="Yes", probability=0.93, claim="Widget Co is acquired before the market closes")
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload(call))
    assert rc == 2 and "restates" in err and not out
    assert not (tmp_path / "research.db").exists()


def test_expired_open_questions_are_named_as_the_cause(monkeypatch, capsys, tmp_path, config_dir):
    path = open_questions.store("Widget Co", _live_questions())
    stored = json.loads(path.read_text())
    stored["timestamp"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(stored))
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload())
    assert rc == 2 and not out
    assert "no open questions are stored for 'Widget Co'" in err and "missing or has expired" in err
    assert not (tmp_path / "research.db").exists()


def test_another_topics_run_does_not_displace_this_topics_questions(monkeypatch, capsys, tmp_path, config_dir):
    open_questions.store("Widget Co", _live_questions())
    open_questions.store("Gadget Inc", [{**_live_questions()[1], "question": "Gadget Inc keynote"}])
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload())
    assert rc == 0, err
    assert "Logged to the forecast ledger (2)" in out


def test_same_event_reworded_on_a_later_run_returns_the_existing_call(monkeypatch, capsys, tmp_path, config_dir):
    open_questions.store("Widget Co", _live_questions())
    rc, out, _ = _add(monkeypatch, capsys, tmp_path, _live_payload(_event_call()))
    assert rc == 0 and "Logged to the forecast ledger (1)" in out
    # The later run lists the same dated page under a new id and new wording.
    later = {**_live_questions()[1], "id": "Q1", "question": "Will Widget Co finally tag 2.0?"}
    open_questions.store("Widget Co", [later])
    reworded = _event_call(question="Q1", claim="Widget Co fails to tag 2.0 by its roadmap date", probability=0.55)
    rc, out, _ = _add(monkeypatch, capsys, tmp_path, _live_payload(reworded))
    assert rc == 0 and "Already in the ledger" in out and "#1 [open" in out and "Logged to" not in out
    assert _open_rows(tmp_path / "research.db") == 1


def test_market_call_under_a_renamed_topic_returns_the_open_call(monkeypatch, capsys, tmp_path, config_dir):
    for topic in ("OpenClaw", "OpenClaw agent"):
        open_questions.store(topic, _live_questions())
    first = json.dumps({"topic": "OpenClaw", "predictions": [_market_call(probability=0.9)]})
    rc, out, _ = _add(monkeypatch, capsys, tmp_path, first)
    assert rc == 0 and "Logged to the forecast ledger (1)" in out
    second = json.dumps({"topic": "OpenClaw agent", "predictions": [_market_call(probability=0.95)]})
    rc, out, _ = _add(monkeypatch, capsys, tmp_path, second)
    assert rc == 0 and "Already in the ledger" in out and "topic: OpenClaw |" in out and "Logged to" not in out
    assert _open_rows(tmp_path / "research.db") == 1


def test_host_sent_market_fields_never_reach_the_ledger(monkeypatch, capsys, tmp_path, config_dir):
    open_questions.store("Widget Co", _live_questions())
    call = _market_call(
        probability=0.9, outcome="no",
        market={"url": "https://polymarket.com/event/some-other-event", "outcome": "Yes", "price": 0.5},
    )
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload(call))
    assert rc == 0, err
    with store.scoped_db(tmp_path / "research.db"):
        [row] = store.list_forecasts()
    assert (row["market_url"], row["market_outcome"], row["market_price"]) == (MARKET_URL, "No", 0.8)
    assert row["resolve_by"] == _future(40) and "some-other-event" not in out


def test_rehearsal_call_is_logged_without_an_open_question(monkeypatch, capsys, tmp_path, config_dir):
    payload = json.dumps({"topic": "Project Heron", "kind": "rehearsal", "predictions": [
        _rehearsal_call(resolve_by=_future(30)),
    ]})
    rc, out, err = _add(monkeypatch, capsys, tmp_path, payload)
    assert rc == 0, err
    assert "Logged to the forecast ledger (1)" in out
    with store.scoped_db(tmp_path / "research.db"):
        [row] = store.list_forecasts()
    assert row["kind"] == "rehearsal" and row["question_key"] is None


def test_private_check_can_refuse_a_submission_before_anything_is_written(monkeypatch, capsys, tmp_path, config_dir):
    seen = []
    monkeypatch.setattr(cli, "_forecast_private_refusal", lambda payload: seen.append(payload) or "held back")
    payload = json.dumps({"topic": "Project Heron", "kind": "rehearsal", "predictions": [
        _rehearsal_call(resolve_by=_future(30)),
    ]})
    rc, out, err = _add(monkeypatch, capsys, tmp_path, payload)
    assert rc == 2 and "forecast add: held back" in err and not out
    assert seen == [json.loads(payload)]
    assert not (tmp_path / "research.db").exists()


@pytest.mark.parametrize("save_args", [["--save-dir", ""], []])
def test_without_a_save_directory_nothing_is_logged_anywhere(monkeypatch, capsys, tmp_path, config_dir, save_args):
    shared = tmp_path / "shared" / "research.db"
    monkeypatch.setattr(store, "DB_PATH", shared)
    monkeypatch.setattr(store, "DB_DIR", shared.parent)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    open_questions.store("Widget Co", _live_questions())
    rc, out, err = _run(
        monkeypatch, capsys, ["forecast", "add", "--forecast-file", "-", *save_args], stdin=_live_payload(),
    )
    assert rc == 0, err
    assert out.strip().startswith("Not logged:") and len(out.strip().splitlines()) == 1
    for command in ("list", "due", "scorecard", "settle"):
        with mock.patch.object(cli, "_fetch_polymarket_events", side_effect=AssertionError("network")):
            rc, out, _ = _run(monkeypatch, capsys, ["forecast", command, *save_args])
        assert rc == 0 and out.strip().startswith("No forecast ledger:")
    # Submitted verdicts that were not written are a failure, not a notice.
    grades = json.dumps({"grades": [{"id": 1, "verdict": "hit", "evidence_url": "https://example.com/x"}]})
    rc, out, _ = _run(
        monkeypatch, capsys, ["forecast", "grade", "--forecast-file", "-", *save_args], stdin=grades,
    )
    assert rc == 2 and out.strip().startswith("No forecast ledger:")
    assert not list(tmp_path.rglob("*.db*"))
    assert list(workdir.iterdir()) == []


def test_feature_turned_off_logs_nothing(monkeypatch, capsys, tmp_path, config_dir):
    monkeypatch.setenv("LAST30DAYS_NEXT30DAYS", "off")
    open_questions.store("Widget Co", _live_questions())
    rc, out, err = _add(monkeypatch, capsys, tmp_path, _live_payload())
    assert rc == 0 and not err
    assert len(out.strip().splitlines()) == 1 and "LAST30DAYS_NEXT30DAYS=off" in out
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
        "not a recommendation",
        "Predictions are part of every ordinary research run",
        "`synthesis.md` holds the whole procedure",
        "Never present a prediction as logged unless the engine printed it with an id",
        "LAST30DAYS_NEXT30DAYS=off",
    ):
        assert rule in text
    # The on-request procedure is gone: predictions come from a run's open questions.
    assert "Making a forecast for a topic" not in text
    assert not re.search(r"3 to 5|three to five", text)
    scorecard = text.split("- `forecast scorecard` prints", 1)[1].split("\n", 1)[0]
    for part in (
        "departed from a market baseline", "skill against the market",
        "The market was closer than these calls.", "departed from a host-stated baseline",
        "agreed with their baseline", "legacy and rehearsal calls", "calibration",
    ):
        assert part in scorecard, part
    assert "—" not in text and "–" not in text
    for block in text.split("<<'")[1:]:
        body = block.split("\n", 1)[1].split("_EOF", 1)[0].rsplit("\n", 1)[0]
        json.loads(body)


def test_rehearsal_keeps_the_draft_private_and_the_predictions_grounded():
    text = reference_text("forecast")
    rehearsal = text.split("## Rehearsal:", 1)[1].split("\n## ", 1)[0]
    for rule in (
        "**Keep the draft private.**",
        "Never place the draft's text, the unannounced product or company name",
        "Search for the category and for the analogs only",
        "do not rehearse against invented or older launches",
        "A predicted reaction with no supporting comment is cut",
        "What the evidence cannot tell you",
        "Never log a prediction about reception without the user's yes",
        "never supply a number",
        "set every `resolve_by` explicitly to a date after it",
        "a codename, never the real name",
        "Criticize the draft, never the person",
        '`--reactions` exits 2 unless it comes with `--private-file <path>` or `--nothing-private`',
        '--reactions --private-file "$PRIVATE_FILE"',
        "never work around the check by dropping `--private-file`",
        "`[launcher reply]`",
        "Save one only when the user asks for it after seeing the rehearsal",
        "A file takes at most 2 predictions",
        '--forecast-file - --private-file "$PRIVATE_FILE"',
        'rm -f "$PRIVATE_FILE"',
    ):
        assert rule in rehearsal, rule
    assert "Rehearsal section below" in text.split("\n## ", 1)[0]
