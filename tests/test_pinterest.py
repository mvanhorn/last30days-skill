"""Tests for pinterest.py — ScrapeCreators Pinterest search module."""

import unittest
from unittest.mock import patch

from lib import pinterest


class TestSearchPinterestRequest(unittest.TestCase):
    """Pin the ScrapeCreators request contract.

    Regression guard: the SC Pinterest endpoint requires a `query` param.
    A prior version sent `keyword`, which the API rejects with
    400 bad_request ("You must provide a 'query' param"), making every
    Pinterest search silently return zero pins.
    """

    def test_uses_query_param_not_keyword(self):
        from lib import http as http_module
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = {"pins": []}
            pinterest.search_pinterest(
                "robot vacuum", "2026-05-01", "2026-06-01",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 1)
            params = mock_http_get.call_args.kwargs["params"]
            # The fix this test guards is the param *name* — SC requires `query`,
            # not `keyword`. Assert the contract only; the exact value is derived
            # from _extract_core_subject() and is that function's concern, not ours.
            self.assertIn("query", params)
            self.assertNotIn("keyword", params)
            self.assertTrue(params["query"])

    def test_no_token_skips_http_call(self):
        from lib import http as http_module
        with patch.object(http_module, "get") as mock_http_get:
            result = pinterest.search_pinterest(
                "robot vacuum", "2026-05-01", "2026-06-01", token=None,
            )
            mock_http_get.assert_not_called()
            self.assertEqual(result["items"], [])
            self.assertIn("error", result)

    def test_parses_pins_response_shape(self):
        from lib import http as http_module
        payload = {
            "pins": [
                {
                    "id": "123",
                    "description": "Robot vacuum that handles pet hair",
                    "link": "https://example.com/p/123",
                    "save_count": 42,
                    "pinner": {"username": "petowner"},
                },
            ],
        }
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = payload
            result = pinterest.search_pinterest(
                "robot vacuum pet hair", "2026-05-01", "2026-06-01",
                depth="default", token="fake-token",
            )
            self.assertEqual(len(result["items"]), 1)
            item = result["items"][0]
            self.assertEqual(item["pin_id"], "123")
            self.assertEqual(item["engagement"]["saves"], 42)
            self.assertEqual(item["url"], "https://example.com/p/123")


if __name__ == "__main__":
    unittest.main()
