"""Tests for scripts/lib/health.py and pipeline degradation preservation."""

import subprocess
from unittest import mock

from lib import health, pipeline


class TestProbeCommand:
    def test_missing_when_not_on_path(self):
        result = health.probe_command(["definitely-not-a-real-binary-xyz"])
        assert result.state == health.MISSING
        assert not result.usable

    def test_ok_on_exit_zero(self):
        result = health.probe_command(["true"])
        assert result.state == health.OK
        assert result.ok

    def test_error_on_nonzero_exit(self):
        result = health.probe_command(["false"])
        assert result.state == health.ERROR
        assert not result.ok

    def test_timeout(self):
        result = health.probe_command(["sleep", "5"], timeout=0.1)
        assert result.state == health.TIMEOUT

    def test_broken_when_exec_raises(self):
        # On PATH (which returns a path) but exec raises -> broken, not missing.
        with mock.patch.object(health.shutil, "which", return_value="/usr/bin/x"), \
             mock.patch.object(health.subprocess, "run", side_effect=OSError("exec format error")):
            result = health.probe_command(["x"])
        assert result.state == health.BROKEN

    def test_broken_on_exit_127(self):
        fake = subprocess.CompletedProcess(args=["x"], returncode=127, stdout="", stderr="not found")
        with mock.patch.object(health.shutil, "which", return_value="/usr/bin/x"), \
             mock.patch.object(health.subprocess, "run", return_value=fake):
            result = health.probe_command(["x"])
        assert result.state == health.BROKEN


class _FakeCandidate:
    def __init__(self, sources):
        self.sources = sources


def _candidates(n=6):
    return [_FakeCandidate(["reddit"]) for _ in range(n)]


class TestDegradationWarnings:
    """Partial failures survive as a distinct 'degraded' warning."""

    def test_degraded_surfaced_distinctly_from_failed(self):
        warnings = pipeline._warnings(
            items_by_source={"reddit": [object()], "github": [object()]},
            candidates=_candidates(),
            errors_by_source={"x": "hard failure"},
            degraded_by_source={"reddit": "429 on one subquery"},
        )
        joined = " | ".join(warnings)
        assert "Some sources failed: x" in joined
        assert "degraded" in joined.lower()
        assert "reddit" in joined

    def test_no_degraded_warning_when_none(self):
        warnings = pipeline._warnings(
            items_by_source={"reddit": [object()]},
            candidates=_candidates(),
            errors_by_source={},
            degraded_by_source={},
        )
        assert not any("degraded" in w.lower() for w in warnings)
