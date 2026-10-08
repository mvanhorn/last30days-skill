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
        assert last30days.parse_meta_ads_page("123456789012345") == "123456789012345"

    def test_ad_library_url(self):
        url = (
            "https://www.facebook.com/ads/library/"
            "?active_status=all&view_all_page_id=123456789012345"
        )
        assert last30days.parse_meta_ads_page(url) == "123456789012345"

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


class TestRetrievalBranch:
    """The dispatch branch itself: brand resolution, config threading, outcome."""

    def _run(self, result, config=None, raw_topic="Brightpan", search_query="kettle reviews"):
        from lib import schema

        subquery = schema.SubQuery(
            label="primary",
            search_query=search_query,
            ranking_query="q",
            sources=["meta_ads"],
        )
        cfg = {"SCRAPECREATORS_API_KEY": "fake-key"}
        cfg.update(config or {})
        captured = {}

        def fake_search(topic, from_date, to_date, **kwargs):
            captured["topic"] = topic
            captured["from_date"] = from_date
            captured["to_date"] = to_date
            captured.update(kwargs)
            return result

        with patch.object(pipeline.meta_ads, "search_meta_ads", side_effect=fake_search):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Brightpan",
                subquery=subquery,
                source="meta_ads",
                config=cfg,
                depth="default",
                date_range=("2026-08-15", "2026-09-14"),
                runtime=schema.ProviderRuntime(
                    reasoning_provider="none",
                    planner_model="none",
                    rerank_model="none",
                ),
                mock=False,
                raw_topic=raw_topic,
            )
        return items, artifact or {}, captured

    def _result(self, **over):
        base = {
            "ads": [{"id": "1"}],
            "page": {"id": "300", "name": "Brightpan"},
            "tally": {"launched_in_window": 1, "resolution": "resolved"},
        }
        base.update(over)
        return base

    def test_advertiser_resolves_from_the_research_topic_not_the_subquery(self):
        # A subquery like "kettle reviews" would resolve a different company
        # than the brand the run is actually about.
        _items, _artifact, captured = self._run(self._result())
        assert captured["topic"] == "Brightpan"

    def test_config_is_threaded_into_the_lane(self):
        _items, _artifact, captured = self._run(
            self._result(),
            config={
                "LAST30DAYS_META_ADS_COUNTRY": "GB",
                "_meta_ads_page": "123456789012345",
            },
        )
        assert captured["token"] == "fake-key"
        assert captured["country"] == "GB"
        assert captured["page_override"] == "123456789012345"

    def test_country_defaults_when_unset(self):
        _items, _artifact, captured = self._run(self._result())
        assert captured["country"] == pipeline.meta_ads.DEFAULT_COUNTRY

    def test_footer_inputs_ride_on_the_stream_artifact(self):
        _items, artifact, _captured = self._run(self._result())
        assert artifact["meta_ads_page"]["name"] == "Brightpan"
        assert artifact["meta_ads_tally"]["launched_in_window"] == 1

    def test_partial_result_forces_a_partial_outcome(self):
        from lib import schema

        _items, artifact, _captured = self._run(
            self._result(partial=True, error="lane budget of 120.0s exceeded")
        )
        assert artifact["_source_outcome"]["state"] == schema.PARTIAL
        assert "budget" in artifact["_source_outcome"]["detail"]

    def test_items_come_back_as_the_stream(self):
        items, _artifact, _captured = self._run(self._result())
        assert items == [{"id": "1"}]

    def test_zero_item_run_still_carries_the_advertiser(self):
        _items, artifact, _captured = self._run(
            self._result(ads=[], tally={"launched_in_window": 0, "resolution": "resolved"})
        )
        assert artifact["meta_ads_page"]["name"] == "Brightpan"


class TestCompetitorIsolation:
    def test_page_override_is_dropped_for_competitor_sub_runs(self):
        # Left in place, one brand's advertiser page would be fetched for
        # every peer in a comparison and rendered as that peer's ads.
        import inspect

        import last30days as engine

        source = inspect.getsource(engine)
        assert 'entity_config.pop("_meta_ads_page", None)' in source
