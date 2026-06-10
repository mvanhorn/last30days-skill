#!/usr/bin/env python3
"""Smoke-test provider paths for last30days social sources."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import env, instagram, pinterest, tiktok, threads  # noqa: E402


DATE_RANGE = ("2026-05-11", "2026-06-10")
DIRECT_CASES = [
    ("tiktok", "Trump Iran", tiktok.search_tiktok),
    ("instagram", "iran", instagram.search_instagram),
    ("threads", "Trump Iran", threads.search_threads),
    ("pinterest", "Trump Iran", pinterest.search_pinterest),
]
FULL_CASES = [
    ("tiktok", "Trump Iran"),
    ("instagram", "iran"),
    ("threads", "Trump Iran"),
    ("pinterest", "Trump Iran"),
]


def _ok(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_direct_cases(config: dict[str, object]) -> list[str]:
    apify_tokens = env.get_apify_tokens(config)
    _ok(bool(apify_tokens), "No Apify tokens configured for direct fallback smoke test")
    passed: list[str] = []
    for source, topic, func in DIRECT_CASES:
        result = func(
            topic,
            DATE_RANGE[0],
            DATE_RANGE[1],
            depth="quick",
            token="",
            apify_tokens=apify_tokens,
        )
        items = result.get("items") or []
        _ok(result.get("provider") == "apify", f"{source}: expected provider=apify, got {result.get('provider')}")
        _ok(len(items) > 0, f"{source}: expected >0 items in direct adapter smoke")
        passed.append(source)
    return passed


def run_full_cases() -> list[str]:
    passed: list[str] = []
    cli = SCRIPT_DIR / "last30days.py"
    for source, topic in FULL_CASES:
        command = [
            sys.executable,
            str(cli),
            "--search",
            source,
            "--topic",
            topic,
            "--quick",
            "--emit=json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(completed.stdout)
        items = (payload.get("items_by_source") or {}).get(source) or []
        errors = payload.get("errors_by_source") or {}
        trace = (payload.get("source_provider_runtime") or {}).get(source) or {}
        _ok(source not in errors, f"{source}: errors_by_source present: {errors.get(source)}")
        _ok(len(items) > 0, f"{source}: expected >0 items in full pipeline smoke")
        _ok(bool(trace.get("actual_provider_used")), f"{source}: missing actual_provider_used in source_provider_runtime")
        passed.append(source)
    return passed


def main() -> int:
    config = env.get_config()
    try:
        direct_passed = run_direct_cases(config)
        full_passed = run_full_cases()
    except Exception as exc:
        sys.stderr.write(f"FAIL: {type(exc).__name__}: {exc}\n")
        return 1

    print(
        json.dumps(
            {
                "status": "PASS",
                "direct_adapter_sources": direct_passed,
                "full_pipeline_sources": full_passed,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
