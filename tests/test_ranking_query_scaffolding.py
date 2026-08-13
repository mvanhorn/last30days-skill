"""Contract test: planner ranking-query scaffolding must not score as topic signal."""
import pytest
from lib import relevance
from lib import planner


# Ranking-query templates emitted by planner.py. Placeholders are substituted
# with sentinels so only the fixed scaffolding words remain.
_RANKING_TEMPLATES = (
    "What recent evidence from the last 30 days is most relevant to {X}?",
    "What recent evidence from the last 30 days is most relevant to {X}, especially about {Y}?",
    "What recent evidence from the last 30 days is most relevant to {X} in the comparison '{Y}'?",
    "What are the current odds, forecasts, or market signals about {X}?",
    "What new reactions or follow-up reporting from the last 30 days matter for {X}?",
    "What real-world workflows or pipelines are people running with {X}?",
    "What production deployments or real-world use cases of {X} are people describing?",
    "What hands-on experience reports or reviews of {X} exist in the last 30 days?",
)

# Template words deliberately NOT added to LOW_SIGNAL_QUERY_TOKENS because they
# are domain nouns that can legitimately be a user's topic. Globally demoting
# them would harm relevance for every source. They only appear in the
# intent-specific templates, which fire because the topic already carries that
# intent, so their presence is correlated with the topic rather than noise.
_ACCEPTED_DOMAIN_NOUNS = frozenset({
    "cases", "deployments", "experience", "forecasts", "market", "pipelines",
    "production", "reactions", "reporting", "reports", "signals", "workflows",
})

_SENTINELS = {"zzqqsentinel", "wwqqsentinel"}


def _fixed_tokens():
    tokens = set()
    for tpl in _RANKING_TEMPLATES:
        filled = tpl.replace("{X}", "zzqqsentinel").replace("{Y}", "wwqqsentinel")
        tokens |= relevance.tokenize(filled)
    return tokens - _SENTINELS


def test_every_ranking_template_word_is_classified():
    """No planner scaffolding word may silently count as informative topic signal."""
    unclassified = sorted(
        t for t in _fixed_tokens()
        if t not in relevance.LOW_SIGNAL_QUERY_TOKENS
        and t not in _ACCEPTED_DOMAIN_NOUNS
    )
    assert not unclassified, (
        "planner ranking-query template words are neither low-signal nor an "
        f"accepted domain noun: {unclassified}. Add them to "
        "LOW_SIGNAL_QUERY_TOKENS, or to _ACCEPTED_DOMAIN_NOUNS with a reason."
    )


def test_base_template_contributes_no_informative_tokens():
    """The base template must not inflate coverage; only the topic should."""
    q = planner._ranking_query("Peter Steinberger steipete", "")
    prepared = relevance.PreparedQuery(q)
    assert prepared.informative_q_tokens <= {"peter", "steinberger", "steipete"}, (
        f"base ranking query leaks scaffolding into informative tokens: "
        f"{sorted(prepared.informative_q_tokens)}"
    )


def test_core_qualified_branch_contributes_no_informative_tokens():
    """The `especially about <core>` branch must not leak `especially` either."""
    q = planner._ranking_query("Peter Steinberger steipete", "steipete")
    prepared = relevance.PreparedQuery(q)
    assert "especially" not in prepared.informative_q_tokens, (
        f"core-qualified branch leaks 'especially': {sorted(prepared.informative_q_tokens)}"
    )


def test_off_topic_post_still_scores_below_floor():
    """Discrimination must survive: unrelated content stays low."""
    q = planner._ranking_query("Peter Steinberger steipete", "")
    prepared = relevance.PreparedQuery(q)
    score = relevance.token_overlap_relevance(
        prepared, "Sourdough starter tips for cold kitchens in winter baking"
    )
    assert score < 0.15, f"off-topic post scored {score}, expected below the 0.15 floor"


def test_zero_overlap_post_remains_zero():
    """Pins the boundary this unit cannot cross; U2's exemption owns these."""
    q = planner._ranking_query("Peter Steinberger steipete", "")
    prepared = relevance.PreparedQuery(q)
    score = relevance.token_overlap_relevance(
        prepared, "cli was a year ago. apps maybe 6 months. now it's services."
    )
    assert score == 0.0, (
        f"expected 0.0 for a post sharing no token with the query, got {score}. "
        "If this changes, KTD7's division of labour between U1 and U2 needs revisiting."
    )
