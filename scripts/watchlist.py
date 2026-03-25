#!/usr/bin/env python3
"""Topic watchlist management for last30days.

CLI for adding, removing, and listing watched topics with auto-bootstrap.
On first `add`, creates the SQLite database.

Usage:
    python3 watchlist.py add "AI video tools" [--schedule "0 8 * * *"]
    python3 watchlist.py add "NVIDIA news" --weekly
    python3 watchlist.py remove "AI video tools"
    python3 watchlist.py list
    python3 watchlist.py run-all
    python3 watchlist.py run-one "AI video tools"
    python3 watchlist.py config delivery telegram
    python3 watchlist.py config budget 10.00
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import store


def cmd_add(args):
    """Add a topic to the watchlist."""
    schedule = "0 8 * * 1" if args.weekly else (args.schedule or "0 8 * * *")
    queries = args.queries.split(",") if args.queries else None

    topic = store.add_topic(args.topic, search_queries=queries, schedule=schedule)

    sched_desc = "weekly (Mondays 8am)" if args.weekly else f"daily ({schedule})"
    result = {
        "action": "added",
        "topic": topic["name"],
        "schedule": sched_desc,
        "message": f'Added "{topic["name"]}" to watchlist. Schedule: {sched_desc}.',
    }

    print(json.dumps(result, default=str))


def cmd_remove(args):
    """Remove a topic from the watchlist."""
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
    """List all watched topics with stats."""
    topics = store.list_topics()
    budget_used = store.get_daily_cost()
    budget_limit = store.get_setting("daily_budget", "5.00")

    result = {
        "topics": topics,
        "budget_used": budget_used,
        "budget_limit": float(budget_limit),
    }
    print(json.dumps(result, default=str))


def cmd_run_one(args):
    """Run research for a single topic."""
    topic = store.get_topic(args.topic)
    if not topic:
        print(json.dumps({"error": f'Topic not found: "{args.topic}"'}))
        sys.exit(1)

    result = _run_topic(topic)
    print(json.dumps(result, default=str))


def cmd_run_all(args):
    """Run research for all enabled topics with budget guard."""
    topics = store.list_topics()
    enabled = [t for t in topics if t["enabled"]]

    if not enabled:
        print(json.dumps({"message": "No enabled topics to research."}))
        return

    budget_limit = float(store.get_setting("daily_budget", "5.00"))
    results = []

    for topic in enabled:
        # Budget guard
        daily_cost = store.get_daily_cost()
        if daily_cost >= budget_limit:
            results.append({
                "topic": topic["name"],
                "status": "skipped",
                "reason": f"Budget exceeded: ${daily_cost:.2f}/${budget_limit:.2f}",
            })
            continue

        result = _run_topic(topic)
        results.append(result)

    print(json.dumps({
        "action": "run_all",
        "results": results,
        "budget_used": store.get_daily_cost(),
        "budget_limit": budget_limit,
    }, default=str))


def _run_topic(topic: dict) -> dict:
    """Run research for a single topic and store findings."""
    start_time = time.time()
    topic_id = topic["id"]

    # Record the run
    run_id = store.record_run(topic_id, source_mode="both", status="running")

    try:
        # Prefer custom search_queries over topic name (#40)
        search_queries = json.loads(topic["search_queries"]) if topic.get("search_queries") else None
        search_term = search_queries[0] if search_queries else topic["name"]
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "last30days.py"),
            search_term,
            "--emit=json",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
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

        # Parse research output
        data = json.loads(result.stdout)

        # Convert research items to findings format
        findings = []
        for item in data.get("reddit", []):
            findings.append({
                "source": "reddit",
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "author": item.get("author", ""),
                "content": item.get("title", ""),
                "summary": item.get("top_comments_summary", ""),
                "engagement_score": item.get("upvotes", 0),
                "relevance_score": item.get("relevance", 0),
            })
        for item in data.get("x", []):
            findings.append({
                "source": "x",
                "url": item.get("url", ""),
                "title": item.get("text", "")[:100],
                "author": item.get("author_handle", ""),
                "content": item.get("text", ""),
                "engagement_score": (item.get("engagement") or {}).get("likes", 0),
                "relevance_score": item.get("relevance", 0),
            })
        for item in data.get("youtube", []):
            findings.append({
                "source": "youtube",
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "author": item.get("channel_name", item.get("channel", "")),
                "content": item.get("transcript_snippet", "") or item.get("title", ""),
                "engagement_score": (item.get("engagement") or {}).get("views", 0),
                "relevance_score": item.get("relevance", 0),
            })
        for item in data.get("tiktok", []):
            findings.append({
                "source": "tiktok",
                "url": item.get("url", ""),
                "title": (item.get("caption_snippet", "") or "")[:120],
                "author": item.get("author", ""),
                "content": item.get("caption_snippet", ""),
                "engagement_score": (item.get("engagement") or {}).get("views", 0),
                "relevance_score": item.get("relevance", 0),
            })
        for item in data.get("instagram", []):
            findings.append({
                "source": "instagram",
                "url": item.get("url", ""),
                "title": (item.get("caption_snippet", "") or "")[:120],
                "author": item.get("author_name", ""),
                "content": item.get("caption_snippet", ""),
                "engagement_score": (item.get("engagement") or {}).get("views", 0),
                "relevance_score": item.get("relevance", 0),
            })

        # Store with dedup
        counts = store.store_findings(run_id, topic_id, findings)

        store.update_run(
            run_id,
            status="completed",
            duration_seconds=duration,
            findings_new=counts["new"],
            findings_updated=counts["updated"],
        )

        result = {
            "topic": topic["name"],
            "status": "completed",
            "new": counts["new"],
            "updated": counts["updated"],
            "duration": duration,
        }

        # Deliver findings if configured
        _deliver_findings(topic, counts, result)

        return result

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        store.update_run(
            run_id, status="failed",
            error_message="Research timed out after 300s",
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": "timeout"}

    except json.JSONDecodeError as e:
        duration = time.time() - start_time
        store.update_run(
            run_id, status="failed",
            error_message=f"Invalid JSON output: {e}",
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": f"parse error: {e}"}

    except Exception as e:
        duration = time.time() - start_time
        store.update_run(
            run_id, status="failed",
            error_message=str(e)[:500],
            duration_seconds=duration,
        )
        return {"topic": topic["name"], "status": "failed", "error": str(e)}


def _deliver_findings(topic: dict, counts: dict, run_result: dict):
    """Send delivery notification if a delivery channel is configured."""
    delivery_channel = store.get_setting("delivery_channel", "")
    delivery_mode = store.get_setting("delivery_mode", "announce")

    if not delivery_channel or counts["new"] == 0:
        return  # Nothing to deliver

    # Build message
    message = _format_delivery_message(topic, counts, delivery_mode)

    # Dispatch by channel type
    if delivery_channel.startswith("https://hooks.slack.com/"):
        _send_slack_webhook(delivery_channel, message)
    elif delivery_channel.startswith("https://"):
        _send_generic_webhook(delivery_channel, message)
    # Future: telegram, discord, etc.


def _format_delivery_message(topic: dict, counts: dict, mode: str) -> str:
    """Format the delivery message."""
    if mode == "announce":
        return (
            f"📰 *last30days update: {topic['name']}*\n"
            f"{counts['new']} new findings, {counts['updated']} updated.\n"
            f"Run `last30 briefing` to see the full summary."
        )
    elif mode == "silent":
        return f"last30days: {counts['new']} new findings for '{topic['name']}'"
    return f"last30days: Research complete for '{topic['name']}'"


def _send_slack_webhook(url: str, message: str):
    """POST to a Slack incoming webhook."""
    import urllib.request
    import urllib.error
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, Exception) as e:
        sys.stderr.write(f"[Delivery] Slack webhook failed: {e}\n")


def _send_generic_webhook(url: str, message: str):
    """POST JSON payload to a generic webhook URL."""
    import urllib.request
    import urllib.error
    payload = json.dumps({"text": message, "source": "last30days"}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, Exception) as e:
        sys.stderr.write(f"[Delivery] Webhook failed: {e}\n")


def cmd_config(args):
    """Configure watchlist settings."""
    if args.setting == "delivery":
        value = args.value
        # Basic validation
        if value and not (value.startswith("https://") or value == ""):
            print(json.dumps({"error": "delivery must be a webhook URL (https://...) or empty string to disable"}))
            return
        store.set_setting("delivery_channel", value)
        status = "enabled" if value else "disabled"
        print(json.dumps({"action": "config", "setting": "delivery_channel", "value": value, "status": status}))
    elif args.setting == "budget":
        store.set_setting("daily_budget", args.value)
        print(json.dumps({"action": "config", "setting": "daily_budget", "value": args.value}))
    else:
        print(json.dumps({"error": f"Unknown setting: {args.setting}. Use 'delivery' or 'budget'."}))


def main():
    parser = argparse.ArgumentParser(description="Manage last30days topic watchlist")
    sub = parser.add_subparsers(dest="command")

    # add
    a = sub.add_parser("add", help="Add a topic to the watchlist")
    a.add_argument("topic", help="Topic name")
    a.add_argument("--schedule", help="Cron expression (default: 0 8 * * *)")
    a.add_argument("--weekly", action="store_true", help="Run weekly instead of daily")
    a.add_argument("--queries", help="Comma-separated custom search queries")
    a.set_defaults(func=cmd_add)

    # remove
    r = sub.add_parser("remove", help="Remove a topic from the watchlist")
    r.add_argument("topic", help="Topic name")
    r.set_defaults(func=cmd_remove)

    # list
    l = sub.add_parser("list", help="List all watched topics")
    l.set_defaults(func=cmd_list)

    # run-all
    ra = sub.add_parser("run-all", help="Run research for all enabled topics")
    ra.set_defaults(func=cmd_run_all)

    # run-one
    ro = sub.add_parser("run-one", help="Run research for a single topic")
    ro.add_argument("topic", help="Topic name")
    ro.set_defaults(func=cmd_run_one)

    # config
    c = sub.add_parser("config", help="Configure watchlist settings")
    c.add_argument("setting", help="Setting name (delivery, budget)")
    c.add_argument("value", help="Setting value")
    c.set_defaults(func=cmd_config)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
