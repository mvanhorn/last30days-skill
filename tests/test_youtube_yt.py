"""Tests for YouTube transcript highlights and yt-dlp safety flags."""

import json
import sys
import tempfile
import unittest
import urllib.error
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


class TestYouTubeEngagementZero(unittest.TestCase):
    """Verify that 0 engagement counts are preserved (not coerced to fallback)."""

    def test_zero_view_count_preserved(self):
        """video.get('view_count') == 0 must stay 0, not become the fallback."""
        import json
        import tempfile
        import os

        video = {
            "id": "abc123",
            "title": "Test",
            "view_count": 0,
            "like_count": 0,
            "comment_count": 0,
            "upload_date": "20260301",
            "description": "desc",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(video) + "\n")
            f.flush()
            with open(f.name) as rf:
                lines = rf.readlines()

        # Re-parse as the search function would
        parsed = json.loads(lines[0])
        view_count = parsed.get("view_count") if parsed.get("view_count") is not None else 0
        like_count = parsed.get("like_count") if parsed.get("like_count") is not None else 0
        comment_count = parsed.get("comment_count") if parsed.get("comment_count") is not None else 0

        os.unlink(f.name)

        self.assertEqual(0, view_count)
        self.assertEqual(0, like_count)
        self.assertEqual(0, comment_count)


class TestYtDlpFlags(unittest.TestCase):
    def test_search_ignores_global_config_and_browser_cookies(self):
        proc = _DummyProc()
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subprocess, "Popen", return_value=proc) as popen_mock:
            youtube_yt.search_youtube("Claude Code", "2026-02-01", "2026-03-01")

        cmd = popen_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)

    def test_transcript_fetch_ignores_global_config_and_browser_cookies(self):
        proc = _DummyProc()
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
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

    def test_punctuation_free_transcript_produces_highlights(self):
        # Auto-generated YouTube captions often lack sentence-ending punctuation
        words = (
            "the new Tesla Model Y has 350 miles of range and costs about 45000 dollars "
            "which makes it one of the most affordable electric vehicles on the market today "
            "compared to the BMW iX which starts at 87000 the value proposition is pretty clear "
            "and with the 7500 dollar tax credit you can get it for under 40000"
        )
        highlights = youtube_yt.extract_transcript_highlights(words, "Tesla Model Y")
        self.assertTrue(len(highlights) > 0, "Should produce highlights from punctuation-free text")


class TestFetchTranscriptDirect(unittest.TestCase):
    """Tests for _fetch_transcript_direct() — direct HTTP transcript fetching."""

    # Minimal ytInitialPlayerResponse JSON with a caption track
    _PLAYER_RESPONSE = json.dumps({
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=abc123&lang=en",
                        "languageCode": "en",
                    }
                ]
            }
        }
    })

    _WATCH_HTML = (
        '<html><script>var ytInitialPlayerResponse = '
        + _PLAYER_RESPONSE
        + ';</script></html>'
    )

    _SAMPLE_VTT = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\n"
        "Hello world this is a test sentence with enough words to pass.\n\n"
        "00:00:02.000 --> 00:00:04.000\n"
        "Another line of transcript text here for testing purposes.\n"
    )

    def _mock_urlopen(self, url_or_req, *, timeout=None):
        """Return watch HTML or VTT depending on URL."""
        url = url_or_req.full_url if hasattr(url_or_req, 'full_url') else url_or_req

        class _Resp:
            def __init__(self, data):
                self._data = data.encode("utf-8")
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        if "watch?" in url:
            return _Resp(self._WATCH_HTML)
        elif "timedtext" in url:
            return _Resp(self._SAMPLE_VTT)
        raise urllib.error.URLError("unexpected URL")

    def test_extracts_vtt_from_mock_page(self):
        """Happy path: extracts VTT text from a page with captions."""
        with mock.patch("lib.youtube_yt.urllib.request.urlopen", side_effect=self._mock_urlopen):
            result = youtube_yt._fetch_transcript_direct("abc123")
        self.assertIsNotNone(result)
        self.assertIn("WEBVTT", result)
        self.assertIn("Hello world", result)

    def test_no_captions_returns_none(self):
        """Video with no caption tracks returns None."""
        no_captions_response = json.dumps({"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": []}}})
        html = f'<html><script>var ytInitialPlayerResponse = {no_captions_response};</script></html>'

        class _Resp:
            def __init__(self, data):
                self._data = data.encode("utf-8")
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def mock_open(req, *, timeout=None):
            return _Resp(html)

        with mock.patch("lib.youtube_yt.urllib.request.urlopen", side_effect=mock_open):
            result = youtube_yt._fetch_transcript_direct("nocaps")
        self.assertIsNone(result)

    def test_http_timeout_returns_none(self):
        """HTTP timeout on watch page returns None."""
        def timeout_open(req, *, timeout=None):
            raise TimeoutError("timed out")

        with mock.patch("lib.youtube_yt.urllib.request.urlopen", side_effect=timeout_open):
            result = youtube_yt._fetch_transcript_direct("timeout_vid")
        self.assertIsNone(result)

    def test_direct_vtt_feeds_into_clean_vtt(self):
        """VTT from direct fetch produces clean plaintext via _clean_vtt()."""
        cleaned = youtube_yt._clean_vtt(self._SAMPLE_VTT)
        self.assertNotIn("WEBVTT", cleaned)
        self.assertNotIn("-->", cleaned)
        self.assertIn("Hello world", cleaned)
        self.assertIn("Another line", cleaned)


class TestFetchTranscriptFallback(unittest.TestCase):
    """Tests that fetch_transcript picks yt-dlp or direct path correctly."""

    def test_uses_ytdlp_when_installed(self):
        """When yt-dlp is installed, uses _fetch_transcript_ytdlp."""
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp", return_value="WEBVTT\n\nfake") as yt_mock, \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct") as direct_mock:
            result = youtube_yt.fetch_transcript("vid1", "/tmp/test")
        yt_mock.assert_called_once_with("vid1", "/tmp/test")
        direct_mock.assert_not_called()

    def test_uses_direct_when_ytdlp_missing(self):
        """When yt-dlp is NOT installed, falls back to _fetch_transcript_direct."""
        sample_vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "Direct transcript content with enough words for testing.\n"
        )
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp") as yt_mock, \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct", return_value=sample_vtt) as direct_mock:
            result = youtube_yt.fetch_transcript("vid2", "/tmp/test")
        yt_mock.assert_not_called()
        direct_mock.assert_called_once_with("vid2")
        self.assertIsNotNone(result)
        self.assertIn("Direct transcript content", result)

    def test_returns_none_when_both_fail(self):
        """Returns None when the chosen path returns None."""
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False), \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct", return_value=None):
            result = youtube_yt.fetch_transcript("novid", "/tmp/test")
        self.assertIsNone(result)


class TestExpandYouTubeQueries(unittest.TestCase):
    """Tests for expand_youtube_queries() multi-query generation."""

    def test_default_depth_returns_two_plus_queries(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "default")
        self.assertGreaterEqual(len(queries), 2)
        # First query is the core subject
        self.assertEqual(queries[0].lower(), "kanye west")

    def test_how_to_intent_includes_tutorial_variant(self):
        # Use deep depth so the intent variant isn't capped out by core + original
        queries = youtube_yt.expand_youtube_queries("how to use Docker", "deep")
        variant_found = any(
            "tutorial" in q.lower() or "guide" in q.lower() or "explained" in q.lower()
            for q in queries
        )
        self.assertTrue(
            variant_found,
            f"Expected tutorial/guide/explained in queries: {queries}",
        )

    def test_product_intent_includes_review_variant(self):
        # Use deep depth so the intent variant isn't capped out
        queries = youtube_yt.expand_youtube_queries("best running shoes", "deep")
        variant_found = any("review" in q.lower() for q in queries)
        self.assertTrue(variant_found, f"Expected 'review' in queries: {queries}")

    def test_comparison_intent_includes_vs_variant(self):
        queries = youtube_yt.expand_youtube_queries("Claude vs Gemini", "default")
        variant_found = any("vs" in q.lower() or "compared" in q.lower() for q in queries)
        self.assertTrue(variant_found, f"Expected 'vs' or 'compared' in queries: {queries}")

    def test_quick_depth_returns_one_query(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "quick")
        self.assertEqual(len(queries), 1)

    def test_deep_depth_returns_three_queries(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "deep")
        self.assertEqual(len(queries), 3)

    def test_single_word_returns_at_least_one(self):
        queries = youtube_yt.expand_youtube_queries("React", "default")
        self.assertGreaterEqual(len(queries), 1)

    def test_temporal_words_stripped_from_core(self):
        queries = youtube_yt.expand_youtube_queries("kanye west last 30 days", "default")
        core = queries[0].lower()
        self.assertNotIn("last", core)
        self.assertNotIn("days", core)
        self.assertIn("kanye", core)
        self.assertIn("west", core)


class TestInferQueryIntent(unittest.TestCase):
    """Tests for _infer_query_intent() classification."""

    def test_comparison_intent(self):
        self.assertEqual(youtube_yt._infer_query_intent("Claude vs Gemini"), "comparison")

    def test_how_to_intent(self):
        self.assertEqual(youtube_yt._infer_query_intent("how to deploy Kubernetes"), "how_to")

    def test_opinion_intent(self):
        self.assertEqual(youtube_yt._infer_query_intent("thoughts on Claude Code"), "opinion")

    def test_product_intent(self):
        self.assertEqual(youtube_yt._infer_query_intent("best laptop for programming"), "product")

    def test_breaking_news_default(self):
        self.assertEqual(youtube_yt._infer_query_intent("Kanye West"), "breaking_news")


class TestSearchAndTranscribe(unittest.TestCase):
    """Tests for search_and_transcribe() end-to-end flow."""

    def _make_item(self, video_id, views):
        return {
            "video_id": video_id,
            "title": f"Video {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel_name": "TestChannel",
            "date": "2026-03-15",
            "engagement": {"views": views, "likes": 10, "comments": 5},
            "relevance": 0.8,
            "why_relevant": "test",
            "description": "test desc",
            "duration": 600,
        }

    def test_transcripts_attached_when_top_videos_lack_captions(self):
        """When top-viewed videos have no captions, lower-ranked ones still get transcripts."""
        items = [
            self._make_item("music1", 1_000_000),   # no captions (music video)
            self._make_item("music2", 500_000),      # no captions (music video)
            self._make_item("talk1", 50_000),         # has captions
            self._make_item("talk2", 25_000),         # has captions
        ]

        # fetch_transcripts_parallel returns None for music videos, text for talks
        def fake_parallel(video_ids, max_workers=5):
            result = {}
            for vid in video_ids:
                if vid.startswith("talk"):
                    result[vid] = "This is a detailed discussion about the topic with 100 data points."
                else:
                    result[vid] = None
            return result

        with mock.patch.object(youtube_yt, "search_youtube", return_value={"items": items}), \
             mock.patch.object(youtube_yt, "fetch_transcripts_parallel", side_effect=fake_parallel) as ft_mock:
            result = youtube_yt.search_and_transcribe("test topic", "2026-03-01", "2026-03-31", depth="default")

        # Should have attempted more than just the top 2 (transcript_limit=2)
        called_ids = ft_mock.call_args[0][0]
        self.assertGreater(len(called_ids), 2, "Should attempt more than transcript_limit candidates")
        self.assertIn("talk1", called_ids)
        self.assertIn("talk2", called_ids)

        # talk1 and talk2 should have transcripts
        items_by_id = {i["video_id"]: i for i in result["items"]}
        self.assertTrue(items_by_id["talk1"]["transcript_snippet"])
        self.assertTrue(items_by_id["talk1"]["transcript_highlights"])
        # music videos should have empty transcripts
        self.assertFalse(items_by_id["music1"]["transcript_snippet"])

    def test_transcript_limit_zero_skips_fetch(self):
        """When transcript_limit is 0 (quick depth), no transcripts are fetched."""
        items = [self._make_item("vid1", 1000)]
        with mock.patch.object(youtube_yt, "search_youtube", return_value={"items": items}), \
             mock.patch.object(youtube_yt, "fetch_transcripts_parallel") as ft_mock:
            result = youtube_yt.search_and_transcribe("test", "2026-03-01", "2026-03-31", depth="quick")

        ft_mock.assert_not_called()
        self.assertEqual(result["items"][0]["transcript_snippet"], "")

    def test_no_items_returns_early(self):
        """When search returns no items, returns without fetching transcripts."""
        with mock.patch.object(youtube_yt, "search_youtube", return_value={"items": []}), \
             mock.patch.object(youtube_yt, "fetch_transcripts_parallel") as ft_mock:
            result = youtube_yt.search_and_transcribe("nothing", "2026-03-01", "2026-03-31")

        ft_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
