"""Unit tests for dedupe.py: text normalization, similarity metrics, and deduplication."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import dedupe
from lib.schema import SourceItem


def _item(title: str, body: str = "", source: str = "reddit", item_id: str = "t1") -> SourceItem:
    return SourceItem(
        item_id=item_id, source=source, title=title, body=body,
        url="https://example.com", engagement={}, metadata={},
    )


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText(unittest.TestCase):

    def test_lowercases(self):
        self.assertEqual(dedupe.normalize_text("Hello World"), "hello world")

    def test_strips_punctuation(self):
        self.assertEqual(dedupe.normalize_text("it's a test!"), "it s a test")

    def test_collapses_whitespace(self):
        self.assertEqual(dedupe.normalize_text("a   b\t\nc"), "a b c")

    def test_empty_string(self):
        self.assertEqual(dedupe.normalize_text(""), "")


# ---------------------------------------------------------------------------
# get_ngrams
# ---------------------------------------------------------------------------

class TestGetNgrams(unittest.TestCase):

    def test_simple_trigrams(self):
        ngrams = dedupe.get_ngrams("abcde")
        self.assertEqual(ngrams, {"abc", "bcd", "cde"})

    def test_short_text_returns_whole(self):
        ngrams = dedupe.get_ngrams("ab")
        self.assertEqual(ngrams, {"ab"})

    def test_empty_returns_empty_set(self):
        self.assertEqual(dedupe.get_ngrams(""), set())

    def test_normalizes_before_ngrams(self):
        # "A!B" -> "a b" after normalization -> {"a b"}
        ngrams = dedupe.get_ngrams("A!B")
        self.assertEqual(ngrams, {"a b"})


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------

class TestJaccardSimilarity(unittest.TestCase):

    def test_identical_sets(self):
        self.assertAlmostEqual(dedupe.jaccard_similarity({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets(self):
        self.assertAlmostEqual(dedupe.jaccard_similarity({"a"}, {"b"}), 0.0)

    def test_partial_overlap(self):
        result = dedupe.jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        self.assertAlmostEqual(result, 2.0 / 4.0)

    def test_empty_left(self):
        self.assertAlmostEqual(dedupe.jaccard_similarity(set(), {"a"}), 0.0)

    def test_both_empty(self):
        self.assertAlmostEqual(dedupe.jaccard_similarity(set(), set()), 0.0)


# ---------------------------------------------------------------------------
# token_jaccard
# ---------------------------------------------------------------------------

class TestTokenJaccard(unittest.TestCase):

    def test_identical_texts(self):
        self.assertAlmostEqual(dedupe.token_jaccard("hello world", "hello world"), 1.0)

    def test_completely_different(self):
        self.assertAlmostEqual(dedupe.token_jaccard("alpha beta", "gamma delta"), 0.0)

    def test_filters_stopwords(self):
        # "the" and "a" are stopwords, so "big cat" vs "big dog" should compare on {big, cat} vs {big, dog}
        result = dedupe.token_jaccard("the big cat", "a big dog")
        self.assertAlmostEqual(result, 1.0 / 3.0)  # {big} / {big, cat, dog}

    def test_filters_single_char_tokens(self):
        # Single char tokens like "I" are filtered (len > 1)
        result = dedupe.token_jaccard("I am great", "I am terrible")
        # "am" is len 2, "great"/"terrible" are content
        self.assertGreater(result, 0.0)


# ---------------------------------------------------------------------------
# hybrid_similarity
# ---------------------------------------------------------------------------

class TestHybridSimilarity(unittest.TestCase):

    def test_identical_texts(self):
        self.assertAlmostEqual(dedupe.hybrid_similarity("same text", "same text"), 1.0)

    def test_completely_different(self):
        result = dedupe.hybrid_similarity("aaaaaa", "zzzzzz")
        self.assertLess(result, 0.1)

    def test_takes_max_of_both_methods(self):
        text_a = "OpenClaw security issues discussion"
        text_b = "OpenClaw security issues thread"
        ngram_sim = dedupe.jaccard_similarity(
            dedupe.get_ngrams(text_a), dedupe.get_ngrams(text_b)
        )
        token_sim = dedupe.token_jaccard(text_a, text_b)
        self.assertAlmostEqual(
            dedupe.hybrid_similarity(text_a, text_b),
            max(ngram_sim, token_sim),
        )


# ---------------------------------------------------------------------------
# item_text
# ---------------------------------------------------------------------------

class TestItemText(unittest.TestCase):

    def test_combines_fields(self):
        item = _item("My Title", "My Body")
        text = dedupe.item_text(item)
        self.assertIn("My Title", text)
        self.assertIn("My Body", text)

    def test_skips_none_fields(self):
        item = _item("Title", "")
        item.author = None
        item.container = None
        text = dedupe.item_text(item)
        self.assertEqual(text, "Title")

    def test_includes_author_and_container(self):
        item = _item("Title", "Body")
        item.author = "john"
        item.container = "r/python"
        text = dedupe.item_text(item)
        self.assertIn("john", text)
        self.assertIn("r/python", text)


# ---------------------------------------------------------------------------
# dedupe_items
# ---------------------------------------------------------------------------

class TestDedupeItems(unittest.TestCase):

    def test_keeps_unique_items(self):
        items = [
            _item("OpenClaw is amazing", item_id="a"),
            _item("NanoClaw security review", item_id="b"),
            _item("IronClaw Rust architecture", item_id="c"),
        ]
        result = dedupe.dedupe_items(items)
        self.assertEqual(len(result), 3)

    def test_removes_near_duplicates(self):
        items = [
            _item("OpenClaw vs NanoClaw comparison review", item_id="a"),
            _item("OpenClaw vs NanoClaw comparison review thread", item_id="b"),
        ]
        result = dedupe.dedupe_items(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].item_id, "a")  # keeps first

    def test_keeps_first_of_duplicates(self):
        items = [
            _item("Same title here", item_id="first"),
            _item("Same title here", item_id="second"),
        ]
        result = dedupe.dedupe_items(items)
        self.assertEqual(result[0].item_id, "first")

    def test_empty_body_items_kept(self):
        item = SourceItem(
            item_id="empty", source="reddit", title="", body="",
            url="", engagement={}, metadata={},
        )
        result = dedupe.dedupe_items([item])
        self.assertEqual(len(result), 1)

    def test_threshold_respected(self):
        items = [
            _item("OpenClaw security analysis", item_id="a"),
            _item("OpenClaw security review", item_id="b"),
        ]
        # With threshold=1.0, only exact matches are removed
        result = dedupe.dedupe_items(items, threshold=1.0)
        self.assertEqual(len(result), 2)
        # With threshold=0.3, these similar items collapse
        result_loose = dedupe.dedupe_items(items, threshold=0.3)
        self.assertEqual(len(result_loose), 1)


if __name__ == "__main__":
    unittest.main()
