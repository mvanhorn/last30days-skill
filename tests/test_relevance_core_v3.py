import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import relevance


class RelevanceCoreV3Tests(unittest.TestCase):
    def test_tokenize_removes_stopwords_and_expands_synonyms(self):
        tokens = relevance.tokenize("How to use JS for hip hop apps")
        self.assertIn("js", tokens)
        self.assertIn("javascript", tokens)
        self.assertIn("hiphop", tokens)
        self.assertNotIn("how", tokens)

    def test_token_overlap_relevance_returns_neutral_for_stopword_only_query(self):
        self.assertEqual(0.5, relevance.token_overlap_relevance("how to", "anything at all"))

    def test_token_overlap_relevance_returns_zero_for_no_overlap(self):
        self.assertEqual(0.0, relevance.token_overlap_relevance("openclaw", "corsair gaming mouse"))

    def test_token_overlap_relevance_rewards_exact_phrase_matches(self):
        phrase_score = relevance.token_overlap_relevance(
            "openclaw nanoclaw",
            "A detailed openclaw nanoclaw comparison for agents.",
        )
        partial_score = relevance.token_overlap_relevance(
            "openclaw nanoclaw",
            "A detailed openclaw comparison for agents.",
        )
        self.assertGreater(phrase_score, partial_score)

    def test_token_overlap_relevance_caps_generic_only_matches(self):
        score = relevance.token_overlap_relevance(
            "anthropic odds",
            "Latest odds and prediction updates for markets",
        )
        self.assertLessEqual(score, 0.24)

    def test_token_overlap_relevance_splits_concatenated_hashtags(self):
        score = relevance.token_overlap_relevance(
            "claude code",
            "Agent workflow discussion",
            hashtags=["ClaudeCode", "BuildInPublic"],
        )
        self.assertGreater(score, 0.0)


if __name__ == "__main__":
    unittest.main()
