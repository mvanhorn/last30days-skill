"""Tests for the Digg bar: which Digg stories are prominent, ordinary or dropped.

The bar is pure. Nothing here starts ``digg-pp-cli`` or touches the network.
"""

from __future__ import annotations

import pytest

from lib import digg, normalize


def _story(
    title: str,
    summary: str = "A short summary of what is happening.",
    *,
    score: float | None = 226,
    sources: int = 44,
    story_id: str | None = "uuid-1",
    url_id: str = "abc123xy",
):
    """A Digg story as the pipeline holds it after normalize."""
    engagement: dict = {"postCount": sources, "uniqueAuthors": 3}
    if score is not None:
        engagement["digg_score"] = score
    raw = {
        "id": url_id,
        "title": title,
        "tldr": summary,
        "url": f"https://di.gg/tech/{url_id}",
        "date": "2026-11-01",
        "engagement": engagement,
        "relevance": 0.5,
    }
    if story_id:
        raw["cluster_id"] = story_id
    return normalize._normalize_digg("digg", raw, 0, "2026-10-13", "2026-11-12")


def _outcome(topic: str, story) -> str:
    """Which of the bar's lists the one story landed in."""
    result = digg.apply_bar(topic, [story])
    names = [
        name
        for name in ("prominent", "ordinary", "dropped", "legacy")
        if getattr(result, name)
    ]
    assert len(names) == 1, names
    assert getattr(result, names[0]) == [story]
    return names[0]


def test_big_story_naming_the_topic_in_its_title_is_prominent():
    story = _story("OpenAI Releases GPT-6.1 Terra", score=226, sources=44)
    assert _outcome("OpenAI", story) == "prominent"


def test_story_naming_the_topic_in_its_title_but_too_small_is_ordinary():
    low_score = _story("OpenAI Releases GPT-6.1 Terra", score=30, sources=44)
    few_sources = _story("OpenAI Releases GPT-6.1 Terra", score=60, sources=3)
    assert _outcome("OpenAI", low_score) == "ordinary"
    assert _outcome("OpenAI", few_sources) == "ordinary"


def test_story_at_exactly_the_thresholds_is_prominent():
    story = _story(
        "OpenAI Releases GPT-6.1 Terra",
        score=digg.PROMINENT_MIN_SCORE,
        sources=digg.PROMINENT_MIN_SOURCES,
    )
    assert (digg.PROMINENT_MIN_SCORE, digg.PROMINENT_MIN_SOURCES) == (40, 5)
    assert _outcome("OpenAI", story) == "prominent"


def test_big_story_naming_the_topic_only_in_its_summary_is_dropped():
    story = _story(
        "Kevin Weil launches Warp Sciences",
        "The former OpenAI product chief is starting a science company.",
        score=92,
        sources=20,
    )
    assert _outcome("OpenAI", story) == "dropped"


@pytest.mark.parametrize("title, summary, outcome", [
    (
        "The Weeknd's 'After Hours Til Dawn' sets male solo touring record",
        "The tour trails only Taylor Swift and Coldplay on the all-time list.",
        "dropped",
    ),
    (
        "Swift's Eras Tour film returns to theaters",
        "Taylor Swift is bringing the concert film back for one weekend.",
        "ordinary",
    ),
    (
        "Taylor Swift becomes Nashville Songwriters Hall of Fame inductee",
        "She is the youngest writer the hall has inducted.",
        "prominent",
    ),
])
def test_story_must_carry_the_person_in_its_title_to_be_kept(title, summary, outcome):
    story = _story(title, summary, score=300, sources=40)
    assert _outcome("Taylor Swift", story) == outcome


def test_big_story_that_never_names_the_person_is_dropped():
    gala = _story(
        "Honoree John Carpenter kicks off the Academy Museum Gala carpet",
        "Stars arrived in Los Angeles for the annual fundraiser.",
        score=652,
        sources=80,
    )
    other_taylor = _story("Taylor Sheridan signs deal", "The deal covers new series.")
    assert _outcome("Taylor Swift", gala) == "dropped"
    assert _outcome("Taylor Swift", other_taylor) == "dropped"


def test_two_letter_topic_matches_only_as_a_whole_word():
    named = _story("AI is forcing us to pay off technical debt")
    maine = _story("Maine fishing season opens", "Anglers wait for the rain to clear.")
    paid = _story("Maintaining a paid mailing list", "Email remains a main channel.")
    assert _outcome("AI", named) == "prominent"
    assert _outcome("AI", maine) == "dropped"
    assert _outcome("AI", paid) == "dropped"


def test_topic_inside_a_longer_word_does_not_name_it():
    metadata = _story(
        "Understanding metadata in Postgres", "How the catalog stores metadata."
    )
    named = _story("Meta ships new glasses")
    assert _outcome("Meta", metadata) == "dropped"
    assert _outcome("Meta", named) == "prominent"


def test_two_word_topic_needs_one_word_in_the_title_and_the_phrase_in_the_text():
    in_title = _story("World Series Game 5 preview")
    one_word_in_title = _story(
        "Brewers take Series lead",
        "Milwaukee is one win from its first World Series title.",
    )
    in_summary = _story(
        "Brewers sweep Dodgers in NLCS upset",
        "Milwaukee reaches the World Series for the first time since 1982.",
    )
    apart = _story(
        "Brewers sweep Dodgers in NLCS upset",
        "The baseball world is stunned. Milwaukee won the series in four games.",
    )
    # A title word with no full phrase anywhere is not enough.
    word_without_phrase = _story(
        "Brewers take Series lead",
        "Milwaukee is one win from the pennant.",
    )
    assert _outcome("World Series", in_title) == "prominent"
    assert _outcome("World Series", one_word_in_title) == "ordinary"
    assert _outcome("World Series", in_summary) == "dropped"
    assert _outcome("World Series", apart) == "dropped"
    assert _outcome("World Series", word_without_phrase) == "dropped"

    surname = _story(
        "Altman says the next model is close",
        "Sam Altman told staff the release is weeks away.",
    )
    assert _outcome("Sam Altman", surname) == "ordinary"


def test_name_hidden_inside_another_word_is_dropped():
    story = _story(
        "Prosecutors file felony charges in fraud case",
        "The filing must be answered within thirty days.",
    )
    assert _outcome("Elon Musk", story) == "dropped"


def test_full_name_in_title_is_prominent_beside_a_lookalike_word():
    story = _story("Sam Altman meets Samsung executives")
    assert _outcome("Sam Altman", story) == "prominent"


def test_question_topic_is_matched_on_its_subject():
    story = _story("Twitter restores verification")
    assert _outcome("what happened to Twitter", story) == "prominent"


def test_comparison_topic_counts_a_story_naming_either_side():
    anthropic = _story("Anthropic ships Claude update", "The model is faster.")
    neither = _story("Mistral ships new model", "The model is faster.")
    assert _outcome("OpenAI vs Anthropic", anthropic) == "prominent"
    assert _outcome("OpenAI versus Anthropic", anthropic) == "prominent"
    assert _outcome("OpenAI vs Anthropic", neither) == "dropped"


def test_topic_with_no_subject_keeps_every_story_as_ordinary():
    stories = [
        _story("OpenAI Releases GPT-6.1 Terra", story_id="uuid-1"),
        _story("Maine fishing season opens", story_id="uuid-2"),
    ]
    for topic in ("what is the latest", ""):
        result = digg.apply_bar(topic, stories)
        assert result.ordinary == stories
        assert result.prominent == []
        assert result.dropped == []


def test_legacy_cluster_without_a_story_id_is_returned_untouched():
    on_topic = _story("OpenAI Releases GPT-6.1 Terra", story_id=None, url_id="old1")
    off_topic = _story("Maine fishing season opens", story_id=None, url_id="old2")
    result = digg.apply_bar("OpenAI", [off_topic, on_topic])
    assert result.legacy == [off_topic, on_topic]
    assert result.prominent == result.ordinary == result.dropped == []
    assert digg.STORY_ID_KEY not in on_topic.metadata


def test_story_without_a_digg_score_is_ordinary_when_named_and_dropped_when_not():
    named = _story("OpenAI Releases GPT-6.1 Terra", score=None, sources=44)
    unnamed = _story("Maine fishing season opens", score=None, sources=44)
    assert _outcome("OpenAI", named) == "ordinary"
    assert _outcome("OpenAI", unnamed) == "dropped"


def test_prominent_stories_are_ordered_by_digg_score_then_story_id():
    small = _story("OpenAI hires a new CFO", score=50, story_id="uuid-a")
    tie_b = _story("OpenAI opens London office", score=226, story_id="uuid-b")
    tie_a = _story("OpenAI Releases GPT-6.1 Terra", score=226, story_id="uuid-a2")
    biggest = _story("OpenAI raises new round", score=1286, story_id="uuid-z")
    ordinary_1 = _story("OpenAI adds a setting", score=10, story_id="uuid-o1")
    ordinary_2 = _story("OpenAI fixes a bug", score=30, story_id="uuid-o2")
    result = digg.apply_bar(
        "OpenAI", [ordinary_1, small, tie_b, ordinary_2, tie_a, biggest]
    )
    assert result.prominent == [biggest, tie_a, tie_b, small]
    assert result.ordinary == [ordinary_1, ordinary_2]


def test_verdict_is_the_same_when_asked_again_and_leaves_items_unchanged():
    big = _story("OpenAI Releases GPT-6.1 Terra", story_id="uuid-1")
    loose = _story("Maine fishing season opens", story_id="uuid-2")
    before = (dict(big.metadata), dict(big.engagement))
    first = digg.apply_bar("OpenAI", [big, loose])
    # Asked later about the kept stories alone, as the source fetch does.
    again = digg.apply_bar("OpenAI", first.prominent + first.ordinary)
    assert again.prominent == first.prominent == [big]
    assert (dict(big.metadata), dict(big.engagement)) == before


def test_short_and_symbol_topics_match_as_whole_tokens():
    assert _outcome("C++", _story("C++ 26 adds reflection")) == "prominent"
    assert _outcome("C++", _story("C compilers get faster", "No plus here.")) == "dropped"
    assert _outcome("Go", _story("Go 1.26 lands generics fix")) == "prominent"
    assert _outcome("Go", _story("Google ships update", "Good news.")) == "dropped"
    assert _outcome("X", _story("X rolls out long posts")) == "prominent"
    assert _outcome("X", _story("Xbox prices rise", "Next year too.")) == "dropped"


def test_phrase_matches_across_a_hyphen_or_extra_space():
    assert _outcome("Claude Code", _story("Claude-Code gets plugins")) == "prominent"
    assert _outcome("claude code", _story("CLAUDE  CODE gets plugins")) == "prominent"


@pytest.mark.parametrize("topic, title, kept", [
    ("why is Nvidia stock down", "Nvidia shares fall after export ruling", True),
    ("why is Nvidia stock down", "Netflix renews Haunted Hotel", False),
    ("Taylor Swift new album", "Taylor Swift announces a record", True),
    ("best noise cancelling headphones", "Sony ships new headphones", True),
    ("best noise cancelling headphones", "Best pizza in Naples", False),
    ("how to use Cursor for refactoring", "Cursor adds background agents", True),
])
def test_descriptive_topic_keeps_stories_that_share_a_telling_word(topic, title, kept):
    story = _story(title, score=300, sources=40)
    assert _outcome(topic, story) == ("ordinary" if kept else "dropped")
