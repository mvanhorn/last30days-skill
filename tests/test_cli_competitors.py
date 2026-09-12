"""CLI parsing and validation for --competitors / --competitors-list."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

import last30days as cli
from lib import fanout


def _fake_report(topic: str):
    """Duck-typed Report stand-in; the guard runs before any field is read."""
    return type("R", (), {"topic": topic, "warnings": []})()


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


class CompetitorMainTopicFailureTests(unittest.TestCase):
    """The CLI layer above run_competitor_fanout.

    The fan-out drops a failed sub-run from its list, and the render takes
    element 0 as the comparison's subject. Nothing between them checked that
    the main topic survived, so a main run that raised while >=2 peers
    succeeded produced a complete-looking comparison headed by a peer, saved
    under that peer's slug, with the user's topic absent.
    """

    def _run(self, surviving_labels):
        surviving = [(label, _fake_report(label)) for label in surviving_labels]
        argv = [
            "last30days", "OpenAI",
            "--competitors-list", "Anthropic,xAI",
            "--mock", "--emit=json",
        ]
        err = io.StringIO()
        # last30days imports fanout locally inside _main, so patch the
        # source module rather than an attribute on the CLI module.
        with mock.patch.object(
            fanout, "run_competitor_fanout", return_value=surviving
        ), mock.patch.object(sys, "argv", argv), redirect_stderr(err):
            rc = cli.main()
        return rc, err.getvalue()

    def test_main_topic_failure_is_not_a_competitor_promotion(self):
        rc, err = self._run(["Anthropic", "xAI"])
        self.assertEqual(1, rc)
        self.assertIn("main topic 'OpenAI' failed", err)
        self.assertIn("Refusing to render a comparison", err)


class CompetitorDuplicateLabelTests(unittest.TestCase):
    """run_competitor_fanout keys its results by label, so a peer sharing the
    main topic's label collapsed both submissions onto one report while the
    returned list still had two entries: a self-comparison whose failed main
    run the survivor check could not see."""

    def _run(self, peers):
        seen: dict[str, list[str]] = {}

        def _capture(**kwargs):
            seen["competitors"] = list(kwargs["competitors"])
            # One survivor trips the <2 guard, so nothing renders from the
            # duck-typed report. The assertion is on what fan-out received.
            return [(kwargs["main_topic"], _fake_report(kwargs["main_topic"]))]

        argv = [
            "last30days", "OpenAI",
            "--competitors-list", peers,
            "--mock", "--emit=json",
        ]
        err = io.StringIO()
        with mock.patch.object(
            fanout, "run_competitor_fanout", side_effect=_capture
        ), mock.patch.object(sys, "argv", argv), redirect_stderr(err):
            rc = cli.main()
        return rc, err.getvalue(), seen.get("competitors")

    def test_peer_equal_to_main_topic_is_dropped(self):
        _rc, err, competitors = self._run("OpenAI,Anthropic")
        self.assertEqual(["Anthropic"], competitors)
        self.assertIn("Dropping 'OpenAI'", err)

    def test_duplicate_match_ignores_case_and_surrounding_space(self):
        _rc, err, competitors = self._run("   OPENAI  ,Anthropic")
        self.assertEqual(["Anthropic"], competitors)
        self.assertIn("Dropping", err)

    def test_differently_worded_peer_is_kept(self):
        # Normalization collapses whitespace runs and folds case; it does not
        # strip spaces. "Open AI" stays a distinct entity from "OpenAI",
        # because merging those would silently drop a real peer.
        _rc, _err, competitors = self._run("Open AI,Anthropic")
        self.assertEqual(["Open AI", "Anthropic"], competitors)

    def test_repeated_peer_is_dropped(self):
        _rc, _err, competitors = self._run("Anthropic,anthropic,xAI")
        self.assertEqual(["Anthropic", "xAI"], competitors)

    def test_all_peers_duplicate_main_topic_aborts(self):
        rc, err, competitors = self._run("openai, OpenAI ")
        self.assertEqual(2, rc)
        self.assertIsNone(competitors, "fan-out must not run with no peers")
        self.assertIn("No peer distinct from 'OpenAI'", err)

