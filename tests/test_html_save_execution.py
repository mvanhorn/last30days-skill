"""Execute the documented save flow with a local renderer boundary."""

import os
from pathlib import Path
import subprocess
from tests.skill_contract import reference_text
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "skills/last30days/references/save-html-brief.md"


@pytest.fixture
def save_command(tmp_path):
    skill_root = tmp_path / "skill with spaces"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "last30days.py").write_text(
        """import os
from pathlib import Path
import sys
import time

assert '--emit=html' in sys.argv
assert '--hiring-signals' in sys.argv
if ready_dir := os.environ.get('RENDER_READY_DIR'):
    ready = Path(ready_dir)
    (ready / str(os.getpid())).touch()
    deadline = time.monotonic() + 10
    while len(list(ready.iterdir())) < 4:
        if time.monotonic() > deadline:
            raise RuntimeError('concurrent renderers did not arrive')
        time.sleep(0.01)
sys.stdout.write(os.environ['RENDER_CONTENT'])
sys.exit(int(os.environ.get('RENDER_EXIT', '0')))
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "saved briefs"
    output_dir.mkdir()
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Fixture synthesis", encoding="utf-8")
    env = {
        **os.environ,
        "LAST30DAYS_PYTHON": sys.executable,
        "SKILL_DIR": str(skill_root),
        "LAST30DAYS_MEMORY_DIR": str(output_dir),
        "SYNTHESIS_FILE": str(synthesis),
        "TOPIC": "Shell Safety",
        "RENDER_CONTENT": "<html>first report</html>",
    }
    env.pop("SKILL_ROOT", None)
    text = reference_text("save-html-brief")
    block = text.split("```bash\n", 1)[1].split("```", 1)[0]
    flow = block[block.index("SLUG="):]
    command = ["bash", "-c", "set -euo pipefail\nset -o noclobber\n"
               "SCOPE_FLAGS=(--hiring-signals)\n" + flow
               + '\nprintf "%s\\n" "$HTML_PATH"\n']
    return command, env, output_dir


def test_repeated_html_exports_preserve_all_previous_bytes(save_command):
    command, env, output_dir = save_command
    saved = {}
    for index in range(4):
        content = f"<html>unique report {index}</html>"
        result = subprocess.run(
            command, env={**env, "RENDER_CONTENT": content},
            text=True, capture_output=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        path = Path(result.stdout.strip())
        assert path not in saved, f"reused existing artifact: {path}"
        saved[path] = content
        assert {path: path.read_text() for path in saved} == saved
    assert output_dir / "shell-safety-brief.html" in saved
    assert set(output_dir.iterdir()) == set(saved)


def test_failed_html_render_preserves_existing_artifacts(save_command):
    command, env, output_dir = save_command
    existing = output_dir / "shell-safety-brief.html"
    existing.write_bytes(b"existing report must survive\n")
    result = subprocess.run(
        command, env={**env, "RENDER_CONTENT": "partial output", "RENDER_EXIT": "42"},
        text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 42, result.stderr
    assert existing.read_bytes() == b"existing report must survive\n"
    assert list(output_dir.iterdir()) == [existing]
    assert not result.stdout


def test_concurrent_html_exports_publish_distinct_complete_files(save_command, tmp_path):
    command, env, output_dir = save_command
    ready = tmp_path / "renderers ready"
    ready.mkdir()
    contents = {f"<html>concurrent report {index}</html>" for index in range(4)}
    processes = [
        subprocess.Popen(
            command,
            env={**env, "RENDER_CONTENT": content, "RENDER_READY_DIR": str(ready)},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for content in contents
    ]
    saved = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            saved.append(Path(stdout.strip()))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert len(set(saved)) == 4
    assert set(output_dir.iterdir()) == set(saved)
    assert {path.read_text() for path in saved} == contents


@pytest.mark.parametrize("collision", ["directory", "broken_symlink"])
def test_html_export_does_not_replace_nonregular_paths(save_command, collision):
    command, env, output_dir = save_command
    occupied = output_dir / "shell-safety-brief.html"
    if collision == "directory":
        occupied.mkdir()
    else:
        occupied.symlink_to(output_dir / "absent-target")
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert occupied.is_dir() if collision == "directory" else occupied.is_symlink()
    assert not (output_dir / "absent-target").exists()
    artifact = Path(result.stdout.strip())
    assert artifact != occupied
    assert artifact.read_text() == env["RENDER_CONTENT"]
