"""The loaded root routes to bounded instructions that survive distribution."""

import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

import pytest
import yaml

from tests.skill_contract import (
    ROUTES, SKILL_ROOT, STANDARD_CONDITIONS, reference_text, root_text, routing_table,
)


ROOT = SKILL_ROOT.parents[1]


def test_every_reference_has_the_correct_conditional_load_gate():
    assert routing_table() == ROUTES
    for names in ROUTES.values():
        for name in names:
            assert reference_text(name).strip(), f"empty runtime reference: {name}"


def test_core_and_standard_research_fit_the_read_budget():
    core = (SKILL_ROOT / "SKILL.md").read_bytes()
    assert len(core.splitlines()) <= 500, "root skill exceeds the 500-line read budget"
    assert len(core) <= 32_000, "root skill exceeds the 32,000-byte read budget"
    routes = routing_table()
    assert routes == ROUTES, "classify new routes before measuring the standard path"
    standard = {name for condition in STANDARD_CONDITIONS for name in routes[condition]}
    assert standard == {"runtime", "research-runbook", "synthesis"}
    total = len(core) + sum(len(reference_text(name).encode("utf-8")) for name in standard)
    assert total <= 180_000, f"standard configured research path loads {total} bytes"


def test_configured_user_without_global_marker_has_a_bounded_setup_check():
    core = root_text()
    gate = core.split("**FIRST-RUN GATE", 1)[1].split("**Onboarding consent", 1)[0]
    assert 'grep -q "SETUP_COMPLETE=true" ~/.config/last30days/.env' in gate
    assert "FIRST_RUN_DETECTED" in gate
    assert "read the setup-wizard reference" in gate
    setup = reference_text("setup-wizard")
    assert "Skip Step 0" in setup.split("## Step 0: First-Run Setup Wizard", 1)[0]

    names = {"runtime", "setup-wizard", "research-runbook", "synthesis"}
    total = len((SKILL_ROOT / "SKILL.md").read_bytes()) + sum(
        len(reference_text(name).encode("utf-8")) for name in names
    )
    assert total <= 190_000, f"configured path without a global marker loads {total} bytes"


def test_existing_users_can_load_repair_without_restarting_onboarding():
    condition = "Source repair requested or indicated by doctor"
    assert routing_table().get(condition) == ("setup-wizard",)
    assert condition not in STANDARD_CONDITIONS
    setup = reference_text("setup-wizard")
    entry = setup.split("## Step 0: First-Run Setup Wizard", 1)[0]
    assert "including when `SETUP_COMPLETE=true`" in entry
    assert "go directly to the applicable **Manual Setup Guide** subsection" in entry
    assert "Skip Step 0" in entry
    assert "do not write `SETUP_COMPLETE=true`" in entry
    assert "do not start research unless it was requested" in entry
    assert "explicit browser-cookie consent" in entry
    assert "Existing authorization still applies" in entry
    assert "a refusal, skip, or no answer is not consent" in entry
    assert "only **X on a Grok Bot (repair)**" in entry
    assert "never read a browser session or use the Linux / Mac mini repair" in entry


def test_doctor_routes_host_specific_repairs_without_authorizing_them():
    runbook = reference_text("research-runbook")
    doctor = runbook.split("**Doctor health check:**", 1)[1].split(
        "**Grok session expiry handling:**", 1,
    )[0]
    assert "Source repair requested or indicated by doctor" in doctor
    assert "read `references/setup-wizard.md` in full" in doctor
    assert "A doctor result is not authorization" in doctor
    assert "For a health-check-only request, relay the audit and repair guidance, then stop" in doctor


def test_selected_references_are_read_fully_without_loading_every_mode():
    core = root_text()
    bootstrap = core.split("## Bootstrap and file reads", 1)[1].split("## Reference routing", 1)[0]
    assert "Read only references whose conditions apply" in bootstrap
    assert "Read each selected reference in full before its phase" in bootstrap
    assert "Do not recursively read every reference" in bootstrap
    assert "continue bounded reads until the entire selected reference is loaded" in bootstrap
    assert "missing or unreadable" in bootstrap
    assert "stop before the affected command/output" in bootstrap
    for line in core.splitlines():
        if line.lower().startswith(("read all references", "always read every reference")):
            pytest.fail(f"unconditional reference loading defeats disclosure: {line}")


def test_paths_stay_bound_to_the_loaded_root_across_hosts():
    core = root_text()
    bootstrap = core.split("## Bootstrap and file reads", 1)[1].split("## Reference routing", 1)[0]
    assert "root SKILL.md actually loaded by the host" in bootstrap
    assert "Never bind it to `references/`, the current directory" in bootstrap
    assert "Read or equivalent local file capability" in bootstrap
    assert "read that replacement root in full, rebind `SKILL_DIR`" in bootstrap
    assert "Paths resolve relative to `SKILL_DIR`" in core
    metadata = yaml.safe_load(core.split("---", 2)[1])
    assert "Read" in metadata["allowed-tools"].split(", ")
    assert "references/*" in metadata["metadata"]["openclaw"]["files"]


def test_json_requests_load_the_structured_contract_without_agent_flag():
    routes = routing_table()
    assert routes["--agent mode or explicit machine-readable JSON"] == ("agent-mode",)
    output = root_text().split("## Output contract", 1)[1].split("**BADGE", 1)[0]
    assert "Explicit machine-readable JSON uses `--emit=json`" in output
    assert "verbatim engine stdout" in output
    assert "no prose badge, synthesis, footer, or invitation added" in output
    contract = reference_text("agent-mode")
    assert "--emit=json" in contract
    assert "--json-profile=raw" in contract


@pytest.fixture(scope="module")
def packaged_skill(tmp_path_factory):
    checkout = tmp_path_factory.mktemp("skill package source")
    skill = checkout / "skills" / "last30days"
    shutil.copytree(SKILL_ROOT, skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / ".gitattributes", checkout / ".gitattributes")
    git_home = checkout / "git-home"
    git_home.mkdir()
    env = {
        **os.environ,
        "HOME": str(git_home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for command in (
        ["git", "-c", "init.templateDir=", "init", "-q"],
        ["git", "add", "--", ".gitattributes", "skills/last30days"],
        ["git", "-c", "user.name=Package test", "-c", "user.email=package@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Package current skill files"],
        ["bash", "skills/last30days/scripts/build-skill.sh"],
    ):
        result = subprocess.run(command, cwd=checkout, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(checkout / "dist" / "last30days.skill") as archive:
        archive.extractall(checkout / "installed skills")
    return checkout, checkout / "installed skills" / "last30days", env


def test_skill_archive_contains_the_actual_routed_reference_bytes(packaged_skill):
    checkout, installed, env = packaged_skill
    assert (installed / "SKILL.md").read_bytes() == (SKILL_ROOT / "SKILL.md").read_bytes()
    for names in ROUTES.values():
        for name in names:
            assert reference_text(name, installed) == reference_text(name)
    result = subprocess.run(
        ["git", "archive", "--format=zip", "HEAD"], cwd=checkout, env=env,
        capture_output=True, check=True,
    )
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as repository_archive:
        for name in {name for names in ROUTES.values() for name in names}:
            path = f"skills/last30days/references/{name}.md"
            assert path in repository_archive.namelist(), f"plugin archive omits {path}"
            assert repository_archive.read(path) == (SKILL_ROOT / "references" / f"{name}.md").read_bytes()


def test_packaged_runtime_resolves_from_a_path_with_spaces_outside_cwd(packaged_skill, tmp_path):
    _, installed, _ = packaged_skill
    assert " " in str(installed)
    runtime = reference_text("runtime", installed)
    assignment = re.findall(r"^LAST30DAYS_MEMORY_DIR=.*$", runtime, re.M)
    assert len(assignment) == 1
    result = subprocess.run(
        ["bash", "-ec", assignment[0] + '\nprintf "%s" "$LAST30DAYS_MEMORY_DIR"'],
        cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "HOME": str(tmp_path / "home"),
            "SKILL_DIR": str(installed),
            "LAST30DAYS_PYTHON": sys.executable,
            "LAST30DAYS_CONFIG_DIR": str(tmp_path / "config"),
            "LAST30DAYS_CACHE_DIR": str(tmp_path / "cache"),
            "LAST30DAYS_MEMORY_DIR": str(tmp_path / "saved research"),
        },
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(tmp_path / "saved research")
