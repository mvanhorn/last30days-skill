"""Tests for scripts/lib/reddit_rss.py — keyless Reddit RSS discovery."""

from pathlib import Path
from unittest import mock

from lib import reddit_rss

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_search_rss_sample.xml"


def _feed_text():
    return FIXTURE.read_text(encoding="utf-8")


class TestParseFeed:
    """_parse_feed turns Atom entries into normalized post dicts."""

    def test_parses_entries(self):
        posts = reddit_rss._parse_feed(_feed_text(), query="lifelock")
        assert len(posts) == 5
        for p in posts:
            assert p["title"]
            assert "/comments/" in p["url"]
            assert p["url"].startswith("https://www.reddit.com/")

    def test_normalized_shape_matches_scrapecreators(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        required = {"id", "title", "url", "score", "num_comments", "subreddit",
                    "created_utc", "author", "selftext", "date",
                    "engagement", "relevance", "why_relevant", "metadata"}
        assert required.issubset(set(post.keys()))
        assert set(post["engagement"].keys()) == {"score", "num_comments", "upvote_ratio"}
        assert post["why_relevant"] == "Reddit RSS"

    def test_score_is_placeholder_zero(self):
        # RSS carries no engagement score; it is backfilled during enrichment.
        for p in reddit_rss._parse_feed(_feed_text(), query="x"):
            assert p["score"] == 0
            assert p["engagement"]["score"] == 0

    def test_subreddit_derivation(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        assert post["subreddit"] == "Rakuten"

    def test_date_parsed_to_iso(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        assert post["date"] and len(post["date"]) == 10  # YYYY-MM-DD
        assert isinstance(post["created_utc"], float)

    def test_author_strips_u_prefix(self):
        authors = [p["author"] for p in reddit_rss._parse_feed(_feed_text(), query="x")]
        assert all(not a.startswith("/u/") and not a.startswith("u/") for a in authors)

    def test_empty_and_malformed_feed_never_raises(self):
        assert reddit_rss._parse_feed("", query="x") == []
        assert reddit_rss._parse_feed("<not xml", query="x") == []
        assert reddit_rss._parse_feed("<feed></feed>", query="x") == []

    def test_entry_without_comments_link_skipped(self):
        feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            '<title>Subreddit itself</title>'
            '<link href="https://www.reddit.com/r/test/" />'
            '<updated>2026-05-20T00:00:00+00:00</updated></entry></feed>'
        )
        assert reddit_rss._parse_feed(feed, query="x") == []


class TestSearchRss:
    """search_rss fans out, dedupes, assigns IDs, and honors depth limits."""

    def test_dedupe_and_ids(self):
        # Same feed returned for every URL -> deduped to 5 unique posts.
        with mock.patch.object(reddit_rss.http, "get_text", return_value=_feed_text()):
            posts = reddit_rss.search_rss("lifelock", depth="default",
                                          subreddits=["Rakuten", "ConsumerAdvice"])
        urls = [p["url"] for p in posts]
        assert len(urls) == len(set(urls))  # no duplicates
        assert [p["id"] for p in posts] == [f"R{i+1}" for i in range(len(posts))]

    def test_depth_limit_quick(self):
        with mock.patch.object(reddit_rss.http, "get_text", return_value=_feed_text()):
            posts = reddit_rss.search_rss("lifelock", depth="quick")
        assert len(posts) <= reddit_rss.DEPTH_LIMITS["quick"]

    def test_all_feeds_fail_returns_empty(self):
        with mock.patch.object(reddit_rss.http, "get_text", return_value=None):
            posts = reddit_rss.search_rss("lifelock", subreddits=["Rakuten"])
        assert posts == []

    def test_builds_keyless_rss_urls(self):
        urls = reddit_rss._build_urls("life lock", "default", ["Rakuten"])
        assert any("search.rss?q=life+lock" in u and "/r/" not in u.split("?")[0] for u in urls)
        assert any("/r/Rakuten/search.rss" in u and "restrict_sr=on" in u for u in urls)
        assert any("/r/Rakuten/top.rss" in u for u in urls)
        assert all(".json" not in u for u in urls)  # never the dead endpoint
