"""Tests for scripts/lib/reddit_listing.py — keyless scored listing scrape."""

from pathlib import Path
from unittest import mock

from lib import reddit_listing as rl

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_listing_cards_sample.html"


def _html():
    return FIXTURE.read_text(encoding="utf-8")


class TestParseCards:
    """parse_cards reads <shreddit-post> cards into scored post dicts."""

    def test_parses_five_cards(self):
        posts = rl.parse_cards(_html(), query="netherlands")
        assert len(posts) == 5

    def test_real_score_and_count(self):
        posts = rl.parse_cards(_html())
        top = posts[0]
        assert top["score"] == 52692           # the real upvote count
        assert top["engagement"]["score"] == 52692
        assert top["num_comments"] == 1743
        assert top["engagement"]["num_comments"] == 1743

    def test_normalized_shape(self):
        post = rl.parse_cards(_html())[0]
        required = {"id", "title", "url", "score", "num_comments", "subreddit",
                    "created_utc", "author", "selftext", "date",
                    "engagement", "relevance", "why_relevant", "metadata"}
        assert required.issubset(set(post.keys()))
        assert post["why_relevant"] == "Reddit listing"
        assert post["metadata"]["post_id"]  # post id captured for backfill

    def test_fields_populated(self):
        post = rl.parse_cards(_html())[0]
        assert post["title"]
        assert post["author"] == "AdSpecialist6598"
        assert post["subreddit"] == "technology"
        assert "/comments/" in post["url"]
        assert post["date"] and len(post["date"]) == 10

    def test_empty_html_returns_empty(self):
        assert rl.parse_cards("") == []
        assert rl.parse_cards("<div>no cards</div>") == []


class TestListingUrl:
    def test_top_includes_timeframe(self):
        u = rl._listing_url("technology", "top")
        assert "community-more-posts/top/" in u and "name=technology" in u and "t=month" in u

    def test_hot_no_timeframe(self):
        u = rl._listing_url("r/technology", "hot")
        assert "community-more-posts/hot/" in u and "name=technology" in u and "t=" not in u
        assert ".json" not in u


class TestFetchListings:
    def test_dedupes_across_sorts(self):
        with mock.patch.object(rl.http, "get_text", return_value=_html()):
            posts = rl.fetch_listings(["technology"], depth="default")
        urls = [p["url"] for p in posts]
        assert len(urls) == len(set(urls))  # top + hot return same cards -> deduped

    def test_no_subreddits_returns_empty(self):
        assert rl.fetch_listings([], depth="default") == []

    def test_all_fetches_fail_returns_empty(self):
        with mock.patch.object(rl.http, "get_text", return_value=None):
            assert rl.fetch_listings(["technology"]) == []


class TestScoreIndex:
    def test_builds_post_id_to_score_map(self):
        with mock.patch.object(rl.http, "get_text", return_value=_html()):
            idx = rl.score_index(["technology"], depth="quick")
        assert idx  # non-empty
        first = next(iter(idx.values()))
        assert set(first.keys()) == {"score", "num_comments"}
        assert any(v["score"] == 52692 for v in idx.values())
