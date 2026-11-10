"""Topic to company, site and ticker through Wikidata, replayed offline.

The fixture holds real Wikidata responses trimmed to the statements the
resolver reads. A row with ``edit`` changes one recorded entity to cover a
shape the live data did not have on the day it was recorded.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest import mock

import pytest

from lib import company_resolve, http, render, schema

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "wikidata_company_resolve.json").read_text(
        encoding="utf-8"
    )
)

NVIDIA = "Q182477"
ALPHABET = "Q20800404"
TOYOTA = "Q53268"
NASDAQ = "Q82059"
TOKYO = "Q217475"
AUTOMOBILE_MANUFACTURER = "Q786820"


class FakeWikidata:
    """Stands in for ``http.get`` and answers from the recorded fixture."""

    def __init__(self, entities: dict | None = None, error: Exception | None = None):
        self.entities = {**FIXTURE["entities"], **(entities or {})}
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, headers=None, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        action = params.get("action")
        if action == "wbsearchentities":
            return FIXTURE["search"].get(params["search"], {"search": [], "success": 1})
        if action == "wbgetentities":
            return {
                "entities": {
                    qid: copy.deepcopy(self.entities.get(qid, {"id": qid, "missing": ""}))
                    for qid in params["ids"].split("|")
                },
                "success": 1,
            }
        if action == "query":
            person = params["srsearch"].rsplit("=", 1)[1]
            return FIXTURE["chief_executive_of"][person]
        raise AssertionError(f"unexpected Wikidata request: {params}")


def _resolve(monkeypatch, topic: str, **fake_kwargs) -> tuple[dict, FakeWikidata]:
    fake = FakeWikidata(**fake_kwargs)
    monkeypatch.setattr(company_resolve.http, "get", fake)
    return company_resolve.resolve_company(topic), fake


def _edited(qid: str, change) -> dict:
    entity = copy.deepcopy(FIXTURE["entities"][qid])
    change(entity)
    return {qid: entity}


def _listing(entity: dict, ticker: str) -> dict:
    for statement in entity["claims"]["P414"]:
        if statement["qualifiers"]["P249"][0]["datavalue"]["value"] == ticker:
            return statement
    raise AssertionError(f"no {ticker} listing in the fixture")


def _prefer(ticker: str, exchange: str | None = None):
    def change(entity: dict) -> None:
        for statement in entity["claims"]["P414"]:
            listed_on = statement["mainsnak"]["datavalue"]["value"]["id"]
            if statement["qualifiers"]["P249"][0]["datavalue"]["value"] == ticker and (
                exchange is None or listed_on == exchange
            ):
                statement["rank"] = "preferred"
                return
        raise AssertionError(f"no {ticker} listing in the fixture")

    return change


def _end_listing(ticker: str):
    def change(entity: dict) -> None:
        _listing(entity, ticker)["qualifiers"]["P582"] = [{
            "snaktype": "value",
            "property": "P582",
            "datavalue": {
                "value": {
                    "time": "+2024-01-01T00:00:00Z", "timezone": 0, "before": 0,
                    "after": 0, "precision": 11,
                    "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
                },
                "type": "time",
            },
        }]

    return change


def _only_instance_of(class_id: str):
    def change(entity: dict) -> None:
        template = entity["claims"]["P31"][0]
        template["mainsnak"]["datavalue"]["value"] = {
            "entity-type": "item", "numeric-id": int(class_id[1:]), "id": class_id,
        }
        entity["claims"]["P31"] = [template]

    return change


NVIDIA_RESULT = {
    "company": "Nvidia",
    "company_site": "https://www.nvidia.com/",
    "ticker": "NVDA",
    "exchange": "Nasdaq",
}

# (topic, edited entities, expected result)
CASES = [
    # Company and ticker.
    pytest.param("Nvidia", None, NVIDIA_RESULT, id="nvidia"),
    pytest.param(
        "Jensen Huang", None, {**NVIDIA_RESULT, "company_via_person": True},
        id="jensen-huang-leads-nvidia",
    ),
    pytest.param(
        "Apple", None,
        # Apple's many regional sites carry no preferred one, so no site.
        {"company": "Apple Inc.", "ticker": "AAPL", "exchange": "Nasdaq"},
        id="apple-ignores-its-ended-tokyo-listing",
    ),
    # Recorded Tesla data lists four exchanges and marks none as primary, so
    # the listings are only candidates.
    pytest.param(
        "Tesla", None,
        {
            "company": "Tesla", "company_site": "https://www.tesla.com/",
            "ticker_candidates": ["TSLA", "TL0", "0R0X"],
        },
        id="tesla-four-exchanges-none-primary",
    ),
    # Company with no ticker.
    pytest.param(
        "Stripe", None,
        {"company": "Stripe", "company_site": "https://stripe.com/"},
        id="stripe-private",
    ),
    pytest.param(
        "OpenAI", None,
        {"company": "OpenAI", "company_site": "https://openai.com/"},
        id="openai-private",
    ),
    pytest.param(
        "Anthropic", None,
        {"company": "Anthropic", "company_site": "https://www.anthropic.com/"},
        id="anthropic-private",
    ),
    pytest.param(
        "Nvidia", _edited(NVIDIA, _end_listing("NVDA")),
        {"company": "Nvidia", "company_site": "https://www.nvidia.com/"},
        id="delisted-listing-has-an-end-time",
    ),
    pytest.param(
        "Patrick Collison", None,
        {
            "company": "Stripe", "company_site": "https://stripe.com/",
            "company_via_person": True,
        },
        id="person-leads-a-private-company",
    ),
    # Nothing at all.
    pytest.param("Kanye West", None, {}, id="kanye-west"),
    pytest.param("Taylor Swift", None, {}, id="taylor-swift"),
    pytest.param("Rust", None, {}, id="rust-programming-language"),
    pytest.param("Sundar Pichai", None, {}, id="person-leads-two-organizations"),
    # Two share classes on one exchange.
    pytest.param(
        "Alphabet", _edited(ALPHABET, _prefer("GOOGL")),
        {
            "company": "Alphabet Inc.", "company_site": "https://abc.xyz/",
            "ticker": "GOOGL", "exchange": "Nasdaq",
        },
        id="alphabet-class-marked-primary",
    ),
    pytest.param(
        "Alphabet", None,
        {
            "company": "Alphabet Inc.", "company_site": "https://abc.xyz/",
            "ticker_candidates": ["GOOG", "GOOGL"],
        },
        id="alphabet-no-class-marked",
    ),
    # Listings on several exchanges.
    pytest.param(
        "Toyota", _edited(TOYOTA, _prefer("7203", TOKYO)),
        {
            "company": "Toyota", "company_site": "https://global.toyota",
            "ticker": "7203", "exchange": "Tokyo Stock Exchange",
        },
        id="toyota-exchange-marked-primary",
    ),
    pytest.param(
        "Toyota", None,
        {
            "company": "Toyota", "company_site": "https://global.toyota",
            "ticker_candidates": ["7203", "TYT", "TM"],
        },
        id="toyota-no-exchange-marked",
    ),
    # An organization typed only by a kind of enterprise.
    pytest.param(
        "Toyota", _edited(TOYOTA, _only_instance_of(AUTOMOBILE_MANUFACTURER)),
        {
            "company": "Toyota", "company_site": "https://global.toyota",
            "ticker_candidates": ["7203", "TYT", "TM"],
        },
        id="typed-as-a-subclass-of-enterprise",
    ),
]


@pytest.mark.parametrize("topic,entities,expected", CASES)
def test_fixture_table(monkeypatch, topic, entities, expected):
    result, fake = _resolve(monkeypatch, topic, entities=entities)

    assert result == expected
    assert 1 <= len(fake.calls) <= 5
    for call in fake.calls:
        assert call["url"] == company_resolve.WIKIDATA_API
        assert call["timeout"] == 5
        assert call["retries"] == 1


@pytest.mark.parametrize(
    "topic",
    ["how to deploy on Fly.io", "What is Nvidia?", "best noise cancelling headphones", "nvidia stock", ""],
)
def test_no_network_when_the_topic_is_not_company_shaped(monkeypatch, topic):
    result, fake = _resolve(monkeypatch, topic)

    assert result == {}
    assert fake.calls == []


@pytest.mark.parametrize(
    "error",
    [
        http.HTTPError("Request timed out"),
        http.HTTPError("HTTP 503: Service Unavailable", status_code=503),
        TimeoutError("timed out"),
    ],
)
def test_wikidata_failure_returns_nothing_and_logs_once(monkeypatch, error):
    logged = mock.Mock()
    monkeypatch.setattr(company_resolve.log, "source_log", logged)

    result, fake = _resolve(monkeypatch, "Nvidia", error=error)

    assert result == {}
    assert isinstance(result, company_resolve.FailedLookup)
    assert len(fake.calls) == 1
    assert logged.call_count == 1
    assert logged.call_args.kwargs == {"tty_only": False}
    assert "Nvidia" in logged.call_args.args[1]


@pytest.mark.parametrize(
    "response",
    [[], {"search": "not a list"}, {"search": [{"label": "Nvidia"}]}],
)
def test_unexpected_search_shape_returns_nothing_and_logs_once(monkeypatch, response):
    logged = mock.Mock()
    monkeypatch.setattr(company_resolve.log, "source_log", logged)
    monkeypatch.setattr(company_resolve.http, "get", lambda *_args, **_kwargs: response)

    result = company_resolve.resolve_company("Nvidia")

    assert result == {}
    assert isinstance(result, company_resolve.FailedLookup)
    assert logged.call_count == 1


def test_http_429_is_a_failed_lookup(monkeypatch):
    monkeypatch.setattr(company_resolve.log, "source_log", mock.Mock())

    result, _fake = _resolve(
        monkeypatch, "Tesla",
        error=http.HTTPError("HTTP 429: Too Many Requests", status_code=429),
    )

    assert result == {}
    assert isinstance(result, company_resolve.FailedLookup)


@pytest.mark.parametrize("topic", ["Rust", "Kanye West", "Sundar Pichai"])
def test_an_answer_of_nothing_certain_is_not_a_failed_lookup(monkeypatch, topic):
    result, fake = _resolve(monkeypatch, topic)

    assert result == {}
    assert fake.calls
    assert not isinstance(result, company_resolve.FailedLookup)


def test_a_topic_that_is_not_company_shaped_is_not_a_failed_lookup(monkeypatch):
    result, fake = _resolve(monkeypatch, "how to deploy on Fly.io")

    assert fake.calls == []
    assert not isinstance(result, company_resolve.FailedLookup)


def test_a_failure_after_the_company_is_known_still_returns_nothing(monkeypatch):
    """The exchange lookup is the last call; its failure must not leak a
    half-resolved company."""
    fake = FakeWikidata()

    def flaky(url, headers=None, **kwargs):
        if kwargs["params"].get("ids") == NASDAQ:
            raise http.HTTPError("Request timed out")
        return fake(url, headers, **kwargs)

    logged = mock.Mock()
    monkeypatch.setattr(company_resolve.log, "source_log", logged)
    monkeypatch.setattr(company_resolve.http, "get", flaky)

    result = company_resolve.resolve_company("Nvidia")

    assert result == {}
    assert isinstance(result, company_resolve.FailedLookup)
    assert logged.call_count == 1


def test_each_entity_of_a_comparison_resolves_on_its_own(monkeypatch):
    fake = FakeWikidata()
    monkeypatch.setattr(company_resolve.http, "get", fake)

    results = {
        entity: company_resolve.resolve_company(entity)
        for entity in ("Nvidia", "Stripe", "Rust")
    }

    assert results["Nvidia"]["ticker"] == "NVDA"
    assert results["Stripe"] == {"company": "Stripe", "company_site": "https://stripe.com/"}
    assert results["Rust"] == {}


def test_comparison_run_writes_each_entity_its_own_company(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    companies = {
        "MainBrand": {"company": "MainBrand Inc.", "ticker": "MAIN", "exchange": "Nasdaq"},
        "PeerOne": {"company": "PeerOne", "company_site": "https://peerone.example/"},
    }
    observed = run_competitor_cli(monkeypatch, tmp_path, companies=companies)

    assert sorted(call.args[0] for call in observed.company_resolver.call_args_list) == [
        "MainBrand", "PeerOne", "PeerTwo",
    ]
    main = observed.reports["MainBrand"].artifacts["resolved"]
    assert (main["company"], main["ticker"], main["exchange"]) == ("MainBrand Inc.", "MAIN", "Nasdaq")
    assert "company_site" not in main
    peer_one = observed.reports["PeerOne"].artifacts["resolved"]
    assert peer_one["company"] == "PeerOne"
    assert peer_one["company_site"] == "https://peerone.example/"
    assert "ticker" not in peer_one
    peer_two = observed.reports["PeerTwo"].artifacts["resolved"]
    assert not {"company", "company_site", "ticker", "exchange"} & set(peer_two)


def test_mock_run_never_asks_for_a_company(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    observed = run_competitor_cli(monkeypatch, tmp_path, args=["--mock"])

    observed.company_resolver.assert_not_called()


def test_company_resolves_when_the_host_supplied_a_plan(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    plan = {
        "intent": "exploration", "freshness_mode": "balanced_recent",
        "cluster_mode": "topic", "raw_topic": "MainBrand",
        "source_weights": {"reddit": 1.0},
        "subqueries": [{
            "label": "primary", "search_query": "MainBrand",
            "ranking_query": "MainBrand", "sources": ["reddit"],
        }],
    }
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--plan", json.dumps(plan), "--x-handle", "mainbrand"],
        competitors_list=None, expected_topics={"MainBrand"},
        companies={"MainBrand": {"company": "MainBrand Inc.", "ticker": "MAIN"}},
    )

    assert observed.calls["MainBrand"]["external_plan"] is not None
    assert observed.reports["MainBrand"].artifacts["resolved"]["ticker"] == "MAIN"


def _report(topic: str, resolved: dict) -> schema.Report:
    return schema.Report(
        topic=topic, range_from="2026-06-01", range_to="2026-06-30",
        generated_at="2026-06-30T12:00:00Z",
        provider_runtime=schema.ProviderRuntime("local", "none", "none"),
        query_plan=schema.QueryPlan("general", "recent", "none", topic, [], {}),
        clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
        artifacts={"resolved": {"entity": topic, **resolved}},
    )


def test_resolved_entities_row_shows_the_ticker():
    rows = render._render_resolved_entities_block([
        ("Nvidia", _report("Nvidia", {"x_handle": "nvidia", **NVIDIA_RESULT})),
        ("Jensen Huang", _report("Jensen Huang", {**NVIDIA_RESULT, "company_via_person": True})),
        ("Stripe", _report("Stripe", {"company": "Stripe"})),
    ])

    assert rows[2] == "- **Nvidia**: X @nvidia | Subs - | GitHub - | Ticker NVDA | Context: -"
    assert rows[3] == (
        "- **Jensen Huang**: X - | Subs - | GitHub - | Ticker NVDA (via Nvidia) | Context: -"
    )
    assert rows[4] == "- **Stripe**: X - | Subs - | GitHub - | Context: -"


def test_resolved_entities_row_marks_a_ticker_from_the_yahoo_backup():
    rows = render._render_resolved_entities_block([
        ("Tesla", _report("Tesla", {
            "company": "Tesla", "ticker": "TSLA", "company_source": "yahoo-backup",
            "company_lookup": "wikidata failed; yahoo backup used",
        })),
        ("Rust", _report("Rust", {"company_lookup": "wikidata failed; no backup match"})),
    ])

    assert rows[2] == "- **Tesla**: X - | Subs - | GitHub - | Ticker TSLA (Yahoo match) | Context: -"
    assert rows[3] == "- **Rust**: X - | Subs - | GitHub - | Context: -"
