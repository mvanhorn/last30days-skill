import json
import sys

import pytest

import last30days as cli
from lib import planner


SINGLE_TOPICS = [
    "CI/CD",
    "I/O performance",
    "openai/openai-python",
    "https://example.com",
    "https://example.com/React/Vue",
    "https://example.com/vs/topic",
    "https://example.com/vs.Vue",
    "React vsVue",
    "vs.code",
    "vs.code/extension",
    "compare CI/CD workflows",
]

COMPARISONS = [
    ("React/Vue/Svelte", ["React", "Vue", "Svelte"]),
    ("React vs Vue", ["React", "Vue"]),
    ("React vs. Vue", ["React", "Vue"]),
    ("React vs. vs. Vue", ["React", "Vue"]),
    ("React vs.Vue", ["React", "Vue"]),
    ("React VS.Vue", ["React", "Vue"]),
    ("React versus Vue", ["React", "Vue"]),
    ("vs.code versus Vue", ["vs.code", "Vue"]),
    ("vs.code vs.Vue", ["vs.code", "Vue"]),
    ("vs.code/extension versus Vue", ["vs.code/extension", "Vue"]),
    ("React versus vs.code", ["React", "vs.code"]),
    ("React vs.vs.code", ["React", "vs.code"]),
    ("React versus vs.code/extension", ["React", "vs.code/extension"]),
    ("vs.code/extension versus vs.code/other", ["vs.code/extension", "vs.code/other"]),
    ("https://example.com/vs.code versus owner/repo",
     ["https://example.com/vs.code", "owner/repo"]),
    ("React versus https://example.com/vs.code", ["React", "https://example.com/vs.code"]),
    ("React compared to Vue", ["React", "Vue"]),
    ("difference between React and Vue", ["React", "Vue"]),
    ("React/Vue/Svelte for CI/CD", ["React", "Vue", "Svelte"]),
    ("CI/CD vs I/O", ["CI/CD", "I/O"]),
    ("CI/CD vs.I/O", ["CI/CD", "I/O"]),
    ("openai/openai-python vs anthropics/anthropic-sdk-python",
     ["openai/openai-python", "anthropics/anthropic-sdk-python"]),
]


@pytest.mark.parametrize("topic", SINGLE_TOPICS)
@pytest.mark.parametrize("uncapped", [False, True])
def test_slashes_without_comparison_intent_do_not_produce_entities(topic, uncapped):
    assert planner._comparison_entities(topic, uncapped=uncapped) == []


@pytest.mark.parametrize("topic,entities", COMPARISONS)
def test_comparison_entities_preserve_slashes_inside_explicit_entities(topic, entities):
    assert planner._comparison_entities(topic) == entities


@pytest.fixture
def run_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LAST30DAYS_STORE", "")

    def run(topic):
        monkeypatch.setattr(sys, "argv", [
            "last30days", topic, "--mock", "--quick", "--search=reddit",
            "--no-browser-cookies", "--emit=json", "--json-profile=raw",
            "--save-dir", "",
        ])
        result = cli.main()
        captured = capsys.readouterr()
        assert result == 0, captured.err
        return json.loads(captured.out)

    return run


@pytest.mark.parametrize("topic", SINGLE_TOPICS)
def test_main_preserves_slash_topics_as_single_research_runs(topic, run_cli):
    payload = run_cli(topic)
    assert payload.get("comparison") is not True
    assert payload["topic"] == topic
    assert payload["query_plan"]["raw_topic"] == topic


@pytest.mark.parametrize("topic,entities", COMPARISONS)
def test_main_keeps_supported_comparison_forms(topic, entities, run_cli):
    payload = run_cli(topic)
    assert payload["comparison"] is True
    assert payload["entities"] == entities
    assert [entry["report"]["topic"] for entry in payload["reports"]] == entities


@pytest.mark.parametrize("left,right", [
    pytest.param("React", "Vue", id="plain"),
    pytest.param("vs.code", "vs.editor", id="dotted"),
    pytest.param("org/repo", "other/lib", id="repository"),
    pytest.param("vs.code/extension", "vs.editor/tool", id="dotted-repository"),
    pytest.param("https://example.com/vs.code", "https://example.org/vs.editor", id="url"),
])
@pytest.mark.parametrize("form", [
    pytest.param("{left} vs {right}", id="spaced-vs"),
    pytest.param("{left} vs. {right}", id="spaced-dotted-vs"),
    pytest.param("{left} versus {right}", id="versus"),
    pytest.param("{left} vs.{right}", id="compact-vs"),
    pytest.param("{left} vs vs {right}", id="repeated-vs"),
    pytest.param("{left} vs. vs. {right}", id="repeated-dotted-vs"),
    pytest.param("{left} versus versus {right}", id="repeated-versus"),
    pytest.param("{left} vs versus vs. {right}", id="mixed-separators"),
    pytest.param("vs {left} vs {right}", id="leading-vs"),
    pytest.param("vs. {left} vs.{right}", id="leading-dotted-vs"),
    pytest.param("versus {left} versus {right}", id="leading-versus"),
    pytest.param("vs vs. versus {left} versus vs. vs {right}", id="leading-and-repeated"),
])
def test_separator_tokens_preserve_entity_names(left, right, form, run_cli):
    topic = form.format(left=left, right=right)
    assert planner._comparison_entities(topic) == [left, right]
    payload = run_cli(topic)
    assert payload["comparison"] is True
    assert payload["entities"] == [left, right]
    assert [entry["report"]["topic"] for entry in payload["reports"]] == [left, right]
