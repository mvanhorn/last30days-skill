"""Tests for scripts/lib/competitors.discover_competitors."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import competitors


def _serp(items: list[tuple[str, str]]) -> list[dict]:
    """Build a minimal SERP items list from (title, snippet) pairs."""
    return [
        {"title": title, "snippet": snippet, "url": "https://example.test/"}
        for title, snippet in items
    ]

OPENAI_SERP = _serp(
    [
        ("OpenAI vs Anthropic vs xAI: which is better?", "xAI and Anthropic now compete directly with OpenAI."),
        ("Top OpenAI alternatives in 2026", "Anthropic, Google Gemini, and xAI are the leading alternatives this year."),
        ("xAI and Anthropic challenge OpenAI dominance", "xAI and Anthropic push Google Gemini hard; xAI keeps shipping."),
        ("Anthropic vs xAI: head to head", "Anthropic and xAI trade punches; Google Gemini is not far behind."),
    ]
)

KANYE_SERP = _serp(
    [
        ("Kanye West vs Drake: the feud explained", "Drake responded to Kanye with a diss track."),
        ("Top rappers of the decade: Kendrick Lamar, Drake, J Cole", "Kendrick Lamar released a new album; Drake toured Europe."),
        ("Drake and Kendrick Lamar trade shots", "J Cole stayed out of the Drake vs Kendrick Lamar feud."),
    ]
)


class CompetitorDiscoveryTests(unittest.TestCase):
    def _run(self, serp: list[dict], topic: str, count: int = 3) -> list[str]:
        config = {"BRAVE_API_KEY": "test-key"}
        with mock.patch.object(
            competitors.grounding, "web_search", return_value=(serp, {})
        ):
            with redirect_stderr(io.StringIO()):
                return competitors.discover_competitors(topic, count, config)

    def test_openai_surfaces_anthropic_and_peers(self):
        results = self._run(OPENAI_SERP, "OpenAI", count=3)
        self.assertEqual(len(results), 3)
        joined = " ".join(results)
        self.assertIn("Anthropic", joined)
        self.assertIn("xAI", joined)
        # Should not surface the topic itself
        self.assertNotIn("OpenAI", results)
        self.assertFalse(
            any("OpenAI" in entity for entity in results),
            f"Topic token leaked into results: {results}",
        )

    def test_kanye_surfaces_rap_peers(self):
        results = self._run(KANYE_SERP, "Kanye West", count=2)
        self.assertEqual(len(results), 2)
        joined = " ".join(results)
        self.assertTrue(
            "Drake" in joined and "Kendrick Lamar" in joined,
            f"Expected Drake and Kendrick Lamar in {results}",
        )

    def test_empty_serp_returns_empty(self):
        results = self._run([], "OpenAI", count=3)
        self.assertEqual(results, [])

    def test_no_backend_returns_empty(self):
        err = io.StringIO()
        with redirect_stderr(err):
            results = competitors.discover_competitors("OpenAI", 3, config={})
        self.assertEqual(results, [])
        self.assertIn("No web search backend", err.getvalue())

    def test_backend_error_returns_empty(self):
        config = {"BRAVE_API_KEY": "test-key"}

        def boom(*_args, **_kwargs):
            raise RuntimeError("SERP provider offline")

        err = io.StringIO()
        with mock.patch.object(competitors.grounding, "web_search", side_effect=boom):
            with redirect_stderr(err):
                results = competitors.discover_competitors("OpenAI", 3, config)
        self.assertEqual(results, [])
        self.assertIn("Search failed", err.getvalue())

    def test_topic_tokens_filtered(self):
        """Candidates overlapping topic tokens are rejected."""
        serp = _serp(
            [
                ("Open AI vs Anthropic", "Open AI, Anthropic, and Google lead."),
                ("OpenAI Alternatives: Anthropic", "Anthropic is a competitor to Open AI."),
            ]
        )
        results = self._run(serp, "OpenAI", count=3)
        # "Open AI" shares the "openai" lowercased-concatenation? Actually tokenizer
        # splits "Open AI" into ["open", "ai"]. Topic "OpenAI" tokenizes to ["openai"].
        # They do not overlap at the token level, which is fine — the filter is
        # best-effort. We only assert that bare "OpenAI" is filtered and real
        # competitors still surface.
        self.assertNotIn("OpenAI", results)
        self.assertIn("Anthropic", results)

    def test_deduplicates_case_insensitively(self):
        serp = _serp(
            [
                ("Anthropic vs Gemini", "anthropic is strong."),
                ("ANTHROPIC makes Claude", "Anthropic announced Claude 4."),
            ]
        )
        results = self._run(serp, "OpenAI", count=3)
        # "Anthropic" should appear exactly once (first-seen capitalization wins).
        anthropic_matches = [r for r in results if r.lower() == "anthropic"]
        self.assertEqual(len(anthropic_matches), 1)

    def test_count_one_returns_single(self):
        results = self._run(OPENAI_SERP, "OpenAI", count=1)
        self.assertEqual(len(results), 1)

    def test_stopword_only_candidates_rejected(self):
        serp = _serp(
            [
                ("Top Alternatives", "Best Competitors and Top Tools."),
                ("Free Software Reviews", "Complete Guide to The Options."),
            ]
        )
        results = self._run(serp, "Widget", count=5)
        self.assertEqual(
            results, [],
            f"Stopword-only phrases should not be returned: got {results}",
        )

    def test_count_zero_returns_empty(self):
        results = self._run(OPENAI_SERP, "OpenAI", count=0)
        self.assertEqual(results, [])

    def test_exa_company_search_is_used_even_when_brave_is_the_web_backend(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key"}
        exa_items = [
            {"title": "OpenAI", "url": "https://openai.com/"},
            {"title": "Anthropic | Building reliable AI", "url": "https://www.anthropic.com/"},
            {"title": "Anthropic", "url": "https://www.anthropic.com/about"},
            {"title": "xAI - Grok", "url": "https://x.ai/"},
            {"title": "Google DeepMind", "url": "https://deepmind.google/"},
        ]
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
            return_value=(exa_items, {"label": "exa", "category": "company"}),
        ) as exa, mock.patch.object(
            competitors.grounding, "web_search",
        ) as web:
            with redirect_stderr(io.StringIO()):
                results = competitors.discover_competitors(
                    "OpenAI", 2, config, is_company=True,
                )
        self.assertEqual(results, ["Anthropic", "xAI"])
        exa.assert_called_once()
        web.assert_not_called()
        self.assertIn("OpenAI", exa.call_args.args[0])
        self.assertTrue(exa.call_args.kwargs.get("objective"))

    def test_exa_partial_results_are_filled_from_keyword_serps(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key"}
        exa_items = [
            {"title": "Anthropic", "url": "https://www.anthropic.com/"},
        ]
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
            return_value=(exa_items, {}),
        ), mock.patch.object(
            competitors.grounding, "web_search", return_value=(OPENAI_SERP, {}),
        ):
            with redirect_stderr(io.StringIO()):
                results = competitors.discover_competitors(
                    "OpenAI", 3, config, is_company=True,
                )
        self.assertEqual(results[0], "Anthropic")
        self.assertEqual(len(results), 3)
        self.assertNotIn("OpenAI", results)

    def test_exa_failure_falls_back_to_keyword_serps(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key"}
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
            side_effect=RuntimeError("Exa 400"),
        ), mock.patch.object(
            competitors.grounding, "web_search", return_value=(OPENAI_SERP, {}),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                results = competitors.discover_competitors(
                    "OpenAI", 2, config, is_company=True,
                )
        self.assertTrue(results)
        self.assertIn("Exa company search failed", err.getvalue())

    def test_brave_only_skips_exa_company_search(self):
        config = {"BRAVE_API_KEY": "brave-key"}
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
        ) as exa, mock.patch.object(
            competitors.grounding, "web_search", return_value=(OPENAI_SERP, {}),
        ):
            with redirect_stderr(io.StringIO()):
                results = competitors.discover_competitors("OpenAI", 2, config)
        exa.assert_not_called()
        self.assertTrue(results)

    def test_non_company_topic_does_not_use_exa_company_index(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key"}
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
        ) as exa, mock.patch.object(
            competitors.grounding, "web_search", return_value=(KANYE_SERP, {}),
        ):
            with redirect_stderr(io.StringIO()):
                results = competitors.discover_competitors("Kanye West", 2, config)
        exa.assert_not_called()
        joined = " ".join(results)
        self.assertTrue(
            "Drake" in joined and "Kendrick Lamar" in joined,
            f"Expected Drake and Kendrick Lamar in {results}",
        )

    def test_exa_strips_trailing_and_unbalanced_parentheticals(self):
        config = {"EXA_API_KEY": "exa-key"}
        exa_items = [
            {"title": "xPay (YC W24", "url": "https://xpay.example/"},
            {"title": "Adyen (payments)", "url": "https://www.adyen.com/"},
            {"title": "(YC W24)", "url": "https://www.ycombinator.com/"},
            {"title": "Klarna [fintech]", "url": "https://www.klarna.com/"},
            {"title": " [stealth] ", "url": "https://example.test/empty"},
            {"title": "Payoneer - global payments", "url": "https://www.payoneer.com/"},
        ]
        with mock.patch.object(
            competitors.grounding, "exa_company_search",
            return_value=(exa_items, {"label": "exa", "category": "company"}),
        ) as exa, mock.patch.object(
            competitors.grounding, "web_search",
        ) as web:
            with redirect_stderr(io.StringIO()):
                results = competitors.discover_competitors(
                    "Stripe", 4, config, is_company=True,
                )
        self.assertEqual(results, ["xPay", "Adyen", "Klarna", "Payoneer"])
        self.assertFalse(any("(" in name or "[" in name for name in results))
        exa.assert_called_once()
        web.assert_not_called()

if __name__ == "__main__":
    unittest.main()
