"""Diffbot Knowledge Graph Article search (requires DIFFBOT_API_KEY).

Queries the DQL endpoint (kg.diffbot.com/kg/v3/dql) for ``type:Article``
entities within the requested date window and normalizes each hit into the
same item shape as web grounding, so results flow through
``normalize._normalize_grounding`` unchanged.

Auth: ``DIFFBOT_API_KEY`` is passed as the ``token`` query parameter (Diffbot's
API names the credential "token" but we standardize the env var on the house
``*_API_KEY`` convention). Get a key at https://app.diffbot.com/account/.

Response shape verified live against kg.diffbot.com on 2026-06-13:
  - results live under top-level ``data[]``; each is ``{entity, entity_ctx}``
  - the Article URL is ``entity.pageUrl`` (``resolvedPageUrl`` is absent)
  - ``entity.date`` / ``entity.estimatedDate`` are objects
    ``{"str": "d2026-06-13T19:59", "precision": 4, "timestamp": <epoch_ms>}``,
    NOT plain strings
  - there is no per-document relevance score (``entity_ctx`` only carries
    ``cluster_id``), but results come back in Diffbot's own relevance order, so
    relevance is synthesized from each hit's rank (see ``_rank_relevance``)
"""

from __future__ import annotations

import re
from datetime import timedelta
from itertools import combinations
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import dates, http, log, query

DQL_ENDPOINT = "https://kg.diffbot.com/kg/v3/dql"

# Per-depth fan-out. Each depth runs ``queries`` distinct DQL formulations
# (different AND'd ``text:`` clause subsets of the topic — see
# ``_clause_combinations``), each returning the top ``size`` results, then merges
# and dedupes them. Adding formulations broadens coverage because each one
# changes the *matched set*, not merely its order; ``size`` stays small so every
# formulation contributes only its highest-relevance hits.
DEPTH_CONFIG = {
    "quick":   {"queries": 2, "size": 5},
    "default": {"queries": 3, "size": 5},
    "deep":    {"queries": 4, "size": 10},
}

# Cap on AND'd text:"..." clauses. Diffbot ANDs separate text clauses, and each
# added term roughly halves the result set (verified live 2026-06-13: a 3-term
# AND returned ~1.8k hits vs ~12k for 2 terms). Three covers an entity plus a
# couple of modifiers without over-constraining into zero results.
_MAX_TEXT_CLAUSES = 3

# Position-based relevance. The DQL response carries no per-document relevance
# score (entity_ctx only has cluster_id), but results come back in Diffbot's own
# relevance order, so we synthesize a score from each hit's 1-based rank:
# rank 1 -> 1.0, decaying _RELEVANCE_DECAY_PER_RANK per position (rank 5 -> 0.80,
# rank 10 -> 0.55), floored at _RELEVANCE_FLOOR. RRF + rerank handle final order.
_TOP_RELEVANCE = 1.0
_RELEVANCE_DECAY_PER_RANK = 0.05
_RELEVANCE_FLOOR = 0.1

# Diffbot timestamps above this magnitude are epoch milliseconds (anything
# smaller would be a date before 2001 in seconds, which Articles never are).
_MS_THRESHOLD = 1e12

# Graduated recency boost tiers: (days back from to_date, should weight 1-100).
# DQL `should` clauses are OPTIONAL scoring boosts, not filters, so these reorder
# results toward fresher articles without narrowing the matched set. An article
# clears every tier whose cutoff it post-dates and accumulates those weights, so
# newer = more stacked weight = higher rank (a smooth recency curve). This
# replaces `sortBy:date`, which discarded Diffbot's relevance ranking entirely
# and flooded the top with same-day spam (verified live 2026-06-15: on
# "spacex ipo", sortBy:date returned 7/10 crypto-spam; default relevance plus
# a date should-boost returned 10/10 on-topic, freshest-first).
_RECENCY_TIERS = ((2, 60), (7, 40), (14, 20))

# Per-clause weight bounds for the optional title-match boost. The weight scales
# with term specificity, approximated by string length: a short generic token
# ("ipo", "ai") gets _TITLE_WEIGHT_MIN, a longer distinctive one ("starlink",
# "valuation") approaches _TITLE_WEIGHT_MAX, and multi-word proper-noun phrases
# kept intact by _text_clauses (e.g. "Jensen Huang") are inherently specific so
# they take the max. The MAX stays below the *stacked* recency weight a fresh
# article accrues (the sum of _RECENCY_TIERS), so even the largest possible total
# title boost can't outrank genuine freshness — though a single strong title
# match can now exceed the weakest individual recency tier, letting headline
# relevance compete with marginal recency. Tuned live 2026-06-15: a flat weight
# 30 cleaned headline relevance with zero stale leakage, while 50+ pulled
# month-old articles to the top; scaling by specificity further demotes
# generic-token matches like "ipo".
_TITLE_WEIGHT_MIN = 10
_TITLE_WEIGHT_MAX = 30


def _log(msg: str) -> None:
    log.source_log("Diffbot", msg)


def _escape(value: str) -> str:
    """Escape backslashes and double quotes for a DQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _text_clauses(topic: str) -> List[str]:
    """Turn a topic/subquery into AND'd ``text:"..."`` match terms.

    Diffbot's ``text:`` is a strict adjacency phrase match, so wrapping a whole
    multi-word subquery in one quoted phrase frequently returns zero hits (no
    article contains that exact phrase). Instead we emit one quoted clause per
    significant term — Diffbot ANDs separate ``text:`` clauses — which recalls
    thousands of articles where the single phrase recalls none.

    Multi-word proper names and hyphenated terms (e.g. "Jensen Huang",
    "multi-agent") are kept intact as single phrase clauses via
    ``query.extract_compound_terms``; remaining significant single words each
    become their own clause. Noise/modifier words ("use cases", "latest",
    "best") are dropped via ``query.NOISE_WORDS``, and the list is capped at
    ``_MAX_TEXT_CLAUSES``.
    """
    topic = topic.strip()
    if not topic:
        return []

    clauses: List[str] = []
    seen: set[str] = set()
    covered: set[str] = set()  # words already inside an accepted phrase

    # Proper-noun / hyphenated phrases first; remove their spans from the
    # remainder so their words aren't re-added as standalone clauses.
    remainder = topic
    for compound in query.extract_compound_terms(topic):
        lowered = compound.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        clauses.append(compound)
        remainder = remainder.replace(compound, " ")
        # The deterministic planner appends a lowercase echo of quoted compound
        # terms (e.g. '"Jensen Huang" jensen huang'); track the constituent
        # words so the echo isn't re-added as redundant AND clauses.
        covered.update(re.findall(r"[\w'-]+", lowered))

    # Remaining significant single words (drop noise, preserve casing/order).
    for word in re.findall(r"[\w'-]+", remainder):
        lowered = word.lower()
        if (
            len(word) < 2
            or lowered in query.NOISE_WORDS
            or lowered in seen
            or lowered in covered
        ):
            continue
        seen.add(lowered)
        clauses.append(word)

    return clauses[:_MAX_TEXT_CLAUSES]


def _recency_should_clauses(from_date: str, to_date: str) -> List[str]:
    """Build graduated ``should[weight]:date>=cutoff`` recency-boost clauses.

    Each tier in ``_RECENCY_TIERS`` boosts articles published on/after
    ``to_date - N days``. Cutoffs are clamped to ``from_date`` so a narrow window
    (e.g. ``--days=7``) collapses redundant tiers to a single boost rather than
    referencing dates outside the window. Returns ``[]`` if either bound is
    unparseable (the query then falls back to plain default-relevance ordering).
    """
    end = dates.parse_date(to_date)
    start = dates.parse_date(from_date)
    if not end or not start:
        return []
    clauses: List[str] = []
    seen: set[str] = set()
    for days_back, weight in _RECENCY_TIERS:
        cutoff = end - timedelta(days=days_back)
        if cutoff < start:
            cutoff = start
        cutoff_str = cutoff.date().isoformat()
        # Tiers ordered strongest-first; when clamping collapses two onto the
        # same date, keep the higher weight (first seen) and drop the rest.
        if cutoff_str in seen:
            continue
        seen.add(cutoff_str)
        clauses.append(f'should[{weight}]:date>="{cutoff_str}"')
    return clauses


def _specificity_to_weight(score: float) -> int:
    """Map a driving-LLM specificity score (0-100) to a title-boost weight.

    0 = generic term, 100 = highly distinctive. Mapped linearly into
    ``[_TITLE_WEIGHT_MIN, _TITLE_WEIGHT_MAX]`` so the LLM expresses *relative*
    specificity without needing to know the recency-tier internals, and the
    engine still guarantees the title boost can never exceed _TITLE_WEIGHT_MAX
    (i.e. recency keeps priority) no matter what the LLM emits.
    """
    score = max(0.0, min(100.0, float(score)))
    span = _TITLE_WEIGHT_MAX - _TITLE_WEIGHT_MIN
    return round(_TITLE_WEIGHT_MIN + span * score / 100.0)


def _title_clause_weight(term: str) -> int:
    """Fallback title-boost weight for one clause when the driving LLM did not
    supply a specificity score: approximate specificity by string length.

    Multi-word proper-noun phrases are inherently specific and take the max;
    single tokens scale linearly with length and clamp to
    ``[_TITLE_WEIGHT_MIN, _TITLE_WEIGHT_MAX]`` so generic short words ("ipo")
    nudge ranking far less than distinctive long ones ("starlink"). This is the
    mediocre heuristic ``term_specificity`` (see ``build_article_dql``) exists to
    override; it underweights short-but-distinctive tokens like tickers ("spcx").
    """
    term = term.strip()
    if " " in term:  # compound proper-noun phrase, e.g. "Jensen Huang"
        return _TITLE_WEIGHT_MAX
    weight = (len(term) - 2) * 5  # rough length-as-specificity proxy
    return max(_TITLE_WEIGHT_MIN, min(_TITLE_WEIGHT_MAX, weight))


def _clause_combinations(
    clauses: List[str],
    title_specificity: Optional[Dict[str, float]],
    num_queries: int,
) -> List[List[str]]:
    """Ordered AND-clause subsets for the fan-out, most-specific first.

    Returns up to ``num_queries`` distinct clause groups built from the topic's
    significant terms. The full clause set comes first (most constrained, highest
    precision); then progressively smaller subsets broaden recall. Within a given
    subset size, groups carrying the more distinctive terms come first — judged by
    the driving LLM's ``term_specificity`` when present, else the length heuristic
    — so a limited query budget is spent on the formulations most likely to
    surface on-topic articles. A single-term topic yields one group (nothing to
    vary), and fewer groups are returned than requested when the term set can't
    produce that many distinct subsets.
    """
    if num_queries <= 0 or not clauses:
        return []
    if len(clauses) <= 1:
        return [list(clauses)]
    spec_lc = {
        str(k).strip().lower(): v
        for k, v in (title_specificity or {}).items()
    }

    def _term_specificity(term: str) -> float:
        score = spec_lc.get(term.strip().lower())
        return float(score) if score is not None else float(_title_clause_weight(term))

    def _group_specificity(group: List[str]) -> float:
        return sum(_term_specificity(c) for c in group)

    ordered: List[List[str]] = []
    for size in range(len(clauses), 0, -1):
        groups = [list(g) for g in combinations(clauses, size)]
        groups.sort(key=_group_specificity, reverse=True)
        ordered.extend(groups)
        if len(ordered) >= num_queries:
            break
    return ordered[:num_queries]


def _build_dql_from_clauses(
    terms: List[str],
    from_date: str,
    to_date: str,
    title_specificity: Optional[Dict[str, float]] = None,
) -> str:
    """Assemble one DQL string from an explicit list of ``text:`` clauses.

    The per-formulation core the fan-out calls once per clause subset. See
    ``build_article_dql`` for the date-window / recency / title-boost design.
    """
    spec_lc = {
        str(k).strip().lower(): v
        for k, v in (title_specificity or {}).items()
    }

    def _title_weight(term: str) -> int:
        score = spec_lc.get(term.strip().lower())
        if score is not None:
            return _specificity_to_weight(score)
        return _title_clause_weight(term)

    text_part = " ".join(f'text:"{_escape(c)}"' for c in terms)
    title_part = " ".join(
        f'should[{_title_weight(c)}]:title:"{_escape(c)}"' for c in terms
    )
    parts = [
        f"type:Article {text_part}",
        f'date>="{from_date}" date<="{to_date}"',
    ]
    if title_part:
        parts.append(title_part)
    recency = _recency_should_clauses(from_date, to_date)
    if recency:
        parts.append(" ".join(recency))
    return " ".join(parts)


def build_article_dql(
    topic: str,
    from_date: str,
    to_date: str,
    title_specificity: Optional[Dict[str, float]] = None,
) -> str:
    """Build a date-bounded ``type:Article`` DQL query for the full clause set.

    Date filters use ISO ``YYYY-MM-DD`` comparisons as a hard window gate. No
    ``sortBy`` is emitted, so results come back in Diffbot's default relevance
    ranking; graduated ``should[weight]:date>=`` clauses (see
    ``_recency_should_clauses``) then boost fresher articles toward the top
    without narrowing the matched set, and a lighter per-clause
    ``should[weight]:title:`` boost favors articles carrying the more distinctive
    query terms in the headline while staying low enough that recency still
    dominates. The topic is decomposed into AND'd ``text:`` clauses (see
    ``_text_clauses``) rather than one strict phrase.

    ``title_specificity`` maps a query term -> a driving-LLM specificity score
    (0-100); when a clause has a matching score (case-insensitive) its title
    weight comes from ``_specificity_to_weight``, otherwise it falls back to the
    length heuristic ``_title_clause_weight``. This builds the single full-clause
    formulation; ``search_diffbot`` additionally fans out across narrower clause
    subsets (see ``_clause_combinations``). Verified live against kg.diffbot.com
    (2026-06-13 base shape; recency/relevance/title ordering 2026-06-15).
    """
    clauses = _text_clauses(topic)
    if not clauses:
        # All-noise or punctuation-only topic: fall back to the raw phrase.
        clauses = [topic.strip()]
    return _build_dql_from_clauses(clauses, from_date, to_date, title_specificity)


def search_diffbot(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
    text_fallback: bool = True,
    title_specificity: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run a date-bounded Article search against the Diffbot DQL endpoint.

    Args:
        topic: Search topic / subquery string.
        from_date: Window start (YYYY-MM-DD).
        to_date: Window end (YYYY-MM-DD).
        depth: 'quick', 'default', or 'deep' (maps through DEPTH_CONFIG).
        token: DIFFBOT_API_KEY value (sent as the DQL ``token`` query param).
        text_fallback: When True, use Diffbot's ``queryTextFallback`` mode so a
            zero-result structured query automatically retries as free text.
            Out-of-window bleed from the text leg is dropped by the parser.
        title_specificity: Optional ``{term: score 0-100}`` map from the driving
            LLM's query plan; forwarded to the DQL builder to weight the per-term
            title boost by judged distinctiveness (falls back to the length
            heuristic for any term absent from the map) and to order which clause
            formulations the fan-out spends its query budget on.

    Returns:
        Merged DQL JSON response: the ``data`` rows from every formulation
        concatenated (most-specific formulation first) under one ``{"data": [...]}``
        plus a ``hits`` count. Returns an empty ``{"data": []}`` when no token is
        supplied. Cross-formulation URL duplicates are collapsed (and used as a
        relevance signal) downstream in ``parse_diffbot_response``. HTTP errors
        propagate so the pipeline's 429/5xx handling and errors_by_source apply.
    """
    if not token:
        return {"data": []}
    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    num_queries, size = cfg["queries"], cfg["size"]
    clauses = _text_clauses(topic)
    if not clauses:
        clauses = [topic.strip()]
    combos = _clause_combinations(clauses, title_specificity, num_queries)
    if not combos:
        combos = [clauses]
    mode = "queryTextFallback" if text_fallback else "query"
    _log(
        f"Searching Articles for '{topic[:60]}' "
        f"(depth={depth}, queries={len(combos)}, size={size}, mode={mode})"
    )
    # The provenance note records whether the per-term title weights came from
    # the driving LLM's plan or the length heuristic; each formulation's exact
    # DQL is logged durably (tty_only=False) so the fan-out is observable on
    # non-interactive hosts.
    provenance = (
        f"title-weights from LLM term_specificity={title_specificity}"
        if title_specificity
        else "title-weights from length heuristic"
    )
    merged: List[Dict[str, Any]] = []
    total_hits = 0
    for terms in combos:
        dql = _build_dql_from_clauses(terms, from_date, to_date, title_specificity)
        log.source_log("Diffbot", f"DQL [{provenance}]: {dql}", tty_only=False)
        response = http.request(
            "POST",
            DQL_ENDPOINT,
            params={"token": token},
            json_data={"query": dql, "type": mode, "size": size, "cluster": "dedupe"},
            timeout=15,
        )
        if not isinstance(response, dict):
            continue
        data = response.get("data") or []
        # Tag each row with its 1-based rank in this formulation's relevance-
        # ordered response, so position survives concatenation across formulations
        # (parse_diffbot_response turns rank into the relevance score).
        for rank, wrapper in enumerate(data, start=1):
            if isinstance(wrapper, dict):
                wrapper["_rank"] = rank
        merged.extend(data)
        hits = response.get("hits")
        if isinstance(hits, (int, float)) and not isinstance(hits, bool):
            total_hits = max(total_hits, int(hits))
    _log(f"{len(merged)} rows across {len(combos)} formulation(s) (max hits={total_hits})")
    return {"data": merged, "hits": total_hits}


def _rank_relevance(rank: int) -> float:
    """Synthesize a relevance score from a hit's 1-based rank in Diffbot's
    relevance-ordered response: rank 1 -> 1.0, -0.05 per position (rank 5 -> 0.80,
    rank 10 -> 0.55), floored at ``_RELEVANCE_FLOOR``."""
    score = _TOP_RELEVANCE - _RELEVANCE_DECAY_PER_RANK * (max(1, rank) - 1)
    return round(max(_RELEVANCE_FLOOR, score), 4)


def parse_diffbot_response(
    response: Dict[str, Any],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse a DQL response into grounding-shaped item dicts.

    When ``from_date``/``to_date`` are supplied, hits outside the window (or
    with no resolvable date) are dropped — this filters any free-text fallback
    bleed, mirroring the date-range guard in grounding.py.

    Relevance is synthesized from each hit's rank (see ``_rank_relevance``):
    Diffbot's ``_rank`` tag from the fan-out merge when present, else the row's
    position in this response. Rows are deduped by URL keeping the first
    occurrence — the fan-out concatenates formulations most-specific-first, so
    that first hit carries the most trustworthy rank.
    """
    results = response.get("data") or []
    items: List[Dict[str, Any]] = []
    by_url: Dict[str, Dict[str, Any]] = {}
    for position, wrapper in enumerate(results, start=1):
        if not isinstance(wrapper, dict):
            continue
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        url = str(entity.get("pageUrl") or entity.get("resolvedPageUrl") or "").strip()
        if not url:
            continue
        date_str = _parse_entity_date(entity)
        if not _in_window(date_str, from_date, to_date):
            continue
        if url in by_url:
            continue
        rank = wrapper.get("_rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            rank = position
        title = str(entity.get("title") or "").strip()
        summary = str(entity.get("summary") or "").strip()
        text = str(entity.get("text") or "").strip()
        snippet = (summary or text)[:500]
        site = str(entity.get("siteName") or "").strip() or _domain(url)
        entity_id = str(entity.get("id") or "").strip()
        index = len(items)
        item = {
            "id": f"DB{index + 1}",
            "title": title or site or f"Diffbot article {index + 1}",
            "url": url,
            "source_domain": site,
            "snippet": snippet,
            "date": date_str,
            "relevance": _rank_relevance(rank),
            "why_relevant": "Diffbot KG Article search",
            "metadata": {"diffbot_id": entity_id} if entity_id else {},
        }
        by_url[url] = item
        items.append(item)
    return items


def _parse_entity_date(entity: Dict[str, Any]) -> Optional[str]:
    """Resolve an Article entity's publish date to YYYY-MM-DD.

    Diffbot exposes ``date``/``estimatedDate`` as objects with ``timestamp``
    (epoch ms) and ``str`` (e.g. ``"d2026-06-13T19:59"``). Prefer the numeric
    timestamp; fall back to the ``str`` form (stripping its leading ``d``).
    """
    for key in ("date", "estimatedDate"):
        val = entity.get(key)
        if isinstance(val, dict):
            ts = val.get("timestamp")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                seconds = ts / 1000 if ts > _MS_THRESHOLD else ts
                resolved = dates.timestamp_to_date(seconds)
                if resolved:
                    return resolved
            raw = val.get("str")
            if isinstance(raw, str) and raw:
                resolved = _parse_date_str(raw)
                if resolved:
                    return resolved
        elif isinstance(val, str) and val:
            resolved = _parse_date_str(val)
            if resolved:
                return resolved
    return None


def _parse_date_str(raw: str) -> Optional[str]:
    """Parse a Diffbot ``str`` date (leading ``d`` prefix) to YYYY-MM-DD."""
    cleaned = raw[1:] if raw.startswith("d") else raw
    parsed = dates.parse_date(cleaned)
    return parsed.date().isoformat() if parsed else None


def _in_window(
    date_str: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
) -> bool:
    if from_date is None or to_date is None:
        return True
    if not date_str:
        return False
    return from_date <= date_str <= to_date


def _domain(url: str) -> str:
    return urlparse(url).netloc.strip().lower()
