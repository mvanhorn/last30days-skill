# ruff: noqa: E402
"""CLI parsing and validation for --competitors / --competitors-list."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import last30days as cli


def _parse(*argv: str):
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(argv)
    return args


class CompetitorsCliTests(unittest.TestCase):
    def test_flag_absent_returns_disabled(self):
        args = _parse("Kanye West")
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertFalse(enabled)
        self.assertEqual(count, 0)
        self.assertEqual(explicit, [])

    def test_bare_flag_defaults_to_two(self):
        args = _parse("Kanye West", "--competitors")
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 2)
        self.assertEqual(explicit, [])

    def test_explicit_three_still_supported(self):
        args = _parse("OpenAI", "--competitors", "3")
        enabled, count, _explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 3)

    def test_explicit_count(self):
        args = _parse("OpenAI", "--competitors", "4")
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 4)
        self.assertEqual(explicit, [])

    def test_explicit_list_preferred_over_discovery(self):
        args = _parse(
            "OpenAI",
            "--competitors",
            "--competitors-list",
            "Anthropic,xAI,Google Gemini",
        )
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 3)
        self.assertEqual(explicit, ["Anthropic", "xAI", "Google Gemini"])

    def test_explicit_list_without_flag_implies_enabled(self):
        args = _parse("OpenAI", "--competitors-list", "Anthropic,xAI")
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 2)
        self.assertEqual(explicit, ["Anthropic", "xAI"])

    def test_list_whitespace_normalized(self):
        args = _parse("OpenAI", "--competitors-list", " Anthropic , xAI ,  Gemini ")
        _enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertEqual(count, 3)
        self.assertEqual(explicit, ["Anthropic", "xAI", "Gemini"])

    def test_zero_count_rejected(self):
        args = _parse("Topic", "--competitors", "0")
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()) as err:
            cli.resolve_competitors_args(args)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--competitors must be >= 1", err.getvalue())

    def test_negative_count_rejected(self):
        args = _parse("Topic", "--competitors", "-1")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.resolve_competitors_args(args)

    def test_over_max_count_clamps_with_warning(self):
        args = _parse("Topic", "--competitors", "99")
        err = io.StringIO()
        with redirect_stderr(err):
            enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, cli.COMPETITORS_MAX)
        self.assertEqual(explicit, [])
        self.assertIn("clamping", err.getvalue())

    def test_overlong_list_clamps_with_warning(self):
        args = _parse(
            "Topic",
            "--competitors-list",
            "A,B,C,D,E,F,G,H",
        )
        err = io.StringIO()
        with redirect_stderr(err):
            enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, cli.COMPETITORS_MAX)
        self.assertEqual(len(explicit), cli.COMPETITORS_MAX)
        self.assertIn("clamping to", err.getvalue())

    def test_list_count_mismatch_warns(self):
        args = _parse(
            "Topic",
            "--competitors",
            "5",
            "--competitors-list",
            "A,B",
        )
        err = io.StringIO()
        with redirect_stderr(err):
            enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(count, 2)
        self.assertEqual(explicit, ["A", "B"])
        self.assertIn("--competitors=5 ignored", err.getvalue())

    def test_empty_list_rejected(self):
        args = _parse("Topic", "--competitors-list", ",,  ,")
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            cli.resolve_competitors_args(args)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
