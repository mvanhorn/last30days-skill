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
assert '--cached-report' in sys.argv
assert '--plan' not in sys.argv and '--competitors-plan' not in sys.argv
synthesis = Path(sys.argv[sys.argv.index('--synthesis-file') + 1])
assert synthesis.parent == Path(os.environ['TMPDIR'])
assert synthesis.stat().st_mode & 0o777 == 0o600
assert synthesis.read_text().startswith('Fixture synthesis')
if '--publish-html' in sys.argv:
    assert Path(sys.argv[sys.argv.index('--output') + 1]) == Path(os.environ['HTML_PATH'])
    Path(os.environ['PUBLISH_CAPTURE']).write_bytes(synthesis.read_bytes())
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
    scratch = tmp_path / "private drafts"
    scratch.mkdir()
    env = {
        **os.environ,
        "LAST30DAYS_PYTHON": sys.executable,
        "SKILL_DIR": str(skill_root),
        "LAST30DAYS_MEMORY_DIR": str(output_dir),
        "TMPDIR": str(scratch),
        "TOPIC": "Shell Safety",
        "RENDER_CONTENT": "<html>first report</html>",
    }
    env.pop("SKILL_ROOT", None)
    text = reference_text("save-html-brief")
    block = text.split("```bash\n", 1)[1].split("```", 1)[0]
    command = ["bash", "-c", "set -euo pipefail\nset -o noclobber\numask 022\n"
               + block.replace("\nWhat I learned:\n", "\nFixture synthesis\n", 1)
               + '\nif [ "${HOLD_DRAFT:-0}" = 0 ]; then rm -f -- "$SYNTHESIS_FILE"; fi\n'
               + 'printf "%s\\n%s\\n" "$HTML_PATH" "$SYNTHESIS_FILE"\n']
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
        path = Path(result.stdout.splitlines()[-2])
        assert path not in saved, f"reused existing artifact: {path}"
        saved[path] = content
        assert {path: path.read_text() for path in saved} == saved
    assert output_dir / "shell-safety-brief.html" in saved
    assert set(output_dir.iterdir()) == set(saved)
    assert not list(Path(env["TMPDIR"]).iterdir())


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
    assert not list(Path(env["TMPDIR"]).iterdir())


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
            saved.append(Path(stdout.splitlines()[-2]))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert len(set(saved)) == 4
    assert set(output_dir.iterdir()) == set(saved)
    assert {path.read_text() for path in saved} == contents
    assert not list(Path(env["TMPDIR"]).iterdir())


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
    artifact = Path(result.stdout.splitlines()[-2])
    assert artifact != occupied
    assert artifact.read_text() == env["RENDER_CONTENT"]


@pytest.mark.parametrize("exit_code", [0, 42])
def test_hosted_handoff_uses_exact_private_draft_and_cleans_up(save_command, tmp_path, exit_code):
    command, env, _ = save_command
    saved = subprocess.run(
        command, env={**env, "HOLD_DRAFT": "1"}, check=True,
        text=True, capture_output=True, timeout=20,
    )
    html_path, draft_path = map(Path, saved.stdout.splitlines()[-2:])
    assert draft_path.stat().st_mode & 0o777 == 0o600
    synthesis = draft_path.read_bytes()
    captures = tmp_path / "published synthesis"
    reference = reference_text("save-html-brief")
    publish_block = reference.split("```bash\n")[2].split("```", 1)[0]

    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + publish_block],
        env={**env, "HTML_PATH": str(html_path), "SYNTHESIS_FILE": str(draft_path),
             "PUBLISH_CAPTURE": str(captures), "RENDER_EXIT": str(exit_code)},
        text=True, capture_output=True, timeout=20,
    )

    assert result.returncode == exit_code, result.stderr
    assert captures.read_bytes() == synthesis
    assert html_path.read_text() == env["RENDER_CONTENT"]
    assert not draft_path.exists()
