"""Report finalize collapses same-URL copies of a thread and keeps enrichment."""

from lib import pipeline, schema


def _item(item_id, url, rank, comments=None):
    item = schema.SourceItem(
        item_id=item_id,
        source="reddit",
        title="Kanye West's sold-out Russia shows canceled",
        body="Kanye West's sold-out Russia shows canceled",
        url=url,
        metadata={"top_comments": comments or []},
    )
    item.local_rank_score = rank
    return item


def test_finalize_merges_same_url_copies_and_keeps_comments():
    url = "https://www.reddit.com/r/Music/comments/1vy0ilk/kanye_wests_soldout_russia/"
    bare = _item("R1", url, 0.677)
    rich = _item("R1", url, 0.487, comments=[{"score": 3293, "author": "u/tableleg7", "excerpt": "Putin"}])

    finalized = pipeline._finalize_items_by_source({"reddit": [bare, rich]}, topic="kanye west", mock=True)

    items = finalized["reddit"]
    assert len(items) == 1
    assert items[0].metadata["top_comments"][0]["score"] == 3293


def _hn(story_id, points, comments, rank):
    item = schema.SourceItem(
        item_id=story_id,
        source="hackernews",
        title="SQLite 3.54.0 Released",
        body="SQLite 3.54.0 Released",
        url="https://sqlite.org/releaselog/3_54_0.html",
        engagement={"points": points, "comments": len(comments)},
        metadata={"hn_url": f"https://news.ycombinator.com/item?id={story_id}", "top_comments": comments},
    )
    item.local_rank_score = rank
    return item


def test_finalize_keeps_two_hn_submissions_of_one_url_apart():
    comment = {"score": 0, "author": "ciupicri", "excerpt": "How nice of them to support Windows XP until now."}
    first = _hn("50040360", 11, [], 0.9)
    second = _hn("50032781", 6, [comment], 0.8)

    finalized = pipeline._finalize_items_by_source({"hackernews": [first, second]}, topic="sqlite", mock=True)

    # Text dedupe may drop the near-identical second story, but no story may
    # carry another story's comment count or comments.
    for item in finalized["hackernews"]:
        assert item.metadata["hn_url"].endswith(item.item_id)
        assert item.engagement["comments"] == len(item.metadata["top_comments"])
    by_id = {item.item_id: item for item in finalized["hackernews"]}
    assert by_id["50040360"].engagement == {"points": 11, "comments": 0}
