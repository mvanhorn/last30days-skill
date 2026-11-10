"""Patient Zero: the spread timeline reports a lower bound and says so."""

import json
import sys
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

import last30days as cli
from lib import origin, pipeline, render
from tests.skill_contract import reference_text


def _item(source, day, url, *, title="Widget leak thread", author="someone",
          confidence="high", container="", engagement=0, metadata=None, body=""):
    return SimpleNamespace(
        source=source, published_at=day, url=url, title=title, author=author,
        container=container, date_confidence=confidence, engagement=engagement,
        metadata=metadata or {}, body=body,
    )


def _candidate(engagement, *items):
    """A candidate whose items default to the given engagement."""
    for item in items:
        if not item.engagement:
            item.engagement = engagement
    return SimpleNamespace(source_items=list(items))


def _build(candidates, range_from="2026-10-10", range_to="2026-11-09", incomplete=(), reach=None):
    return origin.build_timeline(
        candidates, range_from, range_to, lambda item: item.engagement,
        incomplete_sources=incomplete, reach=reach,
    )


def _text(timeline):
    return "\n".join(origin.render_timeline(timeline, _label))


def _label(source):
    return source.upper()


CANDIDATES = [
    _candidate(900, _item("x", "2026-10-22T14:00:00Z", "https://x.test/2", author="bigaccount")),
    _candidate(12, _item("hackernews", "2026-10-18", "https://hn.test/1", author="early_bird")),
    _candidate(40, _item("x", "2026-10-20", "https://x.test/1")),
    _candidate(300, _item("reddit", "2026-10-22", "https://reddit.test/1", author="", container="r/widgets")),
    _candidate(5000, _item("tiktok", "2026-10-25", "https://tiktok.test/1")),
    _candidate(70, _item("polymarket", "2026-09-01", "https://polymarket.test/1")),
    _candidate(70, _item("reddit", "2026-10-12", "https://reddit.test/guess", confidence="low")),
    _candidate(70, _item("youtube", None, "https://youtube.test/1")),
    _candidate(70, _item("x", "not-a-date", "https://x.test/3")),
    _candidate(1, _item("x", "2026-10-20", "https://x.test/1")),
]


def test_timeline_orders_first_sightings_and_finds_the_peak():
    timeline = _build(CANDIDATES)
    assert timeline["earliest"]["url"] == "https://hn.test/1"
    assert [(h["source"], h["day"]) for h in timeline["hops"]] == [
        ("hackernews", "2026-10-18"), ("x", "2026-10-20"),
        ("reddit", "2026-10-22"), ("tiktok", "2026-10-25"),
    ]
    assert timeline["peak_day"] == "2026-10-25"
    assert timeline["breakout"]["url"] == "https://tiktok.test/1"
    # Duplicate URL counted once; market, low-confidence, and undated posts left out.
    assert timeline["sighting_count"] == 5
    assert timeline["undated_count"] == 3
    assert timeline["at_window_edge"] is False


def test_each_post_keeps_its_own_engagement():
    merged = SimpleNamespace(source_items=[
        _item("web", "2026-10-20", "https://blog.test/post", engagement=0),
        _item("hackernews", "2026-10-21", "https://blog.test/post", engagement=640,
              metadata={"hn_url": "https://news.ycombinator.com/item?id=2"}),
    ])
    timeline = _build([merged])
    assert [(h["source"], h["engagement"]) for h in timeline["hops"]] == [("web", 0.0), ("hackernews", 640.0)]
    assert timeline["peak_day"] == "2026-10-21"
    assert timeline["breakout"]["url"] == "https://news.ycombinator.com/item?id=2"


def test_separate_hacker_news_submissions_of_one_article_are_separate_posts():
    late = _candidate(900, _item("hackernews", "2026-10-25", "https://blog.test/post", author="late",
                                 metadata={"hn_url": "https://news.ycombinator.com/item?id=2"}))
    early = _candidate(3, _item("hackernews", "2026-10-15", "https://blog.test/post", author="early",
                                metadata={"hn_url": "https://news.ycombinator.com/item?id=1"}))
    timeline = _build([late, early])
    assert timeline["sighting_count"] == 2
    assert "author" not in timeline["earliest"]
    assert timeline["earliest"]["url"] == "https://news.ycombinator.com/item?id=1"
    # Without a discussion URL the article URL is still a usable identity.
    assert _build([_candidate(1, _item("hackernews", "2026-10-15", "https://blog.test/post"))])["earliest"]["url"] == "https://blog.test/post"


def test_every_platform_is_listed_however_many_report():
    sources = [f"source{n:02d}" for n in range(14)]
    timeline = _build([
        _candidate(1, _item(source, f"2026-10-{11 + n}", f"https://{source}.test/1"))
        for n, source in enumerate(sources)
    ])
    assert [hop["source"] for hop in timeline["hops"]] == sources


def test_market_and_listing_dates_never_anchor_an_origin():
    assert _build([_candidate(70, _item(source, "2026-10-01", f"https://{source}.test/1"))
                   for source in origin.EXCLUDED_SOURCES]) is None
    assert _build([]) is None
    assert _build([_candidate(1, _item("x", "2026-10-20", ""))]) is None


def test_rendered_trace_never_claims_an_origin():
    text = _text(_build(CANDIDATES))
    assert text.startswith("## Spread Timeline")
    assert '"Earliest found" is the earliest post this search returned, not proof of the origin' in text
    assert (
        '- Earliest found: 2026-10-18 - HACKERNEWS: "Widget leak thread" [12 engagement] - '
        "[HACKERNEWS post](https://hn.test/1)"
    ) in text
    assert "early_bird" not in text and "bigaccount" not in text and "someone" not in text
    assert "1. 2026-10-18 (start)" in text
    assert "2. 2026-10-20 (+2 days)" in text
    assert "3. 2026-10-22 (+2 days) - REDDIT in r/widgets" in text
    assert "(searched 2026-10-10 to 2026-11-09)" in text
    assert "- Peak day: 2026-10-25 - 1 post, 5,000 combined engagement" in text
    assert "3 on-topic posts had no reliable date" in text
    assert "started by" not in text.lower() and "patient zero" not in text.lower()
    assert "—" not in text and "–" not in text


def test_limits_name_every_reason_the_true_start_may_be_earlier():
    edge = _build([_candidate(5, _item("x", "2026-10-11", "https://x.test/1", title="t" * 300))],
                  incomplete=["reddit", "polymarket", "reddit"])
    assert edge["at_window_edge"] is True
    assert edge["incomplete_sources"] == ["reddit"]
    text = _text(edge)
    assert "the search was not extended" in text
    assert "almost certainly began before it" in text
    assert "Only one platform returned dated posts" in text
    assert "may hold earlier posts: REDDIT." in text
    assert "t" * 107 + "..." in text and "t" * 111 not in text
    clean = _text(
        _build([_candidate(0, _item("x", "2026-10-20", "https://x.test/1")),
                _candidate(0, _item("reddit", "2026-10-20", "https://reddit.test/1"))]))
    assert "Limits of this trace" not in clean
    assert "(same day)" in clean and "engagement]" not in clean


def test_a_post_that_links_to_an_earlier_sighted_post_is_marked_as_carrying_it():
    timeline = _build([
        _candidate(3, _item("hackernews", "2026-10-18", "https://blog.test/post",
                            metadata={"hn_url": "https://news.ycombinator.com/item?id=41"})),
        _candidate(9, _item("x", "2026-10-20", "https://x.com/poster/status/900",
                            body="wild thread https://news.ycombinator.com/item?id=41 read it")),
        # A different submission whose id merely starts with the same digits.
        _candidate(5, _item("reddit", "2026-10-22", "https://reddit.test/1",
                            body="see https://news.ycombinator.com/item?id=412")),
        # A post whose link target is the earlier X post.
        _candidate(2, _item("web", "2026-10-24", "https://twitter.com/poster/status/900?s=20")),
    ])
    by_source = {hop["source"]: hop for hop in timeline["hops"]}
    assert "links_to" not in by_source["hackernews"]
    assert by_source["x"]["links_to"] == {
        "source": "hackernews", "day": "2026-10-18", "url": "https://news.ycombinator.com/item?id=41",
    }
    assert "links_to" not in by_source["reddit"]
    assert by_source["web"]["links_to"]["url"] == "https://x.com/i/status/900"
    assert [carry["source"] for carry in timeline["carries"]] == ["x", "web"]
    text = _text(timeline)
    assert "Posts that contain the address of an earlier sighted post:" in text
    assert (
        "- 2026-10-20 [X post](https://x.com/i/status/900) links to the 2026-10-18 "
        "[HACKERNEWS post](https://news.ycombinator.com/item?id=41)"
    ) in text
    assert "[REDDIT post](https://reddit.test/1) links to" not in text
    unlinked = _text(_build(CANDIDATES))
    assert "links to" not in unlinked and "contain the address" not in unlinked


def test_an_order_that_depends_on_a_shorter_searched_range_is_flagged():
    candidates = [
        _candidate(3, _item("hackernews", "2026-10-18", "https://hn.test/1")),
        _candidate(9, _item("x", "2026-11-05", "https://x.com/poster/status/900")),
        _candidate(4, _item("reddit", "2026-11-06", "https://reddit.test/1")),
    ]
    timeline = _build(candidates, reach={"x": "2026-11-02", "digg": "2026-11-02"})
    assert [(h["source"], h["searched_from"], h["order_uncertain"]) for h in timeline["hops"]] == [
        ("hackernews", "2026-10-10", False),
        ("x", "2026-11-02", True),
        ("reddit", "2026-10-10", False),
    ]
    assert timeline["reach"] == [
        {"source": "digg", "from": "2026-11-02", "to": "2026-11-09"},
        {"source": "hackernews", "from": "2026-10-10", "to": "2026-11-09"},
        {"source": "reddit", "from": "2026-10-10", "to": "2026-11-09"},
        {"source": "x", "from": "2026-11-02", "to": "2026-11-09"},
    ]
    text = _text(timeline)
    assert "(searched 2026-11-02 to 2026-11-09; order not established)" in text
    assert (
        "X was not searched as far back as the earlier first sightings, so its place in "
        "the order may come from the shorter search rather than from the story."
    ) in text
    assert (
        "Searched over a shorter range than the rest: DIGG (2026-11-02 to 2026-11-09), "
        "X (2026-11-02 to 2026-11-09)."
    ) in text
    even = _build(candidates)
    assert not any(hop["order_uncertain"] for hop in even["hops"])
    assert "order not established" not in _text(even) and "shorter range" not in _text(even)


def test_an_x_post_is_shown_without_its_handle_in_text_or_link():
    text = _text(_build([_candidate(4, _item(
        "x", "2026-10-20", "https://x.com/early_bird/status/1234567890",
        author="early_bird", title="the widget leak is real",
    ))]))
    assert "early_bird" not in text
    assert "[X post](https://x.com/i/status/1234567890)" in text


def test_a_tiktok_post_keeps_its_handle_only_inside_the_link_target():
    url = "https://www.tiktok.com/@dancer99/video/77"
    text = _text(_build([_candidate(4, _item("tiktok", "2026-10-20", url, author="dancer99"))]))
    assert f"[TIKTOK post]({url})" in text
    assert "dancer99" not in text.replace(f"({url})", "()")


def test_a_subreddit_or_channel_is_shown_as_a_venue():
    text = _text(_build([
        _candidate(4, _item("reddit", "2026-10-20", "https://reddit.test/1", author="", container="widgets")),
        _candidate(4, _item("youtube", "2026-10-21", "https://youtube.test/1", author="Widget Weekly")),
        _candidate(4, _item("hackernews", "2026-10-22", "https://hn.test/1", author="pg", container="hackernews")),
    ]))
    assert 'REDDIT in r/widgets: "Widget leak thread"' in text
    assert 'YOUTUBE in Widget Weekly: "Widget leak thread"' in text
    # A venue that only repeats the platform name adds nothing.
    assert 'HACKERNEWS: "Widget leak thread"' in text


# --- extending the search past the window ------------------------------------------


def _traced_report(candidates, *, sources=("hackernews", "x"), x_backend="bird"):
    return SimpleNamespace(
        topic="widget leak", range_from="2026-10-10", range_to="2026-11-09", artifacts={},
        query_plan=SimpleNamespace(
            freshness_mode="balanced_recent",
            subqueries=[SimpleNamespace(
                label="primary", search_query="widget leak", ranking_query="widget leak",
                sources=list(sources),
            )],
        ),
        provider_runtime=SimpleNamespace(x_search_backend=x_backend),
        candidates=candidates,
    )


def _trace(monkeypatch, report, fetch, *, mock_run=False, budget_seconds=5.0, config=None):
    """Run the trace with retrieval replaced by ``fetch(source, date_range)``."""
    calls = []

    def fake_stream(**kwargs):
        calls.append((kwargs["source"], kwargs["date_range"]))
        return fetch(kwargs["source"], kwargs["date_range"]), {}

    monkeypatch.setattr(render, "spread_timeline_inputs", lambda traced: (traced.candidates, []))
    monkeypatch.setattr(pipeline.schema, "item_headline_engagement", lambda item: item.engagement)
    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_stream)
    monkeypatch.setattr(pipeline, "_normalize_score_dedupe", lambda source, raw, *args, **kwargs: raw)
    pipeline.trace_origin(
        report, config=config or {}, mock=mock_run, budget_seconds=budget_seconds,
    )
    return report.artifacts["origin_trace"], calls


def _edge_candidates():
    # Day 29 of a 30-day window that starts 2026-10-10.
    return [
        _candidate(40, _item("x", "2026-10-11", "https://x.com/poster/status/1")),
        _candidate(12, _item("hackernews", "2026-10-15", "https://hn.test/1")),
    ]


def test_an_earliest_sighting_at_the_window_edge_extends_the_search(monkeypatch):
    def fetch(source, date_range):
        if source == "hackernews":
            return [
                _item("hackernews", "2026-09-01", "https://hn.test/0", engagement=7),
                # Already inside the original window: that pool was judged by the main run.
                _item("hackernews", "2026-10-20", "https://hn.test/2", engagement=7),
            ]
        return []

    report = _traced_report(_edge_candidates())
    timeline, calls = _trace(monkeypatch, report, fetch)
    assert sorted(calls) == [
        ("hackernews", ("2026-08-11", "2026-11-09")),
        ("x", ("2026-08-11", "2026-11-09")),
    ]
    assert timeline["earliest"]["url"] == "https://hn.test/0"
    assert timeline["earliest"]["day"] == "2026-09-01"
    assert timeline["range_from"] == "2026-08-11"
    assert timeline["sighting_count"] == 3
    assert timeline["extension"] == {
        "window_from": "2026-10-10", "days": 90, "sources": ["hackernews", "x"],
        "stopped": "", "failed_sources": [],
    }
    assert report.artifacts["trace_origin"] is True
    text = _text(timeline)
    assert "between 2026-08-11 and 2026-11-09" in text
    assert (
        "The earliest post sat at the start of the original window (2026-10-10), so the search "
        "was extended to 90 days on: HACKERNEWS, X."
    ) in text
    assert "(searched 2026-08-11 to 2026-11-09)" in text
    assert "almost certainly began before it" not in text
    json.dumps(timeline)


def test_a_trail_still_at_the_edge_of_90_days_is_searched_over_365(monkeypatch):
    def fetch(source, date_range):
        if source != "hackernews":
            return []
        if date_range[0] == "2026-08-11":
            return [_item("hackernews", "2026-08-12", "https://hn.test/90", engagement=7)]
        return [_item("hackernews", "2026-03-03", "https://hn.test/365", engagement=7)]

    timeline, calls = _trace(monkeypatch, _traced_report(_edge_candidates()), fetch)
    assert sorted({date_range[0] for _source, date_range in calls}) == ["2025-11-09", "2026-08-11"]
    assert timeline["earliest"]["url"] == "https://hn.test/365"
    assert timeline["extension"]["days"] == 365


def test_an_earliest_sighting_mid_window_runs_no_second_pass(monkeypatch):
    # Day 12 of 30.
    candidates = [_candidate(40, _item("x", "2026-10-28", "https://x.com/poster/status/1"))]
    timeline, calls = _trace(monkeypatch, _traced_report(candidates), lambda *_: [])
    assert calls == []
    assert "extension" not in timeline and timeline["at_window_edge"] is False
    assert timeline["range_from"] == "2026-10-10"


def test_the_second_pass_stops_at_its_budget_and_says_the_trail_may_run_further(monkeypatch):
    release = threading.Event()

    def fetch(source, date_range):
        release.wait(10)
        return [_item("hackernews", "2026-09-01", "https://hn.test/0")]

    try:
        timeline, calls = _trace(
            monkeypatch, _traced_report(_edge_candidates()), fetch, budget_seconds=0.2,
        )
    finally:
        release.set()
    assert {date_range[0] for _source, date_range in calls} == {"2026-08-11"}
    assert timeline["extension"]["stopped"] == "budget"
    assert timeline["earliest"]["day"] == "2026-10-11"
    assert timeline["hops"][0]["searched_from"] == "2026-10-10"
    assert (
        "The extended search stopped at its time budget, so the trail may run further "
        "back than shown."
    ) in _text(timeline)


def test_a_failed_second_pass_becomes_a_limit_and_never_fails_the_run(monkeypatch):
    def fetch(source, date_range):
        if source == "hackernews":
            raise RuntimeError("algolia is down")
        return []

    timeline, _calls = _trace(monkeypatch, _traced_report(_edge_candidates()), fetch)
    assert timeline["extension"]["stopped"] == "failed"
    assert timeline["extension"]["failed_sources"] == ["hackernews"]
    assert (
        "The extended search failed on HACKERNEWS, so the trail may run further back than shown."
    ) in _text(timeline)


def test_a_source_capped_at_7_days_is_listed_with_that_range_and_not_extended(monkeypatch):
    candidates = [
        _candidate(12, _item("hackernews", "2026-10-11", "https://hn.test/1")),
        _candidate(40, _item("x", "2026-11-05", "https://x.com/poster/status/1")),
    ]
    report = _traced_report(candidates, sources=("hackernews", "x", "youtube"), x_backend="xapi")
    timeline, calls = _trace(monkeypatch, report, lambda *_: [])
    assert {source for source, _range in calls} == {"hackernews"}
    assert {"source": "x", "from": "2026-11-02", "to": "2026-11-09"} in timeline["reach"]
    assert {"source": "youtube", "from": "2026-10-10", "to": "2026-11-09"} in timeline["reach"]
    assert {"source": "hackernews", "from": "2026-08-11", "to": "2026-11-09"} in timeline["reach"]
    text = _text(timeline)
    assert "X (2026-11-02 to 2026-11-09)" in text
    assert "YOUTUBE (2026-10-10 to 2026-11-09)" in text


def test_a_mock_run_performs_no_second_pass(monkeypatch):
    timeline, calls = _trace(
        monkeypatch, _traced_report(_edge_candidates()), lambda *_: [], mock_run=True,
    )
    assert calls == []
    assert "extension" not in timeline and timeline["at_window_edge"] is True
    assert "the search was not extended" in _text(timeline)


# --- engine ----------------------------------------------------------------------


def _run(monkeypatch, capsys, argv):
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])
    try:
        rc = cli.main()
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_trace_origin_adds_the_block_inside_the_evidence_envelope(monkeypatch, capsys):
    base = ["widget", "--mock", "--emit=compact", "--save-dir", "", "--no-browser-cookies"]
    rc, plain, _ = _run(monkeypatch, capsys, base)
    assert rc == 0 and "## Spread Timeline" not in plain
    rc, traced, _ = _run(monkeypatch, capsys, [*base, "--trace-origin"])
    assert rc == 0
    for register in ("exec", "dev", "creator", "eli5"):
        rc, voiced, _ = _run(monkeypatch, capsys, [*base, "--trace-origin", f"--register={register}"])
        assert rc == 0 and "## Spread Timeline" in voiced, register
    # Reserved words research normally under --research-only, trace included.
    rc, reserved, _ = _run(monkeypatch, capsys, [
        "--research-only", "--mock", "--emit=compact", "--save-dir", "",
        "--no-browser-cookies", "--trace-origin", "--", "setup",
    ])
    assert rc == 0 and "## Spread Timeline" in reserved
    envelope = traced.split("<!-- EVIDENCE FOR SYNTHESIS", 1)[1].split("<!-- END EVIDENCE FOR SYNTHESIS -->", 1)[0]
    assert "## Spread Timeline" in envelope
    assert "First sighting per platform, in order, with the range that platform was searched over:" in envelope
    assert "[Polymarket post]" not in envelope.split("## Spread Timeline", 1)[1].split("\n## ", 1)[0]


def test_the_timeline_is_in_the_last_report_cache_after_the_run(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    rc, traced, _ = _run(monkeypatch, capsys, [
        "widget", "--mock", "--emit=compact", "--save-dir", "", "--no-browser-cookies", "--trace-origin",
    ])
    assert rc == 0
    cached = json.loads((tmp_path / "last-report.json").read_text())
    artifacts = cached["reports"][0]["report"]["artifacts"]
    assert artifacts["trace_origin"] is True
    stored = artifacts["origin_trace"]
    assert stored["hops"] and stored["earliest"]["url"] == stored["hops"][0]["url"]
    assert "extension" not in stored
    # The printed block is the stored timeline, not a second computation.
    assert "\n".join(origin.render_timeline(stored, render._source_label)) in traced


@pytest.mark.parametrize("argv", [
    ["widget", "--emit=json"],
    ["widget", "--emit=html"],
    ["widget vs gadget"],
    ["widget", "--competitors"],
    ["--discover"],
    ["--drill", "3"],
    ["doctor"],
    ["queue", "list"],
    ["library", "search", "widget"],
    ["forecast", "scorecard"],
    ["--diagnose"],
    ["widget", "--diagnose"],
    ["widget", "--preflight"],
    ["widget", "--resolve-save-dir"],
    ["--welcome"],
    ["setup"],
])
def test_trace_origin_is_never_a_silent_no_op(monkeypatch, capsys, argv):
    with mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("research ran")):
        rc, out, err = _run(monkeypatch, capsys, [*argv, "--trace-origin", "--mock", "--save-dir", ""])
    assert rc == 2 and "--trace-origin applies to single-topic research runs" in err and not out


def test_off_topic_cluster_members_cannot_lead_the_timeline(monkeypatch):
    from lib import render, schema

    on_topic = SimpleNamespace(source_items=[_item("x", "2026-10-20", "https://x.test/on")])
    off_topic = SimpleNamespace(source_items=[_item("x", "2026-10-12", "https://x.test/off")])
    monkeypatch.setattr(render, "_best_take_relevance_ok", lambda candidate: candidate is on_topic)
    monkeypatch.setattr(schema, "item_headline_engagement", lambda item: 1.0)
    monkeypatch.setattr(schema, "without_sources", lambda report, excluded: report)
    monkeypatch.setattr(render, "_auxiliary_candidate_pool", lambda report, visible, solid: report.pool)
    monkeypatch.setattr(render, "_clusters_clearing_relevance_floor", lambda report, clusters: [])

    def traced(pool):
        report = SimpleNamespace(
            topic="widget", range_from="2026-10-01", range_to="2026-10-31", source_status={},
            clusters=[], pool=pool, artifacts={},
            query_plan=SimpleNamespace(freshness_mode="balanced_recent", subqueries=[]),
            provider_runtime=SimpleNamespace(x_search_backend=None),
        )
        pipeline.trace_origin(report, config={}, mock=True)
        return "\n".join(render._render_spread_timeline(report))

    text = traced([off_topic, on_topic])
    assert "https://x.test/on" in text and "https://x.test/off" not in text
    assert "no trail to trace" in traced([off_topic])


# --- skill contract ----------------------------------------------------------------


def test_reference_keeps_the_trace_honest_and_people_safe():
    text = reference_text("origin")
    for rule in (
        "add `--trace-origin` to the engine command",
        "`## Spread Timeline`",
        "The engine extends the search on its own",
        "do not re-run with a longer window",
        "only where the engine marks that the later post links to the earlier one",
        "the range each platform was searched over",
        'Always the words "earliest found"',
        "Order in time is not proof that one post caused the next",
        "Relay every line of the engine's `Limits of this trace`",
        "do not single out a private person as its author",
        "A trace is a lower bound",
    ):
        assert rule in text, rule
    assert "Re-run once" not in text
    assert "—" not in text and "–" not in text
