"""Patient Zero: the spread timeline reports a lower bound and says so."""

import sys
from types import SimpleNamespace
from unittest import mock

import pytest

import last30days as cli
from lib import origin
from tests.skill_contract import reference_text


def _item(source, day, url, *, title="Widget leak thread", author="someone",
          confidence="high", container=""):
    return SimpleNamespace(
        source=source, published_at=day, url=url, title=title, author=author,
        container=container, date_confidence=confidence,
    )


def _candidate(engagement, *items):
    return SimpleNamespace(engagement=engagement, source_items=list(items))


def _build(candidates, range_from="2026-10-10", range_to="2026-11-09", incomplete=()):
    return origin.build_timeline(
        candidates, range_from, range_to, lambda c: c.engagement, incomplete_sources=incomplete,
    )


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
    assert [(h["source"], h["day"].isoformat()) for h in timeline["hops"]] == [
        ("hackernews", "2026-10-18"), ("x", "2026-10-20"),
        ("reddit", "2026-10-22"), ("tiktok", "2026-10-25"),
    ]
    assert timeline["peak_day"].isoformat() == "2026-10-25"
    assert timeline["breakout"]["url"] == "https://tiktok.test/1"
    # Duplicate URL counted once; market, low-confidence, and undated posts left out.
    assert timeline["sighting_count"] == 5
    assert timeline["undated_count"] == 3
    assert timeline["at_window_edge"] is False


def test_market_and_listing_dates_never_anchor_an_origin():
    assert _build([_candidate(70, _item(source, "2026-10-01", f"https://{source}.test/1"))
                   for source in origin.EXCLUDED_SOURCES]) is None
    assert _build([]) is None
    assert _build([_candidate(1, _item("x", "2026-10-20", ""))]) is None


def test_rendered_trace_never_claims_an_origin():
    text = "\n".join(origin.render_timeline(_build(CANDIDATES), _label))
    assert text.startswith("## Spread Timeline")
    assert '"Earliest found" is the earliest post this search returned, not proof of the origin' in text
    assert "- Earliest found: 2026-10-18 - HACKERNEWS by early_bird" in text
    assert "1. 2026-10-18 (start)" in text
    assert "2. 2026-10-20 (+2 days)" in text
    assert "3. 2026-10-22 (+2 days) - REDDIT in r/widgets" in text
    assert "- Peak day: 2026-10-25 - 1 post, 5,000 combined engagement" in text
    assert "3 on-topic posts had no reliable date" in text
    assert "started by" not in text.lower() and "patient zero" not in text.lower()
    assert "—" not in text and "–" not in text


def test_limits_name_every_reason_the_true_start_may_be_earlier():
    edge = _build([_candidate(5, _item("x", "2026-10-11", "https://x.test/1", title="t" * 300))],
                  incomplete=["reddit", "polymarket", "reddit"])
    assert edge["at_window_edge"] is True
    assert edge["incomplete_sources"] == ["reddit"]
    text = "\n".join(origin.render_timeline(edge, _label))
    assert "almost certainly began before it" in text
    assert "Only one platform returned dated posts" in text
    assert "may hold earlier posts: REDDIT." in text
    assert "t" * 107 + "..." in text and "t" * 111 not in text
    clean = "\n".join(origin.render_timeline(
        _build([_candidate(0, _item("x", "2026-10-20", "https://x.test/1")),
                _candidate(0, _item("reddit", "2026-10-20", "https://reddit.test/1"))]), _label))
    assert "Limits of this trace" not in clean
    assert "(same day)" in clean and "engagement]" not in clean


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
    envelope = traced.split("<!-- EVIDENCE FOR SYNTHESIS", 1)[1].split("<!-- END EVIDENCE FOR SYNTHESIS -->", 1)[0]
    assert "## Spread Timeline" in envelope
    assert "First sighting per platform, in order:" in envelope
    assert "Polymarket by" not in envelope.split("## Spread Timeline", 1)[1].split("\n## ", 1)[0]


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
])
def test_trace_origin_is_never_a_silent_no_op(monkeypatch, capsys, argv):
    with mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("research ran")):
        rc, _, err = _run(monkeypatch, capsys, [*argv, "--trace-origin", "--mock", "--save-dir", ""])
    assert rc == 2 and "--trace-origin applies to single-topic research runs" in err


# --- skill contract ----------------------------------------------------------------


def test_reference_keeps_the_trace_honest_and_people_safe():
    text = reference_text("origin")
    for rule in (
        "add `--trace-origin` to the engine command",
        "`## Spread Timeline`",
        "Re-run once with `--lookback-days 90`",
        'Always the words "earliest found"',
        "Order in time is not proof that one post caused the next",
        "Relay every line of the engine's `Limits of this trace`",
        "do not single out a private person as its author",
        "A trace is a lower bound",
    ):
        assert rule in text, rule
    assert "—" not in text and "–" not in text
