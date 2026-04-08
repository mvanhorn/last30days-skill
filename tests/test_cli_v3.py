# ruff: noqa: E402
import json
import io
import tempfile
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import last30days as cli
from lib import schema


class CliV3Tests(unittest.TestCase):
    def make_report(self) -> schema.Report:
        return schema.Report(
            topic="OpenClaw vs NanoClaw",
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite-preview",
                rerank_model="gemini-3.1-flash-lite-preview",
            ),
            query_plan=schema.QueryPlan(
                intent="comparison",
                freshness_mode="balanced_recent",
                cluster_mode="debate",
                raw_topic="OpenClaw vs NanoClaw",
                subqueries=[
                    schema.SubQuery(
                        label="primary",
                        search_query="openclaw vs nanoclaw",
                        ranking_query="How does OpenClaw compare to NanoClaw?",
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
            [sys.executable, "scripts/last30days.py", "test topic", "--mock", "--emit=json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("query_plan", payload)
        self.assertIn("ranked_candidates", payload)
        self.assertIn("clusters", payload)

    def test_parse_search_flag_normalizes_aliases_and_dedupes(self):
        self.assertEqual(
            ["grounding", "reddit", "hackernews"],
            cli.parse_search_flag("web, reddit, hn, web"),
        )

    def test_parse_search_flag_rejects_invalid_or_empty_inputs(self):
        with self.assertRaises(SystemExit):
            cli.parse_search_flag("unknown")
        with self.assertRaises(SystemExit):
            cli.parse_search_flag(" , ")

    def test_missing_sources_for_promo_prefers_reddit_x_then_web(self):
        self.assertEqual(
            "both",
            cli._missing_sources_for_promo({"available_sources": ["youtube"]}),
        )
        self.assertEqual(
            "web",
            cli._missing_sources_for_promo({"available_sources": ["reddit", "x"]}),
        )
        self.assertIsNone(
            cli._missing_sources_for_promo({"available_sources": ["reddit", "x", "grounding"]}),
        )

    def test_slugify_and_emit_output_cover_supported_modes(self):
        report = self.make_report()
        self.assertEqual("openclaw-vs-nanoclaw", cli.slugify(report.topic))

        compact = cli.emit_output(report, "compact")
        json_output = cli.emit_output(report, "json")
        context = cli.emit_output(report, "context")

        self.assertIn("# last30days-3 v3.0.0-alpha", compact)
        self.assertIn('"topic": "OpenClaw vs NanoClaw"', json_output)
        self.assertIsInstance(context, str)

        with self.assertRaises(SystemExit):
            cli.emit_output(report, "bad-mode")

    def test_save_output_writes_expected_extension(self):
        report = self.make_report()
        with tempfile.TemporaryDirectory() as tmp:
            path = cli.save_output(report, "json", tmp)
            self.assertEqual(".json", path.suffix)
            payload = json.loads(path.read_text())
            self.assertEqual("OpenClaw vs NanoClaw", payload["topic"])

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


if __name__ == "__main__":
    unittest.main()
