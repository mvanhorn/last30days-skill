"""Quoted proper-noun phrases must survive into the provider query.

Two compounding defects meant a phrase search never actually happened:

1. planner._keyword_query emitted the quoted compound *and* a core that
   already contained the same words: '"Peter Steinberger" peter steinberger
   steipete'.
2. bird_x stripped the quotes before building the X query, degrading an
   intended phrase match into a bare token conjunction
   (peter AND steinberger AND steipete).

On the measured 'Peter Steinberger steipete' baseline the primary X lane
returned nothing as a result, and the whole run depended on the supplement
lanes.
"""

from lib import bird_x, planner


def test_quoted_phrase_words_are_not_repeated_unquoted():
    q = planner._keyword_query("Peter Steinberger steipete", "peter steinberger steipete")
    assert q.lower().count("steinberger") == 1, (
        f"'steinberger' appears more than once in {q!r}; the quoted compound "
        "is being emitted alongside a core that already contains it"
    )
    assert '"Peter Steinberger"' in q, "the proper-noun phrase must stay quoted"


def test_distinct_topic_tokens_survive_alongside_the_phrase():
    q = planner._keyword_query("Peter Steinberger steipete", "peter steinberger steipete")
    assert "steipete" in q.lower(), (
        f"deduplicating the phrase must not drop unrelated topic tokens: {q!r}"
    )


def test_single_word_topic_is_unchanged():
    q = planner._keyword_query("bentgo", "bentgo")
    assert q.strip() == "bentgo"


def test_topic_with_no_title_cased_compound_is_unquoted():
    q = planner._keyword_query("open source llm tooling", "open source llm tooling")
    assert '"' not in q


def test_provider_query_preserves_quoted_phrase():
    """bird_x must not strip quotes out of the phrase before querying X.

    X advanced search supports quoted phrases natively, so passing them
    through is strictly better retrieval than a token conjunction.
    """
    built = bird_x.build_topic_query('"Peter Steinberger" steipete', "2026-07-14")
    assert '"Peter Steinberger"' in built, (
        f"quoted phrase was stripped before reaching X: {built!r}"
    )
    assert "since:2026-07-14" in built


def test_provider_query_keeps_bare_tokens():
    built = bird_x.build_topic_query("bentgo lunch", "2026-07-14")
    assert "bentgo" in built and "lunch" in built
    assert '"' not in built


def test_provider_query_drops_grouping_syntax_but_not_phrases():
    """Bird grouping characters are still noise; phrase quotes are not."""
    built = bird_x.build_topic_query('("Claude Code") review', "2026-07-14")
    assert '"Claude Code"' in built
    assert "(" not in built and ")" not in built


def test_unbalanced_quote_falls_back_to_bare_tokens():
    """An orphan quote must never reach X.

    Upstream trimming (core-subject extraction, the retry ladder shortening a
    query) can cut a topic mid-phrase, leaving 'berlin "mixed-use'. X reads the
    orphan as an unterminated phrase and matches nothing, so a query that
    cannot be balanced is safer unquoted.
    """
    built = bird_x.build_topic_query('immobilienmakler berlin "mixed-use', "2026-07-12")
    assert '"' not in built, f"unbalanced quote survived into the query: {built!r}"
    assert "immobilienmakler" in built and "mixed-use" in built


def test_balanced_quotes_are_still_preserved():
    built = bird_x.build_topic_query('"Claude Code" "Peter Steinberger"', "2026-07-14")
    assert built.count('"') == 4
