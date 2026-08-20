"""Which topics are worth one web search to resolve an X handle.

Gates on brevity rather than capitalization. Requiring a capital letter meant
the most common real-world spelling never resolved a handle: every recent run
on this machine -- bentgo, buzz, getenergy.com -- is lowercase.
"""

import importlib.util
from pathlib import Path

import pytest

_CLI = Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts" / "last30days.py"


def _cli():
    spec = importlib.util.spec_from_file_location("l30_cli", _CLI)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    return module


@pytest.mark.parametrize("topic", [
    "bentgo",
    "Bentgo",
    "peter steinberger",
    "Peter Steinberger",
    "getenergy.com",
    "buzz by block",
    "@steipete",
    "claude code",
])
def test_entity_topics_resolve(topic):
    assert _cli()._looks_like_entity_topic(topic) is True


@pytest.mark.parametrize("topic", [
    "best AI coding tools 2026",
    "how to build agents",
    "top tools for research",
    "what is the best model?",
    "",
])
def test_theme_topics_do_not_resolve(topic):
    assert _cli()._looks_like_entity_topic(topic) is False


def test_casing_does_not_change_the_verdict():
    """The regression this replaces: capitalization decided whether a subject's
    own posts could be protected."""
    cli = _cli()
    for lower, upper in [("bentgo", "Bentgo"), ("peter steinberger", "Peter Steinberger")]:
        assert cli._looks_like_entity_topic(lower) == cli._looks_like_entity_topic(upper)


def test_question_shaped_topics_are_themes():
    assert _cli()._looks_like_entity_topic("who is peter steinberger?") is False
