# ruff: noqa: E402
"""Tests for the BRAVE/SERPER web-promo suppression when hosting-model-driven."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _engine() -> Path:
    return REPO_ROOT / "scripts" / "last30days.py"


class FooterNudgeSuppressionTests(unittest.TestCase):
    def _run(self, *argv: str, topic: str) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable,
            str(_engine()),
            topic,
            "--mock",
            "--emit=md",
            *argv,
        ]
        env = {**os.environ, "LAST30DAYS_SKIP_PREFLIGHT": "1"}
        # Strip any grounded-web keys the host might have so the promo path
        # triggers deterministically in mock + no-backend.
        for key in ("BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY",
                    "PARALLEL_API_KEY", "OPENROUTER_API_KEY"):
            env.pop(key, None)
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_bare_run_emits_web_promo(self):
        result = self._run(topic="OpenAI")
        combined = result.stdout + result.stderr
        # Mock mode still shows the promo when nothing indicates a hosting
        # model is driving. Check both streams since the UI may emit to stderr.
        self.assertIn("BRAVE_API_KEY", combined)

    def test_competitors_plan_suppresses_web_promo(self):
        result = self._run(
            "--competitors-list", "Anthropic",
            "--competitors-plan",
            '{"Anthropic":{"x_handle":"AnthropicAI","subreddits":["ClaudeAI"]}}',
            topic="OpenAI",
        )
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "unlock native grounded web search",
            combined,
            msg="web promo should be suppressed when --competitors-plan is passed",
        )

    def test_plan_suppresses_web_promo(self):
        plan = (
            '{"intent":"concept","freshness_mode":"balanced_recent",'
            '"cluster_mode":"none","subqueries":[{"label":"primary",'
            '"search_query":"OpenAI","ranking_query":"OpenAI",'
            '"sources":["grounding"]}],"source_weights":{"grounding":1.0}}'
        )
        result = self._run("--plan", plan, topic="OpenAI")
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "unlock native grounded web search",
            combined,
            msg="web promo should be suppressed when --plan is passed",
        )


if __name__ == "__main__":
    unittest.main()
