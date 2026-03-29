"""Tests for date safety filters in the main last30days pipeline."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import last30days
from lib import schema


class TestDateSafetyFilters(unittest.TestCase):
    def test_youtube_items_outside_range_are_removed_before_report(self):
        youtube_items = [
            schema.YouTubeItem(
                id="recent",
                title="Recent video",
                url="https://youtube.com/watch?v=recent",
                channel_name="ch",
                date="2026-03-27",
            ),
            schema.YouTubeItem(
                id="old",
                title="Old video",
                url="https://youtube.com/watch?v=old",
                channel_name="ch",
                date="2025-09-20",
            ),
        ]

        filtered = last30days.apply_date_safety_filters(
            youtube_items=youtube_items,
            from_date="2026-02-26",
            to_date="2026-03-28",
        )

        self.assertEqual([item.id for item in filtered["youtube"]], ["recent"])


if __name__ == "__main__":
    unittest.main()
