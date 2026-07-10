from __future__ import annotations

from copy import deepcopy

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
