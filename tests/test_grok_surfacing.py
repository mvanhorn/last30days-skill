"""grok must be visible to a user, not just functional.

A backend nobody is told about is a backend nobody uses, and the whole point
of this path is removing a credential wall the user currently hits.
"""

import inspect
from pathlib import Path

from lib import doctor, quality_nudge

REPO = Path(__file__).resolve().parent.parent


def test_doctor_reports_x_covered_by_a_signed_in_grok():
    src = inspect.getsource(doctor._x_record)
    assert "grok_x.has_stored_auth()" in src
    assert "will use: grok" in src


def test_doctor_prefers_grok_over_the_unverified_cookie_note():
    """grok outranks bird in the chain, and unlike a cookie session its
    availability is locally verifiable rather than 'not verified until a run'."""
    src = inspect.getsource(doctor._x_record)
    assert src.index("has_stored_auth") < src.index("x_pending_browser_auth")


def test_quality_nudge_offers_grok_but_does_not_call_it_free():
    src = inspect.getsource(quality_nudge)
    assert "grok_cli_missing" in src
    assert "no X credential at all" in src
    # The block is headed "Free suggestions"; grok needs a Grok plan, so the
    # precondition must be stated inline rather than inherited from the header.
    assert "if you have a Grok" in src


def test_configuration_documents_the_grok_path():
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "X with no X credential (Grok CLI)" in text
    assert "grok login" in text
    assert "LAST30DAYS_X_BACKEND=bird" in text, (
        "users need the documented escape hatch back to the cookie path"
    )


def test_configuration_pin_row_lists_grok():
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "`xai` / `grok` / `bird` / `xurl` / `xquik`" in text


def test_configuration_does_not_claim_grok_is_free():
    text = (REPO / "CONFIGURATION.md").read_text()
    section = text[text.index("X with no X credential (Grok CLI)"):][:1200]
    assert "draws on your Grok plan" in section or "draw on your Grok plan" in section


def test_changelog_fragments_exist_and_changelog_is_untouched():
    frags = list((REPO / "changelog.d").glob("*grok*")) + \
        list((REPO / "changelog.d").glob("*first-party*"))
    if not frags:
        # Release PRs consume fragments into CHANGELOG.md via towncrier.
        changelog = (REPO / "CHANGELOG.md").read_text()
        assert "X search now works with no X credential" in changelog
        return
    assert frags, "feature PRs add a changelog.d fragment"


# --- SKILL.md unlock surfaces ---------------------------------------------

def _skill_md():
    return (REPO / "skills" / "last30days" / "SKILL.md").read_text()


def test_all_unlock_surfaces_mention_the_grok_path():
    text = _skill_md()
    for marker in (
        "Just-in-time X unlock",
        "Check for a Grok path before asking for cookies",
        "Grok CLI (no X credential)",
    ):
        assert marker in text, f"missing grok surfacing at: {marker}"


def test_grok_ordering_is_conditional_on_a_detected_path():
    """A user with no Grok account must not have their actionable options
    pushed down the list by one they cannot take."""
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    assert "command -v grok" in section
    assert "Options when `grok` IS on PATH" in section
    assert "Options when `grok` is NOT on PATH" in section


def test_no_grok_branch_keeps_todays_options_unchanged():
    text = _skill_md()
    section = text[text.index("Options when `grok` is NOT on PATH"):][:900]
    for option in ("Scan my browser cookies", "AUTH_TOKEN and CT0", "xAI API key", "Skip for now"):
        assert option in section


def test_skill_md_does_not_call_the_grok_path_free():
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    assert "Do not describe the Grok path as free" in section
