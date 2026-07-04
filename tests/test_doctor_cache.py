"""U5: doctor cache — results persist across invocations with a TTL.

Covers the plan's U5 scenarios (R5, R7, KTD 8):
  1. Fresh cache within TTL -> `--cached` returns the stored result and
     spawns zero probes (probe layer spied, never called).
  2. Stale or missing cache -> live run executes and rewrites the cache.
  3. Explicit `doctor` (no `--cached`) -> always live; cache refreshed
     even when a fresh cache exists.
  4. TTL env override (LAST30DAYS_DOCTOR_TTL, seconds) respected;
     malformed/corrupt cache treated as absent — never a crash.
  5. No secret values in the cache file under seeded fake credentials.
  6. SKILL.md contract (test_onboarding_contract.py pattern): doctor
     trigger phrases and the cached standing rule are present, so future
     SKILL.md edits cannot silently erode the integration.
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
from lib import doctor, health

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"

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
    "AUTH_TOKEN": "dummy-auth-token-secret-000",
    "CT0": "dummy-ct0-secret-000",
    "BSKY_HANDLE": "dummy.example.social",
    "BSKY_APP_PASSWORD": "dummy-bsky-secret-000",
    "TRUTHSOCIAL_TOKEN": "dummy-truth-secret-000",
    "GITHUB_TOKEN": "dummy-github-secret-000",
}


def _iso_utc(seconds_ago: float = 0.0) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=seconds_ago)
    ).isoformat()


def _fake_probe(name, timeout=health.PROBE_TIMEOUT):
    return health.DependencyProbe(
        name=name,
        status=health.MISSING,
        detail=f"{name} probe simulated missing",
        prescription=f"install {name}",
        owner_pkg_manager="brew",
    )


class _Hermetic:
    """Machine-independent doctor runs (mirrors tests/test_doctor.py)."""

    def __init__(self, probe=None):
        self.probe_spy = mock.Mock(side_effect=probe or _fake_probe)
        self._patches = [
            mock.patch("lib.health.probe_dependency", self.probe_spy),
            mock.patch("lib.bird_x.is_bird_installed", return_value=False),
            mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None),
            mock.patch("lib.bird_x.get_bird_status", return_value=dict(BIRD_STATUS_OFF)),
            # Doctor path is local-only for xurl: the live `xurl whoami`
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


class _CacheDirCase(unittest.TestCase):
    """Base: isolated CONFIG_DIR + clean TTL env for every test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        patcher = mock.patch.object(cli.env, "CONFIG_DIR", self.config_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("LAST30DAYS_DOCTOR_TTL", None)

    @property
    def cache_file(self) -> Path:
        return self.config_dir / doctor.CACHE_FILENAME

    @staticmethod
    def valid_report(marker="cached-sentinel-report"):
        """A report satisfying the render contract, carrying a marker."""
        return {
            "engine_version": marker,
            "config": {"global_env": None, "config_source": None},
            "setup": {"setup_complete": False, "keys_present": {}},
            "permissions": {"status": "ok"},
            "sources": {
                "hackernews": {
                    "tier": "ok", "status": "ok", "mode": "single",
                    "backends": None, "active_backend": None, "fix": "",
                    "requires": "none", "note": marker, "detail": "",
                    "pin_var": None, "pin_flag": None, "pinned": False,
                },
            },
        }

    def write_cache(
        self,
        *,
        seconds_ago=0.0,
        marker="cached-sentinel-report",
        config=None,
        schema=None,
        fingerprint=None,
        report=None,
    ):
        """Seed a valid cached payload (schema + fingerprint stamped)."""
        if report is None:
            report = self.valid_report(marker)
        payload = {
            "schema": doctor.DOCTOR_CACHE_SCHEMA_VERSION if schema is None else schema,
            "fingerprint": (
                doctor._config_fingerprint(dict(config or {}))
                if fingerprint is None
                else fingerprint
            ),
            "timestamp": _iso_utc(seconds_ago),
            "report": report,
        }
        self.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        return report

    def run_doctor(self, config=None, *, cached, emit_json=True):
        with _Hermetic() as h:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = doctor.run(dict(config or {}), emit_json=emit_json, cached=cached)
        return rc, stdout.getvalue(), h.probe_spy


class FreshCacheServed(_CacheDirCase):
    """Scenario 1: fresh cache within TTL -> stored result, zero probes."""

    def test_cached_returns_stored_report(self):
        self.write_cache(seconds_ago=1)
        rc, out, _ = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertIn("cached-sentinel-report", out)
        payload = json.loads(out)
        self.assertIn("sources", payload)

    def test_cached_spawns_zero_probes_and_no_live_aggregation(self):
        self.write_cache(seconds_ago=1)
        with mock.patch(
            "lib.doctor.build_report",
            side_effect=AssertionError("live aggregation must not run"),
        ) as build:
            rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertFalse(probe_spy.called, "probe layer must not be touched")
        self.assertFalse(build.called)

    def test_cached_hit_does_not_rewrite_cache(self):
        self.write_cache(seconds_ago=1)
        before = self.cache_file.read_text(encoding="utf-8")
        self.run_doctor(cached=True)
        self.assertEqual(before, self.cache_file.read_text(encoding="utf-8"))

    def test_cached_text_render_from_cache(self):
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
        self.assertEqual(0, rc)
        self.assertIn("last30days doctor", out)
        self.assertFalse(probe_spy.called)


class StaleOrMissingCacheRunsLive(_CacheDirCase):
    """Scenario 2: stale or missing cache -> live run rewrites the cache."""

    def test_stale_cache_falls_through_to_live_run(self):
        self.write_cache(seconds_ago=doctor.DEFAULT_CACHE_TTL_SECONDS * 2)
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called, "stale cache must trigger live probes")

    def test_stale_cache_is_rewritten(self):
        self.write_cache(seconds_ago=doctor.DEFAULT_CACHE_TTL_SECONDS * 2)
        self.run_doctor(cached=True)
        payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        self.assertNotIn("cached-sentinel-report", json.dumps(payload))
        self.assertIn("report", payload)
        self.assertIn("sources", payload["report"])
        # New timestamp is fresh.
        age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(
            payload["timestamp"]
        )
        self.assertLess(age.total_seconds(), 60)

    def test_missing_cache_runs_live_and_creates_file(self):
        self.assertFalse(self.cache_file.exists())
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertTrue(probe_spy.called)
        self.assertTrue(self.cache_file.exists())
        payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        self.assertEqual(
            set(doctor.SOURCE_ORDER), set(payload["report"]["sources"].keys())
        )


class ExplicitDoctorAlwaysLive(_CacheDirCase):
    """Scenario 3: no --cached -> live run even when a fresh cache exists."""

    def test_fresh_cache_ignored_without_cached_flag(self):
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor(cached=False)
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_live_run_refreshes_fresh_cache(self):
        self.write_cache(seconds_ago=1)
        self.run_doctor(cached=False)
        raw = self.cache_file.read_text(encoding="utf-8")
        self.assertNotIn("cached-sentinel-report", raw)
        self.assertIn("sources", json.loads(raw)["report"])


class TtlOverrideAndCorruptCache(_CacheDirCase):
    """Scenario 4: TTL env override respected; corrupt cache = absent."""

    def test_ttl_env_override_shrinks_window(self):
        self.write_cache(seconds_ago=10)
        os.environ["LAST30DAYS_DOCTOR_TTL"] = "1"
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_ttl_env_override_widens_window(self):
        self.write_cache(seconds_ago=doctor.DEFAULT_CACHE_TTL_SECONDS * 2)
        os.environ["LAST30DAYS_DOCTOR_TTL"] = str(doctor.DEFAULT_CACHE_TTL_SECONDS * 10)
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertIn("cached-sentinel-report", out)
        self.assertFalse(probe_spy.called)

    def test_ttl_from_config_layer(self):
        # Registered env key: a .env-set value reaches doctor via config.
        self.write_cache(seconds_ago=10)
        rc, out, probe_spy = self.run_doctor({"LAST30DAYS_DOCTOR_TTL": "1"}, cached=True)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_ttl_zero_disables_cache_reuse(self):
        self.write_cache(seconds_ago=0)
        os.environ["LAST30DAYS_DOCTOR_TTL"] = "0"
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_garbage_ttl_falls_back_to_default(self):
        self.write_cache(seconds_ago=1)
        os.environ["LAST30DAYS_DOCTOR_TTL"] = "not-a-number"
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertIn("cached-sentinel-report", out)
        self.assertFalse(probe_spy.called)

    def test_corrupt_cache_files_treated_as_absent(self):
        for corrupt in (
            "not json at all {",
            json.dumps(["a", "list"]),
            json.dumps({"timestamp": _iso_utc(), "report": "not-a-dict"}),
            json.dumps({"timestamp": "garbage-timestamp", "report": {"sources": {}}}),
            json.dumps({"report": {"sources": {}}}),  # no timestamp
            json.dumps({"timestamp": _iso_utc(), "report": {}}),  # no sources
        ):
            with self.subTest(corrupt=corrupt[:40]):
                self.cache_file.write_text(corrupt, encoding="utf-8")
                rc, out, probe_spy = self.run_doctor(cached=True)
                self.assertEqual(0, rc, "corrupt cache must never crash")
                self.assertTrue(probe_spy.called, "corrupt cache must fall through")
                # And the corrupt file is replaced by a valid one.
                payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self.assertIn("sources", payload["report"])

    def test_no_config_dir_runs_live_without_crash(self):
        with mock.patch.object(cli.env, "CONFIG_DIR", None):
            rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertTrue(probe_spy.called)

    def test_ttl_env_var_is_registered(self):
        # The repo foot-gun: unregistered keys are silently swallowed from
        # .env. LAST30DAYS_DOCTOR_TTL must be in env.py's get_config registry.
        import inspect

        from lib import env as env_mod

        self.assertIn("LAST30DAYS_DOCTOR_TTL", inspect.getsource(env_mod.get_config))


class DriftedCacheShapes(_CacheDirCase):
    """F3: cached reports that drift from the render contract fall through
    to a live run — never a KeyError crash — in text mode too."""

    def _write_payload(self, report, *, with_envelope=True, seconds_ago=1.0):
        payload = {"timestamp": _iso_utc(seconds_ago), "report": report}
        if with_envelope:
            payload["schema"] = doctor.DOCTOR_CACHE_SCHEMA_VERSION
            payload["fingerprint"] = doctor._config_fingerprint({})
        self.cache_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_fresh_drifted_report_text_mode_no_crash(self):
        # Exact F3 repro: fresh timestamp, report missing engine_version /
        # config / setup / permissions, record missing tier.
        self._write_payload({"sources": {"hackernews": {"status": "ok"}}})
        rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
        self.assertEqual(0, rc, "drifted cache must never crash")
        self.assertTrue(probe_spy.called, "drifted cache must fall through live")
        self.assertIn("last30days doctor", out)

    def test_fresh_drifted_report_pre_schema_envelope_no_crash(self):
        # The original repro shape (no schema stamp at all).
        self._write_payload(
            {"sources": {"hackernews": {"status": "ok"}}}, with_envelope=False
        )
        rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
        self.assertEqual(0, rc)
        self.assertTrue(probe_spy.called)

    def test_shape_validator_rejects_each_drift(self):
        good = self.valid_report()
        drifted = []
        for key in ("engine_version", "config", "setup", "permissions"):
            broken = json.loads(json.dumps(good))
            del broken[key]
            drifted.append((f"missing {key}", broken))
        for key in ("config", "setup", "permissions"):
            broken = json.loads(json.dumps(good))
            broken[key] = "not-a-dict"
            drifted.append((f"{key} not a dict", broken))
        broken = json.loads(json.dumps(good))
        broken["sources"]["hackernews"] = "not-a-record"
        drifted.append(("record not a dict", broken))
        broken = json.loads(json.dumps(good))
        broken["sources"]["hackernews"]["tier"] = "sideways"
        drifted.append(("unknown tier", broken))
        broken = json.loads(json.dumps(good))
        del broken["sources"]["hackernews"]["tier"]
        drifted.append(("missing tier", broken))
        broken = json.loads(json.dumps(good))
        broken["sources"]["hackernews"]["status"] = 7
        drifted.append(("non-str status", broken))
        for label, report in drifted:
            with self.subTest(drift=label):
                self._write_payload(report)
                rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
                self.assertEqual(0, rc, f"{label}: must never crash")
                self.assertTrue(probe_spy.called, f"{label}: must fall through")

    def test_run_survives_shapes_the_validator_misses(self):
        # Even if a bad shape slips past read_cached_report, run()'s
        # try/except falls through to a live build (never-crash contract).
        bad = self.valid_report()
        with mock.patch(
            "lib.doctor.read_cached_report",
            return_value={"sources": bad["sources"]},  # renders would KeyError
        ):
            rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
        self.assertEqual(0, rc)
        self.assertTrue(probe_spy.called)
        self.assertIn("last30days doctor", out)


class SchemaStamp(_CacheDirCase):
    """F8: payloads without the current schema stamp are treated as absent."""

    def test_schema_mismatch_runs_live(self):
        self.write_cache(seconds_ago=1, schema="last30days-doctor-cache/v0")
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_absent_schema_runs_live(self):
        payload = {
            "fingerprint": doctor._config_fingerprint({}),
            "timestamp": _iso_utc(1),
            "report": self.valid_report(),
        }
        self.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_live_run_stamps_schema_and_fingerprint(self):
        self.run_doctor(cached=False)
        payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        self.assertEqual(doctor.DOCTOR_CACHE_SCHEMA_VERSION, payload["schema"])
        self.assertEqual(doctor._config_fingerprint({}), payload["fingerprint"])


class FingerprintInvalidation(_CacheDirCase):
    """F12a: credential/pin/opt-in changes invalidate the cache."""

    def test_key_added_invalidates(self):
        self.write_cache(seconds_ago=1)  # fingerprint for empty config
        rc, out, probe_spy = self.run_doctor(
            {"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"}, cached=True
        )
        self.assertEqual(0, rc)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called, "new credential must invalidate cache")

    def test_key_removed_invalidates(self):
        cfg = {"SCRAPECREATORS_API_KEY": "dummy-sc-secret-000"}
        self.write_cache(seconds_ago=1, config=cfg)
        rc, out, probe_spy = self.run_doctor({}, cached=True)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called, "removed credential must invalidate cache")

    def test_pin_change_invalidates(self):
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor(
            {"LAST30DAYS_X_BACKEND": "bird"}, cached=True
        )
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called, "pin change must invalidate cache")

    def test_include_sources_change_invalidates(self):
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor({"INCLUDE_SOURCES": "linkedin"}, cached=True)
        self.assertNotIn("cached-sentinel-report", out)
        self.assertTrue(probe_spy.called)

    def test_fingerprint_ignores_non_signal_config(self):
        # TTL knob is not a fingerprint signal; same-fingerprint serve holds.
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor(
            {"LAST30DAYS_DOCTOR_TTL": str(doctor.DEFAULT_CACHE_TTL_SECONDS)},
            cached=True,
        )
        self.assertIn("cached-sentinel-report", out)
        self.assertFalse(probe_spy.called)


class StalenessSignals(_CacheDirCase):
    """F12b: from_cache + generated_at surfaced on every doctor report."""

    def test_matching_fingerprint_serves_cache_with_signals(self):
        self.write_cache(seconds_ago=120)
        original_ts = json.loads(self.cache_file.read_text(encoding="utf-8"))["timestamp"]
        rc, out, probe_spy = self.run_doctor(cached=True)
        self.assertEqual(0, rc)
        self.assertFalse(probe_spy.called)
        data = json.loads(out)
        self.assertTrue(data["from_cache"])
        self.assertEqual(original_ts, data["generated_at"])

    def test_live_run_marks_from_cache_false_with_fresh_generated_at(self):
        rc, out, probe_spy = self.run_doctor(cached=False)
        self.assertTrue(probe_spy.called)
        data = json.loads(out)
        self.assertFalse(data["from_cache"])
        age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(
            data["generated_at"]
        )
        self.assertLess(age.total_seconds(), 60)

    def test_text_mode_prints_cache_status_line(self):
        self.write_cache(seconds_ago=1)
        rc, out, probe_spy = self.run_doctor(cached=True, emit_json=False)
        self.assertFalse(probe_spy.called)
        self.assertIn("generated:", out)
        self.assertIn("(cached)", out)

    def test_text_mode_live_status_line(self):
        rc, out, _ = self.run_doctor(cached=False, emit_json=False)
        self.assertIn("generated:", out)
        self.assertIn("(live)", out)


class CacheWriteFailureWarns(_CacheDirCase):
    """F11: a failing cache write warns on stderr and stays non-fatal."""

    def test_write_failure_warns_and_exits_zero(self):
        with _Hermetic() as h:
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(
                Path, "write_text", side_effect=OSError("disk full")
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                rc = doctor.run({}, emit_json=True, cached=False)
        self.assertEqual(0, rc, "cache write failure must never be fatal")
        self.assertTrue(h.probe_spy.called)
        err = stderr.getvalue()
        self.assertIn("WARNING", err)
        self.assertIn("doctor cache", err)
        self.assertIn("disk full", err)
        # The report itself still rendered.
        self.assertIn("sources", json.loads(stdout.getvalue()))


class NoSecretsInCacheFile(_CacheDirCase):
    """Scenario 5: seeded fake credentials never land in the cache file."""

    def test_cache_file_has_no_secret_values(self):
        rc, out, _ = self.run_doctor(dict(FAKE_SECRETS), cached=False)
        self.assertEqual(0, rc)
        raw = self.cache_file.read_text(encoding="utf-8")
        for var, secret in FAKE_SECRETS.items():
            if var == "BSKY_HANDLE":
                continue  # a handle is an identifier, not a credential
            self.assertNotIn(secret, raw, var)

    def test_fingerprint_field_carries_no_secret_values(self):
        rc, out, _ = self.run_doctor(dict(FAKE_SECRETS), cached=False)
        self.assertEqual(0, rc)
        payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        fingerprint = payload["fingerprint"]
        # An opaque sha256 hex digest only — no raw values of any kind.
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        for var, secret in FAKE_SECRETS.items():
            self.assertNotIn(secret, fingerprint, var)


class CliCachedPassthrough(_CacheDirCase):
    """`doctor --cached` is an accepted passthrough flag wired to doctor.run."""

    def _cli(self, argv):
        with mock.patch("lib.doctor.run", return_value=0) as run, \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(sys, "argv", ["last30days.py"] + argv):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        return rc, run

    def test_cached_flag_passes_through(self):
        rc, run = self._cli(["doctor", "--cached"])
        self.assertEqual(0, rc)
        self.assertTrue(run.call_args.kwargs.get("cached"))

    def test_cached_json_combination(self):
        rc, run = self._cli(["doctor", "--cached", "--json"])
        self.assertEqual(0, rc)
        self.assertTrue(run.call_args.kwargs.get("cached"))
        self.assertTrue(run.call_args.kwargs.get("emit_json"))

    def test_plain_doctor_is_live(self):
        rc, run = self._cli(["doctor"])
        self.assertEqual(0, rc)
        self.assertFalse(run.call_args.kwargs.get("cached"))

    def test_cached_rejected_for_research_topics(self):
        with mock.patch.object(sys, "argv", ["last30days.py", "some", "topic", "--cached"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        self.assertIn("--cached", stderr.getvalue())


class DoctorSkillContract(unittest.TestCase):
    """Scenario 6: SKILL.md integration locked against silent erosion
    (same read-the-runtime-contract pattern as test_onboarding_contract.py)."""

    @classmethod
    def setUpClass(cls):
        cls.text = SKILL_MD.read_text(encoding="utf-8")

    def test_doctor_trigger_phrases_present(self):
        for phrase in (
            "health check",
            "is X working",
            "why is a source missing",
            "what's broken",
        ):
            self.assertIn(phrase, self.text, f"missing doctor trigger phrase: {phrase!r}")

    def test_cached_standing_rule_present(self):
        self.assertIn("doctor --cached --json", self.text)
        self.assertIn("login-backed", self.text)
        self.assertIn("doctor-cache.json", self.text)
        self.assertIn("LAST30DAYS_DOCTOR_TTL", self.text)

    def test_rerun_live_only_when_stale_or_degraded(self):
        self.assertIn("stale", self.text)
        self.assertIn("degraded login-backed source", self.text)

    def test_standing_rule_is_marked_mandatory(self):
        # The pre-research cache consult is a must-fire rule; it uses the
        # same bold MANDATORY marker style as Step 0.45 so it cannot read
        # as soft advisory prose (F16b).
        self.assertIn("**MANDATORY standing rule.**", self.text)

    def test_frontmatter_description_carries_health_check_keywords(self):
        # Cold-start prompts like "is my last30days X search broken?" can
        # only load the skill if the machine-parsed frontmatter description
        # mentions the health surface (F16a). It must stay ONE line.
        lines = self.text.splitlines()
        self.assertEqual("---", lines[0])
        closing = lines[1:].index("---") + 1
        frontmatter = lines[1:closing]
        desc_lines = [l for l in frontmatter if l.startswith("description:")]
        self.assertEqual(1, len(desc_lines), "description must be one line")
        desc = desc_lines[0].lower()
        for needle in ("doctor", "health check", "diagnose", "broken"):
            self.assertIn(needle, desc, f"description missing keyword: {needle!r}")

    def test_predicted_backend_announcement(self):
        self.assertIn("active_backend", self.text)

    def test_configuration_md_documents_doctor(self):
        config_text = (ROOT / "CONFIGURATION.md").read_text(encoding="utf-8")
        for needle in (
            "doctor --json",
            "doctor --cached",
            "doctor-cache.json",
            "LAST30DAYS_DOCTOR_TTL",
            "LAST30DAYS_X_BACKEND",
            "LAST30DAYS_REDDIT_BACKEND",
            "--web-backend",
        ):
            self.assertIn(needle, config_text, f"CONFIGURATION.md missing: {needle!r}")


if __name__ == "__main__":
    unittest.main()
