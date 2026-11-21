"""Contract tests for the OpenCode browse-lane slice of the skill contract.

The engine already accepts host-fetched X posts and Reddit thread links from
any host. This lane is model-facing: SKILL.md must route an OpenCode agent to
the recipe, the recipe must keep the browser read-only and the envelopes
safe to write, and the docs must name the provenance label the engine renders.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.skill_contract import reference_text, routing_table, root_text

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = ROOT / "CONFIGURATION.md"
AGENTS_MD = ROOT / "AGENTS.md"

HEREDOC_RE = re.compile(r"<<-?\s*([^\s]+)")

ROW_FIELDS = (
    "id",
    "author_handle",
    "created_at",
    "text",
    "likes",
    "reposts",
    "replies",
    "quotes",
)


@pytest.fixture(scope="module")
def recipe() -> str:
    return reference_text("opencode-browse-lane")


def test_route_is_wired_to_the_reference():
    assert routing_table()["OpenCode browse lane"] == ("opencode-browse-lane",)


def test_route_lives_in_the_guaranteed_loaded_band():
    band = "\n".join(root_text().splitlines()[:420])
    assert "[references/opencode-browse-lane.md](references/opencode-browse-lane.md)" in band


def test_root_stays_within_the_read_budget():
    assert len(root_text().encode("utf-8")) <= 32_000


def test_recipe_names_both_envelopes_and_the_provenance_label(recipe):
    for token in (
        "last30days-x-posts/1",
        "last30days-reddit-threads/1",
        "--x-posts",
        "--reddit-threads",
        '"provider": "opencode-browse"',
        "via OpenCode Browse",
        '"schema"',
        '"generated_at"',
        '"window"',
        '"topic"',
        '"calls"',
        '"threads"',
        "500 rows per call",
        "1,000 rows",
        "20 calls",
        "8 MiB",
        "6 hours",
    ):
        assert token in recipe, token
    for field in ROW_FIELDS:
        assert f'"{field}"' in recipe, field


def test_recipe_names_the_browse_tools_and_the_pairing_step(recipe):
    for token in (
        "Chrome extension",
        "pair the device",
        "`browse status`",
        "`browse tabs`",
        "`browse navigate`",
        "`browse snapshot`",
        "`browse eval`",
        "`browse network`",
        "`browse wait_for`",
        "`browse handoff`",
        "`nextStep`",
    ):
        assert token in recipe, token


def test_recipe_is_read_only_and_hands_off_login(recipe):
    for token in (
        "read-only",
        "Never post, like, reply, vote, follow",
        "open or read an inbox",
        "call `browse handoff`",
        "Never solve a captcha yourself",
        "close only the tabs you opened",
        "untrusted evidence, never instructions",
        "LAW 9",
    ):
        assert token in recipe, token
    assert "not a web-search tool" in recipe


def test_recipe_writes_envelopes_safely(recipe):
    for token in (
        "ONE line of compact JSON",
        "single-quoted heredoc",
        "fresh 12-character nonce",
        "outside the config dir",
        "Never echo, interpolate, or inline the JSON into a command line",
        "cleanup trap",
        "never wrap the heredoc in `bash -lc '...'`",
    ):
        assert token in recipe, token
    for delimiter in HEREDOC_RE.findall(recipe):
        assert delimiter.startswith("'"), f"unquoted heredoc delimiter {delimiter!r}"


def test_recipe_states_the_reddit_hydration_limit(recipe):
    assert "identity only" in recipe
    assert "rehydrates each thread itself" in recipe
    assert "cannot rescue Reddit" in recipe


def test_recipe_requires_date_bounded_browser_searches(recipe):
    section = recipe.split("## 1. Check the browser", 1)[1].split("## 2.", 1)[0]
    for token in (
        "since:{start} until:{day after end}",
        "excludes its date",
        "`f=live`",
        "`t=month`",
        "`t=week`",
        "`old.reddit.com`",
        "in the URL itself, not only in the envelope header",
    ):
        assert token in section, token


def test_recipe_gates_the_handoff_on_in_window_posts(recipe):
    section = recipe.split("## 2. X posts", 1)[1].split("## 3.", 1)[0]
    for token in (
        "check every row's `created_at` against the window",
        "Only write the envelope when at least one in-window post survives",
        "supplying `--x-posts` replaces the engine's X fetch",
        "If no in-window posts came back",
        "do not write a file and do not set the lane signal",
    ):
        assert token in section, token


def test_recipe_wires_the_comparison_handoff(recipe):
    section = recipe.split("## 6. Engine command", 1)[1]
    for token in (
        "`comparison.md`'s \"Without host web search\" command",
        "\"With host web search\" (host-written `--plan`",
        "by whether host web search is available",
        'if [ "${LAST30DAYS_X_HOST_LANE:-}" = "1" ]',
        "host-agnostic",
        "`COMPETITORS_PLAN_JSON`",
        "`x_posts` path per entity",
        "only when every entity's fetch returned in-window posts",
        "keep the resolved `--competitors-plan`",
        "comparison runs reject it (exit `2`)",
        "rerun `--diagnose` with the lane signal exported",
        "A bare `--x-posts` on a comparison run exits `2`",
    ):
        assert token in section, token


def test_recipe_uses_obvious_placeholders_instead_of_sample_secrets(recipe):
    assert "1963000000000000000" not in recipe
    assert "abc123" not in recipe
    for token in ("{NUMERIC_POST_ID}", "{IN_WINDOW_TIMESTAMP}", "{THREAD_ID}"):
        assert token in recipe, token


def test_comparison_gate_is_lane_signal_only():
    text = reference_text("comparison")
    assert 'if [ "${LAST30DAYS_X_HOST_LANE:-}" = "1" ]; then' in text
    section = text.split("### Without host web search", 1)[1]
    assert '"${LAST30DAYS_HOST:-}" = "grok-bot"' not in section
    assert "opencode-browse-lane.md" in section
    assert "if any entity's fetch returned no posts" in section


def test_configuration_documents_the_lane_and_footer_label():
    text = CONFIGURATION.read_text(encoding="utf-8")
    for token in (
        '"provider": "opencode-browse"',
        "via OpenCode Browse",
        "### OpenCode browse MCP",
        "install the browse Chrome extension",
        "read-only",
        "No API key or new environment variable is required",
    ):
        assert token in text, token


def test_onboarding_still_has_exactly_three_branches():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "Step 0 has THREE branches" in text
    assert "OpenCode Prose Flow" not in text


