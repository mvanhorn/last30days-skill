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
    from scripts.lib.quality_nudge import compute_quality_score
    from scripts.lib import youtube_yt

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
