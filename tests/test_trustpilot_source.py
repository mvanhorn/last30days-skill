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
        lambda cmd, **k: {"isFresh": True} if cmd[1] == "auth" else
        {"name": "ChowNow", "trustScore": 1.2, "reviewCount": 49, "aiSummary": "Bad."},
    )
    out = trustpilot.search_trustpilot("ChowNow", "2026-06-01", "2026-06-27")
    assert len(out["results"]) == 1
    assert out["results"][0]["name"] == "ChowNow"


# ---- degradation paths ----

def test_cli_error_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(
        trustpilot, "_run_cli",
        lambda cmd, **k: {"isFresh": True} if cmd[1] == "auth" else {"error": "HTTP 500"},
    )
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
    # A replayable session unless the test says otherwise: without one the
    # lane runs no fetch command at all.
    # ... and no cached company record unless the test supplies one.
    responses = {
        "auth status": {"isFresh": True, "hasSession": True},
        "info --local": {"error": "no cached info"},
        **responses,
    }

    def fake(cmd, timeout):
        calls.append(list(cmd))
        key = cmd[1] if cmd[1] != "auth" else f"auth {cmd[2]}"
        if key == "info" and "--local" in cmd:
            key = "info --local"
        resp = responses.get(key, {})
        if isinstance(resp, list):
            return dict(resp.pop(0)) if resp else {}
        return dict(resp)

    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", fake)
    return calls


def _info_args(calls):
    """Live `info` calls: the ones that would harvest a session."""
    return [c for c in calls if c[1] == "info" and "--local" not in c]


def _local_info_args(calls):
    return [c for c in calls if c[1] == "info" and "--local" in c]


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


# ---- session check (ensure_session_ready): never launches Chrome ----

def test_fresh_session_is_ready_without_login(monkeypatch):
    calls = _capture_cli(monkeypatch, {"auth status": {"isFresh": True, "hasSession": True}})
    assert trustpilot.ensure_session_ready("ThriftBooks") is True
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]


@pytest.mark.parametrize("status", [
    {"error": "no session"},
    {"hasSession": False, "isFresh": False, "harvestedAt": "0001-01-01T00:00:00Z"},
    {},
])
def test_missing_or_stale_session_never_logs_in(monkeypatch, status):
    # `auth login` is the command that printed "Launching Chrome via
    # agent-browser (about 10 seconds)..." in the 2026-11-10 runs.
    calls = _capture_cli(monkeypatch, {"auth status": status, "auth login": {"ok": True}})
    assert trustpilot.ensure_session_ready("ThriftBooks") is False
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]


def test_session_about_to_lapse_is_not_ready(monkeypatch):
    # The CLI replays a session for four minutes and launches Chrome after
    # that, so one in its last seconds could lapse inside the fetch command.
    from datetime import datetime, timedelta, timezone

    def harvested(seconds_ago):
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()

    _capture_cli(monkeypatch, {"auth status": {"isFresh": True, "harvestedAt": harvested(230)}})
    assert trustpilot.ensure_session_ready("ThriftBooks") is False
    _capture_cli(monkeypatch, {"auth status": {"isFresh": True, "harvestedAt": harvested(60)}})
    assert trustpilot.ensure_session_ready("ThriftBooks") is True


def test_every_check_reads_the_session_again(monkeypatch):
    # No memo: the session lasts four minutes, so a remembered "fresh" could
    # send a fetch command after it lapsed.
    calls = _capture_cli(monkeypatch, {"auth status": [{"isFresh": True}, {"isFresh": False}]})
    assert trustpilot.ensure_session_ready("ThriftBooks") is True
    assert trustpilot.ensure_session_ready("ThriftBooks") is False
    assert len(calls) == 2


# ---- the lane with no replayable session (2026-11-10 Waymo run) ----

def test_stale_session_skips_the_lane_with_the_sign_in_command(monkeypatch):
    calls = _capture_cli(monkeypatch, {
        "auth status": {"hasSession": False, "isFresh": False},
        "info": INFO_OK,
    })
    logged: list[str] = []
    monkeypatch.setattr(trustpilot, "_log", lambda msg: logged.append(msg))

    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")

    # No fetch command ran: `info` and `search` harvest by themselves. The
    # one `info --local` read of the CLI's cached record found nothing.
    assert _info_args(calls) == [] and _search_args(calls) == []
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]
    assert _local_info_args(calls) == [["trustpilot-pp-cli", "info", "waymo.com", "--local", "--agent"]]
    assert out == {"results": [], "needs_session": True, "skipped": trustpilot.SESSION_NEEDED}
    assert "trustpilot-pp-cli auth login" in out["skipped"]
    assert "error" not in out
    assert f"skipped: {trustpilot.SESSION_NEEDED}" in logged


def test_session_lapsing_between_search_and_info_stops_before_info(monkeypatch):
    calls = _capture_cli(monkeypatch, {
        "auth status": [{"isFresh": True}, {"isFresh": True}, {"isFresh": False}],
        "search": THRIFTBOOKS_HITS,
        "info": INFO_OK,
    })

    out = trustpilot.search_trustpilot("ThriftBooks", D1, D2)

    assert out["needs_session"] is True
    assert _info_args(calls) == []


def _run_with_stale_session(monkeypatch):
    """A whole pipeline run (mock sources) whose only real lane is Trustpilot
    with no replayable session."""
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "info": INFO_OK})
    monkeypatch.setattr(pipeline, "which", lambda name: f"/usr/bin/{name}")
    real_impl = pipeline._retrieve_stream_impl

    def impl(**kwargs):
        if kwargs["source"] == "trustpilot":
            return real_impl(**{**kwargs, "mock": False})
        return real_impl(**kwargs)

    monkeypatch.setattr(pipeline, "_retrieve_stream_impl", impl)
    return pipeline.run(
        topic="Waymo", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"}, depth="quick",
        requested_sources=["hackernews", "trustpilot"], mock=True,
        trustpilot_domain="waymo.com",
    )


def test_skipped_lane_is_a_quiet_skip_not_a_failed_source(monkeypatch):
    from lib import render

    report = _run_with_stale_session(monkeypatch)

    outcome = report.source_status["trustpilot"]
    assert (outcome.state, outcome.attempted, outcome.detail) == (
        "no-results", False, trustpilot.SESSION_NEEDED,
    )
    assert "trustpilot" not in report.errors_by_source
    assert not any("trustpilot" in warning.lower() for warning in report.warnings)
    compact = render.render_compact(report)
    assert "Some sources failed" not in compact
    assert "Partial Coverage" not in compact or "Trustpilot" not in compact.split("Partial Coverage")[1].split("\n## ")[0]


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


# ---- cached company record: a free fallback when the session is stale ----

def _fetched(days_ago):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_stale_session_uses_the_cached_record_and_labels_its_age(monkeypatch):
    calls = _capture_cli(monkeypatch, {
        "auth status": {"hasSession": False, "isFresh": False},
        "info --local": {**INFO_OK, "fetchedAt": _fetched(3)},
        "auth login": {"ok": True},
    })

    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")

    # Nothing that can open a browser ran.
    assert _info_args(calls) == [] and _search_args(calls) == []
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]
    assert "needs_session" not in out and "error" not in out
    items = trustpilot.parse_trustpilot_response(out, query="Waymo")
    assert len(items) == 1
    assert items[0]["title"] == "ThriftBooks: TrustScore 4.5 (cached 3 days ago)"
    assert "cached 3 days ago" in items[0]["why_relevant"]


@pytest.mark.parametrize("days,label", [(0, "cached today"), (1, "cached 1 day ago")])
def test_cache_age_labels(monkeypatch, days, label):
    _capture_cli(monkeypatch, {
        "auth status": {"isFresh": False},
        "info --local": {**INFO_OK, "fetchedAt": _fetched(days)},
    })
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    assert label in trustpilot.parse_trustpilot_response(out)[0]["title"]


def test_cached_record_without_a_fetch_time_says_its_age_is_unknown(monkeypatch):
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "info --local": INFO_OK})
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    assert "(cached, age unknown)" in trustpilot.parse_trustpilot_response(out)[0]["title"]


def test_cached_record_older_than_the_window_is_not_used(monkeypatch):
    _capture_cli(monkeypatch, {
        "auth status": {"isFresh": False},
        "info --local": {**INFO_OK, "fetchedAt": _fetched(45)},
    })
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    assert out == {"results": [], "needs_session": True, "skipped": trustpilot.SESSION_NEEDED}


def test_stale_session_with_only_a_name_reads_no_cache(monkeypatch):
    # Cached records are keyed by domain, and name -> domain needs a session.
    calls = _capture_cli(monkeypatch, {
        "auth status": {"isFresh": False}, "info --local": INFO_OK,
    })
    out = trustpilot.search_trustpilot("ThriftBooks", D1, D2)
    assert out["needs_session"] is True
    assert _local_info_args(calls) == []


def test_fresh_session_fetches_live_and_carries_no_cache_label(monkeypatch):
    calls = _capture_cli(monkeypatch, {"info": INFO_OK, "info --local": INFO_OK})
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    assert _local_info_args(calls) == []
    assert "cached" not in trustpilot.parse_trustpilot_response(out)[0]["title"]


# ---- can the lane run now? (--diagnose `trustpilot_can_run`) ----

@pytest.mark.parametrize("status,cache,expected", [
    ({"isFresh": True, "hasSession": True}, {"companies": 0}, True),
    ({"isFresh": False}, {"companies": 2}, True),
    ({"isFresh": False}, {"companies": 0}, False),
    ({"isFresh": False}, {"error": "boom"}, False),
])
def test_can_run_now_needs_a_fresh_session_or_a_cached_company(monkeypatch, status, cache, expected):
    calls = _capture_cli(monkeypatch, {
        "auth status": status, "doctor-tp": cache, "auth login": {"ok": True},
    })
    assert trustpilot.can_run_now({})["can_run"] is expected
    # Local reads only.
    assert {c[1] for c in calls} <= {"auth", "doctor-tp"}
    assert [c[2] for c in calls if c[1] == "auth"] == ["status"]


def test_can_run_now_is_false_without_the_cli_or_with_the_opt_out(monkeypatch):
    calls = _capture_cli(monkeypatch, {})
    assert trustpilot.can_run_now({trustpilot.NO_BROWSER_ENV: "1"})["can_run"] is False
    monkeypatch.setattr(trustpilot, "_is_available", lambda: False)
    assert trustpilot.can_run_now({})["can_run"] is False
    assert calls == []


def test_diagnose_reports_whether_trustpilot_can_run(monkeypatch):
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "doctor-tp": {"companies": 0}})
    assert pipeline.diagnose({}, None, safe=True)["trustpilot_can_run"] is False
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "doctor-tp": {"companies": 1}})
    assert pipeline.diagnose({}, None, safe=True)["trustpilot_can_run"] is True


def test_doctor_record_says_when_trustpilot_cannot_run_now(monkeypatch):
    from lib import doctor, health

    monkeypatch.setattr(
        doctor, "_cli_gated_record",
        lambda config, cli, purpose: doctor._record(status=health.OK, requires=cli),
    )
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "doctor-tp": {"companies": 0}})
    record = doctor._trustpilot_record({})
    assert record["status"] == health.OK
    assert "cannot run now" in record["note"] and "never opens a browser" in record["note"]
    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "doctor-tp": {"companies": 3}})
    assert "3 cached company records" in doctor._trustpilot_record({})["note"]


def test_runbook_resolves_the_domain_only_when_trustpilot_can_run():
    from tests.skill_contract import reference_text

    runbook = reference_text("research-runbook")
    step = runbook.split("### Step 0.5d", 1)[1].split("\n### ", 1)[0]
    assert "only when `--diagnose` reports `trustpilot_can_run: true`" in step
    assert "never opens a browser" in step
    assert step.index("trustpilot_can_run") < step.index("WebSearch(")


# ---- a cached record carries its fetch date, not yesterday ----

def test_cached_record_is_dated_by_its_fetch_not_yesterday(monkeypatch):
    fetched = _fetched(21)
    _capture_cli(monkeypatch, {
        "auth status": {"isFresh": False},
        "info --local": {**INFO_OK, "fetchedAt": fetched},
    })
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    item = trustpilot.parse_trustpilot_response(out)[0]
    assert item["date"] == fetched[:10]
    assert "cached 21 days ago" in item["title"]


def test_cached_record_with_no_fetch_time_is_undated(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from lib import dates

    _capture_cli(monkeypatch, {"auth status": {"isFresh": False}, "info --local": INFO_OK})
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    raw = trustpilot.parse_trustpilot_response(out, query="Waymo")
    assert raw[0]["date"] is None
    # Undated, the item still reaches the report (like other undated items).
    today = datetime.now(timezone.utc).date()
    kept = pipeline._normalize_score_dedupe(
        "trustpilot", raw, (today - timedelta(days=30)).isoformat(), today.isoformat(),
        "balanced_recent", "Waymo",
    )
    assert len(kept) == 1 and kept[0].published_at is None
    assert dates.get_date_range(1)[0] not in str(kept[0].published_at)


def test_live_record_keeps_the_current_date(monkeypatch):
    from lib import dates

    _capture_cli(monkeypatch, {"info": INFO_OK})
    out = trustpilot.search_trustpilot("Waymo", D1, D2, explicit_domain="waymo.com")
    assert trustpilot.parse_trustpilot_response(out)[0]["date"] == dates.get_date_range(1)[0]
