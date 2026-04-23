# ruff: noqa: E402
"""Tests for vs-mode routing into the competitor fanout.

A topic containing " vs " / " versus " triggers N-pass fanout (not the
old single-pipeline comparison plan). Each entity gets its own full
pipeline.run() with its own Step 0.55 targeting.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib import planner


class VsModeEntityDetectionTests(unittest.TestCase):
    """The planner's _comparison_entities helper is the detector we use."""

    def test_two_entity_vs(self):
        self.assertEqual(
            planner._comparison_entities("OpenAI vs Anthropic"),
            ["OpenAI", "Anthropic"],
        )

    def test_three_entity_vs(self):
        self.assertEqual(
            planner._comparison_entities("Kanye West vs Drake vs Kendrick Lamar"),
            ["Kanye West", "Drake", "Kendrick Lamar"],
        )

    def test_versus_alt_spelling(self):
        result = planner._comparison_entities("A versus B")
        self.assertEqual(result, ["A", "B"])

    def test_dotted_vs(self):
        result = planner._comparison_entities("A vs. B")
        self.assertEqual(result, ["A", "B"])

    def test_no_vs_returns_empty(self):
        self.assertEqual(planner._comparison_entities("OpenAI"), [])

    def test_trailing_vs_returns_empty_or_single(self):
        # "OpenAI vs" with nothing after — should not trigger vs-mode
        result = planner._comparison_entities("OpenAI vs")
        # _comparison_entities caps at _max_subqueries("comparison") and
        # requires >=2 parts. Single "OpenAI" with empty after vs -> []
        self.assertLess(len(result), 2)

    def test_dedup_identical_entities(self):
        # Defense against silly input — two "Drake"s should collapse.
        result = planner._comparison_entities("Drake vs Drake")
        self.assertEqual(result, ["Drake"])


if __name__ == "__main__":
    unittest.main()
