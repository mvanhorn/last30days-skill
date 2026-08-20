"""YouTube comments via yt-dlp: the free, keyless path.

ScrapeCreators used to be the only way to get YouTube comments. yt-dlp already
powers YouTube search and transcripts here and can fetch comments too, so the
comment lane no longer needs a paid key. These tests lock in that yt-dlp is
preferred, that ScrapeCreators still works as a fallback, and that a missing
key is no longer fatal.
"""

import json
import unittest
from unittest import mock

from lib import env, youtube_yt
from lib.subproc import SubprocResult


def _ytdlp_payload(comments):
    """A yt-dlp --dump-single-json blob carrying `comments`."""
    return json.dumps({"id": "abc123", "title": "vid", "comments": comments})


# yt-dlp's real comment shape, as emitted by --write-comments.
_RAW = [
    {
        "author": "@BestFlorin",
        "text": "Trump said the Hormuz strait is open",
        "like_count": 11,
        "_time_text": "2 days ago",
    },
    {
        "author": "@princem4006",
        "text": "The U.S. cannot be trusted here",
        "like_count": 7,
        "_time_text": "1 day ago",
    },
]


class TestFetchViaYtdlp(unittest.TestCase):
    def test_parses_ytdlp_comments_into_canonical_shape(self):
        """yt-dlp's like_count/_time_text map onto the engine's likes/date."""
        result = SubprocResult(returncode=0, stdout=_ytdlp_payload(_RAW), stderr="")
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=result):
            got = youtube_yt._fetch_video_comments_ytdlp("abc123", max_comments=5)

        self.assertEqual(2, len(got))
        self.assertEqual(
            {
                "author": "@BestFlorin",
                "text": "Trump said the Hormuz strait is open",
                "likes": 11,
                "date": "2 days ago",
            },
            got[0],
        )

    def test_honors_max_comments(self):
        result = SubprocResult(returncode=0, stdout=_ytdlp_payload(_RAW), stderr="")
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=result):
            got = youtube_yt._fetch_video_comments_ytdlp("abc123", max_comments=1)

        self.assertEqual(1, len(got))

    def test_returns_empty_when_ytdlp_not_installed(self):
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False):
            self.assertEqual([], youtube_yt._fetch_video_comments_ytdlp("abc123"))

    def test_returns_empty_on_ytdlp_failure(self):
        """A non-zero exit is a fetch error, not an empty comment section."""
        result = SubprocResult(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=result):
            self.assertEqual([], youtube_yt._fetch_video_comments_ytdlp("abc123"))

    def test_command_requests_top_sorted_comments(self):
        """Lock the command: top-sort and the max_comments cap must be present,
        or a refactor could silently return arbitrary (newest) comments."""
        result = SubprocResult(returncode=0, stdout=_ytdlp_payload(_RAW), stderr="")
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=result) as run:
            youtube_yt._fetch_video_comments_ytdlp("abc123", max_comments=4)

        cmd = run.call_args.args[0]
        joined = " ".join(cmd)
        self.assertIn("--write-comments", cmd)
        self.assertIn("comment_sort=top", joined)
        self.assertIn("max_comments=4", joined)
        self.assertTrue(any("watch?v=abc123" in a for a in cmd))


class TestBackendPreference(unittest.TestCase):
    def test_prefers_ytdlp_and_never_calls_scrapecreators(self):
        """The free path wins: no SC credit is spent when yt-dlp delivers."""
        with mock.patch.object(
            youtube_yt,
            "_ytdlp_comments_result",
            return_value=([{"author": "a", "text": "t", "likes": 1, "date": ""}], True),
        ), mock.patch.object(youtube_yt.http, "get") as sc_get:
            got = youtube_yt._fetch_video_comments("abc123", token="sk-live", max_comments=5)

        self.assertEqual(1, len(got))
        sc_get.assert_not_called()

    def test_falls_back_to_scrapecreators_when_ytdlp_fails(self):
        """SC remains the backstop when yt-dlp is missing or throttled."""
        sc_payload = {"comments": [{"text": "from SC", "author": {"name": "@x"}, "likes": 3}]}
        with mock.patch.object(youtube_yt, "_ytdlp_comments_result", return_value=([], False)), \
             mock.patch.object(youtube_yt.http, "get", return_value=sc_payload) as sc_get:
            got = youtube_yt._fetch_video_comments("abc123", token="sk-live", max_comments=5)

        sc_get.assert_called_once()
        self.assertEqual("from SC", got[0]["text"])

    def test_no_token_and_ytdlp_failure_yields_no_comments_without_calling_sc(self):
        with mock.patch.object(youtube_yt, "_ytdlp_comments_result", return_value=([], False)), \
             mock.patch.object(youtube_yt.http, "get") as sc_get:
            got = youtube_yt._fetch_video_comments("abc123", token="", max_comments=5)

        self.assertEqual([], got)
        sc_get.assert_not_called()

    def test_no_sc_fallback_when_ytdlp_succeeds_with_zero_comments(self):
        """A video that genuinely has no comments must not burn an SC credit.
        yt-dlp exit 0 + empty comments is success, not a throttle to retry."""
        ok_empty = SubprocResult(returncode=0, stdout=_ytdlp_payload([]), stderr="")
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=ok_empty), \
             mock.patch.object(youtube_yt.http, "get") as sc_get:
            got = youtube_yt._fetch_video_comments("abc123", token="sk-live", max_comments=5)

        self.assertEqual([], got)
        sc_get.assert_not_called()

    def test_sc_fallback_fires_when_ytdlp_actually_fails(self):
        """A non-zero exit is a real failure -> SC backstop should still fire."""
        failed = SubprocResult(returncode=1, stdout="", stderr="throttled")
        sc_payload = {"comments": [{"text": "from SC", "author": {"name": "@x"}, "likes": 3}]}
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=failed), \
             mock.patch.object(youtube_yt.http, "get", return_value=sc_payload) as sc_get:
            got = youtube_yt._fetch_video_comments("abc123", token="sk-live", max_comments=5)

        sc_get.assert_called_once()
        self.assertEqual("from SC", got[0]["text"])


class TestEnrichWithoutKey(unittest.TestCase):
    def test_enriches_with_empty_token_when_ytdlp_available(self):
        """A missing ScrapeCreators key must no longer disable comments."""
        items = [{"video_id": "abc123", "engagement": {"views": 100}}]
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(
                 youtube_yt,
                 "_fetch_video_comments",
                 return_value=[{"author": "@a", "text": "hi", "likes": 2, "date": ""}],
             ):
            youtube_yt.enrich_with_comments(items, token="")

        self.assertEqual("hi", items[0]["top_comments"][0]["text"])

    def test_noop_with_no_token_and_no_ytdlp(self):
        items = [{"video_id": "abc123", "engagement": {"views": 100}}]
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False):
            youtube_yt.enrich_with_comments(items, token="")

        self.assertNotIn("top_comments", items[0])


class TestAvailabilityGate(unittest.TestCase):
    def test_available_without_sc_key_when_ytdlp_installed(self):
        """Comments are free now, so no key and no opt-in should be required."""
        with mock.patch.object(env, "is_ytdlp_available", return_value=True):
            self.assertTrue(env.is_youtube_comments_available({}))

    def test_sc_path_still_available_when_ytdlp_missing(self):
        cfg = {
            "SCRAPECREATORS_API_KEY": "sk-live",
            "INCLUDE_SOURCES": "youtube_comments",
        }
        with mock.patch.object(env, "is_ytdlp_available", return_value=False):
            self.assertTrue(env.is_youtube_comments_available(cfg))

    def test_unavailable_with_no_ytdlp_and_no_key(self):
        with mock.patch.object(env, "is_ytdlp_available", return_value=False):
            self.assertFalse(env.is_youtube_comments_available({}))

    def test_exclude_sources_still_suppresses_the_free_path(self):
        """Comments going default-on must not defeat the documented off-switch."""
        cfg = {"EXCLUDE_SOURCES": "youtube_comments"}
        with mock.patch.object(env, "is_ytdlp_available", return_value=True):
            self.assertFalse(env.is_youtube_comments_available(cfg))


if __name__ == "__main__":
    unittest.main()
