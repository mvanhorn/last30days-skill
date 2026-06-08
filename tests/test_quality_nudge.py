"""Tests for post-research quality score and upgrade nudge.

Reddit is always a core source (free public JSON). The 5 core sources are:
HN, Polymarket, Reddit (always active), X, YouTube.
ScrapeCreators adds TikTok + Instagram as bonus sources, not core.
"""

import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_config(**overrides):
    """Return a minimal config dict."""
    config = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "XAI_API_KEY": None,
        "SCRAPECREATORS_API_KEY": None,
    }
    config.update(overrides)
    return config


def _base_results(**overrides):
    """Return a minimal research_results dict with no errors."""
    results = {
        "x_error": None,
        "youtube_error": None,
        "reddit_error": None,
    }
    results.update(overrides)
    return results


def _compute(config_overrides=None, result_overrides=None, ytdlp_installed=False):
    """Helper to call compute_quality_score with mocked yt-dlp check."""
    from lib.quality_nudge import compute_quality_score
    from lib import youtube_yt

    config = _base_config(**(config_overrides or {}))
    results = _base_results(**(result_overrides or {}))

    with patch.object(youtube_yt, "is_ytdlp_installed", return_value=ytdlp_installed):
        return compute_quality_score(config, results)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBaseline:
    """HN + Polymarket + Reddit always active (no X, no YT) -> 60%."""

    def test_score_60(self):
        q = _compute()
        assert q["score_pct"] == 60

    def test_active_sources(self):
        q = _compute()
        assert "hn" in q["core_active"]
        assert "polymarket" in q["core_active"]
        assert "reddit" in q["core_active"]
        assert len(q["core_active"]) == 3

    def test_missing_x_and_youtube(self):
        q = _compute()
        assert set(q["core_missing"]) == {"x", "youtube"}

    def test_reddit_not_in_missing(self):
        """Reddit is always active - never appears in missing."""
        q = _compute()
        assert "reddit" not in q["core_missing"]
        assert "reddit_comments" not in q["core_missing"]

    def test_nudge_mentions_x_and_youtube(self):
        q = _compute()
        assert q["nudge_text"] is not None
        assert "X/Twitter" in q["nudge_text"]
        assert "YouTube" in q["nudge_text"]

    def test_nudge_does_not_mention_reddit(self):
        """Reddit is free - nudge should not tell user to get SC for it."""
        q = _compute()
        assert "Reddit with comments" not in q["nudge_text"]


class TestXCookies:
    """+X cookies -> 80%."""

    def test_score_80(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert q["score_pct"] == 80

    def test_nudge_mentions_yt_only(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert "YouTube" in q["nudge_text"]
        assert "X/Twitter" not in q["nudge_text"]


class TestXPlusYtdlp:
    """+X + yt-dlp -> 100%. No SC needed for full core coverage."""

    def test_score_100(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
        )
        assert q["score_pct"] == 100

    def test_nudge_is_none(self):
        """Full core coverage with zero paid keys."""
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is None


class TestFullCoverageWithSC:
    """+X + yt-dlp + SC -> still 100%, SC adds bonus sources."""

    def test_score_100(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
        )
        assert q["score_pct"] == 100

    def test_nudge_is_none(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is None


class TestSCDoesNotAffectCoreScore:
    """SC key should not change core score - it only adds bonus sources."""

    def test_sc_alone_still_60(self):
        """SC key without X or yt-dlp is still 60% (3/5 core)."""
        q = _compute(config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"})
        assert q["score_pct"] == 60

    def test_sc_plus_ytdlp_is_80(self):
        q = _compute(
            config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"},
            ytdlp_installed=True,
        )
        assert q["score_pct"] == 80

    def test_nudge_suggests_browser_cookies(self):
        q = _compute(
            config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is not None
        assert "x.com" in q["nudge_text"].lower()


class TestDisclaimerAlwaysPresent:
    """Nudge always includes no-affiliate disclaimer when present."""

    def test_disclaimer_baseline(self):
        q = _compute()
        assert "no affiliation" in q["nudge_text"]

    def test_disclaimer_partial(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert "no affiliation" in q["nudge_text"]

    def test_disclaimer_not_present_at_100(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is None


class TestRedditNeverInCoreErrored:
    """Reddit errors don't affect core score since it's always-active via public path."""

    def test_reddit_error_does_not_affect_score(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"reddit_error": "429 Too Many Requests"},
            ytdlp_installed=True,
        )
        # Reddit is always-active in core (public path), error doesn't demote it
        assert "reddit" in q["core_active"]
        assert q["score_pct"] == 100


class TestYouTubeDegraded:
    """YouTube is `degraded` when videos returned but transcripts below threshold.

    Canonical failure mode: a stale yt-dlp binary still finds videos via search
    but silently fails every transcript fetch because YouTube's caption format
    has moved on. Pre-fix the user got no signal of this; the footer hid zero,
    and quality_nudge only checked top-level errors.
    """

    def test_zero_of_six_transcripts_flags_degraded(self):
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
            },
        )
        assert "youtube" in q["core_degraded"]
        assert q["nudge_text"] is not None
        # Counts surface in the message so the user sees the actual ratio
        assert "6 videos" in q["nudge_text"]
        assert "0 transcripts" in q["nudge_text"]
        assert "stale yt-dlp" in q["nudge_text"].lower()
        # Updates path mentions all three common package managers
        assert "scoop" in q["nudge_text"].lower()
        assert "brew" in q["nudge_text"].lower()
        assert "pip install" in q["nudge_text"].lower()

    def test_five_of_six_transcripts_does_not_flag_degraded(self):
        # 83% transcript success - well above the 50% threshold
        # X is also enabled so all 5 cores are active and no nudge should fire
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 5,
            },
        )
        assert "youtube" not in q["core_degraded"]
        assert q["nudge_text"] is None  # All 5 core sources active, no degradation

    def test_zero_videos_does_not_flag_degraded(self):
        # No videos returned -> degraded check is meaningless and must not fire
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 0,
                "youtube_transcripts_count": 0,
            },
        )
        assert "youtube" not in q["core_degraded"]

    def test_one_of_three_transcripts_flags_degraded(self):
        # 33% - below 50% threshold; the canonical "yt-dlp partially working" case
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 3,
                "youtube_transcripts_count": 1,
            },
        )
        assert "youtube" in q["core_degraded"]
        assert "Degraded: YouTube" in q["nudge_text"]

    def test_threshold_tunable_via_config(self):
        # Operator overrides threshold via env-style config to be more permissive
        q = _compute(
            config_overrides={"DEGRADED_TRANSCRIPT_THRESHOLD": "0.1"},
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 10,
                "youtube_transcripts_count": 2,  # 20%, below default 50% but above override 10%
            },
        )
        assert "youtube" not in q["core_degraded"]

    def test_degraded_does_not_affect_score(self):
        # Degradation is informational, not score-affecting; YouTube still counts as active
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
            },
        )
        assert "youtube" in q["core_active"]
        assert q["score_pct"] == 100  # Full active count regardless of degradation
        # But nudge still fires
        assert q["nudge_text"] is not None
        assert "Degraded: YouTube" in q["nudge_text"]


class TestYouTubeCaptionsDisabledDoesNotFalseFlag:
    """Captions-disabled videos must not lower the transcript-fetch ratio.

    A video where the uploader disabled captions can never produce a transcript,
    no matter how fresh yt-dlp is. Counting it in the denominator of the
    degraded-ratio check produces false positives - one captions-disabled video
    in a small result set was triggering a "stale yt-dlp binary" nudge that was
    wrong. Fix: subtract captions_disabled from the denominator.
    """

    def test_zero_captions_disabled_preserves_existing_behavior(self):
        # Pre-existing case: 0 of 6 transcripts is still degraded (no captions
        # disabled to discount). Behavior is unchanged from TestYouTubeDegraded.
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 0,
            },
        )
        assert "youtube" in q["core_degraded"]

    def test_all_videos_captions_disabled_does_not_flag(self):
        # Every returned video had captions disabled by the uploader.
        # That's not a yt-dlp problem - it's an upstream content fact. Must not
        # flag degraded.
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 3,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 3,
            },
        )
        assert "youtube" not in q["core_degraded"]

    def test_mixed_uses_corrected_denominator(self):
        # 6 videos, 3 captions_disabled, 2 transcripts.
        # Naive (buggy) ratio: 2/6 = 33% (would flag).
        # Corrected ratio: 2/(6-3) = 67% (does NOT flag).
        # This case demonstrates the fix changes the verdict.
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 2,
                "youtube_captions_disabled_count": 3,
            },
        )
        assert "youtube" not in q["core_degraded"]

    def test_mixed_still_flags_when_truly_degraded(self):
        # Even after discounting captions-disabled, the ratio is still bad.
        # 8 videos, 1 captions_disabled, 1 transcript -> 1/(8-1) = 14% (flags).
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 8,
                "youtube_transcripts_count": 1,
                "youtube_captions_disabled_count": 1,
            },
        )
        assert "youtube" in q["core_degraded"]
        # Nudge should still mention the stale yt-dlp possibility but also
        # acknowledge that captions-disabled is a separate cause.
        assert q["nudge_text"] is not None
        assert "captions disabled" in q["nudge_text"].lower()

    def test_missing_count_defaults_to_zero(self):
        # Older callers that don't pass the new key still work (default 0).
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
                # youtube_captions_disabled_count intentionally omitted
            },
        )
        assert "youtube" in q["core_degraded"]


class TestInstagramSilentFailure:
    """Instagram is a `bonus` source via SC. Silent-failure detection: if SC
    is configured but the source returned zero items, surface a nudge so the
    user understands why the brief lacks an Instagram section.

    Pre-fix the user got no signal - SC's /v2/instagram/reels/search 500s
    frequently on multi-token queries and the pipeline silently returned
    empty without any indication.
    """

    def test_zero_items_with_sc_flags_bonus_errored(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" in q["bonus_errored"]
        assert q["nudge_text"] is not None
        assert "Instagram" in q["nudge_text"]

    def test_zero_items_without_sc_does_not_flag(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" not in q.get("bonus_errored", [])

    def test_nonzero_items_does_not_flag(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 5},
        )
        assert "instagram" not in q["bonus_errored"]
        assert q["nudge_text"] is None

    def test_missing_key_means_source_did_not_run(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
        )
        assert "instagram" not in q["bonus_errored"]
        assert q["nudge_text"] is None

    def test_nudge_text_explains_workaround(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert q["nudge_text"] is not None
        text_lower = q["nudge_text"].lower()
        assert "instagram" in text_lower
        assert ("0 reels" in text_lower or "silent" in text_lower
                or "hashtag" in text_lower)

    def test_bonus_errored_does_not_affect_core_score(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert q["score_pct"] == 100
        assert "instagram" in q["bonus_errored"]
        assert q["nudge_text"] is not None
        assert "Bonus source silent" in q["nudge_text"]

    def test_bonus_errored_field_always_present(self):
        q = _compute()
        assert q.get("bonus_errored") == []

    def test_exclude_sources_instagram_suppresses_silent_failure(self):
        """User set EXCLUDE_SOURCES=instagram - the source intentionally did
        not run, so the zero-count instagram_items_count written by
        last30days.py is a non-event, not a silent failure. Pre-fix: the
        nudge fired anyway because the gate only checked SC-key + count.
        """
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "EXCLUDE_SOURCES": "instagram",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" not in q["bonus_errored"]
        assert q["nudge_text"] is None

    def test_exclude_sources_multi_value_with_instagram(self):
        """Canonical parsing pattern is comma-separated; case-insensitive."""
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "EXCLUDE_SOURCES": "threads, Instagram , pinterest",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" not in q["bonus_errored"]

    def test_exclude_sources_other_value_still_flags(self):
        """EXCLUDE_SOURCES that does not mention instagram must not suppress
        the silent-failure nudge for instagram.
        """
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "EXCLUDE_SOURCES": "threads",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" in q["bonus_errored"]

    def test_include_sources_without_instagram_suppresses_silent_failure(self):
        """User set INCLUDE_SOURCES to an opt-in allowlist that omits
        instagram — the pipeline skips the source by allowlist filter, so
        the zero-count instagram_items_count is intentional, not a silent
        failure. Symmetric to the EXCLUDE_SOURCES=instagram guard.
        """
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "INCLUDE_SOURCES": "reddit,hn,x,youtube",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" not in q["bonus_errored"]
        assert q["nudge_text"] is None

    def test_include_sources_multi_value_without_instagram(self):
        """Canonical parsing pattern is comma-separated; case-insensitive."""
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "INCLUDE_SOURCES": " Reddit, HN , YouTube ",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" not in q["bonus_errored"]

    def test_include_sources_with_instagram_still_flags(self):
        """INCLUDE_SOURCES that explicitly names instagram must not suppress
        the silent-failure nudge — the source was opted in, so a zero count
        is a real silent failure.
        """
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "INCLUDE_SOURCES": "reddit,instagram",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" in q["bonus_errored"]

    def test_include_sources_empty_does_not_suppress(self):
        """Empty/unset INCLUDE_SOURCES means no allowlist filter, so the
        silent-failure gate should still fire when instagram is zero.
        """
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
                "INCLUDE_SOURCES": "",
            },
            ytdlp_installed=True,
            result_overrides={"instagram_items_count": 0},
        )
        assert "instagram" in q["bonus_errored"]

