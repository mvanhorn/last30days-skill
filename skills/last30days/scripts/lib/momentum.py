"""Two-window momentum analysis over the saved report cache.

``last30days.py momentum`` re-scores the most recent saved report under a
shorter lookback window (default 7 days) and diffs the two rankings -
offline, no network, no re-search. Because both views come from the same
snapshot seconds apart, there is no freshness drift by construction: the
short window is pure math over the exact corpus the long window gathered.

The diff is the product: it separates what is breaking out (rank climbed)
from what is fading (rank dropped) from what is sustained (top of both),
plus a week-share headline - the fraction of the long window's engagement
that landed inside the short window. A steady field sits near the
proportional baseline (7/30 ~ 23%); well above that means the topic is
moving fast.

Scoring reuses the engine's own functions (``rerank._final_score``,
``schema.candidate_out_of_window``, ``dates.recency_score``) so the
re-scored numbers are computed with identical math - only the freshness
scale and window membership change. Any window pair works: 90->30, 14->3.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import dates as lib_dates
from . import env
from . import rerank as lib_rerank
from . import schema as lib_schema

# Rank positions gained or lost (long -> short window) to count as movement.
CLIMB_THRESHOLD = 5
DEFAULT_SHORT_DAYS = 7


def report_cache_path() -> Path | None:
    """Location of the saved report cache, mirroring _last_report_cache_path."""
    if env.CONFIG_DIR is None:
        return None
    return env.CONFIG_DIR / "last-report.json"


def _best_age_days(candidate: lib_schema.Candidate, as_of: date) -> int | None:
    """Age in days of the candidate's most recent dated item, relative to as_of."""
    pub = lib_schema.candidate_best_published_at(candidate)
    if not pub:
        return None
    try:
        d = datetime.fromisoformat(pub[:10]).date()
    except ValueError:
        return None
    return max(0, (as_of - d).days)


def analyze_report(
    payload: dict[str, Any],
    days: int = DEFAULT_SHORT_DAYS,
    as_of: date | None = None,
    top: int = 8,
) -> dict[str, Any]:
    """Re-score one report payload under a shorter window and diff the rankings.

    Pure function over the cached report dict (the ``reports[i].report``
    object inside last-report.json). Returns a JSON-safe result dict.
    """
    report = payload if "ranked_candidates" in payload else {}
    candidates = [
        lib_schema.candidate_from_dict(item)
        for item in report.get("ranked_candidates") or []
    ]
    range_from = str(report.get("range_from") or "")
    range_to = str(report.get("range_to") or "")
    if as_of is None:
        as_of = (
            datetime.fromisoformat(range_to).date()
            if range_to
            else datetime.now().date()
        )
    short_from = (as_of - timedelta(days=days - 1)).isoformat()

    rows: list[dict[str, Any]] = []
    # Long-window ranks are the engine's saved ranking: the array order of
    # ranked_candidates in the cache. Deliberately no re-sort — a score-only
    # sort reconstructs ties differently than the engine did (its tie-break
    # is (-final_score, title)), and any divergence corrupts every delta.
    for rank_long, cand in enumerate(candidates, start=1):
        age = _best_age_days(cand, as_of)
        # Freshness under the NEW window. Undated candidates keep the engine's
        # original value: an unknown date is a coverage gap, not staleness
        # (same rule as schema.candidate_out_of_window).
        if age is not None:
            cand.freshness = float(
                lib_dates.recency_score(
                    (as_of - timedelta(days=age)).isoformat(),
                    max_days=days,
                    reference_date=as_of.isoformat(),
                )
            )
        # Window membership and final score both evaluate against the NEW
        # window via the candidate's metadata range.
        cand.metadata["range_from"] = short_from
        cand.metadata["range_to"] = as_of.isoformat()
        out_of_window = lib_schema.candidate_out_of_window(cand)
        final_short = lib_rerank._final_score(cand)
        rows.append(
            {
                "candidate_id": cand.candidate_id,
                "title": cand.title[:110],
                "source": cand.source,
                "url": (cand.source_items[0].url if cand.source_items else "") or "",
                "published": (lib_schema.candidate_best_published_at(cand) or "")[:10],
                "age_days": age,
                "in_window": (age is not None and age < days) and not out_of_window,
                "out_of_window": out_of_window,
                "score_long": round(cand.final_score or 0.0, 1),
                "score_short": round(final_short, 1),
                "rank_long": rank_long,
                "engagement": cand.engagement or 0.0,
                "cluster_id": cand.cluster_id,
            }
        )

    rows.sort(key=lambda r: -r["score_short"])
    for rank_short, row in enumerate(rows, start=1):
        row["rank_short"] = rank_short
        delta = row["rank_long"] - rank_short
        row["rank_delta"] = delta
        if delta >= CLIMB_THRESHOLD:
            row["momentum"] = "breakout"
        elif -delta >= CLIMB_THRESHOLD:
            row["momentum"] = "fading"
        elif rank_short <= top:
            row["momentum"] = "sustained"
        else:
            row["momentum"] = "stable"

    def _share(subset: list[dict[str, Any]]) -> float:
        total = sum(float(r["engagement"]) for r in subset)
        if total <= 0:
            return 0.0
        short = sum(float(r["engagement"]) for r in subset if r["in_window"])
        return short / total

    cluster_titles = {}
    for cluster in report.get("clusters") or []:
        cluster_titles[cluster.get("cluster_id") or cluster.get("id")] = (
            cluster.get("title") or cluster.get("label") or "?"
        )
    clusters: list[dict[str, Any]] = []
    by_cluster: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_cluster.setdefault(row["cluster_id"], []).append(row)
    for cluster_id, members in by_cluster.items():
        if cluster_id is None:
            continue
        clusters.append(
            {
                "cluster": cluster_titles.get(cluster_id, str(cluster_id)),
                "items": len(members),
                "short_share": round(_share(members), 3),
                "engagement_total": round(sum(float(m["engagement"]) for m in members), 1),
            }
        )
    clusters.sort(key=lambda c: -c["engagement_total"])

    return {
        "topic": report.get("topic"),
        "window_long": f"{range_from} -> {range_to}",
        "window_short": f"{short_from} -> {as_of.isoformat()} ({days}d)",
        "short_share": round(_share(rows), 3),
        "candidates": rows,
        "clusters": clusters,
    }


def render_markdown(result: dict[str, Any], top: int = 8) -> str:
    """Human brief: headline share, then the momentum quadrants."""
    lines = [
        f"# Momentum diff: {result.get('topic')}",
        f"- long window: {result['window_long']}",
        f"- short window: {result['window_short']}",
        f"- short-share of total engagement: {result['short_share']:.0%}",
        "",
        "## Clusters by short-share (velocity of attention)",
    ]
    for cluster in result["clusters"][:8]:
        lines.append(
            f"- {cluster['cluster']}: {cluster['short_share']:.0%} of its engagement "
            f"landed in the short window ({cluster['items']} items)"
        )
    for bucket, label in (
        ("breakout", "## Breakouts (climbed the most)"),
        ("fading", "## Fading (slipping under the short window)"),
        ("sustained", "## Sustained (top-ranked in both)"),
    ):
        bucket_rows = [r for r in result["candidates"] if r["momentum"] == bucket][:top]
        if not bucket_rows:
            continue
        lines += ["", label]
        for row in bucket_rows:
            age = f"{row['age_days']}d old" if row["age_days"] is not None else "undated"
            lines.append(
                f"- [{row['source']}] {row['title']} - rank {row['rank_long']}->{row['rank_short']} "
                f"(score {row['score_long']}->{row['score_short']}, {age})"
            )
    return "\n".join(lines)


def run(args: Any, config: dict[str, Any]) -> int:
    """CLI glue for the `momentum` topic-word dispatch."""
    cache_path = report_cache_path()
    if cache_path is None or not cache_path.exists():
        sys.stderr.write(
            "No saved report cache found - run a research pass first "
            "(e.g. `last30days.py \"<topic>\" --days 30`), then re-run `momentum`.\n"
        )
        return 2
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        reports = [
            item.get("report") or {}
            for item in payload.get("reports") or []
            if isinstance(item, dict)
        ]
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(
            f"[momentum] could not read report cache {cache_path}: {type(exc).__name__}: {exc}\n"
        )
        return 2
    if not reports:
        sys.stderr.write(
            f"[momentum] report cache {cache_path} carries no reports - re-run research first.\n"
        )
        return 2

    # Default only when the flag is absent: an explicit falsy value such as
    # ``--days 0`` must reach the positivity guard, not be silently promoted
    # to the default window.
    lookback_days = getattr(args, "lookback_days", None)
    days = DEFAULT_SHORT_DAYS if lookback_days is None else int(lookback_days)
    if days < 1:
        sys.stderr.write(
            "--days must be a positive number of days for the short window\n"
        )
        return 2
    as_of_arg = getattr(args, "as_of_date", None)
    emit_json = getattr(args, "emit", None) == "json"

    results = []
    for report in reports:
        as_of = (
            date.fromisoformat(str(as_of_arg)) if as_of_arg else None
        )
        results.append(analyze_report(report, days=days, as_of=as_of))

    # Machine output must be a single JSON document: the result object for a
    # single report, a JSON array for comparison/competitor caches with
    # multiple reports. Concatenated documents are unparseable.
    if emit_json:
        if len(results) == 1:
            print(json.dumps(results[0], indent=2))
        else:
            print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(render_markdown(result))
    return 0
