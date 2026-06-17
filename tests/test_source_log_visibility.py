"""Convention enforcement for `log.source_log(..., tty_only=False)`.

`log.source_log` defaults to `tty_only=True`, silently dropping every line
when stderr isn't a real TTY (every Claude Code / Codex / CI / captured-
output run). The default exists to keep interactive output uncluttered,
but it weaponizes any source module that forgets to opt out: error logs,
query heartbeats, and success signals all disappear.

Ten source modules quietly shipped with this bug. This test prevents the
eleventh. Every `log.source_log(...)` call site under
`skills/last30days/scripts/lib/` must pass `tty_only=False` explicitly.
The cost is one kwarg per call; the value is that source observability
never goes silent again, even when the next contributor copies an old
`_log` template without thinking.

Implementation uses `ast.parse` (not regex) so the convention check is
robust against multi-line calls, nested parens in f-strings, calls inside
docstrings or comments, whitespace variation (`tty_only = False`), and
future indentation styles.
"""

import ast
import io
import pathlib
import sys
import unittest
from unittest.mock import patch

from lib import bluesky, perplexity

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib"


class _SourceLogCallFinder(ast.NodeVisitor):
    """Collect every `log.source_log(...)` call from a parsed module."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "source_log"
            and isinstance(func.value, ast.Name)
            and func.value.id == "log"
        ):
            self.calls.append(node)
        self.generic_visit(node)


def _has_tty_only_false_kwarg(call: ast.Call) -> bool:
    """Return True iff the call passes `tty_only=False` as a keyword arg."""
    for kw in call.keywords:
        if kw.arg == "tty_only" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _iter_source_modules() -> list[pathlib.Path]:
    """Yield production source modules under lib/ (excluding vendor and __init__)."""
    paths: list[pathlib.Path] = []
    for path in LIB_DIR.rglob("*.py"):
        if path.name == "log.py":
            continue  # definition site; not a caller
        if "vendor" in path.parts:
            continue  # vendored third-party code
        if path.name == "__init__.py":
            continue  # bare package marker per AGENTS.md
        paths.append(path)
    return sorted(paths)


class SourceLogConventionTests(unittest.TestCase):
    """Enforce the project convention at the codebase level."""

    def test_every_source_log_call_passes_tty_only_false(self):
        violations: list[str] = []
        for path in _iter_source_modules():
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                self.fail(f"Could not parse {path.name}: {exc}")
            finder = _SourceLogCallFinder()
            finder.visit(tree)
            for call in finder.calls:
                if not _has_tty_only_false_kwarg(call):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{call.lineno}")
        if violations:
            self.fail(
                "Source modules must call `log.source_log(..., tty_only=False)` so "
                "lines stay visible under non-TTY contexts (Claude Code, Codex, CI, "
                "captured output). See AGENTS.md for the convention. Violations:\n  - "
                + "\n  - ".join(violations)
            )


class _NonTTYStringIO(io.StringIO):
    """StringIO subclass that reports as a non-TTY stream."""

    def isatty(self) -> bool:
        return False


class PerplexityAndBlueskyVisibilityTests(unittest.TestCase):
    """Targeted regression tests for the two original bug instances.

    Kept alongside the convention test so a future refactor of either
    module immediately surfaces a regression if the opt-out is dropped.
    """

    def _captured_stderr_under_non_tty(self, log_callable) -> str:
        fake_stderr = _NonTTYStringIO()
        with patch.object(sys, "stderr", fake_stderr):
            log_callable("visibility probe")
        return fake_stderr.getvalue()

    def test_perplexity_log_visible_under_non_tty(self):
        out = self._captured_stderr_under_non_tty(perplexity._log)
        self.assertIn("[Perplexity] visibility probe", out)

    def test_bluesky_log_visible_under_non_tty(self):
        out = self._captured_stderr_under_non_tty(bluesky._log)
        self.assertIn("[Bluesky] visibility probe", out)


if __name__ == "__main__":
    unittest.main()
