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


class TestInstagramNestedMediaParsing(unittest.TestCase):
    """ScrapeCreators' /v1/instagram/user/reels (used for --ig-creators) wraps
    every field -- taken_at, caption, owner, engagement counts -- inside a
    nested "media" object instead of returning them flat on the item, unlike
    /v2/instagram/reels/search. Before the fallback, _parse_items() only read
    top-level keys, so every creator-reels item parsed with date=None and got
    dropped by the hard date-range filter in search_instagram()/fetch_reels().

    Fixture below is a de-identified, trimmed shape of a real
    /v1/instagram/user/reels response item (fake handle/pk/ids).
    """

    def _make_nested_raw(self, **media_overrides):
        media = {
            "id": "3111111111111111111_11111111111",
            "pk": "3111111111111111111",
            "code": "AbCdEfGhIj1",
            "taken_at": 1777024898,  # unix timestamp, nested under media
            "caption": {"text": "test caption #demo"},
            "like_count": 7470,
            "comment_count": 9475,
            "play_count": 333195,
            "video_duration": 47.2,
            "owner": {"username": "demo_creator"},
        }
        media.update(media_overrides)
        return {"media": media}

    def test_nested_media_date_parsed(self):
        items = _parse_items([self._make_nested_raw()], "test")
        self.assertEqual(1, len(items))
        self.assertIsNotNone(items[0]["date"])
        self.assertEqual("2026-04-24", items[0]["date"])

    def test_nested_media_all_fields_extracted(self):
        items = _parse_items([self._make_nested_raw()], "test")
        item = items[0]
        self.assertEqual("demo_creator", item["author_name"])
        self.assertIn("test caption", item["text"])
        self.assertEqual(7470, item["engagement"]["likes"])
        self.assertEqual(9475, item["engagement"]["comments"])
        self.assertEqual(333195, item["engagement"]["views"])
        self.assertEqual(47.2, item["duration"])
        self.assertIn("AbCdEfGhIj1", item["url"])
        self.assertEqual(["demo"], item["hashtags"])

    def test_top_level_wins_over_nested_media(self):
        """If a field is present at both the top level and inside "media"
        with different values, the top-level value must win (merge, not
        wholesale replacement by the nested object)."""
        raw = {
            "id": "top-id",
            "taken_at": "2026-01-01T00:00:00.000Z",
            "caption": "top-level caption",
            "media": {
                "id": "nested-id",
                "taken_at": 1700000000,  # would resolve to a different date
                "caption": {"text": "nested caption"},
                "like_count": 999,
            },
        }
        items = _parse_items([raw], "test")
        item = items[0]
        self.assertEqual("top-id", item["video_id"])
        self.assertEqual("2026-01-01", item["date"])
        self.assertEqual("top-level caption", item["text"])
        # like_count only exists in "media" -> fallback still fills it in
        self.assertEqual(999, item["engagement"]["likes"])

    def test_flat_shape_still_parses(self):
        """/v2/instagram/reels/search returns fields flat (no "media" wrapper);
        must keep working after the nested-media fallback."""
        raw = {
            "id": "1",
            "code": "ABC123",
            "taken_at": "2026-02-26T16:00:00.000Z",
            "caption": "flat caption",
            "owner": {"username": "flatuser"},
            "like_count": 10,
        }
        items = _parse_items([raw], "test")
        self.assertEqual("2026-02-26", items[0]["date"])
        self.assertEqual("flatuser", items[0]["author_name"])
        self.assertEqual(10, items[0]["engagement"]["likes"])


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
