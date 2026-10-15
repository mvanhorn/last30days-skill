"""FXMacroData source: opt-in gating, currency detection, keys, ranking."""

import datetime
import urllib.parse

import pytest

from lib import fxmacrodata, normalize, pipeline

NOW = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.timezone.utc)
FROM, TO = "2026-09-15", "2026-10-15"
DAY = 86400


def _epoch(iso):
    return int(datetime.datetime.fromisoformat(iso).replace(tzinfo=datetime.timezone.utc).timestamp())


def _indicator(slug, name, published, *, val=3.1, prev=3.0, frequency="Monthly", status="unverified"):
    return {
        "indicator": slug,
        "name": name,
        "source": "Bureau of Labor Statistics",
        "source_url": f"https://www.bls.gov/{slug}",
        "unit": "%",
        "frequency": frequency,
        "latest": {
            "date": "2026-09-01",
            "val": val,
            "announcement_datetime": _epoch(published),
            "publication_time_status": status,
            "source": "Bureau of Labor Statistics",
            "source_url": f"https://www.bls.gov/{slug}",
        },
        "previous": {"date": "2026-08-01", "val": prev},
    }


def _calendar_row(slug, name, when, *, tier=3, top=False, time_announced=True):
    return {
        "announcement_datetime": _epoch(when),
        "announcement_datetime_utc": f"{when}+00:00",
        "release": slug,
        "name": name,
        "calendar_event_id": f"usd_{slug}_{_epoch(when)}",
        "market_tier": tier,
        "top_tier_for_currency": top,
        "time_announced": time_announced,
        "announcement_date": when[:10],
        "source": "Official calendar",
        "source_url": "https://example.gov/calendar",
    }


USD_LATEST = {
    "currency": "USD",
    "data": [
        _indicator("inflation", "CPI Inflation", "2026-10-10T12:30:00", status="confirmed"),
        _indicator("policy_rate", "Fed Funds Target Rate", "2026-09-17T18:00:00", val=4.25, prev=4.5),
        _indicator("housing_starts", "Housing Starts", "2026-09-18T12:30:00", val=1.3, prev=1.4),
        _indicator("gov_bond_10y", "10-Year Treasury Yield", "2026-10-14T20:00:00", frequency="Daily"),
        _indicator("gdp", "GDP", "2026-08-28T12:30:00"),  # before the window
        {"indicator": "m2", "name": "M2", "latest": {"date": "2026-09-01", "val": None,
                                                    "announcement_datetime": _epoch("2026-10-01T15:00:00")}},
    ],
    "freemium_delay": {"applied": True, "withheld_count": 0},
}
USD_CALENDAR = {
    "currency": "USD",
    "data": [
        _calendar_row("inflation", "CPI Inflation", "2026-10-10T12:30:00", tier=1),
        _calendar_row("policy_rate", "Fed Funds Target Rate", "2026-10-28T18:00:00", tier=1, top=True),
        _calendar_row("inflation", "CPI Inflation", "2026-11-12T13:30:00", tier=1),
        _calendar_row("initial_jobless_claims", "Initial Jobless Claims", "2026-10-22T12:30:00"),
        _calendar_row("retail_sales", "Retail Sales", "2026-10-16T12:30:00", tier=1, time_announced=False),
    ],
}


class FakeApi:
    def __init__(self, payloads=None, fail=()):
        self.payloads = payloads or {"usd": (USD_LATEST, USD_CALENDAR)}
        self.fail = set(fail)
        self.calls = []

    def __call__(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        parsed = urllib.parse.urlparse(url)
        assert parsed.netloc == "api.fxmacrodata.com"
        assert "api_key" not in url  # keys only ever travel in a header
        parts = parsed.path.split("/")
        if parts[2] == "announcements" and parts[4] == "latest":
            if "latest" in self.fail:
                raise OSError("latest down")
            return self.payloads[parts[3]][0]
        if parts[2] == "calendar":
            if "calendar" in self.fail:
                raise OSError("calendar down")
            return self.payloads[parts[3]][1]
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture(autouse=True)
def _fresh_cache():
    fxmacrodata.clear_cache()
    yield
    fxmacrodata.clear_cache()


def _install(monkeypatch, api):
    monkeypatch.setattr(fxmacrodata.http, "get", api)
    return api


def _search(topic, *, to_date=TO, depth="default", api_key=None):
    return fxmacrodata.search_fxmacrodata(topic, FROM, to_date, depth=depth, api_key=api_key, now=NOW)


# --- activation ------------------------------------------------------------


def test_absent_from_available_sources_by_default():
    assert "fxmacrodata" not in pipeline.available_sources({}, None, x_pending=False)


def test_available_when_requested_or_included():
    assert "fxmacrodata" in pipeline.available_sources({}, ["fxmacrodata"], x_pending=False)
    included = {"INCLUDE_SOURCES": "fxmacrodata"}
    assert "fxmacrodata" in pipeline.available_sources(included, None, x_pending=False)


def test_exclude_sources_wins():
    config = {"INCLUDE_SOURCES": "fxmacrodata", "EXCLUDE_SOURCES": "fxmacrodata"}
    assert "fxmacrodata" not in pipeline.available_sources(config, ["fxmacrodata"], x_pending=False)


def test_a_key_alone_does_not_activate_the_source():
    config = {"FXMACRODATA_API_KEY": "dummy-key"}
    assert "fxmacrodata" not in pipeline.available_sources(config, None, x_pending=False)


def test_doctor_reports_opt_in_until_included():
    from lib import doctor

    assert doctor._fxmacrodata_record({})["status"] == "opt-in"
    assert doctor._fxmacrodata_record({"INCLUDE_SOURCES": "fxmacrodata"})["status"] == "ok"
    assert "fxmacrodata" in doctor.SOURCE_ORDER
    assert "fxmacrodata" not in doctor._probeable_sources()


# --- currency detection ----------------------------------------------------


@pytest.mark.parametrize("topic, expected", [
    ("Fed rate cut", ["USD"]),
    ("FOMC October meeting", ["USD"]),
    ("EUR/USD outlook", ["EUR", "USD"]),
    ("usdjpy carry trade", ["USD", "JPY"]),
    ("Bank of Japan and the yen", ["JPY"]),
    ("Japan inflation", ["JPY"]),
    ("UK GDP", ["GBP"]),
    ("US jobs report", ["USD"]),
    ("ECB vs BoE vs RBA", ["EUR", "GBP", "AUD"]),
    ("offshore yuan CNH vs China CPI", ["CNH"]),
])
def test_resolves_currencies_from_the_topic(topic, expected):
    assert fxmacrodata.resolve_currencies(topic) == expected


@pytest.mark.parametrize("topic", [
    "Taylor Swift", "Japan travel tips", "tell us about kubernetes", "claude code", "fed up with meetings",
])
def test_non_macro_topics_resolve_to_nothing(topic):
    assert fxmacrodata.resolve_currencies(topic) == []


def test_non_macro_topic_sends_no_request(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    result = _search("Taylor Swift")
    assert api.calls == []
    assert result["releases"] == [] and "error" not in result


# --- keys --------------------------------------------------------------------


def test_usd_runs_without_a_key(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    result = _search("Fed rate cut")
    assert result["currencies"] == ["USD"]
    paths = [urllib.parse.urlparse(url).path for url, _ in api.calls]
    assert paths == ["/v1/announcements/usd/latest", "/v1/calendar/usd"]
    assert all("X-API-Key" not in headers for _, headers in api.calls)


def test_non_usd_without_a_key_is_skipped_without_a_request(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    result = _search("EUR/USD outlook")
    assert result["skipped"] == ["EUR"]
    assert all("/eur" not in url for url, _ in api.calls)


def test_key_is_sent_in_the_header_for_other_currencies(monkeypatch):
    api = _install(monkeypatch, FakeApi({"eur": (dict(USD_LATEST, currency="EUR"), USD_CALENDAR)}))
    result = _search("ECB rate decision", api_key="dummy-key")
    assert result["skipped"] == []
    assert api.calls and all(headers.get("X-API-Key") == "dummy-key" for _, headers in api.calls)


# --- retrieval ----------------------------------------------------------------


def test_releases_are_limited_to_the_window(monkeypatch):
    _install(monkeypatch, FakeApi())
    result = _search("US economy")
    slugs = {row["indicator"] for row in result["releases"]}
    assert "gdp" not in slugs  # published before the window
    assert "m2" not in slugs  # no value
    assert {"inflation", "policy_rate", "housing_starts"} <= slugs


def test_calendar_supplies_tiers_and_the_next_release(monkeypatch):
    _install(monkeypatch, FakeApi())
    result = _search("US economy")
    inflation = next(r for r in result["releases"] if r["indicator"] == "inflation")
    assert inflation["_tier"] == 1
    assert inflation["_next_release"]["announcement_datetime"] == _epoch("2026-11-12T13:30:00")
    upcoming = {row["release"] for row in result["upcoming"]}
    assert upcoming == {"policy_rate", "inflation", "initial_jobless_claims", "retail_sales"}


def test_historical_window_has_no_upcoming_list(monkeypatch):
    _install(monkeypatch, FakeApi())
    result = _search("US economy", to_date="2026-10-01")
    assert result["upcoming"] == []
    assert all(row["_next_release"] is None for row in result["releases"])


def test_calendar_failure_keeps_the_releases(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"calendar"}))
    result = _search("US inflation")
    assert result["releases"]
    assert "error" not in result
    assert any("calendar" in e for e in result["errors"])


def test_total_failure_is_reported(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"latest"}))
    result = _search("US inflation")
    assert result["releases"] == []
    assert "latest down" in result["error"]


def test_payloads_are_fetched_once_per_process(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    _search("Fed rate cut")
    _search("Fed rate cut")
    assert len(api.calls) == 2


def test_unexpected_shape_is_an_error(monkeypatch):
    monkeypatch.setattr(fxmacrodata.http, "get", lambda url, headers=None, **kw: ["changed"])
    result = _search("US inflation")
    assert "unexpected FXMacroData response shape" in result["error"]


@pytest.mark.parametrize("payload, message", [
    ({"currency": "USD", "data": {"inflation": {}}}, "data is dict"),
    ({"detail": "Unsupported currency (USD)"}, "Unsupported currency (USD)"),
])
def test_malformed_payload_is_an_error_and_not_cached(monkeypatch, payload, message):
    calls = []

    def malformed(url, headers=None, **kwargs):
        calls.append(url)
        return payload

    monkeypatch.setattr(fxmacrodata.http, "get", malformed)
    result = _search("US inflation")
    assert result["releases"] == []
    assert "unexpected FXMacroData response shape" in result["error"]
    assert message in result["error"]
    _search("US inflation")
    assert len(calls) == 2  # the bad response was not cached


# --- parsing and ranking -----------------------------------------------------


def _parsed(monkeypatch, topic, **kwargs):
    _install(monkeypatch, FakeApi())
    return fxmacrodata.parse_fxmacrodata_response(_search(topic, **kwargs), query=topic)


def test_topic_match_ranks_first(monkeypatch):
    items = _parsed(monkeypatch, "US inflation")
    releases = [i for i in items if i["metadata"]["kind"] == "release"]
    assert releases[0]["metadata"]["indicator"] == "inflation"
    assert releases[0]["title"] == "USD CPI Inflation: 3.1 % (2026-09-01)"
    assert releases[0]["url"] == "https://www.bls.gov/inflation"
    assert releases[0]["date"] == "2026-10-10"


def test_central_bank_topic_ranks_the_policy_rate_first(monkeypatch):
    items = _parsed(monkeypatch, "Fed")
    assert items[0]["metadata"]["indicator"] == "policy_rate"
    assert "Previous: 4.5 for 2026-08-01." in items[0]["text"]


def test_daily_series_only_when_the_topic_asks(monkeypatch):
    slugs = {i["metadata"]["indicator"] for i in _parsed(monkeypatch, "US economy")}
    assert "gov_bond_10y" not in slugs
    fxmacrodata.clear_cache()
    slugs = {i["metadata"]["indicator"] for i in _parsed(monkeypatch, "US treasury yields")}
    assert "gov_bond_10y" in slugs


def test_only_confirmed_times_are_stated_to_the_minute(monkeypatch):
    items = {i["metadata"]["indicator"]: i for i in _parsed(monkeypatch, "US economy")
             if i["metadata"]["kind"] == "release"}
    assert "Released 2026-10-10 12:30 UTC" in items["inflation"]["text"]
    assert "Released 2026-09-17 by" in items["policy_rate"]["text"]


def test_scheduled_releases_are_undated_and_headline_only(monkeypatch):
    items = _parsed(monkeypatch, "US economy")
    scheduled = [i for i in items if i["metadata"]["kind"] == "scheduled"]
    slugs = [i["metadata"]["indicator"] for i in scheduled]
    assert "initial_jobless_claims" not in slugs  # tier 3, not asked for
    assert slugs[0] == "retail_sales"  # soonest headline release first
    assert all(i["date"] is None for i in scheduled)
    assert "time not announced yet" in scheduled[0]["title"]


def test_depth_caps_the_release_count(monkeypatch):
    items = _parsed(monkeypatch, "US economy", depth="quick")
    assert len([i for i in items if i["metadata"]["kind"] == "release"]) <= fxmacrodata.RELEASE_LIMITS["quick"]


def test_normalize_keeps_releases_and_undated_scheduled_items(monkeypatch):
    items = _parsed(monkeypatch, "US economy")
    normalized = normalize.normalize_source_items("fxmacrodata", items, FROM, TO)
    kinds = {i.metadata["kind"] for i in normalized}
    assert kinds == {"release", "scheduled"}
    release = next(i for i in normalized if i.metadata["kind"] == "release")
    assert release.engagement == {}
    assert release.container == "USD"
    assert release.date_confidence == "high"


def _same_url_payloads():
    """Several scheduled events on one calendar URL, and a release whose next
    event falls back to the same per-indicator URL."""
    release = _indicator("policy_rate", "Fed Funds Target Rate", "2026-09-17T18:00:00")
    release["source_url"] = release["latest"]["source_url"] = None
    latest = {"currency": "USD", "data": [release]}
    calendar = {"currency": "USD", "data": [
        _calendar_row("policy_rate", "Fed Funds Target Rate", "2026-10-28T18:00:00", tier=1),
        _calendar_row("inflation", "CPI Inflation", "2026-10-20T12:30:00", tier=1),
        _calendar_row("retail_sales", "Retail Sales", "2026-10-16T12:30:00", tier=1),
        _calendar_row("gdp", "GDP", "2026-10-27T12:30:00", tier=1),
    ]}
    for row in calendar["data"]:
        row["source_url"] = None
        if row["release"] != "policy_rate":
            row["source_url"] = "https://example.gov/calendar"
    return {"usd": (latest, calendar)}


def test_events_sharing_a_url_stay_distinct_through_fusion(monkeypatch):
    from lib import fusion

    _install(monkeypatch, FakeApi(_same_url_payloads()))
    items = fxmacrodata.parse_fxmacrodata_response(_search("US economy"), query="US economy")
    urls = [i["url"] for i in items]
    assert urls.count("https://example.gov/calendar") == 3
    policy = [i for i in items if i["metadata"]["indicator"] == "policy_rate"]
    assert {i["metadata"]["kind"] for i in policy} == {"release", "scheduled"}
    assert len({i["url"] for i in policy}) == 1  # both on the fallback URL

    normalized = normalize.normalize_source_items("fxmacrodata", items, FROM, TO)
    assert len(normalized) == 5
    assert len(fusion.collapse_duplicate_urls(list(normalized))) == 5

    fused = fusion.weighted_rrf(
        {("primary", "fxmacrodata"): normalized}, _plan(["fxmacrodata"]),
        pool_limit=40, range_from=FROM, range_to=TO,
    )
    assert len(fused) == 5
    # Citations still point at the publisher's page.
    assert sorted(c.url for c in fused).count("https://example.gov/calendar") == 3


# --- planner -------------------------------------------------------------------


def _plan(sources):
    from lib import schema

    return schema.QueryPlan(
        intent="breaking_news",
        freshness_mode="balanced_recent",
        cluster_mode="none",
        raw_topic="Fed rate cut",
        subqueries=[schema.SubQuery(label="primary", search_query="Fed rate cut",
                                    ranking_query="q", sources=list(sources), weight=1.0)],
        source_weights={source: 1.0 for source in sources},
    )


def test_opted_in_source_joins_a_plan_that_omits_it():
    plan = _plan(["reddit"])
    pipeline._ensure_fxmacrodata_in_plan(plan, ["reddit", "fxmacrodata"])
    assert "fxmacrodata" in plan.subqueries[0].sources
    assert "fxmacrodata" in plan.source_weights


def test_inactive_source_is_not_added_to_a_plan():
    plan = _plan(["reddit"])
    pipeline._ensure_fxmacrodata_in_plan(plan, ["reddit"])
    assert plan.subqueries[0].sources == ["reddit"]
