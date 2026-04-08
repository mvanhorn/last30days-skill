import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import briefing
import store


class BriefingV3Tests(unittest.TestCase):
    def test_generate_daily_uses_utc_for_last_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "research.db"
            briefs_dir = Path(tmpdir) / "briefs"
            old_db_override = store._db_override
            old_briefs_dir = briefing.BRIEFS_DIR
            try:
                store._db_override = db_path
                briefing.BRIEFS_DIR = briefs_dir
                topic = store.add_topic("test topic")
                store.record_run(topic["id"], source_mode="v3", status="completed")
                result = briefing.generate_daily()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topics"][0]["name"], "test topic")
                self.assertIsNotNone(result["topics"][0]["hours_ago"])
                self.assertGreaterEqual(result["topics"][0]["hours_ago"], 0.0)
            finally:
                store._db_override = old_db_override
                briefing.BRIEFS_DIR = old_briefs_dir


if __name__ == "__main__":
    unittest.main()
