"""Tests for scripts/lib/reddit_listing.py — keyless scored listing scrape."""

from pathlib import Path
from unittest import mock

from lib import reddit_listing as rl

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_listing_cards_sample.html"


def _html():
    return FIXTURE.read_text(encoding="utf-8")


class TestParseCards:
    """parse_cards reads <shreddit-post> cards into scored post dicts."""

    def test_parses_five_cards(self):
        posts = rl.parse_cards(_html(), query="netherlands")
        assert len(posts) == 5

    def test_real_score_and_count(self):
        posts = rl.parse_cards(_html())
        top = posts[0]
        assert top["score"] == 52692           # the real upvote count
        assert top["engagement"]["score"] == 52692
        assert top["num_comments"] == 1743
        assert top["engagement"]["num_comments"] == 1743

    def test_normalized_shape(self):
        post = rl.parse_cards(_html())[0]
        required = {"id", "title", "url", "score", "num_comments", "subreddit",
                    "created_utc", "author", "selftext", "date",
                    "engagement", "relevance", "why_relevant", "metadata"}
        assert required.issubset(set(post.keys()))
        assert post["why_relevant"] == "Reddit listing"
        assert post["metadata"]["post_id"]  # post id captured for backfill

    def test_fields_populated(self):
        post = rl.parse_cards(_html())[0]
        assert post["title"]
        assert post["author"] == "AdSpecialist6598"
        assert post["subreddit"] == "technology"
        assert "/comments/" in post["url"]
        assert post["date"] and len(post["date"]) == 10

    def test_empty_html_returns_empty(self):
        assert rl.parse_cards("") == []
        assert rl.parse_cards("<div>no cards</div>") == []


class TestListingUrl:
    def test_top_includes_timeframe(self):
        u = rl._listing_url("technology", "top")
        assert "community-more-posts/top/" in u and "name=technology" in u and "t=month" in u

    def test_hot_no_timeframe(self):
        u = rl._listing_url("r/technology", "hot")
        assert "community-more-posts/hot/" in u and "name=technology" in u and "t=" not in u
        assert ".json" not in u


class TestFetchListings:
    def test_dedupes_across_sorts(self):
        with mock.patch.object(rl.http, "get_text", return_value=_html()):
            posts = rl.fetch_listings(["technology"], depth="default")
        urls = [p["url"] for p in posts]
        assert len(urls) == len(set(urls))  # top + hot return same cards -> deduped

    def test_no_subreddits_returns_empty(self):
        assert rl.fetch_listings([], depth="default") == []

    def test_all_fetches_fail_returns_empty(self):
        with mock.patch.object(rl.http, "get_text", return_value=None):
            assert rl.fetch_listings(["technology"]) == []


class TestScoreIndex:
    def test_builds_post_id_to_score_map(self):
        with mock.patch.object(rl.http, "get_text", return_value=_html()):
            idx = rl.score_index(["technology"], depth="quick")
        assert idx  # non-empty
        first = next(iter(idx.values()))
        assert set(first.keys()) == {"score", "num_comments"}
        assert any(v["score"] == 52692 for v in idx.values())


class TestFetchDiscoveryListingsFallback:
    """fetch_discovery_listings falls back to arctic-shift when every shreddit
    partial fails (datacenter egress 403), and clears the errors on success."""

    def test_arctic_fallback_when_all_shreddit_feeds_fail(self):
        arctic_post = {
            "id": "", "title": "matcha farm tour", "url": "https://www.reddit.com/r/tea/comments/abc/x/",
            "score": 406, "num_comments": 88, "subreddit": "tea", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 406, "num_comments": 88, "upvote_ratio": None},
            "relevance": 0.5, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "abc"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[arctic_post]) as arctic:
            result = rl.fetch_discovery_listings(["tea"], query="matcha", depth="quick")
        assert result["items"] == [arctic_post]
        assert result["errors"] == []  # recovered — no failure to report
        arctic.assert_called_once()

    def test_errors_preserved_when_both_shreddit_and_arctic_empty(self):
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[]):
            result = rl.fetch_discovery_listings(["tea"], query="matcha", depth="quick")
        assert result["items"] == []
        assert result["errors"]  # the shreddit failures still surface

    def test_arctic_supplements_shreddit_success(self):
        """Arctic is always called to supplement shreddit; results are deduped."""
        arctic_post = {
            "id": "", "title": "netherlands tech news extra", "url": "https://www.reddit.com/r/technology/comments/arctic/x/",
            "score": 100, "num_comments": 10, "subreddit": "technology", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 100, "num_comments": 10, "upvote_ratio": None},
            "relevance": 0.5, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "arctic"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=_html()), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[arctic_post]) as arctic:
            result = rl.fetch_discovery_listings(["technology"], query="netherlands", depth="quick")
        # Both shreddit cards and arctic supplement should be in the result.
        urls = {item["url"] for item in result["items"]}
        assert arctic_post["url"] in urls
        assert len(result["items"]) > 1  # shreddit + arctic
        assert result["errors"] == []
        arctic.assert_called_once()

    def test_no_subreddits_skips_fallback(self):
        with mock.patch("lib.reddit_arctic.fetch_listings") as arctic:
            result = rl.fetch_discovery_listings([], query="matcha", depth="quick")
        assert result == {"items": [], "errors": []}
        arctic.assert_not_called()

    def test_multi_sub_partial_recovery_keeps_unrecovered_errors(self):
        """AE4: arctic recovers only some subreddits; errors kept for missing subs."""
        # Request tea and coffee; arctic only returns posts from tea.
        arctic_post = {
            "id": "", "title": "matcha farm tour", "url": "https://www.reddit.com/r/tea/comments/abc/x/",
            "score": 406, "num_comments": 88, "subreddit": "tea", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 406, "num_comments": 88, "upvote_ratio": None},
            "relevance": 0.5, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "abc"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[arctic_post]):
            result = rl.fetch_discovery_listings(["tea", "coffee"], query="matcha", depth="quick")
        assert result["items"] == [arctic_post]
        # r/coffee had shreddit errors, arctic returned nothing for it → errors kept.
        coffee_errors = [e for e in result["errors"] if "r/coffee" in e.lower()]
        assert coffee_errors, "unrecovered sub's errors should be preserved"
        # r/tea was recovered → its errors should NOT appear.
        tea_errors = [e for e in result["errors"] if "r/tea" in e.lower()]
        assert not tea_errors, "recovered sub's errors should be cleared"

    def test_arctic_rows_failing_keyword_gate_keep_errors(self):
        """AE5: arctic rows that fail the keyword gate do not count as recovery."""
        # Arctic returns a row from tea, but the title doesn't match the query.
        offtopic_post = {
            "id": "", "title": "best oolong guide", "url": "https://www.reddit.com/r/tea/comments/xyz/x/",
            "score": 999, "num_comments": 200, "subreddit": "tea", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 999, "num_comments": 200, "upvote_ratio": None},
            "relevance": 0.0, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "xyz"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[offtopic_post]):
            result = rl.fetch_discovery_listings(["tea"], query="matcha", depth="quick")
        # "matcha" is not in "best oolong guide", so the row fails the keyword gate.
        # With no surviving rows, arctic didn't effectively recover → errors kept.
        assert result["items"] == []
        assert result["errors"], "keyword-rejected rows should not clear errors"

    def test_empty_query_skips_keyword_gate(self):
        """Global --discover (empty query) skips the keyword gate entirely."""
        offtopic_post = {
            "id": "", "title": "best oolong guide", "url": "https://www.reddit.com/r/tea/comments/xyz/x/",
            "score": 999, "num_comments": 200, "subreddit": "tea", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 999, "num_comments": 200, "upvote_ratio": None},
            "relevance": 0.0, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "xyz"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[offtopic_post]):
            # Empty query = global discover, no keyword gate.
            result = rl.fetch_discovery_listings(["tea"], query="", depth="quick")
        # No keyword gate → post survives, errors cleared.
        assert result["items"] == [offtopic_post]
        assert result["errors"] == []


class TestErrorAttributionWordBoundary:
    """Verify error attribution uses word-boundary matching, not substring."""

    def test_recovered_foobar_does_not_keep_foo_errors(self):
        """Recovered r/foobar should not be misattributed to unrecovered r/foo."""
        # Arctic returns posts only from r/foobar (recovered), not r/foo.
        foobar_post = {
            "id": "", "title": "foobar topic", "url": "https://www.reddit.com/r/foobar/comments/abc/x/",
            "score": 500, "num_comments": 50, "subreddit": "foobar", "created_utc": None,
            "author": "u", "selftext": "", "date": "2026-07-02",
            "engagement": {"score": 500, "num_comments": 50, "upvote_ratio": None},
            "relevance": 0.5, "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "abc"},
        }
        with mock.patch.object(rl.http, "get_text", return_value=None), \
             mock.patch("lib.reddit_arctic.fetch_listings", return_value=[foobar_post]):
            result = rl.fetch_discovery_listings(["foo", "foobar"], query="topic", depth="quick")
        # foobar is recovered, foo is not.
        assert result["items"] == [foobar_post]
        # Errors for r/foo should be kept.
        foo_errors = [e for e in result["errors"] if e.lower().startswith("r/foo ")]
        assert foo_errors, "unrecovered r/foo errors should be preserved"
        # Errors for r/foobar should NOT be in the kept errors (foobar was recovered).
        foobar_errors = [e for e in result["errors"] if e.lower().startswith("r/foobar ")]
        assert not foobar_errors, "recovered r/foobar errors should be cleared"


class TestMatchesDiscoveryDomainParity:
    """Verify the copied _matches_discovery_domain stays in sync with pipeline.py."""

    def test_parity_with_pipeline_implementation(self):
        # Import both implementations and verify they agree on test cases.
        from lib import pipeline
        test_cases = [
            ("matcha", "matcha farm tour", True),
            ("matcha", "best oolong guide", False),
            ("AI", "New AI model release", True),  # "ai" is generic but no anchors → use domain_terms
            ("OpenClaw", "My OpenClaw setup", True),
            ("OpenClaw", "Generic post about nothing", False),
            ("Stripe payments", "Stripe is great", True),
            ("bias", "cognitive bias research", True),  # no naive stem corruption
        ]
        for domain, text, expected in test_cases:
            pipeline_result = pipeline._matches_discovery_domain(domain, text)
            listing_result = rl._matches_discovery_domain(domain, text)
            assert pipeline_result == listing_result, (
                f"Parity mismatch for ({domain!r}, {text!r}): "
                f"pipeline={pipeline_result}, listing={listing_result}"
            )
            assert pipeline_result == expected, (
                f"Expected {expected} for ({domain!r}, {text!r}), got {pipeline_result}"
            )
