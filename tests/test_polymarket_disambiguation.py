"""Tests for --polymarket-keywords filter and filter_items_against_keywords."""

from __future__ import annotations

import unittest

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


def test_acronym_title_matches_spelled_out_topic():
    """A title abbreviating what the topic spells out must not be filtered.

    Market titles use shorthand ("AGI by 2030?") while topics arrive spelled
    out, so proportional word overlap scored zero on squarely on-topic markets.
    """
    assert polymarket._passes_topic_filter(
        "artificial general intelligence",
        "OpenAI announces it has achieved AGI before 2027?",
    )
    assert polymarket._passes_topic_filter("artificial general intelligence", "AGI by 2030?")


def test_acronym_credit_does_not_widen_the_filter():
    """The acronym bridge must not rescue off-topic or weak single-word hits."""
    assert not polymarket._passes_topic_filter(
        "artificial general intelligence", "Premier League top scorer 2026"
    )
    assert not polymarket._passes_topic_filter(
        "Mill.com food recycler", "Meek Mill announces tour"
    )
    assert polymarket._passes_topic_filter("AGI", "AGI by 2030?")
    assert polymarket._passes_topic_filter("Kanye West", "Kanye West announces new album")


def test_similarity_scores_acronym_title_as_full_match():
    """Clearing the topic filter is not enough: the relevance floor also drops it.

    An on-topic market passed _passes_topic_filter and was then dropped by the
    0.15 relevance floor, because the similarity scorer compared spelled-out
    topic words against a shorthand title.
    """
    assert (
        polymarket._compute_text_similarity(
            "artificial general intelligence", "AGI by 2030?"
        )
        == 1.0
    )
    assert (
        polymarket._compute_text_similarity(
            "artificial general intelligence", "Premier League top scorer 2026"
        )
        == 0.0
    )
    assert (
        polymarket._compute_text_similarity("Kanye West", "Kanye West announces new album")
        == 1.0
    )


def test_modifier_separated_acronym_matches_filter_and_similarity():
    """A leading modifier must not change the initialism used by the scorer."""
    topic = "impact of artificial general intelligence"
    title = "AGI by 2030?"

    assert polymarket._passes_topic_filter(topic, title)
    assert polymarket._compute_text_similarity(topic, title) == 1.0


def test_two_letter_initialism_does_not_expand_topic():
    """Ambiguous two-letter tokens must not receive full expanded-phrase relevance."""
    topic = "machine learning"
    title = "ML market cap above $1 billion?"

    assert not polymarket._passes_topic_filter(topic, title)
    assert polymarket._compute_text_similarity(topic, title) < 1.0
