import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import entity_extract


class TestExtractSubreddits(unittest.TestCase):
    def test_extracts_primary_subreddit(self):
        items = [{"subreddit": "r/MachineLearning"}]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("MachineLearning", result)

    def test_extracts_subreddit_without_prefix(self):
        items = [{"subreddit": "localLLaMA"}]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("localLLaMA", result)

    def test_extracts_cross_references_from_comment_insights(self):
        items = [
            {
                "subreddit": "technology",
                "comment_insights": ["check out r/MachineLearning and r/LocalLLaMA for more"],
            }
        ]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("MachineLearning", result)
        self.assertIn("LocalLLaMA", result)

    def test_extracts_cross_references_from_top_comments(self):
        items = [
            {
                "subreddit": "AI",
                "top_comments": [
                    {"excerpt": "see r/StableDiffusion for image gen stuff"},
                ],
            }
        ]
        result = entity_extract._extract_subreddits(items)
        self.assertIn("StableDiffusion", result)

    def test_ranks_by_frequency(self):
        items = [
            {"subreddit": "A"},
            {"subreddit": "A"},
            {"subreddit": "B"},
        ]
        result = entity_extract._extract_subreddits(items)
        self.assertEqual(result[0], "A")

    def test_empty_items(self):
        self.assertEqual([], entity_extract._extract_subreddits([]))


if __name__ == "__main__":
    unittest.main()
