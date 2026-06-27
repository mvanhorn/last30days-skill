"""Tests for the Trustpilot source adapter (lib/trustpilot.py).

Covers the brand-shape gate (the primary quiet-keeper), the browser opt-out,
field mapping from the info envelope, and graceful degradation.
"""

from __future__ import annotations

import pytest

from lib import trustpilot


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
