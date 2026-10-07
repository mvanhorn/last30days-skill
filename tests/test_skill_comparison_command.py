"""Execute the documented comparison shell recipe up to its engine boundary."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import last30days as cli
import pytest
from lib import planner
from tests.skill_contract import reference_text


def _command(text: str, heading: str) -> str:
    section = text.split(f"### {heading}\n", 1)[1]
    block = re.search(r"```bash\n(.*?)\n```", section, re.S)
    assert block is not None
    return block[1]


def test_host_comparison_plan_covers_only_the_main_entity():
    research = reference_text("research-runbook")
    planning = research.split("## Step 0.75: Generate Query Plan", 1)[1].split(
        "## Research Execution", 1,
    )[0]
    rule = next(line for line in planning.splitlines() if line.startswith('- For comparison ('))
    assert "TOPIC_A only" in rule
    assert "`comparison.md`" in rule
    assert "peer sub-runs plan independently" in rule
    assert "per-entity subqueries" not in planning
    assert "head-to-head subquery" not in planning

    comparison = reference_text("comparison")
    assert "for **TOPIC_A only**, not the whole vs-string" in comparison
    assert "**When host web search is available, do WebSearch supplements**" in comparison
    assert "`{TOPIC_A} vs {TOPIC_B} comparison {YEAR}`" in comparison


def test_comparison_command_preserves_both_plans_and_cleans_them_up(tmp_path):
    text = reference_text("comparison")
    marker = tmp_path / "must-not-execute"
    untrusted = f"people's choice $(touch {shlex.quote(str(marker))})"
    main_topic = f"Main Widget $(touch {shlex.quote(str(marker))})"
    quoted_context = 'The product says "zero setup" and uses C:\\docs'
    main_plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [{
            "search_query": "Main Widget " + untrusted,
            "ranking_query": "Main Widget people's experience",
            "sources": ["reddit"],
        }],
    }
    peer_plan = {
        "Peer Widget": {
            "x_handle": "peer_handle", "subreddits": ["peer_sub", "peer_extra"],
            "github_user": "peer_github", "context": quoted_context + " " + untrusted,
        },
        "Peer Three": {
            "x_handle": "third_handle", "subreddits": ["third_sub"],
            "github_user": "third_github", "context": "McDonald's `literal backticks`",
        },
    }
    replacements = {
        "QUERY_PLAN_JSON": json.dumps(main_plan),
        "COMPETITORS_PLAN_JSON": json.dumps(peer_plan),
        "TOPIC_A": main_topic, "TOPIC_B": "Peer Widget", "TOPIC_C": "Peer Three",
        "TOPIC_A_HANDLE": f"main_handle$(touch {shlex.quote(str(marker))})",
        "TOPIC_A_SUBS": "main_sub",
    }
    command = _command(text, "With host web search")
    for name, value in replacements.items():
        command = command.replace("{" + name + "}", value)
    assert not re.search(r"\{(?:TOPIC|QUERY_PLAN)_[A-Z_]+\}", command)

    installed = tmp_path / "installed skill"
    (installed / "scripts").mkdir(parents=True)
    (installed / "scripts" / "last30days.py").write_text("")
    capture = tmp_path / "engine calls.jsonl"
    interpreter = tmp_path / "engine capture"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['-m', 'json.tool']:\n"
        "    json.loads(pathlib.Path(args[2]).read_text())\n"
        "    raise SystemExit(0)\n"
        "paths = [pathlib.Path(args[args.index(flag) + 1]) for flag in ('--plan', '--competitors-plan')]\n"
        "record = {'args': args, 'plans': [json.loads(path.read_text()) for path in paths], 'paths': [str(path) for path in paths]}\n"
        f"with open({str(capture)!r}, 'a') as out: out.write(json.dumps(record) + '\\n')\n"
    )
    interpreter.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    mktemp = shutil.which("mktemp")
    assert mktemp
    (binaries / "mktemp").write_text(
        "#!/bin/sh\n"
        'case "$1" in *XXXXXX) ;; *) echo "BSD mktemp requires trailing XXXXXX" >&2; exit 1;; esac\n'
        f"exec {shlex.quote(mktemp)} \"$@\"\n"
    )
    (binaries / "mktemp").chmod(0o755)
    temporary = tmp_path / "temporary plans"
    temporary.mkdir()
    result = subprocess.run(
        ["bash", "-eC", "-c", command], cwd=tmp_path,
        env={
            "PATH": str(binaries) + os.pathsep + os.defpath,
            "HOME": str(tmp_path / "home"),
            "SKILL_DIR": str(installed),
            "LAST30DAYS_PYTHON": str(interpreter),
            "LAST30DAYS_MEMORY_DIR": str(tmp_path / "saved research"),
            "TMPDIR": str(temporary),
        },
        text=True, capture_output=True, timeout=15,
    )
    assert not marker.exists(), "plan content executed as shell code"
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert call["args"][0] == str(installed / "scripts" / "last30days.py")
    assert call["args"][1] == main_topic + " vs Peer Widget vs Peer Three"
    assert f"--x-handle=main_handle$(touch {shlex.quote(str(marker))})" in call["args"]
    assert "--subreddits=main_sub" in call["args"]
    parsed = cli.build_parser().parse_args(call["args"][1:])
    assert parsed.emit == "compact"
    assert parsed.plan == call["paths"][0]
    assert parsed.competitors_plan == call["paths"][1]
    assert call["plans"][0] == main_plan
    planner.validate_external_plan(call["plans"][0])
    assert call["plans"][1] == peer_plan
    assert all(not Path(path).exists() for path in call["paths"])
    assert list(temporary.iterdir()) == []
    parsed_peers = cli.parse_competitors_plan(json.dumps(call["plans"][1]))
    assert set(parsed_peers) == {"peer widget", "peer three"}
    assert parsed_peers["peer widget"]["context"] == quoted_context + " " + untrusted


@pytest.mark.parametrize("peers", [("Peer One",), ("Peer One", "Peer Two")])
def test_no_web_search_comparison_command_uses_engine_planning(tmp_path, peers):
    text = reference_text("comparison")
    command = _command(text, "Without host web search")
    marker = tmp_path / "must-not-execute"
    topic = f"Main $(touch {shlex.quote(str(marker))}) vs " + " vs ".join(peers)
    command = command.replace("{TOPIC_A} vs {TOPIC_B} vs {TOPIC_C}", topic)

    installed = tmp_path / "installed skill"
    (installed / "scripts").mkdir(parents=True)
    (installed / "scripts" / "last30days.py").write_text("")
    capture = tmp_path / "engine args.json"
    interpreter = tmp_path / "engine capture"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
    )
    interpreter.chmod(0o755)
    result = subprocess.run(
        ["bash", "-ec", command], cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "SKILL_DIR": str(installed),
            "LAST30DAYS_PYTHON": str(interpreter),
            "LAST30DAYS_MEMORY_DIR": str(tmp_path / "saved research"),
        },
        text=True, capture_output=True, timeout=15,
    )
    assert not marker.exists(), "comparison topic executed as shell code"
    assert result.returncode == 0, result.stderr
    args = json.loads(capture.read_text())
    assert args[1] == topic
    parsed = cli.build_parser().parse_args(args[1:])
    assert parsed.auto_resolve is True
    assert parsed.plan is None
    assert parsed.competitors_plan is None
    assert parsed.emit == "compact"
