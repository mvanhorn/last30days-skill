"""Regression tests for PR 31: freshness verification & Polymarket matching/outcome parsing.

Covers:
- HIGH-094: Grounding item lookup keyed by candidate_id (with URL fallback) to avoid positional item_id collisions.
- HIGH-095: point_cache lookup alignment with polymarket.refetch_datum (casefold, occurrence-0, None -> unsupported) and extract_claims outcome_counts filtering.
- HIGH-096: _newer_status_contradiction undated claims, parsed UTC/date-only timestamps, copula priority (became > was, len == 1), newest-timestamp aggregation, and URL-based item identity.
- HIGH-194: Original-case all-caps acronym matching and _NOISE_WORDS exclusion in Polymarket EntityMatcher.
- HIGH-195: Multi-market Yes/No synthesis preserving resolved-No (0.0) and <= 0.005 outcomes under include_all_outcomes=True and >= RACE_PRICE_FLOOR.
- HIGH-196: _parse_outcome_prices non-list JSON safety, bool/non-finite/overflow/range rejection, and has_first guard against token-0 quote misattribution.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lib import freshness, polymarket, schema
from tests.test_freshness import _item, _report


# ---------------------------------------------------------------------------
# HIGH-094: Grounding item lookup by candidate_id (no positional ID collision)
# ---------------------------------------------------------------------------


def test_high094_colliding_github_item_ids_verify_against_own_repo():
    repo_a = _item(
        "github",
        item_id="GH1",
        title="orgA/repo1 (500 stars)",
        url="https://github.com/orgA/repo1",
        container="orgA/repo1",
        engagement={"stars": 500},
    )
    repo_b = _item(
        "github",
        item_id="GH1",
        title="orgB/repo2 (10,000 stars)",
        url="https://github.com/orgB/repo2",
        container="orgB/repo2",
        engagement={"stars": 10000},
    )
    report = _report(repo_a, repo_b)
    seen_repos: list[str] = []

    def fake_github_refetch(item, _key):
        seen_repos.append(item.container)
        counts = {"orgA/repo1": 500, "orgB/repo2": 10000}
        return {"value": counts[item.container], "url": item.url}

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-24T12:00:00Z",
        refetchers={"github": fake_github_refetch},
    )

    assert seen_repos == ["orgA/repo1", "orgB/repo2"]
    assert [v.verdict for v in verdicts] == ["current", "current"]
    assert [v.source_url for v in verdicts] == [repo_a.url, repo_b.url]
    assert [v.evidence_url for v in verdicts] == [repo_a.url, repo_b.url]


def test_high094_colliding_stocktwits_and_polymarket_positional_ids():
    st_a = _item(
        "stocktwits",
        item_id="ST1",
        title="$AAPL stream",
        url="https://stocktwits.com/symbol/AAPL/1",
        container="AAPL",
        metadata={"symbol": "AAPL", "sentiment_aggregate": {"pct_bullish": 60}},
    )
    st_b = _item(
        "stocktwits",
        item_id="ST1",
        title="$MSFT stream",
        url="https://stocktwits.com/symbol/MSFT/1",
        container="MSFT",
        metadata={"symbol": "MSFT", "sentiment_aggregate": {"pct_bullish": 80}},
    )
    pm_a = _item(
        "polymarket",
        item_id="PM1",
        title="Event A?",
        url="https://polymarket.com/event/event-a",
        metadata={"event_id": "101", "outcome_prices": [["Yes", 0.30]]},
    )
    pm_b = _item(
        "polymarket",
        item_id="PM1",
        title="Event B?",
        url="https://polymarket.com/event/event-b",
        metadata={"event_id": "202", "outcome_prices": [["Yes", 0.70]]},
    )
    report = _report(st_a, st_b, pm_a, pm_b)

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-24T12:00:00Z",
        refetchers={
            "stocktwits": lambda item, _key: {
                "value": {"AAPL": 60, "MSFT": 80}[item.metadata["symbol"]],
                "url": item.url,
            },
            "polymarket": lambda item, _key: {
                "value": {"101": 0.30, "202": 0.70}[item.metadata["event_id"]],
                "url": item.url,
            },
        },
    )

    assert [v.verdict for v in verdicts] == ["current", "current", "current", "current"]
    assert [v.evidence_url for v in verdicts] == [st_a.url, st_b.url, pm_a.url, pm_b.url]


def test_high094_fallback_lookup_uses_source_url_not_colliding_item_id(monkeypatch):
    repo_a = _item(
        "github",
        item_id="GH1",
        title="orgA/repo1 (500 stars)",
        url="https://github.com/orgA/repo1",
        container="orgA/repo1",
        engagement={"stars": 500},
    )
    repo_b = _item(
        "github",
        item_id="GH1",
        title="orgB/repo2 (10,000 stars)",
        url="https://github.com/orgB/repo2",
        container="orgB/repo2",
        engagement={"stars": 10000},
    )
    report = _report(repo_a, repo_b)
    synthetic_claim = freshness.Claim(
        claim_id="claim-synthetic",
        candidate_id="candidate-missing",
        text="orgA/repo1 has 500 GitHub stars",
        source="github",
        source_item_id="GH1",
        source_url=repo_a.url,
        source_timestamp=repo_a.published_at,
        datum_kind="github_stars",
        datum_key="stars",
        original_value=500,
    )
    monkeypatch.setattr(freshness, "extract_claims", lambda _report: [synthetic_claim])

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-24T12:00:00Z",
        refetchers={
            "github": lambda item, _key: {
                "value": {"orgA/repo1": 500, "orgB/repo2": 10000}[item.container],
                "url": item.url,
            }
        },
    )

    assert len(verdicts) == 1
    assert verdicts[0].verdict == "current"
    assert verdicts[0].evidence_url == repo_a.url


# ---------------------------------------------------------------------------
# HIGH-095: point_cache lookup parity & extract_claims outcome_counts filter
# ---------------------------------------------------------------------------


def test_high095_cached_snapshot_resolves_casefolded_and_whitespace_keys():
    market = _item(
        "polymarket",
        title="Top AI lab?",
        url="https://polymarket.com/event/top-ai-lab",
        metadata={
            "event_id": "555",
            "outcome_prices": [["Yes", 0.42], ["openai", 0.55]],
            "end_date": "2026-12-31",
        },
    )
    report = _report(market)
    calls: list[str] = []

    def fake_refetch(_item, key):
        calls.append(key)
        return {
            "value": 0.42,
            "values": {"Yes": 0.42, " OpenAI ": 0.55, "end_date": "2026-12-31"},
            "url": market.url,
            "timestamp": "2026-11-24T12:00:00Z",
        }

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": fake_refetch},
    )

    assert calls == ["Yes"]
    assert [v.verdict for v in verdicts] == ["current", "current", "current"]


def test_high095_cached_snapshot_resolves_unsuffixed_claim_via_occurrence_zero(monkeypatch):
    market = _item(
        "polymarket",
        title="Multi-outcome shift",
        url="https://polymarket.com/event/multi-shift",
        metadata={
            "event_id": "556",
            "outcome_prices": [["Alpha", 0.30], ["yes", 0.42]],
            "end_date": "2026-12-31",
        },
    )
    event = {
        "id": "556",
        "slug": "multi-shift",
        "title": "Multi-outcome shift",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                "active": True,
                "closed": False,
                "question": "Multi-outcome shift",
                "liquidity": "5000",
                "outcomes": '["Alpha", "Yes", "Yes"]',
                "outcomePrices": '["0.30", "0.42", "0.28"]',
                "endDate": "2026-12-31T00:00:00Z",
            }
        ],
    }
    calls: list[int] = []
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)

    verdicts = freshness.verify_report(
        _report(market),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )

    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "current", "current"]


def test_high095_end_date_does_not_match_occurrence_zero_outcome():
    market = _item(
        "polymarket",
        title="Weird labels",
        url="https://polymarket.com/event/weird",
        metadata={
            "event_id": "557",
            "outcome_prices": [["Alpha", 0.50]],
            "end_date": "2026-12-31",
        },
    )
    verdicts = freshness.verify_report(
        _report(market),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={
            "polymarket": lambda _item, _key: {
                "value": 0.50,
                "values": {"Alpha": 0.50, "end_date\x1f0": 0.25},
                "url": market.url,
            }
        },
    )

    assert [v.verdict for v in verdicts] == ["current", "unsupported"]


def test_high095_none_values_in_snapshot_or_miss_are_unsupported_not_stale():
    market = _item(
        "polymarket",
        title="Null end date",
        url="https://polymarket.com/event/null-end",
        metadata={
            "event_id": "558",
            "outcome_prices": [["Yes", 0.42]],
            "end_date": "2026-12-31",
        },
    )
    verdicts = freshness.verify_report(
        _report(market),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={
            "polymarket": lambda _item, _key: {
                "value": 0.42,
                "values": {"Yes": 0.42, "end_date": None},
                "url": market.url,
            }
        },
    )

    assert [v.verdict for v in verdicts] == ["current", "unsupported"]
    assert "-> None" not in (verdicts[1].detail or "")

    # Cache miss returning value=None must also be unsupported while still caching values for siblings.
    market2 = _item(
        "polymarket",
        title="Null first value",
        url="https://polymarket.com/event/null-first",
        metadata={
            "event_id": "559",
            "outcome_prices": [["First", 0.40], ["Second", 0.60]],
        },
    )
    calls = []

    def refetch_null_first(_item, key):
        calls.append(key)
        return {"value": None, "values": {"First": None, "Second": 0.60}, "url": market2.url}

    verdicts2 = freshness.verify_report(
        _report(market2),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": refetch_null_first},
    )
    assert calls == ["First"]
    assert [v.verdict for v in verdicts2] == ["unsupported", "current"]


def test_high095_extract_claims_filters_invalid_pairs_before_counting_duplicates():
    market = _item(
        "polymarket",
        title="Mixed valid and invalid pairs",
        url="https://polymarket.com/event/mixed-pairs",
        metadata={
            "event_id": "560",
            "outcome_prices": [
                ["Yes", None],
                ["Yes", True],
                ["Yes", False],
                ["Yes", "0.5"],
                ["Yes", float("nan")],
                ["Yes", float("inf")],
                ["Yes", -float("inf")],
                ["Yes", 10**400],
                ["   ", 0.1],
                [" Yes ", 0.55],
            ],
        },
    )
    claims = freshness.extract_claims(_report(market))

    assert len(claims) == 1
    assert claims[0].datum_key == "Yes"
    assert claims[0].original_value == 0.55


def test_high095_polymarket_refetch_datum_strips_whitespace_and_rejects_bad_occurrence(monkeypatch):
    event = {
        "id": "561",
        "slug": "ws-outcome",
        "title": "Whitespace outcome",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                "active": True,
                "closed": False,
                "question": "Whitespace outcome",
                "liquidity": "1000",
                "outcomes": '[" OpenAI ", "Anthropic"]',
                "outcomePrices": '["0.60", "0.40"]',
            }
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "561"}, url="", item_id="pm-561")

    res = polymarket.refetch_datum(item, "openai")
    assert res["value"] == 0.60
    assert "OpenAI" in res["values"]

    for bad_key in ("OpenAI\x1f-1", "OpenAI\x1fnotanint"):
        with pytest.raises(polymarket.DatumNotFound):
            polymarket.refetch_datum(item, bad_key)


# ---------------------------------------------------------------------------
# HIGH-096: _newer_status_contradiction (all 5 defects)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_ts", [None, "", "not-a-timestamp"])
def test_high096_undated_claim_is_never_contradicted_by_dated_items(bad_ts):
    original = _item(
        "grounding",
        item_id="W1",
        url="https://example.com/today",
        title="Widget API is open",
        published_at=bad_ts,
    )
    old_item = _item(
        "reddit",
        item_id="R1",
        url="https://reddit.com/r/dev/1",
        title="Widget API is closed",
        published_at="2026-10-25T12:00:00Z",
    )
    report = _report(original)
    report.items_by_source["reddit"] = [old_item]

    verdict = freshness.verify_report(report)[0]
    assert verdict.verdict == "unsupported"
    assert verdict.evidence_url == ""


@pytest.mark.parametrize(
    ("claim_ts", "item_ts", "expected"),
    [
        # Timezone offset: 12:00-05:00 == 17:00Z > 15:00Z (despite lexicographic '<')
        ("2026-11-20T15:00:00Z", "2026-11-20T12:00:00-05:00", "contradicted"),
        # Reverse timezone offset: 15:00Z < 17:00Z (despite lexicographic '>')
        ("2026-11-20T12:00:00-05:00", "2026-11-20T15:00:00Z", "unsupported"),
        # Date-only claim vs same-day datetime: NOT strictly newer
        ("2026-11-20", "2026-11-20T00:00:00Z", "unsupported"),
        ("2026-11-20", "2026-11-20T23:59:59Z", "unsupported"),
        # Date-only claim vs next-day datetime: strictly newer
        ("2026-11-20", "2026-11-21T00:00:00Z", "contradicted"),
        # Date-only claim vs tz-shifted next UTC day (22:00-05:00 == next day 03:00Z)
        ("2026-11-20", "2026-11-20T22:00:00-05:00", "contradicted"),
        # Datetime claim vs same-day date-only item: NOT strictly newer
        ("2026-11-20T08:00:00Z", "2026-11-20", "unsupported"),
        # Datetime claim vs next-day date-only item: strictly newer
        ("2026-11-20T23:00:00Z", "2026-11-21", "contradicted"),
        # Equal UTC instants in different offset formats: NOT strictly newer
        ("2026-11-20T15:00:00Z", "2026-11-20T10:00:00-05:00", "unsupported"),
    ],
)
def test_high096_timestamp_parsing_tz_offsets_and_date_only_precision(claim_ts, item_ts, expected):
    original = _item(
        "reddit",
        item_id="R1",
        url="https://reddit.com/r/dev/orig",
        title="Widget API is open",
        published_at=claim_ts,
    )
    newer = _item(
        "grounding",
        item_id="W1",
        url="https://status.example.com/update",
        title="Widget API is closed",
        published_at=item_ts,
    )
    report = _report(original)
    report.items_by_source["grounding"] = [newer]

    verdict = freshness.verify_report(report)[0]
    assert verdict.verdict == expected


def test_high096_became_outranks_was_and_conflicting_was_remains_unsupported():
    original = _item(
        "reddit",
        item_id="R1",
        url="https://reddit.com/r/dev/orig",
        title="Service X is open",
        published_at="2026-11-01T10:00:00Z",
    )

    # "was closed ... became open" agrees with "is open" -> NOT contradicted.
    recovered = _item(
        "grounding",
        item_id="W1",
        url="https://status.example.com/recovered",
        title="Service X was closed yesterday and Service X became open today",
        published_at="2026-11-10T10:00:00Z",
    )
    report1 = _report(original)
    report1.items_by_source["grounding"] = [recovered]
    assert freshness.verify_report(report1)[0].verdict == "unsupported"

    # "was open ... became closed" contradicts "is open".
    outage = _item(
        "grounding",
        item_id="W2",
        url="https://status.example.com/outage",
        title="Service X was open yesterday and Service X became closed today",
        published_at="2026-11-10T10:00:00Z",
    )
    report2 = _report(original)
    report2.items_by_source["grounding"] = [outage]
    v2 = freshness.verify_report(report2)[0]
    assert v2.verdict == "contradicted"
    assert v2.evidence_url == outage.url

    # Conflicting historical "was" assertions (len == 2 at priority 0) must NOT contradict.
    ambiguous = _item(
        "grounding",
        item_id="W3",
        url="https://status.example.com/ambiguous",
        title="Service X was closed earlier and Service X was open later",
        published_at="2026-11-10T10:00:00Z",
    )
    report3 = _report(original)
    report3.items_by_source["grounding"] = [ambiguous]
    assert freshness.verify_report(report3)[0].verdict == "unsupported"


@pytest.mark.parametrize("reverse_order", [False, True])
def test_high096_same_day_ties_aggregate_all_matching_items_order_independently(reverse_order):
    original = _item(
        "reddit",
        item_id="R1",
        url="https://reddit.com/r/dev/orig",
        title="Widget API is open",
        published_at="2026-11-01",
    )
    historical_same_day = _item(
        "grounding",
        item_id="W1",
        url="https://example.com/recap",
        title="Widget API was open last week",
        published_at="2026-11-10",
    )
    current_same_day = _item(
        "grounding",
        item_id="W2",
        url="https://example.com/outage",
        title="Widget API is closed today",
        published_at="2026-11-10",
    )
    items = [historical_same_day, current_same_day]
    if reverse_order:
        items.reverse()
    report = _report(original)
    report.items_by_source["grounding"] = items

    verdict = freshness.verify_report(report)[0]
    assert verdict.verdict == "contradicted"
    assert verdict.evidence_url == current_same_day.url

    # Same-day conflicting equal-priority items remain unsupported regardless of order.
    open_same_day = _item(
        "grounding",
        item_id="W3",
        url="https://example.com/still-open",
        title="Widget API is open today",
        published_at="2026-11-10",
    )
    conflict_items = [open_same_day, current_same_day]
    if reverse_order:
        conflict_items.reverse()
    conflict_report = _report(original)
    conflict_report.items_by_source["grounding"] = conflict_items
    assert freshness.verify_report(conflict_report)[0].verdict == "unsupported"


def test_high096_colliding_positional_item_id_does_not_skip_newer_contradiction():
    original = _item(
        "x",
        item_id="X1",
        url="https://x.com/usera/status/100",
        title="Widget API is open",
        published_at="2026-11-01T10:00:00Z",
    )
    newer_same_id = _item(
        "x",
        item_id="X1",
        url="https://x.com/userb/status/200",
        title="Widget API is closed",
        published_at="2026-11-05T10:00:00Z",
    )
    report = _report(original)
    report.items_by_source["x"] = [original, newer_same_id]

    verdict = freshness.verify_report(report)[0]
    assert verdict.verdict == "contradicted"
    assert verdict.evidence_url == newer_same_id.url


# ---------------------------------------------------------------------------
# HIGH-194: All-caps acronym requirement & _NOISE_WORDS exclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "market_text"),
    [
        ("World Health Organization", "Who will win the 2026 Super Bowl?"),
        ("Wireless Internet Network", "Will Team Alpha win the championship?"),
        ("Artificial Intelligence Regulation", "US air strike on Iran by June?"),
        ("Nvidia Earnings Warning", "New York City mayoral race"),
        ("Tariff Hearing Event", "The Federal Reserve cuts rates"),
        ("Federal Oil Reserve", "Vote for Senate spending bill"),
        # Lowercase acronym in market text must NOT trigger acronym credit.
        ("Artificial General Intelligence", "Will labs reach agi before 2030?"),
        # _NOISE_WORDS initialisms must NOT match even when the title is ALL-CAPS.
        ("World Health Organization", "WHO WILL WIN THE 2026 SUPER BOWL?"),
        ("Wireless Internet Network", "WILL TEAM ALPHA WIN THE CHAMPIONSHIP?"),
        ("Tariff Hearing Event", "THE FEDERAL RESERVE CUTS RATES"),
        ("Federal Oil Reserve", "FOR IMMEDIATE RELEASE: RATE CUT"),
    ],
)
def test_high194_false_positive_initialisms_are_rejected(topic, market_text):
    matcher = polymarket.EntityMatcher(topic)
    market = {"question": market_text, "groupItemTitle": "", "outcomes": '["Yes", "No"]'}
    assert matcher.match(market, market_text) is None
    assert polymarket.filter_items_against_topic(topic, [{"title": market_text}]) == []


@pytest.mark.parametrize(
    ("topic", "positive_text", "negative_text"),
    [
        ("Artificial General Intelligence", "AGI by 2030?", "Will agi arrive by 2030?"),
        ("National Basketball Association", "NBA Finals 2026 winner?", "nba finals 2026"),
        ("Consumer Price Index", "Will CPI rise above 3%?", "cpi estimate for June"),
        ("Securities Exchange Commission", "Will the SEC approve the ETF?", "in 30 sec flat"),
    ],
)
def test_high194_legitimate_all_caps_acronyms_still_match(topic, positive_text, negative_text):
    matcher = polymarket.EntityMatcher(topic)
    pos_market = {"question": positive_text, "groupItemTitle": "", "outcomes": '["Yes", "No"]'}
    neg_market = {"question": negative_text, "groupItemTitle": "", "outcomes": '["Yes", "No"]'}
    assert matcher.match(pos_market, positive_text) is not None
    assert matcher.match(neg_market, negative_text) is None
    assert len(polymarket.filter_items_against_topic(topic, [{"title": positive_text}])) == 1
    assert polymarket.filter_items_against_topic(topic, [{"title": negative_text}]) == []


# ---------------------------------------------------------------------------
# HIGH-195: Multi-market Yes/No synthesis & include_all_outcomes / RACE_PRICE_FLOOR
# ---------------------------------------------------------------------------


def _binary_submarket(question: str, label: str, yes: float, *, closed: bool = False, liquidity: str = "500"):
    return {
        "active": not closed,
        "closed": closed,
        "question": question,
        "groupItemTitle": label,
        "liquidity": "0" if closed else liquidity,
        "volume": "5000",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": json.dumps([str(yes), str(round(1.0 - yes, 4))]),
        "endDate": "2026-12-31T00:00:00Z",
    }


def test_high195_resolved_multi_market_loser_verifies_as_stale_not_unsupported(monkeypatch):
    resolved_event = {
        "id": "901",
        "slug": "race-resolved",
        "title": "Who wins the race?",
        "active": False,
        "closed": True,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will Alice win the race?", "Alice", 1.0, closed=True),
            _binary_submarket("Will Bob win the race?", "Bob", 0.0, closed=True),
            _binary_submarket("Will Carol win the race?", "Carol", 0.0, closed=True),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: resolved_event)
    saved = _item(
        "polymarket",
        title="Who wins the race?",
        url="https://polymarket.com/event/race-resolved",
        metadata={
            "event_id": "901",
            "outcome_prices": [["Alice", 0.50], ["Bob", 0.15], ["Carol", 0.35]],
        },
    )

    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )

    by_claim = {v.original_value: v for v in verdicts}
    assert by_claim[0.50].verdict == "stale"
    assert by_claim[0.50].current_value == 1.0
    assert by_claim[0.15].verdict == "stale"
    assert by_claim[0.15].current_value == 0.0
    assert by_claim[0.15].detail == "moved: 15% -> 0%"
    assert by_claim[0.35].verdict == "stale"
    assert by_claim[0.35].current_value == 0.0


def test_high195_active_sub_floor_and_exact_floor_outcomes_preserved_on_refetch(monkeypatch):
    event = {
        "id": "902",
        "slug": "race-active",
        "title": "Active race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will Favorite win the race?", "Favorite", 0.60),
            _binary_submarket("Will ExactFloor win the race?", "ExactFloor", polymarket.RACE_PRICE_FLOOR),
            _binary_submarket("Will Longshot win the race?", "Longshot", 0.002),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "902"}, url="", item_id="pm-902")

    # Normal display parse (include_all_outcomes=False, no topic) keeps >= RACE_PRICE_FLOOR and drops 0.002.
    [normal] = polymarket.parse_polymarket_response({"events": [event]})
    normal_names = [name for name, _p in normal["outcome_prices"]]
    assert "Favorite" in normal_names
    assert "ExactFloor" in normal_names
    assert "Longshot" not in normal_names

    # Refetch (include_all_outcomes=True) preserves both ExactFloor (0.005) and Longshot (0.002).
    assert polymarket.refetch_datum(item, "ExactFloor")["value"] == polymarket.RACE_PRICE_FLOOR
    assert polymarket.refetch_datum(item, "Longshot")["value"] == 0.002


# ---------------------------------------------------------------------------
# HIGH-196: _parse_outcome_prices robustness & token-0 quote isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcomes", "prices", "expected"),
    [
        ('["Yes", "No"]', "123", []),
        ('["Yes", "No"]', "{}", []),
        ('["Yes", "No"]', 123, []),
        ('["Yes", "No"]', {"Yes": 0.5}, []),
        ('["Yes", "No"]', True, []),
        ("123", '["0.6", "0.4"]', [("Outcome 1", 0.6), ("Outcome 2", 0.4)]),
        ("{}", '["0.6", "0.4"]', [("Outcome 1", 0.6), ("Outcome 2", 0.4)]),
        (123, '["0.6", "0.4"]', [("Outcome 1", 0.6), ("Outcome 2", 0.4)]),
    ],
)
def test_high196_non_list_json_or_raw_values_do_not_crash(outcomes, prices, expected):
    market = {"outcomes": outcomes, "outcomePrices": prices}
    assert polymarket._parse_outcome_prices(market) == expected


@pytest.mark.parametrize(
    "bad_first_price",
    [None, "bad", True, False, "NaN", "Infinity", "-Infinity", "1e500", -0.05, 1.05, 10**400],
)
def test_high196_malformed_first_price_never_attributes_token0_quote_to_second_outcome(
    bad_first_price,
):
    market = {
        "outcomes": ["Yes", "No"],
        "outcomePrices": [bad_first_price, "0.35"],
        "bestBid": 0.01,
        "bestAsk": 0.99,
        "spread": 0.98,
        "lastTradePrice": 0.65,
    }
    assert polymarket._parse_outcome_prices(market, book_aware=True) == []
    # Even with a tight book (midpoint path), token-0 was missing so book_aware returns [].
    tight_market = {
        "outcomes": ["Yes", "No"],
        "outcomePrices": [bad_first_price, "0.35"],
        "bestBid": 0.64,
        "bestAsk": 0.66,
        "spread": 0.02,
    }
    assert polymarket._parse_outcome_prices(tight_market, book_aware=True) == []


def test_high196_rejects_bool_non_finite_overflow_and_out_of_range_while_keeping_bounds():
    market = {
        "outcomes": ["Zero", "BoolT", "BoolF", "NaN", "Inf", "Neg", "Over", "Big", "One"],
        "outcomePrices": [
            "0.0",
            True,
            False,
            float("nan"),
            float("inf"),
            -0.01,
            1.01,
            10**400,
            "1.0",
        ],
    }
    assert polymarket._parse_outcome_prices(market, book_aware=False) == [
        ("Zero", 0.0),
        ("One", 1.0),
    ]


def test_high196_three_outcome_market_with_skipped_middle_does_not_overwrite_third():
    market = {
        "outcomes": ["A", "B", "C"],
        "outcomePrices": ["0.5", None, "0.2"],
        "bestBid": 0.10,
        "bestAsk": 0.90,
        "spread": 0.80,
        "lastTradePrice": 0.60,
    }
    assert polymarket._parse_outcome_prices(market, book_aware=True) == [
        ("A", 0.60),
        ("C", 0.20),
    ]
