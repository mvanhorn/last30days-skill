"""Regression tests for entity grounding on generic, entity-less topics."""

import pytest

from lib import rerank

GENERIC_TOPIC = "AI code review bottleneck: can reviewers still judge AI-produced work"


def test_primary_entity_keeps_review_when_review_is_the_subject():
    entity = rerank._primary_entity(GENERIC_TOPIC)
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
    entity = rerank._primary_entity(GENERIC_TOPIC)
    unrelated = "my weekend project: a barcode scanner written in rust"
    assert not rerank._entity_grounded(unrelated, entity), (
        "'code' inside 'barcode' should not ground an unrelated candidate"
    )


@pytest.mark.parametrize(
    "unrelated",
    [
        "This code works",
        "The review was helpful",
        "Work is changing",
    ],
)
def test_broad_trailing_token_alone_does_not_ground(unrelated):
    entity = rerank._primary_entity(GENERIC_TOPIC)
    assert not rerank._entity_grounded(unrelated, entity)


def test_inflected_trailing_token_still_grounds():
    entity = rerank._primary_entity(GENERIC_TOPIC)
    on_topic = "our reviewers cannot keep up with generated diffs"
    assert rerank._entity_grounded(on_topic, entity)


def test_on_topic_item_is_grounded_control():
    entity = rerank._primary_entity(GENERIC_TOPIC)
    on_topic = "How to keep QA from being a giant bottleneck with AI coding"
    assert rerank._entity_grounded(on_topic, entity)


def test_short_generic_head_keeps_safe_no_op():
    assert rerank._entity_grounded("Golang concurrency patterns", "Go programming")
