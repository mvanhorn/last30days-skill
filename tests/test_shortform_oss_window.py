"""Keyless TikTok/Instagram: date window, discovery freshness, creator expansion.

Every test drives the real entry points (``shortform_oss.search`` or
``shortform_oss.discover_urls``) and fakes only the network boundary:
``lib.http.request`` / ``lib.http.get`` for web discovery and
``lib.subproc.run_with_timeout`` for yt-dlp.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from lib import shortform_oss

FIXTURE = Path(__file__).parent / "fixtures" / "tiktok_user_flat_playlist.jsonl"

FROM, TO = "2026-11-01", "2026-11-21"
NATIVE = {"LAST30DAYS_NATIVE_SEARCH": "1"}  # keeps the keyless floor out of the way
_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _unix(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(
        hour=12, tzinfo=timezone.utc).timestamp())


def tt_id(day: str, low: int = 12345) -> str:
    return str((_unix(day) << 32) | low)


def tt_url(handle: str, day: str, low: int = 12345) -> str:
    return f"https://www.tiktok.com/@{handle}/video/{tt_id(day, low)}"


def ig_shortcode(day: str, low: int = 7) -> str:
    media_id = ((_unix(day) * 1000 - 1314220021721) << 23) | low
    out = ""
    while media_id:
        media_id, r = divmod(media_id, 64)
        out = _IG_ALPHABET[r] + out
    return out


def _raw(kind: str, url: str, day: str, text: str = "topic words") -> dict:
    vid = url.rstrip("/").rsplit("/", 1)[-1]
    return {
        "id": vid,
        "uploader": "u",
        "timestamp": _unix(day),
        "view_count": 100,
        "like_count": 10,
        "comment_count": 1,
        "description": text,
        "webpage_url": url,
    }


def _listed_handle(cmd) -> str:
    target = next(a for a in cmd if "tiktok.com/@" in a)
    return target.rsplit("@", 1)[-1]


class FakeNet:
    """Fake Serper + yt-dlp. Records every subprocess call."""

    def __init__(self, links, hydrate=None, playlists=None, on_list=None):
        self.links = links
        self.hydrate = hydrate or {}
        self.playlists = playlists or {}
        self.on_list = on_list
        self.calls = []
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return {"organic": [{"link": u} for u in self.links]}

    def run(self, cmd, timeout=None, input_text=None):
        self.calls.append((list(cmd), input_text))
        if "--flat-playlist" in cmd:
            handle = _listed_handle(cmd)
            if self.on_list:
                self.on_list(handle)
            return SimpleNamespace(stdout=self.playlists.get(handle, ""))
        lines = []
        for url in (input_text or "").split():
            if url in self.hydrate:
                lines.append(json.dumps(self.hydrate[url]))
        return SimpleNamespace(stdout="\n".join(lines))

    def hydrated_urls(self):
        out = []
        for cmd, inp in self.calls:
            if "--dump-json" in cmd:
                out.extend((inp or "").split())
        return out

    def listed_handles(self):
        return [_listed_handle(cmd) for cmd, _ in self.calls if "--flat-playlist" in cmd]


def _run_search(net, kind="tiktok", topic="topic words", depth="default",
                creators=None, cancel=None, config=None, env=None):
    cfg = {"SERPER_API_KEY": "sp", **NATIVE, **(config or {})}
    with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
         mock.patch("lib.http.request", side_effect=net.request), \
         mock.patch("lib.subproc.run_with_timeout", side_effect=net.run), \
         mock.patch.dict(os.environ, env or {}):
        os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)
        return shortform_oss.search(
            kind, topic, FROM, TO, depth, cfg, creators=creators, cancel=cancel,
        )


class TestIdDates:
    def test_tiktok_id_decodes_to_upload_day(self):
        url = "https://www.tiktok.com/@u/video/7710481357683576094"
        assert shortform_oss.post_date_from_url("tiktok", url) == "2026-11-21"

    def test_instagram_shortcode_decodes_within_minutes_of_ytdlp_timestamp(self):
        # yt-dlp -j on this reel reported timestamp 1793923629.
        ts = shortform_oss.post_timestamp_from_url(
            "instagram", "https://www.instagram.com/reel/DfYRA2ghafT/")
        assert ts is not None
        assert abs(ts - 1793923629) < 15 * 60

    def test_undecodable_ids_return_none(self):
        assert shortform_oss.post_date_from_url(
            "tiktok", "https://www.tiktok.com/@u/video/123") is None
        assert shortform_oss.post_date_from_url(
            "instagram", "https://www.instagram.com/reel/ABCDEFGHIJKLMNOPQRST/") is None
        assert shortform_oss.post_date_from_url("tiktok", "not a url") is None


class TestNoStaleLeak:
    def test_stale_posts_are_never_kept_when_nothing_is_in_window(self):
        # Undecodable ids reach hydration; yt-dlp then reports 2024 dates.
        urls = [f"https://www.tiktok.com/@u/video/{n}" for n in (101, 102)]
        net = FakeNet(urls, hydrate={u: _raw("tiktok", u, "2024-03-01") for u in urls})
        result = _run_search(net)
        assert result["items"] == []
        assert "date" in result["error"]

    def test_stale_ids_are_dropped_before_hydration(self):
        fresh = tt_url("a", "2026-11-10")
        stale = tt_url("b", "2025-06-01")
        net = FakeNet([stale, fresh], hydrate={fresh: _raw("tiktok", fresh, "2026-11-10")})
        result = _run_search(net)
        assert stale not in net.hydrated_urls()
        assert fresh in net.hydrated_urls()
        assert [i["url"] for i in result["items"]] == [fresh]

    def test_instagram_stale_shortcodes_are_dropped_before_hydration(self):
        fresh = f"https://www.instagram.com/reel/{ig_shortcode('2026-11-10')}/"
        stale = f"https://www.instagram.com/reel/{ig_shortcode('2025-01-10')}/"
        net = FakeNet([stale, fresh],
                      hydrate={fresh: _raw("instagram", fresh, "2026-11-10")})
        result = _run_search(net, kind="instagram")
        assert net.hydrated_urls() == [fresh]
        assert len(result["items"]) == 1

    def test_only_stale_discovery_returns_zero_with_reason_and_no_hydration(self):
        net = FakeNet([tt_url("b", "2025-06-01")])
        result = _run_search(net)
        assert result["items"] == []
        assert "window" in result["error"]
        assert net.hydrated_urls() == []


class TestFreshness:
    def _discover(self, cfg, env=None, kind="tiktok"):
        seen = {}

        def fake_request(method, url, **kw):
            seen.setdefault("request", []).append((method, url, kw))
            return {}

        def fake_get(url, **kw):
            seen.setdefault("get", []).append(url)
            return {"results": []}

        with mock.patch("lib.http.request", side_effect=fake_request), \
             mock.patch("lib.http.get", side_effect=fake_get), \
             mock.patch.dict(os.environ, env or {}):
            if env is None:
                os.environ.pop("LAST30DAYS_SHORTFORM_FRESHNESS", None)
            shortform_oss.discover_urls(kind, "topic", {**cfg, **NATIVE}, 5, from_date=FROM)
        return seen

    def test_serper_asks_for_past_month(self):
        seen = self._discover({"SERPER_API_KEY": "sp"})
        assert seen["request"][0][2]["json_data"]["tbs"] == "qdr:m"

    def test_brave_asks_for_past_month(self):
        seen = self._discover({"BRAVE_API_KEY": "br"})
        q = parse_qs(urlparse(seen["request"][0][1]).query)
        assert q["freshness"] == ["pm"]

    def test_exa_asks_for_window_start(self):
        seen = self._discover({"EXA_API_KEY": "ex"})
        body = seen["request"][0][2]["json_data"]
        assert body["startPublishedDate"].startswith(FROM)

    def test_searxng_asks_for_past_month(self):
        seen = self._discover({"LAST30DAYS_SEARXNG_URL": "https://searx.example"})
        q = parse_qs(urlparse(seen["get"][0]).query)
        assert q["time_range"] == ["month"]

    def test_off_restores_the_unfiltered_requests(self):
        env = {"LAST30DAYS_SHORTFORM_FRESHNESS": "off"}
        serper = self._discover({"SERPER_API_KEY": "sp"}, env)
        assert "tbs" not in serper["request"][0][2]["json_data"]
        brave = self._discover({"BRAVE_API_KEY": "br"}, env)
        q = parse_qs(urlparse(brave["request"][0][1]).query)
        assert q["freshness"] == ["1970-01-01to2099-12-31"]
        exa = self._discover({"EXA_API_KEY": "ex"}, env)
        assert "startPublishedDate" not in exa["request"][0][2]["json_data"]
        searx = self._discover({"LAST30DAYS_SEARXNG_URL": "https://searx.example"}, env)
        assert "time_range" not in parse_qs(urlparse(searx["get"][0]).query)

    def test_each_backend_switches_individually(self):
        env = {"LAST30DAYS_SHORTFORM_FRESHNESS": "serper,exa"}
        serper = self._discover({"SERPER_API_KEY": "sp"}, env)
        assert serper["request"][0][2]["json_data"]["tbs"] == "qdr:m"
        brave = self._discover({"BRAVE_API_KEY": "br"}, env)
        q = parse_qs(urlparse(brave["request"][0][1]).query)
        assert q["freshness"] == ["1970-01-01to2099-12-31"]

    def test_search_threads_the_window_start_to_exa(self):
        net = FakeNet([])
        cfg = {"EXA_API_KEY": "ex"}
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch("lib.http.request", side_effect=net.request), \
             mock.patch("lib.subproc.run_with_timeout", side_effect=net.run):
            shortform_oss.search("tiktok", "topic", FROM, TO, "default", {**cfg, **NATIVE})
        exa = [kw for _m, url, kw in net.requests if "exa.ai" in url]
        assert exa and exa[0]["json_data"]["startPublishedDate"].startswith(FROM)


class TestCreatorExpansion:
    PLAYLIST = FIXTURE.read_text()

    def test_handle_from_a_stale_video_is_listed_and_filtered_by_window_and_title(self):
        stale = tt_url("mattpocockuk", "2025-02-01")
        keep = "https://www.tiktok.com/@mattpocockuk/video/7704672261512449313"
        net = FakeNet(
            [stale],
            hydrate={keep: _raw("tiktok", keep, "2026-11-05", "Is vibe coding an insult?")},
            playlists={"mattpocockuk": self.PLAYLIST},
        )
        result = _run_search(net, topic="vibe coding")
        assert net.listed_handles() == ["mattpocockuk"]
        # In-window and on-topic only: off-topic in-window titles and the
        # stale October videos never reach hydration.
        assert net.hydrated_urls() == [keep]
        assert [i["url"] for i in result["items"]] == [keep]

    def test_listing_prints_id_and_title_bounded_per_depth(self):
        net = FakeNet([tt_url("h", "2025-02-01")], playlists={"h": self.PLAYLIST})
        _run_search(net, depth="default")
        cmd = next(c for c, _ in net.calls if "--flat-playlist" in c)
        fmt = cmd[cmd.index("--print") + 1]
        assert "id" in fmt and "title" in fmt
        assert cmd[cmd.index("--playlist-end") + 1] == "8"

    @pytest.mark.parametrize("depth,creators", [("quick", 3), ("default", 5), ("deep", 8)])
    def test_number_of_listed_creators_is_capped(self, depth, creators):
        links = [tt_url(f"h{n}", "2025-02-01", low=n) for n in range(12)]
        net = FakeNet(links)
        _run_search(net, depth=depth)
        assert net.listed_handles() == [f"h{n}" for n in range(creators)]

    def test_topic_account_keeps_every_in_window_video(self):
        # "Matt Pocock" is @mattpocockuk: its titles never name it, so the
        # title bar would drop all of them. Authorship makes them on-topic.
        net = FakeNet([tt_url("mattpocockuk", "2025-02-01")],
                      playlists={"mattpocockuk": self.PLAYLIST})
        _run_search(net, topic="Matt Pocock")
        assert len(net.hydrated_urls()) == 5

    def test_creator_is_topic_needs_a_real_match(self):
        assert shortform_oss._creator_is_topic("Sabrina Carpenter", "@sabrinacarpenter")
        assert shortform_oss._creator_is_topic("Sabrina Carpenter", "Sabrina Carpenter")
        assert not shortform_oss._creator_is_topic("air fryer recipes", "airfryerguy")
        assert not shortform_oss._creator_is_topic("AI", "aidaily")

    def test_explicit_creators_are_not_listed_twice(self):
        net = FakeNet([tt_url("mattpocockuk", "2025-02-01")],
                      playlists={"mattpocockuk": self.PLAYLIST})
        _run_search(net, creators=["mattpocockuk"])
        assert net.listed_handles() == ["mattpocockuk"]

    def test_explicit_creator_listing_drops_out_of_window_ids(self):
        net = FakeNet([], playlists={"mattpocockuk": self.PLAYLIST})
        _run_search(net, creators=["mattpocockuk"])
        hydrated = net.hydrated_urls()
        assert hydrated, "in-window explicit-creator videos must be hydrated"
        for url in hydrated:
            assert shortform_oss.post_date_from_url("tiktok", url) >= FROM

    def test_expansion_off_lists_no_harvested_creators(self):
        net = FakeNet([tt_url("mattpocockuk", "2025-02-01")],
                      playlists={"mattpocockuk": self.PLAYLIST})
        _run_search(net, config={"LAST30DAYS_SHORTFORM_CREATOR_EXPANSION": "off"})
        assert net.listed_handles() == []

    def test_instagram_never_lists_creators(self):
        net = FakeNet([f"https://www.instagram.com/reel/{ig_shortcode('2025-01-10')}/"])
        _run_search(net, kind="instagram")
        assert net.listed_handles() == []

    def test_cancel_stops_expansion_between_creators(self):
        cancel = threading.Event()
        links = [tt_url(f"h{n}", "2025-02-01", low=n) for n in range(5)]
        net = FakeNet(links, on_list=lambda _h: cancel.set())
        _run_search(net, cancel=cancel)
        assert net.listed_handles() == ["h0"]
        assert net.hydrated_urls() == []


class TestLaneEntryPoints:
    """The keyless lanes surface the window error instead of stale posts."""

    @pytest.mark.parametrize("lane", ["tiktok", "instagram"])
    def test_keyless_lane_returns_zero_with_reason_for_stale_posts(self, lane):
        from lib import instagram, tiktok

        if lane == "tiktok":
            urls = [f"https://www.tiktok.com/@u/video/{n}" for n in (101, 102)]
        else:
            urls = ["https://www.instagram.com/reel/ABCDEFGHIJKLMNOPQRST/"]
        net = FakeNet(urls, hydrate={u: _raw(lane, u, "2024-03-01") for u in urls})
        module = tiktok if lane == "tiktok" else instagram
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch("lib.http.request", side_effect=net.request), \
             mock.patch("lib.subproc.run_with_timeout", side_effect=net.run):
            result = module.search_and_enrich(
                "topic words", FROM, TO, "default", None,
                config={"SERPER_API_KEY": "sp", **NATIVE},
            )
        assert result["items"] == []
        assert "date range" in result["error"]
