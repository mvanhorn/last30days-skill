"""Reranking with LLM-scored relevance and demotion of low-confidence candidates."""

from __future__ import annotations

import json

from . import http, providers, schema

INTENT_SCORING_HINTS: dict[str, str] = {
    "comparison": (
        "Prefer items that directly compare, contrast, or benchmark the entities"
        " mentioned in the topic. Head-to-head comparisons score higher than items"
        " covering only one entity."
    ),
    "how_to": (
        "Prefer tutorials, step-by-step guides, and practical demonstrations."
        " Video walkthroughs and code examples score higher than theoretical discussion."
    ),
    "prediction": (
        "Prefer items with quantitative forecasts, odds, market data, or expert"
        " predictions. Vague speculation scores lower."
    ),
    "factual": (
        "Prefer items with specific facts, dates, numbers, and primary sources."
        " News reports with direct quotes score higher than commentary."
    ),
    "opinion": (
        "Prefer items with substantive opinions backed by reasoning or evidence."
        " Hot takes without substance score lower."
    ),
    "breaking_news": (
        "Prefer the latest updates, eyewitness reports, and official statements."
        " Recency matters more than depth."
    ),
    "concept": (
        "Prefer clear explanations with examples or analogies. Accessible content"
        " scores higher than dense academic papers unless the topic is highly technical."
    ),
    "product": (
        "Prefer hands-on reviews, benchmarks, and user experience reports."
        " Marketing copy and listicles score lower."
    ),
}


def rerank_candidates(
    *,
    topic: str,
    plan: schema.QueryPlan,
    candidates: list[schema.Candidate],
    provider: providers.ReasoningClient | None,
    model: str | None,
    shortlist_size: int,
) -> list[schema.Candidate]:
    """Rerank the fused shortlist, demoting candidates the reranker scored as irrelevant."""
    shortlisted = candidates[:shortlist_size]
    if provider and model and shortlisted:
        try:
            response = provider.generate_json(model, _build_prompt(topic, plan, shortlisted))
            _apply_llm_scores(shortlisted, response)
        except (ValueError, KeyError, json.JSONDecodeError, OSError, http.HTTPError) as exc:
            import sys
            print(f"[Rerank] LLM reranking failed, using local fallback: {type(exc).__name__}: {exc}", file=sys.stderr)
            _apply_fallback_scores(shortlisted)
    else:
        _apply_fallback_scores(shortlisted)

    if len(candidates) > shortlist_size:
        tail = candidates[shortlist_size:]
        _apply_fallback_scores(tail)

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.final_score,
            -(candidate.engagement or -1),
            min(candidate.native_ranks.values(), default=999),
            candidate.title,
        ),
    )


def _intent_hint_block(plan: schema.QueryPlan) -> str:
    hint = INTENT_SCORING_HINTS.get(plan.intent, "")
    if hint:
        return f"\nIntent-specific guidance ({plan.intent}):\n- {hint}\n"
    return ""


def _build_prompt(topic: str, plan: schema.QueryPlan, candidates: list[schema.Candidate]) -> str:
    ranking_queries = "\n".join(
        f"- {subquery.label}: {subquery.ranking_query}"
        for subquery in plan.subqueries
    )
    candidate_block = "\n".join(
        "\n".join(
            [
                f"- candidate_id: {candidate.candidate_id}",
                f"  sources: {schema.candidate_source_label(candidate)}",
                f"  title: {candidate.title[:220]}",
                f"  snippet: {candidate.snippet[:420]}",
                f"  date: {schema.candidate_best_published_at(candidate) or 'unknown'}",
                f"  matched_subqueries: {', '.join(candidate.subquery_labels)}",
            ]
        )
        for candidate in candidates
    )
    return f"""
Judge search-result relevance for a last-30-days research pipeline.

Topic: {topic}
Intent: {plan.intent}
Ranking queries:
{ranking_queries}

Return JSON only:
{{
  "scores": [
    {{
      "candidate_id": "id",
      "relevance": 0-100,
      "reason": "short reason"
    }}
  ]
}}

Scoring guidance:
- 90 to 100: one of the strongest pieces of evidence
- 70 to 89: clearly relevant and useful
- 40 to 69: somewhat relevant but weaker
- 0 to 39: weak, redundant, or off-target
{_intent_hint_block(plan)}
Candidates:
{candidate_block}
""".strip()


def _apply_llm_scores(candidates: list[schema.Candidate], payload: dict) -> None:
    scores = {}
    for row in payload.get("scores") or []:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        scores[candidate_id] = (
            max(0.0, min(100.0, float(row.get("relevance") or 0.0))),
            str(row.get("reason") or "").strip() or None,
        )
    for candidate in candidates:
        rerank_score, reason = scores.get(candidate.candidate_id, _fallback_tuple(candidate))
        candidate.rerank_score = rerank_score
        candidate.explanation = reason
        candidate.final_score = _final_score(candidate)


def _apply_fallback_scores(candidates: list[schema.Candidate]) -> None:
    for candidate in candidates:
        rerank_score, reason = _fallback_tuple(candidate)
        candidate.rerank_score = rerank_score
        candidate.explanation = reason
        candidate.final_score = _final_score(candidate)


def _fallback_tuple(candidate: schema.Candidate) -> tuple[float, str]:
    score = (
        (candidate.local_relevance * 100.0 * 0.7)
        + (candidate.freshness * 0.2)
        + (candidate.source_quality * 100.0 * 0.1)
    )
    return max(0.0, min(100.0, score)), "fallback-local-score"


def _final_score(candidate: schema.Candidate) -> float:
    normalized_rrf = _normalized_rrf(candidate.rrf_score)
    rerank_score = candidate.rerank_score or 0.0
    # Engagement bonus: high-engagement items (viral TikToks, popular YouTube videos)
    # get a boost so they aren't buried by lower-engagement but text-relevant items.
    # Engagement is log1p-normalized (0-100 range via signals.py), so a 2.5M-view
    # TikTok scores ~15 and a 1500-view one scores ~7. The 0.05 weight gives a
    # meaningful but not dominant boost.
    engagement_val = candidate.engagement if candidate.engagement is not None else 0.0
    base = (
        0.60 * rerank_score
        + 0.20 * normalized_rrf
        + 0.10 * candidate.freshness
        + 0.05 * (candidate.source_quality * 100.0)
        + 0.05 * min(engagement_val * 6.0, 100.0)
    )
    if candidate.rerank_score is not None and candidate.rerank_score < 20.0:
        base *= 0.3
    return base




def score_fun(
    *,
    topic: str,
    candidates: list[schema.Candidate],
    provider: providers.ReasoningClient | None,
    model: str | None,
    max_candidates: int = 60,
) -> None:
    """Score candidates for humor, cleverness, and virality (the fun judge)."""
    pool = candidates[:max_candidates]
    if provider and model and pool:
        try:
            response = provider.generate_json(model, _build_fun_prompt(topic, pool))
            _apply_fun_scores(pool, response)
        except (ValueError, KeyError, json.JSONDecodeError, OSError, http.HTTPError) as exc:
            import sys
            print(f"[FunJudge] LLM scoring failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            _apply_fun_fallback(pool)
    else:
        _apply_fun_fallback(pool)


def _build_fun_prompt(topic: str, candidates: list[schema.Candidate]) -> str:
    candidate_block = "\n".join(
        "\n".join([
            f"- candidate_id: {c.candidate_id}",
            f"  source: {schema.candidate_source_label(c)}",
            f"  title: {c.title[:220]}",
            f"  snippet: {c.snippet[:420]}",
            f"  comments: {_extract_comment_text(c)[:300]}",
        ])
        for c in candidates
    )
    return (
        "Score each item for humor, cleverness, wit, and shareability.\n"
        "You are the fun judge. A press conference is 0. A one-liner that makes you laugh is 95.\n\n"
        f"Topic: {topic}\n\n"
        "Return JSON only:\n"
        '{\n  \"scores\": [{\"candidate_id\": \"id\", \"fun\": 0-100, \"reason\": \"short reason\"}]\n}\n\n'
        "Scoring: 90-100=genuinely hilarious, 70-89=witty/clever, "
        "40-69=has personality, 20-39=straight news, 0-19=dry/official.\n"
        "Prefer SHORT PUNCHY content. A 15-word tweet > a 500-word analysis.\n\n"
        f"Candidates:\n{candidate_block}"
    )


def _extract_comment_text(candidate: schema.Candidate) -> str:
    parts = []
    for item in candidate.source_items:
        for comment in item.metadata.get("top_comments", [])[:3]:
            body = comment.get("body", "") if isinstance(comment, dict) else str(comment)
            if body:
                parts.append(body[:150])
        for insight in item.metadata.get("comment_insights", [])[:2]:
            if insight:
                parts.append(str(insight)[:150])
    return " | ".join(parts) if parts else ""


def _apply_fun_scores(candidates: list[schema.Candidate], payload: dict) -> None:
    scores = {}
    for row in payload.get("scores") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("candidate_id") or "").strip()
        if not cid:
            continue
        scores[cid] = (
            max(0.0, min(100.0, float(row.get("fun") or 0.0))),
            str(row.get("reason") or "").strip() or None,
        )
    for c in candidates:
        if c.candidate_id in scores:
            c.fun_score, c.fun_explanation = scores[c.candidate_id]
        else:
            _apply_single_fun_fallback(c)


def _apply_fun_fallback(candidates: list[schema.Candidate]) -> None:
    for c in candidates:
        _apply_single_fun_fallback(c)


def _apply_single_fun_fallback(candidate: schema.Candidate) -> None:
    text = candidate.title + " " + (candidate.snippet or "") + " " + _extract_comment_text(candidate)
    text_len = len(text.strip())
    eng = candidate.engagement if candidate.engagement is not None else 0.0
    shortness = max(0, (200 - text_len) / 200) * 30
    eng_bonus = min(eng * 2.0, 40)
    markers = ["lol", "lmao", "dead", "hilarious", "funny", "bruh", "ratio", "nah", "bro", "ain't no way", "i'm crying", "rent free"]
    marker_bonus = 10 if any(m in text.lower() for m in markers) else 0
    candidate.fun_score = max(0.0, min(100.0, shortness + eng_bonus + marker_bonus))
    candidate.fun_explanation = "heuristic-fallback"


def _normalized_rrf(rrf_score: float) -> float:
    # Empirical ceiling for normalized RRF scores at the pool sizes we use.
    # Max single-stream RRF at rank 1 is 1/(K+1) ~ 0.016; multi-stream
    # accumulation reaches ~0.08.
    return max(0.0, min(100.0, (rrf_score / 0.08) * 100.0))
