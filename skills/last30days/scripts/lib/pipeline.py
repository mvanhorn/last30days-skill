"""v3.0.0 orchestration pipeline."""

from __future__ import annotations

import copy
import math
import queue
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from shutil import which
from typing import Any

from . import (
    arxiv,
    bird_x,
    bluesky,
    corpus,
    dates,
    dedupe,
    digg,
    dripstack,
    entity_extract,
    env,
    github,
    grounding,
    hackernews,
    health,
    hiring_signals,
    http,
    instagram,
    jobs,
    linkedin,
    library,
    library_index,
    normalize,
    permission_preflight,
    perplexity,
    pinterest,
    planner,
    polymarket,
    providers,
    query,
    reddit,
    reddit_listing,
    reddit_public,
    relevance,
    rerank,
    schema,
    signals,
    snippet,
    stocktwits,
    techmeme,
    threads,
    tiktok,
    truthsocial,
    trustpilot,
    xai_x,
    xiaohongshu_api,
    xquik,
    xurl_x,
    youtube_yt,
)
from .cluster import cluster_candidates
from .fusion import weighted_rrf

DISCOVERY_SOURCES = ("reddit", "hackernews", "digg", "x")
_DISCOVERY_GENERIC_DOMAIN_TERMS = {
    "ai", "artificial", "intelligence", "tech", "technology", "trending", "trend",
}

DEPTH_SETTINGS = {
    "quick": {"per_stream_limit": 6, "pool_limit": 15, "rerank_limit": 12},
    "default": {"per_stream_limit": 12, "pool_limit": 40, "rerank_limit": 40},
    "deep": {"per_stream_limit": 20, "pool_limit": 60, "rerank_limit": 60},
}

SEARCH_ALIAS = {
    "hn": "hackernews",
    "bsky": "bluesky",
    "truth": "truthsocial",
    "web": "grounding",
    "xhs": "xiaohongshu",
    "xquik": "x",  # xquik is a backend of the single "x" source, not its own source
}

# trustpilot is capped at 1: every subquery would use the identical company
# identifier, so N streams are pure redundancy -- and each extra stream risks
# its own WAF-cookie Chrome harvest.
MAX_SOURCE_FETCHES: dict[str, int] = {"x": 2, "jobs": 1, "linkedin": 1, "stocktwits": 1, "trustpilot": 1}

# Per-handle result caps for the X handle-search lanes. The FROM lane (the
# subject's own timeline) is the single best source for a person topic, so it
# gets the highest cap; the ABOUT (mention) and related-handle lanes stay
# modest so total volume and request budget don't balloon.
FROM_LANE_COUNT_PER = 8
MENTION_LANE_COUNT_PER = 5
RELATED_HANDLE_COUNT_PER = 3


def _has_perplexity_provider(config: dict[str, Any]) -> bool:
    return bool(config.get("PERPLEXITY_API_KEY") or config.get("OPENROUTER_API_KEY"))

MOCK_AVAILABLE_SOURCES = [
    "reddit",
    "x",
    "youtube",
    "tiktok",
    "instagram",
    "hackernews",
    "bluesky",
    "truthsocial",
    "polymarket",
    "grounding",
    "xiaohongshu",
    "github",
    "perplexity",
    "threads",
    "pinterest",
    "digg",
    "arxiv",
    "techmeme",
    "trustpilot",
    "jobs",
    "linkedin",
    "corpus",
    "dripstack",
]


def normalize_requested_sources(sources: list[str] | None) -> list[str] | None:
    if not sources:
        return None
    normalized = []
    for source in sources:
        key = SEARCH_ALIAS.get(source.lower(), source.lower())
        if key not in normalized:
            normalized.append(key)
    return normalized


def available_sources(
    config: dict[str, Any],
    requested_sources: list[str] | None = None,
    *,
    x_pending: bool | None = None,
    local_only: bool = False,
) -> list[str]:
    """List the sources the next run can serve.

    ``local_only=True`` is the safe/diagnose flavor (doctor's permission
    block): availability is answered from local evidence only, so the X
    check never spawns xurl's live ``whoami`` network call. Research-time
    callers keep the default live semantics.
    """
    available: list[str] = []
    # reddit_public needs no API key - always available
    available.append("reddit")
    if corpus.resolve_directories(
        config.get("_CORPUS_DIRS"), config.get("LAST30DAYS_CORPUS_DIRS")
    ):
        available.append("corpus")
    if config.get("SCRAPECREATORS_API_KEY"):
        available.extend(["tiktok", "instagram"])
    if env.get_x_source(config, local_only=local_only):
        available.append("x")
    else:
        # Safe inspection (--diagnose/--preflight) skips browser-cookie
        # extraction, so get_x_source is None even though a real run would
        # authenticate X via FROM_BROWSER. Report it as available so consumers
        # of available_sources (SKILL.md ACTIVE_SOURCES_LIST) don't under-report.
        # diagnose() precomputes the predicate and passes it via x_pending to
        # avoid evaluating it twice in one diagnose() call.
        if x_pending is None:
            x_pending = env.x_pending_browser_auth(config)
        if x_pending:
            available.append("x")
    if which("yt-dlp") or env.is_youtube_sc_available(config):
        available.append("youtube")
    available.extend(["hackernews", "polymarket"])
    # StockTwits is gated to ticker/crypto topics only (flag set in run()).
    if config.get("_financial_topic"):
        available.append("stocktwits")
    # GitHub is reachable via the unauthenticated REST tier too, so it is
    # available even without a token/gh CLI (a token only raises rate limits).
    available.append("github")
    # DripStack is opt-in only (owner decision, #791): a commercial
    # third-party API must never receive default-run traffic. Opt in per run
    # (--search dripstack) or persistently (INCLUDE_SOURCES=dripstack in
    # .env, the LinkedIn/Perplexity pattern); the search API is free and
    # public (no key), so the opt-in itself is the gate.
    include_sources = {
        token.strip()
        for token in (config.get("INCLUDE_SOURCES") or "").lower().split(",")
        if token.strip()
    }
    if "dripstack" in include_sources or (
        requested_sources and "dripstack" in requested_sources
    ):
        available.append("dripstack")
    if which("digg-pp-cli"):
        available.append("digg")
    # arXiv is default-on when its Printing Press CLI is installed (zero auth).
    # The adapter relevance-and-recency gates so it stays quiet off-topic.
    if which("arxiv-pp-cli"):
        available.append("arxiv")
    # Techmeme is default-on when its CLI is installed (zero auth; sub-second
    # local sync before each run's first search).
    if which("techmeme-pp-cli"):
        available.append("techmeme")
    if env.is_bluesky_available(config):
        available.append("bluesky")
    if env.is_truthsocial_available(config):
        available.append("truthsocial")
    # Grounding (general web) is available when a paid backend is configured OR
    # the keyless floor is permitted (i.e. the host has no native search). On a
    # native-search host with no paid key, keyless_web_allowed is False and the
    # engine leaves general web to the model's own search.
    if (config.get("BRAVE_API_KEY") or config.get("EXA_API_KEY")
            or config.get("SERPER_API_KEY") or config.get("PARALLEL_API_KEY")
            or env.keyless_web_allowed(config)):
        available.append("grounding")
    if requested_sources and "jobs" in requested_sources:
        available.append("jobs")
    # Perplexity Sonar: opt-in additive source via INCLUDE_SOURCES=perplexity
    if _has_perplexity_provider(config) and (
        "perplexity" in include_sources or (requested_sources and "perplexity" in requested_sources)
    ):
        available.append("perplexity")
    # LinkedIn: opt-in additive source via INCLUDE_SOURCES=linkedin (same
    # consent pattern as Perplexity). Unlike tiktok/instagram, which are
    # offered during SKILL.md Step 0 onboarding, LinkedIn is power-user-only
    # and must not silently activate for existing SCRAPECREATORS_API_KEY
    # holders.
    if config.get("SCRAPECREATORS_API_KEY") and (
        "linkedin" in include_sources or (requested_sources and "linkedin" in requested_sources)
    ):
        available.append("linkedin")
    # Trustpilot: opt-in additive source via INCLUDE_SOURCES=trustpilot (same
    # consent pattern as Perplexity/LinkedIn). Off by default -- unlike arXiv and
    # Techmeme, which are zero-auth, it can spawn a one-time headless-Chrome WAF
    # cookie harvest on a brand topic, so activating it is the user's choice.
    if which("trustpilot-pp-cli") and (
        "trustpilot" in include_sources or (requested_sources and "trustpilot" in requested_sources)
    ):
        available.append("trustpilot")
    if (
        "xiaohongshu" in include_sources
        or (requested_sources and "xiaohongshu" in requested_sources)
    ) and env.is_xiaohongshu_available(config):
        available.append("xiaohongshu")
    # Threads: opt-in via INCLUDE_SOURCES (same pattern as perplexity/linkedin).
    # Was auto-on with the key; gated so the onboarding "Everything" tier is a
    # real choice vs the "Recommended" (TikTok/Instagram) tier.
    if env.is_threads_available(config) and (
        "threads" in include_sources or (requested_sources and "threads" in requested_sources)
    ):
        available.append("threads")
    # Pinterest: opt-in via INCLUDE_SOURCES. Previously read requested_sources
    # only, so a persisted INCLUDE_SOURCES=pinterest never activated it; now it
    # honors both the per-run --sources list and the saved config.
    if env.is_pinterest_available(config) and (
        "pinterest" in include_sources or (requested_sources and "pinterest" in requested_sources)
    ):
        available.append("pinterest")
    # xquik is a backend of the single "x" source (see env.x_backend_chain),
    # not a separate parallel source — registered via the "x" entry above.
    exclude = {s.strip().lower() for s in (config.get("EXCLUDE_SOURCES") or "").split(",") if s.strip()}
    if exclude:
        available = [s for s in available if s not in exclude]
    return available


def _mock_discovery_items(
    source: str,
    domain: str,
    to_date: str,
) -> list[dict[str, Any]]:
    """Deterministic listing fixtures for the public --mock CLI contract."""
    labels = [
        "Agent memory protocols",
        "Browser-using agents",
        "Local agent runtimes",
        "Multi-agent orchestration",
        "Agent security sandboxes",
        "Voice agent latency",
    ]
    end = datetime.fromisoformat(to_date).date()
    items: list[dict[str, Any]] = []
    for index, label in enumerate(labels, start=1):
        published = (end - timedelta(days=index)).isoformat()
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        if source == "reddit":
            items.append({
                "id": f"discovery-r-{index}",
                "title": label,
                "url": f"https://reddit.com/r/example/comments/{slug}",
                "subreddit": "example",
                "date": published,
                "engagement": {"score": 180 - index * 10, "num_comments": 30 + index},
                "selftext": label,
                "relevance": 0.9,
                "why_relevant": "Mock discovery listing",
            })
        elif source == "hackernews":
            items.append({
                "id": f"discovery-hn-{index}",
                "title": label,
                "url": f"https://example.com/{slug}",
                "hn_url": f"https://news.ycombinator.com/item?id={index}",
                "author": f"example{index}",
                "date": published,
                "engagement": {"points": 120 - index * 8, "comments": 20 + index},
                "relevance": 0.88,
                "why_relevant": "Mock HN discovery listing",
            })
        elif source == "digg":
            items.append({
                "id": f"discovery-d-{index}",
                "title": label,
                "url": f"https://di.gg/ai/{slug}",
                "tldr": label,
                "date": published,
                "engagement": {"postCount": 30 - index, "uniqueAuthors": 12 - index},
                "relevance": 0.9,
                "why_relevant": "Mock Digg discovery cluster",
            })
        elif source == "x":
            items.append({
                "id": f"discovery-x-{index}",
                "text": label,
                "url": f"https://x.com/example{index}/status/{index}",
                "author_handle": f"example{index}",
                "date": published,
                "engagement": {"likes": 140 - index * 9, "reposts": 18 + index},
                "relevance": 0.9,
                "why_relevant": "Mock X discovery activity",
            })
    return items


def _matches_discovery_domain(domain: str, text: str) -> bool:
    """Require a distinctive domain term, not a generic token such as ``AI``."""
    def terms(value: str) -> set[str]:
        # Keep BOTH the surface form and the naive stem: replacing the token
        # broke non-plurals ("bias" -> "bia", "crisis" -> "crisi") so in-domain
        # listings stopped intersecting. The union preserves plural matching
        # without corrupting the anchor.
        words: set[str] = set()
        for word in relevance.tokenize(value):
            words.add(word)
            if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
                words.add(word[:-1])
        return words

    domain_terms = terms(domain)
    anchors = domain_terms - _DISCOVERY_GENERIC_DOMAIN_TERMS
    return bool((anchors or domain_terms) & terms(text))


def _fetch_discovery_source(
    source: str,
    plan: schema.DiscoveryPlan,
    *,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
    config: dict[str, Any],
    keyword_gate: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one listing/river source for the nominate stage.

    ``keyword_gate`` controls whether items are filtered to the domain by
    ``_matches_discovery_domain``. Domain-scoped discovery (``--discover X``)
    keeps the gate on; global trending (``--discover`` with no domain) turns it
    off, because there is no keyword to gate against - the river feeds ARE the
    "what is hot right now" signal, and the confidence floor downstream is what
    keeps junk out, not a keyword match.
    """
    if mock:
        return _mock_discovery_items(source, plan.domain, to_date), None
    if source == "reddit":
        result = reddit_listing.fetch_discovery_listings(
            plan.subreddits, depth=depth, query=plan.domain,
        )
        items = result.get("items") or []
        if keyword_gate:
            items = [
                item for item in items
                if _matches_discovery_domain(
                    plan.domain,
                    f"{item.get('title') or ''} {item.get('selftext') or ''}",
                )
            ]
        return items, "; ".join(result.get("errors") or []) or None
    if source == "hackernews":
        result = hackernews.fetch_discovery_listings(from_date, to_date, depth=depth)
        items = result.get("items") or []
        for item in items:
            item["relevance"] = relevance.token_overlap_relevance(
                plan.domain,
                str(item.get("title") or ""),
            )
        # HN is a broad technology listing, so keep only domain-bearing stories
        # when a domain is in play; global trending keeps the whole front page.
        if keyword_gate:
            items = [
                item for item in items
                if _matches_discovery_domain(plan.domain, str(item.get("title") or ""))
            ]
        errors = result.get("errors") or []
        return items, "; ".join(errors) or None
    if source == "digg":
        result = digg.search_digg(plan.domain, from_date, to_date, depth=depth)
        items = digg.parse_digg_response(result, query=plan.domain)
        # Digg is an AI-focused broad listing, so keep only domain-bearing
        # clusters when scoped; global trending keeps the whole feed.
        if keyword_gate:
            items = [
                item for item in items
                if _matches_discovery_domain(plan.domain, str(item.get("title") or ""))
            ]
        return items, result.get("error")
    if source == "x":
        subquery = schema.SubQuery(
            label="discovery-listings",
            search_query=plan.domain,
            ranking_query=f"What is accelerating in {plan.domain}?",
            sources=["x"],
        )
        last_error = ""
        for backend in env.x_backend_chain(config):
            items, error = _fetch_x_backend(
                backend, subquery, from_date, to_date, depth, config,
            )
            if items:
                # Earlier failed-over backends' errors are observability, not
                # degradation - but the producing backend's own error means
                # these items are partial and must surface as such.
                if last_error:
                    print(f"[x] earlier backend failed: {last_error}", file=sys.stderr)
                return items, error or None
            if error:
                last_error = f"{backend}: {error}"
        return [], last_error or None
    raise ValueError(f"Unsupported discovery source: {source}")


def discovery_topic_name(
    cluster: schema.Cluster,
    candidates: dict[str, schema.Candidate],
    domain: str,
) -> str:
    """Turn a story cluster into a concise, reusable research topic."""
    members = [candidates[cid] for cid in cluster.candidate_ids if cid in candidates]
    leader = candidates.get(cluster.representative_ids[0]) if cluster.representative_ids else None
    leader = leader or (members[0] if members else None)
    if leader is None:
        return domain
    title = re.sub(r"^(?:show|ask|tell|launch) hn:\s*", "", leader.title, flags=re.I)
    title = re.sub(r"^digg cluster (?:about|on)\s+", "", title, flags=re.I)
    title = re.sub(r"\s*(?::|-)?\s*(?:discussion thread|gains momentum)$", "", title, flags=re.I)

    if len(members) > 1:
        entity_sets = [
            entity_extract.extract_text_entities(f"{member.title} {member.snippet}")
            for member in members
        ]
        shared = set.intersection(*entity_sets) if entity_sets else set()
        shared_words = [
            word.strip(".,:;!?()[]{}\"'")
            for word in title.split()
            if word.strip(".,:;!?()[]{}\"'").lower() in shared
        ]
        if 2 <= len(shared_words) <= 7:
            title = " ".join(shared_words)

    title = " ".join(title.split()).strip(" -:;,.\"'")
    if len(title) > 96:
        title = title[:93].rsplit(" ", 1)[0] + "..."
    return title or domain


def _discovery_engagement(
    items: list[schema.SourceItem],
) -> dict[str, dict[str, float | int]]:
    totals: dict[str, dict[str, float | int]] = {}
    for item in items:
        bucket = totals.setdefault(item.source, {})
        for field, value in item.engagement.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            # Rank/score/reach metadata is not additive engagement: summing
            # Digg ranks across items fabricates a metric (agent-export uses
            # the same counter-field rule).
            if not schema._is_counter_field(field):
                continue
            bucket[field] = bucket.get(field, 0) + value
    return {
        source: dict(sorted(metrics.items()))
        for source, metrics in sorted(totals.items())
    }


def _discovery_momentum(items: list[schema.SourceItem], to_date: str) -> str:
    as_of = datetime.fromisoformat(to_date).date()
    ages: list[int] = []
    for item in items:
        try:
            published = datetime.fromisoformat((item.published_at or "").replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        ages.append(max(0, (as_of - published).days))
    return "new-this-week" if ages and max(ages) < 7 else "building"


def nominate_candidates(
    plan: schema.DiscoveryPlan,
    *,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
    config: dict[str, Any],
    lookback_days: int,
    keyword_gate: bool = True,
) -> schema.RetrievalBundle:
    """Stage 1 of discovery: fetch, normalize, and bundle candidate hot items
    from the river/listing feeds.

    This is the topic-nomination pass. For domain discovery ``keyword_gate`` is
    on and the feeds are filtered to the domain; for global trending it is off
    and the feeds' own hot ranking IS the signal. The returned bundle feeds the
    clustering + enrichment stages downstream. Every source's failure is
    recorded on the bundle (never raised) so a single dead feed cannot sink the
    run - the confidence floor decides whether the surviving evidence is enough.
    """
    bundle = schema.RetrievalBundle()
    with ThreadPoolExecutor(max_workers=max(1, len(plan.sources))) as executor:
        futures = {
            executor.submit(
                _fetch_discovery_source,
                source,
                plan,
                from_date=from_date,
                to_date=to_date,
                depth=depth,
                mock=mock,
                config=config,
                keyword_gate=keyword_gate,
            ): source
            for source in plan.sources
        }
        for future in as_completed(futures):
            source = futures[future]
            bundle.mark_attempted(source)
            try:
                raw_items, partial_error = future.result()
                normalized = normalize.normalize_source_items(
                    source,
                    raw_items,
                    from_date,
                    to_date,
                    freshness_mode="breaking",
                )
                # Global trending has no domain; annotate against a neutral
                # phrase so snippet extraction still works without biasing
                # relevance toward any keyword.
                prepared = relevance.PreparedQuery(plan.domain or "trending now")
                normalized = signals.annotate_stream(
                    normalized,
                    prepared,
                    "breaking",
                    reference_date=to_date,
                    max_days=lookback_days,
                )
                normalized = dedupe.dedupe_items(normalized)
                for item in normalized:
                    item.snippet = snippet.extract_best_snippet(item, prepared)
                bundle.add_items("discovery-listings", source, normalized)
                if partial_error:
                    failure_state = (
                        bird_x.classify_run_failure(partial_error)
                        if source == "x" and partial_error.startswith("bird:")
                        else http.classify_failure(message=partial_error)
                    )
                    bundle.record_failure(
                        source,
                        failure_state,
                        partial_error,
                    )
            except Exception as exc:
                state, attempted = _classify_source_failure(exc)
                bundle.record_failure(source, state, str(exc), attempted=attempted)
    return bundle


@dataclass(frozen=True)
class Nomination:
    """A named candidate topic produced by the nominate stage.

    ``seed_score`` is the cheap pre-enrichment velocity rank - enough to decide
    WHICH candidates deserve a full pipeline pass, but not the final ranking
    signal (that comes from enriched evidence downstream).
    """

    name: str
    seed_score: float
    items: list[schema.SourceItem] = field(default_factory=list)
    summary: str = ""


def nominate_topics(
    bundle: schema.RetrievalBundle,
    query_plan: schema.QueryPlan,
    plan: schema.DiscoveryPlan,
    *,
    to_date: str,
    limit: int,
) -> list[Nomination]:
    """Stage 1b of discovery: cluster nominated items into named candidate
    topics and rank them by seed velocity.

    Names are deduped casefold so the same story surfacing under two clusters
    yields one nomination. Returns at most ``limit`` nominations, never padded -
    fewer clusters than ``limit`` means a shorter list, and the confidence
    floor downstream decides whether what survived is worth showing.
    """
    candidates = weighted_rrf(bundle.items_by_source_and_query, query_plan, pool_limit=80)
    for candidate in candidates:
        velocity = rerank.discovery_velocity_score(candidate.source_items, as_of_date=to_date)
        candidate.final_score = min(100.0, 12.0 * math.log1p(velocity)) if velocity else 0.0
    candidates.sort(key=lambda candidate: (-candidate.final_score, candidate.title.lower()))
    clusters = cluster_candidates(candidates, query_plan)
    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}

    ranked_clusters: list[tuple[float, schema.Cluster, list[schema.SourceItem]]] = []
    for cluster in clusters:
        cluster_items: list[schema.SourceItem] = []
        for candidate_id in cluster.candidate_ids:
            candidate = candidate_map.get(candidate_id)
            if candidate:
                cluster_items.extend(candidate.source_items)
        score = rerank.discovery_velocity_score(cluster_items, as_of_date=to_date)
        if score <= 0:
            continue
        ranked_clusters.append((score, cluster, cluster_items))
    ranked_clusters.sort(key=lambda entry: (-entry[0], entry[1].title.lower()))

    nominations: list[Nomination] = []
    seen_topic_names: set[str] = set()
    for score, cluster, cluster_items in ranked_clusters:
        name = discovery_topic_name(cluster, candidate_map, plan.domain)
        name_key = name.casefold()
        if name_key in seen_topic_names:
            continue
        seen_topic_names.add(name_key)
        leader = candidate_map.get(cluster.representative_ids[0]) if cluster.representative_ids else None
        summary = (leader.snippet if leader else "") or (leader.title if leader else name)
        nominations.append(Nomination(
            name=name,
            seed_score=score,
            items=cluster_items,
            summary=summary,
        ))
        if len(nominations) >= limit:
            break
    return nominations


# Enrichment fan-out bounds. Sub-runs hit the same upstream APIs as a normal
# research pass, so parallelism stays low and the whole batch runs against a
# wall-clock budget - a slow topic is dropped, never fatal.
ENRICH_LIMIT = 6
ENRICH_DEPTH = "quick"
ENRICH_MAX_WORKERS = 3
ENRICH_BUDGET_SECONDS = 240.0


@dataclass
class EnrichedTopic:
    """A nomination plus the full-pipeline evidence gathered for it.

    ``report`` is None when enrichment for this topic failed or ran past the
    batch budget - the topic survives as nomination-only and the confidence
    floor downstream decides whether its seed evidence is enough to show.
    """

    nomination: Nomination
    report: schema.Report | None = None
    error: str | None = None


def enrich_nominations(
    nominations: list[Nomination],
    *,
    config: dict[str, Any],
    requested_sources: list[str] | None = None,
    mock: bool = False,
    depth: str = ENRICH_DEPTH,
    lookback_days: int = 30,
    as_of_date: str | None = None,
    max_workers: int = ENRICH_MAX_WORKERS,
    budget_seconds: float = ENRICH_BUDGET_SECONDS,
) -> list[EnrichedTopic]:
    """Stage 2 of discovery: run the real research pipeline on each nomination.

    Each nominated topic gets a full ``run()`` pass (``internal_subrun=True``,
    same lane as comparison-mode sub-runs), which buys the whole multi-source
    corpus - Reddit with comments, X, YouTube, Techmeme, arXiv, HN, Polymarket,
    web - plus clustering and ranking, with zero bespoke fetch code.

    Failure containment: a topic whose sub-run raises is returned with
    ``report=None`` and the error recorded; topics still unfinished when the
    batch budget expires are likewise dropped to nomination-only. The batch
    never raises and preserves nomination order.
    """
    if not nominations:
        return []

    def _run_one(nomination: Nomination) -> schema.Report:
        return run(
            topic=nomination.name,
            config=config,
            depth=depth,
            requested_sources=requested_sources,
            mock=mock,
            lookback_days=lookback_days,
            as_of_date=as_of_date,
            internal_subrun=True,
        )

    # Daemon threads + a semaphore instead of ThreadPoolExecutor: executor
    # threads are non-daemon and joined at interpreter shutdown, so one hung
    # sub-run could keep the whole process alive long after its topic was
    # downgraded to nomination-only. Daemon workers make the wall-clock budget
    # real - stragglers cannot delay process exit. Abandonment is safe because
    # internal_subrun passes write nothing to disk (no save, no library sync,
    # no store), and every fetch layer inside run() carries its own timeout.
    enriched: dict[str, EnrichedTopic] = {}
    results_queue: queue.Queue[tuple[Nomination, schema.Report | None, Exception | None]] = queue.Queue()
    slots = threading.Semaphore(max(1, max_workers))

    def _worker(nomination: Nomination) -> None:
        with slots:
            try:
                results_queue.put((nomination, _run_one(nomination), None))
            except Exception as exc:  # noqa: BLE001 - containment is the contract
                results_queue.put((nomination, None, exc))

    for nomination in nominations:
        threading.Thread(
            target=_worker,
            args=(nomination,),
            name=f"discover-enrich-{nomination.name[:32]}",
            daemon=True,
        ).start()

    deadline = time.monotonic() + max(1.0, budget_seconds)
    pending = len(nominations)
    while pending and (remaining := deadline - time.monotonic()) > 0:
        try:
            nomination, report, exc = results_queue.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            continue
        pending -= 1
        if exc is None:
            enriched[nomination.name] = EnrichedTopic(
                nomination=nomination, report=report,
            )
        else:
            enriched[nomination.name] = EnrichedTopic(
                nomination=nomination,
                error=f"{type(exc).__name__}: {exc}",
            )
            print(
                f"[Discover] enrichment failed for {nomination.name!r}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    # Budget expired (or all done): unfinished topics fall through below as
    # nomination-only; their daemon workers are abandoned and cannot block exit.

    results: list[EnrichedTopic] = []
    for nomination in nominations:
        entry = enriched.get(nomination.name)
        if entry is None:
            entry = EnrichedTopic(
                nomination=nomination,
                error="enrichment budget exhausted",
            )
            print(
                f"[Discover] enrichment budget exhausted before {nomination.name!r} "
                "finished; keeping nomination-only evidence",
                file=sys.stderr,
            )
        results.append(entry)
    return results


def _enriched_evidence_items(entry: EnrichedTopic) -> list[schema.SourceItem]:
    """The items a topic is judged on: the enriched corpus when the pipeline
    pass succeeded, the nomination's seed items otherwise."""
    if entry.report is not None:
        flattened: list[schema.SourceItem] = []
        for source_items in entry.report.items_by_source.values():
            flattened.extend(source_items)
        if flattened:
            return flattened
    return entry.nomination.items


def _best_community_comment(items: list[schema.SourceItem]) -> str | None:
    """The strongest verbatim community comment across a topic's evidence,
    formatted with attribution - the voice-of-the-people line on a trend card.

    Vote strength is per-platform-normalized (signals.normalized_comment_vote)
    so one viral platform's counts don't drown out the rest.
    """
    best: tuple[float, str, str | None, float | int | None] | None = None
    for item in items:
        comments = item.metadata.get("top_comments") or []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            body = (comment.get("excerpt") or comment.get("text") or comment.get("body") or "").strip()
            if len(body) < 12:
                continue
            strength = signals.normalized_comment_vote(item.source, comment.get("score"))
            if best is None or strength > best[0]:
                best = (strength, body, comment.get("author"), comment.get("score"))
    if best is None:
        return None
    _, body, author, score = best
    # Comment bodies that themselves start/end with quote characters would
    # render as doubled quotes inside our wrapping quotes.
    body = body.strip('"“”‘’\'').strip()
    if len(body) > 200:
        body = body[:197].rsplit(" ", 1)[0] + "..."
    attribution = f" - {author}" if author else ""
    votes = (
        f" ({int(score):,} votes)"
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score > 0
        else ""
    )
    return f'"{body}"{attribution}{votes}'


def run_discover(
    *,
    domain: str,
    config: dict[str, Any],
    depth: str = "default",
    requested_sources: list[str] | None = None,
    mock: bool = False,
    subreddits: list[str] | None = None,
    lookback_days: int = 30,
    as_of_date: str | None = None,
    limit: int = 10,
    enrich: bool = False,
    enrich_requested_sources: list[str] | None = None,
) -> schema.DiscoveryReport:
    """Sweep category listings and rank the topics gaining velocity.

    ``requested_sources`` bounds the listing sweep (discovery-capable feeds
    only). ``enrich_requested_sources`` bounds the per-topic research passes:
    None means every available source - which is what lets Techmeme, arXiv,
    YouTube, Polymarket, and community comments reach discovery despite having
    no river feed of their own. Pass the user's original --search list here so
    an explicit source boundary holds through enrichment too.
    """
    from_date, to_date = dates.get_date_range(lookback_days, as_of_date=as_of_date)
    requested = normalize_requested_sources(requested_sources)
    unsupported = sorted(set(requested or []) - set(DISCOVERY_SOURCES))
    if unsupported:
        raise ValueError(
            "Discovery supports listing sources only: reddit, hackernews, digg "
            f"(unsupported: {', '.join(unsupported)})"
        )
    available = list(DISCOVERY_SOURCES) if mock else [
        source for source in available_sources(config, requested, x_pending=False)
        if source in DISCOVERY_SOURCES
    ]
    if requested:
        available = [source for source in available if source in requested]
    plan = planner.build_discovery_plan(
        domain,
        available_sources=available,
        subreddits=subreddits,
    )

    global_mode = not plan.domain
    domain_label = plan.domain or "everything"
    source_status: dict[str, schema.SourceOutcome] = {}
    query_plan = schema.QueryPlan(
        intent="breaking_news",
        freshness_mode="breaking",
        cluster_mode="story",
        raw_topic=plan.domain,
        subqueries=[schema.SubQuery(
            label="discovery-listings",
            search_query=plan.domain,
            ranking_query=f"What is accelerating in {domain_label}?",
            sources=list(plan.sources),
        )],
        source_weights={source: 1.0 for source in plan.sources},
        notes=["discover-mode", "listing-sweep"],
    )

    bundle = nominate_candidates(
        plan,
        from_date=from_date,
        to_date=to_date,
        depth=depth,
        mock=mock,
        config=config,
        lookback_days=lookback_days,
        # Global trending has no keyword to gate against - the river feeds' own
        # hot ranking is the signal and the confidence floor culls the junk.
        keyword_gate=not global_mode,
    )

    for source in DISCOVERY_SOURCES:
        if source in bundle.source_status:
            continue
        detail = (
            "Source is not configured for discovery."
        )
        source_status[source] = schema.SourceOutcome(
            source=source,
            state=schema.SKIPPED_UNCONFIGURED,
            attempted=False,
            detail=detail,
            fix_hint="doctor",
        )
    source_status.update(_finalize_source_status(bundle.source_status, bundle.items_by_source))

    topic_limit = max(5, min(10, limit))
    nominations = nominate_topics(
        bundle, query_plan, plan,
        to_date=to_date,
        limit=ENRICH_LIMIT if enrich else topic_limit,
    )

    if enrich and nominations:
        enriched_entries = enrich_nominations(
            nominations,
            config=config,
            requested_sources=enrich_requested_sources,
            mock=mock,
            lookback_days=lookback_days,
            as_of_date=as_of_date,
        )
    else:
        enriched_entries = [
            EnrichedTopic(nomination=nomination) for nomination in nominations
        ]

    topics: list[schema.DiscoveryTopic] = []
    weak_signal: tuple[float, str] | None = None
    for entry in enriched_entries:
        evidence_items = _enriched_evidence_items(entry)
        sources = sorted({item.source for item in evidence_items})
        native_total = sum(
            rerank.discovery_engagement_total(item) for item in evidence_items
        )
        score = rerank.discovery_velocity_score(evidence_items, as_of_date=to_date)
        if not rerank.passes_discovery_floor(
            source_count=len(sources),
            engagement_total=native_total,
            item_count=len(evidence_items),
        ):
            # Sub-floor evidence never ranks; remember what came closest so a
            # nothing-solid brief can still name the strongest weak signal.
            if weak_signal is None or score > weak_signal[0]:
                weak_signal = (score, entry.nomination.name)
            continue
        if len(topics) >= topic_limit:
            break
        nomination = entry.nomination
        source_phrase = ", ".join(sources[:-1]) + (
            f" and {sources[-1]}" if len(sources) > 1 else (sources[0] if sources else "the listings")
        )
        noun = "evidence item" if entry.report is not None else "listing item"
        why = (
            f"{len(evidence_items)} {noun}{'s' if len(evidence_items) != 1 else ''} on "
            f"{source_phrase} generated {native_total:,.0f} native interactions. "
            f"{nomination.summary[:220]}"
        )
        topics.append(schema.DiscoveryTopic(
            rank=len(topics) + 1,
            name=nomination.name,
            why_spiking=why,
            momentum=_discovery_momentum(evidence_items, to_date),
            velocity_score=round(score, 2),
            sources=sources,
            engagement_by_source=_discovery_engagement(evidence_items),
            command=f'/last30days "{nomination.name.replace(chr(34), chr(39))}"',
            evidence_urls=list(dict.fromkeys(item.url for item in evidence_items if item.url))[:5],
            top_comment=_best_community_comment(evidence_items) if entry.report is not None else None,
            corroboration_count=len(sources),
        ))

    outcome = "ok" if topics else "nothing-solid"

    warnings: list[str] = []
    if outcome == "nothing-solid":
        warnings.append(
            "No topic cleared the discovery confidence floor this window; "
            "reporting nothing solid instead of ranked noise."
        )
    elif len(topics) < 5:
        warnings.append("Fewer than five topic clusters cleared the confidence floor this window.")
    if topics and all(len(topic.sources) == 1 for topic in topics):
        warnings.append("Discovery evidence is single-source; configure Digg for broader confirmation.")
    failed = [
        source for source, outcome_state in source_status.items()
        if outcome_state.state not in {health.OK, schema.NO_RESULTS, schema.SKIPPED_UNCONFIGURED}
    ]
    if failed:
        warnings.append(f"Some discovery sources degraded: {', '.join(sorted(failed))}.")

    return schema.DiscoveryReport(
        domain=plan.domain,
        range_from=from_date,
        range_to=to_date,
        generated_at=datetime.now(timezone.utc).isoformat(),
        plan=plan,
        topics=topics,
        source_status=source_status,
        warnings=warnings,
        outcome=outcome,
        weak_signal=weak_signal[1] if weak_signal and not topics else None,
    )


def diagnose(
    config: dict[str, Any],
    requested_sources: list[str] | None = None,
    *,
    safe: bool = False,
) -> dict[str, Any]:
    requested_sources = normalize_requested_sources(requested_sources)
    google_key = _google_key(config)
    x_status = env.get_x_source_status(config, probe=not safe)
    # Compute once and reuse for both the diag flag and available_sources below.
    # safe=True (doctor/--diagnose/--preflight) must stay network-free.
    x_pending = env.x_pending_browser_auth(config, local_only=safe)
    native_web_backend = None
    if config.get("BRAVE_API_KEY"):
        native_web_backend = "brave"
    elif config.get("EXA_API_KEY"):
        native_web_backend = "exa"
    elif config.get("SERPER_API_KEY"):
        native_web_backend = "serper"
    elif config.get("PARALLEL_API_KEY"):
        native_web_backend = "parallel"
    providers_status = {
        "google": bool(google_key),
        "openai": bool(config.get("OPENAI_API_KEY")) and config.get("OPENAI_AUTH_STATUS") == env.AUTH_STATUS_OK,
        "xai": bool(config.get("XAI_API_KEY")),
        "openrouter": bool(config.get("OPENROUTER_API_KEY")),
        "perplexity": bool(config.get("PERPLEXITY_API_KEY")),
    }
    reasoning_provider_available = any(
        providers_status[name] for name in ("google", "openai", "xai", "openrouter")
    )
    external_commands = {
        "yt-dlp": bool(which("yt-dlp")),
        "digg-pp-cli": bool(which("digg-pp-cli")),
        "arxiv-pp-cli": bool(which("arxiv-pp-cli")),
        "techmeme-pp-cli": bool(which("techmeme-pp-cli")),
        "trustpilot-pp-cli": bool(which("trustpilot-pp-cli")),
        "gh": bool(which("gh")),
    }
    credential_destinations = {
        "global_env": str(env.CONFIG_FILE) if env.CONFIG_FILE else None,
    }
    browser_cookies = {
        "mode": config.get("_BROWSER_COOKIE_MODE", "off"),
        "browsers": list(config.get("_BROWSER_COOKIE_BROWSERS") or []),
        "reads_values": False if safe else config.get("_BROWSER_COOKIE_MODE") == "read",
    }
    ignored_project_keys = list(config.get("_IGNORED_PROJECT_CONFIG_KEYS") or [])
    ignored_endpoint_overrides = [
        key for key in ignored_project_keys if key in permission_preflight.ENDPOINT_OVERRIDE_KEYS
    ]
    local_writes: list[dict[str, str]] = []
    if config.get("LAST30DAYS_MEMORY_DIR"):
        local_writes.append({"kind": "report", "path": str(config.get("LAST30DAYS_MEMORY_DIR"))})
    diag = {
        "providers": providers_status,
        "local_mode": not reasoning_provider_available,
        "reasoning_provider": (config.get("LAST30DAYS_REASONING_PROVIDER") or "auto").lower(),
        "x_backend": x_status["source"],
        "bird_installed": x_status["bird_installed"],
        "bird_authenticated": x_status["bird_authenticated"],
        "bird_username": x_status["bird_username"],
        "x_pending_browser_auth": x_pending,
        "xquik_available": x_status.get("xquik_available", False),
        "xquik_working": x_status.get("xquik_working"),
        "xquik_status": x_status.get("xquik_status", ""),
        "native_web_backend": native_web_backend,
        "native_search": env.is_native_search(config),
        "has_scrapecreators": bool(config.get("SCRAPECREATORS_API_KEY")),
        "has_github": bool(config.get("GITHUB_TOKEN") or which("gh")),
        # safe=True (doctor/--diagnose/--preflight) must stay network-free:
        # answer X availability from local evidence only. x_pending is
        # precomputed by diagnose() to avoid double evaluation.
        "available_sources": available_sources(
            config, requested_sources, x_pending=x_pending, local_only=safe
        ),
        "safe": safe,
        "config_source": config.get("_CONFIG_SOURCE"),
        "ignored_project_config": config.get("_IGNORED_PROJECT_CONFIG"),
        "ignored_project_config_keys": ignored_project_keys,
        "ignored_endpoint_overrides": ignored_endpoint_overrides,
        "browser_cookies": browser_cookies,
        "external_commands": external_commands,
        "credential_destinations": credential_destinations,
        "local_writes": local_writes,
    }
    diag["permission_preflight"] = permission_preflight.build(config, diag)
    return diag


def _inner_max_workers(stream_count: int, *, internal_subrun: bool) -> int:
    """Worker-pool size for the per-stream fanout inside a single pipeline run.

    Top-level runs use up to 16 workers. Subruns of ``run_competitor_fanout``
    cap the inner pool to 4 so a six-way competitor fan-out stays below
    roughly 30 worker threads in aggregate instead of ~96.
    """
    if internal_subrun:
        return max(2, min(4, stream_count or 1))
    return max(4, min(16, stream_count or 1))


def _load_library_context(
    *,
    topic: str,
    config: dict[str, Any],
    mock: bool,
    internal_subrun: bool,
    x_handle: str | None,
    github_user: str | None,
    github_repos: list[str] | None,
    save_dir: Path | str | None = None,
) -> tuple[list[schema.LibraryContext], str | None]:
    """Resolve compact prior-run context without making a research run depend on it."""
    setting = str(config.get("LAST30DAYS_LIBRARY_CONTEXT") or "off").strip().lower()
    if mock or internal_subrun or setting in {"0", "false", "no", "off"}:
        return [], None
    if save_dir == "":
        return [], None

    memory_dir = (
        save_dir
        if save_dir is not None
        else config.get("LAST30DAYS_MEMORY_DIR") or library.DEFAULT_MEMORY_DIR
    )
    briefs_dir = config.get("_LAST30DAYS_LIBRARY_BRIEFS_DIR") or (
        Path(memory_dir).expanduser() / "briefings"
        if save_dir is not None
        else library.DEFAULT_BRIEFS_DIR
    )
    db_path = config.get("_LAST30DAYS_LIBRARY_DB")
    if not db_path:
        db_path = (
            Path(memory_dir).expanduser().resolve() / ".last30days-library.db"
            if save_dir is not None
            else library_index.DEFAULT_LIBRARY_DB
        )
    store_db = config.get("_LAST30DAYS_STORE_DB")
    if not store_db:
        # Scoped runs read only a store inside the save dir (usually absent);
        # the shared store would leak other scopes' sightings into this one.
        store_db = (
            Path(memory_dir).expanduser().resolve() / "research.db"
            if save_dir is not None
            else library_index.DEFAULT_STORE_DB
        )
    queries = [topic, x_handle or "", github_user or "", *(github_repos or [])]
    queries = list(dict.fromkeys(value.strip() for value in queries if value and value.strip()))
    try:
        library_index.sync_library(memory_dir, briefs_dir, db_path=db_path)
        matches: list[library_index.LibrarySearchMatch] = []
        for query_text in queries:
            matches.extend(
                library_index.search(
                    query_text,
                    limit=6,
                    db_path=db_path,
                    store_db_path=store_db,
                )
            )
    except (library_index.LibrarySearchUnavailable, OSError, sqlite3.DatabaseError) as exc:
        return [], f"Library context unavailable: {exc}"

    contexts: list[schema.LibraryContext] = []
    seen_runs: set[tuple[str, date]] = set()
    for match in sorted(
        matches,
        key=lambda item: (-item.published_date.toordinal(), item.rank, item.topic.casefold()),
    ):
        if match.run_key in seen_runs:
            continue
        seen_runs.add(match.run_key)
        contexts.append(
            schema.LibraryContext(
                topic=match.topic,
                published_date=match.published_date.isoformat(),
                headline=match.headline,
                summary=match.snippet or match.headline,
                source_kind=match.source_kind,
            )
        )
        if len(contexts) == 3:
            break
    return contexts, None


def run(
    *,
    topic: str,
    config: dict[str, Any],
    depth: str,
    requested_sources: list[str] | None = None,
    mock: bool = False,
    x_handle: str | None = None,
    x_related: list[str] | None = None,
    web_backend: str = "auto",
    external_plan: dict | None = None,
    subreddits: list[str] | None = None,
    tiktok_hashtags: list[str] | None = None,
    tiktok_creators: list[str] | None = None,
    ig_creators: list[str] | None = None,
    lookback_days: int = 30,
    as_of_date: str | None = None,
    github_user: str | None = None,
    github_repos: list[str] | None = None,
    trustpilot_domain: str | None = None,
    trustpilot_domain_is_hint: bool = False,
    hiring_signals_mode: bool = False,
    internal_subrun: bool = False,
    save_dir: Path | str | None = None,
    corpus_dirs: list[str] | None = None,
    corpus_all_time: bool = False,
) -> schema.Report:
    settings = DEPTH_SETTINGS[depth]
    requested_sources = normalize_requested_sources(requested_sources)
    from_date, to_date = dates.get_date_range(lookback_days, as_of_date=as_of_date)
    resolved_corpus_dirs = corpus.resolve_directories(
        corpus_dirs or config.get("_CORPUS_DIRS"),
        config.get("LAST30DAYS_CORPUS_DIRS"),
    )
    excluded_sources = {
        source.strip().lower()
        for source in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        if source.strip()
    }
    corpus_enabled = bool(resolved_corpus_dirs) and "corpus" not in excluded_sources
    corpus_requested = bool(requested_sources and "corpus" in requested_sources)
    if corpus_enabled and requested_sources and "corpus" not in requested_sources:
        requested_sources = [*requested_sources, "corpus"]

    # Gate StockTwits to ticker/crypto topics. Single chokepoint: when False,
    # available_sources() never registers stocktwits, so the planner can't
    # assign it (eligible_sources = available ∩ capabilities).
    config["_financial_topic"] = stocktwits.is_financial_topic(topic)

    if mock:
        runtime = providers.mock_runtime(config, depth)
        reasoning_provider = None
        available = list(requested_sources or MOCK_AVAILABLE_SOURCES)
        if corpus_enabled and "corpus" not in available:
            available.append("corpus")
        if not corpus_enabled and not corpus_requested:
            available = [source for source in available if source != "corpus"]
        if not requested_sources and not hiring_signals_mode and not _company_topic_likely(topic):
            available = [source for source in available if source != "jobs"]
    else:
        runtime, reasoning_provider = providers.resolve_runtime(config, depth)
        available = available_sources(config, requested_sources)
        if requested_sources:
            available = [source for source in available if source in requested_sources]
    # Keep an explicitly requested but unconfigured corpus in the plan long
    # enough to record its skipped-unconfigured source outcome. It is never
    # submitted to the network executor below.
    if corpus_requested and "corpus" not in excluded_sources and "corpus" not in available:
        available.append("corpus")
    if web_backend == "none":
        available = [s for s in available if s != "grounding"]
    elif web_backend in ("brave", "exa", "serper", "parallel", "keyless") and "grounding" not in available:
        available.append("grounding")
    if (hiring_signals_mode or _company_topic_likely(topic)) and "jobs" not in available:
        available.append("jobs")
    if hiring_signals_mode:
        config = dict(config)
        config["_hiring_signals_mode"] = True
        if not requested_sources:
            available = ["jobs"]
    if not available:
        raise RuntimeError("No sources are available for this run.")

    planner_requested_sources = requested_sources
    if hiring_signals_mode and not planner_requested_sources:
        planner_requested_sources = ["jobs"]

    if external_plan:
        # External plan provided (e.g., from Claude Code via --plan flag).
        # Parse it through the same sanitizer to validate structure.
        plan = planner._sanitize_plan(
            external_plan, topic, available, planner_requested_sources, depth,
        )
        plan_source = "external"
    else:
        plan = planner.plan_query(
            topic=topic,
            available_sources=available,
            requested_sources=planner_requested_sources,
            depth=depth,
            provider=None if mock else reasoning_provider,
            model=None if mock else runtime.planner_model,
            context=config.get("_auto_resolve_context", ""),
            internal_subrun=internal_subrun,
        )
        # Source labelling: the fallback path annotates notes with "fallback-plan"
        # or "deterministic-comparison-plan"; anything else came from the LLM.
        if any("fallback" in note or "deterministic" in note for note in (plan.notes or [])):
            plan_source = "deterministic"
        elif not mock and reasoning_provider and runtime.planner_model:
            plan_source = "llm"
        else:
            plan_source = "deterministic"

    # Safety net: ensure grounding appears in all subqueries even if the planner
    # omits it. This is redundant when the planner includes grounding via
    # SOURCE_CAPABILITIES, but kept as a fallback.
    if (
        web_backend != "none"
        and "grounding" in available
        and "drill-mode" not in plan.notes
    ):
        for sq in plan.subqueries:
            if "grounding" not in sq.sources:
                sq.sources.append("grounding")
    if "drill-mode" not in plan.notes:
        # Drill plans re-fetch only the sources that contributed to the matched
        # cluster; the company-topic jobs injection must not widen that set.
        _ensure_jobs_in_plan(plan, available, explicit=hiring_signals_mode, topic=topic)
    if "corpus" in available and plan.subqueries:
        # Corpus is deterministic and user-registered, so it always gets one
        # bounded stream even when a quick/LLM plan omits it. Reuse the primary
        # subquery instead of multiplying local scans across every subquery.
        if "corpus" not in plan.subqueries[0].sources:
            plan.subqueries[0].sources.append("corpus")
        if "corpus" not in plan.source_weights:
            plan.source_weights["corpus"] = 1.0
            plan.source_weights = planner._normalize_weights(plan.source_weights)

    # Always-on planner trace. Emits one summary line plus one per subquery
    # so retrieval-breadth failures like the 2026-04-19 Hermes Agent Use Cases
    # disaster are visible without --debug. Stderr only; does not leak into
    # the user-facing stdout synthesis.
    print(
        f"[Planner] Plan: intent={plan.intent}, freshness={plan.freshness_mode}, "
        f"cluster_mode={plan.cluster_mode}, subqueries={len(plan.subqueries)}, "
        f"source={plan_source}",
        file=sys.stderr,
    )
    if plan.subqueries:
        for index, sq in enumerate(plan.subqueries, start=1):
            sources_str = ",".join(sq.sources) if sq.sources else "(none)"
            print(
                f"[Planner]   sq{index} label={sq.label} "
                f'search="{sq.search_query}" sources=[{sources_str}]',
                file=sys.stderr,
            )
    else:
        print("[Planner]   (no subqueries in plan)", file=sys.stderr)

    bundle = schema.RetrievalBundle(artifacts={"grounding": []})
    for source in (requested_sources or []):
        if source not in available:
            bundle.record_failure(
                source,
                schema.SKIPPED_UNCONFIGURED,
                "Source was requested but is not configured for this run.",
                attempted=False,
            )
    if corpus_requested and not corpus_enabled:
        bundle.record_failure(
            "corpus",
            schema.SKIPPED_UNCONFIGURED,
            "Corpus was requested but no readable directory was configured.",
            attempted=False,
        )
    # Expose plan_source to the renderer so render_compact can emit the
    # DEGRADED RUN banner when a named-entity topic was invoked bare
    # (source=deterministic AND no pre-research flags). LAW 7 backstop.
    bundle.artifacts["plan_source"] = plan_source
    bundle.artifacts["corpus_in_export"] = bool(config.get("_CORPUS_IN_EXPORT"))
    # Hiring-signals is deliberately jobs-only with no multi-source --plan, so
    # the LAW 7 degraded-run and Step 0.55 pre-research banners do not apply -
    # they would contradict the documented jobs-scoped flow. Suppress them.
    bundle.artifacts["hiring_signals_mode"] = hiring_signals_mode

    # Project-mode or person-mode GitHub: run once before the main subquery loop
    _github_custom_done = False
    _github_enriched_repos: set[str] = set()

    # Project mode takes priority over person mode
    if github_repos and "github" in available:
        bundle.mark_attempted("github")
        try:
            project_items = github.search_github_project(
                github_repos, from_date, to_date,
                depth=depth, token=config.get("GITHUB_TOKEN"),
            )
            if project_items:
                normalized = _normalize_score_dedupe(
                    "github", project_items, from_date, to_date,
                    freshness_mode=plan.freshness_mode,
                    ranking_query=f"What are {', '.join(github_repos)} doing on GitHub?",
                )
                primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                bundle.add_items(primary_label, "github", normalized)
                _github_custom_done = True
                _github_enriched_repos = {r.lower() for r in github_repos}
        except Exception as exc:
            bundle.errors_by_source["github"] = f"Project-mode failed: {exc}"
            state, attempted = _classify_source_failure(exc)
            bundle.record_failure("github", state, str(exc), attempted=attempted)

    _github_person_done = False
    if github_user and "github" in available and not _github_custom_done:
        bundle.mark_attempted("github")
        try:
            person_items = github.search_github_person(
                github_user, from_date, to_date,
                depth=depth, token=config.get("GITHUB_TOKEN"),
            )
            if person_items:
                normalized = _normalize_score_dedupe(
                    "github", person_items, from_date, to_date,
                    freshness_mode=plan.freshness_mode,
                    ranking_query=f"What is @{github_user} doing on GitHub?",
                )
                # Use the first subquery's label so RRF can look up the weight
                primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                bundle.add_items(primary_label, "github", normalized)
                _github_person_done = True
        except Exception as exc:
            bundle.errors_by_source["github"] = f"Person-mode failed: {exc}"
            state, attempted = _classify_source_failure(exc)
            bundle.record_failure("github", state, str(exc), attempted=attempted)

    # Trustpilot session warm-up happens inside search_trustpilot at the
    # first (capped, single) fetch -- lazily, so it never delays the other
    # sources' streams and never fires for runs whose plan fetches no
    # Trustpilot. The module-level lock in lib/trustpilot.py serializes
    # concurrent vs-mode sub-runs so they never race Chrome harvests.

    # Thread-safe set prevents redundant fetches after a source returns 429
    rate_limited_sources: set[str] = set()
    rate_limit_lock = threading.Lock()

    # Local corpus retrieval is intentionally outside the network executor and
    # retry budget. One bounded stream participates in the same signal scoring,
    # fusion, reranking, and per-source result cap as remote sources.
    if corpus_enabled and plan.subqueries:
        primary = plan.subqueries[0]
        bundle.mark_attempted("corpus")
        result = corpus.search(
            topic,
            resolved_corpus_dirs,
            from_date=from_date,
            to_date=to_date,
            all_time=corpus_all_time,
            limit=settings["per_stream_limit"],
            cache_dir=env.CONFIG_DIR,
        )
        prepared_query = relevance.PreparedQuery(primary.ranking_query)
        lookback_window_days = (
            datetime.strptime(to_date, "%Y-%m-%d").date()
            - datetime.strptime(from_date, "%Y-%m-%d").date()
        ).days
        corpus_items = signals.annotate_stream(
            result.items,
            prepared_query,
            plan.freshness_mode,
            reference_date=to_date,
            max_days=lookback_window_days,
        )
        corpus_items = signals.prune_low_relevance(corpus_items)
        corpus_items = dedupe.dedupe_items(corpus_items)
        for item in corpus_items:
            item.snippet = snippet.extract_best_snippet(item, prepared_query)
        bundle.add_items(primary.label, "corpus", corpus_items)
        if result.notes:
            outcome = bundle.source_status["corpus"]
            bundle.source_status["corpus"] = schema.SourceOutcome(
                source="corpus",
                state=outcome.state,
                items_returned=outcome.items_returned,
                attempted=True,
                detail="; ".join(result.notes),
            )
        bundle.artifacts["corpus"] = {
            "files_scanned": result.files_scanned,
            "cache_hits": result.cache_hits,
            "all_time": corpus_all_time,
        }

    futures = {}
    # Per-source fetch budget prevents redundant API calls
    source_fetch_count: dict[str, int] = {}
    stream_count = sum(
        1
        for subquery in plan.subqueries
        for source in subquery.sources
        if source in available and source != "corpus"
    )
    max_workers = _inner_max_workers(stream_count, internal_subrun=internal_subrun)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for subquery in plan.subqueries:
            for source in subquery.sources:
                if source not in available:
                    continue
                if source == "corpus":
                    continue
                # Skip GitHub keyword search if person-mode already ran
                if source == "github" and (_github_person_done or _github_custom_done):
                    continue
                # Enforce per-source fetch cap
                cap = MAX_SOURCE_FETCHES.get(source)
                if cap is not None:
                    current = source_fetch_count.get(source, 0)
                    if current >= cap:
                        continue
                    source_fetch_count[source] = current + 1
                bundle.mark_attempted(source)
                futures[
                    executor.submit(
                        _retrieve_stream,
                        topic=topic,
                        subquery=subquery,
                        source=source,
                        config=config,
                        depth=depth,
                        date_range=(from_date, to_date),
                        runtime=runtime,
                        mock=mock,
                        rate_limited_sources=rate_limited_sources,
                        rate_limit_lock=rate_limit_lock,
                        web_backend=web_backend,
                        raw_topic=topic,
                        subreddits=subreddits,
                        tiktok_hashtags=tiktok_hashtags,
                        tiktok_creators=tiktok_creators,
                        ig_creators=ig_creators,
                        trustpilot_domain=trustpilot_domain,
                        trustpilot_domain_is_hint=trustpilot_domain_is_hint,
                    )
                ] = (subquery, source)

        for future in as_completed(futures):
            subquery, source = futures[future]
            try:
                raw_items, artifact = future.result()
            except Exception as exc:
                # Share 429 signal so pending futures skip this source
                if _is_rate_limit_error(exc):
                    with rate_limit_lock:
                        rate_limited_sources.add(source)
                    bundle.errors_by_source[source] = str(exc)
                    state, attempted = _classify_source_failure(exc)
                    bundle.record_failure(source, state, str(exc), attempted=attempted)
                    continue
                # Retry once for transient 5xx errors
                if _is_transient_error(exc):
                    time.sleep(3)
                    try:
                        raw_items, artifact = _retrieve_stream(
                            topic=topic, subquery=subquery, source=source,
                            config=config, depth=depth, date_range=(from_date, to_date),
                            runtime=runtime, mock=mock,
                            rate_limited_sources=rate_limited_sources,
                            rate_limit_lock=rate_limit_lock,
                            web_backend=web_backend,
                            raw_topic=topic,
                            subreddits=subreddits,
                            tiktok_hashtags=tiktok_hashtags,
                            tiktok_creators=tiktok_creators,
                            ig_creators=ig_creators,
                            trustpilot_domain=trustpilot_domain,
                            trustpilot_domain_is_hint=trustpilot_domain_is_hint,
                        )
                    except Exception as retry_exc:
                        detail = f"{exc} (retried once, still failed: {retry_exc})"
                        bundle.errors_by_source[source] = detail
                        state, attempted = _classify_source_failure(retry_exc)
                        bundle.record_failure(source, state, detail, attempted=attempted)
                        continue
                else:
                    bundle.errors_by_source[source] = str(exc)
                    state, attempted = _classify_source_failure(exc)
                    bundle.record_failure(source, state, str(exc), attempted=attempted)
                    continue
            outcome_note = None
            if isinstance(artifact, dict) and artifact.get("_source_outcome"):
                artifact = dict(artifact)
                outcome_note = artifact.pop("_source_outcome")
                bundle.record_failure(
                    source,
                    outcome_note["state"],
                    outcome_note["detail"],
                    attempted=outcome_note.get("attempted", True),
                )
            normalized = _normalize_score_dedupe(
                source, raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=subquery.ranking_query,
            )
            # Jobs is exempt from per_stream_limit: a careers board is a complete
            # snapshot of open roles, and truncating it to the default 12 drops
            # strategic postings (the whole point of hiring-signals coverage).
            if source != "jobs":
                normalized = normalized[: settings["per_stream_limit"]]
            bundle.add_items(subquery.label, source, normalized)
            if artifact:
                bundle.artifacts.setdefault("grounding", []).append(artifact)

    # Phase 2: supplemental entity-based searches
    _run_supplemental_searches(
        topic=topic,
        bundle=bundle,
        plan=plan,
        config=config,
        depth=depth,
        date_range=(from_date, to_date),
        runtime=runtime,
        mock=mock,
        rate_limited_sources=rate_limited_sources,
        rate_limit_lock=rate_limit_lock,
        x_handle=x_handle,
        x_related=x_related,
    )

    # Phase 2b: retry thin sources with simplified query
    # Note: _github_skip_sources tells the retry to not re-run GitHub keyword search
    # when project-mode or person-mode already provided authoritative data.
    _github_skip_retry = {"corpus"}
    if _github_person_done or _github_custom_done:
        _github_skip_retry.add("github")
    _retry_thin_sources(
        topic=topic,
        bundle=bundle,
        plan=plan,
        config=config,
        depth=depth,
        date_range=(from_date, to_date),
        runtime=runtime,
        mock=mock,
        rate_limited_sources=rate_limited_sources,
        rate_limit_lock=rate_limit_lock,
        settings=settings,
        web_backend=web_backend,
        skip_sources=_github_skip_retry,
    )

    # Reclassify partial failures as DEGRADED instead of silently dropping them.
    # A source that 429'd on one subquery but succeeded on another is not a hard
    # failure, but it is not healthy either: it likely returned fewer results
    # than it should have. Move it out of errors_by_source (so it isn't reported
    # as "failed") and into degraded_by_source (so it survives into warnings),
    # rather than deleting the signal outright as the engine used to.
    degraded_by_source: dict[str, str] = {}
    for source in list(bundle.errors_by_source):
        if bundle.items_by_source.get(source):
            degraded_by_source[source] = bundle.errors_by_source[source]
            del bundle.errors_by_source[source]

    hiring_summary = _apply_hiring_signal_gate(
        bundle,
        explicit=hiring_signals_mode,
        topic=topic,
    )
    if hiring_summary:
        bundle.artifacts["hiring_signals"] = hiring_summary

    items_by_source = _finalize_items_by_source(
        bundle.items_by_source, topic=topic, config=config, depth=depth, mock=mock,
    )
    source_status = _finalize_source_status(bundle.source_status, items_by_source)
    candidates = weighted_rrf(bundle.items_by_source_and_query, plan, pool_limit=settings["pool_limit"])
    # Normalized set of handles this run resolved for the topic. A candidate
    # authored by one of these is first-party and is exempted from the
    # entity-miss demotion in rerank (a post never repeats its own author's
    # name, so the body-text grounding check would otherwise zero out the
    # subject's own highest-signal posts).
    resolved_handles = {
        h.lstrip("@").strip().lower()
        for h in ([x_handle, github_user, *(x_related or [])])
        if h and h.strip()
    }
    private_candidates = [
        candidate
        for candidate in candidates
        if candidate.source == "corpus"
        or any(item.source == "corpus" for item in candidate.source_items)
    ]
    private_candidate_ids = {id(candidate) for candidate in private_candidates}
    public_candidates = [
        candidate for candidate in candidates if id(candidate) not in private_candidate_ids
    ]
    ranked_public = rerank.rerank_candidates(
        topic=topic,
        plan=plan,
        candidates=public_candidates,
        provider=None if mock else reasoning_provider,
        model=None if mock else runtime.rerank_model,
        shortlist_size=settings["rerank_limit"],
        resolved_handles=resolved_handles,
    )
    # Corpus titles/snippets must never enter a hosted reasoning prompt. Score
    # every candidate carrying corpus evidence with the deterministic fallback,
    # even when the rest of the run uses a remote reranker.
    ranked_private = rerank.rerank_candidates(
        topic=topic,
        plan=plan,
        candidates=private_candidates,
        provider=None,
        model=None,
        shortlist_size=settings["rerank_limit"],
        resolved_handles=resolved_handles,
    )
    ranked_candidates = sorted(
        [*ranked_public, *ranked_private],
        key=lambda candidate: (
            -candidate.final_score,
            -(candidate.engagement or -1),
            min(candidate.native_ranks.values(), default=999),
            candidate.title,
        ),
    )
    rerank.score_fun(
        topic=topic,
        candidates=ranked_public,
        provider=None if mock else reasoning_provider,
        model=None if mock else runtime.rerank_model,
    )
    rerank.score_fun(
        topic=topic,
        candidates=ranked_private,
        provider=None,
        model=None,
    )

    # Phase 3: post-rerank GitHub star enrichment. Record/replay-aware so the
    # eval harness stays fully offline: this path calls the GitHub API (and the
    # gh-credential fallback) outside the _retrieve_stream seam, so it gets its
    # own fixture exchange keyed by phase.
    if "github" in available and not mock:
        star_request = {
            "source": "github",
            "phase": "post_rerank_star_enrichment",
            "topic": topic,
            "depth": depth,
        }
        star_matched, star_replayed = http.fixture_source_replay(star_request)
        if star_matched:
            star_map = star_replayed if isinstance(star_replayed, dict) else {}
            github.apply_star_map(ranked_candidates, star_map)
        else:
            collected_star_map: dict[str, int] = {}
            github.enrich_candidates_with_stars(
                ranked_candidates,
                token=config.get("GITHUB_TOKEN"),
                already_enriched=_github_enriched_repos,
                collect_map=collected_star_map,
            )
            http.fixture_source_record(star_request, collected_star_map)

    clusters = cluster_candidates(ranked_candidates, plan)
    warnings = _warnings(items_by_source, ranked_candidates, bundle.errors_by_source, degraded_by_source)
    library_context, library_warning = _load_library_context(
        topic=topic,
        config=config,
        mock=mock,
        internal_subrun=internal_subrun,
        x_handle=x_handle,
        github_user=github_user,
        github_repos=github_repos,
        save_dir=save_dir,
    )
    if library_warning:
        warnings.append(library_warning)

    return schema.Report(
        topic=topic,
        range_from=from_date,
        range_to=to_date,
        generated_at=datetime.now(timezone.utc).isoformat(),
        provider_runtime=runtime,
        query_plan=plan,
        clusters=clusters,
        ranked_candidates=ranked_candidates,
        items_by_source=items_by_source,
        errors_by_source=bundle.errors_by_source,
        source_status=source_status,
        warnings=warnings,
        artifacts=bundle.artifacts,
        library_context=library_context,
    )


def _candidate_is_duplicate(
    candidate: schema.Candidate,
    kept: list[schema.Candidate],
) -> bool:
    if any(existing.candidate_id == candidate.candidate_id for existing in kept):
        return True
    if candidate.url and any(existing.url == candidate.url for existing in kept):
        return True
    candidate_text = " ".join((candidate.title, candidate.snippet)).strip()
    return bool(candidate_text) and any(
        dedupe.hybrid_similarity(
            candidate_text,
            " ".join((existing.title, existing.snippet)).strip(),
        ) >= 0.7
        for existing in kept
    )


def merge_drill_report(
    report: schema.Report,
    drill_report: schema.Report,
    matched_clusters: list[schema.Cluster],
    *,
    target: str,
) -> schema.Report:
    """Merge a narrow follow-up into its cached report while preserving other clusters."""
    merged = copy.deepcopy(report)
    selected_cluster_ids = {cluster.cluster_id for cluster in matched_clusters}
    selected_candidate_ids = {
        candidate_id
        for cluster in matched_clusters
        for candidate_id in cluster.candidate_ids
    }
    original_candidates = {
        candidate.candidate_id: candidate for candidate in merged.ranked_candidates
    }
    unrelated_candidates = [
        candidate for candidate in merged.ranked_candidates
        if candidate.candidate_id not in selected_candidate_ids
    ]
    original_summary = ""
    for cluster in matched_clusters:
        for candidate_id in cluster.representative_ids:
            candidate = original_candidates.get(candidate_id)
            if candidate:
                original_summary = candidate.snippet or candidate.explanation or candidate.title
                if original_summary:
                    break
        if original_summary:
            break

    unrelated_candidate_indexes = {
        candidate.candidate_id: index
        for index, candidate in enumerate(unrelated_candidates)
    }
    focused_candidates: list[schema.Candidate] = []
    for candidate in [
        *copy.deepcopy(drill_report.ranked_candidates),
        *[
            copy.deepcopy(candidate)
            for candidate in merged.ranked_candidates
            if candidate.candidate_id in selected_candidate_ids
        ],
    ]:
        unrelated_index = unrelated_candidate_indexes.get(candidate.candidate_id)
        if unrelated_index is not None:
            candidate.cluster_id = unrelated_candidates[unrelated_index].cluster_id
            unrelated_candidates[unrelated_index] = candidate
            continue
        if not _candidate_is_duplicate(candidate, focused_candidates):
            focused_candidates.append(candidate)

    primary_cluster = matched_clusters[0]
    for candidate in focused_candidates:
        candidate.cluster_id = primary_cluster.cluster_id
    focused_ids = [candidate.candidate_id for candidate in focused_candidates]
    focused_sources = sorted({
        source
        for candidate in focused_candidates
        for source in schema.candidate_sources(candidate)
    })
    replacement_cluster = schema.Cluster(
        cluster_id=primary_cluster.cluster_id,
        title=primary_cluster.title,
        candidate_ids=focused_ids,
        representative_ids=focused_ids[:3],
        sources=focused_sources,
        score=max((candidate.final_score for candidate in focused_candidates), default=0.0),
        uncertainty="single-source" if len(focused_sources) == 1 else None,
    )

    first_selected_index = min(
        index
        for index, cluster in enumerate(merged.clusters)
        if cluster.cluster_id in selected_cluster_ids
    )
    remaining_clusters = [
        cluster for cluster in merged.clusters
        if cluster.cluster_id not in selected_cluster_ids
    ]
    remaining_clusters.insert(first_selected_index, replacement_cluster)
    merged.clusters = remaining_clusters

    merged.ranked_candidates = focused_candidates + unrelated_candidates

    all_sources = set(merged.items_by_source) | set(drill_report.items_by_source)
    new_item_count = 0
    merged_items: dict[str, list[schema.SourceItem]] = {}
    for source in sorted(all_sources):
        old_items = merged.items_by_source.get(source, [])
        new_items = drill_report.items_by_source.get(source, [])
        # Collapse exact URL matches first, preferring the drill's copy (it
        # carries fresh transcripts/comments); fuzzy dedupe alone keeps both
        # when enrichment changed the text substantially.
        new_urls = {item.url for item in new_items if item.url}
        kept_old = [item for item in old_items if not (item.url and item.url in new_urls)]
        combined = dedupe.dedupe_items([*copy.deepcopy(new_items), *kept_old])
        old_unique = dedupe.dedupe_items(old_items)
        new_item_count += max(0, len(combined) - len(old_unique))
        merged_items[source] = combined
    merged.items_by_source = merged_items

    merged.generated_at = drill_report.generated_at
    merged.query_plan = drill_report.query_plan
    # The drill's retrieval window is the report's window now (a --days/--as-of
    # override on the drill must not be mislabeled with the cached range).
    merged.range_from = drill_report.range_from
    merged.range_to = drill_report.range_to
    attempted_sources = {
        source
        for source, outcome in drill_report.source_status.items()
        if outcome.attempted or outcome.state == schema.SKIPPED_UNCONFIGURED
    }
    for source in attempted_sources:
        if source in drill_report.errors_by_source:
            merged.errors_by_source[source] = drill_report.errors_by_source[source]
        else:
            merged.errors_by_source.pop(source, None)
        merged.source_status[source] = drill_report.source_status[source]
    merged.source_status = _finalize_source_status(
        merged.source_status,
        merged.items_by_source,
    )
    degraded_by_source = {
        source: outcome.detail or "partial results"
        for source, outcome in merged.source_status.items()
        if outcome.state == schema.PARTIAL
    }
    merged.warnings = _warnings(
        merged.items_by_source,
        merged.ranked_candidates,
        merged.errors_by_source,
        degraded_by_source,
    )
    merged.artifacts.update(copy.deepcopy(drill_report.artifacts))
    history = list(merged.artifacts.get("drill_history") or [])
    history.append({
        "target": target,
        "clusters": [cluster.title for cluster in matched_clusters],
        "new_items": new_item_count,
        "generated_at": drill_report.generated_at,
    })
    merged.artifacts["drill_history"] = history
    merged.artifacts["drill_context"] = {
        "target": target,
        "cluster_titles": [cluster.title for cluster in matched_clusters],
        "original_summary": original_summary,
        "new_items": new_item_count,
        "sources": focused_sources,
    }
    merged.drill_of = primary_cluster.title
    return merged


def _normalize_score_dedupe(
    source: str,
    raw_items: list[dict],
    from_date: str,
    to_date: str,
    freshness_mode: str,
    ranking_query: str,
) -> list[schema.SourceItem]:
    """Normalize, annotate, prune, dedupe, and extract snippets for a batch of raw items."""
    normalized = normalize.normalize_source_items(
        source, raw_items, from_date, to_date,
        freshness_mode=freshness_mode,
    )
    prepared_query = relevance.PreparedQuery(ranking_query)
    lookback_window_days = (
        datetime.strptime(to_date, "%Y-%m-%d").date()
        - datetime.strptime(from_date, "%Y-%m-%d").date()
    ).days
    normalized = signals.annotate_stream(
        normalized,
        prepared_query,
        freshness_mode,
        reference_date=to_date,
        max_days=lookback_window_days,
    )
    if source != "jobs":
        normalized = signals.prune_low_relevance(normalized)
    normalized = dedupe.dedupe_items(normalized)
    for item in normalized:
        item.snippet = snippet.extract_best_snippet(item, prepared_query)
    return normalized


def _finalize_items_by_source(
    items_by_source_raw: dict[str, list[schema.SourceItem]],
    topic: str = "",
    config: dict | None = None,
    depth: str = "default",
    mock: bool = False,
) -> dict[str, list[schema.SourceItem]]:
    finalized = {}
    for source, items in items_by_source_raw.items():
        items = sorted(items, key=lambda item: item.local_rank_score or 0.0, reverse=True)
        items = dedupe.dedupe_items(items)
        enrichment_request = {
            "source": source,
            "phase": "post_ranking_enrichment",
            "topic": topic,
            "depth": depth,
        }
        if source == "youtube" and items and not mock:
            # Same budget-at-the-survivors principle as the digg branch
            # below: retrieval-time transcripts go to each search's
            # top-by-views candidates, while final selection ranks by
            # relevance. Backfill survivors that arrived without one so the
            # transcript budget lands on videos the brief actually shows
            # (#542).
            matched, replayed = http.fixture_source_replay(enrichment_request)
            if matched:
                items = _merge_replayed_enrichment(items, replayed)
            else:
                sc_token = (
                    config.get("SCRAPECREATORS_API_KEY")
                    if config and env.is_youtube_sc_available(config) else None
                )
                youtube_yt.backfill_transcripts(
                    items, topic=topic, depth=depth, token=sc_token,
                )
                http.fixture_source_record(enrichment_request, schema.to_dict(items))
        # Post-merge topic-relevance filter for Polymarket: comparison queries
        # fan out into per-entity subqueries ("Hermes", "OpenClaw") whose topic
        # is too narrow for Gamma API to filter meaningfully. Re-validating the
        # merged list against the full original topic drops off-topic markets
        # (e.g., WTI crude oil, Elon tweet counts) before footer emission.
        if source == "polymarket" and topic:
            items = polymarket.filter_items_against_topic(topic, items)
            # --polymarket-keywords (via config): additional keyword filter
            # for ambiguous single-token topics (e.g., "Warriors" → nba,gsw).
            keywords = config.get("_polymarket_keywords") if isinstance(config, dict) else None
            if keywords:
                items = polymarket.filter_items_against_keywords(items, keywords)
        if source == "digg" and items:
            # Pull top-ranked X posts only for the survivors that will appear
            # in the brief. Spending the enrichment budget here (rather than
            # at retrieval time) keeps the inline 'via Digg' quotes
            # paired with the clusters dedupe actually kept.
            matched, replayed = http.fixture_source_replay(enrichment_request)
            if matched:
                items = _merge_replayed_enrichment(items, replayed)
            else:
                digg.enrich_source_items(items, top_k=3)
                http.fixture_source_record(enrichment_request, schema.to_dict(items))
        finalized[source] = items
    return finalized


def _merge_replayed_enrichment(
    items: list[schema.SourceItem],
    replayed: list[dict],
) -> list[schema.SourceItem]:
    """Apply recorded post-ranking enrichment onto freshly computed items.

    Enrichment (transcripts, Digg posts) only mutates ``metadata``. Merging by
    item_id instead of replacing the list keeps normalization, scoring, and
    dedupe regressions visible to the eval - fixture state must not overwrite
    what the current pipeline computed.
    """
    replayed_by_id = {
        entry.get("item_id"): entry for entry in replayed if isinstance(entry, dict)
    }
    for item in items:
        record = replayed_by_id.get(item.item_id)
        if record and record.get("metadata"):
            item.metadata.update(record["metadata"])
    return items


def _apply_hiring_signal_gate(
    bundle: schema.RetrievalBundle,
    *,
    explicit: bool,
    topic: str,
) -> dict[str, Any] | None:
    jobs_items = bundle.items_by_source.get("jobs") or []
    if not jobs_items:
        if explicit:
            return hiring_signals.analyze([], explicit=True, topic=topic)
        return None

    summary = hiring_signals.analyze(jobs_items, explicit=explicit, topic=topic)
    if not explicit and not summary.get("include"):
        bundle.items_by_source.pop("jobs", None)
        for key in list(bundle.items_by_source_and_query):
            if key[1] == "jobs":
                del bundle.items_by_source_and_query[key]
    return summary


def _ensure_jobs_in_plan(
    plan: schema.QueryPlan,
    available: list[str],
    *,
    explicit: bool,
    topic: str,
) -> None:
    if "jobs" not in available:
        return
    if not (explicit or _company_topic_likely(topic)):
        return
    if "jobs" not in plan.source_weights:
        plan.source_weights["jobs"] = 1.0
    for subquery in plan.subqueries:
        if "jobs" not in subquery.sources:
            subquery.sources.append("jobs")


def _company_topic_likely(topic: str) -> bool:
    text = topic.strip()
    if not text:
        return False
    lower = text.lower()
    if "?" in text or len(text.split()) > 4:
        return False
    generic = {
        "how", "what", "why", "best", "top", "tutorial", "guide", "prompts",
        "news", "latest", "ideas", "examples",
    }
    if any(word in generic for word in lower.split()):
        return False
    known_single_word_companies = {
        "apple", "uber", "google", "microsoft", "amazon", "meta", "netflix",
        "openai", "anthropic", "qualtrics", "stripe", "brex",
    }
    if " vs " in lower or " versus " in lower:
        parts = re.split(r"\s+(?:vs|versus)\s+", text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return False
        return _comparison_side_company_like(parts[0], known_single_word_companies) or _comparison_side_company_like(
            parts[1], known_single_word_companies
        )
    return bool(text[:1].isupper() or lower in known_single_word_companies)


def _comparison_side_company_like(side: str, known_companies: set[str]) -> bool:
    token = re.sub(r"[^\w.+#-]", "", side.strip().split()[0] if side.strip() else "")
    if not token:
        return False
    lower = token.lower()
    common_tech_terms = {
        "python", "ruby", "javascript", "typescript", "java", "go", "golang",
        "rust", "php", "swift", "kotlin", "scala", "clojure", "elixir",
        "react", "vue", "angular", "svelte", "node", "django", "rails",
        "postgres", "mysql", "redis", "kubernetes", "docker",
    }
    if lower in common_tech_terms:
        return False
    return bool(token[:1].isupper() or lower in known_companies)


def _warnings(
    items_by_source: dict[str, list[schema.SourceItem]],
    candidates: list[schema.Candidate],
    errors_by_source: dict[str, str],
    degraded_by_source: dict[str, str] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if not candidates:
        warnings.append("No candidates survived retrieval and ranking.")
    if len(candidates) < 5:
        warnings.append("Evidence is thin for this topic.")
    top_sources = {
        source
        for candidate in candidates[:5]
        for source in schema.candidate_sources(candidate)
    }
    if len(top_sources) <= 1 and len(candidates) >= 3:
        warnings.append("Top evidence is highly concentrated in one source.")
    if errors_by_source:
        warnings.append(f"Some sources failed: {', '.join(sorted(errors_by_source))}")
    if degraded_by_source:
        # Partial failures: the source returned some items but errored/timed out
        # on at least one subquery, so its coverage is likely incomplete. Kept
        # distinct from hard failures so the signal is not silently dropped.
        warnings.append(
            f"Some sources returned partial results (degraded): {', '.join(sorted(degraded_by_source))}"
        )
    if not items_by_source:
        warnings.append("No source returned usable items.")
    return warnings


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect 429 rate-limit errors by status code or message text."""
    if hasattr(exc, "status_code") and getattr(exc, "status_code", None) == 429:
        return True
    return "429" in str(exc)


class SourceRunError(RuntimeError):
    """Source-specific failure that survived a module's fallback logic."""

    def __init__(self, message: str, state: schema.RunOutcomeState | None = None):
        super().__init__(message)
        self.outcome_state = state or http.classify_failure(message=message)


def _classify_source_failure(exc: Exception) -> tuple[schema.RunOutcomeState, bool]:
    """Classify HTTP, subprocess, and module-specific failures consistently."""
    detail = str(exc)
    lowered = detail.lower()
    if any(marker in lowered for marker in ("not configured", "no api key", "not installed")):
        return schema.SKIPPED_UNCONFIGURED, False
    if any(
        marker in lowered
        for marker in ("cookie expired", "expired cookie", "login required", "not logged in")
    ):
        return schema.AUTH_FAILED, True
    state = getattr(exc, "outcome_state", None) or http.classify_failure(
        status_code=getattr(exc, "status_code", None),
        message=detail,
    )
    return state, True


def _outcome_artifact(
    state: schema.RunOutcomeState,
    detail: str,
    *,
    attempted: bool = True,
) -> dict[str, Any]:
    return {
        "_source_outcome": {
            "state": state,
            "detail": detail,
            "attempted": attempted,
        }
    }


def _result_outcome_artifact(source: str, result: Any) -> dict[str, Any]:
    """Convert a legacy ``{"error": ...}`` source result into typed status."""
    if not isinstance(result, dict) or not result.get("error"):
        return {}
    detail = str(result["error"])
    if source == "reddit":
        state = reddit.classify_run_failure(detail)
        attempted = True
    elif source == "youtube":
        state = youtube_yt.classify_run_failure(detail)
        attempted = state != schema.SKIPPED_UNCONFIGURED
    elif source == "x":
        state = bird_x.classify_run_failure(detail)
        attempted = True
    elif source == "truthsocial" and detail == "Truth Social token expired":
        state = schema.AUTH_FAILED
        attempted = True
    elif source == "bluesky" and "network-level block" in detail.lower():
        state = schema.UNREACHABLE
        attempted = True
    else:
        state, attempted = _classify_source_failure(SourceRunError(detail))
    return _outcome_artifact(state, detail, attempted=attempted)


def _legacy_artifact_outcome(
    source: str,
    artifact: Any,
) -> dict[str, Any] | None:
    """Map known pre-outcome artifact contracts to a typed outcome note."""
    if not isinstance(artifact, dict):
        return None
    explicit = artifact.get("_source_outcome")
    if isinstance(explicit, dict):
        return explicit
    if source == "perplexity" and artifact.get("error"):
        error = str(artifact["error"])
        detail = str(
            artifact.get("asyncErrorMessage")
            or artifact.get("message")
            or error
        )
        state = (
            health.TIMEOUT
            if error.lower() == "timeout"
            else http.classify_failure(
                status_code=artifact.get("statusCode"),
                message=f"{error}: {detail}",
            )
        )
        return _outcome_artifact(state, detail)["_source_outcome"]
    if (
        source == "grounding"
        and artifact.get("reason") == "keyless-search-unavailable"
    ):
        return _outcome_artifact(
            schema.UNREACHABLE,
            "Keyless web search unavailable",
        )["_source_outcome"]
    return None


def _resolve_stream_outcome(
    source: str,
    artifact: Any,
    failures: list[http.HTTPError],
) -> dict[str, Any] | None:
    """Choose the most specific artifact or captured HTTP outcome."""
    artifact_outcome = _legacy_artifact_outcome(source, artifact)
    if not failures:
        return artifact_outcome
    # Pick the most specific failure rather than the last-appended one:
    # parallel workers append in nondeterministic order, and an auth failure
    # must not be masked by a later 429 (wrong doctor prescription).
    _FAILURE_SPECIFICITY = {
        health.AUTH_FAILED: 0,
        health.RATE_LIMITED: 1,
        health.SCHEMA_DRIFT: 2,
        health.TIMEOUT: 3,
        health.UNREACHABLE: 4,
        health.ERROR: 5,
    }
    failure = min(
        failures,
        key=lambda f: _FAILURE_SPECIFICITY.get(f.outcome_state, 9),
    )
    captured_outcome = _outcome_artifact(
        failure.outcome_state,
        str(failure),
    )["_source_outcome"]
    if artifact_outcome is None:
        return captured_outcome
    if (
        artifact_outcome.get("state") == health.ERROR
        and failure.outcome_state != health.ERROR
    ):
        return captured_outcome
    return artifact_outcome


def _finalize_source_status(
    outcomes: dict[str, schema.SourceOutcome],
    items_by_source: dict[str, list[schema.SourceItem]],
) -> dict[str, schema.SourceOutcome]:
    """Sync outcome counts to the final post-filter evidence set."""
    finalized: dict[str, schema.SourceOutcome] = {}
    for source, outcome in outcomes.items():
        count = len(items_by_source.get(source, []))
        state = outcome.state
        detail = outcome.detail
        fix_hint = outcome.fix_hint
        if state == schema.NO_RESULTS and count:
            state = health.OK
            detail = None
            fix_hint = None
        elif state == health.OK and not count:
            state = schema.NO_RESULTS
        elif state == schema.PARTIAL and not count:
            state = http.classify_failure(message=detail or "")
        finalized[source] = schema.SourceOutcome(
            source=source,
            state=state,
            items_returned=count,
            attempted=outcome.attempted,
            detail=detail,
            at=outcome.at,
            fix_hint=fix_hint,
        )
    return finalized


def _is_transient_error(exc: Exception) -> bool:
    """Detect 5xx server errors that are worth retrying."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    msg = str(exc)
    return any(code in msg for code in ("500", "502", "503", "504"))


def _run_supplemental_searches(
    *,
    topic: str,
    bundle: schema.RetrievalBundle,
    plan: schema.QueryPlan,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str],
    rate_limit_lock: threading.Lock,
    x_handle: str | None = None,
    x_related: list[str] | None = None,
) -> None:
    """Phase 2: extract entities from Phase 1 results, run targeted supplemental searches."""
    if depth == "quick" or mock:
        return

    from_date, to_date = date_range

    # Convert SourceItems to dicts for entity_extract. All X items (whatever
    # backend fetched them — bird, xai, xurl, xquik) land under the single "x"
    # slug, so this reads the whole X corpus.
    x_dicts = [
        {"author_handle": item.author or "", "text": item.body or ""}
        for item in bundle.items_by_source.get("x", [])
    ]
    reddit_dicts = [
        {
            "subreddit": item.container or "",
            "comment_insights": item.metadata.get("comment_insights", []),
            "top_comments": [
                {"excerpt": c.get("excerpt", c.get("text", ""))}
                for c in (item.metadata.get("top_comments") or [])
                if isinstance(c, dict)
            ],
        }
        for item in bundle.items_by_source.get("reddit", [])
    ]

    if not x_dicts and not reddit_dicts and not x_handle and not x_related:
        return

    entities = entity_extract.extract_entities(
        reddit_dicts, x_dicts,
        max_handles=3, max_subreddits=3,
    )

    handles = entities.get("x_handles", [])

    # Add explicit --x-handle if provided
    if x_handle:
        handle_clean = x_handle.lstrip("@").lower()
        if handle_clean not in [h.lower() for h in handles]:
            handles.insert(0, handle_clean)

    # Collect related handles (searched separately with lower weight)
    related_handles = []
    if x_related:
        primary_lower = x_handle.lstrip("@").lower() if x_handle else ""
        for rh in x_related:
            rh_clean = rh.lstrip("@").lower().strip()
            if rh_clean and rh_clean != primary_lower and rh_clean not in [h.lower() for h in handles]:
                related_handles.append(rh_clean)

    if not handles and not related_handles:
        return

    # Pick the X handle-search backend: the first handle-capable backend in the
    # chain (bird or xquik). These supplemental from:/mentions lanes are
    # complementary to the topic search, so when the topic primary can't run
    # them (xai/xurl have no handle-lane implementation) but a capable backend
    # is available, use it rather than skipping Phase 2. bird scrapes X GraphQL
    # with the user's browser cookies; xquik runs the same lanes over its REST
    # API. All items land under the single "x" slug.
    x_slug = "x"
    chain = env.x_backend_chain(config)
    # Trust an explicit runtime backend as the head of the chain.
    pinned = runtime.x_search_backend
    if pinned:
        chain = [pinned] + [b for b in chain if b != pinned]
    primary = next((b for b in chain if b in ("bird", "xquik")), None)

    if primary == "bird":
        def _from_lane(hs: list, count: int) -> list:
            return bird_x.search_handles(hs, topic, from_date, count_per=count)

        def _about_lane(hs: list, count: int) -> list:
            return bird_x.search_mentions(hs, from_date, count_per=count)
    elif primary == "xquik":
        xquik_token = env.get_xquik_token(config)

        def _from_lane(hs: list, count: int) -> list:
            return xquik.search_handles(hs, topic, from_date, to_date, count_per=count, token=xquik_token)

        def _about_lane(hs: list, count: int) -> list:
            return xquik.search_mentions(hs, from_date, to_date, topic=topic, count_per=count, token=xquik_token)
    else:
        return  # primary X backend has no handle-lane support (xai/xurl) or none configured

    # Skip if the X source is rate-limited.
    if x_slug in rate_limited_sources:
        return

    # Collect existing URLs for deduplication
    existing_urls = {
        item.url
        for items in bundle.items_by_source.values()
        for item in items
        if item.url
    }

    ranking_query = plan.subqueries[0].ranking_query if plan.subqueries else topic
    primary_label = plan.subqueries[0].label if plan.subqueries else "primary"

    # Search primary handles (full weight): FROM lane (their own tweets) +
    # ABOUT lane (tweets mentioning them). Both engagement-weighted and deduped
    # by URL at normalize time.
    if handles:
        # Independent try/except per lane so a failure in one does not discard
        # the other's already-computed results.
        from_items: list = []
        about_items: list = []
        try:
            from_items = _from_lane(handles, FROM_LANE_COUNT_PER)
        except Exception as exc:
            print(f"[Pipeline] Phase 2 FROM-lane search failed: {exc}", file=sys.stderr)
            state, attempted = _classify_source_failure(exc)
            bundle.record_failure(
                x_slug,
                state,
                f"Phase 2 FROM-lane: {exc}",
                attempted=attempted,
            )
            if not bundle.items_by_source.get(x_slug):
                bundle.errors_by_source[x_slug] = f"Phase 2 FROM-lane: {exc}"
        try:
            about_items = _about_lane(handles, MENTION_LANE_COUNT_PER)
        except Exception as exc:
            print(f"[Pipeline] Phase 2 ABOUT-lane search failed: {exc}", file=sys.stderr)
            state, attempted = _classify_source_failure(exc)
            bundle.record_failure(
                x_slug,
                state,
                f"Phase 2 ABOUT-lane: {exc}",
                attempted=attempted,
            )
        raw_items = from_items + about_items

        if raw_items:
            normalized = _normalize_score_dedupe(
                x_slug, raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=ranking_query,
            )
            # Deduplicate against Phase 1 URLs
            normalized = [item for item in normalized if item.url not in existing_urls]
            if normalized:
                bundle.add_items(primary_label, x_slug, normalized)
                # Update existing URLs for related-handle dedup
                for item in normalized:
                    if item.url:
                        existing_urls.add(item.url)

    # Search related handles with lower weight (0.3)
    if related_handles:
        try:
            raw_items = _from_lane(related_handles, RELATED_HANDLE_COUNT_PER)
        except Exception as exc:
            print(f"[Pipeline] Phase 2 related handle search failed: {exc}", file=sys.stderr)
            state, attempted = _classify_source_failure(exc)
            bundle.record_failure(
                x_slug,
                state,
                f"Phase 2 related handle search: {exc}",
                attempted=attempted,
            )
            raw_items = []

        if raw_items:
            normalized = _normalize_score_dedupe(
                x_slug, raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=ranking_query,
            )
            # Deduplicate against all existing URLs (Phase 1 + primary handles)
            normalized = [item for item in normalized if item.url not in existing_urls]
            if normalized:
                # Use a separate subquery label with lower weight so RRF
                # scores related-handle results below primary results.
                bundle.add_items("supplemental-related", x_slug, normalized)
                # Register the supplemental-related label in the plan for fusion
                if not any(sq.label == "supplemental-related" for sq in plan.subqueries):
                    plan.subqueries.append(
                        schema.SubQuery(
                            label="supplemental-related",
                            search_query=", ".join(related_handles),
                            ranking_query=ranking_query,
                            sources=[x_slug],
                            weight=0.3,
                        )
                    )


def _retry_thin_sources(
    *,
    topic: str,
    bundle: schema.RetrievalBundle,
    plan: schema.QueryPlan,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str],
    rate_limit_lock: threading.Lock,
    settings: dict[str, Any],
    web_backend: str = "auto",
    skip_sources: set[str] | None = None,
) -> None:
    """Retry sources with thin results using simplified core subject query."""
    if depth == "quick":
        return

    planned_sources: list[str] = []
    for subquery in plan.subqueries:
        for source in subquery.sources:
            if source not in planned_sources:
                planned_sources.append(source)
    # trustpilot returns at most ONE item by design, so the "<3 items" rule
    # would re-fetch it after every successful lookup -- bypassing
    # MAX_SOURCE_FETCHES and re-resolving WITHOUT the caller's
    # --trustpilot-domain (a lookalike-misattribution path). Its thin result
    # is its normal success state; never retry it here.
    _skip = (skip_sources or set()) | {"trustpilot"}
    thin_sources = [
        source
        for source in planned_sources
        if len(bundle.items_by_source.get(source, [])) < 3
        and source not in bundle.errors_by_source
        and source not in _skip
    ]

    if not thin_sources:
        return

    core = query.extract_core_subject(topic, max_words=3)
    if not core:
        return
    # Note: we intentionally do NOT skip when core == topic. For short topics
    # like "Kanye West", the 3-word core IS the topic — but the planner may
    # have sent a different (worse) query to the source. Retrying with the
    # raw core subject is still valuable.

    from_date, to_date = date_range

    # Create a retry subquery with the simplified core subject
    retry_subquery = schema.SubQuery(
        label="retry",
        search_query=core,
        ranking_query=f"What recent evidence from the last 30 days matters for {core}?",
        sources=thin_sources,
        weight=0.3,
    )

    def _retry_one_source(
        source: str,
    ) -> tuple[str, list[schema.SourceItem], dict[str, Any] | None]:
        raw_items, artifact = _retrieve_stream(
            topic=topic,
            subquery=retry_subquery,
            source=source,
            config=config,
            depth=depth,
            date_range=date_range,
            runtime=runtime,
            mock=mock,
            rate_limited_sources=rate_limited_sources,
            rate_limit_lock=rate_limit_lock,
            web_backend=web_backend,
            raw_topic=topic,
        )
        outcome_note = artifact.get("_source_outcome") if isinstance(artifact, dict) else None
        normalized = _normalize_score_dedupe(
            source,
            raw_items,
            from_date,
            to_date,
            freshness_mode=plan.freshness_mode,
            ranking_query=retry_subquery.ranking_query,
        )
        if source == "jobs":
            return source, normalized, outcome_note
        return source, normalized[:settings["per_stream_limit"]], outcome_note

    retryable = [s for s in thin_sources if s not in rate_limited_sources]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(4, len(retryable) or 1)) as executor:
        futures = {executor.submit(_retry_one_source, s): s for s in retryable}
        for future in as_completed(futures):
            source = futures[future]
            try:
                source, normalized, outcome_note = future.result()
                if outcome_note:
                    bundle.record_failure(
                        source,
                        outcome_note["state"],
                        outcome_note["detail"],
                        attempted=outcome_note.get("attempted", True),
                    )
                existing_urls = {item.url for item in bundle.items_by_source.get(source, []) if item.url}
                new_items = [item for item in normalized if item.url not in existing_urls]

                if new_items:
                    primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                    bundle.add_items(primary_label, source, new_items)
            except Exception as exc:
                print(f"[Pipeline] Retry failed for {source}: {type(exc).__name__}: {exc}", file=sys.stderr)
                state, attempted = _classify_source_failure(exc)
                bundle.record_failure(
                    source,
                    state,
                    f"Simplified-query retry failed: {exc}",
                    attempted=attempted,
                )


def _fetch_x_backend(backend, subquery, from_date, to_date, depth, config):
    """Fetch X items from a single backend. Returns (items, error_str).

    Backends are tried in priority order by the caller (env.x_backend_chain);
    a non-empty error_str signals a hard failure (auth/payment/etc.) so the
    caller can fail over to the next backend or surface the error honestly.
    """
    query = subquery.search_query
    if backend == "bird":
        result = bird_x.search_x(query, from_date, to_date, depth=depth)
        items = bird_x.parse_bird_response(result, query=query)
    elif backend == "xai":
        model = config.get("LAST30DAYS_X_MODEL") or config.get("XAI_MODEL_PIN") or providers.XAI_DEFAULT
        result = xai_x.search_x(config["XAI_API_KEY"], model, query, from_date, to_date, depth=depth)
        items = xai_x.parse_x_response(result)
    elif backend == "xurl":
        result = xurl_x.search_x(query, depth=depth)
        items = xurl_x.parse_x_response(result, topic=query)
    elif backend == "xquik":
        result = xquik.search_xquik(query, from_date, to_date, depth=depth, token=env.get_xquik_token(config))
        items = xquik.parse_xquik_response(result)
    else:
        return [], f"unknown X backend: {backend}"
    err = result.get("error") if isinstance(result, dict) else ""
    return items, (err or "")


def _reddit_post_key(item: dict) -> str:
    """Stable per-thread dedupe key (base36 post id from the url/permalink)."""
    url = item.get("url") or item.get("permalink") or ""
    m = re.search(r"/comments/([A-Za-z0-9]+)", url)
    return m.group(1) if m else url


def _merge_reddit_items(free: list[dict], sc: list[dict]) -> list[dict]:
    """Merge free + ScrapeCreators Reddit items, free first, deduped by post id.

    Used when the thinness-floor trigger backfills a thin free run with SC, so a
    thread present in both is never double-listed.
    """
    merged = list(free)
    seen = {_reddit_post_key(it) for it in free}
    for it in sc:
        key = _reddit_post_key(it)
        if key and key not in seen:
            seen.add(key)
            merged.append(it)
    return merged


def _retrieve_stream(*args, **kwargs) -> tuple[list[dict], dict]:
    """Run one stream and retain HTTP failures swallowed by source adapters."""
    source = str(kwargs.get("source") or "")
    fixture_request = {
        "source": source,
        "topic": kwargs.get("topic") or "",
        "search_query": getattr(kwargs.get("subquery"), "search_query", ""),
        "date_range": list(kwargs.get("date_range") or ()),
        "depth": kwargs.get("depth") or "",
    }
    module_backed = source in {
        "reddit",
        "x",
        "youtube",
        "stocktwits",
        "digg",
        "arxiv",
        "techmeme",
        "trustpilot",
        "github",
    }
    if module_backed:
        matched, replayed = http.fixture_source_replay(fixture_request)
        if matched:
            return replayed[0], replayed[1]
    try:
        with http.capture_failures() as failures, \
             http.fixture_module_capture(module_backed):
            items, artifact = _retrieve_stream_impl(*args, **kwargs)
    except Exception as exc:
        recorded_exc = exc
        if failures and not getattr(exc, "outcome_state", None):
            failure = failures[-1]
            recorded_exc = SourceRunError(str(exc), failure.outcome_state)
        if module_backed:
            http.fixture_source_record_error(fixture_request, recorded_exc)
        if recorded_exc is not exc:
            raise recorded_exc from exc
        raise
    outcome_note = _resolve_stream_outcome(
        str(kwargs.get("source") or ""),
        artifact,
        failures,
    )
    if outcome_note:
        artifact = dict(artifact or {})
        artifact["_source_outcome"] = outcome_note
    if module_backed:
        http.fixture_source_record(fixture_request, [items, artifact])
    return items, artifact


def _retrieve_stream_impl(
    *,
    topic: str,
    subquery: schema.SubQuery,
    source: str,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str] | None = None,
    rate_limit_lock: threading.Lock | None = None,
    web_backend: str = "auto",
    raw_topic: str = "",
    subreddits: list[str] | None = None,
    tiktok_hashtags: list[str] | None = None,
    tiktok_creators: list[str] | None = None,
    ig_creators: list[str] | None = None,
    trustpilot_domain: str | None = None,
    trustpilot_domain_is_hint: bool = False,
) -> tuple[list[dict], dict]:
    # Early exit if source was rate-limited by a sibling future
    if rate_limited_sources is not None and source in rate_limited_sources:
        return [], {}
    from_date, to_date = date_range
    if mock:
        return _mock_stream_results(source, subquery)
    if source == "grounding":
        return grounding.web_search(
            subquery.search_query, date_range, config, backend=web_backend)
    if source == "jobs":
        return jobs.search_jobs(
            raw_topic or topic or subquery.search_query,
            date_range,
            config,
            depth=depth,
            web_backend=web_backend,
            explicit=bool(config.get("_hiring_signals_mode")),
        )
    if source == "reddit":
        # Use raw_topic so expand_reddit_queries() generates diverse variants
        # from the original user topic, not the planner's narrowed search_query.
        reddit_query = raw_topic or subquery.search_query
        dedicated_subreddits = config.get("_dedicated_subreddits") or None
        has_sc_key = bool(config.get("SCRAPECREATORS_API_KEY"))
        sc_first = (
            has_sc_key
            and (config.get(env.REDDIT_BACKEND_PIN_VAR) or "").lower()
            == "scrapecreators"
        )
        if sc_first:
            # env.REDDIT_BACKEND_PIN_VAR=scrapecreators: SC primary, public fallback
            primary_failure: Exception | None = None
            try:
                result = reddit.search_and_enrich(
                    reddit_query, from_date, to_date, depth=depth,
                    token=config.get("SCRAPECREATORS_API_KEY"),
                    subreddits=subreddits,
                )
                items = reddit.parse_reddit_response(result)
                if items:
                    return items, {}
                sys.stderr.write(
                    "[Reddit] ScrapeCreators primary returned no items, "
                    "using public fallback\n"
                )
            except Exception as exc:
                primary_failure = exc
                sys.stderr.write(
                    f"[Reddit] ScrapeCreators primary failed "
                    f"({type(exc).__name__}: {exc}), using public fallback\n"
                )
            public_failure: Exception | None = None
            try:
                public_results = reddit_public.search_reddit_public(
                    reddit_query, from_date, to_date, depth=depth,
                    subreddits=subreddits,
                )
                if public_results:
                    if primary_failure is not None:
                        state = reddit.classify_run_failure(str(primary_failure))
                        return public_results, _outcome_artifact(
                            state,
                            f"Reddit primary failed; public fallback returned "
                            f"{len(public_results)} items: {primary_failure}",
                        )
                    return public_results, {}
                sys.stderr.write(
                    "[Reddit] Public fallback returned no items after "
                    "ScrapeCreators primary miss\n"
                )
            except Exception as exc:
                public_failure = exc
                sys.stderr.write(
                    f"[Reddit] Public fallback also failed "
                    f"({type(exc).__name__}: {exc})\n"
                )
            failure = public_failure or primary_failure
            if failure is not None:
                state = reddit.classify_run_failure(str(failure))
                raise SourceRunError(
                    f"Reddit primary and fallback produced no results after failure: {failure}",
                    state,
                )
            return [], {}

        # Default: public Reddit first (free). ScrapeCreators backfills when the
        # free path is empty OR returns fewer than the configured thinness floor
        # (env.REDDIT_SC_MIN_ITEMS_VAR, default 0 = empty-only — today's
        # behavior, no extra credit spend unless the user opts in).
        try:
            min_items = int(config.get(env.REDDIT_SC_MIN_ITEMS_VAR) or 0)
        except (TypeError, ValueError):
            min_items = 0
        public_results: list[dict] = []
        public_failure: Exception | None = None
        try:
            public_results = reddit_public.search_reddit_public(
                reddit_query, from_date, to_date, depth=depth,
                subreddits=subreddits, dedicated_subreddits=dedicated_subreddits,
            ) or []
        except Exception as exc:
            public_failure = exc
            sys.stderr.write(
                f"[Reddit] Public search failed ({type(exc).__name__}: {exc})"
            )
            if not has_sc_key:
                sys.stderr.write("\n")
                state = reddit.classify_run_failure(str(exc))
                raise SourceRunError(f"Reddit public search failed: {exc}", state) from exc
            sys.stderr.write(", using ScrapeCreators backup\n")
        # Enough free results, or no key to backfill with -> done. max(min_items,
        # 1) keeps the default (min_items=0) as empty-only AND treats exactly
        # `min_items` results as acceptable (no backfill) for min_items > 0.
        if len(public_results) >= max(min_items, 1) or not has_sc_key:
            return public_results, {}
        if public_results:
            sys.stderr.write(
                f"[Reddit] Free path returned {len(public_results)} "
                f"(below the {min_items}-item floor); backfilling with ScrapeCreators\n"
            )
        try:
            result = reddit.search_and_enrich(
                reddit_query, from_date, to_date, depth=depth,
                token=config.get("SCRAPECREATORS_API_KEY"),
                subreddits=subreddits,
            )
            sc_items = reddit.parse_reddit_response(result)
        except Exception as exc:
            sys.stderr.write(
                f"[Reddit] ScrapeCreators backup also failed "
                f"({type(exc).__name__}: {exc})\n"
            )
            state = reddit.classify_run_failure(str(exc))
            return public_results, _outcome_artifact(
                state,
                f"Reddit backup failed after {len(public_results)} public items: {exc}",
            )
        merged = _merge_reddit_items(public_results, sc_items)
        if public_failure is not None:
            state = reddit.classify_run_failure(str(public_failure))
            return merged, _outcome_artifact(
                state,
                f"Reddit public search failed; backup returned {len(sc_items)} items: "
                f"{public_failure}",
            )
        return merged, {}
    if source == "x":
        # One X source, an ordered chain of interchangeable backends. Try the
        # primary; fall through to the next only if it returns nothing or errors.
        chain = env.x_backend_chain(config)
        # Trust an explicit runtime backend as the primary (already resolved as
        # available), keeping the rest of the chain as failover backups.
        pinned = runtime.x_search_backend
        if pinned:
            chain = [pinned] + [b for b in chain if b != pinned]
        if not chain:
            raise RuntimeError("No X backend is available.")
        last_error = ""
        for i, backend in enumerate(chain):
            items, err = _fetch_x_backend(backend, subquery, from_date, to_date, depth, config)
            if items:
                if i > 0:
                    print(f"[X] primary backend(s) returned nothing; used fallback '{backend}'", file=sys.stderr)
                if last_error:
                    state = (
                        bird_x.classify_run_failure(last_error)
                        if last_error.startswith("bird:")
                        else http.classify_failure(message=last_error)
                    )
                    return items, _outcome_artifact(
                        state,
                        f"X fallback '{backend}' returned {len(items)} items after {last_error}",
                    )
                return items, {}
            if err:
                last_error = f"{backend}: {err}"
                print(f"[X] backend '{backend}' failed ({err}); trying next", file=sys.stderr)
        if last_error:
            state = (
                bird_x.classify_run_failure(last_error)
                if last_error.startswith("bird:")
                else http.classify_failure(message=last_error)
            )
            raise SourceRunError(f"All X backends failed — {last_error}", state)
        return [], {}
    if source == "youtube":
        # Use raw_topic so expand_youtube_queries() generates diverse variants
        # from the original user topic, not the planner's narrowed search_query.
        yt_query = raw_topic or subquery.search_query
        result = None
        youtube_failure: str | None = None
        # ScrapeCreators key (when present) is the default-on backup tier: it
        # powers the per-video transcript fallback, the SC search fallback, and
        # comment enrichment. None when no key, which keeps everything keyless.
        sc_token = (
            config.get("SCRAPECREATORS_API_KEY", "")
            if env.is_youtube_sc_available(config) else None
        )
        # Try yt-dlp first; the SC transcript fallback covers per-video failures.
        if which("yt-dlp"):
            try:
                result = youtube_yt.search_and_transcribe(
                    yt_query, from_date, to_date, depth=depth, token=sc_token,
                )
                if result.get("error"):
                    youtube_failure = str(result["error"])
            except Exception as exc:
                youtube_failure = str(exc)
                result = None
        # Fall back to SC YouTube search if yt-dlp failed or isn't installed.
        if (result is None or not result.get("items")) and sc_token:
            try:
                result = youtube_yt.search_youtube_sc(
                    yt_query, from_date, to_date, depth=depth, token=sc_token,
                )
                if result.get("error"):
                    youtube_failure = str(result["error"])
            except Exception as exc:
                youtube_failure = str(exc)
                result = None
        if result is None:
            result = {"items": []}
        # Enrich top videos with comments (default-on when a key is present).
        items = youtube_yt.parse_youtube_response(result)
        if items and env.is_youtube_comments_available(config):
            youtube_yt.enrich_with_comments(
                items, token=config.get("SCRAPECREATORS_API_KEY", ""),
            )
        if youtube_failure:
            state = youtube_yt.classify_run_failure(youtube_failure)
            attempted = state != schema.SKIPPED_UNCONFIGURED
            return items, _outcome_artifact(state, youtube_failure, attempted=attempted)
        return items, {}
    if source == "tiktok":
        # Use raw_topic so expand_tiktok_queries() generates diverse variants
        # from the original user topic, not the planner's narrowed search_query.
        tiktok_query = raw_topic or subquery.search_query
        result = tiktok.search_and_enrich(
            tiktok_query,
            from_date,
            to_date,
            depth=depth,
            token=env.get_tiktok_token(config),
            hashtags=tiktok_hashtags,
            creators=tiktok_creators,
        )
        items = tiktok.parse_tiktok_response(result)
        if items and env.is_tiktok_comments_available(config):
            sc_token = config.get("SCRAPECREATORS_API_KEY", "")
            tiktok.enrich_with_comments(items, token=sc_token)
        return items, _result_outcome_artifact(source, result)
    if source == "instagram":
        # Use raw_topic so expand_instagram_queries() generates diverse variants
        # from the original user topic, not the planner's narrowed search_query.
        ig_query = raw_topic or subquery.search_query
        result = instagram.search_and_enrich(
            ig_query,
            from_date,
            to_date,
            depth=depth,
            token=env.get_instagram_token(config),
            ig_creators=ig_creators,
        )
        items = instagram.parse_instagram_response(result)
        if items and env.is_instagram_comments_available(config):
            instagram.enrich_with_comments(
                items, token=config.get("SCRAPECREATORS_API_KEY", ""),
            )
        return items, _result_outcome_artifact(source, result)
    if source == "linkedin":
        token = config.get("SCRAPECREATORS_API_KEY", "")
        result = linkedin.search_linkedin(
            subquery.search_query,
            from_date,
            to_date,
            depth=depth,
            token=token,
        )
        items = linkedin.parse_linkedin_response(
            result, from_date=from_date, to_date=to_date
        )
        # Articles never appear in post search — surface them (high signal)
        # via a bounded profile-enrichment lane on person topics.
        items += linkedin.enrich_articles(
            items, raw_topic or topic, token, from_date=from_date, to_date=to_date
        )
        return items, _result_outcome_artifact(source, result)
    if source == "hackernews":
        result = hackernews.search_hackernews(subquery.search_query, from_date, to_date, depth=depth)
        return (
            hackernews.parse_hackernews_response(result, query=subquery.search_query),
            _result_outcome_artifact(source, result),
        )
    if source == "stocktwits":
        # Pass raw_topic so symbol detection sees the full topic, not the
        # narrowed per-subquery search_query (same rationale as reddit).
        result = stocktwits.search_stocktwits(
            raw_topic or topic or subquery.search_query, from_date, to_date, depth=depth)
        return (
            stocktwits.parse_stocktwits_response(result, query=subquery.search_query),
            _result_outcome_artifact(source, result),
        )
    if source == "dripstack":
        result = dripstack.search_dripstack(
            subquery.search_query, from_date, to_date, depth=depth)
        relevance_topic = raw_topic or topic or subquery.search_query
        return (
            dripstack.parse_dripstack_response(result, query=relevance_topic),
            _result_outcome_artifact(source, result),
        )
    if source == "digg":
        result = digg.search_digg(subquery.search_query, from_date, to_date, depth=depth)
        items = digg.parse_digg_response(result, query=subquery.search_query)
        # Enrichment with attached X posts is deferred to
        # _finalize_items_by_source so it runs on the items that actually
        # survive dedupe rather than on top-K of the raw fanout.
        return items, _result_outcome_artifact(source, result)
    if source == "arxiv":
        result = arxiv.search_arxiv(subquery.search_query, from_date, to_date, depth=depth)
        # Relevance keys off the stable research topic, not the per-subquery
        # search_query, so off-topic narrowing does not let weak matches through.
        relevance_topic = raw_topic or topic or subquery.search_query
        return (
            arxiv.parse_arxiv_response(result, query=relevance_topic),
            _result_outcome_artifact(source, result),
        )
    if source == "techmeme":
        result = techmeme.search_techmeme(subquery.search_query, from_date, to_date, depth=depth)
        relevance_topic = raw_topic or topic or subquery.search_query
        return (
            techmeme.parse_techmeme_response(result, query=relevance_topic),
            _result_outcome_artifact(source, result),
        )
    if source == "trustpilot":
        # Brand-shape gate keys off the stable research topic, not the narrowed
        # per-subquery search_query, so the company is detected consistently.
        relevance_topic = raw_topic or topic or subquery.search_query
        result = trustpilot.search_trustpilot(
            relevance_topic, from_date, to_date, depth=depth, config=config,
            explicit_domain=trustpilot_domain,
            domain_is_hint=trustpilot_domain_is_hint,
        )
        return (
            trustpilot.parse_trustpilot_response(result, query=relevance_topic),
            _result_outcome_artifact(source, result),
        )
    if source == "bluesky":
        result = bluesky.search_bluesky(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return bluesky.parse_bluesky_response(result), _result_outcome_artifact(source, result)
    if source == "threads":
        result = threads.search_threads(
            subquery.search_query, from_date, to_date,
            depth=depth,
            token=config.get("SCRAPECREATORS_API_KEY"),
        )
        return threads.parse_threads_response(result), _result_outcome_artifact(source, result)
    if source == "truthsocial":
        result = truthsocial.search_truthsocial(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return truthsocial.parse_truthsocial_response(result), _result_outcome_artifact(source, result)
    if source == "polymarket":
        result = polymarket.search_polymarket(subquery.search_query, from_date, to_date, depth=depth)
        # Relevance filtering keys off the stable original research topic, not the
        # per-subquery search_query (which narrows differently on each fanout pass
        # and would let off-topic markets through on broad subqueries while dropping
        # everything on narrow ones).
        relevance_topic = raw_topic or topic or subquery.search_query
        return (
            polymarket.parse_polymarket_response(result, topic=relevance_topic),
            _result_outcome_artifact(source, result),
        )
    if source == "github":
        # Resolve once at the pipeline boundary so search and enrich
        # share the result; otherwise each call would re-run the env
        # lookup and gh-CLI subprocess fallback (up to 5s timeout each).
        token = github.resolve_token(config.get("GITHUB_TOKEN"))
        response = github.search_github(subquery.search_query, from_date, to_date, depth=depth, token=token)
        items = github.parse_github_response(response)
        # Note: an unauth rate-limit (response["error"]) is expected on the
        # tokenless anon tier and returns empty here rather than raising — github
        # is now always eligible, so raising would spam "github failed" on every
        # tokenless run. The condition is logged in github.search_github.
        items = github.enrich_with_comments(items, depth=depth, token=token)
        return items, _result_outcome_artifact(source, response)
    if source == "pinterest":
        result = pinterest.search_pinterest(
            subquery.search_query, from_date, to_date,
            depth=depth,
            token=env.get_pinterest_token(config),
        )
        return pinterest.parse_pinterest_response(result), _result_outcome_artifact(source, result)
    if source == "xiaohongshu":
        return xiaohongshu_api.search_feeds(
            subquery.search_query,
            from_date,
            to_date,
            env.get_xiaohongshu_api_base(config),
            depth=depth,
        ), {}
    if source == "perplexity":
        return perplexity.search(subquery.search_query, date_range, config, deep=config.get("_deep_research", False))
    raise RuntimeError(f"Unsupported source: {source}")


def _google_key(config: dict[str, Any]) -> str | None:
    return config.get("GOOGLE_API_KEY") or config.get("GEMINI_API_KEY") or config.get("GOOGLE_GENAI_API_KEY")




def _mock_stream_results(source: str, subquery: schema.SubQuery) -> tuple[list[dict], dict]:
    payloads = {
        "reddit": [
            {
                "id": "R1",
                "title": f"{subquery.search_query} discussion thread",
                "url": "https://reddit.com/r/example/comments/1",
                "subreddit": "example",
                "date": dates.get_date_range(5)[0],
                "engagement": {"score": 120, "num_comments": 48, "upvote_ratio": 0.91},
                "selftext": f"Community discussion about {subquery.search_query}.",
                "top_comments": [{"excerpt": "Strong firsthand feedback from users."}],
                "relevance": 0.82,
                "why_relevant": "Mock Reddit result",
            }
        ],
        "x": [
            {
                "id": "X1",
                "text": f"People on X are discussing {subquery.search_query} right now.",
                "url": "https://x.com/example/status/1",
                "author_handle": "example",
                "date": dates.get_date_range(2)[0],
                "engagement": {"likes": 200, "reposts": 35, "replies": 18, "quotes": 4},
                "relevance": 0.79,
                "why_relevant": "Mock X result",
            }
        ],
        "grounding": [
            {
                "id": "WB1",
                "title": f"{subquery.search_query} article",
                "url": "https://example.com/article",
                "source_domain": "example.com",
                "snippet": f"Recent web reporting about {subquery.search_query}.",
                "date": dates.get_date_range(7)[0],
                "relevance": 0.88,
                "why_relevant": "Brave web search",
            }
        ],
        "digg": [
            {
                "id": "mock1abc",
                "title": f"Digg cluster about {subquery.search_query}",
                "url": "https://di.gg/ai/mock1abc",
                "tldr": f"Curated cluster summarizing recent {subquery.search_query} discussion across the AI 1000.",
                "author": "",
                "date": dates.get_date_range(3)[0],
                "engagement": {"postCount": 8, "uniqueAuthors": 5, "rank": 2, "rank_score": 49.0},
                "first_post_age": "3d",
                "posts": [
                    {
                        "username": "exampledev",
                        "display_name": "Example Dev",
                        "category": "Engineer",
                        "rank": 142,
                        "body": f"Quote from the AI 1000 about {subquery.search_query}.",
                        "post_type": "tweet",
                        "x_url": "https://x.com/exampledev/status/1",
                        "posted_at": dates.get_date_range(3)[0],
                    },
                ],
                "relevance": 0.84,
                "why_relevant": "Mock Digg cluster",
            },
            {
                "id": "mock2def",
                "title": f"Second Digg cluster on {subquery.search_query}",
                "url": "https://di.gg/ai/mock2def",
                "tldr": f"Another angle on {subquery.search_query}.",
                "author": "",
                "date": dates.get_date_range(8)[0],
                "engagement": {"postCount": 3, "uniqueAuthors": 2, "rank": 18, "rank_score": 33.0},
                "first_post_age": "8d",
                "posts": [],
                "relevance": 0.71,
                "why_relevant": "Mock Digg cluster",
            },
        ],
        "arxiv": [
            {
                "id": "http://arxiv.org/abs/2606.00001v1",
                "title": f"A Survey of {subquery.search_query}",
                "url": "https://arxiv.org/abs/2606.00001v1",
                "summary": f"We present a comprehensive study of {subquery.search_query} and its recent advances.",
                "author": "Ada Lovelace et al.",
                "authors": ["Ada Lovelace", "Alan Turing"],
                "date": dates.get_date_range(20)[0],
                "engagement": {},
                "relevance": 0.86,
                "why_relevant": "Mock arXiv paper",
            },
        ],
        "techmeme": [
            {
                "id": "https://www.techmeme.com/260627/p1",
                "title": f"Major development in {subquery.search_query} reshapes the industry",
                "url": "https://www.techmeme.com/260627/p1",
                "source_name": "techcrunch.com",
                "date": dates.get_date_range(1)[0],
                "engagement": {},
                "relevance": 0.83,
                "why_relevant": "Mock Techmeme headline",
            },
        ],
        "dripstack": [
            {
                "id": "DS1",
                "title": f"Deep dive: {subquery.search_query} from a paid newsletter",
                "url": "https://newsletter.example.com/deep-dive",
                "author": "newsletter.example.com",
                "date": dates.get_date_range(3)[0],
                "engagement": {},
                "relevance": 0.85,
                "why_relevant": "Mock DripStack newsletter result",
                "snippet": f"Professional analyst coverage of {subquery.search_query}.",
                "metadata": {
                    "publication_slug": "newsletter.example.com",
                    "post_slug": "deep-dive",
                    "relevance_score": 85,
                    "match_confidence": "strong",
                },
            },
        ],
        "trustpilot": [
            {
                "id": "example.com",
                "title": f"{subquery.search_query}: TrustScore 3.4",
                "url": "https://www.trustpilot.com/review/example.com",
                "summary": f"Across recent reviews, customers were split on {subquery.search_query}: some praised support, others cited delays.",
                "name": subquery.search_query,
                "trustScore": 3.4,
                "reviewCount": 128,
                "date": dates.get_date_range(1)[0],
                "engagement": {"reviews": 128, "trustScore": 3.4},
                "relevance": 0.8,
                "why_relevant": "Mock Trustpilot sentiment",
            },
        ],
        "jobs": [
            {
                "id": "J1",
                "title": "Founding Enterprise Solutions Engineer",
                "url": "https://boards.greenhouse.io/example/jobs/1",
                "description": (
                    f"Work with enterprise customers on SSO, SOC 2, security, "
                    f"and procurement workflows for {subquery.search_query}."
                ),
                "department": "Sales",
                "location": "San Francisco, CA",
                "date": dates.get_date_range(4)[0],
                "provider": "mock",
                "relevance": 0.8,
                "why_relevant": "Mock public job posting",
            },
            {
                "id": "J2",
                "title": "Security Platform Engineer",
                "url": "https://boards.greenhouse.io/example/jobs/2",
                "description": "Build enterprise security, audit, and admin workflows.",
                "department": "Engineering",
                "location": "Remote",
                "date": dates.get_date_range(6)[0],
                "provider": "mock",
                "relevance": 0.78,
                "why_relevant": "Mock public job posting",
            },
        ],
    }
    if source == "grounding":
        return payloads.get(source, []), {
            "label": subquery.label,
            "mock": True,
            "webSearchQueries": [subquery.search_query],
            "resultCount": 1,
        }
    return payloads.get(source, []), {}
