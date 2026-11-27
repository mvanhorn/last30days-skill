"""Octen web search backend: request shape, parsing, and opt-in selection.

Response fixtures follow a live POST https://api.octen.ai/search response
(2026-11-27); field names per https://docs.octen.ai/api-reference/search.
"""

from unittest.mock import patch

import pytest

from lib import backends, grounding, health, http, pipeline, usage

DATE_RANGE = ("2026-02-25", "2026-03-27")


def _payload(*results, code=0):
    return {"code": code, "msg": "success", "data": {"query": "q", "results": list(results)}}


def _result(url="https://example.com/a", published="2026-03-10T08:00:00Z", **extra):
    item = {"title": "A", "url": url, "highlight": "Snippet text", "time_published": published}
    item.update(extra)
    return item


def test_request_bounds_both_ends_and_over_fetches():
    with patch("lib.grounding.http.request", return_value=_payload()) as request:
        grounding.octen_search("agent runtimes", DATE_RANGE, "test-key", count=5)
    args, kwargs = request.call_args
    assert args == ("POST", "https://api.octen.ai/search")
    assert kwargs["headers"] == {"x-api-key": "test-key"}
    assert kwargs["json_data"] == {
        "query": "agent runtimes",
        "count": 15,
        "time_basis": "auto",
        "start_time": "2026-02-25T00:00:00.000Z",
        "end_time": "2026-03-27T23:59:59.999Z",
        "highlight": {"enable": True},
    }


def test_parses_result_and_caps_snippet():
    long_highlight = "line one\n\n" + ("word " * 200)
    with patch("lib.grounding.http.request", return_value=_payload(_result(highlight=long_highlight))):
        items, artifact = grounding.octen_search("q", DATE_RANGE, "test-key")
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "WO1"
    assert item["url"] == "https://example.com/a"
    assert item["source_domain"] == "example.com"
    assert item["date"] == "2026-03-10"
    assert item["why_relevant"] == "Octen web search"
    assert item["metadata"] == {"date_window_basis": "server_bounds"}
    assert "\n" not in item["snippet"]
    assert item["snippet"].startswith("line one word")
    assert len(item["snippet"]) <= 500
    assert artifact == {"label": "octen", "webSearchQueries": ["q"], "resultCount": 1}


def test_drops_known_out_of_range_dates_and_keeps_undated():
    payload = _payload(
        _result(url="https://example.com/in", published="2026-03-10T00:00:00Z"),
        _result(url="https://example.com/old", published="2025-12-01T00:00:00Z"),
        _result(url="https://example.com/undated", published=""),
    )
    with patch("lib.grounding.http.request", return_value=payload):
        items, _ = grounding.octen_search("q", DATE_RANGE, "test-key")
    assert [i["url"] for i in items] == ["https://example.com/in", "https://example.com/undated"]
    assert items[1]["date"] is None


def test_dated_results_come_first_and_fill_the_count():
    undated = [_result(url=f"https://example.com/u{n}", published="") for n in range(4)]
    dated = [_result(url=f"https://example.com/d{n}", published="2026-03-10T00:00:00Z") for n in range(7)]
    with patch("lib.grounding.http.request", return_value=_payload(*undated, *dated)):
        items, artifact = grounding.octen_search("q", DATE_RANGE, "test-key", count=5)
    urls = [i["url"] for i in items]
    assert urls[:5] == [f"https://example.com/d{n}" for n in range(5)]
    assert urls[5:] == [f"https://example.com/u{n}" for n in range(3)]
    assert [i["id"] for i in items] == [f"WO{n}" for n in range(1, 9)]
    assert artifact["resultCount"] == 8


def test_undated_pages_are_capped_when_dated_results_are_few():
    undated = [_result(url=f"https://example.com/u{n}", published="") for n in range(6)]
    dated = [_result(url="https://example.com/d0", published="2026-03-10T00:00:00Z")]
    with patch("lib.grounding.http.request", return_value=_payload(*undated, *dated)):
        items, _ = grounding.octen_search("q", DATE_RANGE, "test-key", count=5)
    assert [i["url"] for i in items] == [
        "https://example.com/d0", "https://example.com/u0", "https://example.com/u1", "https://example.com/u2",
    ]


def test_skips_malformed_results():
    payload = _payload("not-a-dict", _result(url=""), _result(url="https://example.com/ok"))
    with patch("lib.grounding.http.request", return_value=payload):
        items, _ = grounding.octen_search("q", DATE_RANGE, "test-key")
    assert [i["url"] for i in items] == ["https://example.com/ok"]


@pytest.mark.parametrize(("code", "state"), [(401, "auth-failed"), (429, "rate-limited")])
def test_error_code_in_body_is_classified_like_an_http_status(code, state):
    with patch("lib.grounding.http.request", return_value={"code": code, "msg": "error"}):
        with pytest.raises(http.HTTPError) as exc:
            grounding.octen_search("q", DATE_RANGE, "bad-key")
    assert exc.value.status_code == code
    assert exc.value.outcome_state == state


def test_http_errors_propagate():
    error = http.HTTPError("HTTP 401", status_code=401)
    with patch("lib.grounding.http.request", side_effect=error):
        with pytest.raises(http.HTTPError) as exc:
            grounding.web_search("q", DATE_RANGE, {"OCTEN_API_KEY": "k"}, backend="auto")
    assert exc.value is error


def test_auto_selects_octen_when_it_is_the_only_key():
    with patch("lib.grounding.octen_search", return_value=([], {})) as search:
        grounding.web_search("q", DATE_RANGE, {"OCTEN_API_KEY": "k"}, backend="auto")
    search.assert_called_once_with("q", DATE_RANGE, "k")


@pytest.mark.parametrize("other_key", ["BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"])
def test_every_existing_key_wins_over_octen(other_key):
    assert grounding.configured_backend({"OCTEN_API_KEY": "k", other_key: "k2"}) != "octen"


def test_no_octen_key_never_selects_octen():
    assert grounding.configured_backend({}) is None


def test_pinned_octen_requires_the_key():
    with pytest.raises(RuntimeError, match="OCTEN_API_KEY is required"):
        grounding.web_search("q", DATE_RANGE, {}, backend="octen")


def test_doctor_does_not_list_or_prescribe_octen_without_a_key():
    resolution = backends.resolve("web", {"SERPER_API_KEY": "k"})
    assert "octen" not in resolution.chain
    assert all(f.name != "octen" for f in resolution.findings)


def test_doctor_lists_octen_once_the_key_is_set():
    resolution = backends.resolve("web", {"OCTEN_API_KEY": "k"})
    assert "octen" in resolution.chain
    assert resolution.active_backend == "octen"


def test_doctor_reports_missing_key_for_an_explicit_pin():
    resolution = backends.resolve("web", {}, pin="octen")
    finding = next(f for f in resolution.findings if f.name == "octen")
    assert finding.status == health.MISSING


def test_octen_key_makes_the_web_source_available_on_native_search_hosts():
    config = {"OCTEN_API_KEY": "k", "LAST30DAYS_NATIVE_SEARCH": "1"}
    assert "grounding" in pipeline.available_sources(config)
    assert "grounding" not in pipeline.available_sources({"LAST30DAYS_NATIVE_SEARCH": "1"})


def test_octen_calls_are_recorded_as_paid_usage(monkeypatch, tmp_path):
    monkeypatch.setenv(usage.JOURNAL_ENV, str(tmp_path / "journal"))
    with patch.object(usage, "begin", return_value=("p", "a", "octen")) as begin:
        usage.begin_http("https://api.octen.ai/search", "POST")
    begin.assert_called_once_with("octen")


def test_pinned_run_uses_octen():
    payload = _payload(_result(url="https://example.com/pinned", published="2026-08-01T00:00:00Z"))
    with patch("lib.grounding.http.request", return_value=payload):
        report = pipeline.run(
            topic="test topic",
            config={"OCTEN_API_KEY": "k", "LAST30DAYS_NATIVE_SEARCH": "1"},
            depth="quick",
            requested_sources=["grounding"],
            web_backend="octen",
            as_of_date="2026-08-26",
        )
    assert "grounding" not in report.errors_by_source
    assert [i.url for i in report.items_by_source["grounding"]] == ["https://example.com/pinned"]


def test_pinned_run_without_a_key_reports_the_missing_key():
    report = pipeline.run(
        topic="test topic",
        config={"LAST30DAYS_NATIVE_SEARCH": "1"},
        depth="quick",
        requested_sources=["grounding"],
        web_backend="octen",
        as_of_date="2026-08-26",
    )
    assert "OCTEN_API_KEY is required" in str(report.errors_by_source.get("grounding"))
