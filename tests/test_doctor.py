"""U4: unified `doctor` command (lib/doctor.py + topic-word dispatch).

Covers the plan's U4 scenarios:
  1. Fully keyless env -> free sources (reddit, hackernews, polymarket,
     github) tier ok; key-gated sources tier off with prescriptions; exit 0.
  2. `--json` per-source shape for every registered source (chained and
     single-backend), tier/status rollup rows asserted.
  3. One probe timing out -> that source status `timeout`, tier `error`,
     all other sources still render (plus per-source exception isolation).
  4. No-secrets invariant: seeded fake credentials never appear in text or
     JSON output.
  5. Topic-word dispatch: `doctor` triggers the report; a longer research
     topic containing the word does not (setup's exact-match collision rule).
  6. Native-search host + no web keys -> web tier off with a host-native
     note, never a false-alarm error.
"""

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import last30days as cli
from lib import backends, doctor, health, prescriptions

BIRD_STATUS_OFF = {
    "installed": False,
    "authenticated": False,
    "username": None,
    "can_install": True,
}

# Obvious dummies only (repo security hygiene).
FAKE_SECRETS = {
    "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
    "XAI_API_KEY": "dummy-xai-secret-000",
    "BRAVE_API_KEY": "dummy-brave-secret-000",
    "GROQ_API_KEY": "dummy-groq-secret-000",
    "AUTH_TOKEN": "dummy-auth-token-secret-000",
    "CT0": "dummy-ct0-secret-000",
    "BSKY_HANDLE": "dummy.example.social",
    "BSKY_APP_PASSWORD": "dummy-bsky-secret-000",
    "TRUTHSOCIAL_TOKEN": "dummy-truth-secret-000",
    "GITHUB_TOKEN": "dummy-github-secret-000",
}

VALID_TIERS = {"ok", "warn", "off", "error"}
VALID_STATUSES = {
    "ok", "degraded", "opt-in", "unconfigured", "missing", "broken", "timeout", "error",
}
# The R1 rollup table, row by row.
TIER_BY_STATUS = {
    "ok": "ok",
    "degraded": "warn",
    "opt-in": "off",
    "unconfigured": "off",
    "missing": "error",
    "broken": "error",
    "timeout": "error",
    "error": "error",
}


def _probe_dep(status_map=None, default_status=health.MISSING):
    """Fake health.probe_dependency honoring a per-name status map."""
    status_map = status_map or {}

    def fake(name, timeout=health.PROBE_TIMEOUT):
        status = status_map.get(name, default_status)
        if status == health.OK:
            return health.DependencyProbe(name=name, status=health.OK, detail=f"{name} 1.0.0")
        return health.DependencyProbe(
            name=name,
            status=status,
            detail=f"{name} probe simulated {status}",
            prescription=(
                f"install {name}" if status == health.MISSING else f"reinstall {name}"
            ),
            owner_pkg_manager="brew",
        )

    return fake


class _Hermetic:
    """Context manager stack making doctor runs machine-independent."""

    def __init__(self, probe_map=None, default_status=health.MISSING):
        self._patches = [
            mock.patch("lib.health.probe_dependency", _probe_dep(probe_map, default_status)),
            mock.patch("lib.bird_x.is_bird_installed", return_value=False),
            mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None),
            mock.patch("lib.bird_x.get_bird_status", return_value=dict(BIRD_STATUS_OFF)),
            # The doctor path is local-only for xurl: the live `xurl whoami`
            # network check must never run (no-network guarantee).
            mock.patch(
                "lib.xurl_x.is_available",
                side_effect=AssertionError(
                    "doctor path ran the live `xurl whoami` network check"
                ),
            ),
            mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
            mock.patch(
                "lib.xurl_x.stored_auth_status",
                return_value=("missing", "no token store at ~/.xurl"),
            ),
            mock.patch("lib.backends.which", lambda name: None),
            # Hermetic library: never glob the user's real saved-research dir.
            # Tests that assert a specific brief count override this.
            mock.patch("lib.doctor._count_saved_briefs", return_value=0),
            # Hermetic run-evidence: never read the user's real last-report.json.
            # Tests that inject run evidence override this with a temp file.
            mock.patch("lib.doctor._last_report_path", return_value=None),
            # Hermetic live probe: never make a real network call. Tests that
            # exercise probing override this with canned results.
            mock.patch("lib.doctor._probe_sources", return_value={}),
            # FTS5 is present on CI/dev SQLite; pin it so the library record's
            # branch is deterministic regardless of the host's SQLite build.
            mock.patch("lib.library_index.fts5_available", return_value=True),
            # Snapshot os.environ so the CLAUDECODE scrub below is restored on
            # exit. The real test shell (Claude Code) sets CLAUDECODE=1, which
            # would otherwise make doctor's host-native web detection fire in
            # every test and mask the keyless-degraded path. Tests that want the
            # host-native path pass CLAUDECODE explicitly in their config dict.
            mock.patch.dict(os.environ, {}, clear=False),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        os.environ.pop("CLAUDECODE", None)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _build(config, **kwargs):
    with _Hermetic(**kwargs):
        return doctor.build_report(dict(config))


def _run_cli_doctor(argv, config):
    with _Hermetic(), \
         mock.patch.object(cli.env, "get_config", return_value=dict(config)), \
         mock.patch.object(sys, "argv", ["last30days.py"] + argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main()
    return rc, stdout.getvalue()


class KeylessEnvironment(unittest.TestCase):
    """Scenario 1: fully keyless env."""

    def setUp(self):
        self.report = _build({})

    def test_free_sources_tier_ok(self):
        for name in ("reddit", "hackernews", "polymarket", "github"):
            self.assertEqual("ok", self.report["sources"][name]["tier"], name)
            self.assertEqual("ok", self.report["sources"][name]["status"], name)

    def test_key_gated_sources_off_with_prescriptions(self):
        for name in ("x", "tiktok", "instagram", "threads", "bluesky", "truthsocial"):
            record = self.report["sources"][name]
            self.assertEqual("off", record["tier"], name)
            self.assertIn(record["status"], ("unconfigured", "opt-in"), name)
            self.assertTrue(record["fix"], f"{name} must carry a fix prescription")

    def test_youtube_off_when_ytdlp_missing_and_no_key(self):
        record = self.report["sources"]["youtube"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])
        self.assertTrue(record["fix"])

    def test_web_keyless_floor_is_degraded_not_error(self):
        record = self.report["sources"]["web"]
        self.assertEqual("warn", record["tier"])
        self.assertEqual("degraded", record["status"])
        self.assertEqual("keyless", record["active_backend"])

    def test_cli_exit_code_zero_even_with_problems(self):
        rc, out = _run_cli_doctor(["doctor"], {})
        self.assertEqual(0, rc)
        self.assertIn("last30days doctor", out)


class GitHubAuthDetection(unittest.TestCase):
    """GitHub doctor auth must mirror the real fetcher token source."""

    def test_github_env_token_without_gh_reports_authenticated_tier(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "dummy-github-secret-000"}), \
             mock.patch("lib.doctor.shutil.which", return_value=None):
            record = _build({})["sources"]["github"]

        self.assertEqual("ok", record["tier"])
        self.assertEqual("ok", record["status"])
        self.assertEqual("authenticated tier (GITHUB_TOKEN or gh CLI)", record["detail"])

    def test_github_without_env_token_or_gh_reports_unauthenticated_tier(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": ""}), \
             mock.patch("lib.doctor.shutil.which", return_value=None):
            record = _build({})["sources"]["github"]

        self.assertEqual("ok", record["tier"])
        self.assertEqual("ok", record["status"])
        self.assertIn("unauthenticated REST tier", record["detail"])


class UnconfiguredXWithBrokenNode(unittest.TestCase):
    """F9 repro: no X configuration + a broken node runtime must read as
    off/unconfigured with the cookie fix on bird — never a configured-but-
    broken error carrying a node prescription."""

    def test_x_rolls_up_off_with_cookie_prescription(self):
        report = _build({}, probe_map={"node": health.BROKEN})
        record = report["sources"]["x"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])
        bird = next(b for b in record["backends"] if b["name"] == "bird")
        self.assertEqual("missing", bird["status"])
        self.assertIn("cookie", (bird["detail"] + bird["fix"]).lower())
        self.assertNotIn("node", bird["fix"].lower())


class CookieBackedXReadiness(unittest.TestCase):
    """U2: when bird is installed and FROM_BROWSER will authenticate X at run
    time, doctor reports X as Ready (not Off) with an honest, unverified note -
    matching the real run behavior where browser cookies serve X fine even
    though diagnose loads config in plan_only mode."""

    def test_x_ready_when_bird_installed_and_from_browser(self):
        with _Hermetic(), mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            report = doctor.build_report({"FROM_BROWSER": "auto"})
        record = report["sources"]["x"]
        self.assertEqual("ok", record["tier"])
        self.assertEqual("ok", record["status"])
        note = record["note"].lower()
        self.assertIn("browser cookies", note)
        self.assertIn("not verified", note)
        self.assertIn("xai_api_key", note)

    def test_x_stays_off_when_bird_installed_but_no_consent(self):
        # bird installed but FROM_BROWSER=off -> no cookie path -> genuinely off.
        with _Hermetic(), mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            report = doctor.build_report({"FROM_BROWSER": "off"})
        record = report["sources"]["x"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])

    def test_x_stays_off_when_consent_but_bird_missing(self):
        # FROM_BROWSER set but bird not installed -> no runtime path -> off.
        report = _build({"FROM_BROWSER": "auto"})
        record = report["sources"]["x"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])


class LibraryDoctorLine(unittest.TestCase):
    """U5: doctor reports the local research library so the report's
    'From your library' block is explained on the health surface."""

    def test_library_reports_indexed_brief_count(self):
        with _Hermetic(), mock.patch("lib.doctor._count_saved_briefs", return_value=3):
            record = doctor.build_report({})["sources"]["library"]
        self.assertEqual("ok", record["status"])
        self.assertIn("3 saved briefs", record["note"])

    def test_library_empty_store_is_informational_ok(self):
        record = _build({})["sources"]["library"]  # count stubbed to 0
        self.assertEqual("ok", record["status"])
        self.assertIn("no saved briefs yet", record["note"])

    def test_library_without_fts5_degrades_informationally(self):
        # Inner patch overrides the _Hermetic FTS5 pin.
        with _Hermetic(), mock.patch("lib.library_index.fts5_available", return_value=False):
            record = doctor.build_report({})["sources"]["library"]
        self.assertEqual("ok", record["status"])
        self.assertIn("FTS5", record["note"])

    def test_library_scan_failure_is_informational_ok(self):
        # A glob/OS error must never fail the run - it degrades to an OK line.
        with _Hermetic(), mock.patch(
            "lib.doctor._count_saved_briefs", side_effect=OSError("permission denied")
        ):
            record = doctor.build_report({})["sources"]["library"]
        self.assertEqual("ok", record["status"])
        self.assertIn("local research library", record["note"])

    def test_library_line_present_in_text_render(self):
        text = doctor.render_text(_build({}))
        self.assertTrue(
            any("library" in l for l in text.splitlines()),
            "doctor text output must carry a library line",
        )


class JsonShape(unittest.TestCase):
    """Scenario 2: documented per-source shape for every registered source."""

    def setUp(self):
        self.report = _build(dict(FAKE_SECRETS))

    def test_every_registered_source_present(self):
        self.assertEqual(set(doctor.SOURCE_ORDER), set(self.report["sources"].keys()))

    def test_per_source_record_shape(self):
        for name, record in self.report["sources"].items():
            for key in ("tier", "status", "backends", "mode", "active_backend", "fix", "requires"):
                self.assertIn(key, record, f"{name} missing {key}")
            self.assertIn(record["tier"], VALID_TIERS, name)
            self.assertIn(record["status"], VALID_STATUSES, name)

    def test_tier_status_rollup_rows(self):
        for name, record in self.report["sources"].items():
            self.assertEqual(
                TIER_BY_STATUS[record["status"]], record["tier"],
                f"{name}: status {record['status']} must roll up to "
                f"{TIER_BY_STATUS[record['status']]}",
            )

    def test_chained_sources_expose_backends_and_mode(self):
        for name in ("x", "youtube", "web"):
            record = self.report["sources"][name]
            self.assertEqual("alternative", record["mode"], name)
            self.assertIsInstance(record["backends"], list, name)
            self.assertTrue(record["backends"], name)
        self.assertEqual("conditional", self.report["sources"]["reddit"]["mode"])
        self.assertIsInstance(self.report["sources"]["reddit"]["backends"], list)

    def test_single_backend_sources_have_single_mode(self):
        for name in ("hackernews", "polymarket", "github", "bluesky"):
            record = self.report["sources"][name]
            self.assertEqual("single", record["mode"], name)
            self.assertIsNone(record["backends"], name)

    def test_conditional_reddit_never_picks_a_winner(self):
        record = self.report["sources"]["reddit"]
        self.assertIsNone(record["active_backend"])
        # Conditional wording is U2's, verbatim.
        with _Hermetic():
            expected = backends.resolve("reddit", dict(FAKE_SECRETS)).conditional
        self.assertEqual(expected, record["note"])

    def test_web_pin_is_flag_only_no_env_pin(self):
        # Web search has NO env pin; only the --web-backend flag.
        record = self.report["sources"]["web"]
        self.assertIsNone(record["pin_var"])
        self.assertEqual("--web-backend", record["pin_flag"])

    def test_chained_ok_source_predicts_will_use(self):
        record = self.report["sources"]["web"]
        self.assertEqual("ok", record["tier"])
        self.assertEqual("brave", record["active_backend"])
        self.assertIn("will use: brave", record["note"])

    def test_top_level_block(self):
        for key in ("engine_version", "config", "setup", "permissions", "sources"):
            self.assertIn(key, self.report)
        self.assertIsInstance(self.report["engine_version"], str)
        self.assertTrue(self.report["engine_version"])
        setup = self.report["setup"]
        self.assertIsInstance(setup["setup_complete"], bool)
        for name, present in setup["keys_present"].items():
            self.assertIsInstance(present, bool, name)
        self.assertIn("status", self.report["permissions"])

    def test_json_renderer_round_trips(self):
        payload = json.loads(doctor.render_json(self.report))
        self.assertEqual(set(doctor.SOURCE_ORDER), set(payload["sources"].keys()))


class ProbeFailureIsolation(unittest.TestCase):
    """Scenario 3: one bad probe cannot blank the report."""

    def test_timeout_probe_maps_to_timeout_status_error_tier(self):
        report = _build({}, probe_map={"yt-dlp": health.TIMEOUT})
        record = report["sources"]["youtube"]
        self.assertEqual("timeout", record["status"])
        self.assertEqual("error", record["tier"])
        self.assertTrue(record["fix"])
        # Everything else still renders.
        self.assertEqual("ok", report["sources"]["reddit"]["tier"])
        self.assertEqual("ok", report["sources"]["hackernews"]["tier"])

    def test_broken_probe_maps_to_broken(self):
        report = _build({}, probe_map={"yt-dlp": health.BROKEN})
        record = report["sources"]["youtube"]
        self.assertEqual("broken", record["status"])
        self.assertEqual("error", record["tier"])

    def test_chained_failure_requires_names_the_failed_backend(self):
        """F4: chain[0] merely MISSING while a later backend is BROKEN ->
        the record's requires is the BROKEN backend's (mirroring how the
        OK/WARN branches use the active finding), never chain[0]'s."""
        config = {
            "AUTH_TOKEN": "dummy-auth-token-secret-000",
            "CT0": "dummy-ct0-secret-000",
        }
        with _Hermetic(probe_map={"node": health.BROKEN}), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            report = doctor.build_report(dict(config))
        record = report["sources"]["x"]
        self.assertEqual("broken", record["status"])
        self.assertEqual("error", record["tier"])
        by_name = {b["name"]: b for b in record["backends"]}
        # chain[0] (xai) is merely unconfigured; bird is the broken one.
        self.assertEqual("missing", by_name["xai"]["status"])
        self.assertEqual("broken", by_name["bird"]["status"])
        self.assertEqual(by_name["bird"]["requires"], record["requires"])
        self.assertNotEqual(by_name["xai"]["requires"], record["requires"])

    def test_source_exception_is_isolated(self):
        real_resolve = backends.resolve

        def exploding(source, config, pin=None):
            if source == "x":
                raise RuntimeError("probe blew up")
            return real_resolve(source, config, pin)

        with _Hermetic(), mock.patch("lib.backends.resolve", exploding):
            report = doctor.build_report({})
        record = report["sources"]["x"]
        self.assertEqual("error", record["status"])
        self.assertEqual("error", record["tier"])
        self.assertIn("RuntimeError", record["detail"])
        # The rest of the report survives.
        self.assertEqual("ok", report["sources"]["reddit"]["tier"])
        self.assertEqual(set(doctor.SOURCE_ORDER), set(report["sources"].keys()))
        # And the whole report still renders as text and JSON.
        self.assertTrue(doctor.render_text(report))
        json.loads(doctor.render_json(report))


class NoSecretsInvariant(unittest.TestCase):
    """Scenario 4: seeded fake credentials never appear in any output."""

    def test_no_secret_values_in_text_or_json(self):
        report = _build(dict(FAKE_SECRETS))
        text = doctor.render_text(report)
        raw_json = doctor.render_json(report)
        for var, secret in FAKE_SECRETS.items():
            if var == "BSKY_HANDLE":
                continue  # a handle is an identifier, not a credential
            self.assertNotIn(secret, text, var)
            self.assertNotIn(secret, raw_json, var)

    def test_keys_present_are_booleans(self):
        report = _build(dict(FAKE_SECRETS))
        for name, value in report["setup"]["keys_present"].items():
            self.assertIsInstance(value, bool, name)


class TopicWordDispatch(unittest.TestCase):
    """Scenario 5: `doctor` dispatches exactly like `setup` (exact match only)."""

    def test_doctor_topic_triggers_report(self):
        with mock.patch("lib.doctor.run", return_value=0) as run, \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(sys, "argv", ["last30days.py", "doctor"]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        self.assertTrue(run.called)

    def test_doctor_json_flag_passes_through(self):
        with mock.patch("lib.doctor.run", return_value=0) as run, \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(sys, "argv", ["last30days.py", "doctor", "--json"]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        self.assertTrue(run.call_args.kwargs.get("emit_json"))

    def test_doctor_emit_json_also_works(self):
        rc, out = _run_cli_doctor(["doctor", "--emit=json"], {})
        self.assertEqual(0, rc)
        payload = json.loads(out)
        self.assertIn("sources", payload)

    def test_multiword_topic_containing_doctor_is_research_not_report(self):
        # Same collision rule as setup: exact single-word match only. A real
        # research topic goes down the research path (sentinel raised there).
        with mock.patch("lib.doctor.run", side_effect=AssertionError("doctor must not run")), \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(
                 cli.pipeline, "diagnose", side_effect=RuntimeError("research path reached")
             ), \
             mock.patch.object(sys, "argv", ["last30days.py", "doctor", "who", "reviews"]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(RuntimeError):
                    cli.main()

    def test_json_flag_rejected_for_research_topics(self):
        with mock.patch.object(
            cli.env, "get_config", side_effect=AssertionError("config should not load")
        ), mock.patch.object(sys, "argv", ["last30days.py", "some", "topic", "--json"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        self.assertIn("--json", stderr.getvalue())


class IncludeSourcesTokenParsing(unittest.TestCase):
    """Opt-in gates match whole INCLUDE_SOURCES tokens, never substrings."""

    def test_substring_token_does_not_enable_linkedin(self):
        report = _build({
            "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
            "INCLUDE_SOURCES": "notlinkedincorp",
        })
        record = report["sources"]["linkedin"]
        self.assertEqual("opt-in", record["status"])
        self.assertEqual("off", record["tier"])

    def test_exact_token_enables_linkedin(self):
        report = _build({
            "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
            "INCLUDE_SOURCES": "linkedin",
        })
        record = report["sources"]["linkedin"]
        self.assertEqual("ok", record["status"])
        self.assertEqual("ok", record["tier"])


class YoutubeTranscriptionNote(unittest.TestCase):
    """F7: yt-dlp probes OK but no GROQ_API_KEY/OPENAI_API_KEY -> the ok
    youtube record carries the caption-free note plus the
    transcription_key_missing fix, and (F14) the text renderer surfaces
    that fix even though the record's tier is ok."""

    def setUp(self):
        self.report = _build({}, probe_map={"yt-dlp": health.OK})
        self.entry = prescriptions.get("youtube", "transcription_key_missing")

    def test_ok_record_carries_note_and_fix(self):
        record = self.report["sources"]["youtube"]
        self.assertEqual("ok", record["tier"])
        self.assertEqual("ok", record["status"])
        note = record["note"].lower()
        # Honest note: affirms the working path, scopes the key to caption-free.
        self.assertIn("search + transcripts work", note)
        self.assertIn("caption-free", note)
        # Does not read as broken and does not attribute comment text to yt-dlp.
        self.assertNotIn("no transcription key for caption-free videos", note)
        self.assertIn(self.entry.fix_nl, record["fix"])
        self.assertIn(self.entry.fix_cli, record["fix"])

    def test_comment_text_attributed_to_scrapecreators_not_ytdlp(self):
        # config has no ScrapeCreators key -> comment note names ScrapeCreators,
        # never claims comment text comes from yt-dlp.
        note = self.report["sources"]["youtube"]["note"].lower()
        self.assertIn("comment text needs a scrapecreators key", note)

    def test_text_line_includes_the_fix_on_the_ok_line(self):
        text = doctor.render_text(self.report)
        # Located by source name, not glyph: the four-state audit sorts a
        # no-run-evidence ok source to UNVERIFIED, but the transcription fix
        # must still ride the youtube line.
        line = next(
            l
            for l in text.splitlines()
            if " youtube" in l and "search + transcripts work" in l
        )
        self.assertIn("search + transcripts work", line)
        self.assertIn(f"fix: {self.entry.fix_nl}", line)
        self.assertIn(self.entry.fix_cli, line)


class YoutubeCommentsFixLine(unittest.TestCase):
    """Greptile P2: when only the comment-text caveat fires (transcription key
    present), the record still carries an actionable fix line."""

    def test_comments_fix_names_scrapecreators_when_no_key(self):
        record = _build(
            {"GROQ_API_KEY": "dummy-groq-secret-000"},
            probe_map={"yt-dlp": health.OK},
        )["sources"]["youtube"]
        self.assertEqual("ok", record["status"])
        note = record["note"].lower()
        self.assertIn("comment text needs", note)
        self.assertNotIn("caption-free", note)  # transcription caveat absent
        self.assertTrue(record["fix"], "comment-text caveat must carry a fix")

    def test_comments_fix_names_optin_when_key_present(self):
        record = _build(
            {
                "GROQ_API_KEY": "dummy-groq-secret-000",
                "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
            },
            probe_map={"yt-dlp": health.OK},
        )["sources"]["youtube"]
        self.assertEqual("ok", record["status"])
        self.assertIn("youtube_comments", record["fix"])
        self.assertIn("INCLUDE_SOURCES", record["fix"])

    def test_transcription_fix_takes_precedence_when_both_fire(self):
        record = _build({}, probe_map={"yt-dlp": health.OK})["sources"]["youtube"]
        entry = prescriptions.get("youtube", "transcription_key_missing")
        self.assertIn(entry.fix_nl, record["fix"])


class YoutubeHealthyWhenFullyConfigured(unittest.TestCase):
    """U3: with a transcription key AND comment access, the YouTube note carries
    no caveat - it is cleanly Ready."""

    def test_no_caveats_when_transcription_and_comments_available(self):
        report = _build(
            {
                "GROQ_API_KEY": "dummy-groq-secret-000",
                "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
                "INCLUDE_SOURCES": "youtube_comments",
            },
            probe_map={"yt-dlp": health.OK},
        )
        record = report["sources"]["youtube"]
        self.assertEqual("ok", record["status"])
        note = record["note"].lower()
        self.assertNotIn("caption-free", note)
        self.assertNotIn("comment text needs", note)


class NativeSearchHost(unittest.TestCase):
    """Scenario 6: native-search host with no web keys -> off, not error."""

    def test_web_maps_to_off_with_host_native_note(self):
        report = _build({"LAST30DAYS_NATIVE_SEARCH": "1"})
        record = report["sources"]["web"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])
        self.assertIn("host-native search", record["note"])

    def test_web_on_claudecode_host_is_native_not_degraded(self):
        # CLAUDECODE set but LAST30DAYS_NATIVE_SEARCH unset (the standalone
        # `doctor` case) -> host-native note, not "degraded/keyless".
        report = _build({"CLAUDECODE": "1"})
        record = report["sources"]["web"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])
        note = record["note"]
        self.assertIn("Claude Code", note)
        # Must NOT cite an env var the user never set.
        self.assertNotIn("LAST30DAYS_NATIVE_SEARCH", note)

    def test_web_native_via_real_env_var_not_just_config(self):
        # Production path: env.get_config() never puts CLAUDECODE in the config
        # dict, so the os.environ branch is the ONLY one a real Claude Code
        # session hits. Set the process env var (config has no CLAUDECODE key).
        with _Hermetic(), mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            record = doctor.build_report({})["sources"]["web"]
        self.assertEqual("off", record["tier"])
        self.assertIn("Claude Code", record["note"])
        self.assertNotIn("LAST30DAYS_NATIVE_SEARCH", record["note"])

    def test_web_stays_degraded_keyless_without_native_signal(self):
        # No CLAUDECODE, no LAST30DAYS_NATIVE_SEARCH -> genuine keyless floor.
        record = _build({})["sources"]["web"]
        self.assertEqual("warn", record["tier"])
        self.assertEqual("degraded", record["status"])
        self.assertEqual("keyless", record["active_backend"])

    def test_web_with_key_stays_ok_on_native_host(self):
        report = _build({
            "LAST30DAYS_NATIVE_SEARCH": "1",
            "EXA_API_KEY": "dummy-exa-secret-000",
        })
        record = report["sources"]["web"]
        self.assertEqual("ok", record["tier"])
        self.assertEqual("exa", record["active_backend"])


class TextReport(unittest.TestCase):
    """Grouped text rendering: four-state audit."""

    def test_groups_and_lines(self):
        report = _build({}, probe_map={"yt-dlp": health.BROKEN})
        text = doctor.render_text(report)
        self.assertIn("last30days doctor", text)
        for header in (
            "WORKING",
            "TURNED ON - UNVERIFIED",
            "NOT WORKING",
            "COULD BE ON",
        ):
            self.assertIn(header, text)
        # One line per source: glyph + source name; fix on non-ok lines.
        self.assertIn("reddit", text)
        self.assertIn("youtube", text)
        self.assertIn("reinstall yt-dlp", text)
        # Reddit renders U2's conditional wording verbatim, no single winner.
        with _Hermetic():
            conditional = backends.resolve("reddit", {}).conditional
        self.assertIn(conditional, text)

    def test_will_use_rendered_for_chained_ok_source(self):
        report = _build({"BRAVE_API_KEY": "dummy-brave-secret-000"})
        text = doctor.render_text(report)
        self.assertIn("will use: brave", text)


def _write_last_report(dir_path, *, source_status, topic="wordpress", fresh=True):
    """Write a minimal last-report.json the run-evidence loader can read."""
    ts = datetime.datetime.now(datetime.timezone.utc)
    if not fresh:
        ts = ts - datetime.timedelta(
            seconds=doctor.DEFAULT_REPORT_CACHE_TTL_SECONDS + 600
        )
    iso = ts.isoformat()
    payload = {
        "schema": doctor.REPORT_CACHE_SCHEMA_VERSION,
        "timestamp": iso,
        "topic": topic,
        "reports": [
            {
                "entity": "",
                "report": {
                    "generated_at": iso,
                    "source_status": {
                        src: {
                            "source": src,
                            "state": st.get("state"),
                            "items_returned": st.get("items_returned", 0),
                            "detail": st.get("detail"),
                            "at": iso,
                            "fix_hint": st.get("fix_hint"),
                        }
                        for src, st in source_status.items()
                    },
                },
            }
        ],
    }
    path = Path(dir_path) / doctor.REPORT_CACHE_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class RunEvidenceOverlay(unittest.TestCase):
    """U1: build_report overlays last-report.json per-source outcomes."""

    def _build_with_evidence(self, source_status, fresh=True):
        tmp = tempfile.mkdtemp()
        path = _write_last_report(tmp, source_status=source_status, fresh=fresh)
        with _Hermetic(), mock.patch(
            "lib.doctor._last_report_path", return_value=path
        ):
            return doctor.build_report({})

    def test_failed_source_outcome_overlaid(self):
        report = self._build_with_evidence(
            {
                "youtube": {"state": "error", "items_returned": 0, "detail": "HTTP 500"},
                "reddit": {"state": "ok", "items_returned": 13},
            }
        )
        yt = report["sources"]["youtube"]["run_outcome"]
        self.assertIsNotNone(yt)
        self.assertEqual("error", yt["state"])
        self.assertIn("HTTP 500", yt["detail"])
        self.assertEqual(
            13, report["sources"]["reddit"]["run_outcome"]["items_returned"]
        )
        self.assertTrue(report["run_evidence"]["fresh"])
        self.assertTrue(report["run_evidence"]["present"])

    def test_no_cache_yields_no_outcomes(self):
        with _Hermetic():  # _last_report_path -> None
            report = doctor.build_report({})
        self.assertIsNone(report["sources"]["reddit"]["run_outcome"])
        self.assertFalse(report["run_evidence"]["present"])

    def test_corrupt_cache_treated_as_absent(self):
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / doctor.REPORT_CACHE_FILENAME
        path.write_text("{not valid json", encoding="utf-8")
        with _Hermetic(), mock.patch(
            "lib.doctor._last_report_path", return_value=path
        ):
            report = doctor.build_report({})
        self.assertIsNone(report["sources"]["reddit"]["run_outcome"])
        self.assertFalse(report["run_evidence"]["present"])

    def test_stale_cache_present_but_not_overlaid(self):
        report = self._build_with_evidence(
            {"youtube": {"state": "error", "items_returned": 0}}, fresh=False
        )
        # Present but not fresh: overlay withheld from plain doctor (R4),
        # while --postmortem (U4) can still read it by age.
        self.assertIsNone(report["sources"]["youtube"]["run_outcome"])
        self.assertTrue(report["run_evidence"]["present"])
        self.assertFalse(report["run_evidence"]["fresh"])


class FourStateAudit(unittest.TestCase):
    """U2: audit_state derivation + grouped render."""

    def test_keyless_ok_no_evidence_is_working(self):
        rec = {"tier": "ok", "status": "ok"}
        self.assertEqual(doctor.AUDIT_WORKING, doctor.audit_state("reddit", rec))

    def test_configured_ok_no_evidence_is_unverified(self):
        rec = {"tier": "ok", "status": "ok"}
        self.assertEqual(doctor.AUDIT_UNVERIFIED, doctor.audit_state("tiktok", rec))

    def test_fresh_run_items_is_working(self):
        rec = {"tier": "ok", "status": "ok"}
        ro = {"state": "ok", "items_returned": 13}
        self.assertEqual(doctor.AUDIT_WORKING, doctor.audit_state("tiktok", rec, ro))

    def test_fresh_run_error_is_not_working(self):
        rec = {"tier": "ok", "status": "ok"}
        ro = {"state": "error", "items_returned": 0, "detail": "HTTP 500"}
        self.assertEqual(
            doctor.AUDIT_NOT_WORKING, doctor.audit_state("youtube", rec, ro)
        )

    def test_fresh_run_partial_is_unverified(self):
        rec = {"tier": "ok", "status": "ok"}
        ro = {"state": "partial", "items_returned": 8, "detail": "HTTP 400"}
        self.assertEqual(
            doctor.AUDIT_UNVERIFIED, doctor.audit_state("instagram", rec, ro)
        )

    def test_off_tier_is_could_be_on(self):
        rec = {"tier": "off", "status": "opt-in"}
        self.assertEqual(
            doctor.AUDIT_COULD_BE_ON, doctor.audit_state("threads", rec)
        )

    def test_probe_result_decides_when_no_run(self):
        rec = {"tier": "ok", "status": "ok"}
        self.assertEqual(
            doctor.AUDIT_WORKING, doctor.audit_state("tiktok", rec, None, {"ok": True})
        )
        self.assertEqual(
            doctor.AUDIT_NOT_WORKING,
            doctor.audit_state("tiktok", rec, None, {"ok": False}),
        )

    def test_render_json_keeps_legacy_keys_and_adds_audit(self):
        report = _build({})
        for name, rec in report["sources"].items():
            self.assertIn("tier", rec, name)
            self.assertIn("status", rec, name)
            self.assertIn("audit_state", rec, name)
        self.assertEqual("config", report["mode"])
        blob = json.loads(doctor.render_json(report))
        self.assertIn("mode", blob)
        self.assertIn("audit_state", blob["sources"]["github"])

    def test_every_source_its_own_line(self):
        text = doctor.render_text(_build({}))
        self.assertRegex(text, r"[●◐✕○] github")

    def test_working_line_shows_item_count(self):
        tmp = tempfile.mkdtemp()
        path = _write_last_report(
            tmp, source_status={"reddit": {"state": "ok", "items_returned": 13}}
        )
        with _Hermetic(), mock.patch(
            "lib.doctor._last_report_path", return_value=path
        ):
            text = doctor.render_text(doctor.build_report({}))
        self.assertIn("13 items last run", text)


class JsonContract(unittest.TestCase):
    """U9: doctor --json is additive; --cached serves the new audit shape."""

    LEGACY_RECORD_KEYS = {
        "tier", "status", "mode", "backends", "active_backend", "fix",
        "requires", "note", "detail", "pin_var", "pin_flag", "pinned",
    }

    def test_legacy_record_keys_preserved(self):
        blob = json.loads(doctor.render_json(_build({})))
        rec = blob["sources"]["reddit"]
        for key in self.LEGACY_RECORD_KEYS:
            self.assertIn(key, rec, key)
        self.assertIn("audit_state", rec)  # additive
        self.assertIn("mode", blob)  # top-level additive

    def test_new_keys_additive(self):
        blob = json.loads(
            doctor.render_json(
                _build(
                    {"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"},
                    probe_map={"yt-dlp": health.OK},
                )
            )
        )
        yt = blob["sources"]["youtube"]
        self.assertIn("cli", yt)
        self.assertIn("backups", yt)
        self.assertIn("comments", yt)

    def test_cached_roundtrip_serves_audit_shape(self):
        tmp = Path(tempfile.mkdtemp()) / "doctor-cache.json"
        with _Hermetic(), mock.patch("lib.doctor.cache_path", return_value=tmp):
            report = doctor.build_report({})
            report["generated_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            report["from_cache"] = False
            self.assertTrue(doctor._write_cache(report, {}))
            served = doctor.read_cached_report({})
        self.assertIsNotNone(served)
        self.assertIn("audit_state", served["sources"]["reddit"])
        self.assertIn("WORKING", doctor.render_text(served))


class LiveProbe(unittest.TestCase):
    """U5: bounded live probe (--probe / no-fresh-run auto-fallback)."""

    def test_probeable_excludes_credit_gated(self):
        probeable = set(doctor._probeable_sources())
        for gated in ("x", "tiktok", "instagram", "threads", "linkedin"):
            self.assertNotIn(gated, probeable, gated)
        # free HTTP + keyless CLI sources ARE probeable
        for free in ("reddit", "hackernews", "polymarket", "github", "youtube"):
            self.assertIn(free, probeable, free)

    def test_probe_source_http_reachable(self):
        with mock.patch("lib.doctor._http_ok", return_value=(True, "HTTP 200")):
            res = doctor._probe_source("hackernews", {}, 5)
        self.assertTrue(res["ok"])
        self.assertTrue(res["probed"])

    def test_probe_source_credit_gated_returns_none(self):
        self.assertIsNone(doctor._probe_source("tiktok", {}, 5))

    def test_probe_failure_is_isolated(self):
        def flaky(name, config, timeout):
            if name == "reddit":
                raise RuntimeError("boom")
            return {"ok": True, "probed": True}

        with mock.patch("lib.doctor._probe_source", flaky):
            results = doctor._probe_sources({}, timeout=5)
        self.assertFalse(results["reddit"]["ok"])  # isolated failure
        self.assertIn("boom", results["reddit"]["detail"])
        self.assertTrue(results["hackernews"]["ok"])  # others unaffected

    def test_probe_deadline_never_hangs(self):
        import time

        def too_slow(name, config, timeout):
            time.sleep(1.3)  # exceeds the timeout(0)+1s result deadline
            return {"ok": True, "probed": True}

        with mock.patch("lib.doctor._probe_source", too_slow):
            results = doctor._probe_sources({}, timeout=0)
        self.assertTrue(results)
        self.assertTrue(
            any("deadline" in r.get("detail", "") for r in results.values())
        )

    def test_probe_result_flips_unverified_to_working(self):
        rec = {"tier": "ok", "status": "ok", "audit_state": doctor.AUDIT_UNVERIFIED}
        report = {"sources": {"hackernews": rec}}
        doctor._apply_probe(report, {"hackernews": {"ok": True, "probed": True}})
        self.assertEqual(doctor.AUDIT_WORKING, rec["audit_state"])
        self.assertTrue(rec["probe"]["ok"])

    def test_auto_probe_fires_when_no_fresh_run(self):
        canned = {"hackernews": {"ok": True, "detail": "HTTP 200", "probed": True}}
        with _Hermetic(), mock.patch(
            "lib.doctor._probe_sources", return_value=canned
        ) as probed:
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = doctor.run({})
        self.assertEqual(0, rc)
        probed.assert_called()  # auto-fired: no fresh run
        self.assertIn("live probe", err.getvalue())

    def test_no_auto_probe_when_fresh_run(self):
        tmp = tempfile.mkdtemp()
        path = _write_last_report(
            tmp, source_status={"reddit": {"state": "ok", "items_returned": 5}}
        )
        with _Hermetic(), mock.patch(
            "lib.doctor._last_report_path", return_value=path
        ), mock.patch("lib.doctor._probe_sources") as probed:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                doctor.run({})
        probed.assert_not_called()  # fresh run -> overlay, no probe


class Postmortem(unittest.TestCase):
    """U4: --postmortem reads the last run's per-source outcomes."""

    def _pm(self, source_status, fresh=True):
        tmp = tempfile.mkdtemp()
        path = _write_last_report(tmp, source_status=source_status, fresh=fresh)
        with _Hermetic(), mock.patch(
            "lib.doctor._last_report_path", return_value=path
        ):
            return doctor.build_postmortem({})

    def test_failed_partial_succeeded_grouping(self):
        pm = self._pm(
            {
                "youtube": {
                    "state": "error",
                    "items_returned": 0,
                    "detail": "HTTP 500",
                    "fix_hint": "retry later",
                },
                "instagram": {
                    "state": "partial",
                    "items_returned": 8,
                    "detail": "HTTP 400",
                },
                "reddit": {"state": "ok", "items_returned": 13},
            }
        )
        text = doctor.render_postmortem_text(pm)
        self.assertIn("Failed:", text)
        self.assertIn("HTTP 500", text)
        self.assertIn("retry later", text)
        self.assertIn("Partial:", text)
        self.assertIn("instagram", text)
        self.assertIn("Succeeded:", text)
        self.assertIn("reddit (13)", text)

    def test_empty_state(self):
        with _Hermetic():  # _last_report_path -> None
            pm = doctor.build_postmortem({})
        self.assertFalse(pm["present"])
        self.assertIn("No saved run found", doctor.render_postmortem_text(pm))

    def test_json_mode_shape(self):
        pm = self._pm({"youtube": {"state": "error", "items_returned": 0}})
        self.assertEqual("postmortem", pm["mode"])
        self.assertIn("youtube", pm["outcomes"])

    def test_reads_stale_run_by_age(self):
        pm = self._pm(
            {"youtube": {"state": "timeout", "items_returned": 0}}, fresh=False
        )
        self.assertTrue(pm["present"])
        self.assertIn("youtube", pm["outcomes"])

    def test_cli_dispatch_exits_zero(self):
        rc, out = _run_cli_doctor(["doctor", "--postmortem"], {})
        self.assertEqual(0, rc)
        self.assertIn("post-mortem", out)


class BackupAndCommentLanes(unittest.TestCase):
    """U7: backup + comment sub-lanes render on their parent source."""

    def test_backups_armed_with_sc_key(self):
        report = _build({"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"})
        self.assertTrue(report["sources"]["reddit"]["backups"][0]["armed"])
        yt_backup = report["sources"]["youtube"]["backups"][0]
        self.assertTrue(yt_backup["armed"])
        self.assertIn("rate-limited", yt_backup["note"])
        text = doctor.render_text(report)
        self.assertIn("backup: ScrapeCreators transcript/search backstop — armed", text)

    def test_backups_off_without_sc_key(self):
        report = _build({})
        self.assertFalse(report["sources"]["reddit"]["backups"][0]["armed"])
        self.assertFalse(report["sources"]["youtube"]["backups"][0]["armed"])

    def test_youtube_comments_reflect_include_sources(self):
        on = _build(
            {
                "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
                "INCLUDE_SOURCES": "tiktok,instagram,youtube_comments",
            }
        )
        self.assertTrue(on["sources"]["youtube"]["comments"]["enabled"])
        off = _build({"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"})
        self.assertFalse(off["sources"]["youtube"]["comments"]["enabled"])

    def test_x_dual_path_note(self):
        keyed = _build({"XAI_API_KEY": "dummy-xai-secret-000"})
        note = keyed["sources"]["x"]["backups"][0]["note"]
        self.assertIn("XAI_API_KEY", note)
        self.assertTrue(keyed["sources"]["x"]["backups"][0]["armed"])

    def test_sub_lanes_in_json(self):
        report = _build({"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"})
        blob = json.loads(doctor.render_json(report))
        self.assertIn("backups", blob["sources"]["youtube"])
        self.assertIn("comments", blob["sources"]["youtube"])


class ThreadsOptIn(unittest.TestCase):
    """U6: Threads reports opt-in state honestly against INCLUDE_SOURCES."""

    def test_key_without_optin_is_could_be_on(self):
        report = _build({"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"})
        rec = report["sources"]["threads"]
        self.assertEqual("opt-in", rec["status"])
        self.assertEqual(doctor.AUDIT_COULD_BE_ON, rec["audit_state"])
        self.assertIn("INCLUDE_SOURCES", rec["fix"])

    def test_key_with_optin_is_working(self):
        report = _build(
            {
                "SCRAPECREATORS_API_KEY": "dummy-sc-secret-000",
                "INCLUDE_SOURCES": "tiktok,instagram,threads",
            }
        )
        rec = report["sources"]["threads"]
        self.assertEqual("ok", rec["status"])

    def test_no_key_is_could_be_on_with_sc_fix(self):
        rec = _build({})["sources"]["threads"]
        self.assertEqual("unconfigured", rec["status"])
        self.assertEqual(doctor.AUDIT_COULD_BE_ON, rec["audit_state"])

    def test_tiktok_on_by_default_with_key_unchanged(self):
        # Regression: TikTok/Instagram stay on-by-default with a key (WORKING),
        # they are NOT opt-in-gated like Threads.
        report = _build({"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"})
        self.assertEqual("ok", report["sources"]["tiktok"]["status"])
        self.assertEqual("ok", report["sources"]["instagram"]["status"])


class CliHealth(unittest.TestCase):
    """U3: CLI-dependency health + techmeme/arxiv/trustpilot sources."""

    def test_new_cli_sources_present(self):
        report = _build(
            {},
            probe_map={
                "techmeme-pp-cli": health.OK,
                "arxiv-pp-cli": health.OK,
                "trustpilot-pp-cli": health.OK,
            },
        )
        for src in ("techmeme", "arxiv", "trustpilot"):
            self.assertIn(src, report["sources"], src)
            self.assertEqual("ok", report["sources"][src]["cli"]["status"], src)

    def test_cli_marker_and_block_for_ytdlp(self):
        report = _build({}, probe_map={"yt-dlp": health.OK})
        self.assertEqual("ok", report["sources"]["youtube"]["cli"]["status"])
        text = doctor.render_text(report)
        self.assertIn("CLI health", text)
        self.assertIn("[CLI: yt-dlp ✓]", text)

    def test_keyless_source_has_no_cli(self):
        report = _build({})
        self.assertNotIn("cli", report["sources"]["polymarket"])
        self.assertIn("need no CLI", doctor.render_text(report))

    def test_digg_off_path_is_not_working(self):
        def fake(name, timeout=health.PROBE_TIMEOUT):
            if name == "digg-pp-cli":
                return health.DependencyProbe(
                    name=name,
                    status=health.BROKEN,
                    detail="installed off PATH",
                    off_path=True,
                    prescription="add ~/.local/bin to PATH",
                )
            return health.DependencyProbe(
                name=name, status=health.MISSING, detail="missing",
                prescription="install",
            )

        with _Hermetic(), mock.patch("lib.health.probe_dependency", fake):
            report = doctor.build_report({})
        self.assertTrue(report["sources"]["digg"]["cli"]["off_path"])
        self.assertEqual(
            doctor.AUDIT_NOT_WORKING, report["sources"]["digg"]["audit_state"]
        )

    def test_gh_absent_github_still_working(self):
        report = _build({})  # gh missing by default in _Hermetic
        gh = report["sources"]["github"]
        self.assertEqual(doctor.AUDIT_WORKING, gh["audit_state"])
        self.assertTrue(gh["cli"]["optional"])


if __name__ == "__main__":
    unittest.main()
