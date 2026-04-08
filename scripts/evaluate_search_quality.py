#!/usr/bin/env python3
"""Compare two last30days revisions on the v3 ranked candidate output."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))

from lib import env as envlib
from lib import schema


REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_TOPICS_FILE = REPO_ROOT / "fixtures" / "eval_topics.json"


def _load_default_topics() -> list[tuple[str, str]]:
    if EVAL_TOPICS_FILE.exists():
        rows = json.loads(EVAL_TOPICS_FILE.read_text())
        return [(row["topic"], row["query_type"]) for row in rows]
    return [
        ("nano banana pro prompting", "product"),
        ("codex vs claude code", "comparison"),
        ("openclaw vs nanoclaw vs ironclaw", "comparison"),
        ("anthropic odds", "prediction"),
        ("kanye west", "breaking_news"),
        ("remotion animations for Claude Code", "how_to"),
    ]


DEFAULT_TOPICS = _load_default_topics()
DEFAULT_SEARCH = ""
DEFAULT_JUDGE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"


def stable_item_key(item: dict[str, Any]) -> str:
    return str(item.get("candidate_id") or item.get("url") or item.get("title") or "")


def row_sources(row: dict[str, Any]) -> list[str]:
    candidate = schema.candidate_from_dict(row)
    return schema.candidate_sources(candidate)


def row_best_date(row: dict[str, Any]) -> str | None:
    candidate = schema.candidate_from_dict(row)
    return schema.candidate_best_published_at(candidate)


V2_SOURCE_KEYS = [
    ("reddit", "title"),
    ("x", "text"),
    ("youtube", "title"),
    ("tiktok", "text"),
    ("instagram", "text"),
    ("hackernews", "title"),
    ("bluesky", "text"),
    ("truthsocial", "text"),
    ("polymarket", "question"),
    ("web", "title"),
]


def build_ranked_items(report: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    # v3 format: ranked_candidates list
    if report.get("ranked_candidates"):
        ranked = []
        for row in report["ranked_candidates"][:limit]:
            candidate_sources = row_sources(row)
            ranked.append({
                "key": stable_item_key(row),
                "source": ", ".join(candidate_sources),
                "sources": candidate_sources,
                "url": str(row.get("url") or ""),
                "text": str(row.get("title") or ""),
                "date": row_best_date(row),
                "score": float(row.get("final_score") or 0.0),
            })
        return ranked

    # v2 format: per-source lists (reddit, x, youtube, etc.)
    all_items = []
    for source_key, text_field in V2_SOURCE_KEYS:
        for item in report.get(source_key) or []:
            if not isinstance(item, dict):
                continue
            all_items.append({
                "key": str(item.get("url") or item.get("id") or item.get(text_field) or ""),
                "source": source_key,
                "sources": [source_key],
                "url": str(item.get("url") or ""),
                "text": str(item.get(text_field) or item.get("title") or ""),
                "date": item.get("date"),
                "score": float(item.get("score") or 0.0),
            })
    all_items.sort(key=lambda x: x["score"], reverse=True)
    return all_items[:limit]


def source_sets(report: dict[str, Any], limit: int) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for item in build_ranked_items(report, limit):
        for source in item["sources"]:
            grouped.setdefault(source, set()).add(item["key"])
    return grouped


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def retention(left: set[str], right: set[str]) -> float:
    if not left:
        return 1.0
    return len(left & right) / len(left)


def precision_at_k(ranking: list[dict[str, Any]], judgments: dict[str, int], k: int) -> float:
    top = ranking[:k]
    if not top:
        return 0.0
    return sum(1 for item in top if judgments.get(item["key"], 0) >= 2) / len(top)


def ndcg_at_k(ranking: list[dict[str, Any]], judgments: dict[str, int], k: int, judged_pool: list[dict[str, Any]]) -> float:
    top = ranking[:k]
    if not top:
        return 0.0

    def dcg(grades: list[int]) -> float:
        total = 0.0
        for index, grade in enumerate(grades, start=1):
            total += (2**grade - 1) / math.log2(index + 1)
        return total

    actual = [judgments.get(item["key"], 0) for item in top]
    ideal = sorted((judgments.get(item["key"], 0) for item in judged_pool), reverse=True)[: len(top)]
    ideal_score = dcg(ideal)
    if ideal_score == 0:
        return 0.0
    return dcg(actual) / ideal_score


def source_coverage_recall(ranking: list[dict[str, Any]], judged_pool: list[dict[str, Any]], judgments: dict[str, int]) -> float:
    good_sources = {
        source
        for item in judged_pool
        if judgments.get(item["key"], 0) >= 2
        for source in item["sources"]
    }
    if not good_sources:
        return 1.0
    hit_sources = {
        source
        for item in ranking
        if judgments.get(item["key"], 0) >= 2
        for source in item["sources"]
    }
    return len(hit_sources & good_sources) / len(good_sources)


def resolve_google_judge_api_key(config: dict[str, Any]) -> str | None:
    return (
        os.environ.get("GOOGLE_API_KEY")
        or config.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or config.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_GENAI_API_KEY")
        or config.get("GOOGLE_GENAI_API_KEY")
    )


def extract_gemini_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if part.get("text"):
                return part["text"]
    raise ValueError("Gemini response did not contain text.")


def call_gemini_judge(api_key: str, model: str, prompt: str) -> dict[str, Any]:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    request = Request(
        GEMINI_API_URL.format(model=model, api_key=api_key),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc
    return json.loads(extract_gemini_text(payload))


def build_judge_prompt(topic: str, query_type: str, items: list[dict[str, Any]]) -> str:
    item_lines = []
    for item in items:
        item_lines.append(
            "\n".join([
                f"- id: {item['key']}",
                f"  source: {item['source']}",
                f"  title: {item['text'][:220]}",
                f"  url: {item['url']}",
                f"  date: {item.get('date') or 'unknown'}",
            ])
        )
    return f"""
Judge search-result relevance for a last-30-days research tool.

Topic: {topic}
Query type: {query_type}

Score each item on this 0-3 scale:
- 0 = off-topic or clearly bad
- 1 = weak or tangential
- 2 = relevant and useful
- 3 = highly relevant, one of the best results

Return JSON only:
{{
  "judgments": [
    {{"id": "ITEM_ID", "grade": 0}}
  ]
}}

Items:
{chr(10).join(item_lines)}
""".strip()


def get_judgments(
    *,
    output_dir: Path,
    slug: str,
    topic: str,
    query_type: str,
    items: list[dict[str, Any]],
    judge_model: str,
    gemini_api_key: str | None,
) -> dict[str, int]:
    cache_file = output_dir / "judgments" / f"{slug}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists():
        payload = json.loads(cache_file.read_text())
        return {row["id"]: int(row["grade"]) for row in payload.get("judgments") or []}
    if not gemini_api_key or not items:
        return {}
    payload = call_gemini_judge(gemini_api_key, judge_model, build_judge_prompt(topic, query_type, items))
    cache_file.write_text(json.dumps(payload, indent=2))
    return {row["id"]: int(row["grade"]) for row in payload.get("judgments") or []}


def create_eval_env() -> dict[str, str]:
    config = envlib.get_config()
    passthrough = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", ""),
        "TMPDIR": os.environ.get("TMPDIR", ""),
        "PYTHONUTF8": "1",
        "LAST30DAYS_CONFIG_DIR": "",
    }
    for key in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "SCRAPECREATORS_API_KEY",
        "BSKY_HANDLE",
        "BSKY_APP_PASSWORD",
        "TRUTHSOCIAL_TOKEN",
        "AUTH_TOKEN",
        "CT0",
    ):
        value = os.environ.get(key) or config.get(key)
        if value:
            passthrough[key] = value
    return passthrough


def run_last30days(repo_dir: Path, topic: str, *, search: str, timeout_seconds: int, quick: bool, mock: bool, env: dict[str, str]) -> dict[str, Any]:
    cmd = [sys.executable, "scripts/last30days.py", topic, "--emit=json"]
    if search:
        cmd.extend(["--search", search])
    if quick:
        cmd.append("--quick")
    if mock:
        cmd.append("--mock")
    result = subprocess.run(
        cmd,
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{repo_dir.name} failed for '{topic}' with exit {result.returncode}\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def create_worktree(rev: str) -> Path:
    worktree_dir = Path(tempfile.mkdtemp(prefix="last30days-eval-"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir), rev],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_dir


def resolve_repo_dir(label: str) -> tuple[Path, bool]:
    """Resolve a benchmark label into a repo directory and whether it is temporary."""
    if label == "WORKTREE":
        return REPO_ROOT, False
    return create_worktree(label), True


def remove_worktree(path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        os.rmdir(path)
    except OSError:
        pass


def summarize_topic(topic: str, query_type: str, baseline_report: dict[str, Any], candidate_report: dict[str, Any], judgments: dict[str, int], judged_pool: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    baseline_ranked = build_ranked_items(baseline_report, limit)
    candidate_ranked = build_ranked_items(candidate_report, limit)
    baseline_sets = source_sets(baseline_report, limit)
    candidate_sets = source_sets(candidate_report, limit)
    overall_left = set().union(*baseline_sets.values()) if baseline_sets else set()
    overall_right = set().union(*candidate_sets.values()) if candidate_sets else set()
    sources = sorted(set(baseline_sets) | set(candidate_sets))
    return {
        "topic": topic,
        "query_type": query_type,
        "baseline": {
            "precision_at_5": precision_at_k(baseline_ranked, judgments, 5),
            "ndcg_at_5": ndcg_at_k(baseline_ranked, judgments, 5, judged_pool),
            "source_coverage_recall": source_coverage_recall(baseline_ranked, judged_pool, judgments),
        },
        "candidate": {
            "precision_at_5": precision_at_k(candidate_ranked, judgments, 5),
            "ndcg_at_5": ndcg_at_k(candidate_ranked, judgments, 5, judged_pool),
            "source_coverage_recall": source_coverage_recall(candidate_ranked, judged_pool, judgments),
        },
        "stability": {
            "overall_jaccard": jaccard(overall_left, overall_right),
            "overall_retention_vs_baseline": retention(overall_left, overall_right),
            "per_source": {
                source: {
                    "baseline_count": len(baseline_sets.get(source, set())),
                    "candidate_count": len(candidate_sets.get(source, set())),
                    "jaccard": jaccard(baseline_sets.get(source, set()), candidate_sets.get(source, set())),
                    "retention_vs_baseline": retention(baseline_sets.get(source, set()), candidate_sets.get(source, set())),
                }
                for source in sources
            },
        },
    }


def write_summary(output_dir: Path, baseline_label: str, candidate_label: str, summaries: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline": baseline_label,
        "candidate": candidate_label,
        "topics": summaries,
    }
    (output_dir / "metrics.json").write_text(json.dumps(payload, indent=2))

    lines = [
        "# Search Quality Evaluation",
        "",
        f"- Baseline: `{baseline_label}`",
        f"- Candidate: `{candidate_label}`",
        f"- Generated: {payload['generated_at']}",
        "",
        "| Topic | Base P@5 | Cand P@5 | Base nDCG@5 | Cand nDCG@5 | Jaccard | Retention |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {topic} | {bp:.2f} | {cp:.2f} | {bn:.2f} | {cn:.2f} | {jac:.2f} | {ret:.2f} |".format(
                topic=row["topic"],
                bp=row["baseline"]["precision_at_5"],
                cp=row["candidate"]["precision_at_5"],
                bn=row["baseline"]["ndcg_at_5"],
                cn=row["candidate"]["ndcg_at_5"],
                jac=row["stability"]["overall_jaccard"],
                ret=row["stability"]["overall_retention_vs_baseline"],
            )
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n")


def write_failure_summary(
    output_dir: Path,
    baseline_label: str,
    candidate_label: str,
    summaries: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    write_summary(output_dir, baseline_label, candidate_label, summaries)
    metrics_path = output_dir / "metrics.json"
    payload = json.loads(metrics_path.read_text()) if metrics_path.exists() else {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline": baseline_label,
        "candidate": candidate_label,
        "topics": [],
    }
    payload["failures"] = failures
    metrics_path.write_text(json.dumps(payload, indent=2))

    summary_path = output_dir / "summary.md"
    lines = summary_path.read_text().splitlines() if summary_path.exists() else ["# Search Quality Evaluation", ""]
    if failures:
        lines.extend([
            "",
            "## Failures",
            "",
        ])
        for failure in failures:
            lines.append(f"- `{failure['topic']}`: {failure['error']}")
    summary_path.write_text("\n".join(lines).rstrip() + "\n")


def parse_topics_file(path: Path) -> list[tuple[str, str]]:
    rows = json.loads(path.read_text())
    return [(str(row["topic"]), str(row.get("query_type") or "general")) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two last30days revisions on ranked candidate quality")
    parser.add_argument("--baseline", default="HEAD~1")
    parser.add_argument("--candidate", default="WORKTREE")
    parser.add_argument("--search", default=DEFAULT_SEARCH)
    parser.add_argument("--output-dir", default="tmp/search-quality")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--topics-file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    topics = parse_topics_file(Path(args.topics_file)) if args.topics_file else DEFAULT_TOPICS
    output_dir = Path(args.output_dir).resolve()
    config = envlib.get_config()
    gemini_api_key = resolve_google_judge_api_key(config)
    run_env = create_eval_env()

    baseline_dir, baseline_temp = resolve_repo_dir(args.baseline)
    candidate_dir, candidate_temp = resolve_repo_dir(args.candidate)
    try:
        summaries = []
        failures = []
        for topic, query_type in topics:
            try:
                baseline_report = run_last30days(
                    baseline_dir,
                    topic,
                    search=args.search,
                    timeout_seconds=args.timeout,
                    quick=args.quick,
                    mock=args.mock,
                    env=run_env,
                )
                candidate_report = run_last30days(
                    candidate_dir,
                    topic,
                    search=args.search,
                    timeout_seconds=args.timeout,
                    quick=args.quick,
                    mock=args.mock,
                    env=run_env,
                )
                judged_pool_map = {
                    item["key"]: item
                    for item in build_ranked_items(baseline_report, args.limit) + build_ranked_items(candidate_report, args.limit)
                }
                judged_pool = list(judged_pool_map.values())
                judgments = get_judgments(
                    output_dir=output_dir,
                    slug="".join(char.lower() if char.isalnum() else "-" for char in topic).strip("-"),
                    topic=topic,
                    query_type=query_type,
                    items=judged_pool,
                    judge_model=args.judge_model,
                    gemini_api_key=gemini_api_key,
                )
                summaries.append(summarize_topic(topic, query_type, baseline_report, candidate_report, judgments, judged_pool, args.limit))
            except Exception as exc:
                failures.append({"topic": topic, "query_type": query_type, "error": str(exc)})
        write_failure_summary(output_dir, args.baseline, args.candidate, summaries, failures)
    finally:
        if baseline_temp:
            remove_worktree(baseline_dir)
        if candidate_temp:
            remove_worktree(candidate_dir)
    result = {"output_dir": str(output_dir), "topics": len(topics), "failures": len(failures)}
    print(json.dumps(result, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
