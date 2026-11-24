#!/usr/bin/env python3
"""Topic watchlist management for last30days."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
import os
import secrets
import subprocess
import sys
import time
import tempfile
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import store
from lib import env as envlib, health, http, public_host, schema, usage

_parse_budget = store.parse_budget


# --- Webhook Delivery Functions ---

_RESERVED_WEBHOOK_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".arpa",
)


def _refuse_webhook_redirect(_url: str) -> bool:
    """Webhooks must never follow 3xx redirects."""
    return False


def _validate_webhook_url(url: str) -> urllib.parse.ParseResult:
    """Require a public https:// webhook URL with no userinfo or non-public literals.

    DNS resolution and IP pinning happen fresh at connect time inside
    ``http.post_raw(..., public_only=True)`` so offline configuration stays
    network-free and DNS rebinding cannot bypass the check.
    """
    err = "delivery_channel must be a public https:// URL"
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError(err)
    if any(ch.isspace() or ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in url):
        raise ValueError(err)
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        raise ValueError(err) from None
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(err)
    if "\\" in parsed.netloc or "%" in (parsed.hostname or ""):
        raise ValueError(err)
    if parsed.scheme.lower() != "https":
        raise ValueError(err)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(err)

    raw_host = parsed.hostname or ""
    if not raw_host:
        raise ValueError(err)

    bare_literal = raw_host.rstrip(".").lower()
    if ":" in bare_literal:
        ip = public_host._parse_ip_literal(bare_literal)
        if ip is None or not public_host._is_public_ip(ip):
            raise ValueError(err)
    else:
        try:
            ascii_host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError(err) from None
        bare_host = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
        if not bare_host or ".." in bare_host:
            raise ValueError(err)
        ip = public_host._parse_ip_literal(bare_host)
        if ip is not None:
            if not public_host._is_public_ip(ip):
                raise ValueError(err)
        else:
            if "." not in bare_host:
                raise ValueError(err)
            if bare_host == "localhost" or bare_host.endswith(_RESERVED_WEBHOOK_SUFFIXES):
                raise ValueError(err)

    if not public_host.is_public_address_syntax(url):
        raise ValueError(err)
    return parsed


def _deliver_findings(topic_name: str, counts: dict, *, degraded: bool = False) -> None:
    """Send webhook notification if delivery is configured and there are new findings."""
    channel = store.get_setting("delivery_channel", "")
    if not channel or counts.get("new", 0) == 0:
        return
    
    mode = store.get_setting("delivery_mode", "announce")
    message = _format_delivery_message(topic_name, counts, mode)
    if degraded:
        message += "\nResearch completed with degraded coverage."
    
    # Require a public https:// host before routing. Match Slack on the exact
    # normalized hostname rather than a substring.
    try:
        parsed = urllib.parse.urlparse(channel)
        _ = parsed.port
    except ValueError as error:
        print(f"Delivery failed: {type(error).__name__}", file=sys.stderr)
        return
    try:
        _validate_webhook_url(channel)
    except ValueError:
        print("Delivery skipped: delivery_channel must be a public https:// URL", file=sys.stderr)
        return
    try:
        if (parsed.hostname or "").rstrip(".").lower() == "hooks.slack.com":
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
    _validate_webhook_url(url)
    http.post_raw(
        url,
        json_data={"text": text},
        timeout=10,
        retries=1,
        sensitive_url=True,
        redirect_guard=_refuse_webhook_redirect,
        public_only=True,
        decode_errors="replace",
    )


def _send_generic_webhook(url: str, text: str) -> None:
    """POST JSON payload to generic webhook."""
    _validate_webhook_url(url)
    http.post_raw(
        url,
        json_data={
            "message": text,
            "source": "last30days",
            "timestamp": time.time(),
        },
        timeout=10,
        retries=1,
        sensitive_url=True,
        redirect_guard=_refuse_webhook_redirect,
        public_only=True,
        decode_errors="replace",
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


def _read_daily_budget() -> tuple[float | None, str | None]:
    raw_budget = store.get_setting("daily_budget", "5.00")
    try:
        return _parse_budget(raw_budget), None
    except ValueError as exc:
        return None, str(exc)


def cmd_list(args):
    del args
    topics = store.list_topics()
    budget_used, budget_unknown_runs = store.get_daily_usage()
    budget_limit, budget_error = _read_daily_budget()
    payload = {
        "topics": topics,
        "budget_used": budget_used,
        "budget_limit": budget_limit,
        "budget_unknown_runs": budget_unknown_runs,
    }
    if budget_error is not None:
        payload["budget_error"] = budget_error
    print(json.dumps(payload, default=str))


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

    budget_limit, budget_error = _read_daily_budget()
    results = []
    for topic in topics:
        results.append(_budget_skip(topic) or _run_topic(topic))

    budget_used, budget_unknown_runs = store.get_daily_usage()
    payload = {
        "action": "run_all",
        "results": results,
        "budget_used": budget_used,
        "budget_limit": budget_limit,
        "budget_unknown_runs": budget_unknown_runs,
    }
    if budget_error is not None:
        payload["budget_error"] = budget_error
    print(json.dumps(payload, default=str))


def _budget_skip(topic: dict) -> dict | None:
    limit, budget_error = _read_daily_budget()
    if limit is None:
        return {
            "topic": topic["name"],
            "status": "skipped",
            "reason": (
                f"Invalid daily_budget setting ({budget_error}); "
                "refusing to run until reconfigured"
            ),
        }
    cost, unknown = store.get_daily_usage()
    if cost >= limit:
        reason = f"Budget exceeded: ${cost:.2f}/${limit:.2f}"
    elif unknown:
        reason = f"Budget unknown: {unknown} run(s) today have unreported provider charges"
    else:
        return None
    return {"topic": topic["name"], "status": "skipped", "reason": reason}


def _has_unknown_cost_marker(stderr: object, nonce: str | None = None) -> bool:
    if not stderr:
        return False
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    elif isinstance(stderr, str):
        text = stderr
    else:
        return False
    return usage.unknown_cost_marker(nonce) in text


def _run_topic(topic: dict) -> dict:
    topic_id = topic["id"]
    with tempfile.TemporaryDirectory(prefix="last30days-usage-") as temp_dir:
        journal = Path(temp_dir) / "usage.db"
        usage.create_journal(journal)
        usage_nonce = secrets.token_hex(8)
        # A scheduled run: the logged-in Reddit session stays off (its Keychain
        # prompt cannot be answered unattended), and --no-browser-cookies below
        # keeps every cookie read off besides.
        child_env = dict(os.environ, **{
            usage.JOURNAL_ENV: str(journal),
            usage.NONCE_ENV: usage_nonce,
            "LAST30DAYS_STORE": "0",
            "LAST30DAYS_SCHEDULED_RUN": "1",
        })
        run_id = store.record_run(topic_id, source_mode="v3", status="running")
        unknown_cost_flag = [False]
        try:
            result = _research_topic(
                topic,
                run_id,
                child_env,
                unknown_cost_flag=unknown_cost_flag,
                usage_nonce=usage_nonce,
            )
        finally:
            costs = usage.read_journal(journal)
            if unknown_cost_flag[0]:
                costs["cost_unknown"] = 1
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


def _research_topic(
    topic: dict,
    run_id: int,
    child_env: dict,
    *,
    unknown_cost_flag: list[bool] | None = None,
    usage_nonce: str | None = None,
) -> dict:
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
    except subprocess.TimeoutExpired as exc:
        if unknown_cost_flag is not None and _has_unknown_cost_marker(exc.stderr, usage_nonce):
            unknown_cost_flag[0] = True
        duration = time.time() - start_time
        store.update_run(
            run_id,
            status="failed",
            error_message="Research timed out after 300s",
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": "timeout"}
    except json.JSONDecodeError as exc:
        store.update_run(
            run_id,
            status="failed",
            error_message=f"Invalid saved search queries: {exc}",
            duration_seconds=time.time() - start_time,
        )
        return {"topic": topic["name"], "status": "failed", "error": f"parse error: {exc}"}

    if unknown_cost_flag is not None and _has_unknown_cost_marker(result.stderr, usage_nonce):
        unknown_cost_flag[0] = True

    duration = time.time() - start_time
    degraded = result.returncode == health.STRICT_EXIT_CODE and any(
        line.startswith(health.STRICT_EXIT_PREFIX) and line.removeprefix(health.STRICT_EXIT_PREFIX).strip()
        for line in result.stderr.split("\n")
    )
    if result.returncode != 0 and not degraded:
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
    status = health.DEGRADED if degraded else "completed"
    store.update_run(
        run_id,
        status=status,
        duration_seconds=duration,
        findings_new=counts["new"],
        findings_updated=counts["updated"],
    )

    # Deliver webhook notification if configured
    _deliver_findings(topic["name"], counts, degraded=degraded)

    return {
        "topic": topic["name"],
        "status": status,
        "new": counts["new"],
        "updated": counts["updated"],
        "duration": duration,
    }


def cmd_config(args):
    if args.key == "budget":
        try:
            value = _parse_budget(args.value)
        except ValueError:
            raise SystemExit("daily_budget must be a finite non-negative number")
        canonical = format(Decimal(str(value)), "f")
        store.set_setting("daily_budget", canonical)
        print(json.dumps({"action": "config", "key": "daily_budget", "value": canonical}))
        return
    if args.key == "delivery":
        value = str(args.value)
        # Reject invalid, non-https, or private webhook channels at write time
        # so the operator gets immediate feedback. Matches _deliver_findings.
        if value:
            try:
                _validate_webhook_url(value)
            except ValueError:
                raise SystemExit("delivery_channel must be a public https:// URL")
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
