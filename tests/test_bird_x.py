import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.bird_x import parse_bird_response


class TestBirdXEngagementZero(unittest.TestCase):
    def test_zero_likes_preserved(self):
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "likeCount": 0,
                "retweetCount": 5,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(0, items[0]["engagement"]["likes"])
        self.assertEqual(5, items[0]["engagement"]["reposts"])

    def test_none_likes_when_missing(self):
        tweets = [
            {
                "id": "1",
                "text": "test tweet with no engagement fields",
                "permanent_url": "https://x.com/u/status/1",
                # no likeCount, like_count, or favorite_count
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertIsNone(items[0]["engagement"]["likes"])

    def test_fallback_to_second_key(self):
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "like_count": 7,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(7, items[0]["engagement"]["likes"])

    def test_zero_does_not_fall_through(self):
        """likeCount=0 should not fall through to like_count=10."""
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "likeCount": 0,
                "like_count": 10,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(0, items[0]["engagement"]["likes"])


if __name__ == "__main__":
    unittest.main()
