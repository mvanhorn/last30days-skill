"""Tests for YouTube transcript highlights and yt-dlp safety flags."""

import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

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
    def _fake_result(self, stdout: str = "", returncode: int = 0):
        from lib.subproc import SubprocResult
        return SubprocResult(returncode=returncode, stdout=stdout, stderr="")

    def test_search_ignores_global_config_and_browser_cookies(self):
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.search_youtube("Claude Code", "2026-02-01", "2026-03-01")

        cmd = run_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)

    def test_transcript_fetch_ignores_global_config_and_browser_cookies(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.fetch_transcript("abc123", temp_dir)

        cmd = run_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)


class TestYtDlpSubLangs(unittest.TestCase):
    """Verify LAST30DAYS_YT_SUB_LANGS knob and language-agnostic VTT matching."""

    def _fake_result(self, stdout: str = "", returncode: int = 0):
        from lib.subproc import SubprocResult
        return SubprocResult(returncode=returncode, stdout=stdout, stderr="")

    def test_default_sub_langs_when_env_unset(self):
        """When LAST30DAYS_YT_SUB_LANGS is not set, the default is en,es,pt."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAST30DAYS_YT_SUB_LANGS", None)
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_env_var_overrides_default(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "fr,de"}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "fr,de")

    def test_env_var_normalizes_whitespace_and_case(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": " EN , Es , PT "}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_env_var_handles_empty_segments(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "en,,pt,"}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,pt")

    def test_env_var_empty_string_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "   "}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_transcript_cmd_uses_default_sub_langs(self):
        """Regression: the --sub-lang arg is en,es,pt by default (issue #469)."""
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            os.environ.pop("LAST30DAYS_YT_SUB_LANGS", None)
            youtube_yt.fetch_transcript("abc123", temp_dir)

        cmd = run_mock.call_args_list[0].args[0]
        idx = cmd.index("--sub-lang")
        self.assertEqual(cmd[idx + 1], "en,es,pt")

    def test_transcript_cmd_respects_env_var_override(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "fr,de,it"}), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.fetch_transcript("abc123", temp_dir)

        cmd = run_mock.call_args_list[0].args[0]
        idx = cmd.index("--sub-lang")
        self.assertEqual(cmd[idx + 1], "fr,de,it")

    def test_vtt_matching_picks_non_english_track(self):
        """When yt-dlp writes a Spanish track (no English available), we read it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Simulate yt-dlp output: only a Spanish VTT is available
            (Path(temp_dir) / "abc123.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHola mundo esta es una prueba.\n",
                encoding="utf-8",
            )
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("abc123", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Hola mundo", vtt)

    def test_partial_success_returns_vtt_despite_nonzero_exit(self):
        """A non-zero yt-dlp exit must not discard a VTT already on disk.

        Regression for the 0/N-transcripts bug: with the default
        ``--sub-lang en,es,pt``, an English video fetches ``en`` successfully,
        then ``es``/``pt`` hit a 429 and yt-dlp exits non-zero. The ``en``
        track is already written and must be returned, not discarded (and not
        retried back into the same rate limit).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "abc123.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nThis is the english transcript.\n",
                encoding="utf-8",
            )
            status: dict = {}
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(
                     youtube_yt.subproc,
                     "run_with_timeout",
                     return_value=self._fake_result(returncode=1),
                 ) as run_mock:
                vtt = youtube_yt._fetch_transcript_ytdlp("abc123", temp_dir, status)

        self.assertIsNotNone(vtt)
        self.assertIn("english transcript", vtt)
        self.assertNotIn("ytdlp_error", status)
        # Salvage must short-circuit the retry loop: yt-dlp must not be called a
        # second time when a partial VTT is already on disk (locks in the
        # no-retry guarantee against a future salvage-after-retry regression).
        self.assertEqual(run_mock.call_count, 1)

    def test_vtt_matching_respects_non_default_priority(self):
        """When multiple tracks exist, the user-requested priority wins
        over alphabetical order (regression for the Greptile review on #486)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "abc123.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nEnglish first track.\n",
                encoding="utf-8",
            )
            (Path(temp_dir) / "abc123.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSpanish second track.\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "es,en"}), \
                 mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("abc123", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Spanish", vtt)

    def test_vtt_matching_unknown_suffix_sorts_last(self):
        """A non-lang suffix (e.g. a stray .tmp or .live_chat) must not
        win over a real track that just happens to be alphabetically later."""
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "abc123.zz.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nZZ track content.\n",
                encoding="utf-8",
            )
            (Path(temp_dir) / "abc123.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSpanish content.\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "es,en,pt"}), \
                 mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("abc123", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Spanish", vtt)


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
        yt_mock.assert_called_once_with("vid1", "/tmp/test", status=None, fast_fail=False)
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
        direct_mock.assert_called_once_with("vid2", status=None)
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


class TestTranscriptCandidateSortKey(unittest.TestCase):
    """Tests for _transcript_candidate_sort_key recency-boosted ordering."""

    @staticmethod
    def _d(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    def _make_item(self, video_id, views, date_str):
        return {
            "video_id": video_id,
            "title": f"Video {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel_name": "TestChannel",
            "date": date_str,
            "engagement": {"views": views, "likes": 10, "comments": 5},
            "relevance": 0.8,
            "why_relevant": "test",
            "description": "test desc",
            "duration": 600,
        }

    def test_recency_breaks_views_tie(self):
        """When views are equal, the more recent video gets a higher sort key."""
        new = self._make_item("new", 100_000, self._d(1))
        old = self._make_item("old", 100_000, self._d(13))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(new),
            youtube_yt._transcript_candidate_sort_key(old),
        )

    def test_old_high_view_can_still_qualify_for_transcript(self):
        """An old video with very high views still gets a transcript slot;
        recency is a tiebreaker, not a gate."""
        old_high = self._make_item("old_high", 10_000_000, self._d(45))
        recent_low = self._make_item("recent_low", 100, self._d(1))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(old_high),
            youtube_yt._transcript_candidate_sort_key(recent_low),
        )

    def test_no_date_falls_to_back(self):
        """An item with no date gets recency 0, sorting behind dated items."""
        no_date = self._make_item("no_date", 50_000, "")
        dated = self._make_item("dated", 50_000, self._d(5))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(dated),
            youtube_yt._transcript_candidate_sort_key(no_date),
        )

    def test_transcript_candidates_pick_recent_over_old_same_views(self):
        """search_and_transcribe selects candidates by (views, recency),
        so a recent video is tried before an equal-view older video."""
        items = [
            self._make_item("old", 100_000, self._d(13)),
            self._make_item("recent", 100_000, self._d(1)),
            self._make_item("mid", 50_000, self._d(5)),
        ]

        def fake_search(*args, **kwargs):
            return {"items": items}

        call_args_list = []

        def fake_fetch(video_ids, max_workers=5, out_captions_disabled=None, token=None):
            call_args_list.extend(video_ids)
            return {vid: "transcript" for vid in video_ids}

        with mock.patch.object(youtube_yt, "search_youtube", side_effect=fake_search), \
             mock.patch.object(youtube_yt, "fetch_transcripts_parallel", side_effect=fake_fetch):
            youtube_yt.search_and_transcribe("test", self._d(14), self._d(0), depth="default")

        # transcript_limit=2, attempt_count=4 (limited to 3 items)
        # Sorted by (views, recency): recent(100k) > old(100k) > mid(50k)
        self.assertEqual(call_args_list[:2], ["recent", "old"],
                         "Recent video should be tried before equal-view older video")


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
        def fake_parallel(video_ids, max_workers=5, out_captions_disabled=None, token=None):
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


class TestTranscriptFetchStats(unittest.TestCase):
    """Track yt-dlp fetch outcomes for quality_nudge (#531 false stale-yt-dlp nudge)."""

    FROM_DATE = "2026-03-01"
    TO_DATE = "2026-03-31"

    def setUp(self):
        youtube_yt.reset_transcript_fetch_stats()

    def _make_item(self, video_id, views, date):
        return {
            "video_id": video_id,
            "title": f"Video {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel_name": "TestChannel",
            "date": date,
            "engagement": {"views": views, "likes": 10, "comments": 5},
            "relevance": 0.8,
            "why_relevant": "test",
            "description": "test desc",
            "duration": 600,
        }

    def _run(self, items, fake_parallel=None):
        if fake_parallel is None:
            def fake_parallel(video_ids, max_workers=5, out_captions_disabled=None, token=None):
                return {vid: "A detailed transcript about the topic." for vid in video_ids}
        with mock.patch.object(youtube_yt, "search_youtube", return_value={"items": items}), \
             mock.patch.object(youtube_yt, "fetch_transcripts_parallel", side_effect=fake_parallel):
            return youtube_yt.search_and_transcribe(
                "test topic", self.FROM_DATE, self.TO_DATE, depth="default",
            )

    def test_fetch_stats_track_attempts_and_failures(self):
        items = [
            self._make_item("ok1", 3_000, "2026-03-20"),
            self._make_item("fail1", 2_000, "2026-03-15"),
            self._make_item("nocap1", 1_000, "2026-03-10"),
        ]

        def fake_parallel(video_ids, max_workers=5, out_captions_disabled=None, token=None):
            result = {}
            for vid in video_ids:
                if vid.startswith("nocap"):
                    result[vid] = None
                    if out_captions_disabled is not None:
                        out_captions_disabled.add(vid)
                elif vid.startswith("fail"):
                    result[vid] = None
                else:
                    result[vid] = "A detailed transcript about the topic."
            return result

        self._run(items, fake_parallel)

        stats = youtube_yt.get_transcript_fetch_stats()
        self.assertEqual(stats["attempts"], 3)
        # Captions-disabled videos can never succeed; they are not failures.
        self.assertEqual(stats["failures"], 1)

    def test_fetch_stats_zero_failures_when_all_succeed(self):
        # The #531 scenario: every fetch succeeds (on videos later pruned by
        # freshness scoring). failures must be 0 so quality_nudge does not
        # blame a stale yt-dlp binary.
        items = [self._make_item(f"v{i}", 1_000 * (i + 1), "2024-01-15") for i in range(4)]

        self._run(items)

        stats = youtube_yt.get_transcript_fetch_stats()
        self.assertEqual(stats["attempts"], 4)
        self.assertEqual(stats["failures"], 0)


class TestYtdlpSSHRouting(unittest.TestCase):
    """LAST30DAYS_YOUTUBE_SSH_HOST routes yt-dlp invocations through SSH for residential IP."""

    def setUp(self):
        # Ensure clean env for each test
        self._saved_env = os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)

    def tearDown(self):
        os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)
        if self._saved_env is not None:
            os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = self._saved_env

    def test_no_env_var_returns_none(self):
        """Without the env var set, _ytdlp_ssh_host returns None."""
        self.assertIsNone(youtube_yt._ytdlp_ssh_host())

    def test_env_var_returns_host(self):
        """With LAST30DAYS_YOUTUBE_SSH_HOST set, _ytdlp_ssh_host returns it."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        self.assertEqual(youtube_yt._ytdlp_ssh_host(), "macmini")

    def test_env_var_whitespace_stripped(self):
        """Whitespace around the host alias is stripped."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "  macmini  "
        self.assertEqual(youtube_yt._ytdlp_ssh_host(), "macmini")

    def test_empty_env_var_falls_back_to_none(self):
        """An empty env var is treated as unset."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = ""
        self.assertIsNone(youtube_yt._ytdlp_ssh_host())

    def test_wrap_cmd_passthrough_when_unset(self):
        """_wrap_ytdlp_cmd returns input unchanged when SSH routing is off."""
        cmd = ["yt-dlp", "--ignore-config", "ytsearch5:test"]
        self.assertEqual(youtube_yt._wrap_ytdlp_cmd(cmd), cmd)

    def test_wrap_cmd_prepends_ssh_when_set(self):
        """_wrap_ytdlp_cmd prepends ssh <host> when SSH routing is on."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        cmd = ["yt-dlp", "--ignore-config", "ytsearch5:test"]
        wrapped = youtube_yt._wrap_ytdlp_cmd(cmd)
        self.assertEqual(wrapped[0], "ssh")
        self.assertEqual(wrapped[1], "-o")
        self.assertEqual(wrapped[2], "BatchMode=yes")
        # `--` terminates SSH option parsing so a host starting with `-`
        # (e.g. `-oProxyCommand=...`) cannot be reinterpreted as a flag.
        self.assertEqual(wrapped[3], "--")
        self.assertEqual(wrapped[4], "macmini")
        # Final arg is the shell-quoted command string
        self.assertIn("yt-dlp", wrapped[5])
        self.assertIn("ytsearch5:test", wrapped[5])

    def test_wrap_cmd_quotes_args_with_spaces(self):
        """Args containing spaces or special chars are shell-quoted."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        cmd = ["yt-dlp", "ytsearch5:hello world", "--dump-json"]
        wrapped = youtube_yt._wrap_ytdlp_cmd(cmd)
        # shlex.quote wraps the whole arg in single quotes when it contains spaces
        self.assertIn("'ytsearch5:hello world'", wrapped[5])

    def test_wrap_cmd_uses_option_terminator(self):
        """`--` is inserted before host as defense-in-depth even for valid hosts."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        cmd = ["yt-dlp", "--version"]
        wrapped = youtube_yt._wrap_ytdlp_cmd(cmd)
        dash_idx = wrapped.index("--")
        self.assertEqual(wrapped[dash_idx + 1], "macmini")

    def test_host_alias_with_dash_prefix_is_rejected(self):
        """A host value starting with `-` is rejected by the alias validator.

        Without validation, ssh could parse `-oProxyCommand=...` as a flag
        instead of a hostname. The `--` terminator in _wrap_ytdlp_cmd is
        defense-in-depth; this regex on _ytdlp_ssh_host() rejects the value
        before it ever reaches the ssh command line.
        """
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "-oProxyCommand=evil"
        self.assertIsNone(youtube_yt._ytdlp_ssh_host())
        # And the wrap function falls back to the local-execution path.
        cmd = ["yt-dlp", "--version"]
        self.assertEqual(youtube_yt._wrap_ytdlp_cmd(cmd), cmd)

    def test_host_alias_with_shell_metacharacters_is_rejected(self):
        """Host values containing spaces, semicolons, $, etc. are rejected."""
        for bad in ("host;rm -rf /", "host name", "host$IFS", "host`whoami`", "host&cmd"):
            os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = bad
            self.assertIsNone(
                youtube_yt._ytdlp_ssh_host(),
                msg=f"validator should reject {bad!r}",
            )

    def test_host_alias_validator_accepts_realistic_aliases(self):
        """Valid SSH config aliases are accepted: bare names, FQDNs, IPs."""
        for good in ("macmini", "home-server", "pi5.local", "192.168.1.10", "homelab_box"):
            os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = good
            self.assertEqual(youtube_yt._ytdlp_ssh_host(), good)

    def test_is_ytdlp_installed_short_circuits_with_ssh(self):
        """is_ytdlp_installed returns True without local check when SSH routing is on."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        with mock.patch("lib.youtube_yt.shutil.which", return_value=None) as which_mock:
            self.assertTrue(youtube_yt.is_ytdlp_installed())
            which_mock.assert_not_called()

    def test_is_ytdlp_installed_falls_through_without_ssh(self):
        """is_ytdlp_installed checks PATH normally when SSH routing is off."""
        with mock.patch("lib.youtube_yt.shutil.which", return_value="/usr/bin/yt-dlp"):
            self.assertTrue(youtube_yt.is_ytdlp_installed())
        with mock.patch("lib.youtube_yt.shutil.which", return_value=None):
            self.assertFalse(youtube_yt.is_ytdlp_installed())

    def test_search_call_routes_through_ssh(self):
        """search_youtube wraps the yt-dlp invocation when SSH routing is on."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        from lib.subproc import SubprocResult
        fake_result = SubprocResult(returncode=0, stdout="", stderr="")
        with mock.patch.object(youtube_yt.subproc, "run_with_timeout",
                               return_value=fake_result) as run_mock:
            youtube_yt.search_youtube("test", "2026-02-01", "2026-03-01")
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[3], "--")
        self.assertEqual(cmd[4], "macmini")
        # The shell-quoted yt-dlp invocation lives at index 5
        self.assertIn("yt-dlp", cmd[5])
        self.assertIn("--ignore-config", cmd[5])
        self.assertIn("--no-cookies-from-browser", cmd[5])

    def test_search_surfaces_ssh_failure_as_error(self):
        """SSH connection failures surface as an error, not silent '0 results'."""
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        from lib.subproc import SubprocResult
        fake_result = SubprocResult(
            returncode=255,
            stdout="",
            stderr="ssh: connect to host macmini port 22: Connection refused\n",
        )
        with mock.patch.object(youtube_yt.subproc, "run_with_timeout",
                               return_value=fake_result):
            out = youtube_yt.search_youtube("test", "2026-02-01", "2026-03-01")
        self.assertIn("error", out)
        self.assertIn("Connection refused", out["error"])


class TestTranscriptSSHRouting(unittest.TestCase):
    """LAST30DAYS_YOUTUBE_SSH_HOST routes yt-dlp transcript fetches through SSH."""

    def setUp(self):
        self._saved_env = os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)

    def tearDown(self):
        os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)
        if self._saved_env is not None:
            os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = self._saved_env

    def test_ssh_helper_invokes_remote_mktemp_pipeline(self):
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        from lib.subproc import SubprocResult
        fake_vtt = "WEBVTT\nKind: captions\nLanguage: en\n\n00:00:00.000 --> 00:00:02.000\nhi\n"
        fake_result = SubprocResult(returncode=0, stdout=fake_vtt, stderr="")
        with mock.patch.object(youtube_yt.subproc, "run_with_timeout",
                               return_value=fake_result) as run_mock:
            out = youtube_yt._fetch_transcript_ytdlp_via_ssh("vid1", "macmini")
        self.assertEqual(out, fake_vtt)
        remote_script = run_mock.call_args.args[0][5]
        self.assertIn("mktemp -d", remote_script)
        self.assertIn("find ", remote_script)

    def test_fetch_transcript_uses_ssh_helper_when_routing_on(self):
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = "macmini"
        fake_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there friends\n"
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp_via_ssh",
                               return_value=fake_vtt) as ssh_mock, \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp") as local_mock, \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct") as direct_mock:
            result = youtube_yt.fetch_transcript("vidX", "/tmp/test")
        ssh_mock.assert_called_once_with("vidX", "macmini")
        local_mock.assert_not_called()
        direct_mock.assert_not_called()
        self.assertIn("hello there friends", result)


class TestScTranscriptFallback(unittest.TestCase):
    """ScrapeCreators fallback wiring in fetch_transcript (U1/U2)."""

    def _ytdlp_hard_fail(self, reason="HTTP Error 429: Too Many Requests"):
        def _fake(video_id, temp_dir, status=None, fast_fail=False):
            if status is not None:
                status["ytdlp_error"] = reason
            return None
        return _fake

    def test_sc_fallback_fires_on_ytdlp_hard_failure_with_token(self):
        """A yt-dlp hard failure (429) with a key falls back to ScrapeCreators."""
        status = {}
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp",
                               side_effect=self._ytdlp_hard_fail()), \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct") as direct_mock, \
             mock.patch.object(youtube_yt, "_sc_fetch_transcript",
                               return_value="scrapecreators transcript text") as sc_mock:
            result = youtube_yt.fetch_transcript("vidA", "/tmp/x", status=status, token="key123")
        sc_mock.assert_called_once_with("vidA", "key123")
        direct_mock.assert_not_called()  # hard error skips the (also-blocked) direct path
        self.assertEqual(result, "scrapecreators transcript text")

    def test_sc_not_called_when_ytdlp_succeeds(self):
        """No credit is spent when yt-dlp returns a transcript."""
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp",
                               return_value="WEBVTT\n\nreal captions here"), \
             mock.patch.object(youtube_yt, "_sc_fetch_transcript") as sc_mock:
            result = youtube_yt.fetch_transcript("vidB", "/tmp/x", status={}, token="key123")
        sc_mock.assert_not_called()
        self.assertIn("real captions", result)

    def test_sc_not_called_without_token(self):
        """Keyless behavior unchanged: no token means no ScrapeCreators."""
        status = {}
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp",
                               side_effect=self._ytdlp_hard_fail()), \
             mock.patch.object(youtube_yt, "_sc_fetch_transcript") as sc_mock:
            result = youtube_yt.fetch_transcript("vidC", "/tmp/x", status=status, token=None)
        sc_mock.assert_not_called()
        self.assertIsNone(result)

    def test_sc_skipped_when_proven_captionless(self):
        """A video proven to have no caption track must not spend a credit."""
        def _ytdlp_no_captions(video_id, temp_dir, status=None, fast_fail=False):
            return None  # exit-0 no captions: returns None, no ytdlp_error

        def _direct_no_tracks(video_id, status=None):
            if status is not None:
                status["no_caption_tracks"] = True
            return None

        status = {}
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp", side_effect=_ytdlp_no_captions), \
             mock.patch.object(youtube_yt, "_fetch_transcript_direct", side_effect=_direct_no_tracks), \
             mock.patch.object(youtube_yt, "_sc_fetch_transcript") as sc_mock:
            result = youtube_yt.fetch_transcript("vidD", "/tmp/x", status=status, token="key123")
        sc_mock.assert_not_called()
        self.assertIsNone(result)

    def test_should_try_sc_transcript_predicate(self):
        self.assertTrue(youtube_yt._should_try_sc_transcript(None))
        self.assertTrue(youtube_yt._should_try_sc_transcript({}))
        self.assertTrue(youtube_yt._should_try_sc_transcript({"ytdlp_error": "429"}))
        self.assertFalse(youtube_yt._should_try_sc_transcript({"no_caption_tracks": True}))

    def test_token_threads_through_parallel(self):
        """fetch_transcripts_parallel passes the token to every fetch_transcript."""
        captured = {}

        def _fake_fetch_transcript(video_id, temp_dir, status=None, token=None):
            captured[video_id] = token
            return None

        with mock.patch.object(youtube_yt, "fetch_transcript", side_effect=_fake_fetch_transcript):
            youtube_yt.fetch_transcripts_parallel(["v1", "v2"], token="tok")
        self.assertEqual(captured, {"v1": "tok", "v2": "tok"})


class TestYtdlpFastFail(unittest.TestCase):
    """Fail-fast behavior when a ScrapeCreators key is present (U3)."""

    def _transient_fail(self):
        from lib.subproc import SubprocResult
        return SubprocResult(
            returncode=1, stdout="",
            stderr="ERROR: HTTP Error 429: Too Many Requests",
        )

    def test_fast_fail_single_attempt_short_timeout(self):
        """token present -> one attempt, shortened timeout, no retry sleeps."""
        with tempfile.TemporaryDirectory() as temp_dir:
            status = {}
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout",
                                   return_value=self._transient_fail()) as run_mock, \
                 mock.patch.object(youtube_yt.time, "sleep") as sleep_mock:
                vtt = youtube_yt._fetch_transcript_ytdlp("vidF", temp_dir, status, fast_fail=True)
        self.assertIsNone(vtt)
        self.assertEqual(run_mock.call_count, 1)  # no retries
        self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 12)
        sleep_mock.assert_not_called()
        self.assertIn("ytdlp_error", status)

    def test_no_token_retries_with_full_timeout(self):
        """token absent -> full retry budget and 30s timeout, unchanged."""
        with tempfile.TemporaryDirectory() as temp_dir:
            status = {}
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout",
                                   return_value=self._transient_fail()) as run_mock, \
                 mock.patch.object(youtube_yt.time, "sleep"):
                vtt = youtube_yt._fetch_transcript_ytdlp("vidG", temp_dir, status, fast_fail=False)
        self.assertIsNone(vtt)
        self.assertEqual(run_mock.call_count, youtube_yt._TRANSCRIPT_MAX_RETRIES + 1)
        self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 30)


class TestScTranscriptParsing(unittest.TestCase):
    """ScrapeCreators transcript parse + credits warning (U4)."""

    def test_list_of_dict_segments_parsed_to_text(self):
        payload = {
            "transcript": [
                {"text": "hello there", "startMs": 0, "endMs": 1000},
                {"text": "general kenobi", "startMs": 1000, "endMs": 2000},
            ],
            "credits_remaining": 9999,
        }
        with mock.patch.object(youtube_yt.http, "get", return_value=payload):
            result = youtube_yt._sc_fetch_transcript("vidH", "key")
        self.assertIsNotNone(result)
        self.assertIn("hello there", result)
        self.assertIn("general kenobi", result)
        self.assertNotIn("startMs", result)
        self.assertNotIn("{'text'", result)

    def test_null_text_segment_does_not_emit_none(self):
        """A present-but-null text field (silent/music segment) must not become "None"."""
        payload = {
            "transcript": [
                {"text": "real words here", "startMs": 0},
                {"text": None, "startMs": 1000},
                {"text": "more real words", "startMs": 2000},
            ],
            "credits_remaining": 9999,
        }
        with mock.patch.object(youtube_yt.http, "get", return_value=payload):
            result = youtube_yt._sc_fetch_transcript("vidNull", "key")
        self.assertIsNotNone(result)
        self.assertNotIn("None", result)
        self.assertIn("real words here", result)
        self.assertIn("more real words", result)

    def test_plain_string_transcript_preserved(self):
        payload = {"transcript": "just a plain transcript string here", "credits_remaining": 9999}
        with mock.patch.object(youtube_yt.http, "get", return_value=payload):
            result = youtube_yt._sc_fetch_transcript("vidI", "key")
        self.assertIn("just a plain transcript", result)

    def test_low_credits_emits_warning(self):
        payload = {"transcript": "some transcript text", "credits_remaining": 5}
        logs = []
        with mock.patch.object(youtube_yt.http, "get", return_value=payload), \
             mock.patch.object(youtube_yt, "_log", side_effect=lambda m: logs.append(m)):
            youtube_yt._sc_fetch_transcript("vidJ", "key")
        self.assertTrue(any("credits low" in m.lower() for m in logs))

    def test_healthy_credits_no_warning(self):
        payload = {"transcript": "some transcript text", "credits_remaining": 9999}
        logs = []
        with mock.patch.object(youtube_yt.http, "get", return_value=payload), \
             mock.patch.object(youtube_yt, "_log", side_effect=lambda m: logs.append(m)):
            youtube_yt._sc_fetch_transcript("vidK", "key")
        self.assertFalse(any("credits low" in m.lower() for m in logs))


class TestYoutubeCommentsGating(unittest.TestCase):
    """YouTube comments are opt-in via INCLUDE_SOURCES (Everything tier)."""

    def test_off_with_key_and_no_include_sources(self):
        """Recommended tier (key, no INCLUDE_SOURCES) does NOT fetch comments."""
        from lib import env
        self.assertFalse(env.is_youtube_comments_available({"SCRAPECREATORS_API_KEY": "k"}))

    def test_on_with_include_sources(self):
        from lib import env
        cfg = {"SCRAPECREATORS_API_KEY": "k", "INCLUDE_SOURCES": "youtube_comments"}
        self.assertTrue(env.is_youtube_comments_available(cfg))

    def test_unavailable_without_key(self):
        from lib import env
        self.assertFalse(env.is_youtube_comments_available({"INCLUDE_SOURCES": "youtube_comments"}))

    def test_tiktok_comments_still_opt_in(self):
        """Regression: TikTok comments must STILL require INCLUDE_SOURCES."""
        from lib import env
        self.assertFalse(
            env.is_tiktok_comments_available({"SCRAPECREATORS_API_KEY": "k"})
        )
        self.assertTrue(env.is_tiktok_comments_available(
            {"SCRAPECREATORS_API_KEY": "k", "INCLUDE_SOURCES": "tiktok_comments"}
        ))


if __name__ == "__main__":
    unittest.main()
