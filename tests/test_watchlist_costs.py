"""Exercise provider accounting through real child processes and SQLite."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import store
import watchlist
from lib import usage


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()
    store.set_setting("daily_budget", "1.00")
    for name in ("First", "Second", "Third"):
        store.add_topic(name)


def install_child(tmp_path, monkeypatch, *, cost=0.6, fail=False, free=False, after=""):
    script_dir = Path(watchlist.__file__).resolve().parent
    child = tmp_path / "last30days.py"
    child.write_text(
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(script_dir)!r})\n"
        "from lib import http, providers\n"
        f"payload = {{'choices': [{{'message': {{'content': 'ok'}}}}], 'usage': {{'cost': {cost!r}}}}}\n"
        "class Response(io.BytesIO):\n"
        "    status = 200\n"
        "def respond(req, timeout):\n"
        "    return Response(json.dumps(payload).encode())\n"
        "http._open_request = respond\n"
        + (
            "http.get('https://www.reddit.com/r/example.json')\n"
            if free else
            "providers.OpenRouterClient('dummy-key').generate_text('example/model', 'prompt')\n"
        )
        + ("raise SystemExit(1)\n" if fail else "")
        + after
        + "print(json.dumps({'topic': sys.argv[1], 'range_from': '2026-01-01', 'range_to': '2026-04-01', "
        "'generated_at': '2026-04-01T00:00:00Z', 'provider_runtime': {'reasoning_provider': 'local', "
        "'planner_model': 'local', 'rerank_model': 'local'}, 'query_plan': {'intent': 'test', "
        "'freshness_mode': 'recent', 'cluster_mode': 'standard', 'raw_topic': sys.argv[1]}}))\n"
    )
    monkeypatch.setattr(watchlist, "SCRIPT_DIR", tmp_path)


def test_paid_topics_continue_below_budget_and_stop_at_limit(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch)
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert output["budget_used"] == pytest.approx(1.2)
    assert [row["status"] for row in output["results"]] == ["completed", "completed", "skipped"]


def test_spend_survives_later_child_failure(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch, cost=1.25, fail=True)
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert output["budget_used"] == pytest.approx(1.25)
    assert [row["status"] for row in output["results"]] == ["failed", "skipped", "skipped"]


def test_free_topics_do_not_consume_budget(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch, free=True)
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert output["budget_used"] == 0
    assert [row["status"] for row in output["results"]] == ["completed"] * 3


def test_missing_paid_cost_is_explicit_and_blocks_next_topic(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch, cost=None)
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in output["results"]] == ["completed", "skipped", "skipped"]
    assert output["budget_unknown_runs"] == 1
    assert "unknown" in output["results"][1]["reason"].lower()


def test_completed_spend_survives_child_timeout(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch, cost=1.25, after="import time; time.sleep(60)\n")
    run = subprocess.run

    def short_timeout(*args, **kwargs):
        kwargs["timeout"] = 1
        return run(*args, **kwargs)

    monkeypatch.setattr(watchlist.subprocess, "run", short_timeout)
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert output["budget_used"] == pytest.approx(1.25)
    assert output["results"][0]["error"] == "timeout"
    assert [row["status"] for row in output["results"]] == ["failed", "skipped", "skipped"]


def test_partial_unknown_spend_retains_known_subtotal(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(
        tmp_path, monkeypatch, cost=0.4,
        after="http.post('https://api.openai.com/v1/responses', {'model': 'example'})\n",
    )
    watchlist.cmd_run_all(None)
    output = json.loads(capsys.readouterr().out)
    assert output["budget_used"] == pytest.approx(0.4)
    assert output["budget_unknown_runs"] == 1
    assert [row["status"] for row in output["results"]] == ["completed", "skipped", "skipped"]


def test_run_one_respects_spend_from_previous_failure(isolated_store, tmp_path, monkeypatch, capsys):
    install_child(tmp_path, monkeypatch, cost=1.25, fail=True)
    watchlist._run_topic(store.get_topic("First"))
    args = type("Args", (), {"topic": "Second"})()
    watchlist.cmd_run_one(args)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "skipped"
    assert store.get_daily_cost() == pytest.approx(1.25)


@pytest.mark.parametrize("failure", ["directory", "journal"])
def test_prelaunch_setup_failure_does_not_block_later_research(
    isolated_store, tmp_path, monkeypatch, capsys, failure,
):
    install_child(tmp_path, monkeypatch)
    topic = store.get_topic("First")
    with monkeypatch.context() as setup_failure:
        if failure == "directory":
            unavailable = tmp_path / "not-a-directory"
            unavailable.write_text("file blocks temporary directory creation")
            setup_failure.setattr(watchlist.tempfile, "tempdir", str(unavailable))
        else:
            original_open = usage.os.open

            def deny_journal(path, *args, **kwargs):
                if Path(path).name == "usage.db":
                    raise PermissionError("journal creation denied")
                return original_open(path, *args, **kwargs)

            setup_failure.setattr(usage.os, "open", deny_journal)
        with pytest.raises(OSError):
            watchlist._run_topic(topic)

    assert store.get_daily_unknown_cost_runs() == 0
    assert store.get_daily_cost() == 0
    watchlist.cmd_run_one(SimpleNamespace(topic="First"))
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert store.get_daily_cost() == pytest.approx(0.6)
