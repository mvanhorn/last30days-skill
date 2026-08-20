"""U1 - deterministic name distiller and junk-shape classifier.

topic_shape is the stdlib-only, pure-function foundation for discovery's
content-worthy topic naming: distill_topic_name turns a listing title (+
optional snippet) into a short, ordered, searchable phrase, and is_junk_shape
flags help-me questions, beginner asks, and personal musings that should never
become topics. Titles below marked "real run" are verbatim from the motivating
2026-07 discovery run.
"""

from lib import topic_shape

# Real run: long news headline whose subject entity ("Gemma 4") must survive.
GEMMA_TITLE = (
    "Google is updating Gemma 4's chat templates, bringing major fixes to "
    'tool calling and reducing "laziness", and enabling Flash Attention 4 '
    "on Hopper GPUs"
)

# Real run: anecdote framing that must not leak into the name.
COWORKER_TITLE = (
    "My coworker let an AI agent handle Slack replies while he was "
    '"unavailable." It did not go well.'
)


# ---------------------------------------------------------------- naming ----


def test_distill_prefers_digit_bearing_entity_phrase():
    name = topic_shape.distill_topic_name(GEMMA_TITLE)
    assert "gemma 4" in name.lower()
    assert len(name.split()) <= 6
    # No sentence scaffolding: the verb-phrase framing must be gone.
    assert "is updating" not in name.lower()
    assert '"' not in name


def test_distill_drops_anecdote_framing():
    name = topic_shape.distill_topic_name(COWORKER_TITLE)
    words = name.lower().split()
    assert not name.lower().startswith("my coworker")
    assert "slack" in words
    assert "ai" in words or "agent" in words
    assert len(words) <= 6


def test_distill_short_title_passes_through():
    assert topic_shape.distill_topic_name("Agent swarms economics") == "Agent swarms economics"


def test_distill_show_hn_launch_title():
    name = topic_shape.distill_topic_name("Show HN: I built an open-source agent memory layer")
    assert name == "open-source agent memory layer"


def test_distill_strips_interrogative_scaffolding():
    name = topic_shape.distill_topic_name("How do I get started with LangGraph?")
    assert "how do i" not in name.lower()
    assert "langgraph" in name.lower()


# ------------------------------------------------------------ junk: true ----


def test_junk_beginner_help_ask():
    # Real run.
    assert topic_shape.is_junk_shape("I need help starting to learn about AI AGENTS")


def test_junk_is_anyone_question():
    # Real run.
    assert topic_shape.is_junk_shape(
        "Is anyone actually orchestrating multi-agent workflows well, or are we all duct-taping?"
    )


def test_junk_everyone_musing():
    # Real run: musing shape despite capitalized entities.
    assert topic_shape.is_junk_shape(
        'Everyone Is "Building AI Agents" - But Do We Mean the Same Thing?'
    )


def test_junk_leading_interrogative_asks():
    assert topic_shape.is_junk_shape("How do I get started with agent frameworks")
    assert topic_shape.is_junk_shape("Does anyone use LangGraph in production?")
    assert topic_shape.is_junk_shape("Can someone explain MCP servers to me")
    assert topic_shape.is_junk_shape("where do I start with local models")


def test_junk_trailing_question_without_entity():
    assert topic_shape.is_junk_shape("so are we just letting agents run wild now?")


def test_junk_uses_snippet_only_when_title_has_no_entities():
    assert topic_shape.is_junk_shape(
        "getting into local models",
        "I'm a total beginner, where do I start?",
    )
    # Entity-bearing title wins over a chatty snippet.
    assert not topic_shape.is_junk_shape(
        "Gemma 4 tool calling fixes",
        "need help understanding the changelog",
    )


# ----------------------------------------------------------- junk: false ----


def test_not_junk_news_shaped_statement():
    assert not topic_shape.is_junk_shape("Agent swarms and the new model economics")
    assert not topic_shape.is_junk_shape(GEMMA_TITLE)


def test_not_junk_show_hn_launch():
    assert not topic_shape.is_junk_shape("Show HN: I built an open-source agent memory layer")


def test_not_junk_question_about_named_entity():
    assert not topic_shape.is_junk_shape("Is Gemma 4 actually good?")


def test_not_junk_explainer_headline_with_entity():
    assert not topic_shape.is_junk_shape("What Gemma 4 means for local inference")
    assert not topic_shape.is_junk_shape("Why Gemma 4 flopped on Hopper GPUs")


# ------------------------------------------------------------ edge cases ----


def test_both_functions_work_with_title_alone():
    assert topic_shape.distill_topic_name("Gemma 4 benchmarks") == "Gemma 4 benchmarks"
    assert topic_shape.is_junk_shape("need help with my agent setup") is True
    assert topic_shape.is_junk_shape("Gemma 4 benchmarks") is False


def test_distill_lowercase_no_entity_falls_back_to_truncated_title():
    title = "thoughts on where this is all going"
    name = topic_shape.distill_topic_name(title)
    assert name == "thoughts on where this is all"
    assert topic_shape.is_junk_shape(title) is True


def test_cjk_title_no_crash_and_passes_through():
    title = "大模型智能体的未来发展方向"
    assert topic_shape.distill_topic_name(title) == title
    assert topic_shape.is_junk_shape(title) is False


def test_mixed_cjk_title_extracts_latin_entity():
    name = topic_shape.distill_topic_name("谷歌 更新 Gemma 4 聊天模板")
    assert name == "Gemma 4"


def test_long_unspaced_cjk_title_capped_reasonably():
    title = "大模型" * 60  # single unspaced 180-char run
    name = topic_shape.distill_topic_name(title)
    assert name
    assert len(name) <= 80


def test_name_never_has_trailing_punct_or_quotes():
    cases = [
        'Gemma 4 benchmarks are "insane"...',
        "Agent swarms economics?!",
        "What is the deal with agent frameworks???",
        GEMMA_TITLE,
        COWORKER_TITLE,
    ]
    for title in cases:
        name = topic_shape.distill_topic_name(title)
        assert name, title
        assert name[-1] not in ".,;:!?\"'`- ", title
        assert '"' not in name and "“" not in name and "”" not in name, title


def test_very_long_single_token_title_is_capped():
    name = topic_shape.distill_topic_name("a" * 300)
    assert name
    assert len(name) <= 80


def test_url_only_title_is_safe_and_nonempty():
    name = topic_shape.distill_topic_name("https://news.example.com/2026/07/agent-memory-layer/")
    assert name
    assert len(name.split()) == 1
    assert name[-1] not in "/?.,;:!\"'"


def test_blank_inputs():
    # Sole exception to the never-empty contract: no word content anywhere.
    assert topic_shape.distill_topic_name("") == ""
    assert topic_shape.distill_topic_name("   ", "  ") == ""
    assert topic_shape.is_junk_shape("") is True
    # A blank title falls back to the snippet.
    assert topic_shape.distill_topic_name("", "Gemma 4 rollout chatter") == "Gemma 4 rollout chatter"
