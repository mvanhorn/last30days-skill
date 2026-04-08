#!/usr/bin/env python3
"""E2E comparison: run sample queries on both v3 (current branch) and v2.9.5 (main).

Usage:
    python3 tests/e2e_comparison.py [--v2-script PATH]

Outputs a markdown comparison table with per-query metrics.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
V3_SCRIPT = str(REPO / "scripts" / "last30days.py")

# v2.9.5 from plugin cache (main branch equivalent)
V2_SCRIPT = str(
    Path.home()
    / ".claude/plugins/cache/last30days/last30days/2.9.5/scripts/last30days.py"
)

EVAL_TOPICS_FILE = REPO / "fixtures" / "eval_topics.json"


def _load_queries() -> list[tuple[str, str]]:
    if EVAL_TOPICS_FILE.exists():
        rows = json.loads(EVAL_TOPICS_FILE.read_text())
        return [(row["topic"], row["query_type"]) for row in rows]
    return [
        ("openclaw vs nanoclaw vs ironclaw", "comparison"),
        ("how to deploy on Fly.io", "how_to"),
        ("kanye west", "breaking_news"),
        ("odds of recession", "prediction"),
        ("explain transformer architecture", "concept"),
    ]


QUERIES = _load_queries()


def run_query(script: str, topic: str, timeout: int = 180) -> dict:
    """Run a query and return parsed JSON + timing."""
    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, script, topic, "--emit=json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            return {
                "error": result.stderr[:200],
                "elapsed": elapsed,
                "sources": 0,
                "candidates": 0,
                "intent": "error",
                "subqueries": 0,
            }
        data = json.loads(result.stdout)

        # v3 shape
        if "query_plan" in data:
            items_by_source = data.get("items_by_source", {})
            return {
                "elapsed": elapsed,
                "sources": sum(1 for v in items_by_source.values() if v),
                "total_items": sum(len(v) for v in items_by_source.values()),
                "candidates": len(data.get("ranked_candidates", [])),
                "clusters": len(data.get("clusters", [])),
                "intent": data["query_plan"].get("intent", "?"),
                "subqueries": len(data["query_plan"].get("subqueries", [])),
                "errors": list(data.get("errors_by_source", {}).keys()),
            }

        # v2 shape
        sources_with_items = 0
        total_items = 0
        for key in ["reddit", "x", "youtube", "tiktok", "instagram", "hackernews",
                     "bluesky", "truthsocial", "polymarket", "web"]:
            items = data.get(key, [])
            if items:
                sources_with_items += 1
                total_items += len(items)
        return {
            "elapsed": elapsed,
            "sources": sources_with_items,
            "total_items": total_items,
            "candidates": total_items,
            "clusters": 0,
            "intent": data.get("mode", "?"),
            "subqueries": 0,
            "errors": [k for k in ["reddit_error", "x_error", "youtube_error",
                                     "tiktok_error", "instagram_error"]
                        if data.get(k)],
        }
    except subprocess.TimeoutExpired:
        return {
            "error": "timeout",
            "elapsed": timeout,
            "sources": 0,
            "candidates": 0,
            "intent": "timeout",
            "subqueries": 0,
        }
    except Exception as exc:
        return {
            "error": str(exc)[:200],
            "elapsed": time.time() - start,
            "sources": 0,
            "candidates": 0,
            "intent": "error",
            "subqueries": 0,
        }


def main():
    v2_script = V2_SCRIPT
    if len(sys.argv) > 2 and sys.argv[1] == "--v2-script":
        v2_script = sys.argv[2]

    if not Path(v2_script).exists():
        print(f"v2 script not found at {v2_script}", file=sys.stderr)
        print("Use --v2-script PATH to specify", file=sys.stderr)
        sys.exit(1)

    print("# E2E Comparison: v3.0.0 (branch) vs v2.9.5 (main)")
    print()
    print(f"- v3 script: {V3_SCRIPT}")
    print(f"- v2 script: {v2_script}")
    print(f"- Queries: {len(QUERIES)}")
    print()

    results = []
    for i, (topic, expected_intent) in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] {topic}", file=sys.stderr)
        sys.stderr.flush()

        print(f"  v3...", end="", file=sys.stderr)
        sys.stderr.flush()
        v3 = run_query(V3_SCRIPT, topic)
        print(f" {v3.get('elapsed', 0):.1f}s", file=sys.stderr)
        sys.stderr.flush()

        print(f"  v2...", end="", file=sys.stderr)
        sys.stderr.flush()
        v2 = run_query(v2_script, topic)
        print(f" {v2.get('elapsed', 0):.1f}s", file=sys.stderr)
        sys.stderr.flush()

        results.append({
            "topic": topic,
            "expected_intent": expected_intent,
            "v3": v3,
            "v2": v2,
        })

    # Print comparison table
    print("| Query | Intent | v3 sources | v2 sources | v3 items | v2 items | v3 time | v2 time | v3 errors | v2 errors |")
    print("|-------|--------|-----------|-----------|---------|---------|---------|---------|-----------|-----------|")
    for r in results:
        v3, v2 = r["v3"], r["v2"]
        v3_err = ", ".join(v3.get("errors", [])) or "-"
        v2_err = ", ".join(v2.get("errors", [])) or "-"
        print(
            f"| {r['topic'][:45]} | {v3.get('intent', '?')} | "
            f"{v3.get('sources', 0)} | {v2.get('sources', 0)} | "
            f"{v3.get('total_items', 0)} | {v2.get('total_items', 0)} | "
            f"{v3.get('elapsed', 0):.1f}s | {v2.get('elapsed', 0):.1f}s | "
            f"{v3_err} | {v2_err} |"
        )

    # Summary
    print()
    v3_total_sources = sum(r["v3"].get("sources", 0) for r in results)
    v2_total_sources = sum(r["v2"].get("sources", 0) for r in results)
    v3_total_items = sum(r["v3"].get("total_items", 0) for r in results)
    v2_total_items = sum(r["v2"].get("total_items", 0) for r in results)
    v3_total_time = sum(r["v3"].get("elapsed", 0) for r in results)
    v2_total_time = sum(r["v2"].get("elapsed", 0) for r in results)
    v3_errors = sum(len(r["v3"].get("errors", [])) for r in results)
    v2_errors = sum(len(r["v2"].get("errors", [])) for r in results)

    print("## Summary")
    print()
    print(f"| Metric | v3.0.0 | v2.9.5 | Delta |")
    print(f"|--------|--------|--------|-------|")
    print(f"| Total sources with items | {v3_total_sources} | {v2_total_sources} | {v3_total_sources - v2_total_sources:+d} |")
    print(f"| Total items retrieved | {v3_total_items} | {v2_total_items} | {v3_total_items - v2_total_items:+d} |")
    print(f"| Total wall time | {v3_total_time:.1f}s | {v2_total_time:.1f}s | {v3_total_time - v2_total_time:+.1f}s |")
    print(f"| Source errors | {v3_errors} | {v2_errors} | {v3_errors - v2_errors:+d} |")
    print(f"| Avg sources/query | {v3_total_sources/len(results):.1f} | {v2_total_sources/len(results):.1f} | |")
    print(f"| Avg items/query | {v3_total_items/len(results):.1f} | {v2_total_items/len(results):.1f} | |")
    print(f"| Avg time/query | {v3_total_time/len(results):.1f}s | {v2_total_time/len(results):.1f}s | |")


if __name__ == "__main__":
    main()
