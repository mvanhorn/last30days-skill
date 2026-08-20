"""Tests for the Trustpilot source adapter (lib/trustpilot.py).

Covers the brand-shape gate (the primary quiet-keeper), the browser opt-out,
field mapping from the info envelope, and graceful degradation.
"""

from __future__ import annotations

import pytest

from lib import pipeline, trustpilot


# ---- opt-in availability gating (off by default) ----

def test_not_available_by_default(monkeypatch):
    monkeypatch.setattr(pipeline, "which", lambda name: f"/usr/bin/{name}")
    avail = pipeline.available_sources({})
    assert "trustpilot" not in avail  # opt-in: absent without INCLUDE_SOURCES
    # the zero-auth pair stays default-on
    assert "arxiv" in avail and "techmeme" in avail


def test_available_when_included(monkeypatch):
    monkeypatch.setattr(pipeline, "which", lambda name: f"/usr/bin/{name}")
    avail = pipeline.available_sources({"INCLUDE_SOURCES": "trustpilot"})
    assert "trustpilot" in avail


def test_available_when_requested(monkeypatch):
    monkeypatch.setattr(pipeline, "which", lambda name: f"/usr/bin/{name}")
    avail = pipeline.available_sources({}, requested_sources=["trustpilot"])
    assert "trustpilot" in avail


# ---- brand-shape gate ----

@pytest.mark.parametrize("topic", ["ChowNow", "chownow.com", "Nothing Phone", "OpenAI", "nothing.tech"])
def test_brand_shaped_topics_fire(topic):
    assert trustpilot.is_brand_shaped(topic)


@pytest.mark.parametrize("topic", [
    "AI coding agents",        # 3 words, generic
    "agent memory",            # lowercase, generic token
    "Golden State Warriors",   # 3 words -> not company-shaped
    "best phones",             # generic + lowercase
    "how to use claude",       # generic question
    "",                        # empty
])
def test_non_brand_topics_stay_quiet(topic):
    assert not trustpilot.is_brand_shaped(topic)


@pytest.mark.parametrize("topic", [
    "Python", "React", "Docker", "Rust", "Linux", "Swift", "Java",
    "Kubernetes", "PostgreSQL", "Node",
])
def test_single_word_tech_names_are_not_brands(topic):
    # A bare capitalized language/framework/tool name is a technology query,
    # not a company-review intent -- it must not trigger the Trustpilot CLI.
    assert not trustpilot.is_brand_shaped(topic)


def test_tech_company_still_reachable_by_domain():
    # The conservative tech-token gate does not block explicit company intent:
    # a domain still resolves (e.g. wanting docker.com's reviews specifically).
    assert trustpilot.is_brand_shaped("docker.com")


def test_company_identifier_prefers_domain():
    assert trustpilot._company_identifier("reviews of chownow.com please") == "chownow.com"
    assert trustpilot._company_identifier("ChowNow") == "ChowNow"


# ---- gate short-circuits the CLI (no Chrome on non-brand topics) ----

def test_non_brand_topic_never_calls_cli(monkeypatch):
    called = []
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: called.append(a) or {})
    out = trustpilot.search_trustpilot("AI coding agents", "2026-06-01", "2026-06-27")
    assert out == {"results": []}
    assert called == []  # CLI (and any Chrome harvest) never invoked


# ---- browser opt-out ----

def test_browser_opt_out_skips_even_for_brand(monkeypatch):
    called = []
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: called.append(a) or {})
    config = {trustpilot.NO_BROWSER_ENV: "1"}
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27", config=config)
    assert out == {"results": []}
    assert called == []  # opt-out prevents the harvest-prone CLI call


def test_browser_opt_out_via_env_var_no_config(monkeypatch):
    """The production path: the env var is set but never propagated into the
    config dict. The os.environ fallback must still skip the harvest."""
    called = []
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: called.append(a) or {})
    monkeypatch.setenv(trustpilot.NO_BROWSER_ENV, "1")
    # config is None / lacks the key, mirroring env.get_config's allowlist gap.
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27", config=None)
    assert out == {"results": []}
    assert called == []


# ---- happy path + field mapping ----

def test_happy_path_maps_info_envelope():
    info = {
        "name": "ChowNow",
        "trustScore": 1.2,
        "reviewCount": 49,
        "aiSummary": "Most reviewers were let down: food never arriving, wrong address.",
        "domain": "chownow.com",
    }
    items = trustpilot.parse_trustpilot_response({"results": [info]}, query="ChowNow")
    assert len(items) == 1
    it = items[0]
    assert it["name"] == "ChowNow"
    assert it["trustScore"] == 1.2
    assert it["reviewCount"] == 49
    assert "food never arriving" in it["summary"]
    assert it["engagement"]["reviews"] == 49
    assert it["url"].endswith("chownow.com")


def test_search_returns_info_for_brand(monkeypatch):
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(
        trustpilot, "_run_cli",
        lambda *a, **k: {"name": "ChowNow", "trustScore": 1.2, "reviewCount": 49, "aiSummary": "Bad."},
    )
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27")
    assert len(out["results"]) == 1
    assert out["results"][0]["name"] == "ChowNow"


# ---- degradation paths ----

def test_cli_error_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: {"error": "no chrome"})
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27")
    assert out == {"results": []}


def test_binary_absent_returns_empty(monkeypatch):
    monkeypatch.setattr(trustpilot.shutil, "which", lambda _bin: None)
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27")
    assert out["results"] == []


def test_parse_handles_empty_and_malformed():
    assert trustpilot.parse_trustpilot_response({"results": []}, query="x") == []
    assert trustpilot.parse_trustpilot_response({}, query="x") == []
    assert trustpilot.parse_trustpilot_response({"results": [{}]}, query="x") == []


# ---- shared scaffolding for domain-resolution and warm-up tests ----

D1, D2 = "2026-06-01", "2026-06-27"

THRIFTBOOKS_HITS = {
    "hits": [
        {"displayName": "ThriftBooks", "domain": "www.thriftbooks.com", "numberOfReviews": 2843175},
        {"displayName": "Thrift Books", "domain": "thriftbook.com", "numberOfReviews": 130},
        {"displayName": "Thriftybooks", "domain": "thriftybooks.com", "numberOfReviews": 6},
    ]
}

INFO_OK = {"name": "ThriftBooks", "trustScore": 4.5, "reviewCount": 2843175, "aiSummary": "Great."}


@pytest.fixture(autouse=True)
def _reset_trustpilot_state():
    trustpilot._reset_state_for_tests()
    yield
    trustpilot._reset_state_for_tests()


def _capture_cli(monkeypatch, responses):
    """Mock _run_cli, recording argv. ``responses`` maps a subcommand key
    ("info", "search", "auth status", "auth login") to a dict, or to a list of
    dicts consumed in order."""
    calls: list[list[str]] = []

    def fake(cmd, timeout):
        calls.append(list(cmd))
        key = cmd[1] if cmd[1] != "auth" else f"auth {cmd[2]}"
        resp = responses.get(key, {})
        if isinstance(resp, list):
            return dict(resp.pop(0)) if resp else {}
        return dict(resp)

    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", fake)
    return calls


def _info_args(calls):
    return [c for c in calls if c[1] == "info"]


def _search_args(calls):
    return [c for c in calls if c[1] == "search"]


# ---- explicit domain (--trustpilot-domain) ----

def test_explicit_domain_used_verbatim(monkeypatch):
    calls = _capture_cli(monkeypatch, {"info": INFO_OK})
    out = trustpilot.search_trustpilot(
        "ThriftBooks", D1, D2, explicit_domain="www.thriftbooks.com")
    assert out["results"][0]["name"] == "ThriftBooks"
    assert _info_args(calls)[0][2] == "www.thriftbooks.com"
    assert _search_args(calls) == []  # flag set -> no search call fires


def test_explicit_domain_bypasses_brand_gate(monkeypatch):
    # A 4-word topic fails is_brand_shaped, but an explicit domain is proof
    # of brand intent -- the CLI must still be invoked with the flag value.
    calls = _capture_cli(monkeypatch, {"info": INFO_OK})
    out = trustpilot.search_trustpilot(
        "Stanley Steemer carpet cleaning", D1, D2,
        explicit_domain="stanleysteemer.com")
    assert len(out["results"]) == 1
    assert _info_args(calls)[0][2] == "stanleysteemer.com"


def test_explicit_domain_beats_domain_shaped_topic(monkeypatch):
    calls = _capture_cli(monkeypatch, {"info": INFO_OK})
    trustpilot.search_trustpilot(
        "chownow.com", D1, D2, explicit_domain="www.thriftbooks.com")
    assert _info_args(calls)[0][2] == "www.thriftbooks.com"


def test_user_domain_is_verbatim_final_no_retry(monkeypatch):
    # User-set flag (domain_is_hint=False): a miss degrades, never re-resolves.
    calls = _capture_cli(monkeypatch, {"info": {"error": "HTTP 404"}})
    out = trustpilot.search_trustpilot(
        "ThriftBooks", D1, D2, explicit_domain="thriftbook.com")
    assert out == {"results": []}
    assert _search_args(calls) == []


def test_hint_domain_retries_via_search_on_miss(monkeypatch):
    # Auto-resolved hint (domain_is_hint=True): a 404 falls through to the
    # CLI search resolution and retries with the canonical domain.
    calls = _capture_cli(monkeypatch, {
        "info": [{"error": "HTTP 404"}, INFO_OK],
        "search": THRIFTBOOKS_HITS,
    })
    out = trustpilot.search_trustpilot(
        "ThriftBooks", D1, D2,
        explicit_domain="getthriftbooks.io", domain_is_hint=True)
    assert len(out["results"]) == 1
    infos = _info_args(calls)
    assert infos[0][2] == "getthriftbooks.io"
    assert infos[1][2] == "www.thriftbooks.com"


# ---- name -> domain search fallback ----

def test_bare_name_resolves_via_search(monkeypatch):
    calls = _capture_cli(monkeypatch, {"search": THRIFTBOOKS_HITS, "info": INFO_OK})
    out = trustpilot.search_trustpilot("ThriftBooks", D1, D2)
    assert len(out["results"]) == 1
    assert _search_args(calls)[0][2] == "ThriftBooks"
    assert _info_args(calls)[0][2] == "www.thriftbooks.com"


def test_ambiguous_hits_fall_back_to_topic(monkeypatch):
    # Two same-named companies with comparable volume: review count must never
    # break the tie (silent misattribution is worse than a visible miss).
    hits = {"hits": [
        {"displayName": "Mercury", "domain": "mercury.com", "numberOfReviews": 50000},
        {"displayName": "Mercury", "domain": "mercuryinsurance.com", "numberOfReviews": 40000},
    ]}
    calls = _capture_cli(monkeypatch, {"search": hits, "info": INFO_OK})
    trustpilot.search_trustpilot("Mercury", D1, D2)
    assert _info_args(calls)[0][2] == "Mercury"  # legacy fallback identifier


def test_review_count_never_overrides_name_mismatch(monkeypatch):
    hits = {"hits": [
        {"displayName": "Bolt Technology", "domain": "bolt.eu", "numberOfReviews": 900000},
    ]}
    calls = _capture_cli(monkeypatch, {"search": hits, "info": INFO_OK})
    trustpilot.search_trustpilot("Bolt", D1, D2)
    assert _info_args(calls)[0][2] == "Bolt"


def test_search_error_falls_back_to_topic(monkeypatch):
    calls = _capture_cli(monkeypatch, {"search": {"error": "boom"}, "info": INFO_OK})
    out = trustpilot.search_trustpilot("ChowNow", D1, D2)
    assert len(out["results"]) == 1
    assert _info_args(calls)[0][2] == "ChowNow"


def test_domain_shaped_topic_never_searches(monkeypatch):
    calls = _capture_cli(monkeypatch, {"info": INFO_OK})
    trustpilot.search_trustpilot("chownow.com", D1, D2)
    assert _search_args(calls) == []
    assert _info_args(calls)[0][2] == "chownow.com"


def test_search_cached_per_topic_not_per_process(monkeypatch):
    calls = _capture_cli(monkeypatch, {"search": dict(THRIFTBOOKS_HITS), "info": INFO_OK})
    trustpilot.search_trustpilot("ThriftBooks", D1, D2)
    trustpilot.search_trustpilot("ThriftBooks", D1, D2)
    assert len(_search_args(calls)) == 1  # repeat topic hits the cache

    # A DIFFERENT topic must trigger its own search (vs-mode entities resolve
    # independently; a single process-wide slot would cross-contaminate).
    responses_seen = _search_args(calls)
    trustpilot.search_trustpilot("ChowNow", D1, D2)
    assert len(_search_args(calls)) == len(responses_seen) + 1


# ---- session warm-up (ensure_session_ready) ----

def test_warmup_fresh_session_skips_login(monkeypatch):
    calls = _capture_cli(monkeypatch, {"auth status": {"isFresh": True, "hasSession": True}})
    trustpilot.ensure_session_ready("ThriftBooks")
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]


def test_warmup_stale_session_logs_in_once(monkeypatch):
    calls = _capture_cli(monkeypatch, {
        "auth status": {"error": "no session"},
        "auth login": {"ok": True},
    })
    trustpilot.ensure_session_ready("ThriftBooks")
    trustpilot.ensure_session_ready("ThriftBooks")  # idempotent: no second pass
    auth_calls = [c[2] for c in calls if c[1] == "auth"]
    assert auth_calls == ["status", "login"]


def test_warmup_concurrent_calls_single_warmup(monkeypatch):
    import threading as _threading
    calls = _capture_cli(monkeypatch, {"auth status": {"isFresh": True}})
    threads = [
        _threading.Thread(target=trustpilot.ensure_session_ready, args=("ThriftBooks",))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([c for c in calls if c[1] == "auth"]) == 1  # vs-mode race guard


def test_warmup_skips_non_brand_topic(monkeypatch):
    # AE6: generic topic with trustpilot active -> no warm-up, no Chrome.
    calls = _capture_cli(monkeypatch, {})
    trustpilot.ensure_session_ready("AI coding agents")
    assert calls == []


def test_warmup_domain_bypasses_brand_gate(monkeypatch):
    calls = _capture_cli(monkeypatch, {"auth status": {"isFresh": True}})
    trustpilot.ensure_session_ready("Stanley Steemer carpet cleaning", has_domain=True)
    assert len(calls) == 1


def test_warmup_respects_browser_opt_out(monkeypatch):
    calls = _capture_cli(monkeypatch, {})
    trustpilot.ensure_session_ready("ThriftBooks", config={trustpilot.NO_BROWSER_ENV: "1"})
    assert calls == []


def test_warmup_failure_never_raises(monkeypatch):
    calls = _capture_cli(monkeypatch, {
        "auth status": {"error": "no session"},
        "auth login": {"error": "chrome missing"},
    })
    trustpilot.ensure_session_ready("ThriftBooks")  # must not raise
    assert [c[2] for c in calls if c[1] == "auth"] == ["status", "login"]


def test_warmup_logs_no_token_bytes(monkeypatch):
    # The auth status payload carries live WAF-token prefixes; log lines must
    # only ever contain structured status strings.
    logged: list[str] = []
    monkeypatch.setattr(trustpilot, "_log", lambda msg: logged.append(msg))
    _capture_cli(monkeypatch, {
        "auth status": {"isFresh": False, "tokenPrefix": "48372fee-99b"},
        "auth login": {"ok": True, "token": "48372fee-99b1-dead-beef"},
    })
    trustpilot.ensure_session_ready("ThriftBooks")
    assert logged and all("48372fee" not in msg for msg in logged)


def test_trustpilot_capped_to_single_fetch():
    # N subqueries listing trustpilot must produce exactly one stream: every
    # stream would use the identical company identifier, and each extra one
    # risks its own WAF-cookie Chrome harvest.
    assert pipeline.MAX_SOURCE_FETCHES.get("trustpilot") == 1


# ---- review-driven hardening ----

def test_search_trustpilot_warms_session_at_first_touch(monkeypatch):
    # The warm-up runs inside the source fetch (never the pipeline fan-out
    # setup), so it must precede the info call within one search invocation.
    calls = _capture_cli(monkeypatch, {
        "auth status": {"isFresh": True},
        "info": INFO_OK,
    })
    trustpilot.search_trustpilot(
        "ThriftBooks", D1, D2, explicit_domain="www.thriftbooks.com")
    assert calls[0][1] == "auth" and calls[0][2] == "status"
    assert [c for c in calls if c[1] == "info"]


def test_warmup_ttl_lapse_rechecks(monkeypatch):
    import time as _time
    calls = _capture_cli(monkeypatch, {"auth status": {"isFresh": True}})
    trustpilot.ensure_session_ready("ThriftBooks")
    assert len(calls) == 1
    # Within the TTL: no re-check.
    trustpilot.ensure_session_ready("ThriftBooks")
    assert len(calls) == 1
    # After the TTL lapses (long-lived host process): cheap re-check fires.
    trustpilot._warmup_at = _time.monotonic() - (trustpilot.WARMUP_TTL_SECONDS + 1)
    trustpilot.ensure_session_ready("ThriftBooks")
    assert len(calls) == 2


def test_hint_on_non_brand_topic_stays_quiet(monkeypatch):
    # An auto-resolved hint must not widen activation beyond brand-shaped
    # topics -- only a USER-set domain proves brand intent (AE6 contract).
    calls = _capture_cli(monkeypatch, {})
    out = trustpilot.search_trustpilot(
        "AI coding agents", D1, D2,
        explicit_domain="aicodingagents.com", domain_is_hint=True)
    assert out == {"results": []}
    assert calls == []


def test_transient_search_error_not_cached(monkeypatch):
    # A flaky search must not poison the per-topic cache for the process.
    calls = _capture_cli(monkeypatch, {
        "search": [{"error": "timeout"}, dict(THRIFTBOOKS_HITS)],
        "info": INFO_OK,
    })
    assert trustpilot._search_domain("ThriftBooks") is None
    assert trustpilot._search_domain("ThriftBooks") == "www.thriftbooks.com"
    assert len(_search_args(calls)) == 2


def test_empty_search_payload_not_cached(monkeypatch):
    # Empty stdout parses to {} (exit 0, no output): a degenerate payload,
    # not a definitive no-match -- it must not become a permanent cache entry.
    calls = _capture_cli(monkeypatch, {
        "search": [{}, dict(THRIFTBOOKS_HITS)],
        "info": INFO_OK,
    })
    assert trustpilot._search_domain("ThriftBooks") is None
    assert trustpilot._search_domain("ThriftBooks") == "www.thriftbooks.com"
    assert len(_search_args(calls)) == 2


def test_definitive_no_match_is_cached(monkeypatch):
    # A well-formed empty hits list IS definitive: cache it so repeat lookups
    # for a name Trustpilot does not know cost one subprocess, not N.
    calls = _capture_cli(monkeypatch, {"search": {"hits": []}, "info": INFO_OK})
    assert trustpilot._search_domain("ChowNow") is None
    assert trustpilot._search_domain("ChowNow") is None
    assert len(_search_args(calls)) == 1
