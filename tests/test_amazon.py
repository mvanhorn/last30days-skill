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
            search_record(asin="B0ZZZ", name="Cordless Drill Driver Kit", brand="DeWalt",
                          url="https://www.amazon.com/dp/B0ZZZ"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo lunch box")
        assert [p["asin"] for p in products] == ["B0AAA00001"]

    def test_non_amazon_and_non_https_urls_are_rejected(self):
        records = [
            search_record(asin="B1", url="http://www.amazon.com/dp/B1"),
            search_record(asin="B2", url="https://evil.example.com/dp/B2"),
            search_record(asin="B3", url="https://www.amazon.com/dp/B3"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo chill max lunch box")
        assert [p["asin"] for p in products] == ["B3"]

    def test_alternate_marketplace_domain_is_honored(self):
        record = search_record(url="https://www.amazon.co.uk/dp/B0AAA00001")
        products = amazon.parse_search_response(
            {"records": [record]}, "bentgo", domain="https://www.amazon.co.uk"
        )
        assert products and products[0]["url"].startswith("https://www.amazon.co.uk/dp/")

    def test_sponsored_string_is_recorded_as_bool_never_filtered(self):
        """R4: the flag is metadata only. Filtering can blank the lane."""
        records = [
            search_record(asin="B1", sponsored="true", url="https://www.amazon.com/dp/B1"),
            search_record(asin="B2", sponsored="false", url="https://www.amazon.com/dp/B2"),
        ]
        products = amazon.parse_search_response({"records": records}, "bentgo chill max lunch box")
        assert len(products) == 2
        assert {p["asin"]: p["sponsored"] for p in products} == {"B1": True, "B2": False}

    def test_error_envelope_yields_no_products(self):
        assert amazon.parse_search_response({"records": [], "error": "401"}, "x") == []


class TestTargetSelection:
    def _pool(self):
        return amazon.parse_search_response(
            {
                "records": [
                    search_record(asin="C1", brand="Fimibuke", num_ratings=901,
                                  name="60oz Leakproof Bento Lunch Box",
                                  url="https://www.amazon.com/dp/C1"),
                    search_record(asin="B1", brand="Bentgo", num_ratings=821,
                                  name="Kids Insulated Lunch Bag",
                                  url="https://www.amazon.com/dp/B1"),
                    search_record(asin="B2", brand="Bentgo", num_ratings=710,
                                  name="MicroSteel Bento Lunch Box",
                                  url="https://www.amazon.com/dp/B2"),
                    search_record(asin="B3", brand="Bentgo", num_ratings=623,
                                  name="Classic Stackable Lunch Box",
                                  url="https://www.amazon.com/dp/B3"),
                ]
            },
            "bentgo lunch box",
        )

    def test_brand_topic_excludes_competitors_buying_the_keyword(self):
        """The guard against paying to review a rival's product."""
        targets = amazon.select_enrichment_targets(self._pool(), limit=3, keyword="bentgo lunch box")
        assert [t["asin"] for t in targets] == ["B1", "B2", "B3"]
        assert all(t["brand"] == "Bentgo" for t in targets)

    def test_category_topic_stays_unfiltered_and_ranks_on_merit(self):
        targets = amazon.select_enrichment_targets(self._pool(), limit=3, keyword="kids lunch box")
        assert targets[0]["asin"] == "C1"

    def test_infer_brand_needs_the_keyword_to_name_it(self):
        pool = self._pool()
        assert amazon.infer_brand(pool, "bentgo lunch box") == "Bentgo"
        assert amazon.infer_brand(pool, "best kids lunch box") == ""

    def test_near_identical_variants_do_not_take_two_pulls(self):
        pool = amazon.parse_search_response(
            {
                "records": [
                    search_record(asin="V1", num_ratings=901, name="60oz Leakproof Box | Blue",
                                  url="https://www.amazon.com/dp/V1"),
                    search_record(asin="V2", num_ratings=900, name="60oz Leakproof Box | Pink",
                                  url="https://www.amazon.com/dp/V2"),
                    search_record(asin="V3", num_ratings=500, name="Chill Max XL Box",
                                  url="https://www.amazon.com/dp/V3"),
                ]
            },
            "bentgo lunch box",
        )
        targets = amazon.select_enrichment_targets(pool, limit=2, keyword="bentgo lunch box")
        assert [t["asin"] for t in targets] == ["V1", "V3"]

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
            {"asin": f"B{i}", "url": f"https://www.amazon.com/dp/B{i}",
             "name": f"Product {i}", "short_name": f"Product {i}",
             "brand": "Bentgo", "num_ratings": 900 - i, "rating": 4.4}
            for i in range(n)
        ]

    def test_quick_depth_spawns_no_review_pulls(self):
        calls = []
        out = amazon.enrich_with_reviews(
            self._products(), depth="quick",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []
        assert len(out) == 4

    def test_default_depth_pulls_exactly_three(self):
        calls = []
        amazon.enrich_with_reviews(
            self._products(), depth="default",
            fetcher=lambda url: calls.append(url) or {"records": [review_record(2, 5)]},
        )
        assert len(calls) == 3

    def test_deep_depth_pulls_five(self):
        calls = []
        amazon.enrich_with_reviews(
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
            amazon.fetch_reviews("https://www.amazon.com/dp/B1")
        finally:
            bd.run_pipeline = original
        assert seen["params"] == ["https://www.amazon.com/dp/B1", "50"]

    def test_reviews_attach_to_the_right_product(self):
        out = amazon.enrich_with_reviews(
            self._products(3), depth="default",
            fetcher=lambda url: {"records": [review_record(2, 5, review_id=url)]},
        )
        assert all(p.get("top_comments") for p in out)
        assert out[0]["product_rating_count"] == 459

    def test_one_failing_pull_does_not_discard_its_siblings(self):
        def fetcher(url):
            if url.endswith("B1"):
                return {"records": [], "error": "snapshot failed"}
            return {"records": [review_record(2, 5)]}

        out = amazon.enrich_with_reviews(self._products(3), depth="default", fetcher=fetcher)
        by_asin = {p["asin"]: p for p in out}
        assert not by_asin["B1"].get("top_comments")
        assert by_asin["B0"].get("top_comments")
        assert by_asin["B2"].get("top_comments")

    def test_one_raising_pull_does_not_kill_the_lane(self):
        def fetcher(url):
            if url.endswith("B1"):
                raise RuntimeError("boom")
            return {"records": [review_record(2, 5)]}

        out = amazon.enrich_with_reviews(self._products(3), depth="default", fetcher=fetcher)
        assert sum(1 for p in out if p.get("top_comments")) == 2

    def test_dropped_straggler_keeps_its_product_with_search_stats(self):
        """A deadline drop must never delete the product from the report."""
        import time as _time

        def slow(url):
            if url.endswith("B0"):
                _time.sleep(3)
            return {"records": [review_record(2, 5)]}

        out = amazon.enrich_with_reviews(
            self._products(2), depth="default", fetcher=slow,
            elapsed=amazon.FOREGROUND_CONTRACT - amazon.RENDER_MARGIN - 1,
        )
        by_asin = {p["asin"]: p for p in out}
        assert set(by_asin) == {"B0", "B1"}
        # Search-record stats survive on the dropped product.
        assert by_asin["B0"]["rating"] == 4.4
        assert by_asin["B0"]["num_ratings"] == 900

    def test_exhausted_wall_clock_skips_the_lane_entirely(self):
        calls = []
        amazon.enrich_with_reviews(
            self._products(), depth="default",
            fetcher=lambda url: calls.append(url) or {"records": []},
            elapsed=amazon.FOREGROUND_CONTRACT,
        )
        assert calls == []


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
        items = [_Item("B1"), _Item("B2")]
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
        other = _Item("B1")
        other.source = "reddit"
        amazon.enrich_source_items([other], depth="default", fetcher=lambda url: {"records": []})
        assert "top_comments" not in other.metadata

    def test_already_enriched_items_are_not_re_pulled(self):
        item = _Item("B1", top_comments=[{"excerpt": "cached", "score": 0, "rating": 5, "date": None}])
        calls = []
        amazon.enrich_source_items(
            [item], depth="default",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []

    def test_quick_depth_touches_nothing(self):
        items = [_Item("B1")]
        calls = []
        amazon.enrich_source_items(
            items, depth="quick",
            fetcher=lambda url: calls.append(url) or {"records": []},
        )
        assert calls == []
        assert "top_comments" not in items[0].metadata


class TestStatsFromItem:
    def test_uses_the_cached_block_when_enrichment_already_ran(self):
        item = _Item("B1", stats={"short_name": "Cached", "drift": "up"})
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

    def test_empty_result_names_the_keyword(self):
        assert self._line([]) == '📦 Amazon: no products matched "bentgo lunch box"'

    def test_no_line_at_all_when_the_source_never_ran(self):
        assert self._line([], keyword="") is None

    def test_quote_lands_only_on_the_sharpest_negative_drift(self):
        items = [
            _FooterItem("Mild", reviews=20, all_time=4.4, recent=4.2, drift="down",
                        quote="minor gripe"),
            _FooterItem("Worst", reviews=20, all_time=4.4, recent=3.0, drift="down",
                        quote="the lid jams"),
        ]
        line = self._line(items)
        assert '"the lid jams"' in line
        assert "minor gripe" not in line

    def test_no_quote_when_the_model_supplied_none(self):
        items = [_FooterItem("Sagging", reviews=20, recent=3.8, drift="down")]
        line = self._line(items)
        assert "↓" in line and '"' not in line
