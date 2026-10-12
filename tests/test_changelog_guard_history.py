"""Exercise the workflow shell against real, disposable Git histories."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def workflow_bash():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Changelog workflow requires Bash with the mapfile builtin")
    probe = subprocess.run(
        [bash, "-c", "type -t mapfile"], text=True, capture_output=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "builtin":
        pytest.skip(f"Changelog workflow requires mapfile; unavailable in {bash}")
    return bash


@pytest.fixture
def history(tmp_path, workflow_bash):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Test",
        GIT_COMMITTER_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )

    def git(*args, input=None):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, env=env, input=input,
            text=True, capture_output=True, check=True,
        ).stdout.strip()

    git("init", "--quiet")

    def commit(changes, *parents):
        git("read-tree", parents[0] if parents else "--empty")
        for path, content in changes.items():
            blob = git("hash-object", "-w", "--stdin", input=content)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        tree = git("write-tree")
        return git(
            "commit-tree", tree,
            *[arg for parent in parents for arg in ("-p", parent)],
            input="Fixture commit\n",
        )

    old = commit({
        "pyproject.toml": 'version = "1.0.0"\n',
        "CHANGELOG.md": "# Release 1.0.0\n",
        "skills/last30days/scripts/engine.py": "old\n",
    })
    release_changes = {
        "pyproject.toml": 'version = "2.0.0"\n',
        "CHANGELOG.md": "# Release 2.0.0\n",
        "changelog.d/base-only.fixed.md": "An unrelated base change.\n",
    }
    release = commit(release_changes, old)
    parser = tmp_path / ".github/scripts/read_manifest_version.py"
    parser.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / ".github/scripts/read_manifest_version.py", parser)
    workflow = yaml.safe_load((ROOT / ".github/workflows/changelog-guard.yml").read_text())
    step = next(step for step in workflow["jobs"]["guard"]["steps"] if "run" in step)
    script = step["run"].replace("${{ github.repository }}", "example/repository")

    def run(head, *, base_ref="main", labels="", author="contributor", event_base=None):
        git("update-ref", f"refs/remotes/origin/{base_ref}", release)
        return subprocess.run(
            [workflow_bash, "-c", 'gh() { printf "%s\\n" "$TEST_LABELS"; }\n' + script],
            cwd=tmp_path, text=True, capture_output=True,
            env={
                **env,
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}",
                "TEST_LABELS": labels,
                "PR_NUMBER": "1",
                "PR_AUTHOR": author,
                "BASE_REF": base_ref,
                "BASE_SHA": event_base or release,
                "HEAD_SHA": head,
            },
        )

    return old, release, release_changes, commit, run


@pytest.mark.parametrize("shape", ["behind", "merged", "rebased"])
@pytest.mark.parametrize("base_ref", ["main", "releases/stable"])
def test_base_release_is_not_attributed_to_feature(history, shape, base_ref):
    old, release, release_changes, commit, run = history
    changes = {
        "skills/last30days/scripts/engine.py": "feature\n",
        "changelog.d/feature.fixed.md": "Feature fix.\n",
    }
    head = commit(changes, release if shape == "rebased" else old)
    if shape == "merged":
        head = commit(release_changes, head, release)
    result = run(head, base_ref=base_ref, event_base=release if shape == "behind" else old)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Changelog guard passed." in result.stdout


@pytest.mark.parametrize("labels", ["", "skip-changelog"])
def test_contributor_version_bump_is_rejected(history, labels):
    _, release, _, commit, run = history
    head = commit({"pyproject.toml": 'version = "3.0.0"\n'}, release)
    result = run(head, labels=labels)
    assert result.returncode != 0
    assert "Non-release PRs must not bump lockstep version strings." in result.stdout


def test_base_fragment_does_not_satisfy_feature_requirement(history):
    old, release, release_changes, commit, run = history
    feature = commit({"skills/last30days/scripts/engine.py": "feature\n"}, old)
    head = commit(release_changes, feature, release)
    result = run(head, event_base=old)
    assert result.returncode != 0
    assert "Engine/skill changes need a changelog.d fragment" in result.stdout


def test_contributor_changelog_edit_is_rejected(history):
    _, release, _, commit, run = history
    head = commit({"CHANGELOG.md": "# Contributor release notes\n"}, release)
    result = run(head)
    assert result.returncode != 0
    assert "Do not edit CHANGELOG.md in feature PRs." in result.stdout


@pytest.mark.parametrize(
    ("labels", "author"), [("skip-changelog", "contributor"), ("", "dependabot[bot]")]
)
def test_fragment_exemptions_still_allow_engine_changes(history, labels, author):
    _, release, _, commit, run = history
    head = commit({"skills/last30days/scripts/engine.py": "feature\n"}, release)
    result = run(head, labels=labels, author=author)
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_label_still_allows_version_and_changelog_changes(history):
    _, release, _, commit, run = history
    head = commit({
        "pyproject.toml": 'version = "3.0.0"\n',
        "CHANGELOG.md": "# Release 3.0.0\n",
    }, release)
    result = run(head, labels="release")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "version/CHANGELOG edits allowed." in result.stdout
