"""Stored Reddit comment text ends at a sentence or a word, not mid-word.

Real run, 2026-11-10: comments were stored cut at 200 characters
("...thinking everyone now wants to be a progressive socialist. Th",
"...Giving it away also li") and those strings become quote lines.
"""

from unittest import mock

import pytest

from lib import reddit, reddit_arctic, reddit_enrich, reddit_shreddit, snippet

SENTENCES = (
    "The people who built these companies took real risks for decades and it paid off. "
    "A lot of the commentary pretends that never happened and that wealth simply appears. "
    "Politicians read one poll and start thinking everyone now wants to be a progressive socialist. "
    "That is not what the numbers say when you look at who actually turns out to vote in off years. "
    "Giving it away also limits what the next round of founders can raise from the same investors. "
    "None of this means the current arrangement is fair, only that the alternatives are not free."
)
RUN_ON = " ".join(["supercalifragilistic"] * 40)  # no sentence end anywhere


def test_short_text_is_returned_whole():
    assert snippet.cut_quote("  Why lie about it more?  ") == "Why lie about it more?"


def test_long_text_ends_at_a_sentence_within_the_limit():
    cut = snippet.cut_quote(SENTENCES)

    assert 200 < len(cut) <= 400
    assert cut.endswith("off years.")
    assert SENTENCES.startswith(cut)
    assert not cut.endswith("...")


def test_no_sentence_end_cuts_at_a_word_boundary():
    cut = snippet.cut_quote(RUN_ON)

    assert len(cut) <= 400
    assert cut.split()[-1] == "supercalifragilistic"
    assert RUN_ON[len(cut)] == " "


def test_sentence_end_far_from_the_limit_is_not_used():
    text = "Short opener. " + RUN_ON

    cut = snippet.cut_quote(text)

    assert len(cut) > 300 and cut.split()[-1] == "supercalifragilistic"


def test_one_unbroken_token_is_cut_hard():
    assert snippet.cut_quote("x" * 900) == "x" * 400


def test_decimal_points_and_urls_are_not_sentence_ends():
    text = ("word " * 70) + "the price moved 3.5 percent on example.com today and kept going " + ("more " * 30)

    cut = snippet.cut_quote(text)

    assert not cut.endswith("3.") and not cut.endswith("example.")


def test_shreddit_comments_are_stored_cut_cleanly():
    html = f"""
<shreddit-comment-tree-stats total-comments="1" sort="TOP"></shreddit-comment-tree-stats>
<shreddit-comment created="2026-11-09T12:45:51+0000" author="reader" thingid="t1_aaa"
  permalink="/r/elonmusk/comments/x1/comment/aaa/" score="56" depth="0">
  <div id="t1_aaa-post-rtjson-content"><div><p>{SENTENCES}</p></div></div>
</shreddit-comment>
"""
    comment = reddit_shreddit.parse_comments(html)[0]

    assert comment["excerpt"].endswith("off years.")
    assert len(comment["excerpt"]) > 200


@pytest.fixture
def _no_arctic_network():
    # Overrides conftest's stub so the real archive adapter runs (http.get mocked).
    reddit_arctic.reset_memo()
    yield
    reddit_arctic.reset_memo()


def test_arctic_comments_are_stored_cut_cleanly(_no_arctic_network):
    row = {"author": "reader", "body": SENTENCES, "score": 7,
           "permalink": "/r/elonmusk/comments/abc/x/c1/", "created_utc": 1794268800}
    with mock.patch.object(reddit_arctic.http, "get", return_value={"data": [row]}):
        comment = reddit_arctic.fetch_post_comments("abc")[0]

    assert comment["excerpt"].endswith("off years.")


def test_scrapecreators_search_enrichment_stores_clean_excerpts():
    raw = [{"body": SENTENCES, "ups": 9, "author": f"a{i}", "permalink": f"/c{i}/"} for i in range(2)]
    items = [{"id": "R1", "url": "https://www.reddit.com/r/elonmusk/comments/abc/x/",
              "engagement": {"score": 10}}]

    with mock.patch.object(reddit, "fetch_post_comments", return_value=raw):
        out = reddit.enrich_with_comments(items, "dummy-key", budget_seconds=5)

    excerpts = [comment["excerpt"] for comment in out[0]["top_comments"]]
    assert len(excerpts) == 2 and all(text.endswith("off years.") for text in excerpts)


@pytest.mark.parametrize("position", [0, 1])
def test_scrapecreators_enrichment_stores_clean_excerpts(position):
    raw = [{"body": SENTENCES, "ups": 9, "author": f"a{i}", "permalink": f"/c{i}/"} for i in range(2)]
    item = {"url": "https://www.reddit.com/r/elonmusk/comments/abc/x/"}

    with mock.patch.object(reddit, "fetch_post_comments", return_value=raw):
        out = reddit_enrich.enrich_reddit_item_sc(item, "dummy-key")

    assert out["top_comments"][position]["excerpt"].endswith("off years.")
