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


class TestDiscoveryTierOrder:
    """Tier 0 (.json) is tried first; RSS is the fallback."""

    def test_tier0_success_skips_rss(self):
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[_post(1)]) as t0, \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss") as rss:
            out = reddit_keyless._discover("topic", "default", None)
        assert len(out) == 1
        t0.assert_called_once()
        rss.assert_not_called()

    def test_tier0_empty_falls_to_rss(self):
        with mock.patch.object(reddit_keyless, "_tier0_json", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[_post(1), _post(2)]) as rss:
            out = reddit_keyless._discover("topic", "default", ["test"])
        assert len(out) == 2
        rss.assert_called_once()

    def test_tier0_never_raises(self):
        # reddit_public import/search blowing up must not crash discovery.
        with mock.patch("lib.reddit_public.search", side_effect=Exception("boom")), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss", return_value=[]):
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
