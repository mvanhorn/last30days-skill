import unittest
from unittest.mock import patch

from lib import instagram as instagram_module
from lib.instagram import _parse_items, search_and_enrich


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


class TestInstagramUserPostsParsing(unittest.TestCase):
    """--ig-creators used to only fetch reels via /v1/instagram/user/reels.
    Creators who only publish photo/carousel posts (e.g. shimmer_sinnie)
    got 0 results. /v2/instagram/user/posts returns the full feed --
    photos, carousels, and reels mixed, flat (no "media" wrapper, unlike
    /v1/instagram/user/reels). Fixtures below are de-identified, trimmed
    shapes of real /v2/instagram/user/posts response items.
    """

    def _make_carousel_raw(self, **overrides):
        base = {
            "id": "3600000000000000000_50000000000",
            "code": "DJtrMoOBlDO",
            "media_type": 8,
            "product_type": "carousel_container",
            "taken_at": 1784267398,
            "caption": {"text": "demo carousel caption #umacodes"},
            "like_count": 56,
            "comment_count": 5,
            "carousel_media_count": 7,
            "display_uri": "https://scontent.cdninstagram.com/cover.jpg",
            "owner": {"username": "demo_creator"},
            "url": "https://www.instagram.com/p/DJtrMoOBlDO/",
        }
        base.update(overrides)
        return base

    def _make_photo_raw(self, **overrides):
        base = {
            "id": "3700000000000000000_50000000000",
            "code": "DXphoto1234",
            "media_type": 1,
            "product_type": "feed",
            "taken_at": 1784000000,
            "caption": {"text": "single photo post"},
            "like_count": 20,
            "comment_count": 1,
            "owner": {"username": "demo_creator"},
        }
        base.update(overrides)
        return base

    def _make_reel_raw(self, **overrides):
        base = {
            "id": "3111111111111111111_11111111111",
            "code": "AbCdEfGhIj1",
            "media_type": 2,
            "product_type": "clips",
            "taken_at": 1777024898,
            "caption": {"text": "reel from posts feed"},
            "like_count": 100,
            "comment_count": 10,
            "play_count": 5000,
            "video_duration": 12.5,
            "owner": {"username": "demo_creator"},
        }
        base.update(overrides)
        return base

    def test_carousel_post_type_and_image_count(self):
        items = _parse_items([self._make_carousel_raw()], "test")
        item = items[0]
        self.assertEqual("carousel", item["post_type"])
        self.assertEqual(7, item["image_count"])
        self.assertEqual("https://scontent.cdninstagram.com/cover.jpg", item["cover_image_url"])
        self.assertEqual("https://www.instagram.com/p/DJtrMoOBlDO/", item["url"])
        self.assertEqual("demo_creator", item["author_name"])
        self.assertEqual("2026-07-17", item["date"])
        self.assertEqual(56, item["engagement"]["likes"])

    def test_photo_post_type_no_image_count(self):
        items = _parse_items([self._make_photo_raw()], "test")
        item = items[0]
        self.assertEqual("photo", item["post_type"])
        self.assertIsNone(item["image_count"])

    def test_photo_post_url_fallback_uses_p_path(self):
        """No API-provided url -> fallback must use /p/ (not /reel/) for
        non-reel post types."""
        raw = self._make_photo_raw()
        raw.pop("url", None)
        items = _parse_items([raw], "test")
        self.assertIn("/p/DXphoto1234", items[0]["url"])
        self.assertNotIn("/reel/", items[0]["url"])

    def test_reel_from_posts_feed_still_tagged_as_reel(self):
        items = _parse_items([self._make_reel_raw()], "test")
        item = items[0]
        self.assertEqual("reel", item["post_type"])
        self.assertIsNone(item["image_count"])
        self.assertEqual(12.5, item["duration"])


class TestInstagramReelsPostsMergeDedup(unittest.TestCase):
    """search_and_enrich()'s --ig-creators path fetches _user_reels() and
    _user_posts() independently per creator (isolated failures) then merges
    into the same seen_ids dedup set, keyed by the post's "video_id" (the
    ScrapeCreators id, stable across both endpoints for the same post)."""

    def _reel_item(self, item_id, **overrides):
        base = {
            "id": item_id,
            "code": f"code-{item_id}",
            "media_type": 2,
            "taken_at": 1777024898,
            "caption": {"text": "shared reel"},
            "like_count": 1,
            "owner": {"username": "demo_creator"},
        }
        base.update(overrides)
        return base

    def test_same_reel_from_both_endpoints_deduped(self):
        with patch.object(instagram_module, "_user_reels") as mock_reels, \
             patch.object(instagram_module, "_user_posts") as mock_posts, \
             patch.object(instagram_module, "search_instagram", return_value={"items": []}), \
             patch.object(instagram_module, "fetch_captions", return_value={}):
            mock_reels.return_value = [self._reel_item("dup-1")]
            mock_posts.return_value = [self._reel_item("dup-1")]

            result = search_and_enrich(
                "test topic", "2026-01-01", "2026-12-31",
                token="fake-token", ig_creators=["demo_creator"],
            )

        ids = [i["video_id"] for i in result["items"]]
        self.assertEqual(["dup-1"], ids)

    def test_posts_only_creator_surfaces_photo_when_reels_endpoint_empty(self):
        """Regression case for shimmer_sinnie: reels endpoint returns nothing,
        but posts endpoint has a carousel -- must still surface it."""
        carousel = {
            "id": "carousel-1",
            "code": "carousel-code",
            "media_type": 8,
            "product_type": "carousel_container",
            "taken_at": 1784267398,
            "caption": {"text": "carousel only creator"},
            "like_count": 5,
            "carousel_media_count": 3,
            "owner": {"username": "demo_creator"},
        }
        with patch.object(instagram_module, "_user_reels") as mock_reels, \
             patch.object(instagram_module, "_user_posts") as mock_posts, \
             patch.object(instagram_module, "search_instagram", return_value={"items": []}), \
             patch.object(instagram_module, "fetch_captions", return_value={}):
            mock_reels.return_value = []
            mock_posts.return_value = [carousel]

            result = search_and_enrich(
                "test topic", "2026-01-01", "2026-12-31",
                token="fake-token", ig_creators=["demo_creator"],
            )

        self.assertEqual(1, len(result["items"]))
        self.assertEqual("carousel", result["items"][0]["post_type"])

    def test_user_posts_failure_does_not_block_user_reels(self):
        with patch.object(instagram_module, "_user_reels") as mock_reels, \
             patch.object(instagram_module, "_user_posts") as mock_posts, \
             patch.object(instagram_module, "search_instagram", return_value={"items": []}), \
             patch.object(instagram_module, "fetch_captions", return_value={}):
            mock_reels.return_value = [self._reel_item("reel-only")]
            mock_posts.return_value = []  # simulates _user_posts's own try/except swallowing an error

            result = search_and_enrich(
                "test topic", "2026-01-01", "2026-12-31",
                token="fake-token", ig_creators=["demo_creator"],
            )

        self.assertEqual(1, len(result["items"]))
        self.assertEqual("reel-only", result["items"][0]["video_id"])


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
