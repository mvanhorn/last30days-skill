"""The Bird lane must not forward X advanced-search operators from a topic.

``x_api.build_keyword_query`` drops operator-shaped tokens for the xurl lane
(#1179). The bird lane never did, so a topic like 'since:2020 docker tips'
emitted a duplicate ``since:`` and 'from:someone' silently restricted the
search to one account instead of searching the words.
"""

from lib import bird_x


def test_operator_tokens_never_reach_the_query():
    built = bird_x.build_topic_query("from:someone topic", "2026-11-01", "2026-11-20")
    assert "from:" not in built, f"a from: operator reached X: {built!r}"
    assert "topic" in built


def test_topic_since_does_not_duplicate_the_window_filter():
    built = bird_x.build_topic_query("since:2020 docker tips", "2026-11-01", "2026-11-20")
    assert built.count("since:") == 1, (
        f"a topic-supplied since: collided with the window filter: {built!r}"
    )
    assert "since:2026-11-01" in built
    assert "docker" in built


def test_lang_and_leading_negation_are_dropped():
    lang = bird_x.build_topic_query("lang:en rust tips", "2026-11-01")
    assert "lang:" not in lang
    assert "rust" in lang

    negated = bird_x.build_topic_query("-spam crypto news", "2026-11-01")
    assert "-spam" not in negated, f"leading negation reached X: {negated!r}"
    assert "crypto" in negated


def test_bare_boolean_words_are_dropped():
    built = bird_x.build_topic_query("python and rust", "2026-11-01")
    assert " and " not in built, f"a bare boolean reached X: {built!r}"
    assert "python" in built and "rust" in built


def test_trailing_colon_subject_survives_as_a_word():
    """'Docker:' is a subject with punctuation, not an operator."""
    built = bird_x.build_topic_query("Docker:", "2026-11-01")
    assert "docker" in built.lower()
    assert "docker:" not in built.lower()


def test_query_is_bounded_before_it_reaches_x():
    long_topic = " ".join(f"word{i}" for i in range(120))
    built = bird_x.build_topic_query(long_topic, "2026-11-01", "2026-11-20")
    assert len(built) <= bird_x.MAX_QUERY_CHARS, (
        f"query exceeded X's limit ({len(built)} > {bird_x.MAX_QUERY_CHARS})"
    )
    # The window filter must survive the trim; it is not optional.
    assert "since:2026-11-01" in built


def test_trim_never_leaves_an_unterminated_phrase():
    """Trimming a long quoted topic must not cut the closing quote.

    An orphan quote is read by X as an unterminated phrase and matches nothing,
    so the length bound has to degrade to bare tokens instead.
    """
    phrase = " ".join(f"w{i}" for i in range(200))
    built = bird_x.build_topic_query(f'"{phrase}" extra', "2026-11-01", "2026-11-20")
    assert len(built) <= bird_x.MAX_QUERY_CHARS, f"over budget: {len(built)}"
    assert built.count('"') % 2 == 0, f"unterminated phrase reached X: {built!r}"
    assert "since:2026-11-01" in built
