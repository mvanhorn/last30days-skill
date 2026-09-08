"""Tests for the xueqiu module."""

import unittest
from unittest.mock import patch

from lib import xueqiu


class TestFinancialGate(unittest.TestCase):
    def test_ticker_topic_passes(self):
        self.assertTrue(xueqiu.is_financial_topic("$NVDA earnings"))

    def test_crypto_topic_passes(self):
        self.assertTrue(xueqiu.is_financial_topic("bitcoin price action"))

    def test_non_financial_fails(self):
        self.assertFalse(xueqiu.is_financial_topic("best python tutorials"))


class TestTsToDateMs(unittest.TestCase):
    def test_valid_ms(self):
        # 2024-06-15T00:00:00Z in ms
        self.assertEqual(xueqiu._ts_to_date_ms(1718409600000), "2024-06-15")

    def test_none(self):
        self.assertIsNone(xueqiu._ts_to_date_ms(None))

    def test_invalid(self):
        self.assertIsNone(xueqiu._ts_to_date_ms("nope"))


class TestInWindow(unittest.TestCase):
    def test_within(self):
        self.assertTrue(xueqiu._in_window("2024-06-15", "2024-06-01", "2024-06-30"))

    def test_outside(self):
        self.assertFalse(xueqiu._in_window("2024-05-01", "2024-06-01", "2024-06-30"))

    def test_unknown_kept(self):
        self.assertTrue(xueqiu._in_window(None, "2024-06-01", "2024-06-30"))


class TestStripHtml(unittest.TestCase):
    def test_tags_removed(self):
        self.assertEqual(xueqiu._strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_entities(self):
        self.assertEqual(xueqiu._strip_html("A &amp; B"), "A & B")

    def test_empty(self):
        self.assertEqual(xueqiu._strip_html(None), "")


class TestNormalizeStatus(unittest.TestCase):
    def test_basic_status(self):
        raw = {
            "data": (
                '{"id": 42, "text": "今日 <b>茅台</b> 大涨", "title": "T", '
                '"user": {"screen_name": "trader"}, "like_count": 99, '
                '"reply_count": 5, "retweet_count": 2, '
                '"created_at": 1718409600000, '
                '"target": "/status/42"}'
            )
        }
        item = xueqiu._normalize_status(raw)
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], "42")
        self.assertEqual(item["date"], "2024-06-15")
        self.assertEqual(item["engagement"]["likes"], 99)
        self.assertEqual(item["author"], "trader")

    def test_bad_data_returns_none(self):
        self.assertIsNone(xueqiu._normalize_status({"data": "not json"}))

    def test_missing_data_returns_none(self):
        self.assertIsNone(xueqiu._normalize_status({}))


class TestSearchXueqiu(unittest.TestCase):
    @patch("lib.xueqiu._fetch_timeline")
    @patch("lib.xueqiu._fetch_hot_stocks")
    def test_relevant_only(self, mock_hot, mock_timeline):
        mock_hot.return_value = []
        mock_timeline.return_value = [
            {"data": (
                '{"id": 1, "text": "贵州茅台 今天大涨 讨论", '
                '"user": {"screen_name": "a"}, "like_count": 10, '
                '"created_at": 1718409600000, "target": "/status/1"}'
            )},
            {"data": (
                '{"id": 2, "text": "我家猫在睡觉", '
                '"user": {"screen_name": "b"}, "like_count": 1, '
                '"created_at": 1718409600000, "target": "/status/2"}'
            )},
        ]

        result = xueqiu.search_xueqiu(
            "茅台", "2024-06-01", "2024-06-30",
            depth="quick", config={"XUEQIU_COOKIE": "x=1"},
        )
        results = result["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "1")

    @patch("lib.xueqiu._fetch_timeline")
    @patch("lib.xueqiu._fetch_hot_stocks")
    def test_missing_cookie_is_error(self, mock_hot, mock_timeline):
        mock_hot.return_value = []
        mock_timeline.return_value = []
        result = xueqiu.search_xueqiu("茅台", "2024-06-01", "2024-06-30", config={})
        self.assertEqual(result["results"], [])
        self.assertIn("XUEQIU_COOKIE", result["error"])

    @patch("lib.xueqiu._fetch_timeline")
    @patch("lib.xueqiu._fetch_hot_stocks")
    def test_transport_error_envelope(self, mock_hot, mock_timeline):
        mock_hot.side_effect = xueqiu.http.HTTPError("network down")
        mock_timeline.side_effect = xueqiu.http.HTTPError("network down")
        result = xueqiu.search_xueqiu(
            "茅台", "2024-06-01", "2024-06-30",
            config={"XUEQIU_COOKIE": "x=1"},
        )
        self.assertEqual(result["results"], [])
        self.assertIn("network down", result["error"])

    def test_empty_topic(self):
        result = xueqiu.search_xueqiu("", "2024-06-01", "2024-06-30")
        self.assertEqual(result, {"results": []})


class TestParseXueqiuResponse(unittest.TestCase):
    def test_parses_items(self):
        result = {
            "results": [
                {
                    "id": "1",
                    "title": "T",
                    "url": "https://xueqiu.com/S/600519",
                    "author": "a",
                    "snippet": "s",
                    "body": "b",
                    "date": "2024-06-15",
                    "date_confidence": "high",
                    "relevance": 0.8,
                    "why_relevant": "overlap",
                    "engagement": {"likes": 10},
                    "symbols": ["SH600519"],
                }
            ]
        }
        items = xueqiu.parse_xueqiu_response(result)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["container"], "雪球")
        self.assertEqual(items[0]["metadata"]["symbols"], ["SH600519"])

    def test_empty(self):
        self.assertEqual(xueqiu.parse_xueqiu_response({"results": []}), [])
        self.assertEqual(xueqiu.parse_xueqiu_response(None), [])


if __name__ == "__main__":
    unittest.main()
