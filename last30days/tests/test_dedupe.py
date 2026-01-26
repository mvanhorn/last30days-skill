"""Tests for dedupe module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import dedupe, schema


class TestNormalizeText(unittest.TestCase):
    def test_lowercase(self):
        result = dedupe.normalize_text("HELLO World")
        self.assertEqual(result, "hello world")

    def test_removes_punctuation(self):
        result = dedupe.normalize_text("Hello, World!")
        # Punctuation replaced with space, then whitespace collapsed
        self.assertEqual(result, "hello world")

    def test_collapses_whitespace(self):
        result = dedupe.normalize_text("hello    world")
        self.assertEqual(result, "hello world")


class TestGetNgrams(unittest.TestCase):
    def test_short_text(self):
        result = dedupe.get_ngrams("ab", n=3)
        self.assertEqual(result, {"ab"})

    def test_normal_text(self):
        result = dedupe.get_ngrams("hello", n=3)
        self.assertIn("hel", result)
        self.assertIn("ell", result)
        self.assertIn("llo", result)


class TestJaccardSimilarity(unittest.TestCase):
    def test_identical_sets(self):
        set1 = {"a", "b", "c"}
        result = dedupe.jaccard_similarity(set1, set1)
        self.assertEqual(result, 1.0)

    def test_disjoint_sets(self):
        set1 = {"a", "b", "c"}
        set2 = {"d", "e", "f"}
        result = dedupe.jaccard_similarity(set1, set2)
        self.assertEqual(result, 0.0)

    def test_partial_overlap(self):
        set1 = {"a", "b", "c"}
        set2 = {"b", "c", "d"}
        result = dedupe.jaccard_similarity(set1, set2)
        self.assertEqual(result, 0.5)  # 2 overlap / 4 union

    def test_empty_sets(self):
        result = dedupe.jaccard_similarity(set(), set())
        self.assertEqual(result, 0.0)


class TestFindDuplicates(unittest.TestCase):
    def test_no_duplicates(self):
        items = [
            schema.RedditItem(id="R1", title="Completely different topic A", url="", subreddit=""),
            schema.RedditItem(id="R2", title="Another unrelated subject B", url="", subreddit=""),
        ]
        result = dedupe.find_duplicates(items)
        self.assertEqual(result, [])

    def test_finds_duplicates(self):
        items = [
            schema.RedditItem(id="R1", title="Best practices for Claude Code skills", url="", subreddit=""),
            schema.RedditItem(id="R2", title="Best practices for Claude Code skills guide", url="", subreddit=""),
        ]
        result = dedupe.find_duplicates(items, threshold=0.7)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (0, 1))


class TestDedupeItems(unittest.TestCase):
    def test_keeps_higher_scored(self):
        items = [
            schema.RedditItem(id="R1", title="Best practices for skills", url="", subreddit="", score=90),
            schema.RedditItem(id="R2", title="Best practices for skills guide", url="", subreddit="", score=50),
        ]
        result = dedupe.dedupe_items(items, threshold=0.6)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "R1")

    def test_keeps_all_unique(self):
        items = [
            schema.RedditItem(id="R1", title="Topic about apples", url="", subreddit="", score=90),
            schema.RedditItem(id="R2", title="Discussion of oranges", url="", subreddit="", score=50),
        ]
        result = dedupe.dedupe_items(items)
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        result = dedupe.dedupe_items([])
        self.assertEqual(result, [])

    def test_single_item(self):
        items = [schema.RedditItem(id="R1", title="Test", url="", subreddit="")]
        result = dedupe.dedupe_items(items)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
