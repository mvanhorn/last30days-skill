#!/usr/bin/env python3
"""Topic watchlist management for last30days."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import tempfile
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import store
from lib import env as envlib, http, schema, usage


# --- Webhook Delivery Functions ---

def _deliver_findings(topic_name: str, counts: dict) -> None:
    """Send webhook notification if delivery is configured and there are new findings."""
    channel = store.get_setting("delivery_channel", "")
    if not channel or counts.get("new", 0) == 0:
        return
    
    mode = store.get_setting("delivery_mode", "announce")
    message = _format_delivery_message(topic_name, counts, mode)
    
    # Require https before routing. The old "hooks.slack.com" in channel
    # substring test ran before any scheme check, so a channel like
    # http://evil.example/hooks.slack.com was treated as Slack and POSTed in
    # cleartext to the wrong host. Match Slack on the exact hostname instead.
    try:
        parsed = urllib.parse.urlparse(channel)
        if parsed.scheme != "https":
            print("Delivery skipped: delivery_channel must be an https:// URL", file=sys.stderr)
            return
        if parsed.hostname == "hooks.slack.com":
            _send_slack_webhook(channel, message)
        else:
            _send_generic_webhook(channel, message)
    except http.HTTPError as error:
        detail = f"HTTP {error.status_code}" if error.status_code is not None else error.outcome_state
        print(f"Delivery failed: {detail}", file=sys.stderr)
    except Exception as error:
        print(f"Delivery failed: {type(error).__name__}", file=sys.stderr)


def _format_delivery_message(topic: str, counts: dict, mode: str) -> str:
    """Format notification message based on delivery mode."""
    new = counts.get("new", 0)
    updated = counts.get("updated", 0)
    
    if mode == "announce":
        return f"📰 *last30days update: {topic}*\n{new} new, {updated} updated"
    elif mode == "silent":
        return f"last30days: {new} new findings for '{topic}'"
    else:
        return f"last30days: Research complete for '{topic}'"


def _send_slack_webhook(url: str, text: str) -> None:
    """POST to Slack incoming webhook."""
    http.post(url, json_data={"text": text}, timeout=10, retries=1, sensitive_url=True)


def _send_generic_webhook(url: str, text: str) -> None:
    """POST JSON payload to generic webhook."""
    http.post(
        url,
        json_data={
            "message": text,
            "source": "last30days",
            "timestamp": time.time(),
        },
        timeout=10,
        retries=1,
        sensitive_url=True,
    )


# --- Command Handlers ---

def cmd_add(args):
    schedule = "0 8 * * 1" if args.weekly else (args.schedule or "0 8 * * *")
    queries = [query.strip() for query in (args.queries or "").split(",") if query.strip()] or None
    topic = store.add_topic(args.topic, search_queries=queries, schedule=schedule)
    sched_desc = "weekly (Mondays 8am)" if args.weekly else f"daily ({schedule})"
    print(json.dumps({
        "action": "added",
        "topic": topic["name"],
        "schedule": sched_desc,
        "message": f'Added "{topic["name"]}" to watchlist. Schedule: {sched_desc}.',
    }, default=str))


def cmd_remove(args):
    removed = store.remove_topic(args.topic)
    if not removed:
        print(json.dumps({"action": "not_found", "topic": args.topic, "message": f'Topic not found: "{args.topic}"'}))
        return
    remaining = store.list_topics()
    print(json.dumps({
        "action": "removed",
        "topic": args.topic,
        "message": f'Removed "{args.topic}" from watchlist.',
        "remaining": len(remaining),
    }))


def cmd_list(args):
    del args
    topics = store.list_topics()
    budget_used = store.get_daily_cost()
    budget_limit = float(store.get_setting("daily_budget", "5.00"))
    print(json.dumps({
        "topics": topics,
        "budget_used": budget_used,
        "budget_limit": budget_limit,
        "budget_unknown_runs": store.get_daily_unknown_cost_runs(),
    }, default=str))


def cmd_delta(args):
    topic = store.get_topic(args.topic)
    if not topic:
        print(json.dumps({"error": f'Topic not found: "{args.topic}"'}))
        sys.exit(1)
    print(json.dumps(store.compute_topic_delta(topic["id"]), default=str))


def cmd_run_one(args):
    topic = store.get_topic(args.topic)
    if not topic:
        print(json.dumps({"error": f'Topic not found: "{args.topic}"'}))
        sys.exit(1)
    print(json.dumps(_budget_skip(topic) or _run_topic(topic), default=str))


def cmd_run_all(args):
    del args
    topics = [topic for topic in store.list_topics() if topic["enabled"]]
    if not topics:
        print(json.dumps({"message": "No enabled topics to research."}))
        return

    budget_limit = float(store.get_setting("daily_budget", "5.00"))
    results = []
    for topic in topics:
        results.append(_budget_skip(topic) or _run_topic(topic))

    print(json.dumps({
        "action": "run_all",
        "results": results,
        "budget_used": store.get_daily_cost(),
        "budget_limit": budget_limit,
        "budget_unknown_runs": store.get_daily_unknown_cost_runs(),
    }, default=str))


def _budget_skip(topic: dict) -> dict | None:
    limit = float(store.get_setting("daily_budget", "5.00"))
    cost = store.get_daily_cost()
    unknown = store.get_daily_unknown_cost_runs()
    if cost >= limit:
        reason = f"Budget exceeded: ${cost:.2f}/${limit:.2f}"
    elif unknown:
        reason = f"Budget unknown: {unknown} run(s) today have unreported provider charges"
    else:
        return None
    return {"topic": topic["name"], "status": "skipped", "reason": reason}


def _run_topic(topic: dict) -> dict:
    topic_id = topic["id"]
    with tempfile.TemporaryDirectory(prefix="last30days-usage-") as temp_dir:
        journal = Path(temp_dir) / "usage.db"
        usage.create_journal(journal)
        # A scheduled run: the logged-in Reddit session stays off (its Keychain
        # prompt cannot be answered unattended), and --no-browser-cookies below
        # keeps every cookie read off besides.
        child_env = dict(os.environ, **{
            usage.JOURNAL_ENV: str(journal), "LAST30DAYS_STORE": "0", "LAST30DAYS_SCHEDULED_RUN": "1",
        })
        run_id = store.record_run(topic_id, source_mode="v3", status="running")
        try:
            result = _research_topic(topic, run_id, child_env)
        finally:
            costs = usage.read_journal(journal)
            store.update_run(run_id, **costs)
        result.update(costs)
        return result


def _validate_utf8_text(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"expected {field} to be text")
    value.encode("utf-8")


def _findings_from_output(output: str) -> list[dict]:
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("expected a research report object")
    if payload.get("comparison") is True:
        entries = payload.get("reports")
        if not isinstance(entries, list) or len(entries) < 2:
            raise ValueError("expected at least two reports in a comparison")
        reports = []
        for entry in entries:
            _validate_utf8_text(entry["entity"], "comparison entity")
            reports.append(entry["report"])
    else:
        reports = [payload]
    findings = []
    for raw_report in reports:
        report = schema.report_from_dict(raw_report)
        _validate_utf8_text(report.topic, "report topic")
        findings.extend(store.findings_from_report(report, limit=25))
    for finding in findings:
        for field in ("source", "source_url", "source_title", "author", "content", "summary"):
            _validate_utf8_text(finding[field], f"finding {field}")
        for field in ("engagement_score", "relevance_score"):
            score = finding[field]
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError(f"expected finding {field} to be a finite number")
            finding[field] = float(score)
    return findings


def _research_topic(topic: dict, run_id: int, child_env: dict) -> dict:
    start_time = time.time()
    topic_id = topic["id"]

    try:
        search_queries = json.loads(topic["search_queries"]) if topic.get("search_queries") else None
        search_term = search_queries[0] if search_queries else topic["name"]
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "last30days.py"),
                "--research-only",
                "--emit=json",
                "--json-profile=raw",
                "--require-local-report",
                "--quick",
                "--lookback-days",
                "90",
                # Watchlist is an unattended cron host: never probe browser
                # cookies (matches the MCP server). Avoids a silent Chromium
                # read / unattended macOS Keychain prompt when a user has set
                # FROM_BROWSER=auto for interactive use.
                "--no-browser-cookies",
                "--",
                search_term,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env={**child_env, envlib.ALLOW_ENGINE_PLAN_VAR: "1"},
        )
        duration = time.time() - start_time
        if result.returncode != 0:
            store.update_run(
                run_id,
                status="failed",
                error_message=result.stderr[:500],
                duration_seconds=duration,
            )
            return {
                "topic": topic["name"],
                "status": "failed",
                "error": result.stderr[:200],
                "duration": duration,
            }

        try:
            findings = _findings_from_output(result.stdout)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
            error = f"Invalid research output ({type(exc).__name__})"
            store.update_run(run_id, status="failed", error_message=error, duration_seconds=duration)
            return {"topic": topic["name"], "status": "failed", "error": error, "duration": duration}

        counts = store.store_findings(run_id, topic_id, findings)
        store.update_run(
            run_id,
            status="completed",
            duration_seconds=duration,
            findings_new=counts["new"],
            findings_updated=counts["updated"],
        )
        
        # Deliver webhook notification if configured
        _deliver_findings(topic["name"], counts)
        
        return {
            "topic": topic["name"],
            "status": "completed",
            "new": counts["new"],
            "updated": counts["updated"],
            "duration": duration,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        store.update_run(
            run_id,
            status="failed",
            error_message="Research timed out after 300s",
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": "timeout"}
    except json.JSONDecodeError as exc:
        duration = time.time() - start_time
        store.update_run(
            run_id,
            status="failed",
            error_message=f"Invalid JSON output: {exc}",
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": f"parse error: {exc}"}
def cmd_config(args):
    if args.key == "budget":
        store.set_setting("daily_budget", str(args.value))
        print(json.dumps({"action": "config", "key": "daily_budget", "value": str(args.value)}))
        return
    if args.key == "delivery":
        value = str(args.value)
        # Reject a non-https channel at write time so the operator gets
        # immediate feedback, rather than discovering it via a stderr line
        # buried in a research run hours later. Matches the delivery-time guard
        # in _deliver_findings.
        try:
            valid = not value or urllib.parse.urlparse(value).scheme == "https"
        except ValueError:
            valid = False
        if not valid:
            raise SystemExit("delivery_channel must be an https:// URL")
        store.set_setting("delivery_channel", value)
        print(json.dumps({"action": "config", "key": "delivery_channel", "configured": bool(value)}))
        return
    raise SystemExit(f"Unknown config key: {args.key}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the last30days watchlist")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add")
    add.add_argument("topic")
    add.add_argument("--schedule")
    add.add_argument("--weekly", action="store_true")
    add.add_argument("--queries")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove")
    remove.add_argument("topic")
    remove.set_defaults(func=cmd_remove)

    list_parser = sub.add_parser("list")
    list_parser.set_defaults(func=cmd_list)

    delta = sub.add_parser("delta")
    delta.add_argument("topic")
    delta.set_defaults(func=cmd_delta)

    run_one = sub.add_parser("run-one")
    run_one.add_argument("topic")
    run_one.set_defaults(func=cmd_run_one)

    run_all = sub.add_parser("run-all")
    run_all.set_defaults(func=cmd_run_all)

    config = sub.add_parser("config")
    config.add_argument("key", choices=["delivery", "budget"])
    config.add_argument("value")
    config.set_defaults(func=cmd_config)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
