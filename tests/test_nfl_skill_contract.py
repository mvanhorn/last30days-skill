"""SKILL.md must teach the model about every nfl30 flag and lane (a flag the
skill never mentions is a flag the invoking model will never pass)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "skills" / "nfl30" / "SKILL.md").read_text(encoding="utf-8")
FRONTMATTER = SKILL.split("---", 2)[1]


def test_frontmatter_describes_the_nfl_skill():
    assert re.search(r"^name: nfl30$", FRONTMATTER, re.M)
    assert "NFL" in FRONTMATTER and "beat writers" in FRONTMATTER and "Polymarket" in FRONTMATTER
    assert "nfl30 Packers vs Lions" in FRONTMATTER


def test_every_nfl_flag_is_in_the_preflight_table():
    table = SKILL.split("**Pre-Flight Checklist", 1)[1].split("**Checkpoint before running", 1)[0]
    for flag in ("--team {ABBR}", '--player "{Full Name}"', "--beat-writers off"):
        assert f"| `{flag}`" in table, flag


def test_nfl_sections_exist_and_are_ordered():
    order = [SKILL.index(h) for h in (
        "## CRITICAL: Parse User Intent",
        "**Class 6: NFL name collision",
        "### Section NFL: Resolve the Team",
        "### Section A: Resolve X Handles",
        "> **NFL SHORTCUT:**",
        "### NFL Synthesis (nfl30)",
        "### Prediction Markets (Polymarket)",
    )]
    assert order == sorted(order)


def test_game_is_not_a_comparison_and_default_window_is_seven_days():
    assert "A scheduled two-team matchup is ONE topic" in SKILL
    assert "not COMPARISON" in SKILL.lower() or "NOT COMPARISON" in SKILL
    assert "**7 days**" in SKILL


def test_source_map_and_registers_cover_new_lanes():
    assert "`team_official`→Team official" in SKILL and "`nfl_polymarket`→NFL markets" in SKILL
    assert "REGISTER = [default | exec | dev | creator | eli5 | fan | bettor]" in SKILL
    assert "- **fan** -" in SKILL and "- **bettor** -" in SKILL


def test_synthesis_rules_keep_trust_tiers_and_no_betting_advice():
    block = SKILL.split("### NFL Synthesis (nfl30)", 1)[1].split("### Prediction Markets (Polymarket)", 1)[0]
    for phrase in ("Official.", "Reported.", "Chatter.", "Never invent a quote", "Never give betting advice",
                   "@handle (Outlet)"):
        assert phrase in block, phrase


def test_every_nfl_cli_flag_in_the_engine_is_documented_in_configuration():
    config = (ROOT / "CONFIGURATION.md").read_text(encoding="utf-8")
    for flag in ("--team", "--player", "--beat-writers", "NFL30_BEAT_WRITERS", "beat_writers.json",
                 "team_official", "nfl_polymarket"):
        assert flag in config, flag
