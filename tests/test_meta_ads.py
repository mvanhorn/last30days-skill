"""Tests for the Meta Ad Library source.

Nothing here spawns a subprocess or touches the network. Fixture shapes mirror
live ScrapeCreators payloads captured 2026-09-14 (envelope keys, snapshot
fields, the searchResults/results asymmetry, null collation ids); brand names
are invented.
"""

import datetime
from unittest import mock

import pytest

from lib import meta_ads
from lib.meta_ads import (
    NO_CANDIDATES,
    RESOLVED,
    UNRESOLVED,
    build_item,
    dedupe_key,
    extract_promo_code,
    has_video,
    launch_date,
    names_match,
    resolve_page,
    search_meta_ads,
)

FROM_DATE = "2026-08-15"
TO_DATE = "2026-09-14"
TOKEN = "fake-sc-key"


def ad_row(**over):
    """One ad row in the live shape (trimmed of media blobs)."""
    base = {
        "ad_archive_id": "1000000000000001",
        "collation_id": "col-1",
        "collation_count": 3,
        "page_id": "300000000000001",
        "page_name": "Brightpan",
        "is_active": True,
        "start_date_string": "2026-09-01T07:00:00.000Z",
        "end_date_string": "2026-09-14T07:00:00.000Z",
        "publisher_platform": ["FACEBOOK", "INSTAGRAM", "THREADS"],
        "url": "https://www.facebook.com/ads/library/?id=1000000000000001",
        "reach_estimate": None,
        "spend": None,
        "snapshot": {
            "body": {"text": "Better mornings start here. Shop the Brightpan kettle."},
            "title": "",
            "cta_text": "Shop now",
            "display_format": "DCO",
            "link_url": "https://brightpan.example/collections/kettles",
            "cards": [],
            "videos": [],
        },
    }
    base.update(over)
    return base


def envelope(rows, key="searchResults", total=None, cursor=""):
    return {
        "success": True,
        "credits_remaining": 999,
        "credits_charged": 1,
        key: rows,
        "searchResultsCount": len(rows) if total is None else total,
        "cursor": cursor,
    }


def company_row(page_id, name, likes=100):
    return {
        "page_id": page_id,
        "name": name,
        "likes": likes,
        "category": "Appliances",
        "verification": "NOT_VERIFIED",
        "ig_username": "",
    }


class TestNameMatching:
    def test_exact_brand_matches(self):
        assert names_match("Brightpan", "Brightpan")

    def test_product_page_matches_umbrella_topic_by_shared_token(self):
        # The umbrella brand advertises under product-line pages; containment
        # in either direction is what resolves them.
        assert names_match("BrightpanCo", "Brightpan Kitchen")

    def test_short_token_never_matches(self):
        # "ai" is below the four-character floor, so an unrelated advertiser
        # that shares only that word must not match.
        assert not names_match("Vantage AI", "Jasper AI")

    def test_no_synonym_expansion(self):
        # The shared scoring tokenizer expands "ai" to artificial/intelligence.
        # Matching must not, or this pair would resolve as the same brand.
        assert not names_match("Vantage AI", "Artificial Intelligence Labs")

    def test_unrelated_names_do_not_match(self):
        assert not names_match("Brightpan", "Coastal Realty Group")

    def test_case_and_spacing_are_normalized(self):
        assert names_match("Bright iQ", "brightiq")


class TestResolvePage:
    def test_umbrella_brand_resolves_dominant_product_page(self):
        rows = (
            [ad_row(page_id="1", page_name="Brightpan Kitchen") for _ in range(14)]
            + [ad_row(page_id="2", page_name="Brightpan Beauty") for _ in range(5)]
            + [ad_row(page_id="3", page_name="Brightpan Home") for _ in range(4)]
            + [ad_row(page_id="9", page_name="Unrelated Deals Co") for _ in range(20)]
        )
        page, runner_ups, top = resolve_page("BrightpanCo", rows)
        assert page["name"] == "Brightpan Kitchen"
        assert page["id"] == "1"
        assert runner_ups == ["Brightpan Beauty", "Brightpan Home"]
        assert top == ""

    def test_exact_name_beats_busier_partial_match(self):
        # A reseller running more ads than the brand must not claim the topic.
        rows = [ad_row(page_id="1", page_name="Brightpan") for _ in range(11)] + [
            ad_row(page_id="2", page_name="Brightpan Outlet") for _ in range(18)
        ]
        page, _runner_ups, _top = resolve_page("Brightpan", rows)
        assert page["name"] == "Brightpan"
        assert page["id"] == "1"

    def test_no_match_returns_top_unmatched_candidate(self):
        rows = [ad_row(page_id="9", page_name="Jasper AI") for _ in range(30)]
        page, runner_ups, top = resolve_page("Vantage AI", rows)
        assert page is None
        assert runner_ups == []
        assert top == "Jasper AI"

    def test_no_rows_returns_no_candidate_name(self):
        page, runner_ups, top = resolve_page("Brightpan", [])
        assert page is None
        assert runner_ups == []
        assert top == ""

    def test_rows_without_page_id_are_ignored(self):
        page, _runner_ups, top = resolve_page(
            "Brightpan", [ad_row(page_id="", page_name="Brightpan")]
        )
        assert page is None
        assert top == ""


class TestRowFields:
    def test_launch_date_from_iso_string(self):
        assert launch_date(ad_row()) == "2026-09-01"

    def test_launch_date_falls_back_to_epoch(self):
        epoch = int(
            datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp()
        )
        row = ad_row(start_date_string="", start_date=epoch)
        assert launch_date(row) == "2026-09-01"

    def test_launch_date_missing_is_none(self):
        assert launch_date(ad_row(start_date_string="", start_date=None)) is None

    def test_dedupe_key_prefers_collation(self):
        assert dedupe_key(ad_row(collation_id="col-9")) == "collation:col-9"

    def test_dedupe_key_falls_back_to_archive_id(self):
        row = ad_row(collation_id=None, ad_archive_id="777")
        assert dedupe_key(row) == "ad:777"

    def test_has_video_detects_snapshot_videos(self):
        row = ad_row()
        row["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/v.mp4"}]
        assert has_video(row)

    def test_has_video_detects_card_video(self):
        row = ad_row()
        row["snapshot"]["cards"] = [{"video_sd_url": "https://cdn.example/v.mp4"}]
        assert has_video(row)

    def test_has_video_false_for_image_ad(self):
        assert not has_video(ad_row())

    def test_promo_code_found_after_code_keyword(self):
        assert extract_promo_code("Take 30% off sitewide. Use code SUMMER30.") == "SUMMER30"

    def test_promo_code_ignores_model_numbers(self):
        assert extract_promo_code("The Brightpan E-325 is here.") is None

    def test_promo_code_ignores_lowercase_words_after_code(self):
        assert extract_promo_code("Our code of conduct is published.") is None

    def test_build_item_carries_every_rendered_field(self):
        item = build_item(ad_row(), {"id": "300000000000001", "name": "Brightpan"})
        assert item["title"].startswith("Better mornings")
        assert item["date"] == "2026-09-01"
        assert item["cta"] == "Shop now"
        assert item["display_format"] == "DCO"
        assert item["landing_url"] == "https://brightpan.example/collections/kettles"
        assert item["placements"] == ["FACEBOOK", "INSTAGRAM", "THREADS"]
        assert item["variants"] == 3
        assert item["advertiser"] == "Brightpan"
        assert item["url"].endswith("id=1000000000000001")

    def test_build_item_uses_card_link_when_snapshot_link_absent(self):
        row = ad_row()
        row["snapshot"]["link_url"] = ""
        row["snapshot"]["cards"] = [{"link_url": "https://brightpan.example/p/1"}]
        item = build_item(row, {"id": "1", "name": "Brightpan"})
        assert item["landing_url"] == "https://brightpan.example/p/1"

    def test_build_item_synthesizes_permalink_when_url_missing(self):
        item = build_item(ad_row(url=""), {"id": "1", "name": "Brightpan"})
        assert item["url"] == "https://www.facebook.com/ads/library/?id=1000000000000001"


class TestSearchMetaAds:
    def _run(self, responses, **kwargs):
        """Drive the lane with a scripted sequence of http.get returns."""
        calls = []

        def fake_get(url, **call_kwargs):
            calls.append((url, call_kwargs.get("params") or {}))
            nxt = responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        with mock.patch("lib.meta_ads.http.get", side_effect=fake_get):
            result = search_meta_ads(
                kwargs.pop("topic", "Brightpan"),
                FROM_DATE,
                TO_DATE,
                token=kwargs.pop("token", TOKEN),
                **kwargs,
            )
        return result, calls

    def test_missing_token_makes_no_calls(self):
        with mock.patch("lib.meta_ads.http.get") as get:
            result = search_meta_ads("Brightpan", FROM_DATE, TO_DATE, token="")
        get.assert_not_called()
        assert result["ads"] == []
        assert result["page"] is None

    def test_happy_path_resolves_and_classifies(self):
        discovery = envelope([ad_row() for _ in range(11)])
        window = envelope(
            [
                ad_row(ad_archive_id="1", collation_id="a", start_date_string="2026-09-01"),
                ad_row(ad_archive_id="2", collation_id="b", start_date_string="2026-08-20"),
                ad_row(ad_archive_id="3", collation_id="c", start_date_string="2026-04-03"),
            ],
            key="results",
        )
        result, calls = self._run([discovery, window])
        assert result["page"]["name"] == "Brightpan"
        assert [item["date"] for item in result["ads"]] == ["2026-09-01", "2026-08-20"]
        tally = result["tally"]
        assert tally["resolution"] == RESOLVED
        assert tally["launched_in_window"] == 2
        assert tally["still_running"] == 1
        assert tally["promo_codes"] == []
        assert tally["placements"] == ["FACEBOOK", "INSTAGRAM", "THREADS"]
        assert calls[0][0] == meta_ads.SEARCH_ADS_URL
        assert calls[1][0] == meta_ads.COMPANY_ADS_URL

    def test_window_fetch_requests_all_statuses(self):
        # Without this the endpoint's ACTIVE default hides creatives that
        # launched inside the window and already ended.
        result, calls = self._run(
            [envelope([ad_row()]), envelope([ad_row()], key="results")]
        )
        assert calls[1][1]["status"] == meta_ads.ENRICHMENT_STATUS
        assert calls[1][1]["start_date"] == FROM_DATE
        assert calls[1][1]["end_date"] == TO_DATE
        assert result["tally"]["launched_in_window"] == 1

    def test_ended_in_window_creative_is_kept_and_flagged(self):
        window = envelope(
            [ad_row(is_active=False, end_date_string="2026-09-05T07:00:00.000Z")],
            key="results",
        )
        result, _calls = self._run([envelope([ad_row()]), window])
        assert len(result["ads"]) == 1
        assert result["ads"][0]["is_active"] is False
        assert result["ads"][0]["ended_on"] == "2026-09-05"

    def test_company_search_fallback_resolves(self):
        discovery = envelope([ad_row(page_id="9", page_name="Unrelated Deals Co")])
        companies = envelope(
            [company_row("55", "Brightpan"), company_row("56", "Other Co")],
            key="results",
        )
        window = envelope([ad_row()], key="results")
        result, calls = self._run([discovery, companies, window])
        assert result["page"]["id"] == "55"
        assert calls[1][0] == meta_ads.SEARCH_COMPANIES_URL
        assert calls[2][1]["pageId"] == "55"

    def test_unresolved_names_top_candidate_and_skips_enrichment(self):
        discovery = envelope(
            [ad_row(page_id="9", page_name="Jasper AI") for _ in range(30)]
        )
        companies = envelope([company_row("7", "Jasper AI")], key="results")
        result, calls = self._run([discovery, companies], topic="Vantage AI")
        assert result["ads"] == []
        assert result["page"] is None
        assert result["tally"]["resolution"] == UNRESOLVED
        assert result["tally"]["top_candidate"] == "Jasper AI"
        assert all(call[0] != meta_ads.COMPANY_ADS_URL for call in calls)

    def test_zero_candidates_is_its_own_state(self):
        result, calls = self._run(
            [envelope([]), envelope([], key="results")], topic="Nonexistent Brand"
        )
        assert result["tally"]["resolution"] == NO_CANDIDATES
        assert result["tally"]["top_candidate"] == ""
        assert len(calls) == 2

    def test_page_override_skips_resolution(self):
        window = envelope([ad_row()], key="results")
        result, calls = self._run([window], page_override="300000000000001")
        assert len(calls) == 1
        assert calls[0][0] == meta_ads.COMPANY_ADS_URL
        assert result["page"]["id"] == "300000000000001"

    def test_variants_of_one_creative_collapse(self):
        window = envelope(
            [
                ad_row(ad_archive_id="1", collation_id="same", collation_count=4),
                ad_row(ad_archive_id="2", collation_id="same", collation_count=4),
            ],
            key="results",
        )
        result, _calls = self._run([envelope([ad_row()]), window])
        assert len(result["ads"]) == 1
        assert result["ads"][0]["variants"] == 4

    def test_null_collation_ids_stay_distinct(self):
        window = envelope(
            [
                ad_row(ad_archive_id="1", collation_id=None),
                ad_row(ad_archive_id="2", collation_id=None),
            ],
            key="results",
        )
        result, _calls = self._run([envelope([ad_row()]), window])
        assert len(result["ads"]) == 2

    def test_pagination_stops_at_depth_cap_and_flags_more(self):
        discovery = envelope([ad_row()])
        pages = [
            envelope([ad_row(ad_archive_id=str(i), collation_id=f"c{i}")],
                     key="results", total=222, cursor=f"cur{i}")
            for i in range(5)
        ]
        result, calls = self._run([discovery] + pages, depth="default")
        window_calls = [c for c in calls if c[0] == meta_ads.COMPANY_ADS_URL]
        assert len(window_calls) == 2  # default depth cap
        assert result["tally"]["cursor_remaining"] is True
        assert result["tally"]["endpoint_total"] == 222

    def test_pagination_stops_when_cursor_repeats(self):
        discovery = envelope([ad_row()])
        repeated = envelope(
            [ad_row(ad_archive_id="1", collation_id="c1")], key="results", cursor="same"
        )
        result, calls = self._run([discovery, repeated, repeated, repeated], depth="deep")
        window_calls = [c for c in calls if c[0] == meta_ads.COMPANY_ADS_URL]
        assert len(window_calls) == 2
        assert result["tally"]["cursor_remaining"] is False

    def test_pagination_stops_on_empty_cursor(self):
        discovery = envelope([ad_row()])
        window = envelope([ad_row()], key="results", cursor="")
        _result, calls = self._run([discovery, window], depth="deep")
        assert len([c for c in calls if c[0] == meta_ads.COMPANY_ADS_URL]) == 1

    @pytest.mark.parametrize("status", [401, 402, 403, 429])
    def test_fatal_status_stops_the_lane(self, status):
        err = meta_ads.http.HTTPError(f"boom {status}", status_code=status)
        result, calls = self._run([err])
        assert len(calls) == 1
        assert result["ads"] == []
        assert str(status) in result["error"]

    def test_fatal_status_during_enrichment_reports_error(self):
        err = meta_ads.http.HTTPError("rate limited", status_code=429)
        result, calls = self._run([envelope([ad_row()]), err])
        assert len(calls) == 2
        assert "429" in result["error"]
        assert result["ads"] == []

    def test_empty_success_response_is_not_an_error(self):
        result, _calls = self._run([envelope([]), envelope([], key="results")])
        assert "error" not in result
        assert result["tally"]["resolution"] == NO_CANDIDATES

    def test_transcripts_cover_newest_videos_across_pages(self):
        discovery = envelope([ad_row()])
        older_video = ad_row(
            ad_archive_id="old", collation_id="old", start_date_string="2026-08-20"
        )
        older_video["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/a.mp4"}]
        newer_video = ad_row(
            ad_archive_id="new", collation_id="new", start_date_string="2026-09-10"
        )
        newer_video["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/b.mp4"}]
        page_one = envelope([older_video], key="results", cursor="c1")
        page_two = envelope([newer_video], key="results", cursor="")
        transcript = {"transcript_available": True, "transcript": "Say what you need to say."}
        result, calls = self._run(
            [discovery, page_one, page_two, transcript, transcript], depth="default"
        )
        transcript_calls = [c for c in calls if c[0] == meta_ads.AD_TRANSCRIPT_URL]
        # Newest first: the page-two creative is transcribed before the older one.
        assert transcript_calls[0][1]["id"] == "new"
        assert result["tally"]["transcribed"] == 2
        assert result["ads"][0]["transcript"].startswith("Say what")

    def test_unavailable_transcript_is_not_an_error(self):
        discovery = envelope([ad_row()])
        video = ad_row()
        video["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/a.mp4"}]
        window = envelope([video], key="results")
        result, _calls = self._run(
            [discovery, window, {"transcript_available": False, "transcript": None}]
        )
        assert "error" not in result
        assert result["tally"]["transcribed"] == 0
        assert result["ads"][0]["transcript"] == ""

    def test_quick_depth_pulls_no_transcripts(self):
        discovery = envelope([ad_row()])
        video = ad_row()
        video["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/a.mp4"}]
        _result, calls = self._run(
            [discovery, envelope([video], key="results")], depth="quick"
        )
        assert all(c[0] != meta_ads.AD_TRANSCRIPT_URL for c in calls)

    def test_rate_limit_retries_are_disabled_on_every_call(self):
        # The shared client retries a 429 twice by default, which would both
        # contradict the no-further-calls contract and burn the lane budget.
        _result, _calls = self._run(
            [envelope([ad_row()]), envelope([ad_row()], key="results")]
        )
        with mock.patch("lib.meta_ads.http.get") as get:
            get.return_value = envelope([])
            search_meta_ads("Brightpan", FROM_DATE, TO_DATE, token=TOKEN)
        assert get.call_args.kwargs["max_429_retries"] == 0
        assert get.call_args.kwargs["deadline_monotonic"] is not None

    def test_exhausted_budget_keeps_what_was_fetched_and_reports_partial(self):
        # Running out of time must not discard creatives already in hand: thin
        # coverage caused by our own clock would otherwise read as a finding
        # about how little the advertiser is running.
        discovery = envelope([ad_row()])
        window = envelope([ad_row()], key="results", cursor="more")
        ticks = {"n": 0}
        real_monotonic = meta_ads.time.monotonic

        def creeping_clock():
            ticks["n"] += 1
            # Jump past the lane budget once the first window page is in.
            return real_monotonic() + (0 if ticks["n"] < 6 else 10_000)

        with mock.patch("lib.meta_ads.time.monotonic", side_effect=creeping_clock):
            with mock.patch("lib.meta_ads.http.get", side_effect=[discovery, window]):
                result = search_meta_ads(
                    "Brightpan", FROM_DATE, TO_DATE, token=TOKEN, depth="deep"
                )
        assert result.get("partial") is True
        assert "budget" in result["error"]
        assert len(result["ads"]) == 1
        assert result["page"]["name"] == "Brightpan"
        assert result["tally"]["cursor_remaining"] is True

    def test_country_override_is_passed_through(self):
        _result, calls = self._run(
            [envelope([ad_row()]), envelope([ad_row()], key="results")], country="GB"
        )
        assert calls[0][1]["country"] == "GB"
        assert calls[1][1]["country"] == "GB"


class TestWindowBoundaries:
    def test_creative_launched_on_window_edges_is_included(self):
        window = envelope(
            [
                ad_row(ad_archive_id="1", collation_id="a", start_date_string=FROM_DATE),
                ad_row(ad_archive_id="2", collation_id="b", start_date_string=TO_DATE),
            ],
            key="results",
        )
        with mock.patch(
            "lib.meta_ads.http.get", side_effect=[envelope([ad_row()]), window]
        ):
            result = search_meta_ads("Brightpan", FROM_DATE, TO_DATE, token=TOKEN)
        assert result["tally"]["launched_in_window"] == 2

    def test_undated_creative_counts_as_still_running(self):
        window = envelope(
            [ad_row(start_date_string="", start_date=None)], key="results"
        )
        with mock.patch(
            "lib.meta_ads.http.get", side_effect=[envelope([ad_row()]), window]
        ):
            result = search_meta_ads("Brightpan", FROM_DATE, TO_DATE, token=TOKEN)
        assert result["ads"] == []
        assert result["tally"]["still_running"] == 1


class TestTransientVersusFatal:
    """R11: only credential/account statuses discard the lane's work."""

    def _drive(self, responses, depth="default"):
        def fake_get(url, **kwargs):
            nxt = responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        with mock.patch("lib.meta_ads.http.get", side_effect=fake_get):
            return search_meta_ads(
                "Brightpan", FROM_DATE, TO_DATE, token=TOKEN, depth=depth
            )

    def test_transient_error_mid_pagination_keeps_fetched_creatives(self):
        # A 500 on page two must not throw away page one: those creatives were
        # already paid for and are real evidence.
        page_one = envelope([ad_row()], key="results", cursor="more")
        boom = meta_ads.http.HTTPError("upstream hiccup", status_code=500)
        result = self._drive([envelope([ad_row()]), page_one, boom], depth="deep")
        assert len(result["ads"]) == 1
        assert result["page"]["name"] == "Brightpan"
        assert result.get("partial") is True
        assert "500" in result["error"]

    def test_transient_error_during_transcripts_keeps_creatives(self):
        video = ad_row()
        video["snapshot"]["videos"] = [{"video_hd_url": "https://cdn.example/a.mp4"}]
        boom = meta_ads.http.HTTPError("transcoder down", status_code=503)
        result = self._drive(
            [envelope([ad_row()]), envelope([video], key="results"), boom]
        )
        assert len(result["ads"]) == 1
        assert result.get("partial") is True

    def test_fatal_status_still_discards(self):
        page_one = envelope([ad_row()], key="results", cursor="more")
        boom = meta_ads.http.HTTPError("rate limited", status_code=429)
        result = self._drive([envelope([ad_row()]), page_one, boom], depth="deep")
        assert result["ads"] == []
        assert result["page"] is None
        assert "429" in result["error"]
