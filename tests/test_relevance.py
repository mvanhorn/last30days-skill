"""Tests for relevance.py — shared relevance scoring.

Migrated from test_youtube_relevance.py + new hashtag/synonym tests.
"""

import unittest

from lib.relevance import STOPWORDS, SYNONYMS, token_overlap_relevance, tokenize


class TestTokenize(unittest.TestCase):
    """Tests for tokenize()."""

    def test_removes_stopwords(self):
        tokens = tokenize("how to use the AI tools")
        self.assertNotIn("how", tokens)
        self.assertNotIn("to", tokens)
        self.assertNotIn("the", tokens)
        self.assertIn("ai", tokens)
        self.assertIn("tools", tokens)

    def test_lowercases(self):
        tokens = tokenize("Python REACT")
        self.assertIn("python", tokens)
        self.assertIn("react", tokens)

    def test_strips_punctuation(self):
        tokens = tokenize("hello, world!")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_drops_single_char(self):
        tokens = tokenize("a b c python")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertNotIn("c", tokens)
        self.assertIn("python", tokens)

    def test_expands_synonyms(self):
        tokens = tokenize("ai tools")
        self.assertIn("artificial", tokens)
        self.assertIn("intelligence", tokens)

    def test_expands_js_synonym(self):
        tokens = tokenize("js framework")
        self.assertIn("javascript", tokens)

    def test_expands_svelte(self):
        tokens = tokenize("svelte app")
        self.assertIn("sveltejs", tokens)

    def test_expands_vue(self):
        tokens = tokenize("vue components")
        self.assertIn("vuejs", tokens)


class TestTokenOverlapRelevance(unittest.TestCase):
    """Tests for token_overlap_relevance()."""

    def test_high_relevance_exact_match(self):
        rel = token_overlap_relevance("claude code", "Claude Code tricks and tips")
        self.assertGreater(rel, 0.7)

    def test_low_relevance_no_match(self):
        rel = token_overlap_relevance("claude code tips", "Best AI tools for coding")
        self.assertLess(rel, 0.5)

    def test_empty_query_returns_neutral(self):
        rel = token_overlap_relevance("", "Some video title")
        self.assertEqual(rel, 0.5)

    def test_floor_at_0_1(self):
        rel = token_overlap_relevance("quantum physics", "cat dancing video")
        self.assertEqual(rel, 0.0)

    def test_full_match_returns_1(self):
        rel = token_overlap_relevance("python tutorial", "Python Tutorial for Beginners")
        self.assertEqual(rel, 1.0)

    def test_partial_match(self):
        rel = token_overlap_relevance("react native tutorial", "React Native Guide")
        self.assertGreater(rel, 0.3)
        self.assertLess(rel, 1.0)

    def test_synonym_boosts_relevance(self):
        # "js" should match "javascript" via synonym expansion
        rel_with_syn = token_overlap_relevance("js framework", "javascript framework comparison")
        rel_without = token_overlap_relevance("python framework", "javascript framework comparison")
        self.assertGreater(rel_with_syn, rel_without)

    def test_stopword_only_query(self):
        rel = token_overlap_relevance("the a is", "some content here")
        self.assertEqual(rel, 0.5)

    def test_generic_only_overlap_stays_below_filter_threshold(self):
        rel = token_overlap_relevance("anthropic odds", "Republican house odds update")
        self.assertLess(rel, 0.3)

    def test_informative_partial_match_stays_above_generic_only(self):
        generic_only = token_overlap_relevance("anthropic odds", "Republican house odds update")
        informative = token_overlap_relevance("anthropic odds", "Anthropic valuation market")
        self.assertGreater(informative, generic_only)


class TestHashtagRelevance(unittest.TestCase):
    """Tests for hashtag-aware relevance (TikTok/Instagram pattern)."""

    def test_hashtag_boost(self):
        rel_no_hash = token_overlap_relevance("claude code", "random video about stuff")
        rel_with_hash = token_overlap_relevance(
            "claude code", "random video about stuff", ["claudecode", "ai"]
        )
        self.assertGreater(rel_with_hash, rel_no_hash)

    def test_concatenated_hashtag_splitting(self):
        # "claudecode" should match "claude" from query via substring check
        rel = token_overlap_relevance("claude", "video", ["claudecode"])
        self.assertGreater(rel, 0.5)

    def test_none_hashtags_same_as_no_hashtags(self):
        rel1 = token_overlap_relevance("test query", "test content", None)
        rel2 = token_overlap_relevance("test query", "test content")
        self.assertEqual(rel1, rel2)

if __name__ == "__main__":
    unittest.main()
