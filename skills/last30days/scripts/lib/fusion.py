"""Weighted reciprocal rank fusion for per-(subquery, source) streams."""

from __future__ import annotations

import copy
import dataclasses
import math
import re
from collections.abc import Iterable
from datetime import date, datetime, timezone

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from . import entity_extract, reddit_refs, schema

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
    """Normalize URL for dedup: lowercase scheme/host, strip www/old/m prefixes,
    drop fragments and utm_* params, and sort remaining query parameters."""
    cleaned = url.strip()
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return cleaned
    netloc = parsed.netloc.lower()
    changed = True
    while changed:
        changed = False
        for prefix in ("www.", "old.", "m."):
            if netloc.startswith(prefix):
                netloc = netloc[len(prefix):]
                changed = True
    # Strip tracking params and canonicalize parameter name order while
    # preserving value order for repeated parameter names.
    params = parse_qs(parsed.query, keep_blank_values=True)
    clean_params = sorted(
        (
            (k, v)
            for k, v in params.items()
            if not k.lower().startswith("utm_")
        ),
        key=lambda kv: kv[0],
    )
    query = urlencode(clean_params, doseq=True)
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", query, ""))


def candidate_key(
    item: schema.SourceItem,
    *,
    polymarket_eids_by_url: dict[str, set[str]] | None = None,
) -> str:
    if item.url:
        if item.source == "polymarket" and polymarket_eids_by_url:
            poly_key = polymarket_url_key(item, polymarket_eids_by_url)
            if poly_key is not None:
                return poly_key
        # Every URL form of one Reddit thread (old., redd.it, a comment
        # permalink) is one candidate, keyed by the thread id.
        return reddit_refs.thread_key(item.url) or _normalize_url(item.url)
    return f"{item.source}:{item.item_id}"


def source_item_key(
    item: schema.SourceItem,
    *,
    polymarket_eids_by_url: dict[str, set[str]] | None = None,
) -> tuple[str, str]:
    """Identity of one source item, for same-source de-duplication.

    Usually the normalized URL (see :func:`candidate_key`). A Hacker News
    item's ``url`` is the linked article, and one article can be submitted
    several times as distinct stories with their own points and comments, so
    HN identity is the discussion URL. ``candidate_key`` still clusters those
    submissions (and other sources linking the article) into one candidate.
    """
    if item.source == "hackernews":
        hn_url = item.metadata.get("hn_url")
        if hn_url:
            return (item.source, _normalize_url(str(hn_url)))
    return (item.source, candidate_key(item, polymarket_eids_by_url=polymarket_eids_by_url))


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


def _clone_source_item(item: schema.SourceItem) -> schema.SourceItem:
    """Shallow-copy a SourceItem and its mutable engagement/metadata containers."""
    metadata = {
        k: (
            [dict(e) if isinstance(e, dict) else e for e in v]
            if isinstance(v, list)
            else dict(v)
            if isinstance(v, dict)
            else v
        )
        for k, v in (item.metadata or {}).items()
    }
    return dataclasses.replace(
        item,
        engagement=dict(item.engagement or {}),
        metadata=metadata,
    )


def _item_engagement_score(item: schema.SourceItem) -> int | float | None:
    if item.engagement_score is not None:
        return item.engagement_score
    return (item.metadata or {}).get("engagement_score")


def _candidate_engagement_from_items(source_items: list[schema.SourceItem]) -> int | float | None:
    scores = [
        val
        for si in source_items
        if (val := _item_engagement_score(si)) is not None
    ]
    return max(scores) if scores else None


def _primary_score(local_relevance: float, freshness: int, source_quality: float) -> float:
    return (local_relevance * 100.0) + freshness + (source_quality * 10.0)


def _parse_iso_day(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).date().isoformat()
    except ValueError:
        return None


def _matched_date_in_window(
    date_str: str,
    range_from: str | None,
    range_to: str | None,
) -> bool:
    text = str(date_str or "").strip()
    if not text:
        return False
    if not range_from and not range_to:
        return True
    iso = _parse_iso_day(text)
    if not iso:
        return False
    start = _parse_iso_day(range_from) if range_from else None
    end = _parse_iso_day(range_to) if range_to else None
    if start and iso < start:
        return False
    if end and iso > end:
        return False
    return True


def _sanitize_reddit_matched_comment(
    item: schema.SourceItem,
    range_from: str | None,
    range_to: str | None,
) -> None:
    if item.source != "reddit" or not isinstance(item.metadata, dict):
        return
    if not range_from and not range_to:
        return
    matched_date = str(item.metadata.get("matched_comment_date") or "").strip()
    if matched_date and not _matched_date_in_window(matched_date, range_from, range_to):
        item.metadata.pop("matched_comment_date", None)
        item.metadata.pop("matched_comment_url", None)


# Polymarket display and ranking fields that belong to a matched-market verdict
# and must move together so a merged item never mixes one market's price with
# another market's question/odds/close date or loses its selection ranking.
_POLYMARKET_MATCH_BUNDLE_KEYS = (
    "matched_market",
    "odds",
    "headline",
    "end_date",
    "question",
    "outcome_prices",
    "outcome_questions",
    "outcome_market_ids",
    "outcomes_remaining",
    "price_movement",
    "selection_score",
    "selection_tier",
)


def _polymarket_event_id(item: schema.SourceItem) -> str:
    if item.source != "polymarket":
        return ""
    return str((item.metadata or {}).get("event_id") or "").strip()


def _polymarket_event_ids_conflict(
    a: schema.SourceItem,
    b: schema.SourceItem,
) -> bool:
    """True when two Polymarket items carry distinct known Gamma event IDs."""
    id_a = _polymarket_event_id(a)
    id_b = _polymarket_event_id(b)
    return bool(id_a and id_b and id_a != id_b)


def polymarket_event_ids_by_url(
    items: Iterable[schema.SourceItem],
) -> dict[str, set[str]]:
    """Map each normalized Polymarket URL to all distinct non-empty event IDs seen."""
    by_url: dict[str, set[str]] = {}
    for item in items:
        if item.source != "polymarket":
            continue
        url = (item.url or "").strip()
        if not url:
            continue
        eid = _polymarket_event_id(item)
        if eid:
            by_url.setdefault(_normalize_url(url), set()).add(eid)
    return by_url


def polymarket_url_key(
    item: schema.SourceItem,
    eids_by_url: dict[str, set[str]] | None = None,
) -> str | None:
    """Collision-safe URL key for a Polymarket item, or None if not Polymarket/URL-less.

    When multiple distinct ``event_id`` values share one reused slug URL:
    - items with a known ``event_id`` key under ``"{norm_url}\\x1fevent={eid}"``
    - ID-less items key under ``"{norm_url}\\x1fidless"`` so they never merge
      with or bridge conflicting events.
    When zero or one ``event_id`` exists at ``norm_url``, every copy shares
    ``norm_url`` so ID-less copies merge cleanly and inherit the sole ``event_id``.
    """
    if item.source != "polymarket":
        return None
    url = (item.url or "").strip()
    if not url:
        return None
    norm = _normalize_url(url)
    eids = (eids_by_url or {}).get(norm)
    if eids and len(eids) > 1:
        eid = _polymarket_event_id(item)
        return f"{norm}\x1fevent={eid}" if eid else f"{norm}\x1fidless"
    return norm


def _merge_polymarket_match_bundle(
    existing: schema.SourceItem,
    incoming: schema.SourceItem,
) -> bool:
    """Keep Polymarket's matched-market display and ranking fields coherent.

    Returns True when one copy is matched and the other is unmatched, so the
    caller leaves the matched copy's ``body`` and ``snippet`` intact rather
    than replacing them with longer text from the unmatched copy.
    """
    if existing.source != "polymarket" or incoming.source != "polymarket":
        return False
    if _polymarket_event_ids_conflict(existing, incoming):
        return False
    in_meta = incoming.metadata or {}
    mine_matched = bool(existing.metadata.get("matched_market"))
    theirs_matched = bool(in_meta.get("matched_market"))
    if mine_matched == theirs_matched:
        return False
    if theirs_matched:
        for key in _POLYMARKET_MATCH_BUNDLE_KEYS:
            if key in in_meta and in_meta[key] is not None:
                existing.metadata[key] = copy.deepcopy(in_meta[key])
            else:
                existing.metadata.pop(key, None)
        if in_meta.get("event_id") and not existing.metadata.get("event_id"):
            existing.metadata["event_id"] = in_meta["event_id"]
        if incoming.title:
            existing.title = incoming.title
        if incoming.body:
            existing.body = incoming.body
        existing.snippet = incoming.snippet
    return True


def merge_source_items(
    existing: schema.SourceItem,
    incoming: schema.SourceItem,
    *,
    range_from: str | None = None,
    range_to: str | None = None,
) -> schema.SourceItem:
    """Fold ``incoming``'s enrichment and counts into ``existing`` (same thread).

    Keeps the richer value per field: an enrichment list the existing copy
    lacks (or a longer one), a longer enrichment string, the larger numeric
    engagement counters, and the longer body/snippet. Reddit copies merge per
    field by provenance: the score from the most trusted source wins (a live
    score beats an archive snapshot, which beats a pending one) and comments
    are unioned by comment id. Recomputes ``existing.engagement_score`` when
    engagement or top comments change. Mutates and returns ``existing``.
    """
    if existing.engagement is None:
        existing.engagement = {}
    if existing.metadata is None:
        existing.metadata = {}
    if _polymarket_event_ids_conflict(existing, incoming):
        return existing
    eng_before = dict(existing.engagement)
    eng_src_before = existing.metadata.get("engagement_source")
    comments_before = existing.metadata.get("top_comments")

    reddit = existing.source == "reddit" and incoming.source == "reddit"
    if reddit:
        _merge_reddit_fields(
            existing, incoming, range_from=range_from, range_to=range_to,
        )
    polymarket_match_preserved = _merge_polymarket_match_bundle(existing, incoming)
    in_meta = incoming.metadata or {}
    if existing.source == "polymarket" and incoming.source == "polymarket":
        incoming_eid = _polymarket_event_id(incoming)
        if incoming_eid and not _polymarket_event_id(existing):
            existing.metadata["event_id"] = incoming_eid
    for meta_key in ("enriched_via", "reddit_subreddit"):
        val = in_meta.get(meta_key)
        if val and not existing.metadata.get(meta_key):
            existing.metadata[meta_key] = val
    theirs_rts = in_meta.get("reddit_thread_score")
    if (
        isinstance(theirs_rts, (int, float))
        and not isinstance(theirs_rts, bool)
        and math.isfinite(theirs_rts)
    ):
        mine_rts = existing.metadata.get("reddit_thread_score")
        if (
            not isinstance(mine_rts, (int, float))
            or isinstance(mine_rts, bool)
            or not math.isfinite(mine_rts)
            or theirs_rts > mine_rts
        ):
            existing.metadata["reddit_thread_score"] = theirs_rts
    for key in _ENRICHMENT_KEYS:
        if reddit and key == "top_comments":
            continue
        theirs = incoming.metadata.get(key)
        if not theirs or (isinstance(theirs, str) and not theirs.strip()):
            continue
        mine = existing.metadata.get(key)
        mine_empty = not mine or (isinstance(mine, str) and not mine.strip())
        if (
            mine_empty
            or (isinstance(theirs, list) and isinstance(mine, list) and len(theirs) > len(mine))
            or (isinstance(theirs, str) and isinstance(mine, str) and len(theirs.strip()) > len(mine.strip()))
        ):
            existing.metadata[key] = (
                [dict(e) if isinstance(e, dict) else e for e in theirs]
                if isinstance(theirs, list)
                else theirs
            )
    for field_name, value in (incoming.engagement or {}).items():
        if reddit and field_name == "score":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if existing.engagement.get(field_name) is None and value is not None:
                existing.engagement[field_name] = value
            continue
        current = existing.engagement.get(field_name)
        if not isinstance(current, (int, float)) or isinstance(current, bool) or value > current:
            existing.engagement[field_name] = value
    if (
        existing.engagement != eng_before
        or existing.metadata.get("engagement_source") != eng_src_before
        or existing.metadata.get("top_comments") != comments_before
    ):
        from . import signals

        existing.engagement_score = signals.engagement_score(existing)
    if not polymarket_match_preserved:
        if len(incoming.body or "") > len(existing.body or ""):
            existing.body = incoming.body
        # A reel or TikTok fetched twice can come back once with its spoken
        # transcript and once with only its written description. `spoken_caption`
        # says which one the snippet is, so the two move together: the transcript
        # wins whatever its length, and a longer written description never takes
        # its place.
        theirs_spoken = bool(incoming.metadata.get("spoken_caption"))
        mine_spoken = bool(existing.metadata.get("spoken_caption"))
        if theirs_spoken != mine_spoken:
            if theirs_spoken and (incoming.snippet or "").strip():
                existing.snippet = incoming.snippet
                existing.metadata["spoken_caption"] = True
        elif len(incoming.snippet or "") > len(existing.snippet or ""):
            existing.snippet = incoming.snippet
    return existing


def _reddit_score_key(score: object, source: str | None) -> tuple[int, float]:
    """Order-independent rank for a Reddit copy's ``(score, engagement_source)``.

    Higher wins regardless of merge order:
      3 — measured live score (explicit ``LIVE`` with any finite number,
          including measured ``0``; or unlabeled copy with a non-zero number)
      2 — measured archive score (``ARCTIC`` with any finite number)
      1 — explicitly withheld fresh archive score (``ARCTIC_PENDING``)
      0 — unmeasured / ``UNKNOWN`` / placeholder ``0`` or ``None``
    """
    numeric = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
    )
    if source == reddit_refs.ARCTIC_PENDING:
        return (1, 0.0)
    if source == reddit_refs.UNKNOWN:
        return (0, 0.0)
    if source == reddit_refs.ARCTIC:
        return (2, float(score)) if numeric else (0, 0.0)
    if source == reddit_refs.LIVE:
        return (3, float(score)) if numeric else (0, 0.0)
    if not source:
        # Unlabeled copy: a non-zero number is treated as live (matching
        # reddit_hydrate._effective_score_rank); placeholder 0 or None is unmeasured.
        return (3, float(score)) if (numeric and score != 0) else (0, 0.0)
    return (reddit_refs.score_rank(source), float(score)) if numeric else (0, 0.0)


def _merge_reddit_fields(
    existing: schema.SourceItem,
    incoming: schema.SourceItem,
    *,
    range_from: str | None = None,
    range_to: str | None = None,
) -> None:
    """The Reddit fields whose winner depends on where each copy got them."""
    mine_source = existing.metadata.get("engagement_source")
    theirs_source = incoming.metadata.get("engagement_source")
    mine_score = existing.engagement.get("score")
    theirs_score = (incoming.engagement or {}).get("score")
    if _reddit_score_key(theirs_score, theirs_source) > _reddit_score_key(mine_score, mine_source):
        existing.engagement["score"] = theirs_score
        if theirs_source:
            existing.metadata["engagement_source"] = theirs_source
        else:
            existing.metadata.pop("engagement_source", None)
    theirs_comments = incoming.metadata.get("top_comments") or []
    if theirs_comments:
        existing.metadata["top_comments"] = [
            dict(c) if isinstance(c, dict) else c
            for c in reddit_refs.union_comments(
                existing.metadata.get("top_comments") or [], theirs_comments,
            )
        ]
        sources = {
            existing.metadata.get("comments_source"), incoming.metadata.get("comments_source"),
        } - {None, ""}
        if sources:
            existing.metadata["comments_source"] = "+".join(sorted(sources))
    in_meta = incoming.metadata if isinstance(incoming.metadata, dict) else {}
    mine_date = str(existing.metadata.get("matched_comment_date") or "").strip()
    theirs_date = str(in_meta.get("matched_comment_date") or "").strip()
    mine_url = str(existing.metadata.get("matched_comment_url") or "").strip()
    theirs_url = str(in_meta.get("matched_comment_url") or "").strip()
    if mine_date and not _matched_date_in_window(mine_date, range_from, range_to):
        existing.metadata.pop("matched_comment_date", None)
        existing.metadata.pop("matched_comment_url", None)
        mine_date = ""
        mine_url = ""
    theirs_in_window = _matched_date_in_window(theirs_date, range_from, range_to)
    mine_cmp = _parse_iso_day(mine_date) or mine_date
    theirs_cmp = _parse_iso_day(theirs_date) or theirs_date
    if theirs_in_window and (
        not mine_date
        or theirs_cmp > mine_cmp
        or (theirs_cmp == mine_cmp and not mine_url and theirs_url)
    ):
        existing.metadata["matched_comment_date"] = theirs_date
        if theirs_url:
            existing.metadata["matched_comment_url"] = theirs_url
        else:
            existing.metadata.pop("matched_comment_url", None)


def collapse_duplicate_urls(items: list[schema.SourceItem]) -> list[schema.SourceItem]:
    """Collapse same-source, same-URL copies, keeping order and merging enrichment.

    Per-stream item ids (``R1``, ``X1``) collide across subqueries, so identity
    is :func:`source_item_key` (the normalized URL, or the HN discussion URL),
    not the id. The first occurrence stays in place and absorbs later copies
    via :func:`merge_source_items`. Reused Polymarket slug URLs carrying
    distinct known ``event_id`` values (and ID-less copies at those URLs) are
    kept separate so conflicting events never merge or bridge.
    """
    poly_eids = polymarket_event_ids_by_url(items)
    first_by_key: dict[tuple[str, str], schema.SourceItem] = {}
    kept: list[schema.SourceItem] = []
    for item in items:
        key = source_item_key(item, polymarket_eids_by_url=poly_eids)
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


# Capability reservation: on an enablement plan ("X control my computer"),
# pool slots held for the best results of the searches for the capability
# alone ("computer use MCP server"). Posts naming the host match several
# searches and outrank them on RRF, so the tools that answer the question
# would otherwise never reach the reranker.
_CAPABILITY_RESERVE = 6
_CAPABILITY_RESERVE_PER_SOURCE = 3
ENABLEMENT_HOST_NOTE = "enablement-host:"  # planner.ENABLEMENT_HOST_NOTE


def enablement_host(plan: schema.QueryPlan) -> str:
    """The host an enablement plan names ("grok"), or "" for any other plan."""
    for note in plan.notes:
        if note.startswith(ENABLEMENT_HOST_NOTE):
            return note[len(ENABLEMENT_HOST_NOTE):]
    return ""


def _names(text: str, host: str) -> bool:
    return bool(re.search(rf"\b{re.escape(host)}", text, re.IGNORECASE))


def capability_labels(plan: schema.QueryPlan) -> set[str]:
    """Labels of an enablement plan's subqueries that leave out the host."""
    host = enablement_host(plan)
    if not host:
        return set()
    return {subquery.label for subquery in plan.subqueries if not _names(subquery.search_query, host)}


def is_capability_result(candidate: schema.Candidate, labels: set[str], host: str) -> bool:
    """A result of a capability-only search that does not itself name the host.

    Some sources search the raw topic whatever the subquery says, so a
    capability label alone also tags posts about the host; those need no help.
    """
    if not labels.intersection(candidate.subquery_labels):
        return False
    return not _names(f"{candidate.title} {candidate.snippet}", host)


def _capability_reservation(results: list[schema.Candidate]) -> list[schema.Candidate]:
    """Up to _CAPABILITY_RESERVE results, taken round-robin across sources.

    Tool pages (Web, GitHub) rank below social posts on fused rank, so a plain
    top-N went entirely to Reddit and TikTok posts about the capability.
    """
    by_source: dict[str, list[schema.Candidate]] = {}
    for candidate in results:
        by_source.setdefault(candidate.source, []).append(candidate)
    chosen: list[schema.Candidate] = []
    for rank in range(_CAPABILITY_RESERVE_PER_SOURCE):
        for queue in by_source.values():
            if rank < len(queue) and len(chosen) < _CAPABILITY_RESERVE:
                chosen.append(queue[rank])
    return chosen


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
        # Jobs use the ATS provider as author, not an individual contributor.
        if item.source != "jobs" and item.author:
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
    and on-topic, in engagement order. A copy whose score is pending has no
    engagement to rank by yet and is passed over."""
    if reserve <= 0:
        return []
    floor = relevance_floor_for_entity(entity)
    eligible = []
    for c in fused:
        if c.source != "reddit" or schema.candidate_out_of_window(c):
            continue
        reddit_items = [
            it for it in c.source_items
            if it.source == "reddit" and not reddit_refs.score_pending(it.metadata)
        ]
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
    capability: set[str] | None = None,
    host: str = "",
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
    if capability:
        reserved_ids = {c.candidate_id for c in protected}
        protected += _capability_reservation(
            [
                c
                for c in fused
                if c.candidate_id not in reserved_ids
                and not schema.candidate_out_of_window(c)
                and is_capability_result(c, capability, host)
            ]
        )
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


def _pin_snippet_to_primary_submission(candidate: schema.Candidate) -> None:
    """Keep a card's Evidence on the item whose metrics it shows.

    The card renders :func:`schema.candidate_primary_item`'s metrics beside
    ``candidate.snippet``. When the candidate also holds a distinct
    same-source sibling (two HN submissions of one article), the longest- or
    best-snippet rule above can pick the sibling's text, so one story's
    numbers would sit beside another story's words. Other candidates keep
    their snippet unchanged.
    """
    primary = schema.candidate_primary_item(candidate)
    if primary is None:
        return
    primary_key = source_item_key(primary)
    if any(
        item.source == primary.source and source_item_key(item) != primary_key
        for item in candidate.source_items
    ):
        candidate.snippet = primary.snippet or ""


def weighted_rrf(
    streams: dict[tuple[str, str], list[schema.SourceItem]],
    plan: schema.QueryPlan,
    *,
    pool_limit: int,
    range_from: str | None = None,
    range_to: str | None = None,
    first_party_handles: Iterable[str] | None = None,
    primary_handle: str | None = None,
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
    primary_scores: dict[str, float] = {}
    candidate_spoken: dict[str, bool] = {}
    # Track source items already attached to each candidate, keyed by
    # source_item_key (source + normalized URL, or the HN discussion URL):
    # per-stream ids collide across subqueries, and
    # a repeat copy may carry enrichment the first one lacks.
    seen_source_items: dict[str, dict[tuple[str, str], schema.SourceItem]] = {}
    poly_eids = polymarket_event_ids_by_url(
        item
        for items in streams.values()
        for item in items
        if item.source == "polymarket"
        and entity_extract.matches_person_identity(
            item, plan.person_identity_anchors, primary_handle,
            person_run=getattr(plan, "person_run", False),
        )
    )

    for (label, source), items in streams.items():
        subquery = subqueries[label]
        weight = subquery.weight * plan.source_weights.get(source, 1.0)
        for rank, item in enumerate(items, start=1):
            # Reject only the unqualified copy before combining its text,
            # provenance, or votes with qualified evidence at the same URL.
            if not entity_extract.matches_person_identity(
                item, plan.person_identity_anchors, primary_handle,
                person_run=getattr(plan, "person_run", False),
            ):
                continue
            key = candidate_key(item, polymarket_eids_by_url=poly_eids)
            score = weight / (RRF_K + rank)
            item_local_relevance = item.local_relevance if item.local_relevance is not None else float(item.metadata.get("local_relevance", item.relevance_hint))
            item_freshness = item.freshness if item.freshness is not None else int(item.metadata.get("freshness", 0))
            item_source_quality = item.source_quality if item.source_quality is not None else float(item.metadata.get("source_quality", 0.6))
            incoming_primary_score = _primary_score(item_local_relevance, item_freshness, item_source_quality)
            incoming_spoken = bool((item.metadata or {}).get("spoken_caption")) and bool((item.snippet or "").strip())
            if key not in candidates:
                attached = _clone_source_item(item)
                _sanitize_reddit_matched_comment(attached, range_from, range_to)
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
                    engagement=_item_engagement_score(attached),
                    source_quality=item_source_quality,
                    rrf_score=score,
                    sources=[item.source],
                    source_items=[attached],
                    metadata=candidate_metadata,
                )
                primary_scores[key] = incoming_primary_score
                candidate_spoken[key] = incoming_spoken
                seen_source_items[key] = {
                    source_item_key(attached, polymarket_eids_by_url=poly_eids): attached,
                }
                continue

            candidate = candidates[key]
            rank_key = f"{label}:{source}"
            if rank_key not in candidate.native_ranks:
                candidate.rrf_score += score
                candidate.native_ranks[rank_key] = rank
            else:
                candidate.native_ranks[rank_key] = min(candidate.native_ranks[rank_key], rank)
            candidate.local_relevance = max(
                candidate.local_relevance,
                item_local_relevance,
            )
            candidate.freshness = max(candidate.freshness, item_freshness)
            candidate.source_quality = max(
                candidate.source_quality,
                item_source_quality,
            )
            if label not in candidate.subquery_labels:
                candidate.subquery_labels.append(label)
            if item.source not in candidate.sources:
                candidate.sources.append(item.source)
            promoted = incoming_primary_score > primary_scores[key]
            item_key = source_item_key(item, polymarket_eids_by_url=poly_eids)
            existing_item = seen_source_items[key].get(item_key)
            if existing_item is None:
                attached = _clone_source_item(item)
                _sanitize_reddit_matched_comment(attached, range_from, range_to)
                seen_source_items[key][item_key] = attached
                candidate.source_items.append(attached)
            elif promoted:
                new_primary = _clone_source_item(item)
                merge_source_items(
                    new_primary, existing_item,
                    range_from=range_from, range_to=range_to,
                )
                idx = candidate.source_items.index(existing_item)
                candidate.source_items[idx] = new_primary
                seen_source_items[key][item_key] = new_primary
                attached = new_primary
            else:
                merge_source_items(
                    existing_item, _clone_source_item(item),
                    range_from=range_from, range_to=range_to,
                )
                attached = existing_item
            candidate.engagement = _candidate_engagement_from_items(candidate.source_items)
            candidate.metadata.setdefault("provenance", []).append(
                {
                    "source": source,
                    "subquery_label": label,
                    "native_rank": rank,
                    "item_id": item.item_id,
                }
            )
            if promoted:
                primary_scores[key] = incoming_primary_score
                candidate.item_id = attached.item_id
                candidate.source = attached.source
                candidate.title = attached.title
                candidate.url = attached.url
                if candidate.source_items and candidate.source_items[0] is not attached:
                    candidate.source_items.remove(attached)
                    candidate.source_items.insert(0, attached)
            elif (
                attached.source == "polymarket"
                and candidate.source == "polymarket"
                and (attached.metadata or {}).get("matched_market")
            ):
                candidate.title = attached.title
            if incoming_spoken and not candidate_spoken[key]:
                candidate.snippet = item.snippet
                candidate_spoken[key] = True
            elif incoming_spoken == candidate_spoken[key]:
                if promoted or (
                    attached.source == "polymarket"
                    and candidate.source == "polymarket"
                    and (attached.metadata or {}).get("matched_market")
                ):
                    candidate.snippet = attached.snippet
                elif len((candidate.snippet or "").split()) < len((item.snippet or "").split()):
                    candidate.snippet = item.snippet

    for candidate in candidates.values():
        _pin_snippet_to_primary_submission(candidate)

    fused = sorted(candidates.values(), key=_candidate_sort_key)
    fused = _apply_per_author_cap(fused, first_party_handles=first_party_handles)
    from . import rerank

    entity = rerank._primary_entity(plan.raw_topic or "") if plan.raw_topic else ""
    return _diversify_pool(
        fused, pool_limit, entity=entity, capability=capability_labels(plan), host=enablement_host(plan)
    )
