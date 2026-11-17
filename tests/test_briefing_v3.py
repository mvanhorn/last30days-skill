import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_generate_weekly_ranks_top_findings_by_engagement(self):
        """Weekly digest top_findings must be the highest-engagement items, not
        the most recent. get_new_findings returns first_seen DESC, so without an
        explicit engagement sort the digest headlines recent low-engagement noise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "research.db"
            briefs_dir = Path(tmpdir) / "briefs"
            old_db_override = store._db_override
            old_briefs_dir = briefing.BRIEFS_DIR
            try:
                store._db_override = db_path
                briefing.BRIEFS_DIR = briefs_dir
                topic = store.add_topic("test topic")
                run_id = store.record_run(
                    topic["id"], source_mode="v3", status="completed"
                )

                now = datetime.now(timezone.utc)
                # Finding i: i days ago, engagement i. The most recent (i=0) has
                # the lowest engagement, so a recency sort and an engagement sort
                # disagree on the top 5.
                conn = store._connect()
                try:
                    for i in range(6):
                        first_seen = (now - timedelta(days=i, hours=1)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        conn.execute(
                            """INSERT INTO findings
                               (run_id, topic_id, source, source_url,
                                source_title, engagement_score, first_seen)
                               VALUES (?, ?, 'reddit', ?, ?, ?, ?)""",
                            (
                                run_id,
                                topic["id"],
                                f"https://example.com/{i}",
                                f"finding-{i}",
                                float(i),
                                first_seen,
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()

                result = briefing.generate_weekly()
                top = result["topics"][0]["top_findings"]
                scores = [f["engagement_score"] for f in top]

                self.assertEqual(len(top), 5)
                self.assertEqual(scores, [5.0, 4.0, 3.0, 2.0, 1.0])
                # The most-recent, lowest-engagement finding is dropped, not kept.
                self.assertNotIn(0.0, scores)
            finally:
                store._db_override = old_db_override
                briefing.BRIEFS_DIR = old_briefs_dir

    def test_save_briefing_uses_utf8_encoding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_briefs_dir = briefing.BRIEFS_DIR
            try:
                briefing.BRIEFS_DIR = Path(tmpdir) / "briefs"
                payload = {"status": "ok", "message": "emoji 💬 and accents café"}
                with mock.patch(
                    "briefing.write_private_text", wraps=briefing.write_private_text
                ) as write:
                    briefing._save_briefing(payload)

                write.assert_called_once()
                self.assertEqual("utf-8", write.call_args.kwargs["encoding"])
                path = next(briefing.BRIEFS_DIR.glob("*.json"))
                self.assertEqual(payload, json.loads(path.read_text(encoding="utf-8")))
            finally:
                briefing.BRIEFS_DIR = old_briefs_dir

if __name__ == "__main__":
    unittest.main()
