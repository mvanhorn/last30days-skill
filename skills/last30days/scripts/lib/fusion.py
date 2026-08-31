"""Weighted reciprocal rank fusion for per-(subquery, source) streams."""

from __future__ import annotations

from collections.abc import Iterable

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from . import schema

# Standard RRF smoothing constant (Cormack et al. 2009)
RRF_K = 60


def _candidate_sort_key(c: schema.Candidate) -> tuple:
    # Out-of-window evidence sorts strictly below anything in the window. A
    # "last 30 days" brief that ranks a nine-month-old video at #1 breaks its
    # own contract, however relevant that video is; it still appears, just
    # never above in-window evidence.
    return (
        1 if schema.candidate_out_of_window(c) else 0,
        -c.rrf_score,
        -c.local_relevance,
        -c.freshness,
        schema.candidate_source_label(c),
        c.title,
    )


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: lowercase, strip www/old/m prefixes, remove tracking params."""
    parsed = urlparse(url.strip().lower())
    netloc = parsed.netloc
    for prefix in ("www.", "old.", "m."):
        if netloc.startswith(prefix):
            netloc = netloc[len(prefix):]
    # Strip tracking params
    params = parse_qs(parsed.query)
    clean_params = {k: v for k, v in params.items() if not k.startswith("utm_")}
    query = urlencode(clean_params, doseq=True)
    return urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", query, ""))


def candidate_key(item: schema.SourceItem) -> str:
    if item.url:
        return _normalize_url(item.url)
    return f"{item.source}:{item.item_id}"


# Enrichment that one copy of a thread may carry and another may lack. When
# the same URL arrives from two subquery streams (each stream enriches its own
# top-N), the copy that won a comment slot must survive de-duplication.
_ENRICHMENT_KEYS = (
    "top_comments",
    "comment_insights",
    "transcript_highlights",
    "transcript_snippet",
    "transcript",
)


def merge_source_items(existing: schema.SourceItem, incoming: schema.SourceItem) -> schema.SourceItem:
    """Fold ``incoming``'s enrichment and counts into ``existing`` (same thread).

    Keeps the richer value per field: an enrichment list the existing copy
    lacks (or a longer one), the larger numeric engagement counters, and the
    longer body/snippet. Mutates and returns ``existing``.
    """
    for key in _ENRICHMENT_KEYS:
        theirs = incoming.metadata.get(key)
        if not theirs:
            continue
        mine = existing.metadata.get(key)
        if not mine or (isinstance(theirs, list) and isinstance(mine, list) and len(theirs) > len(mine)):
            existing.metadata[key] = theirs
    for field_name, value in (incoming.engagement or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if existing.engagement.get(field_name) is None and value is not None:
                existing.engagement[field_name] = value
            continue
        current = existing.engagement.get(field_name)
        if not isinstance(current, (int, float)) or isinstance(current, bool) or value > current:
            existing.engagement[field_name] = value
    if len(incoming.body or "") > len(existing.body or ""):
        existing.body = incoming.body
    if len(incoming.snippet or "") > len(existing.snippet or ""):
        existing.snippet = incoming.snippet
    return existing


def collapse_duplicate_urls(items: list[schema.SourceItem]) -> list[schema.SourceItem]:
    """Collapse same-source, same-URL copies, keeping order and merging enrichment.

    Per-stream item ids (``R1``, ``X1``) collide across subqueries, so identity
    is the normalized URL, not the id. The first occurrence stays in place and
    absorbs later copies via :func:`merge_source_items`.
    """
    first_by_key: dict[tuple[str, str], schema.SourceItem] = {}
    kept: list[schema.SourceItem] = []
    for item in items:
        key = (item.source, candidate_key(item))
        existing = first_by_key.get(key)
        if existing is None:
            first_by_key[key] = item
            kept.append(item)
        else:
            merge_source_items(existing, item)
    return kept


_DIVERSITY_RELEVANCE_THRESHOLD = 0.25

# Reddit engagement reservation: pool slots held for the highest-engagement
# entity-grounded in-window Reddit candidates, scaled by pool size (quick 15
# -> 2, default 40 -> 3, deep 60 -> 4). The fused order is RRF-first and each
# stream is relevance-first, so the month's most-discussed on-topic thread can
# otherwise lose its slot to a one-upvote post with better title overlap.
_REDDIT_RESERVE_BY_POOL = ((15, 2), (40, 3))
_REDDIT_RESERVE_MAX = 4


def _reddit_reserve_for(pool_limit: int) -> int:
    for ceiling, reserve in _REDDIT_RESERVE_BY_POOL:
        if pool_limit <= ceiling:
            return reserve
    return _REDDIT_RESERVE_MAX


def relevance_floor_for_entity(entity: str) -> float:
    """Relevance a Reddit thread must clear to earn a reservation or keeper slot.

    Grounding keys on the entity's head token (see ``rerank._entity_grounded``).
    A generic head ("ai", "x", "new") matches almost anything, so the floor
    rises to the diversity threshold; a distinctive head keeps the shared
    ``RELEVANCE_FLOOR``.
    """
    from . import relevance

    head = (entity or "").lower().split()[:1]
    if not head:
        return relevance.RELEVANCE_FLOOR
    token = head[0]
    generic = (
        len(token) <= 2
        or token in relevance.STOPWORDS
        or token in relevance.LOW_SIGNAL_QUERY_TOKENS
    )
    return _DIVERSITY_RELEVANCE_THRESHOLD if generic else relevance.RELEVANCE_FLOOR


def raw_engagement(item: schema.SourceItem) -> float:
    """Upvotes plus comments as a plain number, for engagement-first ordering."""
    eng = item.engagement or {}
    total = 0.0
    for key in ("score", "num_comments"):
        value = eng.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def reddit_thread_qualifies(
    item: schema.SourceItem,
    entity: str,
    floor: float,
    relevance: float | None = None,
) -> bool:
    """On-topic enough for an engagement slot: clears the floor and names the entity.

    ``relevance`` overrides the item's own ``local_relevance`` (a fused
    candidate carries the max across its copies).
    """
    from . import rerank

    score = relevance if relevance is not None else (item.local_relevance or 0.0)
    if score < floor:
        return False
    if raw_engagement(item) <= 0:
        return False
    if not entity:
        return True
    return rerank._entity_grounded(f"{item.title or ''} {item.body or ''}", entity)

# Per-author cap: no single author/handle should dominate the pool.
_MAX_ITEMS_PER_AUTHOR = 3

# Raised cap for the subject of the topic (a handle in the run's
# resolved_handles). On a person or company topic the subject is what the user
# asked about, so the flat cap discards exactly the evidence the run worked
# hardest to retrieve -- the measured 'Peter Steinberger steipete' baseline
# recovered 8 subject-authored posts and would have kept 3. Still bounded: a
# prolific subject must not crowd out commentary about them, which is the other
# half of the answer a user wants.
_MAX_ITEMS_PER_FIRST_PARTY_AUTHOR = 8


def _extract_author(candidate: schema.Candidate) -> str | None:
    """Return a normalized author key from a candidate's source items."""
    for item in candidate.source_items:
        if item.author:
            return item.author.strip().lower()
    return None


def _apply_per_author_cap(
    candidates: list[schema.Candidate],
    max_per_author: int = _MAX_ITEMS_PER_AUTHOR,
    first_party_handles: Iterable[str] | None = None,
    max_per_first_party_author: int = _MAX_ITEMS_PER_FIRST_PARTY_AUTHOR,
) -> list[schema.Candidate]:
    """Keep at most *max_per_author* items from any single author.

    Authors named in *first_party_handles* -- the subject of the topic -- get
    the higher *max_per_first_party_author* allowance instead, because their
    own posts are the point of the query rather than one voice among many.

    Candidates are assumed to already be sorted by quality (rrf_score etc.),
    so the first N encountered per author are the best ones.
    """
    first_party = {
        h.strip().lstrip("@").lower()
        for h in (first_party_handles or ())
        if h and h.strip()
    }
    author_counts: dict[str, int] = {}
    result: list[schema.Candidate] = []
    for c in candidates:
        author = _extract_author(c)
        if author is None:
            result.append(c)
            continue
        limit = (
            max_per_first_party_author
            if author.strip().lstrip("@").lower() in first_party
            else max_per_author
        )
        count = author_counts.get(author, 0)
        if count < limit:
            result.append(c)
            author_counts[author] = count + 1
    return result


def _reddit_engagement_reservation(
    fused: list[schema.Candidate],
    reserve: int,
    entity: str,
) -> list[schema.Candidate]:
    """The *reserve* highest-engagement Reddit candidates that are in-window
    and on-topic, in engagement order."""
    if reserve <= 0:
        return []
    floor = relevance_floor_for_entity(entity)
    eligible = []
    for c in fused:
        if c.source != "reddit" or schema.candidate_out_of_window(c):
            continue
        reddit_items = [it for it in c.source_items if it.source == "reddit"]
        if not reddit_items:
            continue
        best = max(reddit_items, key=raw_engagement)
        relevance = max(c.local_relevance or 0.0, best.local_relevance or 0.0)
        if not reddit_thread_qualifies(best, entity, floor, relevance=relevance):
            continue
        eligible.append((raw_engagement(best), c))
    eligible.sort(key=lambda pair: -pair[0])
    return [c for _, c in eligible[:reserve]]


def _diversify_pool(
    fused: list[schema.Candidate],
    pool_limit: int,
    min_per_source: int = 2,
    entity: str = "",
) -> list[schema.Candidate]:
    """Ensure at least *min_per_source* items per qualifying source survive truncation.

    Sources only qualify for reserved slots if their best item exceeds
    the relevance threshold. Low-relevance sources compete on merit only.
    Reddit additionally gets an engagement reservation (see
    ``_reddit_reserve_for``) for its most-discussed on-topic threads.
    """
    max_relevance: dict[str, float] = {}
    for c in fused:
        current = max_relevance.get(c.source, 0.0)
        if c.local_relevance > current:
            max_relevance[c.source] = c.local_relevance

    protected = _reddit_engagement_reservation(fused, _reddit_reserve_for(pool_limit), entity)
    protected_ids = {c.candidate_id for c in protected}
    pool: list[schema.Candidate] = list(protected)
    seen = set(protected_ids)
    reserved: dict[str, list[schema.Candidate]] = {}
    remainder: list[schema.Candidate] = []
    for c in fused:
        if c.candidate_id in seen:
            continue
        qualifies = max_relevance.get(c.source, 0.0) >= _DIVERSITY_RELEVANCE_THRESHOLD
        bucket = reserved.setdefault(c.source, [])
        if qualifies and len(bucket) < min_per_source:
            bucket.append(c)
        else:
            remainder.append(c)
    pool.extend(c for per_source in reserved.values() for c in per_source)
    seen = {c.candidate_id for c in pool}
    for c in remainder:
        if len(pool) >= pool_limit:
            break
        if c.candidate_id not in seen:
            pool.append(c)
    pool.sort(key=_candidate_sort_key)
    if len(pool) > pool_limit:
        # The per-source buckets can overfill a small pool. The Reddit
        # reservation is low-RRF by construction, so a plain slice would cut
        # exactly the threads it exists to keep: trim unprotected candidates
        # from the sorted tail instead.
        keep_unprotected = pool_limit - len(protected_ids)
        trimmed: list[schema.Candidate] = []
        for c in pool:
            if c.candidate_id in protected_ids:
                trimmed.append(c)
            elif keep_unprotected > 0:
                trimmed.append(c)
                keep_unprotected -= 1
        pool = trimmed
    return pool[:pool_limit]


def weighted_rrf(
    streams: dict[tuple[str, str], list[schema.SourceItem]],
    plan: schema.QueryPlan,
    *,
    pool_limit: int,
    range_from: str | None = None,
    range_to: str | None = None,
    first_party_handles: Iterable[str] | None = None,
) -> list[schema.Candidate]:
    """Fuse ranked lists into a single candidate pool.

    When ``range_from`` and ``range_to`` are provided, they are stored in each
    candidate's metadata so ``candidate_out_of_window`` can compare the actual
    date against the run window (instead of relying solely on adapter-provided
    ``date_confidence``). ``first_party_handles`` raises the per-author cap
    for the topic's subject so their own posts are not flattened to the
    incidental-account allowance.
    """
    subqueries = {subquery.label: subquery for subquery in plan.subqueries}
    candidates: dict[str, schema.Candidate] = {}
    # Track source items already attached to each candidate, keyed by
    # (source, normalized URL): per-stream ids collide across subqueries, and
    # a repeat copy may carry enrichment the first one lacks.
    seen_source_items: dict[str, dict[tuple[str, str], schema.SourceItem]] = {}

    for (label, source), items in streams.items():
        subquery = subqueries[label]
        weight = subquery.weight * plan.source_weights.get(source, 1.0)
        for rank, item in enumerate(items, start=1):
            key = candidate_key(item)
            score = weight / (RRF_K + rank)
            item_local_relevance = item.local_relevance if item.local_relevance is not None else float(item.metadata.get("local_relevance", item.relevance_hint))
            item_freshness = item.freshness if item.freshness is not None else int(item.metadata.get("freshness", 0))
            item_source_quality = item.source_quality if item.source_quality is not None else float(item.metadata.get("source_quality", 0.6))
            if key not in candidates:
                candidate_metadata: dict = {
                    "provenance": [
                        {
                            "source": source,
                            "subquery_label": label,
                            "native_rank": rank,
                            "item_id": item.item_id,
                        }
                    ]
                }
                if range_from:
                    candidate_metadata["range_from"] = range_from
                if range_to:
                    candidate_metadata["range_to"] = range_to
                candidates[key] = schema.Candidate(
                    candidate_id=key,
                    item_id=item.item_id,
                    source=item.source,
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    subquery_labels=[label],
                    native_ranks={f"{label}:{source}": rank},
                    local_relevance=item_local_relevance,
                    freshness=item_freshness,
                    engagement=item.engagement_score if item.engagement_score is not None else item.metadata.get("engagement_score"),
                    source_quality=item_source_quality,
                    rrf_score=score,
                    sources=[item.source],
                    source_items=[item],
                    metadata=candidate_metadata,
                )
                seen_source_items[key] = {(item.source, candidate_key(item)): item}
                continue

            candidate = candidates[key]
            candidate.rrf_score += score
            previous_primary_score = (candidate.local_relevance * 100.0) + candidate.freshness + (candidate.source_quality * 10.0)
            incoming_primary_score = (item_local_relevance * 100.0) + item_freshness + (item_source_quality * 10.0)
            candidate.local_relevance = max(
                candidate.local_relevance,
                item_local_relevance,
            )
            candidate.freshness = max(candidate.freshness, item_freshness)
            item_eng = item.engagement_score if item.engagement_score is not None else item.metadata.get("engagement_score")
            if candidate.engagement is None:
                candidate.engagement = item_eng
            elif item_eng is not None:
                candidate.engagement = max(candidate.engagement, item_eng)
            candidate.source_quality = max(
                candidate.source_quality,
                item_source_quality,
            )
            candidate.native_ranks[f"{label}:{source}"] = rank
            if label not in candidate.subquery_labels:
                candidate.subquery_labels.append(label)
            if item.source not in candidate.sources:
                candidate.sources.append(item.source)
            source_item_key = (item.source, candidate_key(item))
            existing_item = seen_source_items[key].get(source_item_key)
            if existing_item is None:
                seen_source_items[key][source_item_key] = item
                candidate.source_items.append(item)
            else:
                merge_source_items(existing_item, item)
            candidate.metadata.setdefault("provenance", []).append(
                {
                    "source": source,
                    "subquery_label": label,
                    "native_rank": rank,
                    "item_id": item.item_id,
                }
            )
            if incoming_primary_score > previous_primary_score:
                candidate.item_id = item.item_id
                candidate.source = item.source
                candidate.title = item.title
                candidate.snippet = item.snippet
            if len(candidate.snippet.split()) < len(item.snippet.split()):
                candidate.snippet = item.snippet

    fused = sorted(candidates.values(), key=_candidate_sort_key)
    fused = _apply_per_author_cap(fused, first_party_handles=first_party_handles)
    from . import rerank

    entity = rerank._primary_entity(plan.raw_topic or "") if plan.raw_topic else ""
    return _diversify_pool(fused, pool_limit, entity=entity)
