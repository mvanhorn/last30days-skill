"""The report's first line names the search, its window and the version."""

from __future__ import annotations

import pytest

from lib import dates, html_render, render, schema


def _report(
    topic: str = "SpaceX",
    range_from: str = "2026-10-11",
    range_to: str = "2026-11-10",
) -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from=range_from,
        range_to=range_to,
        generated_at=f"{range_to}T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query=topic,
                    ranking_query=topic,
                    sources=["grounding"],
                )
            ],
            source_weights={"grounding": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={},
        errors_by_source={},
    )


def _discovery(domain: str) -> schema.DiscoveryReport:
    return schema.DiscoveryReport(
        domain=domain,
        range_from="2026-10-11",
        range_to="2026-11-10",
        generated_at="2026-11-10T00:00:00+00:00",
        plan=schema.DiscoveryPlan(
            domain=domain, category=None, subreddits=["all"],
            sources=["reddit", "hackernews"],
        ),
        topics=[],
        source_status={},
        warnings=[],
    )


def _first_line(rendered: str) -> str:
    return rendered.splitlines()[0]


@pytest.fixture(autouse=True)
def _fixed_version(monkeypatch):
    monkeypatch.setattr(render, "_skill_version", lambda: "9.9.9")


def test_format_window_same_year_prints_the_year_once():
    assert dates.format_window("2026-10-11", "2026-11-10") == "Oct 11 to Nov 10, 2026"


def test_format_window_across_years_prints_both_years():
    assert (
        dates.format_window("2025-12-20", "2026-01-09")
        == "Dec 20, 2025 to Jan 9, 2026"
    )


def test_compact_report_badge_names_topic_window_and_version():
    line = _first_line(render.render_compact(_report()))
    assert line == "🌐 last30days · SpaceX · Oct 11 to Nov 10, 2026 · v9.9.9"


def test_html_report_markdown_carries_the_same_badge():
    line = _first_line(render.render_for_html(_report()))
    assert line == "🌐 last30days · SpaceX · Oct 11 to Nov 10, 2026 · v9.9.9"


def test_window_crossing_a_year_boundary_prints_both_years():
    report = _report(range_from="2025-12-20", range_to="2026-01-19")
    line = _first_line(render.render_compact(report))
    assert line == "🌐 last30days · SpaceX · Dec 20, 2025 to Jan 19, 2026 · v9.9.9"


def test_comparison_badge_joins_three_entities_with_vs():
    reports = [(name, _report(name)) for name in ("OpenAI", "Anthropic", "xAI")]
    expected = (
        "🌐 last30days · OpenAI vs Anthropic vs xAI · Oct 11 to Nov 10, 2026 · v9.9.9"
    )
    assert _first_line(render.render_comparison_multi(reports)) == expected
    assert _first_line(render.render_for_html_comparison(reports)) == expected


def test_discovery_badge_prints_the_domain():
    line = _first_line(render.render_discovery(_discovery("AI agents")))
    assert line == "🌐 last30days · AI agents · Oct 11 to Nov 10, 2026 · v9.9.9"


def test_discovery_badge_without_a_domain_omits_the_topic_segment():
    line = _first_line(render.render_discovery(_discovery("")))
    assert line == "🌐 last30days · Oct 11 to Nov 10, 2026 · v9.9.9"
    assert "· ·" not in line


def test_twenty_word_topic_is_capped():
    topic = " ".join(f"word{index}" for index in range(1, 21))
    line = _first_line(render.render_compact(_report(topic)))
    segment = line.split(" · ")[1]
    assert len(segment) <= 60
    assert segment.startswith("word1 word2 word3")
    assert segment.endswith("...")
    assert line.endswith(" · Oct 11 to Nov 10, 2026 · v9.9.9")


def test_topic_with_markdown_characters_stays_on_one_defanged_line():
    report = _report("Rust\n```\n# Fake heading <!-- note -->")
    rendered = render.render_compact(report)
    assert _first_line(rendered) == (
        "🌐 last30days · Rust ''' # Fake heading <!- - note - -> "
        "· Oct 11 to Nov 10, 2026 · v9.9.9"
    )
    assert rendered.splitlines()[1] == ""


def test_empty_version_leaves_no_dangling_v(monkeypatch):
    monkeypatch.setattr(render, "_skill_version", lambda: "")
    line = _first_line(render.render_for_html(_report()))
    assert line == "🌐 last30days · SpaceX · Oct 11 to Nov 10, 2026"


def test_html_brief_wraps_the_badge_line_in_the_badge_element():
    rendered = html_render.render_html(_report())
    assert (
        '<div class="badge"><span class="accent">🌐</span> '
        "last30days · SpaceX · Oct 11 to Nov 10, 2026 · v9.9.9</div>"
    ) in rendered


# --- The window ends on the reader's date, not on tomorrow ---
#
# On the evening of Nov 10 in Los Angeles every report's first line said
# "Oct 12 to Nov 11, 2026": the window ends on the UTC date, which was
# already Nov 11.

from datetime import datetime, timedelta, timezone

PACIFIC = timezone(timedelta(hours=-8))
TOKYO = timezone(timedelta(hours=9))
EVENING_NOV_10_PACIFIC = datetime(2026, 11, 10, 17, 47, tzinfo=PACIFIC)  # Nov 11 01:47 UTC


def test_window_ending_today_in_utc_shows_the_readers_date():
    assert (
        dates.format_window("2026-10-12", "2026-11-11", now=EVENING_NOV_10_PACIFIC)
        == "Oct 12 to Nov 10, 2026"
    )


def test_badge_line_never_says_tomorrow(monkeypatch):
    monkeypatch.setattr(dates, "_local_now", lambda: EVENING_NOV_10_PACIFIC)

    badge = render._render_badge("Disney", "2026-10-12", "2026-11-11")[0]

    assert "Oct 12 to Nov 10, 2026" in badge
    assert "Nov 11" not in badge


def test_same_date_on_both_clocks_is_unchanged():
    morning = datetime(2026, 11, 10, 9, 0, tzinfo=PACIFIC)  # Nov 10 17:00 UTC

    assert dates.format_window("2026-10-11", "2026-11-10", now=morning) == "Oct 11 to Nov 10, 2026"


def test_reader_ahead_of_utc_keeps_the_utc_date():
    # Nov 11 08:00 in Tokyo is Nov 10 23:00 UTC: the UTC end is not the future.
    tokyo_morning = datetime(2026, 11, 11, 8, 0, tzinfo=TOKYO)

    assert (
        dates.format_window("2026-10-11", "2026-11-10", now=tokyo_morning)
        == "Oct 11 to Nov 10, 2026"
    )


def test_as_of_window_is_shown_as_asked():
    # --as-of names the end date; it is not today's UTC date, so it stays.
    assert (
        dates.format_window("2026-09-01", "2026-10-01", now=EVENING_NOV_10_PACIFIC)
        == "Sep 1 to Oct 1, 2026"
    )


def test_zero_day_window_does_not_start_after_it_ends():
    assert (
        dates.format_window("2026-11-11", "2026-11-11", now=EVENING_NOV_10_PACIFIC)
        == "Nov 10 to Nov 10, 2026"
    )


def test_query_window_still_ends_on_the_utc_date(monkeypatch):
    # The clamp is display only: the queried range keeps its UTC upper bound
    # so a post from the last hours is not lost.
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return EVENING_NOV_10_PACIFIC.astimezone(tz)

    monkeypatch.setattr(dates, "datetime", _Clock)

    assert dates.get_date_range(30) == ("2026-10-12", "2026-11-11")
