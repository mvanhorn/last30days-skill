"""Tests for lib.cost_markers: rate-card pricing and the [cost] marker grammar.

The round-trip tests parse emitted markers with the SAME regexes the
last30days-com research-worker (research-worker/cost.py) uses, so a format drift
that would silently break settlement fails here instead of in production.
"""

import io
import re
import unittest

from lib import cost_markers

# Mirror of the worker's parser (research-worker/cost.py). Kept in the test so a
# divergence between engine emit and worker parse is caught.
_COST_LINE_RE = re.compile(r"^\[cost\]\s+(.*)$")
_KV_RE = re.compile(r"(\w+)=(\S+)")


def parse_one(line: str) -> dict:
    m = _COST_LINE_RE.match(line.strip())
    assert m, f"marker did not match worker grammar: {line!r}"
    return dict(_KV_RE.findall(m.group(1)))


class TestPriceFor(unittest.TestCase):
    def test_rate_card_provider(self):
        self.assertEqual(cost_markers.price_for("scrapecreators"), 0.003)

    def test_model_override_beats_provider(self):
        self.assertEqual(
            cost_markers.price_for("perplexity", "sonar-deep-research"), 0.90
        )
        self.assertEqual(cost_markers.price_for("perplexity", "sonar-pro"), 0.008)

    def test_unknown_provider_is_zero(self):
        self.assertEqual(cost_markers.price_for("not-a-provider"), 0.0)


class TestEmitCost(unittest.TestCase):
    def test_emits_rate_card_price(self):
        buf = io.StringIO()
        cost = cost_markers.emit_cost("scrapecreators", model="reddit", stream=buf)
        self.assertAlmostEqual(cost, 0.003)
        kv = parse_one(buf.getvalue())
        self.assertEqual(kv["provider"], "scrapecreators")
        self.assertEqual(kv["model"], "reddit")
        self.assertEqual(kv["calls"], "1")
        self.assertAlmostEqual(float(kv["cost_usd"]), 0.003)

    def test_calls_multiplier(self):
        buf = io.StringIO()
        cost = cost_markers.emit_cost("exa", calls=3, stream=buf)
        self.assertAlmostEqual(cost, 0.015)
        self.assertAlmostEqual(float(parse_one(buf.getvalue())["cost_usd"]), 0.015)

    def test_explicit_cost_overrides_rate_card(self):
        buf = io.StringIO()
        cost = cost_markers.emit_cost("perplexity", model="sonar-deep-research",
                                      cost_usd=0.91, stream=buf)
        self.assertAlmostEqual(cost, 0.91)

    def test_unknown_provider_emits_zero_no_raise(self):
        buf = io.StringIO()
        cost = cost_markers.emit_cost("mystery", stream=buf)
        self.assertEqual(cost, 0.0)
        self.assertAlmostEqual(float(parse_one(buf.getvalue())["cost_usd"]), 0.0)

    def test_marker_has_all_grammar_fields(self):
        buf = io.StringIO()
        cost_markers.emit_cost("gemini", model="gemini-3.1-flash-lite-preview", stream=buf)
        kv = parse_one(buf.getvalue())
        for field in ("provider", "model", "prompt_tokens", "completion_tokens",
                      "reasoning_tokens", "cached_tokens", "calls", "cost_usd"):
            self.assertIn(field, kv)

    def test_bad_calls_value_does_not_raise(self):
        buf = io.StringIO()
        cost = cost_markers.emit_cost("brave", calls=0, stream=buf)
        self.assertEqual(cost, 0.0)


if __name__ == "__main__":
    unittest.main()
