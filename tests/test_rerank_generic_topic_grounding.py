"""Regression tests for entity grounding on generic, entity-less topics.

These were written as xfail(strict=True) characterisation tests pinning two live
defects, then flipped to ordinary assertions when the fix landed and they XPASSed.

Observed 2026-07-26 on a real run of the topic
"AI code review bottleneck: can reviewers still judge AI-produced work".
Of the eight rendered clusters, three were pure off-topic noise (a promotional
post for an AI course, a Chinese web-drama upload, and a space-astronomy video)
and they scored 39-41 against on-topic clusters scoring 39-47, so the drama
upload outranked the most on-topic thread in the corpus. Neither downstream
filter is at fault: `render._clusters_clearing_relevance_floor` only drops a
cluster whose representatives are *all* explicitly entity-miss-demoted, and the
demotion never fired. The two defects below are why it never fired.
"""

from lib import rerank

GENERIC_TOPIC = "AI code review bottleneck: can reviewers still judge AI-produced work"


def test_primary_entity_keeps_review_when_review_is_the_subject():
    entity = rerank._primary_entity(GENERIC_TOPIC)
    # Substring matching is not enough here: the unrelated token "reviewers"
    # survives stripping and would satisfy a bare `"review" in entity` check.
    # The phrase that actually disappears is "code review".
    assert "code review" in entity.lower(), (
        f"expected the subject phrase to survive intent-modifier stripping, got {entity!r}"
    )


def test_ubiquitous_head_token_does_not_ground_off_topic_items():
    entity = rerank._primary_entity(GENERIC_TOPIC)
    off_topic = (
        "INSTEAD OF WATCHING NETFLIX TONIGHT Spend 1 hour with this Claude AI FULL "
        "COURSE that teaches you how to BUILD and AUTOMATE anything"
    )
    assert not rerank._entity_grounded(off_topic, entity), (
        "a course advertisement sharing only the token 'AI' should not count as grounded"
    )


def test_broad_token_buried_inside_another_word_does_not_ground():
    """A trailing token must begin a word to count as a mention.

    Raised in review of the fix: once the ubiquitous head token stops grounding,
    the fallback tests the remaining tokens, and a broad one like "code" matches
    as a bare substring inside unrelated words. This item is off-topic and the
    pre-fix head check demoted it correctly, so the fix must not newly ground it.
    """
    entity = rerank._primary_entity(GENERIC_TOPIC)
    unrelated = "my weekend project: a barcode scanner written in rust"
    assert not rerank._entity_grounded(unrelated, entity), (
        "'code' inside 'barcode' should not ground an unrelated candidate"
    )


def test_inflected_trailing_token_still_grounds():
    """Word-start anchoring must keep the suffix tolerance it replaces."""
    entity = rerank._primary_entity(GENERIC_TOPIC)
    on_topic = "our reviewers cannot keep up with generated diffs"
    assert rerank._entity_grounded(on_topic, entity)


def test_on_topic_item_is_grounded_control():
    """Control: the same machinery must still ground a genuinely on-topic item.

    Without this, a fix could satisfy the two xfails above by grounding nothing.
    """
    entity = rerank._primary_entity(GENERIC_TOPIC)
    on_topic = "How to keep QA from being a giant bottleneck with AI coding"
    assert rerank._entity_grounded(on_topic, entity)
