"""Story-diverse ordering of ranked clusters.

Clustering can split one dominant story into many singleton clusters (the
same-source merge ban, leader-only matching, or a ``cluster_mode=none`` plan),
and clusters are ranked by score alone. Without a cross-cluster rule the eight
clusters the chat report sees can all be the same event while other stories
the plan searched for sit at rank 19 and beyond.

``diversify_clusters`` reorders ``report.clusters`` once, in the pipeline,
right after clustering. It lives there and not in render so that the compact
numbering, the saved raw file and ``--drill N`` (which resolves
``report.clusters[N - 1]``) all point at the same cluster.

Rules, applied to the head window (the first ``HEAD_SIZE`` slots):

- Near-duplicates are penalized MMR style. Similarity is measured on titles
  only (the cluster title plus its representatives' titles); snippets are
  long and inflate overlap.
- One story takes at most ``STORY_CAP`` head slots. A story is a chain of
  title near-duplicates, or a subquery angle: clusters retrieved by a
  non-primary subquery whose titles use that subquery's own search terms
  (``outage`` for ``microsoft outage`` on the topic ``Microsoft``). Headlines
  about one event are often phrased too differently for pairwise similarity,
  but they keep the event's word. Selected clusters linked by either rule
  share a single cap.
- Each non-primary subquery with a cluster that clears the relevance floor
  gets at least one head slot.

Clusters below the relevance floor sink after every solid cluster, so the
visible window never wastes a slot on a cluster render will hide. With no
near-duplicates and every floor-clearing non-primary subquery already in the
head, the solid order is unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import NamedTuple

from . import cjk, dedupe, rerank, schema

HEAD_SIZE = 8
STORY_CAP = 3
# Title similarity at or above this marks two clusters as the same story.
NEAR_DUPLICATE_SIMILARITY = 0.4
# MMR weight: a near-duplicate's score is scaled by (1 - weight * similarity).
DUPLICATE_PENALTY = 0.6


def cluster_clears_relevance_floor(
    cluster: schema.Cluster,
    candidate_by_id: dict[str, schema.Candidate],
) -> bool:
    """Mirror of render's relevance floor for one cluster.

    Positive score, and at least one known member passes
    ``rerank.candidate_relevance_ok``. Missing member records are not treated
    as misses, matching ``render._clusters_clearing_relevance_floor``.
    """
    if cluster.score <= 0:
        return False
    members = [
        candidate_by_id[candidate_id]
        for candidate_id in cluster.candidate_ids
        if candidate_id in candidate_by_id
    ]
    if members and not any(rerank.candidate_relevance_ok(c) for c in members):
        return False
    return True


def _only_out_of_window(
    cluster: schema.Cluster,
    candidate_by_id: dict[str, schema.Candidate],
) -> bool:
    """Every known member of the cluster is out of the requested window."""
    members = [
        candidate_by_id[candidate_id]
        for candidate_id in cluster.candidate_ids
        if candidate_id in candidate_by_id
    ]
    return bool(members) and all(schema.candidate_out_of_window(c) for c in members)


def _terms(text: str, exclude: frozenset[str] = frozenset()) -> frozenset[str]:
    """Content tokens of ``text``, minus stopwords and ``exclude``."""
    return frozenset(
        token
        for token in cjk.segment(dedupe.normalize_text(text))
        if len(token) > 1 and token not in dedupe.STOPWORDS and token not in exclude
    )


def _angle_terms(plan: schema.QueryPlan | None) -> dict[str, frozenset[str]]:
    """Non-primary subquery label -> its own search terms, in plan order.

    The topic's tokens are removed, so ``microsoft outage`` on the topic
    ``Microsoft`` leaves ``{outage}``: the angle the planner named.
    """
    if plan is None or not plan.subqueries:
        return {}
    topic = _terms(plan.raw_topic or "")
    primary = plan.subqueries[0].label
    angles: dict[str, frozenset[str]] = {}
    for subquery in plan.subqueries[1:]:
        if subquery.label == primary or subquery.label in angles:
            continue
        angles[subquery.label] = _terms(subquery.search_query, topic)
    return angles


def _carried_labels(
    cluster: schema.Cluster,
    candidate_by_id: dict[str, schema.Candidate],
    wanted: Iterable[str],
) -> set[str]:
    """Subquery labels carried by the cluster's on-topic members."""
    wanted = set(wanted)
    labels: set[str] = set()
    for candidate_id in cluster.candidate_ids:
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or not rerank.candidate_relevance_ok(candidate):
            continue
        labels.update(label for label in candidate.subquery_labels if label in wanted)
    return labels


def _cluster_angle(
    title_terms: frozenset[str],
    carried: set[str],
    angles: dict[str, frozenset[str]],
) -> str | None:
    """The subquery angle a cluster's titles belong to, if any.

    A cluster belongs to an angle when one of its on-topic members came from
    that subquery and its titles use that subquery's own search terms. Ties go
    to the earlier subquery in the plan.
    """
    best, best_hits = None, 0
    for label, terms in angles.items():
        if label not in carried or not terms:
            continue
        hits = len(title_terms & terms)
        if hits > best_hits:
            best, best_hits = label, hits
    return best


def _title_texts(
    cluster: schema.Cluster,
    candidate_by_id: dict[str, schema.Candidate],
) -> list[dedupe._PreparedText]:
    titles: list[str] = []
    for title in [cluster.title] + [
        candidate_by_id[cid].title
        for cid in cluster.representative_ids
        if cid in candidate_by_id
    ]:
        if title and title not in titles:
            titles.append(title)
    return [dedupe._PreparedText(title) for title in titles]


def _similarity(
    left: Iterable[dedupe._PreparedText],
    right: Iterable[dedupe._PreparedText],
) -> float:
    right = list(right)
    return max(
        (dedupe.prepared_similarity(a, b) for a in left for b in right),
        default=0.0,
    )


def diversify_clusters(
    clusters: list[schema.Cluster],
    candidates: list[schema.Candidate],
    plan: schema.QueryPlan | None,
    *,
    head_size: int = HEAD_SIZE,
    story_cap: int = STORY_CAP,
) -> list[schema.Cluster]:
    """Return ``clusters`` reordered for story and subquery diversity."""
    if not clusters:
        return clusters
    candidate_by_id = {c.candidate_id: c for c in candidates}
    solid: list[schema.Cluster] = []
    sunk: list[schema.Cluster] = []
    for cluster in clusters:
        if cluster_clears_relevance_floor(cluster, candidate_by_id):
            solid.append(cluster)
        else:
            sunk.append(cluster)
    if len(solid) <= 1:
        return solid + sunk

    angles = _angle_terms(plan)
    texts = [_title_texts(c, candidate_by_id) for c in solid]
    angle_of: list[str | None] = []
    covers: list[set[str]] = []
    stale = [_only_out_of_window(cluster, candidate_by_id) for cluster in solid]
    for cluster, prepared in zip(solid, texts):
        carried = _carried_labels(cluster, candidate_by_id, angles)
        title_terms = frozenset().union(*(p.tokens for p in prepared))
        angle = _cluster_angle(title_terms, carried, angles)
        angle_of.append(angle)
        # A subquery with its own terms is represented only by a cluster on
        # its angle; one whose search terms are just the topic is represented
        # by any cluster it retrieved.
        # A cluster holding only out-of-window evidence never fills a
        # subquery's reserved slot: it stays behind current clusters.
        covers.append(
            set()
            if stale[len(covers)]
            else ({angle} if angle else set())
            | {label for label in carried if not angles[label]}
        )
    # Only labels some solid cluster represents can be reserved.
    uncovered = set().union(*covers)

    remaining = list(range(len(solid)))
    # Picked cluster indices in head order, each mapped to its current story.
    story_of: dict[int, int] = {}

    def story_matches(index: int) -> tuple[float, frozenset[int]]:
        best_sim = 0.0
        stories: set[int] = set()
        angle = angle_of[index]
        for chosen, story in story_of.items():
            sim = _similarity(texts[index], texts[chosen])
            best_sim = max(best_sim, sim)
            if sim >= NEAR_DUPLICATE_SIMILARITY or (
                angle is not None and angle == angle_of[chosen]
            ):
                stories.add(story)
        return best_sim, frozenset(stories)

    while remaining and len(story_of) < head_size:
        slots_left = head_size - len(story_of)
        story_counts = Counter(story_of.values())
        options: list[_Option] = []
        for index in remaining:
            sim, stories = story_matches(index)
            if 1 + sum(story_counts[story] for story in stories) > story_cap:
                continue
            options.append(_Option(index, sim, sim >= NEAR_DUPLICATE_SIMILARITY, stories))
        if not options:
            break

        # A label whose clusters are all story-capped cannot be reserved.
        uncovered &= set().union(*(covers[opt.index] for opt in options))
        if uncovered and slots_left <= len(uncovered):
            options = [opt for opt in options if covers[opt.index] & uncovered]
        # Out-of-window clusters are never promoted: one is eligible only
        # while no unplaced current cluster comes before it in the incoming
        # order, including one the story cap just skipped.
        first_current = min(
            (index for index in remaining if not stale[index]), default=None,
        )
        if first_current is not None:
            options = [
                opt for opt in options
                if not stale[opt.index] or opt.index < first_current
            ]
            if not options:
                break

        choice = _mmr_pick(options, solid)
        story = min(choice.stories, default=choice.index)
        for chosen, previous_story in story_of.items():
            if previous_story in choice.stories:
                story_of[chosen] = story
        story_of[choice.index] = story
        uncovered -= covers[choice.index]
        remaining.remove(choice.index)

    head = [solid[i] for i in story_of]
    tail = [solid[i] for i in range(len(solid)) if i not in story_of]
    return head + tail + sunk


class _Option(NamedTuple):
    index: int
    similarity: float
    duplicate: bool  # a title near-duplicate of a picked cluster
    stories: frozenset[int]


def _mmr_pick(options: list[_Option], solid: list[schema.Cluster]) -> _Option:
    """Pick the next head cluster, preserving the incoming order when nothing
    is a near-duplicate.

    The first non-duplicate option (in incoming order) is the default. A
    near-duplicate ahead of it wins only if its penalized score still beats the
    default's score. When every option is a near-duplicate, the best penalized
    score wins, ties by incoming order.
    """
    def penalized(opt: _Option) -> float:
        score = solid[opt.index].score
        if not opt.duplicate:
            return score
        return score * (1.0 - DUPLICATE_PENALTY * opt.similarity)

    default = next((opt for opt in options if not opt.duplicate), None)
    if default is None:
        return max(options, key=lambda opt: (penalized(opt), -opt.index))
    best = default
    for opt in options:
        if opt.index >= default.index:
            break
        if penalized(opt) >= solid[default.index].score and (
            best is default or penalized(opt) > penalized(best)
        ):
            best = opt
    return best
