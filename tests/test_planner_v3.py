import unittest

from lib import fusion, planner


class PlannerV3Tests(unittest.TestCase):
    def test_external_plan_rejects_valid_json_with_wrong_structure(self):
        with self.assertRaisesRegex(ValueError, "intent"):
            planner.validate_external_plan({"queries": {"web": ["Berlin"]}})

    def test_external_plan_accepts_documented_shape_without_source_weights(self):
        planner.validate_external_plan(
            {
                "intent": "breaking_news",
                "freshness_mode": "strict_recent",
                "cluster_mode": "story",
                "subqueries": [
                    {
                        "label": "primary",
                        "search_query": "kanye west",
                        "ranking_query": "What happened with Kanye West?",
                        "sources": ["reddit", "x"],
                        "weight": 1.0,
                    }
                ],
            }
        )

    def test_external_plan_rejects_non_numeric_weight(self):
        base_plan = {
            "intent": "breaking_news",
            "freshness_mode": "strict_recent",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "kanye west",
                    "ranking_query": "What happened with Kanye West?",
                    "sources": ["reddit", "x"],
                    "weight": 1.0,
                }
            ],
        }
        for invalid_weight in ("heavy", True):
            with self.subTest(subquery_weight=invalid_weight):
                invalid_plan = dict(base_plan)
                invalid_plan["subqueries"] = [
                    dict(base_plan["subqueries"][0], weight=invalid_weight)
                ]
                with self.assertRaisesRegex(ValueError, "weight"):
                    planner.validate_external_plan(invalid_plan)

        invalid_source_weight = dict(base_plan, source_weights={"x": True})
        with self.assertRaisesRegex(ValueError, "source_weights"):
            planner.validate_external_plan(invalid_source_weight)

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

    def test_operator_plan_keeps_per_subquery_sources_at_default_and_deep(self):
        # Issue #1073: operator --plan sources must not be replaced with the
        # full available list outside --quick.
        raw = {
            "intent": "opinion",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [{
                "label": "primary",
                "search_query": "late diagnosed autism adults",
                "ranking_query": "late diagnosed autism adults",
                "sources": ["reddit", "x", "youtube"],
                "weight": 1.0,
            }],
        }
        avail = ["reddit", "x", "youtube", "hackernews", "polymarket", "github"]
        for depth in ("default", "deep"):
            with self.subTest(depth=depth):
                plan = planner._sanitize_plan(
                    raw,
                    "late diagnosed autism adults",
                    avail,
                    None,
                    depth,
                    honor_plan_sources=True,
                )
                self.assertEqual(["reddit", "x", "youtube"], plan.subqueries[0].sources)

    def test_llm_plan_still_expands_narrow_sources_at_default_and_deep(self):
        # Engine-internal LLM plans keep the expansion that lets fusion
        # decide quality. Same snippet as #1073, without honor_plan_sources.
        raw = {
            "intent": "opinion",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "debate",
            "subqueries": [{
                "label": "primary",
                "search_query": "late diagnosed autism adults",
                "ranking_query": "late diagnosed autism adults",
                "sources": ["reddit", "x", "youtube"],
                "weight": 1.0,
            }],
        }
        avail = ["reddit", "x", "youtube", "hackernews", "polymarket", "github"]
        for depth in ("default", "deep"):
            with self.subTest(depth=depth):
                plan = planner._sanitize_plan(
                    raw,
                    "late diagnosed autism adults",
                    avail,
                    None,
                    depth,
                )
                self.assertEqual(avail, plan.subqueries[0].sources)

    def test_operator_plan_preserves_distinct_per_subquery_source_lists(self):
        raw = {
            "intent": "breaking_news",
            "freshness_mode": "strict_recent",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "kanye west",
                    "ranking_query": "What happened with Kanye West?",
                    "sources": ["reddit", "x", "youtube"],
                    "weight": 1.0,
                },
                {
                    "label": "album",
                    "search_query": "kanye west bully album",
                    "ranking_query": "How was Kanye West's BULLY album received?",
                    "sources": ["youtube", "reddit"],
                    "weight": 0.8,
                },
            ],
        }
        avail = ["reddit", "x", "youtube", "hackernews", "polymarket", "github"]
        plan = planner._sanitize_plan(
            raw,
            "Kanye West",
            avail,
            None,
            "default",
            honor_plan_sources=True,
        )
        by_label = {sq.label: sq.sources for sq in plan.subqueries}
        self.assertEqual(["reddit", "x", "youtube"], by_label["primary"])
        self.assertEqual(["youtube", "reddit"], by_label["album"])

    def test_quick_mode_prioritizes_explicit_requested_sources_within_cap(self):
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
            ["digg", "reddit", "youtube", "grounding"],
            "quick",
        )
        self.assertIn("digg", plan.subqueries[0].sources)
        self.assertLessEqual(len(plan.subqueries[0].sources), 2)

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

    def test_product_plan_can_include_jobs_source(self):
        plan = planner.plan_query(
            topic="Listen Labs features",
            available_sources=["reddit", "youtube", "jobs", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("product", plan.intent)
        self.assertIn("jobs", plan.subqueries[0].sources)
        self.assertGreater(plan.source_weights["jobs"], plan.source_weights["youtube"])

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

    def test_explicit_requested_sources_survive_intent_routing(self):
        """--search sources are a user override at default/deep depth too."""
        for topic, requested, expected_intent in [
            ("will the fed cut rates in december", ["hackernews", "polymarket"], "concept"),
            ("how to deploy on Fly.io", ["reddit", "youtube", "polymarket", "stocktwits"], "how_to"),
        ]:
            for depth in ("default", "deep"):
                with self.subTest(topic=topic, depth=depth):
                    plan = planner.plan_query(
                        topic=topic,
                        available_sources=requested,
                        requested_sources=requested,
                        depth=depth,
                        provider=None,
                        model=None,
                    )
                    self.assertEqual(expected_intent, plan.intent)
                    all_sources = set()
                    for subquery in plan.subqueries:
                        all_sources.update(subquery.sources)
                    self.assertEqual(set(requested), all_sources)

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
        from lib import competitors
        self.assertEqual(
            competitors.COMPARISON_ENTITY_MAX + 1,
            planner._max_subqueries("comparison"),
        )

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

    def test_provider_failure_logs_error_and_law7_reminder(self):
        # When a provider is configured, the provider path runs; if it
        # errors, we get the "LLM planning failed" message AND the
        # "No --plan passed" guidance: a failed internal planner degrades
        # the run exactly like having none, and the host may not be
        # detectable (see test_law7_host_plan_gate.py).
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
        self.assertIn("No --plan passed", output)


class EnablementIntentTests(unittest.TestCase):
    """Issue Esper-Labs/last30days-com#616: "best Grok AI bot to control my
    computer agent" was planned as a strict-recent comparison about Grok, so
    retrieval never looked for the tools that give Grok that capability."""

    TOPIC = "best Grok AI bot to control my computer agent"
    SOURCES = ["reddit", "x", "youtube", "hackernews", "github"]

    def _raw(self, intent="comparison", cluster_mode="debate"):
        return {
            "intent": intent,
            "freshness_mode": "strict_recent",
            "cluster_mode": cluster_mode,
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "Grok computer control",
                    "ranking_query": "How can Grok control my computer?",
                    "sources": ["reddit", "github"],
                }
            ],
        }

    def test_enablement_phrasing_infers_how_to(self):
        for topic in (
            self.TOPIC,
            "best grok bot control my computer agent",
            "get claude to control my browser",
            "how to make chatgpt use my mac",
            "agent that automates the desktop",
            "let codex drive my desktop",
            "get claude code to use my terminal",
        ):
            with self.subTest(topic=topic):
                self.assertEqual("how_to", planner._infer_intent(topic))

    def test_near_misses_keep_their_intent(self):
        self.assertEqual("concept", planner._infer_intent("best wireless earbuds"))
        for topic in (
            "best browser for privacy",
            "make sure I use the right charger",
            "how to get users to use my app",
            "should I let my kid use the phone",
            "make kids use the computer less",
            "AI drives the PC upgrade cycle",
        ):
            with self.subTest(topic=topic):
                self.assertFalse(planner._is_enablement(topic))
        self.assertEqual("comparison", planner._infer_intent("Grok vs Claude to control my computer"))

    def test_ordinary_device_use_is_not_enablement(self):
        for topic in (
            "how to use my computer for video editing",
            "how to use the browser devtools",
            "Chrome uses the browser sandbox",
            "teens use the phone too much",
        ):
            with self.subTest(topic=topic):
                self.assertFalse(planner._is_enablement(topic))

    def test_news_and_threats_are_not_enablement(self):
        self.assertEqual(
            "breaking_news", planner._infer_intent("latest news about malware controlling my browser")
        )
        for topic in (
            "latest news about malware controlling my browser",
            "is a hacker controlling my computer",
            "someone is controlling my laptop remotely",
            "Anthropic launched an agent that controls your computer",
        ):
            with self.subTest(topic=topic):
                self.assertFalse(planner._is_enablement(topic))

    def test_news_and_threat_words_outside_their_role_keep_enablement(self):
        for topic in (
            "get the latest Claude to control my browser",
            "how to get the newly released Claude to use my mac",
            "get claude to control my browser without malware",
            "best AI agent to control my computer safely from hackers",
        ):
            with self.subTest(topic=topic):
                self.assertEqual("how_to", planner._infer_intent(topic))

    def test_llm_news_verdict_is_not_overridden(self):
        topic = "latest news about malware controlling my browser"
        plan = planner._sanitize_plan(
            self._raw(intent="breaking_news", cluster_mode="story"), topic, self.SOURCES, None, "default"
        )
        self.assertEqual("breaking_news", plan.intent)
        self.assertEqual("strict_recent", plan.freshness_mode)
        self.assertEqual("story", plan.cluster_mode)

    def test_llm_comparison_verdict_becomes_how_to(self):
        plan = planner._sanitize_plan(self._raw(), self.TOPIC, self.SOURCES, None, "default")
        self.assertEqual("how_to", plan.intent)
        self.assertEqual("evergreen_ok", plan.freshness_mode)
        self.assertEqual("workflow", plan.cluster_mode)

    def test_explicit_plan_keeps_its_intent(self):
        plan = planner._sanitize_plan(
            self._raw(), self.TOPIC, self.SOURCES, None, "default", honor_plan_sources=True
        )
        self.assertEqual("comparison", plan.intent)
        self.assertEqual("debate", plan.cluster_mode)

    def test_explicit_comparison_is_not_overridden(self):
        topic = "Grok vs Claude to control my computer"
        plan = planner._sanitize_plan(self._raw(), topic, self.SOURCES, None, "default")
        self.assertEqual("comparison", plan.intent)

    def test_ordinary_topic_keeps_llm_intent(self):
        plan = planner._sanitize_plan(
            self._raw(intent="product", cluster_mode="none"), "best wireless earbuds", self.SOURCES, None, "default"
        )
        self.assertEqual("product", plan.intent)
        self.assertEqual("none", plan.cluster_mode)

    @staticmethod
    def _searches(plan):
        return [sq.search_query for sq in plan.subqueries]

    def test_fallback_plan_searches_the_capability_without_the_host(self):
        # The LLM planner can be down; the fallback still has to find general
        # tools such as MCP servers that no post names beside the host.
        plan = planner._fallback_plan(self.TOPIC, self.SOURCES, None, "default")
        self.assertEqual("how_to", plan.intent)
        self.assertEqual(
            ["computer use MCP server", "desktop automation CLI for AI agents"],
            self._searches(plan)[1:],
        )

    def test_fallback_capability_searches_follow_the_device(self):
        plan = planner._fallback_plan("get claude to control my browser", self.SOURCES, None, "default")
        self.assertIn("browser automation MCP server", self._searches(plan))

    def test_fallback_adds_no_capability_search_to_other_topics(self):
        for topic in ("best wireless earbuds", "how to use my computer for video editing"):
            with self.subTest(topic=topic):
                plan = planner._fallback_plan(topic, self.SOURCES, None, "default")
                self.assertFalse(any("MCP" in search for search in self._searches(plan)))

    def test_quick_fallback_keeps_one_search(self):
        self.assertEqual(1, len(planner._fallback_plan(self.TOPIC, self.SOURCES, None, "quick").subqueries))

    def test_llm_plan_without_a_capability_search_gets_one(self):
        plan = planner._sanitize_plan(self._raw(), self.TOPIC, self.SOURCES, None, "default")
        self.assertEqual(["Grok computer control", "computer use MCP server"], self._searches(plan))

    def test_llm_plan_with_a_capability_search_is_left_alone(self):
        raw = self._raw()
        raw["subqueries"].append(
            {"label": "tools", "search_query": "browser automation CLI agent", "ranking_query": "Which CLIs?"}
        )
        plan = planner._sanitize_plan(raw, self.TOPIC, self.SOURCES, None, "default")
        self.assertEqual(["Grok computer control", "browser automation CLI agent"], self._searches(plan))

    def test_llm_capability_search_naming_the_host_still_gets_a_host_free_one(self):
        raw = self._raw()
        raw["subqueries"][0]["search_query"] = "Grok MCP computer control"
        plan = planner._sanitize_plan(raw, self.TOPIC, self.SOURCES, None, "default")
        self.assertEqual(["Grok MCP computer control", "computer use MCP server"], self._searches(plan))
        self.assertTrue(fusion.capability_labels(plan))

    def test_explicit_plan_gets_no_capability_search(self):
        plan = planner._sanitize_plan(
            self._raw(), self.TOPIC, self.SOURCES, None, "default", honor_plan_sources=True
        )
        self.assertEqual(["Grok computer control"], self._searches(plan))


if __name__ == "__main__":
    unittest.main()
