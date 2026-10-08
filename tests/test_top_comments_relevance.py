"""Tests for relevance-blended Top Community Comments ranking (issue #641).

Off-topic high-traffic threads should not crowd out on-topic lower-voted threads.
"""

import math

from lib import render, schema, signals


def _candidate(
    *,
    source: str = "reddit",
    title: str = "Post",
    url: str = "https://example.com/1",
    local_relevance: float = 0.8,
    top_comments=None,
) -> schema.Candidate:
    source_items = []
    if top_comments is not None:
        source_items.append(
            schema.SourceItem(
                item_id=f"si-{url}",
                source=source,
                title=title,
                body="",
                url=url,
                metadata={"top_comments": top_comments},
            )
        )
    c = schema.Candidate(
        candidate_id=f"c-{url}",
        item_id=f"i-{url}",
        source=source,
        title=title,
        url=url,
        snippet="",
        subquery_labels=["q1"],
        native_ranks={source: 1},
        local_relevance=local_relevance,
        freshness=50,
        engagement=0,
        source_quality=0.5,
        rrf_score=0.01,
        source_items=source_items,
    )
    c.final_score = 50.0
    return c


def _make_report(*candidates) -> schema.Report:
    return schema.Report(
        topic="test topic",
        range_from="2026-01-01",
        range_to="2026-01-31",
        generated_at="2026-01-31T00:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="test", planner_model="test", rerank_model="test",
        ),
        query_plan=schema.QueryPlan(
            intent="general", freshness_mode="balanced_recent",
            cluster_mode="debate", raw_topic="test", subqueries=[],
            source_weights={},
        ),
        ranked_candidates=list(candidates),
        clusters=[],
        items_by_source={},
        errors_by_source={},
    )


class TestTopCommentsRelevanceGate:
    def test_below_floor_candidates_excluded(self):
        """Candidates below RELEVANCE_FLOOR must not contribute comments."""
        off_topic = _candidate(
            url="https://example.com/offtopic",
            local_relevance=0.05,  # below RELEVANCE_FLOOR=0.1
            top_comments=[{"body": "viral off-topic comment nine thousand votes", "score": 9000}],
        )
        on_topic = [
            _candidate(
                url=f"https://example.com/ontopic-{idx}",
                local_relevance=0.7,
                top_comments=[{"body": f"directly about the topic comment {idx}", "score": 50 - idx}],
            )
            for idx in range(5)
        ]
        report = _make_report(off_topic, *on_topic)
        lines = render._render_top_comments(report)
        combined = "\n".join(lines)
        assert "viral off-topic comment" not in combined
        assert "directly about the topic" in combined

    def test_high_relevance_beats_low_relevance_despite_moderate_vote_lead(self):
        """On-topic comment (high relevance) should outrank a moderately more-voted off-topic one.

        Vote normalization: reddit ref = log1p(2000) ≈ 7.6
        - 200 upvotes: log1p(200)/7.6 ≈ 0.70
        - 50 upvotes:  log1p(50)/7.6  ≈ 0.52

        Blended scores (60% vote, 40% relevance):
        - viral_offtopic (200 votes, rel=0.12):  0.6*0.70 + 0.4*0.12 = 0.468
        - on_topic (50 votes, rel=0.90):         0.6*0.52 + 0.4*0.90 = 0.672
        """
        viral_offtopic = _candidate(
            url="https://example.com/viral",
            local_relevance=0.12,  # just above RELEVANCE_FLOOR
            top_comments=[{"body": "Viral off-topic comment with moderate vote lead", "score": 200}],
        )
        ontopic_modest = _candidate(
            url="https://example.com/ontopic",
            local_relevance=0.90,
            top_comments=[{"body": "On-topic comment fewer votes but on-topic", "score": 50}],
        )
        report = _make_report(viral_offtopic, ontopic_modest)
        lines = render._render_top_comments(report)
        combined = "\n".join(lines)
        assert combined.index("On-topic comment") < combined.index("Viral off-topic")

    def test_sparse_topics_keep_comments_when_floor_would_remove_everything(self):
        """Sparse niche topics should still surface comments below the soft relevance floor."""
        low_relevance_a = _candidate(
            url="https://example.com/sparse-a",
            local_relevance=0.08,
            top_comments=[{"body": "Sparse topic comment still relevant enough to show", "score": 70}],
        )
        low_relevance_b = _candidate(
            url="https://example.com/sparse-b",
            local_relevance=0.07,
            top_comments=[{"body": "Another sparse topic comment below floor", "score": 50}],
        )
        low_relevance_c = _candidate(
            url="https://example.com/sparse-c",
            local_relevance=0.05,
            top_comments=[{"body": "Third sparse topic comment below floor", "score": 30}],
        )
        report = _make_report(low_relevance_a, low_relevance_b, low_relevance_c)

        lines = render._render_top_comments(report)
        combined = "\n".join(lines)

        assert "Sparse topic comment" in combined
        assert "Another sparse topic comment" in combined

    def test_maximally_viral_low_relevance_comment_loses_to_on_topic_comment(self):
        """A clamped 5000-vote comment should still lose to a highly relevant 50-vote one."""
        viral_low_relevance = _candidate(
            url="https://example.com/max-viral",
            local_relevance=0.12,
            top_comments=[{"body": "Maximally viral but barely related comment", "score": 5000}],
        )
        on_topic = _candidate(
            url="https://example.com/on-topic-boundary",
            local_relevance=0.90,
            top_comments=[{"body": "Highly relevant lower voted boundary comment", "score": 50}],
        )
        report = _make_report(viral_low_relevance, on_topic)

        lines = render._render_top_comments(report)
        combined = "\n".join(lines)

        assert combined.index("Highly relevant") < combined.index("Maximally viral")

    def test_no_duplicate_comments(self):
        """Same comment body must appear at most once even if two candidates share the text."""
        cand_a = _candidate(
            url="https://example.com/a",
            local_relevance=0.8,
            top_comments=[{"body": "Shared comment body appears in two different threads", "score": 100}],
        )
        cand_b = _candidate(
            url="https://example.com/b",
            local_relevance=0.8,
            top_comments=[
                {"body": "Shared comment body appears in two different threads", "score": 80},
                {"body": "Second distinct comment that is different from the first", "score": 60},
            ],
        )
        report = _make_report(cand_a, cand_b)
        lines = render._render_top_comments(report)
        occurrences = sum(1 for line in lines if "Shared comment body" in line)
        assert occurrences == 1


def _cluster(index: int, candidate: schema.Candidate, score: float) -> schema.Cluster:
    return schema.Cluster(
        cluster_id=f"cluster-{index}",
        title=candidate.title,
        candidate_ids=[candidate.candidate_id],
        representative_ids=[candidate.candidate_id],
        sources=[candidate.source],
        score=score,
        uncertainty=None,
    )


class TestTopCommentsReadAllFloorClearingClusters:
    """The comments block is a cross-cutting evidence surface: a strong comment
    on a thread in cluster eleven must render even though only the top eight
    clusters are shown in ## Ranked Evidence Clusters."""

    def _report(self):
        candidates = []
        clusters = []
        for i in range(12):
            comments = None
            if i == 10:  # cluster 11, below the compact cluster_limit of 8
                comments = [
                    {"score": 3293, "author": "u/tableleg7", "excerpt": "Vladimir Putin doesn't care about black people"},
                    {"score": 1151, "author": "u/stereosalvation", "excerpt": "Someone didn't get paid off yet"},
                    {"score": 400, "author": "u/third", "excerpt": "third comment body here"},
                ]
            cand = _candidate(
                title=f"Thread {i}",
                url=f"https://www.reddit.com/r/Kanye/comments/t{i}/",
                local_relevance=0.6,
                top_comments=comments if comments else [],
            )
            cand.final_score = 60.0 - i
            candidates.append(cand)
            clusters.append(_cluster(i + 1, cand, cand.final_score))
        report = _make_report(*candidates)
        report.clusters = clusters
        return report

    def test_comment_from_cluster_eleven_renders_in_compact(self):
        text = render.render_compact(self._report(), cluster_limit=8)
        assert "## Top Community Comments" in text
        assert "Vladimir Putin doesn't care about black people" in text

    def test_entity_miss_cluster_member_still_excluded(self):
        report = self._report()
        report.ranked_candidates[10].explanation = "fallback-local-score (entity-miss demotion)"
        text = render.render_compact(report, cluster_limit=8)
        assert "Vladimir Putin doesn't care" not in text
