"""Tests for YouTube integration."""

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

from lib import youtube, normalize, score, dedupe, schema


class TestYouTubeParser(unittest.TestCase):
    """Test YouTube response parsing."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "youtube_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)

    def test_parse_returns_items(self):
        items = youtube.parse_youtube_response(self.sample)
        self.assertEqual(len(items), 3)

    def test_parse_item_fields(self):
        items = youtube.parse_youtube_response(self.sample)
        item = items[0]
        self.assertEqual(item["id"], "YT1")
        self.assertIn("React Server Components", item["title"])
        self.assertEqual(item["url"], "https://www.youtube.com/watch?v=abc123def")
        self.assertEqual(item["channel"], "Tech Channel")
        self.assertEqual(item["date"], "2026-01-20")

    def test_parse_engagement(self):
        items = youtube.parse_youtube_response(self.sample)
        eng = items[0]["engagement"]
        self.assertIsNotNone(eng)
        self.assertEqual(eng["views"], 150000)
        self.assertEqual(eng["likes"], 5200)
        self.assertEqual(eng["comments"], 340)

    def test_parse_html_entities(self):
        items = youtube.parse_youtube_response(self.sample)
        item = items[1]
        # &amp; should be decoded to &, &#39; to '
        self.assertIn("&", item["title"])
        self.assertIn("'", item["title"])
        self.assertNotIn("&amp;", item["title"])

    def test_parse_empty_response(self):
        items = youtube.parse_youtube_response({})
        self.assertEqual(items, [])

    def test_parse_error_response(self):
        items = youtube.parse_youtube_response({"error": "quota exceeded"})
        self.assertEqual(items, [])

    def test_parse_no_stats(self):
        response = {
            "items": [{
                "id": {"videoId": "test123"},
                "snippet": {
                    "title": "Test Video",
                    "publishedAt": "2026-02-01T00:00:00Z",
                    "channelTitle": "Test Channel",
                },
            }],
        }
        items = youtube.parse_youtube_response(response)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["engagement"])

    def test_relevance_decreases_with_rank(self):
        items = youtube.parse_youtube_response(self.sample)
        # First item should have higher relevance than last
        self.assertGreater(items[0]["relevance"], items[2]["relevance"])

    def test_relevance_boosted_by_engagement(self):
        # High engagement should boost relevance
        rel = youtube._estimate_relevance(5, {"views": 200000, "likes": 10000})
        rel_low = youtube._estimate_relevance(5, {"views": 100, "likes": 5})
        self.assertGreater(rel, rel_low)


class TestYouTubeHelpers(unittest.TestCase):
    """Test YouTube helper functions."""

    def test_format_count_millions(self):
        self.assertEqual(youtube._format_count(1500000), "1.5M")

    def test_format_count_thousands(self):
        self.assertEqual(youtube._format_count(5200), "5.2K")

    def test_format_count_small(self):
        self.assertEqual(youtube._format_count(42), "42")

    def test_clean_html_entities(self):
        self.assertEqual(youtube._clean_html_entities("foo &amp; bar"), "foo & bar")
        self.assertEqual(youtube._clean_html_entities("it&#39;s"), "it's")

    def test_build_relevance_reason(self):
        reason = youtube._build_relevance_reason(100000, 5000, 200, "Tech Channel")
        self.assertIn("Tech Channel", reason)
        self.assertIn("100.0K views", reason)
        self.assertIn("5.0K likes", reason)

    def test_build_relevance_reason_no_data(self):
        reason = youtube._build_relevance_reason(None, None, None, "")
        self.assertEqual(reason, "YouTube video result")

    def test_safe_int(self):
        self.assertEqual(youtube._safe_int("123"), 123)
        self.assertEqual(youtube._safe_int(None), None)
        self.assertEqual(youtube._safe_int("abc"), None)

    def test_mock_response_passthrough(self):
        mock = {"items": [], "_video_stats": {}}
        result = youtube.search_youtube("key", "topic", "2026-01-01", "2026-02-01", mock_response=mock)
        self.assertEqual(result, mock)


class TestYouTubeNormalize(unittest.TestCase):
    """Test YouTube item normalization."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "youtube_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)
        self.items = youtube.parse_youtube_response(self.sample)

    def test_normalize_creates_yt_items(self):
        normalized = normalize.normalize_yt_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        self.assertEqual(len(normalized), 3)
        self.assertIsInstance(normalized[0], schema.YTItem)

    def test_normalize_preserves_engagement(self):
        normalized = normalize.normalize_yt_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        eng = normalized[0].engagement
        self.assertIsNotNone(eng)
        self.assertEqual(eng.views, 150000)
        self.assertEqual(eng.likes, 5200)
        self.assertEqual(eng.num_comments, 340)

    def test_normalize_date_confidence(self):
        normalized = normalize.normalize_yt_items(
            self.items, "2026-01-15", "2026-02-14"
        )
        # Dates from YouTube API should be high confidence
        self.assertEqual(normalized[0].date_confidence, "high")


class TestYouTubeScoring(unittest.TestCase):
    """Test YouTube item scoring."""

    def setUp(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "youtube_sample.json"
        with open(fixture_path) as f:
            self.sample = json.load(f)
        raw = youtube.parse_youtube_response(self.sample)
        self.items = normalize.normalize_yt_items(raw, "2026-01-15", "2026-02-14")

    def test_scoring_assigns_scores(self):
        scored = score.score_yt_items(self.items)
        for item in scored:
            self.assertGreater(item.score, 0)
            self.assertLessEqual(item.score, 100)

    def test_scoring_subscores(self):
        scored = score.score_yt_items(self.items)
        for item in scored:
            self.assertIsNotNone(item.subs)
            self.assertGreater(item.subs.relevance, 0)

    def test_high_engagement_scores_higher(self):
        scored = score.score_yt_items(self.items)
        # First item has 150K views, should score highest engagement
        sorted_by_eng = sorted(scored, key=lambda x: x.subs.engagement, reverse=True)
        self.assertEqual(sorted_by_eng[0].id, "YT1")

    def test_engagement_raw_computation(self):
        eng = schema.Engagement(views=100000, likes=5000, num_comments=200)
        raw = score.compute_yt_engagement_raw(eng)
        self.assertIsNotNone(raw)
        self.assertGreater(raw, 0)

    def test_engagement_raw_none(self):
        self.assertIsNone(score.compute_yt_engagement_raw(None))
        eng = schema.Engagement()
        self.assertIsNone(score.compute_yt_engagement_raw(eng))


class TestYouTubeDedupe(unittest.TestCase):
    """Test YouTube deduplication."""

    def test_dedupe_removes_similar(self):
        items = [
            schema.YTItem(id="YT1", title="React Server Components Tutorial", url="u1", channel="Ch1", score=80, relevance=0.9),
            schema.YTItem(id="YT2", title="React Server Components Tutorial Guide", url="u2", channel="Ch2", score=60, relevance=0.7),
        ]
        deduped = dedupe.dedupe_yt(items)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].id, "YT1")

    def test_dedupe_keeps_different(self):
        items = [
            schema.YTItem(id="YT1", title="React Server Components", url="u1", channel="Ch1", score=80, relevance=0.9),
            schema.YTItem(id="YT2", title="Python Machine Learning Basics", url="u2", channel="Ch2", score=70, relevance=0.8),
        ]
        deduped = dedupe.dedupe_yt(items)
        self.assertEqual(len(deduped), 2)


class TestYouTubeSchema(unittest.TestCase):
    """Test YouTube schema serialization."""

    def test_yt_item_to_dict(self):
        item = schema.YTItem(
            id="YT1",
            title="Test Video",
            url="https://youtube.com/watch?v=test",
            channel="Test Channel",
            date="2026-02-01",
            engagement=schema.Engagement(views=1000, likes=50, num_comments=10),
            relevance=0.8,
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "YT1")
        self.assertEqual(d["channel"], "Test Channel")
        self.assertEqual(d["engagement"]["views"], 1000)

    def test_report_with_yt(self):
        report = schema.create_report("test", "2026-01-15", "2026-02-14", "both")
        report.yt = [
            schema.YTItem(id="YT1", title="Test", url="u1", channel="Ch1"),
        ]
        d = report.to_dict()
        self.assertEqual(len(d["yt"]), 1)

    def test_report_roundtrip(self):
        report = schema.create_report("test", "2026-01-15", "2026-02-14", "both")
        report.yt = [
            schema.YTItem(
                id="YT1", title="Test", url="u1", channel="Ch1",
                engagement=schema.Engagement(views=500),
            ),
        ]
        report.yt_error = "test error"
        d = report.to_dict()
        restored = schema.Report.from_dict(d)
        self.assertEqual(len(restored.yt), 1)
        self.assertEqual(restored.yt[0].channel, "Ch1")
        self.assertEqual(restored.yt[0].engagement.views, 500)
        self.assertEqual(restored.yt_error, "test error")

    def test_engagement_views_field(self):
        eng = schema.Engagement(views=10000)
        d = eng.to_dict()
        self.assertEqual(d["views"], 10000)


if __name__ == "__main__":
    unittest.main()
