"""`momentum` subcommand (lib/momentum.py + topic-word dispatch).

Scenarios:
  1. Classification math: a fresh high-component item climbs (breakout),
     a stale saved-front-runner drops (fading), an undated mid-item holds
     (sustained), and short-share counts only in-window engagement.
  2. `--as-of` shifts the window end; items age out accordingly.
  3. Undated candidates keep the engine's original freshness (coverage
     gap, not staleness) and never count toward short-share.
  4. CLI glue: missing/unreadable cache exits 2 with the remedy on stderr;
     a valid cache renders the markdown brief; `--emit=json` round-trips.
  5. Topic-word dispatch: `momentum` triggers the analysis; a longer
     research topic containing the word does not (exact-match collision
     rule shared with doctor/setup).
"""

import datetime
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import last30days as cli
from lib import momentum

AS_OF = datetime.date(2030, 1, 30)
RANGE = ("2030-01-01", "2030-01-30")


def _candidate(
    cid,
    *,
    title=None,
    final_score=0.0,
    rerank_score=None,
    rrf_score=0.0,
    freshness=0,
    engagement=0.0,
    source_quality=0.0,
    published_at=None,
    cluster_id=None,
):
    return {
        "candidate_id": cid,
        "item_id": cid,
        "source": "reddit",
        "title": title or cid,
        "url": "https://example.com/" + cid,
        "snippet": "",
        "subquery_labels": [],
        "native_ranks": {},
        "local_relevance": 0.0,
        "freshness": freshness,
        "engagement": engagement,
        "source_quality": source_quality,
        "rrf_score": rrf_score,
        "sources": ["reddit"],
        "source_items": [
            {
                "item_id": cid,
                "source": "reddit",
                "title": title or cid,
                "url": "https://example.com/" + cid,
                "published_at": published_at,
                "date_confidence": "high" if published_at else "low",
            }
        ],
        "rerank_score": rerank_score,
        "final_score": final_score,
        "cluster_id": cluster_id,
        "metadata": {"range_from": RANGE[0], "range_to": RANGE[1]},
    }


def _report(candidates, clusters=None):
    return {
        "topic": "synthetic topic",
        "range_from": RANGE[0],
        "range_to": RANGE[1],
        "generated_at": "2030-01-30T00:00:00+00:00",
        "ranked_candidates": candidates,
        "clusters": clusters or [],
    }


def _engineered():
    """Nine candidates whose saved long-window ranks disagree with their
    component scores, so the short-window re-rank must reorder them.

    saved final_score (rank_long): old_top 90 (1), old_mid 80 (2), undated 70
    (3), five undated fillers 60..40 (4-8), fresh_low 30 (9). Component
    strength: fresh_low strongest (and in window), undated mid (kept
    freshness), old_mid just under the fillers' re-score, old_top weakest
    plus the out-of-window penalty.
    """
    candidates = [
        _candidate(
            "old_top",
            final_score=90.0,
            rerank_score=0.0,
            engagement=100.0,
            published_at="2030-01-10",
        ),
        _candidate(
            "old_mid",
            final_score=80.0,
            rerank_score=30.0,
            rrf_score=0.03,
            engagement=60.0,
            published_at=(AS_OF - datetime.timedelta(days=4)).isoformat(),
        ),
        _candidate(
            "undated",
            final_score=70.0,
            rerank_score=40.0,
            rrf_score=0.04,
            freshness=80,
            engagement=0.0,
        ),
    ]
    for index, saved in enumerate((60.0, 55.0, 50.0, 45.0, 40.0)):
        candidates.append(
            _candidate(
                f"filler{index}",
                final_score=saved,
                rerank_score=30.0,
                rrf_score=0.02,
            )
        )
    candidates.append(
        _candidate(
            "fresh_low",
            final_score=30.0,
            rerank_score=90.0,
            rrf_score=0.08,
            freshness=95,
            engagement=40.0,
            source_quality=1.0,
            published_at=(AS_OF - datetime.timedelta(days=1)).isoformat(),
        )
    )
    return candidates


class AnalyzeReport(unittest.TestCase):
    def test_classification_and_share(self):
        result = momentum.analyze_report(
            _report(_engineered()), days=7, as_of=AS_OF, top=2
        )
        by_id = {row["candidate_id"]: row for row in result["candidates"]}

        # Long-window ranks come from the saved array order (the engine's
        # ranking), which this fixture lists in descending final_score order.
        self.assertEqual(1, by_id["old_top"]["rank_long"])
        self.assertEqual(9, by_id["fresh_low"]["rank_long"])

        # Short-window re-rank: strong fresh item leads, stale items sink.
        self.assertEqual(1, by_id["fresh_low"]["rank_short"])
        self.assertLessEqual(by_id["undated"]["rank_short"], 2)
        self.assertEqual(9, by_id["old_top"]["rank_short"])

        self.assertEqual("breakout", by_id["fresh_low"]["momentum"])
        self.assertEqual("fading", by_id["old_top"]["momentum"])
        self.assertEqual("sustained", by_id["undated"]["momentum"])

        # Only in-window engagement counts toward short-share (fresh_low +
        # old_mid are inside the window; old_top is not; undated never is).
        self.assertEqual(0.5, result["short_share"])

        self.assertEqual("2030-01-24 -> 2030-01-30 (7d)", result["window_short"])
        self.assertEqual("2030-01-01 -> 2030-01-30", result["window_long"])

    def test_fading_needs_threshold(self):
        # old_mid also sinks (rank 2 -> 3), but by fewer than CLIMB_THRESHOLD,
        # so it is stable rather than fading.
        result = momentum.analyze_report(
            _report(_engineered()), days=7, as_of=AS_OF, top=2
        )
        by_id = {row["candidate_id"]: row for row in result["candidates"]}
        self.assertEqual("stable", by_id["old_mid"]["momentum"])

    def test_as_of_shifts_window_and_ages_items_out(self):
        result = momentum.analyze_report(
            _report(_engineered()), days=7, as_of=AS_OF - datetime.timedelta(days=25)
        )
        by_id = {row["candidate_id"]: row for row in result["candidates"]}
        # fresh_low (published Jan 29) is far outside the Jan 1-6 window now.
        self.assertTrue(by_id["fresh_low"]["out_of_window"])
        self.assertFalse(by_id["fresh_low"]["in_window"])
        self.assertEqual(0.0, result["short_share"])

    def test_undated_keeps_engine_freshness_and_never_counts_in_share(self):
        result = momentum.analyze_report(
            _report(_engineered()), days=7, as_of=AS_OF, top=2
        )
        undated = next(
            row for row in result["candidates"] if row["candidate_id"] == "undated"
        )
        self.assertIsNone(undated["age_days"])
        self.assertFalse(undated["in_window"])
        # Kept freshness yields a nonzero short score: coverage gap, not stale.
        self.assertGreater(undated["score_short"], 0.0)

    def test_empty_report_is_valid(self):
        result = momentum.analyze_report(_report([]), days=7, as_of=AS_OF)
        self.assertEqual([], result["candidates"])
        self.assertEqual(0.0, result["short_share"])

    def test_rank_long_follows_cache_order_not_score_order(self):
        # The cached array order is the engine's authoritative ranking; a
        # score-only reconstruction could tie-break differently and corrupt
        # every rank delta (greptile P1).
        shuffled = [
            _candidate(
                "listed_first",
                final_score=30.0,
                rerank_score=90.0,
                engagement=10.0,
                published_at=(AS_OF - datetime.timedelta(days=1)).isoformat(),
            ),
            _candidate("listed_second", final_score=90.0, rerank_score=0.0),
        ]
        result = momentum.analyze_report(_report(shuffled), days=7, as_of=AS_OF)
        by_id = {row["candidate_id"]: row for row in result["candidates"]}
        self.assertEqual(1, by_id["listed_first"]["rank_long"])
        self.assertEqual(2, by_id["listed_second"]["rank_long"])

    def test_cluster_share_uses_cluster_titles(self):
        candidates = _engineered()
        for cand in candidates:
            cand["cluster_id"] = "c1"
        result = momentum.analyze_report(
            _report(candidates, clusters=[{"cluster_id": "c1", "title": "the cluster"}]),
            days=7,
            as_of=AS_OF,
        )
        self.assertEqual(1, len(result["clusters"]))
        self.assertEqual("the cluster", result["clusters"][0]["cluster"])
        self.assertEqual(9, result["clusters"][0]["items"])


class MomentumRun(unittest.TestCase):
    def _cache_env(self, payload=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache_dir = Path(tmp.name)
        if payload is not None:
            (cache_dir / "last-report.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        return cache_dir

    def _args(self, **overrides):
        base = {"lookback_days": 7, "as_of_date": None, "emit": "md"}
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_missing_cache_exits_two_with_remedy(self):
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env()):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = momentum.run(self._args(), {})
        self.assertEqual(2, rc)
        self.assertIn("run a research pass first", stderr.getvalue())

    def test_unreadable_cache_exits_two(self):
        bad = self._cache_env()
        (bad / "last-report.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(momentum.env, "CONFIG_DIR", bad):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = momentum.run(self._args(), {})
        self.assertEqual(2, rc)
        self.assertIn("could not read report cache", stderr.getvalue())

    def test_valid_cache_renders_brief(self):
        payload = {
            "schema": "last30days-report-cache/v1",
            "reports": [{"entity": "synthetic topic", "report": _report(_engineered())}],
        }
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = momentum.run(self._args(), {})
        self.assertEqual(0, rc)
        out = stdout.getvalue()
        self.assertIn("# Momentum diff: synthetic topic", out)
        self.assertIn("Breakouts", out)
        self.assertIn("rank 9->1", out)

    def test_emit_json_round_trips(self):
        payload = {
            "reports": [{"entity": "synthetic topic", "report": _report(_engineered())}]
        }
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = momentum.run(self._args(emit="json"), {})
        self.assertEqual(0, rc)
        parsed = json.loads(stdout.getvalue())
        self.assertEqual("synthetic topic", parsed["topic"])
        self.assertEqual(9, len(parsed["candidates"]))

    def test_days_flag_controls_window(self):
        payload = {
            "reports": [{"entity": "synthetic topic", "report": _report(_engineered())}]
        }
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = momentum.run(self._args(emit="json", lookback_days=21), {})
        self.assertEqual(0, rc)
        parsed = json.loads(stdout.getvalue())
        self.assertIn("(21d)", parsed["window_short"])

    def test_negative_days_exits_two(self):
        payload = {
            "reports": [{"entity": "synthetic topic", "report": _report(_engineered())}]
        }
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = momentum.run(self._args(lookback_days=-5), {})
        self.assertEqual(2, rc)
        self.assertIn("--days must be a positive", stderr.getvalue())

    def test_zero_days_exits_two(self):
        # Regression: `0` is falsy, so the old `or DEFAULT_SHORT_DAYS`
        # fallback silently promoted an explicit `--days 0` to a 7-day
        # analysis instead of letting the positivity guard reject it.
        payload = {
            "reports": [{"entity": "synthetic topic", "report": _report(_engineered())}]
        }
        for explicit_zero in (0, "0"):
            with self.subTest(explicit_zero=explicit_zero):
                with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        rc = momentum.run(self._args(lookback_days=explicit_zero), {})
                self.assertEqual(2, rc)
                self.assertIn("--days must be a positive", stderr.getvalue())

    def test_multi_report_json_is_a_single_document(self):
        # Comparison caches carry several reports; machine output must stay
        # one parseable JSON document (array), not concatenated documents.
        payload = {
            "reports": [
                {"entity": "alpha", "report": _report(_engineered())},
                {"entity": "beta", "report": _report(_engineered())},
            ]
        }
        with mock.patch.object(momentum.env, "CONFIG_DIR", self._cache_env(payload)):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = momentum.run(self._args(emit="json"), {})
        self.assertEqual(0, rc)
        parsed = json.loads(stdout.getvalue())
        self.assertIsInstance(parsed, list)
        self.assertEqual(2, len(parsed))


class TopicWordDispatch(unittest.TestCase):
    def test_momentum_dispatches_to_run(self):
        with mock.patch(
            "lib.momentum.run", return_value=0
        ) as run, mock.patch.object(
            cli.env, "get_config", return_value={}
        ), mock.patch.object(
            sys, "argv", ["last30days.py", "momentum", "--days", "7"]
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        run.assert_called_once()
        self.assertEqual(7, run.call_args[0][0].lookback_days)

    def test_multiword_topic_containing_momentum_is_research_not_analysis(self):
        # Exact single-word match only: a real research topic goes down the
        # research path (sentinel raised there), same collision rule as doctor.
        with mock.patch(
            "lib.momentum.run", side_effect=AssertionError("momentum must not run")
        ), mock.patch.object(
            cli.env, "get_config", return_value={}
        ), mock.patch.object(
            cli.pipeline, "diagnose", side_effect=RuntimeError("research path reached")
        ), mock.patch.object(
            sys, "argv", ["last30days.py", "momentum", "investing"]
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(RuntimeError):
                    cli.main()


if __name__ == "__main__":
    unittest.main()
