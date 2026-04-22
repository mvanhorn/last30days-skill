"""End-to-end regression test for the 2026-04-22 `Prompting GPT Image 2` bug.

Guards the failing run's Resolved-block shape end-to-end: stubs
`grounding.web_search` to return the OpenAI-only subs that caused the
original failure, then asserts that `auto_resolve` now returns the widened
list and emits the expected stderr trace.

If this test starts failing after a `scripts/lib/categories.py` edit, either
the fix regressed or the map intentionally dropped the `ai_image_generation`
category — update the test deliberately.

Fixture reference: `tests/fixtures/prompting-gpt-image-2-resolved-block.md`.
"""

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import resolve


OPENAI_BRAND_SUBREDDIT_RESULTS = [
    {
        "title": "r/OpenAI community hub",
        "snippet": "Discussion at r/ChatGPT and r/singularity about GPT Image 2.",
        "url": "https://reddit.com/r/OpenAI/",
    },
    {
        "title": "r/ChatGPTpromptengineering prompt collection",
        "snippet": "Also see r/artificial for broader AI chatter.",
        "url": "",
    },
]

EMPTY_RESULTS: list[dict] = []


def _fake_websearch(label_to_items: dict[str, list[dict]]):
    def _search(query, date_range, config):
        if "subreddit" in query:
            return label_to_items.get("subreddit", EMPTY_RESULTS), {}
        if "news" in query:
            return label_to_items.get("news", EMPTY_RESULTS), {}
        if "handle" in query:
            return label_to_items.get("x_handle", EMPTY_RESULTS), {}
        if "github" in query:
            return label_to_items.get("github", EMPTY_RESULTS), {}
        return EMPTY_RESULTS, {}

    return _search


class PromptingGptImage2RegressionGuard(unittest.TestCase):
    """The named 2026-04-22 failure mode. Resolved block must include peers."""

    @patch("lib.resolve.grounding.web_search")
    def test_auto_resolve_widens_to_image_gen_peers(self, mock_search):
        mock_search.side_effect = _fake_websearch({
            "subreddit": OPENAI_BRAND_SUBREDDIT_RESULTS,
        })

        result = resolve.auto_resolve(
            "Prompting GPT Image 2",
            {"BRAVE_API_KEY": "fake"},
        )

        subs_lower = [s.lower() for s in result["subreddits"]]

        # Original WebSearch-returned brand subs preserved
        self.assertIn("openai", subs_lower)
        self.assertIn("chatgpt", subs_lower)
        self.assertIn("singularity", subs_lower)

        # At least three of the image-gen peers were added
        expected_peers = {"stablediffusion", "midjourney", "dalle2", "aiart", "promptengineering"}
        found_peers = expected_peers.intersection(subs_lower)
        self.assertGreaterEqual(
            len(found_peers),
            3,
            f"Expected at least 3 image-gen peer subs, found: {found_peers}. "
            f"Actual subs: {result['subreddits']}",
        )

        self.assertEqual(result["category"], "ai_image_generation")

    @patch("lib.resolve.grounding.web_search")
    def test_stderr_contains_category_match_log_line(self, mock_search):
        mock_search.side_effect = _fake_websearch({
            "subreddit": OPENAI_BRAND_SUBREDDIT_RESULTS,
        })

        buf = io.StringIO()
        with redirect_stderr(buf):
            resolve.auto_resolve(
                "Prompting GPT Image 2",
                {"BRAVE_API_KEY": "fake"},
            )

        self.assertIn("Matched category=ai_image_generation", buf.getvalue())

    @patch("lib.resolve.grounding.web_search")
    def test_cap_enforced_end_to_end(self, mock_search):
        # Synthesize a subreddit response with 9 brand subs
        many_subs_items = [
            {"title": f"r/Brand{i}", "snippet": "", "url": ""}
            for i in range(9)
        ]
        mock_search.side_effect = _fake_websearch({
            "subreddit": many_subs_items,
        })

        result = resolve.auto_resolve(
            "Prompting GPT Image 2",
            {"BRAVE_API_KEY": "fake"},
        )

        self.assertLessEqual(len(result["subreddits"]), resolve.MAX_SUBS)
        # The first WebSearch sub is still present (brand subs never evicted)
        self.assertIn("Brand0", result["subreddits"])

    @patch("lib.resolve.grounding.web_search")
    def test_uncategorized_topic_does_not_inject_peers(self, mock_search):
        mock_search.side_effect = _fake_websearch({
            "subreddit": [{"title": "r/Kanye is wild", "snippet": "", "url": ""}],
        })

        buf = io.StringIO()
        with redirect_stderr(buf):
            result = resolve.auto_resolve(
                "Kanye West latest album",
                {"BRAVE_API_KEY": "fake"},
            )

        self.assertEqual(result["subreddits"], ["Kanye"])
        self.assertIsNone(result["category"])
        self.assertNotIn("Matched category=", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
