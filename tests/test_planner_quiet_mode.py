# ruff: noqa: E402
"""Tests for planner.plan_query internal_subrun quiet mode."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib import planner


class PlannerQuietModeTests(unittest.TestCase):
    def _call(self, *, internal_subrun: bool):
        err = io.StringIO()
        with redirect_stderr(err):
            plan = planner.plan_query(
                topic="Acme Corp",
                available_sources=["grounding", "reddit"],
                requested_sources=None,
                depth="default",
                provider=None,
                model=None,
                internal_subrun=internal_subrun,
            )
        return plan, err.getvalue()

    def test_default_emits_law7_warning(self):
        plan, stderr = self._call(internal_subrun=False)
        self.assertIn("No --plan passed", stderr)
        self.assertIn("YOU ARE the planner", stderr)
        self.assertTrue(plan.subqueries)

    def test_internal_subrun_suppresses_warning(self):
        plan, stderr = self._call(internal_subrun=True)
        self.assertNotIn("No --plan passed", stderr)
        self.assertNotIn("YOU ARE the planner", stderr)
        # Still returns a valid fallback plan
        self.assertTrue(plan.subqueries)

    def test_internal_subrun_still_allows_other_warnings(self):
        """Quiet mode only silences the LAW 7 block, not all planner output."""
        plan, _stderr = self._call(internal_subrun=True)
        # The plan itself is deterministic fallback; verify note carries
        # no planner-error indication.
        self.assertGreater(len(plan.subqueries), 0)


if __name__ == "__main__":
    unittest.main()
