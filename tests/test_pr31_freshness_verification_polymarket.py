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


def test_high095_ambiguous_split_returns_unsupported_consistently_on_direct_and_cached(
    monkeypatch,
):
    """When a previously unique unsuffixed outcome ('Yes') now appears multiple times
    ('Yes\\x1f0' and 'Yes\\x1f1'), BOTH direct refetch_datum and cached verify_report
    must return unsupported rather than arbitrarily matching occurrence 0."""
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
    item = SimpleNamespace(metadata={"event_id": "556"}, url="", item_id="pm-556")

    # Direct fetch: ambiguous unsuffixed "yes" raises DatumNotFound, while explicit "yes\x1f0" succeeds.
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item, "yes")
    assert polymarket.refetch_datum(item, "yes\x1f0")["value"] == 0.42
    assert polymarket.refetch_datum(item, "yes\x1f1")["value"] == 0.28

    # Cached lookup matches direct fetch identically using only 1 HTTP call.
    calls.clear()
    verdicts = freshness.verify_report(
        _report(market),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "unsupported", "current"]

    # When a snapshot carries a sole \x1f0 entry (no sibling \x1f1), unsuffixed lookup resolves it.
    assert polymarket.resolve_snapshot_value({"Yes\x1f0": 0.42}, "yes") == 0.42
    assert polymarket.resolve_snapshot_value({"Yes\x1f0": 0.42, "Yes\x1f1": 0.28}, "yes") is None


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


# ---------------------------------------------------------------------------
# Greptile #1612 review fixes (6 regression tests)
# ---------------------------------------------------------------------------


def test_greptile1_collapsed_duplicate_matches_identically_on_direct_and_cached_lookup(
    monkeypatch,
):
    """When two saved `Yes` rows collapse to one live `Yes` row (without a book drop),
    direct fetch and cached lookup must agree on both `Yes\\x1f0` and `Yes\\x1f1`."""
    event = {
        "id": "701",
        "slug": "collapse-yes",
        "title": "Collapsed Yes rows",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                "active": True,
                "closed": False,
                "question": "Collapsed Yes rows",
                "liquidity": "5000",
                "outcomes": '["Alpha", "Yes"]',
                "outcomePrices": '["0.35", "0.65"]',
            }
        ],
    }
    calls: list[int] = []
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)
    item = SimpleNamespace(metadata={"event_id": "701"}, url="", item_id="pm-701")

    # Direct fetch: Yes\x1f0 resolves to 0.65; Yes\x1f1 raises DatumNotFound.
    assert polymarket.refetch_datum(item, "Yes\x1f0")["value"] == 0.65
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item, "Yes\x1f1")

    # Cached lookup after another claim ("Alpha") fetches the event first:
    # must match direct fetch identically using only 1 HTTP call.
    calls.clear()
    saved = _item(
        "polymarket",
        title="Collapsed Yes rows",
        url="https://polymarket.com/event/collapse-yes",
        metadata={
            "event_id": "701",
            "outcome_prices": [["Alpha", 0.35], ["Yes", 0.65], ["Yes", 0.20]],
        },
    )
    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "current", "unsupported"]

    # Reverse order (Yes\x1f0 fetched directly first, Alpha + Yes\x1f1 cached): same verdicts.
    calls.clear()
    saved_first = _item(
        "polymarket",
        title="Collapsed Yes rows",
        url="https://polymarket.com/event/collapse-yes",
        metadata={
            "event_id": "701",
            "outcome_prices": [["Yes", 0.65], ["Yes", 0.20], ["Alpha", 0.35]],
        },
    )
    verdicts_first = freshness.verify_report(
        _report(saved_first),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts_first] == ["current", "unsupported", "current"]


def test_greptile1_book_dropped_repeat_still_blocked_on_cached_lookup(monkeypatch):
    """When a sibling repeat was dropped by the book check, cached lookup must
    also block `Bitcoin\\x1f0` and `Bitcoin\\x1f1` just like direct fetch."""
    question = "Will Bitcoin reach $150k in November?"
    event = {
        "id": "702",
        "slug": "bitcoin-november",
        "title": "Bitcoin in November",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                "active": True,
                "closed": False,
                "question": "Will Ethereum reach $10k in November?",
                "groupItemTitle": "ETH",
                "liquidity": "500",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.25", "0.75"]',
                "bestBid": 0.24,
                "bestAsk": 0.26,
                "spread": 0.02,
            },
            {
                "active": True,
                "closed": False,
                "question": question,
                "groupItemTitle": "A",
                "liquidity": "500",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.60", "0.40"]',
                "bestBid": 0.01,
                "bestAsk": 0.90,
                "spread": 0.89,
            },
            {
                "active": True,
                "closed": False,
                "question": question,
                "groupItemTitle": "B",
                "liquidity": "500",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.30", "0.70"]',
                "bestBid": 0.29,
                "bestAsk": 0.31,
                "spread": 0.02,
            },
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    legacy = polymarket._legacy_shorten_question(question).strip()
    saved = _item(
        "polymarket",
        title="Bitcoin in November",
        url="https://polymarket.com/event/bitcoin-november",
        metadata={
            "event_id": "702",
            # "ETH" fetches and populates cache first; both legacy repeats must still fail closed.
            "outcome_prices": [["ETH", 0.25], [legacy, 0.60], [legacy, 0.30]],
        },
    )
    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in verdicts] == ["current", "unsupported", "unsupported"]


def test_greptile2_shared_group_title_uses_subject_so_swapped_prices_never_switch_markets(
    monkeypatch,
):
    """Two child markets sharing groupItemTitle='OpenAI' (GPT-5 at 0.60, Sora 2 at 0.002):
    full-event collision detection labels GPT-5 unambiguously as 'GPT-5' even during
    display parsing, so verification tracks GPT-5 accurately whether unchanged or swapped,
    and an older saved claim with ambiguous 'OpenAI' returns unsupported instead of
    silently attributing Sora 2's swapped price to GPT-5."""
    event = {
        "id": "703",
        "slug": "ai-launches",
        "title": "AI launches in Q4?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will GPT-5 win the benchmark?", "OpenAI", 0.60),
            _binary_submarket("Will Sora 2 win the benchmark?", "OpenAI", 0.002),
            _binary_submarket("Will Claude 5 win the benchmark?", "Anthropic", 0.40),
        ],
    }
    # Display parse detects the full-event groupItemTitle collision and saves "GPT-5" (not "OpenAI").
    [display_item] = polymarket.parse_polymarket_response({"events": [event]})
    assert display_item["outcome_prices"] == [("GPT-5", 0.60), ("Anthropic", 0.40)]

    saved = _item(
        "polymarket",
        title=display_item["title"],
        url="https://polymarket.com/event/ai-launches",
        metadata={
            "event_id": "703",
            "outcome_prices": [list(pair) for pair in display_item["outcome_prices"]],
        },
    )

    # Unchanged prices -> both current, and sub-floor Sora 2 is also directly refetchable.
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "703"}, url="", item_id="pm-703")
    assert polymarket.refetch_datum(item, "GPT-5")["value"] == 0.60
    assert polymarket.refetch_datum(item, "Sora 2")["value"] == 0.002
    assert polymarket.refetch_datum(item, "Anthropic")["value"] == 0.40
    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in verdicts] == ["current", "current"]

    # Swapped prices (GPT-5 drops to 0.002, Sora 2 rises to 0.60):
    # GPT-5 claim must be stale (60% -> 0.2%), NOT current!
    swapped_event = {
        **event,
        "markets": [
            _binary_submarket("Will GPT-5 win the benchmark?", "OpenAI", 0.002),
            _binary_submarket("Will Sora 2 win the benchmark?", "OpenAI", 0.60),
            _binary_submarket("Will Claude 5 win the benchmark?", "Anthropic", 0.40),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: swapped_event)
    swapped_verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in swapped_verdicts] == ["stale", "current"]
    assert swapped_verdicts[0].current_value == 0.002

    # A pre-PR saved claim carrying ambiguous "OpenAI" on this 2-market event
    # must return unsupported rather than reading Sora 2's swapped 0.60 as current.
    legacy_saved = _item(
        "polymarket",
        title=display_item["title"],
        url="https://polymarket.com/event/ai-launches",
        metadata={"event_id": "703", "outcome_prices": [["OpenAI", 0.60]]},
    )
    legacy_verdicts = freshness.verify_report(
        _report(legacy_saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in legacy_verdicts] == ["unsupported"]


def test_greptile_current_name_occurrence_zero_beats_legacy_cut_repeats(monkeypatch):
    """Verify current-over-legacy namespace precedence in resolve_snapshot_value AND
    that cross-market ambiguous labels without outcome_market_ids fail closed while
    new reports carrying outcome_market_ids resolve directly by market ID."""
    # Direct unit test on resolve_snapshot_value: current Apple wins over legacy Apple\x1f0,
    # and Apple\x1f1 does not leak into legacy_values when current namespace owns 'Apple'.
    assert (
        polymarket.resolve_snapshot_value(
            {"Apple": 0.70},
            "Apple\x1f0",
            legacy_values={"Apple\x1f0": 0.20, "Apple\x1f1": 0.10},
        )
        == 0.70
    )
    assert (
        polymarket.resolve_snapshot_value(
            {"Apple": 0.70},
            "Apple\x1f1",
            legacy_values={"Apple\x1f0": 0.20, "Apple\x1f1": 0.10},
        )
        is None
    )

    event = {
        "id": "707",
        "slug": "apple-markets",
        "title": "Apple milestones?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Tim Cook remain CEO of the company?", "Apple", 0.70), "id": "m-tim"},
            {**_binary_submarket("Will Apple reach a $4T market cap?", "$4T", 0.20), "id": "m-4t"},
            {**_binary_submarket("Will Apple reach a $5T market cap?", "$5T", 0.10), "id": "m-5t"},
            {**_binary_submarket("Will OldCut win the legacy milestone?", "Milestone", 0.30), "id": "m-old"},
        ],
    }
    calls: list[int] = []
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)
    item_no_ids = SimpleNamespace(metadata={"event_id": "707"}, url="", item_id="pm-707")

    # Without outcome_market_ids, 'Apple' is cross-market ambiguous across m-tim (groupItemTitle)
    # and m-4t/m-5t (legacy cut) -> unsupported; unambiguous legacy cuts ('OldCut', 'Tim Cook') resolve.
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item_no_ids, "Apple")
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item_no_ids, "Apple\x1f0")
    assert polymarket.refetch_datum(item_no_ids, "OldCut")["value"] == 0.30
    assert polymarket.refetch_datum(item_no_ids, "Tim Cook")["value"] == 0.70

    # With outcome_market_ids (new report), 'Apple' resolves directly via m-tim -> 0.70
    # identically on direct refetch_datum and cached verify_report, even if m-5t is book-dropped.
    calls.clear()
    saved_with_ids = _item(
        "polymarket",
        title="Apple milestones?",
        url="https://polymarket.com/event/apple-markets",
        metadata={
            "event_id": "707",
            "outcome_prices": [["Milestone", 0.30], ["Apple", 0.70], ["$4T", 0.20]],
            "outcome_market_ids": ["m-old", "m-tim", "m-4t"],
        },
    )
    verdicts = freshness.verify_report(
        _report(saved_with_ids),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "current", "current"]
    assert verdicts[1].current_value == 0.70

    dropped_legacy_event = {
        **event,
        "markets": [
            {**_binary_submarket("Will Tim Cook remain CEO of the company?", "Apple", 0.70), "id": "m-tim"},
            {**_binary_submarket("Will Apple reach a $4T market cap?", "$4T", 0.20), "id": "m-4t"},
            {
                **_binary_submarket("Will Apple reach a $5T market cap?", "$5T", 0.10),
                "id": "m-5t",
                "bestBid": 0.01,
                "bestAsk": 0.99,
                "spread": 0.98,
            },
            {**_binary_submarket("Will OldCut win the legacy milestone?", "Milestone", 0.30), "id": "m-old"},
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: dropped_legacy_event)
    assert polymarket.refetch_datum(saved_with_ids, "Apple")["value"] == 0.70


def test_greptile_r3_three_market_subject_group_title_cross_collision(monkeypatch):
    """Greptile R3 P1:
    - Market 1: 'Will GPT-5 win?' (subject 'GPT-5'), groupItemTitle='OpenAI', price 0.60
    - Market 2: 'Will Sora win?' (subject 'Sora'), groupItemTitle='OpenAI', price 0.20
    - Market 3: 'Will OpenAI release hardware?' (subject 'OpenAI'), groupItemTitle='GPT-5', price 0.002
    Full-event disambiguation prevents Market 1 and Market 3 from both taking 'GPT-5',
    keeping labels stable between display parsing and refetch even if Market 3 trades above Market 1."""
    event = {
        "id": "708",
        "slug": "openai-products",
        "title": "OpenAI milestones?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will GPT-5 win?", "OpenAI", 0.60),
            _binary_submarket("Will Sora win?", "OpenAI", 0.20),
            _binary_submarket("Will OpenAI release hardware?", "GPT-5", 0.002),
        ],
    }
    [display_item] = polymarket.parse_polymarket_response({"events": [event]})
    # Market 1 must NOT take 'GPT-5' (collides with Market 3's groupItemTitle='GPT-5').
    assert display_item["outcome_prices"] == [("Will GPT-5 win", 0.60), ("Sora", 0.20)]

    saved = _item(
        "polymarket",
        title=display_item["title"],
        url="https://polymarket.com/event/openai-products",
        metadata={
            "event_id": "708",
            "outcome_prices": [list(pair) for pair in display_item["outcome_prices"]],
        },
    )

    # Market 3 surges to 0.85 (above Market 1's 0.60): Market 1 must stay 'current' at 0.60,
    # NOT read Market 3's 0.85!
    surged_event = {
        **event,
        "markets": [
            _binary_submarket("Will GPT-5 win?", "OpenAI", 0.60),
            _binary_submarket("Will Sora win?", "OpenAI", 0.20),
            _binary_submarket("Will OpenAI release hardware?", "GPT-5", 0.85),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: surged_event)
    item = SimpleNamespace(metadata={"event_id": "708"}, url="", item_id="pm-708")
    assert polymarket.refetch_datum(item, "Will GPT-5 win")["value"] == 0.60
    assert polymarket.refetch_datum(item, "Will OpenAI release hardware")["value"] == 0.85

    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in verdicts] == ["current", "current"]
    assert verdicts[0].current_value == 0.60


def test_greptile_r3_unsuffixed_legacy_shared_label_never_switches_on_price_swap(monkeypatch):
    """When an old report saved unsuffixed 'OpenAI' (0.60) for 'Will OpenAI release GPT-5?'
    while 'Will OpenAI release Sora?' was 0.002, and prices later swap (Sora=0.60, GPT-5=0.002),
    unsuffixed 'OpenAI' must return unsupported instead of reading Sora's swapped 0.60 via legacy OpenAI\\x1f0."""
    swapped_event = {
        "id": "709",
        "slug": "openai-releases",
        "title": "OpenAI releases?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will OpenAI release GPT-5?", "OpenAI", 0.002),
            _binary_submarket("Will OpenAI release Sora?", "OpenAI", 0.60),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: swapped_event)
    item = SimpleNamespace(metadata={"event_id": "709"}, url="", item_id="pm-709")

    # Direct fetch: unsuffixed 'OpenAI' is ambiguous across legacy OpenAI\x1f0 and OpenAI\x1f1.
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item, "OpenAI")

    # Cached verify_report also returns unsupported (never marks GPT-5 'current' from Sora's 0.60).
    old_saved = _item(
        "polymarket",
        title="OpenAI releases?",
        url="https://polymarket.com/event/openai-releases",
        metadata={"event_id": "709", "outcome_prices": [["OpenAI", 0.60]]},
    )
    verdicts = freshness.verify_report(
        _report(old_saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in verdicts] == ["unsupported"]

    # Newly generated report labels both unambiguously by whole question, so swapped prices mark stale!
    initial_event = {
        **swapped_event,
        "markets": [
            _binary_submarket("Will OpenAI release GPT-5?", "OpenAI", 0.60),
            _binary_submarket("Will OpenAI release Sora?", "OpenAI", 0.002),
        ],
    }
    [display_item] = polymarket.parse_polymarket_response({"events": [initial_event]})
    assert display_item["outcome_prices"] == [("Will OpenAI release GPT-5", 0.60)]
    new_saved = _item(
        "polymarket",
        title="OpenAI releases?",
        url="https://polymarket.com/event/openai-releases",
        metadata={"event_id": "709", "outcome_prices": [list(p) for p in display_item["outcome_prices"]]},
    )
    new_verdicts = freshness.verify_report(
        _report(new_saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in new_verdicts] == ["stale"]
    assert new_verdicts[0].current_value == 0.002


def test_greptile_r4_cross_market_group_title_vs_legacy_subject_returns_unsupported(monkeypatch):
    """Greptile R4 P1:
    - Market A: 'Will Alice win?', groupItemTitle='Bob', price 0.60
    - Market B: 'Will Bob win?', groupItemTitle='', price 0.002
    An older report could have saved Market A's 0.60 under 'Bob' (its groupItemTitle)
    when Market B was below the display floor. Refetch must NOT fall back to Market B's
    legacy 'Bob' (0.002) and mark Market A stale; it must return unsupported on both
    direct fetch and cached lookup, while unambiguous legacy names ('Alice') and
    legitimate legacy cut names still resolve cleanly."""
    event = {
        "id": "710",
        "slug": "alice-bob-race",
        "title": "Race winner?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will Alice win?", "Bob", 0.60),
            _binary_submarket("Will Bob win?", "", 0.002),
            _binary_submarket("Will Carol win?", "Carol Winner", 0.35),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "710"}, url="", item_id="pm-710")

    # Direct fetch: 'Bob' is cross-market ambiguous -> DatumNotFound (NOT Market B's 0.002).
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item, "Bob")
    # Unambiguous legacy names ('Alice' -> 0.60, 'Carol' -> 0.35) and current labels still resolve.
    assert polymarket.refetch_datum(item, "Alice")["value"] == 0.60
    assert polymarket.refetch_datum(item, "Carol")["value"] == 0.35
    assert polymarket.refetch_datum(item, "Will Alice win")["value"] == 0.60
    assert polymarket.refetch_datum(item, "Will Bob win")["value"] == 0.002

    # Cached verify_report (after 'Carol Winner' fetches first): 'Bob' must be unsupported,
    # NOT stale (60% -> 0.2%), while 'Alice' (unambiguous legacy) resolves to current 0.60.
    calls: list[int] = []
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)
    old_saved = _item(
        "polymarket",
        title="Race winner?",
        url="https://polymarket.com/event/alice-bob-race",
        metadata={
            "event_id": "710",
            "outcome_prices": [["Carol Winner", 0.35], ["Bob", 0.60], ["Alice", 0.60]],
        },
    )
    verdicts = freshness.verify_report(
        _report(old_saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "unsupported", "current"]

    # Also verify old saved 'GPT-5' / 'OpenAI' on the 3-market cross-collision event
    # return unsupported instead of reading the wrong market via legacy_values.
    three_market_event = {
        "id": "711",
        "slug": "openai-3mkt",
        "title": "OpenAI milestones?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Will GPT-5 win?", "OpenAI", 0.60),
            _binary_submarket("Will Sora win?", "OpenAI", 0.20),
            _binary_submarket("Will OpenAI release hardware?", "GPT-5", 0.002),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: three_market_event)
    item_3 = SimpleNamespace(metadata={"event_id": "711"}, url="", item_id="pm-711")
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item_3, "GPT-5")
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item_3, "OpenAI")
    assert polymarket.refetch_datum(item_3, "Sora")["value"] == 0.20


def test_greptile_r5_unused_group_title_on_non_subject_question_does_not_block_sibling(
    monkeypatch,
):
    """Greptile R5 P2:
    'Does Alice qualify?' has no 'Will ...' question subject, so the parser never
    names Alice's market from its groupItemTitle ('Bob'). That unused groupItemTitle
    must NOT block saved 'Bob' odds for 'Will Bob win?' (either directly or via
    legacy_values when Bob's own current label is its groupItemTitle)."""
    # Variant 1: Bob has no groupItemTitle (current label is 'Bob').
    event_plain = {
        "id": "712",
        "slug": "qualify-plain",
        "title": "Qualification?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Does Alice qualify?", "Bob", 0.60),
            _binary_submarket("Will Bob win?", "", 0.35),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event_plain)
    item_plain = SimpleNamespace(metadata={"event_id": "712"}, url="", item_id="pm-712")
    assert polymarket.refetch_datum(item_plain, "Bob")["value"] == 0.35

    # Variant 2: Bob has groupItemTitle='Bob Winner', so legacy 'Bob' MUST resolve
    # through legacy_values—proving Alice's unused groupItemTitle='Bob' did not purge it.
    event_legacy = {
        "id": "713",
        "slug": "qualify-legacy",
        "title": "Qualification?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            _binary_submarket("Does Alice qualify?", "Bob", 0.60),
            _binary_submarket("Will Bob win?", "Bob Winner", 0.35),
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event_legacy)
    item_legacy = SimpleNamespace(metadata={"event_id": "713"}, url="", item_id="pm-713")
    assert polymarket.refetch_datum(item_legacy, "Bob")["value"] == 0.35

    saved = _item(
        "polymarket",
        title="Qualification?",
        url="https://polymarket.com/event/qualify-legacy",
        metadata={
            "event_id": "713",
            "outcome_prices": [["Does Alice qualify", 0.60], ["Bob", 0.35]],
        },
    )
    verdicts = freshness.verify_report(
        _report(saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in verdicts] == ["current", "current"]
    assert verdicts[1].current_value == 0.35


def test_greptile_r6_and_r7_market_id_matching_and_legacy_cross_market_ambiguity(monkeypatch):
    """Greptile R6 (4681850135) + R7 (4681961728):
    - 'Will Alice win?' (groupItemTitle='Bob', 0.60)
    - 'Will Bob win?' (groupItemTitle='Winner', 0.35)
    - 'Does X qualify?' (groupItemTitle='Bob', 0.002)
    - 'Does Y qualify?' (groupItemTitle='Winner', 0.002)
    1. New parser keeps 'Alice' and 'Bob' AND records outcome_market_ids (preserved
       through normalize.normalize_source_items).
    2. New reports (with outcome_market_ids) match directly by market ID, verifying
       'Bob' -> 0.35 (current) and returning unsupported if that market ID disappears
       (never falling back to Alice).
    3. Older reports (without outcome_market_ids) treat 'Bob' as ambiguous because
       an older display parse (filtering out 0.002 'Does ...' siblings) could have
       saved Alice's 0.60 under 'Bob', so both direct fetch and cached lookup return
       unsupported instead of marking Alice stale against Bob's 0.35."""
    from lib import normalize

    event = {
        "id": "714",
        "slug": "alice-bob-does",
        "title": "Tournament outcomes?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Alice win?", "Bob", 0.60), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Winner", 0.35), "id": "m-bob"},
            {**_binary_submarket("Does X qualify?", "Bob", 0.002), "id": "m-x"},
            {**_binary_submarket("Does Y qualify?", "Winner", 0.002), "id": "m-y"},
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [event]})
    assert parsed["outcome_prices"] == [("Alice", 0.60), ("Bob", 0.35)]
    assert parsed["outcome_market_ids"] == ["m-alice", "m-bob"]

    # normalize preserves outcome_market_ids in SourceItem.metadata.
    [normalized_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )
    assert normalized_item.metadata["outcome_market_ids"] == ["m-alice", "m-bob"]

    calls: list[int] = []
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)

    # New report (with outcome_market_ids): direct refetch AND cached verify_report
    # both match by market ID -> Alice 0.60 (current), Bob 0.35 (current).
    assert polymarket.refetch_datum(normalized_item, "Bob")["value"] == 0.35
    assert polymarket.refetch_datum(normalized_item, "Alice")["value"] == 0.60
    calls.clear()
    new_verdicts = freshness.verify_report(
        _report(normalized_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in new_verdicts] == ["current", "current", "current"]
    assert new_verdicts[0].current_value == 0.60
    assert new_verdicts[1].current_value == 0.35

    # If Bob's specific sub-market is dropped (wide book), market_id lookup fails closed
    # to unsupported rather than falling back to a label collision.
    bob_dropped_event = {
        **event,
        "markets": [
            event["markets"][0],
            {**event["markets"][1], "bestBid": 0.01, "bestAsk": 0.99, "spread": 0.98},
            event["markets"][2],
            event["markets"][3],
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: bob_dropped_event)
    dropped_verdicts = freshness.verify_report(
        _report(normalized_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert [v.verdict for v in dropped_verdicts] == ["current", "unsupported", "current"]

    # Older report (NO outcome_market_ids): 'Bob' could have been Alice (0.60 via
    # groupItemTitle when 0.002 'Does ...' siblings were omitted) -> unsupported on
    # BOTH direct fetch and cached lookup, while unambiguous 'Alice' still resolves!
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event)
    item_no_ids = SimpleNamespace(metadata={"event_id": "714"}, url="", item_id="pm-714")
    with pytest.raises(polymarket.DatumNotFound):
        polymarket.refetch_datum(item_no_ids, "Bob")
    assert polymarket.refetch_datum(item_no_ids, "Alice")["value"] == 0.60

    calls.clear()
    old_saved = _item(
        "polymarket",
        title="Tournament outcomes?",
        url="https://polymarket.com/event/alice-bob-does",
        metadata={"event_id": "714", "outcome_prices": [["Alice", 0.60], ["Bob", 0.60]]},
    )
    old_verdicts = freshness.verify_report(
        _report(old_saved),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in old_verdicts] == ["current", "unsupported"]


def test_greptile_r8_last_remaining_active_market_still_verifies_by_market_id(monkeypatch):
    """Greptile R8 P1 (4682122363):
    When a two-market event loses one active market (closing/resolving or removed),
    parse_polymarket_response displays the lone remaining active market as raw Yes/No
    without outcome_market_ids. refetch_datum must still build market_values directly
    from eligible markets so both direct fetch and cached verification verify the
    remaining active market (and any closed sibling's resolution price) by market ID."""
    from lib import normalize

    initial_event = {
        "id": "715",
        "slug": "two-to-one",
        "title": "Two-market race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.60), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Bob", 0.35), "id": "m-bob"},
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [initial_event]})
    assert parsed["outcome_prices"] == [("Alice", 0.60), ("Bob", 0.35)]
    assert parsed["outcome_market_ids"] == ["m-alice", "m-bob"]
    [saved_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )

    # Scenario A: Bob closes (resolves to 0.0) while Alice stays active at 0.70.
    one_closed_event = {
        **initial_event,
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.70), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Bob", 0.0, closed=True), "id": "m-bob"},
        ],
    }
    # Confirm display parse now emits Yes/No without outcome_market_ids.
    [refreshed_display] = polymarket.parse_polymarket_response(
        {"events": [one_closed_event]}, include_all_outcomes=True, include_closed=False,
    )
    assert "outcome_market_ids" not in refreshed_display
    assert refreshed_display["outcome_prices"] == [("Yes", 0.70), ("No", 0.30)]

    calls: list[int] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or one_closed_event,
    )
    assert polymarket.refetch_datum(saved_item, "Alice")["value"] == 0.70
    assert polymarket.refetch_datum(saved_item, "Bob")["value"] == 0.0

    calls.clear()
    verdicts = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["stale", "stale", "current"]
    assert verdicts[0].current_value == 0.70
    assert verdicts[1].current_value == 0.0

    # Scenario B: Bob is removed completely, leaving only Alice active at 0.70.
    # Alice verifies to 0.70 (stale), Bob fails closed to unsupported.
    only_alice_event = {
        **initial_event,
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.70), "id": "m-alice"},
        ],
    }
    calls.clear()
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or only_alice_event,
    )
    verdicts_only_alice = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts_only_alice] == ["stale", "unsupported", "current"]
    assert verdicts_only_alice[0].current_value == 0.70


def test_greptile_r9_resolved_market_verifies_when_only_open_market_has_zero_liquidity(
    monkeypatch,
):
    """Greptile R9 P1 (4682261324):
    If one market resolves at 0% and the only open market has zero liquidity,
    parse_polymarket_response returns no display rows, yet refetch_datum and
    cached verify_report must still verify the resolved market by market ID."""
    from lib import normalize

    initial_event = {
        "id": "716",
        "slug": "zero-liq-plus-resolved",
        "title": "Two-market race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.40), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Bob", 0.60), "id": "m-bob"},
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [initial_event]})
    assert parsed["outcome_prices"] == [("Bob", 0.60), ("Alice", 0.40)]
    assert parsed["outcome_market_ids"] == ["m-bob", "m-alice"]
    [saved_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )

    # Alice stays open with zero liquidity; Bob resolves to 0.0 (closed=True).
    updated_event = {
        **initial_event,
        "markets": [
            {
                **_binary_submarket("Will Alice win?", "Alice", 0.50),
                "id": "m-alice",
                "liquidity": "0",
            },
            {
                **_binary_submarket("Will Bob win?", "Bob", 0.0, closed=True),
                "id": "m-bob",
                "liquidity": "0",
            },
        ],
    }
    # Confirm display parse produces no rows when open market has zero liquidity.
    assert (
        polymarket.parse_polymarket_response(
            {"events": [updated_event]}, include_all_outcomes=True, include_closed=False,
        )
        == []
    )

    calls: list[int] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or updated_event,
    )
    # Direct fetch: Bob resolves to 0.0; Alice raises DatumNotFound with snapshot.
    assert polymarket.refetch_datum(saved_item, "Bob")["value"] == 0.0
    with pytest.raises(polymarket.DatumNotFound) as exc_info:
        polymarket.refetch_datum(saved_item, "Alice")
    assert exc_info.value.snapshot is not None
    assert exc_info.value.snapshot["market_values"] == {"m-bob": 0.0}

    # Cached verify_report (Bob first, Alice second, end_date third): 1 HTTP call.
    calls.clear()
    verdicts = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["stale", "unsupported", "unsupported"]
    assert verdicts[0].current_value == 0.0
    assert verdicts[0].detail == "moved: 60% -> 0%"

    # Reverse claim order (Alice first misses and caches snapshot -> Bob still resolves).
    reversed_item = _item(
        "polymarket",
        item_id="pm-716-rev",
        url=saved_item.url,
        title=saved_item.title,
        metadata={
            **saved_item.metadata,
            "outcome_prices": [("Alice", 0.40), ("Bob", 0.60)],
            "outcome_market_ids": ["m-alice", "m-bob"],
        },
    )
    calls.clear()
    rev_verdicts = freshness.verify_report(
        _report(reversed_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in rev_verdicts] == ["unsupported", "stale", "unsupported"]
    assert rev_verdicts[1].current_value == 0.0
    assert rev_verdicts[1].detail == "moved: 60% -> 0%"


def test_greptile_r9_yes_other_child_market_verifies_with_same_yes_rule(monkeypatch):
    """Greptile R9 P2 (4682261343):
    If a child market has ['Yes', 'Other'] outcomes, parse_polymarket_response
    saves its Yes price and market_id during multi-market synthesis. refetch_datum
    and cached verify_report must accept that same Yes outcome instead of
    requiring strictly {'yes', 'no'} on every child market."""
    from lib import normalize

    event = {
        "id": "717",
        "slug": "yes-other-child",
        "title": "Mixed binary child race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                **_binary_submarket("Will Alice win?", "Alice", 0.55),
                "id": "m-alice",
                "volume": "50000",
            },
            {
                **_binary_submarket("Will Bob win?", "Bob", 0.35),
                "id": "m-bob",
                "volume": "10000",
                "outcomes": '["Yes", "Other"]',
            },
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [event]})
    assert parsed["outcome_prices"] == [("Alice", 0.55), ("Bob", 0.35)]
    assert parsed["outcome_market_ids"] == ["m-alice", "m-bob"]
    [saved_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )

    calls: list[int] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or event,
    )
    assert polymarket.refetch_datum(saved_item, "Alice")["value"] == 0.55
    assert polymarket.refetch_datum(saved_item, "Bob")["value"] == 0.35

    calls.clear()
    verdicts = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "current", "current"]
    assert verdicts[1].current_value == 0.35


def test_greptile_r10_closed_unsettled_market_does_not_mark_old_odds_current(monkeypatch):
    """Greptile R10 P1 (4682403571):
    A closed child market that stopped trading before settling still carries its
    pre-close odds (e.g. 60%/40%). Neither the empty-display fallback nor the
    market_values map may mark a saved 60% claim current without 0/1 settlement
    proof; a settled closed sibling (0%/100%) still verifies."""
    from lib import normalize

    initial_event = {
        "id": "718",
        "slug": "closed-unsettled-vs-settled",
        "title": "Three-way race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.60), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Bob", 0.25), "id": "m-bob"},
            {**_binary_submarket("Will Carol win?", "Carol", 0.15), "id": "m-carol"},
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [initial_event]})
    assert parsed["outcome_prices"] == [("Alice", 0.60), ("Bob", 0.25), ("Carol", 0.15)]
    assert parsed["outcome_market_ids"] == ["m-alice", "m-bob", "m-carol"]
    [saved_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )

    # Scenario A: No display rows (Carol open w/ zero liquidity), Alice closed
    # before settling at old 60%/40% odds, Bob settled at 0%/100%.
    mixed_closed_event = {
        **initial_event,
        "markets": [
            {
                **_binary_submarket("Will Alice win?", "Alice", 0.60, closed=True),
                "id": "m-alice",
                "liquidity": "0",
            },
            {
                **_binary_submarket("Will Bob win?", "Bob", 0.0, closed=True),
                "id": "m-bob",
                "liquidity": "0",
            },
            {
                **_binary_submarket("Will Carol win?", "Carol", 0.15),
                "id": "m-carol",
                "liquidity": "0",
            },
        ],
    }
    calls: list[int] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or mixed_closed_event,
    )
    with pytest.raises(polymarket.DatumNotFound) as exc_info:
        polymarket.refetch_datum(saved_item, "Alice")
    assert exc_info.value.snapshot is not None
    assert exc_info.value.snapshot["market_values"] == {"m-bob": 0.0}
    assert polymarket.refetch_datum(saved_item, "Bob")["value"] == 0.0

    calls.clear()
    verdicts = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == [
        "unsupported",  # Alice: closed but unsettled 60%/40% must NOT pass as current
        "stale",        # Bob: settled 0%/100% verifies 25% -> 0%
        "unsupported",  # Carol: open with zero liquidity
        "unsupported",  # end_date
    ]
    assert verdicts[1].current_value == 0.0

    # Scenario B: Closed market with 0/1 prices but umaResolutionStatus='proposed'
    # (not yet finalized) is also rejected, while 'resolved' is accepted.
    proposed_vs_resolved_event = {
        **initial_event,
        "markets": [
            {
                **_binary_submarket("Will Alice win?", "Alice", 1.0, closed=True),
                "id": "m-alice",
                "liquidity": "0",
                "umaResolutionStatus": "proposed",
            },
            {
                **_binary_submarket("Will Bob win?", "Bob", 0.0, closed=True),
                "id": "m-bob",
                "liquidity": "0",
                "umaResolutionStatus": "resolved",
            },
            {
                **_binary_submarket("Will Carol win?", "Carol", 0.15),
                "id": "m-carol",
                "liquidity": "0",
            },
        ],
    }
    calls.clear()
    monkeypatch.setattr(
        polymarket.http,
        "request",
        lambda *_a, **_k: calls.append(1) or proposed_vs_resolved_event,
    )
    verdicts_uma = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts_uma] == [
        "unsupported",
        "stale",
        "unsupported",
        "unsupported",
    ]
    assert verdicts_uma[1].current_value == 0.0


def test_greptile_r10_omitted_question_still_verifies_by_unique_market_id(monkeypatch):
    """Greptile R10 P2 (4682403585):
    If Gamma omits or blanks 'question' on a child market while returning its
    unique market ID and usable Yes price, refetch_datum and cached
    verify_report must still verify the saved market_id claim."""
    from lib import normalize

    initial_event = {
        "id": "719",
        "slug": "omitted-question-recheck",
        "title": "Two-market race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {**_binary_submarket("Will Alice win?", "Alice", 0.60), "id": "m-alice"},
            {**_binary_submarket("Will Bob win?", "Bob", 0.35), "id": "m-bob"},
        ],
    }
    [parsed] = polymarket.parse_polymarket_response({"events": [initial_event]})
    assert parsed["outcome_market_ids"] == ["m-alice", "m-bob"]
    [saved_item] = normalize.normalize_source_items(
        "polymarket", [parsed], "2026-10-25", "2026-11-24",
    )

    alice_no_q = {**_binary_submarket("Will Alice win?", "Alice", 0.60), "id": "m-alice"}
    alice_no_q.pop("question")
    bob_blank_q = {**_binary_submarket("", "Bob", 0.45), "id": "m-bob"}
    updated_event = {
        **initial_event,
        "markets": [alice_no_q, bob_blank_q],
    }

    calls: list[int] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *_a, **_k: calls.append(1) or updated_event,
    )
    assert polymarket.refetch_datum(saved_item, "Alice")["value"] == 0.60
    assert polymarket.refetch_datum(saved_item, "Bob")["value"] == 0.45

    calls.clear()
    verdicts = freshness.verify_report(
        _report(saved_item),
        checked_at="2026-11-24T12:05:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    assert len(calls) == 1
    assert [v.verdict for v in verdicts] == ["current", "stale", "current"]
    assert verdicts[0].current_value == 0.60
    assert verdicts[1].current_value == 0.45







def test_greptile3_bad_price_on_top_binary_market_does_not_hide_valid_siblings():
    """If the highest-volume Yes/No market has a bad price (e.g., bool True),
    top_outcomes_are_binary must still read raw outcomes so valid siblings survive."""
    bad_top = {
        "active": True,
        "closed": False,
        "question": "Will BadTop win the race?",
        "groupItemTitle": "BadTop",
        "liquidity": "500",
        "volume": "999999",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": [True, "0.40"],
        "endDate": "2026-12-31T00:00:00Z",
    }
    valid_a = _binary_submarket("Will Alice win the race?", "Alice", 0.55)
    valid_b = _binary_submarket("Will Bob win the race?", "Bob", 0.35)
    event = {
        "id": "704",
        "slug": "bad-top-sibling",
        "title": "Who wins the race?",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [bad_top, valid_a, valid_b],
    }

    [parsed] = polymarket.parse_polymarket_response({"events": [event]})
    assert parsed["outcome_prices"] == [("Alice", 0.55), ("Bob", 0.35)]


@pytest.mark.parametrize("bad_spread", ["NaN", "inf", "-inf", True, "bad", -0.05])
def test_greptile4_malformed_spread_does_not_return_midpoint_on_wide_book(bad_spread):
    """A malformed spread (e.g., 'NaN') must be treated as missing (using ask - bid),
    never coerced to 0.0 to accept a wide 0.01/0.99 book's midpoint."""
    wide_with_trade = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.50", "0.50"]',
        "bestBid": 0.01,
        "bestAsk": 0.99,
        "spread": bad_spread,
        "lastTradePrice": 0.02,
    }
    assert polymarket._quoted_price(wide_with_trade, 0.50) == 0.02
    assert polymarket._parse_outcome_prices(wide_with_trade) == [("Yes", 0.02), ("No", 0.98)]

    wide_no_trade = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.50", "0.50"]',
        "bestBid": 0.01,
        "bestAsk": 0.99,
        "spread": bad_spread,
    }
    assert polymarket._quoted_price(wide_no_trade, 0.50) is None
    assert polymarket._parse_outcome_prices(wide_no_trade) == []

    # Tight bid/ask with bad spread still computes ask - bid = 0.08 <= 0.10 -> midpoint.
    tight_bad_spread = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.50", "0.50"]',
        "bestBid": 0.46,
        "bestAsk": 0.54,
        "spread": bad_spread,
        "lastTradePrice": 0.70,
    }
    assert polymarket._quoted_price(tight_bad_spread, 0.50) == 0.50


def test_greptile5_has_been_outranks_became_while_became_outranks_was():
    """'became closed last year; has been open since May' must not contradict 'is open'."""
    original = _item(
        "reddit",
        item_id="R1",
        url="https://reddit.com/r/dev/orig",
        title="Widget API is open",
        published_at="2026-11-01T10:00:00Z",
    )
    recovered = _item(
        "grounding",
        item_id="W1",
        url="https://status.example.com/recovery",
        title="Widget API became closed last year. Widget API has been open since May.",
        published_at="2026-11-10T10:00:00Z",
    )
    report = _report(original)
    report.items_by_source["grounding"] = [recovered]
    assert freshness.verify_report(report)[0].verdict == "unsupported"

    # Reverse: 'became open ... has been closed' contradicts 'is open'.
    relapsed = _item(
        "grounding",
        item_id="W2",
        url="https://status.example.com/relapse",
        title="Widget API became open last year. Widget API has been closed since May.",
        published_at="2026-11-10T10:00:00Z",
    )
    report2 = _report(original)
    report2.items_by_source["grounding"] = [relapsed]
    v2 = freshness.verify_report(report2)[0]
    assert v2.verdict == "contradicted"
    assert v2.evidence_url == relapsed.url


@pytest.mark.parametrize("unicode_digit", ["²", "٠", "①"])
def test_greptile6_non_ascii_digit_suffix_raises_datum_not_found_with_snapshot(
    monkeypatch, unicode_digit,
):
    """Non-ASCII unicode digits like '²' or '٠' pass str.isdigit() but must raise
    DatumNotFound with the fetched snapshot intact instead of ValueError."""
    event = {
        "id": "706",
        "slug": "unicode-occ",
        "title": "Unicode suffix check",
        "active": True,
        "closed": False,
        "updatedAt": "2026-11-24T12:00:00Z",
        "markets": [
            {
                "active": True,
                "closed": False,
                "question": "Unicode suffix check",
                "liquidity": "1000",
                "outcomes": '["OpenAI", "Anthropic"]',
                "outcomePrices": '["0.60", "0.40"]',
            }
        ],
    }
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "706"}, url="", item_id="pm-706")

    with pytest.raises(polymarket.DatumNotFound) as exc_info:
        polymarket.refetch_datum(item, f"OpenAI\x1f{unicode_digit}")
    assert exc_info.value.snapshot is not None
    assert exc_info.value.snapshot["values"]["OpenAI"] == 0.60
    assert exc_info.value.snapshot["values"]["Anthropic"] == 0.40

