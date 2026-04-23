# ruff: noqa: E402
"""Tests for scripts/lib/fanout.run_competitor_fanout."""

from __future__ import annotations

import io
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib import fanout


def _fake_report(topic: str):
    """Build a lightweight Report stand-in. Tests only check identity."""
    class _R:
        pass

    r = _R()
    r.topic = topic
    return r


class FanoutOrchestratorTests(unittest.TestCase):
    def test_main_plus_two_competitors_all_succeed(self):
        def main_runner():
            return _fake_report("OpenAI")

        def comp_runner(entity):
            return _fake_report(entity)

        err = io.StringIO()
        with redirect_stderr(err):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=main_runner,
                competitors=["Anthropic", "xAI"],
                competitor_runner=comp_runner,
            )
        labels = [label for label, _ in results]
        self.assertEqual(labels, ["OpenAI", "Anthropic", "xAI"])
        self.assertEqual(results[0][1].topic, "OpenAI")
        self.assertEqual(results[1][1].topic, "Anthropic")

    def test_one_competitor_failure_degrades_gracefully(self):
        def main_runner():
            return _fake_report("OpenAI")

        def comp_runner(entity):
            if entity == "BrokenCo":
                raise RuntimeError("upstream offline")
            return _fake_report(entity)

        err = io.StringIO()
        with redirect_stderr(err):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=main_runner,
                competitors=["Anthropic", "BrokenCo", "xAI"],
                competitor_runner=comp_runner,
            )
        labels = [label for label, _ in results]
        self.assertEqual(labels, ["OpenAI", "Anthropic", "xAI"])
        self.assertIn("BrokenCo", err.getvalue())
        self.assertIn("upstream offline", err.getvalue())

    def test_main_topic_failure_leaves_only_competitors(self):
        def main_runner():
            raise RuntimeError("main exploded")

        def comp_runner(entity):
            return _fake_report(entity)

        err = io.StringIO()
        with redirect_stderr(err):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=main_runner,
                competitors=["Anthropic", "xAI"],
                competitor_runner=comp_runner,
            )
        labels = [label for label, _ in results]
        self.assertEqual(labels, ["Anthropic", "xAI"])
        self.assertIn("main exploded", err.getvalue())

    def test_empty_competitor_list_runs_only_main(self):
        def main_runner():
            return _fake_report("OpenAI")

        def comp_runner(_entity):
            raise AssertionError("should not be called when competitors=[]")

        err = io.StringIO()
        with redirect_stderr(err):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=main_runner,
                competitors=[],
                competitor_runner=comp_runner,
            )
        self.assertEqual([label for label, _ in results], ["OpenAI"])

    def test_sub_runs_execute_in_parallel(self):
        """Wall clock should be closer to max(latency) than sum(latency)."""
        delay = 0.2
        call_count = 3  # main + 2 competitors

        def make_runner(_label):
            def runner():
                time.sleep(delay)
                return _fake_report(_label)
            return runner

        def comp_runner(entity):
            return make_runner(entity)()

        start = time.monotonic()
        with redirect_stderr(io.StringIO()):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=make_runner("OpenAI"),
                competitors=["Anthropic", "xAI"],
                competitor_runner=comp_runner,
            )
        elapsed = time.monotonic() - start
        self.assertEqual(len(results), 3)
        # Generous margin: parallel execution should finish well under
        # sum(call_count * delay) == 0.6s. We accept anything under 0.5s.
        self.assertLess(
            elapsed, delay * call_count,
            f"Expected parallel execution < {delay * call_count:.2f}s, "
            f"got {elapsed:.2f}s (sub-runs likely serialized)",
        )

    def test_all_competitors_fail_leaves_main_only(self):
        def main_runner():
            return _fake_report("OpenAI")

        def comp_runner(_entity):
            raise RuntimeError("all offline")

        with redirect_stderr(io.StringIO()):
            results = fanout.run_competitor_fanout(
                main_topic="OpenAI",
                main_runner=main_runner,
                competitors=["A", "B", "C"],
                competitor_runner=comp_runner,
            )
        self.assertEqual([label for label, _ in results], ["OpenAI"])


if __name__ == "__main__":
    unittest.main()
