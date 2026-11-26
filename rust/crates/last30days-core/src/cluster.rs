//! Candidate clustering, MMR representative selection, and cross-source entity merging.
//!
//! Mirrors `skills/last30days/scripts/lib/cluster.py`.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::dedupe::PreparedText;
use crate::entity;

/// Whether a candidate falls within or outside the requested research date window.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum WindowState {
    /// Candidate is inside the requested window (or undated).
    #[default]
    InWindow,
    /// All dated source items for this candidate fall outside the requested window.
    OutOfWindow,
}

/// Minimal candidate projection passed into [`cluster_candidates`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClusterCandidate {
    /// Stable candidate identifier.
    pub candidate_id: String,
    /// Display title.
    pub title: String,
    /// Display snippet.
    pub snippet: String,
    /// Distinct sources backing this candidate.
    pub sources: Vec<String>,
    /// Final reranked score.
    pub final_score: f64,
    /// Whether this candidate is out of the requested date window.
    #[serde(default)]
    pub window_state: WindowState,
}

/// Query plan parameters controlling clustering behavior.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClusterPlan {
    /// Plan intent (e.g. `"breaking_news"`, `"opinion"`, `"comparison"`, `"prediction"`, `"how_to"`).
    pub intent: String,
    /// Cluster mode (`"none"`, `"story"`, etc.).
    pub cluster_mode: String,
    /// Raw user topic for filtering topic-only entities during cross-source merge.
    pub raw_topic: String,
}

/// Output cluster matching `schema.Cluster`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClusterOutput {
    /// Cluster identifier (e.g. `"cluster-1"`).
    pub cluster_id: String,
    /// Representative cluster title.
    pub title: String,
    /// Ordered candidate identifiers belonging to this cluster.
    pub candidate_ids: Vec<String>,
    /// Selected MMR representative candidate identifiers (up to 3).
    pub representative_ids: Vec<String>,
    /// Sorted unique source names represented in this cluster.
    pub sources: Vec<String>,
    /// Cluster score (highest in-window member score, or highest overall when all are stale).
    pub score: f64,
    /// Uncertainty tag (`"single-source"`, `"thin-evidence"`, or `None`).
    pub uncertainty: Option<String>,
}

const MMR_LIMIT: usize = 3;
const MMR_DIVERSITY_LAMBDA: f64 = 0.75;
const MIN_SHARED_ENTITIES: usize = 2;
const MAX_CLUSTER_SIZE: usize = 3;
const ENTITY_OVERLAP_THRESHOLD: f64 = 0.45;
const THIN_EVIDENCE_SCORE_FLOOR: f64 = 55.0;

fn is_clusterable_intent(intent: &str) -> bool {
    matches!(
        intent,
        "breaking_news" | "opinion" | "comparison" | "prediction"
    )
}

fn candidate_text(candidate: &ClusterCandidate) -> String {
    match (candidate.title.is_empty(), candidate.snippet.is_empty()) {
        (true, true) => String::new(),
        (false, true) => candidate.title.trim().to_owned(),
        (true, false) => candidate.snippet.trim().to_owned(),
        (false, false) => format!("{} {}", candidate.title, candidate.snippet)
            .trim()
            .to_owned(),
    }
}

fn window_rank(state: WindowState) -> u8 {
    match state {
        WindowState::InWindow => 0,
        WindowState::OutOfWindow => 1,
    }
}

fn compare_cluster_member(a: &ClusterCandidate, b: &ClusterCandidate) -> std::cmp::Ordering {
    window_rank(a.window_state)
        .cmp(&window_rank(b.window_state))
        .then_with(|| b.final_score.total_cmp(&a.final_score))
}

fn cluster_score(group: &[usize], candidates: &[ClusterCandidate]) -> f64 {
    if group.is_empty() {
        return 0.0;
    }
    let mut best_in_window: Option<f64> = None;
    let mut best_all = f64::NEG_INFINITY;
    for &idx in group {
        let c = &candidates[idx];
        if c.final_score > best_all {
            best_all = c.final_score;
        }
        if c.window_state == WindowState::InWindow {
            best_in_window = Some(match best_in_window {
                Some(prev) => prev.max(c.final_score),
                None => c.final_score,
            });
        }
    }
    best_in_window.unwrap_or(best_all)
}

fn cluster_uncertainty(group: &[usize], candidates: &[ClusterCandidate]) -> Option<String> {
    if group.is_empty() {
        return Some("thin-evidence".to_owned());
    }
    let mut sources = BTreeSet::new();
    for &idx in group {
        for s in &candidates[idx].sources {
            sources.insert(s.as_str());
        }
    }
    if sources.len() == 1 {
        return Some("single-source".to_owned());
    }
    if cluster_score(group, candidates) < THIN_EVIDENCE_SCORE_FLOOR {
        return Some("thin-evidence".to_owned());
    }
    None
}

fn sorted_sources(group: &[usize], candidates: &[ClusterCandidate]) -> Vec<String> {
    let mut set = BTreeSet::new();
    for &idx in group {
        for s in &candidates[idx].sources {
            set.insert(s.clone());
        }
    }
    set.into_iter().collect()
}

fn mmr_representatives(
    group: &[usize],
    candidates: &[ClusterCandidate],
    prepared: &[PreparedText],
) -> Vec<String> {
    let mut remaining: Vec<usize> = group.to_vec();
    let mut selected: Vec<usize> = Vec::with_capacity(MMR_LIMIT.min(group.len()));

    while !remaining.is_empty() && selected.len() < MMR_LIMIT {
        if selected.is_empty() {
            // Python min(remaining, key=_cluster_sort_key): first minimum wins on ties.
            let mut best_pos = 0;
            for pos in 1..remaining.len() {
                if compare_cluster_member(
                    &candidates[remaining[pos]],
                    &candidates[remaining[best_pos]],
                ) == std::cmp::Ordering::Less
                {
                    best_pos = pos;
                }
            }
            let best_idx = remaining.remove(best_pos);
            selected.push(best_idx);
            continue;
        }

        // Python max(remaining, key=score): first maximum wins on ties.
        let mut best_pos = 0;
        let mut best_key: Option<(u8, f64)> = None;
        for (pos, &cand_idx) in remaining.iter().enumerate() {
            let prep = &prepared[cand_idx];
            let mut diversity_penalty = 0.0_f64;
            for &sel_idx in &selected {
                let sim = prep.similarity(&prepared[sel_idx]);
                if sim > diversity_penalty {
                    diversity_penalty = sim;
                }
            }
            let base_score = (MMR_DIVERSITY_LAMBDA * candidates[cand_idx].final_score)
                - ((1.0 - MMR_DIVERSITY_LAMBDA) * diversity_penalty * 100.0);
            let in_window_flag: u8 =
                u8::from(candidates[cand_idx].window_state == WindowState::InWindow);
            let key = (in_window_flag, base_score);
            let is_better = match best_key {
                None => true,
                Some((prev_flag, prev_score)) => {
                    key.0 > prev_flag || (key.0 == prev_flag && key.1 > prev_score)
                }
            };
            if is_better {
                best_key = Some(key);
                best_pos = pos;
            }
        }
        let best_idx = remaining.remove(best_pos);
        selected.push(best_idx);
    }

    selected
        .into_iter()
        .map(|idx| candidates[idx].candidate_id.clone())
        .collect()
}

struct InternalCluster {
    cluster_id: String,
    member_indices: Vec<usize>,
    output: ClusterOutput,
}

/// Clusters ranked candidates using greedy leader assignment, MMR representative
/// selection, and cross-source entity merging.
pub fn cluster_candidates(
    candidates: &[ClusterCandidate],
    plan: &ClusterPlan,
) -> Vec<ClusterOutput> {
    if !is_clusterable_intent(&plan.intent) || plan.cluster_mode == "none" {
        return candidates
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let mut sources: Vec<String> = c
                    .sources
                    .iter()
                    .cloned()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect();
                sources.sort();
                ClusterOutput {
                    cluster_id: format!("cluster-{}", i + 1),
                    title: c.title.clone(),
                    candidate_ids: vec![c.candidate_id.clone()],
                    representative_ids: vec![c.candidate_id.clone()],
                    sources,
                    score: c.final_score,
                    uncertainty: None,
                }
            })
            .collect();
    }

    let texts: Vec<String> = candidates.iter().map(candidate_text).collect();
    let prepared: Vec<PreparedText> = texts.iter().map(|t| PreparedText::new(t)).collect();

    let threshold = if plan.intent == "breaking_news" {
        0.42
    } else {
        0.48
    };

    let mut groups: Vec<Vec<usize>> = Vec::new();
    for cand_idx in 0..candidates.len() {
        let mut assigned = false;
        for group in &mut groups {
            let leader_idx = group[0];
            if prepared[cand_idx].similarity_at_least(&prepared[leader_idx], threshold) {
                group.push(cand_idx);
                assigned = true;
                break;
            }
        }
        if !assigned {
            groups.push(vec![cand_idx]);
        }
    }

    let mut clusters: Vec<InternalCluster> = Vec::with_capacity(groups.len());
    for (idx, mut group) in groups.into_iter().enumerate() {
        group.sort_by(|&a, &b| compare_cluster_member(&candidates[a], &candidates[b]));
        let cluster_id = format!("cluster-{}", idx + 1);
        let reps = mmr_representatives(&group, candidates, &prepared);
        let sources = sorted_sources(&group, candidates);
        let score = cluster_score(&group, candidates);
        let uncertainty = cluster_uncertainty(&group, candidates);
        let candidate_ids = group
            .iter()
            .map(|&i| candidates[i].candidate_id.clone())
            .collect();
        let title = candidates[group[0]].title.clone();
        clusters.push(InternalCluster {
            cluster_id: cluster_id.clone(),
            member_indices: group,
            output: ClusterOutput {
                cluster_id,
                title,
                candidate_ids,
                representative_ids: reps,
                sources,
                score,
                uncertainty,
            },
        });
    }

    let topic_entities = entity::extract_text_entities(&plan.raw_topic);
    let merged = merge_entity_clusters(clusters, candidates, &texts, &prepared, &topic_entities);

    let mut outputs: Vec<(u8, f64, ClusterOutput)> = merged
        .into_iter()
        .map(|cl| {
            let all_stale = !cl.member_indices.is_empty()
                && cl
                    .member_indices
                    .iter()
                    .all(|&idx| candidates[idx].window_state == WindowState::OutOfWindow);
            (u8::from(all_stale), cl.output.score, cl.output)
        })
        .collect();

    outputs.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| b.1.total_cmp(&a.1)));
    outputs.into_iter().map(|(_, _, out)| out).collect()
}

fn compute_entity_merge_targets(
    clusters: &[InternalCluster],
    cluster_entities: &[BTreeSet<String>],
    topic_entities: &BTreeSet<String>,
) -> BTreeMap<usize, usize> {
    let mut merged_into: BTreeMap<usize, usize> = BTreeMap::new();
    let mut cluster_sizes: Vec<usize> = clusters.iter().map(|cl| cl.member_indices.len()).collect();

    for i in 0..clusters.len() {
        if merged_into.contains_key(&i) || cluster_sizes[i] >= MAX_CLUSTER_SIZE {
            continue;
        }
        let mut sources_i: BTreeSet<&str> = clusters[i]
            .output
            .sources
            .iter()
            .map(String::as_str)
            .collect();
        for j in (i + 1)..clusters.len() {
            if merged_into.contains_key(&j)
                || cluster_sizes[i] + cluster_sizes[j] > MAX_CLUSTER_SIZE
            {
                continue;
            }
            let sources_j: BTreeSet<&str> = clusters[j]
                .output
                .sources
                .iter()
                .map(String::as_str)
                .collect();
            if sources_i == sources_j && sources_i.len() == 1 {
                continue;
            }
            if sources_i.contains("polymarket") != sources_j.contains("polymarket") {
                continue;
            }
            let shared_count = cluster_entities[i]
                .intersection(&cluster_entities[j])
                .count();
            if shared_count < MIN_SHARED_ENTITIES {
                continue;
            }
            if !topic_entities.is_empty() {
                let has_non_topic_i = cluster_entities[i]
                    .iter()
                    .any(|e| !topic_entities.contains(e));
                let has_non_topic_j = cluster_entities[j]
                    .iter()
                    .any(|e| !topic_entities.contains(e));
                let has_shared_non_topic = cluster_entities[i]
                    .intersection(&cluster_entities[j])
                    .any(|e| !topic_entities.contains(e));
                if (has_non_topic_i || has_non_topic_j) && !has_shared_non_topic {
                    continue;
                }
            }
            if entity::entity_overlap(&cluster_entities[i], &cluster_entities[j])
                >= ENTITY_OVERLAP_THRESHOLD
            {
                merged_into.insert(j, i);
                cluster_sizes[i] += cluster_sizes[j];
                sources_i.extend(sources_j);
                if cluster_sizes[i] >= MAX_CLUSTER_SIZE {
                    break;
                }
            }
        }
    }
    merged_into
}

fn merge_entity_clusters(
    clusters: Vec<InternalCluster>,
    candidates: &[ClusterCandidate],
    texts: &[String],
    prepared: &[PreparedText],
    topic_entities: &BTreeSet<String>,
) -> Vec<InternalCluster> {
    if clusters.len() < 2 {
        return clusters;
    }

    let cluster_entities: Vec<BTreeSet<String>> = clusters
        .iter()
        .map(|cl| {
            let mut set = BTreeSet::new();
            for &idx in &cl.member_indices {
                set.extend(entity::extract_text_entities(&texts[idx]));
            }
            set
        })
        .collect();

    let merged_into = compute_entity_merge_targets(&clusters, &cluster_entities, topic_entities);
    if merged_into.is_empty() {
        return clusters;
    }

    let mut result = Vec::with_capacity(clusters.len() - merged_into.len());
    for i in 0..clusters.len() {
        if merged_into.contains_key(&i) {
            continue;
        }
        let mut merge_sources = vec![i];
        for (&src, &target) in &merged_into {
            if target == i {
                merge_sources.push(src);
            }
        }
        if merge_sources.len() == 1 {
            let cl = &clusters[i];
            result.push(InternalCluster {
                cluster_id: cl.cluster_id.clone(),
                member_indices: cl.member_indices.clone(),
                output: cl.output.clone(),
            });
            continue;
        }

        let mut combined_indices: Vec<usize> = Vec::new();
        let mut combined_sources: BTreeSet<String> = BTreeSet::new();
        for &idx in &merge_sources {
            combined_indices.extend_from_slice(&clusters[idx].member_indices);
            for s in &clusters[idx].output.sources {
                combined_sources.insert(s.clone());
            }
        }
        combined_indices.sort_by(|&a, &b| compare_cluster_member(&candidates[a], &candidates[b]));
        let reps = mmr_representatives(&combined_indices, candidates, prepared);
        let score = cluster_score(&combined_indices, candidates);
        let uncertainty = cluster_uncertainty(&combined_indices, candidates);
        let title = candidates[combined_indices[0]].title.clone();
        let candidate_ids = combined_indices
            .iter()
            .map(|&idx| candidates[idx].candidate_id.clone())
            .collect();
        let cluster_id = clusters[i].cluster_id.clone();

        result.push(InternalCluster {
            cluster_id: cluster_id.clone(),
            member_indices: combined_indices,
            output: ClusterOutput {
                cluster_id,
                title,
                candidate_ids,
                representative_ids: reps,
                sources: combined_sources.into_iter().collect(),
                score,
                uncertainty,
            },
        });
    }

    result
}
