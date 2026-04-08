import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import resolve


class TestHasBackend(unittest.TestCase):
    def test_no_keys_returns_false(self):
        self.assertFalse(resolve._has_backend({}))

    def test_brave_key_returns_true(self):
        self.assertTrue(resolve._has_backend({"BRAVE_API_KEY": "key"}))

    def test_exa_key_returns_true(self):
        self.assertTrue(resolve._has_backend({"EXA_API_KEY": "key"}))

    def test_serper_key_returns_true(self):
        self.assertTrue(resolve._has_backend({"SERPER_API_KEY": "key"}))


class TestExtractSubreddits(unittest.TestCase):
    def test_extracts_from_title_and_snippet(self):
        items = [
            {"title": "Check out r/MachineLearning", "snippet": "Also r/artificial", "url": ""},
            {"title": "More at r/datascience", "snippet": "", "url": ""},
        ]
        result = resolve._extract_subreddits(items)
        self.assertEqual(result, ["MachineLearning", "artificial", "datascience"])

    def test_extracts_from_url(self):
        items = [
            {"title": "Discussion", "snippet": "", "url": "https://reddit.com/r/python/comments/123"},
        ]
        result = resolve._extract_subreddits(items)
        self.assertEqual(result, ["python"])

    def test_deduplicates_case_insensitive(self):
        items = [
            {"title": "r/Python", "snippet": "r/python is great", "url": ""},
        ]
        result = resolve._extract_subreddits(items)
        self.assertEqual(len(result), 1)

    def test_empty_items_returns_empty(self):
        self.assertEqual(resolve._extract_subreddits([]), [])

    def test_no_subreddits_in_text(self):
        items = [{"title": "No subreddits here", "snippet": "Just text", "url": ""}]
        self.assertEqual(resolve._extract_subreddits(items), [])


class TestExtractXHandle(unittest.TestCase):
    def test_extracts_from_url(self):
        items = [
            {"title": "OpenAI on X", "snippet": "Updates from @OpenAI", "url": "https://x.com/OpenAI"},
        ]
        result = resolve._extract_x_handle(items)
        self.assertEqual(result, "openai")

    def test_extracts_from_text(self):
        items = [
            {"title": "Follow @elonmusk", "snippet": "Also @elonmusk tweeted", "url": ""},
        ]
        result = resolve._extract_x_handle(items)
        self.assertEqual(result, "elonmusk")

    def test_filters_generic_handles(self):
        items = [
            {"title": "Go to @twitter", "snippet": "Visit @x", "url": ""},
        ]
        result = resolve._extract_x_handle(items)
        self.assertEqual(result, "")

    def test_empty_items_returns_empty(self):
        self.assertEqual(resolve._extract_x_handle([]), "")


class TestBuildContextSummary(unittest.TestCase):
    def test_builds_from_snippets(self):
        items = [
            {"snippet": "First news item about topic."},
            {"snippet": "Second news item with details."},
            {"snippet": "Third item ignored."},
        ]
        result = resolve._build_context_summary(items)
        self.assertIn("First news item", result)
        self.assertIn("Second news item", result)
        # Only first 2 snippets used
        self.assertNotIn("Third item", result)

    def test_truncates_long_text(self):
        items = [{"snippet": "A" * 200}, {"snippet": "B" * 200}]
        result = resolve._build_context_summary(items)
        self.assertLessEqual(len(result), 300)
        self.assertTrue(result.endswith("..."))

    def test_empty_items_returns_empty(self):
        self.assertEqual(resolve._build_context_summary([]), "")

    def test_items_with_empty_snippets(self):
        items = [{"snippet": ""}, {"snippet": ""}]
        self.assertEqual(resolve._build_context_summary(items), "")


class TestAutoResolve(unittest.TestCase):
    def test_no_backend_returns_empty(self):
        result = resolve.auto_resolve("test topic", {})
        self.assertEqual(result["subreddits"], [])
        self.assertEqual(result["x_handle"], "")
        self.assertEqual(result["context"], "")
        self.assertEqual(result["searches_run"], 0)

    @patch("lib.resolve.grounding.web_search")
    def test_full_resolve(self, mock_search):
        def side_effect(query, date_range, config):
            if "subreddit" in query:
                return [
                    {"title": "r/technology discussion", "snippet": "Also r/gadgets", "url": ""},
                ], {"label": "brave"}
            if "news" in query:
                return [
                    {"snippet": "Major tech breakthrough announced this week."},
                ], {"label": "brave"}
            if "handle" in query:
                return [
                    {"title": "TechCo on X", "snippet": "@TechCo", "url": "https://x.com/TechCo"},
                ], {"label": "brave"}
            return [], {}

        mock_search.side_effect = side_effect
        result = resolve.auto_resolve("tech", {"BRAVE_API_KEY": "fake"})

        self.assertEqual(result["subreddits"], ["technology", "gadgets"])
        self.assertEqual(result["x_handle"], "techco")
        self.assertIn("breakthrough", result["context"])
        self.assertEqual(result["searches_run"], 3)
        self.assertEqual(mock_search.call_count, 3)

    @patch("lib.resolve.grounding.web_search")
    def test_search_failure_graceful(self, mock_search):
        mock_search.side_effect = RuntimeError("API error")
        result = resolve.auto_resolve("test", {"BRAVE_API_KEY": "fake"})
        self.assertEqual(result["subreddits"], [])
        self.assertEqual(result["x_handle"], "")
        self.assertEqual(result["context"], "")
        self.assertEqual(result["searches_run"], 0)

    @patch("lib.resolve.grounding.web_search")
    def test_partial_failure(self, mock_search):
        call_count = 0

        def side_effect(query, date_range, config):
            nonlocal call_count
            call_count += 1
            if "subreddit" in query:
                return [{"title": "r/cooking tips", "snippet": "", "url": ""}], {}
            if "news" in query:
                raise RuntimeError("Timeout")
            return [], {}

        mock_search.side_effect = side_effect
        result = resolve.auto_resolve("cooking", {"EXA_API_KEY": "fake"})
        self.assertEqual(result["subreddits"], ["cooking"])
        # News search failed, so context is empty
        self.assertEqual(result["context"], "")
        # 2 out of 3 succeeded
        self.assertEqual(result["searches_run"], 2)


if __name__ == "__main__":
    unittest.main()
