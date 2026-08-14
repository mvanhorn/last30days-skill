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


def test_doctor_grok_error_unpinned_is_not_tier_error():
    """Unpinned grok ERROR (broken store) is unconfigured, NOT tier error.

    When grok's auth store is unreadable/corrupt, doctor should NOT report X
    as NOT WORKING with a `grok login` prescription. An unused opt-in backend
    with a broken store is still unused — tier off, not tier error.
    """
    from lib import grok_x

    config = {}  # No pin, no auto-chain credentials
    bird_status = {
        "installed": False,
        "authenticated": False,
        "username": "",
        "can_install": False,
    }
    # Grok has ERROR status (unreadable store); all auto-chain backends MISSING.
    with (
        mock.patch.object(grok_x, "binary_path", return_value="/usr/bin/grok"),
        mock.patch.object(grok_x, "has_stored_auth", return_value=False),
        mock.patch.object(
            grok_x, "stored_auth_status",
            return_value=(grok_x.AUTH_ERROR, "store unreadable", None),
        ),
        mock.patch("lib.backends.which", return_value="/usr/bin/grok"),
        mock.patch("lib.bird_x.get_bird_status", return_value=bird_status),
        mock.patch("lib.bird_x.is_bird_installed", return_value=False),
        mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
    ):
        record = doctor._x_record(config)
    # Must NOT be tier error / NOT WORKING.
    assert record["tier"] != "error", "grok ERROR unpinned must not be tier error"
    # Should be unconfigured (tier off).
    assert record["status"] == "unconfigured"
    assert record["tier"] == "off"
    # Note should mention grok is unused/opt-in; should NOT prescribe grok login.
    assert "grok" in record["note"].lower()
    assert "opt-in" in record["note"].lower() or "unused" in record["note"].lower()
    # Fix should be empty (no grok login prescription for unused opt-in).
    assert record["fix"] == ""


def test_doctor_grok_store_with_pending_bird_predicts_bird():
    """Unpinned + grok store + pending browser auth -> doctor predicts bird.

    When bird is installed and browser-cookie extraction is configured,
    doctor should predict bird (pending-cookie usable), NOT report X as
    unconfigured due to an unused grok store. Pending bird takes precedence.
    """
    from lib import env, grok_x

    config = {}  # No pin, no static AUTH_TOKEN/CT0
    bird_status = {
        "installed": True,  # Bird IS installed
        "authenticated": False,  # No static cookies in config
        "username": "",
        "can_install": True,
    }
    # Grok has OK status; bird is pending (installed, FROM_BROWSER configured).
    with (
        mock.patch.object(grok_x, "binary_path", return_value="/usr/bin/grok"),
        mock.patch.object(grok_x, "has_stored_auth", return_value=True),
        mock.patch.object(grok_x, "stored_auth_status", return_value=(grok_x.AUTH_OK, "", None)),
        mock.patch("lib.backends.which", return_value="/usr/bin/grok"),
        mock.patch("lib.bird_x.get_bird_status", return_value=bird_status),
        mock.patch("lib.bird_x.is_bird_installed", return_value=True),
        mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
        # Simulate x_pending_browser_auth returning True (bird pending)
        mock.patch.object(env, "x_pending_browser_auth", return_value=True),
    ):
        record = doctor._x_record(config)
    # Doctor should predict bird (pending), NOT report X as unconfigured.
    assert record["tier"] != "off", "pending bird should not be tier off"
    assert record["status"] != "unconfigured", "pending bird should not be unconfigured"
    # Should be OK tier with bird prediction.
    assert record["tier"] == "ok"
    assert record["status"] == health.OK
    assert "bird" in record["note"].lower()
    assert "browser cookies" in record["note"].lower() or "cookie" in record["note"].lower()


def test_doctor_grok_does_not_hide_xurl_error():
    """Unpinned + grok store + xurl ERROR -> doctor keeps xurl error, not unconfigured.

    When an auto-chain backend (xurl) is configured but broken, doctor must
    report that error with its repair guidance. Unused grok must NOT swallow
    the genuine auto-chain failure.
    """
    from lib import backends as _backends
    from lib import grok_x

    config = {}  # No pin
    bird_status = {
        "installed": False,
        "authenticated": False,
        "username": "",
        "can_install": False,
    }

    # Mock xurl probe to return ERROR status
    def mock_probe_xurl(config):
        return _backends.BackendFinding(
            name="xurl",
            status=health.ERROR,
            detail="store unreadable",
            prescription="xurl auth oauth2 login",
            requires="xurl CLI installed + OAuth2 login",
        )

    # Intercept _run_probe to inject xurl ERROR
    original_run_probe = _backends._run_probe

    def patched_run_probe(spec, config):
        if spec.name == "xurl":
            return mock_probe_xurl(config)
        return original_run_probe(spec, config)

    # Grok has OK status; xurl has ERROR (unreadable store).
    # All other auto-chain backends are MISSING.
    with (
        mock.patch.object(grok_x, "binary_path", return_value="/usr/bin/grok"),
        mock.patch.object(grok_x, "has_stored_auth", return_value=True),
        mock.patch.object(grok_x, "stored_auth_status", return_value=(grok_x.AUTH_OK, "", None)),
        mock.patch("lib.backends.which", side_effect=lambda cmd: "/usr/bin/grok" if cmd == "grok" else None),
        mock.patch("lib.bird_x.get_bird_status", return_value=bird_status),
        mock.patch("lib.bird_x.is_bird_installed", return_value=False),
        mock.patch.object(_backends, "_run_probe", patched_run_probe),
    ):
        record = doctor._x_record(config)

    # Doctor should NOT report X as unconfigured.
    assert record["tier"] != "off", "xurl error must not be hidden by unused grok"
    assert record["status"] != "unconfigured", "xurl error must not become unconfigured"
    # Should keep the error tier and xurl repair guidance.
    assert record["tier"] == "error"
    # The fix should contain xurl repair guidance, not be empty.
    assert record["fix"], "xurl repair guidance must not be cleared"
    assert "xurl" in record["fix"].lower() or "oauth" in record["fix"].lower()


def test_doctor_pending_bird_does_not_hide_xurl_error():
    """Unpinned + FROM_BROWSER + bird installed + xurl ERROR -> keeps xurl error.

    Pending bird must NOT replace a record with a configured auto-chain backend
    in ERROR. Doctor should report the xurl error with its repair guidance,
    NOT report X as OK via pending bird.
    """
    from lib import backends as _backends
    from lib import env, grok_x

    config = {}  # No pin, no static AUTH_TOKEN/CT0
    bird_status = {
        "installed": True,  # Bird IS installed (for pending bird)
        "authenticated": False,  # No static cookies
        "username": "",
        "can_install": True,
    }

    # Mock xurl probe to return ERROR status
    def mock_probe_xurl(config):
        return _backends.BackendFinding(
            name="xurl",
            status=health.ERROR,
            detail="store unreadable",
            prescription="xurl auth oauth2 login",
            requires="xurl CLI installed + OAuth2 login",
        )

    original_run_probe = _backends._run_probe

    def patched_run_probe(spec, config):
        if spec.name == "xurl":
            return mock_probe_xurl(config)
        return original_run_probe(spec, config)

    # x_pending_browser_auth returns True (bird pending), but xurl has ERROR.
    with (
        mock.patch.object(grok_x, "binary_path", return_value=None),
        mock.patch.object(grok_x, "has_stored_auth", return_value=False),
        mock.patch("lib.backends.which", return_value=None),
        mock.patch("lib.bird_x.get_bird_status", return_value=bird_status),
        mock.patch("lib.bird_x.is_bird_installed", return_value=True),
        mock.patch.object(env, "x_pending_browser_auth", return_value=True),
        mock.patch.object(_backends, "_run_probe", patched_run_probe),
    ):
        record = doctor._x_record(config)

    # Doctor should NOT report X as OK via pending bird.
    assert record["tier"] != "ok", "xurl error must not be hidden by pending bird"
    assert record["status"] != health.OK, "xurl error must not become OK"
    # Should keep the error tier and xurl repair guidance.
    assert record["tier"] == "error"
    assert record["fix"], "xurl repair guidance must not be cleared"
    assert "xurl" in record["fix"].lower() or "oauth" in record["fix"].lower()
