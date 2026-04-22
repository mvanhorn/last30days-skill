"""Unit tests for scripts/lib/categories.py — the Step 0.55 category-peer map.

Guards the 2026-04-22 `Prompting GPT Image 2` failure mode: the original bug
was that Step 0.55 resolved only brand-adjacent subs (r/OpenAI, r/ChatGPT)
and missed the category peers (r/StableDiffusion, r/midjourney, r/dalle2)
where prompting techniques actually live.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import categories
from lib.categories import CATEGORY_PEERS, detect_category, peer_subs_for


class DetectCategoryHappyPath(unittest.TestCase):
    def test_prompting_gpt_image_2_matches_image_generation(self):
        self.assertEqual(
            detect_category("Prompting GPT Image 2"),
            "ai_image_generation",
        )

    def test_claude_code_matches_coding_agent(self):
        self.assertEqual(
            detect_category("Claude Code skills"),
            "ai_coding_agent",
        )

    def test_suno_matches_music_generation(self):
        self.assertEqual(detect_category("Suno v4 review"), "ai_music_generation")

    def test_polymarket_matches_prediction_markets(self):
        self.assertEqual(
            detect_category("Polymarket election odds"),
            "prediction_markets",
        )

    def test_sora_matches_video_generation(self):
        self.assertEqual(detect_category("Sora 2 prompts"), "ai_video_generation")


class PeerSubsForHappyPath(unittest.TestCase):
    def test_image_generation_peer_subs_priority_order(self):
        subs = peer_subs_for("ai_image_generation")
        self.assertIn("StableDiffusion", subs)
        self.assertIn("midjourney", subs)
        self.assertIn("dalle2", subs)
        self.assertLess(subs.index("StableDiffusion"), subs.index("midjourney"))
        self.assertLess(subs.index("midjourney"), subs.index("dalle2"))

    def test_unknown_category_returns_empty_list(self):
        self.assertEqual(peer_subs_for("unknown_category"), [])

    def test_none_category_returns_empty_list(self):
        self.assertEqual(peer_subs_for(None), [])

    def test_returned_list_is_fresh_copy(self):
        first = peer_subs_for("ai_image_generation")
        first.append("MutatedSub")
        second = peer_subs_for("ai_image_generation")
        self.assertNotIn("MutatedSub", second)


class DetectCategoryEdgeCases(unittest.TestCase):
    def test_case_insensitive_match(self):
        self.assertEqual(
            detect_category("STABLE DIFFUSION walkthrough"),
            "ai_image_generation",
        )

    def test_non_category_topic_returns_none(self):
        self.assertIsNone(detect_category("Kanye West"))

    def test_bare_image_word_does_not_trigger_image_generation(self):
        # Compound-term guard: "image" alone is not a pattern; only
        # multi-word compounds or domain-specific brand names match.
        self.assertIsNone(detect_category("image editing on my phone"))

    def test_bare_ai_word_does_not_trigger_any_category(self):
        self.assertIsNone(detect_category("ai news today"))

    def test_empty_topic_returns_none(self):
        self.assertIsNone(detect_category(""))

    def test_none_topic_returns_none(self):
        self.assertIsNone(detect_category(None))

    def test_first_match_wins_image_gen_before_chat_model(self):
        # "gpt image 2" contains "gpt image" (ai_image_generation) and the
        # substring "gpt" could resemble gpt-N chat-model patterns. The
        # narrower category wins because it is declared earlier.
        self.assertEqual(
            detect_category("gpt image 2 review"),
            "ai_image_generation",
        )


class CategoryMapInvariants(unittest.TestCase):
    """Regression guards on the map itself — catch accidental bare-word patterns."""

    # Common nouns that would produce false positives if used as bare patterns.
    FORBIDDEN_BARE_PATTERNS = frozenset({
        "image", "video", "music", "ai", "model", "agent", "chat",
        "code", "cli", "app", "tool", "defi",
    })

    def test_no_category_has_a_bare_common_noun_pattern(self):
        offenders = []
        for category_id, entry in CATEGORY_PEERS.items():
            for pattern in entry["patterns"]:
                if pattern.strip() in self.FORBIDDEN_BARE_PATTERNS:
                    offenders.append((category_id, pattern))
        self.assertEqual(
            offenders,
            [],
            msg=(
                "Bare common-noun patterns cause false positives. "
                f"Offenders: {offenders}. Patterns must be compound "
                "(e.g. 'image generation') or domain-specific "
                "(e.g. 'midjourney')."
            ),
        )

    def test_every_category_has_at_least_one_compound_or_brand_pattern(self):
        multi_word_or_brand = re.compile(r"(\s|-|\.)|^[a-z][a-z0-9]{3,}$")
        for category_id, entry in CATEGORY_PEERS.items():
            patterns = entry["patterns"]
            self.assertTrue(patterns, f"{category_id} has no patterns")
            has_strong = any(multi_word_or_brand.search(p) for p in patterns)
            self.assertTrue(
                has_strong,
                f"{category_id} needs at least one multi-word or brand pattern",
            )

    def test_every_category_has_at_least_two_peer_subs(self):
        for category_id, entry in CATEGORY_PEERS.items():
            self.assertGreaterEqual(
                len(entry["peer_subs"]),
                2,
                f"{category_id} should list at least 2 peer subs",
            )

    def test_category_count_is_in_expected_range(self):
        # Sanity check: the map is intentionally small and curated.
        self.assertGreaterEqual(len(CATEGORY_PEERS), 8)
        self.assertLessEqual(len(CATEGORY_PEERS), 20)


if __name__ == "__main__":
    unittest.main()
