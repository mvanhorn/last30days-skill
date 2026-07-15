"""Tests for the dedicated-subreddit lane in reddit_keyless (U1).

A dedicated subreddit (the entity's home, e.g. r/Kanye for "Kanye West") is
wholly on-topic: pulled in full via top+hot+new listings and exempt from the
relevance floor, so an on-topic post whose title lacks the entity name is kept.
"""

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


def _listing_post(i, score, title, rel=0.0):
    p = _post(i, rel=rel)
    p["title"] = title
    p["score"] = score
    p["engagement"]["score"] = score
    p["why_relevant"] = "Reddit listing"
    p["metadata"] = {"post_id": f"{i:06d}"}
    return p


def _no_enrich():
    return mock.patch.object(
        reddit_keyless.reddit_shreddit, "fetch_comments",
        return_value={"top_comments": [], "comment_insights": [], "num_comments": None},
    )


class TestDedicatedLane:
    def test_dedicated_listings_pulled_with_top_hot_new_and_marked(self):
        ded = _listing_post(1, 2643, "What the actual fuck is this ye?")
        captured = {}

        def fake_fetch(subs, depth="default", query="", sorts=None):
            if sorts == reddit_keyless.DEDICATED_SORTS:
                captured["sorts"] = sorts
                captured["subs"] = subs
                return [ded]
            return []

        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               side_effect=fake_fetch), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss", return_value=[]):
            out = reddit_keyless._discover("Kanye West", "default", None,
                                           dedicated_subreddits=["Kanye"])
        assert captured["sorts"] == ["top", "hot", "new"]
        assert captured["subs"] == ["Kanye"]
        assert len(out) == 1
        assert out[0]["dedicated"] is True

    def test_dedicated_post_survives_floor_without_entity_name(self):
        # On-topic dedicated post whose title lacks "Kanye" (relevance 0) must be
        # kept; off-topic non-dedicated posts (relevance 0) are floored out.
        ded = _listing_post(1, 2284, "There's 2 types of people", rel=0.0)
        ded["dedicated"] = True
        offtopic = [_post(10 + i, rel=0.0) for i in range(5)]
        with mock.patch.object(reddit_keyless, "_discover", return_value=[ded] + offtopic), \
             _no_enrich():
            out = reddit_keyless.search_and_enrich("Kanye West", "2026-05-01", "2026-05-31")
        urls = {p["url"] for p in out}
        assert ded["url"] in urls            # dedicated kept despite relevance 0
        assert all(o["url"] not in urls for o in offtopic)  # off-topic floored

    def test_dedicated_dedup_keeps_floor_exempt_status(self):
        # A thread present in both the dedicated lane and a broad listing keeps
        # its dedicated (floor-exempt) flag — dedicated is merged first.
        shared_ded = _listing_post(1, 500, "fresh thread", rel=0.0)
        shared_broad = _listing_post(1, 500, "fresh thread", rel=0.0)  # same url/id

        def fake_fetch(subs, depth="default", query="", sorts=None):
            return [shared_ded] if sorts == reddit_keyless.DEDICATED_SORTS else [shared_broad]

        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               side_effect=fake_fetch), \
             mock.patch.object(reddit_keyless.reddit_rss, "search_rss", return_value=[]):
            out = reddit_keyless._discover("Kanye West", "default", ["hiphopheads"],
                                           dedicated_subreddits=["Kanye"])
        same = [p for p in out if p["url"] == shared_ded["url"]]
        assert len(same) == 1
        assert same[0].get("dedicated") is True

    def test_no_dedicated_subs_is_noop(self):
        with mock.patch.object(reddit_keyless.reddit_rss, "search_rss",
                               return_value=[_post(1)]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               return_value=[]):
            out = reddit_keyless._discover("topic", "default", ["test"])
        assert all(not p.get("dedicated") for p in out)
