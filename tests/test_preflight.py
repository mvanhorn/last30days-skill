"""Tests for scripts/lib/preflight.py Class 1 keyword-trap refuse-gate.

Class 1 (demographic shopping) is the one failure class that shipped to
public v3.0.8 and still returned junk for queries like 'birthday gift for
40 year old'. This module is the engine's structural refusal, so the model
cannot bypass by skipping SKILL.md.
"""

import unittest

from lib import preflight


class TestClass1Match(unittest.TestCase):
    """Queries that MUST trigger the refuse-gate."""

    def test_birthday_gift_for_age(self):
        self.assertIsNotNone(preflight.check_class_1_trap("birthday gift for 40 year old"))

    def test_gift_for_age(self):
        self.assertIsNotNone(preflight.check_class_1_trap("gift for 42 year old"))

    def test_gift_for_age_relationship(self):
        self.assertIsNotNone(preflight.check_class_1_trap("gift for my 42 year old husband"))

    def test_gift_ideas_for_age(self):
        self.assertIsNotNone(preflight.check_class_1_trap("gift ideas for 30 year old"))

    def test_present_for_age(self):
        self.assertIsNotNone(preflight.check_class_1_trap("present for a 50 year old"))

    def test_hyphenated_year_old(self):
        self.assertIsNotNone(preflight.check_class_1_trap("gift for 40-year-old"))

    def test_best_for_men(self):
        self.assertIsNotNone(preflight.check_class_1_trap("best running shoes for men"))

    def test_best_for_women(self):
        self.assertIsNotNone(preflight.check_class_1_trap("best gifts for women"))

    def test_best_for_kids(self):
        self.assertIsNotNone(preflight.check_class_1_trap("best toys for kids"))

    def test_what_to_buy_husband(self):
        self.assertIsNotNone(preflight.check_class_1_trap("what to buy my husband"))

    def test_what_to_get_boss(self):
        self.assertIsNotNone(preflight.check_class_1_trap("what to get my boss"))

    def test_what_to_gift_age(self):
        self.assertIsNotNone(preflight.check_class_1_trap("what to gift a 35 year old"))

    def test_gifts_for_husband(self):
        self.assertIsNotNone(preflight.check_class_1_trap("gifts for my husband"))

    def test_case_insensitive(self):
        self.assertIsNotNone(preflight.check_class_1_trap("Birthday Gift For 40 Year Old"))

    def test_leading_whitespace(self):
        self.assertIsNotNone(preflight.check_class_1_trap("  gift for 40 year old  "))


class TestClass1Skip(unittest.TestCase):
    """Queries that MUST NOT trigger the refuse-gate (qualifier present or not shopping)."""

    def test_named_person(self):
        self.assertIsNone(preflight.check_class_1_trap("Peter Steinberger"))

    def test_comparison(self):
        self.assertIsNone(preflight.check_class_1_trap("OpenClaw vs Paperclip"))

    def test_entity_query(self):
        self.assertIsNone(preflight.check_class_1_trap("Kanye West"))

    def test_general_concept(self):
        self.assertIsNone(preflight.check_class_1_trap("vibe coding"))

    def test_budget_qualifier(self):
        self.assertIsNone(preflight.check_class_1_trap("gift for my husband, $200 budget"))

    def test_hobby_qualifier(self):
        self.assertIsNone(preflight.check_class_1_trap("gift for my cooking-obsessed husband"))

    def test_loves_qualifier(self):
        self.assertIsNone(preflight.check_class_1_trap("gift for my dad who loves golf"))

    def test_is_into_qualifier(self):
        self.assertIsNone(preflight.check_class_1_trap("gift for my brother who is into woodworking"))

    def test_specific_interest_in_query(self):
        self.assertIsNone(preflight.check_class_1_trap("birthday gift for 40 year old runner"))


class TestRefuseMessage(unittest.TestCase):
    """The REFUSE message must contain the diagnostic content the model needs."""

    def test_refuse_mentions_class_1(self):
        msg = preflight.check_class_1_trap("birthday gift for 40 year old")
        assert msg is not None
        self.assertIn("Class 1", msg)

    def test_refuse_asks_for_hobbies(self):
        msg = preflight.check_class_1_trap("gift for 40 year old")
        assert msg is not None
        self.assertIn("hobbies", msg.lower())

    def test_refuse_asks_for_relationship(self):
        msg = preflight.check_class_1_trap("gift for 40 year old")
        assert msg is not None
        self.assertIn("relationship", msg.lower())

    def test_refuse_asks_for_budget(self):
        msg = preflight.check_class_1_trap("gift for 40 year old")
        assert msg is not None
        self.assertIn("budget", msg.lower())

    def test_refuse_echoes_topic(self):
        msg = preflight.check_class_1_trap("birthday gift for 40 year old")
        assert msg is not None
        self.assertIn("birthday gift for 40 year old", msg)

if __name__ == "__main__":
    unittest.main()
