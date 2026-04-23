# ruff: noqa: E402
"""Tests for --competitors-plan JSON parsing and per-entity kwargs threading."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import last30days as cli


class ParseCompetitorsPlanTests(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(cli.parse_competitors_plan(None), {})

    def test_empty_string_returns_empty(self):
        self.assertEqual(cli.parse_competitors_plan(""), {})

    def test_inline_json_parsed(self):
        raw = '{"Drake": {"x_handle": "Drake", "subreddits": ["Drizzy"]}}'
        out = cli.parse_competitors_plan(raw)
        self.assertIn("drake", out)
        self.assertEqual(out["drake"]["x_handle"], "Drake")
        self.assertEqual(out["drake"]["subreddits"], ["Drizzy"])

    def test_file_path_accepted(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        ) as f:
            json.dump(
                {"Anthropic": {"x_handle": "AnthropicAI", "github_user": "anthropics"}},
                f,
            )
            path = f.name
        try:
            out = cli.parse_competitors_plan(path)
            self.assertEqual(out["anthropic"]["x_handle"], "AnthropicAI")
            self.assertEqual(out["anthropic"]["github_user"], "anthropics")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_case_insensitive_key_normalization(self):
        raw = '{"DRAKE": {"x_handle": "Drake"}}'
        out = cli.parse_competitors_plan(raw)
        self.assertIn("drake", out)
        self.assertNotIn("DRAKE", out)

    def test_unknown_fields_warned_and_ignored(self):
        raw = '{"Drake": {"x_handle": "Drake", "bogus_field": 42}}'
        err = io.StringIO()
        with redirect_stderr(err):
            out = cli.parse_competitors_plan(raw)
        self.assertIn("drake", out)
        self.assertNotIn("bogus_field", out["drake"])
        self.assertIn("Unknown fields", err.getvalue())

    def test_malformed_json_exits_2(self):
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()) as err:
            cli.parse_competitors_plan("{not valid json")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("Invalid JSON", err.getvalue())

    def test_top_level_list_rejected(self):
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            cli.parse_competitors_plan('["Drake", "Kendrick"]')
        self.assertEqual(cm.exception.code, 2)

    def test_entry_non_dict_skipped_with_warning(self):
        raw = '{"Drake": "not-a-dict", "Kendrick": {"x_handle": "kendricklamar"}}'
        err = io.StringIO()
        with redirect_stderr(err):
            out = cli.parse_competitors_plan(raw)
        self.assertNotIn("drake", out)
        self.assertIn("kendrick", out)
        self.assertIn("must be a dict", err.getvalue())

    def test_all_six_fields_accepted(self):
        raw = json.dumps({
            "OpenAI": {
                "x_handle": "OpenAI",
                "x_related": ["sama", "gdb"],
                "subreddits": ["OpenAI", "MachineLearning"],
                "github_user": "openai",
                "github_repos": ["openai/gpt-5"],
                "context": "GPT-5 launch imminent",
            }
        })
        out = cli.parse_competitors_plan(raw)
        entry = out["openai"]
        self.assertEqual(entry["x_handle"], "OpenAI")
        self.assertEqual(entry["x_related"], ["sama", "gdb"])
        self.assertEqual(entry["subreddits"], ["OpenAI", "MachineLearning"])
        self.assertEqual(entry["github_user"], "openai")
        self.assertEqual(entry["github_repos"], ["openai/gpt-5"])
        self.assertEqual(entry["context"], "GPT-5 launch imminent")


class SubrunKwargsForTests(unittest.TestCase):
    def test_plan_wins_over_auto_resolve(self):
        plan_entry = {"x_handle": "Drake", "subreddits": ["Drizzy"]}
        resolved = {"x_handle": "wrong", "subreddits": ["wrong"]}
        kwargs = cli.subrun_kwargs_for("Drake", plan_entry, resolved=resolved)
        self.assertEqual(kwargs["x_handle"], "Drake")
        self.assertEqual(kwargs["subreddits"], ["Drizzy"])

    def test_auto_resolve_used_when_plan_missing(self):
        resolved = {
            "x_handle": "Drake",
            "subreddits": ["Drizzy", "hiphopheads"],
            "github_user": "",
            "github_repos": [],
        }
        kwargs = cli.subrun_kwargs_for("Drake", {}, resolved=resolved)
        self.assertEqual(kwargs["x_handle"], "Drake")
        self.assertEqual(kwargs["subreddits"], ["Drizzy", "hiphopheads"])

    def test_both_empty_yields_all_none(self):
        kwargs = cli.subrun_kwargs_for("Drake", {}, resolved={})
        self.assertIsNone(kwargs["x_handle"])
        self.assertIsNone(kwargs["subreddits"])
        self.assertIsNone(kwargs["github_user"])
        self.assertIsNone(kwargs["github_repos"])
        self.assertIsNone(kwargs["x_related"])
        self.assertEqual(kwargs["_context"], "")

    def test_x_handle_strips_at_sign(self):
        kwargs = cli.subrun_kwargs_for(
            "Drake", {"x_handle": "@Drake"}, resolved={},
        )
        self.assertEqual(kwargs["x_handle"], "Drake")

    def test_subreddits_strip_r_prefix(self):
        kwargs = cli.subrun_kwargs_for(
            "Drake", {"subreddits": ["r/Drizzy", "hiphopheads"]}, resolved={},
        )
        self.assertEqual(kwargs["subreddits"], ["Drizzy", "hiphopheads"])

    def test_github_repos_filter_non_slash(self):
        kwargs = cli.subrun_kwargs_for(
            "Drake",
            {"github_repos": ["drake/ovo", "not-a-repo"]},
            resolved={},
        )
        self.assertEqual(kwargs["github_repos"], ["drake/ovo"])

    def test_x_related_list_normalized(self):
        kwargs = cli.subrun_kwargs_for(
            "Drake",
            {"x_related": ["@pnd", "drakefan"]},
            resolved={},
        )
        self.assertEqual(kwargs["x_related"], ["pnd", "drakefan"])

    def test_github_user_lowercased(self):
        kwargs = cli.subrun_kwargs_for(
            "OpenAI", {"github_user": "@OpenAI"}, resolved={},
        )
        self.assertEqual(kwargs["github_user"], "openai")

    def test_context_from_plan_or_resolved(self):
        plan_entry = {"context": "Plan context"}
        resolved = {"context": "Resolved context"}
        kwargs = cli.subrun_kwargs_for("X", plan_entry, resolved=resolved)
        self.assertEqual(kwargs["_context"], "Plan context")

        kwargs = cli.subrun_kwargs_for("X", {}, resolved=resolved)
        self.assertEqual(kwargs["_context"], "Resolved context")


if __name__ == "__main__":
    unittest.main()
