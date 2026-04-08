import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.instagram import _parse_items


class TestInstagramOwnerTypeSafety(unittest.TestCase):
    def _make_raw(self, **overrides):
        base = {
            "id": "1",
            "code": "ABC123",
            "caption": "test caption",
            "owner": {"username": "testuser"},
        }
        base.update(overrides)
        return base

    def test_owner_as_dict(self):
        items = _parse_items([self._make_raw()], "test")
        self.assertEqual("testuser", items[0]["author_name"])

    def test_owner_as_string(self):
        items = _parse_items([self._make_raw(owner="stringuser")], "test")
        self.assertEqual("stringuser", items[0]["author_name"])

    def test_owner_missing(self):
        raw = self._make_raw()
        del raw["owner"]
        items = _parse_items([raw], "test")
        self.assertEqual("", items[0]["author_name"])

    def test_owner_none(self):
        items = _parse_items([self._make_raw(owner=None)], "test")
        self.assertEqual("", items[0]["author_name"])

    def test_user_field_fallback(self):
        raw = self._make_raw()
        del raw["owner"]
        raw["user"] = {"username": "fallbackuser"}
        items = _parse_items([raw], "test")
        self.assertEqual("fallbackuser", items[0]["author_name"])


class TestExpandInstagramQueries(unittest.TestCase):
    """Tests for expand_instagram_queries() multi-query generation."""

    def test_default_depth_returns_two_plus_queries(self):
        from lib.instagram import expand_instagram_queries
        queries = expand_instagram_queries("Kanye West", "default")
        self.assertGreaterEqual(len(queries), 2)
        # Breaking_news intent should include reaction/edit variant
        variant_found = any(
            "reaction" in q.lower() or "edit" in q.lower()
            for q in queries
        )
        self.assertTrue(variant_found, f"Expected reaction/edit variant: {queries}")

    def test_quick_depth_returns_one_query(self):
        from lib.instagram import expand_instagram_queries
        queries = expand_instagram_queries("Kanye West", "quick")
        self.assertEqual(len(queries), 1)


if __name__ == "__main__":
    unittest.main()
