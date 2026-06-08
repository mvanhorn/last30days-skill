import unittest

from lib import planner


class PlannerV3Tests(unittest.TestCase):
    def test_default_how_to_expands_past_llm_narrow_source_weights(self):
        raw = {
            "intent": "how_to",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "workflow",
            "source_weights": {"hackernews": 0.7, "reddit": 0.3},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "deploy app to Fly.io guide",
                    "ranking_query": "How do I deploy an app to Fly.io?",
                    "sources": ["hackernews"],
                    "weight": 1.0,
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw,
            "how to deploy on Fly.io",
            ["reddit", "x", "youtube", "hackernews"],
            None,
            "default",
        )
        sources = plan.subqueries[0].sources
        # how_to capability routing selects video + discussion
        self.assertIn("reddit", sources)
        self.assertIn("youtube", sources)
        self.assertIn("reddit", plan.source_weights)
        self.assertIn("youtube", plan.source_weights)
        self.assertEqual("evergreen_ok", plan.freshness_mode)

    def test_comparison_uses_deterministic_plan_and_preserves_entities(self):
        plan = planner.plan_query(
            topic="openclaw vs nanoclaw vs ironclaw",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=object(),
            model="ignored",
        )
        self.assertEqual("comparison", plan.intent)
        self.assertEqual(["deterministic-comparison-plan"], plan.notes)
        self.assertEqual(4, len(plan.subqueries))
        joined_queries = "\n".join(subquery.search_query for subquery in plan.subqueries).lower()
        self.assertIn("openclaw", joined_queries)
        self.assertIn("nanoclaw", joined_queries)
        self.assertIn("ironclaw", joined_queries)

    def test_fallback_plan_emits_dual_query_fields(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        self.assertGreaterEqual(len(plan.subqueries), 2)
        for subquery in plan.subqueries:
            self.assertTrue(subquery.search_query)
            self.assertTrue(subquery.ranking_query)

    def test_factual_topic_uses_no_cluster_mode(self):
        plan = planner.plan_query(
            topic="what is the parameter count of claude code",
            available_sources=["reddit", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("factual", plan.intent)
        self.assertEqual("none", plan.cluster_mode)

    def test_quick_mode_collapses_fallback_to_single_subquery(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x"],
            requested_sources=None,
            depth="quick",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        self.assertEqual(1, len(plan.subqueries))
        self.assertEqual(["reddit", "x"], plan.subqueries[0].sources)

    def test_quick_mode_preserves_explicit_requested_sources(self):
        raw = {
            "intent": "product",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "AI coding agents",
                    "ranking_query": "What are people saying about AI coding agents?",
                    "sources": ["reddit", "youtube", "grounding", "digg"],
                    "weight": 1.0,
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw,
            "AI coding agents",
            ["reddit", "youtube", "grounding", "digg"],
            ["reddit", "youtube", "grounding", "digg"],
            "quick",
        )
        self.assertIn("digg", plan.subqueries[0].sources)

    def test_quick_mode_preserves_explicit_requested_sources_in_fallback_plan(self):
        plan = planner.plan_query(
            topic="AI coding agents",
            available_sources=["reddit", "youtube", "github"],
            requested_sources=["reddit", "github"],
            depth="quick",
            provider=None,
            model=None,
        )
        self.assertIn("github", plan.subqueries[0].sources)

    def test_default_comparison_uses_all_capable_sources(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        for subquery in plan.subqueries:
            # Default depth should not artificially cap sources
            self.assertGreaterEqual(len(subquery.sources), 4)

    def test_default_how_to_keeps_youtube_in_source_mix(self):
        plan = planner.plan_query(
            topic="how to deploy remotion animations for claude code",
            available_sources=["reddit", "x", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)

    def test_how_to_sources_includes_capability_matched_extras(self):
        """how_to routing should include additional sources beyond the core ones."""
        plan = planner.plan_query(
            topic="how to deploy on Fly.io",
            available_sources=["reddit", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)
        # Additional capability-matched sources should also be included
        self.assertGreater(len(sources), 2,
                           f"how_to should include >2 sources, got {len(sources)}: {sources}")

    def test_ncaa_tournament_is_breaking_news(self):
        intent = planner._infer_intent("NCAA tournament brackets")
        self.assertEqual("breaking_news", intent)

    def test_march_madness_is_breaking_news(self):
        intent = planner._infer_intent("2026 March Madness")
        self.assertEqual("breaking_news", intent)

    def test_factual_plan_has_at_most_2_subqueries(self):
        plan = planner.plan_query(
            topic="who acquired Wiz",
            available_sources=["reddit", "x", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("factual", plan.intent)
        self.assertLessEqual(len(plan.subqueries), 2)

    def test_default_how_to_prefers_longform_video_over_shortform(self):
        plan = planner.plan_query(
            topic="how to deploy on Fly.io",
            available_sources=["reddit", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        # how_to routing should include youtube (longform) over tiktok/instagram
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)

    def test_prediction_includes_tiktok_and_instagram(self):
        """TikTok and Instagram are no longer excluded from prediction intent."""
        plan = planner.plan_query(
            topic="odds of US recession 2026",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("prediction", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)

    def test_opinion_includes_tiktok_and_instagram(self):
        """TikTok and Instagram are no longer excluded from opinion intent."""
        plan = planner.plan_query(
            topic="thoughts on OpenAI Codex pricing",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("opinion", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)

    def test_comparison_includes_polymarket(self):
        """Polymarket should not be excluded from comparison intent plans."""
        plan = planner.plan_query(
            topic="Sam Altman vs Dario Amodei",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("polymarket", all_sources)

    def test_polymarket_excluded_from_how_to_and_concept(self):
        """Polymarket should remain excluded from how_to and concept intents."""
        for topic, expected_intent in [
            ("how to deploy on Fly.io", "how_to"),
            ("explain transformer architecture", "concept"),
        ]:
            plan = planner.plan_query(
                topic=topic,
                available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
                requested_sources=None,
                depth="default",
                provider=None,
                model=None,
            )
            self.assertEqual(expected_intent, plan.intent)
            all_sources = set()
            for subquery in plan.subqueries:
                all_sources.update(subquery.sources)
            self.assertNotIn("polymarket", all_sources,
                             f"polymarket should be excluded from {expected_intent}")

    def test_opinion_includes_polymarket(self):
        """Polymarket should not be excluded from opinion intent plans."""
        plan = planner.plan_query(
            topic="thoughts on OpenAI future",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("opinion", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("polymarket", all_sources)

    def test_breaking_news_includes_tiktok_and_instagram(self):
        plan = planner.plan_query(
            topic="2026 March Madness",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("breaking_news", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)


class IntentModifierBreadthTests(unittest.TestCase):
    """Unit 2: Topics with intent modifiers (use cases, workflows, examples,
    review, comparison) must fan out across paraphrased subqueries rather
    than echo the literal phrase. 2026-04-19 Hermes Agent Use Cases failure.
    """

    def test_max_subqueries_raised_to_5_for_how_to(self):
        self.assertEqual(5, planner._max_subqueries("how_to"))

    def test_max_subqueries_raised_to_5_for_opinion(self):
        self.assertEqual(5, planner._max_subqueries("opinion"))

    def test_max_subqueries_raised_to_5_for_product(self):
        self.assertEqual(5, planner._max_subqueries("product"))

    def test_max_subqueries_unchanged_for_comparison(self):
        self.assertEqual(4, planner._max_subqueries("comparison"))

    def test_max_subqueries_unchanged_for_factual_and_concept(self):
        self.assertEqual(2, planner._max_subqueries("factual"))
        self.assertEqual(2, planner._max_subqueries("concept"))

    def test_has_intent_modifier_detects_use_cases(self):
        self.assertTrue(planner._has_intent_modifier("Hermes Agent use cases"))
        self.assertTrue(planner._has_intent_modifier("Hermes Agent Actual Use Cases"))

    def test_has_intent_modifier_detects_workflows(self):
        self.assertTrue(planner._has_intent_modifier("Claude Code workflows"))

    def test_has_intent_modifier_detects_review_and_tutorial(self):
        self.assertTrue(planner._has_intent_modifier("Ollama review"))
        self.assertTrue(planner._has_intent_modifier("DSPy tutorial"))

    def test_has_intent_modifier_false_for_bare_entity(self):
        self.assertFalse(planner._has_intent_modifier("Kanye West"))
        self.assertFalse(planner._has_intent_modifier("hermes agent"))

    def test_fallback_fans_out_when_intent_modifier_present(self):
        plan = planner.plan_query(
            topic="Hermes Agent use cases",
            available_sources=["reddit", "x", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        # Expect at least 3 subqueries total (primary + fanout); cap is 5 for
        # how_to/opinion/product/breaking_news. Label set should include at
        # least one of the paraphrase labels.
        labels = {sq.label for sq in plan.subqueries}
        self.assertGreaterEqual(len(plan.subqueries), 3)
        self.assertTrue(
            labels & {"workflows", "production", "experience"},
            f"Expected paraphrase labels in {labels}",
        )

    def test_fallback_does_not_fan_out_for_bare_entity(self):
        plan = planner.plan_query(
            topic="Kanye West",
            available_sources=["reddit", "x", "grounding"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        # Bare entity without intent modifier should not trigger the paraphrase
        # fanout (those labels are not in the plan).
        labels = {sq.label for sq in plan.subqueries}
        self.assertFalse(labels & {"workflows", "production", "experience"})

    def test_prompt_includes_intent_modifier_rule(self):
        prompt = planner._build_prompt(
            topic="Hermes Agent use cases",
            available_sources=["reddit", "x", "youtube"],
            requested_sources=None,
            depth="default",
        )
        self.assertIn("INTENT-MODIFIER HANDLING", prompt)
        self.assertIn("use cases", prompt)
        self.assertIn("STRIP that phrase", prompt)


class FallbackDefaultsTests(unittest.TestCase):
    """Unit 3: Deterministic fallback defaults and keyword_query quoting.
    2026-04-19 Hermes Agent Use Cases failure.
    """

    def test_unclassified_topic_defaults_to_concept_not_breaking_news(self):
        # Prior default was "breaking_news" with strict_recent freshness,
        # which biased against older relevant material on unfamiliar topics.
        self.assertEqual("concept", planner._infer_intent("some unfamiliar topic"))
        self.assertEqual("concept", planner._infer_intent("Hermes Agent"))

    def test_recency_signals_still_break_out_to_breaking_news(self):
        self.assertEqual("breaking_news", planner._infer_intent("trending AI tools"))
        self.assertEqual("breaking_news", planner._infer_intent("what's happening today"))
        self.assertEqual("breaking_news", planner._infer_intent("this week in AI"))

    def test_specific_intents_still_classify_correctly(self):
        # Regression: other regex branches still fire as before.
        self.assertEqual("how_to", planner._infer_intent("how to deploy Docker"))
        self.assertEqual("factual", planner._infer_intent("who acquired Wiz"))
        self.assertEqual("opinion", planner._infer_intent("thoughts on OpenAI Codex"))
        self.assertEqual("comparison", planner._infer_intent("Codex vs Claude Code"))

    def test_keyword_query_quotes_only_title_cased_proper_nouns(self):
        # "Hermes Agent" is a multi-word title-cased proper noun — keep quoted.
        # "Use Cases" is also title-cased BUT we only quote the first 2
        # title-cased compounds; the first extracted is "Hermes Agent".
        search = planner._keyword_query("Hermes Agent use cases", "hermes agent")
        self.assertIn('"Hermes Agent"', search)
        # The old behavior quoted the entire typed topic; confirm it does not.
        self.assertNotIn('"Hermes Agent Actual Use Cases"', search)

    def test_keyword_query_does_not_quote_bare_lowercase_topic(self):
        search = planner._keyword_query("kanye west bully", "kanye west bully")
        # Lowercase topics have no title-cased compound to quote.
        self.assertNotIn('"', search)

    def test_fallback_logs_warning_when_no_provider(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            planner.plan_query(
                topic="Hermes Agent use cases",
                available_sources=["reddit", "x"],
                requested_sources=None,
                depth="default",
                provider=None,
                model=None,
            )
        output = buf.getvalue()
        # New language: "No --plan passed" + "YOU ARE the planner" +
        # runtime enumeration. Unit 4 (2026-04-19) rewrite to stop the
        # "no provider = no LLM = I need a key" misread.
        self.assertIn("No --plan passed", output)
        self.assertIn("YOU ARE the planner", output)
        self.assertIn("you ARE the LLM", output)
        # Runtime-agnostic: each supported runtime name should appear.
        for runtime_name in ("Claude Code", "Codex", "Hermes", "Gemini"):
            self.assertIn(runtime_name, output)
        # The old misleading phrasing must NOT appear.
        self.assertNotIn("No --plan and no LLM provider configured", output)

    def test_fallback_does_not_log_new_warning_when_provider_present(self):
        # When a provider is configured, the provider path runs; if it
        # errors, we get the "LLM planning failed" message, NOT the
        # "No --plan passed" guidance (which is specifically for the
        # no-provider-no-plan caller path).
        import io
        import contextlib
        buf = io.StringIO()

        class _NoopProvider:
            def generate_json(self, model, prompt):
                raise ValueError("force fallback for test")

        with contextlib.redirect_stderr(buf):
            planner.plan_query(
                topic="Kanye West",
                available_sources=["reddit", "x"],
                requested_sources=None,
                depth="default",
                provider=_NoopProvider(),
                model="some-model",
            )
        output = buf.getvalue()
        self.assertIn("LLM planning failed", output)
        self.assertNotIn("No --plan passed", output)

if __name__ == "__main__":
    unittest.main()
