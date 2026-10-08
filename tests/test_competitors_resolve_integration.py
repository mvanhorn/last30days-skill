"""Per-entity resolution through the real CLI and competitor fan-out."""

import json

import pytest

from tests.competitor_cli_audit_helpers import run_competitor_cli


def test_resolver_results_reach_each_competitor_pipeline(monkeypatch, tmp_path):
    resolutions = {
        "PeerOne": {
            "x_handle": "peerone", "subreddits": ["PeerOneNews"],
            "github_user": "ownerone", "github_repos": ["ownerone/project"],
            "context": "PeerOne launch",
        },
        "PeerTwo": {
            "x_handle": "peertwo", "subreddits": ["PeerTwoNews"],
            "github_user": "ownertwo", "github_repos": ["ownertwo/project"],
            "context": "PeerTwo launch",
        },
    }
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--x-handle", "mainonly", "--subreddits", "MainOnly"],
        config={"BRAVE_API_KEY": "dummy-brave-key"},
        has_backend=True, resolutions=resolutions,
    )

    assert sorted(call.args[0] for call in observed.resolver.call_args_list) == ["PeerOne", "PeerTwo"]
    for peer, expected in resolutions.items():
        call = observed.calls[peer]
        assert call["internal_subrun"] is True
        for key in ("x_handle", "subreddits", "github_user", "github_repos"):
            assert call[key] == expected[key]
        assert call["config"]["_auto_resolve_context"] == expected["context"]
        assert observed.reports[peer].artifacts["resolved"] == {
            "entity": peer, "trustpilot_domain": "", **expected,
        }


def test_peer_plan_overrides_conflicting_resolver_values(monkeypatch, tmp_path):
    plan = {
        "PeerOne": {
            "x_handle": "@planned_one", "x_related": ["@planned_friend"],
            "github_user": "@PlannedOwner", "github_repos": ["plannedowner/project"],
            "context": "Planned PeerOne context",
        }
    }
    plan_file = tmp_path / "peer-plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--x-handle", "mainonly", "--subreddits", "MainOnly", "--competitors-plan", str(plan_file)],
        config={"BRAVE_API_KEY": "dummy-brave-key"}, has_backend=True,
        resolutions={
            "PeerOne": {"x_handle": "resolved_one", "subreddits": ["ResolvedOne"],
                        "github_user": "resolvedowner", "github_repos": ["resolvedowner/project"],
                        "context": "Resolved PeerOne context"},
            "PeerTwo": {},
        },
    )

    assert sorted(call.args[0] for call in observed.resolver.call_args_list) == ["PeerOne", "PeerTwo"]
    peer = observed.calls["PeerOne"]
    assert peer["x_handle"] == "planned_one"
    assert peer["x_related"] == ["planned_friend"]
    assert peer["subreddits"] == ["ResolvedOne"]
    assert peer["github_user"] == "plannedowner"
    assert peer["github_repos"] == ["plannedowner/project"]
    assert peer["config"]["_auto_resolve_context"] == "Planned PeerOne context"
    assert observed.reports["PeerOne"].artifacts["resolved"]["context"] == "Planned PeerOne context"


def test_complete_peer_plan_skips_only_that_peers_resolver(monkeypatch, tmp_path):
    plan_file = tmp_path / "peer-plan.json"
    plan_file.write_text(json.dumps({"PeerOne": {"x_handle": "planned_one", "subreddits": ["PlannedOne"]}}))
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--x-handle", "mainonly", "--competitors-plan", str(plan_file)],
        config={"BRAVE_API_KEY": "dummy-brave-key"}, has_backend=True,
        resolutions={"PeerTwo": {"x_handle": "resolved_two"}},
    )

    assert [call.args[0] for call in observed.resolver.call_args_list] == ["PeerTwo"]
    assert observed.calls["PeerOne"]["x_handle"] == "planned_one"
    assert observed.calls["PeerOne"]["subreddits"] == ["PlannedOne"]
    assert observed.calls["PeerTwo"]["x_handle"] == "resolved_two"


@pytest.mark.parametrize("mock_mode,has_backend", [(True, True), (False, False)])
def test_unavailable_resolution_keeps_peer_defaults(monkeypatch, tmp_path, mock_mode, has_backend):
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--x-handle", "mainonly", *(["--mock"] if mock_mode else [])],
        has_backend=has_backend,
    )

    observed.resolver.assert_not_called()
    for peer in ("PeerOne", "PeerTwo"):
        assert observed.calls[peer]["x_handle"] is None
        assert observed.calls[peer]["subreddits"] is None


def test_resolver_failure_warns_and_runs_peer_with_defaults(monkeypatch, tmp_path, capsys):
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--x-handle", "mainonly"], has_backend=True,
        resolutions=RuntimeError("dummy resolver offline"),
    )

    assert observed.resolver.call_count == 2
    stderr = capsys.readouterr().err
    for peer in ("PeerOne", "PeerTwo"):
        assert f"auto_resolve failed for '{peer}': RuntimeError: dummy resolver offline" in stderr
        assert observed.calls[peer]["x_handle"] is None
        assert observed.calls[peer]["subreddits"] is None
        assert observed.reports[peer].artifacts["resolved"]["entity"] == peer
