"""Entity X handles: homepage links, and the hosted-run handle decision check.

Hosted runs pass --plan, so the engine's search-based auto-resolve stands down
and the host model resolves handles. A skipped lookup used to cost nothing:
the run went ahead with no --x-handle and X never searched the subject's own
posts. These tests pin the keyless homepage reader and the check that makes a
hosted entity run either name a handle or say why there is none.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest

import last30days as cli
from lib import resolve

DUMMY_CONFIG = {"OPENROUTER_API_KEY": "dummy-openrouter-key-000"}
DIAG = {
    "available_sources": ["reddit", "x", "hackernews"],
    "providers": {"google": False, "openai": False, "xai": False},
    "x_backend": None,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": None,
    "permission_preflight": {},
}
PLAN_WITH_X = (
    '{"intent":"product","freshness_mode":"balanced_recent","cluster_mode":"none",'
    '"subqueries":[{"label":"primary","search_query":"Jev","ranking_query":'
    '"What are people saying about Jev?","sources":["reddit","x","hackernews"],"weight":1.0}]}'
)
PLAN_WITHOUT_X = PLAN_WITH_X.replace('"x",', "")

HOMEPAGE = """
<html><body>
<a href="https://x.com/intent/tweet?text=hi">Share</a>
<a href="https://twitter.com/someone/status/123">Quoted post</a>
<p>Follow @notourhandle for updates</p>
<footer>
  <a href="https://x.com/typesafeai">X</a>
  <a href='https://twitter.com/typesafeai/'>Twitter</a>
  <a href="https://github.com/typesafe-ai/typesafe-sdk-python">SDK</a>
  <a href="https://github.com/typesafe-ai">GitHub</a>
  <a href="https://github.com/features">Features</a>
</footer>
</body></html>
"""


class _RetrievalReached(Exception):
    pass


def _run_main(monkeypatch, argv, *, host_env=None, socials=None, diag=None):
    for name in ("LAST30DAYS_API_KEY", "LAST30DAYS_API_BASE", "LAST30DAYS_MEMORY_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    for name, value in (host_env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])
    run_mock = mock.Mock(side_effect=_RetrievalReached)
    socials_mock = mock.Mock(return_value=socials or {"x_handle": "", "github_user": ""})
    err = io.StringIO()
    with mock.patch.object(cli.env, "get_config", return_value=dict(DUMMY_CONFIG)), \
         mock.patch.object(cli.pipeline, "diagnose", mock.Mock(return_value=dict(diag or DIAG))), \
         mock.patch.object(cli.pipeline, "run", run_mock), \
         mock.patch("lib.resolve.auto_resolve", mock.Mock(return_value={})), \
         mock.patch("lib.resolve.official_site_socials", socials_mock), \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()), \
         redirect_stdout(io.StringIO()), redirect_stderr(err):
        try:
            rc = cli.main()
        except _RetrievalReached:
            rc = None
        except SystemExit as exc:
            rc = exc.code
    return rc, err.getvalue(), run_mock, socials_mock


HOSTED = {"CLAUDECODE": "1"}


# --- homepage reader -------------------------------------------------------


def test_official_site_socials_reads_profile_links_only():
    with mock.patch.object(resolve.http, "get_text", return_value=HOMEPAGE) as get:
        socials = resolve.official_site_socials("typesafe.ai")
    assert socials == {"x_handle": "typesafeai", "github_user": "typesafe-ai"}
    assert get.call_args.args[0] == "https://typesafe.ai/"


def test_official_site_socials_leaves_ambiguous_accounts_unresolved():
    page = """
    <a href="https://x.com/happycustomer">Customer</a>
    <a href="https://x.com/someinvestor">Investor</a>
    <a href="https://github.com/somedependency/lib">Built on</a>
    <a href="https://github.com/otherdependency">Also</a>
    """
    with mock.patch.object(resolve.http, "get_text", return_value=page):
        assert resolve.official_site_socials("typesafe.ai") == {"x_handle": "", "github_user": ""}


def test_official_site_socials_prefers_the_footer_over_a_repeated_testimonial():
    page = """
    <a href="https://x.com/happycustomer">quote</a>
    <a href="https://x.com/happycustomer">quote again</a>
    <footer><a href="https://x.com/acmehq">X</a></footer>
    """
    with mock.patch.object(resolve.http, "get_text", return_value=page):
        assert resolve.official_site_socials("example.com")["x_handle"] == "acmehq"


def test_official_site_socials_prefers_the_account_named_after_the_site():
    page = """
    <a href="https://x.com/happycustomer">quote</a>
    <a href="https://x.com/TypeSafeAI">Follow us</a>
    """
    with mock.patch.object(resolve.http, "get_text", return_value=page):
        assert resolve.official_site_socials("www.typesafe.ai")["x_handle"] == "TypeSafeAI"


def test_official_site_socials_treats_capitalizations_as_one_account():
    page = """
    <a href="https://x.com/TypeSafeAI">X</a>
    <a href="https://twitter.com/typesafeai">Twitter</a>
    <a href="https://github.com/TypeSafe-AI">GitHub</a>
    <a href="https://github.com/typesafe-ai/sdk">SDK</a>
    """
    with mock.patch.object(resolve.http, "get_text", return_value=page):
        assert resolve.official_site_socials("example.com") == {
            "x_handle": "TypeSafeAI", "github_user": "TypeSafe-AI",
        }


def test_official_site_socials_empty_when_page_unreachable():
    with mock.patch.object(resolve.http, "get_text", return_value=None):
        assert resolve.official_site_socials("typesafe.ai") == {"x_handle": "", "github_user": ""}


@pytest.mark.parametrize(
    "site,url",
    [
        ("typesafe.ai", "https://typesafe.ai/"),
        ("https://www.TypeSafe.ai/pricing?x=1", "https://www.typesafe.ai/"),
        ("not a domain", ""),
        ("", ""),
    ],
)
def test_official_site_url_normalizes_to_homepage(site, url):
    assert resolve._official_site_url(site) == url


def test_official_site_socials_skips_invalid_site_without_fetching():
    with mock.patch.object(resolve.http, "get_text") as get:
        assert resolve.official_site_socials("javascript:alert(1)") == {"x_handle": "", "github_user": ""}
    get.assert_not_called()


# --- hosted handle decision check -----------------------------------------


def test_hosted_entity_run_planning_x_without_handle_stops(monkeypatch):
    rc, err, run_mock, _ = _run_main(monkeypatch, ["Jev", "--plan", PLAN_WITH_X], host_env=HOSTED)
    assert rc == cli.X_HANDLE_DECISION_EXIT
    assert "X handle decision missing" in err
    assert "--official-site" in err and "--no-x-handle" in err
    run_mock.assert_not_called()


def test_official_site_handle_satisfies_the_check(monkeypatch):
    rc, err, run_mock, socials_mock = _run_main(
        monkeypatch, ["Jev", "--plan", PLAN_WITH_X, "--official-site", "typesafe.ai"],
        host_env=HOSTED, socials={"x_handle": "typesafeai", "github_user": "typesafe-ai"},
    )
    assert rc is None
    assert "@typesafeai" in err
    socials_mock.assert_called_once_with("typesafe.ai")
    assert run_mock.call_args.kwargs["x_handle"] == "typesafeai"


def test_user_x_handle_beats_the_homepage(monkeypatch):
    rc, _err, run_mock, _ = _run_main(
        monkeypatch,
        ["Jev", "--plan", PLAN_WITH_X, "--official-site", "typesafe.ai", "--x-handle", "CompleteSkeptic"],
        host_env=HOSTED, socials={"x_handle": "typesafeai", "github_user": ""},
    )
    assert rc is None
    assert run_mock.call_args.kwargs["x_handle"] == "CompleteSkeptic"


def test_official_site_without_x_link_still_needs_a_decision(monkeypatch):
    rc, err, _run, _ = _run_main(
        monkeypatch, ["Jev", "--plan", PLAN_WITH_X, "--official-site", "typesafe.ai"],
        host_env=HOSTED,
    )
    assert rc == cli.X_HANDLE_DECISION_EXIT
    assert "X handle decision missing" in err


def test_no_x_handle_reason_satisfies_the_check(monkeypatch):
    rc, err, _run, _ = _run_main(
        monkeypatch,
        ["Jev", "--plan", PLAN_WITH_X, "--no-x-handle", "official site links no X account"],
        host_env=HOSTED,
    )
    assert rc is None
    assert "No X handle: official site links no X account" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["Jev", "--plan", PLAN_WITHOUT_X],
        ["Jev", "--plan", PLAN_WITH_X, "--quick"],
        ["how do decision models work?", "--plan", PLAN_WITH_X],
        ["@typesafeai", "--plan", PLAN_WITH_X],
    ],
)
def test_check_exempts_runs_that_do_not_need_a_handle(monkeypatch, argv):
    rc, _err, _run, _ = _run_main(monkeypatch, argv, host_env=HOSTED)
    assert rc is None


def test_check_does_not_apply_when_x_will_not_run(monkeypatch):
    # --search without x, EXCLUDE_SOURCES=x, or no X backend all drop x from
    # diagnose's available_sources; the plan naming x must not stop the run.
    no_x = dict(DIAG, available_sources=["reddit", "hackernews"])
    rc, _err, _run, _ = _run_main(
        monkeypatch, ["Jev", "--plan", PLAN_WITH_X], host_env=HOSTED, diag=no_x,
    )
    assert rc is None


def test_check_does_not_apply_outside_agent_hosts(monkeypatch):
    rc, _err, _run, _ = _run_main(monkeypatch, ["Jev", "--plan", PLAN_WITH_X])
    assert rc is None
