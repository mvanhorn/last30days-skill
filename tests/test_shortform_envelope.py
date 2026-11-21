"""The ``--shortform-urls`` host lane: a host's own web search hands the
keyless TikTok/Instagram lane post URLs and TikTok creator profiles. The engine
reads only each URL, drops stale posts by their id date before any fetch,
hydrates the rest with yt-dlp, holds them to the window and the topic bar, and
names the lane in the footer. With a ScrapeCreators key the URLs wait unused.

Network is faked at ``lib.subproc.run_with_timeout`` (yt-dlp) and
``lib.http.request`` (web discovery)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import dates, pipeline, render, schema, shortform_envelope, shortform_oss
from tests.test_shortform_oss_window import FakeNet, _raw, ig_shortcode, tt_url

TOPIC = "labubu dolls"
FROM, TO = dates.get_date_range(30)
NATIVE = {"LAST30DAYS_NATIVE_SEARCH": "1"}


def _day(offset: int) -> str:
    return dates.get_date_range(offset)[0]


FRESH, OLD = _day(5), _day(200)


def _envelope(urls, **over) -> dict:
    payload = {"schema": shortform_envelope.SCHEMA, "topic": TOPIC,
               "urls": [u if isinstance(u, dict) else {"url": u} for u in urls]}
    payload.update(over)
    return payload


def _write(tmp_path: Path, payload, name: str = "shortform.json") -> str:
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return str(path)


def _read(path: str, topic: str = TOPIC) -> shortform_envelope.Envelope:
    with redirect_stderr(io.StringIO()):
        return shortform_envelope.read(path, topic)


def _search(net, envelope, kind="tiktok", config=None):
    cfg = {**NATIVE, **(config or {})}
    if envelope is not None:
        cfg["_shortform_envelope"] = envelope
    with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
         mock.patch("lib.http.request", side_effect=net.request), \
         mock.patch("lib.subproc.run_with_timeout", side_effect=net.run), \
         mock.patch.dict(os.environ, {}), redirect_stderr(io.StringIO()):
        os.environ.pop("LAST30DAYS_YOUTUBE_SSH_HOST", None)
        return shortform_oss.search(kind, TOPIC, FROM, TO, "default", cfg)


# ---------------------------------------------------------------------------
# Reading the envelope
# ---------------------------------------------------------------------------


class TestReader:
    def test_posts_and_creators_are_sorted_and_bad_rows_counted(self, tmp_path):
        tt = tt_url("dollfan", FRESH)
        ig = f"https://www.instagram.com/p/{ig_shortcode(FRESH)}/?igsh=abc"
        urls = [
            tt + "?is_from_webapp=1",
            ig,
            "https://www.tiktok.com/@labubu_official",
            "https://www.instagram.com/labubu_official/",
            "https://vm.tiktok.com/ZMabc123/",
            "https://www.google.com/url?q=" + tt,
            "https://evil.example/?u=" + tt,
            "https://www.tiktok.com/tag/labubu",
            {"url": tt, "caption": "HOST CAPTION", "views": 99999999},
            {"id": "123"},
        ]
        envelope = _read(_write(tmp_path, _envelope(urls)))
        assert envelope.total == 10
        assert envelope.posts("tiktok") == [tt]
        assert envelope.posts("instagram") == [f"https://www.instagram.com/reel/{ig_shortcode(FRESH)}/"]
        assert envelope.creators("tiktok") == ["labubu_official"]
        assert envelope.creators("instagram") == []
        c = envelope.counters
        assert c["instagram-profile"] == 1
        assert c["not-a-post"] == 2  # vm.tiktok.com short link, tag page
        assert c["redirect"] == 1
        assert c["non-shortform"] == 1
        assert c["duplicate"] == 1
        assert c["extra-fields"] == 2  # the host's caption/views row, and the id-only row
        assert c["invalid"] == 1  # an id is not a URL

    def test_only_the_first_40_urls_are_used(self, tmp_path):
        urls = [tt_url("u", FRESH, low=n) for n in range(45)]
        envelope = _read(_write(tmp_path, _envelope(urls)))
        assert envelope.accepted == shortform_envelope.MAX_URLS == 40
        assert envelope.counters["truncated"] == 5
        assert "first 40" in envelope.receipt()

    @pytest.mark.parametrize("payload, field", [
        (_envelope(["https://www.tiktok.com/@a"], schema="last30days-reddit-threads/1"), "schema"),
        (_envelope(["https://www.tiktok.com/@a"], topic="something else"), "topic"),
        (_envelope([]), "empty"),
        ({"schema": shortform_envelope.SCHEMA, "topic": TOPIC, "urls": "x"}, "not a list"),
        ("not json", "valid JSON"),
    ])
    def test_envelope_level_problems_fail_closed(self, tmp_path, payload, field):
        path = _write(tmp_path, payload)
        with pytest.raises(shortform_envelope.EnvelopeContractError) as err:
            _read(path)
        assert field in err.value.message
        assert "without --shortform-urls" in err.value.message

    def test_a_non_json_suffix_is_refused(self, tmp_path):
        path = _write(tmp_path, _envelope(["https://www.tiktok.com/@a"]), "urls.txt")
        with pytest.raises(shortform_envelope.EnvelopeContractError, match=".json file"):
            _read(path)


# ---------------------------------------------------------------------------
# The keyless lane
# ---------------------------------------------------------------------------


class TestKeylessLane:
    def test_host_posts_are_id_dated_hydrated_and_marked(self, tmp_path):
        fresh = tt_url("dollfan", FRESH)
        stale = tt_url("oldfan", OLD)
        envelope = _read(_write(tmp_path, _envelope([fresh, stale])))
        net = FakeNet([], hydrate={
            fresh: _raw("tiktok", fresh, FRESH, text="my labubu dolls haul"),
            stale: _raw("tiktok", stale, OLD, text="labubu dolls"),
        })
        result = _search(net, envelope)
        assert stale not in net.hydrated_urls()  # dropped by its id date, never fetched
        assert [i["url"] for i in result["items"]] == [fresh]
        assert result["items"][0]["_host_found"] is True

    def test_off_topic_host_posts_are_dropped(self, tmp_path):
        on = tt_url("a", FRESH, low=1)
        off = tt_url("b", FRESH, low=2)
        envelope = _read(_write(tmp_path, _envelope([on, off])))
        net = FakeNet([], hydrate={
            on: _raw("tiktok", on, FRESH, text="labubu dolls restock"),
            off: _raw("tiktok", off, FRESH, text="sourdough bread recipe"),
        })
        result = _search(net, envelope)
        assert [i["url"] for i in result["items"]] == [on]

    def test_host_urls_merge_with_engine_discovery_deduped_by_id(self, tmp_path):
        shared = tt_url("a", FRESH, low=1)
        engine_only = tt_url("c", FRESH, low=3)
        envelope = _read(_write(tmp_path, _envelope([shared])))
        net = FakeNet([shared, engine_only], hydrate={
            u: _raw("tiktok", u, FRESH, text="labubu dolls") for u in (shared, engine_only)
        })
        result = _search(net, envelope, config={"SERPER_API_KEY": "sp"})
        hydrated = net.hydrated_urls()
        assert hydrated.count(shared) == 1 and engine_only in hydrated
        by_url = {i["url"]: i for i in result["items"]}
        assert set(by_url) == {shared, engine_only}
        assert by_url[shared].get("_host_found") is True
        assert not by_url[engine_only].get("_host_found")

    def test_tiktok_creator_profiles_feed_creator_expansion(self, tmp_path):
        listed = tt_url("labubu_official", FRESH, low=9)
        envelope = _read(_write(tmp_path, _envelope(["https://www.tiktok.com/@labubu_official"])))
        playlist = json.dumps({"id": listed.rsplit("/", 1)[-1], "title": "new labubu dolls drop"})
        net = FakeNet([], hydrate={listed: _raw("tiktok", listed, FRESH, text="new labubu dolls drop")},
                      playlists={"labubu_official": playlist})
        result = _search(net, envelope, config={"LAST30DAYS_SHORTFORM_CREATOR_EXPANSION": "off"})
        assert net.listed_handles() == ["labubu_official"]
        assert [i["url"] for i in result["items"]] == [listed]
        assert result["items"][0]["_host_found"] is True

    def test_instagram_posts_come_from_the_same_envelope(self, tmp_path):
        reel = f"https://www.instagram.com/reel/{ig_shortcode(FRESH)}/"
        envelope = _read(_write(tmp_path, _envelope([reel, tt_url("a", FRESH)])))
        net = FakeNet([], hydrate={reel: _raw("instagram", reel, FRESH, text="labubu dolls unboxing")})
        result = _search(net, envelope, kind="instagram")
        assert net.hydrated_urls() == [reel]
        assert result["items"][0]["_host_found"] is True

    def test_native_host_without_urls_says_why_it_found_nothing(self):
        result = _search(FakeNet([]), None)
        assert result["items"] == []
        assert "--shortform-urls" in result["error"]

    def test_scrapecreators_key_keeps_the_envelope_unused(self, tmp_path):
        from lib import tiktok
        fresh = tt_url("dollfan", FRESH)
        envelope = _read(_write(tmp_path, _envelope([fresh])))
        sc_item = {"video_id": "1", "text": "labubu dolls", "url": "https://www.tiktok.com/@x/video/1",
                   "engagement": {"views": 5}}
        with mock.patch.object(tiktok, "search_tiktok", return_value={"items": [sc_item]}), \
             mock.patch.object(tiktok, "fetch_captions"), \
             mock.patch.object(shortform_oss, "search", side_effect=AssertionError("keyless must not run")):
            result = tiktok.search_and_enrich(
                TOPIC, FROM, TO, depth="quick", token="sc-test",
                config={"_shortform_envelope": envelope},
            )
        assert [i["video_id"] for i in result["items"]] == ["1"]


# ---------------------------------------------------------------------------
# Pipeline and footer
# ---------------------------------------------------------------------------


class TestPipeline:
    def _plan(self):
        return {
            "intent": "opinion", "freshness_mode": "balanced_recent", "cluster_mode": "story",
            "subqueries": [{"label": "primary", "search_query": TOPIC, "ranking_query": TOPIC,
                            "sources": ["tiktok"]}],
            "source_weights": {"tiktok": 1.0},
        }

    def test_host_found_items_reach_the_report_and_the_footer_names_the_lane(self, tmp_path):
        fresh = tt_url("dollfan", FRESH)
        envelope = _read(_write(tmp_path, _envelope([fresh])))
        net = FakeNet([], hydrate={fresh: _raw("tiktok", fresh, FRESH, text="labubu dolls haul")})
        with mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=True), \
             mock.patch("lib.http.request", side_effect=net.request), \
             mock.patch("lib.subproc.run_with_timeout", side_effect=net.run), \
             redirect_stderr(io.StringIO()):
            report = pipeline.run(
                topic=TOPIC, config=dict(NATIVE), depth="default", requested_sources=["tiktok"],
                mock=False, external_plan=self._plan(), web_backend="none", save_dir="",
                shortform_urls=envelope,
            )
        items = report.items_by_source.get("tiktok") or []
        assert [i.url for i in items] == [fresh]
        assert items[0].metadata["host_search"] is True
        footer = "\n".join(render._build_source_footer_lines(report))
        assert render.HOST_SEARCH_LABEL in footer

    def test_a_run_searching_neither_platform_ignores_the_envelope(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([tt_url("a", FRESH)])))
        err = io.StringIO()
        with redirect_stderr(err), pytest.raises(ValueError, match="No available planned sources"):
            pipeline.run(
                topic=TOPIC, config={"EXCLUDE_SOURCES": "tiktok,instagram"}, depth="default",
                requested_sources=None, mock=False, external_plan=self._plan(),
                web_backend="none", save_dir="", shortform_urls=envelope,
            )
        assert "envelope ignored (TikTok and Instagram are excluded" in err.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DIAG = {
    "available_sources": ["tiktok", "instagram"],
    "x_pending_browser_auth": False,
    "native_search": True,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": None,
    "safe": False,
}


def _cli(argv, tmp_path, *, run=None, environ=None, config=None):
    out, err = io.StringIO(), io.StringIO()
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cli.env, "get_config", return_value=dict(config or {})))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_DIR", cfg_dir))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_FILE", cfg_dir / ".env"))
        stack.enter_context(mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()))
        stack.enter_context(mock.patch.dict(
            os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1", **(environ or {})}, clear=False,
        ))
        stack.enter_context(mock.patch.object(sys, "argv", ["last30days.py", *argv]))
        stack.enter_context(mock.patch.object(cli.pipeline, "diagnose", return_value=dict(_DIAG)))
        stack.enter_context(mock.patch.object(
            cli.pipeline, "run", side_effect=run or AssertionError("pipeline.run must not run"),
        ))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(redirect_stderr(err))
        try:
            rc = cli.main()
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _fake_report(topic: str) -> schema.Report:
    return schema.Report(
        topic=topic, range_from=FROM, range_to=TO, generated_at="2026-11-15T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(reasoning_provider="local", planner_model="d", rerank_model="l"),
        query_plan=schema.QueryPlan(intent="general", freshness_mode="balanced_recent", cluster_mode="story",
                                    raw_topic=topic, subqueries=[], source_weights={}),
        clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
    )


def _capturing(store: dict):
    def fake_run(**kwargs):
        store.update(kwargs)
        return _fake_report(kwargs["topic"])
    return fake_run


class TestCli:
    def test_valid_envelope_reaches_run(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH), "https://example.com/x"]))
        store: dict = {}
        rc, _, err = _cli([TOPIC, "--shortform-urls", path], tmp_path, run=_capturing(store))
        assert rc == 0, err
        envelope = store["shortform_urls"]
        assert envelope.posts("tiktok") == [tt_url("a", FRESH)]
        assert envelope.counters["non-shortform"] == 1
        assert "ScrapeCreators is configured" not in err

    def test_ignored_when_neither_platform_is_searched(self, tmp_path):
        store: dict = {}
        missing = str(tmp_path / "nope.json")
        rc, _, err = _cli([TOPIC, "--search", "hackernews", "--shortform-urls", missing],
                          tmp_path, run=_capturing(store))
        assert rc == 0, err
        assert "--shortform-urls ignored: TikTok and Instagram are not searched" in err
        assert store.get("shortform_urls") is None

    def test_a_scrapecreators_key_gets_a_one_line_note(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH)]))
        store: dict = {}
        rc, _, err = _cli([TOPIC, "--shortform-urls", path], tmp_path, run=_capturing(store),
                          config={"SCRAPECREATORS_API_KEY": "sc-test"})
        assert rc == 0, err
        notes = [line for line in err.splitlines() if "--shortform-urls" in line]
        assert len(notes) == 1 and "ScrapeCreators is configured" in notes[0]
        assert "sc-test" not in err

    def test_wrong_schema_exits_2_without_echoing_values(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH)], schema="nope"))
        rc, _, err = _cli([TOPIC, "--shortform-urls", path], tmp_path)
        assert rc == 2
        assert path in err and "schema" in err and "without --shortform-urls" in err
        assert "nope" not in err

    def test_protected_dir_exits_2(self, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir(exist_ok=True)
        path = _write(cfg, _envelope([tt_url("a", FRESH)]))
        rc, _, err = _cli([TOPIC, "--shortform-urls", path], tmp_path)
        assert rc == 2
        assert "credential directory" in err

    def test_inline_json_exits_2(self, tmp_path):
        rc, _, err = _cli([TOPIC, "--shortform-urls", json.dumps(_envelope([tt_url("a", FRESH)]))], tmp_path)
        assert rc == 2
        assert "--shortform-urls accepts a file path" in err

    def test_comparison_run_exits_2(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH)], topic="labubu vs smiski"))
        rc, _, err = _cli(["labubu vs smiski", "--shortform-urls", path], tmp_path)
        assert rc == 2
        assert "single-topic" in err

    def test_hosted_backend_rejects_the_flag(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH)]))
        with mock.patch.object(cli.env, "read_secret_env", return_value="hosted-test-key"), \
             mock.patch("lib.hosted.run_hosted", side_effect=AssertionError("hosted must not run")):
            rc, _, err = _cli(
                [TOPIC, "--shortform-urls", path], tmp_path,
                environ={"LAST30DAYS_API_BASE": "https://hosted.example.test"},
            )
        assert rc == 2
        assert "--shortform-urls is not supported by the hosted backend" in err

    def test_cached_report_refuses_the_flag(self, tmp_path):
        path = _write(tmp_path, _envelope([tt_url("a", FRESH)]))
        synth = tmp_path / "synth.md"
        synth.write_text("# s\n")
        rc, _, err = _cli(
            [TOPIC, "--cached-report", "--emit", "html", "--synthesis-file", str(synth),
             "--shortform-urls", path], tmp_path,
        )
        assert rc == 2
        assert "--cached-report" in err

    def test_the_envelope_digest_binds_the_report_cache(self, tmp_path):
        first = _read(_write(tmp_path, _envelope([tt_url("a", FRESH)]), "first.json"))
        second = _read(_write(tmp_path, _envelope([tt_url("b", FRESH)]), "second.json"))
        assert cli._envelope_digest(None, None, None, None) is None
        one = cli._envelope_digest(None, None, None, first)
        assert one and one != cli._envelope_digest(None, None, None, second)
