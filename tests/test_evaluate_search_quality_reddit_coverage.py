"""Offline tests for evaluate_search_quality.py --reddit-coverage."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from unittest import mock

import evaluate_search_quality as evaluator

ROOT = Path(__file__).resolve().parents[1]
TOPICS_FIXTURE = ROOT / "fixtures" / "reddit_coverage_topics.json"


def _thread(title: str, *, comments: int = 0, source: str = "arctic") -> dict:
    return {
        "item_id": title, "source": "reddit", "title": title, "url": f"https://www.reddit.com/r/t/comments/{abs(hash(title)) % 99999}/",
        "metadata": {"top_comments": [{"id": f"c{i}"} for i in range(comments)], "engagement_source": source},
    }


def _report(threads: list[dict], detail: str = "web+arctic served 3") -> dict:
    return {
        "topic": "x",
        "ranked_candidates": [],
        "items_by_source": {"reddit": threads},
        "source_status": {"reddit": {"source": "reddit", "state": "ok", "detail": detail}},
    }


def _v2_report(threads: list[dict]) -> dict:
    """A pre-v3 engine's report: a top-level reddit list with no topic bar."""
    return {"reddit": [{"title": t["title"], "url": t["url"], "top_comments": [{"id": "c"}]} for t in threads]}


def _in_process_judge(topic: str, titles: list[str]) -> list[bool]:
    from lib import rerank

    entity = rerank._primary_entity(topic).lower()
    return [rerank._entity_grounded(title, entity) if entity else True for title in titles]


def test_fixture_has_about_twelve_topics_with_the_required_kinds():
    rows = evaluator.load_reddit_coverage_topics(TOPICS_FIXTURE)
    assert 10 <= len(rows) <= 14
    kinds = [row["kind"] for row in rows]
    assert kinds.count("no_coverage") == 1
    assert "homonym" in kinds and "breaking" in kinds
    no_coverage = next(row for row in rows if row["kind"] == "no_coverage")
    assert no_coverage["expect_coverage"] is False
    assert all(row["expect_coverage"] for row in rows if row["kind"] != "no_coverage")
    assert len({row["topic"].lower() for row in rows}) == len(rows)


def test_coverage_env_never_loads_config_or_keychain(monkeypatch):
    with mock.patch.object(evaluator.envlib, "get_config", side_effect=AssertionError("config loaded")):
        run_env = evaluator.create_reddit_coverage_env()
    assert run_env["LAST30DAYS_SKIP_KEYCHAIN"] == "1"


def test_coverage_env_has_no_sc_key_no_session_and_no_config(monkeypatch):
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "dummy-sc-key")
    monkeypatch.setenv("REDDIT_SESSION_COOKIE", "dummy-cookie")
    monkeypatch.setenv("XAI_API_KEY", "dummy-xai-key")
    with mock.patch.object(evaluator.envlib, "get_config", return_value={
        "SCRAPECREATORS_API_KEY": "dummy-config-sc-key", "REDDIT_BROWSER_CONSENT": "true",
    }):
        run_env = evaluator.create_reddit_coverage_env()
        # The default eval env still forwards the key; only coverage drops it.
        assert evaluator.create_eval_env().get("SCRAPECREATORS_API_KEY")
    assert "SCRAPECREATORS_API_KEY" not in run_env
    assert "REDDIT_SESSION_COOKIE" not in run_env
    assert run_env["REDDIT_BROWSER_CONSENT"] == "false"
    assert run_env["FROM_BROWSER"] == "off"
    assert run_env["LAST30DAYS_SKIP_KEYCHAIN"] == "1"
    assert run_env["LAST30DAYS_CONFIG_DIR"] == ""
    assert run_env["XAI_API_KEY"] == "dummy-xai-key"


def test_coverage_env_closes_the_reddit_session_gate(monkeypatch):
    from lib import env

    run_env = evaluator.create_reddit_coverage_env()
    config = {key: run_env[key] for key in ("REDDIT_BROWSER_CONSENT", "FROM_BROWSER")}
    config["_BROWSER_COOKIE_MODE"] = "read"
    allowed, why = env.reddit_session_gate(config)
    assert allowed is False and "FROM_BROWSER=off" in why


def test_same_off_topic_thread_is_off_topic_for_baseline_and_candidate():
    off_topic = _thread("Best pizza in Chicago?", comments=4)
    on_topic = _thread("Claude Code hooks are underrated", comments=2)
    candidate = evaluator.summarize_reddit_coverage(_report([on_topic, off_topic]), "Claude Code", _in_process_judge)
    baseline = evaluator.summarize_reddit_coverage(_v2_report([on_topic, off_topic]), "Claude Code", _in_process_judge)
    assert candidate["threads"] == baseline["threads"] == 2
    assert candidate["on_topic"] == baseline["on_topic"] == 1
    # Comments are counted on on-topic threads only.
    assert candidate["comments"] == 2


def test_title_judge_runs_the_candidate_trees_rerank_check():
    judge = evaluator.make_title_judge(ROOT)
    titles = ["Claude Code hooks are underrated", "Best pizza in Chicago?"]
    assert judge("Claude Code", titles) == _in_process_judge("Claude Code", titles) == [True, False]
    assert judge("Claude Code", []) == []


def test_summary_reports_served_path_and_pending_share():
    threads = [
        _thread("Steam Deck OLED battery", comments=3, source="arctic-pending"),
        _thread("Steam Deck dock review", comments=1, source="arctic"),
        _thread("Unrelated", source="arctic-pending"),
    ]
    summary = evaluator.summarize_reddit_coverage(_report(threads, "web+arctic served 3"), "Steam Deck", _in_process_judge)
    assert summary == {
        "threads": 3, "on_topic": 2, "pending_share": 0.5, "comments": 4,
        "served_path": "web+arctic served 3",
    }
    assert evaluator.reddit_served_path({}) == "-"
    assert evaluator.reddit_served_path({"source_status": {"reddit": {"state": "no-results"}}}) == "no-results"


def _run_main(tmp_path, topics, reports, *, baseline="WORKTREE"):
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(json.dumps(topics))
    argv = [
        "evaluate_search_quality.py", "--reddit-coverage", f"--baseline={baseline}", "--candidate=WORKTREE",
        "--topics-file", str(topics_file), "--output-dir", str(tmp_path / "out"),
    ]
    calls = []

    def fake_run(repo_dir, topic, **kwargs):
        calls.append({"topic": topic, **kwargs})
        return reports[topic]

    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch.object(evaluator, "run_last30days", side_effect=fake_run),
        mock.patch.object(evaluator, "make_title_judge", return_value=_in_process_judge),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        code = evaluator.main()
    return code, calls, tmp_path / "out"


def test_mode_parses_topics_and_builds_a_per_topic_table(tmp_path):
    topics = [
        {"topic": "Claude Code", "kind": "mainstream"},
        {"topic": "Quillmark Vesperloom firmware", "kind": "no_coverage", "expect_coverage": False},
    ]
    reports = {
        "Claude Code": _report([_thread("Claude Code tips", comments=5), _thread("Pizza")]),
        "Quillmark Vesperloom firmware": _report([], "no path served; brave empty"),
    }
    code, calls, out = _run_main(tmp_path, topics, reports)
    assert code == 0
    # Floor only: Reddit search, no consent or envelope flags, an env with no SC key.
    assert {call["search"] for call in calls} == {"reddit"}
    assert all("SCRAPECREATORS_API_KEY" not in call["env"] for call in calls)
    assert len(calls) == 4  # baseline and candidate for each topic
    payload = json.loads((out / "reddit-coverage.json").read_text())
    rows = {row["topic"]: row for row in payload["topics"]}
    assert rows["Claude Code"]["candidate"]["on_topic"] == 1
    assert rows["Claude Code"]["candidate"]["comments"] == 5
    assert rows["Claude Code"]["baseline"]["on_topic"] == 1
    table = (out / "reddit-coverage.md").read_text()
    assert "| Claude Code | mainstream | 1/2 | 0% | 5 | web+arctic served 3 | 1/2 | 0% | 5 | web+arctic served 3 |  |" in table


def test_no_coverage_topic_reports_zero_without_failing(tmp_path):
    topics = [{"topic": "Quillmark Vesperloom firmware", "kind": "no_coverage", "expect_coverage": False}]
    reports = {"Quillmark Vesperloom firmware": _report([], "no path served; brave empty")}
    code, _calls, out = _run_main(tmp_path, topics, reports, baseline="none")
    assert code == 0
    payload = json.loads((out / "reddit-coverage.json").read_text())
    assert payload["failures"] == []
    assert payload["baseline"] is None
    row = payload["topics"][0]
    assert row["candidate"]["on_topic"] == 0
    assert row["baseline"] is None
    assert row["flag"] == "no coverage (expected)"
    assert "| - | - | - | - |" in (out / "reddit-coverage.md").read_text()


def test_engine_failure_is_recorded_and_exits_nonzero(tmp_path):
    topics = [{"topic": "Claude Code", "kind": "mainstream"}]
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(json.dumps(topics))
    argv = ["evaluate_search_quality.py", "--reddit-coverage", "--baseline=none", "--candidate=WORKTREE",
            "--topics-file", str(topics_file), "--output-dir", str(tmp_path / "out")]
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch.object(evaluator, "run_last30days", side_effect=RuntimeError("engine exit 1")),
        mock.patch.object(evaluator, "make_title_judge", return_value=_in_process_judge),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        assert evaluator.main() == 1
    payload = json.loads((tmp_path / "out" / "reddit-coverage.json").read_text())
    assert payload["failures"] == [{"topic": "Claude Code", "error": "engine exit 1"}]


def test_mode_defaults_to_the_coverage_fixture(tmp_path):
    rows = evaluator.load_reddit_coverage_topics(TOPICS_FIXTURE)
    argv = ["evaluate_search_quality.py", "--reddit-coverage", "--baseline=none", "--candidate=WORKTREE",
            "--limit-topics", "2", "--output-dir", str(tmp_path / "out")]
    seen = []
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch.object(evaluator, "run_last30days", side_effect=lambda _d, topic, **_k: seen.append(topic) or _report([])),
        mock.patch.object(evaluator, "make_title_judge", return_value=_in_process_judge),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        assert evaluator.main() == 0
    assert seen == [row["topic"] for row in rows[:2]]
