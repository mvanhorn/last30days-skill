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
