from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "security.yml"
# AGENTS.md is the canonical agent-guidance file; CLAUDE.md is a one-line
# pointer (`@AGENTS.md`) so anything Claude Code-shaped reads the same source.
AGENTS = ROOT / "AGENTS.md"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_security_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_security_workflow_runs_dependency_audit_as_blocking_check() -> None:
    text = _workflow_text()
    dependency_audit_job = text.split("dependency-audit:", 1)[1].split("secret-scan:", 1)[0]

    assert "dependency-audit:" in text
    assert "uv audit --locked" in dependency_audit_job
    assert "continue-on-error: true" not in dependency_audit_job


def test_security_workflow_runs_secret_scan_for_pull_requests_and_main_pushes() -> None:
    text = _workflow_text()
    secret_scan_job = text.split("secret-scan:", 1)[1].split("sast-scan:", 1)[0]

    assert "secret-scan:" in text
    assert "pull_request:" in text
    assert "push:" in text
    assert "workflow_dispatch:" in text
    assert "branches:\n      - main" in text
    assert "trufflesecurity/trufflehog" in secret_scan_job
    assert "version: 3.95.5" in secret_scan_job
    assert "extra_args: --results=verified" in secret_scan_job
    assert "continue-on-error: true" not in secret_scan_job
    assert "if: github.event_name" not in secret_scan_job
    assert "path: ./" not in secret_scan_job


def test_security_workflow_runs_sast_without_unused_code_scanning_permission() -> None:
    text = _workflow_text()
    sast_job = text.split("sast-scan:", 1)[1]

    assert "semgrep/semgrep@sha256:" in sast_job
    assert "semgrep scan --config=auto" in sast_job
    assert "SEMGREP_SEND_METRICS: off" in sast_job
    assert "continue-on-error: true" in sast_job
    assert "contents: read" in sast_job
    assert "security-events: write" not in sast_job
    assert "--sarif" not in sast_job
    assert "upload-sarif" not in sast_job


def test_agent_guidance_mentions_secret_hygiene() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    assert "Security hygiene" in text
    assert "Never commit real API keys" in text
    assert "skills/last30days/scripts/lib/env.py" in text
    assert "fixtures" in text
