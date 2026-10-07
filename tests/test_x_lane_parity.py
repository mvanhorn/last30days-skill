"""x_lane_parity: compare the X results of two saved report JSON files."""

from __future__ import annotations

import json
from pathlib import Path

import x_lane_parity as parity


def _item(post_id: int, handle: str, likes: int | None, **engagement) -> dict:
    eng = {"likes": likes, "reposts": 1, "replies": 0, "quotes": 0, **engagement}
    if likes is None:
        eng.pop("likes")
    return {
        "source": "x",
        "url": f"https://x.com/{handle}/status/{post_id}",
        "author": handle,
        "engagement": eng,
    }


def _report(tmp_path: Path, name: str, items: list[dict]) -> str:
    path = tmp_path / name
    path.write_text(json.dumps({"topic": "t", "items_by_source": {"x": items}}), encoding="utf-8")
    return str(path)


def test_counts_ratio_and_top_liked_recall(tmp_path):
    baseline = [_item(100 + i, f"b{i}", likes=100 - i) for i in range(12)]
    # The candidate holds 7 of the baseline's 10 most-liked posts plus 3 others.
    candidate = [_item(100 + i, f"b{i}", likes=100 - i) for i in (0, 1, 2, 3, 4, 5, 6)]
    candidate += [_item(900 + i, "c", likes=5) for i in range(3)]
    result = parity.compare(_report(tmp_path, "a.json", baseline), _report(tmp_path, "b.json", candidate))
    assert result["baseline_count"] == 12
    assert result["candidate_count"] == 10
    assert result["count_ratio"] == round(10 / 12, 3)
    assert result["top_liked_recall"] == 0.7
    assert result["baseline_authors"] == 12
    assert result["candidate_authors"] == 8


def test_recall_also_counts_posts_present_only_in_the_envelope(tmp_path):
    baseline = [_item(100 + i, f"b{i}", likes=50 - i) for i in range(10)]
    candidate = [_item(100, "b0", likes=50)]
    envelope = tmp_path / "env.json"
    envelope.write_text(json.dumps({"calls": [{"lane": "topic", "posts": [
        {"id": str(100 + i), "author_handle": f"b{i}"} for i in range(1, 5)
    ]}]}), encoding="utf-8")
    result = parity.compare(
        _report(tmp_path, "a.json", baseline), _report(tmp_path, "b.json", candidate), envelope=str(envelope),
    )
    assert result["top_liked_recall"] == 0.5


def test_field_completeness_counts_missing_metrics(tmp_path):
    candidate = [_item(1, "a", likes=3), _item(2, "b", likes=None)]
    result = parity.compare(_report(tmp_path, "a.json", [_item(1, "a", likes=3)]), _report(tmp_path, "b.json", candidate))
    assert result["candidate_field_completeness"] == 0.5


def test_empty_reports_do_not_crash(tmp_path):
    result = parity.compare(_report(tmp_path, "a.json", []), _report(tmp_path, "b.json", []))
    assert result["baseline_count"] == 0
    assert result["count_ratio"] == 0.0
    assert result["top_liked_recall"] == 0.0
