"""The Bird lane must not forward X advanced-search operators from a topic.

``x_api.build_keyword_query`` drops operator-shaped tokens for the xurl lane
(#1179). The bird lane never did, so a topic like 'since:2020 docker tips'
emitted a duplicate ``since:`` and 'from:someone' silently restricted the
search to one account instead of searching the words.
"""

from unittest import mock

from lib import bird_x, subproc


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


def _search_queries(topic):
    with mock.patch.object(
        bird_x.subproc, "run_with_timeout", autospec=True,
        return_value=subproc.SubprocResult(returncode=0, stdout='{"items": []}', stderr=""),
    ) as run:
        bird_x.search_x(topic, "2026-11-01", "2026-11-20")
    for call in run.call_args_list:
        assert call.args[0][-2] == "--"
    return [call.args[0][-1] for call in run.call_args_list]


def test_topic_operators_are_removed_before_selecting_retry_keywords():
    queries = _search_queries("since:2012 from:noise lang:fr -spam multi-agent autonomous mission")
    filters = "since:2026-11-01 until:2026-11-21"
    assert queries == [
        f"multi-agent autonomous mission {filters}",
        f'("multi-agent") {filters}',
        f"multi-agent autonomous {filters}",
        f"multi-agent {filters}",
    ]


def test_quoted_name_keeps_its_conjunction_in_primary_and_retries():
    queries = _search_queries('"Research and Development" quantum topology simulations')
    assert len(queries) >= 2
    for query in queries:
        assert query.lower().startswith(('"research and development" ', '("research and development") '))


def test_retries_keep_punctuated_title_even_with_an_unquoted_compound():
    queries = _search_queries('"Mission: Impossible" multi-agent fallout')
    filters = "since:2026-11-01 until:2026-11-21"
    assert queries == [
        f'"Mission: Impossible" multi-agent fallout {filters}',
        f'("Mission: Impossible") {filters}',
        f'"Mission: Impossible" multi-agent {filters}',
        f'"Mission: Impossible" {filters}',
    ]


def test_retries_prioritize_late_quoted_title_when_shortening():
    queries = _search_queries('decentralization infrastructure economics "Dune" stunts')
    filters = "since:2026-11-01 until:2026-11-21"
    assert queries == [
        f'decentralization infrastructure economics "Dune" stunts {filters}',
        f'("Dune") {filters}',
        f'"Dune" decentralization {filters}',
        f'"Dune" decentralization {filters}',
    ]


def test_quoted_phrase_keeps_literal_grouping_and_operator_shaped_words():
    phrase = '"From: quote (and) -dash [literal]"'
    query = bird_x.build_topic_query(f"{phrase} (from:ignored product)", "2026-11-01")
    assert query == f"{phrase} product since:2026-11-01"


def test_or_retry_drops_oversized_compounds_without_breaking_group_or_window():
    topic = " ".join(["Alpha"] * 112) + " multi-agent"
    queries = _search_queries(topic)
    assert len(queries) == 4
    assert queries[1] == '("multi-agent") since:2026-11-01 until:2026-11-21'
    assert all(len(query) <= bird_x.MAX_QUERY_CHARS for query in queries)


def test_operator_only_topic_never_starts_a_window_only_search():
    assert _search_queries("since:2012 lang:en -spam OR") == []


def test_oversized_late_phrase_does_not_send_an_empty_retry():
    phrase = "x" * bird_x.MAX_QUERY_CHARS
    queries = _search_queries(f'decentralization economics "{phrase}"')
    assert queries == ["decentralization economics since:2026-11-01 until:2026-11-21"]
