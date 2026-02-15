"""Tests for Product Hunt integration."""

import json
import os
import sys
import unittest
from pathlib import Path

# Add scripts to path
SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

# Set clean config mode for tests
os.environ['LAST30DAYS_CONFIG_DIR'] = ''

from lib import producthunt, normalize, score, dedupe, schema


class TestPHParser(unittest.TestCase):
    """Test Product Hunt response parsing."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "producthunt_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)

    def test_parse_returns_items(self):
        items = producthunt.parse_ph_response(self.sample)
        self.assertEqual(len(items), 3)

    def test_parse_item_fields(self):
        items = producthunt.parse_ph_response(self.sample)
        item = items[0]
        self.assertEqual(item["id"], "PH1")
        self.assertEqual(item["name"], "AI Code Assistant Pro")
        self.assertEqual(item["tagline"], "Your AI pair programmer that actually works")
        self.assertIn("producthunt.com", item["url"])
        self.assertEqual(item["website"], "https://aicodeassistant.pro")
        self.assertEqual(item["date"], "2026-02-05")

    def test_parse_engagement(self):
        items = producthunt.parse_ph_response(self.sample)
        eng = items[0]["engagement"]
        self.assertIsNotNone(eng)
        self.assertEqual(eng["votes"], 542)
        self.assertEqual(eng["comments"], 87)

    def test_parse_topics(self):
        items = producthunt.parse_ph_response(self.sample)
        topics = items[0]["topics"]
        self.assertEqual(len(topics), 2)
        self.assertIn("Artificial Intelligence", topics)
        self.assertIn("Developer Tools", topics)

    def test_parse_makers(self):
        items = producthunt.parse_ph_response(self.sample)
        makers = items[0]["makers"]
        self.assertEqual(len(makers), 1)
        self.assertEqual(makers[0], "Jane Smith")

    def test_parse_multiple_makers(self):
        items = producthunt.parse_ph_response(self.sample)
        makers = items[1]["makers"]
        self.assertEqual(len(makers), 2)

    def test_parse_empty_response(self):
        items = producthunt.parse_ph_response({})
        self.assertEqual(items, [])

    def test_parse_error_response(self):
        items = producthunt.parse_ph_response({"error": "unauthorized"})
        self.assertEqual(items, [])

    def test_parse_graphql_error(self):
        items = producthunt.parse_ph_response({"errors": [{"message": "bad query"}]})
        self.assertEqual(items, [])

    def test_relevance_decreases_with_rank(self):
        items = producthunt.parse_ph_response(self.sample)
        self.assertGreaterEqual(items[0]["relevance"], items[2]["relevance"])

    def test_relevance_boosted_by_votes(self):
        rel_high = producthunt._estimate_relevance(5, 600, 50)
        rel_low = producthunt._estimate_relevance(5, 10, 2)
        self.assertGreater(rel_high, rel_low)


class TestPHHelpers(unittest.TestCase):
    """Test Product Hunt helper functions."""

    def test_build_relevance_reason(self):
        reason = producthunt._build_relevance_reason(
            542, 87, ["AI", "DevTools"], ["Jane Smith"]
        )
        self.assertIn("542 upvotes", reason)
        self.assertIn("87 comments", reason)
        self.assertIn("AI", reason)
        self.assertIn("Jane Smith", reason)

    def test_build_relevance_reason_no_data(self):
        reason = producthunt._build_relevance_reason(0, 0, [], [])
        self.assertEqual(reason, "Product Hunt launch")

    def test_mock_response_passthrough(self):
        mock = {"data": {"posts": {"edges": []}}}
        result = producthunt.search_producthunt(
            "token", ["test-slug"], "2026-01-01", "2026-02-01", mock_response=mock
        )
        self.assertEqual(result, mock)


class TestPHNormalize(unittest.TestCase):
    """Test Product Hunt item normalization."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "producthunt_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)
        self.items = producthunt.parse_ph_response(self.sample)

    def test_normalize_creates_ph_items(self):
        normalized = normalize.normalize_ph_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        self.assertEqual(len(normalized), 3)
        self.assertIsInstance(normalized[0], schema.PHItem)

    def test_normalize_preserves_engagement(self):
        normalized = normalize.normalize_ph_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        eng = normalized[0].engagement
        self.assertIsNotNone(eng)
        self.assertEqual(eng.votes, 542)
        self.assertEqual(eng.num_comments, 87)

    def test_normalize_preserves_topics(self):
        normalized = normalize.normalize_ph_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        self.assertEqual(len(normalized[0].topics), 2)

    def test_normalize_date_confidence(self):
        normalized = normalize.normalize_ph_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        # Dates from Product Hunt API should be high confidence
        self.assertEqual(normalized[0].date_confidence, "high")


class TestPHScoring(unittest.TestCase):
    """Test Product Hunt item scoring."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "producthunt_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)
        raw = producthunt.parse_ph_response(self.sample)
        self.items = normalize.normalize_ph_items(raw, "2026-01-15", "2026-02-14")

    def test_scoring_assigns_scores(self):
        scored = score.score_ph_items(self.items)
        for item in scored:
            self.assertGreater(item.score, 0)
            self.assertLessEqual(item.score, 100)

    def test_scoring_subscores(self):
        scored = score.score_ph_items(self.items)
        for item in scored:
            self.assertIsNotNone(item.subs)
            self.assertGreater(item.subs.relevance, 0)

    def test_high_votes_scores_higher(self):
        scored = score.score_ph_items(self.items)
        # First item has 542 votes, should have highest engagement subscore
        sorted_by_eng = sorted(scored, key=lambda x: x.subs.engagement, reverse=True)
        self.assertEqual(sorted_by_eng[0].id, "PH1")

    def test_engagement_raw_computation(self):
        eng = schema.Engagement(votes=500, num_comments=50)
        raw = score.compute_ph_engagement_raw(eng)
        self.assertIsNotNone(raw)
        self.assertGreater(raw, 0)

    def test_engagement_raw_none(self):
        self.assertIsNone(score.compute_ph_engagement_raw(None))
        eng = schema.Engagement()
        self.assertIsNone(score.compute_ph_engagement_raw(eng))


class TestPHDedupe(unittest.TestCase):
    """Test Product Hunt deduplication."""

    def test_dedupe_removes_similar(self):
        items = [
            schema.PHItem(id="PH1", name="AI Code Assistant Pro", tagline="t1", url="u1", website="w1", score=80, relevance=0.9),
            schema.PHItem(id="PH2", name="AI Code Assistant Pro Edition", tagline="t2", url="u2", website="w2", score=60, relevance=0.7),
        ]
        deduped = dedupe.dedupe_ph(items)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].id, "PH1")

    def test_dedupe_keeps_different(self):
        items = [
            schema.PHItem(id="PH1", name="AI Code Assistant", tagline="t1", url="u1", website="w1", score=80, relevance=0.9),
            schema.PHItem(id="PH2", name="Project Management Tool", tagline="t2", url="u2", website="w2", score=70, relevance=0.8),
        ]
        deduped = dedupe.dedupe_ph(items)
        self.assertEqual(len(deduped), 2)


class TestPHSchema(unittest.TestCase):
    """Test Product Hunt schema serialization."""

    def test_ph_item_to_dict(self):
        item = schema.PHItem(
            id="PH1",
            name="Test Product",
            tagline="A test product",
            url="https://producthunt.com/posts/test",
            website="https://test.com",
            date="2026-02-01",
            engagement=schema.Engagement(votes=100, num_comments=20),
            topics=["AI", "DevTools"],
            makers=["Jane"],
            relevance=0.8,
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "PH1")
        self.assertEqual(d["name"], "Test Product")
        self.assertEqual(d["engagement"]["votes"], 100)
        self.assertEqual(d["topics"], ["AI", "DevTools"])

    def test_report_with_ph(self):
        report = schema.create_report("test", "2026-01-15", "2026-02-14", "both")
        report.ph = [
            schema.PHItem(id="PH1", name="Test", tagline="t", url="u1", website="w1"),
        ]
        d = report.to_dict()
        self.assertEqual(len(d["ph"]), 1)

    def test_report_roundtrip(self):
        report = schema.create_report("test", "2026-01-15", "2026-02-14", "both")
        report.ph = [
            schema.PHItem(
                id="PH1", name="Test", tagline="tagline", url="u1", website="w1",
                engagement=schema.Engagement(votes=200, num_comments=30),
                topics=["AI"],
                makers=["Bob"],
            ),
        ]
        report.ph_error = "test error"
        d = report.to_dict()
        restored = schema.Report.from_dict(d)
        self.assertEqual(len(restored.ph), 1)
        self.assertEqual(restored.ph[0].name, "Test")
        self.assertEqual(restored.ph[0].engagement.votes, 200)
        self.assertEqual(restored.ph[0].topics, ["AI"])
        self.assertEqual(restored.ph_error, "test error")

    def test_engagement_votes_field(self):
        eng = schema.Engagement(votes=500)
        d = eng.to_dict()
        self.assertEqual(d["votes"], 500)


if __name__ == "__main__":
    unittest.main()
