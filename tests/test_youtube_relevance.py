"""Tests for YouTube relevance scoring."""

import unittest

# Add lib to path

from lib.relevance import token_overlap_relevance as _compute_relevance, tokenize as _tokenize


class TestTokenize(unittest.TestCase):
    def test_basic(self):
        tokens = _tokenize("Claude Code Tutorial")
        self.assertIn("claude", tokens)
        self.assertIn("code", tokens)
        self.assertIn("tutorial", tokens)

    def test_removes_stopwords(self):
        tokens = _tokenize("how to use Claude Code for beginners")
        self.assertNotIn("how", tokens)
        self.assertNotIn("to", tokens)
        self.assertNotIn("for", tokens)
        self.assertIn("claude", tokens)
        self.assertIn("code", tokens)
        self.assertIn("beginners", tokens)

    def test_strips_punctuation(self):
        tokens = _tokenize("What's new in Claude?")
        self.assertIn("claude", tokens)
        self.assertNotIn("what's", tokens)

    def test_drops_single_char(self):
        tokens = _tokenize("a b c Claude")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("claude", tokens)

    def test_empty(self):
        tokens = _tokenize("")
        self.assertEqual(tokens, set())

    def test_all_stopwords(self):
        tokens = _tokenize("the a an to for how")
        self.assertEqual(tokens, set())


class TestComputeRelevance(unittest.TestCase):
    def test_exact_match(self):
        result = _compute_relevance("Claude Code", "Claude Code")
        self.assertEqual(result, 1.0)

    def test_full_match_in_longer_title(self):
        result = _compute_relevance("Claude Code", "Claude Code Tutorial")
        self.assertEqual(result, 1.0)

    def test_partial_match(self):
        result = _compute_relevance("Claude Code Tips", "Claude Tips for Beginners")
        self.assertGreater(result, 0.5)
        self.assertLess(result, 1.0)

    def test_no_match(self):
        result = _compute_relevance("Claude Code", "Python Web Scraping")
        self.assertEqual(result, 0.0)

    def test_empty_query_returns_neutral(self):
        result = _compute_relevance("", "Some Video Title")
        self.assertEqual(result, 0.5)

    def test_stopword_only_query(self):
        result = _compute_relevance("how to the", "Some Video Title")
        self.assertEqual(result, 0.5)

    def test_empty_title(self):
        result = _compute_relevance("Claude Code", "")
        self.assertEqual(result, 0.0)

    def test_case_insensitive(self):
        result = _compute_relevance("claude code", "CLAUDE CODE Tutorial")
        self.assertEqual(result, 1.0)

    def test_stopwords_in_title_dont_inflate(self):
        # "Claude Code" query against a title with lots of stopwords
        # Should still match well since Claude and Code are present
        result = _compute_relevance(
            "Claude Code",
            "How To Use Claude Code For Complete Beginners"
        )
        self.assertEqual(result, 1.0)

    def test_no_match_returns_zero(self):
        result = _compute_relevance("quantum computing", "cat videos compilation")
        self.assertEqual(result, 0.0)

    def test_cap_at_1_0(self):
        result = _compute_relevance("AI", "AI AI AI AI AI")
        self.assertLessEqual(result, 1.0)

    def test_single_word_query(self):
        result = _compute_relevance("Seedance", "Seedance AI Video Generator Review")
        self.assertEqual(result, 1.0)

    def test_single_word_no_match(self):
        result = _compute_relevance("Seedance", "Random cooking video")
        self.assertEqual(result, 0.0)

if __name__ == "__main__":
    unittest.main()
