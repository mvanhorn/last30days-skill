"""Routing for vs-mode / --competitors / --competitors-plan (#868).

Plan implies competitor mode; vs-split supplies peers when not in discover-N
mode; entity caps align with COMPETITORS_MAX and warn when truncating.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr

import last30days as cli
from lib import competitors, planner, render


def _parse(*argv: str):
    return cli.build_parser().parse_args(argv)


def _route(topic: str, *argv: str):
    args = _parse(topic, *argv)
    enabled, count, explicit = cli.resolve_competitors_args(args)
    plan = cli.parse_competitors_plan(args.competitors_plan)
    return cli.apply_vs_competitor_routing(
        topic,
        competitors_flag=args.competitors,
        comp_enabled=enabled,
        comp_count=count,
        comp_explicit=explicit,
        comp_plan=plan,
    )


FIVE_WAY = "Weber vs Traeger vs Big Green Egg vs Blackstone vs Napoleon Grills"


class TestCompetitorsPlanImpliesEnabled(unittest.TestCase):
    def test_plan_alone_enables_competitor_mode(self):
        plan = json.dumps({
            "Traeger": {"x_handle": "TraegerGrills"},
            "Napoleon Grills": {"subreddits": ["NapoleonGrill"]},
        })
        args = _parse("Weber grills", "--competitors-plan", plan)
        enabled, count, explicit = cli.resolve_competitors_args(args)
        self.assertTrue(enabled)
        self.assertEqual(explicit, [])  # peers filled by routing from plan keys
        topic, enabled, count, explicit = _route(
            "Weber grills", "--competitors-plan", plan,
        )
        self.assertEqual(topic, "Weber grills")
        self.assertTrue(enabled)
        self.assertEqual(explicit, ["Traeger", "Napoleon Grills"])
        self.assertEqual(count, 2)

    def test_plan_does_not_override_explicit_list(self):
        plan = json.dumps({"Ignored": {}})
        topic, enabled, count, explicit = _route(
            "Weber",
            "--competitors-list", "Traeger,Blackstone",
            "--competitors-plan", plan,
        )
        self.assertEqual(topic, "Weber")
        self.assertTrue(enabled)
        self.assertEqual(explicit, ["Traeger", "Blackstone"])
        self.assertEqual(count, 2)


class TestVsRoutingWithPlan(unittest.TestCase):
    def test_five_way_vs_plus_plan_keeps_all_five(self):
        plan = json.dumps({
            "Traeger": {},
            "Big Green Egg": {},
            "Blackstone": {},
            "Napoleon Grills": {"subreddits": ["NapoleonGrill"]},
        })
        err = io.StringIO()
        with redirect_stderr(err):
            topic, enabled, count, explicit = _route(
                FIVE_WAY, "--competitors-plan", plan,
            )
        self.assertEqual(topic, "Weber")
        self.assertTrue(enabled)
        self.assertEqual(
            explicit,
            ["Traeger", "Big Green Egg", "Blackstone", "Napoleon Grills"],
        )
        self.assertEqual(count, 4)
        self.assertIn("Napoleon Grills", err.getvalue())

    def test_bare_five_way_vs_keeps_all_five(self):
        with redirect_stderr(io.StringIO()):
            topic, enabled, count, explicit = _route(FIVE_WAY)
        self.assertEqual(topic, "Weber")
        self.assertEqual(
            explicit,
            ["Traeger", "Big Green Egg", "Blackstone", "Napoleon Grills"],
        )

    def test_competitors_n_on_vs_topic_skips_vs_split(self):
        """Discover-N mode must not rewrite a vs-string into named peers."""
        topic, enabled, count, explicit = _route(FIVE_WAY, "--competitors", "2")
        self.assertEqual(topic, FIVE_WAY)
        self.assertTrue(enabled)
        self.assertEqual(count, 2)
        self.assertEqual(explicit, [])


class TestEntityCapAlignment(unittest.TestCase):
    def test_comparison_entity_max_matches_competitors_max_plus_main(self):
        self.assertEqual(
            competitors.COMPARISON_ENTITY_MAX,
            competitors.COMPETITORS_MAX + 1,
        )
        self.assertEqual(
            planner._max_subqueries("comparison"),
            competitors.COMPARISON_ENTITY_MAX + 1,  # primary + per-entity
        )

    def test_five_way_not_silently_truncated(self):
        entities = planner._comparison_entities(FIVE_WAY)
        self.assertEqual(
            entities,
            ["Weber", "Traeger", "Big Green Egg", "Blackstone", "Napoleon Grills"],
        )

    def test_fallback_plan_keeps_all_entities_at_ceiling(self):
        topic = "A vs B vs C vs D vs E vs F vs G"
        entities = planner._comparison_entities(topic)
        self.assertEqual(len(entities), competitors.COMPARISON_ENTITY_MAX)
        plan = planner._fallback_plan(topic, ["reddit"], None, "standard")
        entity_labels = [sq.label for sq in plan.subqueries if sq.label.startswith("entity-")]
        self.assertEqual(len(entity_labels), competitors.COMPARISON_ENTITY_MAX)
        self.assertIn("primary", [sq.label for sq in plan.subqueries])

    def test_empty_plan_alone_errors_clearly(self):
        args = _parse("Weber grills", "--competitors-plan", "{}")
        enabled, count, explicit = cli.resolve_competitors_args(args)
        plan = cli.parse_competitors_plan(args.competitors_plan)
        topic, enabled, count, explicit = cli.apply_vs_competitor_routing(
            "Weber grills",
            competitors_flag=args.competitors,
            comp_enabled=enabled,
            comp_count=count,
            comp_explicit=explicit,
            comp_plan=plan,
        )
        self.assertTrue(enabled)
        self.assertEqual(explicit, [])
        # Guard in _main: plan present, no peers, not discover-N
        self.assertIsNone(args.competitors)
        self.assertTrue(args.competitors_plan)
        self.assertFalse(explicit)

    def test_over_max_warns_and_names_dropped(self):
        # main + 6 peers = max; 8th entity dropped with warning
        topic = "A vs B vs C vs D vs E vs F vs G vs H"
        err = io.StringIO()
        with redirect_stderr(err):
            kept = cli.truncate_comparison_entities(
                planner._comparison_entities(topic, uncapped=True),
                warn=True,
            )
        self.assertEqual(len(kept), competitors.COMPARISON_ENTITY_MAX)
        self.assertIn("dropped", err.getvalue().lower())
        self.assertIn("H", err.getvalue())

    def test_render_scaffold_keeps_five_columns(self):
        entities = render._parse_comparison_entities(FIVE_WAY)
        self.assertEqual(len(entities), 5)
        lines = render._render_comparison_scaffold(FIVE_WAY)
        header = next(line for line in lines if line.startswith("| Dimension |"))
        self.assertIn("Napoleon Grills", header)


if __name__ == "__main__":
    unittest.main()
