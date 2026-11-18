"""Pin the keyless short-form fallback gate: yt-dlp + web discovery.

TikTok and Instagram are no longer purely key-gated. Without
SCRAPECREATORS_API_KEY they can still run when yt-dlp is installed and a
web-discovery backend (SearXNG / Serper / Exa / Brave / keyless
floor) is available. This file pins BOTH branches of that gate so a later
change to is_tiktok_available() / is_instagram_available() cannot silently
collapse the fallback back to key-only, and so a keyless run does not keep
telling the user to buy a ScrapeCreators key it will never need.

The gate reads yt-dlp presence through youtube_yt.is_ytdlp_installed(), not
through the env.is_ytdlp_available() wrapper, so that is the symbol patched
below -- same reason tests/test_doctor.py::_Hermetic pins it.
"""

from unittest import mock

import pytest

from lib import env, health

from tests.test_doctor import _build


# Each of these alone must be enough to arm the fallback.
DISCOVERY_BACKENDS = [
    {"LAST30DAYS_SEARXNG_URL": "https://searx.example"},
    {"SERPER_API_KEY": "sp"},
    {"EXA_API_KEY": "ex"},
    {"BRAVE_API_KEY": "br"},
]

# Host that has its own web search: the keyless floor is suppressed, paid
# keys are not. Set this to isolate "no backend at all" from "no keyless".
NATIVE_SEARCH_HOST = {"LAST30DAYS_NATIVE_SEARCH": "1"}

SOURCES = [env.is_tiktok_available, env.is_instagram_available]


def _gate(config, ytdlp):
    """is_shortform_oss_available() with yt-dlp presence pinned."""
    with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=ytdlp):
        return env.is_shortform_oss_available(config)


def _source_available(fn, config, ytdlp):
    """A source-availability predicate with yt-dlp presence pinned."""
    with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=ytdlp):
        return fn(config)


class TestShortformGate:
    def test_ytdlp_missing_closes_the_gate_even_with_a_paid_backend(self):
        for cfg in DISCOVERY_BACKENDS:
            assert _gate(cfg, False) is False, cfg

    def test_blank_searxng_url_does_not_open_the_gate(self):
        # A whitespace-only URL is a misconfiguration, not a backend.
        assert _gate(
            {"LAST30DAYS_SEARXNG_URL": "   ", **NATIVE_SEARCH_HOST}, True
        ) is False

    @pytest.mark.parametrize("cfg", DISCOVERY_BACKENDS)
    def test_paid_backend_arms_the_gate_on_a_native_search_host(self, cfg):
        # Native-search hosts suppress only the keyless floor, never a paid key.
        assert _gate({**cfg, **NATIVE_SEARCH_HOST}, True) is True

    def test_keyless_floor_arms_the_gate_without_native_search(self):
        assert _gate({}, True) is True

    def test_keyless_floor_is_suppressed_on_a_native_search_host(self):
        assert _gate(dict(NATIVE_SEARCH_HOST), True) is False


class TestSourceAvailability:
    @pytest.mark.parametrize("fn", SOURCES)
    def test_scrapecreators_key_is_enough_without_ytdlp(self, fn):
        assert _source_available(fn, {"SCRAPECREATORS_API_KEY": "sc"}, False) is True

    @pytest.mark.parametrize("fn", SOURCES)
    def test_oss_fallback_keeps_the_source_available_keyless(self, fn):
        assert _source_available(fn, {}, True) is True

    @pytest.mark.parametrize("fn", SOURCES)
    def test_source_stays_unavailable_with_no_key_and_no_fallback(self, fn):
        assert _source_available(fn, dict(NATIVE_SEARCH_HOST), False) is False

    @pytest.mark.parametrize("fn", SOURCES)
    def test_tiktok_and_instagram_never_diverge(self, fn):
        # They share one gate; a divergence is a bug, not a feature.
        other = SOURCES[1] if fn is SOURCES[0] else SOURCES[0]
        for cfg, ytdlp in (({}, True), (dict(NATIVE_SEARCH_HOST), True),
                           (dict(NATIVE_SEARCH_HOST), False),
                           ({"SCRAPECREATORS_API_KEY": "sc"}, False)):
            assert _source_available(fn, cfg, ytdlp) is _source_available(other, cfg, ytdlp)


class TestTokens:
    """APIFY_API_TOKEN is a legacy alias only; it was never wired to an actor."""

    def test_tiktok_token_never_falls_back_to_apify(self):
        assert env.get_tiktok_token({"APIFY_API_TOKEN": "legacy"}) == ""
        assert env.get_tiktok_token({"APIFY_API_TOKEN": "legacy",
                                     "SCRAPECREATORS_API_KEY": "sc"}) == "sc"

    def test_instagram_token_never_falls_back_to_apify(self):
        assert env.get_instagram_token({"APIFY_API_TOKEN": "legacy"}) == ""
        assert env.get_instagram_token({"SCRAPECREATORS_API_KEY": "sc"}) == "sc"


class TestDoctorSurface:
    """The user-visible half: doctor's verdict must follow the gate."""

    def test_doctor_reports_the_fallback_armed_when_ytdlp_is_present(self):
        record = _build({}, probe_map={"yt-dlp": health.OK})["sources"]["tiktok"]
        assert record["status"] == "ok"
        assert "yt-dlp + web discovery" in record["requires"]

    def test_doctor_stops_prescribing_a_scrapecreators_key_when_armed(self):
        # The old key-gated wording is the failure mode worth pinning.
        record = _build({}, probe_map={"yt-dlp": health.OK})["sources"]["tiktok"]
        assert record["requires"] != "SCRAPECREATORS_API_KEY"

    def test_doctor_keeps_the_key_gated_verdict_when_ytdlp_is_absent(self):
        report = _build({}, probe_map={"yt-dlp": health.MISSING})
        for name in ("tiktok", "instagram"):
            record = report["sources"][name]
            assert record["tier"] == "off", name
            assert record["requires"] == "SCRAPECREATORS_API_KEY", name