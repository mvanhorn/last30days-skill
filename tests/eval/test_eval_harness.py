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
    failures += harness.per_fixture_failures(results)
    assert not failures, "\n".join(failures)


def test_per_fixture_floor_catches_single_broken_archetype():
    results = harness.evaluate_all()
    # Simulate a total clustering failure on one clustered fixture: the
    # average stays above the aggregate floor but the per-fixture floor fires.
    broken = None
    for result in results:
        if result.fixture.manifest.get("expects_clusters"):
            result.scores["cluster_coherence"] = 0.0
            broken = result.fixture.name
            break
    assert broken is not None
    aggregate_ok = not harness.baseline_failures(harness.aggregate_scores(results))
    per_fixture = harness.per_fixture_failures(results)
    assert any(f.startswith(f"{broken}/cluster_coherence") for f in per_fixture)
    # Document why the per-fixture layer exists: with 7 fixtures the aggregate
    # can absorb one zero.
    if aggregate_ok:
        assert per_fixture


def test_entity_overlap_predicate_pinned():
    # The coherence metric shares extract_text_entities/entity_overlap with
    # production clustering. Pin the predicate on fixed inputs so a
    # too-permissive drift is caught independently of the (circular) metric.
    from lib import entity_extract

    same = entity_extract.entity_overlap(
        entity_extract.extract_text_entities("OpenAI ships GPT-6 to enterprise customers"),
        entity_extract.extract_text_entities("Enterprise customers get GPT-6 from OpenAI"),
    )
    unrelated = entity_extract.entity_overlap(
        entity_extract.extract_text_entities("OpenAI ships GPT-6 to enterprise customers"),
        entity_extract.extract_text_entities("Best sourdough starter recipes for beginners"),
    )
    assert same >= harness.ENTITY_OVERLAP_FLOOR, f"related pair fell below floor: {same}"
    assert unrelated < harness.ENTITY_OVERLAP_FLOOR, (
        f"unrelated pair passed the overlap floor ({unrelated}); the shared "
        "predicate got too permissive and the coherence metric is now blind"
    )


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


def test_coherence_fails_when_expected_clusters_vanish():
    fixtures = {f.name: f for f in harness.load_fixtures()}
    clustered = fixtures["breaking-event"]
    assert clustered.manifest["expects_clusters"] is True
    report = harness._run_once(clustered)
    # Simulate cluster formation regressing to singletons.
    report.clusters = []
    assert harness._cluster_coherence(report, clustered) == 0.0


def test_coherence_allows_singletons_for_sparse_fixtures():
    fixtures = {f.name: f for f in harness.load_fixtures()}
    sparse = fixtures["niche"]
    assert sparse.manifest.get("expects_clusters") is False
    report = harness._run_once(sparse)
    report.clusters = []
    assert harness._cluster_coherence(report, sparse) == 1.0


def test_enrichment_replay_merges_metadata_without_replacing_items():
    import sys
    sys.path.insert(0, "skills/last30days/scripts")
    from lib import pipeline, schema

    fresh = schema.SourceItem(
        item_id="yt-1",
        source="youtube",
        title="Fresh title from current normalization",
        body="fresh body",
        url="https://youtube.com/watch?v=1",
        published_at="2026-07-01",
        snippet="fresh snippet",
        engagement={"views": 10},
        metadata={"channel": "fresh-channel"},
    )
    replayed = [{
        "item_id": "yt-1",
        "title": "STALE fixture title",
        "snippet": "STALE snippet",
        "metadata": {"transcript_snippet": "recorded transcript"},
    }]
    merged = pipeline._merge_replayed_enrichment([fresh], replayed)
    assert merged[0].title == "Fresh title from current normalization"
    assert merged[0].snippet == "fresh snippet"
    assert merged[0].metadata["transcript_snippet"] == "recorded transcript"
    assert merged[0].metadata["channel"] == "fresh-channel"


def test_star_enrichment_apply_map_offline():
    import sys
    sys.path.insert(0, "skills/last30days/scripts")
    from lib import github, schema

    candidate = schema.Candidate(
        candidate_id="c-gh",
        item_id="gh-1",
        source="github",
        title="repo mvanhorn/last30days-skill discussion",
        url="https://github.com/mvanhorn/last30days-skill",
        snippet="s",
        subquery_labels=["primary"],
        native_ranks={"primary:github": 1},
        local_relevance=0.9,
        freshness=90,
        engagement=10,
        source_quality=0.5,
        rrf_score=0.1,
        final_score=90,
        cluster_id="cl",
        source_items=[],
        metadata={},
    )
    enriched = github.apply_star_map(
        [candidate], {"mvanhorn/last30days-skill": 51436}
    )
    assert enriched == 1
    assert candidate.metadata["github_stars"]["mvanhorn/last30days-skill"] == 51436
