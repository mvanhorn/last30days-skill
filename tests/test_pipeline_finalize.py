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
