"""The topic subject gets a higher per-author cap than incidental authors.

The flat cap of 3 is anti-flooding protection and is right for third parties.
But on a person or company topic the subject is the point of the query: the
measured 'Peter Steinberger steipete' run recovered 8 subject-authored posts,
and a flat cap would discard 5 of them after the rest of Phase A worked to
retrieve and keep them.

The raised cap stays bounded so a prolific subject cannot crowd out all
commentary about them.
"""

from lib import fusion, schema


def _cand(cid: str, author: str) -> schema.Candidate:
    url = f"https://x.com/{author}/status/{cid}"
    cand = schema.Candidate(
        candidate_id=cid,
        item_id=f"i{cid}",
        source="x",
        title=f"post {cid}",
        url=url,
        snippet="s",
        subquery_labels=["primary"],
        native_ranks={"primary:x": 1},
        local_relevance=0.5,
        freshness=80,
        engagement=50,
        source_quality=0.68,
        rrf_score=0.02,
    )
    cand.source_items = [
        schema.SourceItem(
            item_id=f"i{cid}", source="x", title=f"post {cid}",
            body="b", url=url, author=author,
        )
    ]
    return cand


def _subject_posts(n: int, author: str = "steipete"):
    return [_cand(str(i), author) for i in range(1, n + 1)]


def test_third_party_author_is_still_capped_at_three():
    kept = fusion._apply_per_author_cap(_subject_posts(8, author="rando"))
    assert len(kept) == 3, "the anti-flooding cap must be unchanged for third parties"


def test_subject_keeps_more_than_three_posts():
    kept = fusion._apply_per_author_cap(
        _subject_posts(8), first_party_handles={"steipete"}
    )
    assert len(kept) > 3, (
        "the subject of the topic was capped at 3 of 8 recovered posts, "
        "discarding evidence the rest of Phase A worked to keep"
    )


def test_subject_cap_is_bounded():
    """A prolific subject must not fill the pool."""
    kept = fusion._apply_per_author_cap(
        _subject_posts(40), first_party_handles={"steipete"}
    )
    assert len(kept) <= fusion._MAX_ITEMS_PER_FIRST_PARTY_AUTHOR
    assert len(kept) < 40


def test_mixed_pool_caps_each_author_by_its_own_rule():
    pool = _subject_posts(8) + [_cand(f"r{i}", "rando") for i in range(1, 8)]
    kept = fusion._apply_per_author_cap(pool, first_party_handles={"steipete"})
    subject = [c for c in kept if c.source_items[0].author == "steipete"]
    third = [c for c in kept if c.source_items[0].author == "rando"]
    assert len(subject) > 3
    assert len(third) == 3


def test_no_handles_behaves_exactly_as_before():
    pool = _subject_posts(8)
    assert len(fusion._apply_per_author_cap(pool)) == \
        len(fusion._apply_per_author_cap(pool, first_party_handles=set()))


def test_handles_are_matched_case_insensitively():
    kept = fusion._apply_per_author_cap(
        _subject_posts(8), first_party_handles={"SteiPete"}
    )
    assert len(kept) > 3


def test_ordering_within_an_author_is_preserved():
    """Candidates arrive sorted by quality; the cap keeps the best N."""
    kept = fusion._apply_per_author_cap(
        _subject_posts(8), first_party_handles={"steipete"}
    )
    assert [c.candidate_id for c in kept] == \
        [str(i) for i in range(1, len(kept) + 1)]


def test_weighted_rrf_accepts_first_party_handles():
    import inspect
    sig = inspect.signature(fusion.weighted_rrf)
    assert "first_party_handles" in sig.parameters, (
        "the fusion entry point must accept the run's resolved handles or the "
        "raised cap can never fire in production"
    )
    assert sig.parameters["first_party_handles"].default in (None, frozenset()), (
        "must stay optional so the discovery caller is unaffected"
    )
