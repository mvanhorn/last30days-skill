"""Tests for query.py — shared query utilities."""

import unittest

from lib.query import (
    NOISE_WORDS,
    SOCIAL_NOISE,
    VIRAL_NOISE,
    extract_compound_terms,
    extract_core_subject,
    infer_query_intent,
)


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



class TestSharedAdapterNoiseSets(unittest.TestCase):
    """Pin SOCIAL_NOISE / VIRAL_NOISE so adapters can rely on stable membership.

    Bluesky, Threads, Truth Social use SOCIAL_NOISE.
    TikTok, Instagram, Pinterest use VIRAL_NOISE.
    YouTube extends VIRAL_NOISE with temporal/meta tokens (asserted in its
    own adapter test).
    """

    def test_social_noise_membership(self):
        # Words shared with the historical _BSKY_NOISE / _TS_NOISE / _THREADS_NOISE.
        expected = {
            'best', 'top', 'good', 'great', 'awesome',
            'latest', 'new', 'news', 'update', 'updates',
            'trending', 'hottest', 'popular', 'viral',
            'practices', 'features', 'recommendations', 'advice',
            'or', 'and',
        }
        self.assertEqual(set(SOCIAL_NOISE), expected)

    def test_viral_noise_is_social_superset(self):
        self.assertTrue(SOCIAL_NOISE.issubset(VIRAL_NOISE))
        # The extra words VIRAL adds on top of SOCIAL: the historical
        # tiktok / instagram / pinterest delta.
        delta = VIRAL_NOISE - SOCIAL_NOISE
        self.assertEqual(
            delta,
            {'killer', 'prompt', 'prompts', 'prompting',
             'methods', 'strategies', 'approaches'},
        )

    def test_extract_core_subject_with_social_noise(self):
        # Sanity: a Bluesky-style query strips through SOCIAL_NOISE.
        result = extract_core_subject(
            "best new Claude Code update",
            noise=SOCIAL_NOISE,
        )
        self.assertEqual(result, "claude code")

    def test_extract_core_subject_with_viral_noise(self):
        # Viral set strips 'killer' and the prompt cluster.
        result = extract_core_subject(
            "killer prompting strategies for React",
            noise=VIRAL_NOISE,
        )
        # 'for' is in the default NOISE_WORDS path but VIRAL_NOISE alone
        # doesn't include articles/prepositions; extract_core_subject
        # falls back to original when nothing survives, so allow 'for'.
        self.assertIn("react", result)
        self.assertNotIn("killer", result)
        self.assertNotIn("prompting", result)


class TestInferQueryIntent(unittest.TestCase):
    """Tests for infer_query_intent() — the shared canonical classifier.

    Adapters previously kept five near-duplicate copies with subtle drift
    (reddit added `prediction` and the longest `how_to` regex; youtube had
    a partial extension; instagram and tiktok lagged). Canonical here is
    reddit's superset.
    """

    def test_comparison(self):
        self.assertEqual(infer_query_intent("Claude vs Gemini"), "comparison")
        self.assertEqual(infer_query_intent("difference between X and Y"), "comparison")

    def test_how_to_base(self):
        self.assertEqual(infer_query_intent("how to deploy Kubernetes"), "how_to")
        self.assertEqual(infer_query_intent("install nginx"), "how_to")
        self.assertEqual(infer_query_intent("setup OAuth tutorial"), "how_to")

    def test_how_to_extended_keywords(self):
        # Bare imperatives covered by reddit's extended regex.
        self.assertEqual(infer_query_intent("configure DNS"), "how_to")
        self.assertEqual(infer_query_intent("troubleshoot router"), "how_to")
        self.assertEqual(infer_query_intent("debug python"), "how_to")
        self.assertEqual(infer_query_intent("fix kernel panic"), "how_to")

    def test_opinion(self):
        self.assertEqual(infer_query_intent("thoughts on Claude Code"), "opinion")
        self.assertEqual(infer_query_intent("should I buy a Pixel"), "opinion")

    def test_product(self):
        self.assertEqual(infer_query_intent("best laptop for programming"), "product")
        self.assertEqual(infer_query_intent("Surface pricing"), "product")

    def test_prediction(self):
        self.assertEqual(infer_query_intent("predict the 2028 election"), "prediction")
        self.assertEqual(infer_query_intent("odds Trump wins"), "prediction")
        self.assertEqual(infer_query_intent("forecast Q4 earnings"), "prediction")

    def test_breaking_news_default(self):
        self.assertEqual(infer_query_intent("Kanye West"), "breaking_news")
        self.assertEqual(infer_query_intent("OpenAI"), "breaking_news")


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
