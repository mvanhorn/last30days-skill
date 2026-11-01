"""Execute the raw reference recipe without root-only host substitutions."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import pytest

import last30days as cli
from tests.skill_contract import reference_text


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("ambient_arguments", [None, "WRONG TOPIC --emit=json --save-dir=/wrong"])
@pytest.mark.parametrize("saving_enabled", [False, True])
@pytest.mark.parametrize("with_plan", [False, True])
def test_raw_research_recipe_preserves_the_topic_without_host_argument_expansion(
    tmp_path, ambient_arguments, saving_enabled, with_plan, shell,
):
    if not shutil.which(shell, path=os.defpath):
        pytest.skip(f"{shell} is not installed")
    reference = reference_text("research-runbook")
    commands = re.findall(r"```bash\n(.*?)\n```", reference, re.S)
    marker = tmp_path / "topic-must-not-execute"
    shell_marker = shlex.quote(str(marker))
    topic = f'McDonald\'s new tools $(touch {shell_marker}) `touch {shell_marker}` "$HOME"'
    command = next(
        block for block in commands if "{TOPIC}" in block and "--save-suffix=v3" in block
    ).replace("{TOPIC}", topic)
    plan = {
        "subqueries": [{
            "search_query": topic,
            "ranking_query": topic,
            "sources": ["reddit"],
        }],
    }
    if with_plan:
        plan_command = next(
            block for block in commands if "{QUERY_PLAN_JSON_FROM_STEP_0.75}" in block
        ).replace("{QUERY_PLAN_JSON_FROM_STEP_0.75}", json.dumps(plan))
        command = command.rsplit("\n", 1)[0] + "\n" + plan_command

    installed = tmp_path / "installed skill"
    (installed / "scripts").mkdir(parents=True)
    (installed / "scripts" / "last30days.py").write_text("")
    capture = tmp_path / "engine arguments.jsonl"
    interpreter = tmp_path / "argument capture"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "record = {'args': args, 'plan': json.load(sys.stdin) if '--plan' in args else None}\n"
        f"with open({str(capture)!r}, 'a') as output:\n"
        "    output.write(json.dumps(record) + '\\n')\n"
    )
    interpreter.chmod(0o755)
    save_dir = str(tmp_path / "saved research") if saving_enabled else ""
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path / "home"),
        "SKILL_DIR": str(installed),
        "LAST30DAYS_PYTHON": str(interpreter),
        "LAST30DAYS_MEMORY_DIR": save_dir,
    }
    if ambient_arguments is not None:
        env["ARGUMENTS"] = ambient_arguments
    result = subprocess.run(
        [shell, "-euC", "-c", command], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "topic text executed as shell code"
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert call["args"][0] == str(installed / "scripts" / "last30days.py")
    parsed = cli.build_parser().parse_args(call["args"][1:])
    assert parsed.topic == [topic], "the full parsed topic must be one literal argument"
    assert parsed.emit == "compact"
    assert parsed.save_dir == save_dir
    assert parsed.save_suffix == "v3"
    assert parsed.plan == ("-" if with_plan else None)
    assert call["plan"] == (plan if with_plan else None)
