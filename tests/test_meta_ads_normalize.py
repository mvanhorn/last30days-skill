"""Normalization, identity, and ranking behavior for Meta Ad Library items."""

from lib import fusion, normalize, planner, rerank, schema, signals

FROM_DATE = "2026-08-15"
TO_DATE = "2026-09-14"


def raw_ad(**over):
    base = {
        "id": "1000000000000001",
        "title": "Better mornings start here.",
        "text": "Better mornings start here. Use code SUMMER30 on the Brightpan kettle.",
        "url": "https://www.facebook.com/ads/library/?id=1000000000000001",
        "date": "2026-09-01",
        "advertiser": "Brightpan",
        "page_id": "300000000000001",
        "is_active": True,
        "ended_on": None,
        "display_format": "DCO",
        "cta": "Shop now",
        "landing_url": "https://brightpan.example/collections/kettles",
        "placements": ["FACEBOOK", "INSTAGRAM"],
        "promo_code": "SUMMER30",
        "variants": 3,
        "has_video": False,
        "transcript": "",
    }
    base.update(over)
    return base


def normalize_one(**over):
    items = normalize.normalize_source_items(
        "meta_ads", [raw_ad(**over)], FROM_DATE, TO_DATE
    )
    assert len(items) == 1
    return items[0]


class TestNormalizer:
    def test_source_is_registered(self):
        # A missing dispatch entry raises "Unsupported source" at runtime.
        assert normalize.normalize_source_items("meta_ads", [], FROM_DATE, TO_DATE) == []

    def test_identity_is_the_ad_library_permalink(self):
        item = normalize_one()
        assert item.url.endswith("id=1000000000000001")
        assert item.metadata["landing_url"] == "https://brightpan.example/collections/kettles"

    def test_launch_date_is_the_published_date(self):
        assert normalize_one().published_at == "2026-09-01"

    def test_creative_fields_ride_in_metadata(self):
        meta = normalize_one().metadata
        assert meta["advertiser"] == "Brightpan"
        assert meta["cta"] == "Shop now"
        assert meta["promo_code"] == "SUMMER30"
        assert meta["display_format"] == "DCO"
        assert meta["placements"] == ["FACEBOOK", "INSTAGRAM"]
        assert meta["variants"] == 3

    def test_variant_count_is_the_engagement_proxy(self):
        assert normalize_one().engagement == {"variants": 3}

    def test_transcript_rides_on_the_item(self):
        item = normalize_one(has_video=True, transcript="Stop making boring drinks.")
        assert item.metadata["transcript_snippet"].startswith("Stop making")
        assert "Stop making boring drinks." in item.snippet

    def test_items_are_grounding_exempt(self):
        assert normalize_one().metadata["grounding_exempt"] is True

    def test_pre_window_creative_is_filtered_out(self):
        # The adapter already classifies, but the shared window filter is the
        # backstop and must agree with it rather than drop everything.
        items = normalize.normalize_source_items(
            "meta_ads", [raw_ad(date="2026-04-03")], FROM_DATE, TO_DATE
        )
        assert items == []

    def test_missing_advertiser_still_yields_a_reason(self):
        assert normalize_one(advertiser="").why_relevant == "Active paid creative"


class TestIdentityThroughFusion:
    def test_creatives_sharing_a_landing_page_stay_distinct(self):
        # Ten creatives for one product commonly share a landing URL. Keying
        # identity on it would collapse the campaign into a single candidate.
        first = normalize_one(id="1", url="https://www.facebook.com/ads/library/?id=1")
        second = normalize_one(id="2", url="https://www.facebook.com/ads/library/?id=2")
        assert fusion.candidate_key(first) != fusion.candidate_key(second)

    def test_same_creative_from_two_streams_shares_one_key(self):
        one = normalize_one()
        two = normalize_one(variants=4)
        assert fusion.candidate_key(one) == fusion.candidate_key(two)


class TestRankingRegistries:
    def test_grounding_exemption_skips_the_entity_miss_demotion(self):
        # Ad copy that never names the brand is the normal case, so the
        # entity-miss demotion must not reach these items.
        item = normalize_one(text="Stop making boring drinks.", title="Boring drinks")
        candidate = schema.Candidate(
            candidate_id="c1",
            item_id=item.item_id,
            source="meta_ads",
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            subquery_labels=[],
            native_ranks={},
            local_relevance=0.1,
            freshness=1,
            engagement=None,
            source_quality=signals.source_quality("meta_ads"),
            rrf_score=0.0,
            source_items=[item],
        )
        assert rerank._is_grounding_exempt(candidate) is True

    def test_source_quality_is_registered(self):
        assert signals.source_quality("meta_ads") == 0.72

    def test_engagement_weights_use_variants(self):
        assert signals.ENGAGEMENT_WEIGHTS["meta_ads"] == [("variants", 1.0)]

    def test_planner_capabilities_are_pinned(self):
        assert planner.SOURCE_CAPABILITIES["meta_ads"] == {
            "reference",
            "company_signal",
            "product_signal",
        }
