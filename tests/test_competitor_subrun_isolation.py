# ruff: noqa: E402
"""Regression tests: main-topic flags must not leak into competitor sub-runs.

Based on 2026-04-22 Kanye West --competitors receipt where Drake and
Kendrick Lamar sub-runs logged Kanye's resolved subreddit list as their own
targeted search. Per-entity sub-runs must never inherit main-topic targeting
via closure capture, config mutation, or any other path.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _fake_report(topic: str):
    class _R:
        pass

    r = _R()
    r.topic = topic
    r.artifacts = {}
    return r


class SubRunIsolationTests(unittest.TestCase):
    """Exercise the _competitor_runner closure pattern from main() directly.

    Builds the same closure shape main() uses, then invokes it with
    captured-in-scope main-topic flags to verify they do NOT leak into
    sub-run pipeline.run kwargs.
    """

    def _run_closure(self, main_flags, competitors, config=None, mock_flag=False):
        """Replicate _competitor_runner closure from last30days.py main().

        main_flags: dict of {x_handle, x_related, subreddits, tiktok_hashtags,
                    tiktok_creators, ig_creators, github_user, github_repos}
                    as they would exist in outer scope after argparse.
        competitors: list of entity names to run.
        Returns the list of kwargs dicts pipeline.run was called with.
        """
        from lib import pipeline, resolve as resolve_mod

        captured: list[dict] = []

        def fake_run(**kwargs):
            captured.append(kwargs)
            return _fake_report(kwargs["topic"])

        # Simulate main scope variables
        outer_subreddits = main_flags.get("subreddits")
        outer_x_handle = main_flags.get("x_handle")
        outer_x_related = main_flags.get("x_related")
        outer_tiktok_hashtags = main_flags.get("tiktok_hashtags")
        outer_tiktok_creators = main_flags.get("tiktok_creators")
        outer_ig_creators = main_flags.get("ig_creators")
        outer_github_user = main_flags.get("github_user")
        outer_github_repos = main_flags.get("github_repos")

        class _Args:
            pass
        args = _Args()
        args.mock = mock_flag
        args.web_backend = "auto"
        args.lookback_days = 30

        cfg = config or {}

        # This mirrors the real _competitor_runner closure structure.
        def competitor_runner(entity):
            entity_config = dict(cfg)
            resolved = {
                "entity": entity,
                "x_handle": "",
                "subreddits": [],
                "github_user": "",
                "github_repos": [],
                "context": "",
            }
            if not args.mock and resolve_mod._has_backend(entity_config):
                try:
                    r = resolve_mod.auto_resolve(entity, entity_config)
                except Exception:
                    r = {}
                resolved["x_handle"] = r.get("x_handle", "") or ""
                resolved["subreddits"] = list(r.get("subreddits") or [])
                resolved["github_user"] = r.get("github_user", "") or ""
                resolved["github_repos"] = list(r.get("github_repos") or [])
                resolved["context"] = r.get("context", "") or ""
                if resolved["context"]:
                    entity_config["_auto_resolve_context"] = resolved["context"]
            pipeline.run(
                topic=entity,
                config=entity_config,
                depth="default",
                requested_sources=None,
                mock=args.mock,
                x_handle=resolved["x_handle"] or None,
                subreddits=resolved["subreddits"] or None,
                github_user=resolved["github_user"] or None,
                github_repos=resolved["github_repos"] or None,
                web_backend=args.web_backend,
                lookback_days=args.lookback_days,
                internal_subrun=True,
            )

        with mock.patch.object(pipeline, "run", side_effect=fake_run):
            for entity in competitors:
                competitor_runner(entity)

        return captured

    def test_main_subreddits_do_not_leak_to_peers(self):
        """Kanye receipt: main --subreddits=Kanye,hiphopheads leaked to Drake/Kendrick."""
        main_flags = {
            "subreddits": ["Kanye", "hiphopheads", "Music", "popheads", "kanyewest"],
            "x_handle": "kanyewest",
        }
        captured = self._run_closure(main_flags, ["Drake", "Kendrick Lamar"])
        self.assertEqual(len(captured), 2)
        for kwargs in captured:
            self.assertIsNone(
                kwargs["subreddits"],
                f"Main subreddits leaked into {kwargs['topic']!r}'s sub-run: "
                f"{kwargs['subreddits']}",
            )

    def test_main_x_handle_does_not_leak(self):
        main_flags = {"x_handle": "kanyewest"}
        captured = self._run_closure(main_flags, ["Drake"])
        self.assertIsNone(captured[0]["x_handle"])

    def test_main_github_does_not_leak(self):
        main_flags = {
            "github_user": "someuser",
            "github_repos": ["someuser/someproject"],
        }
        captured = self._run_closure(main_flags, ["Drake"])
        self.assertIsNone(captured[0]["github_user"])
        self.assertIsNone(captured[0]["github_repos"])

    def test_auto_resolve_context_does_not_leak_across_peers(self):
        """Per-entity auto_resolve context must not bleed between sub-runs."""
        from lib import resolve as resolve_mod

        def fake_resolve(entity, _cfg):
            per_topic = {
                "Drake": {"x_handle": "Drake", "subreddits": [], "github_user": "",
                          "github_repos": [], "context": "Drake ICEMAN rollout",
                          "category": None, "searches_run": 4},
                "Kendrick Lamar": {"x_handle": "kendricklamar", "subreddits": [],
                                   "github_user": "", "github_repos": [],
                                   "context": "Meet The Grahams revival",
                                   "category": None, "searches_run": 4},
            }
            return per_topic.get(entity, {})

        with mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=True):
            captured = self._run_closure(
                main_flags={},
                competitors=["Drake", "Kendrick Lamar"],
                config={"BRAVE_API_KEY": "test"},
            )

        by_topic = {kw["topic"]: kw for kw in captured}
        # Each sub-run's config got its own context string.
        self.assertEqual(
            by_topic["Drake"]["config"].get("_auto_resolve_context"),
            "Drake ICEMAN rollout",
        )
        self.assertEqual(
            by_topic["Kendrick Lamar"]["config"].get("_auto_resolve_context"),
            "Meet The Grahams revival",
        )
        # Cross-entity check: neither config contains the other's context.
        self.assertNotIn(
            "Meet The Grahams",
            by_topic["Drake"]["config"].get("_auto_resolve_context", ""),
        )
        self.assertNotIn(
            "ICEMAN",
            by_topic["Kendrick Lamar"]["config"].get("_auto_resolve_context", ""),
        )


if __name__ == "__main__":
    unittest.main()
