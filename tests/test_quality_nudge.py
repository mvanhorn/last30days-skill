"""Tests for post-research quality score and upgrade nudge.

Reddit is always a core source (free public JSON). X remains supported when
active, but its absence is optional and must not lower the quality grade or
trigger an authentication nudge.
ScrapeCreators adds TikTok + Instagram as bonus sources, not core.
"""

import threading

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
        "XQUIK_API_KEY": None,
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
    """HN + Polymarket + Reddit active; X omitted and YouTube missing."""

    def test_score_75(self):
        q = _compute()
        assert q["score_pct"] == 75

    def test_active_sources(self):
        q = _compute()
        assert "hn" in q["core_active"]
        assert "polymarket" in q["core_active"]
        assert "reddit" in q["core_active"]
        assert len(q["core_active"]) == 3

    def test_only_youtube_is_missing(self):
        q = _compute()
        assert q["core_missing"] == ["youtube"]
        assert "x" not in q["core_active"]

    def test_reddit_not_in_missing(self):
        """Reddit is always active - never appears in missing."""
        q = _compute()
        assert "reddit" not in q["core_missing"]
        assert "reddit_comments" not in q["core_missing"]

    def test_nudge_mentions_youtube_not_x(self):
        q = _compute()
        assert q["nudge_text"] is not None
        assert "YouTube" in q["nudge_text"]
        assert "X/Twitter" not in q["nudge_text"]

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

    def test_x_remains_active_when_configured(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert "x" in q["core_active"]


class TestXquikKey:
    """+Xquik key -> 80% without browser-cookie or xAI credentials."""

    def test_score_80(self):
        q = _compute(config_overrides={"XQUIK_API_KEY": "xq_test"})
        assert q["score_pct"] == 80
        assert "x" in q["core_active"]

    def test_nudge_mentions_yt_only(self):
        q = _compute(config_overrides={"XQUIK_API_KEY": "xq_test"})
        assert "YouTube" in q["nudge_text"]
        assert "X/Twitter" not in q["nudge_text"]


class TestActiveSourceX:
    """The runtime active-source list preserves X without legacy credentials."""

    def test_active_x_is_counted(self):
        q = _compute(result_overrides={"active_sources": ["reddit", "x", "youtube"]})
        assert "x" in q["core_active"]


class TestConfiguredXErrored:
    """A configured X that errored is a real outage: docked and surfaced,
    never disguised as an optional omission."""

    def test_errored_x_docks_the_score(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"x_error": "401 unauthorized"},
            ytdlp_installed=True,
        )
        assert q["score_pct"] == 80  # 4/5 - X stays in the denominator
        assert q["core_missing"] == ["x"]
        assert q["core_errored"] == ["x"]

    def test_errored_x_nudge_surfaces_the_repair(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"x_error": "401 unauthorized"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is not None
        assert "X/Twitter (errored this run)" in q["nudge_text"]

    def test_runtime_active_x_that_errored_is_also_docked(self):
        q = _compute(
            result_overrides={
                "active_sources": ["reddit", "x", "youtube"],
                "x_error": "429 rate limited",
            },
            ytdlp_installed=True,
        )
        assert q["core_errored"] == ["x"]
        assert q["score_pct"] == 80


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

    def test_sc_alone_still_75(self):
        """SC key without yt-dlp is still 75% of non-optional core."""
        q = _compute(config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"})
        assert q["score_pct"] == 75

    def test_sc_plus_ytdlp_is_100(self):
        q = _compute(
            config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"},
            ytdlp_installed=True,
        )
        assert q["score_pct"] == 100

    def test_no_x_cookie_nudge_after_complete_available_source_run(self):
        q = _compute(
            config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"},
            ytdlp_installed=True,
        )
        assert q["nudge_text"] is None


class TestYouTubeFallbackProvider:
    """YouTube data from fallback/provider paths is degraded, not missing."""

    def test_fallback_youtube_data_without_ytdlp_counts_active_not_missing(self):
        q = _compute(
            config_overrides={
                "AUTH_TOKEN": "tok123",
                "SCRAPECREATORS_API_KEY": "sc_key",
            },
            ytdlp_installed=False,
            result_overrides={
                "youtube_videos_count": 5,
                "youtube_transcripts_count": 4,
            },
        )
        assert q["score_pct"] == 100
        assert "youtube" in q["core_active"]
        assert "youtube" not in q["core_missing"]
        assert "youtube" in q["core_degraded"]
        assert q["nudge_text"] is not None
        assert "Missing: YouTube" not in q["nudge_text"]
        assert "Degraded: YouTube" in q["nudge_text"]
        assert "fallback/provider" in q["nudge_text"]
        assert "local yt-dlp is not installed" in q["nudge_text"]
        assert "stale yt-dlp" not in q["nudge_text"].lower()

    def test_ytdlp_install_check_runs_once_per_score(self):
        from lib.quality_nudge import compute_quality_score
        from lib import youtube_yt

        with patch.object(youtube_yt, "is_ytdlp_installed", return_value=False) as ytdlp_check:
            compute_quality_score(
                _base_config(AUTH_TOKEN="tok123"),
                _base_results(
                    youtube_videos_count=5,
                    youtube_transcripts_count=4,
                ),
            )

        ytdlp_check.assert_called_once()

    def test_no_fallback_data_without_ytdlp_still_missing(self):
        q = _compute(
            config_overrides={"SCRAPECREATORS_API_KEY": "sc_key"},
            ytdlp_installed=False,
            result_overrides={
                "youtube_videos_count": 0,
                "youtube_transcripts_count": 0,
            },
        )
        assert "youtube" in q["core_missing"]
        assert "youtube" not in q["core_active"]
        assert "youtube" not in q["core_degraded"]
        assert "Missing: YouTube" in q["nudge_text"]
        assert "X/Twitter" not in q["nudge_text"]


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


class TestStaleNudgeRequiresActualFetchFailures:
    """Zero failed fetches must suppress the stale-yt-dlp nudge (#531).

    The report counts (youtube_videos_count / youtube_transcripts_count) are
    computed from post-pruning items. A run where every transcript fetch
    succeeded but the fetched videos were later pruned by freshness scoring
    looks identical to a stale-binary run from those counts alone, producing
    a false "stale yt-dlp binary" nudge. When actual fetch outcomes are
    available and show zero failures, the binary demonstrably works.
    """

    def test_zero_failures_does_not_flag(self):
        # The #531 repro: 2 in-report videos, 0 transcripts among them, but
        # all 6 attempted fetches succeeded (on videos pruned later).
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 2,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 0,
                "youtube_transcript_fetch_attempts": 6,
                "youtube_transcript_fetch_failures": 0,
            },
        )
        assert "youtube" not in q["core_degraded"]

    def test_actual_failures_still_flag(self):
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 0,
                "youtube_transcript_fetch_attempts": 6,
                "youtube_transcript_fetch_failures": 6,
            },
        )
        assert "youtube" in q["core_degraded"]

    def test_missing_fetch_stats_preserves_existing_behavior(self):
        # Callers that don't pass fetch stats (or the SC path, which doesn't
        # use the local yt-dlp binary) fall back to the ratio heuristic.
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 0,
            },
        )
        assert "youtube" in q["core_degraded"]

    def test_zero_attempts_preserves_existing_behavior(self):
        q = _compute(
            ytdlp_installed=True,
            result_overrides={
                "youtube_videos_count": 6,
                "youtube_transcripts_count": 0,
                "youtube_captions_disabled_count": 0,
                "youtube_transcript_fetch_attempts": 0,
                "youtube_transcript_fetch_failures": 0,
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


class TestBearerCredential:
    """X_BEARER_TOKEN counts as an X credential where the xapi backend can
    run (a Grok Bot host, or an explicit xapi pin); an ambient bearer on a
    plain host stays what it is today: not a configured X source."""

    def test_bearer_counts_on_grok_bot(self):
        q = _compute(
            config_overrides={"LAST30DAYS_HOST": "grok-bot", "X_BEARER_TOKEN": "dummy-bearer"},
            ytdlp_installed=True,
        )
        assert "x" in q["core_active"]
        assert q["score_pct"] == 100

    def test_bearer_counts_when_xapi_is_pinned(self):
        q = _compute(
            config_overrides={"X_BEARER_TOKEN": "dummy-bearer", "LAST30DAYS_X_BACKEND": "xapi"},
            ytdlp_installed=True,
        )
        assert "x" in q["core_active"]

    def test_ambient_bearer_on_plain_host_is_an_optional_omission(self):
        q = _compute(config_overrides={"X_BEARER_TOKEN": "dummy-bearer"}, ytdlp_installed=True)
        assert "x" not in q["core_active"]
        assert q["core_missing"] == []
        assert q["nudge_text"] is None

    def test_grok_bot_x_error_nudge_names_bearer_never_x_login(self):
        q = _compute(
            config_overrides={"LAST30DAYS_HOST": "grok-bot", "X_BEARER_TOKEN": "dummy-bearer"},
            result_overrides={"x_error": "401 unauthorized"},
            ytdlp_installed=True,
        )
        assert q["core_errored"] == ["x"]
        text = q["nudge_text"].lower()
        assert "x_bearer_token" in text
        for word in ("x.com", "cookie", "bird", "auth_token", "ct0", "xquik", "grok cli", "grok login"):
            assert word not in text, word

    def test_declared_lane_without_envelope_prescribes_the_connector(self):
        from lib import x_envelope
        q = _compute(
            config_overrides={"LAST30DAYS_HOST": "grok-bot"},
            result_overrides={"x_error": x_envelope.DETAIL_NOT_PASSED, "active_sources": ["x"]},
            ytdlp_installed=True,
        )
        assert q["core_errored"] == ["x"]
        text = q["nudge_text"].lower()
        assert "connector" in text
        assert "--x-posts" in text
        for word in ("x.com", "cookie", "bird", "auth_token", "ct0"):
            assert word not in text, word

    def test_grok_bot_credit_error_nudge_says_top_up(self):
        q = _compute(
            config_overrides={"LAST30DAYS_HOST": "grok-bot", "X_BEARER_TOKEN": "dummy-bearer"},
            result_overrides={"x_error": "xapi: payment required (X API credits exhausted)"},
            ytdlp_installed=True,
        )
        assert "top up" in q["nudge_text"].lower()
        assert "x.com" not in q["nudge_text"].lower()


class TestXaiErrorRemediation:
    """The selected API backend must not prescribe a browser-cookie login."""

    @pytest.mark.parametrize("host", [None, "grok-bot"])
    @pytest.mark.parametrize("message", [
        "All X backends failed — xai: HTTP 403: Permission denied",
        "xai: HTTP 401: Unauthorized",
        "All X backends failed — xai: HTTP 404: Model not found",
    ])
    def test_runtime_xai_error_points_to_key_and_model_permissions(self, host, message):
        q = _compute(
            config_overrides={"XAI_API_KEY": "dummy-xai-key", "LAST30DAYS_HOST": host},
            result_overrides={"x_error": message},
            ytdlp_installed=True,
        )
        assert "XAI_API_KEY" in q["nudge_text"]
        assert "LAST30DAYS_X_MODEL" in q["nudge_text"]
        assert "console.x.ai" in q["nudge_text"]
        assert "log into x.com" not in q["nudge_text"]
        assert "X_BEARER_TOKEN" not in q["nudge_text"]

    def test_runtime_bird_error_is_not_changed_by_a_fallback_xai_key(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "dummy-cookie", "CT0": "dummy-ct0", "XAI_API_KEY": "dummy-xai-key"},
            result_overrides={"x_error": "All X backends failed — bird: expired cookies"},
            ytdlp_installed=True,
        )
        assert "log into x.com" in q["nudge_text"]
        assert "console.x.ai" not in q["nudge_text"]

    @pytest.mark.parametrize("backend,credentials,error,expected,unexpected", [
        ("xai", {"XAI_API_KEY": "dummy-xai-key"}, "HTTP 403: Permission denied", "console.x.ai", "log into x.com"),
        ("bird", {"AUTH_TOKEN": "dummy-cookie", "CT0": "dummy-ct0"}, "expired cookies", "log into x.com", "console.x.ai"),
    ])
    def test_thin_retry_failure_preserves_backend_repair(self, backend, credentials, error, expected, unexpected):
        from lib import bird_x, pipeline, schema

        config = _base_config(**credentials)
        config["LAST30DAYS_X_BACKEND"] = backend
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="OpenClaw",
            subqueries=[schema.SubQuery("primary", "OpenClaw", "Recent OpenClaw news", ["x"])],
            source_weights={"x": 1.0},
        )
        bundle = schema.RetrievalBundle()
        runtime = schema.ProviderRuntime("local", "", "", backend)

        with patch.object(bird_x, "is_bird_installed", return_value=True), \
             patch.object(pipeline, "_fetch_x_backend", return_value=([], error)):
            pipeline._retry_thin_sources(
                topic="OpenClaw",
                bundle=bundle,
                plan=plan,
                config=config,
                depth="default",
                date_range=("2026-09-01", "2026-09-30"),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=pipeline.DEPTH_SETTINGS["default"],
            )

        assert bundle.errors_by_source["x"].startswith("Simplified-query retry failed: All X backends failed — ")
        q = _compute(config_overrides=config, result_overrides={"x_error": bundle.errors_by_source["x"]}, ytdlp_installed=True)
        assert expected in q["nudge_text"]
        assert unexpected not in q["nudge_text"]

    @pytest.mark.parametrize("status,message,expected_state,expected_fix", [
        (403, "Forbidden", "auth-failed", "console.x.ai"),
        (402, "Quota blocked", "payment-required", "billing"),
        (429, "Quota blocked", "rate-limited", "rate limit"),
        (None, "Request deadline exceeded", "timeout", "timed out"),
    ])
    def test_raised_xai_error_from_thin_retry_keeps_cause(self, status, message, expected_state, expected_fix):
        from lib import http, pipeline, schema, xai_x

        config = _base_config(XAI_API_KEY="dummy-xai-key")
        config["LAST30DAYS_X_BACKEND"] = "xai"
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="OpenClaw",
            subqueries=[schema.SubQuery("primary", "OpenClaw", "Recent OpenClaw news", ["x"])],
            source_weights={"x": 1.0},
        )
        bundle = schema.RetrievalBundle()
        runtime = schema.ProviderRuntime("local", "", "", "xai")
        error = http.DeadlineExceeded() if status is None else http.HTTPError(message, status_code=status)

        with patch.object(xai_x, "search_x", side_effect=error):
            pipeline._retry_thin_sources(
                topic="OpenClaw",
                bundle=bundle,
                plan=plan,
                config=config,
                depth="default",
                date_range=("2026-09-01", "2026-09-30"),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=pipeline.DEPTH_SETTINGS["default"],
            )

        assert bundle.errors_by_source["x"].startswith("Simplified-query retry failed: All X backends failed — xai:")
        assert bundle.source_status["x"].state == expected_state
        q = _compute(
            config_overrides=config,
            result_overrides={"x_error": bundle.errors_by_source["x"]},
            ytdlp_installed=True,
        )
        assert expected_fix in q["nudge_text"]
        assert "log into x.com" not in q["nudge_text"]

    def test_raised_xai_auth_error_still_uses_xquik_fallback(self):
        from lib import http, pipeline, schema, xai_x, xquik

        config = _base_config(XAI_API_KEY="dummy-xai-key", XQUIK_API_KEY="dummy-xquik-key")
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="OpenClaw",
            subqueries=[schema.SubQuery("primary", "OpenClaw", "Recent OpenClaw news", ["x"])],
            source_weights={"x": 1.0},
        )
        bundle = schema.RetrievalBundle()
        runtime = schema.ProviderRuntime("local", "", "", "xai")
        fallback_item = {
            "text": "Recent OpenClaw update",
            "url": "https://x.com/example/status/1234567890123456789",
            "author_handle": "example",
            "date": "2026-09-15",
            "engagement": {"likes": 2},
            "relevance": 0.9,
        }

        with patch.object(xai_x, "search_x", side_effect=http.HTTPError("Forbidden", status_code=403)), \
             patch.object(xquik, "search_xquik", return_value={"items": []}) as fallback_search, \
             patch.object(xquik, "parse_xquik_response", return_value=[fallback_item]):
            pipeline._retry_thin_sources(
                topic="OpenClaw",
                bundle=bundle,
                plan=plan,
                config=config,
                depth="default",
                date_range=("2026-09-01", "2026-09-30"),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=pipeline.DEPTH_SETTINGS["default"],
            )

        fallback_search.assert_called_once()
        assert bundle.items_by_source["x"]
        assert bundle.errors_by_source["x"].startswith("X served via xquik after xai:")
        q = _compute(config_overrides=config, result_overrides={"x_error": bundle.errors_by_source["x"]}, ytdlp_installed=True)
        assert "console.x.ai" in q["nudge_text"]
        assert "log into x.com" not in q["nudge_text"]

    @pytest.mark.parametrize("status,message,expected_state,expected_fix", [
        (403, "Forbidden", "auth-failed", "console.x.ai"),
        (402, "Quota blocked", "ok", "billing"),
        (429, "Quota blocked", "ok", "rate limit"),
        (None, "Request deadline exceeded", "ok", "timed out"),
    ])
    def test_xai_fallback_keeps_repair_in_final_report(self, status, message, expected_state, expected_fix):
        import last30days as cli
        from lib import http, pipeline, providers, render, schema, xai_x, xquik

        config = _base_config(XAI_API_KEY="dummy-xai-key", XQUIK_API_KEY="dummy-xquik-key")
        runtime = schema.ProviderRuntime("local", "", "", "xai")
        fallback_item = {
            "text": "Recent OpenClaw update",
            "url": "https://x.com/example/status/1234567890123456789",
            "author_handle": "example",
            "date": "2026-09-15",
            "engagement": {"likes": 2},
            "relevance": 0.9,
        }
        error = http.DeadlineExceeded() if status is None else http.HTTPError(message, status_code=status)
        with patch.object(providers, "resolve_runtime", return_value=(runtime, None)), \
             patch.object(xai_x, "search_x", side_effect=error), \
             patch.object(xquik, "search_xquik", return_value={"items": []}), \
             patch.object(xquik, "parse_xquik_response", return_value=[fallback_item]):
            report = pipeline.run(
                topic="OpenClaw", config=config, depth="default", mock=False,
                requested_sources=["x"], web_backend="none", as_of_date="2026-09-30",
                external_plan={
                    "intent": "breaking_news", "freshness_mode": "strict_recent",
                    "cluster_mode": "story",
                    "subqueries": [{
                        "label": "primary", "search_query": "OpenClaw",
                        "ranking_query": "OpenClaw", "sources": ["x"],
                    }],
                },
            )

        assert report.items_by_source["x"]
        assert "x" not in report.errors_by_source
        assert report.source_status["x"].state == expected_state
        assert "xai:" in report.source_status["x"].detail
        assert "re-login needed" not in report.source_status["x"].detail
        rendered = render.render_compact(report)
        if status == 403:
            assert "xai: HTTP 403" in rendered
        assert "re-login needed" not in rendered
        research_results = cli._quality_research_results(
            report, {"available_sources": ["x"]}, {"attempts": 0, "failures": 0}
        )
        assert research_results["x_error"] is None
        assert research_results["x_degraded_error"].startswith("X served via xquik after xai:")
        q = _compute(
            config_overrides=config,
            result_overrides=research_results,
            ytdlp_installed=True,
        )
        assert "x" in q["core_active"]
        assert "x" in q["core_degraded"]
        assert expected_fix in q["nudge_text"]
        assert "log into x.com" not in q["nudge_text"]

    def test_xai_rate_limit_does_not_skip_working_backup_on_later_x_subquery(self):
        import last30days as cli
        from lib import http, pipeline, providers, schema, xai_x, xquik

        config = _base_config(XAI_API_KEY="dummy-xai-key", XQUIK_API_KEY="dummy-xquik-key")
        runtime = schema.ProviderRuntime("local", "", "", "xai")
        fallback_items = [
            [{
                "id": f"x{index}{slot}",
                "text": f"Recent OpenClaw update {index} {slot}",
                "url": f"https://x.com/example/status/12345678901234567{index}{slot}",
                "author_handle": "",
                "date": "2026-09-15",
                "engagement": {"likes": 2},
                "relevance": 0.9,
            } for slot in range(3)]
            for index in (0, 1, 2)
        ]
        second_query_items = []
        add_items = schema.RetrievalBundle.add_items

        def capture_items(bundle, label, source, items):
            if label == "second" and source == "x":
                second_query_items.extend(items)
            return add_items(bundle, label, source, items)

        with patch.object(providers, "resolve_runtime", return_value=(runtime, None)), \
             patch.object(pipeline, "_inner_max_workers", return_value=1), \
             patch.object(schema.RetrievalBundle, "add_items", new=capture_items), \
             patch.object(xai_x, "search_x", side_effect=http.HTTPError("Quota blocked", status_code=429)) as xai_search, \
             patch.object(xquik, "search_xquik", return_value={"items": []}) as fallback_search, \
             patch.object(xquik, "parse_xquik_response", side_effect=fallback_items):
            report = pipeline.run(
                topic="OpenClaw", config=config, depth="default", mock=False,
                requested_sources=["x"], web_backend="none", as_of_date="2026-09-30",
                external_plan={
                    "intent": "breaking_news", "freshness_mode": "strict_recent",
                    "cluster_mode": "story",
                    "subqueries": [
                        {
                            "label": label, "search_query": f"OpenClaw {label}",
                            "ranking_query": "OpenClaw", "sources": ["x"],
                        }
                        for label in ("first", "second")
                    ],
                },
            )

        assert xai_search.call_count >= 2
        assert fallback_search.call_count >= 2
        assert any(
            "status/123456789012345671" in item.url
            for item in second_query_items
        )
        assert report.source_status["x"].lane_failure_state == schema.RATE_LIMITED
        research_results = cli._quality_research_results(
            report, {"available_sources": ["x"]}, {"attempts": 0, "failures": 0}
        )
        assert "HTTP 429" in research_results["x_degraded_error"]
        q = _compute(config_overrides=config, result_overrides=research_results, ytdlp_installed=True)
        assert "x" in q["core_degraded"]
        assert "rate limit" in q["nudge_text"]

    @pytest.mark.parametrize("xai_status,expected_state,expected_fix", [
        (429, "ok", "rate limit"),
        (403, "auth-failed", "console.x.ai"),
    ])
    def test_rate_limited_backup_stops_later_x_subqueries(self, xai_status, expected_state, expected_fix):
        import last30days as cli
        from lib import http, pipeline, providers, schema, xai_x, xquik

        config = _base_config(XAI_API_KEY="dummy-xai-key", XQUIK_API_KEY="dummy-xquik-key")
        runtime = schema.ProviderRuntime("local", "", "", "xai")
        topic = "OpenClaw launch update"
        assert len(xquik.expand_xquik_queries(topic, "default")) == 2
        get_calls = 0

        def partial_xquik_response(*args, **kwargs):
            nonlocal get_calls
            get_calls += 1
            if get_calls == 1:
                return {"tweets": [{
                    "id": "1234567890123456789",
                    "author": {"username": "example"},
                    "createdAt": "2026-09-15T12:00:00Z",
                    "text": "Recent OpenClaw update",
                }]}
            http._raise(http.HTTPError("Too many requests", status_code=429))

        def rejected_xai_response(*args, **kwargs):
            message = "Forbidden" if xai_status == 403 else "Quota blocked"
            http._raise(http.HTTPError(message, status_code=xai_status))

        with patch.object(providers, "resolve_runtime", return_value=(runtime, None)), \
             patch.object(pipeline, "_inner_max_workers", return_value=1), \
             patch.object(xai_x, "search_x", wraps=xai_x.search_x) as xai_search, \
             patch.object(xquik, "search_xquik", wraps=xquik.search_xquik) as fallback_search, \
             patch.object(http, "post", side_effect=rejected_xai_response) as xai_post, \
             patch.object(http, "get", side_effect=partial_xquik_response):
            report = pipeline.run(
                topic=topic, config=config, depth="default", mock=False,
                requested_sources=["x"], web_backend="none", as_of_date="2026-09-30",
                external_plan={
                    "intent": "breaking_news", "freshness_mode": "strict_recent",
                    "cluster_mode": "story",
                    "subqueries": [
                        {
                            "label": label, "search_query": f"OpenClaw {label}",
                            "ranking_query": "OpenClaw", "sources": ["x"],
                        }
                        for label in ("first", "second")
                    ],
                },
            )

        assert xai_search.call_count == 1
        assert xai_post.call_count == 1
        assert fallback_search.call_count == 1
        assert report.items_by_source["x"]
        assert report.source_status["x"].state == expected_state
        assert report.source_status["x"].lane_failure_state == schema.RATE_LIMITED
        assert "xquik also rate-limited" in report.source_status["x"].detail
        assert f"xai: HTTP {xai_status}" in report.source_status["x"].detail
        results = cli._quality_research_results(
            report, {"available_sources": ["x"]}, {"attempts": 0, "failures": 0}
        )
        q = _compute(config_overrides=config, result_overrides=results, ytdlp_installed=True)
        assert expected_fix in q["nudge_text"]
        assert "log into x.com" not in q["nudge_text"]
