import json
import unittest
from unittest import mock

from lib.xai_x import parse_x_response, search_x


def _wrap_items(items):
    """Wrap items list in the xAI response envelope."""
    payload = json.dumps({"items": items})
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": payload}],
            }
        ]
    }


class TestXaiXEngagementZero(unittest.TestCase):
    def test_zero_likes_preserved(self):
        raw_items = [
            {
                "text": "test",
                "url": "https://x.com/u/status/1",
                "engagement": {"likes": 0, "reposts": 5},
                "relevance": 0.8,
            }
        ]
        items = parse_x_response(_wrap_items(raw_items))
        self.assertEqual(0, items[0]["engagement"]["likes"])
        self.assertEqual(5, items[0]["engagement"]["reposts"])

    def test_none_engagement_when_missing(self):
        raw_items = [
            {
                "text": "test",
                "url": "https://x.com/u/status/1",
                "engagement": {},
                "relevance": 0.8,
            }
        ]
        items = parse_x_response(_wrap_items(raw_items))
        self.assertIsNone(items[0]["engagement"]["likes"])

    def test_nonzero_engagement(self):
        raw_items = [
            {
                "text": "test",
                "url": "https://x.com/u/status/1",
                "engagement": {"likes": 42, "reposts": 3, "replies": 1, "quotes": 0},
                "relevance": 0.9,
            }
        ]
        items = parse_x_response(_wrap_items(raw_items))
        eng = items[0]["engagement"]
        self.assertEqual(42, eng["likes"])
        self.assertEqual(3, eng["reposts"])
        self.assertEqual(1, eng["replies"])
        self.assertEqual(0, eng["quotes"])


class TestXaiXRequest(unittest.TestCase):
    def test_grok_45_search_uses_low_reasoning(self):
        with mock.patch("lib.xai_x.http.post", return_value={}) as post:
            search_x(
                "key",
                "grok-4.5",
                "topic",
                "2026-07-01",
                "2026-07-31",
            )
        payload = post.call_args.args[1]
        self.assertEqual({"effort": "low"}, payload["reasoning"])
        self.assertEqual("grok-4.5", payload["model"])

    def test_older_model_pin_does_not_receive_45_only_fields(self):
        with mock.patch("lib.xai_x.http.post", return_value={}) as post:
            search_x(
                "key",
                "grok-4.3",
                "topic",
                "2026-07-01",
                "2026-07-31",
            )
        self.assertNotIn("reasoning", post.call_args.args[1])

if __name__ == "__main__":
    unittest.main()
