#!/usr/bin/env python3
"""Run the v3 verification bundle for last30days."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

SMOKE_TOPIC = "openclaw skills"
SMOKE_CASES = [
    ("gemini", ["--quick", "--search=grounding,hackernews"]),
    ("openai", ["--quick", "--search=reddit,hackernews"]),
    ("xai", ["--quick", "--search=reddit,hackernews"]),
    ("auto", ["--quick", "--search=reddit,grounding,hackernews"]),
]

LATENCY_TOPICS = [
    "openclaw skills",
    "codex vs claude code",
    "anthropic odds",
]
LATENCY_PROFILES = [
    ("quick", ["--quick", "--search=grounding,hackernews"]),
    ("default", ["--search=grounding,hackernews"]),
    ("deep", ["--deep", "--search=grounding,hackernews"]),
]


def run_command(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )


def verify_unit() -> dict[str, str]:
    run_command([PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], timeout=600)
    run_command(
        [
            PYTHON,
            "-m",
            "py_compile",
            *subprocess.run(
                ["rg", "--files", "scripts", "tests", "-g", "*.py", "-g", "!scripts/lib/vendor/**"],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.split(),
        ],
        timeout=600,
    )
    return {"status": "ok"}


def verify_diagnose() -> dict[str, object]:
    result = run_command([PYTHON, "scripts/last30days.py", "--diagnose"], timeout=120)
    return json.loads(result.stdout)


def verify_smoke() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for provider, extra in SMOKE_CASES:
        env = os.environ.copy()
        env["LAST30DAYS_REASONING_PROVIDER"] = provider
        start = time.time()
        result = run_command(
            [PYTHON, "scripts/last30days.py", SMOKE_TOPIC, "--emit=json", *extra],
            env=env,
            timeout=240,
        )
        duration = round(time.time() - start, 2)
        report = json.loads(result.stdout)
        rows.append(
            {
                "provider": provider,
                "duration_seconds": duration,
                "reasoning_provider": (report.get("provider_runtime") or {}).get("reasoning_provider"),
                "cluster_count": len(report.get("clusters") or []),
                "candidate_count": len(report.get("ranked_candidates") or []),
                "error_sources": sorted((report.get("errors_by_source") or {}).keys()),
            }
        )
    return rows


def verify_latency() -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for profile, extra in LATENCY_PROFILES:
        timings = []
        for topic in LATENCY_TOPICS:
            start = time.time()
            run_command(
                [PYTHON, "scripts/last30days.py", topic, "--emit=json", *extra],
                timeout=300,
            )
            timings.append(time.time() - start)
        results[profile] = {
            "times": [round(value, 2) for value in timings],
            "median_seconds": round(statistics.median(timings), 2),
            "max_seconds": round(max(timings), 2),
        }
    return results


def verify_eval(
    *,
    baseline: str,
    candidate: str,
    output_dir: str,
    quick: bool,
    limit: int,
    timeout: int,
) -> dict[str, object]:
    cmd = [
        PYTHON,
        "scripts/evaluate_search_quality.py",
        f"--baseline={baseline}",
        f"--candidate={candidate}",
        f"--output-dir={output_dir}",
        f"--limit={limit}",
        f"--timeout={timeout}",
    ]
    if quick:
        cmd.append("--quick")
    run_command(cmd, timeout=max(timeout * 8, 600))
    output = Path(output_dir)
    metrics = json.loads((output / "metrics.json").read_text())
    summary = (output / "summary.md").read_text()
    return {"metrics": metrics, "summary": summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the v3 verification bundle")
    parser.add_argument("--skip-eval", action="store_true", help="Skip the judged evaluator")
    parser.add_argument("--skip-latency", action="store_true", help="Skip live latency sampling")
    parser.add_argument("--baseline", default="HEAD~1")
    parser.add_argument("--candidate", default="WORKTREE")
    parser.add_argument("--output-dir", default="/tmp/last30days-v3-verify")
    parser.add_argument("--quick-eval", action="store_true", help="Use evaluator quick mode")
    parser.add_argument("--eval-limit", type=int, default=20)
    parser.add_argument("--eval-timeout", type=int, default=240)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary: dict[str, object] = {}
    summary["unit"] = verify_unit()
    summary["diagnose"] = verify_diagnose()
    summary["smoke"] = verify_smoke()
    if not args.skip_latency:
        summary["latency"] = verify_latency()
    if not args.skip_eval:
        summary["eval"] = verify_eval(
            baseline=args.baseline,
            candidate=args.candidate,
            output_dir=args.output_dir,
            quick=args.quick_eval,
            limit=args.eval_limit,
            timeout=args.eval_timeout,
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
