"""Tests for scripts/lib/reddit_keyless.py — tiered keyless Reddit pipeline."""

from unittest import mock

from lib import reddit_keyless, relevance, rerank


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


class TestDiscovery:
    """RSS breadth + scored listings are the keyless discovery path (no .json)."""

    def test_keyless_path_runs_rss_and_listings(self):
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[_post(1), _post(2)]) as rss, \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        assert len(out) == 2
        rss.assert_called_once()

    def test_listing_scores_backfill_rss_posts(self):
        # RSS finds post 1 (no score); listing card for post 1 carries the score.
        rss_post = _post(1)
        listing_post = _scored(1, score=52692, ncmt=1743)
        listing_post["subreddit"] = "test"  # Match the requested subreddit.
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[listing_post]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings",
                               return_value=[]):  # No arctic supplement.
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
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[listing_post]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        backfilled = [p for p in out if p["url"] == rss_post["url"]][0]
        assert backfilled["engagement"]["score"] == 999

    def test_arctic_cannot_erase_verified_listing_count(self):
        rss_post = _post(7)
        listing_post = _scored(7, score=0, ncmt=0)
        listing_post["engagement"]["counts_verified"] = True
        listing_post["url"] = "https://www.reddit.com/r/test/comments/zzzzzz/other/"

        with mock.patch.object(
            reddit_keyless.reddit_rss, "search_rss", return_value=[rss_post]
        ), mock.patch.object(
            reddit_keyless.reddit_listing,
            "fetch_listings",
            return_value=[listing_post],
        ), mock.patch.object(
            reddit_keyless.reddit_arctic,
            "fetch_scores",
            return_value={
                "000007": {
                    "score": 12,
                    "num_comments": 0,
                    "counts_verified": False,
                }
            },
        ):
            out = reddit_keyless._discover("topic", "default", ["test"])

        backfilled = [p for p in out if p["url"] == rss_post["url"]][0]
        assert backfilled["engagement"]["score"] == 12
        assert backfilled["engagement"]["counts_verified"] is True
        assert backfilled["engagement"]["num_comments"] == 0

    def test_bare_query_does_not_merge_listing_discovery(self):
        # No subreddits provided: derived-subreddit listings must NOT be added as
        # results (avoids flooding with off-topic high-upvote posts) — only used
        # to backfill scores onto the keyword-matched RSS posts.
        rss_post = _post(1)  # on-topic keyword match
        offtopic_listing = _scored(99, score=88888)  # high score, unrelated sub
        offtopic_listing["url"] = "https://www.reddit.com/r/random/comments/zzz999/x/"
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[rss_post]), \
             mock.patch.object(reddit_keyless, "_top_subreddits", return_value=["random"]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[offtopic_listing]):
            out = reddit_keyless._discover("topic", "default", None)
        urls = [p["url"] for p in out]
        assert rss_post["url"] in urls
        assert offtopic_listing["url"] not in urls  # not merged as discovery

    def test_discover_never_raises_returns_empty(self):
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss", return_value=[]), \
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


class TestSlotPriority:
    """Enrichment slot selection prefers entity-matching posts (R1-R3)."""

    @staticmethod
    def _titled(i, title, score=0, selftext="", ncmt=0):
        p = _post(i)
        p["title"] = title
        p["selftext"] = selftext
        p["score"] = score
        p["engagement"]["score"] = score
        p["num_comments"] = ncmt
        p["engagement"]["num_comments"] = ncmt
        return p

    @staticmethod
    def _as_discovered(topic, posts):
        """Fill post["relevance"] the way discovery does — from the title alone.

        reddit_rss and reddit_listing both score the title only, and slot
        ordering reads that stored value. A fixture that leaves it at 0.0 tests
        a state the pipeline never produces.
        """
        prepared = relevance.PreparedQuery(topic)
        for post in posts:
            post["relevance"] = round(
                relevance.token_overlap_relevance(prepared, post["title"]), 3)
        return posts

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

        def _capture(url):
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

    def test_entity_match_tier_orders_by_relevance(self):
        viral_off_topic = [
            self._titled(1, "OpenClaw licensing debate", score=3156, ncmt=389),
            self._titled(2, "OpenClaw trademark policy dispute", score=3048, ncmt=204),
            self._titled(3, "OpenClaw release packaging", score=1824, ncmt=496),
        ]
        on_topic = [
            self._titled(4, "OpenClaw and Obsidian as a maintained second brain",
                         score=361, ncmt=49),
            self._titled(5, "OpenClaw personal knowledge base setup",
                         score=23, ncmt=17),
        ]
        topic = "OpenClaw second brain personal knowledge base"
        posts = self._as_discovered(topic, viral_off_topic + on_topic)
        assert all(rerank._entity_grounded(post["title"], topic) for post in posts)

        out = reddit_keyless._slot_priority(
            topic, posts)
        assert {id(p) for p in out[:2]} == {id(p) for p in on_topic}
        assert {id(p) for p in out[2:]} == {id(p) for p in viral_off_topic}

    def test_zero_comment_thread_never_takes_a_slot(self):
        # A comment slot spent on a thread with no comments yields nothing, so it
        # sorts last however high its score or relevance.
        no_comments = self._titled(1, "openclaw deluxe leak", score=900, ncmt=0)
        no_comments["engagement"]["counts_verified"] = True
        has_comments = self._titled(2, "openclaw first reactions", score=400, ncmt=220)
        out = reddit_keyless._slot_priority(
            "openclaw", self._as_discovered("openclaw", [no_comments, has_comments]))
        assert out[0] is has_comments
        assert out[1] is no_comments

    def test_unknown_comment_count_is_not_treated_as_empty(self):
        # RSS-discovered posts carry a placeholder count of 0 until shreddit
        # backfills them. Sorting those behind a thread known to be empty buries
        # exactly the posts nothing is known about yet.
        unknown = self._titled(1, "openclaw thread", score=0, ncmt=0)
        known_empty = self._titled(2, "openclaw thread", score=900, ncmt=0)
        known_empty["engagement"]["counts_verified"] = True
        out = reddit_keyless._slot_priority(
            "openclaw", self._as_discovered("openclaw", [known_empty, unknown]))
        assert out[0] is unknown
        assert out[1] is known_empty

    def test_verified_zero_outranked_by_unknown_count(self):
        # A listing post downvoted to 0 with 0 comments is confirmed empty, so
        # its slot can never pay off — even though its score reads like the RSS
        # placeholder. Only the producer's own verification tells them apart.
        verified_empty = self._titled(1, "openclaw thread", score=0, ncmt=0)
        verified_empty["engagement"]["counts_verified"] = True
        unknown = self._titled(2, "openclaw thread", score=0, ncmt=0)
        out = reddit_keyless._slot_priority(
            "openclaw", self._as_discovered("openclaw", [verified_empty, unknown]))
        assert out[0] is unknown
        assert out[1] is verified_empty

    def test_relevant_unknown_count_beats_weaker_known_thread(self):
        topic = "openclaw second brain knowledge base"
        unknown = self._titled(
            1, "openclaw second brain knowledge base", score=0, ncmt=0)
        known_busy = self._titled(
            2, "openclaw release notes", score=500, ncmt=400)
        posts = self._as_discovered(topic, [known_busy, unknown])

        out = reddit_keyless._slot_priority(topic, posts)
        assert out[0] is unknown
        assert out[1] is known_busy

    def test_known_comments_break_equal_relevance_tie(self):
        unknown = self._titled(1, "openclaw thread", score=0, ncmt=0)
        known_busy = self._titled(2, "openclaw thread", score=0, ncmt=40)
        out = reddit_keyless._slot_priority("openclaw", [unknown, known_busy])
        assert out[0] is known_busy

    def test_slot_order_matches_display_order(self):
        # A slot handed to a post that final ranking then drops below the fold
        # is the exact failure this ordering exists to prevent, so within a tier
        # the two orders must agree. Here the more relevant post (0.31) carries
        # the smaller thread, so any key that leans on engagement over stored
        # relevance hands the slot to the one displayed second.
        topic = "AI second brain personal knowledge base"
        higher_relevance = self._titled(
            1, "It appears that the anti opensource AI lobby is far outgunned",
            score=1824, ncmt=496)
        bigger_thread = self._titled(
            2, "Linus Torvalds tells people to stop attacking others for using AI",
            score=3156, ncmt=389)
        posts = self._as_discovered(topic, [bigger_thread, higher_relevance])

        display_order = sorted(
            posts, key=reddit_keyless._relevance_rank_key, reverse=True)
        slot_order = reddit_keyless._slot_priority(topic, posts)
        assert [id(p) for p in slot_order] == [id(p) for p in display_order]
        assert display_order[0] is higher_relevance

    def test_slot_ranking_scores_the_same_text_as_display_ranking(self):
        # Discovery scores the title alone (reddit_rss, reddit_listing) and the
        # display sort reads that stored value, so a slot key that scores title
        # plus selftext ranks a different quantity under the same name. The
        # body-heavy post then wins a slot and is displayed last anyway.
        topic = "AI second brain personal knowledge base"
        body_heavy = self._titled(
            1, "My personal setup after two years", score=40, ncmt=12,
            selftext="I finally got my second brain working as a personal "
                     "knowledge base, fully AI maintained.")
        titled_on_topic = self._titled(
            2, "AI knowledge base tips", score=40, ncmt=12)
        posts = self._as_discovered(topic, [body_heavy, titled_on_topic])
        assert body_heavy["relevance"] < titled_on_topic["relevance"]

        out = reddit_keyless._slot_priority(topic, posts)
        display_order = sorted(
            posts, key=reddit_keyless._relevance_rank_key, reverse=True)
        assert out[0] is titled_on_topic
        assert [id(p) for p in out] == [id(p) for p in display_order]

    def test_equal_relevance_keeps_score_order(self):
        # Identical titles score identical relevance, so the engagement bonus
        # is left to decide and the bigger thread keeps the slot.
        high = self._titled(1, "openclaw thread", score=500, ncmt=10)
        low = self._titled(2, "openclaw thread", score=5, ncmt=10)
        out = reddit_keyless._slot_priority(
            "openclaw", self._as_discovered("openclaw", [low, high]))
        assert out[0] is high


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
