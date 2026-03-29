"""Tests for yt-dlp invocation safety flags."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import youtube_yt


class _DummyProc:
    def __init__(self):
        self.pid = 12345
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def wait(self, timeout=None):
        return 0


class _JsonProc:
    def __init__(self, lines):
        self.pid = 12345
        self.returncode = 0
        self._stdout = "\n".join(lines)

    def communicate(self, timeout=None):
        return self._stdout, ""

    def wait(self, timeout=None):
        return 0


class TestYtDlpFlags(unittest.TestCase):
    def test_search_ignores_global_config_and_browser_cookies(self):
        proc = _DummyProc()
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subprocess, "Popen", return_value=proc) as popen_mock:
            youtube_yt.search_youtube("Claude Code", "2026-02-01", "2026-03-01")

        cmd = popen_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)

    def test_search_prefers_only_in_range_results_when_recent_exist(self):
        recent = (
            '{"id":"recent1","title":"Recent video","channel":"ch",'
            '"view_count":100,"like_count":10,"comment_count":1,"upload_date":"20260327"}'
        )
        old_1 = (
            '{"id":"old1","title":"Old video 1","channel":"ch",'
            '"view_count":9999,"like_count":500,"comment_count":50,"upload_date":"20250920"}'
        )
        old_2 = (
            '{"id":"old2","title":"Old video 2","channel":"ch",'
            '"view_count":8888,"like_count":400,"comment_count":40,"upload_date":"20250820"}'
        )
        proc = _JsonProc([recent, old_1, old_2])
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subprocess, "Popen", return_value=proc):
            result = youtube_yt.search_youtube("AI video tools", "2026-02-26", "2026-03-28", depth="quick")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["video_id"], "recent1")

    def test_transcript_fetch_ignores_global_config_and_browser_cookies(self):
        proc = _DummyProc()
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(youtube_yt.subprocess, "Popen", return_value=proc) as popen_mock:
            youtube_yt.fetch_transcript("abc123", temp_dir)

        cmd = popen_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)


class TestExtractTranscriptHighlights(unittest.TestCase):
    def test_extracts_specific_sentences(self):
        transcript = (
            "Hey guys welcome back to the channel. "
            "In today's video we're looking at something special. "
            "The Lego Bugatti Chiron took 13,438 hours to build with over 1 million pieces. "
            "Don't forget to subscribe and hit the bell. "
            "The tolerance on each brick is 0.002 millimeters which is insane for injection molding. "
            "So yeah that's pretty cool. "
            "Thanks for watching see you next time."
        )
        highlights = youtube_yt.extract_transcript_highlights(transcript, "Lego")
        self.assertTrue(len(highlights) > 0)
        # Should pick the sentences with numbers and topic relevance, not filler
        joined = " ".join(highlights)
        self.assertIn("13,438", joined)
        self.assertNotIn("subscribe", joined)
        self.assertNotIn("welcome back", joined)

    def test_empty_transcript(self):
        self.assertEqual(youtube_yt.extract_transcript_highlights("", "test"), [])

    def test_respects_limit(self):
        sentences = ". ".join(
            f"The model {i} has {i * 100} parameters and runs at {i * 10} tokens per second"
            for i in range(20)
        ) + "."
        highlights = youtube_yt.extract_transcript_highlights(sentences, "model", limit=3)
        self.assertEqual(len(highlights), 3)


if __name__ == "__main__":
    unittest.main()
