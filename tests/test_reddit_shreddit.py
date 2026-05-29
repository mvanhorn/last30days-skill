"""Tests for scripts/lib/reddit_shreddit.py — keyless shreddit comment scrape."""

from pathlib import Path
from unittest import mock

from lib import reddit_shreddit as rs

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_shreddit_comments_sample.html"


def _html():
    return FIXTURE.read_text(encoding="utf-8")


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
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=None):
            out = rs.fetch_comments(url)
        assert out["top_comments"] == [] and out["num_comments"] is None
