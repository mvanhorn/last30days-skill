import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evaluate_search_quality as evaluator


class EvaluatorV3Tests(unittest.TestCase):
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
        self.assertGreater(evaluator.ndcg_at_k(ranking, judgments, 2, judged), 0.0)
        self.assertEqual(1.0, evaluator.source_coverage_recall(ranking, judged, judgments))
        self.assertEqual(0.0, evaluator.precision_at_k([], judgments, 5))
        self.assertEqual(0.0, evaluator.ndcg_at_k([], judgments, 5, judged))

    def test_resolve_google_judge_api_key_prefers_google_key(self):
        with mock.patch.dict("os.environ", {"GOOGLE_API_KEY": "google", "GEMINI_API_KEY": "gemini"}, clear=False):
            self.assertEqual("google", evaluator.resolve_google_judge_api_key({}))
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
            cache_dir = output_dir / "judgments"
            cache_dir.mkdir()
            (cache_dir / "topic.json").write_text(json.dumps({"judgments": [{"id": "a", "grade": 3}]}))
            cached = evaluator.get_judgments(
                output_dir=output_dir,
                slug="topic",
                topic="test topic",
                query_type="general",
                items=[{"key": "a"}],
                judge_model="gemini-3.1-flash-lite-preview",
                gemini_api_key="key",
            )
            self.assertEqual({"a": 3}, cached)

            skipped = evaluator.get_judgments(
                output_dir=output_dir,
                slug="fresh",
                topic="test topic",
                query_type="general",
                items=[],
                judge_model="gemini-3.1-flash-lite-preview",
                gemini_api_key=None,
            )
            self.assertEqual({}, skipped)

    def test_create_eval_env_and_run_last30days(self):
        with mock.patch.object(evaluator.envlib, "get_config", return_value={"OPENAI_API_KEY": "config-openai"}):
            with mock.patch.dict("os.environ", {"PATH": "/bin", "GOOGLE_API_KEY": "env-google"}, clear=False):
                created = evaluator.create_eval_env()
        self.assertEqual("/bin", created["PATH"])
        self.assertEqual("env-google", created["GOOGLE_API_KEY"])
        self.assertEqual("config-openai", created["OPENAI_API_KEY"])
        self.assertEqual("", created["LAST30DAYS_CONFIG_DIR"])

        with mock.patch.object(evaluator.subprocess, "run", return_value=mock.Mock(returncode=0, stdout='{"topic":"x"}', stderr="")):
            payload = evaluator.run_last30days(
                Path("/tmp/repo"),
                "topic",
                search="reddit",
                timeout_seconds=30,
                quick=True,
                mock=True,
                env={"PATH": "/bin"},
            )
        self.assertEqual("x", payload["topic"])

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
