"""Tests for scripts/lib/web_search_keyless.py — keyless web search floor."""

from unittest import mock

from lib import web_search_keyless

_DDG_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpost&amp;rut=x">First &amp; Best</a>
  <a class="result__snippet" href="//x">A snippet about the <b>topic</b>.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.example.org%2Fa">Second result</a>
  <a class="result__snippet" href="//y">Second snippet.</a>
</div>
"""


class TestKeylessSearch:
    def test_ddg_parsing_happy_path(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            items, artifact = web_search_keyless.keyless_search(
                "topic", ("2026-02-25", "2026-03-27"), {})
        assert len(items) == 2
        assert items[0]["url"] == "https://example.com/post"
        assert items[0]["title"] == "First & Best"
        assert items[0]["source_domain"] == "example.com"
        assert "snippet about the topic" in items[0]["snippet"]
        assert artifact["keyless_backend"] == "ddg"
        # Floor relevance is below the paid backends' 0.8.
        assert items[0]["relevance"] < 0.8

    def test_item_shape_matches_grounding_backends(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            items, _ = web_search_keyless.keyless_search("topic", ("2026-02-25", "2026-03-27"), {})
        required = {"id", "title", "url", "source_domain", "snippet", "date", "relevance", "why_relevant"}
        assert required.issubset(items[0].keys())

    def test_current_window_tags_undated_items_for_normalize(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            items, _ = web_search_keyless.keyless_search("topic", ("2026-02-25", "2999-12-31"), {})
        assert all(item["metadata"] == {"date_window_basis": "keyless_current"} for item in items)

    def test_historical_window_leaves_items_untagged(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            items, _ = web_search_keyless.keyless_search("topic", ("2020-01-01", "2020-01-31"), {})
        assert items and all("metadata" not in item for item in items)

    def test_count_cap(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            items, _ = web_search_keyless.keyless_search("topic", ("2026-02-25", "2026-03-27"), {}, count=1)
        assert len(items) == 1

    def test_ddg_down_no_searxng_returns_degraded(self):
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=None):
            items, artifact = web_search_keyless.keyless_search("topic", ("2026-02-25", "2026-03-27"), {})
        assert items == []
        assert artifact["reason"] == "keyless-search-unavailable"

    def test_searxng_fallback_when_ddg_empty(self):
        searxng_payload = {"results": [
            {"url": "https://searxng.example/a", "title": "SX A", "content": "sx snippet"},
        ]}
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=None), \
             mock.patch.object(web_search_keyless.http, "get", return_value=searxng_payload):
            items, artifact = web_search_keyless.keyless_search(
                "topic", ("2026-02-25", "2026-03-27"),
                {"LAST30DAYS_SEARXNG_URL": "https://searxng.example"})
        assert len(items) == 1
        assert items[0]["url"] == "https://searxng.example/a"
        assert artifact["keyless_backend"] == "searxng"

    def test_skips_non_http_results(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=javascript%3Avoid(0)">bad</a>'
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=html):
            items, _ = web_search_keyless.keyless_search("topic", ("2026-02-25", "2026-03-27"), {})
        assert items == []


# Startpage marks each hit with an <a class="result-title result-link …"> whose
# href is the target and whose <h2> is the title; the emotion <style> block
# (inline CSS) must never leak into the parsed title. Description follows in a
# <p class="…description…">.
_STARTPAGE_HTML = """
<a class="result-title result-link css-1bggj8v" href="https://example.com/post" data-testid="gl-title-link">
  <style data-emotion="css i3irj7">.css-i3irj7{line-height:18px;color:#2E39B3;}</style>
  <h2 class="wgl-title css-i3irj7">First &amp; Best Result</h2>
</a>
<p class="description css-abc">A snippet about the topic.</p>
<a class="result-title result-link css-1bggj8v" href="https://news.example.org/a" data-testid="gl-title-link">
  <h2 class="wgl-title css-i3irj7">Second result</h2>
</a>
<p class="description css-abc">Second snippet.</p>
"""


class TestStartpageFallback:
    def _get_text(self, startpage_html):
        # DuckDuckGo yields nothing (its datacenter-IP 202 challenge page has no
        # result anchors); Startpage yields real results.
        def side_effect(url, *args, **kwargs):
            return startpage_html if "startpage.com" in url else ""
        return side_effect

    def test_startpage_used_when_ddg_empty(self):
        with mock.patch.object(
            web_search_keyless.http, "get_text",
            side_effect=self._get_text(_STARTPAGE_HTML),
        ):
            items, artifact = web_search_keyless.keyless_search(
                "topic", ("2026-02-25", "2026-03-27"), {})
        assert artifact["keyless_backend"] == "startpage"
        assert artifact.get("reason") is None
        assert len(items) == 2
        assert items[0]["url"] == "https://example.com/post"
        # The inline <style> CSS must not bleed into the title.
        assert items[0]["title"] == "First & Best Result"
        assert "css-" not in items[0]["title"]
        assert items[0]["snippet"] == "A snippet about the topic."
        assert items[0]["source_domain"] == "example.com"

    def test_ddg_preferred_over_startpage(self):
        # When DuckDuckGo returns results, Startpage is not consulted.
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=_DDG_HTML):
            _, artifact = web_search_keyless.keyless_search(
                "topic", ("2026-02-25", "2026-03-27"), {})
        assert artifact["keyless_backend"] == "ddg"


class TestStripHtml:
    def test_strips_style_block_contents(self):
        got = web_search_keyless._strip_html(
            "<style>.a{color:red}</style><h2>Title</h2>")
        assert got == "Title"


# --- Brave HTML rung -------------------------------------------------------

import datetime as _dt
from pathlib import Path

import pytest

from lib import http

_BRAVE_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "brave_reddit_serp.html"
).read_text(encoding="utf-8")
_REDDIT_QUERY = "site:reddit.com kanye west"
_WINDOW = ("2026-10-16", "2026-11-15")


@pytest.fixture(autouse=True)
def _fresh_rung_health():
    web_search_keyless.reset_rung_health()
    yield
    web_search_keyless.reset_rung_health()


def _failing(status):
    """A get_text stand-in that fails the way the transport does: the
    HTTPError reaches the failure sink and the call returns None."""
    def fail():
        http._record_failure(http.HTTPError(f"HTTP {status}", status))
        return None
    return fail


def _by_host(brave=None, ddg="", startpage=""):
    """Route get_text by host; a callable answers for its rung."""
    calls = []

    def side_effect(url, *args, **kwargs):
        calls.append(url)
        for host, answer in (("search.brave.com", brave), ("duckduckgo.com", ddg),
                             ("startpage.com", startpage)):
            if host in url:
                return answer() if callable(answer) else answer
        raise AssertionError(f"unexpected url {url}")
    return side_effect, calls


class TestBraveParse:
    def test_captured_serp_yields_dated_reddit_results(self):
        now = _dt.datetime(2026, 11, 15, tzinfo=_dt.timezone.utc)
        items = web_search_keyless.parse_brave(_BRAVE_FIXTURE, 50, now=now)
        urls = [i["url"] for i in items]
        assert len(items) >= 10
        assert "https://www.reddit.com/r/Kanye/comments/1xattdz/debunked_north_west_is_not_pregnant_with_ken/" in urls
        # Each web result is read once: the inline comment cards under a result
        # repeat its link and are not results of their own.
        assert len(urls) == len(set(urls))
        first = next(i for i in items if "1xattdz" in i["url"])
        assert first["title"].endswith(
            "Debunked: North West is not pregnant with Ken Carson’s baby and they never dated")
        assert "Kanye's daughter" in first["snippet"]
        # "1 month ago" on the capture day.
        assert first["date"] == "2026-10-16"
        assert first["source_domain"] == "www.reddit.com"

    def test_count_caps_results(self):
        assert len(web_search_keyless.parse_brave(_BRAVE_FIXTURE, 3)) == 3

    def test_page_with_no_results_parses_empty(self):
        assert web_search_keyless.parse_brave("<html><body>nothing</body></html>", 10) == []


class TestBraveRung:
    def test_reddit_query_tries_brave_first(self):
        side_effect, calls = _by_host(brave=_BRAVE_FIXTURE, ddg=_DDG_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            items, artifact = web_search_keyless.keyless_search(
                _REDDIT_QUERY, _WINDOW, {}, count=20)
        assert artifact["keyless_backend"] == "brave"
        assert all("search.brave.com" in url for url in calls)
        assert items and all("reddit.com" in i["url"] for i in items)
        assert artifact["keyless_rungs"] == {"brave": "ok"}

    def test_other_queries_never_use_brave(self):
        side_effect, calls = _by_host(brave=_BRAVE_FIXTURE, ddg=_DDG_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            _, artifact = web_search_keyless.keyless_search("kanye west", _WINDOW, {})
        assert artifact["keyless_backend"] == "ddg"
        assert not any("brave" in url for url in calls)

    def test_month_freshness_reaches_every_rung(self):
        side_effect, calls = _by_host(brave="", ddg="", startpage="")
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {}, freshness="month")
        brave, ddg, startpage = calls
        assert "tf=pm" in brave
        assert "df=m" in ddg
        assert "with_date=m" in startpage

    def test_brave_429_falls_through_to_ddg_then_startpage(self):
        side_effect, calls = _by_host(brave=_failing(429), ddg="", startpage=_STARTPAGE_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            items, artifact = web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
        assert [u.split("/")[2] for u in calls] == [
            "search.brave.com", "html.duckduckgo.com", "www.startpage.com"]
        assert artifact["keyless_backend"] == "startpage"
        assert items
        assert artifact["keyless_rungs"] == {
            "brave": "throttled", "ddg": "empty", "startpage": "ok"}
        assert web_search_keyless.rung_health()["brave"] == "throttled"

    def test_brave_403_skips_brave_for_the_rest_of_the_run(self):
        side_effect, calls = _by_host(brave=_failing(403), ddg=_DDG_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
            calls.clear()
            _, artifact = web_search_keyless.keyless_search(
                "site:reddit.com another topic", _WINDOW, {})
        assert not any("brave" in url for url in calls)
        assert artifact["keyless_backend"] == "ddg"
        assert artifact["keyless_rungs"]["brave"] == "skipped"
        assert web_search_keyless.rung_health()["brave"] == "blocked"

    def test_seeded_brave_skip_goes_straight_to_ddg_and_startpage(self):
        # The seam a persisted health cache seeds at run start.
        web_search_keyless.seed_rung_health({"brave": "blocked"})
        side_effect, calls = _by_host(brave=_BRAVE_FIXTURE, ddg="", startpage=_STARTPAGE_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            _, artifact = web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
        assert [u.split("/")[2] for u in calls] == ["html.duckduckgo.com", "www.startpage.com"]
        assert artifact["keyless_rungs"]["brave"] == "skipped"

    def test_one_failed_page_does_not_skip_a_rung(self):
        side_effect, calls = _by_host(brave=_failing(500), ddg=_DDG_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
            calls.clear()
            web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
        assert any("brave" in url for url in calls)
        assert web_search_keyless.rung_health()["brave"] == "error"

    def test_ddg_challenge_page_counts_as_throttled(self):
        challenge = '<div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>'
        side_effect, calls = _by_host(ddg=challenge, startpage=_STARTPAGE_HTML)
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            web_search_keyless.keyless_search("topic", _WINDOW, {})
            calls.clear()
            _, artifact = web_search_keyless.keyless_search("topic", _WINDOW, {})
        assert not any("duckduckgo" in url for url in calls)
        assert artifact["keyless_rungs"] == {"ddg": "skipped", "startpage": "ok"}

    def test_rung_failures_still_reach_the_callers_sink(self):
        side_effect, _calls = _by_host(brave=_failing(429), ddg=_DDG_HTML)
        with http.capture_failures() as failures, \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=side_effect):
            web_search_keyless.keyless_search(_REDDIT_QUERY, _WINDOW, {})
        assert [f.status_code for f in failures] == [429]
