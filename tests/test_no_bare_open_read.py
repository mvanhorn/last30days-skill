"""Prevent direct ``open(...).read()`` calls in the CLI entrypoint."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAST30DAYS = ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


class BareOpenReadFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "read"
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and func.value.func.id == "open"
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)


def test_last30days_does_not_call_read_directly_on_open():
    tree = ast.parse(LAST30DAYS.read_text(encoding="utf-8"), filename=str(LAST30DAYS))
    finder = BareOpenReadFinder()
    finder.visit(tree)

    assert not finder.violations, (
        "Use a context manager for file reads instead of direct open(...).read() "
        f"in {LAST30DAYS.relative_to(ROOT)} at lines: {finder.violations}"
    )
