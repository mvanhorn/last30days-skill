#!/usr/bin/env python3
# ruff: noqa: E402
"""last30days v3.0.0 CLI."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import signal
import sys
import threading
from pathlib import Path

MIN_PYTHON = (3, 12)


def ensure_supported_python(version_info: tuple[int, int, int] | object | None = None) -> None:
    if version_info is None:
        version_info = sys.version_info
    major, minor, micro = tuple(version_info[:3])
    if (major, minor) >= MIN_PYTHON:
        return
    sys.stderr.write(
        "last30days v3 requires Python 3.12+.\n"
        f"Detected Python {major}.{minor}.{micro}.\n"
        "Install and use python3.12 or python3.13, then rerun this command.\n"
    )
    raise SystemExit(1)


ensure_supported_python()

if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import env, pipeline, render, schema, ui

_child_pids: set[int] = set()
_child_pids_lock = threading.Lock()


def register_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.add(pid)


def unregister_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.discard(pid)


def _cleanup_children() -> None:
    with _child_pids_lock:
        pids = list(_child_pids)
    for pid in pids:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue


atexit.register(_cleanup_children)


def parse_search_flag(raw: str) -> list[str]:
    sources = []
    for source in raw.split(","):
        source = source.strip().lower()
        if not source:
            continue
        normalized = pipeline.SEARCH_ALIAS.get(source, source)
        if normalized not in pipeline.MOCK_AVAILABLE_SOURCES:
            raise SystemExit(f"Unknown search source: {source}")
        if normalized not in sources:
            sources.append(normalized)
    if not sources:
        raise SystemExit("--search requires at least one source.")
    return sources


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "last30days"


def save_output(report: schema.Report, emit: str, save_dir: str, suffix: str = "") -> Path:
    from datetime import datetime
    path = Path(save_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    slug = slugify(report.topic)
    extension = "json" if emit == "json" else "md"
    suffix_part = f"-{suffix}" if suffix else ""
    out_path = path / f"{slug}-raw{suffix_part}.{extension}"
    if out_path.exists():
        out_path = path / f"{slug}-raw{suffix_part}-{datetime.now().strftime('%Y-%m-%d')}.{extension}"
    # Always save the FULL dump to disk (all items, all sources, transcripts).
    # Claude sees compact clusters via --emit=compact on stdout.
    # The saved file is the complete debug artifact.
    if emit == "json":
        content = emit_output(report, emit)
    else:
        content = render.render_full(report)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def emit_output(report: schema.Report, emit: str, fun_level: str = "medium", save_path: str | None = None) -> str:
    if emit == "json":
        return json.dumps(schema.to_dict(report), indent=2, sort_keys=True)
    if emit in {"compact", "md"}:
        return render.render_compact(report, fun_level=fun_level, save_path=save_path)
    if emit == "context":
        return render.render_context(report)
    raise SystemExit(f"Unsupported emit mode: {emit}")


def compute_save_path_display(save_dir: str, topic: str, suffix: str, emit: str) -> str:
    """Compute the user-friendly save path string that will be shown in the footer.

    Uses ~ for the home directory so the footer reads "~/Documents/Last30Days/slug-raw.md"
    instead of an absolute machine-local path.
    """
    from pathlib import Path as _Path
    path = _Path(save_dir).expanduser().resolve()
    slug = slugify(topic)
    extension = "json" if emit == "json" else "md"
    suffix_part = f"-{suffix}" if suffix else ""
    raw = path / f"{slug}-raw{suffix_part}.{extension}"
    try:
        home = _Path.home().resolve()
        relative = raw.relative_to(home)
        return f"~/{relative}"
    except ValueError:
        return str(raw)


def persist_report(report: schema.Report) -> dict[str, int]:
    import store

    store.init_db()
    topic_row = store.add_topic(report.topic)
    topic_id = topic_row["id"]
    source_mode = ",".join(sorted(report.items_by_source)) or "v3"
    run_id = store.record_run(topic_id, source_mode=source_mode, status="running")
    try:
        findings = store.findings_from_report(report)
        counts = store.store_findings(run_id, topic_id, findings)
        store.update_run(
            run_id,
            status="completed",
            findings_new=counts["new"],
            findings_updated=counts["updated"],
        )
        return counts
    except Exception as exc:
        store.update_run(run_id, status="failed", error_message=str(exc)[:500])
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research a topic across live social, market, and grounded web sources.")
    parser.add_argument("topic", nargs="*", help="Research topic")
    parser.add_argument("--emit", default="compact", choices=["compact", "json", "context", "md"])
    parser.add_argument("--search", help="Comma-separated source list")
    parser.add_argument("--quick", action="store_true", help="Lower-latency retrieval profile")
    parser.add_argument("--deep", action="store_true", help="Higher-recall retrieval profile")
    parser.add_argument("--debug", action="store_true", help="Enable HTTP debug logging")
    parser.add_argument("--mock", action="store_true", help="Use mock retrieval fixtures")
    parser.add_argument("--diagnose", action="store_true", help="Print provider and source availability")
    parser.add_argument("--save-dir", help="Optional directory for saving the rendered output")
    parser.add_argument("--store", action="store_true", help="Persist ranked findings to the SQLite research store")
    parser.add_argument("--x-handle", help="X handle for targeted supplemental search")
    parser.add_argument("--x-related", help="Comma-separated related X handles (searched with lower weight)")
    parser.add_argument("--web-backend", default="auto",
                        choices=["auto", "brave", "exa", "serper", "parallel", "none"],
                        help="Web search backend (default: auto, tries Brave then Exa then Serper then Parallel)")
    parser.add_argument("--deep-research", action="store_true",
                        help="Use Perplexity Deep Research (~$0.90/query) for in-depth analysis. Requires OPENROUTER_API_KEY.")
    parser.add_argument("--plan", help="JSON query plan (skips internal LLM planner). Can be a JSON string or a file path.")
    parser.add_argument("--save-suffix", help="Suffix for saved output filename (e.g., 'gemini' → kanye-west-raw-gemini.md)")
    parser.add_argument("--subreddits", help="Comma-separated subreddit names to search (e.g., SaaS,Entrepreneur)")
    parser.add_argument("--tiktok-hashtags", help="Comma-separated TikTok hashtags without # (e.g., tella,screenrecording)")
    parser.add_argument("--tiktok-creators", help="Comma-separated TikTok creator handles (e.g., TellaHQ,taborplace)")
    parser.add_argument("--ig-creators", help="Comma-separated Instagram creator handles (e.g., tella.tv,laborstories)")
    parser.add_argument(
        "--days",
        "--lookback-days",
        dest="lookback_days",
        type=int,
        default=30,
        help="Number of days to look back for research (default: 30, watchlist uses 90)",
    )
    parser.add_argument("--auto-resolve", action="store_true",
                        help="Use web search to discover subreddits/handles before planning (for platforms without WebSearch)")
    parser.add_argument("--github-user", help="GitHub username for person-mode search (e.g., steipete)")
    parser.add_argument("--github-repo", help="Comma-separated owner/repo for project-mode search (e.g., openclaw/openclaw,paperclipai/paperclip)")
    return parser


def _missing_sources_for_promo(diag: dict[str, object]) -> str | None:
    available = set(diag.get("available_sources") or [])
    missing = []
    if "reddit" not in available:
        missing.append("reddit")
    if "x" not in available:
        missing.append("x")
    if "grounding" not in available:
        missing.append("web")
    if not missing:
        return None
    if "reddit" in missing and "x" in missing:
        return "both"
    return missing[0]


def _show_runtime_ui(report: schema.Report, progress: ui.ProgressDisplay, diag: dict[str, object]) -> None:
    counts = {source: len(items) for source, items in report.items_by_source.items()}
    display_sources = list(
        dict.fromkeys(
            [
                *report.query_plan.source_weights.keys(),
                *report.items_by_source.keys(),
                *report.errors_by_source.keys(),
            ]
        )
    )
    progress.end_processing()
    progress.show_complete(
        source_counts=counts,
        display_sources=display_sources,
    )
    promo = _missing_sources_for_promo(diag)
    if promo:
        progress.show_promo(promo, diag=diag)


def main() -> int:
    parser = build_parser()
    # Use parse_known_args so setup sub-flags (--device-auth, --github,
    # --openclaw) pass through without argparse hard-exiting.
    args, extra_argv = parser.parse_known_args()
    if args.debug:
        os.environ["LAST30DAYS_DEBUG"] = "1"

    config = env.get_config()

    # Handle setup subcommand
    topic = " ".join(args.topic).strip()
    if topic.lower() == "setup":
        from lib import setup_wizard
        if "--openclaw" in extra_argv:
            results = setup_wizard.run_openclaw_setup(config)
            print(json.dumps(results))
            return 0
        if "--github" in extra_argv:
            results = setup_wizard.run_github_auth()
            print(json.dumps(results))
            return 0
        if "--device-auth" in extra_argv:
            results = setup_wizard.run_full_device_auth()
            print(json.dumps(results))
            return 0
        sys.stderr.write("Running auto-setup...\n")
        results = setup_wizard.run_auto_setup(config)
        from_browser = "auto"
        if results.get("cookies_found"):
            first_browser = next(iter(results["cookies_found"].values()))
            from_browser = first_browser
        setup_wizard.write_setup_config(env.CONFIG_FILE, from_browser=from_browser)
        results["env_written"] = True
        sys.stderr.write(setup_wizard.get_setup_status_text(results) + "\n")
        return 0

    requested_sources = parse_search_flag(args.search) if args.search else None
    diag = pipeline.diagnose(config, requested_sources)

    if args.diagnose:
        print(json.dumps(diag, indent=2, sort_keys=True))
        return 0

    if not topic:
        parser.print_usage(sys.stderr)
        return 2

    progress = ui.ProgressDisplay(topic, show_banner=True)
    progress.start_processing()

    depth = "deep" if args.deep else "quick" if args.quick else "default"
    try:
        x_related = [h.strip() for h in args.x_related.split(",") if h.strip()] if args.x_related else None
        subreddits = [s.strip().lstrip("r/") for s in args.subreddits.split(",") if s.strip()] if args.subreddits else None
        tiktok_hashtags = [h.strip().lstrip("#") for h in args.tiktok_hashtags.split(",") if h.strip()] if args.tiktok_hashtags else None
        tiktok_creators = [c.strip().lstrip("@") for c in args.tiktok_creators.split(",") if c.strip()] if args.tiktok_creators else None
        ig_creators = [c.strip().lstrip("@") for c in args.ig_creators.split(",") if c.strip()] if args.ig_creators else None
        # Parse external plan if provided via --plan flag
        external_plan = None
        if args.plan:
            import json as _json
            plan_str = args.plan
            if os.path.isfile(plan_str):
                plan_str = open(plan_str).read()
            try:
                external_plan = _json.loads(plan_str)
            except _json.JSONDecodeError as exc:
                sys.stderr.write(f"[Planner] Invalid --plan JSON: {exc}\n")

        # Auto-resolve: use web search to discover subreddits/handles before planning.
        # This is the engine-side equivalent of SKILL.md Steps 0.55/0.75 for platforms
        # without WebSearch (OpenClaw, Codex, raw CLI).
        if args.auto_resolve and not external_plan:
            from lib import resolve
            resolution = resolve.auto_resolve(topic, config)
            if resolution.get("subreddits") and not subreddits:
                subreddits = resolution["subreddits"]
                sys.stderr.write(f"[AutoResolve] Subreddits: {', '.join(subreddits)}\n")
            if resolution.get("x_handle") and not args.x_handle:
                args.x_handle = resolution["x_handle"]
                sys.stderr.write(f"[AutoResolve] X handle: @{args.x_handle}\n")
            if resolution.get("github_user") and not args.github_user:
                args.github_user = resolution["github_user"]
                sys.stderr.write(f"[AutoResolve] GitHub user: @{args.github_user}\n")
            if resolution.get("github_repos") and not args.github_repo:
                args.github_repo = ",".join(resolution["github_repos"])
                sys.stderr.write(f"[AutoResolve] GitHub repos: {args.github_repo}\n")
            if resolution.get("context"):
                # Inject context into external_plan metadata for the planner to use
                if not external_plan:
                    external_plan = None  # planner will use its own, but with context
                # Store context for the planner prompt injection
                config["_auto_resolve_context"] = resolution["context"]
                sys.stderr.write(f"[AutoResolve] Context: {resolution['context'][:80]}...\n")

        github_user = args.github_user.lstrip("@").lower() if args.github_user else None
        github_repos = [r.strip() for r in args.github_repo.split(",") if r.strip() and "/" in r.strip()] if args.github_repo else None

        # --deep-research: auto-enable perplexity source and set deep flag
        if args.deep_research:
            if not config.get("OPENROUTER_API_KEY"):
                print("Error: --deep-research requires OPENROUTER_API_KEY", file=sys.stderr)
                sys.exit(1)
            config["_deep_research"] = True
            # Auto-enable perplexity in INCLUDE_SOURCES
            include = config.get("INCLUDE_SOURCES") or ""
            if "perplexity" not in include.lower():
                config["INCLUDE_SOURCES"] = f"{include},perplexity" if include else "perplexity"

        report = pipeline.run(
            topic=topic,
            config=config,
            depth=depth,
            requested_sources=requested_sources,
            mock=args.mock,
            x_handle=args.x_handle,
            x_related=x_related,
            web_backend=args.web_backend,
            external_plan=external_plan,
            subreddits=subreddits,
            tiktok_hashtags=tiktok_hashtags,
            tiktok_creators=tiktok_creators,
            ig_creators=ig_creators,
            lookback_days=args.lookback_days,
            github_user=github_user,
            github_repos=github_repos,
        )
    except Exception as exc:
        progress.end_processing()
        progress.show_error(str(exc))
        raise
    _show_runtime_ui(report, progress, diag)
    if args.store:
        counts = persist_report(report)
        sys.stderr.write(
            f"[last30days] Stored {counts['new']} new, {counts['updated']} updated findings\n"
        )
        sys.stderr.flush()

    # Show quality nudge if applicable
    try:
        from lib import quality_nudge
        quality = quality_nudge.compute_quality_score(config, {})
        if quality.get("nudge_text"):
            sys.stderr.write(f"\n{quality['nudge_text']}\n")
            sys.stderr.flush()
    except Exception:
        pass

    fun_level = config.get("FUN_LEVEL", "medium").lower()
    footer_save_path = None
    if args.save_dir:
        footer_save_path = compute_save_path_display(
            args.save_dir, report.topic, args.save_suffix or "", args.emit
        )

    # Signal to render_compact whether pre-research flags were supplied.
    # Used to emit a Pre-Research Status warning when the model skipped
    # Step 0.5 / 0.55 and invoked the engine bare on an eligible topic.
    pre_research_flags_present = bool(
        args.x_handle
        or args.github_user
        or args.subreddits
        or args.plan
        or args.auto_resolve
        or args.tiktok_creators
        or args.ig_creators
    )
    report.artifacts["pre_research_flags_present"] = pre_research_flags_present

    rendered = emit_output(report, args.emit, fun_level=fun_level, save_path=footer_save_path)
    if args.save_dir:
        save_path = save_output(report, args.emit, args.save_dir, suffix=args.save_suffix or "")
        sys.stderr.write(f"[last30days] Saved output to {save_path}\n")
        sys.stderr.flush()
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
