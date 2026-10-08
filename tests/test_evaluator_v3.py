import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evaluate_search_quality as evaluator


class EvaluatorV3Tests(unittest.TestCase):
    def test_main_rejudges_changed_results_and_reuses_identical_results(self):
        for changed_id in (False, True):
            with self.subTest(changed_id=changed_id), tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp)
                topics = output_dir / "topics.json"
                topics.write_text(json.dumps([{"topic": "test topic", "query_type": "general"}]))
                baseline_url = "https://example.com/baseline-only"
                shared_url = "https://example.com/shared"
                old_url = "https://example.com/old"
                new_url = "https://example.com/new" if changed_id else old_url
                shared_item = {"url": shared_url, "title": "Shared relevant result", "score": 80}
                baseline_report = {"reddit": [
                    {"url": baseline_url, "title": "Unrelated baseline result", "score": 90},
                    shared_item,
                ]}
                old_report = {"reddit": [shared_item, {"url": old_url, "title": "Relevant result", "score": 90}]}
                new_title = "New relevant result" if changed_id else "Off-topic replacement"
                new_report = {"reddit": [shared_item, {"url": new_url, "title": new_title, "score": 90}]}
                new_grade = 3 if changed_id else 0
                responses = [
                    mock.Mock(returncode=0, stdout=json.dumps(report), stderr="")
                    for report in (baseline_report, old_report, baseline_report, new_report, baseline_report, new_report)
                ]
                argv = [
                    "evaluate_search_quality.py", "--baseline=WORKTREE", "--candidate=WORKTREE",
                    "--topics-file", str(topics), "--output-dir", str(output_dir),
                ]
                with (
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "dummy-evaluation-key"}),
                    mock.patch.object(evaluator.subprocess, "run", autospec=True, side_effect=responses),
                    mock.patch.object(evaluator, "call_gemini_judge", autospec=True, side_effect=[
                        {"judgments": [{"id": baseline_url, "grade": 0}, {"id": shared_url, "grade": 3}, {"id": old_url, "grade": 3}]},
                        {"judgments": [{"id": baseline_url, "grade": 0}, {"id": shared_url, "grade": 3}, {"id": new_url, "grade": new_grade}]},
                    ]) as judge,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(0, evaluator.main())
                    first = json.loads((output_dir / "metrics.json").read_text())
                    self.assertEqual(0.5, first["topics"][0]["baseline"]["precision_at_5"])
                    self.assertEqual(1.0, first["topics"][0]["candidate"]["precision_at_5"])
                    self.assertEqual(0, evaluator.main())
                    second = json.loads((output_dir / "metrics.json").read_text())
                    self.assertEqual(0.5, second["topics"][0]["baseline"]["precision_at_5"])
                    self.assertEqual((1 + (new_grade >= 2)) / 2, second["topics"][0]["candidate"]["precision_at_5"])
                    self.assertEqual(2, judge.call_count)
                    for call, result_url in zip(judge.call_args_list, (old_url, new_url)):
                        judged_ids = [line.removeprefix("- id: ") for line in call.args[2].splitlines() if line.startswith("- id: ")]
                        self.assertCountEqual([baseline_url, shared_url, result_url], judged_ids)
                    self.assertEqual(0, evaluator.main())
                    third = json.loads((output_dir / "metrics.json").read_text())
                    self.assertEqual(second["topics"], third["topics"])
                    self.assertEqual(2, judge.call_count)

    def test_build_ranked_items_uses_multi_source_provenance_and_best_date(self):
        report = {
            "ranked_candidates": [
                {
                    "candidate_id": "c1",
                    "item_id": "i1",
                    "source": "grounding",
                    "sources": ["grounding", "reddit"],
                    "title": "Title",
                    "url": "https://example.com",
                    "snippet": "Snippet",
                    "subquery_labels": ["primary"],
                    "native_ranks": {"primary:grounding": 1},
                    "local_relevance": 0.8,
                    "freshness": 90,
                    "engagement": None,
                    "source_quality": 1.0,
                    "rrf_score": 0.02,
                    "final_score": 88.0,
                    "source_items": [
                        {"item_id": "i1", "source": "grounding", "title": "Title", "body": "Body", "url": "https://example.com", "published_at": "2026-03-10"},
                        {"item_id": "i2", "source": "reddit", "title": "Title", "body": "Body", "url": "https://example.com", "published_at": "2026-03-12"},
                    ],
                }
            ]
        }
        items = evaluator.build_ranked_items(report, 10)
        self.assertEqual(["grounding", "reddit"], items[0]["sources"])
        self.assertEqual("grounding, reddit", items[0]["source"])
        self.assertEqual("2026-03-12", items[0]["date"])

        grouped = evaluator.source_sets(report, 10)
        self.assertEqual({"c1"}, grouped["grounding"])
        self.assertEqual({"c1"}, grouped["reddit"])

    def test_write_failure_summary_persists_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            evaluator.write_failure_summary(
                output_dir,
                "HEAD~1",
                "HEAD",
                summaries=[{
                    "topic": "test topic",
                    "baseline": {"precision_at_5": 0.5, "ndcg_at_5": 0.6, "source_coverage_recall": 1.0},
                    "candidate": {"precision_at_5": 0.7, "ndcg_at_5": 0.8, "source_coverage_recall": 1.0},
                    "stability": {"overall_jaccard": 0.4, "overall_retention_vs_baseline": 0.9},
                }],
                failures=[{"topic": "broken topic", "error": "timeout"}],
            )
            metrics = json.loads((output_dir / "metrics.json").read_text())
            summary = (output_dir / "summary.md").read_text()
            self.assertEqual(1, len(metrics["failures"]))
            self.assertIn("broken topic", summary)
            self.assertIn("## Failures", summary)

    def test_resolve_repo_dir_keeps_live_worktree(self):
        repo_dir, is_temp = evaluator.resolve_repo_dir("WORKTREE")
        self.assertEqual(evaluator.REPO_ROOT, repo_dir)
        self.assertFalse(is_temp)

    def test_resolve_repo_dir_materializes_git_ref_in_temp_worktree(self):
        fake_dir = Path("/tmp/last30days-eval-fake")
        with mock.patch.object(evaluator, "create_worktree", return_value=fake_dir) as create_worktree:
            repo_dir, is_temp = evaluator.resolve_repo_dir("HEAD~2")
        create_worktree.assert_called_once_with("HEAD~2")
        self.assertEqual(fake_dir, repo_dir)
        self.assertTrue(is_temp)

    def test_metric_helpers_cover_empty_and_ranked_cases(self):
        ranking = [
            {"key": "a", "sources": ["grounding"]},
            {"key": "b", "sources": ["reddit"]},
        ]
        judged = [{"key": "a", "sources": ["grounding"]}, {"key": "b", "sources": ["reddit"]}]
        judgments = {"a": 3, "b": 1}

        self.assertEqual(1.0, evaluator.jaccard(set(), set()))
        self.assertEqual(1.0, evaluator.retention(set(), {"a"}))
        self.assertEqual(0.5, evaluator.precision_at_k(ranking, judgments, 2))
        self.assertEqual(1.0, evaluator.ndcg_at_k(ranking, judgments, 2, judged))
        expected_reversed = (1 + 7 / math.log2(3)) / (7 + 1 / math.log2(3))
        self.assertAlmostEqual(
            expected_reversed,
            evaluator.ndcg_at_k(list(reversed(ranking)), judgments, 2, judged),
        )
        self.assertEqual(1.0, evaluator.source_coverage_recall(ranking, judged, judgments))
        self.assertEqual(0.0, evaluator.precision_at_k([], judgments, 5))
        self.assertEqual(0.0, evaluator.ndcg_at_k([], judgments, 5, judged))
        self.assertEqual(0.0, evaluator.ndcg_at_k(ranking, {"a": 0, "b": 0}, 2, judged))

    def test_resolve_google_judge_api_key_prefers_google_key(self):
        with mock.patch.dict("os.environ", {"GOOGLE_API_KEY": "google", "GEMINI_API_KEY": "gemini"}, clear=False):
            self.assertEqual("google", evaluator.resolve_google_judge_api_key({}))
        with mock.patch.dict("os.environ", {k: "" for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY")}, clear=False):
            for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY"):
                os.environ.pop(k, None)
            self.assertEqual("fallback", evaluator.resolve_google_judge_api_key({"GOOGLE_GENAI_API_KEY": "fallback"}))

    def test_extract_gemini_text_raises_when_missing(self):
        self.assertEqual(
            "hello",
            evaluator.extract_gemini_text({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}),
        )
        with self.assertRaises(ValueError):
            evaluator.extract_gemini_text({"candidates": [{"content": {"parts": [{}]}}]})

    def test_get_judgments_uses_cache_and_skips_when_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            items = [{"key": "a", "source": "reddit", "text": "Test topic", "url": "https://example.com/a"}]
            with mock.patch.object(evaluator, "call_gemini_judge", autospec=True, return_value={
                "judgments": [{"id": "a", "grade": 3}],
            }) as judge:
                evaluator.get_judgments(
                    output_dir=output_dir,
                    slug="topic",
                    topic="test topic",
                    query_type="general",
                    items=items,
                    judge_model="gemini-3.1-flash-lite",
                    gemini_api_key="dummy-evaluation-key",
                )
                judge.assert_called_once()
            cached = evaluator.get_judgments(
                output_dir=output_dir,
                slug="topic",
                topic="test topic",
                query_type="general",
                items=items,
                judge_model="gemini-3.1-flash-lite",
                gemini_api_key=None,
            )
            self.assertEqual({"a": 3}, cached)

            skipped = evaluator.get_judgments(
                output_dir=output_dir,
                slug="fresh",
                topic="test topic",
                query_type="general",
                items=[],
                judge_model="gemini-3.1-flash-lite",
                gemini_api_key=None,
            )
            self.assertEqual({}, skipped)

    def test_get_judgments_rejects_unverified_legacy_cache_without_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            cache_dir = output_dir / "judgments"
            cache_dir.mkdir()
            (cache_dir / "topic.json").write_text(
                json.dumps(
                    {
                        "judge_model": "gemini-3.1-flash-lite",
                        "judgments": [{"id": "a", "grade": 3}],
                    }
                )
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = evaluator.get_judgments(
                    output_dir=output_dir,
                    slug="topic",
                    topic="test topic",
                    query_type="general",
                    items=[{"key": "a", "source": "reddit", "text": "Test topic", "url": "https://example.com/a"}],
                    judge_model="gemini-3.1-flash-lite",
                    gemini_api_key=None,
                )
            self.assertEqual({}, result)
            self.assertIn("unverified", stderr.getvalue())

    def test_get_judgments_refreshes_legacy_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            cache_dir = output_dir / "judgments"
            cache_dir.mkdir()
            (cache_dir / "topic.json").write_text(json.dumps({
                "judge_model": "gemini-3.1-flash-lite",
                "judgments": [{"id": "a", "grade": 3}],
            }))
            with mock.patch.object(evaluator, "call_gemini_judge", autospec=True, return_value={
                "judgments": [{"id": "a", "grade": 0}],
            }) as judge:
                result = evaluator.get_judgments(
                    output_dir=output_dir,
                    slug="topic",
                    topic="test topic",
                    query_type="general",
                    items=[{"key": "a", "source": "reddit", "text": "Off topic", "url": "https://example.com/a"}],
                    judge_model="gemini-3.1-flash-lite",
                    gemini_api_key="dummy-evaluation-key",
                )
            self.assertEqual({"a": 0}, result)
            judge.assert_called_once()

    def test_get_judgments_rejudges_changed_context(self):
        changes = [
            {"topic": "other topic"},
            {"query_type": "comparison"},
            {"judge_model": "other-model"},
            {"source": "youtube"},
            {"url": "https://example.com/changed"},
            {"date": "2026-10-02"},
        ]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                item = {"key": "a", "source": "reddit", "text": "Test topic", "url": "https://example.com/a", "date": "2026-10-01"}
                arguments = dict(
                    output_dir=Path(tmp), slug="topic", topic="test topic", query_type="general",
                    items=[item], judge_model="gemini-3.1-flash-lite", gemini_api_key="dummy-evaluation-key",
                )
                with mock.patch.object(evaluator, "call_gemini_judge", autospec=True, side_effect=[
                    {"judgments": [{"id": "a", "grade": 3}]},
                    {"judgments": [{"id": "a", "grade": 0}]},
                ]) as judge:
                    self.assertEqual({"a": 3}, evaluator.get_judgments(**arguments))
                    for key, value in change.items():
                        if key in item:
                            item[key] = value
                        else:
                            arguments[key] = value
                    self.assertEqual({"a": 0}, evaluator.get_judgments(**arguments))
                    self.assertEqual(2, judge.call_count)

    def test_create_eval_env_and_run_last30days(self):
        credential_env = {
            key: ""
            for key in evaluator.EVAL_CREDENTIAL_ENV_KEYS
        }
        credential_env.update({"PATH": "/bin", "GOOGLE_API_KEY": "env-google"})
        with mock.patch.object(evaluator.envlib, "get_config", return_value={"OPENAI_API_KEY": "config-openai"}):
            with mock.patch.dict("os.environ", credential_env, clear=False):
                created = evaluator.create_eval_env()
        self.assertEqual("/bin", created["PATH"])
        self.assertEqual("env-google", created["GOOGLE_API_KEY"])
        self.assertEqual("config-openai", created["OPENAI_API_KEY"])
        self.assertEqual("", created["LAST30DAYS_CONFIG_DIR"])

        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            engine = repo_dir / "skills" / "last30days" / "scripts" / "last30days.py"
            engine.parent.mkdir(parents=True)
            engine.write_text('parser.add_argument("--json-profile")')
            completed = mock.Mock(returncode=0, stdout='{"topic":"x"}', stderr="")
            with mock.patch.object(evaluator.subprocess, "run", return_value=completed) as run:
                payload = evaluator.run_last30days(
                    repo_dir,
                    "topic",
                    search="reddit",
                    timeout_seconds=30,
                    quick=True,
                    mock=True,
                    env={"PATH": "/bin"},
                )
        self.assertEqual("x", payload["topic"])
        self.assertIn("--json-profile=raw", run.call_args.args[0])

        with mock.patch.object(evaluator.subprocess, "run", return_value=mock.Mock(returncode=2, stdout="", stderr="bad run")):
            with self.assertRaises(RuntimeError):
                evaluator.run_last30days(
                    Path("/tmp/repo"),
                    "topic",
                    search="reddit",
                    timeout_seconds=30,
                    quick=False,
                    mock=False,
                    env={"PATH": "/bin"},
                )

    def test_run_last30days_keeps_legacy_engine_implicit_raw_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            engine = repo_dir / "skills" / "last30days" / "scripts" / "last30days.py"
            engine.parent.mkdir(parents=True)
            engine.write_text('parser.add_argument("--emit")')
            completed = mock.Mock(returncode=0, stdout='{"topic":"x"}', stderr="")
            with mock.patch.object(evaluator.subprocess, "run", return_value=completed) as run:
                evaluator.run_last30days(
                    repo_dir,
                    "topic",
                    search="reddit",
                    timeout_seconds=30,
                    quick=False,
                    mock=False,
                    env={"PATH": "/bin"},
                )

        self.assertNotIn("--json-profile=raw", run.call_args.args[0])

    def test_parse_topics_file_and_summary_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topics_path = tmp_path / "topics.json"
            topics_path.write_text(json.dumps([{"topic": "topic a", "query_type": "comparison"}, {"topic": "topic b"}]))
            self.assertEqual(
                [("topic a", "comparison"), ("topic b", "general")],
                evaluator.parse_topics_file(topics_path),
            )

            evaluator.write_summary(
                tmp_path,
                "HEAD~1",
                "WORKTREE",
                [
                    {
                        "topic": "topic a",
                        "baseline": {"precision_at_5": 0.1, "ndcg_at_5": 0.2, "source_coverage_recall": 0.5},
                        "candidate": {"precision_at_5": 0.3, "ndcg_at_5": 0.4, "source_coverage_recall": 0.8},
                        "stability": {"overall_jaccard": 0.6, "overall_retention_vs_baseline": 0.7},
                    }
                ],
            )
            summary = (tmp_path / "summary.md").read_text()
            metrics = json.loads((tmp_path / "metrics.json").read_text())
            self.assertIn("| topic a | 0.10 | 0.30 |", summary)
            self.assertEqual("HEAD~1", metrics["baseline"])

if __name__ == "__main__":
    unittest.main()
