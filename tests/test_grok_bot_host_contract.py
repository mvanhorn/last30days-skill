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

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
CONFIGURATION = ROOT / "CONFIGURATION.md"
AGENTS_MD = ROOT / "AGENTS.md"

FLOW_HEADING = "### Grok Bot Prose Flow"
RECIPE_MARKER = "X connector recipe"
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
    research = text[text.index("## Research Execution") :]
    return _slice_between(research, RECIPE_MARKER, RECIPE_END)


def _guaranteed_band(text: str) -> str:
    return "\n".join(text.splitlines()[:420])


def _extras_passages(text: str) -> dict[str, str]:
    step0 = _slice_between(text, "## Step 0: First-Run Setup Wizard", "## CRITICAL: Parse User Intent")
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
        self.text = _text()
        self.flow = _grok_flow(self.text)

    def test_flow_is_the_third_step0_branch(self):
        step0 = _slice_between(
            self.text, "## Step 0: First-Run Setup Wizard", "## CRITICAL: Parse User Intent"
        )
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
            "--preflight",
        ):
            self.assertIn(token, self.flow, token)

    def test_flow_names_the_call_counts_and_lanes(self):
        self.assertRegex(self.flow, r"10\s*/\s*30\s*/\s*60")
        self.assertRegex(self.flow, r"\b8\b.*\b5\b.*\b3\b")
        for lane in LANE_TAGS:
            self.assertIn(f"`{lane}`", self.flow, lane)
        for field in ROW_FIELDS:
            self.assertIn(f"`{field}`", self.flow, field)

    def test_connector_step_precedes_bearer_offer(self):
        connector = self.flow.index("search_posts_all")
        bearer = self.flow.index("X_BEARER_TOKEN")
        self.assertLess(connector, bearer)

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
        self.assertIn("never place post text unquoted", self.band)
        self.assertIn("CURSOR_AGENT", self.band)

    def test_rule_is_not_keyed_on_cursor_agent_alone(self):
        start = self.band.index("LAST30DAYS_HOST=grok-bot")
        rule = self.band[max(0, start - 600) : start + 1200]
        self.assertIn("CURSOR_AGENT", rule)
        self.assertRegex(rule, r"(?i)not .*CURSOR_AGENT.*alone|CURSOR_AGENT.*alone")


class TestConnectorRecipe(unittest.TestCase):
    def setUp(self):
        self.recipe = _recipe(_text())

    def test_recipe_precedes_the_engine_command(self):
        text = _text()
        research = text[text.index("## Research Execution") :]
        self.assertLess(research.index(RECIPE_MARKER), research.index(RECIPE_END))

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

    def test_recipe_has_no_r4_vocabulary(self):
        self.assertEqual([], _forbidden_hits(self.recipe))


class TestExtrasPassagesRescoped(unittest.TestCase):
    def test_extras_passages_no_longer_name_grok_bot(self):
        for name, passage in _extras_passages(_text()).items():
            self.assertNotIn("Grok Bot", passage, f"{name} extras passage still names Grok Bot")
            self.assertIn("grok-bot", passage, f"{name} extras passage does not exclude the grok-bot host")

    def test_manual_repair_heading_rescoped(self):
        text = _text()
        self.assertIn("**X on Linux / Mac mini (repair).**", text)
        self.assertNotIn("X on Linux / Grok Bot / Mac mini", text)


class TestManualSetupGuide(unittest.TestCase):
    def setUp(self):
        text = _text()
        step0 = _slice_between(text, "## Step 0: First-Run Setup Wizard", "## CRITICAL: Parse User Intent")
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
        overview = _slice_between(text, "**Permissions overview:**", "Research ANY topic")
        self.assertIn("api.x.com", overview)


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
