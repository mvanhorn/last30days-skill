"""Core data model for the v3.0.0 last30days pipeline."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from . import health


def _drop_none(value: Any) -> Any:
    """Recursively remove None values from dataclass-derived structures."""
    if is_dataclass(value):
        return _drop_none(asdict(value))
    if isinstance(value, dict):
        return {
            key: _drop_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class ProviderRuntime:
    """Resolved runtime provider selection."""

    reasoning_provider: Literal["gemini", "openai", "xai", "local"]
    planner_model: str
    rerank_model: str
    x_search_backend: Literal["xai", "bird"] | None = None


@dataclass(frozen=True)
class SubQuery:
    """Planner-emitted retrieval unit."""

    label: str
    search_query: str
    ranking_query: str
    sources: list[str]
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("SubQuery must have at least one source")
        if self.weight <= 0:
            raise ValueError(f"SubQuery weight must be positive, got {self.weight}")


@dataclass
class QueryPlan:
    """Planner output."""

    intent: str
    freshness_mode: str
    cluster_mode: str
    raw_topic: str
    subqueries: list[SubQuery]
    source_weights: dict[str, float]
    notes: list[str] = field(default_factory=list)


@dataclass
class SourceItem:
    """Generic normalized evidence item."""

    item_id: str
    source: str
    title: str
    body: str
    url: str
    author: str | None = None
    container: str | None = None
    published_at: str | None = None
    date_confidence: Literal["high", "med", "low"] = "low"
    engagement: dict[str, float | int] = field(default_factory=dict)
    relevance_hint: float = 0.5
    why_relevant: str = ""
    snippet: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Signal fields populated by signals.annotate_stream (after construction)
    local_relevance: float | None = None
    freshness: int | None = None
    engagement_score: float | None = None
    source_quality: float | None = None
    local_rank_score: float | None = None


@dataclass
class Candidate:
    """Global candidate after fusion and reranking."""

    candidate_id: str
    item_id: str
    source: str
    title: str
    url: str
    snippet: str
    subquery_labels: list[str]
    native_ranks: dict[str, int]
    local_relevance: float
    freshness: int
    engagement: int | float | None
    source_quality: float
    rrf_score: float
    sources: list[str] = field(default_factory=list)
    source_items: list[SourceItem] = field(default_factory=list)
    rerank_score: float | None = None
    final_score: float = 0.0
    explanation: str | None = None
    fun_score: float | None = None
    fun_explanation: str | None = None
    cluster_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Cluster:
    """Ranked cluster of related candidates."""

    cluster_id: str
    title: str
    candidate_ids: list[str]
    representative_ids: list[str]
    sources: list[str]
    score: float
    uncertainty: Literal["single-source", "thin-evidence"] | None = None

    def __post_init__(self) -> None:
        if not set(self.representative_ids) <= set(self.candidate_ids):
            raise ValueError("representative_ids must be a subset of candidate_ids")


RunOutcomeState = Literal[
    "ok",
    "no-results",
    "partial",
    "rate-limited",
    "auth-failed",
    "unreachable",
    "timeout",
    "schema-drift",
    "skipped-unconfigured",
    "error",
]

FreshnessVerdictState = Literal[
    "current",
    "stale",
    "contradicted",
    "unsupported",
]

NO_RESULTS = health.NO_RESULTS
PARTIAL = health.PARTIAL
RATE_LIMITED = health.RATE_LIMITED
AUTH_FAILED = health.AUTH_FAILED
UNREACHABLE = health.UNREACHABLE
SCHEMA_DRIFT = health.SCHEMA_DRIFT
SKIPPED_UNCONFIGURED = health.SKIPPED_UNCONFIGURED


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SourceOutcome:
    """What happened to one source during this run.

    Doctor predicts whether a source is configured and healthy before a run;
    this records the observed retrieval result. Shared states reuse
    ``health.py`` values (``ok``, ``timeout``, ``error``), while the remaining
    states describe run-only outcomes.
    """

    source: str
    state: RunOutcomeState
    items_returned: int = 0
    attempted: bool = True
    detail: str | None = None
    at: str = field(default_factory=_utc_now)
    fix_hint: str | None = None

    def __post_init__(self) -> None:
        valid_states = {
            health.OK,
            health.TIMEOUT,
            health.ERROR,
            NO_RESULTS,
            PARTIAL,
            RATE_LIMITED,
            AUTH_FAILED,
            UNREACHABLE,
            SCHEMA_DRIFT,
            SKIPPED_UNCONFIGURED,
        }
        if self.state not in valid_states:
            raise ValueError(f"Unknown source outcome state: {self.state}")
        if self.items_returned < 0:
            raise ValueError("items_returned cannot be negative")


@dataclass(frozen=True)
class FreshnessVerdict:
    """Act-time verification result for one source-grounded claim."""

    claim_id: str
    candidate_id: str
    claim: str
    source: str
    source_item_id: str
    verdict: FreshnessVerdictState
    checked_at: str
    source_url: str = ""
    source_timestamp: str | None = None
    evidence_url: str = ""
    evidence_timestamp: str | None = None
    original_value: Any = None
    current_value: Any = None
    detail: str | None = None


@dataclass(frozen=True)
class LibraryContext:
    """One prior research run relevant to the current report."""

    topic: str
    published_date: str
    headline: str
    summary: str
    source_kind: Literal["brief", "store"]


@dataclass
class Report:
    """Final pipeline output."""

    topic: str
    range_from: str
    range_to: str
    generated_at: str
    provider_runtime: ProviderRuntime
    query_plan: QueryPlan
    clusters: list[Cluster]
    ranked_candidates: list[Candidate]
    items_by_source: dict[str, list[SourceItem]]
    errors_by_source: dict[str, str]
    source_status: dict[str, SourceOutcome] = field(default_factory=dict)
    freshness_verdicts: list[FreshnessVerdict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    library_context: list[LibraryContext] = field(default_factory=list)
    drill_of: str | None = None


@dataclass(frozen=True)
class DiscoveryPlan:
    """Topic-less listing feeds selected for a domain sweep."""

    domain: str
    category: str | None
    subreddits: list[str]
    sources: list[str]


@dataclass(frozen=True)
class DiscoveryTopic:
    """One engagement-ranked topic produced by a discovery sweep."""

    rank: int
    name: str
    why_spiking: str
    momentum: Literal["new-this-week", "building"]
    velocity_score: float
    sources: list[str]
    engagement_by_source: dict[str, dict[str, float | int]]
    command: str
    evidence_urls: list[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    """Versioned result of a domain-level listing sweep."""

    domain: str
    range_from: str
    range_to: str
    generated_at: str
    plan: DiscoveryPlan
    topics: list[DiscoveryTopic]
    source_status: dict[str, SourceOutcome] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RetrievalBundle:
    """Structured retrieval output before global ranking."""

    items_by_source_and_query: dict[tuple[str, str], list[SourceItem]] = field(default_factory=dict)
    items_by_source: dict[str, list[SourceItem]] = field(default_factory=dict)
    errors_by_source: dict[str, str] = field(default_factory=dict)
    source_status: dict[str, SourceOutcome] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def mark_attempted(self, source: str) -> None:
        """Register a planned source before its first retrieval starts."""
        self.source_status.setdefault(
            source,
            SourceOutcome(source=source, state=NO_RESULTS),
        )

    def record_failure(
        self,
        source: str,
        state: RunOutcomeState,
        detail: str,
        *,
        attempted: bool = True,
    ) -> None:
        """Record a failure, preserving already-returned items as partial."""
        count = len(self.items_by_source.get(source, []))
        outcome_state: RunOutcomeState = PARTIAL if count else state
        self.errors_by_source.setdefault(source, detail)
        self.source_status[source] = SourceOutcome(
            source=source,
            state=outcome_state,
            items_returned=count,
            attempted=attempted,
            detail=detail,
            fix_hint="doctor",
        )

    def add_items(self, label: str, source: str, items: list[SourceItem]) -> None:
        """Atomically append items to both items_by_source_and_query and items_by_source."""
        self.items_by_source_and_query.setdefault((label, source), []).extend(items)
        self.items_by_source.setdefault(source, []).extend(items)
        previous = self.source_status.get(source)
        state: RunOutcomeState = health.OK if items else NO_RESULTS
        detail = None
        fix_hint = None
        if previous and previous.state not in (health.OK, NO_RESULTS):
            state = PARTIAL if self.items_by_source[source] else previous.state
            detail = previous.detail
            fix_hint = previous.fix_hint
        self.source_status[source] = SourceOutcome(
            source=source,
            state=state,
            items_returned=len(self.items_by_source[source]),
            attempted=True,
            detail=detail,
            fix_hint=fix_hint,
        )


def to_dict(value: Any) -> Any:
    """Serialize dataclasses and nested containers."""
    return _drop_none(value)


def provider_runtime_from_dict(payload: dict[str, Any]) -> ProviderRuntime:
    return ProviderRuntime(
        reasoning_provider=payload["reasoning_provider"],
        planner_model=payload["planner_model"],
        rerank_model=payload["rerank_model"],
        x_search_backend=payload.get("x_search_backend"),
    )


def subquery_from_dict(payload: dict[str, Any]) -> SubQuery:
    return SubQuery(
        label=payload["label"],
        search_query=payload["search_query"],
        ranking_query=payload["ranking_query"],
        sources=list(payload.get("sources") or []),
        weight=float(payload.get("weight") or 1.0),
    )


def query_plan_from_dict(payload: dict[str, Any]) -> QueryPlan:
    return QueryPlan(
        intent=payload["intent"],
        freshness_mode=payload["freshness_mode"],
        cluster_mode=payload["cluster_mode"],
        raw_topic=payload["raw_topic"],
        subqueries=[subquery_from_dict(item) for item in payload.get("subqueries") or []],
        source_weights=dict(payload.get("source_weights") or {}),
        notes=list(payload.get("notes") or []),
    )


def source_item_from_dict(payload: dict[str, Any]) -> SourceItem:
    meta = payload.get("metadata") or {}
    return SourceItem(
        item_id=payload["item_id"],
        source=payload["source"],
        title=payload["title"],
        body=payload.get("body") or "",
        url=payload.get("url") or "",
        author=payload.get("author"),
        container=payload.get("container"),
        published_at=payload.get("published_at"),
        date_confidence=payload.get("date_confidence") or "low",
        engagement=dict(payload.get("engagement") or {}),
        relevance_hint=float(_first_non_none(payload.get("relevance_hint"), 0.5)),
        why_relevant=payload.get("why_relevant") or "",
        snippet=payload.get("snippet") or "",
        metadata=dict(meta),
        local_relevance=_first_non_none(payload.get("local_relevance"), meta.get("local_relevance")),
        freshness=_first_non_none(payload.get("freshness"), meta.get("freshness")),
        engagement_score=_first_non_none(payload.get("engagement_score"), meta.get("engagement_score")),
        source_quality=_first_non_none(payload.get("source_quality"), meta.get("source_quality")),
        local_rank_score=_first_non_none(payload.get("local_rank_score"), meta.get("local_rank_score")),
    )


def candidate_from_dict(payload: dict[str, Any]) -> Candidate:
    return Candidate(
        candidate_id=payload["candidate_id"],
        item_id=payload["item_id"],
        source=payload["source"],
        title=payload["title"],
        url=payload.get("url") or "",
        snippet=payload.get("snippet") or "",
        subquery_labels=list(payload.get("subquery_labels") or []),
        native_ranks={key: int(value) for key, value in (payload.get("native_ranks") or {}).items()},
        local_relevance=float(_first_non_none(payload.get("local_relevance"), 0.0)),
        freshness=int(_first_non_none(payload.get("freshness"), 0)),
        engagement=payload.get("engagement"),
        source_quality=float(_first_non_none(payload.get("source_quality"), 0.0)),
        rrf_score=float(_first_non_none(payload.get("rrf_score"), 0.0)),
        sources=list(payload.get("sources") or []),
        source_items=[source_item_from_dict(item) for item in payload.get("source_items") or []],
        rerank_score=float(payload["rerank_score"]) if payload.get("rerank_score") is not None else None,
        final_score=float(_first_non_none(payload.get("final_score"), 0.0)),
        explanation=payload.get("explanation"),
        fun_score=float(payload["fun_score"]) if payload.get("fun_score") is not None else None,
        fun_explanation=payload.get("fun_explanation"),
        cluster_id=payload.get("cluster_id"),
        metadata=dict(payload.get("metadata") or {}),
    )


def cluster_from_dict(payload: dict[str, Any]) -> Cluster:
    return Cluster(
        cluster_id=payload["cluster_id"],
        title=payload["title"],
        candidate_ids=list(payload.get("candidate_ids") or []),
        representative_ids=list(payload.get("representative_ids") or []),
        sources=list(payload.get("sources") or []),
        score=float(_first_non_none(payload.get("score"), 0.0)),
        uncertainty=payload.get("uncertainty"),
    )


def report_from_dict(payload: dict[str, Any]) -> Report:
    return Report(
        topic=payload["topic"],
        range_from=payload["range_from"],
        range_to=payload["range_to"],
        generated_at=payload["generated_at"],
        provider_runtime=provider_runtime_from_dict(payload["provider_runtime"]),
        query_plan=query_plan_from_dict(payload["query_plan"]),
        clusters=[cluster_from_dict(item) for item in payload.get("clusters") or []],
        ranked_candidates=[candidate_from_dict(item) for item in payload.get("ranked_candidates") or []],
        items_by_source={
            source: [source_item_from_dict(item) for item in items]
            for source, items in (payload.get("items_by_source") or {}).items()
        },
        errors_by_source=dict(payload.get("errors_by_source") or {}),
        source_status={
            source: SourceOutcome(
                source=outcome.get("source") or source,
                state=outcome["state"],
                items_returned=int(outcome.get("items_returned") or 0),
                attempted=bool(outcome.get("attempted", True)),
                detail=outcome.get("detail"),
                at=outcome.get("at") or _utc_now(),
                fix_hint=outcome.get("fix_hint"),
            )
            for source, outcome in (payload.get("source_status") or {}).items()
        },
        freshness_verdicts=[
            FreshnessVerdict(
                claim_id=item["claim_id"],
                candidate_id=item["candidate_id"],
                claim=item["claim"],
                source=item["source"],
                source_item_id=item["source_item_id"],
                verdict=item["verdict"],
                checked_at=item["checked_at"],
                source_url=item.get("source_url") or "",
                source_timestamp=item.get("source_timestamp"),
                evidence_url=item.get("evidence_url") or "",
                evidence_timestamp=item.get("evidence_timestamp"),
                original_value=item.get("original_value"),
                current_value=item.get("current_value"),
                detail=item.get("detail"),
            )
            for item in (payload.get("freshness_verdicts") or [])
            if isinstance(item, dict)
        ],
        warnings=list(payload.get("warnings") or []),
        artifacts=dict(payload.get("artifacts") or {}),
        library_context=[
            LibraryContext(
                topic=str(item.get("topic") or ""),
                published_date=str(item.get("published_date") or ""),
                headline=str(item.get("headline") or ""),
                summary=str(item.get("summary") or ""),
                source_kind=(
                    "store" if item.get("source_kind") == "store" else "brief"
                ),
            )
            for item in (payload.get("library_context") or [])
            if isinstance(item, dict)
        ],
        drill_of=payload.get("drill_of"),
    )


def candidate_sources(candidate: Candidate) -> list[str]:
    if candidate.sources:
        return candidate.sources
    return [candidate.source] if candidate.source else []


def candidate_source_label(candidate: Candidate) -> str:
    sources = candidate_sources(candidate)
    return ", ".join(sources) if sources else "unknown"


def candidate_best_published_at(candidate: Candidate) -> str | None:
    return max(
        (item.published_at for item in candidate.source_items if item.published_at),
        default=None,
    )


def candidate_primary_item(candidate: Candidate) -> SourceItem | None:
    if not candidate.source_items:
        return None
    for item in candidate.source_items:
        if item.source == candidate.source:
            return item
    return candidate.source_items[0]


AGENT_EXPORT_SCHEMA_VERSION = "1.2"


def without_sources(report: Report, excluded_sources: set[str]) -> Report:
    """Return a deep-copied report with private source evidence removed.

    This is the publication boundary used by agent JSON, hosted HTML, and
    future outbound surfaces. Cluster titles are rebuilt when a removed item
    participated so text derived from a private representative cannot survive
    after its candidate is gone.
    """
    excluded = {source.lower() for source in excluded_sources}
    if not excluded:
        return copy.deepcopy(report)
    clean = copy.deepcopy(report)
    clean.items_by_source = {
        source: items
        for source, items in clean.items_by_source.items()
        if source.lower() not in excluded
    }
    clean.errors_by_source = {
        source: detail
        for source, detail in clean.errors_by_source.items()
        if source.lower() not in excluded
    }
    clean.source_status = {
        source: outcome
        for source, outcome in clean.source_status.items()
        if source.lower() not in excluded
    }
    clean.query_plan.source_weights = {
        source: weight
        for source, weight in clean.query_plan.source_weights.items()
        if source.lower() not in excluded
    }
    for subquery in clean.query_plan.subqueries:
        subquery.sources[:] = [
            source for source in subquery.sources if source.lower() not in excluded
        ]

    kept_candidates: list[Candidate] = []
    removed_candidate_ids: set[str] = set()
    for candidate in clean.ranked_candidates:
        if candidate.source.lower() in excluded:
            removed_candidate_ids.add(candidate.candidate_id)
            continue
        candidate.source_items = [
            item for item in candidate.source_items if item.source.lower() not in excluded
        ]
        candidate.sources = [
            source for source in candidate.sources if source.lower() not in excluded
        ]
        candidate.native_ranks = {
            key: rank
            for key, rank in candidate.native_ranks.items()
            if key.rsplit(":", 1)[-1].lower() not in excluded
        }
        kept_candidates.append(candidate)
    clean.ranked_candidates = kept_candidates
    candidate_by_id = {
        candidate.candidate_id: candidate for candidate in clean.ranked_candidates
    }

    kept_clusters: list[Cluster] = []
    for cluster in clean.clusters:
        original_ids = list(cluster.candidate_ids)
        cluster.candidate_ids = [
            candidate_id for candidate_id in original_ids if candidate_id in candidate_by_id
        ]
        if not cluster.candidate_ids:
            continue
        cluster.representative_ids = [
            candidate_id
            for candidate_id in cluster.representative_ids
            if candidate_id in candidate_by_id
        ] or [cluster.candidate_ids[0]]
        cluster.sources = sorted({
            source
            for candidate_id in cluster.candidate_ids
            for source in candidate_sources(candidate_by_id[candidate_id])
            if source.lower() not in excluded
        })
        if any(candidate_id in removed_candidate_ids for candidate_id in original_ids):
            cluster.title = candidate_by_id[cluster.representative_ids[0]].title
        kept_clusters.append(cluster)
    clean.clusters = kept_clusters
    clean.freshness_verdicts = [
        verdict
        for verdict in clean.freshness_verdicts
        if verdict.source.lower() not in excluded
        and verdict.candidate_id in candidate_by_id
    ]
    for key in list(clean.artifacts):
        if any(source in key.lower() for source in excluded):
            del clean.artifacts[key]
    return clean


DISCOVERY_EXPORT_SCHEMA_VERSION = "1.0"


def _agent_summary(candidate: Candidate) -> str:
    primary = candidate_primary_item(candidate)
    return (
        candidate.snippet
        or (primary.snippet if primary else "")
        or candidate.explanation
        or (primary.body if primary else "")
    )


def _agent_engagement(candidate: Candidate) -> dict[str, float | int]:
    primary = candidate_primary_item(candidate)
    return dict(primary.engagement) if primary else {}


_HEADLINE_ENGAGEMENT_FIELDS_BY_SOURCE = {
    "digg": ("postCount",),
    "reddit": ("score",),
    "stocktwits": ("likes", "reshares"),
}


def _is_counter_field(field: str) -> bool:
    normalized = field.lower()
    return not (
        # Author-reach and position/score metadata, not per-item engagement.
        normalized in {"rank", "rating", "score", "trustscore", "followers", "subscribers"}
        or normalized.endswith(("_rank", "_score", "_ratio", "_rate", "_followers"))
    )


def _headline_engagement(candidate: Candidate) -> float:
    """Return the primary item's largest native engagement counter."""
    engagement = _agent_engagement(candidate)
    preferred_fields = _HEADLINE_ENGAGEMENT_FIELDS_BY_SOURCE.get(candidate.source, ())
    preferred_values = [
        float(engagement[field])
        for field in preferred_fields
        if isinstance(engagement.get(field), (int, float))
        and not isinstance(engagement[field], bool)
    ]
    if preferred_values:
        return max(preferred_values)

    values = [
        float(value)
        for field, value in engagement.items()
        if _is_counter_field(field)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ]
    return max(values, default=0.0)


def _window_days(report: Report) -> int:
    start = datetime.fromisoformat(report.range_from).date()
    end = datetime.fromisoformat(report.range_to).date()
    return max(0, (end - start).days)


def _agent_generated_at(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_agent_export(
    report: Report,
    *,
    corpus_in_export: bool | None = None,
) -> dict[str, Any]:
    """Serialize a report to the stable, versioned agent JSON contract.

    Local corpus evidence is private by default. Callers must opt in explicitly
    either with ``corpus_in_export=True`` or the CLI-populated report artifact.
    """
    if corpus_in_export is None:
        corpus_in_export = bool(report.artifacts.get("corpus_in_export"))
    if not corpus_in_export:
        report = without_sources(report, {"corpus"})
    candidates = {candidate.candidate_id: candidate for candidate in report.ranked_candidates}
    cluster_by_candidate: dict[str, int] = {}
    cluster_by_id: dict[str, int] = {}
    exported_clusters: list[dict[str, Any]] = []

    for index, cluster in enumerate(report.clusters):
        cluster_by_id[cluster.cluster_id] = index
        for candidate_id in cluster.candidate_ids:
            cluster_by_candidate.setdefault(candidate_id, index)
        representative = next(
            (candidates[candidate_id] for candidate_id in cluster.representative_ids if candidate_id in candidates),
            None,
        )
        engagement_total = sum(
            _headline_engagement(candidates[candidate_id])
            for candidate_id in cluster.candidate_ids
            if candidate_id in candidates
        )
        exported_clusters.append(
            {
                "title": cluster.title,
                "summary": _agent_summary(representative) if representative else "",
                "sources": list(cluster.sources),
                "engagement_total": (
                    int(engagement_total) if engagement_total.is_integer() else engagement_total
                ),
            }
        )

    results: list[dict[str, Any]] = []
    for candidate in report.ranked_candidates:
        primary = candidate_primary_item(candidate)
        cluster_index = cluster_by_id.get(candidate.cluster_id or "")
        if cluster_index is None:
            cluster_index = cluster_by_candidate.get(candidate.candidate_id)
        results.append(
            _drop_none(
                {
                    "candidate_id": candidate.candidate_id,
                    "title": candidate.title,
                    "source": candidate.source,
                    "url": candidate.url,
                    "published_at": primary.published_at if primary else None,
                    "summary": _agent_summary(candidate),
                    "engagement": _agent_engagement(candidate),
                    "relevance_score": round(
                        max(0.0, min(1.0, candidate.final_score / 100.0)),
                        4,
                    ),
                    "cluster": cluster_index,
                }
            )
        )

    return {
        "schema_version": AGENT_EXPORT_SCHEMA_VERSION,
        "query": report.topic,
        "generated_at": _agent_generated_at(report.generated_at),
        "window_days": _window_days(report),
        "source_status": {
            source: outcome.state
            for source, outcome in sorted(report.source_status.items())
        },
        "freshness_verdicts": [
            _drop_none(asdict(verdict)) for verdict in report.freshness_verdicts
        ],
        "clusters": exported_clusters,
        "results": results,
    }


def to_discovery_export(report: DiscoveryReport) -> dict[str, Any]:
    """Serialize discovery output without changing the normal agent contract."""
    start = datetime.fromisoformat(report.range_from).date()
    end = datetime.fromisoformat(report.range_to).date()
    return {
        "schema_version": DISCOVERY_EXPORT_SCHEMA_VERSION,
        "kind": "discovery",
        "domain": report.domain,
        "generated_at": _agent_generated_at(report.generated_at),
        "window_days": max(0, (end - start).days),
        "source_status": {
            source: outcome.state
            for source, outcome in sorted(report.source_status.items())
        },
        "feeds": {
            "category": report.plan.category,
            "subreddits": list(report.plan.subreddits),
            "sources": list(report.plan.sources),
        },
        "results": [
            {
                "rank": topic.rank,
                "topic": topic.name,
                "why_spiking": topic.why_spiking,
                "momentum": topic.momentum,
                "velocity_score": topic.velocity_score,
                "sources": list(topic.sources),
                "engagement": topic.engagement_by_source,
                "command": topic.command,
                "evidence_urls": list(topic.evidence_urls),
            }
            for topic in report.topics
        ],
        "warnings": list(report.warnings),
    }
