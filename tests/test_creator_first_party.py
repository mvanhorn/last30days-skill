"""Named creator accounts (--ig-creators / TikTok --creators) are first-party evidence.

The production failure this pins (issue #1101): creator reels are fetched and
merged into the stream correctly, but ``signals.prune_low_relevance`` then
drops them - a creator's caption rarely contains the topic's literal tokens,
so it scores under the relevance floor, and the first-party exemption covered
only --x-handle / --github-user / --x-related / topic-mention handles. A mixed
batch (keyword hits survive, creator reels fail) therefore loses every creator
item silently: the ``filtered or items`` rescue only fires when *everything*
fails, and no drop was logged.
"""

import contextlib
import io

from lib import pipeline, schema, signals


def _shortform_item(item_id, source, author, relevance, engagement=None):
    item = schema.SourceItem(
        item_id=item_id,
        source=source,
        title="",
        body="post body",
        url=f"https://{source}.com/{author}/{item_id}",
        author=author,
        engagement=engagement or {},
    )
    item.local_relevance = relevance
    return item


def _annotate(items):
    """Populate engagement_score the way the real pipeline does."""
    scores = signals.normalize([signals.engagement_raw(i) for i in items])
    for item, score in zip(items, scores, strict=True):
        item.engagement_score = score
    return items


def _mixed_ig_batch():
    """The named creator's reel scores 0.0 (a caption rarely repeats the
    topic's tokens) and sits under the 1000-view floor; the keyword hit and
    the X post clear their floors."""
    return [
        _shortform_item("ig-creator", "instagram", "linkuptv", 0.0,
                        {"views": 400, "likes": 12, "comments": 1}),
        _shortform_item("ig-random", "instagram", "random_reposter", 0.02,
                        {"views": 300, "likes": 4, "comments": 0}),
        _shortform_item("x-hit", "x", "someone", 0.4,
                        {"likes": 90, "reposts": 9}),
    ]


def test_named_ig_creator_survives_mixed_batch():
    items = _annotate(_mixed_ig_batch())
    kept = signals.prune_low_relevance(items, first_party_handles={"linkuptv"})
    ids = [item.item_id for item in kept]
    assert "ig-creator" in ids, (
        "a reel by an account named via --ig-creators is evidence by "
        "provenance; pruning it from a mixed batch is the #1101 defect"
    )
    assert "ig-random" not in ids, (
        "the exemption must stay scoped to named creators, not blanket-keep "
        "low-relevance reels"
    )


def test_named_tiktok_creator_survives_mixed_batch():
    """TikTok --creators had the identical gap; tiktok author is the unique_id
    handle, so the normalized comparison is symmetric with Instagram."""
    items = _annotate([
        _shortform_item("tt-creator", "tiktok", "mixtapemadness", 0.0,
                        {"views": 800, "likes": 30, "comments": 2}),
        _shortform_item("x-hit", "x", "someone", 0.4,
                        {"likes": 90, "reposts": 9}),
    ])
    kept = signals.prune_low_relevance(items, first_party_handles={"mixtapemadness"})
    assert "tt-creator" in [item.item_id for item in kept]


def test_named_creator_handle_normalization():
    """@ prefix and mixed case on either side must still match."""
    items = _annotate([
        _shortform_item("ig-creator", "instagram", "@LinkUpTV", 0.0,
                        {"views": 400, "likes": 12, "comments": 1}),
        _shortform_item("x-hit", "x", "someone", 0.4,
                        {"likes": 90, "reposts": 9}),
    ])
    kept = signals.prune_low_relevance(items, first_party_handles={"linkuptv"})
    assert "ig-creator" in [item.item_id for item in kept]


def test_unnamed_creator_content_still_pruned():
    """Characterizes the defect: without the named-creator wiring the batch
    drops them. If this starts failing, the floor changed and the exemption's
    justification needs rechecking."""
    items = _annotate(_mixed_ig_batch())
    kept = signals.prune_low_relevance(items)
    assert all(item.author != "linkuptv" for item in kept)


def test_creator_handles_scoped_to_their_platform():
    """The wiring this whole file depends on: creator flags must produce the
    platform-scoped exemption map the prunes receive, normalized the same way
    item authors are."""
    scoped = pipeline._creator_first_party_by_source(
        ["@MixtapeMadness", "  "], ["LinkUpTV"]
    )
    assert scoped == {
        "instagram": {"linkuptv"},
        "tiktok": {"mixtapemadness"},
    }


def test_ig_creator_does_not_exempt_same_name_tiktok_account():
    """An account named via --ig-creators is an Instagram account; a same-name
    TikTok account is a different person and gets no exemption."""
    items = _annotate([
        _shortform_item("tt-same-name", "tiktok", "linkuptv", 0.0,
                        {"views": 800, "likes": 30, "comments": 2}),
        _shortform_item("x-hit", "x", "someone", 0.4,
                        {"likes": 90, "reposts": 9}),
    ])
    kept = signals.prune_low_relevance(
        items, first_party_by_source={"instagram": {"linkuptv"}}
    )
    assert "tt-same-name" not in [item.item_id for item in kept]


def test_scoped_exemption_keeps_creator_on_own_platform():
    items = _annotate(_mixed_ig_batch())
    kept = signals.prune_low_relevance(
        items, first_party_by_source={"instagram": {"linkuptv"}}
    )
    ids = [item.item_id for item in kept]
    assert "ig-creator" in ids
    assert "ig-random" not in ids


def test_deferred_x_floor_ignores_creator_only_handles():
    """The deferred X prune receives resolved_handles minus the creator sets:
    an X account that merely shares a creator's handle still faces the floor."""
    items = _annotate([
        _shortform_item("x-same-name", "x", "linkuptv", 0.0, {"likes": 0}),
        _shortform_item("x-hit", "x", "someone", 0.4, {"likes": 90, "reposts": 9}),
    ])
    kept = signals.prune_low_relevance(
        items,
        first_party_handles=set(),  # creator handles were subtracted out
        first_party_by_source={"instagram": {"linkuptv"}},
    )
    assert "x-same-name" not in [item.item_id for item in kept]


def _raw_ig(item_id, author, text, views):
    return {
        "id": item_id,
        "text": text,
        "url": f"https://instagram.com/reel/{item_id}",
        "author_name": author,
        "date": "2026-09-01",
        "engagement": {"views": views, "likes": 3, "comments": 0},
    }


def _normalize_quietly(raw, ranking_query="fly.io deploy guide"):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        kept = pipeline._normalize_score_dedupe(
            "instagram",
            raw,
            "2026-08-06",
            "2026-09-05",
            freshness_mode="balanced_recent",
            ranking_query=ranking_query,
        )
    return kept, buf.getvalue()


def test_prune_drop_is_logged_with_source_and_count():
    raw = [
        _raw_ig("keep1", "acct", "fly.io deploy guide walkthrough", 5000),
        _raw_ig("drop1", "acct2", "unrelated dance clip", 100),
    ]
    kept, logs = _normalize_quietly(raw)
    assert [item.item_id for item in kept] == ["keep1"], (
        "the off-topic low-engagement reel should still be pruned"
    )
    assert "prune" in logs.lower() and "1" in logs, (
        "a silent drop is the observability half of #1101: the reporter saw "
        "36 reels fetched, 0 reported, and no line explaining why"
    )


def test_no_log_when_nothing_is_pruned():
    raw = [
        _raw_ig("keep1", "acct", "fly.io deploy guide walkthrough", 5000),
        _raw_ig("keep2", "acct2", "deploying on fly.io with a Dockerfile", 3000),
    ]
    kept, logs = _normalize_quietly(raw)
    assert len(kept) == 2
    assert "prune" not in logs.lower(), (
        "a clean stream must not emit a misleading drop line"
    )


def test_all_weak_rescue_is_not_logged_as_a_drop():
    """When every item fails and prune_low_relevance keeps the originals, the
    stream lost nothing - logging a drop would be false."""
    raw = [
        _raw_ig("weak1", "acct", "unrelated clip one", 100),
        _raw_ig("weak2", "acct2", "unrelated clip two", 90),
    ]
    kept, logs = _normalize_quietly(raw)
    assert len(kept) == 2, "the filtered-or-items rescue keeps weak sole batches"
    assert "prune" not in logs.lower()
def test_creator_only_handles_preserves_x_provenance():
    """--x-handle foo --ig-creators foo names the same person twice; the X
    exemption must survive the creator-only subtraction (Greptile re-review
    on f49c7bf)."""
    creators = pipeline._creator_first_party_by_source([], ["Foo"])
    real_x = {"foo"}  # normalized --x-handle / @mention / Phase 2 discovery
    creator_only = pipeline._creator_only_handles(creators, real_x)
    assert creator_only == set(), "foo has X provenance and is not creator-only"
    creators = pipeline._creator_first_party_by_source([], ["bar"])
    creator_only = pipeline._creator_only_handles(creators, real_x)
    assert creator_only == {"bar"}, "bar was named only as an IG creator"


def test_topic_token_alone_is_not_x_provenance():
    """A creator handle that also appears as a plain topic token keeps no X
    exemption: X provenance is real_x_handles only (Greptile re-review on
    2a62aea). The pipeline passes real_x_handles as the provenance set, so a
    topic-token-only handle lands in creator_only and out of x_floor_handles."""
    creators = pipeline._creator_first_party_by_source([], ["linkuptv"])
    # topic mention of "linkuptv" without an @ or X flag: not in real_x_handles
    creator_only = pipeline._creator_only_handles(creators, {"someone_else"})
    assert creator_only == {"linkuptv"}
