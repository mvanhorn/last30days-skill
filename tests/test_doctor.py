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

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
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
        self.assertIn("no transcription key for caption-free videos", record["note"])
        self.assertIn(self.entry.fix_nl, record["fix"])
        self.assertIn(self.entry.fix_cli, record["fix"])

    def test_text_line_includes_the_fix_on_the_ok_line(self):
        text = doctor.render_text(self.report)
        line = next(
            l for l in text.splitlines() if l.strip().startswith("✓ youtube")
        )
        self.assertIn("no transcription key for caption-free videos", line)
        self.assertIn(f"fix: {self.entry.fix_nl}", line)
        self.assertIn(self.entry.fix_cli, line)


class NativeSearchHost(unittest.TestCase):
    """Scenario 6: native-search host with no web keys -> off, not error."""

    def test_web_maps_to_off_with_host_native_note(self):
        report = _build({"LAST30DAYS_NATIVE_SEARCH": "1"})
        record = report["sources"]["web"]
        self.assertEqual("off", record["tier"])
        self.assertEqual("unconfigured", record["status"])
        self.assertIn("host-native search", record["note"])

    def test_web_with_key_stays_ok_on_native_host(self):
        report = _build({
            "LAST30DAYS_NATIVE_SEARCH": "1",
            "EXA_API_KEY": "dummy-exa-secret-000",
        })
        record = report["sources"]["web"]
        self.assertEqual("ok", record["tier"])
        self.assertEqual("exa", record["active_backend"])


class TextReport(unittest.TestCase):
    """Grouped text rendering: ready / degraded / off / error."""

    def test_groups_and_lines(self):
        report = _build({}, probe_map={"yt-dlp": health.BROKEN})
        text = doctor.render_text(report)
        self.assertIn("last30days doctor", text)
        for header in ("Ready", "Degraded", "Off", "Errors"):
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


if __name__ == "__main__":
    unittest.main()
