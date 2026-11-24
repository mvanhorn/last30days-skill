"""Regression tests for PR 36: jobs ownership/Workday timeouts/teams crash,
Hacker News query matching, StockTwits symbol gating, and Meta Ads failures.

Covers HIGH-165, HIGH-166, HIGH-172, HIGH-193, HIGH-197, HIGH-198, and HIGH-206.
"""

from __future__ import annotations

import copy
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import hackernews, health, http, jobs, jobs_teams, meta_ads, pipeline, schema, stocktwits
from lib.job_boards import robots, sitemap


WINDOW = ("2026-10-15", "2026-11-14")


# ---------------------------------------------------------------------------
# HIGH-165: jobs.py sitemap ownership & multi-tenant host exclusion
# ---------------------------------------------------------------------------


def _sitemap_xml(host: str, slug: str) -> tuple[str, str]:
    robots_txt = f"User-agent: *\nSitemap: https://{host}/jobs/sitemap.xml\n"
    urlset = (
        f"<urlset><url><loc>https://{host}/jobs/10001/{slug}</loc></url></urlset>"
    )
    return robots_txt, urlset


@pytest.fixture
def _fresh_jobs_robots(monkeypatch):
    robots.clear_cache()
    monkeypatch.setattr(sitemap, "_resolves_public", lambda _name: True)
    yield
    robots.clear_cache()


def test_high165_sitemap_hosts_filters_shared_and_untrusted_hosts():
    assert jobs._sitemap_hosts(
        [
            "careers.kula.ai",
            "jpmc.fa.oraclecloud.com",
            "eeho.fa.us2.oraclecloud.com",
            "boards.greenhouse.io",
            "acme.wd5.myworkdayjobs.com",
            "stranger.example",
        ],
        "https://www.acme.com/",
    ) == ["jobs.acme.com", "careers.acme.com", "www.acme.com"]

    assert jobs._sitemap_hosts(["stranger.example", "careers.kula.ai"], "") == []
    assert jobs._sitemap_hosts(["join.disneycareers.com"], "https://disney.com/")[0] == (
        "join.disneycareers.com"
    )
    assert jobs._sitemap_hosts(["www.amazon.jobs"], "https://www.amazon.com/")[0] == (
        "www.amazon.jobs"
    )
    assert jobs._sitemap_hosts(["explore.jobs.netflix.net"], "https://www.netflix.com/")[0] == (
        "explore.jobs.netflix.net"
    )


def test_high165_search_discovered_careers_host_never_stamped_owned_sitemap(_fresh_jobs_robots):
    stranger_robots, stranger_xml = _sitemap_xml("careers.stranger.example", "unrelated-role")
    asked: list[str] = []

    def fake_get_text(url, *_args, **kwargs):
        asked.append(url)
        if url == "https://careers.stranger.example/jobs":
            if kwargs.get("landed") is not None:
                kwargs["landed"].append(url)
            return "<html><body><h1>Open positions</h1></body></html>"
        if url == "https://careers.stranger.example/robots.txt":
            return stranger_robots
        if url == "https://careers.stranger.example/jobs/sitemap.xml":
            return stranger_xml
        return None

    with patch.object(jobs.http, "get_text", side_effect=fake_get_text), \
            patch.object(jobs.http, "get", side_effect=http.HTTPError("404", status_code=404)), \
            patch.object(jobs, "_search_for_careers_url", return_value="https://careers.stranger.example/jobs"), \
            patch.object(jobs, "search_jobs_web", return_value=([], {"tier": "web"})):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="auto", site="https://www.acme.com/",
        )

    assert "https://careers.stranger.example/robots.txt" not in asked
    assert items == []
    assert artifact["tier"] == "web"
    assert artifact.get("owned") is not True


def test_high165_search_discovered_host_does_not_preempt_company_sitemap(_fresh_jobs_robots):
    stranger_robots, stranger_xml = _sitemap_xml("careers.stranger.example", "stranger-role")
    acme_robots, acme_xml = _sitemap_xml("jobs.acme.com", "acme-staff-engineer")
    asked: list[str] = []

    def fake_get_text(url, *_args, **kwargs):
        asked.append(url)
        table = {
            "https://careers.stranger.example/jobs": "<html><body><h1>Open positions</h1></body></html>",
            "https://careers.stranger.example/robots.txt": stranger_robots,
            "https://careers.stranger.example/jobs/sitemap.xml": stranger_xml,
            "https://jobs.acme.com/robots.txt": acme_robots,
            "https://jobs.acme.com/jobs/sitemap.xml": acme_xml,
        }
        body = table.get(url)
        if body is not None and kwargs.get("landed") is not None:
            kwargs["landed"].append(url)
        return body

    with patch.object(jobs.http, "get_text", side_effect=fake_get_text), \
            patch.object(jobs.http, "get", side_effect=http.HTTPError("404", status_code=404)), \
            patch.object(jobs, "_search_for_careers_url", return_value="https://careers.stranger.example/jobs"):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="auto", site="https://www.acme.com/",
        )

    assert "https://careers.stranger.example/robots.txt" not in asked
    assert artifact["provider"] == "sitemap"
    assert artifact["board_sitemap_host"] == "jobs.acme.com"
    assert artifact["owned"] is True
    assert [item["title"] for item in items] == ["Acme Staff Engineer"]


def test_high165_kula_multi_tenant_host_not_queried_on_platform_fallthrough(_fresh_jobs_robots):
    kula_robots, kula_xml = _sitemap_xml("careers.kula.ai", "other-company-role")
    asked: list[str] = []

    def fake_get_text(url, *_args, **kwargs):
        asked.append(url)
        if url == "https://careers.kula.ai/nothing":
            if kwargs.get("landed") is not None:
                kwargs["landed"].append(url)
            # Contains Kula link but invalid payload so kula.read_board raises BoardShapeError.
            return '<html><a href="https://careers.kula.ai/nothing">Open roles</a></html>'
        if url == "https://careers.kula.ai/robots.txt":
            return kula_robots
        if url == "https://careers.kula.ai/jobs/sitemap.xml":
            return kula_xml
        return None

    with patch.object(jobs.http, "get_text", side_effect=fake_get_text), \
            patch.object(jobs.http, "get", side_effect=http.HTTPError("404", status_code=404)):
        items, artifact = jobs.search_jobs(
            "Nothing", WINDOW, {}, web_backend="none", site="https://nothing.tech/",
        )

    assert "https://careers.kula.ai/robots.txt" not in asked
    assert items == []
    assert artifact.get("provider") != "sitemap"
    assert artifact["job_site_unreadable"] == "Nothing"


def test_high165_oracle_multi_tenant_host_not_queried_on_platform_fallthrough(_fresh_jobs_robots):
    oracle_robots, oracle_xml = _sitemap_xml("jpmc.fa.oraclecloud.com", "oracle-shared-role")
    oracle_page = (
        '<a href="https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions">'
        "Search jobs</a><p>Join our team</p>"
    )
    asked: list[str] = []

    def fake_get_text(url, *_args, **kwargs):
        asked.append(url)
        table = {
            "https://www.jpmorganchase.com/careers": oracle_page,
            "https://jpmc.fa.oraclecloud.com/robots.txt": oracle_robots,
            "https://jpmc.fa.oraclecloud.com/jobs/sitemap.xml": oracle_xml,
        }
        body = table.get(url)
        if body is not None and kwargs.get("landed") is not None:
            kwargs["landed"].append(url)
        return body

    with patch.object(jobs.http, "get_text", side_effect=fake_get_text), \
            patch.object(jobs.http, "get", side_effect=http.HTTPError("404", status_code=404)):
        items, artifact = jobs.search_jobs(
            "JPMorgan", WINDOW, {}, web_backend="none", site="https://www.jpmorganchase.com/",
        )

    assert "https://jpmc.fa.oraclecloud.com/robots.txt" not in asked
    assert items == []
    assert artifact.get("provider") != "sitemap"
    assert artifact["job_site_unreadable"] == "JPMorgan"


# ---------------------------------------------------------------------------
# HIGH-166: Workday & SmartRecruiters first-page timeouts/429/5xx unanswered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        http.DeadlineExceeded(),
        http.HTTPError("timed out", outcome_state=health.TIMEOUT),
        http.HTTPError("rate limited", status_code=429),
        http.HTTPError("server error", status_code=503),
    ],
)
def test_high166_workday_first_page_marks_board_unanswered(exc):
    with patch.object(jobs.http, "post", side_effect=exc):
        items, info = jobs.search_workday_board("intel.wd1/External")
    assert items == []
    assert info.get("board_unanswered") is True


def test_high166_workday_first_page_out_of_time_marks_board_unanswered():
    with patch.object(jobs.http, "post", side_effect=AssertionError("should not run")):
        items, info = jobs.search_workday_board(
            "intel.wd1/External", deadline=10.0, clock=lambda: 15.0,
        )
    assert items == []
    assert info.get("board_unanswered") is True


@pytest.mark.parametrize("status", [403, 404])
def test_high166_workday_403_404_not_marked_unanswered(status):
    with patch.object(jobs.http, "post", side_effect=http.HTTPError("refused", status_code=status)):
        items, info = jobs.search_workday_board("intel.wd1/External")
    assert items == []
    assert not info.get("board_unanswered")


@pytest.mark.parametrize(
    "exc",
    [
        http.DeadlineExceeded(),
        http.HTTPError("rate limited", status_code=429),
        http.HTTPError("server error", status_code=500),
    ],
)
def test_high166_smartrecruiters_first_page_marks_board_unanswered(exc):
    with patch.object(jobs.http, "get", side_effect=exc):
        items, info = jobs.read_smartrecruiters_board("acme")
    assert items == []
    assert info.get("board_unanswered") is True


def test_high166_smartrecruiters_first_page_out_of_time_and_404():
    with patch.object(jobs.http, "get", side_effect=AssertionError("should not run")):
        items, info = jobs.read_smartrecruiters_board("acme", deadline=10.0, clock=lambda: 12.0)
    assert items == []
    assert info.get("board_unanswered") is True

    with patch.object(jobs.http, "get", side_effect=http.HTTPError("not found", status_code=404)):
        items, info = jobs.read_smartrecruiters_board("acme")
    assert items == []
    assert not info.get("board_unanswered")


@pytest.mark.parametrize(
    ("company", "site", "board_token"),
    [
        ("Intel", "https://www.intel.com/", "intel.wd1/External"),
        ("Salesforce", "https://www.salesforce.com/", "salesforce.wd12/External_Career_Site"),
    ],
)
@pytest.mark.parametrize(
    "exc",
    [
        http.DeadlineExceeded(),
        http.HTTPError("rate limited", status_code=429),
        http.HTTPError("server error", status_code=503),
    ],
)
def test_high166_registry_workday_timeout_reports_retryable_timeout_not_unreadable(
    company, site, board_token, exc,
):
    budget = jobs.LaneBudget(45.0)
    with patch.object(jobs.http, "get_text", return_value=None), \
            patch.object(jobs.http, "post", side_effect=exc):
        items, artifact = jobs.search_jobs(
            company, WINDOW, {}, web_backend="none", site=site, budget=budget,
        )

    assert items == []
    assert "job_site_unreadable" not in artifact
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert f"the workday board {board_token} did not answer" in artifact["_source_outcome"]["detail"]
    assert "_source_out_of_time" not in artifact
    assert f"the workday board {board_token} did not answer" in budget.slow


@pytest.mark.parametrize(
    ("provider", "html", "board_id", "patch_target"),
    [
        (
            "workday",
            '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>',
            "acme.wd5/AcmeCareers",
            "post",
        ),
        (
            "smartrecruiters",
            '<a href="https://careers.smartrecruiters.com/AcmeCorp">Jobs</a>',
            "AcmeCorp",
            "get",
        ),
    ],
)
def test_high166_careers_discovered_hosted_board_timeout_reports_timeout_not_no_board(
    provider, html, board_id, patch_target, capsys,
):
    budget = jobs.LaneBudget(45.0)
    exc = http.DeadlineExceeded()

    def fake_text(url, *_args, **kwargs):
        if url == "https://www.acme.com/careers":
            if kwargs.get("landed") is not None:
                kwargs["landed"].append(url)
            return html
        return None

    with patch.object(jobs.http, "get_text", side_effect=fake_text), \
            patch.object(jobs.http, patch_target, side_effect=exc):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com/", budget=budget,
        )

    assert items == []
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert f"the {provider} board {board_id} did not answer" in artifact["_source_outcome"]["detail"]
    assert "_source_out_of_time" not in artifact
    assert "no supported job board found" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HIGH-172: jobs_teams.py ZeroDivisionError on &-joined company names
# ---------------------------------------------------------------------------


def _team_role(title: str, idx: int) -> dict:
    return {
        "title": title,
        "department": "",
        "location": "San Francisco, CA",
        "date": "2026-11-05",
        "posted_date_true": True,
        "url": f"https://jobs.example.com/{idx}",
    }


@pytest.mark.parametrize(
    ("company", "segment", "expected_team"),
    [
        ("AI", "Cloud&AI", "cloud"),
        ("X", "X&Safety", "safety"),
        ("S&P", "S&P&Analytics", "analytics"),
        ("AI", "Cloud&AI&Data", "cloud data"),
        ("AI", "Cloud & AI", "cloud"),
        ("AT&T", "AT&T", None),
    ],
)
def test_high172_ampersand_joined_company_stripping_never_raises(company, segment, expected_team):
    rows = [_team_role(f"Engineer {i}, {segment}", i) for i in range(4)]
    result = jobs_teams.named_teams(rows, company=company, window_start="2026-10-15")
    team_names = [t["name"] for t in result["teams"]]
    if expected_team is None:
        assert team_names == []
    else:
        assert expected_team in team_names
        assert all(not name.startswith("&") and not name.endswith("&") for name in team_names)


def test_high172_legitimate_ampersand_team_names_stay_intact():
    rows = (
        [_team_role(f"Specialist {i}, Trust & Safety", i) for i in range(4)]
        + [_team_role(f"Scientist {i}, R&D", i + 10) for i in range(4)]
    )
    result = jobs_teams.named_teams(rows, company="Acme", window_start="2026-10-15")
    team_names = {t["name"] for t in result["teams"]}
    assert "trust & safety" in team_names
    assert "r&d" in team_names


# ---------------------------------------------------------------------------
# HIGH-193: hackernews.py quoted proper nouns, punctuation, symbol-edged terms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "query", "expected"),
    [
        # Planner-quoted multi-word proper nouns
        ("Claude Code v2.1 released", '"Claude Code"', True),
        ("Hermes Agent launches open-source framework", '"Hermes Agent"', True),
        ("Nous Research releases new model", '"Nous Research"', True),
        ("Totally unrelated gardening post", '"Claude Code"', False),
        # Symbol-edged tech terms
        ("Why C++ is fast", "C++", True),
        ("C++23 modules in practice", "C++", True),
        ("C# 14 new features", "C#", True),
        (".NET 10 performance benchmarks", ".NET", True),
        ("F# for backend services", "F#", True),
        # Punctuated queries
        ("Rust 1.90 released", "What is Rust?", True),
        ("What is happening with Java?", "What is Rust?", False),
        ("Claude Code overview", '"What is Claude Code?"', True),
        # Anti-substring boundaries preserved
        ("New email service", "ai", False),
        ("Best artists of 2026", "ts", False),
        ("Hosting at example.net", ".NET", False),
        ("Fishing net design", ".NET", False),
        ("C+++ weird syntax", "C++", False),
    ],
)
def test_high193_title_matches_query_quotes_symbols_and_boundaries(title, query, expected):
    assert hackernews._title_matches_query(title, query) is expected


def test_high193_parse_hackernews_response_keeps_quoted_and_symbol_hits():
    response = {
        "hits": [
            {"objectID": "1", "title": "Claude Code v2.1 released", "points": 200, "num_comments": 50},
            {"objectID": "2", "title": "Unrelated article", "points": 100, "num_comments": 10},
        ]
    }
    items = hackernews.parse_hackernews_response(response, query='"Claude Code"')
    assert [item["id"] for item in items] == ["1"]

    cpp_response = {
        "hits": [
            {"objectID": "3", "title": "Why C++ is fast", "points": 150, "num_comments": 40},
            {"objectID": "4", "title": "Rust borrow checker", "points": 150, "num_comments": 40},
        ]
    }
    cpp_items = hackernews.parse_hackernews_response(cpp_response, query="C++")
    assert [item["id"] for item in cpp_items] == ["3"]


def test_high193_search_hackernews_strips_quotes_from_query_and_optional_words():
    with patch.object(hackernews.http, "request", return_value={"hits": [], "nbHits": 0}) as mock_req:
        hackernews.search_hackernews('"Claude Code"', "2026-01-01", "2026-01-31", depth="quick")
    url = mock_req.call_args.args[1]
    qs = parse_qs(urlsplit(url).query)
    assert qs["query"] == ["claude code"]
    assert qs["optionalWords"] == ["code"]
    assert '"' not in qs["query"][0] and '"' not in qs["optionalWords"][0]


# ---------------------------------------------------------------------------
# HIGH-197 & HIGH-198: stocktwits.py symbol gating & crypto alias gating
# ---------------------------------------------------------------------------


def test_high197_cashtag_handles_lowercase_suffixes():
    assert stocktwits.is_financial_topic("$btc.x") is True
    assert stocktwits.is_financial_topic("$brk.b") is True
    assert stocktwits.detect_symbols("$btc.x", resolve=False) == ["BTC.X"]
    assert stocktwits.detect_symbols("$brk.b", resolve=False) == ["BRK.B"]


def test_high197_search_stocktwits_routes_short_words_and_names_via_detect_symbols():
    stream_ok = {"symbol": {"watchlist_count": 10}, "messages": [], "cursor": {"more": False}}

    def fake_get(url, timeout=20):
        if "search/symbols.json" in url:
            if "q=Apple" in url:
                return {"results": [{"symbol": "AAPL"}]}
            if "q=Tesla" in url:
                return {"results": [{"symbol": "TSLA"}]}
            return {"results": []}
        return copy.deepcopy(stream_ok)

    with patch.object(stocktwits, "_get_json", side_effect=fake_get) as mock_get:
        for query, expected_sym in [("btc", "BTC.X"), ("BTC", "BTC.X"), ("xrp", "XRP.X"), ("XRP", "XRP.X")]:
            res = stocktwits.search_stocktwits(query)
            assert res["symbols"] == [expected_sym]
            assert mock_get.call_args.args[0].endswith(f"/symbol/{expected_sym}.json")

        for generic in ("defi", "DEFI", "stock", "STOCK", "Apple", "Tesla"):
            mock_get.reset_mock()
            res = stocktwits.search_stocktwits(generic)
            assert res["symbols"] == []
            assert res["error"] == "no symbol resolved"
            mock_get.assert_not_called()

        for phrase, expected_sym in [("Apple stock", "AAPL"), ("Tesla stock", "TSLA")]:
            res = stocktwits.search_stocktwits(phrase)
            assert res["symbols"] == [expected_sym]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$AAPL", "AAPL"),
        ("$btc.x", "BTC.X"),
        ("$brk.b", "BRK.B"),
        ("AAPL", "AAPL"),
        ("NVDA", "NVDA"),
        ("TSLA", "TSLA"),
        ("BRK.B", "BRK.B"),
        ("BTC.X", "BTC.X"),
    ],
)
def test_high197_explicit_cashtags_and_uppercase_tickers_hit_stream_directly(raw, expected):
    stream_ok = {"symbol": {"watchlist_count": 10}, "messages": [], "cursor": {"more": False}}
    with patch.object(stocktwits, "_get_json", return_value=stream_ok) as mock_get:
        res = stocktwits.search_stocktwits(raw)
    assert res["symbols"] == [expected]
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0].endswith(f"/symbol/{expected}.json")


@pytest.mark.parametrize(
    "topic",
    ["ADA compliance", "ETH Zurich", "ripple effect", "doge memes", "sol system"],
)
def test_high198_ambiguous_short_crypto_aliases_blocked_on_non_financial_topics(topic):
    assert stocktwits.is_financial_topic(topic) is False
    assert stocktwits.detect_symbols(topic, resolve=False) == []
    with patch.object(stocktwits, "_get_json") as mock_get:
        assert stocktwits.detect_symbols(topic, resolve=True) == []
        mock_get.assert_not_called()


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("ETH crypto", ["ETH.X"]),
        ("Bitcoin", ["BTC.X"]),
        ("$ETH", ["ETH"]),
        ("Solana price", ["SOL.X"]),
        ("ADA price target", ["ADA.X"]),
        ("doge crypto", ["DOGE.X"]),
        ("ripple crypto", ["XRP.X"]),
    ],
)
def test_high198_crypto_aliases_still_resolve_on_financial_topics_and_cashtags(topic, expected):
    assert stocktwits.detect_symbols(topic, resolve=False) == expected


# ---------------------------------------------------------------------------
# HIGH-206: meta_ads.py 0-row _Fatal classification & transcript suppression
# ---------------------------------------------------------------------------


def _video_ad_row(ad_id: str = "1001") -> dict:
    return {
        "ad_archive_id": ad_id,
        "collation_id": f"col-{ad_id}",
        "collation_count": 1,
        "page_id": "300000000000001",
        "page_name": "Brightpan",
        "is_active": True,
        "start_date_string": "2026-09-01T07:00:00.000Z",
        "end_date_string": "2026-09-14T07:00:00.000Z",
        "publisher_platform": ["FACEBOOK", "INSTAGRAM"],
        "url": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "snapshot": {
            "body": {"text": "Shop Brightpan."},
            "title": "",
            "cta_text": "Shop now",
            "display_format": "VIDEO",
            "link_url": "https://brightpan.example/kettles",
            "cards": [],
            "videos": [{"video_hd_url": "https://cdn.example/v.mp4"}],
        },
    }


def _ads_envelope(rows: list[dict], *, key: str = "results", cursor: str = "") -> dict:
    return {
        "success": True,
        key: rows,
        "searchResultsCount": len(rows),
        "cursor": cursor,
    }


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        (401, schema.AUTH_FAILED),
        (402, schema.PAYMENT_REQUIRED),
        (403, schema.AUTH_FAILED),
        (429, schema.RATE_LIMITED),
    ],
)
def test_high206_zero_row_fatal_with_page_override_not_masked_as_partial(status, expected_state):
    err = http.HTTPError(f"boom {status}", status_code=status)
    with patch.object(meta_ads.http, "get", side_effect=err):
        result = meta_ads.search_meta_ads(
            "Brightpan",
            "2026-08-15",
            "2026-09-14",
            token="fake-sc-key",
            page_override="300000000000001",
        )
    assert result["ads"] == []
    assert "partial" not in result
    assert str(status) in result["error"]

    subquery = schema.SubQuery(
        label="primary", search_query="Brightpan", ranking_query="Brightpan", sources=["meta_ads"],
    )
    with patch.object(meta_ads.http, "get", side_effect=err):
        items, artifact = pipeline._retrieve_stream_impl(
            topic="Brightpan",
            subquery=subquery,
            source="meta_ads",
            config={"SCRAPECREATORS_API_KEY": "fake-sc-key", "_meta_ads_page": "300000000000001"},
            depth="default",
            date_range=("2026-08-15", "2026-09-14"),
            runtime=schema.ProviderRuntime("none", "none", "none"),
            mock=False,
            raw_topic="Brightpan",
        )
    assert items == []
    assert artifact["_source_outcome"]["state"] == expected_state


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_high206_interrupted_window_fetch_skips_paid_transcript_calls(status):
    discovery = _ads_envelope([_video_ad_row("1000")], key="searchResults")
    page_one = _ads_envelope([_video_ad_row("1001")], key="results", cursor="cur-2")
    boom = http.HTTPError(f"boom {status}", status_code=status)
    responses = [discovery, page_one, boom]
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        nxt = responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    with patch.object(meta_ads.http, "get", side_effect=fake_get):
        result = meta_ads.search_meta_ads(
            "Brightpan", "2026-08-15", "2026-09-14", depth="default", token="fake-sc-key",
        )

    assert meta_ads.AD_TRANSCRIPT_URL not in calls
    assert len(calls) == 3
    assert len(result["ads"]) == 1
    assert result.get("partial") is True
    assert result["tally"]["transcribed"] == 0
    assert str(status) in result["error"]


# ---------------------------------------------------------------------------
# PR #1611 Greptile review regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        http.DeadlineExceeded(),
        http.HTTPError("server error", status_code=503),
    ],
)
def test_greptile_registry_workday_unanswered_tries_owned_sitemap_first(_fresh_jobs_robots, exc):
    sf_robots, sf_xml = _sitemap_xml("jobs.salesforce.com", "principal-distributed-systems-engineer")
    asked: list[str] = []

    def fake_get_text(url, *_args, **kwargs):
        asked.append(url)
        table = {
            "https://jobs.salesforce.com/robots.txt": sf_robots,
            "https://jobs.salesforce.com/jobs/sitemap.xml": sf_xml,
        }
        body = table.get(url)
        if body is not None and kwargs.get("landed") is not None:
            kwargs["landed"].append(url)
        return body

    budget = jobs.LaneBudget(45.0)
    with patch.object(jobs.http, "get_text", side_effect=fake_get_text), \
            patch.object(jobs.http, "post", side_effect=exc):
        items, artifact = jobs.search_jobs(
            "Salesforce", WINDOW, {}, web_backend="none",
            site="https://www.salesforce.com/", budget=budget,
        )

    assert "https://jobs.salesforce.com/robots.txt" in asked
    assert artifact["tier"] == "ats"
    assert artifact["provider"] == "sitemap"
    assert artifact["owned"] is True
    assert artifact["board_sitemap_host"] == "jobs.salesforce.com"
    assert "workday board did not answer" in artifact["board_fallback"]
    assert "_source_outcome" not in artifact
    assert [item["title"] for item in items] == ["Principal Distributed Systems Engineer"]


def test_greptile_registry_workday_unanswered_preserves_timeout_when_sitemap_and_web_empty(
    _fresh_jobs_robots,
):
    budget = jobs.LaneBudget(45.0)
    with patch.object(jobs.http, "get_text", return_value=None), \
            patch.object(jobs.http, "post", side_effect=http.HTTPError("503", status_code=503)), \
            patch.object(jobs, "search_jobs_web", return_value=([], {"tier": "web"})):
        items, artifact = jobs.search_jobs(
            "Salesforce", WINDOW, {}, web_backend="auto",
            site="https://www.salesforce.com/", budget=budget,
        )

    assert items == []
    assert "job_site_unreadable" not in artifact
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert "salesforce.wd12/External_Career_Site did not answer" in artifact["_source_outcome"]["detail"]
    assert "_source_out_of_time" not in artifact


@pytest.mark.parametrize(
    ("topic", "expected_query", "expected_sym"),
    [
        ("Equity Residential stock", "Equity Residential", "EQR"),
        ("Capital One stock", "Capital One", "COF"),
        ("Bank of America earnings", "Bank of America", "BAC"),
        ("Digital Realty Trust stock", "Digital Realty Trust", "DLR"),
        ("The Trade Desk stock", "The Trade Desk", "TTD"),
    ],
)
def test_greptile_stocktwits_preserves_company_name_words(topic, expected_query, expected_sym):
    captured_q: list[str] = []

    def fake_get(url, timeout=20):
        qs = parse_qs(urlsplit(url).query)
        captured_q.extend(qs.get("q", []))
        return {"results": [{"symbol": expected_sym}]}

    with patch.object(stocktwits, "_get_json", side_effect=fake_get):
        syms = stocktwits.detect_symbols(topic, resolve=True)

    assert captured_q == [expected_query]
    assert syms == [expected_sym]


def test_greptile_meta_ads_budget_exhausted_after_discovery_classifies_as_timeout():
    discovery = _ads_envelope([_video_ad_row("1000")], key="searchResults")
    ticks = {"n": 0}
    real_monotonic = meta_ads.time.monotonic

    def advancing_clock():
        ticks["n"] += 1
        # Budget initialized + discovery call pass; before first /company/ads call, budget expires.
        return real_monotonic() + (0.0 if ticks["n"] <= 3 else 10_000.0)

    with patch.object(meta_ads.time, "monotonic", side_effect=advancing_clock), \
            patch.object(meta_ads.http, "get", side_effect=[discovery]) as mock_get:
        result = meta_ads.search_meta_ads(
            "Brightpan", "2026-08-15", "2026-09-14", depth="default", token="fake-sc-key",
        )

    assert mock_get.call_count == 1
    assert result["ads"] == []
    assert "partial" not in result
    assert result.get("outcome_state") == health.TIMEOUT
    assert "budget" in result["error"]

    subquery = schema.SubQuery(
        label="primary", search_query="Brightpan", ranking_query="Brightpan", sources=["meta_ads"],
    )
    ticks["n"] = 0
    with patch.object(meta_ads.time, "monotonic", side_effect=advancing_clock), \
            patch.object(meta_ads.http, "get", side_effect=[discovery]):
        items, artifact = pipeline._retrieve_stream_impl(
            topic="Brightpan",
            subquery=subquery,
            source="meta_ads",
            config={"SCRAPECREATORS_API_KEY": "fake-sc-key"},
            depth="default",
            date_range=("2026-08-15", "2026-09-14"),
            runtime=schema.ProviderRuntime("none", "none", "none"),
            mock=False,
            raw_topic="Brightpan",
        )

    assert items == []
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_greptile_meta_ads_deadline_exceeded_on_first_window_call_classifies_as_timeout():
    discovery = _ads_envelope([_video_ad_row("1000")], key="searchResults")
    subquery = schema.SubQuery(
        label="primary", search_query="Brightpan", ranking_query="Brightpan", sources=["meta_ads"],
    )
    with patch.object(meta_ads.http, "get", side_effect=[discovery, http.DeadlineExceeded()]):
        items, artifact = pipeline._retrieve_stream_impl(
            topic="Brightpan",
            subquery=subquery,
            source="meta_ads",
            config={"SCRAPECREATORS_API_KEY": "fake-sc-key"},
            depth="default",
            date_range=("2026-08-15", "2026-09-14"),
            runtime=schema.ProviderRuntime("none", "none", "none"),
            mock=False,
            raw_topic="Brightpan",
        )

    assert items == []
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_greptile_jobs_teams_preserves_ampersands_when_company_not_adjacent():
    rows = [
        _team_role(f"Engineer {i}, Research &Development", i) for i in range(4)
    ] + [
        _team_role(f"Scientist {i}, Acme Research &Development", i + 10) for i in range(4)
    ]
    result = jobs_teams.named_teams(rows, company="Acme", window_start="2026-10-15")
    by_name = {t["name"]: t["roles"] for t in result["teams"]}
    assert by_name == {"research &development": 8}

    mixed = [_team_role(f"Engineer {i}, R&D Cloud&AI", i) for i in range(4)]
    mixed_result = jobs_teams.named_teams(mixed, company="AI", window_start="2026-10-15")
    assert [t["name"] for t in mixed_result["teams"]] == ["r&d cloud"]


# ---------------------------------------------------------------------------
# PR #1611 Greptile round-2 regressions (4/5 -> 5/5)
# ---------------------------------------------------------------------------


def test_greptile2_registry_unanswered_sitemap_respects_discovery_share(_fresh_jobs_robots):
    now = [1000.0]
    budget = jobs.LaneBudget(45.0, clock=lambda: now[0])
    seen_remaining: list[float] = []
    seen_deadlines: list[float] = []

    def fake_read_roster(hosts, *, company, deadline):
        seen_remaining.append(budget.remaining())
        seen_deadlines.append(deadline - now[0])
        return [], {"board_total": 0}

    with patch.object(jobs.http, "get_text", return_value=None), \
            patch.object(jobs.http, "post", side_effect=http.HTTPError("503", status_code=503)), \
            patch.object(sitemap, "read_roster", side_effect=fake_read_roster):
        items, artifact = jobs.search_jobs(
            "Salesforce", WINDOW, {}, web_backend="none",
            site="https://www.salesforce.com/", budget=budget, discovery_seconds=12.0,
        )

    assert items == []
    assert seen_remaining and seen_remaining[0] <= 12.0
    assert seen_deadlines and seen_deadlines[0] <= 12.0
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_greptile2_jobs_teams_spaced_ampersand_around_company_name():
    rows = (
        [_team_role(f"Engineer {i}, Cloud& AI&Data", i) for i in range(4)]
        + [_team_role(f"Specialist {i}, Cloud&AI&Data", i + 10) for i in range(4)]
    )
    result = jobs_teams.named_teams(rows, company="AI", window_start="2026-10-15")
    by_name = {t["name"]: t["roles"] for t in result["teams"]}
    assert by_name == {"cloud data": 8}


@pytest.mark.parametrize(
    "topic",
    [
        "crypto token",
        "market cap",
        "dividend valuation",
        "pre-market trading",
        "after hours",
        "equity markets",
        "defi tokens",
        "crypto coin",
    ],
)
def test_greptile2_stocktwits_skips_generic_finance_only_topics(topic):
    with patch.object(stocktwits, "_get_json") as mock_get:
        assert stocktwits.detect_symbols(topic, resolve=True) == []
        mock_get.assert_not_called()
