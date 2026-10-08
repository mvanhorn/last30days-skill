"""Contract tests for the Grok Bot host slice of SKILL.md (R4, R12, R16; AE9).

On a Grok Bot host the model-facing contract must drive X through the
official path only: the X connector lane first, the X API bearer or the xAI
key as backups, keys written only through the engine's ``setup --store-key``
path, and no browser-session step of any kind. These tests read SKILL.md as
text - the model's runtime contract - the way tests/test_onboarding_contract.py
and tests/test_codex_host_contract.py do, and slice the Grok Bot passages so a
word that is fine elsewhere (the cookie recipes for Linux / Mac mini) cannot
satisfy or fail an assertion here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.skill_contract import reference_text

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
CONFIGURATION = ROOT / "CONFIGURATION.md"
AGENTS_MD = ROOT / "AGENTS.md"

FLOW_HEADING = "### Grok Bot Prose Flow"
RECIPE_MARKER = "Grok Bot X recipe"
RECIPE_END = "**Step 1: Run the research script"

# R4 vocabulary: none of it may appear in the Grok Bot flow (case-insensitive).
FORBIDDEN = (
    "cookie",
    "box-chrome",
    "cdp",
    "auth_token",
    "ct0",
    "bird",
    "18800",
    "xquik",
    "grok login",
    "from_browser",
    "last30days_x_backend",
    "askuserquestion",
)

LANE_TAGS = ("topic", "from", "mention", "related")
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
KEY_TOKENS = ("X_BEARER_TOKEN", "XAI_API_KEY", "--x-posts", "envelope", ".json")

HEREDOC_RE = re.compile(r"<<-?\s*([^\s]+)")


def _text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _slice_between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    assert start != -1, f"missing marker: {start_marker!r}"
    end = text.find(end_marker, start + len(start_marker))
    assert end != -1, f"missing end marker after {start_marker!r}: {end_marker!r}"
    return text[start:end]


def _grok_flow(text: str) -> str:
    """The Grok Bot Prose Flow, from its heading to the next ### heading."""
    start = text.find(FLOW_HEADING)
    assert start != -1, f"missing {FLOW_HEADING!r}"
    match = re.search(r"^### ", text[start + len(FLOW_HEADING):], flags=re.MULTILINE)
    assert match is not None, "no ### heading follows the Grok Bot Prose Flow"
    return text[start : start + len(FLOW_HEADING) + match.start()]


def _recipe(text: str) -> str:
    assert RECIPE_MARKER in text, f"missing {RECIPE_MARKER!r}"
    return text[text.index(RECIPE_MARKER):]


def _guaranteed_band(text: str) -> str:
    return "\n".join(text.splitlines()[:420])


def _extras_passages(text: str) -> dict[str, str]:
    step0 = text
    assert "## Step 0: First-Run Setup Wizard" in step0
    modal = _slice_between(step0, "### Claude Code Modal Flow", "### Non-Modal Prose Flow")
    prose = _slice_between(step0, "### Non-Modal Prose Flow", FLOW_HEADING)
    manual = step0[step0.index("### Manual Setup Guide") :]
    modal_extras = _slice_between(modal, "**Extras-host X login", "**macOS Full Disk Access")
    prose_extras = _slice_between(prose, "**Extras hosts", "   - On **no**")
    manual_extras = _slice_between(manual, "**X on Linux / Mac mini (repair).**", "**Reddit (free")
    return {"modal": modal_extras, "prose": prose_extras, "manual": manual_extras}


def _forbidden_hits(slice_text: str) -> list[str]:
    lowered = slice_text.lower()
    return [word for word in FORBIDDEN if word in lowered]


class TestGrokBotProseFlow(unittest.TestCase):
    def setUp(self):
        self.text = reference_text("setup-wizard")
        self.flow = _grok_flow(self.text)

    def test_flow_is_the_third_step0_branch(self):
        step0 = self.text
        split = _slice_between(step0, "**Platform split", "### Claude Code Modal Flow")
        self.assertIn("Grok Bot Prose Flow", split)
        # Cursor stays a Non-Modal host; it is not routed to the Grok Bot flow.
        self.assertIn("Cursor", _slice_between(step0, "### Non-Modal Prose Flow", FLOW_HEADING))
        self.assertNotIn("Cursor", self.flow)

    def test_flow_has_no_r4_vocabulary(self):
        self.assertEqual([], _forbidden_hits(self.flow))

    def test_flow_names_the_official_contract(self):
        for token in (
            "LAST30DAYS_HOST=grok-bot",
            "LAST30DAYS_X_HOST_LANE=1",
            "X_BEARER_TOKEN",
            "XAI_API_KEY",
            "--x-posts",
            "search_posts_all",
            "generated_at",
            "window-unsupported",
            "setup --store-key",
            "about the last week",
            "SETUP_COMPLETE=true",
            "X_DECLINED=grok-bot",
        ):
            self.assertIn(token, self.flow, token)

    def test_flow_names_the_call_counts_and_lanes(self):
        self.assertRegex(self.flow, r"10\s*/\s*30\s*/\s*60")
        self.assertRegex(self.flow, r"\b8\b.*\b5\b.*\b3\b")
        for lane in LANE_TAGS:
            self.assertIn(f"`{lane}`", self.flow, lane)
        for field in ROW_FIELDS:
            self.assertIn(f"`{field}`", self.flow, field)

    def test_flow_names_the_x_for_grok_bot_plugin(self):
        """The connector is the marketplace "X for Grok Bot" plugin: the flow
        names it and keys the lane on its post-search tools, not on one
        tool name alone (search_posts_all stays as the example)."""
        self.assertIn('"X for Grok Bot"', self.flow)
        self.assertIn("search_posts_all", self.flow)
        rule = _guaranteed_band(_text())
        self.assertIn('"X for Grok Bot"', rule)

    def test_connector_step_precedes_bearer_offer(self):
        connector = self.flow.index("search_posts_all")
        bearer = self.flow.index("X_BEARER_TOKEN")
        self.assertLess(connector, bearer)

    def test_built_in_x_tools_come_before_the_plugin_and_need_no_setup(self):
        native = self.flow.index("namespace `x`")
        plugin = self.flow.index('"X for Grok Bot"')
        bearer = self.flow.index("X_BEARER_TOKEN")
        self.assertLess(native, plugin)
        self.assertLess(plugin, bearer)
        self.assertIn("nothing to configure", self.flow)
        self.assertIn("only when neither is present", self.flow)

    def test_lane_signal_is_exported_only_after_a_fetch_returned_posts(self):
        self.assertIn("only in an engine shell whose fetch returned posts", self.flow)

    def test_bearer_coverage_caveat_never_implies_parity(self):
        self.assertIn(
            "recent posts, about the last week, unless your X developer project has full-archive access",
            self.flow,
        )
        self.assertIn("X developer console", self.flow)
        self.assertIn("console.x.ai", self.flow)

    def test_key_persistence_only_through_engine_and_masked(self):
        self.assertIn("setup --store-key", self.flow)
        self.assertIn("=****", self.flow)
        self.assertIn("never echo the value back", self.flow)
        for line in self.flow.splitlines():
            if not re.search(r"\b(echo|printf)\b", line):
                continue
            writes = re.search(r"\b(echo|printf)\b[^\n]*(>>|>|\|)", line)
            if writes and any(token in line for token in KEY_TOKENS):
                self.fail(f"a shell write of a key or the envelope: {line.strip()!r}")

    def test_every_heredoc_in_flow_uses_single_quoted_delimiter(self):
        for delim in HEREDOC_RE.findall(self.flow):
            self.assertTrue(delim.startswith("'"), f"unquoted heredoc delimiter {delim!r}")

    def test_flow_declines_write_marker_and_has_no_modals(self):
        self.assertIn("X_DECLINED=grok-bot", self.flow)
        self.assertNotIn("AskUserQuestion", self.flow)


class TestGuaranteedLoadedRule(unittest.TestCase):
    def setUp(self):
        self.band = _guaranteed_band(_text())

    def test_rule_lives_in_the_guaranteed_loaded_band(self):
        self.assertIn("LAST30DAYS_HOST=grok-bot", self.band)
        self.assertIn("LAST30DAYS_X_HOST_LANE=1", self.band)
        self.assertIn("never place post text unquoted", self.band.lower())
        self.assertIn("CURSOR_AGENT", self.band)

    def test_rule_is_not_keyed_on_cursor_agent_alone(self):
        start = self.band.index("LAST30DAYS_HOST=grok-bot")
        rule = self.band[max(0, start - 600) : start + 1200]
        self.assertIn("CURSOR_AGENT", rule)
        self.assertRegex(rule, r"(?i)not .*CURSOR_AGENT.*alone|CURSOR_AGENT.*alone")


class TestConnectorRecipe(unittest.TestCase):
    def setUp(self):
        self.recipe = _recipe(reference_text("grok-bot-x"))

    def test_recipe_precedes_the_engine_command(self):
        text = reference_text("research-runbook")
        research = text[text.index("## Research Execution") :]
        self.assertLess(research.index("grok-bot-x.md"), research.index(RECIPE_END))
        self.assertIn("before", research[:research.index(RECIPE_END)].lower())

    def test_recipe_names_counts_window_and_status(self):
        for token in (
            "search_posts_all",
            "-is:retweet",
            "window-unsupported",
            "partial",
            "generated_at",
            "last30days-x-posts/1",
            "--x-posts",
            "x_posts",
            "--competitors-plan",
            "X via X connector",
            "LAW 9",
            "stderr",
        ):
            self.assertIn(token, self.recipe, token)
        self.assertRegex(self.recipe, r"10 .*30 .*60")
        for lane in LANE_TAGS:
            self.assertIn(f'"lane": "{lane}"', self.recipe, lane)
        for field in ROW_FIELDS:
            self.assertIn(f'"{field}"', self.recipe, field)

    def test_recipe_forbids_raw_tool_output_in_error(self):
        self.assertIn("never raw tool output", self.recipe)
        for category in ("credits", "not-connected", "unavailable"):
            self.assertIn(category, self.recipe)

    def test_recipe_heredocs_are_single_quoted_and_no_echo_writes(self):
        for delim in HEREDOC_RE.findall(self.recipe):
            self.assertTrue(delim.startswith("'"), f"unquoted heredoc delimiter {delim!r}")
        for line in self.recipe.splitlines():
            if re.search(r"\b(echo|printf)\b[^\n]*(>>|>|\|)", line) and any(
                token in line for token in KEY_TOKENS
            ):
                self.fail(f"a shell write of the envelope: {line.strip()!r}")

    def test_recipe_heredoc_sentinel_is_per_run_and_json_is_one_line(self):
        """Post text is attacker-controlled: a fixed public sentinel could be
        echoed by a post to close the heredoc early. The recipe demands a
        per-run nonce in both sentinel lines and single-line JSON."""
        delims = HEREDOC_RE.findall(self.recipe)
        self.assertTrue(any("X_POSTS_EOF_{X_POSTS_NONCE}" in d for d in delims), delims)
        self.assertNotIn("<<'X_POSTS_EOF'", self.recipe)
        self.assertIn("\nX_POSTS_EOF_{X_POSTS_NONCE}\n", self.recipe)
        self.assertIn("ONE line", self.recipe)
        self.assertIn("random", self.recipe)

    def test_recipe_has_no_r4_vocabulary(self):
        self.assertEqual([], _forbidden_hits(self.recipe))

    def test_recipe_names_both_host_lanes_by_source_before_the_engine_chain(self):
        plugin = self.recipe.index('"X for Grok Bot"')
        native = self.recipe.index("namespace `x`")
        bearer = self.recipe.index("X_BEARER_TOKEN")
        self.assertLess(native, plugin)
        self.assertLess(plugin, bearer)
        self.assertIn("never by its name", self.recipe)
        for token in ('"provider": "x-native"', '"x-connector"', "X via Grok Bot X"):
            self.assertIn(token, self.recipe, token)

    def test_recipe_pages_recency_to_the_depth_count(self):
        for token in ("`recency`", "next_token", "max_results", "ONE envelope call", "note_tweet"):
            self.assertIn(token, self.recipe, token)
        for metric in ("like_count", "retweet_count", "reply_count", "quote_count"):
            self.assertIn(metric, self.recipe, metric)

    def test_recipe_samples_popular_posts_across_the_whole_window(self):
        self.assertIn("popular pass", self.recipe)
        popular = self.recipe[self.recipe.index("popular pass"):]
        popular = popular[: popular.index("\n")]
        self.assertIn("first topic query only", popular)
        self.assertIn("10 equal slices", popular)
        self.assertIn("`relevancy`", popular)
        self.assertIn("ONE envelope call", popular)

    def test_recipe_keeps_the_envelope_inside_engine_limits(self):
        self.assertIn("500 posts per call", self.recipe)
        self.assertIn("1,000 posts and 20 calls in total", self.recipe)
        self.assertIn("drop discovered-author calls first", self.recipe)

    def test_comparison_uses_envelopes_only_when_every_entity_has_posts(self):
        self.assertIn("only when every entity's fetch returned posts", self.recipe)

    def test_comparison_paces_tool_calls_under_the_per_minute_limit(self):
        self.assertIn("no minute holds more than 30 tool calls", self.recipe)
        self.assertIn("popular pass for the main entity only", self.recipe)

    def test_recipe_gives_discovered_authors_full_handle_lanes(self):
        authors = self.recipe[self.recipe.index("**Discovered authors.**"):]
        authors = authors[: authors.index("\n   - ")]
        self.assertIn("`from` call", authors)
        self.assertIn("8 posts", authors)
        self.assertIn("`mention` call", authors)
        self.assertIn("5 posts", authors)
        self.assertIn("`--x-related`", authors)

    def test_recipe_exports_the_lane_only_after_posts_came_back(self):
        self.assertIn("only when a lane returned posts", self.recipe)
        self.assertIn("unset LAST30DAYS_X_HOST_LANE", self.recipe)


class TestExtrasPassagesRescoped(unittest.TestCase):
    def test_extras_passages_no_longer_name_grok_bot(self):
        for name, passage in _extras_passages(reference_text("setup-wizard")).items():
            self.assertNotIn("Grok Bot", passage, f"{name} extras passage still names Grok Bot")
            self.assertIn("grok-bot", passage, f"{name} extras passage does not exclude the grok-bot host")

    def test_manual_repair_heading_rescoped(self):
        text = reference_text("setup-wizard")
        self.assertIn("**X on Linux / Mac mini (repair).**", text)
        self.assertNotIn("X on Linux / Grok Bot / Mac mini", text)


class TestManualSetupGuide(unittest.TestCase):
    def setUp(self):
        step0 = reference_text("setup-wizard")
        self.manual = step0[step0.index("### Manual Setup Guide") :]

    def test_bearer_bullet_comes_first_in_x_section(self):
        x_section = _slice_between(self.manual, "**X/Twitter (pick one", "**X on Linux / Mac mini (repair).**")
        bullets = [line for line in x_section.splitlines() if line.startswith("- ")]
        self.assertTrue(bullets, "no X bullets in the Manual Setup Guide")
        self.assertIn("X_BEARER_TOKEN", bullets[0])
        self.assertIn("about a week", bullets[0])

    def test_grok_bot_repair_paragraph_is_official_only(self):
        para = _slice_between(self.manual, "**X on a Grok Bot (repair).**", "**X on Linux / Mac mini (repair).**")
        self.assertEqual([], _forbidden_hits(para))
        for token in ("connect X", "X_BEARER_TOKEN", "about the last week", "XAI_API_KEY", "top up"):
            self.assertIn(token, para, token)


class TestSecurityAndFrontmatter(unittest.TestCase):
    def test_frontmatter_optional_env_lists_bearer(self):
        text = _text()
        frontmatter = text[: text.index("---", 3)]
        self.assertIn("- X_BEARER_TOKEN", frontmatter)

    def test_security_section_lists_x_api_and_envelope(self):
        text = _text()
        security = text[text.index("## Security & Permissions") :]
        self.assertIn("api.x.com", security)
        self.assertIn("--x-posts", security)
        self.assertIn("X connector", security)
        self.assertIn("X_BEARER_TOKEN", security)
        self.assertIn("sent only in the Authorization header", security)
        self.assertIn("no X backend is called", security)


class TestConfigurationGrokBotSubsection(unittest.TestCase):
    def test_configuration_grok_bot_subsection_has_no_r4_vocabulary(self):
        text = CONFIGURATION.read_text(encoding="utf-8")
        match = re.search(r"^(#{2,4}) [^\n]*Grok Bot[^\n]*$", text, flags=re.MULTILINE)
        if match is None:
            self.skipTest("CONFIGURATION.md has no Grok Bot subsection yet")
        level = len(match.group(1))
        rest = text[match.end() :]
        nxt = re.search(rf"^#{{1,{level}}} ", rest, flags=re.MULTILINE)
        section = rest if nxt is None else rest[: nxt.start()]
        self.assertEqual([], _forbidden_hits(section))


class TestAgentsMd(unittest.TestCase):
    def test_agents_md_names_three_branches(self):
        text = AGENTS_MD.read_text(encoding="utf-8")
        self.assertIn("Step 0 has THREE branches", text)
        self.assertIn("Grok Bot Prose Flow", text)
        self.assertNotIn("Step 0 has TWO branches", text)


if __name__ == "__main__":
    unittest.main()
