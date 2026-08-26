"""River listings must not report a bot-gate as a clean no-results.

Issue #940: a global ``--discover --nominate-only`` sweep treated Reddit's
r/all as quiet when the listing fetch returned HTTP 200 with a large HTML
block page and zero ``<shreddit-post>`` cards. r/all, the HN front page, and
the Digg feed cannot be legitimately empty; a successful transport with
nothing parseable is a gated/degraded outcome, and the HTTP status belongs
in ``source_status.detail``. Keyword-scoped zero after a domain gate is
still a real no-results.
"""

from unittest import mock
from unittest.mock import MagicMock

from lib import hackernews, pipeline, reddit_listing, schema


BLOCK_HTML = (
    "<!DOCTYPE html><html><head><title>Just a moment</title></head>"
    "<body>" + ("x" * 180_000) + "<p>Please wait while we verify you are human</p>"
    "</body></html>"
)


def _html_response(body: str, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _nominate_reddit(*, domain: str, urlopen_side_effect):
    with mock.patch.object(pipeline, "available_sources", return_value=["reddit"]), \
         mock.patch("lib.http.time.sleep"), \
         mock.patch("lib.http.urllib.request.urlopen", side_effect=urlopen_side_effect):
        return pipeline.run_discover_nominate(
            domain=domain,
            config={},
            as_of_date="2026-07-10",
        )


def test_global_r_all_block_page_is_not_reported_as_no_results():
    result = _nominate_reddit(domain="", urlopen_side_effect=lambda *a, **k: _html_response(BLOCK_HTML))

    outcome = result.source_status["reddit"]
    assert outcome.state != schema.NO_RESULTS
    assert outcome.state == schema.SCHEMA_DRIFT
    assert outcome.items_returned == 0
    assert outcome.attempted is True
    assert "200" in (outcome.detail or "")


def test_keyword_scoped_r_all_block_page_is_not_reported_as_no_results():
    # Uncategorized domains still sweep r/all; an empty parse is a gate, not
    # a quiet topic.
    result = _nominate_reddit(
        domain="urban gardening",
        urlopen_side_effect=lambda *a, **k: _html_response(BLOCK_HTML),
    )

    outcome = result.source_status["reddit"]
    assert outcome.state != schema.NO_RESULTS
    assert "200" in (outcome.detail or "")


def test_fetch_discovery_listings_records_r_all_block_page():
    with mock.patch("lib.http.time.sleep"), mock.patch(
        "lib.http.urllib.request.urlopen",
        side_effect=lambda *a, **k: _html_response(BLOCK_HTML),
    ):
        result = reddit_listing.fetch_discovery_listings(["all"], query="")

    assert result["items"] == []
    assert result["errors"]
    assert any("200" in error for error in result["errors"])


def test_dedicated_sub_empty_partial_is_a_real_empty_listing():
    with mock.patch.object(
        reddit_listing.http, "reddit_keyless_get_text", return_value="<div></div>"
    ):
        items, error = reddit_listing._fetch_one_with_status("tea", "hot", "matcha")

    assert items == []
    assert error is None


def test_dedicated_sub_html_block_page_is_gated():
    with mock.patch.object(
        reddit_listing.http, "reddit_keyless_get_text", return_value=BLOCK_HTML
    ):
        items, error = reddit_listing._fetch_one_with_status("tea", "hot", "matcha")

    assert items == []
    assert error is not None
    assert "200" in error


def test_keyword_gate_dropping_every_card_is_still_no_results():
    payload = {
        "items": [
            {"title": "OpenAI ships a new frontier model", "selftext": ""},
            {"title": "Anthropic updates its agent SDK", "selftext": ""},
        ],
        "errors": [],
    }
    plan = schema.DiscoveryPlan(
        domain="urban gardening", category=None, subreddits=["all"], sources=["reddit"],
    )
    with mock.patch.object(reddit_listing, "fetch_discovery_listings", return_value=payload):
        items, error = pipeline._fetch_discovery_source(
            "reddit", plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, keyword_gate=True,
        )

    assert items == []
    assert error is None


def test_hn_front_page_empty_parse_is_not_a_clean_empty_feed():
    def fake_request(method, url, **_kwargs):
        assert method == "GET"
        return {"hits": []}

    with mock.patch.object(hackernews.http, "request", side_effect=fake_request):
        result = hackernews.fetch_discovery_listings("2026-06-10", "2026-07-10", depth="quick")

    assert result["items"] == []
    assert result["errors"]
    assert any("200" in error for error in result["errors"])


def test_global_digg_empty_river_is_not_reported_as_no_results():
    plan = schema.DiscoveryPlan(
        domain="", category=None, subreddits=["all"], sources=["digg"],
    )
    with mock.patch.object(pipeline.digg, "search_digg", return_value={"results": []}):
        items, error = pipeline._fetch_discovery_source(
            "digg", plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, keyword_gate=False,
        )

    assert items == []
    assert error
    assert "200" in error
