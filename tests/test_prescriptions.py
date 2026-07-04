"""Tests for the fix-prescription registry (doctor plan U3, KTD 7).

One registry maps (source, failure mode) to its remediation in BOTH
natural-language and direct-CLI forms. Consumers: the doctor command (U4)
and lib/quality_nudge.py, which builds its fix text from the same entries
so the two surfaces cannot drift.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import health, prescriptions


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGURATION_MD = REPO_ROOT / "CONFIGURATION.md"

# A CLI fix must start with a runnable token: the engine invocation, an env
# assignment (optionally exported), or a documented binary name.
RUNNABLE = re.compile(
    r"^(?:python3 \S*last30days\.py\b"
    r"|[A-Z][A-Z0-9_]*="
    r"|export [A-Z][A-Z0-9_]*="
    r"|(?:brew|pipx|pip|scoop|npx|npm|xurl|yt-dlp|docker) )"
)

# The seed failure inventory from the plan (U3 approach section).
SEED_INVENTORY = {
    ("x", "cookies_missing"),
    ("x", "cookies_expired"),
    ("scrapecreators", "key_missing"),
    ("bluesky", "app_password_missing"),
    ("youtube", "transcription_key_missing"),
    ("digg", "pp_cli_missing"),
    ("digg", "pp_cli_off_path"),
    ("digg", "pp_cli_broken"),
    ("youtube", "ytdlp_missing"),
    ("youtube", "ytdlp_stale"),
    ("youtube", "ytdlp_broken"),
    ("truthsocial", "token_missing"),
    ("xiaohongshu", "service_unreachable"),
}


def _configuration_md_slugs():
    """GitHub-style anchor slugs for every CONFIGURATION.md heading."""
    slugs = set()
    for line in CONFIGURATION_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if not m:
            continue
        text = re.sub(r"[^\w\s-]", "", m.group(1).lower()).strip()
        slugs.add(text.replace(" ", "-"))
    return slugs


# ---------------------------------------------------------------------------
# Scenario 1: completeness lint over every registered entry
# ---------------------------------------------------------------------------

class TestCompletenessLint:
    def test_seed_inventory_is_registered(self):
        assert SEED_INVENTORY <= set(prescriptions.REGISTRY)

    def test_every_entry_has_cause_and_both_fix_forms(self):
        for key, entry in prescriptions.REGISTRY.items():
            assert entry.cause.strip(), f"{key}: empty cause"
            assert entry.fix_nl.strip(), f"{key}: empty natural-language fix"
            assert entry.fix_cli.strip(), f"{key}: empty CLI fix"

    def test_every_cli_form_starts_with_a_runnable_token(self):
        for key, entry in prescriptions.REGISTRY.items():
            for cli in (entry.fix_cli, *entry.alt_cli):
                assert RUNNABLE.match(cli), f"{key}: not runnable: {cli!r}"

    def test_nl_fix_never_duplicates_the_cli_string_verbatim(self):
        for key, entry in prescriptions.REGISTRY.items():
            assert entry.fix_nl.strip() != entry.fix_cli.strip(), key

    def test_registry_keys_match_entry_fields(self):
        for (source, failure), entry in prescriptions.REGISTRY.items():
            assert entry.source == source
            assert entry.failure == failure

    def test_anchors_point_at_real_configuration_md_headings(self):
        slugs = _configuration_md_slugs()
        for key, entry in prescriptions.REGISTRY.items():
            if entry.anchor:
                assert entry.anchor in slugs, (
                    f"{key}: anchor #{entry.anchor} not a CONFIGURATION.md heading"
                )

    def test_no_secret_looking_values(self):
        """Placeholders only - no copy-pasteable live credentials."""
        secretish = re.compile(r"(sk-[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|xox[bap]-)")
        for key, entry in prescriptions.REGISTRY.items():
            blob = " ".join((entry.cause, entry.fix_nl, entry.fix_cli, *entry.alt_cli))
            assert not secretish.search(blob), key


# ---------------------------------------------------------------------------
# Documented CLI forms for the flagship entries
# ---------------------------------------------------------------------------

class TestDocumentedCliForms:
    def test_x_cookie_fixes_use_setup_with_browser_cookie_consent(self):
        expected = "python3 skills/last30days/scripts/last30days.py setup --allow-browser-cookies"
        assert prescriptions.get("x", "cookies_missing").fix_cli == expected
        assert prescriptions.get("x", "cookies_expired").fix_cli == expected

    def test_scrapecreators_fix_is_the_github_device_flow(self):
        entry = prescriptions.get("scrapecreators", "key_missing")
        assert entry.fix_cli == "python3 skills/last30days/scripts/last30days.py setup --github"

    def test_ytdlp_install_and_reinstall_reference_u1_health_strings(self):
        """Binary-class fixes reference U1's tables instead of restating them."""
        install, reinstall = health._MANAGER_PRESCRIPTIONS["yt-dlp"]["brew"]
        assert prescriptions.get("youtube", "ytdlp_missing").fix_cli == install
        assert prescriptions.get("youtube", "ytdlp_broken").fix_cli == reinstall

    def test_digg_install_references_u1_printing_press_command(self):
        entry = prescriptions.get("digg", "pp_cli_missing")
        assert entry.fix_cli == health._pp_install_cmd("digg-pp-cli")


# ---------------------------------------------------------------------------
# Scenario 2: quality_nudge text derives from the same registry entries
# ---------------------------------------------------------------------------

def _nudge(config_overrides=None, result_overrides=None, ytdlp_installed=False):
    from lib.quality_nudge import compute_quality_score
    from lib import youtube_yt

    config = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "XAI_API_KEY": None,
        "XQUIK_API_KEY": None,
        "SCRAPECREATORS_API_KEY": None,
    }
    config.update(config_overrides or {})
    results = {"x_error": None, "youtube_error": None, "reddit_error": None}
    results.update(result_overrides or {})
    with patch.object(youtube_yt, "is_ytdlp_installed", return_value=ytdlp_installed):
        return compute_quality_score(config, results)


class TestSharedWithQualityNudge:
    def test_x_cookie_expired_nudge_is_built_from_the_registry_entry(self):
        entry = prescriptions.get("x", "cookies_expired")
        q = _nudge(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"x_error": "401 unauthorized"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is not None
        assert entry.fix_nl in q["nudge_text"]

    def test_x_cookie_missing_nudge_is_built_from_the_registry_entry(self):
        entry = prescriptions.get("x", "cookies_missing")
        q = _nudge(ytdlp_installed=True)
        assert entry.fix_nl in q["nudge_text"]

    def test_ytdlp_missing_nudge_uses_registry_cli(self):
        entry = prescriptions.get("youtube", "ytdlp_missing")
        q = _nudge(config_overrides={"AUTH_TOKEN": "tok123"}, ytdlp_installed=False)
        assert entry.fix_cli in q["nudge_text"]

    def test_ytdlp_stale_degraded_nudge_uses_registry_cli_forms(self):
        entry = prescriptions.get("youtube", "ytdlp_stale")
        q = _nudge(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
            },
        )
        assert entry.fix_cli in q["nudge_text"]
        for alt in entry.alt_cli:
            assert alt in q["nudge_text"]

    def test_quality_nudge_source_no_longer_hardcodes_fix_strings(self):
        """The migrated fix strings must live in the registry only.

        Trigger logic legitimately still reads credential names (e.g.
        ``config.get("XAI_API_KEY")``); this guards the FIX text.
        """
        source = (
            REPO_ROOT / "skills/last30days/scripts/lib/quality_nudge.py"
        ).read_text(encoding="utf-8")
        assert "brew " not in source
        assert "api.x.ai" not in source
        assert "log into x.com" not in source
        assert "yt-dlp: brew" not in source


# ---------------------------------------------------------------------------
# Scenario 3: unregistered failure -> generic fallback, no crash
# ---------------------------------------------------------------------------

class TestFallback:
    def test_lookup_returns_none_for_unregistered(self):
        assert prescriptions.lookup("linkedin", "flux_capacitor_missing") is None

    def test_get_returns_generic_configuration_md_pointer(self):
        entry = prescriptions.get("linkedin", "flux_capacitor_missing")
        assert "CONFIGURATION.md" in entry.fix_nl
        assert RUNNABLE.match(entry.fix_cli)
        assert entry.source == "linkedin"
        assert entry.failure == "flux_capacitor_missing"

    def test_get_returns_registered_entry_when_present(self):
        assert prescriptions.get("x", "cookies_missing") is prescriptions.REGISTRY[
            ("x", "cookies_missing")
        ]


# ---------------------------------------------------------------------------
# Composition with U1 dependency probes (health.DependencyProbe)
# ---------------------------------------------------------------------------

class TestDependencyProbeComposition:
    def test_ok_probe_needs_no_prescription(self):
        probe = health.DependencyProbe(name="yt-dlp", status=health.OK, detail="2026.06.01")
        assert prescriptions.for_dependency_probe(probe) is None

    def test_probe_prescription_wins_the_cli_form(self):
        """U1's machine-aware string (pipx owner here) overrides the static CLI."""
        probe = health.DependencyProbe(
            name="yt-dlp",
            status=health.BROKEN,
            detail="yt-dlp resolves to /x/yt-dlp but won't execute: stale shim",
            prescription="pipx reinstall yt-dlp",
        )
        entry = prescriptions.for_dependency_probe(probe)
        assert entry is not None
        assert entry.fix_cli == "pipx reinstall yt-dlp"
        # Registry vocabulary (NL form) is retained.
        assert entry.fix_nl == prescriptions.get("youtube", "ytdlp_broken").fix_nl

    def test_digg_off_path_probe_maps_to_the_path_entry(self):
        probe = health.DependencyProbe(
            name="digg-pp-cli",
            status=health.MISSING,
            detail=(
                "digg-pp-cli is installed at /home/u/.local/bin/digg-pp-cli but "
                "that directory is not on this process's PATH"
            ),
            prescription='add $HOME/.local/bin to PATH (e.g. export PATH="$HOME/.local/bin:$PATH") so digg-pp-cli resolves',
            off_path=True,
        )
        entry = prescriptions.for_dependency_probe(probe)
        assert entry is not None
        assert entry.failure == "pp_cli_off_path"

    def test_digg_broken_probe_maps_to_the_reinstall_entry(self):
        """An installed-but-broken digg binary must get reinstall-framed
        text, never the never-installed "install it" entry (F6); the
        probe's own prescription wins the CLI form, mirroring
        test_probe_prescription_wins_the_cli_form."""
        reinstall = f"re-run the Printing Press install: {health.pp_install_cmd('digg')}"
        probe = health.DependencyProbe(
            name="digg-pp-cli",
            status=health.BROKEN,
            detail=(
                "digg-pp-cli resolves to /home/u/.local/bin/digg-pp-cli "
                "but won't execute"
            ),
            prescription=reinstall,
        )
        entry = prescriptions.for_dependency_probe(probe)
        assert entry is not None
        assert entry.failure == "pp_cli_broken"
        assert entry.fix_cli == reinstall
        # Registry vocabulary (NL form) is retained and reinstall-framed.
        assert entry.fix_nl == prescriptions.get("digg", "pp_cli_broken").fix_nl
        assert "reinstall" in entry.fix_nl
        assert entry.fix_nl != prescriptions.get("digg", "pp_cli_missing").fix_nl

    def test_digg_timeout_probe_also_maps_to_the_reinstall_entry(self):
        probe = health.DependencyProbe(
            name="digg-pp-cli",
            status=health.TIMEOUT,
            detail="digg-pp-cli --version timed out",
            prescription="reinstall digg-pp-cli",
        )
        entry = prescriptions.for_dependency_probe(probe)
        assert entry is not None
        assert entry.failure == "pp_cli_broken"

    def test_unregistered_dependency_wraps_the_probe(self):
        probe = health.DependencyProbe(
            name="ffmpeg",
            status=health.MISSING,
            detail="ffmpeg not found on PATH",
            prescription="brew install ffmpeg",
        )
        entry = prescriptions.for_dependency_probe(probe)
        assert entry is not None
        assert entry.fix_cli == "brew install ffmpeg"
        assert entry.fix_nl  # still has a natural-language form


# ---------------------------------------------------------------------------
# Composition with U2 backend findings (lib/backends.py)
# ---------------------------------------------------------------------------

class TestBackendComposition:
    def test_bird_cookie_prescription_embeds_the_registry_cli(self):
        from lib import backends, bird_x

        entry = prescriptions.get("x", "cookies_missing")
        ok_node = health.DependencyProbe(name="node", status=health.OK, detail="v22.0.0")
        with patch.object(bird_x, "is_bird_installed", return_value=True), \
                patch.object(health, "probe_dependency", return_value=ok_node):
            finding = backends._X_PROBES["bird"]({})
        assert finding.status == health.MISSING
        assert entry.fix_cli in finding.prescription

    def test_scrapecreators_prescription_embeds_the_registry_cli(self):
        from lib import backends

        entry = prescriptions.get("scrapecreators", "key_missing")
        finding = backends._SC_SPEC.probe({})
        assert finding.status == health.MISSING
        assert entry.fix_cli in finding.prescription
        # test_backend_descriptors requires the key name to stay present.
        assert "SCRAPECREATORS_API_KEY" in finding.prescription


class TestAltCliArityPin:
    """Greptile PR review: quality_nudge composes YouTube nudges from the two
    platform alternates on the ytdlp entries. The consumer is now tolerant of
    any arity (degrades wording instead of crashing), and this pin keeps the
    wording rich: both entries must keep at least the scoop + pip alternates."""

    @pytest.mark.parametrize("failure", ["ytdlp_missing", "ytdlp_stale"])
    def test_ytdlp_entries_keep_two_platform_alternates(self, failure):
        entry = prescriptions.get("youtube", failure)
        assert len(entry.alt_cli) >= 2, (
            f"youtube/{failure} lost a platform alternate; quality_nudge "
            "wording degrades (tolerant, but fix the entry or the prose)"
        )
