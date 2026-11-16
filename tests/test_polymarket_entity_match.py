"""The entity matcher every Polymarket relevance gate shares.

A market names the entity when a topic term (or a host alias) appears as a
whole phrase in that market's question, its ``groupItemTitle``, one of its
non-Yes/No outcome labels, or its event title. Related names (a team's
players) count only on the market's own fields and are reported apart.
"""

import pytest

from lib import polymarket


def _market(question="", label="", outcomes='["Yes", "No"]'):
    return {"question": question, "groupItemTitle": label, "outcomes": outcomes}


NINERS = _market(
    "Will the San Francisco 49ers win the 2027 NFL league championship?",
    "San Francisco 49ers",
)


def _match(topic, market, title="", **hints):
    return polymarket.EntityMatcher(topic, polymarket.PolymarketHints.coerce(hints or None)).match(
        market, title,
    )


def test_topic_matches_group_item_title_and_question():
    found = _match("49ers", NINERS, "Pro Football: 2027 Champion")
    assert found is not None
    assert found.field == "market"
    assert found.related is None
    assert _match("49ers", _market(label="San Francisco 49ers"), "Pro Football: 2027 Champion")
    assert _match(
        "49ers",
        _market("Will the San Francisco 49ers win the 2027 NFL league championship?"),
        "Pro Football: 2027 Champion",
    )


def test_a_sibling_market_naming_the_topic_does_not_qualify_another_market():
    chargers = _market(
        "Will the Los Angeles Chargers win the 2027 NFL league championship?",
        "Los Angeles Chargers",
    )
    assert _match("49ers", chargers, "Pro Football: 2027 Champion") is None


def test_event_title_match_is_reported_as_a_title_match():
    found = _match("49ers", _market("8.5+ Wins", "8.5+ Wins"), "Pro Football: 49ers 2026 Win Total")
    assert found is not None
    assert found.field == "title"


@pytest.mark.parametrize("topic, market, title", [
    ("Kanye West", _market("Will the 49ers win the NFC West?", "San Francisco 49ers"),
     "Pro Football: NFC West Champion"),
    ("Muse", _market("Will Lorenzo Musetti win the 2026 ATP Finals?", "Lorenzo Musetti"),
     "ATP Finals Winner"),
    ("June Oven", _market("Will China invade Taiwan by June 30, 2027?"),
     "Will China invade Taiwan by June 30, 2027?"),
])
def test_single_word_collisions_do_not_match(topic, market, title):
    assert _match(topic, market, title) is None


@pytest.mark.parametrize("topic, question, title", [
    ("Nvidia earnings", "Will Nvidia beat quarterly earnings?", "Nvidia quarterly earnings beat?"),
    ("2028 presidential election", "Will JD Vance win the 2028 US Presidential Election?",
     "Presidential Election Winner 2028"),
    ("Sam Altman OpenAI", "Sam Altman CEO OpenAI?", "Sam Altman CEO OpenAI"),
    ("Fed rate cut", "Will the Fed cut rates in December?", "Fed decision in December"),
    ("tariffs on China", "Will the US raise its tariff on China?", "US tariff on China"),
])
def test_descriptive_topic_matches_with_its_words_in_any_order(topic, question, title):
    assert _match(topic, _market(question), title)


def test_outcome_label_matches_and_yes_no_labels_do_not():
    best_model = _market(
        "Which company has best AI model end of 2026?",
        outcomes='["Google", "Anthropic", "OpenAI"]',
    )
    assert _match("Anthropic", best_model, "Which company has best AI model end of 2026?")
    assert _match("Yes", _market("Will it rain?"), "Weather") is None


def test_alias_matches_like_the_topic():
    action = _market("Will Kimi Antonelli win F1 Action of the Year?", "Kimi Antonelli")
    title = "F1: Action of the Year"
    assert _match("Formula 1", action, title) is None
    found = _match("Formula 1", action, title, aliases=["F1"])
    assert found is not None
    assert found.related is None


@pytest.mark.parametrize("topic, text", [
    ("Apple", "Apple's Market Cap end of 2026?"),
    ("Apple", "Apple’s Market Cap end of 2026?"),
    ("Apple's", "Will Apple be the largest company?"),
    ("the Lakers", "Will the Los Angeles Lakers win the 2027 NBA Finals?"),
])
def test_possessive_and_leading_article_are_ignored(topic, text):
    assert _match(topic, _market(text), "Some event")


def test_related_name_matches_only_as_a_related_match():
    lendeborg = _market(
        "Will Yaxel Lendeborg win the 2026-27 NBA Rookie of the Year?", "Yaxel Lendeborg",
    )
    title = "NBA: 2026-27 Rookie of the Year"
    assert _match("Warriors", lendeborg, title) is None
    found = _match(
        "Warriors", lendeborg, title,
        kind="team", related=[{"name": "Yaxel Lendeborg", "relation": "player"}],
    )
    assert found is not None
    assert found.related == "Yaxel Lendeborg"
    assert found.field == "market"


def test_related_name_in_the_event_title_alone_does_not_match():
    hints = {"related": [{"name": "Stephen Curry", "relation": "player"}]}
    found = _match("Warriors", _market("Over 29.5 points?", "Over 29.5"),
                   "Stephen Curry points vs Lakers", **hints)
    assert found is None


def test_entity_match_wins_over_related_match():
    market = _market("Will the Golden State Warriors and Stephen Curry ...?", "Golden State Warriors")
    found = _match("Warriors", market, "NBA", related=[{"name": "Stephen Curry"}])
    assert found is not None
    assert found.related is None


def test_comparison_topic_matches_either_side():
    market = _market("Will Anthropic have the best AI model?", "Anthropic")
    assert _match("OpenAI vs Anthropic", market, "Which company has best AI model end of 2026?")


def test_acronym_title_matches_spelled_out_topic():
    """Market titles use shorthand ("AGI by 2030?") while topics arrive spelled out."""
    for title in ("OpenAI announces it has achieved AGI before 2027?", "AGI by 2030?"):
        assert _match("artificial general intelligence", _market(title), title)
    assert _match("impact of artificial general intelligence", _market("AGI by 2030?"), "AGI by 2030?")


def test_acronym_credit_does_not_widen_the_match():
    assert _match("artificial general intelligence", _market("Premier League top scorer 2026"),
                  "Premier League top scorer 2026") is None
    assert _match("Mill.com food recycler", _market("Meek Mill announces tour"),
                  "Meek Mill announces tour") is None
    # Two letters are too ambiguous to stand for a spelled-out phrase.
    assert _match("machine learning", _market("ML market cap above $1 billion?"),
                  "ML market cap above $1 billion?") is None


def test_soft_domain_sweep_matches_its_domain_word():
    """A sweep topic ("AI frontier developments") is about the domain itself."""
    title = "Will AI models beat humans at coding?"
    assert _match("AI frontier developments", _market(title), title)
    assert _match("MCP protocol benchmark", _market("Will the Kyoto Protocol survive?"),
                  "Will the Kyoto Protocol survive?") is None
