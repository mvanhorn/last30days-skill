"""Contract tests for towncrier + lockstep release preparation."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _assert_run_blocks_indented(text: str, *, label: str) -> None:
    """Fail if a ``run: |`` block contains under-indented (esp. column-0) lines.

    A prior bug treated indent <= run_indent as "end of block" and skipped the
    assertion, so column-0 Python inside the scalar never failed the test.
    """
    in_run = False
    run_indent = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^(\s*)run:\s*\|\s*$", line)
        if match:
            in_run = True
            run_indent = len(match.group(1))
            continue
        if not in_run or not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent > run_indent:
            continue
        # Valid block end: a YAML key or list item at/above the run: indent.
        # Column-0 (or any non-key dedent) is the Actions parse failure.
        looks_like_yaml = bool(
            re.match(r"^\s*(- )?[A-Za-z_][\w-]*\s*:", line)
            or re.match(r"^\s*-\s+\S", line)
        )
        if indent == 0 or not looks_like_yaml:
            raise AssertionError(
                f"{label}:{lineno} under-indented inside run: | "
                f"(indent={indent}, run_indent={run_indent}): {line!r}"
            )
        in_run = False


def _tag_release_workflow_text() -> str:
    return (ROOT / ".github" / "workflows" / "tag-release.yml").read_text(
        encoding="utf-8"
    )


def _tag_release_version_sed_expr(workflow_text: str) -> str:
    """Extract the sed expression from tag-release.yml's VERSION pipeline."""
    match = re.search(
        r'printf \'%s\\n\' "\$\{HEAD_MSG\}" \| sed -n \'([^\']+)\' \| head -n1',
        workflow_text,
    )
    if not match:
        raise AssertionError(
            "Could not find VERSION sed pipeline in tag-release.yml"
        )
    return match.group(1)


def _parse_release_version_from_message(message: str, sed_expr: str) -> str:
    """Run the same sed pipeline tag-release.yml uses to extract VERSION."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"printf '%s\\n' \"${{HEAD_MSG}}\" | sed -n '{sed_expr}' | head -n1",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HEAD_MSG": message},
    )
    return result.stdout.strip()


def _load_prepare_release():
    path = ROOT / ".github" / "scripts" / "prepare_release.py"
    spec = importlib.util.spec_from_file_location("prepare_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestChangelogWorkflow(unittest.TestCase):
    def test_towncrier_config_present(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.towncrier]", text)
        self.assertIn('directory = "changelog.d"', text)
        self.assertIn('filename = "CHANGELOG.md"', text)
        for fragment_type in (
            "security",
            "removed",
            "deprecated",
            "added",
            "changed",
            "fixed",
        ):
            self.assertIn(f'directory = "{fragment_type}"', text)

    def test_changelog_has_towncrier_start_marker(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("<!-- towncrier release notes start -->", text)
        self.assertNotIn("## [Unreleased]", text)

    def test_changelog_d_readme_exists(self) -> None:
        self.assertTrue((ROOT / "changelog.d" / "README.md").is_file())

    def test_prepare_release_script_exists(self) -> None:
        self.assertTrue((ROOT / ".github" / "scripts" / "prepare_release.py").is_file())

    def test_release_workflows_exist(self) -> None:
        workflows = ROOT / ".github" / "workflows"
        for name in (
            "prepare-release.yml",
            "tag-release.yml",
            "changelog-guard.yml",
        ):
            self.assertTrue((workflows / name).is_file(), msg=name)

    def test_tag_release_workflow_yaml_parses(self) -> None:
        """Bare ``chore(release):`` in an unquoted ``if:`` breaks Actions YAML.

        GitHub then reports the run as failed with zero jobs on every push to
        main. The expression must be double-quoted; prefer ``contains`` so
        merge-commit messages still match.
        """
        text = _tag_release_workflow_text()
        yaml.safe_load(text)
        self.assertRegex(
            text,
            r'(?m)^\s+if:\s+"contains\(github\.event\.head_commit\.message, '
            r"'chore\(release\): bump version to '\)\"\s*$",
        )
        # VERSION parse must scan the full message (merge commits put the
        # chore line in the body, not on line 1).
        self.assertNotIn("| head -n1 | sed -n", text)

    def test_tag_release_workflow_version_extraction(self) -> None:
        """VERSION sed must match direct and merge-commit message shapes."""
        text = _tag_release_workflow_text()
        sed_expr = _tag_release_version_sed_expr(text)
        expected = "3.18.3"
        direct = f"chore(release): bump version to {expected}"
        merge = (
            "Merge pull request #879 from mvanhorn/release/v3.18.3\n\n"
            f"chore(release): bump version to {expected}"
        )
        for message in (direct, merge):
            with self.subTest(message=message.splitlines()[0]):
                self.assertEqual(
                    _parse_release_version_from_message(message, sed_expr),
                    expected,
                )

    def test_changelog_guard_run_blocks_stay_indented(self) -> None:
        """Column-0 lines inside ``run: |`` break Actions YAML parsing."""
        path = ROOT / ".github" / "workflows" / "changelog-guard.yml"
        _assert_run_blocks_indented(
            path.read_text(encoding="utf-8"),
            label=str(path),
        )

    def test_run_block_indent_checker_rejects_column_zero(self) -> None:
        malformed = (
            "jobs:\n"
            "  guard:\n"
            "    steps:\n"
            "      - name: Enforce\n"
            "        run: |\n"
            "          set -euo pipefail\n"
            "import json\n"
        )
        with self.assertRaises(AssertionError):
            _assert_run_blocks_indented(malformed, label="synthetic")

    def test_read_manifest_version_helper(self) -> None:
        script = ROOT / ".github" / "scripts" / "read_manifest_version.py"
        self.assertTrue(script.is_file())
        spec = importlib.util.spec_from_file_location("read_manifest_version", script)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(
            mod.version_from("pyproject.toml", 'version = "3.18.1"\n'),
            "3.18.1",
        )
        self.assertEqual(
            mod.version_from("skills/last30days/SKILL.md", 'version: "3.18.1"\n'),
            "3.18.1",
        )
        self.assertEqual(
            mod.version_from(
                "uv.lock",
                '[[package]]\nname = "last30days-skill"\nversion = "3.18.1"\n',
            ),
            "3.18.1",
        )
        self.assertEqual(
            mod.version_from(
                ".claude-plugin/plugin.json",
                json.dumps({"version": "3.18.1"}),
            ),
            "3.18.1",
        )
        self.assertEqual(
            mod.version_from(
                ".claude-plugin/marketplace.json",
                json.dumps({"plugins": [{"version": "3.18.1"}]}),
            ),
            "3.18.1",
        )
        with self.assertRaises(SystemExit):
            mod.version_from(".claude-plugin/plugin.json", "{not-json")

    def test_pr_template_has_agent_and_relationship_sections(self) -> None:
        text = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Agent disclosure", text)
        self.assertIn("### Relationship to this change", text)
        self.assertIn("changelog.d/", text)
        self.assertIn("What does this PR do?", text)
        self.assertTrue((ROOT / "CONTRIBUTING.md").is_file())

    def test_next_version_bumps(self) -> None:
        mod = _load_prepare_release()
        self.assertEqual(mod.next_version("3.18.1", "patch"), "3.18.2")
        self.assertEqual(mod.next_version("3.18.1", "minor"), "3.19.0")
        self.assertEqual(mod.next_version("3.18.1", "major"), "4.0.0")

    def test_main_refuses_equal_version_outside_dry_run(self) -> None:
        mod = _load_prepare_release()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "pyproject.toml").write_text(
                '[project]\nname = "last30days-skill"\nversion = "3.18.1"\n',
                encoding="utf-8",
            )
            mod.ROOT = tmp_path
            mod.PYPROJECT = tmp_path / "pyproject.toml"
            with self.assertRaises(SystemExit) as ctx:
                mod.main(["--version", "3.18.1"])
            self.assertIn("Refusing to re-release", str(ctx.exception))

    def test_bump_all_updates_lockstep_surfaces(self) -> None:
        mod = _load_prepare_release()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Minimal fixtures mirroring the lockstep layout.
            (tmp_path / "skills" / "last30days").mkdir(parents=True)
            (tmp_path / ".claude-plugin").mkdir()
            (tmp_path / ".codex-plugin").mkdir()
            (tmp_path / ".grok-plugin").mkdir()

            (tmp_path / "pyproject.toml").write_text(
                '[project]\nname = "last30days-skill"\nversion = "3.18.1"\n',
                encoding="utf-8",
            )
            (tmp_path / "skills" / "last30days" / "SKILL.md").write_text(
                '---\nversion: "3.18.1"\n---\n\n# last30days v3.18.1: Title\n',
                encoding="utf-8",
            )
            for rel in (
                ".claude-plugin/plugin.json",
                ".codex-plugin/plugin.json",
                ".grok-plugin/plugin.json",
                "gemini-extension.json",
            ):
                (tmp_path / rel).write_text(
                    json.dumps({"name": "last30days", "version": "3.18.1"}, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
            for rel in (
                ".claude-plugin/marketplace.json",
                ".grok-plugin/marketplace.json",
            ):
                (tmp_path / rel).write_text(
                    json.dumps(
                        {
                            "name": "last30days-skill",
                            "plugins": [{"name": "last30days", "version": "3.18.1"}],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (tmp_path / "uv.lock").write_text(
                'version = 1\n\n[[package]]\nname = "last30days-skill"\n'
                'version = "3.18.1"\nsource = { virtual = "." }\n',
                encoding="utf-8",
            )

            # Point module paths at the temp tree.
            mod.ROOT = tmp_path
            mod.SKILL_MD = tmp_path / "skills" / "last30days" / "SKILL.md"
            mod.PYPROJECT = tmp_path / "pyproject.toml"
            mod.UV_LOCK = tmp_path / "uv.lock"
            mod.JSON_VERSION_FILES = (
                tmp_path / ".claude-plugin" / "plugin.json",
                tmp_path / ".codex-plugin" / "plugin.json",
                tmp_path / ".grok-plugin" / "plugin.json",
                tmp_path / "gemini-extension.json",
            )
            mod.MARKETPLACE_FILES = (
                tmp_path / ".claude-plugin" / "marketplace.json",
                tmp_path / ".grok-plugin" / "marketplace.json",
            )

            touched = mod.bump_all("9.9.9")
            self.assertEqual(len(touched), 9)

            pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
            self.assertIn('version = "9.9.9"', pyproject)

            skill = (tmp_path / "skills" / "last30days" / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertIn('version: "9.9.9"', skill)
            self.assertIn("# last30days v9.9.9:", skill)

            for rel in (
                ".claude-plugin/plugin.json",
                ".codex-plugin/plugin.json",
                ".grok-plugin/plugin.json",
                "gemini-extension.json",
            ):
                data = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
                self.assertEqual(data["version"], "9.9.9", msg=rel)

            for rel in (
                ".claude-plugin/marketplace.json",
                ".grok-plugin/marketplace.json",
            ):
                data = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
                self.assertEqual(data["plugins"][0]["version"], "9.9.9", msg=rel)

            uv_lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
            self.assertRegex(
                uv_lock,
                re.compile(
                    r'(?ms)^\[\[package\]\]\nname = "last30days-skill"\nversion = "9\.9\.9"',
                ),
            )


if __name__ == "__main__":
    unittest.main()
