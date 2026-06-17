from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scorecard.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_scorecard_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_scorecard_runs_ossf_action_and_uploads_sarif() -> None:
    text = _workflow_text()

    assert "ossf/scorecard-action" in text
    assert "github/codeql-action/upload-sarif" in text
    assert "results_format: sarif" in text


def test_scorecard_runs_on_schedule_and_default_branch() -> None:
    text = _workflow_text()

    # A weekly schedule surfaces security-health regressions even with no code
    # changes; push-to-main keeps the score fresh on every merge.
    assert "schedule:" in text
    assert "cron:" in text
    assert "branches:" in text
    assert "- main" in text


def test_scorecard_requests_minimal_permissions() -> None:
    text = _workflow_text()

    # Top-level is read-only; only the analysis job widens what it needs.
    assert "permissions: read-all" in text
    # Job-level permissions fully replace the top-level block, so the reads
    # checkout and Scorecard require must be listed explicitly or they default
    # to none and every run fails.
    assert "contents: read" in text
    assert "actions: read" in text
    assert "security-events: write" in text
    assert "id-token: write" in text


def test_scorecard_documents_advisory_policy() -> None:
    text = _workflow_text()

    assert "advisory-first" in text.lower()
    assert "never blocks merges" in text.lower()
