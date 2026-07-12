"""Normalization of source-specific payloads into the v3 generic item model."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from . import dates, schema


def filter_by_date_range(
    items: list[schema.SourceItem],
    from_date: str,
    to_date: str,
    require_date: bool = False,
) -> list[schema.SourceItem]:
    """Keep only items within the requested window."""
    filtered: list[schema.SourceItem] = []
    for item in items:
        if not item.published_at:
            if not require_date:
                filtered.append(item)
            continue
        if item.published_at < from_date or item.published_at > to_date:
            continue
        filtered.append(item)
    return filtered


def normalize_source_items(
    source: str,
    items: list[dict[str, Any]],
    from_date: str,
    to_date: str,
    freshness_mode: str = "balanced_recent",
) -> list[schema.SourceItem]:
    """Normalize raw source items, filter by date range, with evergreen fallback for how_to queries."""
    source = source.lower()
    normalizers = {
        "reddit": _normalize_reddit,
        "x": _normalize_x,
        "youtube": _normalize_youtube,
        "tiktok": lambda s, i, idx, fd, td: _normalize_shortform_video(s, i, idx, fd, td, "TK", "TikTok post"),
        "instagram": lambda s, i, idx, fd, td: _normalize_shortform_video(s, i, idx, fd, td, "IG", "Instagram reel"),
        "hackernews": _normalize_hackernews,
        "stocktwits": _normalize_stocktwits,
        "dripstack": _normalize_dripstack,
        "bluesky": lambda s, i, idx, fd, td: _normalize_microblog(s, i, idx, fd, td, "BS", "Bluesky post"),
        "truthsocial": lambda s, i, idx, fd, td: _normalize_microblog(s, i, idx, fd, td, "TS", "Truth Social post"),
        "threads": lambda s, i, idx, fd, td: _normalize_microblog(s, i, idx, fd, td, "TH", "Threads post"),
        "xquik": _normalize_x,
        "pinterest": _normalize_pinterest,
        "polymarket": _normalize_polymarket,
        "digg": _normalize_digg,
        "arxiv": _normalize_arxiv,
        "techmeme": _normalize_techmeme,
        "trustpilot": _normalize_trustpilot,
        "grounding": _normalize_grounding,
        "xiaohongshu": _normalize_grounding,
        "github": _normalize_github,
        "perplexity": _normalize_grounding,
        "jobs": _normalize_jobs,
        "linkedin": _normalize_linkedin,
    }
    normalizer = normalizers.get(source)
    if normalizer is None:
        raise ValueError(f"Unsupported source: {source}")
    normalized = [normalizer(source, item, index, from_date, to_date) for index, item in enumerate(items)]
    if source == "jobs":
        # A careers board is a snapshot of CURRENTLY OPEN roles. An open posting
        # is current evidence regardless of when it was posted, so date-windowing
        # it drops still-open roles (the "Founding Research Scientist, Human
        # Simulation" miss: 26 open roles filtered to 3 by a 30-day window).
        # Keep the full board; recency is annotated, not used to drop.
        return normalized
    require_date = source == "grounding"
    filtered = filter_by_date_range(normalized, from_date, to_date, require_date=require_date)
    if filtered:
        return filtered
    if freshness_mode == "evergreen_ok" and source == "youtube":
        if require_date:
            return [item for item in normalized if item.published_at]
        return normalized
    return filtered


def _remap_comments(
    raw: list[Any],
    score_keys: tuple[str, ...],
    excerpt_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Normalize comments from any source into the shared Reddit-compatible shape.

    Downstream code (signals._top_comment_score, render._top_comments_list,
    entity_extract, rerank) all expect `score` and `excerpt`. This helper maps
    per-source field names (YT: likes/text, TikTok: digg_count/text) onto that
    shape while preserving author/date/url passthrough.
    """
    out: list[dict[str, Any]] = []
    for raw_c in raw:
        if not isinstance(raw_c, dict):
            continue
        score = _first_present(raw_c, score_keys, default=0)
        excerpt = _first_present(raw_c, excerpt_keys, default="")
        try:
            score_int = int(score or 0)
        except (TypeError, ValueError):
            score_int = 0
        entry: dict[str, Any] = {
            "score": score_int,
            "excerpt": str(excerpt or "")[:400],
            "author": str(raw_c.get("author") or ""),
            "date": str(raw_c.get("date") or ""),
        }
        if raw_c.get("url"):
            entry["url"] = str(raw_c["url"])
        out.append(entry)
    return out


def _first_present(d: dict[str, Any], keys: tuple[str, ...], default: Any) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _join_comment_excerpts(
    top_comments: list[Any],
    key: str,
    limit: int = 3,
) -> str:
    """Space-join the `key` field from the first `limit` dict-shaped comments."""
    return " ".join(
        str(comment.get(key) or "").strip()
        for comment in top_comments[:limit]
        if isinstance(comment, dict)
    )


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    domain = urlparse(url).netloc.strip().lower()
    return domain or None


def _date_confidence(item: dict[str, Any], from_date: str, to_date: str, default: str = "low") -> str:
    if item.get("date_confidence"):
        return str(item["date_confidence"])
    date_value = item.get("date")
    if not date_value:
        return default
    return dates.get_date_confidence(str(date_value), from_date, to_date)


def _source_item(
    *,
    item_id: str,
    source: str,
    title: str,
    body: str,
    url: str,
    published_at: str | None,
    date_confidence: str,
    relevance_hint: float,
    why_relevant: str,
    author: str | None = None,
    container: str | None = None,
    engagement: dict[str, float | int] | None = None,
    snippet: str = "",
    metadata: dict[str, Any] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title.strip() or body.strip()[:160] or item_id,
        body=body.strip(),
        url=url.strip(),
        author=(author or "").strip() or None,
        container=(container or "").strip() or None,
        published_at=published_at,
        date_confidence=date_confidence,
        engagement=engagement or {},
        relevance_hint=max(0.0, min(1.0, float(relevance_hint or 0.0))),
        why_relevant=why_relevant.strip(),
        snippet=snippet.strip(),
        metadata=metadata or {},
    )


def _normalize_stocktwits(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    meta = item.get("metadata") or {}
    return _source_item(
        item_id=str(item.get("id") or f"ST{index + 1}"),
        source=source,
        title=str(item.get("title") or ""),
        body=str(item.get("snippet") or ""),
        url=str(item.get("url") or ""),
        author=str(item.get("author") or "") or None,
        container=str(meta.get("symbol") or "") or None,
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.7),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=str(item.get("snippet") or "")[:400],
        metadata=meta,   # carries sentiment + symbol-level bull/bear aggregate
    )


def _normalize_dripstack(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for DripStack newsletter search results.

    DripStack returns article metadata from paid financial newsletters.
    No engagement signal — ranking relies on DripStack's own relevanceScore
    (0-100, normalized to 0-1) plus recency. The publication name serves as
    author/attribution (e.g. "SemiAnalysis", "Bloomberg").
    """
    meta = item.get("metadata") or {}
    return _source_item(
        item_id=str(item.get("id") or f"DS{index + 1}"),
        source=source,
        title=str(item.get("title") or ""),
        body=str(item.get("body") or "") or str(item.get("snippet") or "") or str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        author=str(item.get("author") or "") or None,
        container=str(meta.get("publication_slug") or "") or None,
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement={},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=str(item.get("snippet") or "")[:400],
        metadata={
            **meta,
            "publication_slug": meta.get("publication_slug"),
        },
    )


def _normalize_reddit(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    top_comments = item.get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(
        part
        for part in [
            str(item.get("title") or "").strip(),
            str(item.get("selftext") or "").strip(),
            comment_text,
        ]
        if part
    )
    return _source_item(
        item_id=str(item.get("id") or f"R{index + 1}"),
        source=source,
        title=str(item.get("title") or ""),
        body=body,
        url=str(item.get("url") or ""),
        author=None,
        container=str(item.get("subreddit") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or str(item.get("selftext") or "")[:400],
        metadata={
            "top_comments": top_comments,
            "comment_insights": item.get("comment_insights") or [],
        },
    )


def _normalize_x(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    text = str(item.get("text") or "").strip()
    mentioned = item.get("mentioned_handles") or []
    return _source_item(
        item_id=str(item.get("id") or f"X{index + 1}"),
        source=source,
        title=text[:140] or f"X post {index + 1}",
        body=text,
        url=str(item.get("url") or ""),
        author=str(item.get("author_handle") or "").lstrip("@"),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        metadata={"mentioned_handles": list(mentioned)} if mentioned else {},
    )


def _normalize_jobs(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    description = str(item.get("description") or item.get("snippet") or "").strip()
    title = str(item.get("title") or "").strip()
    department = str(item.get("department") or "").strip()
    location = str(item.get("location") or "").strip()
    body = "\n".join(part for part in [title, department, location, description] if part)
    provider = str(item.get("provider") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"J{index + 1}"),
        source=source,
        title=title or f"Job posting {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=provider or None,
        container=department or None,
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement={"open_roles": 1},
        relevance_hint=item.get("relevance", 0.65),
        why_relevant=str(item.get("why_relevant") or "Public job posting"),
        snippet=description[:500],
        metadata={
            "provider": provider,
            "department": department,
            "departments": item.get("departments") or ([department] if department else []),
            "location": location,
            "offices": item.get("offices") or [],
            "board_token": item.get("board_token") or "",
            "source_url": item.get("source_url") or "",
            "source_domain": item.get("source_domain") or _domain_from_url(str(item.get("url") or "")) or "",
        },
    )


def _normalize_youtube(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    transcript = str(item.get("transcript_snippet") or "").strip()
    description = str(item.get("description") or "").strip()
    title = str(item.get("title") or "").strip()
    highlights = item.get("transcript_highlights") or []
    metadata: dict[str, Any] = {}
    if highlights:
        metadata["transcript_highlights"] = highlights
    if item.get("captions_disabled"):
        # Surfaced for quality_nudge: uploader disabled captions, so this
        # video should be subtracted from the degraded-transcript-ratio
        # denominator (it was never going to produce a transcript).
        metadata["captions_disabled"] = True
    metadata["top_comments"] = _remap_comments(
        item.get("top_comments") or [],
        score_keys=("score", "likes"),
        excerpt_keys=("excerpt", "text"),
    )
    return _source_item(
        item_id=str(item.get("video_id") or item.get("id") or f"YT{index + 1}"),
        source=source,
        title=title,
        body="\n".join(part for part in [title, description, transcript] if part),
        url=str(item.get("url") or ""),
        author=str(item.get("channel_name") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=transcript,
        metadata=metadata,
    )


def _normalize_shortform_video(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
    id_prefix: str,
    default_title: str,
) -> schema.SourceItem:
    """Shared normalizer for TikTok and Instagram (identical structure)."""
    caption = str(item.get("caption_snippet") or "").strip()
    text = str(item.get("text") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"{id_prefix}{index + 1}"),
        source=source,
        title=text[:140] or caption[:140] or f"{default_title} {index + 1}",
        body="\n".join(part for part in [text, caption] if part),
        url=str(item.get("url") or ""),
        author=str(item.get("author_name") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=caption,
        metadata={
            "hashtags": item.get("hashtags") or [],
            "top_comments": _remap_comments(
                item.get("top_comments") or [],
                # Instagram comments use comment_like_count as the vote field
                # (ScrapeCreators /v2/instagram/post/comments); digg_count/likes
                # kept for shape compatibility.
                score_keys=("score", "comment_like_count", "digg_count", "likes"),
                excerpt_keys=("excerpt", "text"),
            ),
        },
    )


def _normalize_pinterest(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for Pinterest pins (visual content with descriptions).

    Saves are the primary engagement signal, analogous to likes/upvotes.
    """
    description = str(item.get("description") or "").strip()
    return _source_item(
        item_id=str(item.get("pin_id") or item.get("id") or f"PI{index + 1}"),
        source=source,
        title=description[:140] or f"Pinterest pin {index + 1}",
        body=description,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or ""),
        container=str(item.get("board") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="low"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=description[:400],
    )


def _normalize_hackernews(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    top_comments = item.get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "text")
    title = str(item.get("title") or "").strip()
    body = "\n".join(part for part in [title, str(item.get("text") or "").strip(), comment_text] if part)
    return _source_item(
        item_id=str(item.get("id") or f"HN{index + 1}"),
        source=source,
        title=title or f"HN story {index + 1}",
        body=body,
        url=str(item.get("url") or item.get("hn_url") or ""),
        author=str(item.get("author") or ""),
        container="Hacker News",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text,
        metadata={
            "hn_url": item.get("hn_url"),
            "top_comments": top_comments,
            "comment_insights": item.get("comment_insights") or [],
        },
    )


def _normalize_microblog(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
    id_prefix: str,
    default_title: str,
) -> schema.SourceItem:
    """Shared normalizer for Bluesky and Truth Social (identical structure)."""
    text = str(item.get("text") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"{id_prefix}{index + 1}"),
        source=source,
        title=text[:140] or f"{default_title} {index + 1}",
        body=text,
        url=str(item.get("url") or ""),
        author=str(item.get("handle") or item.get("author_handle") or "").lstrip("@"),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        metadata={"display_name": item.get("display_name")},
    )


def _normalize_digg(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for Digg AI 1000 clusters.

    Each cluster is one item. The TLDR carries the most useful body for
    rerank and synthesis. Top-ranked X posts attached at search time are
    passed through under metadata['posts'] so render can emit them as
    inline 'via Digg' quotes.
    """
    title = str(item.get("title") or "").strip()
    tldr = str(item.get("tldr") or "").strip()
    body = "\n\n".join(part for part in [title, tldr] if part)
    posts = item.get("posts") or []
    if not isinstance(posts, list):
        posts = []
    cluster_url_id = str(item.get("id") or f"DG{index + 1}")
    return _source_item(
        item_id=cluster_url_id,
        source=source,
        title=title or f"Digg cluster {index + 1}",
        body=body,
        url=str(item.get("url") or f"https://di.gg/ai/{cluster_url_id}"),
        author="",
        container="Digg",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=tldr[:400],
        metadata={
            "clusterUrlId": cluster_url_id,
            "tldr": tldr,
            "rank": (item.get("engagement") or {}).get("rank"),
            "uniqueAuthors": (item.get("engagement") or {}).get("uniqueAuthors"),
            "postCount": (item.get("engagement") or {}).get("postCount"),
            "firstPostAge": item.get("first_post_age"),
            "posts": posts,
        },
    )


def _normalize_arxiv(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for arXiv papers.

    The abstract (summary) is the body that feeds rerank and synthesis. arXiv
    has no engagement signal, so engagement is empty and ranking leans on
    relevance and recency.
    """
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    body = "\n\n".join(part for part in [title, summary] if part)
    authors = item.get("authors") or []
    if not isinstance(authors, list):
        authors = []
    paper_id = str(item.get("id") or f"AX{index + 1}")
    return _source_item(
        item_id=paper_id,
        source=source,
        title=title or f"arXiv paper {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or "") or None,
        container="arXiv",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement={},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=summary[:400],
        metadata={
            "authors": authors,
            "summary": summary,
        },
    )


def _normalize_techmeme(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for Techmeme headlines.

    The headline is both title and body (Techmeme carries no abstract). The
    publication is the container/author. No engagement signal in the search
    shape, so ranking leans on relevance and recency.
    """
    title = str(item.get("title") or "").strip()
    source_name = str(item.get("source_name") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"TM{index + 1}"),
        source=source,
        title=title or f"Techmeme headline {index + 1}",
        body=title,
        url=str(item.get("url") or ""),
        author=source_name or None,
        container=source_name or "Techmeme",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="low"),
        engagement={},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=title[:400],
        metadata={
            "publication": source_name,
        },
    )


def _normalize_trustpilot(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for Trustpilot company sentiment.

    One item per company. The AI summary (already balanced positive/negative)
    is the body. TrustScore and review count are engagement and metadata.
    """
    title = str(item.get("title") or "").strip()
    name = str(item.get("name") or "").strip()
    summary = str(item.get("summary") or "").strip()
    body = "\n\n".join(part for part in [title, summary] if part)
    return _source_item(
        item_id=str(item.get("id") or f"TP{index + 1}"),
        source=source,
        title=title or (f"{name} on Trustpilot" if name else f"Trustpilot reviews {index + 1}"),
        body=body,
        url=str(item.get("url") or ""),
        author=name or None,
        container="Trustpilot",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="low"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.6),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=summary[:400],
        metadata={
            "name": name,
            "trustScore": item.get("trustScore"),
            "reviewCount": item.get("reviewCount"),
            "aiSummary": summary,
        },
    )


def _normalize_polymarket(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    question = str(item.get("question") or "").strip()
    engagement = {
        "volume": item.get("volume1mo") or item.get("volume24hr") or 0,
        "liquidity": item.get("liquidity") or 0,
    }
    return _source_item(
        item_id=str(item.get("event_id") or item.get("id") or f"PM{index + 1}"),
        source=source,
        title=title or question or f"Polymarket event {index + 1}",
        body="\n".join(part for part in [title, question, str(item.get("price_movement") or "")] if part),
        url=str(item.get("url") or ""),
        author=None,
        container="Polymarket",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=engagement,
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=str(item.get("price_movement") or ""),
        metadata={
            "event_id": item.get("event_id"),
            "question": question,
            "end_date": item.get("end_date"),
            "outcome_prices": item.get("outcome_prices") or [],
            "outcomes_remaining": item.get("outcomes_remaining"),
        },
    )



def _normalize_github(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    snippet_text = str(item.get("snippet") or "").strip()
    top_comments = item.get("metadata", {}).get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(part for part in [title, snippet_text, comment_text] if part)
    metadata = item.get("metadata") or {}
    return _source_item(
        item_id=str(item.get("id") or f"GH{index + 1}"),
        source=source,
        title=title or f"GitHub item {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or ""),
        container=str(item.get("container") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or snippet_text[:400],
        metadata={
            "top_comments": top_comments,
            "labels": metadata.get("labels") or [],
            "state": metadata.get("state", ""),
            "is_pr": metadata.get("is_pr", False),
        },
    )

def _normalize_grounding(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or "").strip()
    url = str(item.get("url") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"W{index + 1}"),
        source=source,
        title=title or _domain_from_url(url) or f"Web result {index + 1}",
        body="\n".join(part for part in [title, snippet] if part),
        url=url,
        author=None,
        container=str(item.get("source_domain") or _domain_from_url(url) or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=snippet,
        metadata=item.get("metadata") or {},
    )


def _normalize_linkedin(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for LinkedIn posts and articles via ScrapeCreators.

    A LinkedIn article (Pulse long-form, under a /pulse/ URL) is treated as
    high signal: it ranks above ordinary posts. Detection is belt-and-suspenders
    — honor the parser's `is_article` flag, and re-derive from the URL so an
    article still ranks high even if the flag wasn't set upstream.
    """
    text = str(item.get("text") or "").strip()
    author = str(item.get("author") or "").strip()
    url = str(item.get("url") or "").strip()
    is_article = bool(item.get("is_article")) or "/pulse/" in url.lower()
    kind = "article" if is_article else "post"
    default_relevance = 0.9 if is_article else 0.5
    return _source_item(
        item_id=str(item.get("id") or f"LI{index + 1}"),
        source=source,
        title=text[:140] or f"LinkedIn {kind} {index + 1}",
        body=text,
        url=url,
        author=author,
        container="LinkedIn Article" if is_article else "LinkedIn",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="medium"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", default_relevance),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=text[:200],
        metadata={"author_display": author, "is_article": is_article},
    )
