import json
import os
import unittest
from unittest import mock

from lib import xai_x
from lib.xai_x import parse_x_response


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


class TestXaiXBaseUrlOverride(unittest.TestCase):
    """XAI_BASE_URL redirects the x_search request like the reasoning client."""

    def _posted_url(self, env):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "lib.xai_x.http.post", return_value={"output": []}
        ) as post:
            xai_x.search_x("xai-test", "grok-test", "topic", "2026-05-01", "2026-06-01")
        return post.call_args.args[0]

    def test_default_endpoint_without_override(self):
        self.assertEqual(xai_x.XAI_RESPONSES_URL, self._posted_url({}))

    def test_api_root_override_is_honored(self):
        self.assertEqual(
            "https://gateway.test/v1/responses",
            self._posted_url({"XAI_BASE_URL": "https://gateway.test/v1"}),
        )

    def test_override_preserves_request_and_deadline(self):
        endpoint = "https://gateway.test/v1/responses"
        cancel = object()
        with mock.patch.dict(os.environ, {"XAI_BASE_URL": endpoint}), mock.patch(
            "lib.xai_x.http.post", return_value={"output": []}
        ) as post:
            xai_x.search_x(
                "xai-test", "grok-test", "routing topic", "2026-05-01", "2026-06-01",
                depth="quick", deadline_monotonic=1234.5, cancel=cancel,
            )

        post.assert_called_once()
        url, payload = post.call_args.args
        self.assertEqual(endpoint, url)
        self.assertEqual("grok-test", payload["model"])
        self.assertIn("routing topic", payload["input"][0]["content"])
        self.assertEqual(
            [{"type": "x_search", "from_date": "2026-05-01", "to_date": "2026-06-01"}],
            payload["tools"],
        )
        self.assertEqual("Bearer xai-test", post.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual(90, post.call_args.kwargs["timeout"])
        self.assertEqual(1, post.call_args.kwargs["retries"])
        self.assertEqual(1234.5, post.call_args.kwargs["deadline_monotonic"])
        self.assertIs(cancel, post.call_args.kwargs["cancel"])


if __name__ == "__main__":
    unittest.main()
