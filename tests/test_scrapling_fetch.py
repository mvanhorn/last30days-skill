"""Tests for the optional Scrapling-CLI fetch helper (lib/scrapling_fetch.py).

Covers the CLI-gated availability check, argv construction (including the
stealthy-only ``--solve-cloudflare`` and the CSS selector), the success path
that reads the CLI's output file back as a string, and graceful degradation
on every failure mode: absent binary, unknown mode/format, non-zero exit,
timeout, and empty output. The Scrapling CLI is never actually invoked --
``subproc.run_with_timeout`` is monkeypatched, matching the arXiv/YouTube
source tests.
"""

from __future__ import annotations

from pathlib import Path

from lib import scrapling_fetch
from lib.subproc import SubprocResult, SubprocTimeout


def _force_available(monkeypatch, present=True):
    monkeypatch.setattr(
        scrapling_fetch.shutil,
        "which",
        lambda _bin: "/usr/local/bin/scrapling" if present else None,
    )


# ---- availability gate ----

def test_is_available_reflects_which(monkeypatch):
    _force_available(monkeypatch, present=True)
    assert scrapling_fetch.is_available() is True
    _force_available(monkeypatch, present=False)
    assert scrapling_fetch.is_available() is False


def test_fetch_returns_none_when_cli_absent(monkeypatch):
    _force_available(monkeypatch, present=False)
    # run_with_timeout must never be reached when the CLI is absent.
    def _boom(*a, **k):  # pragma: no cover - asserted not called
        raise AssertionError("subprocess spawned despite absent CLI")
    monkeypatch.setattr(scrapling_fetch.subproc, "run_with_timeout", _boom)
    assert scrapling_fetch.fetch("https://example.com") is None


# ---- argv construction ----

def test_build_args_basic_get():
    args = scrapling_fetch._build_args("get", "https://example.com", Path("/tmp/o.md"))
    assert args == ["scrapling", "extract", "get", "https://example.com", "/tmp/o.md"]


def test_build_args_stealthy_includes_solve_and_selector():
    args = scrapling_fetch._build_args(
        "stealthy-fetch", "https://x.test", Path("/tmp/o.html"),
        css_selector="#main", solve_cloudflare=True,
    )
    assert "--css-selector" in args and "#main" in args
    assert "--solve-cloudflare" in args


def test_solve_cloudflare_dropped_for_non_stealthy_mode():
    args = scrapling_fetch._build_args(
        "get", "https://x.test", Path("/tmp/o.md"), solve_cloudflare=True,
    )
    assert "--solve-cloudflare" not in args


# ---- input validation ----

def test_unknown_mode_returns_none(monkeypatch):
    _force_available(monkeypatch)
    assert scrapling_fetch.fetch("https://x.test", mode="teleport") is None


def test_unknown_format_returns_none(monkeypatch):
    _force_available(monkeypatch)
    assert scrapling_fetch.fetch("https://x.test", fmt="pdf") is None


# ---- success + failure paths (CLI mocked) ----

def _run_writing(payload, returncode=0, stderr=""):
    """Return a fake run_with_timeout that writes ``payload`` to the out file
    (the last argv element) and returns a SubprocResult."""
    def _fake(cmd, timeout):
        Path(cmd[-1]).write_text(payload, encoding="utf-8")
        return SubprocResult(returncode=returncode, stdout="", stderr=stderr)
    return _fake


def test_fetch_success_reads_output_file(monkeypatch):
    _force_available(monkeypatch)
    monkeypatch.setattr(
        scrapling_fetch.subproc, "run_with_timeout",
        _run_writing("# Example Domain\n\nHello."),
    )
    out = scrapling_fetch.fetch("https://example.com")
    assert out == "# Example Domain\n\nHello."


def test_fetch_cleans_up_temp_file(monkeypatch):
    _force_available(monkeypatch)
    seen = {}

    def _fake(cmd, timeout):
        seen["path"] = cmd[-1]
        Path(cmd[-1]).write_text("content", encoding="utf-8")
        return SubprocResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scrapling_fetch.subproc, "run_with_timeout", _fake)
    scrapling_fetch.fetch("https://example.com")
    assert not Path(seen["path"]).exists()  # temp file removed after read


def test_nonzero_exit_returns_none(monkeypatch):
    _force_available(monkeypatch)
    monkeypatch.setattr(
        scrapling_fetch.subproc, "run_with_timeout",
        _run_writing("", returncode=1, stderr="blocked\nmore"),
    )
    assert scrapling_fetch.fetch("https://example.com") is None


def test_empty_output_returns_none(monkeypatch):
    _force_available(monkeypatch)
    monkeypatch.setattr(
        scrapling_fetch.subproc, "run_with_timeout",
        _run_writing("   \n  "),
    )
    assert scrapling_fetch.fetch("https://example.com") is None


def test_timeout_returns_none(monkeypatch):
    _force_available(monkeypatch)

    def _timeout(cmd, timeout):
        raise SubprocTimeout("timed out")

    monkeypatch.setattr(scrapling_fetch.subproc, "run_with_timeout", _timeout)
    assert scrapling_fetch.fetch("https://example.com", mode="stealthy-fetch") is None


def test_spawn_failure_returns_none(monkeypatch):
    _force_available(monkeypatch)

    def _missing(cmd, timeout):
        raise FileNotFoundError("no scrapling")

    monkeypatch.setattr(scrapling_fetch.subproc, "run_with_timeout", _missing)
    assert scrapling_fetch.fetch("https://example.com") is None


def test_fetch_returns_none_when_temp_file_creation_fails(monkeypatch):
    # The never-raise contract covers the output temp file too: a full or
    # unwritable temp dir degrades to None, and the CLI is never spawned
    # (it would have no output path to write to).
    _force_available(monkeypatch, present=True)

    def _no_tmp(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr(scrapling_fetch.tempfile, "NamedTemporaryFile", _no_tmp)

    def _boom(*a, **k):  # pragma: no cover - asserted not called
        raise AssertionError("CLI must not run without an output file")

    monkeypatch.setattr(scrapling_fetch.subproc, "run_with_timeout", _boom)
    assert scrapling_fetch.fetch("https://example.com/x") is None
