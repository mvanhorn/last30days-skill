"""Tests for scripts/lib/reddit_keyless.py — tiered keyless Reddit pipeline."""

from unittest import mock

from lib import reddit_keyless


def _post(i, date="2026-05-20", rel=0.0):
    url = f"https://www.reddit.com/r/test/comments/{i:06d}/post_{i}/"
    return {
        "id": "", "title": f"Post {i}", "url": url, "score": 0, "num_comments": 0,
        "subreddit": "test", "created_utc": None, "author": "u", "selftext": "",
        "date": date, "engagement": {"score": 0, "num_comments": 0, "upvote_ratio": None},
        "relevance": rel, "why_relevant": "Reddit RSS", "metadata": {},
    }


def _scored(i, score, ncmt=0):
    p = _post(i)
    p["score"] = score
    p["num_comments"] = ncmt
    p["engagement"]["score"] = score
    p["engagement"]["num_comments"] = ncmt
    p["why_relevant"] = "Reddit listing"
    p["metadata"] = {"post_id": f"{i:06d}"}
    return p


class TestDiscoveryTierOrder:
    """Tier 0 (.json) is tried first; RSS + scored listings are the keyless path."""

    def test_tier0_success_skips_keyless(self):
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[_post(1)]) as t0, \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss") as rss, \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings") as lst:
            out = reddit_keyless._discover("topic", "default", None)
        assert len(out) == 1
        t0.assert_called_once()
        rss.assert_not_called()
        lst.assert_not_called()

    def test_tier0_empty_falls_to_keyless(self):
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[_post(1), _post(2)]) as rss, \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        assert len(out) == 2
        rss.assert_called_once()

    def test_listing_scores_backfill_rss_posts(self):
        # RSS finds post 1 (no score); listing card for post 1 carries the score.
        rss_post = _post(1)
        listing_post = _scored(1, score=52692, ncmt=1743)
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[listing_post]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        # listing post (scored) is kept; RSS dup of same url is dropped
        assert len(out) == 1
        assert out[0]["engagement"]["score"] == 52692
        assert out[0]["num_comments"] == 1743

    def test_scores_flow_to_distinct_rss_posts(self):
        # Distinct RSS post whose id matches a listing card gets backfilled.
        rss_post = _post(7)  # url .../000007/...
        listing_post = _scored(7, score=999)
        listing_post["url"] = "https://www.reddit.com/r/test/comments/zzzzzz/other/"
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[listing_post]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        backfilled = [p for p in out if p["url"] == rss_post["url"]][0]
        assert backfilled["engagement"]["score"] == 999

    def test_bare_query_does_not_merge_listing_discovery(self):
        # No subreddits provided: derived-subreddit listings must NOT be added as
        # results (avoids flooding with off-topic high-upvote posts) — only used
        # to backfill scores onto the keyword-matched RSS posts.
        rss_post = _post(1)  # on-topic keyword match
        offtopic_listing = _scored(99, score=88888)  # high score, unrelated sub
        offtopic_listing["url"] = "https://www.reddit.com/r/random/comments/zzz999/x/"
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless, "_top_subreddits", return_value=["random"]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[offtopic_listing]):
            out = reddit_keyless._discover("topic", "default", None)
        urls = [p["url"] for p in out]
        assert rss_post["url"] in urls
        assert offtopic_listing["url"] not in urls  # not merged as discovery

    def test_tier0_never_raises(self):
        with mock.patch("lib.reddit_public.search", side_effect=Exception("boom")), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]):
            assert reddit_keyless._discover("t", "default", None) == []


class TestSearchAndEnrich:
    """Full pipeline: discover -> date filter -> rank -> enrich -> reindex."""

    def _patch_enrich_passthrough(self):
        return mock.patch.object(
            reddit_keyless.reddit_shreddit, "fetch_comments",
            return_value={"top_comments": [], "comment_insights": [], "num_comments": None},
        )

    def test_returns_empty_when_no_discovery(self):
        with mock.patch.object(reddit_keyless, "_discover", return_value=[]):
            assert reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31") == []

    def test_date_filter_keeps_in_range_and_unknown(self):
        posts = [_post(1, date="2026-05-10"), _post(2, date="2020-01-01"),
                 _post(3, date=None)]
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             self._patch_enrich_passthrough():
            out = reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31")
        titles = {p["title"] for p in out}
        assert "Post 1" in titles and "Post 3" in titles
        assert "Post 2" not in titles

    def test_reindexes_ids(self):
        posts = [_post(1), _post(2), _post(3)]
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             self._patch_enrich_passthrough():
            out = reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31")
        assert [p["id"] for p in out] == ["R1", "R2", "R3"]

    def test_enrichment_attaches_comments(self):
        posts = [_post(1)]
        enriched = {
            "top_comments": [{"score": 9, "date": "2026-05-19", "author": "a",
                              "excerpt": "great", "url": "https://reddit.com/x"}],
            "comment_insights": ["great point about X"],
            "num_comments": 14,
        }
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               return_value=enriched):
            out = reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31")
        assert out[0]["top_comments"][0]["score"] == 9
        assert out[0]["num_comments"] == 14
        assert out[0]["engagement"]["num_comments"] == 14

    def test_enrichment_failure_keeps_posts(self):
        posts = [_post(i) for i in range(8)]
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=Exception("svc down")):
            out = reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31")
        assert len(out) == 8  # all posts retained despite enrichment failure

    def test_only_top_n_enriched_by_depth(self):
        posts = [_post(i, rel=1.0 - i / 100) for i in range(10)]
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               return_value={"top_comments": [], "comment_insights": [],
                                             "num_comments": None}) as fc:
            reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31", depth="quick")
        # quick depth enriches only top 3 posts
        assert fc.call_count == reddit_keyless.ENRICH_LIMITS["quick"]
