"""Tests for the optional official Threads API lane (lib/threads_api.py)."""

import sys
import unittest
from unittest import mock

from lib import env, http, subproc, threads, threads_api

USER_TOKEN = "dummy-threads-user-token-000"
APP_TOKEN = "TH|dummy-app|dummy-secret-000"


def _api_post(code, stamp="2026-11-20T05:42:03+0000", user="alice", text="claude code is great"):
    return {
        "id": f"18{code}",
        "text": text,
        "media_type": "TEXT_POST",
        "permalink": f"https://www.threads.com/@{user}/post/{code}",
        "timestamp": stamp,
        "username": user,
    }


class TestToWebShape(unittest.TestCase):
    def test_maps_graph_fields_onto_web_shape(self):
        shaped = threads_api._to_web_shape(_api_post("AbC_1"))
        self.assertEqual(shaped["code"], "AbC_1")
        self.assertEqual(shaped["user"], {"username": "alice"})
        self.assertEqual(shaped["caption"], {"text": "claude code is great"})
        self.assertEqual(threads._parse_date(shaped), "2026-11-20")

    def test_shortcode_field_wins_over_permalink(self):
        post = dict(_api_post("FromLink"), shortcode="Short1", permalink="https://threads.com/x")
        self.assertEqual(threads_api._to_web_shape(post)["code"], "Short1")

    def test_rejects_non_dict_and_empty(self):
        self.assertIsNone(threads_api._to_web_shape("x"))
        self.assertIsNone(threads_api._to_web_shape({}))


class TestKeywordSearch(unittest.TestCase):
    CFG = {threads_api.USER_TOKEN_ENV: USER_TOKEN}

    def test_no_token(self):
        out = threads_api.keyword_search("claude", "2026-10-29", "2026-11-28", 20, {})
        self.assertIn("No THREADS_ACCESS_TOKEN", out["error"])

    def test_malformed_token_never_reaches_network(self):
        with mock.patch.object(http, "get") as get:
            out = threads_api.keyword_search(
                "claude", "2026-10-29", "2026-11-28", 20,
                {threads_api.USER_TOKEN_ENV: "bad\r\nX-Injected: 1"})
        get.assert_not_called()
        self.assertEqual(out["error"], "Invalid token format")

    def test_recent_and_top_with_window(self):
        with mock.patch.object(http, "get", return_value={"data": [_api_post("A1")]}) as get:
            out = threads_api.keyword_search("claude code", "2026-10-29", "2026-11-01", 20, self.CFG)
        self.assertEqual(len(out["posts"]), 2)
        types = [c.kwargs["params"]["search_type"] for c in get.call_args_list]
        self.assertEqual(types, ["RECENT", "TOP"])
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["since"], 1793232000)  # 2026-10-29T00:00Z
        self.assertEqual(params["until"], 1793577600)  # 2026-11-02T00:00Z: 11-01 inclusive
        self.assertNotIn("access_token", params)
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": f"Bearer {USER_TOKEN}"})
        self.assertTrue(get.call_args.kwargs["sensitive_url"])
        self.assertTrue(get.call_args.args[0].endswith("/keyword_search"))

    def test_until_omitted_when_window_ends_today_or_later(self):
        with mock.patch.object(http, "get", return_value={"data": []}) as get:
            threads_api.keyword_search("claude", "2026-10-29", "2999-01-01", 20, self.CFG)
        self.assertNotIn("until", get.call_args.kwargs["params"])

    def test_error_is_scrubbed(self):
        exc = http.HTTPError(f"HTTP 400: bad access_token={USER_TOKEN}", 400,
                             body='{"error":{"message":"(#10) permission"}}')
        with mock.patch.object(http, "get", side_effect=exc):
            out = threads_api.keyword_search("claude", "2026-10-29", "2026-11-28", 20, self.CFG)
        self.assertNotIn(USER_TOKEN, out["error"])
        self.assertIn("permission", out["error"])

    def test_deadline_reached_skips_calls(self):
        import time
        with mock.patch.object(http, "get") as get:
            out = threads_api.keyword_search(
                "claude", "2026-10-29", "2026-11-28", 20, self.CFG,
                deadline=time.monotonic() + 0.5)
        get.assert_not_called()
        self.assertIn("deadline", out["error"])


class TestWindowBounds(unittest.TestCase):
    NOW = 1795824000 + 3600  # 2026-11-28T01:00Z

    def test_until_is_the_next_utc_midnight(self):
        self.assertEqual(threads_api.window_bounds("2026-11-01", "2026-11-20", now=self.NOW),
                         (1793491200, 1795219200))  # until 2026-11-21T00:00Z

    def test_window_ending_today_has_no_until(self):
        self.assertEqual(threads_api.window_bounds("2026-10-29", "2026-11-28", now=self.NOW)[1], None)

    def test_since_clamped_to_threads_launch(self):
        self.assertEqual(threads_api.window_bounds("2020-01-01", "2020-01-05", now=self.NOW),
                         (threads_api._MIN_SINCE, None))

    def test_bad_dates_are_left_out(self):
        self.assertEqual(threads_api.window_bounds("", "nope", now=self.NOW), (None, None))


class TestMatchingTrend(unittest.TestCase):
    CFG = {threads_api.APP_TOKEN_ENV: APP_TOKEN}
    TRENDS = {"trends": [
        {"title": "World Cup draw", "description": "Groups revealed", "trend_id": "111"},
        {"title": "Claude Code launch", "description": "Anthropic ships an update", "trend_id": "222"},
    ]}

    def test_no_app_token_is_silent(self):
        with mock.patch.object(http, "get") as get:
            out = threads_api.matching_trend("claude code", {})
        get.assert_not_called()
        self.assertEqual(out, {"trend": None, "posts": []})

    def test_match_returns_trend_and_posts(self):
        responses = [self.TRENDS, {"data": [_api_post("T1")]}]
        with mock.patch.object(http, "get", side_effect=responses) as get:
            out = threads_api.matching_trend("claude code", self.CFG)
        self.assertEqual(out["trend"]["trend_id"], "222")
        self.assertEqual(out["trend"]["country"], "US")
        self.assertEqual(out["posts"][0]["_trend"], "Claude Code launch")
        self.assertEqual(get.call_args_list[0].kwargs["params"]["country_code"], "US")
        self.assertTrue(get.call_args_list[1].args[0].endswith("/222/posts"))

    def test_posts_refused_keeps_trend(self):
        responses = [self.TRENDS, http.HTTPError("HTTP 403: needs advanced access", 403)]
        with mock.patch.object(http, "get", side_effect=responses):
            out = threads_api.matching_trend("claude code", self.CFG)
        self.assertEqual(out["trend"]["title"], "Claude Code launch")
        self.assertEqual(out["posts"], [])
        self.assertNotIn("error", out)

    def test_non_numeric_trend_id_is_ignored(self):
        trends = {"trends": [{"title": "Claude Code", "trend_id": "../me?x=1"}]}
        with mock.patch.object(http, "get", return_value=trends) as get:
            out = threads_api.matching_trend("claude code", self.CFG)
        self.assertIsNone(out["trend"])
        self.assertEqual(get.call_count, 1)

    def test_no_match(self):
        with mock.patch.object(http, "get", return_value=self.TRENDS) as get:
            out = threads_api.matching_trend("kubernetes operators", self.CFG)
        self.assertIsNone(out["trend"])
        self.assertEqual(get.call_count, 1)

    def test_country_choice(self):
        self.assertEqual(threads_api.trends_country({threads_api.TRENDS_COUNTRY_ENV: "jp"}), "JP")
        self.assertEqual(threads_api.trends_country({threads_api.TRENDS_COUNTRY_ENV: "VN"}), "US")


class TestSearchThreadsWithApi(unittest.TestCase):
    def _browser(self, posts):
        result = subproc.SubprocResult(returncode=0, stdout=__import__("json").dumps({"posts": posts}), stderr="")
        return mock.patch.object(subproc, "run_with_timeout", return_value=result)

    def test_api_only_when_browser_off(self):
        cfg = {threads.BROWSER_ENV: "off", threads_api.USER_TOKEN_ENV: USER_TOKEN}
        self.assertTrue(env.is_threads_available(cfg))
        with mock.patch.object(http, "get", return_value={"data": [_api_post("A1")]}), \
                mock.patch.object(subproc, "run_with_timeout") as run:
            out = threads.search_threads("claude code", "2026-10-29", "2026-11-28", config=cfg)
        run.assert_not_called()
        self.assertEqual([i["url"] for i in out["items"]], ["https://www.threads.com/@alice/post/A1"])

    def test_browser_copy_wins_on_duplicates(self):
        browser_post = {
            "pk": "18A1", "code": "A1", "taken_at": 1795851801, "like_count": 42,
            "caption": {"text": "claude code is great"}, "user": {"username": "alice"},
            "text_post_app_info": {"direct_reply_count": 3},
        }
        cfg = {threads.PYTHON_ENV: sys.executable, threads_api.USER_TOKEN_ENV: USER_TOKEN}
        with mock.patch.object(http, "get", return_value={"data": [_api_post("A1"), _api_post("B2")]}), \
                self._browser([browser_post]):
            out = threads.search_threads("claude code", "2026-10-29", "2026-11-28", config=cfg)
        ids = [i["url"].rsplit("/", 1)[-1] for i in out["items"]]
        self.assertEqual(ids, ["A1", "B2"])
        self.assertEqual(out["items"][0]["engagement"]["likes"], 42)

    def test_api_error_does_not_hide_browser_results(self):
        browser_post = {"pk": "1", "code": "C3", "taken_at": 1795851801,
                        "caption": {"text": "claude code"}, "user": {"username": "bob"}}
        cfg = {threads.PYTHON_ENV: sys.executable, threads_api.USER_TOKEN_ENV: USER_TOKEN}
        with mock.patch.object(http, "get", side_effect=http.HTTPError("HTTP 400", 400)), \
                self._browser([browser_post]):
            out = threads.search_threads("claude code", "2026-10-29", "2026-11-28", config=cfg)
        self.assertNotIn("error", out)
        self.assertEqual(len(out["items"]), 1)

    def test_trend_rides_on_the_result(self):
        cfg = {threads.BROWSER_ENV: "off", threads_api.USER_TOKEN_ENV: USER_TOKEN,
               threads_api.APP_TOKEN_ENV: APP_TOKEN}
        trends = {"trends": [{"title": "Claude Code", "trend_id": "9"}]}
        responses = [{"data": []}, {"data": []}, trends, {"data": [_api_post("T9")]}]
        with mock.patch.object(http, "get", side_effect=responses):
            out = threads.search_threads("claude code", "2026-10-29", "2026-11-28", config=cfg)
        self.assertEqual(out["trend"]["trend_id"], "9")
        self.assertIn("Threads trending (Claude Code)", out["items"][0]["why_relevant"])

    def test_trend_label_survives_browser_copy_winning(self):
        browser_post = {"pk": "1", "code": "T9", "taken_at": 1795851801, "like_count": 7,
                        "caption": {"text": "claude code"}, "user": {"username": "bob"}}
        cfg = {threads.PYTHON_ENV: sys.executable, threads_api.APP_TOKEN_ENV: APP_TOKEN}
        trends = {"trends": [{"title": "Claude Code", "trend_id": "9"}]}
        with mock.patch.object(http, "get", side_effect=[trends, {"data": [_api_post("T9")]}]), \
                self._browser([browser_post]):
            out = threads.search_threads("claude code", "2026-10-29", "2026-11-28", config=cfg)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["engagement"]["likes"], 7)
        self.assertIn("Threads trending (Claude Code)", out["items"][0]["why_relevant"])

    def test_describe_backend_lists_lanes(self):
        cfg = {threads.PYTHON_ENV: sys.executable, threads_api.USER_TOKEN_ENV: USER_TOKEN,
               threads_api.APP_TOKEN_ENV: APP_TOKEN}
        text = threads.describe_backend(cfg)
        self.assertIn("official API keyword search", text)
        self.assertIn("trending topics (US)", text)
        self.assertNotIn(USER_TOKEN, text)


if __name__ == "__main__":
    unittest.main()
