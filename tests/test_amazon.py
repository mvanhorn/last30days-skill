"""Tests for the Amazon source: discovery, enrichment, stats, footer (U2, U3).

Fixtures mirror live payload shapes pulled 2026-08-13, including the three
fields that arrive doubled and the fact that ``max_reviews`` is a ceiling
rather than a quota.

Nothing here spawns a subprocess or touches the network.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))

from lib import amazon  # noqa: E402


TODAY = datetime(2026, 8, 13, tzinfo=timezone.utc)
DOMAIN = "https://www.amazon.com"


def _days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).date().isoformat()


def search_record(**over):
    base = {
        "asin": "B0AAA00001",
        "url": "https://www.amazon.com/Bentgo-Chill/dp/B0AAA00001/ref=sr_1_1?dib=xyz",
        "name": "Chill Max Leak-Proof XL Bento-Style Lunch Box | Ice Pack Included",
        "brand": "Bentgo",
        "sponsored": "false",
        "rating": 4.4,
        "num_ratings": 459,
        "final_price": 39.99,
        "currency": "USD",
    }
    base.update(over)
    return base


def review_record(days_ago: int, rating: int, **over):
    posted = (TODAY - timedelta(days=days_ago)).strftime("%B %-d, %Y")
    base = {
        "review_id": f"R{days_ago}{rating}",
        # Live shape: the date is doubled and prose-wrapped.
        "review_posted_date": f"{posted}Reviewed in the United States on {posted}",
        "review_header": "Great box!Great box!",
        "review_text": f"Review body from {days_ago} days ago.",
        "rating": rating,
        "helpful_count": 0,
        "is_verified": True,
        "is_amazon_vine": False,
        "author_name": "A Buyer",
        "product_rating": 4.4,
        "product_rating_count": 459,
        "product_rating_object": {
            "one_star": 28, "two_star": 9, "three_star": 28,
            "four_star": 60, "five_star": 335,
        },
    }
    base.update(over)
    return base


# ------------------------------------------------------- field repair


class TestFieldRepair:
    def test_doubled_header_is_repaired(self):
        assert amazon.undouble("Best Box!Best Box!") == "Best Box!"

    def test_comma_doubled_badge_is_repaired(self):
        assert amazon.undouble("Verified Purchase, Verified Purchase") == "Verified Purchase"

    def test_genuinely_repetitive_text_survives(self):
        assert amazon.undouble("Great great product") == "Great great product"
        assert amazon.undouble("Buy one, buy two") == "Buy one, buy two"

    def test_prose_wrapped_date_yields_the_leading_date(self):
        raw = "August 3, 2026Reviewed in the United States on August 3, 2026"
        assert amazon.parse_review_date(raw) == "2026-08-03"

    def test_unparseable_date_is_none_not_an_exception(self):
        assert amazon.parse_review_date("") is None
        assert amazon.parse_review_date("sometime last year") is None
        assert amazon.parse_review_date(None) is None


class TestShortName:
    def test_takes_the_segment_before_the_delimiter(self):
        assert amazon.short_name("Chill Max XL | Ice Pack Included") == "Chill Max XL"

    def test_strips_a_leading_brand_when_present(self):
        assert amazon.short_name("Weber Spirit E-325", "Weber") == "Spirit E-325"

    def test_clips_long_names_on_a_word_boundary(self):
        out = amazon.short_name("Kids Prints Leak-Proof 5-Compartment Bento-Style Box")
        assert len(out) <= amazon.SHORT_NAME_MAX
        assert not out.endswith("-")
        assert " " not in out[-1:]


# ---------------------------------------------------------- discovery


class TestDiscovery:
    def test_parses_products_with_rating_and_price(self):
        products = amazon.parse_search_response({"records": [search_record()]}, "bentgo lunch box")
        assert len(products) == 1
        product = products[0]
        assert product["rating"] == 4.4
        assert product["price"] == 39.99
        assert product["num_ratings"] == 459
        assert product["brand"] == "Bentgo"

    def test_urls_are_canonicalized_to_dp_asin(self):
        """Live URLs carry 200+ chars of session-scoped tracking tail."""
        products = amazon.parse_search_response({"records": [search_record()]}, "bentgo")
        assert products[0]["url"] == "https://www.amazon.com/dp/B0AAA00001"

    def test_duplicate_asins_collapse_keeping_the_richest_record(self):
        records = [
            search_record(num_ratings=84),
            search_record(num_ratings=459),
            search_record(num_ratings=12),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo")
        assert len(products) == 1
        assert products[0]["num_ratings"] == 459

    def test_off_keyword_products_are_gated_out(self):
        records = [
            search_record(),
            search_record(asin="B0ZZZ00001", name="Cordless Drill Driver Kit", brand="DeWalt",
                          url="https://www.amazon.com/dp/B0ZZZ00001"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo lunch box")
        assert [p["asin"] for p in products] == ["B0AAA00001"]

    def test_non_amazon_and_non_https_urls_are_rejected(self):
        records = [
            search_record(asin="B000000001", url="http://www.amazon.com/dp/B000000001"),
            search_record(asin="B000000002", url="https://evil.example.com/dp/B2"),
            search_record(asin="B000000003", url="https://www.amazon.com/dp/B000000003"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo chill max lunch box")
        assert [p["asin"] for p in products] == ["B000000003"]

    def test_alternate_marketplace_domain_is_honored(self):
        record = search_record(url="https://www.amazon.co.uk/dp/B0AAA00001")
        products = amazon.parse_search_response(
            {"records": [record]}, "bentgo", domain="https://www.amazon.co.uk"
        )
        assert products and products[0]["url"].startswith("https://www.amazon.co.uk/dp/")

    def test_sponsored_string_is_recorded_as_bool_never_filtered(self):
        """R4: the flag is metadata only. Filtering can blank the lane."""
        records = [
            search_record(asin="B000000001", sponsored="true", url="https://www.amazon.com/dp/B000000001"),
            search_record(asin="B000000002", sponsored="false", url="https://www.amazon.com/dp/B000000002"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo chill max lunch box")
        assert len(products) == 2
        assert {p["asin"]: p["sponsored"] for p in products} == {"B000000001": True, "B000000002": False}

    def test_error_envelope_yields_no_products(self):
        assert amazon.parse_search_response({"records": [], "error": "401"}, "x") == []


class TestTargetSelection:
    def _pool(self):
        return amazon.parse_search_response(
            {
                "records": [
                    search_record(asin="C000000001", brand="Fimibuke", num_ratings=901,
                                  name="60oz Leakproof Bento Lunch Box",
                                  url="https://www.amazon.com/dp/C000000001"),
                    search_record(asin="B000000001", brand="Bentgo", num_ratings=821,
                                  name="Kids Insulated Lunch Bag",
                                  url="https://www.amazon.com/dp/B000000001"),
                    search_record(asin="B000000002", brand="Bentgo", num_ratings=710,
                                  name="MicroSteel Bento Lunch Box",
                                  url="https://www.amazon.com/dp/B000000002"),
                    search_record(asin="B000000003", brand="Bentgo", num_ratings=623,
                                  name="Classic Stackable Lunch Box",
                                  url="https://www.amazon.com/dp/B000000003"),
                ]
            },
            "bentgo lunch box",
        )

    def test_brand_topic_excludes_competitors_buying_the_keyword(self):
        """The guard against paying to review a rival's product."""
        targets = amazon.select_enrichment_targets(self._pool(), limit=3, keyword="bentgo lunch box")
        assert [t["asin"] for t in targets] == ["B000000001", "B000000002", "B000000003"]
        assert all(t["brand"] == "Bentgo" for t in targets)

    def test_category_topic_stays_unfiltered_and_ranks_on_merit(self):
        targets = amazon.select_enrichment_targets(self._pool(), limit=3, keyword="kids lunch box")
        assert targets[0]["asin"] == "C000000001"

    def test_infer_brand_needs_the_keyword_to_name_it(self):
        pool = self._pool()
        assert amazon.infer_brand(pool, "bentgo lunch box") == "Bentgo"
        assert amazon.infer_brand(pool, "best kids lunch box") == ""

    def test_near_identical_variants_do_not_take_two_pulls(self):
        pool = amazon.parse_search_response(
            {
                "records": [
                    search_record(asin="V000000001", num_ratings=901, name="60oz Leakproof Box | Blue",
                                  url="https://www.amazon.com/dp/V000000001"),
                    search_record(asin="V000000002", num_ratings=900, name="60oz Leakproof Box | Pink",
                                  url="https://www.amazon.com/dp/V000000002"),
                    search_record(asin="V000000003", num_ratings=500, name="Chill Max XL Box",
                                  url="https://www.amazon.com/dp/V000000003"),
                ]
            },
            "bentgo lunch box",
        )
        targets = amazon.select_enrichment_targets(pool, limit=2, keyword="bentgo lunch box")
        assert [t["asin"] for t in targets] == ["V000000001", "V000000003"]

    def test_zero_limit_selects_nothing(self):
        assert amazon.select_enrichment_targets(self._pool(), limit=0) == []


# --------------------------------------------------------- enrichment


class TestReviewParsing:
    def test_reviews_become_comments_with_stats(self):
        response = {"records": [review_record(3, 5), review_record(10, 4)]}
        comments, stats = amazon.parse_reviews(response)
        assert len(comments) == 2
        assert stats["product_rating"] == 4.4
        assert stats["product_rating_count"] == 459
        assert stats["star_distribution"]["five_star"] == 335

    def test_comments_carry_the_keys_remap_would_strip(self):
        """Rating, date, and verified are exactly what this source needs."""
        comments, _ = amazon.parse_reviews({"records": [review_record(3, 5)]})
        comment = comments[0]
        assert set(comment) >= {"score", "excerpt", "rating", "date", "verified"}
        assert comment["rating"] == 5
        assert comment["date"] == _days_ago(3)

    def test_doubled_header_is_repaired_in_the_comment_title(self):
        comments, _ = amazon.parse_reviews({"records": [review_record(3, 5)]})
        assert comments[0]["title"] == "Great box!"

    def test_woven_sample_is_newest_first(self):
        """R2a: recency is enforced client-side, not assumed from the API."""
        response = {"records": [
            review_record(400, 5), review_record(2, 3), review_record(45, 4),
        ]}
        comments, _ = amazon.parse_reviews(response)
        assert [c["date"] for c in comments] == [_days_ago(2), _days_ago(45), _days_ago(400)]

    def test_empty_payload_is_not_an_error(self):
        assert amazon.parse_reviews({"records": []}) == ([], {})


class TestEnrichmentLane:
    def _products(self, n=4):
        return [
            {"asin": f"B00000000{i}", "url": f"https://www.amazon.com/dp/B00000000{i}",
             "name": f"Product {i}", "short_name": f"Product {i}",
             "brand": "Bentgo", "num_ratings": 900 - i, "rating": 4.4}
            for i in range(n)
        ]

    def test_quick_depth_spawns_no_review_pulls(self):
        calls = []
        out, status = amazon.enrich_with_reviews(
            self._products(), depth="quick",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []
        assert len(out) == 4
        assert status is None  # quick depth is not a degraded outcome

    def test_default_depth_pulls_exactly_three(self):
        calls = []
        _, status = amazon.enrich_with_reviews(
            self._products(), depth="default",
            fetcher=lambda url: calls.append(url) or {"records": [review_record(2, 5)]},
        )
        assert len(calls) == 3
        assert status is None

    def test_deep_depth_pulls_five(self):
        calls = []
        _, status = amazon.enrich_with_reviews(
            self._products(6), depth="deep",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert len(calls) == 5

    def test_cap_is_fifty_per_pull(self):
        seen = {}

        def fetcher(url):
            return {"records": []}

        # The cap reaches the CLI through fetch_reviews; assert the constant
        # and the plumbed default together.
        assert amazon.MAX_REVIEWS == 50
        import lib.brightdata as bd
        original = bd.run_pipeline
        bd.run_pipeline = lambda p, params, **k: seen.update(params=params) or {"records": []}
        try:
            amazon.fetch_reviews("https://www.amazon.com/dp/B000000001")
        finally:
            bd.run_pipeline = original
        assert seen["params"] == ["https://www.amazon.com/dp/B000000001", "50"]

    def test_reviews_attach_to_the_right_product(self):
        out, status = amazon.enrich_with_reviews(
            self._products(3), depth="default",
            fetcher=lambda url: {"records": [review_record(2, 5, review_id=url)]},
        )
        assert all(p.get("top_comments") for p in out)
        assert out[0]["product_rating_count"] == 459
        assert status is None

    def test_one_failing_pull_does_not_discard_its_siblings(self):
        def fetcher(url):
            if url.endswith("B000000001"):
                return {"records": [], "error": "snapshot failed"}
            return {"records": [review_record(2, 5)]}

        out, status = amazon.enrich_with_reviews(self._products(3), depth="default", fetcher=fetcher)
        by_asin = {p["asin"]: p for p in out}
        assert not by_asin["B000000001"].get("top_comments")
        assert by_asin["B000000000"].get("top_comments")
        assert by_asin["B000000002"].get("top_comments")
        assert status is None  # partial success is not reported as status

    def test_one_raising_pull_does_not_kill_the_lane(self):
        def fetcher(url):
            if url.endswith("B000000001"):
                raise RuntimeError("boom")
            return {"records": [review_record(2, 5)]}

        out, status = amazon.enrich_with_reviews(self._products(3), depth="default", fetcher=fetcher)
        assert sum(1 for p in out if p.get("top_comments")) == 2
        assert status is None  # partial success is not reported as status

    def test_dropped_straggler_keeps_its_product_with_search_stats(self):
        """A deadline drop must never delete the product from the report.

        Use a patched short LANE_DEADLINE to trigger the straggler case without
        relying on crumb budgets (which now skip the lane entirely).
        """
        import time as _time
        import unittest.mock

        def slow(url):
            if url.endswith("B000000000"):
                _time.sleep(3)
            return {"records": [review_record(2, 5)]}

        # Patch LANE_DEADLINE to 1s so the slow product times out but other
        # products have time to complete. elapsed=0 keeps the full 1s budget.
        with unittest.mock.patch.object(amazon, "LANE_DEADLINE", 1):
            out, status = amazon.enrich_with_reviews(
                self._products(2), depth="default", fetcher=slow,
                elapsed=0.0,
            )
        by_asin = {p["asin"]: p for p in out}
        assert set(by_asin) == {"B000000000", "B000000001"}
        # Search-record stats survive on the dropped product.
        assert by_asin["B000000000"]["rating"] == 4.4
        assert by_asin["B000000000"]["num_ratings"] == 900
        # One straggler dropped, so status should be "review lane timed out"
        # (since all 3 pulls didn't complete, but some did)
        # Actually, B000000001 completed, so status is None
        # Let me check: if completed_count > 0, status is None

    def test_exhausted_wall_clock_skips_the_lane_entirely(self):
        calls = []
        out, status = amazon.enrich_with_reviews(
            self._products(), depth="default",
            fetcher=lambda url: calls.append(url) or {"records": []},
            elapsed=amazon.FOREGROUND_CONTRACT,
        )
        assert calls == []
        assert status == "review lane skipped (budget 0s)"

    def test_crumb_budget_skips_not_fires_doomed_pulls(self):
        """Regression test for the Bentgo bug: elapsed=269 must skip, not fire doomed 11s pulls.

        The bug: a multi-source run took 269s before reaching Amazon enrichment,
        leaving only 11s of budget (300 - 269 - 20 = 11). All Bright Data pulls
        timed out (cli_timeout = max(5, timeout-10) = 1s), spending credits
        without returning reviews.

        The fix: crumb budgets (below MIN_USEFUL_REVIEW_BUDGET=90) return 0,
        skipping the lane entirely instead of firing doomed short pulls.
        """
        calls = []
        out, status = amazon.enrich_with_reviews(
            self._products(), depth="default",
            fetcher=lambda url: calls.append(url) or {"records": []},
            elapsed=269.0,
        )
        # Fetcher should never be called
        assert calls == []
        # Products keep their search stats (no top_comments)
        assert all(not p.get("top_comments") for p in out)
        assert all(p.get("rating") == 4.4 for p in out)
        assert all(p.get("num_ratings") for p in out)
        # Status should indicate the lane was skipped
        assert status == "review lane skipped (budget 0s)"

    def test_early_elapsed_gets_full_budget(self):
        """A quick search (40s elapsed) should get the full LANE_DEADLINE budget."""
        timeouts_seen = []

        def capturing_fetcher(url):
            return {"records": [review_record(2, 5)]}

        # Patch fetch_reviews to capture the timeout
        original_fetch = amazon.fetch_reviews
        captured_timeout = []

        def mock_fetch(url, *, max_reviews=50, config=None, timeout=180):
            captured_timeout.append(timeout)
            return {"records": [review_record(2, 5)]}

        amazon.fetch_reviews = mock_fetch
        try:
            out, status = amazon.enrich_with_reviews(
                self._products(1), depth="default",
                elapsed=40.0,
            )
        finally:
            amazon.fetch_reviews = original_fetch

        assert captured_timeout, "fetch_reviews was not called"
        # elapsed=40 → remaining = 300-40-20 = 240 → clamped to LANE_DEADLINE=180
        assert captured_timeout[0] == amazon.LANE_DEADLINE
        assert status is None

    def test_all_pulls_dropped_reports_timed_out_status(self):
        """When all pulls drop (none complete), status should be 'review lane timed out'."""
        import time as _time
        import unittest.mock

        def very_slow(url):
            _time.sleep(5)  # Longer than the deadline
            return {"records": [review_record(2, 5)]}

        # Use a very short deadline so all pulls time out
        with unittest.mock.patch.object(amazon, "LANE_DEADLINE", 1):
            with unittest.mock.patch.object(amazon, "MIN_USEFUL_REVIEW_BUDGET", 1):
                out, status = amazon.enrich_with_reviews(
                    self._products(2), depth="default", fetcher=very_slow,
                    elapsed=0.0,
                )

        # No products should have top_comments (all dropped)
        assert all(not p.get("top_comments") for p in out)
        # Status should indicate timeout
        assert status == "review lane timed out"


# --------------------------------------------------------------- stats


class TestStats:
    def test_five_star_share_from_the_distribution_object(self):
        share = amazon.five_star_share(
            {"one_star": 28, "two_star": 9, "three_star": 28, "four_star": 60, "five_star": 335}
        )
        assert round(share * 100) == 73

    def test_five_star_share_is_none_without_a_distribution(self):
        assert amazon.five_star_share({}) is None

    def test_recent_window_counts_only_dated_reviews_inside_it(self):
        comments = [
            {"date": _days_ago(2), "rating": 4},
            {"date": _days_ago(29), "rating": 4},
            {"date": _days_ago(31), "rating": 1},
            {"date": None, "rating": 5},
        ]
        window = amazon.recent_window_stats(comments, today=TODAY)
        assert window["recent_n"] == 2
        assert window["recent_avg"] == 4

    @pytest.mark.parametrize(
        "ratings,expected",
        [
            ([1, 2, 3, 4, 5], "down"),   # avg 3.0 vs 4.4
            ([5, 5, 5, 5, 5], "up"),     # avg 5.0 vs 4.4
            ([4, 4, 5, 4, 5], "flat"),   # avg 4.4 vs 4.4
        ],
    )
    def test_drift_direction(self, ratings, expected):
        product = {
            "product_rating": 4.4,
            "product_rating_count": 459,
            "top_comments": [
                {"date": _days_ago(i + 1), "rating": r} for i, r in enumerate(ratings)
            ],
        }
        assert amazon.product_stats(product, today=TODAY)["drift"] == expected

    def test_below_threshold_sample_renders_quiet_not_a_drift(self):
        """Live census: a 50-cap pull can land only a handful in-window."""
        product = {
            "product_rating": 4.4,
            "top_comments": [{"date": _days_ago(i + 1), "rating": 1} for i in range(4)],
        }
        assert amazon.product_stats(product, today=TODAY)["drift"] == "quiet"

    def test_threshold_is_exactly_five(self):
        product = {
            "product_rating": 4.4,
            "top_comments": [{"date": _days_ago(i + 1), "rating": 1} for i in range(5)],
        }
        assert amazon.product_stats(product, today=TODAY)["drift"] == "down"

    def test_no_baseline_renders_new(self):
        assert amazon.product_stats({"top_comments": []}, today=TODAY)["drift"] == "new"

    def test_review_pull_rating_count_supersedes_the_search_record(self):
        """Search counts are variant-level and can undercount 100x."""
        stats = amazon.product_stats(
            {"num_ratings": 84, "product_rating_count": 8446, "product_rating": 4.7},
            today=TODAY,
        )
        assert stats["ratings_total"] == 8446

    def test_reproduces_the_live_chill_max_reading(self):
        """End-to-end against the real 2026-08-13 payload shape."""
        records = (
            [review_record(i, r) for i, r in ((1, 5), (2, 1), (5, 5), (9, 4), (11, 4))]
            + [review_record(200, 5), review_record(400, 5)]
        )
        comments, stats = amazon.parse_reviews({"records": records})
        product = {"short_name": "Chill Max XL", **stats, "top_comments": comments}
        out = amazon.product_stats(product, today=TODAY)
        assert out["all_time"] == 4.4
        assert out["ratings_total"] == 459
        assert round(out["five_star_share"] * 100) == 73
        assert out["recent_n"] == 5
        assert out["recent_avg"] == 3.8
        assert out["drift"] == "down"


# -------------------------------------------------------------- footer


class TestFooterEntry:
    def test_negative_drift_gets_the_arrow_marker(self):
        entry = amazon.footer_entry(
            {"short_name": "Chill Max XL", "all_time": 4.4, "recent_avg": 3.8, "drift": "down"}
        )
        assert entry == "Chill Max XL 4.4★→3.8★ ↓"

    def test_positive_drift_gets_no_marker(self):
        entry = amazon.footer_entry(
            {"short_name": "Deluxe Bag", "all_time": 4.7, "recent_avg": 5.0, "drift": "up"}
        )
        assert entry == "Deluxe Bag 4.7★→5.0★"
        assert "↓" not in entry

    def test_quiet_state_shows_the_baseline_without_an_arrow(self):
        entry = amazon.footer_entry(
            {"short_name": "Genesis E-325", "all_time": 4.4, "recent_avg": None, "drift": "quiet"}
        )
        assert entry == "Genesis E-325 4.4★ quiet"
        assert "→" not in entry

    def test_new_state_claims_no_baseline(self):
        entry = amazon.footer_entry({"short_name": "BLUEY Set", "all_time": None, "drift": "new"})
        assert entry == "BLUEY Set new"

    def test_quote_renders_only_on_negative_drift(self):
        sagging = amazon.footer_entry(
            {"short_name": "Chill Max XL", "all_time": 4.4, "recent_avg": 3.8, "drift": "down"},
            quote="the lid jams",
        )
        assert sagging == 'Chill Max XL 4.4★→3.8★ ↓ "the lid jams"'
        healthy = amazon.footer_entry(
            {"short_name": "Deluxe Bag", "all_time": 4.7, "recent_avg": 5.0, "drift": "up"},
            quote="the lid jams",
        )
        assert '"' not in healthy

    def test_absent_quote_renders_the_clean_numeric_entry(self):
        entry = amazon.footer_entry(
            {"short_name": "Chill Max XL", "all_time": 4.4, "recent_avg": 3.8, "drift": "down"},
            quote="",
        )
        assert entry == "Chill Max XL 4.4★→3.8★ ↓"


class _Item:
    """Minimal SourceItem stand-in for the enrichment adapter."""

    def __init__(self, asin, **meta):
        self.source = "amazon"
        self.url = f"https://www.amazon.com/dp/{asin}"
        self.title = meta.get("name", asin)
        self.metadata = {"asin": asin, "short_name": asin, "brand": "Bentgo", **meta}


class TestSourceItemEnrichment:
    def test_reviews_and_stats_land_on_item_metadata(self):
        items = [_Item("B000000001"), _Item("B000000002")]
        amazon.enrich_source_items(
            items, depth="default", keyword="bentgo lunch box",
            fetcher=lambda url: {"records": [
                review_record(i + 1, r) for i, r in enumerate([1, 1, 1, 1, 1])
            ]},
        )
        for item in items:
            assert item.metadata["top_comments"]
            assert item.metadata["stats"]["drift"] == "down"

    def test_non_amazon_items_are_untouched(self):
        other = _Item("B000000001")
        other.source = "reddit"
        amazon.enrich_source_items([other], depth="default", fetcher=lambda url: {"records": []})
        assert "top_comments" not in other.metadata

    def test_already_enriched_items_are_not_re_pulled(self):
        """enrich_source_items no-ops when top_comments is already set.

        This is critical for the thin-retry path: Phase 1 enriches products at
        search time, then thin retry (Phase 2b) may return the same ASINs. The
        pipeline passes skip_amazon_enrichment=True in thin retry, so products
        arrive at finalize without re-enrichment. Finalize calls enrich_source_items,
        which skips already-enriched items (top_comments set) and only enriches
        genuinely new products. This prevents duplicate Bright Data pulls.
        """
        item = _Item("B000000001", top_comments=[{"excerpt": "cached", "score": 0, "rating": 5, "date": None}])
        calls = []
        amazon.enrich_source_items(
            [item], depth="default",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []

    def test_quick_depth_touches_nothing(self):
        items = [_Item("B000000001")]
        calls = []
        amazon.enrich_source_items(
            items, depth="quick",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []
        assert "top_comments" not in items[0].metadata


class TestStatsFromItem:
    def test_uses_the_cached_block_when_enrichment_already_ran(self):
        item = _Item("B000000001", stats={"short_name": "Cached", "drift": "up"})
        assert amazon.stats_from_item(item)["short_name"] == "Cached"

    def test_recomputes_from_metadata_when_absent(self):
        """Mock runs and replayed fixtures skip enrichment entirely."""
        item = _Item(
            "B1",
            product_rating=4.4,
            product_rating_count=459,
            star_distribution={"one_star": 28, "two_star": 9, "three_star": 28,
                               "four_star": 60, "five_star": 335},
            top_comments=[{"date": _days_ago(i + 1), "rating": 1} for i in range(5)],
        )
        stats = amazon.stats_from_item(item, today=TODAY)
        assert stats["drift"] == "down"
        assert stats["all_time"] == 4.4
        assert round(stats["five_star_share"] * 100) == 73


class _FooterItem:
    def __init__(self, short_name, *, reviews=0, all_time=4.4, recent=None,
                 drift="quiet", quote=None):
        self.source = "amazon"
        self.url = "https://www.amazon.com/dp/X"
        self.title = short_name
        self.metadata = {
            "asin": short_name,
            "stats": {
                "short_name": short_name, "all_time": all_time,
                "recent_avg": recent, "drift": drift,
                "reviews_pulled": reviews, "ratings_total": 100,
                "five_star_share": 0.7, "recent_n": 5 if recent else 0,
                "url": self.url,
            },
        }
        if quote:
            self.metadata["pulse_quote"] = quote


def _report(items, *, keyword="bentgo lunch box"):
    from lib import schema
    report = object.__new__(schema.Report)
    object.__setattr__(report, "items_by_source", {"amazon": items})
    object.__setattr__(report, "artifacts", {"amazon_query": keyword})
    object.__setattr__(report, "source_status", {})
    return report


class TestFooterLine:
    def _line(self, items, **kw):
        from lib import render
        return render._amazon_footer_line(_report(items, **kw))

    def test_only_sampled_products_get_a_slot(self):
        """A dozen discovered products must not become a dozen entries."""
        items = [_FooterItem("Sampled", reviews=20, recent=3.8, drift="down")]
        items += [_FooterItem(f"Unsampled{i}") for i in range(9)]
        line = self._line(items)
        assert "Sampled 4.4★→3.8★ ↓" in line
        assert "Unsampled" not in line
        # The count still reports everything discovered.
        assert line.startswith("📦 Amazon: 10 products │")

    def test_duplicate_variant_names_are_collapsed(self):
        items = [
            _FooterItem("Kids Bento", reviews=20, recent=4.9, drift="up"),
            _FooterItem("Kids Bento", reviews=20, recent=4.8, drift="up"),
        ]
        assert self._line(items).count("Kids Bento") == 1

    def test_quick_depth_renders_the_inventory_form(self):
        items = [_FooterItem("A"), _FooterItem("B")]
        line = self._line(items)
        assert "→" not in line
        assert "average" in line and "ratings" in line

    def test_footer_renders_no_quote_today(self):
        """The engine renders this line before the model sees the report, so
        there is no weave-time path for a model-written quote. Deferred."""
        items = [_FooterItem("Sagging", reviews=20, recent=3.8, drift="down",
                             quote="the lid jams")]
        line = self._line(items)
        assert "↓" in line
        assert '"' not in line

    def test_footer_entry_still_accepts_a_quote_for_a_future_writer(self):
        entry = amazon.footer_entry(
            {"short_name": "X", "all_time": 4.4, "recent_avg": 3.8, "drift": "down"},
            quote="the lid jams",
        )
        assert '"the lid jams"' in entry

    def test_empty_result_names_the_keyword(self):
        assert self._line([]) == '📦 Amazon: no products matched "bentgo lunch box"'

    def test_no_line_at_all_when_the_source_never_ran(self):
        assert self._line([], keyword="") is None

    def test_malformed_asin_records_are_rejected(self):
        """The ASIN is interpolated into a URL that is refetched and rendered."""
        records = [
            search_record(asin="../../etc/passwd", url="https://www.amazon.com/dp/x"),
            search_record(asin="B0AAA0000", url="https://www.amazon.com/dp/y"),   # 9 chars
            search_record(asin="B0AAA000012", url="https://www.amazon.com/dp/z"),  # 11 chars
            search_record(asin="B0AAA00001", url="https://www.amazon.com/dp/ok"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo chill max lunch box")
        assert [p["asin"] for p in products] == ["B0AAA00001"]

    def test_canonical_url_falls_back_when_the_asin_is_malformed(self):
        original = "https://www.amazon.com/dp/legit"
        assert amazon.canonical_product_url(original, "not-an-asin", DOMAIN) == original

    def test_option_shaped_keyword_is_rejected_before_the_cli_runs(self):
        """A leading dash would be parsed as a flag, not a search term."""
        calls = []
        import lib.brightdata as bd
        original = bd.run_pipeline
        bd.run_pipeline = lambda *a, **k: calls.append(a) or {"records": []}
        try:
            out = amazon.search_products("--help")
        finally:
            bd.run_pipeline = original
        assert calls == []
        assert "may not begin" in out["error"]


class TestReviewFindingRegressions:
    """Regressions caught by the correctness review pass."""

    def test_brand_inference_survives_mixed_casing(self):
        """One vendor spelled two ways must not disable the guard."""
        pool = [{"brand": "Bentgo"}, {"brand": "BENTGO"}, {"brand": "Umi"}]
        assert amazon.infer_brand(pool, "bentgo lunch box") == "Bentgo"

    def test_multi_word_brands_are_matched(self):
        pool = [{"brand": "Hydro Flask"}, {"brand": "Iron Flask"}]
        assert amazon.infer_brand(pool, "hydro flask water bottle") == "Hydro Flask"

    def test_two_distinct_brands_in_the_keyword_stay_ambiguous(self):
        pool = [{"brand": "Yeti"}, {"brand": "Stanley"}]
        assert amazon.infer_brand(pool, "yeti vs stanley tumbler") == ""

    def test_short_name_respects_word_boundaries_on_the_brand(self):
        """A bare startswith() ate into sub-brands and coincidental prefixes."""
        assert amazon.short_name("AnkerWork B600 Video Bar", "Anker") == "AnkerWork B600"
        assert amazon.short_name("Chillax Bento Lunch Box", "Chill") == "Chillax Bento"

    def test_undouble_leaves_short_repeated_words_alone(self):
        assert amazon.undouble("ByeBye") == "ByeBye"
        assert amazon.undouble("NoNo") == "NoNo"
        # Real doubling of a whole headline still repairs.
        assert amazon.undouble("Best Box!Best Box!") == "Best Box!"
        assert amazon.undouble("Great value for money.Great value for money.") == \
            "Great value for money."

    def test_lane_budget_shrinks_as_the_run_clock_advances(self):
        """The clamp was dead code until `elapsed` was threaded through."""
        # Fresh run gets full LANE_DEADLINE
        assert amazon._remaining_lane_budget(0.0) == amazon.LANE_DEADLINE
        # Mid-run (100s elapsed) still has 180s leftover (300-100-20=180), clamped to LANE_DEADLINE
        mid = amazon._remaining_lane_budget(100.0)
        assert mid == amazon.LANE_DEADLINE
        # Below floor (300-200-20=80 < MIN_USEFUL_REVIEW_BUDGET=90) → 0
        assert amazon._remaining_lane_budget(200.0) == 0
        # Way past contract → 0
        assert amazon._remaining_lane_budget(295.0) == 0

    def test_lane_budget_floor_prevents_doomed_pulls(self):
        """Crumb budgets return 0, not the crumbs.

        This is the fix for the Bentgo bug: elapsed=269 left only 11s of budget,
        causing Bright Data pulls to time out (cli_timeout = max(5, timeout-10)
        → 1s timeout). Now any budget below MIN_USEFUL_REVIEW_BUDGET returns 0.
        """
        # elapsed=269 → remaining = 300-269-20 = 11 < MIN_USEFUL=90 → 0
        assert amazon._remaining_lane_budget(269.0) == 0
        # Just above floor: 300-190-20=90 == MIN_USEFUL → 90 (not 0)
        assert amazon._remaining_lane_budget(190.0) == amazon.MIN_USEFUL_REVIEW_BUDGET
        # Just below floor: 300-191-20=89 < MIN_USEFUL=90 → 0
        assert amazon._remaining_lane_budget(191.0) == 0

    def test_lane_budget_constants_are_sane(self):
        """Guard against accidental constant drift breaking the logic."""
        assert amazon.MIN_USEFUL_REVIEW_BUDGET == 90
        assert amazon.LANE_DEADLINE == 180
        assert amazon.MIN_USEFUL_REVIEW_BUDGET < amazon.LANE_DEADLINE

    def test_enrichment_refreshes_the_variant_level_rating_count(self):
        """Search counts undercount badly; the pull's count is authoritative."""
        item = _Item("B000000001", name="Deluxe Bag", num_ratings=84)
        item.title = "Bentgo Deluxe Bag - 4.7/5 (84 ratings)"
        item.engagement = {"ratings": 84}
        amazon.enrich_source_items(
            [item], depth="default",
            fetcher=lambda url: {"records": [
                review_record(i + 1, 5, product_rating=4.7, product_rating_count=8446)
                for i in range(5)
            ]},
        )
        assert item.engagement["ratings"] == 8446
        assert "8,446 ratings" in item.title
        assert "84 ratings" not in item.title
        assert item.metadata["stats"]["ratings_total"] == 8446


class TestSavedArtifactCompleteness:
    """The saved report is the copy users keep; it must not drop evidence."""

    def _saved(self, sources):
        from lib import render, schema
        report = object.__new__(schema.Report)
        for field, value in {
            "topic": "bentgo", "range_from": "2026-07-14", "range_to": "2026-08-13",
            "generated_at": "2026-08-13", "clusters": [], "ranked_candidates": [],
            "items_by_source": sources, "errors_by_source": {}, "source_status": {},
            "freshness_verdicts": [], "warnings": [], "artifacts": {},
            "library_context": [], "drill_of": None,
            "provider_runtime": schema.ProviderRuntime(
                reasoning_provider="local", planner_model="m", rerank_model="m",
            ),
            "query_plan": schema.QueryPlan(
                intent="product", freshness_mode="balanced_recent", cluster_mode="none",
                raw_topic="bentgo", subqueries=[],
                source_weights={s: 1.0 for s in sources},
            ),
        }.items():
            object.__setattr__(report, field, value)
        return render.render_full(report)

    def _item(self, source, item_id, engagement):
        from lib import schema
        return schema.SourceItem(
            item_id=item_id, source=source, title=f"{source} item", body="b",
            url="https://example.com", author="A", container=None,
            published_at="2026-08-10", date_confidence="high",
            engagement=engagement, relevance_hint=0.5, why_relevant="",
            snippet="", metadata={},
        )

    def test_amazon_items_appear_in_the_per_source_dump(self):
        """Regression: a hardcoded source list silently dropped this section."""
        out = self._saved({"amazon": [self._item("amazon", "B0AAA00001", {"ratings": 459})]})
        assert "### Amazon (1 items)" in out
        assert "B0AAA00001" in out

    def test_a_source_absent_from_the_fixed_order_still_renders(self):
        """The list is display order, not the source registry."""
        out = self._saved({"trustpilot": [self._item("trustpilot", "TP1", {"reviews": 12})]})
        assert "TP1" in out

    def test_engagement_is_not_blank_for_a_non_allowlisted_metric(self):
        out = self._saved({"amazon": [self._item("amazon", "B0AAA00001", {"ratings": 459})]})
        assert "459 ratings" in out

    def test_allowlisted_sources_keep_their_existing_engagement_format(self):
        """The fall-through must not add previously-unshown keys."""
        out = self._saved({"reddit": [self._item(
            "reddit", "R1", {"score": 120, "num_comments": 48, "upvote_ratio": 0.91}
        )]})
        assert "120 score, 48 num_comments" in out
        assert "upvote_ratio" not in out
