"""Within-source near-duplicate detection."""

from __future__ import annotations

import re

from . import cjk, fusion, reddit_refs, rust_accel, schema

STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "for",
        "how",
        "is",
        "in",
        "of",
        "on",
        "and",
        "with",
        "from",
        "by",
        "at",
        "this",
        "that",
        "it",
        "what",
        "are",
        "do",
        "can",
    }
) | cjk.CHINESE_STOPWORDS


def normalize_text(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _ngrams_of_normalized(norm: str, n: int = 3) -> set[str]:
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[index:index + n] for index in range(len(norm) - n + 1)}


def get_ngrams(text: str, n: int = 3) -> set[str]:
    return _ngrams_of_normalized(normalize_text(text), n)


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def token_jaccard(text_a: str, text_b: str) -> float:
    tokens_a = {
        token
        for token in cjk.segment(normalize_text(text_a))
        if len(token) > 1 and token not in STOPWORDS
    }
    tokens_b = {
        token
        for token in cjk.segment(normalize_text(text_b))
        if len(token) > 1 and token not in STOPWORDS
    }
    return jaccard_similarity(tokens_a, tokens_b)


def hybrid_similarity(text_a: str, text_b: str) -> float:
    return max(
        jaccard_similarity(get_ngrams(text_a), get_ngrams(text_b)),
        token_jaccard(text_a, text_b),
    )


def _tokenize(normalized: str) -> frozenset[str]:
    return frozenset(
        tok for tok in cjk.segment(normalized)
        if len(tok) > 1 and tok not in STOPWORDS
    )


class _PreparedText:
    """Pre-computed text representations for fast repeated similarity checks."""

    __slots__ = ("ngrams", "tokens")

    def __init__(self, raw: str) -> None:
        norm = normalize_text(raw)
        self.ngrams = _ngrams_of_normalized(norm)
        self.tokens = _tokenize(norm)


def prepared_similarity(a: _PreparedText, b: _PreparedText) -> float:
    return max(
        jaccard_similarity(a.ngrams, b.ngrams),
        jaccard_similarity(a.tokens, b.tokens),
    )


def item_text(item: schema.SourceItem) -> str:
    parts = [item.title, item.body, item.author or "", item.container or ""]
    return " ".join(part for part in parts if part).strip()


def _url_dedupe_key(
    item: schema.SourceItem,
    *,
    polymarket_eids_by_url: dict[str, set[str]] | None = None,
) -> tuple[str, str] | None:
    """Normalized ``(source, identity_url)`` for exact-URL deduplication, or None."""
    if item.source == "hackernews":
        hn_url = (item.metadata or {}).get("hn_url")
        if hn_url and str(hn_url).strip():
            return (item.source, fusion._normalize_url(str(hn_url)))
    if item.source == "polymarket":
        poly_key = fusion.polymarket_url_key(item, polymarket_eids_by_url)
        return (item.source, poly_key) if poly_key is not None else None
    url = (item.url or "").strip()
    if not url:
        return None
    if item.source == "reddit":
        thread = reddit_refs.thread_key(url)
        if thread:
            return (item.source, thread)
    return (item.source, fusion._normalize_url(url))


def _is_url_only_dedupe(
    item: schema.SourceItem,
    conflicted_poly_urls: set[str] | None = None,
) -> bool:
    if item.source == "jobs":
        return True
    if item.source == "polymarket":
        if (item.metadata or {}).get("matched_market"):
            return True
        if conflicted_poly_urls and item.url:
            return fusion._normalize_url(item.url) in conflicted_poly_urls
    return False


def dedupe_items(items: list[schema.SourceItem], threshold: float = 0.7) -> list[schema.SourceItem]:
    """Remove exact-URL and near-duplicate items while keeping earlier, better-scored items.

    Every source first collapses normalized ``(source, url)`` duplicates (using
    ``hn_url`` for Hacker News and canonical ``thread_key`` for Reddit), merging
    enrichment, engagement, and metadata from later copies into the first
    occurrence before text similarity runs so a bare copy never discards a
    saved copy's transcripts, comments, or ``matched_market``.

    Jobs are deduped by normalized URL only: distinct postings on the same
    careers board share heavy boilerplate (company intro, "TL;DR", benefits)
    that trips fuzzy text similarity and collapses unrelated roles (a 26-role
    board fell to 7). A unique posting URL is an unambiguous identity, so use
    it instead.

    Polymarket events that carry a matched market are deduped by normalized
    event URL only for the same reason: "Pro Football: 2027 Champion" and
    "Pro Football: 2027 NFC Champion" share most of their text but are
    different questions. Unmatched Polymarket items share the same URL pass so
    mixed matched/unmatched copies of one event merge and never survive together.
    """
    # Pass 1: collapse exact normalized-URL duplicates in-order, merging
    # enrichment/counts/metadata from later copies without mutating caller inputs.
    # Reused Polymarket slug URLs carrying distinct known event_ids (and ID-less
    # copies at those URLs) get disjoint keys so conflicting events never merge
    # or bridge through an ID-less copy.
    poly_eids = fusion.polymarket_event_ids_by_url(items)
    conflicted_poly_urls = {url for url, eids in poly_eids.items() if len(eids) > 1}
    url_index: dict[tuple[str, str], int] = {}
    cloned_indices: set[int] = set()
    collapsed: list[schema.SourceItem] = []
    for item in items:
        url_key = _url_dedupe_key(item, polymarket_eids_by_url=poly_eids)
        if url_key is None:
            collapsed.append(item)
            continue
        existing_idx = url_index.get(url_key)
        if existing_idx is None:
            url_index[url_key] = len(collapsed)
            collapsed.append(item)
            continue
        if existing_idx not in cloned_indices:
            collapsed[existing_idx] = fusion._clone_source_item(collapsed[existing_idx])
            cloned_indices.add(existing_idx)
        fusion.merge_source_items(collapsed[existing_idx], item)

    # Pass 2: fuzzy near-duplicate removal on remaining distinct-URL items.
    mode = rust_accel.engine_mode()
    use_rust = mode == "rust" or (mode == "auto" and len(collapsed) >= 12)
    if use_rust:
        entries: list[dict[str, str]] = []
        has_jieba_cjk = False
        jieba_active = cjk._jieba is not None
        for item in collapsed:
            if _is_url_only_dedupe(item, conflicted_poly_urls):
                entries.append({"mode": "url_only", "text": ""})
            else:
                text = item_text(item)
                if jieba_active and cjk.has_cjk(text):
                    has_jieba_cjk = True
                    break
                entries.append({"mode": "fuzzy", "text": text})
        if not has_jieba_cjk:
            kept_indices = rust_accel.dedupe_kept_indices(entries, threshold)
            if kept_indices is not None:
                return [collapsed[idx] for idx in kept_indices]

    kept: list[schema.SourceItem] = []
    kept_prepared: list[_PreparedText] = []
    for item in collapsed:
        if _is_url_only_dedupe(item, conflicted_poly_urls):
            kept.append(item)
            continue
        text = item_text(item)
        if not text:
            kept.append(item)
            continue
        prep = _PreparedText(text)
        is_duplicate = False
        for existing_prep in kept_prepared:
            if prepared_similarity(prep, existing_prep) >= threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(item)
            kept_prepared.append(prep)
    return kept
