"""Tests for planner.plan_query internal_subrun quiet mode."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock

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
        provider = Mock()
        provider.generate_json.side_effect = ValueError("invalid-plan-sentinel")
        err = io.StringIO()
        with redirect_stderr(err):
            plan = planner.plan_query(
                topic="Acme Corp",
                available_sources=["grounding", "reddit"],
                requested_sources=None,
                depth="default",
                provider=provider,
                model="test-model",
                internal_subrun=True,
            )
        provider.generate_json.assert_called_once()
        self.assertTrue(plan.subqueries)
        self.assertIn("LLM planning failed", err.getvalue())
        self.assertIn("ValueError: invalid-plan-sentinel", err.getvalue())
        self.assertNotIn("No --plan passed", err.getvalue())
        self.assertNotIn("YOU ARE the planner", err.getvalue())

if __name__ == "__main__":
    unittest.main()
