# ruff: noqa: E402
"""Tests for --polymarket-keywords filter and filter_items_against_keywords."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib import polymarket


def _item(title: str) -> dict:
    return {"title": title}


class FilterItemsAgainstKeywordsTests(unittest.TestCase):
    def test_no_keywords_returns_all(self):
        items = [_item("NBA Finals"), _item("Glasgow Warriors")]
        out = polymarket.filter_items_against_keywords(items, [])
        self.assertEqual(out, items)

    def test_single_keyword_filters(self):
        items = [
            _item("Golden State Warriors win title"),
            _item("Glasgow Warriors rugby"),
            _item("Honor of Kings: Rogue Warriors"),
        ]
        out = polymarket.filter_items_against_keywords(items, ["golden"])
        self.assertEqual(len(out), 1)
        self.assertIn("Golden State", out[0]["title"])

    def test_multiple_keywords_any_match(self):
        items = [
            _item("NBA Finals: Warriors vs Celtics"),
            _item("Glasgow rugby"),
            _item("GSW schedule"),
        ]
        out = polymarket.filter_items_against_keywords(items, ["nba", "gsw"])
        self.assertEqual(len(out), 2)

    def test_case_insensitive_match(self):
        items = [_item("Golden State Warriors"), _item("GLASGOW WARRIORS")]
        out = polymarket.filter_items_against_keywords(items, ["GOLDEN"])
        self.assertEqual(len(out), 1)
        self.assertIn("Golden State", out[0]["title"])

    def test_empty_keyword_strings_ignored(self):
        items = [_item("NBA Finals")]
        out = polymarket.filter_items_against_keywords(items, ["", "  ", ""])
        # All keywords are empty → treated as no filter
        self.assertEqual(out, items)

    def test_sourceitem_like_objects(self):
        class _SI:
            def __init__(self, t):
                self.title = t

        items = [_SI("NBA Finals"), _SI("Glasgow Warriors rugby")]
        out = polymarket.filter_items_against_keywords(items, ["nba"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "NBA Finals")

    def test_no_match_returns_empty(self):
        items = [_item("Glasgow Warriors"), _item("Rogue Warriors")]
        out = polymarket.filter_items_against_keywords(items, ["nba", "gsw"])
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
