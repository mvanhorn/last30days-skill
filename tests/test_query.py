"""Tests for query.py — shared query utilities."""

import unittest

from lib.query import NOISE_WORDS, extract_compound_terms, extract_core_subject


class TestExtractCoreSubject(unittest.TestCase):
    """Tests for extract_core_subject() with default noise set."""

    def test_strips_what_are_prefix(self):
        self.assertEqual(extract_core_subject("what are the best AI tools"), "ai")

    def test_strips_how_to_prefix(self):
        self.assertEqual(extract_core_subject("how to use cursor IDE"), "cursor ide")

    def test_strips_what_do_people_think(self):
        result = extract_core_subject("what do people think about React Server Components")
        self.assertEqual(result, "react server components")

    def test_preserves_product_name(self):
        self.assertEqual(extract_core_subject("cursor IDE"), "cursor ide")

    def test_strips_trailing_punctuation(self):
        result = extract_core_subject("what is Claude?")
        self.assertFalse(result.endswith("?"))

    def test_empty_string(self):
        self.assertEqual(extract_core_subject(""), "")

    def test_all_noise_returns_original(self):
        # When all words are noise, fall back to original text
        result = extract_core_subject("best latest new")
        self.assertTrue(len(result) > 0)

    def test_only_first_prefix_stripped(self):
        # "how to" should match, stripping once, not recursively
        result = extract_core_subject("how to use how to debug")
        self.assertIn("debug", result)


class TestMaxWords(unittest.TestCase):
    """Tests for max_words parameter."""

    def test_max_words_caps_output(self):
        result = extract_core_subject(
            "multi agent reinforcement learning framework",
            max_words=5,
        )
        self.assertLessEqual(len(result.split()), 5)

    def test_max_words_none_no_cap(self):
        result = extract_core_subject("cursor IDE react native components")
        # Without max_words, no cap applied
        self.assertGreaterEqual(len(result.split()), 3)

    def test_max_words_fallback_on_empty(self):
        # All words filtered + max_words should fall back to original
        result = extract_core_subject("best top latest", max_words=3)
        self.assertTrue(len(result) > 0)


class TestStripSuffixes(unittest.TestCase):
    """Tests for strip_suffixes parameter."""

    def test_strips_best_practices(self):
        result = extract_core_subject(
            "claude code best practices",
            strip_suffixes=True,
        )
        self.assertNotIn("practices", result)

    def test_strips_use_cases(self):
        result = extract_core_subject(
            "react hooks use cases",
            strip_suffixes=True,
        )
        self.assertNotIn("cases", result)

    def test_no_strip_without_flag(self):
        result = extract_core_subject("claude code best practices")
        # "best" and "practices" are noise words so they get filtered anyway
        # but the suffix phase doesn't run
        self.assertIn("claude", result)


class TestCustomNoise(unittest.TestCase):
    """Tests for noise override parameter."""

    def test_custom_noise_keeps_tips(self):
        # YouTube keeps tips/tricks/tutorial — pass a noise set without them
        youtube_noise = frozenset({
            'best', 'top', 'good', 'great', 'awesome', 'killer',
            'latest', 'new', 'news', 'update', 'updates',
            'trending', 'hottest', 'popular', 'viral',
            'practices', 'features',
            'recommendations', 'advice',
            'prompt', 'prompts', 'prompting',
            'methods', 'strategies', 'approaches',
        })
        result = extract_core_subject("best react tips", noise=youtube_noise)
        self.assertIn("tips", result)

    def test_default_noise_removes_tips(self):
        result = extract_core_subject("best react tips")
        self.assertNotIn("tips", result)


class TestNoiseWordsCompleteness(unittest.TestCase):
    """Verify NOISE_WORDS superset covers all platform sets."""

    def test_question_words_present(self):
        for w in ('who', 'why', 'when', 'where', 'does', 'should', 'could', 'would'):
            self.assertIn(w, NOISE_WORDS, f"Missing question word: {w}")

    def test_core_filler_present(self):
        for w in ('the', 'a', 'an', 'is', 'are', 'for', 'with', 'about'):
            self.assertIn(w, NOISE_WORDS)

    def test_research_meta_present(self):
        for w in ('best', 'top', 'latest', 'trending', 'popular'):
            self.assertIn(w, NOISE_WORDS)


class TestExtractCompoundTerms(unittest.TestCase):
    """Tests for extract_compound_terms()."""

    def test_hyphenated(self):
        terms = extract_compound_terms("multi-agent reinforcement learning")
        self.assertIn("multi-agent", terms)

    def test_title_case(self):
        terms = extract_compound_terms("Claude Code and React Native")
        self.assertTrue(any("Claude Code" in t for t in terms))
        self.assertTrue(any("React Native" in t for t in terms))

    def test_no_compounds(self):
        terms = extract_compound_terms("python tutorial")
        self.assertEqual(len(terms), 0)

    def test_multiple_hyphens(self):
        terms = extract_compound_terms("vc-backed start-up")
        self.assertIn("vc-backed", terms)
        self.assertIn("start-up", terms)

if __name__ == "__main__":
    unittest.main()
