"""Typed planned-source accounting tests (SKIPPED_UNCONFIGURED).

Regression surface for the "planned-source truth" repair:

- External plans preserve every explicitly requested unavailable source as a
  typed ``skipped-unconfigured`` outcome with source, state, and safe detail.
- Mixed plans run the available requested sources AND retain typed skips for
  the unavailable requested sources.
- An all-unavailable subquery is NEVER silently rewritten to unrelated
  eligible sources (no substitution).
- Generic eligibility fallback still works for plans that did NOT explicitly
  constrain sources.
- Absent X / TikTok / Instagram (no sanctioned backend configured) surface as
  typed skips — never as silent drops, never as substituted sources.
- Rendering/export carry the typed skip states without secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))

from lib import health, pipeline, planner, render, schema  # noqa: E402
from lib.schema import SKIPPED_UNCONFIGURED  # noqa: E402

# Obvious dummies only (repo security hygiene): no real credentials anywhere.
FAKE_YT_ITEMS = [
    {
        "video_id": "v1",
        "title": "Test video",
        "url": "https://www.youtube.com/watch?v=v1",
        "channel_name": "Chan",
        "date": "2026-07-20",
        "engagement": {"views": 100, "likes": 5, "comments": 1},
        "description": "",
    }
]

EXTERNAL_PLAN = {
    "intent": "product",
    "freshness_mode": "balanced_recent",
    "cluster_mode": "debate",
    "source_weights": {"youtube": 1.0},
    "subqueries": [
        {
            "label": "primary",
            "search_query": "test topic",
            "ranking_query": "What are people saying about test topic?",
            "sources": ["youtube"],
            "weight": 1.0,
        }
    ],
}


@pytest.fixture
def youtube_local(monkeypatch):
    """Make the youtube source available through the canonical predicate and
    stub its network path. The 'local' route is the one the live host uses."""
    with mock.patch.object(pipeline.youtube_yt, "ytdlp_route", return_value="local"), \
         mock.patch.object(pipeline.youtube_yt, "is_ytdlp_installed", return_value=True), \
         mock.patch.object(
             pipeline.youtube_yt, "search_and_transcribe",
             return_value={"items": FAKE_YT_ITEMS},
         ), \
         mock.patch.object(pipeline.youtube_yt, "enrich_with_comments", return_value=None):
        yield


class TestSanitizePlanAccounting:
    def test_mixed_plan_preserves_skipped_and_runs_available(self):
        raw = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "ranking",
                    "sources": ["youtube", "tiktok", "instagram"],
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw, "test topic", ["youtube"], ["youtube", "tiktok", "instagram"], "quick",
        )
        assert plan.subqueries[0].sources == ["youtube"]
        skipped = dict(plan.skipped_sources)
        assert skipped["tiktok"]
        assert skipped["instagram"]
        assert "youtube" not in skipped
        assert "reddit" not in skipped  # no unrelated substitution anywhere

    def test_all_unavailable_subquery_is_dropped_not_substituted(self):
        """Every explicit source unavailable => the subquery is dropped and
        typed skips recorded; no unrelated eligible sources are substituted."""
        raw = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "ranking",
                    "sources": ["tiktok", "instagram", "x"],
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw, "test topic", ["youtube", "reddit"], None, "quick",
        )
        assert plan.subqueries == []
        skipped = dict(plan.skipped_sources)
        assert set(skipped) == {"tiktok", "instagram", "x"}
        assert "all-requested-sources-unavailable" in plan.notes
        # No unrelated substitution: the plan never suggests youtube/reddit.
        assert "youtube" not in dict(plan.skipped_sources)

    def test_unconstrained_subquery_keeps_generic_fallback(self):
        """A subquery with no explicit sources may use any eligible source —
        the generic eligibility fallback is NOT weakened."""
        raw = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "ranking",
                    "sources": [],
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw, "test topic", ["youtube", "reddit"], None, "quick",
        )
        assert plan.subqueries[0].sources == ["youtube", "reddit"]
        assert plan.skipped_sources == []

    def test_unavailable_plan_weight_is_typed_skip(self):
        raw = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "source_weights": {"youtube": 2.0, "tiktok": 1.0},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "ranking",
                    "sources": ["youtube"],
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw, "test topic", ["youtube"], None, "quick",
        )
        assert dict(plan.skipped_sources).get("tiktok")

    def test_duplicate_labels_normalize_away(self):
        """Duplicate/aliased requested labels collapse to one canonical entry
        (regression: duplicates must not produce double skip records)."""
        normalized = pipeline.normalize_requested_sources(
            ["YouTube", "youtube", "x", "xquik", "X"]
        )
        assert normalized == ["youtube", "x"]


class TestRunAccounting:
    def test_mixed_requested_sources_run_typed_skips(
        self, youtube_local,
    ):
        """--search youtube,tiktok,instagram,x with only youtube configured:
        youtube runs, every other requested source is a typed skip."""
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            requested_sources=["youtube", "tiktok", "instagram", "x"],
        )
        assert set(report.items_by_source) == {"youtube"}
        assert report.source_status["youtube"].state == health.OK
        for name in ("tiktok", "instagram", "x"):
            outcome = report.source_status[name]
            assert outcome.state == SKIPPED_UNCONFIGURED, name
            assert outcome.attempted is False, name
            assert outcome.detail, name
            assert name in report.errors_by_source, name

    def test_external_plan_mixed_run(self, youtube_local):
        """External plan naming youtube+tiktok with youtube available: plan
        runs youtube, plan-level skip recorded for tiktok."""
        plan = dict(EXTERNAL_PLAN)
        plan["subqueries"] = [
            {
                "label": "primary",
                "search_query": "test topic",
                "ranking_query": "ranking",
                "sources": ["youtube", "tiktok"],
            }
        ]
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            external_plan=plan,
        )
        assert set(report.items_by_source) == {"youtube"}
        assert report.source_status["tiktok"].state == SKIPPED_UNCONFIGURED

    def test_all_unavailable_requested_sources_return_honest_empty(
        self, monkeypatch,
    ):
        """Every requested source unavailable: no RuntimeError, no unrelated
        substitution — an honest empty report carrying typed skips."""
        monkeypatch.setattr(pipeline.youtube_yt, "ytdlp_route", lambda: "unavailable")
        monkeypatch.setattr(pipeline.youtube_yt, "is_ytdlp_installed", lambda: False)
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            requested_sources=["tiktok", "instagram"],
        )
        assert report.items_by_source == {}
        assert report.source_status["tiktok"].state == SKIPPED_UNCONFIGURED
        assert report.source_status["instagram"].state == SKIPPED_UNCONFIGURED
        assert report.source_status["tiktok"].attempted is False

    def test_external_plan_all_unavailable_no_substitution(self, monkeypatch):
        """External plan whose every subquery names only unconfigured sources:
        the run returns typed skips and executes NO unrelated source."""
        monkeypatch.setattr(pipeline.youtube_yt, "ytdlp_route", lambda: "unavailable")
        monkeypatch.setattr(pipeline.youtube_yt, "is_ytdlp_installed", lambda: False)
        plan = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "ranking",
                    "sources": ["x", "tiktok", "instagram"],
                }
            ],
        }
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            external_plan=plan,
        )
        assert report.items_by_source == {}
        for name in ("x", "tiktok", "instagram"):
            assert report.source_status[name].state == SKIPPED_UNCONFIGURED, name
            assert report.source_status[name].attempted is False, name


class TestRenderingAndExport:
    def test_compact_render_shows_typed_skips_without_secrets(self, youtube_local):
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            requested_sources=["youtube", "tiktok"],
        )
        text = render.render_compact(report)
        assert "skipped-unconfigured" in text
        assert "tiktok" in text
        # The typed detail appears, with no credential-like content.
        assert "not configured for this run" in text
        for secret_marker in ("api_key", "token=", "password", "cookies"):
            assert secret_marker not in text.lower()

    def test_report_serialization_preserves_skips(self, youtube_local):
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            requested_sources=["youtube", "tiktok"],
        )
        payload = schema.to_dict(report)
        rebuilt = schema.report_from_dict(payload)
        assert rebuilt.source_status["tiktok"].state == SKIPPED_UNCONFIGURED
        assert rebuilt.source_status["tiktok"].attempted is False
        assert rebuilt.source_status["tiktok"].detail == (
            "Source was requested but is not configured for this run."
        )

    def test_agent_export_lists_skipped_state(self, youtube_local):
        report = pipeline.run(
            topic="test topic",
            config={},
            depth="quick",
            web_backend="none",
            requested_sources=["youtube", "tiktok", "instagram", "x"],
        )
        payload = schema.to_agent_export(report)
        status = payload["source_status"]
        assert status["tiktok"] == SKIPPED_UNCONFIGURED
        assert status["instagram"] == SKIPPED_UNCONFIGURED
        assert status["x"] == SKIPPED_UNCONFIGURED
        assert status["youtube"] == "ok"
