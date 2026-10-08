"""Execute the HTML reference commands with the runtime's skill directory."""

import os
from pathlib import Path
import re
import subprocess
import sys

from tests.skill_contract import reference_text


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "last30days"
REFERENCE = SKILL / "references" / "save-html-brief.md"


def _command_environment(tmp_path):
    installed_skill = tmp_path / "installed skills" / "last30days"
    installed_skill.parent.mkdir()
    installed_skill.symlink_to(SKILL, target_is_directory=True)
    synthesis = tmp_path / "research synthesis.md"
    synthesis.write_text("What I learned:\n\n**Path regression** works offline.\n")
    output = tmp_path / "saved brief.html"
    return {
        "PATH": os.defpath,
        "HOME": str(tmp_path / "home"),
        "LAST30DAYS_CONFIG_DIR": str(tmp_path / "config"),
        "LAST30DAYS_CACHE_DIR": str(tmp_path / "cache"),
        "LAST30DAYS_PYTHON": sys.executable,
        "LAST30DAYS_SKIP_PREFLIGHT": "1",
        "SKILL_DIR": str(installed_skill),
        "TOPIC": "test topic",
        "SYNTHESIS_FILE": str(synthesis),
        "HTML_PATH": str(output),
        "HTML_TMP": str(output),
    }, output


def _engine_command(block):
    start = block.index('"${LAST30DAYS_PYTHON}"')
    lines = []
    for line in block[start:].splitlines():
        lines.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return "\n".join(lines)


def test_documented_local_export_uses_runtime_skill_directory(tmp_path):
    blocks = re.findall(r"```bash\n(.*?)```", reference_text("save-html-brief"), re.DOTALL)
    env, output = _command_environment(tmp_path)
    command = _engine_command(blocks[0])
    result = subprocess.run(
        [
            "bash", "-ec",
            "SCOPE_FLAGS=(--mock --quick --search=reddit --no-browser-cookies)\n"
            + command,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    html = output.read_text()
    assert html.lower().startswith("<!doctype html>")
    assert "</html>" in html
    assert "test topic" in html
    assert "<strong>Path regression</strong> works offline." in html


def test_documented_publish_command_reaches_engine_parser(tmp_path):
    blocks = re.findall(r"```bash\n(.*?)```", reference_text("save-html-brief"), re.DOTALL)
    env, _ = _command_environment(tmp_path)
    result = subprocess.run(
        ["bash", "-ec", "SCOPE_FLAGS=(--help)\n" + blocks[1]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
