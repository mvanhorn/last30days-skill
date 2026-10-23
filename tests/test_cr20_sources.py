import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import dates, grok_x, subproc, trustpilot, web_search_keyless, xquik


def test_adapters_are_imported_from_this_checkout():
    scripts = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
    for module in (dates, grok_x, trustpilot, web_search_keyless, xquik):
        assert Path(module.__file__).resolve().is_relative_to(scripts)


def _grok_query(cmd):
    prompt = cmd[cmd.index("-p") + 1]
    prefix = "Query (JSON string literal): "
    return json.loads(next(line[len(prefix):] for line in prompt.splitlines() if line.startswith(prefix)))


def _search_grok(lane, start, end):
    if lane == "topic":
        return grok_x.search_x("boundary subject", start, end, depth="deep")["items"]
    if lane == "from":
        return grok_x.search_handles(["subject"], "boundary subject", start, end)[0]
    if lane == "from-topic":
        return grok_x.search_handles(["subject"], "boundary subject", start, end, and_topic=True)[0]
    if lane == "mentions":
        return grok_x.search_mentions(["subject"], start, end)[0]
    return grok_x.search_name("Boundary Subject", start, end)[0]


def _search_xquik(lane, start, end):
    if lane == "topic":
        return xquik.search_xquik("boundary subject", start, end, depth="deep", token="dummy-key")["items"]
    if lane == "from":
        return xquik.search_handles(["subject"], "boundary subject", start, end, token="dummy-key")
    return xquik.search_mentions(["subject"], start, end, token="dummy-key")


@pytest.mark.parametrize("lane", ["topic", "from", "from-topic", "mentions", "name"])
@pytest.mark.parametrize("end,exclusive", [
    ("2024-02-29", "2024-03-01"),
    ("2024-12-31", "2025-01-01"),
    ("9999-12-30", "9999-12-31"),
    ("9999-12-31", None),
])
def test_grok_all_lanes_request_the_inclusive_end_date(monkeypatch, lane, end, exclusive):
    queries = []

    def run(cmd, **kwargs):
        queries.append(_grok_query(cmd))
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(grok_x, "binary_path", lambda: "/dummy/grok")
    monkeypatch.setattr(grok_x, "_check_cli_version", lambda *args: None)
    monkeypatch.setattr(grok_x.subprocess, "run", run)
    _search_grok(lane, "2024-02-01", end)
    assert queries
    for query in queries:
        assert "since:2024-02-01" in query.split()
        assert [part for part in query.split() if part.startswith("until:")] == (
            [f"until:{exclusive}"] if exclusive else []
        )


@pytest.mark.parametrize("lane", ["topic", "from", "mentions"])
@pytest.mark.parametrize("end,exclusive", [
    ("2024-02-29", "2024-03-01"),
    ("2024-12-31", "2025-01-01"),
    ("9999-12-30", "9999-12-31"),
    ("9999-12-31", None),
])
def test_xquik_all_lanes_request_the_inclusive_end_date(monkeypatch, lane, end, exclusive):
    queries = []

    def get(url, **kwargs):
        queries.append(parse_qs(urlsplit(url).query)["q"][0])
        return {"tweets": []}

    monkeypatch.setattr(xquik.http, "get", get)
    _search_xquik(lane, "2024-02-01", end)
    assert queries
    for query in queries:
        assert "since:2024-02-01" in query.split()
        assert [part for part in query.split() if part.startswith("until:")] == (
            [f"until:{exclusive}"] if exclusive else []
        )


def _snowflake(timestamp):
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return str((int(instant.timestamp() * 1000) - 1288834974657) << 22)


@pytest.mark.parametrize("adapter,lane", [
    ("grok", "topic"), ("grok", "from"), ("grok", "from-topic"),
    ("grok", "mentions"), ("grok", "name"),
    ("xquik", "topic"), ("xquik", "from"), ("xquik", "mentions"),
])
def test_x_adapters_retain_final_day_but_not_following_day(monkeypatch, adapter, lane):
    timestamps = ["2024-02-29T23:59:59.999Z", "2024-03-01T00:00:00Z"]
    handle = "subject" if lane.startswith("from") else "observer"

    def matching(query):
        match = re.search(r"\buntil:(\d{4}-\d{2}-\d{2})\b", query)
        return [stamp for stamp in timestamps if match is None or stamp[:10] < match.group(1)]

    def grok_run(cmd, **kwargs):
        posts = [
            {"url": f"https://x.com/{handle}/status/{_snowflake(stamp)}", "text": "boundary subject", "likes": 10}
            for stamp in matching(_grok_query(cmd))
        ]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(posts), "")

    def xquik_get(url, **kwargs):
        query = parse_qs(urlsplit(url).query)["q"][0]
        return {"tweets": [
            {"id": _snowflake(stamp), "author": {"username": handle}, "text": "boundary subject", "createdAt": stamp}
            for stamp in matching(query)
        ]}

    monkeypatch.setattr(grok_x, "binary_path", lambda: "/dummy/grok")
    monkeypatch.setattr(grok_x, "_check_cli_version", lambda *args: None)
    monkeypatch.setattr(grok_x.subprocess, "run", grok_run)
    monkeypatch.setattr(xquik.http, "get", xquik_get)
    search = _search_grok if adapter == "grok" else _search_xquik
    items = search(lane, "2024-02-01", "2024-02-29")
    assert [item["url"] for item in items] == [f"https://x.com/{handle}/status/{_snowflake(timestamps[0])}"]


def test_grok_validates_the_whole_inclusive_day():
    timestamps = ["2024-02-29T23:59:59.999Z", "2024-03-01T00:00:00Z"]
    posts = [
        {"url": f"https://x.com/observer/status/{_snowflake(stamp)}", "text": "boundary subject", "likes": 10, "created_at": stamp}
        for stamp in timestamps
    ]
    items = grok_x.parse_x_response({"text": json.dumps(posts)}, "boundary subject", "2024-02-01", "2024-02-29")
    assert [item["date"] for item in items] == ["2024-02-29"]


@pytest.fixture
def trustpilot_cli(monkeypatch):
    calls = []
    trustpilot._reset_state_for_tests()

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "auth":
            data = {"isFresh": True}
        elif cmd[1] == "search":
            data = {"hits": [{"displayName": "Currys", "domain": "currys.co.uk", "numberOfReviews": 100}]}
        else:
            data = {"name": "Selected company", "domain": cmd[2], "reviewCount": 100}
        return subproc.SubprocResult(returncode=0, stdout=json.dumps(data), stderr="")

    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot.subproc, "run_with_timeout", run)
    yield calls
    trustpilot._reset_state_for_tests()


@pytest.mark.parametrize("topic,domain", [
    ("currys.co.uk", "currys.co.uk"),
    ("woolworths.com.au", "woolworths.com.au"),
    ("reviews of https://www.currys.co.uk/help", "www.currys.co.uk"),
    ("Reviews of WOOLWORTHS.COM.AU.", "woolworths.com.au"),
    ("chownow.com.other", "chownow.com.other"),
    ("my-brand.com", "my-brand.com"),
])
def test_trustpilot_domain_topics_reach_the_cli_intact(trustpilot_cli, topic, domain):
    response = trustpilot.search_trustpilot(topic, "2026-10-01", "2026-10-22")
    items = trustpilot.parse_trustpilot_response(response, query=topic)
    assert [call[2] for call in trustpilot_cli if call[1] == "info"] == [domain]
    assert [item["url"] for item in items] == [f"https://www.trustpilot.com/review/{domain}"]
    assert not [call for call in trustpilot_cli if call[1] == "search"]


def test_trustpilot_explicit_domain_overrides_the_topic(trustpilot_cli):
    response = trustpilot.search_trustpilot(
        "currys.co.uk", "2026-10-01", "2026-10-22", explicit_domain="www.woolworths.com.au"
    )
    assert response["results"][0]["domain"] == "www.woolworths.com.au"
    assert [call[2] for call in trustpilot_cli if call[1] == "info"] == ["www.woolworths.com.au"]
    assert not [call for call in trustpilot_cli if call[1] == "search"]


def test_trustpilot_company_names_still_resolve_through_search(trustpilot_cli):
    response = trustpilot.search_trustpilot("Currys", "2026-10-01", "2026-10-22")
    assert response["results"][0]["domain"] == "currys.co.uk"
    assert [call[2] for call in trustpilot_cli if call[1] == "search"] == ["Currys"]
    assert [call[2] for call in trustpilot_cli if call[1] == "info"] == ["currys.co.uk"]


@pytest.mark.parametrize("count", [1, 3])
def test_startpage_keeps_snippets_with_their_results(monkeypatch, tmp_path, count):
    second_snippet = f"Only for the second result: {tmp_path.name}"
    page = f'''
        <a class="result-title result-link" href="https://first.example"><h2>First result</h2></a>
        <a class="result-title result-link" href="https://second.example"><h2>Second result</h2></a>
        <p class="description">{second_snippet}</p>
        <a class="result-title result-link" href="https://third.example"><h2>Third &amp; final</h2></a><p class="description">Third snippet</p>
    '''

    def get_text(url, **kwargs):
        return page if urlsplit(url).hostname == "www.startpage.com" else ""

    monkeypatch.setattr(web_search_keyless.http, "get_text", get_text)
    items, artifact = web_search_keyless.keyless_search(
        "example", ("2026-10-01", "2026-10-22"), {}, count=count
    )
    assert artifact["keyless_backend"] == "startpage"
    assert [(item["title"], item["url"], item["snippet"]) for item in items] == [
        ("First result", "https://first.example", ""),
        ("Second result", "https://second.example", second_snippet),
        ("Third & final", "https://third.example", "Third snippet"),
    ][:count]


def test_startpage_titleless_result_still_bounds_the_previous_snippet(monkeypatch, tmp_path):
    misplaced = f"Titleless result description: {tmp_path.name}"
    page = f'''
        <a class="result-title" href="https://first.example"><h2>First result</h2></a>
        <a class="result-title" href="https://empty.example"><h2></h2></a>
        <p class="description">{misplaced}</p>
        <a class="result-title" href="https://third.example"><h2>Third result</h2></a>
        <p class="description">Third snippet</p>
    '''
    monkeypatch.setattr(web_search_keyless.http, "get_text", lambda *args, **kwargs: page)
    items = web_search_keyless._search_startpage("example", 3)
    assert [(item["url"], item["snippet"]) for item in items] == [
        ("https://first.example", ""),
        ("https://third.example", "Third snippet"),
    ]
