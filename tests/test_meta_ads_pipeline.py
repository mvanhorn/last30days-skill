"""Pipeline gating, flag handling, and artifact wiring for the Meta Ads source."""

import sys
from unittest.mock import patch

import pytest

import last30days
from lib import env, pipeline


class TestMetaAdsSourceGating:
    """Dual-gated on the Amazon precedent: key present AND the run asks."""

    def _config(self, include=""):
        return {"SCRAPECREATORS_API_KEY": "fake-key", "INCLUDE_SOURCES": include}

    def test_unavailable_without_a_key_even_when_requested(self):
        available = pipeline.available_sources(
            {"INCLUDE_SOURCES": "meta_ads"}, ["meta_ads"]
        )
        assert "meta_ads" not in available

    def test_unavailable_with_a_key_when_not_requested(self):
        # Holding a ScrapeCreators key for TikTok must not start spending
        # credits resolving advertisers.
        assert "meta_ads" not in pipeline.available_sources(self._config(), None)

    def test_available_via_per_run_request(self):
        assert "meta_ads" in pipeline.available_sources(self._config(), ["meta_ads"])

    def test_available_via_durable_include_sources(self):
        assert "meta_ads" in pipeline.available_sources(self._config("meta_ads"), None)

    def test_exclude_sources_wins_over_activation(self):
        config = self._config("meta_ads")
        config["EXCLUDE_SOURCES"] = "meta_ads"
        assert "meta_ads" not in pipeline.available_sources(config, ["meta_ads"])

    def test_never_inferred_from_a_brand_shaped_topic(self):
        # Topic shape must not activate it: keyword ad search on the wrong
        # topic resolves the wrong company at full credit cost.
        assert "meta_ads" not in pipeline.available_sources(self._config(), [])

    def test_capped_at_one_fetch_per_run(self):
        assert pipeline.MAX_SOURCE_FETCHES["meta_ads"] == 1

    def test_exempt_from_thin_source_retry(self):
        # A brand that genuinely ran two creatives is a complete result; a
        # retry would re-resolve the page and re-spend the discovery credit.
        assert "meta_ads" in pipeline.THIN_RETRY_EXEMPT


class TestSearchFlag:
    def test_canonical_token_is_accepted(self):
        assert "meta_ads" in last30days.parse_search_flag("reddit,x,meta_ads")

    @pytest.mark.parametrize("alias", ["meta", "meta-ads"])
    def test_aliases_resolve(self, alias):
        assert "meta_ads" in last30days.parse_search_flag(f"reddit,{alias}")

    def test_source_is_in_the_known_source_registry(self):
        # parse_search_flag rejects any token absent from this list, so a
        # missing entry silently breaks the per-run activation path.
        assert "meta_ads" in pipeline.MOCK_AVAILABLE_SOURCES


class TestPageOverrideParsing:
    def test_bare_numeric_page_id(self):
        assert last30days.parse_meta_ads_page("300411646810133") == "300411646810133"

    def test_ad_library_url(self):
        url = (
            "https://www.facebook.com/ads/library/"
            "?active_status=all&view_all_page_id=300411646810133"
        )
        assert last30days.parse_meta_ads_page(url) == "300411646810133"

    def test_vanity_url_is_rejected(self):
        # A vanity handle is not a page id: one live check resolved a
        # brand-looking handle to a private person's profile.
        assert last30days.parse_meta_ads_page("https://facebook.com/somebrand") == ""

    def test_blank_is_rejected(self):
        assert last30days.parse_meta_ads_page("  ") == ""

    def test_short_numeric_string_is_rejected(self):
        assert last30days.parse_meta_ads_page("42") == ""


class TestEnvContract:
    def test_country_key_is_resolvable(self):
        config = env.get_config()
        assert "LAST30DAYS_META_ADS_COUNTRY" in config

    def test_country_defaults_to_us(self):
        assert env.get_config().get("LAST30DAYS_META_ADS_COUNTRY") == "US"

    def test_no_durable_page_override_key_exists(self):
        # A page id is per-topic state, and env keys ride through the
        # competitor runner's config copy.
        assert "LAST30DAYS_META_ADS_PAGE" not in env.get_config()


class _FakeBundle:
    def __init__(self, artifacts):
        self.artifacts = artifacts


class TestArtifactLift:
    """Stream artifacts only reach the report as anonymous grounding entries."""

    def test_footer_inputs_are_promoted_to_named_artifacts(self):
        bundle = _FakeBundle(
            {
                "grounding": [
                    {"x_receipts": ["unrelated"]},
                    {
                        "meta_ads_page": {"id": "1", "name": "Brightpan"},
                        "meta_ads_tally": {"launched_in_window": 7},
                    },
                ]
            }
        )
        pipeline._lift_stream_artifacts(bundle)
        assert bundle.artifacts["meta_ads_page"]["name"] == "Brightpan"
        assert bundle.artifacts["meta_ads_tally"]["launched_in_window"] == 7

    def test_lift_survives_a_zero_item_run(self):
        # The advertiser must still be nameable when no creative landed.
        bundle = _FakeBundle(
            {"grounding": [{"meta_ads_page": {"id": "1", "name": "Brightpan"}}]}
        )
        pipeline._lift_stream_artifacts(bundle)
        assert bundle.artifacts["meta_ads_page"]["name"] == "Brightpan"

    def test_lift_ignores_non_dict_entries(self):
        bundle = _FakeBundle({"grounding": ["not a dict", None]})
        pipeline._lift_stream_artifacts(bundle)
        assert "meta_ads_page" not in bundle.artifacts

    def test_lift_is_a_no_op_without_grounding_artifacts(self):
        bundle = _FakeBundle({})
        pipeline._lift_stream_artifacts(bundle)
        assert bundle.artifacts == {}
