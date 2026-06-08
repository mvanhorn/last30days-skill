"""Tests for entity_extract module."""

import unittest

# Add lib to path

from lib import entity_extract


class TestExtractXHandles(unittest.TestCase):
    def test_basic_author_handle(self):
        items = [{"author_handle": "techguru", "text": ""}]
        result = entity_extract._extract_x_handles(items)
        self.assertEqual(result, ["techguru"])

    def test_mentions_in_text(self):
        items = [{"text": "Great thread by @airesearcher and @mldev"}]
        result = entity_extract._extract_x_handles(items)
        self.assertIn("airesearcher", result)
        self.assertIn("mldev", result)

    def test_generic_handles_filtered(self):
        items = [
            {"author_handle": "@openai", "text": ""},
            {"author_handle": "@elonmusk", "text": ""},
            {"author_handle": "realexpert", "text": ""},
        ]
        result = entity_extract._extract_x_handles(items)
        self.assertEqual(result, ["realexpert"])

    def test_case_normalization(self):
        items = [{"author_handle": "@CamelCase", "text": ""}]
        result = entity_extract._extract_x_handles(items)
        self.assertEqual(result, ["camelcase"])

    def test_frequency_ranking(self):
        items = [
            {"author_handle": "popular", "text": ""},
            {"author_handle": "popular", "text": ""},
            {"author_handle": "popular", "text": ""},
            {"author_handle": "rare", "text": ""},
        ]
        result = entity_extract._extract_x_handles(items)
        self.assertEqual(result[0], "popular")

    def test_leading_at_stripped(self):
        items = [{"author_handle": "@withatsign", "text": ""}]
        result = entity_extract._extract_x_handles(items)
        self.assertEqual(result, ["withatsign"])

    def test_empty_input(self):
        result = entity_extract._extract_x_handles([])
        self.assertEqual(result, [])

    def test_mixed_items(self):
        items = [
            {"author_handle": "poster1", "text": "Check @mentioned"},
            {"text": "No author here"},
            {"author_handle": "", "text": ""},
        ]
        result = entity_extract._extract_x_handles(items)
        self.assertIn("poster1", result)
        self.assertIn("mentioned", result)
        self.assertEqual(len(result), 2)


class TestExtractXHashtags(unittest.TestCase):
    def test_basic_hashtag(self):
        items = [{"text": "Exciting news #AI"}]
        result = entity_extract._extract_x_hashtags(items)
        self.assertEqual(result, ["#ai"])

    def test_multiple_tags(self):
        items = [{"text": "#Python and #MachineLearning are trending"}]
        result = entity_extract._extract_x_hashtags(items)
        self.assertIn("#python", result)
        self.assertIn("#machinelearning", result)

    def test_frequency_ranking(self):
        items = [
            {"text": "#ai is great"},
            {"text": "#ai again"},
            {"text": "#rare tag"},
        ]
        result = entity_extract._extract_x_hashtags(items)
        self.assertEqual(result[0], "#ai")

    def test_single_char_tag_filtered(self):
        items = [{"text": "#X is not enough chars but #AI is"}]
        result = entity_extract._extract_x_hashtags(items)
        # #X is only 1 char, filtered by \w{2,30} regex
        self.assertNotIn("#x", result)
        self.assertIn("#ai", result)

    def test_empty_input(self):
        result = entity_extract._extract_x_hashtags([])
        self.assertEqual(result, [])


class TestExtractSubreddits(unittest.TestCase):
    def test_basic_subreddit_field(self):
        items = [{"subreddit": "MachineLearning"}]
        result = entity_extract._extract_subreddits(items)
        self.assertEqual(result, ["MachineLearning"])

    def test_cross_ref_in_comment_insights(self):
        items = [{"subreddit": "AI", "comment_insights": ["Check out r/localLLaMA for more"]}]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("localLLaMA", result)

    def test_cross_ref_in_top_comments(self):
        items = [{"subreddit": "tech", "top_comments": [{"excerpt": "Also see r/programming"}]}]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("programming", result)

    def test_frequency_ranking(self):
        items = [
            {"subreddit": "popular"},
            {"subreddit": "popular"},
            {"subreddit": "rare"},
        ]
        result = entity_extract._extract_subreddits(items)
        self.assertEqual(result[0], "popular")

    def test_leading_r_slash_stripped(self):
        items = [{"subreddit": "r/stripped"}]
        result = entity_extract._extract_subreddits(items)
        self.assertEqual(result, ["stripped"])

    def test_empty_input(self):
        result = entity_extract._extract_subreddits([])
        self.assertEqual(result, [])


class TestExtractEntities(unittest.TestCase):
    def test_integration(self):
        reddit = [{"subreddit": "AI", "comment_insights": ["r/localLLaMA"]}]
        x = [{"author_handle": "researcher", "text": "#deeplearning @colleague"}]
        result = entity_extract.extract_entities(reddit, x)
        self.assertIn("researcher", result["x_handles"])
        self.assertIn("#deeplearning", result["x_hashtags"])
        self.assertIn("AI", result["reddit_subreddits"])

    def test_max_limits(self):
        x = [
            {"author_handle": f"user{i}", "text": ""}
            for i in range(10)
        ]
        result = entity_extract.extract_entities([], x, max_handles=2)
        self.assertLessEqual(len(result["x_handles"]), 2)

    def test_empty_inputs(self):
        result = entity_extract.extract_entities([], [])
        self.assertEqual(result["x_handles"], [])
        self.assertEqual(result["x_hashtags"], [])
        self.assertEqual(result["reddit_subreddits"], [])

    def test_return_keys(self):
        result = entity_extract.extract_entities([], [])
        self.assertSetEqual(set(result.keys()), {"x_handles", "x_hashtags", "reddit_subreddits"})

if __name__ == "__main__":
    unittest.main()
