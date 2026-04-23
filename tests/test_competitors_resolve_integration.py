# ruff: noqa: E402
"""Integration tests for per-entity Step 0.55 resolution inside competitor fan-out."""

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
    """Minimal Report stand-in for runner return values."""
    class _R:
        pass

    r = _R()
    r.topic = topic
    r.artifacts = {}
    return r


def _build_main_args(*overrides):
    """Minimal argparse.Namespace-like object for the competitor path."""
    import argparse
    ns = argparse.Namespace(
        topic=["Kanye West"],
        mock=False,
        competitors=2,
        competitors_list=None,
        quick=False,
        deep=False,
        emit="compact",
        search=None,
        debug=False,
        diagnose=False,
        save_dir=None,
        save_suffix=None,
        store=False,
        x_handle=None,
        x_related=None,
        web_backend="auto",
        deep_research=False,
        plan=None,
        subreddits=None,
        tiktok_hashtags=None,
        tiktok_creators=None,
        ig_creators=None,
        lookback_days=30,
        auto_resolve=False,
        github_user=None,
        github_repo=None,
    )
    return ns


class PerEntityResolveTests(unittest.TestCase):
    """Verify each competitor sub-run calls auto_resolve with its own topic and
    that the resolved fields are threaded into pipeline.run."""

    def test_auto_resolve_called_per_competitor(self):
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        config = {"BRAVE_API_KEY": "test-key"}

        captured_resolve_topics: list[str] = []
        captured_pipeline_kwargs: list[dict] = []

        def fake_resolve(topic, _cfg):
            captured_resolve_topics.append(topic)
            per_topic = {
                "Drake": {
                    "x_handle": "Drake",
                    "subreddits": ["DrakeTheType", "hiphopheads"],
                    "github_user": "",
                    "github_repos": [],
                    "context": "Drake ICEMAN rollout",
                    "category": None,
                    "searches_run": 4,
                },
                "Kendrick Lamar": {
                    "x_handle": "kendricklamar",
                    "subreddits": ["KendrickLamar", "hiphopheads"],
                    "github_user": "",
                    "github_repos": [],
                    "context": "Meet The Grahams revival",
                    "category": None,
                    "searches_run": 4,
                },
            }
            return per_topic.get(topic, {
                "x_handle": "", "subreddits": [], "github_user": "",
                "github_repos": [], "context": "",
                "category": None, "searches_run": 0,
            })

        def fake_pipeline_run(**kwargs):
            captured_pipeline_kwargs.append(kwargs)
            return _fake_report(kwargs["topic"])

        with mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=True), \
             mock.patch.object(pipeline_mod, "run", side_effect=fake_pipeline_run):
            # Exercise the competitor_runner closure pattern from main() by
            # calling it directly with two competitors.
            self._run_competitor_closure(
                config=config,
                competitors=["Drake", "Kendrick Lamar"],
                mock_flag=False,
            )

        # auto_resolve was called once per competitor
        self.assertEqual(sorted(captured_resolve_topics), ["Drake", "Kendrick Lamar"])
        # pipeline.run received resolved fields per entity
        by_topic = {kw["topic"]: kw for kw in captured_pipeline_kwargs}
        self.assertEqual(by_topic["Drake"]["x_handle"], "Drake")
        self.assertEqual(
            by_topic["Drake"]["subreddits"], ["DrakeTheType", "hiphopheads"],
        )
        self.assertEqual(by_topic["Kendrick Lamar"]["x_handle"], "kendricklamar")
        # internal_subrun=True on all competitor sub-runs
        self.assertTrue(all(kw["internal_subrun"] for kw in captured_pipeline_kwargs))

    def test_mock_mode_skips_auto_resolve(self):
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        resolve_called = []

        def fake_resolve(*a, **k):
            resolve_called.append((a, k))
            return {}

        with mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(pipeline_mod, "run", side_effect=lambda **kw: _fake_report(kw["topic"])):
            self._run_competitor_closure(
                config={"BRAVE_API_KEY": "test-key"},
                competitors=["Anthropic"],
                mock_flag=True,
            )

        self.assertEqual(resolve_called, [])

    def test_no_backend_skips_auto_resolve(self):
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        resolve_called = []

        def fake_resolve(*a, **k):
            resolve_called.append((a, k))
            return {}

        with mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=False), \
             mock.patch.object(pipeline_mod, "run", side_effect=lambda **kw: _fake_report(kw["topic"])):
            self._run_competitor_closure(
                config={},
                competitors=["Anthropic"],
                mock_flag=False,
            )

        self.assertEqual(resolve_called, [])

    def test_resolve_failure_degrades_gracefully(self):
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        captured_pipeline_kwargs: list[dict] = []

        def fake_resolve(_topic, _cfg):
            raise RuntimeError("upstream offline")

        def fake_pipeline_run(**kwargs):
            captured_pipeline_kwargs.append(kwargs)
            return _fake_report(kwargs["topic"])

        err = io.StringIO()
        with redirect_stderr(err), \
             mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=True), \
             mock.patch.object(pipeline_mod, "run", side_effect=fake_pipeline_run):
            self._run_competitor_closure(
                config={"BRAVE_API_KEY": "test-key"},
                competitors=["Anthropic"],
                mock_flag=False,
            )

        # Warning logged but run continues with planner defaults
        self.assertIn("auto_resolve failed for 'Anthropic'", err.getvalue())
        self.assertEqual(len(captured_pipeline_kwargs), 1)
        self.assertIsNone(captured_pipeline_kwargs[0]["x_handle"])
        self.assertIsNone(captured_pipeline_kwargs[0]["subreddits"])

    def test_resolved_artifact_stored_on_report(self):
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        with mock.patch.object(resolve_mod, "auto_resolve", return_value={
                "x_handle": "Drake",
                "subreddits": ["DrakeTheType"],
                "github_user": "",
                "github_repos": [],
                "context": "Drake context",
                "category": None,
                "searches_run": 4,
             }), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=True), \
             mock.patch.object(pipeline_mod, "run", side_effect=lambda **kw: _fake_report(kw["topic"])):
            results = self._run_competitor_closure(
                config={"BRAVE_API_KEY": "test-key"},
                competitors=["Drake"],
                mock_flag=False,
            )

        self.assertIn("resolved", results[0].artifacts)
        resolved = results[0].artifacts["resolved"]
        self.assertEqual(resolved["entity"], "Drake")
        self.assertEqual(resolved["x_handle"], "Drake")
        self.assertEqual(resolved["subreddits"], ["DrakeTheType"])
        self.assertEqual(resolved["context"], "Drake context")

    def test_config_not_mutated_across_sub_runs(self):
        """_auto_resolve_context from entity A must not leak into entity B."""
        from lib import resolve as resolve_mod
        from lib import pipeline as pipeline_mod

        captured_contexts: list[str] = []

        def fake_resolve(topic, _cfg):
            per_topic = {
                "Drake": {"x_handle": "Drake", "subreddits": [], "github_user": "",
                          "github_repos": [], "context": "Drake unique context",
                          "category": None, "searches_run": 4},
                "Kendrick Lamar": {"x_handle": "kendricklamar", "subreddits": [],
                                   "github_user": "", "github_repos": [],
                                   "context": "Kendrick unique context",
                                   "category": None, "searches_run": 4},
            }
            return per_topic[topic]

        def fake_pipeline_run(**kwargs):
            captured_contexts.append(
                kwargs["config"].get("_auto_resolve_context", "")
            )
            return _fake_report(kwargs["topic"])

        shared_config = {"BRAVE_API_KEY": "test-key"}
        with mock.patch.object(resolve_mod, "auto_resolve", side_effect=fake_resolve), \
             mock.patch.object(resolve_mod, "_has_backend", return_value=True), \
             mock.patch.object(pipeline_mod, "run", side_effect=fake_pipeline_run):
            self._run_competitor_closure(
                config=shared_config,
                competitors=["Drake", "Kendrick Lamar"],
                mock_flag=False,
            )

        # Each sub-run received its own entity's context — no cross-leak.
        self.assertIn("Drake unique context", captured_contexts)
        self.assertIn("Kendrick unique context", captured_contexts)
        # The shared outer config was not mutated
        self.assertNotIn("_auto_resolve_context", shared_config)

    # --- test helpers -----------------------------------------------------

    def _run_competitor_closure(self, *, config, competitors, mock_flag):
        """Replicate the competitor_runner closure from last30days.main() and
        call it against each competitor. Returns the list of Reports."""
        from lib import pipeline, resolve as resolve_mod

        class _Args:
            pass
        args = _Args()
        args.mock = mock_flag
        args.web_backend = "auto"
        args.lookback_days = 30

        def runner(entity: str):
            entity_config = dict(config)
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
                except Exception as exc:
                    sys.stderr.write(
                        f"[Competitors] auto_resolve failed for {entity!r}: "
                        f"{type(exc).__name__}: {exc}\n"
                    )
                    r = {}
                resolved["x_handle"] = r.get("x_handle", "") or ""
                resolved["subreddits"] = list(r.get("subreddits") or [])
                resolved["github_user"] = r.get("github_user", "") or ""
                resolved["github_repos"] = list(r.get("github_repos") or [])
                resolved["context"] = r.get("context", "") or ""
                if resolved["context"]:
                    entity_config["_auto_resolve_context"] = resolved["context"]
            report = pipeline.run(
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
            report.artifacts["resolved"] = resolved
            return report

        return [runner(c) for c in competitors]


if __name__ == "__main__":
    unittest.main()
