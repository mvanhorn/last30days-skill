"""Tests for the automated GitHub star history SVG generator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / ".github" / "scripts" / "update_star_history.py"
SEED_PATH = ROOT / ".github" / "data" / "star-history-seed.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("update_star_history", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["update_star_history"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_seed_is_sorted_and_monotonic():
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    pts = data["points"]
    assert len(pts) >= 50
    for prev, cur in zip(pts, pts[1:]):
        assert cur[0] > prev[0], f"Dates not strictly increasing: {prev} -> {cur}"
        assert cur[1] >= prev[1], f"Stars decreased: {prev} -> {cur}"


def test_merge_point_is_monotonic_and_idempotent():
    mod = _load_module()
    pts = [["2026-01-25", 1], ["2026-11-24", 74000]]
    # Same day with lower count never regresses
    updated = mod.merge_point(pts, "2026-11-24", 73500)
    assert updated[-1] == ["2026-11-24", 74000]
    # Same day with higher count updates in place
    updated = mod.merge_point(updated, "2026-11-24", 74500)
    assert updated[-1] == ["2026-11-24", 74500]
    # New day appends in sorted order
    updated = mod.merge_point(updated, "2026-11-25", 75200)
    assert updated[-1] == ["2026-11-25", 75200]
    # Next-day dip carries highest count forward instead of regressing
    updated = mod.merge_point(updated, "2026-11-26", 75100)
    assert updated[-1] == ["2026-11-26", 75200]
    assert len(updated) == 4


def test_invalid_date_rejected_before_saving(tmp_path, monkeypatch):
    import pytest

    mod = _load_module()
    data_file = tmp_path / "star-history.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_star_history.py",
            "--seed",
            str(SEED_PATH),
            "--data",
            str(data_file),
            "--out-light",
            str(tmp_path / "light.svg"),
            "--out-dark",
            str(tmp_path / "dark.svg"),
            "--stars",
            "75000",
            "--date",
            "2026-13-45",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 2
    assert not data_file.exists()


def test_render_svg_light_and_dark(tmp_path):
    mod = _load_module()
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for theme in ("light", "dark"):
        svg = mod.render_svg(data, theme)
        assert svg.startswith("<svg ")
        assert svg.strip().endswith("</svg>")
        assert "mvanhorn/last30days-skill" in svg
        assert "74,042" in svg
        assert "v4 Launch" in svg


def test_nice_y_scale_grows_cleanly():
    mod = _load_module()
    y_max, ticks = mod.nice_y_scale(74057)
    assert y_max == 80000
    assert ticks == [0, 20000, 40000, 60000, 80000]

    y_max_big, ticks_big = mod.nice_y_scale(105000)
    assert y_max_big >= 110000
    assert ticks_big[0] == 0 and ticks_big[-1] == y_max_big
