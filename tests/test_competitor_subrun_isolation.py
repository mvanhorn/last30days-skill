"""Main-topic targeting must not reach another entity's pipeline invocation."""

import json

import pytest

from tests.competitor_cli_audit_helpers import run_competitor_cli


@pytest.mark.parametrize(
    ("flag", "value", "keyword", "main_value"),
    [
        ("--subreddits", "MainOnly,MainNews", "subreddits", ["MainOnly", "MainNews"]),
        ("--x-handle", "MainOnly", "x_handle", "MainOnly"),
        ("--x-related", "MainFriend", "x_related", ["MainFriend"]),
        ("--github-user", "MainOwner", "github_user", "mainowner"),
        ("--github-repo", "mainowner/project", "github_repos", ["mainowner/project"]),
        ("--tiktok-hashtags", "MainOnly", "tiktok_hashtags", ["MainOnly"]),
        ("--tiktok-creators", "MainCreator", "tiktok_creators", ["MainCreator"]),
        ("--ig-creators", "MainInstagram", "ig_creators", ["MainInstagram"]),
    ],
)
def test_main_targeting_does_not_leak_to_peer(
    monkeypatch, tmp_path, flag, value, keyword, main_value
):
    observed = run_competitor_cli(monkeypatch, tmp_path, args=[flag, value])

    assert observed.calls["MainBrand"][keyword] == main_value
    for peer in ("PeerOne", "PeerTwo"):
        assert observed.calls[peer].get(keyword) is None


def test_main_plan_is_not_reused_for_a_peer(monkeypatch, tmp_path):
    main_plan = {
        "intent": "general",
        "freshness_mode": "recent",
        "cluster_mode": "none",
        "subqueries": [{
            "label": "main-only", "search_query": "MainBrand only",
            "ranking_query": "MainBrand", "sources": ["reddit"],
        }],
    }
    plan_file = tmp_path / "main-plan.json"
    plan_file.write_text(json.dumps(main_plan), encoding="utf-8")

    observed = run_competitor_cli(monkeypatch, tmp_path, args=["--plan", str(plan_file)])

    assert observed.calls["MainBrand"]["external_plan"] == main_plan
    assert observed.calls["PeerOne"].get("external_plan") is None
    assert observed.calls["PeerTwo"].get("external_plan") is None


def test_peer_context_does_not_mutate_main_or_an_unresolved_peer(monkeypatch, tmp_path):
    config = {"BRAVE_API_KEY": "dummy-brave-key"}
    observed = run_competitor_cli(
        monkeypatch, tmp_path, args=["--x-handle", "MainOnly"],
        config=config, has_backend=True,
        resolutions={"PeerOne": {"context": "PeerOne unique context"}, "PeerTwo": {}},
    )

    assert observed.calls["PeerOne"]["config"].get("_auto_resolve_context", "") == "PeerOne unique context"
    assert observed.calls["PeerTwo"]["config"].get("_auto_resolve_context", "") == ""
    assert observed.calls["MainBrand"]["config"].get("_auto_resolve_context", "") == ""
    assert "_auto_resolve_context" not in config


def test_main_context_does_not_leak_to_peer_without_context(monkeypatch, tmp_path):
    observed = run_competitor_cli(
        monkeypatch, tmp_path, args=["--auto-resolve"],
        config={"BRAVE_API_KEY": "dummy-brave-key"}, has_backend=True,
        resolutions={
            "MainBrand": {"context": "MainBrand private launch context"},
            "PeerOne": {"context": "PeerOne distinct context"},
            "PeerTwo": {},
        },
    )

    assert observed.calls["MainBrand"]["config"]["_auto_resolve_context"] == "MainBrand private launch context"
    assert observed.calls["PeerOne"]["config"]["_auto_resolve_context"] == "PeerOne distinct context"
    assert observed.calls["PeerTwo"]["config"].get("_auto_resolve_context", "") == ""
    assert observed.reports["PeerTwo"].artifacts["resolved"]["context"] == ""
