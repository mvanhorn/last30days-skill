"""Tests for schema.py — data class serialization roundtrips."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import schema


class TestEngagement(unittest.TestCase):
    """Tests for Engagement.to_dict()."""

    def test_sparse_fields(self):
        eng = schema.Engagement(score=100, num_comments=50)
        d = eng.to_dict()
        self.assertEqual(d, {"score": 100, "num_comments": 50})
        self.assertNotIn("likes", d)

    def test_all_none_returns_none(self):
        eng = schema.Engagement()
        self.assertIsNone(eng.to_dict())

    def test_all_fields(self):
        eng = schema.Engagement(
            score=1, num_comments=2, upvote_ratio=0.9,
            likes=3, reposts=4, replies=5, quotes=6,
            views=7, shares=8, volume=9.0, liquidity=10.0,
        )
        d = eng.to_dict()
        self.assertEqual(len(d), 11)


class TestComment(unittest.TestCase):
    def test_basic(self):
        c = schema.Comment(score=50, date="2026-03-01", author="user", excerpt="text", url="http://x")
        d = c.to_dict()
        self.assertEqual(d["score"], 50)
        self.assertEqual(d["author"], "user")
        self.assertEqual(len(d), 5)


class TestRedditItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.RedditItem(
            id="R1", title="Test", url="http://reddit.com/r/test",
            subreddit="test", date="2026-03-01",
            engagement=schema.Engagement(score=100),
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "R1")
        self.assertEqual(d["subreddit"], "test")
        self.assertEqual(d["engagement"], {"score": 100})
        self.assertNotIn("cross_refs", d)

    def test_cross_refs_included_when_present(self):
        item = schema.RedditItem(
            id="R1", title="T", url="u", subreddit="s",
            cross_refs=["X1", "HN2"],
        )
        d = item.to_dict()
        self.assertEqual(d["cross_refs"], ["X1", "HN2"])


class TestXItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.XItem(
            id="X1", text="tweet", url="http://x.com/1",
            author_handle="user", date="2026-03-01",
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "X1")
        self.assertEqual(d["author_handle"], "user")
        self.assertNotIn("cross_refs", d)


class TestYouTubeItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.YouTubeItem(
            id="YT1", title="Video", url="http://youtube.com/1",
            channel_name="chan",
        )
        d = item.to_dict()
        self.assertEqual(d["channel_name"], "chan")
        self.assertEqual(d["date_confidence"], "high")


class TestTikTokItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.TikTokItem(
            id="TK1", text="caption", url="http://tiktok.com/1",
            author_name="creator", hashtags=["ai", "code"],
        )
        d = item.to_dict()
        self.assertEqual(d["hashtags"], ["ai", "code"])
        self.assertEqual(d["author_name"], "creator")


class TestInstagramItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.InstagramItem(
            id="IG1", text="caption", url="http://instagram.com/reel/1",
            author_name="creator",
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "IG1")


class TestWebSearchItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.WebSearchItem(
            id="W1", title="Article", url="http://example.com",
            source_domain="example.com", snippet="text",
        )
        d = item.to_dict()
        self.assertEqual(d["source_domain"], "example.com")


class TestHackerNewsItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.HackerNewsItem(
            id="HN1", title="Show HN", url="http://example.com",
            hn_url="http://news.ycombinator.com/item?id=1", author="pg",
        )
        d = item.to_dict()
        self.assertTrue(d["hn_url"].startswith("http://news.ycombinator.com"))


class TestPolymarketItem(unittest.TestCase):
    def test_roundtrip(self):
        item = schema.PolymarketItem(
            id="PM1", title="Election", question="Who wins?",
            url="http://polymarket.com/1",
        )
        d = item.to_dict()
        self.assertEqual(d["question"], "Who wins?")


class TestReport(unittest.TestCase):
    def test_to_dict_includes_display_mode_for_auxiliary_sources(self):
        report = schema.Report(
            topic="test",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="reddit-only",
            youtube=[
                schema.YouTubeItem(
                    id="YT1",
                    title="Video",
                    url="http://youtube.com/1",
                    channel_name="chan",
                    date="2026-01-15",
                )
            ],
            hackernews=[
                schema.HackerNewsItem(
                    id="HN1",
                    title="Story",
                    url="http://example.com/story",
                    hn_url="http://news.ycombinator.com/item?id=1",
                    author="pg",
                    date="2026-01-16",
                )
            ],
        )

        d = report.to_dict()
        self.assertEqual(d["display_mode"], "reddit-only + youtube + hn")


if __name__ == "__main__":
    unittest.main()
