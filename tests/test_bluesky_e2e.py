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
# Matches actual Bluesky AT Protocol API response structure
SAMPLE_BLUESKY_RESPONSE = {
    "posts": [
        {
            "uri": "at://did:plc:testuser123/app.bsky.feed.post/abc123",
            "author": {
                "handle": "testuser.bsky.social",
                "displayName": "Test User"
            },
            "record": {
                "text": "Just discovered an amazing new tool for productivity!",
                "createdAt": "2026-01-15T12:30:00.000Z"
            },
            "likeCount": 42,
            "repostCount": 10,
            "replyCount": 5,
            "quoteCount": 2
        },
        {
            "uri": "at://did:plc:reviewer456/app.bsky.feed.post/def456",
            "author": {
                "handle": "reviewer.bsky.social",
                "displayName": "App Reviewer"
            },
            "record": {
                "text": "Here's my review of the latest features in the app",
                "createdAt": "2026-01-20T18:45:00.000Z"
            },
            "likeCount": 128,
            "repostCount": 25,
            "replyCount": 15,
            "quoteCount": 8
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
            mock_http.get.return_value = SAMPLE_BLUESKY_RESPONSE

            result = bluesky.search_bluesky(
                topic="productivity tools",
                from_date="2026-01-01",
                to_date="2026-01-31",
            )

            self.assertIsInstance(result, dict)
            self.assertIn("posts", result)

    def test_parse_response_extracts_items(self):
        """Test that parse_bluesky_response extracts items correctly."""
        items = bluesky.parse_bluesky_response(SAMPLE_BLUESKY_RESPONSE)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["author_handle"], "testuser.bsky.social")
        self.assertEqual(items[0]["text"], "Just discovered an amazing new tool for productivity!")
        self.assertEqual(items[0]["engagement"]["likes"], 42)
        self.assertEqual(items[1]["engagement"]["likes"], 128)
        self.assertEqual(items[1]["author_handle"], "reviewer.bsky.social")

    def test_parse_response_handles_empty(self):
        """Test that parse handles empty responses gracefully."""
        items = bluesky.parse_bluesky_response({})
        self.assertEqual(items, [])

    def test_parse_response_validates_dates(self):
        """Test that invalid dates are handled."""
        response = {
            "posts": [
                {
                    "uri": "at://did:plc:test123/app.bsky.feed.post/123",
                    "author": {
                        "handle": "test.bsky.social"
                    },
                    "record": {
                        "text": "Test post",
                        "createdAt": "invalid-date-format"
                    },
                    "likeCount": 0,
                    "repostCount": 0,
                    "replyCount": 0,
                    "quoteCount": 0
                }
            ]
        }
        items = bluesky.parse_bluesky_response(response)
        # Invalid date should be set to None
        self.assertEqual(len(items), 1)
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
        try:
            result = bluesky.search_bluesky(
                topic="python programming",
                from_date="2026-01-01",
                to_date="2026-01-31",
                depth="quick",
            )

            self.assertIsInstance(result, dict)
            # Should have posts array from the API
            self.assertIn("posts", result)
        except Exception as e:
            # Handle 403 or other API access errors gracefully
            # Bluesky API might be restricted or changed
            if "403" in str(e) or "Forbidden" in str(e):
                self.skipTest(f"Bluesky API returned 403 Forbidden - API may have restricted access or requires auth: {e}")
            else:
                # Re-raise other unexpected errors
                raise


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
        self.assertIn("posts", SAMPLE_BLUESKY_RESPONSE)
        posts = SAMPLE_BLUESKY_RESPONSE["posts"]

        self.assertEqual(len(posts), 2)

        for post in posts:
            # Required fields in raw API response
            self.assertIn("uri", post)
            self.assertIn("author", post)
            self.assertIn("record", post)

            # Author fields
            self.assertIn("handle", post["author"])

            # Record fields
            self.assertIn("text", post["record"])
            self.assertIn("createdAt", post["record"])

            # URI should be valid AT Protocol URI
            self.assertTrue(
                post["uri"].startswith("at://"),
                f"Invalid AT URI: {post['uri']}"
            )

            # Engagement metrics at top level
            self.assertIn("likeCount", post)
            self.assertIn("repostCount", post)


if __name__ == "__main__":
    unittest.main()
