import json
import io
import shutil
import tempfile
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import last30days as cli
from lib import schema

REPO_ROOT = Path(__file__).resolve().parents[1]


class CliV3Tests(unittest.TestCase):
    def make_report(self, topic: str = "OpenClaw vs NanoClaw") -> schema.Report:
        return schema.Report(
            topic=topic,
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite",
                rerank_model="gemini-3.1-flash-lite",
            ),
            query_plan=schema.QueryPlan(
                intent="comparison",
                freshness_mode="balanced_recent",
                cluster_mode="debate",
                raw_topic=topic,
                subqueries=[
                    schema.SubQuery(
                        label="primary",
                        search_query=topic.lower(),
                        ranking_query=f"What are people saying about {topic}?",
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

    def test_mock_json_cli(self):
        result = subprocess.run(
            [sys.executable, "skills/last30days/scripts/last30days.py", "test topic", "--mock", "--emit=json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("query_plan", payload)
        self.assertIn("ranked_candidates", payload)
        self.assertIn("clusters", payload)

    def test_invalid_plan_json_exits_nonzero(self):
        """Malformed --plan JSON must fail fast, not silently fall back to the
        internal planner and burn a paid run the user did not ask for."""
        result = subprocess.run(
            [
                sys.executable,
                "skills/last30days/scripts/last30days.py",
                "test topic",
                "--mock",
                "--emit=json",
                "--plan",
                "{not valid json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Invalid --plan JSON", result.stderr)

    def test_parse_search_flag_normalizes_aliases_and_dedupes(self):
        self.assertEqual(
            ["grounding", "reddit", "hackernews"],
            cli.parse_search_flag("web, reddit, hn, web"),
        )

    def test_parse_search_flag_accepts_optional_social_sources(self):
        self.assertEqual(
            ["threads", "pinterest"],
            cli.parse_search_flag("threads, pinterest"),
        )

    def test_explicit_threads_search_uses_scrapecreators_key_without_include_sources(self):
        available = cli.pipeline.available_sources(
            {"SCRAPECREATORS_API_KEY": "test-key", "INCLUDE_SOURCES": ""},
            requested_sources=["threads"],
        )
        self.assertIn("threads", available)

    def test_explicit_perplexity_search_uses_openrouter_key_without_include_sources(self):
        available = cli.pipeline.available_sources(
            {"OPENROUTER_API_KEY": "test-key", "INCLUDE_SOURCES": ""},
            requested_sources=["perplexity"],
        )
        self.assertIn("perplexity", available)

    def test_explicit_perplexity_search_uses_direct_key_without_include_sources(self):
        available = cli.pipeline.available_sources(
            {"PERPLEXITY_API_KEY": "test-key", "INCLUDE_SOURCES": ""},
            requested_sources=["perplexity"],
        )
        self.assertIn("perplexity", available)

    def test_parse_search_flag_rejects_invalid_or_empty_inputs(self):
        with self.assertRaises(SystemExit):
            cli.parse_search_flag("unknown")
        with self.assertRaises(SystemExit):
            cli.parse_search_flag(" , ")

    def test_resolve_requested_sources_flag_wins_over_config_default(self):
        sources = cli.resolve_requested_sources(
            "reddit", {"LAST30DAYS_DEFAULT_SEARCH": "x,youtube"},
        )
        self.assertEqual(["reddit"], sources)

    def test_resolve_requested_sources_falls_back_to_config_default(self):
        sources = cli.resolve_requested_sources(
            None, {"LAST30DAYS_DEFAULT_SEARCH": "web, reddit, hn"},
        )
        self.assertEqual(["grounding", "reddit", "hackernews"], sources)

    def test_resolve_requested_sources_none_when_neither_set(self):
        self.assertIsNone(cli.resolve_requested_sources(None, {}))
        self.assertIsNone(
            cli.resolve_requested_sources(None, {"LAST30DAYS_DEFAULT_SEARCH": ""})
        )
        self.assertIsNone(
            cli.resolve_requested_sources(None, {"LAST30DAYS_DEFAULT_SEARCH": "  "})
        )

    def test_resolve_requested_sources_invalid_config_default_names_env_var(self):
        with self.assertRaises(SystemExit) as exc:
            cli.resolve_requested_sources(
                None, {"LAST30DAYS_DEFAULT_SEARCH": "notasource"},
            )
        self.assertIn("LAST30DAYS_DEFAULT_SEARCH", str(exc.exception))

    def test_build_parser_accepts_days_alias_and_preserves_topic_tokens(self):
        parser = cli.build_parser()
        args, extra = parser.parse_known_args(["--days", "7", "biosecurity", "ai", "agents"])
        self.assertEqual(7, args.lookback_days)
        self.assertEqual(["biosecurity", "ai", "agents"], args.topic)
        self.assertEqual([], extra)

    def test_build_parser_accepts_explicit_output_file(self):
        parser = cli.build_parser()
        args, extra = parser.parse_known_args(
            ["--emit", "json", "--output", "results/run.json", "biosecurity"]
        )
        self.assertEqual("results/run.json", args.output)
        self.assertEqual(["biosecurity"], args.topic)
        self.assertEqual([], extra)

    def test_research_unknown_flag_fails_before_config_load(self):
        with mock.patch.object(
            cli.env, "get_config", side_effect=AssertionError("config should not load")
        ), mock.patch.object(sys, "argv", ["last30days.py", "topic", "--save"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        self.assertIn("--save", stderr.getvalue())

    def test_agent_is_skill_argument_not_python_cli_flag(self):
        with mock.patch.object(
            cli.env, "get_config", side_effect=AssertionError("config should not load")
        ), mock.patch.object(sys, "argv", ["last30days.py", "topic", "--agent"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        self.assertIn("skill arguments", stderr.getvalue())

    def test_agent_error_includes_other_unknown_flags(self):
        with mock.patch.object(
            cli.env, "get_config", side_effect=AssertionError("config should not load")
        ), mock.patch.object(sys, "argv", ["last30days.py", "topic", "--agent", "--save"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        message = stderr.getvalue()
        self.assertIn("--agent", message)
        self.assertIn("--save", message)

    def test_setup_passthrough_flags_remain_scoped_to_setup(self):
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch("lib.setup_wizard.run_github_auth", return_value={"status": "cancelled"}), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup", "--github"]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)

    def test_setup_rejects_unknown_passthrough_flag_before_config_load(self):
        with mock.patch.object(
            cli.env, "get_config", side_effect=AssertionError("config should not load")
        ), mock.patch.object(sys, "argv", ["last30days.py", "setup", "--bad"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(2, exc.exception.code)
        self.assertIn("--bad", stderr.getvalue())

    def test_ensure_supported_python_rejects_old_interpreter_with_actionable_error(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                cli.ensure_supported_python((3, 9, 6))
        self.assertEqual(1, exc.exception.code)
        message = stderr.getvalue()
        self.assertIn("last30days v3 requires Python 3.12+", message)
        self.assertIn("Detected Python 3.9.6", message)
        self.assertIn("python3.12", message)

    def test_ensure_supported_python_allows_supported_interpreter(self):
        cli.ensure_supported_python((3, 12, 0))

    def test_missing_sources_for_promo_prefers_reddit_x_then_web(self):
        self.assertEqual(
            "both",
            cli._missing_sources_for_promo({"available_sources": ["youtube"]}),
        )
        self.assertEqual(
            "web",
            cli._missing_sources_for_promo({"available_sources": ["reddit", "x"]}),
        )
        # The web promo is satisfied by a paid backend (better web search), not
        # by the keyless grounding floor — keyless web is always available now.
        self.assertIsNone(
            cli._missing_sources_for_promo(
                {"available_sources": ["reddit", "x", "grounding"], "native_web_backend": "brave"}
            ),
        )
        # ...or suppressed entirely on a native-search host.
        self.assertIsNone(
            cli._missing_sources_for_promo(
                {"available_sources": ["reddit", "x", "grounding"], "native_search": True}
            ),
        )

    def test_slugify_and_emit_output_cover_supported_modes(self):
        report = self.make_report()
        self.assertEqual("openclaw-vs-nanoclaw", cli.slugify(report.topic))
        self.assertEqual("last30days CLI.", cli.__doc__)

        compact = cli.emit_output(report, "compact")
        json_output = cli.emit_output(report, "json")
        context = cli.emit_output(report, "context")
        brief = cli.emit_output(report, "brief")

        self.assertIn("# last30days v", compact)
        self.assertIn('"topic": "OpenClaw vs NanoClaw"', json_output)
        self.assertIsInstance(context, str)
        self.assertIn("# Production Brief:", brief)

        with self.assertRaises(SystemExit):
            cli.emit_output(report, "bad-mode")

    def test_save_output_writes_expected_extension(self):
        report = self.make_report()
        with tempfile.TemporaryDirectory() as tmp:
            path = cli.save_output(report, "json", tmp)
            self.assertEqual(".json", path.suffix)
            payload = json.loads(path.read_text())
            self.assertEqual("OpenClaw vs NanoClaw", payload["topic"])

    def test_save_output_writes_utf8_encoded_markdown(self):
        report = self.make_report()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pathlib.Path.write_text", autospec=True, return_value=1) as write_text:
                cli.save_output(report, "md", tmp)
        _, kwargs = write_text.call_args
        self.assertEqual("utf-8", kwargs.get("encoding"))

    def test_save_rendered_output_writes_exact_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "nested" / "results.json"
            saved = cli.save_rendered_output('{"ok": true}', str(out_path))
            self.assertEqual(out_path.resolve(), saved)
            self.assertEqual('{"ok": true}', out_path.read_text(encoding="utf-8"))

    def test_compute_save_path_display_uses_posix_slashes_under_home(self):
        # Regression: f"~/{relative}" stringified pathlib.Path with the
        # OS-native separator, producing "~/Documents\\Last30Days\\..." on
        # Windows that no shell or File Explorer could open. The fix is
        # f"~/{relative.as_posix()}" which forces forward slashes regardless
        # of host OS. On POSIX hosts this asserts the contract for
        # cross-platform safety; on Windows hosts it would fail without the fix.
        real_home = Path.home()
        tmp_under_home = Path(tempfile.mkdtemp(prefix="l30d_save_path_", dir=str(real_home)))
        try:
            save_dir = tmp_under_home / "Documents" / "Last30Days"
            save_dir.mkdir(parents=True, exist_ok=True)
            display = cli.compute_save_path_display(
                str(save_dir), "british airways middle east", "v3", "compact"
            )
            self.assertTrue(display.startswith("~/"), f"Expected '~/' prefix, got: {display}")
            self.assertNotIn("\\", display, f"Backslash leaked into display: {display}")
            self.assertTrue(
                display.endswith("british-airways-middle-east-raw-v3.md"),
                f"Expected slug+suffix at end, got: {display}",
            )
        finally:
            shutil.rmtree(tmp_under_home, ignore_errors=True)

    def test_compute_output_path_display_uses_posix_slashes_under_home(self):
        real_home = Path.home()
        tmp_under_home = Path(tempfile.mkdtemp(prefix="l30d_output_path_", dir=str(real_home)))
        try:
            output_path = tmp_under_home / "Documents" / "Last30Days" / "run.json"
            display = cli.compute_output_path_display(str(output_path))
            self.assertTrue(display.startswith("~/"), f"Expected '~/' prefix, got: {display}")
            self.assertNotIn("\\", display, f"Backslash leaked into display: {display}")
            self.assertTrue(display.endswith("Documents/Last30Days/run.json"), display)
        finally:
            shutil.rmtree(tmp_under_home, ignore_errors=True)

    def test_persist_report_updates_run_status_on_success_and_failure(self):
        report = self.make_report()

        success_store = types.SimpleNamespace(
            init_db=mock.Mock(),
            add_topic=mock.Mock(return_value={"id": 7}),
            record_run=mock.Mock(return_value=11),
            findings_from_report=mock.Mock(return_value=[{"title": "x"}]),
            store_findings=mock.Mock(return_value={"new": 2, "updated": 1}),
            update_run=mock.Mock(),
        )
        with mock.patch.dict(sys.modules, {"store": success_store}):
            counts = cli.persist_report(report)
        self.assertEqual({"new": 2, "updated": 1}, counts)
        success_store.update_run.assert_called_once_with(
            11,
            status="completed",
            findings_new=2,
            findings_updated=1,
        )

        failure_store = types.SimpleNamespace(
            init_db=mock.Mock(),
            add_topic=mock.Mock(return_value={"id": 7}),
            record_run=mock.Mock(return_value=12),
            findings_from_report=mock.Mock(side_effect=RuntimeError("boom")),
            store_findings=mock.Mock(),
            update_run=mock.Mock(),
        )
        with mock.patch.dict(sys.modules, {"store": failure_store}):
            with self.assertRaises(RuntimeError):
                cli.persist_report(report)
        failure_store.update_run.assert_called_once()
        _, kwargs = failure_store.update_run.call_args
        self.assertEqual("failed", kwargs["status"])
        self.assertIn("boom", kwargs["error_message"])

    def test_main_wires_banner_and_progress_display(self):
        report = self.make_report()
        diag = {
            "available_sources": ["grounding", "youtube"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
            "native_web_backend": "brave",
        }
        fake_progress = mock.Mock()
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(cli.pipeline, "diagnose", return_value=diag), \
             mock.patch.object(cli.pipeline, "run", return_value=report), \
             mock.patch.object(cli.ui, "show_diagnostic_banner") as banner, \
             mock.patch.object(cli.ui, "ProgressDisplay", return_value=fake_progress) as progress_cls, \
             mock.patch.object(cli, "emit_output", return_value="# rendered"), \
             mock.patch.object(sys, "argv", ["last30days.py", "test", "topic"]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        banner.assert_not_called()  # Banner moved to post-research
        progress_cls.assert_called_once_with("test topic", show_banner=True)
        fake_progress.start_processing.assert_called_once()
        fake_progress.end_processing.assert_called_once()
        fake_progress.show_complete.assert_called_once_with(
            source_counts={"grounding": 0},
            display_sources=["grounding"],
        )
        fake_progress.show_promo.assert_called_once_with("both", diag=diag)
        self.assertIn("# rendered", stdout.getvalue())

    def test_main_writes_rendered_output_to_explicit_file(self):
        report = self.make_report()
        diag = {
            "available_sources": ["grounding"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
            "native_web_backend": "brave",
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "exports" / "run.json"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=diag), \
                 mock.patch.object(cli.pipeline, "run", return_value=report), \
                 mock.patch.object(cli, "emit_output", return_value='{"rendered": true}') as emit, \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "test",
                     "topic",
                     "--emit=json",
                     "--output",
                     str(output_path),
                 ]):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()
            self.assertEqual(0, rc)
            emit.assert_called_once()
            self.assertEqual('{"rendered": true}\n', stdout.getvalue())
            self.assertEqual('{"rendered": true}', output_path.read_text(encoding="utf-8"))
            self.assertIn(f"[last30days] Saved output to {output_path.resolve()}", stderr.getvalue())

    def test_main_combines_output_and_save_dir_for_comparison_html(self):
        diag = {
            "available_sources": ["grounding"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
            "native_web_backend": "brave",
        }
        fake_progress = mock.Mock()

        def run_report(*_args, **kwargs):
            return self.make_report(topic=kwargs["topic"])

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "exports" / "comparison.html"
            save_dir = Path(tmp) / "saved"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=diag), \
                 mock.patch.object(cli.pipeline, "run", side_effect=run_report), \
                 mock.patch.object(cli.ui, "ProgressDisplay", return_value=fake_progress), \
                 mock.patch.object(
                     cli, "emit_comparison_output", return_value="<html>comparison</html>"
                 ) as emit_comparison, \
                 mock.patch.object(cli, "emit_output", return_value="<html>peer</html>"), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "Alpha",
                     "vs",
                     "Beta",
                     "--mock",
                     "--emit=html",
                     "--output",
                     str(output_path),
                     "--save-dir",
                     str(save_dir),
                 ]):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            output_display = cli.compute_output_path_display(str(output_path))
            _, kwargs = emit_comparison.call_args
            self.assertEqual(output_display, kwargs["save_path"])
            self.assertEqual("<html>comparison</html>\n", stdout.getvalue())
            self.assertEqual("<html>comparison</html>", output_path.read_text(encoding="utf-8"))
            comparison_saved = save_dir / "alpha-vs-beta-raw-html.html"
            self.assertEqual(
                "<html>comparison</html>",
                comparison_saved.read_text(encoding="utf-8"),
            )
            peer_saved = save_dir / "beta-raw-html.html"
            self.assertEqual("<html>peer</html>", peer_saved.read_text(encoding="utf-8"))
            self.assertIn(f"[last30days] Saved output to {output_path.resolve()}", stderr.getvalue())
            self.assertIn(f"[last30days] Saved output to {comparison_saved.resolve()}", stderr.getvalue())
            self.assertIn(f"[last30days] Saved output to {peer_saved.resolve()}", stderr.getvalue())
            self.assertIn(
                f"[last30days] Comparison artifact set: main={comparison_saved.resolve()}; "
                f"peers={peer_saved.resolve()}",
                stderr.getvalue(),
            )

    def test_main_canonicalizes_explicit_github_repo_flags(self):
        report = self.make_report()
        diag = {
            "available_sources": ["grounding"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
            "native_web_backend": "brave",
        }
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(cli.pipeline, "diagnose", return_value=diag), \
             mock.patch.object(cli.pipeline, "run", return_value=report) as run_mock, \
             mock.patch.object(cli, "emit_output", return_value="# rendered"), \
             mock.patch.object(sys, "argv", [
                 "last30days.py",
                 "claude",
                 "code",
                 "vs",
                 "codex",
                 "--github-repo",
                 "openai/codex,anthropics/claude-code-action",
             ]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        # In vs-mode main + competitors run in parallel via ThreadPoolExecutor,
        # so the order of pipeline.run invocations is non-deterministic. Find
        # the main runner's call by predicate on the canonicalized github_repos
        # rather than by index.
        expected_repos = ["openai/codex", "anthropics/claude-code"]
        main_call = next(
            (c for c in run_mock.call_args_list if c.kwargs.get("github_repos") == expected_repos),
            None,
        )
        self.assertIsNotNone(
            main_call,
            f"No pipeline.run call had github_repos={expected_repos}; "
            f"saw {[c.kwargs.get('github_repos') for c in run_mock.call_args_list]}",
        )
        self.assertIn("[GitHub] Canonicalized repos:", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
