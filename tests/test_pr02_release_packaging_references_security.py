"""Security regression tests for PR02 (HIGH-223, HIGH-224, HIGH-225, HIGH-226).

Covers:
- HIGH-223: Private mktemp directory + noclobber + EXIT cleanup for host
  Reddit/shortform envelopes in skills/last30days/references/research-runbook.md.
- HIGH-224: Quoted heredoc capture and `--` / `=` separation preventing shell
  command substitution and flag injection in followups.md and library-queue.md.
- HIGH-225: Release workflow tag validation, refs/tags/ checkout pinning,
  lockstep manifest verification, and zizmor-safe env handling in release.yml.
- HIGH-226: Secret/cache exclusions and non-runtime script parity across
  .skillignore, .clawhubignore, and skills/last30days/.skillignore.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.skill_contract import reference_text

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "last30days"
REFERENCES_DIR = SKILL_ROOT / "references"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _ignore_entries(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _extract_bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", markdown, re.DOTALL)


def _assert_run_blocks_indented(text: str, *, label: str) -> None:
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
        looks_like_yaml = bool(
            re.match(r"^\s*(- )?[A-Za-z_][\w-]*\s*:", line)
            or re.match(r"^\s*-\s+\S", line)
            or re.match(r"^\s+#", line)
        )
        if indent == 0 or not looks_like_yaml:
            raise AssertionError(
                f"{label}:{lineno} under-indented inside run: | "
                f"(indent={indent}, run_indent={run_indent}): {line!r}"
            )
        in_run = False


# ---------------------------------------------------------------------------
# HIGH-223: Private mktemp dir + noclobber + cleanup for host thread envelopes
# ---------------------------------------------------------------------------


def test_no_predictable_tmp_envelope_paths_in_references() -> None:
    for path in sorted(REFERENCES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "/tmp/l30d-threads.json" not in text, path.name
        assert "/tmp/l30d-shortform.json" not in text, path.name


def test_research_runbook_host_envelopes_use_private_mktemp_noclobber_and_cleanup() -> None:
    runbook = reference_text("research-runbook")
    section_2b = runbook.split("**2b. Host Reddit threads.**", 1)[1].split(
        "**2c. Host TikTok/Instagram URLs.**", 1
    )[0]
    section_2c = runbook.split("**2c. Host TikTok/Instagram URLs.**", 1)[1].split(
        "**3. TikTok hashtags", 1
    )[0]

    assert 'mktemp -d "${TMPDIR:-/tmp}/last30days-reddit-threads.XXXXXX"' in section_2b
    assert 'REDDIT_THREADS_DIR=${X_POSTS_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/last30days-reddit-threads.XXXXXX")}' in section_2b
    assert 'REDDIT_THREADS_FILE="$REDDIT_THREADS_DIR/threads.json"' in section_2b
    assert (
        'trap \'rm -rf -- ${X_POSTS_DIR:+"$X_POSTS_DIR"} ${REDDIT_THREADS_DIR:+"$REDDIT_THREADS_DIR"}\' EXIT'
        in section_2b
    )
    assert "cat >| \"$REDDIT_THREADS_FILE\" <<'THREADS_EOF_{NONCE}'" in section_2b
    assert '--reddit-threads="$REDDIT_THREADS_FILE"' in section_2b

    assert "mktemp -d" in section_2c
    assert "EXIT" in section_2c
    assert ">|" in section_2c
    assert "$REDDIT_THREADS_DIR" in section_2c and "$X_POSTS_DIR" in section_2c
    assert '--shortform-urls="$SHORTFORM_FILE"' in section_2c


@pytest.mark.parametrize("order", ["x_then_reddit", "reddit_then_x"])
def test_grok_bot_x_and_reddit_threads_traps_clean_up_all_temp_dirs_in_both_orders(
    tmp_path: Path, order: str
) -> None:
    runbook = reference_text("research-runbook")
    section_2b = runbook.split("**2b. Host Reddit threads.**", 1)[1].split(
        "**2c. Host TikTok/Instagram URLs.**", 1
    )[0]
    reddit_block = _extract_bash_blocks(section_2b)[0]
    reddit_block = reddit_block.replace("{NONCE}", "Ab12Cd34Ef56")
    reddit_block = re.sub(
        r'\{"schema": "last30days-reddit-threads/1".*\}',
        lambda _: json.dumps(
            {
                "schema": "last30days-reddit-threads/1",
                "topic": "test topic",
                "threads": [
                    {"url": "https://www.reddit.com/r/python/comments/abc123/test_post/"}
                ],
            }
        ),
        reddit_block,
    )

    grok_block = _extract_bash_blocks(reference_text("grok-bot-x"))[0]
    grok_block = grok_block.replace("{X_POSTS_NONCE}", "Zy98Xw76Vu54").replace(
        "{X_POSTS_ENVELOPE_JSON}",
        json.dumps({"schema": "last30days-x-posts/1", "topic": "test topic", "calls": []}),
    )

    combined = (
        grok_block + "\n" + reddit_block
        if order == "x_then_reddit"
        else reddit_block + "\n" + grok_block
    )
    script = (
        "set -euo pipefail\n"
        "set -o noclobber\n"
        + combined
        + "\n"
        + 'test -f "$X_POSTS_FILE"\n'
        + 'test -f "$REDDIT_THREADS_FILE"\n'
        + 'printf "X_DIR=%s\\nR_DIR=%s\\n" "$X_POSTS_DIR" "$REDDIT_THREADS_DIR"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    x_dir = Path(re.search(r"^X_DIR=(.+)$", result.stdout, re.MULTILINE).group(1))
    r_dir = Path(re.search(r"^R_DIR=(.+)$", result.stdout, re.MULTILINE).group(1))
    assert not x_dir.exists(), f"X_POSTS_DIR leaked for order={order}: {x_dir}"
    assert not r_dir.exists(), f"REDDIT_THREADS_DIR leaked for order={order}: {r_dir}"
    assert list(tmp_path.iterdir()) == [], f"Leftover temp dirs in TMPDIR: {list(tmp_path.iterdir())}"


def test_grok_bot_x_and_reddit_threads_traps_clean_distinct_dirs_when_prepopulated(
    tmp_path: Path,
) -> None:
    """Even if both directories were created independently, either EXIT trap cleans both."""
    runbook = reference_text("research-runbook")
    section_2b = runbook.split("**2b. Host Reddit threads.**", 1)[1].split(
        "**2c. Host TikTok/Instagram URLs.**", 1
    )[0]
    reddit_block = _extract_bash_blocks(section_2b)[0]
    reddit_trap = next(line for line in reddit_block.splitlines() if line.startswith("trap "))
    grok_block = _extract_bash_blocks(reference_text("grok-bot-x"))[0]
    grok_trap = next(line for line in grok_block.splitlines() if line.startswith("trap "))

    for label, trap_line in (("reddit", reddit_trap), ("grok", grok_trap)):
        dir_a = tmp_path / f"{label}-x"
        dir_b = tmp_path / f"{label}-reddit"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "x-posts.json").write_text("{}", encoding="utf-8")
        (dir_b / "threads.json").write_text("{}", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", trap_line],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "X_POSTS_DIR": str(dir_a),
                "REDDIT_THREADS_DIR": str(dir_b),
            },
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not dir_a.exists(), f"{label} trap leaked X_POSTS_DIR"
        assert not dir_b.exists(), f"{label} trap leaked REDDIT_THREADS_DIR"


def test_research_runbook_host_threads_snippet_runs_under_noclobber_and_cleans_up(
    tmp_path: Path,
) -> None:
    runbook = reference_text("research-runbook")
    section_2b = runbook.split("**2b. Host Reddit threads.**", 1)[1].split(
        "**2c. Host TikTok/Instagram URLs.**", 1
    )[0]
    blocks = _extract_bash_blocks(section_2b)
    assert len(blocks) == 1

    canary = tmp_path / "pwned"
    hostile_topic = f'topic $(touch "{canary}") `id` $HOME'
    payload = json.dumps(
        {
            "schema": "last30days-reddit-threads/1",
            "topic": hostile_topic,
            "threads": [
                {"url": "https://www.reddit.com/r/python/comments/abc123/test_post/"}
            ],
        }
    )
    snippet = blocks[0].replace("{NONCE}", "Ab12Cd34Ef56")
    snippet = re.sub(
        r'\{"schema": "last30days-reddit-threads/1".*\}',
        lambda _: payload,
        snippet,
    )

    script = (
        "set -euo pipefail\n"
        "set -o noclobber\n"
        + snippet
        + "\n"
        + 'printf "DIR=%s\\nFILE=%s\\n" "$REDDIT_THREADS_DIR" "$REDDIT_THREADS_FILE"\n'
        + f'"{sys.executable}" -c '
        + "'import sys; sys.path.insert(0, sys.argv[1]); "
        + "from lib import reddit_envelope; "
        + "env = reddit_envelope.read(sys.argv[2], sys.argv[3]); "
        + "assert len(env.refs) == 1 and env.refs[0].post_id == \"abc123\"' "
        + f'"{SKILL_ROOT / "scripts"}" "$REDDIT_THREADS_FILE" "$HOSTILE_TOPIC"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TMPDIR": str(tmp_path),
            "HOSTILE_TOPIC": hostile_topic,
        },
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not canary.exists()

    dir_match = re.search(r"^DIR=(.+)$", result.stdout, re.MULTILINE)
    file_match = re.search(r"^FILE=(.+)$", result.stdout, re.MULTILINE)
    assert dir_match and file_match
    created_dir = Path(dir_match.group(1))
    created_file = Path(file_match.group(1))
    assert created_file.suffix == ".json"
    assert created_file.parent == created_dir
    assert not created_dir.exists(), "EXIT trap must remove the temporary directory"


# ---------------------------------------------------------------------------
# HIGH-224: Quoted heredoc + `--`/`=` separation in followups & library-queue
# ---------------------------------------------------------------------------


def test_followups_and_library_queue_eliminate_double_quoted_untrusted_interpolation() -> None:
    followups = reference_text("followups")
    library_queue = reference_text("library-queue")

    assert '--drill "<their target>"' not in followups
    assert 'queue cover "<topic name>"' not in followups
    assert 'queue cover "<topic name>"' not in library_queue
    assert 'library search "${LIBRARY_QUERY}"' not in library_queue

    assert "IFS= read -r -d '' DRILL_TARGET <<'TARGET_EOF' || true" in followups
    assert '--drill="$DRILL_TARGET"' in followups
    assert "IFS= read -r -d '' TOPIC_NAME <<'TOPIC_EOF' || true" in followups
    assert '--save-dir="${LAST30DAYS_MEMORY_DIR}" -- queue cover "$TOPIC_NAME"' in followups

    assert "IFS= read -r -d '' LIBRARY_QUERY <<'QUERY_EOF' || true" in library_queue
    assert '--save-dir="${LAST30DAYS_MEMORY_DIR}" -- library search "$LIBRARY_QUERY"' in library_queue
    assert "IFS= read -r -d '' TOPIC_NAME <<'TOPIC_EOF' || true" in library_queue
    assert '--save-dir="${LAST30DAYS_MEMORY_DIR}" -- queue cover "$TOPIC_NAME"' in library_queue


def test_followups_and_library_queue_snippets_block_shell_and_flag_injection(
    tmp_path: Path,
) -> None:
    probe_skill = tmp_path / "skill"
    probe_scripts = probe_skill / "scripts"
    probe_scripts.mkdir(parents=True)
    (probe_scripts / "last30days.py").write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(SKILL_ROOT / 'scripts')!r})\n"
        "import last30days as cli\n"
        "args, extra = cli.build_parser().parse_known_args(sys.argv[1:])\n"
        "print(json.dumps({'topic': args.topic, 'drill': args.drill, "
        "'followup_topic': args.followup_topic, 'followup_run': args.followup_run, "
        "'save_dir': args.save_dir, 'extra': extra}))\n",
        encoding="utf-8",
    )

    canary = tmp_path / "injected"
    hostile = f"--mock $(touch {canary}) `touch {canary}` $HOME 'single' \"double\""
    env = {
        **os.environ,
        "LAST30DAYS_PYTHON": sys.executable,
        "SKILL_DIR": str(probe_skill),
        "LAST30DAYS_MEMORY_DIR": str(tmp_path / "memory"),
        "FOLLOWUP_CACHE_ID": "0123456789abcdef0123456789abcdef",
    }

    followups_blocks = _extract_bash_blocks(reference_text("followups"))
    assert len(followups_blocks) == 2

    # 1. followups.md --drill block
    drill_script = (
        followups_blocks[0]
        .replace("<their target>", hostile)
        .replace("<original topic>", hostile)
    )
    res = subprocess.run(["bash", "-euo", "pipefail", "-c", drill_script], capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not canary.exists()
    parsed = json.loads(res.stdout)
    assert parsed["drill"] == hostile
    assert parsed["followup_topic"] == hostile
    assert parsed["extra"] == []

    # 2. followups.md queue cover block
    cover_script = followups_blocks[1].replace("<topic name>", hostile)
    res = subprocess.run(["bash", "-euo", "pipefail", "-c", cover_script], capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not canary.exists()
    parsed = json.loads(res.stdout)
    assert parsed["topic"] == ["queue", "cover", hostile]
    assert parsed["save_dir"] == str(tmp_path / "memory")
    assert parsed["extra"] == []

    # 3. library-queue.md library search & queue cover blocks
    lq_blocks = _extract_bash_blocks(reference_text("library-queue"))
    search_block = next(b for b in lq_blocks if "library search" in b)
    lq_cover_block = next(b for b in lq_blocks if "queue cover" in b)

    res = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", search_block.replace("<query>", hostile)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert not canary.exists()
    parsed = json.loads(res.stdout)
    assert parsed["topic"] == ["library", "search", hostile]
    assert parsed["save_dir"] == str(tmp_path / "memory")
    assert parsed["extra"] == []

    res = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", lq_cover_block.replace("<topic name>", hostile)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert not canary.exists()
    parsed = json.loads(res.stdout)
    assert parsed["topic"] == ["queue", "cover", hostile]
    assert parsed["save_dir"] == str(tmp_path / "memory")
    assert parsed["extra"] == []


# ---------------------------------------------------------------------------
# HIGH-225: Release workflow tag validation, refs/tags/ pin & zizmor hygiene
# ---------------------------------------------------------------------------


def test_release_workflow_structure_permissions_pinning_and_zizmor_safety() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    _assert_run_blocks_indented(text, label=str(RELEASE_WORKFLOW))
    workflow = yaml.safe_load(text)

    assert workflow.get("permissions") == {}
    assert "env" not in workflow, "Untrusted RELEASE_TAG must not be exported workflow-wide"

    jobs = workflow["jobs"]
    assert "validate" in jobs
    validate_job = jobs["validate"]
    assert validate_job["permissions"] == {"contents": "read"}
    assert set(validate_job.get("outputs", {}).keys()) >= {"tag", "version", "commit"}

    for gated_job in ("build-skill", "build-mcpb"):
        needs = jobs[gated_job].get("needs")
        needs_list = [needs] if isinstance(needs, str) else list(needs or [])
        assert "validate" in needs_list, f"{gated_job} must depend on validate"

    release_needs = jobs["release"].get("needs") or []
    assert {"validate", "build-skill", "build-mcpb"} <= set(release_needs)

    checkout_count = 0
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            if "uses" in step:
                ref = step["uses"]
                _, _, sha = ref.partition("@")
                assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
                    f"{job_name}: unpinned action {ref}"
                )
                if ref.startswith("actions/checkout@"):
                    checkout_count += 1
                    with_cfg = step.get("with") or {}
                    assert with_cfg.get("persist-credentials") is False
                    checkout_ref = str(with_cfg.get("ref", ""))
                    assert checkout_ref.startswith("refs/tags/${{"), (
                        f"{job_name}: checkout ref must pin refs/tags/, got {checkout_ref!r}"
                    )
            if "run" in step:
                run_body = step["run"]
                assert "${{" not in run_body, (
                    f"{job_name}/{step.get('name')}: never inline GitHub expressions in run:"
                )

    assert checkout_count == 4

    mcpb_build_step = next(
        s for s in jobs["build-mcpb"]["steps"] if s.get("name") == "Build MCP binary"
    )
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in mcpb_build_step["run"]


@pytest.mark.parametrize(
    "raw_tag,expected_ok",
    [
        ("v4.6.0", True),
        ("v0.0.1", True),
        ("v12.34.56", True),
        ("", False),
        ("4.6.0", False),
        ("main", False),
        ("48385610", False),
        ("refs/tags/v4.6.0", False),
        ("v4.6.0 -extldflags=-Wl,--evil", False),
        ("v4.6.0 -X main.Version=pwn", False),
        ("v4.6.0$(id)", False),
        ("v4.6.0;id", False),
        ("v4.6.0`id`", False),
        ('v4.6.0"quoted', False),
        ("v4.6.0..main", False),
        ("v4.6.0\nv4.6.1", False),
    ],
)
def test_release_workflow_tag_format_step_executes_strict_validation(
    tmp_path: Path, raw_tag: str, expected_ok: bool
) -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "format"
    )
    gh_output = tmp_path / "github_output"
    gh_output.write_text("", encoding="utf-8")

    result = subprocess.run(
        ["bash", "-c", step["run"]],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RAW_RELEASE_TAG": raw_tag,
            "GITHUB_OUTPUT": str(gh_output),
        },
        timeout=15,
    )
    if expected_ok:
        assert result.returncode == 0, result.stdout + result.stderr
        output = gh_output.read_text(encoding="utf-8")
        assert f"tag={raw_tag}\n" in output
        assert f"version={raw_tag[1:]}\n" in output
    else:
        assert result.returncode != 0, f"Expected rejection for {raw_tag!r}"
        assert gh_output.read_text(encoding="utf-8") == ""


def test_release_workflow_verify_step_checks_git_tag_and_manifest_versions(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "verify"
    )

    repo = tmp_path / "repo"
    (repo / ".github" / "scripts").mkdir(parents=True)
    (repo / "mcp").mkdir()
    shutil.copy2(
        ROOT / ".github" / "scripts" / "read_manifest_version.py",
        repo / ".github" / "scripts" / "read_manifest_version.py",
    )
    (repo / "pyproject.toml").write_text('[project]\nversion = "4.6.0"\n', encoding="utf-8")
    (repo / "mcp" / "manifest.json").write_text(
        json.dumps({"manifest_version": "0.3", "version": "4.6.0"}) + "\n",
        encoding="utf-8",
    )

    git_env = {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for cmd in (
        ["git", "-c", "init.templateDir=", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false",
            "commit", "-qm", "Initial",
        ],
    ):
        subprocess.run(cmd, cwd=repo, env=git_env, check=True)

    def run_verify(tag: str, version: str) -> tuple[int, str, str]:
        gh_out = tmp_path / f"out-{tag}-{version}"
        gh_out.write_text("", encoding="utf-8")
        proc = subprocess.run(
            ["bash", "-c", step["run"]],
            cwd=repo,
            capture_output=True,
            text=True,
            env={
                **git_env,
                "RELEASE_TAG": tag,
                "RELEASE_VERSION": version,
                "GITHUB_OUTPUT": str(gh_out),
            },
            timeout=15,
        )
        return proc.returncode, proc.stdout + proc.stderr, gh_out.read_text(encoding="utf-8")

    # 1. Missing git tag (even if a branch with that name exists) -> must fail.
    subprocess.run(["git", "branch", "v4.6.0"], cwd=repo, env=git_env, check=True)
    rc, _, _ = run_verify("v4.6.0", "4.6.0")
    assert rc != 0

    # 2. Real git tag matching both manifests -> passes and emits commit SHA.
    subprocess.run(
        [
            "git",
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "-c", "tag.gpgSign=false",
            "tag", "-a", "v4.6.0", "-m", "Release v4.6.0",
        ],
        cwd=repo,
        env=git_env,
        check=True,
    )
    rc, msg, out = run_verify("v4.6.0", "4.6.0")
    assert rc == 0, msg
    assert "tag=v4.6.0\n" in out
    assert "version=4.6.0\n" in out
    assert re.search(r"^commit=[0-9a-f]{40}$", out, re.MULTILINE)

    # 3. mcp/manifest.json version mismatch -> fails.
    (repo / "mcp" / "manifest.json").write_text(
        json.dumps({"manifest_version": "0.3", "version": "4.5.0"}) + "\n",
        encoding="utf-8",
    )
    rc, msg, _ = run_verify("v4.6.0", "4.6.0")
    assert rc != 0 and "mcp/manifest.json" in msg

    # 4. pyproject.toml version mismatch -> fails.
    (repo / "mcp" / "manifest.json").write_text(
        json.dumps({"manifest_version": "0.3", "version": "4.6.0"}) + "\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text('[project]\nversion = "4.5.0"\n', encoding="utf-8")
    rc, msg, _ = run_verify("v4.6.0", "4.6.0")
    assert rc != 0 and "pyproject.toml" in msg


# ---------------------------------------------------------------------------
# HIGH-226: Packaging / scanner ignore files exclude secrets & stay in parity
# ---------------------------------------------------------------------------


def test_all_ignore_files_exclude_secrets_keys_and_caches() -> None:
    required_secrets = {".env", ".env.*", ".claude/", "*.pem", "*.key", "__pycache__/", "*.pyc"}
    for path in (
        ROOT / ".skillignore",
        ROOT / ".clawhubignore",
        SKILL_ROOT / ".skillignore",
    ):
        entries = _ignore_entries(path)
        missing = required_secrets - entries
        assert not missing, f"{path.relative_to(ROOT)} missing secret/cache exclusions: {sorted(missing)}"


def test_root_skillignore_and_clawhubignore_stay_in_parity_with_dev_exclusions() -> None:
    skillignore = _ignore_entries(ROOT / ".skillignore")
    clawhubignore = _ignore_entries(ROOT / ".clawhubignore")
    assert skillignore == clawhubignore

    required_root = {
        ".env",
        ".env.*",
        ".claude/",
        "*.pem",
        "*.key",
        ".entire/",
        ".context/",
        ".memsearch/",
        ".pytest_cache/",
        ".worktrees/",
        ".codex-plugin/",
        ".grok-plugin/",
        "uv.lock",
        "changelog.d/",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "README.*.md",
        "skills/last30days/agents/",
        "skills/last30days/scripts/reddit_canary.py",
        "skills/last30days/scripts/x_lane_parity.py",
        "skills/last30days/scripts/setup-keychain.sh",
        "skills/last30days/scripts/setup-pass.sh",
    }
    assert required_root <= skillignore


def test_skill_dir_skillignore_reconciles_non_runtime_scripts_and_keeps_runtime() -> None:
    root_entries = _ignore_entries(ROOT / ".skillignore")
    skill_entries = _ignore_entries(SKILL_ROOT / ".skillignore")

    root_skill_scoped = {
        entry.removeprefix("skills/last30days/")
        for entry in root_entries
        if entry.startswith("skills/last30days/")
    }
    assert root_skill_scoped <= skill_entries, (
        f"skills/last30days/.skillignore missing entries from root .skillignore: "
        f"{sorted(root_skill_scoped - skill_entries)}"
    )

    for runtime_entry in (
        "SKILL.md",
        "references/",
        "scripts/",
        "scripts/last30days.py",
        "scripts/lib/",
        "scripts/watchlist.py",
        "scripts/store.py",
        "scripts/briefing.py",
    ):
        assert runtime_entry not in skill_entries
        assert f"skills/last30days/{runtime_entry}" not in root_entries
