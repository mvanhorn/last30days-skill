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

import os
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from lib import env, health, shortform_oss

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

    def test_exa_key_arms_only_tiktok_not_instagram(self):
        # Exa returns 0 Instagram posts; its key must not advertise Instagram.
        cfg = {"EXA_API_KEY": "ex", **NATIVE_SEARCH_HOST}
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True):
            assert env.is_shortform_oss_available(cfg, "tiktok") is True
            assert env.is_shortform_oss_available(cfg, "instagram") is False
            assert env.is_instagram_available(cfg) is False

    def test_instagram_gate_still_accepts_its_own_backends(self):
        cfg = {"BRAVE_API_KEY": "br", **NATIVE_SEARCH_HOST}
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True):
            assert env.is_shortform_oss_available(cfg, "instagram") is True


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

class TestDeadlineAndCancel:
    """The fallback must stop starting work once the run is cancelled."""

    def _no_work(self):
        return mock.patch.multiple(
            "lib.shortform_oss",
            discover_urls=mock.DEFAULT,
            enrich_urls=mock.DEFAULT,
            creator_urls=mock.DEFAULT,
        )

    def test_past_deadline_returns_error_without_discovery(self):
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch("lib.shortform_oss.discover_urls") as disc, \
             mock.patch("lib.shortform_oss.enrich_urls") as enrich:
            result = shortform_oss.search(
                "tiktok", "topic", "2026-01-01", "2026-12-31", "default", {},
                deadline=time.monotonic() - 1.0,
            )
        assert result["items"] == []
        assert "deadline" in result["error"]
        disc.assert_not_called()
        enrich.assert_not_called()

    def test_cancelled_event_returns_error_without_discovery(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch("lib.shortform_oss.discover_urls") as disc, \
             mock.patch("lib.shortform_oss.enrich_urls") as enrich:
            result = shortform_oss.search(
                "instagram", "topic", "2026-01-01", "2026-12-31", "default", {},
                cancel=cancel,
            )
        assert result["items"] == []
        assert result["error"]
        disc.assert_not_called()
        enrich.assert_not_called()

    def test_enrich_skips_when_deadline_passes_during_discovery(self):
        with mock.patch("lib.shortform_oss.discover_urls",
                        return_value=(["https://www.tiktok.com/@u/video/1"], "searxng")), \
             mock.patch("lib.shortform_oss.enrich_urls") as enrich:
            result = shortform_oss.search(
                "tiktok", "topic", "2026-01-01", "2026-12-31", "default", {},
                deadline=time.monotonic() - 1.0,
            )
        enrich.assert_not_called()
        assert result["items"] == []


class TestKeylessRungPolicy:
    """Discovery must follow the same keyless policy the availability gate used."""

    def test_keyless_rung_is_skipped_on_native_search_hosts(self):
        with mock.patch("lib.web_search_keyless.keyless_search",
                        side_effect=AssertionError("keyless ran on a native-search host")):
            urls, backend = shortform_oss.discover_urls(
                "tiktok", "topic", {"LAST30DAYS_NATIVE_SEARCH": "1"}, 5,
            )
        assert urls == []
        assert backend == "none"

    def test_keyless_rung_runs_when_policy_allows_it(self):
        canned = ({"url": "https://www.tiktok.com/@u/video/1"},)
        with mock.patch("lib.web_search_keyless.keyless_search",
                        return_value=(list(canned), {"keyless_backend": "ddg"})) as ks:
            urls, backend = shortform_oss.discover_urls("tiktok", "topic", {}, 5)
        ks.assert_called_once()
        assert urls == ["https://www.tiktok.com/@u/video/1"]
        assert backend == "ddg"


class TestInstagramFallbackErrorSurfaces:
    """Without a token the paid loop never runs, so the OSS error must surface."""

    def test_no_token_oss_error_is_returned(self):
        from lib import instagram as ig

        with mock.patch("lib.shortform_oss.search",
                        return_value={"items": [], "error": "oss discovery empty"}):
            result = ig.search_and_enrich(
                "topic", "2026-01-01", "2026-12-31", "default", None, config={},
            )
        assert result["error"] == "oss discovery empty"


class TestSshRoutedExtraction:
    """SSH-routed hosts (LAST30DAYS_YOUTUBE_SSH_HOST) extract remotely.

    The availability gate follows youtube_yt.is_ytdlp_installed(), which is
    True on SSH-routed hosts without a local binary — so extraction must run
    through the same SSH wrapper instead of a local FileNotFoundError.
    """

    SSH_ENV = {"LAST30DAYS_YOUTUBE_SSH_HOST": "macmini"}

    def test_gate_opens_on_ssh_routed_host_without_local_binary(self):
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True) as inst, \
             mock.patch.dict(os.environ, self.SSH_ENV):
            # is_ytdlp_installed is what the gate reads; on SSH hosts it
            # returns True without a local binary, and extraction must
            # therefore route over ssh (pinned below).
            assert env.is_shortform_oss_available({"SERPER_API_KEY": "sp"}) is True
        inst.assert_called_once()

    def test_enrich_urls_wrap_the_command_with_ssh(self):
        from lib import subproc as subproc_mod

        captured = {}

        def fake_run(cmd, timeout=None, input_text=None):
            captured["cmd"] = cmd
            captured["input"] = input_text
            return SimpleNamespace(stdout="")

        url = "https://www.tiktok.com/@u/video/1"
        with mock.patch.dict(os.environ, self.SSH_ENV), \
             mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch.object(subproc_mod, "run_with_timeout", side_effect=fake_run):
            items = shortform_oss.enrich_urls("tiktok", [url], "topic")
        assert items == []
        cmd = captured["cmd"]
        assert cmd[0] == "ssh"
        assert "macmini" in cmd
        assert "yt-dlp" in " ".join(cmd)
        assert "--dump-json" in " ".join(cmd)
        assert url in (captured["input"] or "")

    def test_creator_urls_wrap_the_command_with_ssh(self):
        from lib import subproc as subproc_mod

        captured = {}

        def fake_run(cmd, timeout=None, input_text=None):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="1234567890")

        with mock.patch.dict(os.environ, self.SSH_ENV), \
             mock.patch.object(subproc_mod, "run_with_timeout", side_effect=fake_run):
            urls = shortform_oss.creator_urls("tiktok", "creator", 3)
        assert urls == ["https://www.tiktok.com/@creator/video/1234567890"]
        assert captured["cmd"][0] == "ssh"

    def test_local_hosts_still_run_yt_dlp_directly(self):
        from lib import subproc as subproc_mod

        captured = {}

        def fake_run(cmd, timeout=None, input_text=None):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="")

        # Pin yt-dlp presence: CI runners do not have yt-dlp on PATH, and
        # enrich_urls gates on is_ytdlp_installed() before building the
        # command — unpinned, the fake never runs there. The SSH var is also
        # unset (not empty-string-set) so _ytdlp_ssh_host() cannot wrap.
        saved = os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)
        try:
            with mock.patch.dict(os.environ, {}, clear=False), \
                 mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
                 mock.patch.object(subproc_mod, "run_with_timeout", side_effect=fake_run):
                shortform_oss.enrich_urls(
                    "tiktok", ["https://www.tiktok.com/@u/video/1"], "topic",
                )
        finally:
            if saved is not None:
                os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = saved
        assert captured["cmd"][0] == "yt-dlp"
