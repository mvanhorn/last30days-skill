"""Proxy mode (LAST30DAYS_API_MODE=proxy): the http.request seam.

Every test is offline: transport is mocked at ``http._open_request``, the same
seam tests/test_usage_accounting.py uses. Keys and hosts are dummy values.
"""

from __future__ import annotations

import io
import json
import urllib.error
from decimal import Decimal
from email.message import Message
from unittest import mock

import pytest

from lib import backends, doctor, env, grounding, http, parallel_mcp, providers, proxy, render, tiktok

TEST_KEY = "sk_live_DUMMY_PROXY_KEY_00000"
TEST_BASE = "https://api.example.test/api/v1"
SC_KEYWORD = "https://api.scrapecreators.com/v1/tiktok/search/keyword"


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch):
    for name in ("LAST30DAYS_API_MODE", "LAST30DAYS_API_KEY", "LAST30DAYS_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    proxy.reset()
    yield
    proxy.reset()


@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_MODE", "proxy")
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)


def _headers(values: dict[str, str] | None = None) -> Message:
    message = Message()
    for name, value in (values or {}).items():
        message[name] = value
    return message


class Response(io.BytesIO):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = _headers(headers)


def _http_error(url, status, payload, headers=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    return urllib.error.HTTPError(url, status, "error", _headers(headers), io.BytesIO(body))


class Transport:
    """Records each request and answers from a queue (or one fixed answer)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, req, timeout, **_kwargs):
        self.requests.append(req)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer):
            answer = answer(req)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _config(**keys):
    config = dict(keys)
    proxy.fill_config(config)
    return config


# ---------------------------------------------------------------------------
# Config: which lanes the proxy serves
# ---------------------------------------------------------------------------


def test_fill_config_marks_only_absent_proxy_lanes(proxy_env):
    config = _config(SCRAPECREATORS_API_KEY="user-sc-key")
    assert config["SCRAPECREATORS_API_KEY"] == "user-sc-key"
    for var in ("XAI_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"):
        assert proxy.is_sentinel(config[var])
    assert proxy.via_api_key(config) == ["XAI_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"]
    # Lanes outside the proxy are never filled.
    assert "OPENAI_API_KEY" not in config and "BRAVE_API_KEY" not in config


@pytest.mark.parametrize(
    "env_values",
    [
        {},
        {"LAST30DAYS_API_KEY": TEST_KEY, "LAST30DAYS_API_BASE": TEST_BASE},
        {"LAST30DAYS_API_MODE": "proxy", "LAST30DAYS_API_BASE": TEST_BASE},
        {"LAST30DAYS_API_MODE": "proxy", "LAST30DAYS_API_KEY": TEST_KEY,
         "LAST30DAYS_API_BASE": "http://remote.example.test/api/v1"},
        {"LAST30DAYS_API_MODE": "hosted", "LAST30DAYS_API_KEY": TEST_KEY,
         "LAST30DAYS_API_BASE": TEST_BASE},
    ],
)
def test_no_lane_is_filled_unless_proxy_mode_is_fully_configured(monkeypatch, env_values):
    for name, value in env_values.items():
        monkeypatch.setenv(name, value)
    assert _config() == {}


def test_get_config_fills_lanes_in_proxy_mode_and_leaves_hosted_mode_alone(monkeypatch):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
        monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
        hosted = env.get_config()
        assert not any(proxy.is_sentinel(value) for value in hosted.values())
        assert env.hosted_backend_configured() is True

        monkeypatch.setenv("LAST30DAYS_API_MODE", "proxy")
        proxied = env.get_config()
        assert proxied["SCRAPECREATORS_API_KEY"] == proxy.SENTINEL
        assert env.hosted_backend_configured() is False
        assert env.is_tiktok_available(proxied)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_own_scrapecreators_key_goes_direct_in_proxy_mode(proxy_env, monkeypatch):
    """AE2: a key the user set wins; the proxy never sees the call."""
    transport = Transport(Response({"search_item_list": []}))
    monkeypatch.setattr(http, "_open_request", transport)
    config = _config(SCRAPECREATORS_API_KEY="user-sc-key")

    tiktok.search_tiktok("ai agents", "2026-10-19", "2026-11-18", depth="quick", token=config["SCRAPECREATORS_API_KEY"])

    req = transport.requests[0]
    assert req.full_url.startswith(SC_KEYWORD + "?")
    assert req.get_header("X-api-key") == "user-sc-key"
    assert req.get_header("Authorization") is None
    assert req.get_header("Idempotency-key") is None
    assert proxy.footer_line() is None


def test_sentinel_scrapecreators_call_goes_through_proxy(proxy_env, monkeypatch):
    transport = Transport(Response({"search_item_list": []}, headers={"x-l30d-charge-credits": "1.750000"}))
    monkeypatch.setattr(http, "_open_request", transport)
    config = _config()

    tiktok.search_tiktok("ai agents", "2026-10-19", "2026-11-18", depth="quick", token=config["SCRAPECREATORS_API_KEY"])

    req = transport.requests[0]
    assert req.full_url.startswith(f"{TEST_BASE}/proxy/scrapecreators/v1/tiktok/search/keyword?")
    assert "query=ai" in req.full_url
    assert req.get_header("Authorization") == f"Bearer {TEST_KEY}"
    assert req.get_header("X-api-key") is None
    assert proxy.SENTINEL not in json.dumps(req.header_items())
    assert req.get_header("Idempotency-key")
    session = req.get_header("X-l30d-session")
    assert session and len(session) <= 128
    assert proxy.session_summary() == (1, Decimal("1.75"))


def test_free_source_is_never_rewritten(proxy_env, monkeypatch):
    transport = Transport(Response({"hits": []}))
    monkeypatch.setattr(http, "_open_request", transport)
    _config()

    http.get("https://hn.algolia.com/api/v1/search_by_date", params={"query": "ai"})

    req = transport.requests[0]
    assert req.full_url.startswith("https://hn.algolia.com/api/v1/search_by_date?")
    assert req.get_header("Authorization") is None
    assert req.get_header("X-l30d-session") is None
    assert proxy.footer_line() is None


def test_proxied_xai_call_carries_its_timeout_as_deadline(proxy_env, monkeypatch):
    transport = Transport(Response({"output": []}, headers={"x-l30d-charge-credits": "0.5"}))
    monkeypatch.setattr(http, "_open_request", transport)
    client = providers.XAIClient(_config()["XAI_API_KEY"])

    client.generate_text("grok-4-1-fast-non-reasoning", "plan this")

    req = transport.requests[0]
    assert req.full_url == f"{TEST_BASE}/proxy/xai/v1/responses"
    assert req.get_method() == "POST"
    assert req.get_header("X-l30d-deadline") == "90"
    assert req.get_header("Authorization") == f"Bearer {TEST_KEY}"
    assert json.loads(req.data)["input"] == [{"role": "user", "content": "plan this"}]


def test_retry_reuses_idempotency_key_and_calls_get_new_ones(proxy_env, monkeypatch):
    transport = Transport(
        _http_error(SC_KEYWORD, 503, {"message": "upstream busy"}),
        Response({"ok": True}, headers={"x-l30d-charge-credits": "1"}),
    )
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"}, retries=2)
    first, retried = transport.requests
    assert first.get_header("Idempotency-key") == retried.get_header("Idempotency-key")

    transport.answers = [Response({"ok": True}, headers={"x-l30d-charge-credits": "1"})]
    http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "b"})
    assert transport.requests[2].get_header("Idempotency-key") != first.get_header("Idempotency-key")
    # One session for the whole run.
    assert len({req.get_header("X-l30d-session") for req in transport.requests}) == 1


def test_already_settled_is_not_retried_and_its_charge_counts(proxy_env, monkeypatch):
    transport = Transport(_http_error(
        SC_KEYWORD, 409, {"error": "already charged", "code": "already_settled"},
        headers={"x-l30d-charge-credits": "2.000000"},
    ))
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError) as exc:
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"}, retries=3)

    assert exc.value.status_code == 409
    assert len(transport.requests) == 1
    assert proxy.session_summary() == (1, Decimal("2"))


def test_first_402_disables_paid_lanes_for_the_run(proxy_env, monkeypatch, capsys):
    """AE3: one 402 insufficient_credits, a single hint, then no more proxy calls."""
    transport = Transport(_http_error(
        SC_KEYWORD, 402, {"error": "Not enough credits", "code": "insufficient_credits"},
    ))
    monkeypatch.setattr(http, "_open_request", transport)
    config = _config()

    with pytest.raises(http.HTTPError):
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(config["SCRAPECREATORS_API_KEY"]), params={"query": "a"})
    with pytest.raises(http.HTTPError) as later:
        providers.XAIClient(config["XAI_API_KEY"]).generate_text("grok-4-1-fast-non-reasoning", "x")

    assert len(transport.requests) == 1
    assert later.value.status_code == 402
    assert proxy.exhausted()
    err = capsys.readouterr().err
    assert err.count("out of credits") == 1
    assert "https://api.example.test/dashboard/billing" in err
    assert TEST_KEY not in err

    # Free sources keep working after the paid lanes stop.
    transport.answers = [Response({"hits": []})]
    assert http.get("https://hn.algolia.com/api/v1/search", params={"query": "ai"}) == {"hits": []}


def test_spend_ceiling_stops_paid_lanes_without_waiting_out_retry_after(proxy_env, monkeypatch):
    slept = []
    monkeypatch.setattr(http.time, "sleep", slept.append)
    transport = Transport(_http_error(
        SC_KEYWORD, 429, {"error": "ceiling", "code": "spend_ceiling"}, headers={"Retry-After": "3600"},
    ))
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError):
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"})

    assert len(transport.requests) == 1
    assert slept == []
    assert proxy.exhausted()


def test_upstream_provider_402_without_code_does_not_exhaust_the_key(proxy_env, monkeypatch):
    transport = Transport(_http_error(
        SC_KEYWORD, 402, {"message": "provider out of credits"},
        headers={"x-l30d-charge-credits": "0.000000"},
    ))
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError):
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"})

    assert not proxy.exhausted()
    assert proxy.session_summary() == (0, Decimal(0))


def test_unlisted_endpoint_never_leaves_the_machine(proxy_env, monkeypatch):
    transport = Transport(Response({}))
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError) as exc:
        http.get("https://api.scrapecreators.com/v1/twitter/profile", headers=http.scrapecreators_headers(token))
    with pytest.raises(http.HTTPError):
        http.post("https://xai.example.test/v1/responses", {"model": "m"}, headers={"Authorization": f"Bearer {proxy.SENTINEL}"})

    assert exc.value.status_code == 404
    assert transport.requests == []


def test_parallel_mcp_and_transcription_never_receive_the_sentinel(proxy_env, monkeypatch):
    config = _config()
    seen = []
    monkeypatch.setattr(parallel_mcp, "search", lambda query, date_range, key: seen.append(key) or ([], {}))

    grounding.web_search("ai", ("2026-10-19", "2026-11-18"), config, backend="parallel-mcp")

    assert seen == [None]
    assert env.transcription_providers(config) == []


def test_user_keys_win_lane_selection_over_proxied_ones(proxy_env):
    config = _config(PARALLEL_API_KEY="user-parallel", OPENROUTER_API_KEY="user-openrouter")
    assert grounding.configured_backend(config) == "parallel"
    runtime, client = providers.resolve_runtime(config, "default")
    assert runtime.reasoning_provider == "openrouter"

    proxied_only = _config()
    assert grounding.configured_backend(proxied_only) == "serper"
    runtime, client = providers.resolve_runtime(proxied_only, "default")
    assert runtime.reasoning_provider == "xai"


def test_proxied_xai_ranks_after_the_users_own_x_backends(proxy_env, monkeypatch):
    config = _config(XQUIK_API_KEY="user-xquik")
    monkeypatch.setattr(env, "is_xquik_available", lambda cfg: True)
    chain = env.x_backend_chain(config, local_only=True)
    assert chain[-1] == "xai"
    assert chain.index("xquik") < chain.index("xai")


# ---------------------------------------------------------------------------
# Footer, fixtures, and mode boundaries
# ---------------------------------------------------------------------------


def test_footer_total_equals_sum_of_charge_headers(proxy_env, monkeypatch):
    charges = ["0.125000", "1.000000", "0.000000", "2.333333"]
    transport = Transport(*[Response({"ok": True}, headers={"x-l30d-charge-credits": c}) for c in charges])
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    for query in "abcd":
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": query})

    total = sum(Decimal(c) for c in charges)
    assert proxy.session_summary() == (4, total)
    line = proxy.footer_line()
    assert line == "💳 API key: 3.458333 credits across 4 paid calls"

    report = mock.Mock()
    with mock.patch.object(render, "_build_source_footer_lines", return_value=["🔴 Reddit: 1 thread"]), \
         mock.patch.object(render, "_top_voices_footer_line", return_value=None), \
         mock.patch.object(render, "_assess_data_freshness", return_value=None):
        footer = render._render_emoji_footer(report, "/tmp/raw.md")
    assert f"├─ {line}" in footer


def test_footer_has_no_proxy_line_outside_proxy_mode():
    report = mock.Mock()
    with mock.patch.object(render, "_build_source_footer_lines", return_value=["🔴 Reddit: 1 thread"]), \
         mock.patch.object(render, "_top_voices_footer_line", return_value=None), \
         mock.patch.object(render, "_assess_data_freshness", return_value=None):
        footer = render._render_emoji_footer(report, None)
    assert footer == ["---", "✅ All agents reported back!", "└─ 🔴 Reddit: 1 thread", "---"]


def test_recorded_fixtures_never_contain_the_bearer_key(proxy_env, monkeypatch, tmp_path):
    transport = Transport(Response({"echo": f"Bearer {TEST_KEY}"}, headers={"x-l30d-charge-credits": "1"}))
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with http.recording_requests(tmp_path / "fixture"):
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"})

    fixture_text = (tmp_path / "fixture" / "http.json").read_text(encoding="utf-8")
    assert TEST_KEY not in fixture_text
    assert "/proxy/scrapecreators/v1/tiktok/search/keyword" in fixture_text


def test_hosted_mode_without_api_mode_routes_nothing(monkeypatch):
    """Without LAST30DAYS_API_MODE, a user key call is untouched by the seam."""
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    transport = Transport(Response({"ok": True}))
    monkeypatch.setattr(http, "_open_request", transport)

    http.get(SC_KEYWORD, headers=http.scrapecreators_headers("user-sc-key"), params={"query": "a"})

    req = transport.requests[0]
    assert req.full_url == f"{SC_KEYWORD}?query=a"
    assert sorted(name.lower() for name, _ in req.header_items()) == ["content-type", "user-agent", "x-api-key"]
    assert proxy.footer_line() is None


def test_sentinel_without_proxy_mode_is_refused_locally(monkeypatch):
    transport = Transport(Response({}))
    monkeypatch.setattr(http, "_open_request", transport)
    with pytest.raises(http.HTTPError):
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(proxy.SENTINEL))
    assert transport.requests == []


def test_doctor_reports_proxied_lanes_via_api_key(proxy_env):
    config = _config(SCRAPECREATORS_API_KEY="")
    record = doctor._sc_gated_record(config, "TikTok")
    assert record["detail"] == "ScrapeCreators via API key (TikTok)"
    setup = doctor._setup_block(config)
    assert setup["keys_present"]["SCRAPECREATORS_API_KEY"] is False
    assert setup["via_api_key"] == ["SCRAPECREATORS_API_KEY", "XAI_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"]
    assert doctor._x_auth_path(config)["note"].startswith("xAI via API key")


def test_doctor_setup_block_lists_no_proxied_lane_outside_proxy_mode():
    config = {"SCRAPECREATORS_API_KEY": "user-sc-key"}
    setup = doctor._setup_block(config)
    assert setup["via_api_key"] == []
    assert setup["keys_present"]["SCRAPECREATORS_API_KEY"] is True


def test_run_end_prints_the_session_total_on_stderr(proxy_env, monkeypatch, capsys):
    import last30days as cli

    cli._report_proxy_session()
    assert capsys.readouterr().err == ""

    transport = Transport(Response({"ok": True}, headers={"x-l30d-charge-credits": "1.5"}))
    monkeypatch.setattr(http, "_open_request", transport)
    http.get(SC_KEYWORD, headers=http.scrapecreators_headers(_config()["SCRAPECREATORS_API_KEY"]), params={"query": "a"})
    cli._report_proxy_session()

    err = capsys.readouterr().err
    assert err == f"[last30days] API key session {proxy.session_id()}: 1.5 credits across 1 paid call\n"
    assert TEST_KEY not in err


# ---------------------------------------------------------------------------
# Review regressions
# ---------------------------------------------------------------------------


class _BrokenBody(Response):
    """A charged response whose body read fails after the headers arrived."""

    def read(self, *args):
        raise ConnectionResetError("connection reset mid-body")


def test_charge_counts_once_when_a_body_read_fails_and_the_retry_is_already_settled(proxy_env, monkeypatch):
    transport = Transport(
        _BrokenBody({"ok": True}, headers={"x-l30d-charge-credits": "2.000000"}),
        _http_error(
            SC_KEYWORD, 409, {"error": "already charged", "code": "already_settled"},
            headers={"x-l30d-charge-credits": "2.000000"},
        ),
    )
    monkeypatch.setattr(http, "_open_request", transport)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError) as exc:
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"}, retries=3)

    assert exc.value.status_code == 409
    first, retried = transport.requests
    assert first.get_header("Idempotency-key") == retried.get_header("Idempotency-key")
    assert proxy.session_summary() == (1, Decimal("2"))


def test_pending_retry_stops_once_another_call_exhausts_the_key(proxy_env, monkeypatch):
    transport = Transport(_http_error(SC_KEYWORD, 503, {"message": "upstream busy"}))
    monkeypatch.setattr(http, "_open_request", transport)

    def another_call_hits_402(_delay):
        proxy.observe_error(
            "xai", 402, json.dumps({"error": "Not enough credits", "code": "insufficient_credits"}), None,
        )

    monkeypatch.setattr(http.time, "sleep", another_call_hits_402)
    token = _config()["SCRAPECREATORS_API_KEY"]

    with pytest.raises(http.HTTPError) as exc:
        http.get(SC_KEYWORD, headers=http.scrapecreators_headers(token), params={"query": "a"}, retries=3)

    assert len(transport.requests) == 1
    assert exc.value.status_code == 402


def test_doctor_predicts_the_same_web_backend_as_the_runtime(proxy_env):
    own_parallel = _config(PARALLEL_API_KEY="user-parallel")
    resolution = backends.resolve("web", own_parallel)
    assert resolution.active_backend == grounding.configured_backend(own_parallel) == "parallel"
    assert resolution.chain == ["brave", "exa", "parallel", "serper", "keyless", "parallel-mcp"]

    proxied_only = _config()
    resolution = backends.resolve("web", proxied_only)
    assert resolution.active_backend == grounding.configured_backend(proxied_only) == "serper"

    # An explicit pin still wins over the preference.
    assert backends.resolve("web", own_parallel, pin="serper").active_backend == "serper"


def test_doctor_x_chain_ranks_proxied_xai_last_like_the_runtime(proxy_env):
    config = _config(XQUIK_API_KEY="user-xquik")
    resolution = backends.resolve("x", config)
    assert resolution.chain[-1] == "xai"
    assert resolution.chain.index("xquik") < resolution.chain.index("xai")


def test_doctor_chain_order_unchanged_outside_proxy_mode():
    config = {"PARALLEL_API_KEY": "user-parallel", "SERPER_API_KEY": "user-serper"}
    resolution = backends.resolve("web", config)
    assert resolution.chain == list(backends.WEB_BACKEND_ORDER)
    assert resolution.active_backend == "serper"


def test_web_falls_back_to_parallel_when_the_service_cannot_serve_serper(proxy_env, monkeypatch):
    """The hosted service may hold no Serper key; web search moves on to Parallel."""
    def answer(req):
        if "/proxy/serper/" in req.full_url:
            return _http_error(req.full_url, 503, {"error": "not available", "code": "provider_unavailable"})
        return Response(
            {"results": [{"url": "https://example.com/a", "title": "A", "excerpts": ["x"], "publish_date": "2026-11-10"}]},
            headers={"x-l30d-charge-credits": "0.500000"},
        )

    transport = Transport(answer)
    monkeypatch.setattr(http, "_open_request", transport)
    config = _config()
    config["EXCLUDE_SOURCES"] = "reddit"

    items, artifact = grounding.web_search("claude", ("2026-10-20", "2026-11-19"), config)

    urls = [req.full_url for req in transport.requests]
    assert any("/proxy/serper/" in url for url in urls)
    assert any("/proxy/parallel/" in url for url in urls)
    assert artifact.get("label") == "parallel"
    assert proxy.provider_unavailable("serper")
    assert grounding.configured_backend(config) == "parallel"


def test_web_failure_on_an_available_backend_still_raises(proxy_env, monkeypatch):
    transport = Transport(_http_error("https://x", 500, {"error": "boom"}))
    monkeypatch.setattr(http, "_open_request", transport)
    config = _config()
    with pytest.raises(Exception):
        grounding.web_search("claude", ("2026-10-20", "2026-11-19"), config)
    assert not any("/proxy/parallel/" in req.full_url for req in transport.requests)
