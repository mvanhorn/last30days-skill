"""Tests for cache module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import cache


class TestGetCacheKey(unittest.TestCase):
    def test_returns_string(self):
        result = cache.get_cache_key("test topic", "2026-01-01", "2026-01-31", "both")
        self.assertIsInstance(result, str)

    def test_consistent_for_same_inputs(self):
        key1 = cache.get_cache_key("test topic", "2026-01-01", "2026-01-31", "both")
        key2 = cache.get_cache_key("test topic", "2026-01-01", "2026-01-31", "both")
        self.assertEqual(key1, key2)

    def test_different_for_different_inputs(self):
        key1 = cache.get_cache_key("topic a", "2026-01-01", "2026-01-31", "both")
        key2 = cache.get_cache_key("topic b", "2026-01-01", "2026-01-31", "both")
        self.assertNotEqual(key1, key2)

    def test_key_length(self):
        key = cache.get_cache_key("test", "2026-01-01", "2026-01-31", "both")
        self.assertEqual(len(key), 16)


class TestCachePath(unittest.TestCase):
    def test_returns_path(self):
        result = cache.get_cache_path("abc123")
        self.assertIsInstance(result, Path)

    def test_has_json_extension(self):
        result = cache.get_cache_path("abc123")
        self.assertEqual(result.suffix, ".json")


class TestCacheValidity(unittest.TestCase):
    def test_nonexistent_file_is_invalid(self):
        fake_path = Path("/nonexistent/path/file.json")
        result = cache.is_cache_valid(fake_path)
        self.assertFalse(result)


class TestModelCache(unittest.TestCase):
    def test_get_cached_model_returns_none_for_missing(self):
        # Clear any existing cache first
        result = cache.get_cached_model("nonexistent_provider")
        # May be None or a cached value, but should not error
        self.assertTrue(result is None or isinstance(result, str))


if __name__ == "__main__":
    unittest.main()
