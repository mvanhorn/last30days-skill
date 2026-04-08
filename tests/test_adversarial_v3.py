"""Adversarial query tests for the v3 planner.

These target edge cases found during regression analysis: slash-separated
comparisons, 'difference between X and Y' phrasing, trailing context
leaking into entities, degenerate inputs, and false-positive resistance.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import planner


class TestSlashSeparatedComparison(unittest.TestCase):
    """'React/Vue/Svelte' should be detected as comparison intent
    and produce entity subqueries."""

    def test_slash_triggers_comparison_intent(self):
        self.assertEqual(planner._infer_intent("React/Vue/Svelte"), "comparison")

    def test_slash_extracts_entities(self):
        entities = planner._comparison_entities("React/Vue/Svelte")
        self.assertEqual(len(entities), 3)
        self.assertIn("React", entities)
        self.assertIn("Vue", entities)
        self.assertIn("Svelte", entities)

    def test_slash_forces_deterministic(self):
        self.assertTrue(planner._should_force_deterministic_plan("React/Vue"))

    def test_url_slash_does_not_trigger_comparison(self):
        self.assertNotEqual(planner._infer_intent("https://example.com/path"), "comparison")

    def test_slash_trailing_context_stripped(self):
        entities = planner._comparison_entities("React/Vue/Svelte for frontend in 2026")
        for entity in entities:
            self.assertNotIn("frontend", entity.lower(),
                             f"Trailing context leaked: '{entity}'")


class TestDifferenceBetweenPhrasing(unittest.TestCase):
    """'difference between X and Y' should extract both entities."""

    def test_intent_is_comparison(self):
        self.assertEqual(
            planner._infer_intent("difference between OpenClaw and NanoClaw"),
            "comparison",
        )

    def test_entities_extracted(self):
        entities = planner._comparison_entities("difference between OpenClaw and NanoClaw")
        self.assertEqual(len(entities), 2)
        self.assertIn("OpenClaw", entities)
        self.assertIn("NanoClaw", entities)

    def test_forces_deterministic(self):
        self.assertTrue(
            planner._should_force_deterministic_plan("difference between OpenClaw and NanoClaw")
        )


class TestAndFalsePositive(unittest.TestCase):
    """'and' must not split entities outside 'difference between' context."""

    def test_pros_and_cons_no_entities(self):
        entities = planner._comparison_entities("pros and cons of AI")
        self.assertEqual(entities, [])

    def test_react_and_vue_no_entities(self):
        # No "vs" or "difference between" -- just "and"
        entities = planner._comparison_entities("React and Vue")
        self.assertEqual(entities, [])


class TestTrailingContextStripping(unittest.TestCase):
    """Trailing preposition phrases must not leak into entity strings."""

    def test_for_stripped(self):
        entities = planner._comparison_entities("A vs B for production use")
        self.assertNotIn("production", entities[-1].lower())

    def test_in_stripped(self):
        entities = planner._comparison_entities("A vs B in 2026")
        self.assertNotIn("2026", entities[-1])

    def test_with_stripped(self):
        entities = planner._comparison_entities("A vs B with better security")
        self.assertNotIn("security", entities[-1].lower())

    def test_core_entity_preserved(self):
        entities = planner._comparison_entities("Fly.io vs Railway.app for deployment")
        self.assertTrue(any("Fly" in e for e in entities))
        self.assertTrue(any("Railway" in e for e in entities))


class TestDuplicateEntities(unittest.TestCase):

    def test_deduped(self):
        entities = planner._comparison_entities("OpenClaw vs OpenClaw")
        self.assertEqual(len(entities), 1)


class TestFiveWayComparison(unittest.TestCase):

    def test_capped_at_max(self):
        topic = "A vs B vs C vs D vs E vs F"
        entities = planner._comparison_entities(topic)
        self.assertLessEqual(len(entities), planner._max_subqueries("comparison"))

    def test_does_not_crash(self):
        plan = planner.plan_query(
            topic="A vs B vs C vs D vs E",
            available_sources=["reddit", "x", "grounding"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertLessEqual(len(plan.subqueries), 4)


class TestDegenerateInputs(unittest.TestCase):

    def test_single_word(self):
        plan = planner.plan_query(
            topic="Bitcoin",
            available_sources=["reddit"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertGreater(len(plan.subqueries), 0)

    def test_empty_vs_split(self):
        plan = planner.plan_query(
            topic="vs vs vs",
            available_sources=["reddit"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        for sq in plan.subqueries:
            self.assertTrue(sq.search_query.strip())

    def test_very_long_comparison(self):
        topic = " vs ".join(f"Tool{i}" for i in range(20))
        plan = planner.plan_query(
            topic=topic,
            available_sources=["reddit", "x"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertLessEqual(len(plan.subqueries), 4)


class TestMixedCaseAndPunctuation(unittest.TestCase):

    def test_uppercase_vs_period(self):
        self.assertEqual(
            planner._infer_intent("OpenClaw VS. NanoClaw VS. IronClaw"),
            "comparison",
        )

    def test_entities_preserved_with_mixed_case(self):
        entities = planner._comparison_entities("OpenClaw VS. NanoClaw VS. IronClaw")
        self.assertGreaterEqual(len(entities), 3)


class TestSubstringEntity(unittest.TestCase):

    def test_react_vs_react_native_not_collapsed(self):
        entities = planner._comparison_entities("React vs React Native")
        self.assertEqual(len(entities), 2)
        self.assertTrue(any("Native" in e for e in entities))


class TestNoiseWordEntities(unittest.TestCase):
    """Entities that are also common English words (Swift, Rust, Go)
    must survive entity extraction."""

    def test_swift_preserved(self):
        entities = planner._comparison_entities("Swift vs Rust vs Go")
        self.assertGreaterEqual(len(entities), 3)

    def test_go_not_stripped(self):
        entities = planner._comparison_entities("Swift vs Rust vs Go")
        self.assertTrue(any("Go" in e for e in entities))


if __name__ == "__main__":
    unittest.main()
