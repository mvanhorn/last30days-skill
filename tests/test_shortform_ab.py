"""Metric math for scripts/shortform_ab.py (offline, no engine runs)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "shortform_ab.py"
_SPEC = importlib.util.spec_from_file_location("shortform_ab", _PATH)
ab = importlib.util.module_from_spec(_SPEC)
sys.modules["shortform_ab"] = ab
_SPEC.loader.exec_module(ab)


def _tt(vid: str, views: int | None = None, day: str | None = "2026-11-10", **extra):
    item = {"url": f"https://www.tiktok.com/@someone/video/{vid}", "published_at": day,
            "engagement": {"views": views} if views is not None else {}}
    item.update(extra)
    return item


def _ig(code: str, likes: int | None = None, kind: str = "reel"):
    return {"url": f"https://www.instagram.com/{kind}/{code}/", "engagement": {"likes": likes}}


def test_video_id_tiktok():
    assert ab.video_id("tiktok", "https://www.tiktok.com/@a.b/video/7710481357683576094?lang=en") == "7710481357683576094"
    assert ab.video_id("tiktok", "https://www.tiktok.com/@a/photo/7500000000000000001") == "7500000000000000001"
    assert ab.video_id("tiktok", "https://www.tiktok.com/@a") is None
    assert ab.video_id("tiktok", "") is None


def test_video_id_instagram():
    assert ab.video_id("instagram", "https://www.instagram.com/reel/DfYRA2ghafT/") == "DfYRA2ghafT"
    assert ab.video_id("instagram", "https://instagram.com/p/C-x_Y1z/?igsh=1") == "C-x_Y1z"
    assert ab.video_id("instagram", "https://www.instagram.com/reels/Abc123/") == "Abc123"
    assert ab.video_id("instagram", "https://www.instagram.com/someuser/") is None


def test_window_counts_splits_in_window_stale_and_undated():
    items = [_tt("1", day="2026-10-22"), _tt("2", day="2026-11-21T08:00:00"),
             _tt("3", day="2026-10-21"), _tt("4", day="2024-05-01"), _tt("5", day=None),
             _tt("6", day="not a date")]
    assert ab.window_counts(items, "2026-10-22", "2026-11-21") == {"in_window": 2, "stale": 2, "undated": 2}


def test_recall():
    assert ab.recall({"a", "b", "c", "d"}, {"a", "c", "z"}) == 0.5
    assert ab.recall(set(), {"a"}) is None
    assert ab.recall({"a"}, set()) == 0.0


def test_head_recall_uses_top_ten_by_views_for_tiktok():
    baseline = [_tt(str(i), views=i * 100) for i in range(1, 16)]  # top 10 are ids 6..15
    arm_ids = {"15", "14", "13", "12", "1", "2"}
    assert ab.head_ids("tiktok", baseline) == [str(i) for i in range(15, 5, -1)]
    assert ab.head_recall("tiktok", baseline, arm_ids) == 0.4


def test_head_recall_uses_likes_for_instagram_and_small_baselines():
    baseline = [_ig("AAA", likes=5), _ig("BBB", likes=50), _ig("CCC", likes=None, kind="p")]
    assert ab.head_ids("instagram", baseline) == ["BBB", "AAA", "CCC"]
    assert ab.head_recall("instagram", baseline, {"BBB"}) == 1 / 3
    assert ab.head_recall("instagram", [], {"BBB"}) is None


def test_completeness_per_field():
    items = [
        {"published_at": "2026-11-01", "author": "x", "body": "cap",
         "engagement": {"views": 1, "likes": 2, "comments": 0}},
        {"published_at": None, "author": "", "snippet": "",
         "engagement": {"likes": 3}},
    ]
    comp = ab.completeness(items)
    assert comp == {"date": 0.5, "creator": 0.5, "views": 0.5, "likes": 1.0, "comments": 0.5, "caption": 0.5}
    assert ab.completeness([])["date"] is None


def test_p90_nearest_rank_and_median():
    assert ab.p90([float(i) for i in range(1, 11)]) == 9.0
    assert ab.p90([5.0]) == 5.0
    assert ab.p90([]) is None
    assert ab.median([1, None, 3]) == 2


def _verdict(**overrides):
    base = dict(
        arm_in_window=[10, 12, 8], a_in_window=[20, 20, 20],
        arm_head_recall=[0.5, 0.4, None], arm_stale=[0, 0, 0],
        arm_wall=[40.0, 50.0, 60.0], a_wall=[45.0, 50.0, 55.0],
    )
    base.update(overrides)
    return ab.parity_verdict(**base)


def test_parity_pass():
    verdict = _verdict()
    assert verdict["pass"] is True
    assert all(verdict["checks"].values())


def test_parity_fails_each_bar():
    assert _verdict(arm_in_window=[9, 9, 9])["checks"]["in_window"] is False
    assert _verdict(arm_head_recall=[0.3, 0.39])["checks"]["head_recall"] is False
    assert _verdict(arm_head_recall=[None])["checks"]["head_recall"] is False
    assert _verdict(arm_stale=[0, 1, 0])["checks"]["stale"] is False
    assert _verdict(arm_wall=[86.0, 86.0, 86.0])["checks"]["wall"] is False  # A p90 55 + 30 = 85
    slow = _verdict(arm_wall=[180.0], a_wall=[170.0])
    assert slow["checks"]["wall"] is False and slow["pass"] is False


def test_strip_sc_keys_drops_only_scrapecreators_lines():
    text = "A=1\nSCRAPECREATORS_API_KEY=dummy\nexport SCRAPE_CREATORS_API_KEY=dummy\n# note\nSERPER_API_KEY=x\n"
    assert ab.strip_sc_keys(text) == "A=1\n# note\nSERPER_API_KEY=x\n"


def test_slugify():
    assert ab.slugify("Claude Code") == "claude-code"
    assert ab.slugify("  Dubai chocolate!! ") == "dubai-chocolate"
