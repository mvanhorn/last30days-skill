import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import dates


class DatesV3Tests(unittest.TestCase):
    def test_get_date_range_returns_iso_window(self):
        start, end = dates.get_date_range(7)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(7, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days)

    def test_parse_date_supports_timestamps_and_iso_variants(self):
        unix_parsed = dates.parse_date("1710460800")
        self.assertEqual("2024-03-15T00:00:00+00:00", unix_parsed.isoformat())

        plain = dates.parse_date("2026-03-16")
        self.assertEqual("2026-03-16T00:00:00+00:00", plain.isoformat())

        zulu = dates.parse_date("2026-03-16T12:34:56Z")
        self.assertEqual("2026-03-16T12:34:56+00:00", zulu.isoformat())

        fractional = dates.parse_date("2026-03-16T12:34:56.123456+02:00")
        self.assertEqual("2026-03-16T10:34:56.123456+00:00", fractional.isoformat())

    def test_parse_date_rejects_empty_and_invalid_values(self):
        self.assertIsNone(dates.parse_date(None))
        self.assertIsNone(dates.parse_date(""))
        self.assertIsNone(dates.parse_date("not-a-date"))

    def test_timestamp_to_date_handles_valid_and_invalid_values(self):
        self.assertEqual("2024-03-15", dates.timestamp_to_date(1710460800))
        self.assertIsNone(dates.timestamp_to_date(None))
        self.assertIsNone(dates.timestamp_to_date("bad"))

    def test_get_date_confidence_distinguishes_in_range_and_invalid(self):
        self.assertEqual("high", dates.get_date_confidence("2026-03-10", "2026-03-01", "2026-03-16"))
        self.assertEqual("low", dates.get_date_confidence("2026-02-10", "2026-03-01", "2026-03-16"))
        self.assertEqual("low", dates.get_date_confidence("2026-03-20", "2026-03-01", "2026-03-16"))
        self.assertEqual("low", dates.get_date_confidence("bad", "2026-03-01", "2026-03-16"))
        self.assertEqual("low", dates.get_date_confidence(None, "2026-03-01", "2026-03-16"))

    def test_days_ago_and_recency_score_cover_edge_cases(self):
        today = datetime.now(timezone.utc).date()
        old = (today - timedelta(days=45)).isoformat()
        future = (today + timedelta(days=1)).isoformat()

        self.assertEqual(0, dates.days_ago(today.isoformat()))
        self.assertIsNone(dates.days_ago("bad"))

        self.assertEqual(100, dates.recency_score(today.isoformat()))
        self.assertEqual(0, dates.recency_score(old))
        self.assertEqual(100, dates.recency_score(future))
        self.assertEqual(0, dates.recency_score(None))


if __name__ == "__main__":
    unittest.main()
