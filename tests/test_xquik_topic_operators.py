"""The Xquik lane must not forward X advanced-search operators from a topic.

Xquik runs an X tweet search, so X applies its advanced-search grammar to the
``q`` string. ``x_api.build_keyword_query`` drops operator-shaped tokens for
the xurl lane (#1179) and ``bird_x`` does the same for the Bird lane (#1544);
the xquik lane forwarded the raw topic, so ``from:someone docker`` silently
restricted the search to one account, ``since:2020 docker tips`` put a second
``since:`` beside the research window, and ``Mission: Impossible`` turned a
title into a ``mission:`` operator.
"""

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from lib import x_api, xquik


def _sent_queries(topic, depth="default", tweets=None):
    """The ``q`` values xquik actually put on the wire for ``topic``."""
    payload = {"tweets": tweets or []}
    with patch("lib.xquik.http.get", return_value=payload) as mock_get:
        xquik.search_xquik(topic, "2026-11-01", "2026-11-20", depth=depth, token="k")
    return [parse_qs(urlsplit(call.args[0]).query)["q"][0] for call in mock_get.call_args_list]


class TestTopicOperatorsNeverReachXquik(unittest.TestCase):

    def test_from_operator_does_not_restrict_the_search_to_one_account(self):
        for query in _sent_queries("from:someone docker tips"):
            self.assertNotIn("from:", query, f"a from: operator reached X: {query!r}")
            self.assertIn("docker", query)

    def test_topic_since_does_not_duplicate_the_window_filter(self):
        for query in _sent_queries("since:2020 docker tips"):
            self.assertEqual(
                1, query.count("since:"),
                f"a topic-supplied since: collided with the window filter: {query!r}",
            )
            self.assertIn("since:2026-11-01", query)
            self.assertIn("docker", query)

    def test_lang_and_leading_negation_are_dropped(self):
        for query in _sent_queries("lang:fr rust tips"):
            self.assertNotIn("lang:", query, f"a lang: operator reached X: {query!r}")
            self.assertIn("rust", query)
        for query in _sent_queries("-spam crypto news"):
            self.assertNotIn("-spam", query, f"leading negation reached X: {query!r}")
            self.assertIn("crypto", query)

    def test_bare_boolean_words_are_dropped(self):
        for query in _sent_queries("review and security Or tools"):
            lowered = query.lower()
            self.assertNotIn(" and ", lowered, f"a bare boolean reached X: {query!r}")
            self.assertNotIn(" or ", lowered, f"a bare boolean reached X: {query!r}")
            self.assertIn("review", query)

    def test_retweet_filter_cannot_be_smuggled_in(self):
        for query in _sent_queries("topic -is:retweet"):
            self.assertNotIn("-is:retweet", query, f"a filter reached X: {query!r}")
            self.assertIn("topic", query)

    def test_trailing_colon_subject_survives_as_a_word(self):
        """'Mission: Impossible' is a subject with punctuation, not an operator."""
        sent = _sent_queries("Mission: Impossible")
        self.assertTrue(sent, "the lane stopped searching a legitimate topic")
        for query in sent:
            self.assertIn("mission", query.lower())
            self.assertNotIn("mission:", query.lower())

    def test_operator_only_topic_never_starts_a_window_only_search(self):
        self.assertEqual([], _sent_queries("since:2012 lang:en -spam OR"))

    def test_topic_portion_stays_inside_the_shared_x_query_budget(self):
        # The window filter is engine-appended after the topic, so this asserts
        # the topic-derived part honours the shared X query budget.
        topic = " ".join(f"word{i}" for i in range(200))
        window = " since:2026-11-01 until:2026-11-21"
        for query in _sent_queries(topic):
            self.assertTrue(query.endswith(window), f"window filter lost: {query!r}")
            topic_part = query[: -len(window)]
            self.assertLessEqual(
                len(topic_part), x_api.MAX_QUERY_CHARS,
                f"topic portion exceeded X's budget ({len(topic_part)}): {topic_part!r}",
            )


class TestLegitimateTopicsAreUnchanged(unittest.TestCase):

    def test_plain_topic_is_sent_as_one_keyword_query(self):
        self.assertEqual(
            ["claude code agents since:2026-11-01 until:2026-11-21"],
            _sent_queries("Claude Code agents", depth="quick"),
        )

    def test_engine_authored_compound_variant_still_searches_the_compound(self):
        # The second variant exists to hunt the compound term; sanitizing must
        # not drop it, only unwrap the grouping this module added.
        sent = _sent_queries("multi-agent orchestration patterns")
        self.assertEqual(2, len(sent), f"a query variant was lost: {sent!r}")
        self.assertIn("multi-agent", sent[1])


if __name__ == "__main__":
    unittest.main()
