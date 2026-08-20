import unittest

from lib.instagram import _parse_items


class TestInstagramOwnerTypeSafety(unittest.TestCase):
    def _make_raw(self, **overrides):
        base = {
            "id": "1",
            "code": "ABC123",
            "caption": "test caption",
            "owner": {"username": "testuser"},
        }
        base.update(overrides)
        return base

    def test_owner_as_dict(self):
        items = _parse_items([self._make_raw()], "test")
        self.assertEqual("testuser", items[0]["author_name"])

    def test_owner_as_string(self):
        items = _parse_items([self._make_raw(owner="stringuser")], "test")
        self.assertEqual("stringuser", items[0]["author_name"])

    def test_owner_missing(self):
        raw = self._make_raw()
        del raw["owner"]
        items = _parse_items([raw], "test")
        self.assertEqual("", items[0]["author_name"])

    def test_owner_none(self):
        items = _parse_items([self._make_raw(owner=None)], "test")
        self.assertEqual("", items[0]["author_name"])

    def test_user_field_fallback(self):
        raw = self._make_raw()
        del raw["owner"]
        raw["user"] = {"username": "fallbackuser"}
        items = _parse_items([raw], "test")
        self.assertEqual("fallbackuser", items[0]["author_name"])


class TestInstagramComments(unittest.TestCase):
    """U1: Instagram comment fetching via ScrapeCreators."""

    def test_fetch_post_comments_parses_and_sorts_by_likes(self):
        from unittest.mock import patch
        from lib import instagram

        fake = {
            "success": True,
            "comments": [
                {"text": "mid", "comment_like_count": 3,
                 "created_at": "2026-07-04T14:27:58.000Z", "user": {"username": "bob"}},
                {"text": "gold", "comment_like_count": 500,
                 "created_at": "2026-07-03T10:00:00.000Z", "user": {"username": "alice"}},
                {"text": "", "comment_like_count": 999,
                 "created_at": "2026-07-02T10:00:00.000Z", "user": {"username": "skip"}},
            ],
            "cursor": None,
        }
        with patch.object(instagram.http, "get", return_value=fake):
            out = instagram._fetch_post_comments(
                "https://www.instagram.com/reel/ABC/", token="k", max_comments=5,
            )
        # Empty-text dropped; sorted desc by comment_like_count.
        self.assertEqual([c["text"] for c in out], ["gold", "mid"])
        self.assertEqual(out[0]["comment_like_count"], 500)
        self.assertEqual(out[0]["author"], "alice")
        self.assertEqual(out[0]["date"], "2026-07-03")

    def test_fetch_post_comments_error_returns_empty(self):
        from unittest.mock import patch
        from lib import instagram

        def _boom(*a, **k):
            raise RuntimeError("network")

        with patch.object(instagram.http, "get", side_effect=_boom):
            out = instagram._fetch_post_comments("https://x/", token="k")
        self.assertEqual(out, [])

    def test_enrich_with_comments_no_token_or_items_noop(self):
        from lib import instagram
        self.assertEqual([], instagram.enrich_with_comments([], token="k"))
        items = [{"url": "u", "engagement": {"likes": 5}}]
        self.assertEqual(items, instagram.enrich_with_comments(items, token=""))
        self.assertNotIn("top_comments", items[0])

    def test_is_instagram_comments_available_gate(self):
        from lib import env
        self.assertFalse(env.is_instagram_comments_available({}))
        self.assertFalse(env.is_instagram_comments_available(
            {"SCRAPECREATORS_API_KEY": "k"}))  # key but no INCLUDE_SOURCES
        self.assertFalse(env.is_instagram_comments_available(
            {"INCLUDE_SOURCES": "instagram_comments"}))  # opt-in but no key
        self.assertTrue(env.is_instagram_comments_available(
            {"SCRAPECREATORS_API_KEY": "k", "INCLUDE_SOURCES": "tiktok,instagram_comments"}))


class TestExpandInstagramQueries(unittest.TestCase):
    """Tests for expand_instagram_queries() multi-query generation."""

    def test_default_depth_returns_two_plus_queries(self):
        from lib.instagram import expand_instagram_queries
        queries = expand_instagram_queries("Kanye West", "default")
        self.assertGreaterEqual(len(queries), 2)
        # Breaking_news intent should include reaction/edit variant
        variant_found = any(
            "reaction" in q.lower() or "edit" in q.lower()
            for q in queries
        )
        self.assertTrue(variant_found, f"Expected reaction/edit variant: {queries}")

    def test_quick_depth_returns_one_query(self):
        from lib.instagram import expand_instagram_queries
        queries = expand_instagram_queries("Kanye West", "quick")
        self.assertEqual(len(queries), 1)

if __name__ == "__main__":
    unittest.main()
