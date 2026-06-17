import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_save_briefing_uses_utf8_encoding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_briefs_dir = briefing.BRIEFS_DIR
            try:
                briefing.BRIEFS_DIR = Path(tmpdir) / "briefs"
                payload = {"status": "ok", "message": "emoji 💬 and accents café"}
                with mock.patch("briefing.open", create=True) as mock_open:
                    handle = mock.Mock()
                    handle.__enter__ = mock.Mock(return_value=handle)
                    handle.__exit__ = mock.Mock(return_value=False)
                    mock_open.return_value = handle

                    briefing._save_briefing(payload)

                mock_open.assert_called_once()
                _, kwargs = mock_open.call_args
                self.assertEqual("w", kwargs["mode"] if "mode" in kwargs else mock_open.call_args.args[1])
                self.assertEqual("utf-8", kwargs["encoding"])
            finally:
                briefing.BRIEFS_DIR = old_briefs_dir

if __name__ == "__main__":
    unittest.main()
