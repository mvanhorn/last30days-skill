"""Tests for scripts/lib/reddit_shreddit.py — keyless shreddit comment scrape."""

from pathlib import Path
from unittest import mock

from lib import reddit_shreddit as rs

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_shreddit_comments_sample.html"


def _html():
    return FIXTURE.read_text(encoding="utf-8")


# The same shreddit markup as fetched through a real browser (the Scrapling
# fallback): the DOM lowercases every attribute NAME, so ``thingId`` -> ``thingid``.
# The parser must read this shape too, or every comment loses its body and drops.
BROWSER_SHAPED_HTML = """
<shreddit-comment-tree-stats total-comments="2" sort="TOP"></shreddit-comment-tree-stats>
<shreddit-comment created="2026-08-09T12:45:51+0000" author="alice" thingid="t1_aaa"
  permalink="/r/videos/comments/x1/comment/aaa/" score="1136" depth="0">
  <div id="t1_aaa-post-rtjson-content"><div><p>First body about $750 pending.</p></div></div>
</shreddit-comment>
<shreddit-comment created="2026-08-09T12:46:00+0000" author="bob" thingid="t1_bbb"
  permalink="/r/videos/comments/x1/comment/bbb/" score="42" depth="0">
  <div id="t1_bbb-post-rtjson-content"><div><p>Second body.</p></div></div>
</shreddit-comment>
"""


class TestExtractPostRef:
    def test_extracts_sub_and_id(self):
        ref = rs.extract_post_ref("https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/")
        assert ref == ("Rakuten", "1taeiw0")

    def test_non_thread_url_returns_none(self):
        assert rs.extract_post_ref("https://www.reddit.com/r/Rakuten/") is None
        assert rs.extract_post_ref("") is None

    def test_svc_url_shape(self):
        # sort=top guarantees the highest-scored comments land on page 1.
        assert rs._svc_url("Rakuten", "1taeiw0") == (
            "https://www.reddit.com/svc/shreddit/comments/r/Rakuten/t3_1taeiw0?sort=top"
        )


class TestParseComments:
    """parse_comments reads <shreddit-comment> elements into scored dicts."""

    def test_happy_path(self):
        comments = rs.parse_comments(_html())
        assert len(comments) >= 1
        for c in comments:
            assert isinstance(c["score"], int)
            assert c["author"] and c["author"] not in ("[deleted]", "[removed]")
            assert c["body"]

    def test_sorted_by_score_desc(self):
        scores = [c["score"] for c in rs.parse_comments(_html())]
        assert scores == sorted(scores, reverse=True)

    def test_deleted_and_removed_filtered(self):
        authors = [c["author"] for c in rs.parse_comments(_html())]
        assert "[deleted]" not in authors and "[removed]" not in authors

    def test_negative_score_retained(self):
        scores = [c["score"] for c in rs.parse_comments(_html())]
        assert -7 in scores  # synthetic downvoted-but-real comment

    def test_limit_honored(self):
        assert len(rs.parse_comments(_html(), limit=2)) == 2

    def test_body_text_extracted(self):
        bodies = [c["body"] for c in rs.parse_comments(_html())]
        assert any("$750" in b or "pending" in b for b in bodies)

    def test_comment_url_built(self):
        for c in rs.parse_comments(_html()):
            if c["url"]:
                assert c["url"].startswith("https://reddit.com/r/")

    def test_empty_html_returns_empty(self):
        assert rs.parse_comments("") == []
        assert rs.parse_comments("<html>no comments here</html>") == []

    def test_browser_lowercased_attrs_parsed(self):
        # Regression guard for the Scrapling fallback: browser-serialized markup
        # carries `thingid` (lowercased), which must still yield full comments.
        comments = rs.parse_comments(BROWSER_SHAPED_HTML)
        assert [c["score"] for c in comments] == [1136, 42]
        assert comments[0]["author"] == "alice"
        assert "$750" in comments[0]["body"]


class TestTotalComments:
    def test_reads_total(self):
        assert rs._total_comments(_html()) == 14

    def test_missing_returns_none(self):
        assert rs._total_comments("<html></html>") is None


class TestFetchComments:
    """fetch_comments wires URL -> svc fetch -> parse, never raising."""

    def test_happy_path(self):
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=_html()) as m:
            out = rs.fetch_comments(url)
        # svc endpoint, not .json
        assert "/svc/shreddit/comments/" in m.call_args[0][0]
        assert ".json" not in m.call_args[0][0]
        assert out["num_comments"] == 14
        assert len(out["top_comments"]) >= 1
        first = out["top_comments"][0]
        assert {"score", "date", "author", "excerpt", "url"} <= set(first.keys())
        assert isinstance(out["comment_insights"], list)

    def test_bad_url_returns_empty(self):
        out = rs.fetch_comments("https://www.reddit.com/r/Rakuten/")
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_fetch_failure_returns_empty(self):
        # Keyless HTTP fails AND Scrapling is unavailable (the CI/Cowork case):
        # behaves exactly as before the fallback existed. is_available is forced
        # False so the test is deterministic on a dev box where scrapling is on PATH.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=False):
            out = rs.fetch_comments(url)
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_scrapling_fallback_used_when_keyless_blocked(self):
        # Keyless HTTP returns nothing (403'd); Scrapling is installed and its
        # browser fetch returns the same (lowercased-attr) markup -> comments.
        url = "https://www.reddit.com/r/videos/comments/1vjokkz/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch", return_value=BROWSER_SHAPED_HTML) as sf:
            out = rs.fetch_comments(url)
        # stealthy-fetch, html format, on the svc URL
        assert sf.call_args.kwargs["mode"] == rs.scrapling_fetch.MODE_STEALTHY
        assert sf.call_args.kwargs["fmt"] == "html"
        assert "/svc/shreddit/comments/" in sf.call_args[0][0]
        assert [c["score"] for c in out["top_comments"]] == [1136, 42]
        assert out["num_comments"] == 2

    def test_scrapling_not_called_when_keyless_succeeds(self):
        # No regression: when the HTTP path works, the browser fallback is never
        # invoked (it is slow and should stay a last resort).
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=_html()), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert len(out["top_comments"]) >= 1

    def test_scrapling_absent_no_fallback(self):
        # Keyless fails and scrapling is not installed: no fetch attempt, empty out.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=False), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None


class TestScraplingDeadline:
    """The browser fallback must respect the caller's aggregate enrichment
    budget: its subprocess timeout is capped to the time remaining before
    ``deadline`` (so run_with_timeout kills the browser at the budget edge
    instead of outliving a cancelled future), and it is skipped outright
    when too little budget remains to be worth a browser launch."""

    URL = "https://www.reddit.com/r/videos/comments/1vjokkz/title/"

    def _fetch(self, deadline, now=1000.0):
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch",
                                  return_value=BROWSER_SHAPED_HTML) as sf, \
                mock.patch.object(rs.time, "monotonic", return_value=now):
            out = rs.fetch_comments(self.URL, deadline=deadline)
        return out, sf

    def test_timeout_capped_to_remaining_budget(self):
        # 30s left of a 45s budget -> the 75s default must shrink to 30s.
        out, sf = self._fetch(deadline=1030.0)
        assert sf.call_args.kwargs["timeout"] == 30
        assert [c["score"] for c in out["top_comments"]] == [1136, 42]

    def test_ample_budget_keeps_default_timeout(self):
        # More time left than the default: never widen beyond SCRAPLING_TIMEOUT.
        _, sf = self._fetch(deadline=1000.0 + rs.SCRAPLING_TIMEOUT + 60)
        assert sf.call_args.kwargs["timeout"] == rs.SCRAPLING_TIMEOUT

    def test_skipped_when_budget_nearly_spent(self):
        # Below the minimum useful window a browser launch can only burn the
        # tail of the budget -> no fetch attempt, empty result.
        out, sf = self._fetch(deadline=1000.0 + rs.SCRAPLING_MIN_BUDGET - 1)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_no_deadline_keeps_default_timeout(self):
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch",
                                  return_value=BROWSER_SHAPED_HTML) as sf:
            rs.fetch_comments(self.URL)
        assert sf.call_args.kwargs["timeout"] == rs.SCRAPLING_TIMEOUT
