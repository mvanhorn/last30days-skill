#!/usr/bin/env python3
"""Compare the X results of two saved last30days report JSON files.

Dev tool for checking one X lane against another on the same topic and
window. Each input is the output of ``last30days.py <topic> --emit=json
--json-profile=raw``; the optional envelope is the candidate's ``--x-posts``
file, so a baseline post the candidate fetched but did not keep still counts
toward recall.

    python3 scripts/x_lane_parity.py baseline.json candidate.json [--envelope x-posts.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_STATUS_RE = re.compile(r"/status/(\d+)")
_METRICS = ("likes", "reposts", "replies", "quotes")
TOP_N = 10


def _x_items(path: str) -> list[dict]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return list((report.get("items_by_source") or {}).get("x") or [])


def _post_id(item: dict) -> str | None:
    match = _STATUS_RE.search(str(item.get("url") or ""))
    return match.group(1) if match else None


def _likes(item: dict) -> int:
    value = (item.get("engagement") or {}).get("likes")
    return int(value) if isinstance(value, (int, float)) else 0


def _envelope_ids(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        str(post.get("id"))
        for call in payload.get("calls") or []
        for post in call.get("posts") or []
        if post.get("id")
    }


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def compare(baseline: str, candidate: str, *, envelope: str | None = None) -> dict:
    """Counts, top-liked recall, field completeness, and author spread."""
    base_items = _x_items(baseline)
    cand_items = _x_items(candidate)
    cand_ids = {pid for pid in map(_post_id, cand_items) if pid} | _envelope_ids(envelope)
    top = sorted(base_items, key=_likes, reverse=True)[:TOP_N]
    top_ids = [pid for pid in map(_post_id, top) if pid]
    complete = sum(
        1 for item in cand_items
        if all(isinstance((item.get("engagement") or {}).get(m), (int, float)) for m in _METRICS)
    )
    return {
        "baseline_count": len(base_items),
        "candidate_count": len(cand_items),
        "count_ratio": _ratio(len(cand_items), len(base_items)),
        "top_liked_recall": _ratio(sum(1 for pid in top_ids if pid in cand_ids), len(top_ids)),
        "candidate_field_completeness": _ratio(complete, len(cand_items)),
        "baseline_authors": len({item.get("author") for item in base_items}),
        "candidate_authors": len({item.get("author") for item in cand_items}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--envelope")
    args = parser.parse_args(argv)
    json.dump(compare(args.baseline, args.candidate, envelope=args.envelope), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
