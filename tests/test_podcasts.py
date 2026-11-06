"""Podcast library retrieval, opt-in policy, and report integration."""

import threading
import time
from unittest.mock import Mock

import pytest

from lib import dates, doctor, html_render, normalize, pipeline, podcasts, render, schema


WINDOW = ("2026-10-06", "2026-11-05")
EPISODE_URL = "https://exa.ai/library/podcast/show-1/episode/episode-1"
HEADER = """# Workout episode

- **Podcast:** [Huberman Lab](https://exa.ai/library/podcast/show-1)
- **Published:** 2026-11-05T09:00:00+00:00
- **Duration:** 38:51.664
- **Language:** en
- **Hosts:** [Andrew Huberman](https://exa.ai/library/person/host-1)
- **Guests:** [Susanna Soberg](https://exa.ai/library/person/guest-1), Guest Two

## Transcript

**Andrew Huberman 00:00.251-38:03.542**

Workout training advice from this episode.
"""


def episode(number=1, **overrides):
    return {
        "id": f"episode-{number}",
        "title": "Workout episode",
        "url": EPISODE_URL.replace("episode-1", f"episode-{number}"),
        "publishedDate": "2026-11-05T09:00:00+00:00",
        "text": HEADER,
        "highlights": ["Workout training advice.", "A second passage."],
        **overrides,
    }


def search(monkeypatch, rows, count=8):
    request = Mock(return_value={"results": rows})
    monkeypatch.setattr(podcasts.http, "request", request)
    items, artifact = podcasts.search_podcasts("workout", WINDOW, "dummy-key", count)
    return items, artifact, request


def test_exact_request_and_artifact(monkeypatch):
    items, artifact, request = search(monkeypatch, [episode()], count=3)
    request.assert_called_once_with(
        "POST", "https://api.exa.ai/search",
        headers={"x-api-key": "dummy-key"},
        json_data={
            "query": "workout", "type": "auto",
            "includeDomains": ["exa.ai/library/podcast"], "numResults": 6,
            "startPublishedDate": "2026-10-06T00:00:00.000Z",
            "endPublishedDate": "2026-11-05T23:59:59.999Z",
            "contents": {
                "highlights": {"query": "workout", "maxCharacters": 1500},
                "text": {"maxCharacters": 1000},
            },
        }, timeout=15,
        deadline_monotonic=None,
        cancel=None,
    )
    assert items[0]["id"] == "PC1"
    assert artifact == {"label": "exa-podcasts", "webSearchQueries": ["workout"], "resultCount": 1}


def test_episode_filter_url_dedupe_and_count_cap(monkeypatch):
    rows = [
        None, {}, episode(url=""), episode(url="https://[invalid"),
        episode(url="https://exa.ai/library/podcast/show-1"),
        episode(url="https://example.com/library/podcast/show-1/episode/fake"),
        episode(), episode(title="Same URL with a different title"),
        episode(2), episode(3),
    ]
    items, artifact, _ = search(monkeypatch, rows, count=2)
    assert [i["url"] for i in items] == [episode()["url"], episode(2)["url"]]
    assert [i["id"] for i in items] == ["PC1", "PC2"]
    assert artifact["resultCount"] == 2


def test_show_hosts_guests_metadata(monkeypatch):
    items, _, _ = search(monkeypatch, [episode()])
    assert items[0]["metadata"] == {
        "show": "Huberman Lab", "hosts": ["Andrew Huberman"],
        "guests": ["Susanna Soberg", "Guest Two"],
    }
    normalized = normalize.normalize_source_items("podcasts", items, *WINDOW)[0]
    assert normalized.container == "Huberman Lab"
    assert normalized.metadata == items[0]["metadata"]
    assert normalized.author is None


def test_plain_text_header_names(monkeypatch):
    text = "- **Podcast:** Modern Wisdom\n- **Hosts:** Chris Williamson\n- **Guests:** Guest One\n"
    items, _, _ = search(monkeypatch, [episode(text=text)])
    assert items[0]["metadata"] == {
        "show": "Modern Wisdom", "hosts": ["Chris Williamson"], "guests": ["Guest One"],
    }


@pytest.mark.parametrize("text", [None, {}, "", "- **Podcast:** [Broken](\n- **Hosts:** [Oops\n"])
def test_malformed_header_survives_with_domain_fallback(monkeypatch, text):
    items, _, _ = search(monkeypatch, [episode(text=text)])
    assert len(items) == 1
    assert items[0]["metadata"]["show"] == ""
    normalized = normalize.normalize_source_items("podcasts", items, *WINDOW)[0]
    assert normalized.container == "exa.ai"


def test_highlights_joined_and_capped(monkeypatch):
    items, _, _ = search(monkeypatch, [episode(), episode(2, highlights=["x" * 900, "y" * 900])])
    assert items[0]["snippet"] == "Workout training advice. A second passage."
    assert items[1]["snippet"] == ("x" * 900 + " " + "y" * 900)[:1200]


def test_transcript_fallback_excludes_header(monkeypatch):
    items, _, _ = search(monkeypatch, [episode(highlights=[]), episode(2, highlights=None, text=HEADER.split("## Transcript")[0])])
    assert items[0]["snippet"].startswith("**Andrew Huberman 00:00.251")
    assert "Workout training advice" in items[0]["snippet"]
    assert "**Podcast:**" not in items[0]["snippet"]
    assert "**Duration:**" not in items[0]["snippet"]
    assert items[1]["snippet"] == ""


def test_transcript_is_not_parsed_as_header(monkeypatch):
    items, _, _ = search(monkeypatch, [episode(text="## Transcript\n- **Podcast:** Not a show\n", highlights=[])])
    assert items[0]["metadata"]["show"] == ""


@pytest.mark.parametrize("published", [None, "", "invalid", "2026-02-30", "2026-10-05", "2026-11-06"])
def test_undated_invalid_and_outside_window_dropped(monkeypatch, published):
    items, artifact, _ = search(monkeypatch, [episode(publishedDate=published)])
    assert items == []
    assert artifact["resultCount"] == 0


def test_date_boundaries_kept(monkeypatch):
    items, _, _ = search(monkeypatch, [episode(publishedDate=WINDOW[0]), episode(2, publishedDate=WINDOW[1])])
    assert [i["date"] for i in items] == list(WINDOW)


def test_normalization_requires_date():
    assert normalize.normalize_source_items("podcasts", [{"id": "PC1", "url": EPISODE_URL}], *WINDOW) == []


def test_default_off_and_alias():
    assert "podcasts" not in pipeline.available_sources({"EXA_API_KEY": "dummy-key"})
    assert pipeline.normalize_requested_sources(["podcast", "podcasts"]) == ["podcasts"]
    assert "podcasts" not in pipeline.run(topic="test topic", config={"EXA_API_KEY": "dummy-key"}, depth="quick", mock=True).items_by_source


@pytest.mark.parametrize("config,requested", [
    ({"EXA_API_KEY": "dummy-key", "INCLUDE_SOURCES": "podcasts"}, None),
    ({"EXA_API_KEY": "dummy-key"}, ["podcasts"]),
])
def test_opt_in_availability(config, requested):
    assert "podcasts" in pipeline.available_sources(config, requested)


def test_explicit_search_omitting_podcasts_excludes_it(monkeypatch):
    config = {"EXA_API_KEY": "dummy-key", "INCLUDE_SOURCES": "podcasts"}
    assert "podcasts" not in pipeline.available_sources(config, ["reddit"])
    request = Mock(side_effect=AssertionError("podcasts must not be called"))
    monkeypatch.setattr(podcasts, "search_podcasts", request)
    report = pipeline.run(topic="test topic", config=config, depth="quick", mock=True, requested_sources=["reddit"])
    assert "podcasts" not in report.items_by_source
    request.assert_not_called()


def test_missing_key_uses_typed_unavailable(monkeypatch):
    assert "podcasts" not in pipeline.available_sources({"INCLUDE_SOURCES": "podcasts"}, ["podcasts"])
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a: (pipeline.providers.mock_runtime({}, "quick"), None))
    with pytest.raises(pipeline.NoRequestedSourceAvailable, match="podcasts"):
        pipeline.run(topic="test topic", config={}, depth="quick", requested_sources=["podcasts"])


@pytest.mark.parametrize("options", [
    {"web_backend": "none"},
    {"config": {"EXCLUDE_SOURCES": "podcasts", "INCLUDE_SOURCES": "podcasts"}},
])
def test_web_none_and_exclude_win(options):
    kwargs = {"config": {"INCLUDE_SOURCES": "podcasts"}, **options}
    report = pipeline.run(topic="test topic", depth="quick", mock=True, **kwargs)
    assert "podcasts" not in report.items_by_source
    assert all("podcasts" not in sq.sources for sq in report.query_plan.subqueries)


def test_plan_fallback_preserves_existing_route():
    primary = schema.SubQuery(label="primary", search_query="workout", ranking_query="workout", sources=["reddit"])
    plan = schema.QueryPlan(raw_topic="workout", intent="opinion", freshness_mode="balanced_recent",
                            cluster_mode="debate", subqueries=[primary], source_weights={"reddit": 1.0})
    pipeline._ensure_podcasts_in_plan(plan, ["reddit", "podcasts"])
    assert primary.sources == ["reddit", "podcasts"]
    assert "podcasts" in plan.source_weights
    pipeline._ensure_podcasts_in_plan(plan, ["reddit", "podcasts"])
    assert primary.sources.count("podcasts") == 1


def test_host_plan_backfills_opted_in_source():
    report = pipeline.run(topic="test topic", config={"INCLUDE_SOURCES": "podcasts"}, depth="default", mock=True, external_plan={
        "intent": "opinion", "freshness_mode": "balanced_recent", "cluster_mode": "debate",
        "subqueries": [{"label": "primary", "search_query": "test topic", "ranking_query": "test topic", "sources": ["reddit"]}],
    })
    assert "podcasts" in report.items_by_source


def test_episode_survives_pipeline_and_report_formats(monkeypatch):
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a: (pipeline.providers.mock_runtime({}, "quick"), None))
    monkeypatch.setattr(podcasts.http, "request", Mock(return_value={"results": [episode(publishedDate=dates.get_date_range(1)[1])]}))
    report = pipeline.run(topic="workout", config={"EXA_API_KEY": "dummy-key"}, depth="quick", requested_sources=["podcasts"])
    assert report.source_status["podcasts"].state == "ok"
    assert report.ranked_candidates[0].source == "podcasts"
    footer = "🎙️ Podcasts: 1 episode - Huberman Lab"
    assert footer in render.render_compact(report)
    assert footer in html_render.render_html(report)
    full = render.render_full(report)
    assert "### Podcasts (1 items)" in full
    assert "shows: Huberman Lab" in full
    assert "*Huberman Lab*" in full
    assert "hosts" in schema.to_dict(report)["items_by_source"]["podcasts"][0]["metadata"]


def test_raised_error_does_not_abort_pipeline(monkeypatch):
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a: (pipeline.providers.mock_runtime({}, "quick"), None))
    reddit_items, _ = pipeline._mock_stream_results("reddit", schema.SubQuery(label="primary", search_query="test topic", ranking_query="test topic", sources=["reddit"]))
    monkeypatch.setattr(pipeline.reddit_public, "search_reddit_public", lambda *a, **k: reddit_items)
    request = Mock(side_effect=RuntimeError("Podcast service unavailable"))
    monkeypatch.setattr(podcasts.http, "request", request)
    report = pipeline.run(topic="test topic", config={"EXA_API_KEY": "dummy-key"}, depth="quick", requested_sources=["reddit", "podcasts"])
    request.assert_called_once()
    assert report.errors_by_source["podcasts"] == "Podcast service unavailable"
    assert report.items_by_source["reddit"]
    assert report.ranked_candidates
    assert "Some sources failed: podcasts" in report.warnings


@pytest.mark.parametrize("stopped_by", ["cancel", "deadline"])
def test_stopped_run_skips_podcast_request(monkeypatch, stopped_by):
    request = Mock(side_effect=AssertionError("no new request after cancellation"))
    monkeypatch.setattr(podcasts.http, "request", request)
    cancel = threading.Event()
    cancel.set()
    # Exercise the same dispatch guard used before Exa web retrieval.
    config = {"EXA_API_KEY": "dummy-key"}
    if stopped_by == "cancel":
        config["_enrich_cancel"] = cancel
    else:
        config["_research_deadline"] = time.monotonic() - 1
    items, artifact = pipeline._retrieve_stream_impl(
        source="podcasts", subquery=schema.SubQuery(label="primary", search_query="workout", ranking_query="workout", sources=["podcasts"]),
        date_range=WINDOW, config=config, depth="quick", mock=False,
        topic="workout", runtime=pipeline.providers.mock_runtime({}, "quick"),
    )
    assert items == []
    assert artifact["_source_outcome"]["attempted"] is False
    request.assert_not_called()


def test_doctor_opt_in_and_key_gate():
    assert doctor._SOURCE_BUILDERS["podcasts"]({})["status"] == "unconfigured"
    assert doctor._SOURCE_BUILDERS["podcasts"]({"EXA_API_KEY": "dummy-key"})["status"] == "opt-in"
    assert doctor._SOURCE_BUILDERS["podcasts"]({"EXA_API_KEY": "dummy-key", "INCLUDE_SOURCES": "podcasts"})["status"] == "ok"


def test_truncated_guest_link_keeps_complete_names():
    header = (
        "- **Guests:** [Ada Example](https://exa.ai/library/person/a1), "
        "[Bo Example](https://exa.ai/library/person/b2), [Cy Exam"
    )
    assert podcasts._header_names(header, "Guests") == ["Ada Example", "Bo Example"]


def test_thin_results_do_not_trigger_a_second_exa_call():
    assert "podcasts" in pipeline.THIN_RETRY_EXEMPT


def test_explicit_search_survives_web_backend_none():
    report = pipeline.run(
        topic="test topic", config={"EXA_API_KEY": "dummy-key"}, depth="quick", mock=True,
        requested_sources=["podcasts"], web_backend="none", as_of_date="2026-07-10",
    )
    assert "podcasts" in report.items_by_source


def test_dispatch_passes_run_deadline_and_cancel_to_http(monkeypatch):
    request = Mock(return_value={"results": []})
    monkeypatch.setattr(podcasts.http, "request", request)
    cancel = threading.Event()
    deadline = time.monotonic() + 60
    pipeline._retrieve_stream_impl(
        source="podcasts",
        subquery=schema.SubQuery(label="primary", search_query="workout", ranking_query="workout", sources=["podcasts"]),
        date_range=WINDOW,
        config={"EXA_API_KEY": "dummy-key", "_enrich_cancel": cancel, "_research_deadline": deadline},
        depth="quick", mock=False, topic="workout",
        runtime=pipeline.providers.mock_runtime({}, "quick"),
    )
    assert request.call_args.kwargs["deadline_monotonic"] == deadline
    assert request.call_args.kwargs["cancel"] is cancel
