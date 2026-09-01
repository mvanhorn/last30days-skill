"""grok is a FAIL-CLOSED backup after bird — not first place, not pin-only.

The unpinned auto chain is bird -> grok -> xai -> xurl -> xquik. grok is
auto-selected only when the CLI is signed in with an affirmatively-valid
session, so a leftover or expired ``~/.grok/auth.json`` never steals first
place over bird and never blocks the fall-through to xai. (This supersedes the
2026-08-14 grok-pin-only decision on membership only.)
"""

import inspect
from pathlib import Path
from unittest import mock

from lib import backends, doctor, grok_x, health, quality_nudge

REPO = Path(__file__).resolve().parent.parent

_BIRD_OFF = {
    "installed": False,
    "authenticated": False,
    "username": "",
    "can_install": False,
}


def _x_record(config, *, grok_status, grok_installed=True, bird_installed=False,
              pending_bird=False, run_probe=None):
    """Build doctor's X record with a mocked grok state (no real binary/store)."""
    if grok_status == grok_x.AUTH_OK:
        stored = (grok_x.AUTH_OK, "stored Grok credentials", None)
        auto_ok = True
    elif grok_status == grok_x.AUTH_EXPIRED:
        stored = (grok_x.AUTH_EXPIRED, "Grok session expired at 2020-01-01T00:00:00+00:00", None)
        auto_ok = False
    elif grok_status == grok_x.AUTH_ERROR:
        stored = (grok_x.AUTH_ERROR, "store unreadable", None)
        auto_ok = False
    else:
        stored = (grok_x.AUTH_MISSING, "no store", None)
        auto_ok = False

    ctxs = [
        mock.patch.object(grok_x, "binary_path",
                          return_value="/usr/bin/grok" if grok_installed else None),
        mock.patch.object(grok_x, "stored_auth_status", return_value=stored),
        mock.patch.object(grok_x, "has_stored_auth",
                          return_value=grok_installed and grok_status in (grok_x.AUTH_OK, grok_x.AUTH_EXPIRED)),
        mock.patch.object(grok_x, "is_auto_available", return_value=grok_installed and auto_ok),
        mock.patch("lib.backends.which",
                   side_effect=lambda name: "/usr/bin/grok" if (name == "grok" and grok_installed) else None),
        mock.patch("lib.bird_x.get_bird_status", return_value=dict(_BIRD_OFF)),
        mock.patch("lib.bird_x.is_bird_installed", return_value=bird_installed),
        mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None),
        mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
        mock.patch("lib.xurl_x.stored_auth_status", return_value=("missing", "no token store")),
        mock.patch.object(doctor.env, "x_pending_browser_auth", return_value=pending_bird),
    ]
    if run_probe is not None:
        ctxs.append(mock.patch.object(backends, "_run_probe", run_probe))
    with mock.patch.multiple("lib.health", probe_dependency=_ok_probe):
        for c in ctxs:
            c.start()
        try:
            return doctor._x_record(dict(config))
        finally:
            for c in reversed(ctxs):
                c.stop()


def _ok_probe(name, timeout=health.PROBE_TIMEOUT):
    return health.DependencyProbe(name=name, status=health.OK, detail=f"{name} 1.0.0")


# --- Doctor: fail-closed grok membership -----------------------------------


def test_doctor_x_record_mentions_fail_closed():
    """Doctor's _x_record documents grok's fail-closed handling, not opt-in."""
    src = inspect.getsource(doctor._x_record)
    assert "fail-closed" in src.lower()


def test_doctor_selects_grok_when_signed_in_unpinned():
    """grok signed in (valid) and nothing else -> doctor predicts grok."""
    record = _x_record({}, grok_status=grok_x.AUTH_OK)
    assert record["tier"] == "ok"
    assert record["status"] == health.OK
    assert record["active_backend"] == "grok"
    assert "grok" in record["note"].lower()


def test_doctor_dead_grok_only_is_unconfigured_not_broken():
    """Expired grok as the only backend -> unconfigured (COULD BE ON), not
    NOT WORKING; fail-closed skip, no hard grok prescription."""
    record = _x_record({}, grok_status=grok_x.AUTH_EXPIRED)
    assert record["tier"] == "off"
    assert record["status"] == "unconfigured"
    assert record["fix"] == ""
    assert "fail-closed" in record["note"].lower()


def test_doctor_broken_grok_store_only_is_unconfigured():
    """Unreadable grok store as the only backend -> unconfigured, not error."""
    record = _x_record({}, grok_status=grok_x.AUTH_ERROR)
    assert record["tier"] == "off"
    assert record["status"] == "unconfigured"
    assert record["fix"] == ""


def test_doctor_pending_bird_beats_signed_in_grok():
    """Pending browser cookies (bird is chain[0]) predict bird even when grok
    is signed in — bird wins at run time before grok."""
    record = _x_record({}, grok_status=grok_x.AUTH_OK, bird_installed=True, pending_bird=True)
    assert record["tier"] == "ok"
    assert "bird" in record["note"].lower()


def test_doctor_dead_grok_does_not_hide_xurl_error():
    """A non-grok auto backend that is configured-but-broken (xurl ERROR) is
    still surfaced; an expired grok does not mask it."""
    original = backends._run_probe

    def patched(spec, config):
        if spec.name == "xurl":
            return backends.BackendFinding(
                name="xurl", status=health.ERROR, detail="store unreadable",
                prescription="xurl auth oauth2 login",
                requires="xurl CLI installed + OAuth2 login",
            )
        return original(spec, config)

    record = _x_record({}, grok_status=grok_x.AUTH_EXPIRED, run_probe=patched)
    assert record["tier"] == "error"
    assert record["fix"]
    assert "xurl" in record["fix"].lower() or "oauth" in record["fix"].lower()


# --- Quality nudge (unchanged: grok is a suggestion, never called free) -----


def test_quality_nudge_offers_grok_but_does_not_call_it_free():
    src = inspect.getsource(quality_nudge)
    assert "grok_cli_missing" in src
    assert "no X credential at all" in src
    assert "if you have a Grok" in src


# --- CONFIGURATION.md ------------------------------------------------------


def test_configuration_documents_the_grok_path_as_fail_closed_backup():
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "Grok CLI (fail-closed backup)" in text
    assert "grok login" in text
    assert "LAST30DAYS_X_BACKEND=grok" in text


def test_configuration_pin_row_lists_grok_after_bird():
    """Pin row lists the chain order with grok second (after bird)."""
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "`bird` / `grok` / `xai` / `xurl` / `xquik`" in text


def test_configuration_does_not_claim_grok_is_free():
    text = (REPO / "CONFIGURATION.md").read_text()
    section = text[text.index("Grok CLI (fail-closed backup)"):][:1400]
    assert "draws on your Grok plan" in section or "draw on your Grok plan" in section


def test_configuration_documents_bird_first_chain():
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "bird first" in text.lower()


def test_configuration_documents_cookie_discovery_and_agentcookie():
    """The new X cookie sources (agentcookie, live Chrome CDP) are documented."""
    text = (REPO / "CONFIGURATION.md").read_text()
    assert "agentcookie" in text
    assert "AGENTCOOKIE" in text
    assert "CDP" in text or "DevTools" in text


def test_changelog_fragments_exist_and_changelog_is_untouched():
    frags = (
        list((REPO / "changelog.d").glob("*grok*"))
        + list((REPO / "changelog.d").glob("*bird*"))
        + list((REPO / "changelog.d").glob("*cookie*"))
        + list((REPO / "changelog.d").glob("*agentcookie*"))
        + list((REPO / "changelog.d").glob("*x-cookie*"))
    )
    if not frags:
        changelog = (REPO / "CHANGELOG.md").read_text()
        assert "X search now works with no X credential" in changelog or "bird first" in changelog.lower()
        return
    assert frags, "feature PRs add a changelog.d fragment"


# --- SKILL.md unlock surfaces ---------------------------------------------


def _skill_md():
    return (REPO / "skills" / "last30days" / "SKILL.md").read_text()


def test_skill_md_does_not_check_grok_first():
    """grok is a backup: SKILL.md must not check for grok before cookies."""
    text = _skill_md()
    assert "Check for a Grok path before asking for cookies" not in text


def test_skill_md_presents_grok_as_fail_closed_backup():
    text = _skill_md()
    assert "Grok CLI is a fail-closed backup" in text


def test_skill_md_manual_guide_mentions_agentcookie_and_live_chrome():
    """U4: the repair/first-run copy leads with cookie sources — agentcookie,
    then a signed-in Chrome left open — before grok and xAI."""
    text = _skill_md()
    section = text[text.index("Manual Setup Guide"):]
    section = section[:section.index("**Reddit")]
    assert "agentcookie" in section
    assert "Chrome" in section
    # Do not tell Grok Bot users to install Firefox as the default.
    assert "Do not assume Firefox" in section


def test_skill_md_just_in_time_unlock_defaults():
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    assert "Scan my browser cookies" in section
    assert "xAI API key" in section


def test_skill_md_does_not_call_the_grok_path_free():
    text = _skill_md()
    section = text[text.index("Just-in-time X unlock"):][:3000]
    assert "Do not describe the Grok path as free" in section or "Do not call it free" in section
