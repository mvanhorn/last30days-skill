"""Tests for scripts/lib/web_fetch_keyless.py — keyless URL-to-markdown fetch."""

from unittest import mock

from lib import web_fetch_keyless


class TestFetchMarkdown:
    """fetch_markdown turns a URL into clean markdown via the keyless reader."""

    def test_happy_path(self):
        body = "Title: Example\nURL Source: https://example.com\n\n# Example\n\nReal content."
        with mock.patch.object(web_fetch_keyless.http, "get_text", return_value=body) as gt:
            result = web_fetch_keyless.fetch_markdown("https://example.com")
        assert result.ok is True
        assert "Real content." in result.markdown
        assert result.cached_snapshot is False
        # Requests the reader-prefixed URL with a text accept header.
        called_url = gt.call_args.args[0]
        assert called_url == "https://r.jina.ai/https://example.com"
        assert gt.call_args.kwargs.get("accept") == "text/plain"

    def test_cached_snapshot_flagged(self):
        body = "Warning: showing a cached snapshot of this page.\n\n# Title\n\nbody"
        with mock.patch.object(web_fetch_keyless.http, "get_text", return_value=body):
            result = web_fetch_keyless.fetch_markdown("https://example.com/post")
        assert result.ok is True
        assert result.cached_snapshot is True

    def test_fetch_failure_returns_typed_empty(self):
        with mock.patch.object(web_fetch_keyless.http, "get_text", return_value=None):
            result = web_fetch_keyless.fetch_markdown("https://example.com")
        assert result.ok is False
        assert result.markdown == ""
        assert result.reason == "fetch-failed"

    def test_empty_body_returns_typed_empty(self):
        with mock.patch.object(web_fetch_keyless.http, "get_text", return_value="   \n  "):
            result = web_fetch_keyless.fetch_markdown("https://example.com")
        assert result.ok is False
        assert result.reason == "empty-body"

    def test_invalid_url_makes_no_request(self):
        with mock.patch.object(web_fetch_keyless.http, "get_text") as gt:
            result = web_fetch_keyless.fetch_markdown("not a url")
        assert result.ok is False
        assert result.reason == "invalid-url"
        gt.assert_not_called()

    def test_empty_url_makes_no_request(self):
        with mock.patch.object(web_fetch_keyless.http, "get_text") as gt:
            result = web_fetch_keyless.fetch_markdown("")
        assert result.ok is False
        assert result.reason == "invalid-url"
        gt.assert_not_called()
