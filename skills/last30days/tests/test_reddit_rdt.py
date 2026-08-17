import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.lib import reddit_rdt


class RdtAdapterTests(unittest.TestCase):
    def _proc(self, rows, returncode=0):
        return subprocess.CompletedProcess(
            ["rdt", "search"], returncode, json.dumps({"ok": True, "data": rows}), ""
        )

    @patch("scripts.lib.reddit_rdt._command", return_value="rdt")
    @patch("scripts.lib.reddit_rdt.subprocess.run")
    def test_normalizes_filters_deduplicates_and_scopes_subreddit(self, run, _command):
        run.return_value = self._proc([
            {"id": "t3_abc", "title": "Hermes Agent tips", "subreddit": "hermesagent", "url": "https://www.reddit.com/r/hermesagent/comments/abc/x", "score": 4, "num_comments": 2, "created_utc": 1785826514},
            {"id": "abc", "title": "duplicate", "subreddit": "hermesagent", "permalink": "/r/hermesagent/comments/abc/x", "created_utc": 1785826514},
            {"id": "old", "title": "old", "subreddit": "hermesagent", "url": "https://www.reddit.com/r/hermesagent/comments/old/x", "created_utc": 1},
            {"id": "other", "title": "other", "subreddit": "python", "url": "https://www.reddit.com/r/python/comments/other/x", "created_utc": 1785826514},
        ])
        items, outcome = reddit_rdt.search("Hermes Agent", "2026-08-01", "2026-08-31", subreddits=["r/hermesagent"])
        self.assertIsNone(outcome)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reddit_id"], "abc")
        self.assertEqual(items[0]["engagement"]["score"], 4)
        args = run.call_args.args[0]
        self.assertEqual(args[0:3], ["rdt", "search", "Hermes Agent"])
        self.assertIn("--subreddit", args)

    @patch("scripts.lib.reddit_rdt._command", return_value="rdt")
    @patch("scripts.lib.reddit_rdt.subprocess.run", side_effect=subprocess.TimeoutExpired("rdt", 20))
    def test_timeout_is_typed(self, run, _command):
        items, outcome = reddit_rdt.search("x", "2026-08-01", "2026-08-31")
        self.assertEqual(items, [])
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.state, "timeout")

    @patch("scripts.lib.reddit_rdt._command", return_value="rdt")
    @patch("scripts.lib.reddit_rdt.subprocess.run")
    def test_schema_drift_is_typed(self, run, _command):
        run.return_value = subprocess.CompletedProcess(["rdt"], 0, "{\"ok\": true, \"data\": \"unexpected\"}", "")
        items, outcome = reddit_rdt.search("x", "2026-08-01", "2026-08-31")
        self.assertEqual(items, [])
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.state, "schema-drift")


if __name__ == "__main__":
    unittest.main()
