"""End-to-end tests for Bluesky search functionality.

These tests verify the Bluesky search integration works correctly.
Tests will skip if Bluesky module is not yet implemented.

Note: Bluesky's public search API (app.bsky.feed.searchPosts) does NOT
require authentication, unlike Reddit/X which use OpenAI and xAI APIs.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import schema

# Check if Bluesky module exists
try:
    from lib import bluesky
    BLUESKY_AVAILABLE = True
except ImportError:
    BLUESKY_AVAILABLE = False
    bluesky = None


# Sample Bluesky response for mock testing
SAMPLE_BLUESKY_RESPONSE = {
    "items": [
        {
            "text": "Just discovered an amazing new tool for productivity!",
            "url": "https://bsky.app/profile/testuser.bsky.social/post/abc123",
            "author_handle": "testuser.bsky.social",
            "date": "2026-01-15",
            "engagement": {
                "likes": 42,
                "reposts": 10,
                "replies": 5,
                "quotes": 2
            },
            "why_relevant": "Discusses productivity tools",
            "relevance": 0.85
        },
        {
            "text": "Here's my review of the latest features in the app",
            "url": "https://bsky.app/profile/reviewer.bsky.social/post/def456",
            "author_handle": "reviewer.bsky.social",
            "date": "2026-01-20",
            "engagement": {
                "likes": 128,
                "reposts": 25,
                "replies": 15,
                "quotes": 8
            },
            "why_relevant": "In-depth review with community discussion",
            "relevance": 0.92
        }
    ]
}


@unittest.skipUnless(BLUESKY_AVAILABLE, "Bluesky module not yet implemented")
class TestBlueskySearch(unittest.TestCase):
    """Test Bluesky search functionality."""

    def test_search_returns_items(self):
        """Test that search_bluesky returns valid items."""
        # Mock the HTTP call - Bluesky public API needs no auth
        with patch.object(bluesky, 'http') as mock_http:
            mock_http.get.return_value = {
                "posts": SAMPLE_BLUESKY_RESPONSE["items"]
            }

            result = bluesky.search_bluesky(
                topic="productivity tools",
                from_date="2026-01-01",
                to_date="2026-01-31",
            )

            self.assertIsInstance(result, dict)

    def test_parse_response_extracts_items(self):
        """Test that parse_bluesky_response extracts items correctly."""
        items = bluesky.parse_bluesky_response(SAMPLE_BLUESKY_RESPONSE)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["author_handle"], "testuser.bsky.social")
        self.assertEqual(items[1]["engagement"]["likes"], 128)

    def test_parse_response_handles_empty(self):
        """Test that parse handles empty responses gracefully."""
        items = bluesky.parse_bluesky_response({})
        self.assertEqual(items, [])

    def test_parse_response_validates_dates(self):
        """Test that invalid dates are handled."""
        response = {
            "items": [
                {
                    "text": "Test post",
                    "url": "https://bsky.app/profile/test/post/123",
                    "author_handle": "test",
                    "date": "invalid-date",
                    "relevance": 0.5
                }
            ]
        }
        items = bluesky.parse_bluesky_response(response)
        # Invalid date should be set to None
        self.assertIsNone(items[0].get("date"))


@unittest.skipUnless(BLUESKY_AVAILABLE, "Bluesky module not yet implemented")
class TestBlueskyNormalization(unittest.TestCase):
    """Test Bluesky item normalization."""

    def test_normalize_creates_bluesky_items(self):
        """Test that normalization creates proper BlueskyItem objects."""
        from lib import normalize

        items = [
            {
                "id": "B1",
                "text": "Test post content",
                "url": "https://bsky.app/profile/user/post/123",
                "author_handle": "user.bsky.social",
                "date": "2026-01-15",
                "engagement": {
                    "likes": 50,
                    "reposts": 10,
                    "replies": 5,
                    "quotes": 2
                },
                "relevance": 0.8
            }
        ]

        result = normalize.normalize_bluesky_items(items, "2026-01-01", "2026-01-31")

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.BlueskyItem)
        self.assertEqual(result[0].author_handle, "user.bsky.social")


@unittest.skipUnless(BLUESKY_AVAILABLE, "Bluesky module not yet implemented")
class TestBlueskyLiveSearch(unittest.TestCase):
    """Live integration tests for Bluesky search.

    Bluesky's public search API does NOT require authentication.
    These tests hit the real API to verify integration works.
    """

    @unittest.skipIf(
        os.environ.get("SKIP_LIVE_TESTS"),
        "Live tests skipped via SKIP_LIVE_TESTS env var"
    )
    def test_live_search(self):
        """Test live search against Bluesky public API (no auth required)."""
        result = bluesky.search_bluesky(
            topic="python programming",
            from_date="2026-01-01",
            to_date="2026-01-31",
            depth="quick",
        )

        self.assertIsInstance(result, dict)
        # Should have posts array from the API
        self.assertIn("posts", result)


class TestBlueskyDataSchemas(unittest.TestCase):
    """Test Bluesky-related data structures.

    These tests run even if Bluesky module isn't implemented yet,
    to verify expected schema when Bluesky is added.
    """

    def test_engagement_supports_bluesky_fields(self):
        """Test that Engagement dataclass can hold Bluesky metrics."""
        engagement = schema.Engagement(
            likes=100,
            reposts=25,
            replies=15,
            quotes=5,
        )

        self.assertEqual(engagement.likes, 100)
        self.assertEqual(engagement.reposts, 25)
        self.assertEqual(engagement.replies, 15)
        self.assertEqual(engagement.quotes, 5)

    def test_engagement_to_dict(self):
        """Test Engagement serialization with Bluesky fields."""
        engagement = schema.Engagement(
            likes=50,
            reposts=10,
            replies=5,
            quotes=2,
        )
        d = engagement.to_dict()

        self.assertEqual(d["likes"], 50)
        self.assertEqual(d["reposts"], 10)
        self.assertEqual(d["replies"], 5)
        self.assertEqual(d["quotes"], 2)


class TestMockBlueskyWorkflow(unittest.TestCase):
    """Test the mock workflow for Bluesky search.

    These tests verify the expected workflow even without
    the Bluesky module, using fixture data.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.fixtures_dir = Path(__file__).parent.parent / "fixtures"
        self.bluesky_fixture = self.fixtures_dir / "bluesky_sample.json"

    def test_fixture_exists_or_skip(self):
        """Test that fixture file can be loaded if it exists."""
        if not self.bluesky_fixture.exists():
            self.skipTest("Bluesky fixture not yet created")

        with open(self.bluesky_fixture) as f:
            data = json.load(f)

        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)

    def test_mock_response_structure(self):
        """Test the expected mock response structure."""
        # Use the sample response defined at module level
        self.assertIn("items", SAMPLE_BLUESKY_RESPONSE)
        items = SAMPLE_BLUESKY_RESPONSE["items"]

        self.assertEqual(len(items), 2)

        for item in items:
            # Required fields
            self.assertIn("text", item)
            self.assertIn("url", item)
            self.assertIn("author_handle", item)
            self.assertIn("relevance", item)

            # URL should be valid Bluesky URL
            self.assertTrue(
                item["url"].startswith("https://bsky.app/"),
                f"Invalid Bluesky URL: {item['url']}"
            )

            # Engagement should have expected fields
            if item.get("engagement"):
                eng = item["engagement"]
                self.assertIn("likes", eng)
                self.assertIn("reposts", eng)


if __name__ == "__main__":
    unittest.main()
