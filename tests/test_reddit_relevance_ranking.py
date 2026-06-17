"""Tests for relevance-floor + relevance-first ranking in the Reddit paths.

Reddit's highest-upvote content (relationship drama, AITA, viral news) often
has near-zero topic overlap. Before this change both the keyed (ScrapeCreators)
and keyless (RSS) paths ranked the final list engagement-first, so a viral
off-topic post outranked on-topic posts. These tests pin the new behavior:
on-topic posts rank first and pure zero-overlap posts are dropped when anything
relevant remains.
"""

from unittest import mock

from lib import reddit, reddit_keyless


# --------------------------------------------------------------------------- #
# Shared ranking key
# --------------------------------------------------------------------------- #

class TestRelevanceRankKey:
    def test_on_topic_low_upvote_beats_off_topic_viral(self):
        on_topic = {"relevance": 0.3, "engagement": {"score": 10, "num_comments": 5}}
        off_topic = {"relevance": 0.0, "engagement": {"score": 99999, "num_comments": 4000}}
        assert reddit._relevance_rank_key(on_topic) > reddit._relevance_rank_key(off_topic)

    def test_engagement_bonus_is_bounded(self):
        # Even astronomical engagement adds at most 0.25, so it can never lift a
        # relevance-0 post above a post that cleared the floor.
        huge = {"relevance": 0.0, "engagement": {"score": 10**9, "num_comments": 10**9}}
        assert reddit._relevance_rank_key(huge) <= 0.25 + 1e-9
        floored = {"relevance": 0.3, "engagement": {"score": 0, "num_comments": 0}}
        assert reddit._relevance_rank_key(floored) > reddit._relevance_rank_key(huge)

    def test_keyless_key_matches_keyed_semantics(self):
        on_topic = {"relevance": 0.3, "engagement": {"score": 10, "num_comments": 5}}
        off_topic = {"relevance": 0.0, "engagement": {"score": 99999, "num_comments": 4000}}
        assert reddit_keyless._relevance_rank_key(on_topic) > reddit_keyless._relevance_rank_key(off_topic)


# --------------------------------------------------------------------------- #
# Keyed path (reddit.search_reddit)
# --------------------------------------------------------------------------- #

def _raw(rid, title, ups, sub):
    return {
        "id": f"t3_{rid}",
        "title": title,
        "selftext": "",
        "permalink": f"/r/{sub}/comments/{rid}/post/",
        "subreddit": sub,
        "created_utc": 1716000000,  # 2024-05-18, kept by a wide date range
        "ups": ups,
        "num_comments": max(1, ups // 10),
    }


class TestKeyedRanking:
    def test_on_topic_outranks_viral_and_zero_overlap_dropped(self):
        topic = "electric vehicle home charging"
        on_topic = _raw("aaa", "Electric vehicle home charging setup guide", 5, "electricvehicles")
        viral = _raw("bbb", "AITA for not sharing my lottery winnings", 99999, "AmItheAsshole")

        with mock.patch.object(reddit, "_global_search", return_value=[on_topic, viral]), \
             mock.patch.object(reddit, "_subreddit_search", return_value=[]):
            result = reddit.search_reddit(topic, "2000-01-01", "2100-01-01", depth="default", token="x")

        items = result["items"]
        urls = [it["url"] for it in items]
        # Zero-overlap viral post is stripped because an on-topic post exists.
        assert any("electricvehicles" in u for u in urls)
        assert not any("AmItheAsshole" in u for u in urls)
        # On-topic post leads.
        assert "electricvehicles" in items[0]["url"]


# --------------------------------------------------------------------------- #
# Keyless path (reddit_keyless.search_and_enrich)
# --------------------------------------------------------------------------- #

def _kpost(rid, rel, score, date="2026-05-20"):
    return {
        "id": "", "title": f"Post {rid}", "url": f"https://www.reddit.com/r/t/comments/{rid}/p/",
        "score": score, "num_comments": score, "subreddit": "t", "created_utc": None,
        "author": "u", "selftext": "", "date": date,
        "engagement": {"score": score, "num_comments": score, "upvote_ratio": None},
        "relevance": rel, "why_relevant": "Reddit RSS", "metadata": {},
    }


class TestKeylessRanking:
    def test_relevance_first_and_zero_overlap_dropped(self):
        on_strong = _kpost("aaa", 0.5, 10)
        on_weak = _kpost("bbb", 0.2, 5000)
        off_viral = _kpost("ccc", 0.0, 99999)

        with mock.patch.object(reddit_keyless, "_discover",
                               return_value=[off_viral, on_weak, on_strong]), \
             mock.patch.object(reddit_keyless, "_enrich", side_effect=lambda posts, depth: posts):
            out = reddit_keyless.search_and_enrich(
                "some topic", "2026-05-07", "2026-06-06", depth="default")

        urls = [p["url"] for p in out]
        # Zero-overlap viral post dropped; on-topic posts kept, strongest first.
        assert "ccc" not in "".join(urls)
        assert out[0]["url"].endswith("/aaa/p/")
        assert len(out) == 2
