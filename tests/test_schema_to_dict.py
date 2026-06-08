import json, unittest
from unittest.mock import MagicMock
from scripts.lib.schema import to_dict

def _make_report(sources: dict, topic: str = "test") -> MagicMock:
    r = MagicMock()
    r.topic = topic
    r.items_by_source = {
        src: [MagicMock(to_dict=lambda _item=item: {**_item, "_rank": 0.9})]
        for src, item in sources.items()
    }
    r.errors_by_source = {}
    r.query_plan.lookback_days = 30
    r.query_plan.to_dict.return_value = {}
    r.artifacts = {}
    return r

class TestToDict(unittest.TestCase):

    def setUp(self):
        self.report = _make_report({"reddit": {"title": "foo", "url": "http://x"}})

    def test_schema_version_present(self):
        out = to_dict(self.report)
        self.assertEqual(out["schema_version"], "1.0")

    def test_private_keys_stripped(self):
        out = to_dict(self.report)
        reddit_item = out["sources"]["reddit"][0]
        self.assertNotIn("_rank", reddit_item)

    def test_topic_preserved(self):
        out = to_dict(self.report)
        self.assertEqual(out["topic"], "test")

    def test_generated_at_iso8601(self):
        from datetime import datetime
        out = to_dict(self.report)
        datetime.fromisoformat(out["generated_at"])  # raises if malformed

    def test_engagement_summary_counts(self):
        out = to_dict(self.report)
        self.assertEqual(out["engagement_summary"]["total_items"], 1)
        self.assertEqual(out["engagement_summary"]["top_signal_source"], "reddit")

    def test_output_is_json_serialisable(self):
        out = to_dict(self.report)
        json.dumps(out)  # must not raise

    def test_synthesis_included_when_provided(self):
        self.report.artifacts["synthesis_md"] = "# Summary\nContent here."
        out = to_dict(self.report)
        self.assertIn("synthesis", out)
        self.assertIn("Summary", out["synthesis"])
