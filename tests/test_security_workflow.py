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


def test_security_workflow_runs_dependency_audit_advisory_first() -> None:
    text = _workflow_text()

    assert "dependency-audit:" in text
    assert "pip-audit" in text
    assert "continue-on-error: true" in text
    assert "Set continue-on-error: false once a clean baseline run is confirmed" in text


def test_security_workflow_runs_secret_scan_for_pull_requests_and_main_pushes() -> None:
    text = _workflow_text()

    assert "secret-scan:" in text
    assert "trufflesecurity/trufflehog" in text
    assert "github.event_name == 'pull_request'" in text
    assert "github.event_name == 'push'" in text
    assert "--only-verified" in text


def test_security_workflow_documents_advisory_policy() -> None:
    text = _workflow_text()

    assert "advisory-first" in text.lower()
    assert "does not block merges" in text.lower()
    assert "fixtures" in text.lower()
    assert "env-based auth" in text.lower()


def test_agent_guidance_mentions_secret_hygiene() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    assert "Security hygiene" in text
    assert "Never commit real API keys" in text
    assert "skills/last30days/scripts/lib/env.py" in text
    assert "fixtures" in text
