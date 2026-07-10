from __future__ import annotations

import json
from copy import deepcopy
from unittest import mock

from . import harness


def test_fixture_matrix_covers_required_topic_archetypes():
    fixtures = harness.load_fixtures()
    archetypes = {fixture.manifest["archetype"] for fixture in fixtures}

    assert 6 <= len(fixtures) <= 8
    assert {
        "tech-product",
        "person",
        "comparison",
        "breaking-event",
        "niche",
        "non-english-cjk",
    } <= archetypes


def test_research_quality_scores_meet_committed_baselines():
    results = harness.evaluate_all()
    print(harness.format_score_table(results))

    failures = harness.baseline_failures(harness.aggregate_scores(results))
    assert not failures, "\n".join(failures)


def test_replay_uses_manifest_source_availability(tmp_path):
    fixture_path = tmp_path / "cli-sources"
    fixture_path.mkdir()
    (fixture_path / "http.json").write_text(
        json.dumps(
            {
                "format": "last30days-http-fixture/v1",
                "exchanges": [],
                "source_exchanges": [],
            }
        ),
        encoding="utf-8",
    )
    fixture = harness.EvalFixture(
        name="cli-sources",
        path=fixture_path,
        manifest={
            "topic": "fixture topic",
            "as_of_date": "2026-07-10",
            "fixture_sources": ["digg", "arxiv", "techmeme", "trustpilot"],
            "plan": {},
        },
        input_urls=frozenset(),
    )

    def observe_availability(**_kwargs):
        return harness.pipeline.available_sources({}, fixture.manifest["fixture_sources"])

    with mock.patch.object(harness.pipeline, "run", side_effect=observe_availability), \
         mock.patch.object(harness.pipeline, "which", return_value=None):
        available = harness._run_once(fixture)

    assert available == fixture.manifest["fixture_sources"]


def test_intentional_out_of_window_regression_fails_recency_floor():
    fixture = harness.load_fixtures()[0]
    result = harness.evaluate_fixture(fixture)
    regressed = deepcopy(result.report)
    primary = regressed.ranked_candidates[0].source_items[0]
    primary.published_at = "2025-01-01"

    scores = harness.score_report(regressed, fixture, deterministic=True)
    failures = harness.baseline_failures(scores)

    assert scores["recency_compliance"] < 1.0
    assert any(failure.startswith("recency_compliance:") for failure in failures)
