"""Tests for dates module."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import dates


class TestGetDateRange(unittest.TestCase):
    def test_returns_tuple_of_two_strings(self):
        from_date, to_date = dates.get_date_range(30)
        self.assertIsInstance(from_date, str)
        self.assertIsInstance(to_date, str)

    def test_date_format(self):
        from_date, to_date = dates.get_date_range(30)
        # Should be YYYY-MM-DD format
        self.assertRegex(from_date, r'^\d{4}-\d{2}-\d{2}$')
        self.assertRegex(to_date, r'^\d{4}-\d{2}-\d{2}$')

    def test_range_is_correct_days(self):
        from_date, to_date = dates.get_date_range(30)
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        delta = end - start
        self.assertEqual(delta.days, 30)


class TestParseDate(unittest.TestCase):
    def test_parse_iso_date(self):
        result = dates.parse_date("2026-01-15")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 15)

    def test_parse_timestamp(self):
        # Unix timestamp for 2026-01-15 00:00:00 UTC
        result = dates.parse_date("1768435200")
        self.assertIsNotNone(result)

    def test_parse_none(self):
        result = dates.parse_date(None)
        self.assertIsNone(result)

    def test_parse_empty_string(self):
        result = dates.parse_date("")
        self.assertIsNone(result)


class TestTimestampToDate(unittest.TestCase):
    def test_valid_timestamp(self):
        # 2026-01-15 00:00:00 UTC
        result = dates.timestamp_to_date(1768435200)
        self.assertEqual(result, "2026-01-15")

    def test_none_timestamp(self):
        result = dates.timestamp_to_date(None)
        self.assertIsNone(result)


class TestGetDateConfidence(unittest.TestCase):
    def test_high_confidence_in_range(self):
        result = dates.get_date_confidence("2026-01-15", "2026-01-01", "2026-01-31")
        self.assertEqual(result, "high")

    def test_low_confidence_before_range(self):
        result = dates.get_date_confidence("2025-12-15", "2026-01-01", "2026-01-31")
        self.assertEqual(result, "low")

    def test_low_confidence_no_date(self):
        result = dates.get_date_confidence(None, "2026-01-01", "2026-01-31")
        self.assertEqual(result, "low")


class TestDaysAgo(unittest.TestCase):
    def test_today(self):
        today = datetime.now(timezone.utc).date().isoformat()
        result = dates.days_ago(today)
        self.assertEqual(result, 0)

    def test_none_date(self):
        result = dates.days_ago(None)
        self.assertIsNone(result)


class TestRecencyScore(unittest.TestCase):
    def test_today_is_100(self):
        today = datetime.now(timezone.utc).date().isoformat()
        result = dates.recency_score(today)
        self.assertEqual(result, 100)

    def test_30_days_ago_is_0(self):
        old_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        result = dates.recency_score(old_date)
        self.assertEqual(result, 0)

    def test_15_days_ago_is_50(self):
        mid_date = (datetime.now(timezone.utc).date() - timedelta(days=15)).isoformat()
        result = dates.recency_score(mid_date)
        self.assertEqual(result, 50)

    def test_none_date_is_0(self):
        result = dates.recency_score(None)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
