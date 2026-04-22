import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import resolve
from lib.resolve import MAX_SUBS, _merge_category_peers


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
        self.assertEqual(result["searches_run"], 4)
        self.assertEqual(mock_search.call_count, 4)

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
        # 3 out of 4 succeeded (subreddit, x_handle, github; news failed)
        self.assertEqual(result["searches_run"], 3)


class MergeCategoryPeersHappyPath(unittest.TestCase):
    def test_image_gen_topic_appends_peers(self):
        merged, category = _merge_category_peers(
            "Prompting GPT Image 2",
            ["OpenAI", "ChatGPT", "singularity"],
        )
        self.assertEqual(category, "ai_image_generation")
        self.assertIn("OpenAI", merged)
        self.assertIn("ChatGPT", merged)
        self.assertIn("singularity", merged)
        self.assertIn("StableDiffusion", merged)
        self.assertIn("midjourney", merged)
        self.assertIn("dalle2", merged)

    def test_preserves_websearch_order_then_appends_peers(self):
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            ["OpenAI", "ChatGPT"],
        )
        self.assertEqual(merged[0], "OpenAI")
        self.assertEqual(merged[1], "ChatGPT")
        self.assertEqual(merged[2], "StableDiffusion")

    def test_emits_stderr_log_when_peers_added(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers(
                "Prompting GPT Image 2",
                ["OpenAI", "ChatGPT"],
            )
        output = buf.getvalue()
        self.assertIn("Matched category=ai_image_generation", output)
        self.assertIn("StableDiffusion", output)


class MergeCategoryPeersDedupe(unittest.TestCase):
    def test_peer_already_in_websearch_not_duplicated(self):
        merged, _ = _merge_category_peers(
            "midjourney v7 prompts",
            ["midjourney", "aiArt"],
        )
        self.assertEqual(
            sum(1 for s in merged if s.lower() == "midjourney"),
            1,
        )

    def test_dedupe_is_case_insensitive(self):
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            ["STABLEDIFFUSION"],
        )
        lower = [s.lower() for s in merged]
        self.assertEqual(lower.count("stablediffusion"), 1)

    def test_no_log_when_all_peers_already_present(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers(
                "Prompting GPT Image 2",
                [
                    "StableDiffusion",
                    "midjourney",
                    "dalle2",
                    "aiArt",
                    "PromptEngineering",
                    "MediaSynthesis",
                ],
            )
        self.assertNotIn("Matched category=", buf.getvalue())


class MergeCategoryPeersEdgeCases(unittest.TestCase):
    def test_topic_with_no_category_returns_unchanged(self):
        merged, category = _merge_category_peers(
            "Kanye West",
            ["Kanye", "hiphopheads"],
        )
        self.assertIsNone(category)
        self.assertEqual(merged, ["Kanye", "hiphopheads"])

    def test_empty_subreddit_list_with_category_still_adds_peers(self):
        merged, category = _merge_category_peers("Prompting GPT Image 2", [])
        self.assertEqual(category, "ai_image_generation")
        self.assertIn("StableDiffusion", merged)

    def test_empty_topic_returns_unchanged(self):
        merged, category = _merge_category_peers("", ["foo", "bar"])
        self.assertIsNone(category)
        self.assertEqual(merged, ["foo", "bar"])

    def test_none_topic_returns_unchanged(self):
        merged, category = _merge_category_peers(None, ["foo", "bar"])
        self.assertIsNone(category)
        self.assertEqual(merged, ["foo", "bar"])

    def test_no_log_when_topic_has_no_category(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers("Kanye West", ["Kanye"])
        self.assertNotIn("Matched category=", buf.getvalue())


class MergeCategoryPeersCap(unittest.TestCase):
    def test_cap_is_enforced_at_max_subs(self):
        websearch_subs = [f"Sub{i}" for i in range(9)]
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            websearch_subs,
        )
        self.assertEqual(len(merged), MAX_SUBS)
        for s in websearch_subs:
            self.assertIn(s, merged)
        self.assertEqual(len(merged) - len(websearch_subs), 1)
        self.assertEqual(merged[9], "StableDiffusion")

    def test_cap_preserves_highest_priority_peer_when_trimming(self):
        websearch_subs = [f"Sub{i}" for i in range(8)]
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            websearch_subs,
        )
        self.assertEqual(len(merged), MAX_SUBS)
        self.assertEqual(merged[8], "StableDiffusion")
        self.assertEqual(merged[9], "midjourney")


class MergeCategoryPeersClassificationFailure(unittest.TestCase):
    def test_classification_error_returns_unwidened_list_and_logs(self):
        original = resolve.categories.detect_category

        def boom(_topic):
            raise RuntimeError("synthetic classifier failure")

        resolve.categories.detect_category = boom
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                merged, category = _merge_category_peers(
                    "Prompting GPT Image 2",
                    ["OpenAI"],
                )
            self.assertEqual(merged, ["OpenAI"])
            self.assertIsNone(category)
            self.assertIn("Category classification failed", buf.getvalue())
        finally:
            resolve.categories.detect_category = original


class AutoResolveCategoryIntegration(unittest.TestCase):
    @patch("lib.resolve.grounding.web_search")
    def test_auto_resolve_returns_category_key(self, mock_search):
        def side_effect(query, date_range, config):
            if "subreddit" in query:
                return [
                    {"title": "r/OpenAI", "snippet": "r/ChatGPT r/singularity", "url": ""},
                ], {}
            return [], {}

        mock_search.side_effect = side_effect
        result = resolve.auto_resolve(
            "Prompting GPT Image 2",
            {"BRAVE_API_KEY": "fake"},
        )
        self.assertEqual(result["category"], "ai_image_generation")
        self.assertIn("StableDiffusion", result["subreddits"])
        self.assertIn("OpenAI", result["subreddits"])

    def test_no_backend_returns_category_none(self):
        result = resolve.auto_resolve("test topic", {})
        self.assertIsNone(result["category"])


if __name__ == "__main__":
    unittest.main()
