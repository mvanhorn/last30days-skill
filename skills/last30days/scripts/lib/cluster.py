"""Candidate clustering and representative selection."""

from __future__ import annotations

from . import dedupe, entity_extract, schema


def _cluster_score(group: list[schema.Candidate]) -> float:
    """Highest score among in-window members, falling back to all members when all are stale."""
    if not group:
        return 0.0
    in_window = [c for c in group if not schema.candidate_out_of_window(c)]
    return max(c.final_score for c in (in_window or group))


def _cluster_out_of_window(
    cluster: schema.Cluster,
    candidate_by_id: dict[str, schema.Candidate],
) -> bool:
    """True when every known member of the cluster is out of the requested window."""
    members = [
        candidate_by_id[cid]
        for cid in cluster.candidate_ids
        if cid in candidate_by_id
    ]
    return bool(members) and all(schema.candidate_out_of_window(c) for c in members)


def _cluster_sort_key(candidate: schema.Candidate) -> tuple:
    """Sort key that partitions stale candidates below fresh ones.

    Stale items (all dated source_items outside the window) must never lead
    cluster representatives or render as the cluster title.
    """
    return (
        1 if schema.candidate_out_of_window(candidate) else 0,
        -candidate.final_score,
    )

CLUSTERABLE_INTENTS = {"breaking_news", "opinion", "comparison", "prediction"}

def _candidate_text(candidate: schema.Candidate) -> str:
    return " ".join(part for part in [candidate.title, candidate.snippet] if part).strip()

def _mmr_representatives(
    candidates: list[schema.Candidate],
    text_cache: dict[str, dedupe._PreparedText],
    limit: int = 3,
    diversity_lambda: float = 0.75,
) -> list[str]:
    selected: list[schema.Candidate] = []
    remaining_set = {c.candidate_id for c in candidates}
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        if not selected:
            best = min(remaining, key=_cluster_sort_key)
            selected.append(best)
            remaining_set.discard(best.candidate_id)
            remaining = [c for c in remaining if c.candidate_id in remaining_set]
            continue

        selected_preps = [text_cache[c.candidate_id] for c in selected]

        def score(candidate: schema.Candidate) -> tuple:
            prep = text_cache[candidate.candidate_id]
            diversity_penalty = max(
                dedupe.prepared_similarity(prep, sp) for sp in selected_preps
            )
            base_score = (diversity_lambda * candidate.final_score) - ((1 - diversity_lambda) * diversity_penalty * 100)
            return (
                0 if schema.candidate_out_of_window(candidate) else 1,
                base_score,
            )

        best = max(remaining, key=score)
        selected.append(best)
        remaining_set.discard(best.candidate_id)
        remaining = [c for c in remaining if c.candidate_id in remaining_set]
    return [candidate.candidate_id for candidate in selected]


def cluster_candidates(
    candidates: list[schema.Candidate],
    plan: schema.QueryPlan,
) -> list[schema.Cluster]:
    """Greedy clustering around high-ranked leaders."""
    if plan.intent not in CLUSTERABLE_INTENTS or plan.cluster_mode == "none":
        clusters = []
        for index, candidate in enumerate(candidates, start=1):
            cluster_id = f"cluster-{index}"
            candidate.cluster_id = cluster_id
            clusters.append(
                schema.Cluster(
                    cluster_id=cluster_id,
                    title=candidate.title,
                    candidate_ids=[candidate.candidate_id],
                    representative_ids=[candidate.candidate_id],
                    sources=sorted(schema.candidate_sources(candidate)),
                    score=candidate.final_score,
                    uncertainty=None,
                )
            )
        return clusters

    text_cache: dict[str, dedupe._PreparedText] = {
        c.candidate_id: dedupe._PreparedText(_candidate_text(c))
        for c in candidates
    }

    groups: list[list[schema.Candidate]] = []
    # Lower threshold for breaking_news: related articles share fewer exact
    # words but cover the same event.
    threshold = 0.42 if plan.intent == "breaking_news" else 0.48
    for candidate in candidates:
        assigned = False
        cand_prep = text_cache[candidate.candidate_id]
        for group in groups:
            leader = group[0]
            similarity = dedupe.prepared_similarity(cand_prep, text_cache[leader.candidate_id])
            if similarity >= threshold:
                group.append(candidate)
                assigned = True
                break
        if not assigned:
            groups.append([candidate])

    clusters: list[schema.Cluster] = []
    for index, group in enumerate(groups, start=1):
        group.sort(key=_cluster_sort_key)
        cluster_id = f"cluster-{index}"
        representatives = _mmr_representatives(group, text_cache)
        for candidate in group:
            candidate.cluster_id = cluster_id
        clusters.append(
            schema.Cluster(
                cluster_id=cluster_id,
                title=group[0].title,
                candidate_ids=[candidate.candidate_id for candidate in group],
                representative_ids=representatives,
                sources=sorted({source for candidate in group for source in schema.candidate_sources(candidate)}),
                score=_cluster_score(group),
                uncertainty=_cluster_uncertainty(group),
            )
        )

    # Second pass: merge small clusters that share entities across sources.
    clusters = _merge_entity_clusters(
        clusters,
        candidates,
        min_shared_entities=2,
        topic_entities=entity_extract.extract_text_entities(plan.raw_topic),
    )

    candidate_by_id = {c.candidate_id: c for c in candidates}
    return sorted(
        clusters,
        key=lambda cluster: (
            1 if _cluster_out_of_window(cluster, candidate_by_id) else 0,
            -cluster.score,
        ),
    )


def _merge_entity_clusters(
    clusters: list[schema.Cluster],
    all_candidates: list[schema.Candidate],
    *,
    min_shared_entities: int = 2,
    topic_entities: set[str] | frozenset[str] = frozenset(),
    max_cluster_size: int = 3,
) -> list[schema.Cluster]:
    """Merge small clusters that cover the same story across different sources.

    The initial greedy pass uses text similarity which misses cross-source
    matches where phrasing differs. This second pass looks at entity overlap
    (proper nouns, names, numbers) to catch cases like:
      - Reddit: "Kanye West to headline all three nights of Wireless Festival 2026"
      - X: "BREAKING: Kanye West (Ye) is making his massive UK comeback!"
    """
    if len(clusters) < 2:
        return clusters

    candidate_map = {c.candidate_id: c for c in all_candidates}

    # Build entity sets per cluster
    cluster_entities: list[set[str]] = []
    for cl in clusters:
        entities: set[str] = set()
        for cid in cl.candidate_ids:
            cand = candidate_map.get(cid)
            if cand:
                entities |= entity_extract.extract_text_entities(_candidate_text(cand))
        cluster_entities.append(entities)

    # Track running merged cluster sizes so a small cluster cannot absorb
    # unbounded unrelated stories past max_cluster_size.
    merged_into: dict[int, int] = {}  # index -> merge target index
    cluster_sizes = [len(cl.candidate_ids) for cl in clusters]
    for i in range(len(clusters)):
        if i in merged_into or cluster_sizes[i] >= max_cluster_size:
            continue
        sources_i = set(clusters[i].sources)
        for j in range(i + 1, len(clusters)):
            if j in merged_into or cluster_sizes[i] + cluster_sizes[j] > max_cluster_size:
                continue
            # Require different sources to merge (same-source should already be grouped)
            sources_j = set(clusters[j].sources)
            if sources_i == sources_j and len(sources_i) == 1:
                continue
            # Prevent Polymarket clusters from merging with non-Polymarket
            # clusters. Prediction markets about "Sam Altman equity" should not
            # merge into a news cluster about "Sam Altman rivalry" just because
            # both mention the same entity.
            poly_i = "polymarket" in sources_i
            poly_j = "polymarket" in sources_j
            if poly_i != poly_j:
                continue

            shared_entities = cluster_entities[i] & cluster_entities[j]
            if len(shared_entities) < min_shared_entities:
                continue
            if topic_entities:
                non_topic_i = cluster_entities[i] - topic_entities
                non_topic_j = cluster_entities[j] - topic_entities
                if (non_topic_i or non_topic_j) and not (shared_entities - topic_entities):
                    continue
            overlap = entity_extract.entity_overlap(cluster_entities[i], cluster_entities[j])
            if overlap >= 0.45:
                merged_into[j] = i
                cluster_sizes[i] += cluster_sizes[j]
                sources_i |= sources_j
                if cluster_sizes[i] >= max_cluster_size:
                    break

    if not merged_into:
        return clusters

    # Build merged cluster list
    result: list[schema.Cluster] = []
    for i, cl in enumerate(clusters):
        if i in merged_into:
            continue
        # Collect all clusters merged into this one
        merge_sources = [i] + [j for j, target in merged_into.items() if target == i]
        if len(merge_sources) == 1:
            result.append(cl)
            continue

        # Combine candidates from all merged clusters
        combined_cids: list[str] = []
        combined_sources: set[str] = set()
        best_score = 0.0
        for idx in merge_sources:
            combined_cids.extend(clusters[idx].candidate_ids)
            combined_sources.update(clusters[idx].sources)
            best_score = max(best_score, clusters[idx].score)

        # Pick representatives from combined pool
        combined_candidates = [candidate_map[cid] for cid in combined_cids if cid in candidate_map]
        combined_candidates.sort(key=_cluster_sort_key)
        ordered_cids = [c.candidate_id for c in combined_candidates]
        known_cids = set(ordered_cids)
        ordered_cids.extend(cid for cid in combined_cids if cid not in known_cids)
        merge_text_cache = {
            c.candidate_id: dedupe._PreparedText(_candidate_text(c))
            for c in combined_candidates
        }
        reps = _mmr_representatives(combined_candidates, merge_text_cache)

        cluster_id = cl.cluster_id
        for cid in combined_cids:
            cand = candidate_map.get(cid)
            if cand:
                cand.cluster_id = cluster_id

        result.append(schema.Cluster(
            cluster_id=cluster_id,
            title=combined_candidates[0].title if combined_candidates else cl.title,
            candidate_ids=ordered_cids,
            representative_ids=reps,
            sources=sorted(combined_sources),
            score=_cluster_score(combined_candidates) if combined_candidates else best_score,
            uncertainty=_cluster_uncertainty(combined_candidates),
        ))

    return result


def _cluster_uncertainty(group: list[schema.Candidate]) -> str | None:
    if not group:
        return "thin-evidence"
    sources = {source for candidate in group for source in schema.candidate_sources(candidate)}
    if len(sources) == 1:
        return "single-source"
    if _cluster_score(group) < 55:
        return "thin-evidence"
    return None
