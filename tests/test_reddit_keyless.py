"""Tests for scripts/lib/reddit_keyless.py: tiered keyless Reddit pipeline."""

from unittest import mock

from lib import reddit_keyless


def _post(i, date="2026-05-20", rel=0.0):
    url = f"https://www.reddit.com/r/test/comments/{i:06d}/post_{i}/"
    return {
        "id": "", "title": f"Post {i}", "url": url, "score": 0, "num_comments": 0,
        "subreddit": "test", "created_utc": None, "author": "u", "selftext": "",
        "date": date, "engagement": {"score": 0, "num_comments": 0, "upvote_ratio": None},
        "relevance": rel, "why_relevant": "Reddit search", "metadata": {},
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


def _searched(i, score, ncmt=0, rel=0.5):
    """A site-search result: dated and scored straight from the search page."""
    p = _scored(i, score, ncmt)
    p["relevance"] = rel
    p["why_relevant"] = "Reddit search"
    return p


def _lanes(search=(), listing=(), arctic_listing=(), arctic_scores=None):
    """Patch every discovery lane at its module boundary; return the mocks."""
    return (
        mock.patch.object(reddit_keyless.reddit_search, "search", return_value=list(search)),
        mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                          return_value=list(listing)),
        mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                          return_value=list(arctic_listing)),
        mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores",
                          return_value=dict(arctic_scores or {})),
    )


class TestDiscovery:
    """Reddit site search + scored listings are the keyless discovery path."""

    def test_bare_run_returns_search_posts_without_listing_requests(self):
        hits = [_searched(1, score=412, ncmt=38), _searched(2, score=77, ncmt=5)]
        p_search, p_listing, p_arctic_listing, p_scores = _lanes(search=hits)
        with p_search as search, p_listing as listing, \
             p_arctic_listing as arctic_listing, p_scores as scores:
            out = reddit_keyless._discover("topic", "default", None)
        search.assert_called_once()
        assert search.call_args.kwargs["subreddits"] is None
        listing.assert_not_called()
        arctic_listing.assert_not_called()
        scores.assert_not_called()  # real scores, nothing to backfill
        assert [p["url"] for p in out] == [h["url"] for h in hits]
        assert [p["engagement"]["score"] for p in out] == [412, 77]
        assert [p["num_comments"] for p in out] == [38, 5]

    def test_targeted_run_merges_search_and_listing_first_writer_wins(self):
        listing_post = _scored(1, score=52692, ncmt=1743)
        listing_only = _scored(2, score=10)
        search_dup = _searched(1, score=50000, ncmt=1700)  # same url as listing_post
        search_only = _searched(3, score=9)
        p_search, p_listing, p_arctic_listing, p_scores = _lanes(
            search=[search_dup, search_only], listing=[listing_post, listing_only])
        with p_search as search, p_listing as listing, p_arctic_listing, p_scores:
            out = reddit_keyless._discover("topic", "default", ["test"])
        assert search.call_args.kwargs["subreddits"] == ["test"]
        assert listing.call_args.args[0] == ["test"]
        urls = [p["url"] for p in out]
        assert urls == [listing_post["url"], listing_only["url"], search_only["url"]]
        assert out[0]["why_relevant"] == "Reddit listing"  # one copy, listing kept
        assert out[0]["engagement"]["score"] == 52692

    def test_targeted_listing_score_fills_distinct_search_post(self):
        # A search post whose id matches a listing card under another url takes
        # the listing's live score.
        search_post = _searched(7, score=0)
        listing_post = _scored(7, score=999)
        listing_post["url"] = "https://www.reddit.com/r/test/comments/zzzzzz/other/"
        p_search, p_listing, p_arctic_listing, p_scores = _lanes(
            search=[search_post], listing=[listing_post])
        with p_search, p_listing, p_arctic_listing, p_scores:
            out = reddit_keyless._discover("topic", "default", ["test"])
        filled = [p for p in out if p["url"] == search_post["url"]][0]
        assert filled["engagement"]["score"] == 999

    def test_zero_score_search_post_gets_arctic_fill(self):
        unscored = _searched(4, score=0)
        scored = _searched(5, score=120, ncmt=3)
        p_search, p_listing, p_arctic_listing, p_scores = _lanes(
            search=[unscored, scored],
            arctic_scores={"000004": {"score": 31, "num_comments": 6}})
        with p_search, p_listing, p_arctic_listing, p_scores as scores:
            out = reddit_keyless._discover("topic", "default", None)
        scores.assert_called_once_with(["000004"])
        by_url = {p["url"]: p for p in out}
        assert by_url[unscored["url"]]["engagement"]["score"] == 31
        assert by_url[unscored["url"]]["num_comments"] == 6
        assert by_url[scored["url"]]["engagement"]["score"] == 120

    def test_bare_query_does_not_merge_listing_discovery(self):
        # No subreddits provided: no listing is fetched, so high-upvote
        # off-topic listing posts can never flood the keyword-matched results.
        on_topic = _searched(1, score=15)
        offtopic_listing = _scored(99, score=88888)
        offtopic_listing["url"] = "https://www.reddit.com/r/random/comments/zzz999/x/"
        p_search, p_listing, p_arctic_listing, p_scores = _lanes(
            search=[on_topic], listing=[offtopic_listing], arctic_listing=[offtopic_listing])
        with p_search, p_listing as listing, p_arctic_listing as arctic_listing, p_scores:
            out = reddit_keyless._discover("topic", "default", None)
        urls = [p["url"] for p in out]
        assert urls == [on_topic["url"]]
        listing.assert_not_called()
        arctic_listing.assert_not_called()

    def test_discover_never_raises_returns_empty(self):
        p_search, p_listing, p_arctic_listing, p_scores = _lanes()
        with p_search, p_listing, p_arctic_listing, p_scores:
            assert reddit_keyless._discover("t", "default", None) == []

    def test_empty_search_makes_keyless_path_return_empty(self):
        p_search, p_listing, p_arctic_listing, p_scores = _lanes()
        with p_search, p_listing, p_arctic_listing, p_scores:
            assert reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31") == []

    def test_search_window_follows_lookback(self):
        p_search, p_listing, p_arctic_listing, p_scores = _lanes()
        with p_search as search, p_listing, p_arctic_listing, p_scores:
            reddit_keyless.search_and_enrich("t", "2026-05-24", "2026-05-31", depth="quick")
        kwargs = search.call_args.kwargs
        assert kwargs["from_date"] == "2026-05-24"
        assert kwargs["to_date"] == "2026-05-31"
        assert kwargs["depth"] == "quick"


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

    def test_enrichment_passes_budget_deadline(self):
        # Every enrichment worker receives a monotonic deadline bounded by
        # ENRICH_BUDGET, so the slow Scrapling browser fallback inside
        # fetch_comments cannot outlive the aggregate budget (its subprocess
        # timeout is capped to the time remaining).
        posts = [_post(1)]
        empty = {"top_comments": [], "comment_insights": [], "num_comments": None}
        before = reddit_keyless.time.monotonic()
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               return_value=empty) as fc:
            reddit_keyless.search_and_enrich("t", "2026-05-01", "2026-05-31")
        after = reddit_keyless.time.monotonic()
        deadline = fc.call_args.kwargs["deadline"]
        assert before < deadline <= after + reddit_keyless.ENRICH_BUDGET


class TestSlotPriority:
    """Enrichment slot selection prefers entity-matching posts (R1-R3)."""

    @staticmethod
    def _titled(i, title, score=0, selftext=""):
        p = _post(i)
        p["title"] = title
        p["selftext"] = selftext
        p["score"] = score
        p["engagement"]["score"] = score
        return p

    def test_on_topic_low_score_beats_off_topic_high_score(self):
        # 3 off-topic monsters + 2 on-topic small threads; quick depth = 3 slots.
        posts = [
            self._titled(1, "Stop asking what model to run", score=2662),
            self._titled(2, "RTX 4090 PSA", score=2068),
            self._titled(3, "Gemma 4 release", score=997),
            self._titled(4, "My OpenClaw self-migrated", score=73),
            self._titled(5, "Using openclaw with Claude API key is so expensive", score=47),
        ]
        enriched_urls = []

        def _capture(url, **kwargs):
            enriched_urls.append(url)
            return {"top_comments": [], "comment_insights": [], "num_comments": None}

        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_capture):
            reddit_keyless.search_and_enrich(
                "openclaw", "2026-05-01", "2026-05-31", depth="quick")
        assert posts[3]["url"] in enriched_urls
        assert posts[4]["url"] in enriched_urls
        assert len(enriched_urls) == reddit_keyless.ENRICH_LIMITS["quick"]

    def test_slot_priority_grounds_on_head_token_not_full_phrase(self):
        # Mirrors rerank's head-token grounding: a post naming the brand head
        # ("Stripe") lands in the match tier even without the trailing search
        # descriptor ("payments"), so it is not buried under an unrelated
        # high-upvote post that never names the brand.
        head_only = self._titled(1, "Stripe is friendly to 'friendly fraud'", score=5)
        off_topic = self._titled(2, "PayPal raises dispute fees again", score=900)
        out = reddit_keyless._slot_priority("Stripe payments", [off_topic, head_only])
        assert out[0] is head_only
        assert out[1] is off_topic

    def test_intent_modifier_topic_prioritizes_head_token_match(self):
        # Intent-modifier topics still partition by the brand head token: the
        # on-entity post wins over a high-upvote post that never names the brand.
        on_topic = self._titled(1, "Hermes Agent v0.13 is great", score=1)
        off_topic = self._titled(2, "LangGraph tutorial walkthrough", score=900)
        out = reddit_keyless._slot_priority("Hermes Agent review", [off_topic, on_topic])
        assert out[0] is on_topic

    def test_all_miss_keeps_score_order_and_full_slots(self):
        posts = [self._titled(i, f"Gemma thread {i}", score=1000 - i) for i in range(5)]
        out = reddit_keyless._slot_priority("openclaw", posts)
        assert out == posts  # order unchanged
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               return_value={"top_comments": [], "comment_insights": [],
                                             "num_comments": None}) as fc:
            reddit_keyless.search_and_enrich(
                "openclaw", "2026-05-01", "2026-05-31", depth="quick")
        assert fc.call_count == reddit_keyless.ENRICH_LIMITS["quick"]

    def test_same_tier_order_preserved(self):
        posts = [self._titled(i, f"openclaw thread {i}", score=100 - i) for i in range(4)]
        out = reddit_keyless._slot_priority("openclaw", posts)
        assert out == posts

    def test_empty_entity_falls_back_to_token_overlap(self):
        # Pure intent-modifier topic yields no primary entity; fallback path
        # must not raise and must keep every post.
        posts = [self._titled(1, "Post one"), self._titled(2, "review of things")]
        out = reddit_keyless._slot_priority("review", posts)
        assert len(out) == 2
        assert {p["url"] for p in out} == {p["url"] for p in posts}

    def test_selftext_match_lands_in_match_tier(self):
        body_match = self._titled(1, "Need help with my setup", score=2,
                                  selftext="my openclaw agent keeps asking for ssh keys")
        off_topic = self._titled(2, "Gemma 4 with QAT", score=700)
        out = reddit_keyless._slot_priority("openclaw", [off_topic, body_match])
        assert out[0] is body_match

    def test_none_score_posts_do_not_break_partition(self):
        p1 = self._titled(1, "openclaw tips")
        p1["engagement"]["score"] = None
        p2 = self._titled(2, "Gemma news")
        p2["engagement"]["score"] = None
        out = reddit_keyless._slot_priority("openclaw", [p2, p1])
        assert out[0] is p1

    def test_partition_never_raises(self):
        posts = [self._titled(1, "openclaw tips", score=1)]
        with mock.patch("lib.rerank._primary_entity", side_effect=Exception("boom")):
            out = reddit_keyless._slot_priority("openclaw", posts)
        assert out == posts

    @staticmethod
    def _titled_nc(i, title, score=0, ncmt=0, selftext=""):
        """_titled variant that also sets a real comment count (both surfaces)."""
        p = TestSlotPriority._titled(i, title, score=score, selftext=selftext)
        p["num_comments"] = ncmt
        p["engagement"]["num_comments"] = ncmt
        return p

    def test_comment_count_orders_within_match_tier(self):
        # Two entity-matching posts: the low-score high-comment thread wins the slot.
        high_comments = self._titled_nc(1, "openclaw thread with lots of discussion", score=1, ncmt=45)
        low_comments = self._titled_nc(2, "openclaw thread, quiet", score=900, ncmt=3)
        out = reddit_keyless._slot_priority("openclaw", [low_comments, high_comments])
        assert out[0] is high_comments
        assert out[1] is low_comments

    def test_entity_match_tier_beats_comment_count(self):
        # Entity priority is preserved: a miss with 100 comments still follows a
        # match with 1 comment, regardless of discussion volume.
        match = self._titled_nc(1, "openclaw tips", score=10, ncmt=1)
        miss = self._titled_nc(2, "Gemma news", score=100, ncmt=100)
        out = reddit_keyless._slot_priority("openclaw", [miss, match])
        assert out[0] is match
        assert out[1] is miss

    def test_equal_comment_counts_preserve_incoming_order_stable(self):
        # Stable tiebreak: equal comment counts preserve the incoming order. The
        # score-first order is established by search_and_enrich's provisional
        # sort before _slot_priority runs; _slot_priority must not re-sort ties.
        p1 = self._titled_nc(1, "openclaw thread a", score=100, ncmt=5)
        p2 = self._titled_nc(2, "openclaw thread b", score=50, ncmt=5)
        out = reddit_keyless._slot_priority("openclaw", [p2, p1])
        assert out[0] is p2
        assert out[1] is p1

    def test_unknown_comment_count_ties_with_zero(self):
        # Missing/None comment count is treated as 0: it ties with a known-zero
        # post (stable) and sorts below any positive-count post in its tier.
        unknown = self._titled_nc(1, "openclaw unknown", score=100, ncmt=None)
        positive = self._titled_nc(2, "openclaw positive", score=10, ncmt=3)
        known_zero = self._titled_nc(3, "openclaw zero", score=5, ncmt=0)
        out = reddit_keyless._slot_priority("openclaw", [known_zero, unknown, positive])
        assert out[0] is positive
        assert out[1:] == [known_zero, unknown]

    def test_richest_thread_gets_slot_when_score_ranked_low(self):
        # Issue #906 regression: a 45-comment thread ranked last by score must
        # get an enrichment slot at default depth (limit 8) while a 4-comment
        # thread above it in score order does not. All posts are in the same
        # entity tier; there are more posts than slots so ordering matters.
        posts = [
            self._titled_nc(1, "openclaw thread one", score=1000, ncmt=4),
            self._titled_nc(2, "openclaw thread two", score=900, ncmt=4),
            self._titled_nc(3, "openclaw thread three", score=800, ncmt=4),
            self._titled_nc(4, "openclaw thread four", score=700, ncmt=4),
            self._titled_nc(5, "openclaw thread five", score=600, ncmt=6),
            self._titled_nc(6, "openclaw thread six", score=500, ncmt=5),
            self._titled_nc(7, "openclaw thread seven", score=300, ncmt=4),
            self._titled_nc(9, "openclaw thread nine", score=250, ncmt=7),
            self._titled_nc(10, "openclaw thread ten", score=200, ncmt=8),
            self._titled_nc(11, "openclaw thread eleven", score=150, ncmt=9),
            self._titled_nc(8, "openclaw thread eight", score=77, ncmt=45),
        ]
        enriched_urls = []

        def _capture(url, **kwargs):
            enriched_urls.append(url)
            return {"top_comments": [], "comment_insights": [], "num_comments": None}

        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_capture):
            reddit_keyless.search_and_enrich(
                "openclaw", "2026-05-01", "2026-05-31", depth="default")
        assert posts[10]["url"] in enriched_urls     # 45-comment thread enriched
        assert posts[6]["url"] not in enriched_urls   # 4-comment thread above it skipped
        assert len(enriched_urls) == reddit_keyless.ENRICH_LIMITS["default"]

    def test_miss_tier_orders_by_comments_for_leftover_slots(self):
        # Review finding #1 (validated): when the entity-match tier is smaller
        # than ENRICH_LIMITS, leftover slots are filled from the miss tier in
        # comment-count order. 1 match + 4 misses at quick depth (limit 4): the
        # three most-commented misses get slots, the least-commented miss does not.
        # Score order deliberately differs from comment order so this test
        # discriminates the miss-tier sort from the old score-first order.
        posts = [
            self._titled_nc(1, "openclaw thread", score=100, ncmt=2),
            self._titled_nc(2, "Gemma thread A", score=5, ncmt=30),
            self._titled_nc(3, "Gemma thread B", score=40, ncmt=9),
            self._titled_nc(4, "Gemma thread C", score=30, ncmt=2),
            self._titled_nc(5, "Gemma thread D", score=20, ncmt=1),
        ]
        enriched_urls = []

        def _capture(url, **kwargs):
            enriched_urls.append(url)
            return {"top_comments": [], "comment_insights": [], "num_comments": None}

        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_capture):
            reddit_keyless.search_and_enrich(
                "openclaw", "2026-05-01", "2026-05-31", depth="quick")
        assert posts[0]["url"] in enriched_urls       # entity match always slotted
        assert posts[1]["url"] in enriched_urls       # 30-comment miss (top miss)
        assert posts[2]["url"] in enriched_urls       # 9-comment miss
        assert posts[3]["url"] in enriched_urls       # 2-comment miss takes the last slot
        assert posts[4]["url"] not in enriched_urls   # 1-comment miss below the cut
        assert len(enriched_urls) == reddit_keyless.ENRICH_LIMITS["quick"]


class TestScoredListingsFallback:
    """_scored_listings falls back to the arctic-shift archive when the
    shreddit listing partials return nothing (datacenter egress 403)."""

    def test_arctic_fallback_when_shreddit_empty(self):
        arctic_post = _scored(1, score=406)
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[arctic_post]) as arctic:
            out = reddit_keyless._scored_listings(["tea"], depth="quick", query="matcha")
        assert out == [arctic_post]
        arctic.assert_called_once_with(["tea"], depth="quick", query="matcha", sorts=None)

    def test_shreddit_and_arctic_both_called_deduped(self):
        """Shreddit and arctic are both called; arctic supplements missing posts."""
        shreddit_post = _scored(1, score=42)
        shreddit_post["subreddit"] = "tea"
        arctic_post = _scored(2, score=100)
        arctic_post["subreddit"] = "tea"
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[shreddit_post]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[arctic_post]) as arctic:
            out = reddit_keyless._scored_listings(["tea"], depth="quick", query="matcha")
        # Both shreddit and arctic posts should be in the result (deduped by URL).
        assert len(out) == 2
        urls = {p["url"] for p in out}
        assert shreddit_post["url"] in urls
        assert arctic_post["url"] in urls
        arctic.assert_called_once()

    def test_both_empty_returns_empty(self):
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[]):
            out = reddit_keyless._scored_listings(["tea"], depth="quick", query="matcha")
        assert out == []

    def test_never_raises_when_arctic_fails(self):
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               side_effect=Exception("boom")):
            out = reddit_keyless._scored_listings(["tea"], depth="quick", query="matcha")
        assert out == []

    def test_dedicated_sorts_passed_through(self):
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[]) as arctic:
            reddit_keyless._scored_listings(
                ["Kanye"], depth="default", query="Kanye", sorts=["top", "hot", "new"]
            )
        arctic.assert_called_once_with(
            ["Kanye"], depth="default", query="Kanye", sorts=["top", "hot", "new"]
        )

    def test_arctic_supplements_all_subreddits(self):
        """Arctic is called for all subreddits to supplement any failed sort lanes."""
        shreddit_post = _scored(1, score=100)
        shreddit_post["subreddit"] = "tea"
        arctic_post_tea = _scored(2, score=200)
        arctic_post_tea["subreddit"] = "tea"
        arctic_post_coffee = _scored(3, score=150)
        arctic_post_coffee["subreddit"] = "coffee"

        def shreddit_side_effect(subs, **kwargs):
            # Shreddit only returns posts for "tea", not "coffee".
            return [shreddit_post] if "tea" in subs else []

        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               side_effect=shreddit_side_effect), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[arctic_post_tea, arctic_post_coffee]) as arctic:
            out = reddit_keyless._scored_listings(
                ["tea", "coffee"], depth="quick", query="beverages"
            )
        # Arctic is called for ALL requested subreddits to supplement any failed sorts.
        arctic.assert_called_once()
        call_args = arctic.call_args
        assert set(call_args[0][0]) == {"tea", "coffee"}, "arctic should be called for all subs"
        # All posts should be in the result (deduped by URL).
        urls = [p["url"] for p in out]
        assert shreddit_post["url"] in urls
        assert arctic_post_tea["url"] in urls
        assert arctic_post_coffee["url"] in urls
