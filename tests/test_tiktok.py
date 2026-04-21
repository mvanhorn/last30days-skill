import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.tiktok import _parse_items


class TestTikTokAuthorTypeSafety(unittest.TestCase):
    def _make_raw(self, **overrides):
        base = {
            "aweme_id": "1",
            "desc": "test video",
            "share_url": "https://www.tiktok.com/@u/video/1",
            "author": {"unique_id": "testuser"},
            "statistics": {"play_count": 100, "digg_count": 50, "comment_count": 10, "share_count": 5},
        }
        base.update(overrides)
        return base

    def test_author_as_dict(self):
        items = _parse_items([self._make_raw()], "test")
        self.assertEqual("testuser", items[0]["author_name"])

    def test_author_as_string(self):
        items = _parse_items([self._make_raw(author="stringuser")], "test")
        self.assertEqual("stringuser", items[0]["author_name"])

    def test_author_missing(self):
        raw = self._make_raw()
        del raw["author"]
        items = _parse_items([raw], "test")
        self.assertEqual("", items[0]["author_name"])

    def test_author_none(self):
        items = _parse_items([self._make_raw(author=None)], "test")
        self.assertEqual("", items[0]["author_name"])


class TestTikTokStatsZeroPreserved(unittest.TestCase):
    def test_zero_play_count(self):
        raw = {
            "aweme_id": "1",
            "desc": "test",
            "share_url": "https://www.tiktok.com/@u/video/1",
            "author": {"unique_id": "u"},
            "statistics": {"play_count": 0, "digg_count": 0, "comment_count": 0, "share_count": 0},
        }
        items = _parse_items([raw], "test")
        self.assertEqual(0, items[0]["engagement"]["views"])
        self.assertEqual(0, items[0]["engagement"]["likes"])
        self.assertEqual(0, items[0]["engagement"]["comments"])
        self.assertEqual(0, items[0]["engagement"]["shares"])

    def test_stats_missing(self):
        raw = {
            "aweme_id": "1",
            "desc": "test",
            "share_url": "https://www.tiktok.com/@u/video/1",
            "author": {"unique_id": "u"},
        }
        items = _parse_items([raw], "test")
        self.assertEqual(0, items[0]["engagement"]["views"])

    def test_stats_as_non_dict(self):
        raw = {
            "aweme_id": "1",
            "desc": "test",
            "share_url": "https://www.tiktok.com/@u/video/1",
            "author": {"unique_id": "u"},
            "statistics": "invalid",
        }
        items = _parse_items([raw], "test")
        self.assertEqual(0, items[0]["engagement"]["views"])


class TestExpandTikTokQueries(unittest.TestCase):
    """Tests for expand_tiktok_queries() multi-query generation."""

    def test_default_depth_returns_two_plus_queries(self):
        from lib.tiktok import expand_tiktok_queries
        queries = expand_tiktok_queries("Kanye West", "default")
        self.assertGreaterEqual(len(queries), 2)
        # Breaking_news intent should include reaction/edit variant
        variant_found = any(
            "reaction" in q.lower() or "edit" in q.lower() or "trend" in q.lower()
            for q in queries
        )
        self.assertTrue(variant_found, f"Expected reaction/edit/trend variant: {queries}")

    def test_product_intent_includes_review_variant(self):
        from lib.tiktok import expand_tiktok_queries
        # "best laptop for coding" triggers the product intent (best .* for pattern)
        queries = expand_tiktok_queries("best laptop for coding", "deep")
        variant_found = any(
            "review" in q.lower() or "haul" in q.lower() or "unboxing" in q.lower()
            for q in queries
        )
        self.assertTrue(variant_found, f"Expected review/haul/unboxing variant: {queries}")

    def test_quick_depth_returns_one_query(self):
        from lib.tiktok import expand_tiktok_queries
        queries = expand_tiktok_queries("Kanye West", "quick")
        self.assertEqual(len(queries), 1)


class TestTikTokCommentsGate(unittest.TestCase):
    def test_gate_requires_key_and_token(self):
        from lib import env
        self.assertFalse(env.is_tiktok_comments_available({}))
        self.assertFalse(env.is_tiktok_comments_available(
            {"SCRAPECREATORS_API_KEY": "k"}
        ))
        self.assertFalse(env.is_tiktok_comments_available(
            {"INCLUDE_SOURCES": "tiktok_comments"}
        ))
        self.assertTrue(env.is_tiktok_comments_available(
            {"SCRAPECREATORS_API_KEY": "k", "INCLUDE_SOURCES": "tiktok,tiktok_comments"}
        ))

    def test_gate_case_matches_youtube_pattern(self):
        from lib import env
        # Matches the existing youtube_comments behaviour — plain substring match via _parse_include_sources.
        self.assertTrue(env.is_tiktok_comments_available(
            {"SCRAPECREATORS_API_KEY": "k", "INCLUDE_SOURCES": "TIKTOK,TIKTOK_COMMENTS"}
        ))


class TestTikTokEnrichWithComments(unittest.TestCase):
    def test_empty_items_returns_empty(self):
        from lib import tiktok
        self.assertEqual([], tiktok.enrich_with_comments([], token="k"))

    def test_missing_token_is_noop(self):
        from lib import tiktok
        items = [{"video_id": "1", "url": "https://www.tiktok.com/@u/video/1", "engagement": {"views": 100}}]
        result = tiktok.enrich_with_comments(items, token="")
        self.assertNotIn("top_comments", result[0])

    def test_fetch_post_comments_parses_sc_response(self):
        from unittest.mock import patch
        from lib import tiktok

        fake_sc_response = {
            "comments": [
                {"text": "loved it", "user": {"nickname": "Alice"},
                 "digg_count": 420, "create_time": 1709251200},
                {"text": "meh", "user": {"nickname": "Bob"},
                 "digg_count": 3, "create_time": 1709251300},
                {"text": "", "user": {"nickname": "Skip"},
                 "digg_count": 999, "create_time": 1709251400},
            ],
            "total": 3,
        }

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return fake_sc_response

        with patch.object(tiktok, "_requests") as mock_req:
            mock_req.get.return_value = FakeResp()
            out = tiktok._fetch_post_comments(
                "https://www.tiktok.com/@u/video/1",
                token="k",
                max_comments=5,
            )
        # Empty-text comment dropped; rest sorted desc by digg_count.
        self.assertEqual(2, len(out))
        self.assertEqual("loved it", out[0]["text"])
        self.assertEqual(420, out[0]["digg_count"])
        self.assertEqual("Alice", out[0]["author"])
        self.assertEqual("2024-03-01", out[0]["date"])
        self.assertEqual(3, out[1]["digg_count"])

    def test_fetch_post_comments_prefers_unique_id_over_nickname(self):
        """Author prefers unique_id (@handle) over nickname (display name)."""
        from unittest.mock import patch
        from lib import tiktok

        fake_sc_response = {
            "comments": [
                {"text": "first", "user": {"unique_id": "moosanoormahomed", "nickname": "Moosa Noormahomed"},
                 "digg_count": 3986, "create_time": 1709251200},
                {"text": "second", "user": {"nickname": "Muna9e"},  # no unique_id, falls back to nickname
                 "digg_count": 925, "create_time": 1709251300},
                {"text": "third", "user": {},  # neither - empty string
                 "digg_count": 100, "create_time": 1709251400},
            ],
            "total": 3,
        }

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return fake_sc_response

        with patch.object(tiktok, "_requests") as mock_req:
            mock_req.get.return_value = FakeResp()
            out = tiktok._fetch_post_comments(
                "https://www.tiktok.com/@u/video/1",
                token="k",
                max_comments=5,
            )
        self.assertEqual(3, len(out))
        # unique_id wins over nickname when both present
        self.assertEqual("moosanoormahomed", out[0]["author"])
        # nickname used when unique_id missing
        self.assertEqual("Muna9e", out[1]["author"])
        # both missing → empty string, comment still included
        self.assertEqual("", out[2]["author"])

    def test_fetch_post_comments_swallows_http_error(self):
        from unittest.mock import patch
        from lib import tiktok

        with patch.object(tiktok, "_requests") as mock_req:
            mock_req.get.side_effect = Exception("429 rate limit")
            out = tiktok._fetch_post_comments(
                "https://www.tiktok.com/@u/video/1",
                token="k",
                max_comments=5,
            )
        self.assertEqual([], out)

    def test_enrich_attaches_top_comments_to_top_ranked_items(self):
        from unittest.mock import patch
        from lib import tiktok

        items = [
            {"video_id": "low", "url": "https://www.tiktok.com/@u/video/low",
             "engagement": {"views": 10, "likes": 1, "comments": 0}},
            {"video_id": "high", "url": "https://www.tiktok.com/@u/video/high",
             "engagement": {"views": 10000, "likes": 500, "comments": 30}},
            {"video_id": "mid", "url": "https://www.tiktok.com/@u/video/mid",
             "engagement": {"views": 1000, "likes": 50, "comments": 5}},
        ]
        with patch.object(tiktok, "_fetch_post_comments") as mock_fetch:
            mock_fetch.return_value = [
                {"author": "A", "text": "fire", "digg_count": 100, "date": "2024-03-01"}
            ]
            tiktok.enrich_with_comments(items, token="k", max_posts=2)
        # High and mid get comments; low does not.
        by_id = {i["video_id"]: i for i in items}
        self.assertIn("top_comments", by_id["high"])
        self.assertIn("top_comments", by_id["mid"])
        self.assertNotIn("top_comments", by_id["low"])


if __name__ == "__main__":
    unittest.main()
