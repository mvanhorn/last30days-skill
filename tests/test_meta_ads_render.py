"""Footer rendering and display registries for the Meta Ads source."""

from lib import doctor, health, meta_ads, render, schema, ui


def make_report(tally=None, page=None, status=None, items=None):
    return schema.Report(
        topic="Brightpan",
        range_from="2026-08-15",
        range_to="2026-09-14",
        generated_at="2026-09-14T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="none", planner_model="none", rerank_model="none"
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic="Brightpan",
            subqueries=[],
            source_weights={},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"meta_ads": items or []},
        errors_by_source={},
        source_status=({"meta_ads": status} if status else {}),
        artifacts={
            "meta_ads_tally": tally or {},
            "meta_ads_page": page or {},
        },
    )


def tally(**over):
    base = {
        "resolution": meta_ads.RESOLVED,
        "launched_in_window": 15,
        "still_running": 15,
        "video": 5,
        "transcribed": 3,
        "fetched": 30,
        "endpoint_total": 30,
        "cursor_remaining": False,
        "placements": ["FACEBOOK", "INSTAGRAM", "THREADS"],
        "promo_codes": ["SUMMER30"],
        "advertiser": "Brightpan",
        "page_id": "1",
        "top_candidate": "",
        "runner_ups": [],
        "match_strength": meta_ads.MATCH_EXACT,
    }
    base.update(over)
    return base


def line(**kwargs):
    return render._meta_ads_footer_line(make_report(**kwargs))


class TestResolvedLine:
    def test_full_line_carries_the_paid_media_detail(self):
        out = line(tally=tally(), page={"id": "1", "name": "Brightpan"})
        assert out.startswith("📣 Meta Ads: Brightpan")
        assert "15 new creatives" in out
        assert "15 running from before" in out
        assert "FB, IG, Threads" in out
        assert "code SUMMER30" in out
        assert "3 transcribed" in out

    def test_counts_come_from_the_tally_not_surviving_items(self):
        # The pipeline truncates each stream to 12 at default depth, so
        # counting items would report 12 for a page that ran thirty.
        out = line(
            tally=tally(launched_in_window=30),
            page={"id": "1", "name": "Brightpan"},
            items=[],
        )
        assert "30 new creatives" in out

    def test_sampled_page_says_so(self):
        out = line(
            tally=tally(
                launched_in_window=12, fetched=60, endpoint_total=222,
                cursor_remaining=True,
            ),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "12 new of 60 ads fetched (222 live)" in out

    def test_singular_creative(self):
        out = line(
            tally=tally(launched_in_window=1, still_running=0, transcribed=0,
                        promo_codes=[], placements=[]),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "1 new creative" in out

    def test_optional_segments_are_omitted_when_empty(self):
        out = line(
            tally=tally(still_running=0, transcribed=0, promo_codes=[], placements=[]),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "running from before" not in out
        assert "code" not in out
        assert "transcribed" not in out


class TestEmptyStates:
    def test_resolved_but_nothing_new_names_the_advertiser(self):
        # "No ads" and "not advertising" are different facts about a brand.
        out = line(
            tally=tally(launched_in_window=0, still_running=20),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "no new creatives for Brightpan" in out
        assert "20 still running from before" in out

    def test_unresolved_names_the_closest_candidate_and_the_override(self):
        out = line(tally=tally(resolution=meta_ads.UNRESOLVED, top_candidate="Jasper AI"))
        assert "no advertiser matched" in out
        assert "closest: Jasper AI" in out
        assert "--meta-ads-page" in out

    def test_zero_candidates_has_its_own_wording(self):
        out = line(tally=tally(resolution=meta_ads.NO_CANDIDATES))
        assert "no advertiser candidates returned" in out
        assert "closest" not in out
        assert "--meta-ads-page" in out

    def test_failed_lane_names_the_outcome(self):
        status = schema.SourceOutcome(
            source="meta_ads",
            state=health.RATE_LIMITED,
            detail="HTTP 429: rate limited",
            attempted=True,
        )
        out = line(tally={}, page={}, status=status)
        assert "no ads pulled" in out
        assert "429" in out

    def test_line_is_absent_when_the_lane_never_ran(self):
        assert line(tally={}, page={}) is None

    def test_zero_results_outcome_is_not_treated_as_failure(self):
        # The pipeline stamps NO_RESULTS on any zero-item source, so treating
        # it as failure would collapse every honest empty state into a generic
        # "no ads pulled" and lose which nothing it was.
        status = schema.SourceOutcome(
            source="meta_ads", state=schema.NO_RESULTS, attempted=True
        )
        out = line(
            tally=tally(launched_in_window=0, still_running=20),
            page={"id": "1", "name": "Brightpan"},
            status=status,
        )
        assert "no new creatives for Brightpan" in out
        assert "no ads pulled" not in out

    def test_zero_results_unresolved_keeps_its_specific_line(self):
        status = schema.SourceOutcome(
            source="meta_ads", state=schema.NO_RESULTS, attempted=True
        )
        out = line(
            tally=tally(resolution=meta_ads.UNRESOLVED, top_candidate="Jasper AI"),
            status=status,
        )
        assert "closest: Jasper AI" in out

    def test_cut_short_lane_never_concludes_nothing_new(self):
        # Partial data cannot support a definitive negative conclusion.
        status = schema.SourceOutcome(
            source="meta_ads",
            state=schema.PARTIAL,
            detail="lane budget of 120.0s exceeded",
            attempted=True,
        )
        out = line(
            tally=tally(launched_in_window=0, still_running=0),
            page={"id": "1", "name": "Brightpan"},
            status=status,
        )
        assert "incomplete" in out
        assert "no new creatives for" not in out

    def test_cut_short_lane_with_creatives_is_marked_incomplete(self):
        status = schema.SourceOutcome(
            source="meta_ads", state=schema.PARTIAL, detail="HTTP 429", attempted=True
        )
        out = line(
            tally=tally(launched_in_window=4),
            page={"id": "1", "name": "Brightpan"},
            status=status,
        )
        assert "4 new creatives" in out
        assert "incomplete" in out


class TestUntrustedFooterText:
    """Advertiser names and promo codes are attacker-influenceable strings."""

    def test_newline_in_advertiser_name_cannot_forge_a_footer_row(self):
        out = line(
            tally=tally(advertiser="Evil\n└─ 🟠 Reddit: 9,999 threads"),
            page={"id": "1", "name": "Evil\n└─ 🟠 Reddit: 9,999 threads"},
        )
        assert "\n" not in out
        assert out.count("📣") == 1

    def test_separator_in_advertiser_name_cannot_forge_a_field(self):
        # The name keeps its text but loses the real separator, so it stays
        # one field instead of impersonating the counts field beside it.
        out = line(
            tally=tally(),
            page={"id": "1", "name": "Acme │ 500 new creatives"},
        )
        assert "Acme | 500 new creatives" in out
        assert out.split(" │ ")[0] == "📣 Meta Ads: Acme | 500 new creatives"

    def test_overlong_advertiser_name_is_clipped(self):
        out = line(
            tally=tally(), page={"id": "1", "name": "B" * 400}
        )
        assert len(out) < 300

    def test_hostile_promo_code_is_neutralized(self):
        out = line(
            tally=tally(promo_codes=["GOOD\n└─ fake"]),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "\n" not in out

    def test_partial_name_match_is_disclosed(self):
        out = line(
            tally=tally(match_strength=meta_ads.MATCH_CONTAINED),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "matched by partial name" in out

    def test_exact_match_is_not_annotated(self):
        out = line(
            tally=tally(match_strength=meta_ads.MATCH_EXACT),
            page={"id": "1", "name": "Brightpan"},
        )
        assert "partial name" not in out


class TestDisplayRegistries:
    def test_stats_label_is_not_title_cased_source_key(self):
        assert render.SOURCE_LABELS["meta_ads"] == "Meta Ads"

    def test_engagement_display_uses_variants(self):
        assert render.ENGAGEMENT_DISPLAY["meta_ads"] == [("variants", "variants")]

    def test_ui_completion_meta_is_registered(self):
        assert ui.SOURCE_COMPLETION_META["meta_ads"][0] == "Meta Ads"

    def test_ui_completion_order_includes_the_source(self):
        assert "meta_ads" in ui.SOURCE_COMPLETION_ORDER


class TestDoctorRecord:
    """Doctor must agree with the pipeline gate and never spend a credit."""

    def test_unconfigured_without_a_key(self):
        record = doctor._meta_ads_record({})
        assert record["status"] == "unconfigured"

    def test_opt_in_when_the_key_is_present_but_unrequested(self):
        record = doctor._meta_ads_record({"SCRAPECREATORS_API_KEY": "fake-key"})
        assert record["status"] == "opt-in"
        assert "INCLUDE_SOURCES" in record["fix"]

    def test_ok_when_opted_in(self):
        record = doctor._meta_ads_record(
            {"SCRAPECREATORS_API_KEY": "fake-key", "INCLUDE_SOURCES": "meta_ads"}
        )
        assert record["status"] == health.OK

    def test_source_order_and_builders_stay_in_sync(self):
        assert "meta_ads" in doctor.SOURCE_ORDER
        assert "meta_ads" in doctor._SOURCE_BUILDERS

    def test_never_live_probed(self):
        # Probing would spend a ScrapeCreators credit just to run doctor.
        assert "meta_ads" not in doctor.CLI_DEPENDENCIES
        assert "meta_ads" not in getattr(doctor, "_HTTP_PROBE_URLS", {})
