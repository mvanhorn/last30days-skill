from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from lib import competitors, dates, grounding, resolve
from tests.skill_contract import reference_text


@pytest.fixture(autouse=True)
def _first_party_modules():
    scripts = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
    assert Path(grounding.__file__).resolve().is_relative_to(scripts)
    assert Path(resolve.__file__).resolve().is_relative_to(scripts)


@pytest.mark.parametrize("key", ["OPENROUTER_API_KEY", "PERPLEXITY_API_KEY"])
@pytest.mark.parametrize("native_search", ["0", "1"])
def test_unsupported_search_credentials_do_not_claim_autoresolve(
    monkeypatch, capsys, key, native_search,
):
    config = {key: "dummy-key", "LAST30DAYS_NATIVE_SEARCH": native_search}

    def unexpected_request(*args, **kwargs):
        pytest.fail("Unsupported search credentials must not trigger requests")

    monkeypatch.setattr(grounding.http, "request", unexpected_request)
    monkeypatch.setattr(grounding.http, "get_text", unexpected_request)
    result = resolve.auto_resolve("Widgetworks", config)

    assert not resolve._has_backend(config)
    assert result["searches_run"] == 0
    assert result["subreddits"] == []
    assert "No web search backend available" in capsys.readouterr().err
    assert competitors.discover_competitors("Widgetworks", 2, config) == []


@pytest.mark.parametrize("backend,key", [
    ("brave", "BRAVE_API_KEY"),
    ("exa", "EXA_API_KEY"),
    ("serper", "SERPER_API_KEY"),
    ("parallel", "PARALLEL_API_KEY"),
])
def test_supported_search_credentials_resolve_through_real_dispatcher(
    monkeypatch, backend, key,
):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        body = kwargs.get("json_data", {})
        query = (
            parse_qs(urlparse(url).query).get("q", [""])[0]
            or body.get("query")
            or body.get("q")
            or (body.get("search_queries") or [""])[0]
        )
        if "subreddit" in query:
            title, snippet = "r/widgetworks discussion", "Community updates"
        elif "handle" in query:
            title, snippet = "Widgetworks on X", "Follow @widgetworks"
        else:
            title, snippet = "Widgetworks update", "Widgetworks announced a new product."
        item = {
            "title": title,
            "url": "https://example.org/article",
            "link": "https://example.org/article",
            "description": snippet,
            "snippet": snippet,
            "text": snippet,
            "excerpts": [snippet],
        }
        return {"web": {"results": [item]}, "results": [item], "organic": [item]}

    monkeypatch.setattr(grounding.http, "request", request)
    config = {key: "dummy-key", "LAST30DAYS_NATIVE_SEARCH": "1"}
    result = resolve.auto_resolve("Widgetworks", config)

    assert resolve._has_backend(config)
    assert result["searches_run"] == 4
    assert result["subreddits"] == ["widgetworks"]
    assert result["x_handle"] == "widgetworks"
    assert "announced a new product" in result["context"]
    assert len(calls) == 4
    assert all(backend in url for _, url in calls)


def test_non_latin_auto_search_uses_available_exa_without_brave(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"results": [{
            "title": "קפה עלית",
            "url": "https://example.org/coffee",
            "text": "עדכונים על קפה עלית",
        }]}

    monkeypatch.setattr(grounding.http, "request", request)
    topic = "קפה עלית"
    items, artifact = grounding.web_search(
        topic, dates.get_date_range(30), {"EXA_API_KEY": "dummy-key"},
    )

    assert artifact["label"] == "exa"
    assert items[0]["title"] == topic
    assert calls[0][1] == "https://api.exa.ai/search"
    assert calls[0][2]["json_data"]["query"] == topic


def test_non_latin_auto_search_keeps_keyless_floor(monkeypatch):
    html = (
        '<a class="result__a" href="https://example.org/coffee">קפה עלית</a>'
        '<a class="result__snippet">עדכונים על קפה עלית</a>'
    )
    monkeypatch.setattr(grounding.http, "get_text", lambda *args, **kwargs: html)
    items, artifact = grounding.web_search("קפה עלית", dates.get_date_range(30), {})
    assert items[0]["title"] == "קפה עלית"
    assert artifact["keyless_backend"] == "ddg"


def test_native_search_host_does_not_start_keyless_search(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Native-search hosts supply their own web results")

    monkeypatch.setattr(grounding.http, "get_text", unexpected_request)
    assert grounding.web_search(
        "קפה עלית", dates.get_date_range(30), {"LAST30DAYS_NATIVE_SEARCH": "1"},
    ) == ([], {})


def test_explicit_unavailable_backend_still_reports_configuration_error():
    with pytest.raises(RuntimeError, match="BRAVE_API_KEY is required"):
        grounding.web_search(
            "קפה עלית", dates.get_date_range(30), {"EXA_API_KEY": "dummy-key"},
            backend="brave",
        )


def test_non_latin_runbook_preserves_available_backend_selection():
    runbook = reference_text("research-runbook")
    section = runbook.split("**Class 5:", 1)[1].split("**Pre-Flight decision flow", 1)[0]
    assert "Leave `--web-backend` unset" in section
    assert "host's native web search" in section
    assert "Never force `--web-backend brave`" in section
    assert "Force `--web-backend brave`" not in section
    assert "| `--web-backend brave` | Step 0.45 Class 5 |" not in runbook


def test_competitor_instructions_only_claim_supported_search_backends():
    text = reference_text("competitors")
    capability = text.split("**Engine-internal auto-resolve:**", 1)[1].split("\n", 1)[0]
    for key in ("BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"):
        assert key in capability
    assert "PERPLEXITY_API_KEY" not in capability
    assert "OPENROUTER_API_KEY" not in capability
