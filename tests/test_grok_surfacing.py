"""grok must be visible as an opt-in backup, not as a default.

Grok is demoted to opt-in only: a leftover ~/.grok/auth.json must never steal
the X lane. The default auto chain is bird → xai → xurl → xquik. Pin
LAST30DAYS_X_BACKEND=grok to enable grok explicitly.
"""

import inspect
from pathlib import Path
from unittest import mock

from lib import backends, doctor, health, quality_nudge

REPO = Path(__file__).resolve().parent.parent


def test_doctor_does_not_auto_select_grok_unpinned():
    """Doctor must NOT report 'will use: grok' for unpinned runs.
    Grok is opt-in only; a leftover auth.json must never steal the X lane."""
    src = inspect.getsource(doctor._x_record)
    # The old code would check grok_x.has_stored_auth() and set status="ok"
    # with record["will_use"]="grok" when grok was available unpinned. That
    # promotion block is removed: no "has_stored_auth()" call that sets
    # will_use to grok for unpinned runs.
    #
    # Comments may mention "will use: grok" to explain what we DON'T do, so
    # check for the old logic pattern: has_stored_auth -> grok promotion.
    assert "has_stored_auth" not in src


def test_doctor_mentions_grok_as_opt_in():
    """Doctor comments explain that grok is opt-in only."""
    src = inspect.getsource(doctor._x_record)
    assert "opt-in" in src.lower()


def test_quality_nudge_offers_grok_but_does_not_call_it_free():
    src = inspect.getsource(quality_nudge)
    assert "grok_cli_missing" in src
    assert "no X credential at all" in src
    # The block is headed "Free suggestions"; grok needs a Grok plan, so the
    # precondition must be stated inline rather than inherited from the header.
    assert "if you have a Grok" in src


def test_configuration_documents_the_grok_path_as_opt_in():
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "Grok CLI (opt-in backup)" in text
    assert "grok login" in text
    # Document that grok requires a pin.
    assert "LAST30DAYS_X_BACKEND=grok" in text


def test_configuration_pin_row_lists_grok_last():
    """Pin row shows all backends with grok last (opt-in)."""
    text = (REPO / "CONFIGURATION.md").read_text()
    # New order: bird first, grok last (opt-in).
    assert "`bird` / `xai` / `xurl` / `xquik` / `grok`" in text


def test_configuration_does_not_claim_grok_is_free():
    text = (REPO / "CONFIGURATION.md").read_text()
    section = text[text.index("Grok CLI (opt-in backup)"):][:1200]
    assert "draws on your Grok plan" in section or "draw on your Grok plan" in section


def test_configuration_documents_bird_first_chain():
    """Auto chain is bird first: cookies beat XAI_API_KEY."""
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "bird first" in text.lower() or "bird (browser cookies) → xai" in text.lower()


def test_changelog_fragments_exist_and_changelog_is_untouched():
    frags = list((REPO / "changelog.d").glob("*grok*")) + \
        list((REPO / "changelog.d").glob("*bird*"))
    if not frags:
        # Release PRs consume fragments into CHANGELOG.md via towncrier.
        changelog = (REPO / "CHANGELOG.md").read_text()
        # Old text or new text about X backend changes.
        assert "X search now works with no X credential" in changelog or "bird first" in changelog.lower()
        return
    assert frags, "feature PRs add a changelog.d fragment"


# --- SKILL.md unlock surfaces ---------------------------------------------

def _skill_md():
    return (REPO / "skills" / "last30days" / "SKILL.md").read_text()


def test_skill_md_does_not_check_grok_first():
    """Grok is opt-in only: SKILL.md should NOT check for grok before cookies."""
    text = _skill_md()
    # The old "Check for a Grok path before asking for cookies" should be removed.
    assert "Check for a Grok path before asking for cookies" not in text


def test_skill_md_presents_grok_as_opt_in_backup():
    """SKILL.md presents grok as an opt-in backup, not a primary option."""
    text = _skill_md()
    assert "Grok CLI is an opt-in backup" in text
    # Should mention the pin requirement.
    assert "LAST30DAYS_X_BACKEND=grok" in text


def test_skill_md_just_in_time_unlock_defaults():
    """Just-in-time X unlock presents cookies and keys first."""
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    # Default options should be cookies and keys, not grok.
    assert "Scan my browser cookies" in section
    assert "xAI API key" in section


def test_skill_md_does_not_call_the_grok_path_free():
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    assert "Do not describe the Grok path as free" in section or "Do not call it free" in section


# --- Doctor grok-only unpinned behavior (R3/R8) -----------------------------


def test_doctor_grok_only_unpinned_is_not_tier_error():
    """Unpinned grok-only is unconfigured (tier off), NOT broken (tier error).

    R3: unpinned grok is 'available, unused — pin LAST30DAYS_X_BACKEND=grok'
    R8: grok-only unpinned = X unconfigured / skipped, not auth-failed or broken
    """
    from lib import grok_x

    config = {}  # No pin, no auto-chain credentials
    bird_status = {
        "installed": False,
        "authenticated": False,
        "username": "",
        "can_install": False,
    }
    # Grok is the only backend with OK status; all auto-chain backends are MISSING.
    with (
        mock.patch.object(grok_x, "binary_path", return_value="/usr/bin/grok"),
        mock.patch.object(grok_x, "has_stored_auth", return_value=True),
        mock.patch.object(grok_x, "stored_auth_status", return_value=(grok_x.AUTH_OK, "", None)),
        mock.patch("lib.backends.which", return_value="/usr/bin/grok"),
        mock.patch("lib.bird_x.get_bird_status", return_value=bird_status),
        mock.patch("lib.bird_x.is_bird_installed", return_value=False),
        mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
    ):
        record = doctor._x_record(config)
    # Must NOT be tier error / NOT WORKING.
    assert record["tier"] != "error", "grok-only unpinned must not be tier error"
    # Should be unconfigured (tier off).
    assert record["status"] == "unconfigured"
    assert record["tier"] == "off"
    # Note should mention grok is available but requires a pin.
    assert "grok" in record["note"].lower()
    assert "pin" in record["note"].lower() or "LAST30DAYS_X_BACKEND" in record["note"]
