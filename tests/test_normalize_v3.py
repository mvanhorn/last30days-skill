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
