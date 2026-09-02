"""Parity tests for hooks/scripts/check-config.sh inline-comment stripping.

Issue #948: lib/env.py strips inline comments quote-aware and BEFORE quote
removal, so `SETUP_COMPLETE="true" # note` parses to `true`. The bash hook
must agree, or SessionStart shows onboarding while the runtime considers
setup complete. The test drives the hook's own parser functions (extracted
from the file so the test tracks the real implementation) through the same
pipeline order load_env_vars uses, and asserts end-to-end behavior of the
hook for the observable regression.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"

# (env-file value, expected parsed value after the full load_env_vars
# pipeline: strip_inline_comment -> trim_ws -> strip_outer_quotes).
CASES: list[tuple[str, str]] = [
    # The #948 regression: quoted boolean with a trailing comment.
    ('"true" # setup finished', "true"),
    # Plain comment strip.
    ("abc # trailing note", "abc"),
    # Hash inside a quoted region is literal.
    ('"a # b"', "a # b"),
    # Hash with no preceding whitespace is literal (URL fragments).
    ("https://x/y#frag", "https://x/y#frag"),
    # Value that is only a comment collapses to empty.
    (" # comment", ""),
    # Mid-value quote is literal, trailing comment still strips.
    ("O'Reilly # c", "O'Reilly"),
    # Comment after a closed quoted region.
    ('"x" # c', "x"),
    # Backslash-escaped quote inside a region does not close it; outer
    # quotes still get removed by strip_outer_quotes.
    ('"a \\" # b"', 'a \\" # b'),
    # Hash mid-value without whitespace survives.
    ("abc#def", "abc#def"),
    # Quoted region containing a hash, then a trailing comment after it.
    ('"x # y" # trailing', "x # y"),
    # Fully-quoted value with no comment keeps working.
    ('"hello"', "hello"),
]


def _bash() -> str:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    return bash


def _driver_script() -> str:
    """Extract the parser functions from the hook and drive the pipeline."""
    lines = HOOK.read_text(encoding="utf-8").splitlines()
    funcs: list[str] = []
    for name in ("trim_ws", "strip_outer_quotes", "strip_inline_comment"):
        start = next(
            i for i, line in enumerate(lines) if line.startswith(f"{name}() {{")
        )
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
        funcs.append("\n".join(lines[start : end + 1]))
    return "\n".join(funcs) + (
        '\nvalue="$(cat "$1")"\n'
        'value="$(strip_inline_comment "$value")"\n'
        'value="$(trim_ws "$value")"\n'
        'value="$(strip_outer_quotes "$value")"\n'
        'printf \'%s\' "$value"\n'
    )


def test_strip_inline_comment_pipeline_parity(tmp_path: Path) -> None:
    bash = _bash()
    script = _driver_script()
    value_file = tmp_path / "value.txt"
    for value, expected in CASES:
        # Write via file: Windows argv mangling eats quotes/backslashes.
        value_file.write_text(value, encoding="utf-8")
        result = subprocess.run(
            [bash, "-c", script, "driver", str(value_file).replace("\\", "/")],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, f"driver failed: {result.stderr}"
        assert result.stdout == expected, (
            f"bash parsed {value!r} as {result.stdout!r}, parity expects {expected!r}"
        )


def _run_hook(tmp_path: Path, project_env_lines: list[str]) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    project = tmp_path / "repo"
    env_file = project / ".claude" / "last30days.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("\n".join(project_env_lines) + "\n", encoding="utf-8")
    (tmp_path / "empty-config").mkdir()
    (tmp_path / "memory").mkdir()
    env = os.environ.copy()
    env.update(
        {
            "LAST30DAYS_CONFIG_DIR": str(tmp_path / "empty-config"),
            "LAST30DAYS_MEMORY_DIR": str(tmp_path / "memory"),
            "LAST30DAYS_TRUST_PROJECT_CONFIG": "1",
        }
    )
    return subprocess.run(
        [bash, str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project),
        timeout=30,
        check=False,
    )


def test_check_config_quoted_boolean_comment_means_setup_complete(tmp_path: Path) -> None:
    """#948 end-to-end: quoted true with a comment must read as setup complete."""
    result = _run_hook(tmp_path, ['SETUP_COMPLETE="true" # setup finished'])
    assert result.returncode == 0, f"hook failed: {result.stderr!r}"
    assert "Ready — " in result.stdout, (
        f"hook showed onboarding, parity bug persists. stdout={result.stdout!r}"
    )
    assert "setup takes 30 seconds" not in result.stdout
