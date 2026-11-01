import unittest
from unittest.mock import patch

from lib import env, tiktok, tiktok_scavio


def _aweme(vid="7000000000000000001", author="chef", views=5000, created=1793300000, desc="pasta recipe #cooking"):
    return {
        "aweme_id": vid,
        "desc": desc,
        "create_time": created,
        "statistics": {"play_count": views, "digg_count": 300, "comment_count": 12, "share_count": 4},
        "author": {"unique_id": author},
        "share_url": f"https://www.tiktok.com/@{author}/video/{vid}?is_from_webapp=1",
        "text_extra": [{"hashtag_name": "cooking"}],
        "video": {"duration": 31},
    }


def _ok(data):
    # Scavio's envelope: the TikTok payload sits under "data".
    return {"data": data, "credits_used": 1, "credits_remaining": 49}


class TestBackendSelection(unittest.TestCase):
    def test_scrapecreators_stays_the_default_when_both_keys_are_set(self):
        cfg = {"SCRAPECREATORS_API_KEY": "sc", "SCAVIO_API_KEY": "sv"}
        self.assertEqual("scrapecreators", env.get_tiktok_backend(cfg))
        self.assertEqual("sc", env.get_tiktok_token(cfg))

    def test_legacy_apify_token_still_means_scrapecreators(self):
        self.assertEqual("scrapecreators", env.get_tiktok_backend({"APIFY_API_TOKEN": "a", "SCAVIO_API_KEY": "sv"}))

    def test_scavio_key_alone_does_not_select_scavio(self):
        cfg = {"SCAVIO_API_KEY": "sv"}
        self.assertIsNone(env.get_tiktok_backend(cfg))
        self.assertFalse(env.is_tiktok_available(cfg))

    def test_pin_selects_scavio_when_it_is_the_only_key(self):
        cfg = {"SCAVIO_API_KEY": "sv", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}
        self.assertEqual("scavio", env.get_tiktok_backend(cfg))
        self.assertEqual("sv", env.get_tiktok_token(cfg))
        self.assertTrue(env.is_tiktok_available(cfg))

    def test_pin_selects_scavio_over_scrapecreators(self):
        cfg = {"SCRAPECREATORS_API_KEY": "sc", "SCAVIO_API_KEY": "sv", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}
        self.assertEqual("scavio", env.get_tiktok_backend(cfg))
        self.assertEqual("sv", env.get_tiktok_token(cfg))

    def test_pin_without_its_key_turns_tiktok_off_instead_of_falling_back(self):
        cfg = {"SCRAPECREATORS_API_KEY": "sc", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}
        self.assertIsNone(env.get_tiktok_backend(cfg))
        self.assertFalse(env.is_tiktok_available(cfg))

    def test_no_key_no_backend(self):
        self.assertIsNone(env.get_tiktok_backend({}))
        self.assertEqual("", env.get_tiktok_token({}))

    def test_comments_follow_the_scavio_backend(self):
        pin = {"SCAVIO_API_KEY": "sv", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}
        self.assertTrue(env.is_tiktok_comments_available({**pin, "INCLUDE_SOURCES": "tiktok_comments"}))
        self.assertFalse(env.is_tiktok_comments_available(pin))
        self.assertFalse(env.is_tiktok_comments_available(
            {"SCAVIO_API_KEY": "sv", "INCLUDE_SOURCES": "tiktok_comments"}))

    def test_scavio_only_config_makes_tiktok_available_but_not_instagram(self):
        from lib import pipeline
        sources = pipeline.available_sources(
            {"SCAVIO_API_KEY": "sv", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}, local_only=True)
        self.assertIn("tiktok", sources)
        self.assertNotIn("instagram", sources)
        self.assertNotIn("tiktok", pipeline.available_sources({"SCAVIO_API_KEY": "sv"}, local_only=True))

    def test_scavio_pin_without_its_key_keeps_tiktok_off_even_with_scrapecreators(self):
        from lib import pipeline
        sources = pipeline.available_sources(
            {"SCRAPECREATORS_API_KEY": "sc", "LAST30DAYS_TIKTOK_BACKEND": "scavio"}, local_only=True)
        self.assertNotIn("tiktok", sources)
        self.assertIn("instagram", sources)

    def test_default_scrapecreators_config_keeps_tiktok_and_instagram(self):
        from lib import pipeline
        sources = pipeline.available_sources({"SCRAPECREATORS_API_KEY": "sc"}, local_only=True)
        self.assertIn("tiktok", sources)
        self.assertIn("instagram", sources)
        self.assertLess(sources.index("tiktok"), sources.index("instagram"))


class TestTransport(unittest.TestCase):
    @patch("lib.tiktok_scavio.http.post")
    def test_keyword_search_request_and_unwrap(self, mock_post):
        mock_post.return_value = _ok({"search_item_list": [{"aweme_info": _aweme()}]})
        items = tiktok_scavio.keyword_search("pasta", "sv", 20)
        url, body = mock_post.call_args.args[0], mock_post.call_args.args[1]
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual("https://api.scavio.dev/api/v1/tiktok/search/videos", url)
        self.assertEqual({"keyword": "pasta", "count": 20, "sort_type": "0"}, body)
        self.assertEqual("Bearer sv", headers["Authorization"])
        self.assertEqual("last30days", headers["X-Client-Source"])
        self.assertEqual("7000000000000000001", items[0]["aweme_id"])

    @patch("lib.tiktok_scavio.http.post")
    def test_hashtag_resolves_the_id_then_fetches_videos(self, mock_post):
        mock_post.side_effect = [
            _ok({"challengeInfo": {"challenge": {"id": "123", "title": "cooking"}}}),
            _ok({"aweme_list": [_aweme()]}),
        ]
        items = tiktok_scavio.hashtag_items("cooking", "sv")
        self.assertEqual({"hashtag_name": "cooking"}, mock_post.call_args_list[0].args[1])
        self.assertEqual("123", mock_post.call_args_list[1].args[1]["hashtag_id"])
        self.assertEqual(1, len(items))

    @patch("lib.tiktok_scavio.http.post")
    def test_unknown_hashtag_is_empty_without_a_second_call(self, mock_post):
        mock_post.return_value = _ok({"challengeInfo": {}})
        self.assertEqual([], tiktok_scavio.hashtag_items("nope", "sv"))
        self.assertEqual(1, mock_post.call_count)

    @patch("lib.tiktok_scavio.http.post")
    def test_profile_resolves_sec_uid_then_fetches_latest_posts(self, mock_post):
        mock_post.side_effect = [
            _ok({"user": {"unique_id": "chef", "sec_uid": "MS4wLjABAAAA_sec"}}),
            _ok({"aweme_list": [_aweme(str(i)) for i in range(15)]}),
        ]
        items = tiktok_scavio.profile_items("chef", "sv", 10)
        posts_body = mock_post.call_args_list[1].args[1]
        self.assertEqual("MS4wLjABAAAA_sec", posts_body["sec_user_id"])
        self.assertEqual("0", posts_body["sort_type"])  # latest
        self.assertEqual(10, len(items))

    @patch("lib.tiktok_scavio.http.post")
    def test_transcript_is_plain_text_or_empty(self, mock_post):
        mock_post.return_value = _ok({"available": True, "text": "add the pasta to boiling water"})
        self.assertEqual("add the pasta to boiling water", tiktok_scavio.transcript("1", "sv"))
        mock_post.return_value = _ok({"available": False, "text": None})
        self.assertEqual("", tiktok_scavio.transcript("1", "sv"))


class TestTikTokModuleWithScavio(unittest.TestCase):
    @patch("lib.tiktok_scavio.http.post")
    def test_search_parses_scavio_items_with_the_existing_parser(self, mock_post):
        mock_post.return_value = _ok({"search_item_list": [{"aweme_info": _aweme()}]})
        with patch("lib.tiktok.http.get") as sc_get:
            result = tiktok.search_tiktok("pasta recipe", "2026-10-01", "2026-12-31",
                                          depth="quick", token="sv", backend="scavio")
        sc_get.assert_not_called()  # never touches ScrapeCreators
        item = result["items"][0]
        self.assertEqual("https://www.tiktok.com/@chef/video/7000000000000000001", item["url"])
        self.assertEqual(5000, item["engagement"]["views"])
        self.assertEqual(["cooking"], item["hashtags"])

    def test_missing_scavio_key_names_the_right_variable(self):
        result = tiktok.search_tiktok("x", "2026-10-01", "2026-12-31", token="", backend="scavio")
        self.assertIn("SCAVIO_API_KEY", result["error"])

    @patch("lib.tiktok_scavio.http.post")
    def test_captions_use_the_scavio_transcript(self, mock_post):
        mock_post.return_value = _ok({"available": True, "text": "spoken words here"})
        items = [{"video_id": "1", "url": "https://www.tiktok.com/@chef/video/1", "text": "desc"}]
        tiktok.fetch_captions(items, "sv", depth="quick", backend="scavio")
        self.assertEqual("spoken words here", items[0]["caption_snippet"])

    @patch("lib.tiktok_scavio.http.post")
    def test_comments_are_shaped_like_scrapecreators(self, mock_post):
        mock_post.return_value = _ok({"comments": [
            {"text": "low", "digg_count": 1, "create_time": 1793300000, "user": {"unique_id": "a"}},
            {"text": "high", "digg_count": 90, "create_time": 1793300000, "user": {"unique_id": "b"}},
        ]})
        out = tiktok._fetch_post_comments("https://www.tiktok.com/@chef/video/1", "sv", 5,
                                          backend="scavio", video_id="1")
        self.assertEqual(["high", "low"], [c["text"] for c in out])
        self.assertEqual("b", out[0]["author"])
        self.assertEqual({"video_id": "1", "count": 20}, mock_post.call_args.args[1])

    def test_comments_without_a_video_id_are_skipped(self):
        self.assertEqual([], tiktok._fetch_post_comments("u", "sv", 5, backend="scavio", video_id=""))


class TestDoctor(unittest.TestCase):
    def test_doctor_reports_scavio_when_it_serves_tiktok(self):
        from lib import doctor
        record = doctor._tiktok_record({"SCAVIO_API_KEY": "sv", "LAST30DAYS_TIKTOK_BACKEND": "scavio"})
        self.assertIn("SCAVIO_API_KEY", record["requires"])

    def test_doctor_keeps_the_scrapecreators_record_by_default(self):
        from lib import doctor
        record = doctor._tiktok_record({"SCRAPECREATORS_API_KEY": "sc", "SCAVIO_API_KEY": "sv"})
        self.assertIn("SCRAPECREATORS_API_KEY", record["requires"])

    def test_switching_the_tiktok_pin_invalidates_the_doctor_cache(self):
        from lib import doctor
        keys = {"SCRAPECREATORS_API_KEY": "sc", "SCAVIO_API_KEY": "sv"}
        self.assertNotEqual(
            doctor._config_fingerprint(keys),
            doctor._config_fingerprint({**keys, "LAST30DAYS_TIKTOK_BACKEND": "scavio"}),
        )


if __name__ == "__main__":
    unittest.main()
