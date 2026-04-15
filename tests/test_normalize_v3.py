import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import normalize


class NormalizeV3Tests(unittest.TestCase):
    def test_youtube_evergreen_fallback_keeps_older_items_when_recent_pool_is_empty(self):
        items = [
            {
                "video_id": "vid-1",
                "title": "Deploy to Fly.io tutorial",
                "url": "https://youtube.com/watch?v=vid-1",
                "channel_name": "Example",
                "date": "2026-01-10",
                "engagement": {"views": 1000, "likes": 50, "comments": 10},
            }
        ]
        normalized = normalize.normalize_source_items(
            "youtube",
            items,
            "2026-02-15",
            "2026-03-17",
            freshness_mode="evergreen_ok",
        )
        self.assertEqual(1, len(normalized))
        self.assertEqual("2026-01-10", normalized[0].published_at)

    def test_grounding_still_drops_older_items_in_evergreen_mode(self):
        items = [
            {
                "id": "g-1",
                "title": "Fly.io guide",
                "url": "https://example.com/fly-guide",
                "date": "2026-01-08",
                "date_confidence": "high",
                "snippet": "Step-by-step guide.",
            }
        ]
        normalized = normalize.normalize_source_items(
            "grounding",
            items,
            "2026-02-15",
            "2026-03-17",
            freshness_mode="evergreen_ok",
        )
        self.assertEqual([], normalized)

    def test_youtube_top_comments_passthrough_with_field_mapping(self):
        """YT comments from enrich_with_comments use likes/text; normalize must
        carry them into metadata as the Reddit-compatible {score, excerpt} shape."""
        items = [
            {
                "video_id": "vid-1",
                "title": "How to deploy",
                "url": "https://youtube.com/watch?v=vid-1",
                "channel_name": "Example",
                "date": "2026-03-01",
                "engagement": {"views": 10000, "likes": 500, "comments": 30},
                "top_comments": [
                    {"author": "Alice", "text": "Best tutorial ever", "likes": 120, "date": "2026-03-02"},
                    {"author": "Bob", "text": "Helped me ship", "likes": 45, "date": "2026-03-03"},
                    {"author": "Carol", "text": "Solid walkthrough", "likes": 7, "date": "2026-03-04"},
                ],
            }
        ]
        normalized = normalize.normalize_source_items(
            "youtube", items, "2026-02-15", "2026-03-17",
        )
        self.assertEqual(1, len(normalized))
        top = normalized[0].metadata.get("top_comments")
        self.assertIsNotNone(top)
        self.assertEqual(3, len(top))
        # First comment: likes->score, text->excerpt
        self.assertEqual(120, top[0]["score"])
        self.assertEqual("Best tutorial ever", top[0]["excerpt"])
        self.assertEqual("Alice", top[0]["author"])
        self.assertEqual("2026-03-02", top[0]["date"])
        # Preserves ordering from input (already sorted desc upstream)
        self.assertEqual(45, top[1]["score"])
        self.assertEqual(7, top[2]["score"])

    def test_youtube_top_comments_empty_list_passes_through_cleanly(self):
        items = [
            {
                "video_id": "vid-2",
                "title": "Short clip",
                "url": "https://youtube.com/watch?v=vid-2",
                "channel_name": "Example",
                "date": "2026-03-01",
                "engagement": {"views": 50, "likes": 2},
                "top_comments": [],
            }
        ]
        normalized = normalize.normalize_source_items(
            "youtube", items, "2026-02-15", "2026-03-17",
        )
        self.assertEqual(1, len(normalized))
        # Empty list is fine; metadata may have empty top_comments or omit it.
        top = normalized[0].metadata.get("top_comments", [])
        self.assertEqual([], top)

    def test_youtube_without_top_comments_key_does_not_crash(self):
        items = [
            {
                "video_id": "vid-3",
                "title": "No comments fetched",
                "url": "https://youtube.com/watch?v=vid-3",
                "channel_name": "Example",
                "date": "2026-03-01",
                "engagement": {"views": 100, "likes": 5},
            }
        ]
        normalized = normalize.normalize_source_items(
            "youtube", items, "2026-02-15", "2026-03-17",
        )
        self.assertEqual(1, len(normalized))
        self.assertEqual([], normalized[0].metadata.get("top_comments", []))

    def test_youtube_top_comments_feed_top_comment_score_signal(self):
        """Integration: after normalize, signals._top_comment_score should
        return log1p(first comment score) for YT, proving the full chain."""
        from lib import signals
        import math
        items = [
            {
                "video_id": "vid-4",
                "title": "Viral comment thread",
                "url": "https://youtube.com/watch?v=vid-4",
                "channel_name": "Example",
                "date": "2026-03-01",
                "engagement": {"views": 1000, "likes": 50, "comments": 10},
                "top_comments": [
                    {"author": "A", "text": "Legendary", "likes": 9999, "date": "2026-03-02"},
                ],
            }
        ]
        normalized = normalize.normalize_source_items(
            "youtube", items, "2026-02-15", "2026-03-17",
        )
        self.assertAlmostEqual(math.log1p(9999), signals._top_comment_score(normalized[0]), places=4)

    def test_tiktok_top_comments_passthrough_with_digg_count_mapping(self):
        """TikTok comments from enrich_with_comments use digg_count/text;
        normalize must map to the shared {score, excerpt} shape."""
        items = [
            {
                "id": "tt-1",
                "text": "POV: shipping on Friday",
                "url": "https://www.tiktok.com/@u/video/tt-1",
                "author_name": "u",
                "date": "2026-03-01",
                "engagement": {"views": 50000, "likes": 2000, "comments": 300},
                "top_comments": [
                    {"author": "Alice", "text": "dead", "digg_count": 1200, "date": "2026-03-02"},
                    {"author": "Bob", "text": "so real", "digg_count": 400, "date": "2026-03-03"},
                ],
            }
        ]
        normalized = normalize.normalize_source_items(
            "tiktok", items, "2026-02-15", "2026-03-17",
        )
        self.assertEqual(1, len(normalized))
        top = normalized[0].metadata.get("top_comments")
        self.assertEqual(2, len(top))
        self.assertEqual(1200, top[0]["score"])
        self.assertEqual("dead", top[0]["excerpt"])
        self.assertEqual("Alice", top[0]["author"])
        self.assertEqual(400, top[1]["score"])

    def test_tiktok_without_top_comments_does_not_crash(self):
        items = [
            {
                "id": "tt-2",
                "text": "plain clip",
                "url": "https://www.tiktok.com/@u/video/tt-2",
                "author_name": "u",
                "date": "2026-03-01",
                "engagement": {"views": 1000, "likes": 20},
            }
        ]
        normalized = normalize.normalize_source_items(
            "tiktok", items, "2026-02-15", "2026-03-17",
        )
        self.assertEqual([], normalized[0].metadata.get("top_comments", []))

    def test_tiktok_top_comments_feed_top_comment_score_signal(self):
        from lib import signals
        import math
        items = [
            {
                "id": "tt-3",
                "text": "viral",
                "url": "https://www.tiktok.com/@u/video/tt-3",
                "author_name": "u",
                "date": "2026-03-01",
                "engagement": {"views": 100000, "likes": 5000, "comments": 500},
                "top_comments": [
                    {"author": "A", "text": "this aged well", "digg_count": 50000, "date": "2026-03-02"},
                ],
            }
        ]
        normalized = normalize.normalize_source_items(
            "tiktok", items, "2026-02-15", "2026-03-17",
        )
        self.assertAlmostEqual(math.log1p(50000), signals._top_comment_score(normalized[0]), places=4)

    def test_grounding_requires_a_usable_date(self):
        items = [
            {
                "id": "g-1",
                "title": "Undated result",
                "url": "https://example.com/undated",
                "snippet": "No date attached.",
            }
        ]
        normalized = normalize.normalize_source_items(
            "grounding",
            items,
            "2026-02-15",
            "2026-03-17",
        )
        self.assertEqual([], normalized)


if __name__ == "__main__":
    unittest.main()
