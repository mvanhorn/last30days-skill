import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import last30days as cli
from lib import schema

REPO_ROOT = Path(__file__).resolve().parents[1]
LAST30DAYS_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"
SKILL_MD = REPO_ROOT / "skills" / "last30days" / "SKILL.md"


def run_last30days(topic: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAST30DAYS_SCRIPT), topic, "--mock", "--emit=json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _report(topic: str) -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-05-01",
        range_to="2026-05-31",
        generated_at="2026-05-31T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=schema.QueryPlan(
            intent="concept",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query=topic,
                    ranking_query=topic,
                    sources=["grounding"],
                )
            ],
            source_weights={"grounding": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"grounding": []},
        errors_by_source={},
    )


def _diag() -> dict[str, object]:
    return {
        "available_sources": ["grounding"],
        "providers": {"google": True, "openai": False, "xai": False},
        "x_backend": None,
        "bird_installed": True,
        "bird_authenticated": False,
        "bird_username": None,
        "native_web_backend": "brave",
    }


class LastRunStateTests(unittest.TestCase):
    def test_empty_config_override_disables_last_run_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["LAST30DAYS_CONFIG_DIR"] = ""

            result = run_last30days("synthetic eval query", env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / ".config" / "last30days" / "last-run.json").exists())

    def test_custom_config_override_writes_last_run_to_custom_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = run_last30days("custom config query", env)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((config_dir / "last-run.json").read_text())
            self.assertEqual(payload["topic"], "custom config query")
            self.assertGreaterEqual(payload["total"], 0)
            self.assertEqual(str(config_dir / "last-report.json"), payload["report_cache"])
            self.assertTrue((config_dir / "last-report.json").exists())

    def test_last_report_cache_round_trips_single_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                report = _report("OpenClaw")
                cli._write_last_run("OpenClaw", report)
                loaded = cli._load_last_report_cache("OpenClaw")

            self.assertIsNotNone(loaded)
            cached_report, entity_reports, cache_path = loaded
            self.assertEqual("OpenClaw", cached_report.topic)
            self.assertIsNone(entity_reports)
            self.assertEqual(config_dir / "last-report.json", cache_path)

    def test_last_report_cache_expires_after_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("OpenClaw", _report("OpenClaw"))
                cache_path = config_dir / "last-report.json"
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                payload["timestamp"] = "2026-01-01T00:00:00+00:00"
                cache_path.write_text(json.dumps(payload), encoding="utf-8")
                loaded = cli._load_last_report_cache("OpenClaw", ttl_seconds=3600)

            self.assertIsNone(loaded)

    def test_last_report_cache_ttl_zero_disables_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("OpenClaw", _report("OpenClaw"))
                loaded = cli._load_last_report_cache("OpenClaw", ttl_seconds=0)

            self.assertIsNone(loaded)

    def test_partial_comparison_cache_does_not_degrade_to_single_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            reports = [("Alpha", _report("Alpha")), ("Beta", _report("Beta"))]
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("Alpha vs Beta", reports[0][1], entity_reports=reports)
                cache_path = config_dir / "last-report.json"
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                payload["reports"] = payload["reports"][:1]
                cache_path.write_text(json.dumps(payload), encoding="utf-8")
                loaded = cli._load_last_report_cache("Alpha vs Beta")

            self.assertIsNone(loaded)

    def test_html_synthesis_reuses_cached_single_report_without_pipeline_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            synthesis_path = Path(tmp) / "synthesis.md"
            synthesis_path.write_text("# OpenClaw\n\nCached synthesis body.", encoding="utf-8")
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("OpenClaw", _report("OpenClaw"))

            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
                 mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("pipeline should not run")), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--synthesis-file",
                     str(synthesis_path),
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertIn("Cached synthesis body.", stdout.getvalue())
            self.assertIn("Reusing cached report data", stderr.getvalue())

    def test_html_synthesis_reuses_cached_comparison_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            synthesis_path = Path(tmp) / "synthesis.md"
            synthesis_path.write_text(
                "# Alpha vs Beta\n\nCached comparison body.",
                encoding="utf-8",
            )
            reports = [("Alpha", _report("Alpha")), ("Beta", _report("Beta"))]
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("Alpha vs Beta", reports[0][1], entity_reports=reports)

            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
                 mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("pipeline should not run")), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "Alpha",
                     "vs",
                     "Beta",
                     "--emit=html",
                     "--synthesis-file",
                     str(synthesis_path),
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertIn("Cached comparison body.", stdout.getvalue())
            self.assertIn("last30days · Alpha vs Beta", stdout.getvalue())
            self.assertIn("Reusing cached report data", stderr.getvalue())

    def test_html_synthesis_warns_and_falls_back_when_cache_topic_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            synthesis_path = Path(tmp) / "synthesis.md"
            synthesis_path.write_text("Cached synthesis body.", encoding="utf-8")
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("OpenClaw", _report("OpenClaw"))

            fresh_report = _report("Different Topic")
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
                 mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=fresh_report) as run_mock, \
                 mock.patch.object(cli.ui, "ProgressDisplay"), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "Different",
                     "Topic",
                     "--emit=html",
                     "--synthesis-file",
                     str(synthesis_path),
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertTrue(run_mock.called)
            self.assertIn("No matching cached report data", stderr.getvalue())
            self.assertIn("Cached synthesis body.", stdout.getvalue())

    def test_html_synthesis_falls_back_when_cache_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            synthesis_path = Path(tmp) / "synthesis.md"
            synthesis_path.write_text("Cached synthesis body.", encoding="utf-8")
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
                cli._write_last_run("OpenClaw", _report("OpenClaw"))
                cache_path = config_dir / "last-report.json"
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                payload["timestamp"] = "2026-01-01T00:00:00+00:00"
                cache_path.write_text(json.dumps(payload), encoding="utf-8")

            fresh_report = _report("OpenClaw")
            with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
                 mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=fresh_report) as run_mock, \
                 mock.patch.object(cli.ui, "ProgressDisplay"), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--synthesis-file",
                     str(synthesis_path),
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertTrue(run_mock.called)
            self.assertIn("No matching cached report data", stderr.getvalue())
            self.assertIn("Cached synthesis body.", stdout.getvalue())

    @unittest.skipIf(shutil.which("bash") is None, "bash not available")
    def test_hook_reads_last_run_from_custom_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            config_dir.mkdir()
            (config_dir / "last-run.json").write_text(
                json.dumps(
                    {
                        "topic": "custom hook query",
                        "timestamp": "2026-04-30T00:00:00+00:00",
                        "sources": {"reddit": 2},
                        "total": 2,
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Last run: "custom hook query"', result.stdout)

    def test_hook_exits_0_when_no_last_run(self):
        """Script exits 0 when ScrapeCreators configured but no prior run (last-run.json absent)."""
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["SETUP_COMPLETE"] = "true"
            env["ENV_SCRAPECREATORS_API_KEY"] = "sk-test"

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ready —", result.stdout)
            self.assertNotIn("Last run:", result.stdout)

    def test_hook_parses_dotenv_with_unbalanced_quote(self):
        """Script exits 0 when .env contains an unbalanced quote in a value."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config_dir = home / ".config" / "last30days"
            config_dir.mkdir(parents=True)
            env_file = config_dir / ".env"
            env_file.write_text(
                "SETUP_COMPLETE=true\n"
                "XAI_API_KEY=xai-key-with-apostrophe's-ok\n"
                "AUTH_TOKEN=test-auth\n"
                "CT0=test-ct0\n"
            )
            env = os.environ.copy()
            env["HOME"] = str(home)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ready —", result.stdout)

    @staticmethod
    def _extract_source_count(output: str) -> int:
        match = re.search(r"Ready — (\d+) sources active", output)
        if not match:
            raise AssertionError(f"Could not find source count in: {repr(output[:200])}")
        return int(match.group(1))

    def _run_hook(self, tmp: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(Path(tmp) / "home")
        env["SETUP_COMPLETE"] = "true"
        # Strip credentials that could bleed in from the test-runner environment
        # and corrupt source-count baseline comparisons.
        for key in ("AUTH_TOKEN", "CT0", "XAI_API_KEY", "BSKY_HANDLE", "EXA_API_KEY", "SCRAPECREATORS_API_KEY"):
            env.pop(key, None)
        env.update(env_overrides)
        return subprocess.run(
            ["bash", "hooks/scripts/check-config.sh"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_x_not_counted_with_only_auth_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            only_auth = self._extract_source_count(
                self._run_hook(tmp, {"AUTH_TOKEN": "test_auth"}).stdout
            )
            self.assertEqual(
                only_auth, neither,
                "X should not be counted when only AUTH_TOKEN is set (CT0 missing)",
            )

    def test_x_not_counted_with_only_ct0(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            only_ct0 = self._extract_source_count(
                self._run_hook(tmp, {"CT0": "test_ct0"}).stdout
            )
            self.assertEqual(
                only_ct0, neither,
                "X should not be counted when only CT0 is set (AUTH_TOKEN missing)",
            )

    def test_x_counted_when_both_auth_token_and_ct0(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            both = self._extract_source_count(
                self._run_hook(tmp, {"AUTH_TOKEN": "test_auth", "CT0": "test_ct0"}).stdout
            )
            self.assertEqual(
                both, neither + 1,
                "X should add 1 source when both AUTH_TOKEN and CT0 are set",
            )

    def test_hook_shows_last_run_when_json_exists(self):
        """Script exits 0 and shows last-run summary when last-run.json exists."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            config_dir.mkdir()
            (config_dir / "last-run.json").write_text(
                json.dumps(
                    {
                        "topic": "prior research",
                        "timestamp": "2026-06-01T12:00:00+00:00",
                        "sources": {"reddit": 5},
                        "total": 5,
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["SETUP_COMPLETE"] = "true"
            env["ENV_SCRAPECREATORS_API_KEY"] = "sk-test"
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Last run: "prior research"', result.stdout)


class TestSkillMdFirstRunReference(unittest.TestCase):
    """Verifies SKILL.md references that exist in the CLI."""

    def test_nux_wizard_not_referenced(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn(
            "nux-wizard.md", content,
            "SKILL.md should not reference the missing nux-wizard.md file",
        )

    def test_skill_md_references_setup_command(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn(
            "last30days.py setup", content,
            "SKILL.md should reference the Python setup subcommand",
        )

    def test_setup_subcommand_dispatches(self):
        """topic 'setup' must reach setup_wizard, not be swallowed by argparse."""
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as mock_setup, \
             mock.patch("lib.setup_wizard.write_setup_config") as mock_write, \
             mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        mock_setup.assert_called_once()
        mock_write.assert_called_once()


class TestCheckPermsAutoFix(unittest.TestCase):
    """check_perms should auto-fix loose .env permissions instead of warning only."""

    def test_loose_env_is_tightened_by_check_perms(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "last30days"
            config_dir.mkdir(parents=True)
            env_file = config_dir / ".env"
            env_file.write_text("SETUP_COMPLETE=true\n")
            os.chmod(env_file, 0o644)

            env = os.environ.copy()
            env["HOME"] = str(Path(tmp))
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("auto-fixed", result.stdout.lower())
            self.assertEqual(stat.S_IMODE(os.stat(env_file).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
