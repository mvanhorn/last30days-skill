"""Tests for reddit_enrich.py — comment enrichment and parsing."""

import json
import unittest
from pathlib import Path

# Add lib to path

from lib import reddit_enrich

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name):
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


class TestExtractRedditPath(unittest.TestCase):
    """Tests for extract_reddit_path()."""

    def test_valid_url(self):
        url = "https://www.reddit.com/r/ClaudeAI/comments/abc123/post_title/"
        path = reddit_enrich.extract_reddit_path(url)
        self.assertEqual(path, "/r/ClaudeAI/comments/abc123/post_title/")

    def test_non_reddit_url(self):
        self.assertIsNone(reddit_enrich.extract_reddit_path("https://example.com/foo"))

    def test_empty_string(self):
        self.assertIsNone(reddit_enrich.extract_reddit_path(""))

    def test_old_reddit(self):
        url = "https://old.reddit.com/r/test/comments/xyz/"
        self.assertIsNotNone(reddit_enrich.extract_reddit_path(url))


class TestParseThreadData(unittest.TestCase):
    """Tests for parse_thread_data() using fixture."""

    def test_parses_submission(self):
        data = _load_fixture("reddit_thread_sample.json")
        result = reddit_enrich.parse_thread_data(data)
        self.assertIsNotNone(result["submission"])
        self.assertEqual(result["submission"]["score"], 847)
        self.assertEqual(result["submission"]["num_comments"], 156)

    def test_parses_comments(self):
        data = _load_fixture("reddit_thread_sample.json")
        result = reddit_enrich.parse_thread_data(data)
        self.assertEqual(len(result["comments"]), 8)
        self.assertEqual(result["comments"][0]["author"], "skill_expert")

    def test_empty_input(self):
        result = reddit_enrich.parse_thread_data([])
        self.assertIsNone(result["submission"])
        self.assertEqual(result["comments"], [])

    def test_malformed_input(self):
        result = reddit_enrich.parse_thread_data("not a list")
        self.assertIsNone(result["submission"])

    def test_none_input(self):
        result = reddit_enrich.parse_thread_data(None)
        self.assertIsNone(result["submission"])


class TestGetTopComments(unittest.TestCase):
    """Tests for get_top_comments()."""

    def test_sorted_by_score(self):
        comments = [
            {"score": 10, "author": "a"},
            {"score": 100, "author": "b"},
            {"score": 50, "author": "c"},
        ]
        top = reddit_enrich.get_top_comments(comments, limit=3)
        self.assertEqual(top[0]["score"], 100)
        self.assertEqual(top[1]["score"], 50)

    def test_filters_deleted(self):
        comments = [
            {"score": 100, "author": "[deleted]"},
            {"score": 50, "author": "[removed]"},
            {"score": 10, "author": "real_user"},
        ]
        top = reddit_enrich.get_top_comments(comments)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["author"], "real_user")

    def test_respects_limit(self):
        comments = [{"score": i, "author": f"u{i}"} for i in range(20)]
        top = reddit_enrich.get_top_comments(comments, limit=5)
        self.assertEqual(len(top), 5)

    def test_empty_list(self):
        self.assertEqual(reddit_enrich.get_top_comments([]), [])


class TestExtractCommentInsights(unittest.TestCase):
    """Tests for extract_comment_insights()."""

    def test_filters_short_comments(self):
        comments = [
            {"body": "yes"},
            {"body": "A" * 50 + " this is a substantive comment about the topic."},
        ]
        insights = reddit_enrich.extract_comment_insights(comments)
        self.assertEqual(len(insights), 1)

    def test_filters_low_value_patterns(self):
        comments = [
            {"body": "This."},
            {"body": "lol that's hilarious"},
            {"body": "A" * 50 + " Here's a real insight about how to approach this problem."},
        ]
        insights = reddit_enrich.extract_comment_insights(comments)
        self.assertEqual(len(insights), 1)

    def test_respects_limit(self):
        comments = [{"body": f"Comment number {i} " + "x" * 50} for i in range(20)]
        insights = reddit_enrich.extract_comment_insights(comments, limit=3)
        self.assertLessEqual(len(insights), 3)

if __name__ == "__main__":
    unittest.main()
