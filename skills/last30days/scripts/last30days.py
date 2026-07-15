#!/usr/bin/env python3
# ruff: noqa: E402
"""last30days CLI."""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import re
import signal
import sqlite3
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
    req = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    sys.stderr.write(
        f"last30days v3 requires Python {req}+.\n"
        f"Detected Python {major}.{minor}.{micro}.\n"
        f"Install with:\n"
        f"  Mac:     brew install python@{req}\n"
        f"  Windows: winget install Python.Python.{req}\n"
        f"  Linux:   sudo apt install python{req}  (or pyenv install {req})\n"
        f"Then rerun: python{req} <path-to-script> setup\n"
    )
    raise SystemExit(1)


ensure_supported_python()

if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import corpus, dates, env, freshness, html_render, http, permission_preflight, pipeline, registers, render, schema, ui

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
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue


atexit.register(_cleanup_children)


def parse_search_flag(raw: str, flag_name: str = "--search") -> list[str]:
    sources = []
    for source in raw.split(","):
        source = source.strip().lower()
        if not source:
            continue
        normalized = pipeline.SEARCH_ALIAS.get(source, source)
        if normalized not in pipeline.MOCK_AVAILABLE_SOURCES:
            raise SystemExit(f"Unknown search source in {flag_name}: {source}")
        if normalized not in sources:
            sources.append(normalized)
    if not sources:
        raise SystemExit(f"{flag_name} requires at least one source.")
    return sources

def parse_as_of_date_arg(value: str) -> str:
    try:
        parsed = dates.parse_as_of_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return parsed

def resolve_requested_sources(args_search: str | None, config: dict) -> list[str] | None:
    """Resolve the requested source set: explicit --search wins, then the
    LAST30DAYS_DEFAULT_SEARCH config key (env var or .env file), then None
    (per-query default behavior). The config fallback lets users pin a fixed
    source set that survives upgrades without patching SKILL.md (#442).
    """
    if args_search:
        return parse_search_flag(args_search)
    default_search = (config.get("LAST30DAYS_DEFAULT_SEARCH") or "").strip()
    if default_search:
        return parse_search_flag(default_search, flag_name="LAST30DAYS_DEFAULT_SEARCH")
    return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "last30days"


def _report_has_private_corpus(report: schema.Report) -> bool:
    items_by_source = getattr(report, "items_by_source", {})
    if isinstance(items_by_source, dict) and items_by_source.get("corpus"):
        return True
    candidates = getattr(report, "ranked_candidates", ())
    if not isinstance(candidates, (list, tuple)):
        return False
    return any(
        candidate.source == "corpus"
        or any(item.source == "corpus" for item in candidate.source_items)
        for candidate in candidates
    )


def _ensure_output_directory(path: Path, *, private: bool) -> None:
    if not private:
        path.mkdir(parents=True, exist_ok=True)
        return
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        directory.chmod(0o700)


def save_output(
    report: schema.Report,
    emit: str,
    save_dir: str,
    suffix: str = "",
    synthesis_md: str | None = None,
    topic_override: str | None = None,
    rendered_content: str | None = None,
    json_profile: str = "agent",
    register: str = "default",
    private: bool | None = None,
) -> Path:
    from datetime import datetime
    path = Path(save_dir).expanduser().resolve()
    slug = slugify(topic_override or report.topic)
    extension = "json" if emit == "json" else "html" if emit == "html" else "md"
    raw_label = "raw-html" if emit == "html" else "raw"
    suffix_part = f"-{suffix}" if suffix else ""
    base = path / f"{slug}-{raw_label}{suffix_part}.{extension}"
    date_str = datetime.now().strftime('%Y-%m-%d')
    candidates = [base]
    candidates.append(path / f"{slug}-{raw_label}{suffix_part}-{date_str}.{extension}")
    for i in range(1, 100):
        candidates.append(path / f"{slug}-{raw_label}{suffix_part}-{date_str}-{i}.{extension}")
    # Markdown saves keep the complete debug artifact. JSON and HTML preserve
    # their requested wire format so file extensions match their content.
    if rendered_content is not None:
        content = rendered_content
    elif emit in {"json", "html"}:
        content = emit_output(
            report,
            emit,
            synthesis_md=synthesis_md,
            json_profile=json_profile,
            register=register,
        )
    else:
        content = render.render_full(report)
    private_corpus = _report_has_private_corpus(report) or bool(private)
    _ensure_output_directory(path, private=private_corpus)
    encoded = content.encode("utf-8")
    for candidate in candidates:
        try:
            fd = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600 if private_corpus else 0o644,
            )
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
        if candidate.suffix.lower() == ".md":
            try:
                from lib import library, library_index

                save_root = candidate.parent.resolve()
                if save_root == Path(library.DEFAULT_MEMORY_DIR).expanduser().resolve():
                    library_index.sync_library(save_root)
                else:
                    # A scoped save must sync a per-directory index with the
                    # same paths scoped search uses; syncing the shared DB
                    # from one scope's scan would prune other scopes' rows.
                    library_index.sync_library(
                        save_root,
                        save_root / "briefings",
                        db_path=save_root / ".last30days-library.db",
                    )
            except (library_index.LibrarySearchUnavailable, OSError, sqlite3.DatabaseError):
                # Saving research must not depend on the optional local index;
                # `library search` reports a clear capability error on demand.
                pass
        return candidate
    # Fallback: all 101 candidates existed (extremely unlikely).
    raise RuntimeError(
        f"save_output: could not find a unique filename after 101 attempts in {path}"
    )


def save_rendered_output(
    rendered_content: str,
    output_file: str,
    *,
    private: bool = False,
) -> Path:
    out_path = Path(output_file).expanduser().resolve()
    _ensure_output_directory(out_path.parent, private=private)
    if private and out_path.exists():
        out_path.chmod(0o600)
    fd = os.open(
        out_path,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600 if private else 0o644,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(rendered_content)
    if private:
        out_path.chmod(0o600)
    return out_path


def _publish_metadata_path(html_path: Path) -> Path:
    return html_path.with_name(f"{html_path.name}.publish.json")


def _write_publish_metadata(html_path: Path, publish_result: dict[str, object]) -> None:
    payload = {
        "url": publish_result.get("url"),
        "site_id": publish_result.get("site_id"),
        "status": publish_result.get("status"),
        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _publish_metadata_path(html_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def publish_rendered_html(
    rendered: str,
    *,
    password: str | None = None,
    companion_paths: list[Path] | None = None,
) -> dict[str, object]:
    from lib import html_publish

    result = html_publish.publish_html(rendered, password=password)
    metadata_errors: list[str] = []
    for path in companion_paths or []:
        try:
            _write_publish_metadata(path, result)
        except OSError as exc:
            metadata_errors.append(f"{path}: {exc}")
    if metadata_errors:
        result = dict(result)
        result["_metadata_errors"] = metadata_errors
    return result


def _publish_password_for_args(
    args: argparse.Namespace,
    config: dict[str, object] | None = None,
) -> str | None:
    return (
        args.publish_password
        or env.read_secret_env("LAST30DAYS_PUBLISH_PASSWORD")
        or (config or {}).get("LAST30DAYS_PUBLISH_PASSWORD")
        or None
    )


def emit_output(
    report: schema.Report,
    emit: str,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
    json_profile: str = "agent",
    register: str = "default",
) -> str:
    if emit == "json":
        payload = (
            schema.to_dict(report)
            if json_profile == "raw"
            else schema.to_agent_export(report)
        )
        return json.dumps(payload, indent=2, sort_keys=True)
    if emit == "html":
        return html_render.render_html(
            report,
            fun_level=fun_level,
            save_path=save_path,
            synthesis_md=synthesis_md,
            register=register,
        )
    if emit in {"compact", "md"}:
        return render.render_compact(
            report,
            fun_level=fun_level,
            save_path=save_path,
            register=register,
        )
    if emit == "context":
        return render.render_context(report)
    if emit == "brief":
        return render.render_brief(report)
    raise SystemExit(f"Unsupported emit mode: {emit}")


def emit_comparison_output(
    entity_reports: list[tuple[str, schema.Report]],
    emit: str,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
    json_profile: str = "agent",
) -> str:
    if emit == "json":
        payload = {
            "comparison": True,
            "entities": [label for label, _ in entity_reports],
            "reports": [
                {
                    "entity": label,
                    "report": (
                        schema.to_dict(report)
                        if json_profile == "raw"
                        else schema.to_agent_export(report)
                    ),
                }
                for label, report in entity_reports
            ],
        }
        if json_profile == "agent":
            payload["schema_version"] = schema.AGENT_EXPORT_SCHEMA_VERSION
        return json.dumps(payload, indent=2, sort_keys=True)
    if emit == "html":
        return html_render.render_html_comparison(
            entity_reports,
            fun_level=fun_level,
            save_path=save_path,
            synthesis_md=synthesis_md,
        )
    if emit in {"compact", "md"}:
        return render.render_comparison_multi(
            entity_reports, fun_level=fun_level, save_path=save_path,
        )
    if emit == "context":
        return render.render_comparison_multi_context(entity_reports)
    raise SystemExit(f"Unsupported emit mode: {emit}")


def comparison_topic(entity_reports: list[tuple[str, schema.Report]]) -> str:
    return " vs ".join(label for label, _ in entity_reports)


def compute_save_path_display(save_dir: str, topic: str, suffix: str, emit: str) -> str:
    """Compute the user-friendly save path string that will be shown in the footer.

    Uses ~ when the saved file is under the user's home directory; otherwise
    returns the absolute path.
    """
    from pathlib import Path as _Path
    path = _Path(save_dir).expanduser().resolve()
    slug = slugify(topic)
    extension = "json" if emit == "json" else "html" if emit == "html" else "md"
    raw_label = "raw-html" if emit == "html" else "raw"
    suffix_part = f"-{suffix}" if suffix else ""
    raw = path / f"{slug}-{raw_label}{suffix_part}.{extension}"
    try:
        home = _Path.home().resolve()
        relative = raw.relative_to(home)
        return f"~/{relative.as_posix()}"
    except ValueError:
        return raw.as_posix()


def compute_output_path_display(output_file: str) -> str:
    """Compute the user-friendly explicit output path shown in render footers."""
    raw = Path(output_file).expanduser().resolve()
    try:
        home = Path.home().resolve()
        relative = raw.relative_to(home)
        return f"~/{relative.as_posix()}"
    except ValueError:
        return raw.as_posix()


def read_synthesis_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[last30days] Cannot read --synthesis-file: {exc}\n")
        raise SystemExit(2)


def _scoped_store_db(args: argparse.Namespace) -> Path | None:
    """Scoped runs write findings inside the save dir, matching scoped reads."""
    save_dir = getattr(args, "save_dir", None)
    if save_dir:
        return Path(save_dir).expanduser().resolve() / "research.db"
    return None


def persist_report(report: schema.Report, store_db: Path | None = None) -> dict[str, int]:
    import store

    private_corpus = _report_has_private_corpus(report)
    with store.scoped_db(store_db):
        if private_corpus:
            store.ensure_private_db_files()
        store.init_db()
        if private_corpus:
            store.ensure_private_db_files()
        topic_row = store.add_topic(report.topic)
        topic_id = topic_row["id"]
        source_mode = ",".join(sorted(report.items_by_source)) or "v3"
        run_id = store.record_run(topic_id, source_mode=source_mode, status="running")
        try:
            findings = store.findings_from_report(report)
            if private_corpus:
                store.ensure_private_db_files()
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
        finally:
            if private_corpus:
                store.ensure_private_db_files()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research a topic across live social, market, and grounded web sources.",
        allow_abbrev=False,
    )
    parser.add_argument("topic", nargs="*", help="Research topic")
    parser.add_argument("--emit", default="compact", choices=["compact", "json", "context", "md", "html", "brief"])
    parser.add_argument(
        "--register",
        choices=registers.REGISTER_NAMES,
        default=None,
        help="Audience synthesis preset for the standard brief (default, exec, dev, creator, eli5)",
    )
    parser.add_argument(
        "--json-profile",
        default="agent",
        choices=["agent", "raw"],
        help="JSON export profile for --emit=json (default: agent)",
    )
    parser.add_argument("--search", help="Comma-separated source list")
    parser.add_argument("--quick", action="store_true", help="Lower-latency retrieval profile")
    parser.add_argument("--deep", action="store_true", help="Higher-recall retrieval profile")
    freshness_group = parser.add_mutually_exclusive_group()
    freshness_group.add_argument(
        "--verify-freshness",
        action="store_true",
        default=None,
        help="Re-check source-grounded claims after research, or verify the cached report when no topic is supplied",
    )
    freshness_group.add_argument(
        "--no-verify-freshness",
        dest="verify_freshness",
        action="store_false",
        help="Disable freshness verification configured by LAST30DAYS_VERIFY_FRESHNESS",
    )
    parser.add_argument(
        "--drill",
        metavar="TARGET",
        help="Deep follow-up on a cluster from the fresh last-report.json cache",
    )
    parser.add_argument(
        "--discover",
        metavar="DOMAIN",
        nargs="?",
        const="",
        default=None,
        help=(
            "Sweep river listings and rank the topics accelerating in a domain; "
            "each survivor gets a full research pass. Bare --discover (no domain) "
            "runs global trending across every feed's hot list"
        ),
    )
    parser.add_argument(
        "--discover-shallow",
        action="store_true",
        help=(
            "Skip the per-topic research pass during --discover: rank on listing "
            "evidence only (faster, thinner; the confidence floor still applies)"
        ),
    )
    parser.add_argument("--debug", action="store_true", help="Enable HTTP debug logging")
    parser.add_argument("--mock", action="store_true", help="Use mock retrieval fixtures")
    parser.add_argument(
        "--record-fixtures",
        metavar="DIR",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--diagnose", action="store_true", help="Print provider and source availability")
    parser.add_argument("--preflight", action="store_true",
                        help="Print a safe human-readable permission preflight")
    parser.add_argument("--welcome", action="store_true",
                        help="Print the first-run welcome text (engine-owned; relay verbatim)")
    parser.add_argument("--preflight-report-on-save-dir", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser-cookies", action="store_true",
                        help="Disable browser-cookie extraction even when FROM_BROWSER is configured")
    parser.add_argument("--save-dir", help="Optional directory for saving the rendered output")
    parser.add_argument(
        "--corpus",
        action="append",
        default=[],
        metavar="DIR",
        help="Add a local .md/.txt/.pdf directory as a private ranked source (repeatable)",
    )
    parser.add_argument(
        "--corpus-all-time",
        action="store_true",
        help="Include matching corpus files older than the research window",
    )
    parser.add_argument("--output", help="Optional exact file path for saving the rendered output")
    parser.add_argument("--synthesis-file", help="Markdown synthesis to embed in --emit=html output")
    parser.add_argument("--publish-html", action="store_true",
                        help="Publish --emit=html output to ht-ml.app (explicit opt-in; public by default)")
    parser.add_argument("--publish", action="store_true",
                        help="With 'library feed', publish the HTML index and briefs (explicit opt-in; public by default); feed.xml remains local")
    parser.add_argument("--publish-password",
                        help="Optional shared password for --publish-html or 'library feed --publish'; prefer LAST30DAYS_PUBLISH_PASSWORD to avoid exposing secrets in process lists")
    parser.add_argument("--store", action="store_true", help="Persist ranked findings to the SQLite research store")
    parser.add_argument("--x-handle", help="X handle for targeted supplemental search")
    parser.add_argument("--x-related", help="Comma-separated related X handles (searched with lower weight)")
    parser.add_argument("--web-backend", default="auto",
                        choices=["auto", "brave", "exa", "serper", "parallel", "none"],
                        help="Web search backend (default: auto, tries Brave then Exa then Serper then Parallel)")
    parser.add_argument("--deep-research", action="store_true",
                        help="Use Perplexity Deep Research (~$0.90/query) for in-depth analysis. Requires PERPLEXITY_API_KEY or OPENROUTER_API_KEY.")
    parser.add_argument("--hiring-signals", action="store_true",
                        help="Analyze public jobs/careers postings as evidence-backed company focus signals.")
    parser.add_argument("--plan", help="JSON query plan (skips internal LLM planner). Can be a JSON string or a file path.")
    parser.add_argument("--save-suffix", help="Suffix for saved output filename (e.g., 'gemini' → kanye-west-raw-gemini.md)")
    parser.add_argument("--subreddits", help="Comma-separated broad/category subreddit names to search (e.g., SaaS,Entrepreneur)")
    parser.add_argument("--dedicated-subreddits", help="Comma-separated entity-home subreddit names (e.g., Kanye,WestSubEver). Pulled in full (top+hot+new) and exempt from the relevance floor since the whole sub is the topic.")
    parser.add_argument("--tiktok-hashtags", help="Comma-separated TikTok hashtags without # (e.g., tella,screenrecording)")
    parser.add_argument("--tiktok-creators", help="Comma-separated TikTok creator handles (e.g., TellaHQ,taborplace)")
    parser.add_argument("--ig-creators", help="Comma-separated Instagram creator handles (e.g., tella.tv,laborstories)")
    parser.add_argument(
        "--days",
        "--lookback-days",
        dest="lookback_days",
        type=int,
        default=None,
        help="Number of days to look back for research (default: 30, watchlist uses 90)",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of_date",
        type=parse_as_of_date_arg,
        help=(
            "End date for the lookback window in YYYY-MM-DD format. "
            "When set, --days looks back from this date instead of today."
            ),
    )
    parser.add_argument("--auto-resolve", action="store_true",
                        help="Use web search to discover subreddits/handles before planning (for platforms without WebSearch)")
    parser.add_argument("--github-user", help="GitHub username for person-mode search (e.g., steipete)")
    parser.add_argument("--github-repo", help="Comma-separated owner/repo for project-mode search (e.g., openclaw/openclaw,paperclipai/paperclip)")
    parser.add_argument("--trustpilot-domain", help="Trustpilot review-page domain for the topic (e.g., www.thriftbooks.com). Used verbatim and bypasses the brand-shape gate; find it with `trustpilot-pp-cli search '<name>'`.")
    parser.add_argument(
        "--competitors",
        nargs="?",
        const=2,
        type=int,
        default=None,
        metavar="N",
        help="Auto-discover N competitor entities and fan out last30days across all of them as a comparison (default N=2 → 3-way: original + 2 peers; range 1..6). Use --competitors-list to override discovery.",
    )
    parser.add_argument(
        "--competitors-list",
        dest="competitors_list",
        help="Comma-separated competitor entities to skip discovery (e.g., 'Anthropic,xAI,Google Gemini'). Implies --competitors.",
    )
    parser.add_argument(
        "--polymarket-keywords",
        dest="polymarket_keywords",
        help=(
            "Comma-separated keywords that Polymarket market titles must match "
            "to be included. Use for ambiguous single-token topics like 'Warriors' "
            "(nba,gsw,golden-state) to filter out Glasgow Warriors rugby, Honor "
            "of Kings Rogue Warriors, etc. When omitted, Polymarket returns all "
            "matching markets — so expect cross-entity noise on generic topics."
        ),
    )
    parser.add_argument(
        "--competitors-plan",
        dest="competitors_plan",
        help=(
            "JSON mapping of per-entity Step 0.55 targeting for competitor / vs-mode "
            "sub-runs. Schema: {entity_name: {x_handle?, x_related?, subreddits?, "
            "github_user?, github_repos?, context?}}. Accepts inline JSON or a file "
            "path. Implies --competitors. Preferred over --competitors-list when the "
            "hosting model has already resolved per-entity handles and subs."
        ),
    )
    return parser


def parse_competitors_plan(raw: str | None) -> dict[str, dict]:
    """Parse a --competitors-plan argument into a {entity_name_lower: plan_entry} dict.

    Accepts inline JSON or a file path (matches --plan). Returns {} on None/empty.
    Validation: top-level must be a dict; each value must be a dict. Unknown fields
    in entry values log a warning but do not abort. Invalid JSON or non-dict shape
    raises SystemExit(2) with a clear stderr message.
    """
    if not raw:
        return {}
    plan_str = raw
    if os.path.isfile(plan_str):
        try:
            with open(plan_str, encoding="utf-8") as f:
                plan_str = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f"[CompetitorsPlan] Cannot read plan file: {exc}\n")
            raise SystemExit(2)
    try:
        parsed = json.loads(plan_str)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[CompetitorsPlan] Invalid JSON: {exc}\n")
        raise SystemExit(2)
    if not isinstance(parsed, dict):
        sys.stderr.write(
            f"[CompetitorsPlan] Top-level must be a dict of "
            f"{{entity: {{targeting}}}}, got {type(parsed).__name__}\n"
        )
        raise SystemExit(2)
    known_fields = {
        "x_handle", "x_related", "subreddits",
        "github_user", "github_repos", "trustpilot_domain", "context",
    }
    normalized: dict[str, dict] = {}
    for entity, entry in parsed.items():
        if not isinstance(entry, dict):
            sys.stderr.write(
                f"[CompetitorsPlan] Entry for {entity!r} must be a dict, "
                f"got {type(entry).__name__}; skipping.\n"
            )
            continue
        unknown = set(entry.keys()) - known_fields
        if unknown:
            sys.stderr.write(
                f"[CompetitorsPlan] Unknown fields in {entity!r}: "
                f"{sorted(unknown)}; ignoring.\n"
            )
        normalized[entity.strip().lower()] = {
            k: v for k, v in entry.items() if k in known_fields
        }
    return normalized


def subrun_kwargs_for(
    entity: str,
    plan_entry: dict,
    *,
    resolved: dict,
) -> dict:
    """Build an explicit per-entity kwargs dict for pipeline.run().

    Plan values win over auto_resolve values. Returns keys for all per-entity
    targeting flags so callers never fall through to closure defaults.

    This helper is the single source of truth for sub-run kwargs — main-topic
    flags can only leak if a caller bypasses it.
    """
    def _choose(plan_key: str, resolved_key: str | None = None):
        if plan_key in plan_entry and plan_entry[plan_key]:
            return plan_entry[plan_key]
        if resolved_key is not None and resolved.get(resolved_key):
            return resolved[resolved_key]
        return None

    x_handle = _choose("x_handle", "x_handle")
    if isinstance(x_handle, str):
        x_handle = x_handle.lstrip("@") or None

    subreddits = _choose("subreddits", "subreddits")
    if isinstance(subreddits, list):
        subreddits = [s.strip().removeprefix("r/") for s in subreddits if s.strip()] or None

    x_related = plan_entry.get("x_related")
    if isinstance(x_related, list):
        x_related = [h.strip().lstrip("@") for h in x_related if h.strip()] or None
    else:
        x_related = None

    github_user = _choose("github_user", "github_user")
    if isinstance(github_user, str):
        github_user = github_user.lstrip("@").lower() or None

    github_repos = _choose("github_repos", "github_repos")
    if isinstance(github_repos, list):
        github_repos = [r.strip() for r in github_repos if r.strip() and "/" in r.strip()] or None

    trustpilot_domain = _choose("trustpilot_domain", "trustpilot_domain")
    if isinstance(trustpilot_domain, str):
        trustpilot_domain = trustpilot_domain.strip() or None
    # Provenance: a plan-supplied domain is user-set (verbatim-final); one that
    # only came from auto_resolve is a hint that retries via search on a miss.
    trustpilot_domain_is_hint = bool(
        trustpilot_domain and not plan_entry.get("trustpilot_domain")
    )

    context = plan_entry.get("context") or resolved.get("context") or ""

    return {
        "x_handle": x_handle,
        "x_related": x_related,
        "subreddits": subreddits,
        "github_user": github_user,
        "github_repos": github_repos,
        "trustpilot_domain": trustpilot_domain,
        "_trustpilot_domain_is_hint": trustpilot_domain_is_hint,
        "_context": context,
    }


COMPETITORS_MIN = 1
COMPETITORS_MAX = 6
COMPETITORS_DEFAULT = 2


def resolve_competitors_args(args: argparse.Namespace) -> tuple[bool, int, list[str]]:
    """Normalize --competitors / --competitors-list into (enabled, count, explicit_list).

    - (False, 0, []) when neither flag is set.
    - An explicit list always wins; count is derived from list length.
    - A numeric count outside [1, 6] is clamped with a stderr warning.
    - count <= 0 (explicit) raises SystemExit(2).
    """
    explicit_list: list[str] = []
    list_flag_provided = args.competitors_list is not None
    if list_flag_provided:
        explicit_list = [
            entity.strip()
            for entity in args.competitors_list.split(",")
            if entity.strip()
        ]
        if not explicit_list:
            sys.stderr.write("[Competitors] --competitors-list is empty.\n")
            raise SystemExit(2)

    competitors_flag = args.competitors
    list_present = bool(explicit_list)
    flag_present = competitors_flag is not None

    if not list_present and not flag_present:
        return False, 0, []

    if list_present:
        count = len(explicit_list)
        if flag_present and competitors_flag != count:
            sys.stderr.write(
                f"[Competitors] --competitors={competitors_flag} ignored; using "
                f"{count} entries from --competitors-list.\n"
            )
        if count > COMPETITORS_MAX:
            sys.stderr.write(
                f"[Competitors] --competitors-list has {count} entries, clamping to {COMPETITORS_MAX}.\n"
            )
            explicit_list = explicit_list[:COMPETITORS_MAX]
            count = COMPETITORS_MAX
        return True, count, explicit_list

    # flag_present, no explicit list
    count = competitors_flag
    if count < COMPETITORS_MIN:
        sys.stderr.write(
            f"[Competitors] --competitors must be >= {COMPETITORS_MIN} (got {count}).\n"
        )
        raise SystemExit(2)
    if count > COMPETITORS_MAX:
        sys.stderr.write(
            f"[Competitors] --competitors={count} exceeds max {COMPETITORS_MAX}; clamping.\n"
        )
        count = COMPETITORS_MAX
    return True, count, []


def _missing_sources_for_promo(diag: dict[str, object]) -> str | None:
    available = set(diag.get("available_sources") or [])
    missing = []
    if "reddit" not in available:
        missing.append("reddit")
    if "x" not in available:
        missing.append("x")
    # The web promo nudges toward a paid backend for higher-quality web search.
    # Grounding is now available keyless on non-native hosts, so key the promo on
    # the absence of a *paid* backend, not on grounding availability. Suppress it
    # entirely on native-search hosts, where the model's own search is better and
    # setting a paid engine key would be the wrong advice.
    if not diag.get("native_web_backend") and not diag.get("native_search"):
        missing.append("web")
    if not missing:
        return None
    if "reddit" in missing and "x" in missing:
        return "both"
    return missing[0]


def _show_runtime_ui(
    report: schema.Report,
    progress: ui.ProgressDisplay,
    diag: dict[str, object],
    suppress_web_promo: bool = False,
) -> None:
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
    # The `web` promo nudges users to set BRAVE_API_KEY / SERPER_API_KEY, which
    # is wrong advice when a hosting reasoning model (Claude Code, Codex,
    # Hermes, Gemini) is driving — those already have WebSearch and can
    # pre-resolve Step 0.55 themselves. Suppress the web promo when a hosting
    # model signal is present (--plan or --competitors-plan was passed).
    if promo:
        if suppress_web_promo and promo == "web":
            return
        if suppress_web_promo and promo == "both":
            # "both" means reddit + web both missing; still nudge reddit but
            # skip the web line. show_promo has a per-source variant.
            progress.show_promo("reddit", diag=diag)
            return
        progress.show_promo(promo, diag=diag)


REPORT_CACHE_VERSION = "last30days-report-cache/v1"
DEFAULT_REPORT_CACHE_TTL_SECONDS = 3600


def _last_report_cache_path() -> Path | None:
    if env.CONFIG_DIR is None:
        return None
    return env.CONFIG_DIR / "last-report.json"


def _report_cache_ttl_seconds(config: dict[str, object]) -> int:
    raw = os.environ.get("LAST30DAYS_REPORT_CACHE_TTL_SECONDS")
    if raw is None:
        raw = config.get("LAST30DAYS_REPORT_CACHE_TTL_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_REPORT_CACHE_TTL_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_REPORT_CACHE_TTL_SECONDS


def _is_report_cache_fresh(timestamp: object, ttl_seconds: int) -> bool:
    return env.is_timestamp_fresh(timestamp, ttl_seconds)


def _write_last_run(
    topic: str,
    report: "schema.Report",
    entity_reports: list[tuple[str, schema.Report]] | None = None,
) -> bool:
    try:
        if env.CONFIG_DIR is None:
            return False
        target = env.CONFIG_DIR
        cached_reports = entity_reports or [(report.topic, report)]
        has_private_corpus = any(
            cached_report.items_by_source.get("corpus")
            for _, cached_report in cached_reports
        )
        _ensure_output_directory(target, private=has_private_corpus)
        counts = {source: len(items) for source, items in report.items_by_source.items()}
        payload = {
            "topic": topic,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sources": counts,
            "total": sum(counts.values()),
            "report_cache": str(target / "last-report.json"),
            "comparison": bool(entity_reports),
        }
        (target / "last-run.json").write_text(json.dumps(payload, indent=2))
        cache_payload = {
            "schema": REPORT_CACHE_VERSION,
            "topic": topic,
            "timestamp": payload["timestamp"],
            "comparison": bool(entity_reports),
            "reports": [
                {"entity": label, "report": schema.to_dict(cached_report)}
                for label, cached_report in cached_reports
            ],
        }
        report_cache_path = target / "last-report.json"
        report_cache_path.write_text(json.dumps(cache_payload, indent=2))
        if has_private_corpus:
            report_cache_path.chmod(0o600)
        return True
    except Exception as exc:
        # Never fatal, but never silent either (#787's lesson): callers that
        # promise cache state (drill chaining) branch on the return value.
        sys.stderr.write(f"[last30days] warning: could not write run cache: {exc}\n")
        return False


def _load_last_report_cache(
    topic: str | None,
    ttl_seconds: int = DEFAULT_REPORT_CACHE_TTL_SECONDS,
) -> tuple[schema.Report, list[tuple[str, schema.Report]] | None, Path] | None:
    cache_path = _last_report_cache_path()
    if cache_path is None or not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("report cache payload must be a JSON object")
        if payload.get("schema") != REPORT_CACHE_VERSION:
            return None
        if not _is_report_cache_fresh(payload.get("timestamp"), ttl_seconds):
            return None
        cached_topic = str(payload.get("topic") or "").strip().lower()
        if topic is not None and cached_topic != topic.strip().lower():
            return None
        reports_payload = payload.get("reports") or []
        if not reports_payload:
            return None
        entity_reports = [
            (str(item.get("entity") or ""), schema.report_from_dict(item["report"]))
            for item in reports_payload
            if isinstance(item, dict) and isinstance(item.get("report"), dict)
        ]
        if not entity_reports:
            return None
        if payload.get("comparison"):
            if len(entity_reports) < 2:
                return None
            if len(entity_reports) != len(reports_payload):
                return None
            return entity_reports[0][1], entity_reports, cache_path
        return entity_reports[0][1], None, cache_path
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(
            f"[last30days] Could not read report cache {cache_path}: "
            f"{type(exc).__name__}: {exc}\n"
        )
        return None


def _config_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _freshness_enabled(args: argparse.Namespace, config: dict[str, object]) -> bool:
    if args.verify_freshness is not None:
        return bool(args.verify_freshness)
    return _config_truthy(config.get("LAST30DAYS_VERIFY_FRESHNESS"))


def _update_cached_freshness(
    cache_path: Path,
    report: schema.Report,
    entity_reports: list[tuple[str, schema.Report]] | None,
) -> bool:
    """Rewrite cached report bodies without extending the research-cache TTL."""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != REPORT_CACHE_VERSION:
            return False
        existing = payload.get("reports") or []
        if entity_reports:
            cached_reports = entity_reports
        else:
            label = (
                str(existing[0].get("entity") or report.topic)
                if existing and isinstance(existing[0], dict)
                else report.topic
            )
            cached_reports = [(label, report)]
        payload["reports"] = [
            {"entity": label, "report": schema.to_dict(cached_report)}
            for label, cached_report in cached_reports
        ]
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        sys.stderr.write(
            f"[last30days] warning: could not update freshness cache: {exc}\n"
        )
        return False


def _verify_report_set(
    report: schema.Report,
    entity_reports: list[tuple[str, schema.Report]] | None,
    *,
    allow_network: bool,
) -> None:
    reports = [item for _, item in entity_reports] if entity_reports else [report]
    for current_report in reports:
        freshness.verify_report(current_report, allow_network=allow_network)
    if not any(current_report.freshness_verdicts for current_report in reports):
        # An empty verdict list is a legitimate outcome, but a silent one has
        # already misled operators once; say why there is nothing to show.
        sys.stderr.write(
            "[last30days] Freshness verification found no re-checkable claims"
            " in this report; the verdict list is empty.\n"
        )


def _run_cached_freshness(
    args: argparse.Namespace,
    config: dict[str, object],
) -> int:
    cached = _load_last_report_cache(
        None,
        ttl_seconds=_report_cache_ttl_seconds(config),
    )
    if cached is None:
        sys.stderr.write("[last30days] No fresh cached report; run a research pass first.\n")
        return 2
    report, entity_reports, cache_path = cached
    _verify_report_set(report, entity_reports, allow_network=not args.mock)
    if _update_cached_freshness(cache_path, report, entity_reports):
        sys.stderr.write(f"[last30days] Updated freshness verdicts in {cache_path}\n")
    else:
        sys.stderr.write("[last30days] warning: freshness cache update failed\n")
    return _render_save_and_print(args, report, entity_reports, None, config)


def _drill_config(config: dict[str, object], sources: list[str]) -> dict[str, object]:
    """Enable configured comment enrichments for a deep follow-up."""
    drill_config = dict(config)
    include = {
        value.strip().lower()
        for value in str(config.get("INCLUDE_SOURCES") or "").split(",")
        if value.strip()
    }
    comment_flags = {
        "youtube": "youtube_comments",
        "tiktok": "tiktok_comments",
        "instagram": "instagram_comments",
    }
    include.update(comment_flags[source] for source in sources if source in comment_flags)
    if include:
        drill_config["INCLUDE_SOURCES"] = ",".join(sorted(include))
    drill_config["_drill_mode"] = True
    return drill_config


def _run_drill(
    args: argparse.Namespace,
    config: dict[str, object],
) -> int:
    from lib import planner

    cached = _load_last_report_cache(
        None,
        ttl_seconds=_report_cache_ttl_seconds(config),
    )
    if cached is None:
        sys.stderr.write(
            "[last30days] No fresh cached report; run a research pass first.\n"
        )
        return 2
    report, entity_reports, cache_path = cached
    if entity_reports:
        sys.stderr.write(
            "[last30days] Drill mode needs a single-topic cached report; "
            "run a research pass for one entity first.\n"
        )
        return 2

    lookback_days = args.lookback_days
    if lookback_days is None:
        range_from = datetime.date.fromisoformat(report.range_from)
        range_to = datetime.date.fromisoformat(report.range_to)
        lookback_days = (range_to - range_from).days
    as_of_date = args.as_of_date or report.range_to

    try:
        matched_clusters = planner.resolve_drill_clusters(report, args.drill)
        drill_plan = planner.build_drill_plan(
            report,
            args.drill,
            clusters=matched_clusters,
        )
    except planner.DrillTargetError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2

    sources = list(drill_plan.source_weights)
    drill_config = _drill_config(config, sources)
    diag = pipeline.diagnose(drill_config, sources, safe=False)
    progress = ui.ProgressDisplay(
        f"{report.topic} — drill: {args.drill}",
        show_banner=True,
    )
    progress.start_processing()
    resolved = report.artifacts.get("resolved") or {}
    try:
        drill_report = pipeline.run(
            # Keep source gating anchored to the cached entity (for example,
            # StockTwits needs the original cashtag/finance context). The
            # external drill plan below remains cluster-focused.
            topic=report.topic,
            config=drill_config,
            depth="deep",
            requested_sources=sources,
            mock=args.mock,
            x_handle=(
                (args.x_handle or resolved.get("x_handle") or None)
                if "x" in sources else None
            ),
            x_related=(
                [value.strip() for value in args.x_related.split(",") if value.strip()]
                if (args.x_related and "x" in sources) else None
            ),
            web_backend=args.web_backend,
            external_plan=schema.to_dict(drill_plan),
            subreddits=(
                ([value.strip().removeprefix("r/") for value in args.subreddits.split(",") if value.strip()]
                 if args.subreddits else list(resolved.get("subreddits") or []) or None)
                if "reddit" in sources else None
            ),
            tiktok_hashtags=(
                [value.strip().lstrip("#") for value in args.tiktok_hashtags.split(",") if value.strip()]
                if args.tiktok_hashtags else None
            ),
            tiktok_creators=(
                [value.strip().lstrip("@") for value in args.tiktok_creators.split(",") if value.strip()]
                if args.tiktok_creators else None
            ),
            ig_creators=(
                [value.strip().lstrip("@") for value in args.ig_creators.split(",") if value.strip()]
                if args.ig_creators else None
            ),
            lookback_days=lookback_days,
            as_of_date=as_of_date,
            github_user=(
                (args.github_user or resolved.get("github_user") or None)
                if "github" in sources else None
            ),
            github_repos=(
                ([value.strip() for value in args.github_repo.split(",") if value.strip()]
                 if args.github_repo else list(resolved.get("github_repos") or []) or None)
                if "github" in sources else None
            ),
            trustpilot_domain=(
                (args.trustpilot_domain or resolved.get("trustpilot_domain") or None)
                if "trustpilot" in sources else None
            ),
            internal_subrun=True,
            corpus_dirs=args.corpus,
            corpus_all_time=args.corpus_all_time,
        )
    except Exception:
        progress.end_processing()
        raise

    _show_runtime_ui(drill_report, progress, diag, suppress_web_promo=True)
    merged = pipeline.merge_drill_report(
        report,
        drill_report,
        matched_clusters,
        target=args.drill,
    )
    if _freshness_enabled(args, config):
        _verify_report_set(merged, None, allow_network=not args.mock)
    else:
        merged.freshness_verdicts = []
    if _write_last_run(report.topic, merged):
        sys.stderr.write(f"[last30days] Updated drill cache in {cache_path}\n")
    else:
        sys.stderr.write(
            "[last30days] warning: drill cache update failed; the next drill "
            "will see the pre-drill report\n"
        )

    store_default = str(
        os.environ.get("LAST30DAYS_STORE")
        or config.get("LAST30DAYS_STORE")
        or ""
    ).lower()
    if args.store or store_default in {"1", "true", "yes"}:
        counts = persist_report(merged, store_db=_scoped_store_db(args))
        sys.stderr.write(
            f"[last30days] Stored {counts['new']} new, "
            f"{counts['updated']} updated findings\n"
        )

    synthesis_md = None
    if args.synthesis_file:
        if args.emit == "html":
            synthesis_md = read_synthesis_file(args.synthesis_file)
        else:
            sys.stderr.write(
                "[last30days] Warning: --synthesis-file is only used with "
                "--emit=html; ignoring.\n"
            )
    return _render_save_and_print(args, merged, None, synthesis_md, config)


def _save_discovery_output(
    rendered: str,
    *,
    domain: str,
    emit: str,
    save_dir: str,
    suffix: str = "",
) -> Path:
    directory = Path(save_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    extension = "json" if emit == "json" else "md"
    suffix_part = f"-{suffix}" if suffix else ""
    stem = f"{slugify(domain)}-discover-raw{suffix_part}"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    candidates = [directory / f"{stem}.{extension}", directory / f"{stem}-{date_str}.{extension}"]
    candidates.extend(directory / f"{stem}-{date_str}-{index}.{extension}" for index in range(1, 100))
    encoded = rendered.encode("utf-8")
    for candidate in candidates:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
        return candidate
    raise RuntimeError("Could not find a unique discovery output filename")


def _run_discover(args: argparse.Namespace, config: dict[str, object]) -> int:
    domain = " ".join(str(args.discover or "").split())
    # Empty domain = global trending: sweep every river feed's hot list with no
    # keyword gate. The confidence floor is what keeps junk out, not a keyword.
    if args.as_of_date:
        sys.stderr.write(
            "[last30days] --as-of cannot be used with --discover because discovery "
            "sweeps current live listings.\n"
        )
        return 2
    if args.emit == "html" or args.publish_html:
        sys.stderr.write("[last30days] discovery mode does not support HTML publishing yet.\n")
        return 2
    if args.store:
        sys.stderr.write("[last30days] Warning: --store is not used by discovery mode.\n")
    if args.synthesis_file:
        sys.stderr.write("[last30days] Warning: --synthesis-file is not used by discovery mode.\n")

    requested_sources = resolve_requested_sources(args.search, config)
    # The user's original source boundary, honored by the per-topic research
    # passes (which reach beyond the discovery-capable listing feeds - e.g.
    # Techmeme, arXiv, YouTube, Polymarket). None = every available source.
    enrich_requested_sources = list(requested_sources) if requested_sources else None
    if requested_sources:
        discovery_sources = [
            source for source in requested_sources
            if source in pipeline.DISCOVERY_SOURCES
        ]
        if not discovery_sources:
            # A configured source boundary holds even when it leaves nothing
            # to sweep: silently widening to all feeds would query sources
            # the user filtered out.
            origin = "--search" if args.search is not None else "LAST30DAYS_DEFAULT_SEARCH"
            sys.stderr.write(
                f"[last30days] {origin} has no discovery-capable sources "
                f"(unsupported: {', '.join(requested_sources)}); discovery "
                f"sweeps use: {', '.join(pipeline.DISCOVERY_SOURCES)}. Pass "
                "--search with one of those (or clear the source filter) to "
                "run a sweep.\n"
            )
            return 2
        requested_sources = discovery_sources
    subreddits = (
        [value.strip().removeprefix("r/") for value in args.subreddits.split(",") if value.strip()]
        if args.subreddits else None
    )
    depth = "deep" if args.deep else "quick" if args.quick else "default"
    try:
        report = pipeline.run_discover(
            domain=domain,
            config=config,
            depth=depth,
            requested_sources=requested_sources,
            mock=args.mock,
            subreddits=subreddits,
            lookback_days=args.lookback_days or 30,
            as_of_date=args.as_of_date,
            enrich=not args.discover_shallow,
            enrich_requested_sources=enrich_requested_sources,
        )
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2

    if args.emit == "json":
        payload = schema.to_dict(report) if args.json_profile == "raw" else schema.to_discovery_export(report)
        rendered = json.dumps(payload, indent=2, sort_keys=True)
    else:
        rendered = render.render_discovery(report)

    if args.output:
        output_path = save_rendered_output(rendered, args.output)
        sys.stderr.write(f"[last30days] Saved output to {output_path}\n")
    if args.save_dir:
        save_path = _save_discovery_output(
            rendered,
            domain=domain or "trending",
            emit=args.emit,
            save_dir=args.save_dir,
            suffix=args.save_suffix or "",
        )
        sys.stderr.write(f"[last30days] Saved output to {save_path}\n")
    print(rendered)

    strict = str(config.get("LAST30DAYS_STRICT_EXIT") or "").strip().lower()
    degraded = [
        source for source, outcome in report.source_status.items()
        if outcome.state not in _STRICT_EXIT_OK_STATES
    ]
    if strict in {"1", "true", "yes", "on"} and degraded:
        sys.stderr.write(
            f"[last30days] strict-exit: degraded sources: {', '.join(sorted(degraded))}\n"
        )
        return 3
    return 0


_STRICT_EXIT_OK_STATES = {"ok", "no-results", "skipped-unconfigured"}


def _strict_exit_code(
    report: schema.Report,
    entity_reports: list[tuple[str, schema.Report]] | None,
    config: dict[str, object],
) -> int:
    """Opt-in machine-detectable degraded-run signal (issue #384).

    When LAST30DAYS_STRICT_EXIT is truthy, a run whose report carries any
    source outcome that is neither clean nor a plain no-results exits 3 so
    cron/CI wrappers can distinguish degraded coverage from success. Default
    behavior (exit 0, warning rendered in the report footer) is unchanged.
    """
    raw = str(config.get("LAST30DAYS_STRICT_EXIT") or "").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return 0
    reports = [report] + [rep for _, rep in (entity_reports or [])]
    degraded = sorted({
        name
        for rep in reports
        for name, outcome in (rep.source_status or {}).items()
        if outcome.state not in _STRICT_EXIT_OK_STATES
    })
    if not degraded:
        return 0
    sys.stderr.write(
        f"[last30days] strict-exit: degraded sources: {', '.join(degraded)}\n"
    )
    sys.stderr.flush()
    return 3


def _audience_register_for_run(
    args: argparse.Namespace,
    config: dict[str, object],
    entity_reports: list[tuple[str, schema.Report]] | None,
) -> registers.AudienceRegister:
    """Resolve CLI > config for single-topic standard brief renderers."""

    from lib import planner

    topic = " ".join(getattr(args, "topic", [])).strip()
    comparison_topic_requested = bool(
        len(planner._comparison_entities(topic)) >= 2
        or args.competitors is not None
        or args.competitors_list
        or args.competitors_plan
    )
    if (
        entity_reports
        or comparison_topic_requested
        or args.drill
        or args.emit not in {"compact", "md", "html"}
    ):
        return registers.get_register()
    explicit = getattr(args, "register", None)
    configured = config.get("LAST30DAYS_REGISTER")
    name = explicit or (str(configured) if configured else "default")
    # Preserve configs written by the pre-register ELI5 follow-up command.
    legacy_eli5 = str(config.get("ELI5_MODE") or "").strip().lower()
    if not explicit and not configured and legacy_eli5 in {"1", "true", "yes", "on"}:
        name = "eli5"
    return registers.get_register(name)


def _render_save_and_print(
    args: argparse.Namespace,
    report: schema.Report,
    entity_reports: list[tuple[str, schema.Report]] | None,
    synthesis_md: str | None,
    config: dict[str, object],
) -> int:
    fun_level = str(config.get("FUN_LEVEL", "medium")).lower()
    try:
        audience = _audience_register_for_run(args, config, entity_reports)
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2
    if audience.name != "default":
        sys.stderr.write(f"[last30days] Audience register: {audience.name}\n")
        sys.stderr.flush()
    # Comparison HTML is the one case where the saved file's title and content
    # have to be overridden away from the leading entity's report. Compute the
    # gate once so the footer-display and save-output paths can't disagree.
    is_comparison_html = bool(entity_reports) and args.emit == "html"
    footer_save_path = None
    if args.output:
        footer_save_path = compute_output_path_display(args.output)
    elif args.save_dir:
        save_topic_for_display = comparison_topic(entity_reports) if is_comparison_html else report.topic
        footer_save_path = compute_save_path_display(
            args.save_dir, save_topic_for_display, args.save_suffix or "", args.emit
        )

    if entity_reports:
        rendered = emit_comparison_output(
            entity_reports,
            args.emit,
            fun_level=fun_level,
            save_path=footer_save_path,
            synthesis_md=synthesis_md,
            json_profile=args.json_profile,
        )
    else:
        rendered = emit_output(
            report,
            args.emit,
            fun_level=fun_level,
            save_path=footer_save_path,
            synthesis_md=synthesis_md,
            json_profile=args.json_profile,
            register=audience.name,
        )
    has_private_corpus = _report_has_private_corpus(report) or bool(
        entity_reports
        and any(_report_has_private_corpus(entity) for _label, entity in entity_reports)
    )
    private_saved_format = has_private_corpus
    publish_companion_paths: list[Path] = []
    if args.output:
        output_path = save_rendered_output(
            rendered,
            args.output,
            private=private_saved_format,
        )
        if args.emit == "html":
            publish_companion_paths.append(output_path)
        sys.stderr.write(f"[last30days] Saved output to {output_path}\n")
        sys.stderr.flush()
    if args.save_dir:
        # Save the main topic's raw file (single-entity or comparison main).
        save_path = save_output(
            report,
            args.emit,
            args.save_dir,
            suffix=args.save_suffix or "",
            synthesis_md=synthesis_md,
            topic_override=comparison_topic(entity_reports) if is_comparison_html else None,
            rendered_content=rendered if is_comparison_html else None,
            json_profile=args.json_profile,
            register=audience.name,
            private=private_saved_format,
        )
        if args.emit == "html":
            publish_companion_paths.append(save_path)
        sys.stderr.write(f"[last30days] Saved output to {save_path}\n")
        comparison_peer_paths: list[Path] = []
        # Competitor / vs-mode: also save a per-entity raw file for each peer.
        # Matches historical vs-mode behavior (N passes -> N save files).
        if entity_reports and len(entity_reports) > 1:
            for label, entity_report in entity_reports[1:]:
                peer_path = save_output(
                    entity_report, args.emit, args.save_dir,
                    suffix=args.save_suffix or "",
                    synthesis_md=synthesis_md,
                    json_profile=args.json_profile,
                    private=_report_has_private_corpus(entity_report),
                )
                comparison_peer_paths.append(peer_path)
                sys.stderr.write(f"[last30days] Saved output to {peer_path}\n")
            peers_display = ", ".join(str(path) for path in comparison_peer_paths)
            sys.stderr.write(
                f"[last30days] Comparison artifact set: main={save_path}; "
                f"peers={peers_display}\n"
            )
        sys.stderr.flush()
    if args.publish_html:
        try:
            has_private_corpus = "corpus" in report.source_status or bool(
                entity_reports
                and any("corpus" in entity.source_status for _label, entity in entity_reports)
            )
            publish_rendered = rendered
            if has_private_corpus:
                sys.stderr.write(
                    "[last30days] Excluding local corpus evidence and synthesis from published HTML.\n"
                )
                if entity_reports:
                    publish_rendered = emit_comparison_output(
                        [
                            (label, schema.without_sources(entity, {"corpus"}))
                            for label, entity in entity_reports
                        ],
                        "html",
                        fun_level=fun_level,
                        save_path=footer_save_path,
                        synthesis_md=None,
                        json_profile=args.json_profile,
                    )
                else:
                    publish_rendered = emit_output(
                        schema.without_sources(report, {"corpus"}),
                        "html",
                        fun_level=fun_level,
                        save_path=footer_save_path,
                        synthesis_md=None,
                        json_profile=args.json_profile,
                        register=audience.name,
                    )
            publish_result = publish_rendered_html(
                publish_rendered,
                password=_publish_password_for_args(args, config),
                companion_paths=publish_companion_paths,
            )
            sys.stderr.write(f"[last30days] Published HTML to {publish_result['url']}\n")
            for warning in publish_result.get("_metadata_errors") or []:
                sys.stderr.write(f"[last30days] Publish metadata warning: {warning}\n")
            if publish_result.get("update_key"):
                sys.stderr.write(
                    "[last30days] ht-ml.app returned an update key; not writing it "
                    "to stdout, HTML, or publish metadata.\n"
                )
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"[last30days] HTML publish failed: {exc}\n")
            sys.stderr.flush()
    print(rendered)
    return _strict_exit_code(report, entity_reports, config)


def _propagate_config_to_environ(config: dict[str, object]) -> None:
    """Push relevant env keys to os.environ so provider modules can read them.

    The env.get_config() function reads from a .env file, but providers.py
    reads from os.environ directly. Without this, OPENAI_BASE_URL and
    XAI_BASE_URL overrides are silently ignored. This is a no-op for
    keys that are already set in process env.
    """
    for key in ("OPENAI_BASE_URL", "XAI_BASE_URL"):
        val = config.get(key)
        if val and not os.environ.get(key):
            os.environ[key] = val


def _setup_allows_browser_cookies(args: argparse.Namespace, extra_argv: list[str]) -> bool:
    return (
        not args.no_browser_cookies
        and not args.diagnose
        and not args.preflight
        and "--allow-browser-cookies" in extra_argv
    )


SETUP_PASSTHROUGH_FLAGS = {
    "--allow-browser-cookies",
    "--device-auth",
    "--github",
    "--github-start",
    "--github-poll",
    "--openclaw",
}

SKILL_ONLY_FLAGS = {
    "--agent",
}

# Doctor passthrough: `doctor --json` / `doctor --cached` mirror the setup
# passthrough pattern (neither is a global parser flag; they only mean
# something to doctor). `--cached` serves the stored doctor-cache.json report
# within its TTL and falls through to a live run otherwise.
DOCTOR_PASSTHROUGH_FLAGS = {
    "--json",
    "--cached",
    "--postmortem",
    "--probe",
}


def _validate_extra_argv(parser: argparse.ArgumentParser, topic: str, extra_argv: list[str]) -> None:
    if not extra_argv:
        return
    if topic.lower() == "setup":
        unsupported = [arg for arg in extra_argv if arg not in SETUP_PASSTHROUGH_FLAGS]
        if unsupported:
            parser.error(
                "unsupported setup argument(s): "
                + ", ".join(unsupported)
                + f"; supported setup passthrough flags are {', '.join(sorted(SETUP_PASSTHROUGH_FLAGS))}"
            )
        return
    if topic.lower() == "doctor":
        unsupported = [arg for arg in extra_argv if arg not in DOCTOR_PASSTHROUGH_FLAGS]
        if unsupported:
            parser.error(
                "unsupported doctor argument(s): "
                + ", ".join(unsupported)
                + f"; supported doctor passthrough flags are {', '.join(sorted(DOCTOR_PASSTHROUGH_FLAGS))}"
            )
        return
    skill_only = [arg for arg in extra_argv if arg in SKILL_ONLY_FLAGS]
    other_unknown = [arg for arg in extra_argv if arg not in SKILL_ONLY_FLAGS]
    if skill_only:
        message = (
            "unsupported Python CLI argument(s): "
            + ", ".join(skill_only)
            + "; these are skill arguments and must not be forwarded to scripts/last30days.py"
        )
        if other_unknown:
            message += "; also unsupported: " + ", ".join(other_unknown)
        parser.error(message)
    parser.error("unsupported Python CLI argument(s): " + ", ".join(extra_argv))


def _config_policy_for_args(args: argparse.Namespace, topic: str, extra_argv: list[str]) -> env.ConfigLoadPolicy:
    normalized_topic = topic.lower()
    is_library_command = (
        normalized_topic == "library feed"
        or normalized_topic == "library search"
        or normalized_topic.startswith("library search ")
    )
    is_cached_verification = bool(getattr(args, "verify_freshness", None)) and not normalized_topic
    if args.no_browser_cookies:
        browser_mode = "off"
    elif (
        args.diagnose or args.preflight or normalized_topic == "doctor"
        or is_library_command or is_cached_verification
    ):
        # doctor is plan-only like --diagnose: it must never read cookies.
        # Cache-only freshness verification hits only point APIs (Polymarket,
        # GitHub, StockTwits) - no cookie-backed source, so no Keychain prompt.
        browser_mode = "plan_only"
    elif normalized_topic == "setup":
        browser_mode = "read" if _setup_allows_browser_cookies(args, extra_argv) else "off"
    else:
        browser_mode = "read"
    return env.ConfigLoadPolicy(
        browser_cookies=browser_mode,
        inspect_ignored_project_config=args.diagnose or args.preflight or normalized_topic == "doctor",
    )


def _run_library_feed(args: argparse.Namespace, config: dict[str, object]) -> int:
    """Generate the local research index/feed and optionally publish it."""
    from lib import feed, html_publish, library

    if args.publish_html:
        sys.stderr.write(
            "[last30days] library feed uses --publish, not --publish-html.\n"
        )
        return 2
    if args.output:
        sys.stderr.write(
            "[last30days] library feed writes index.html and feed.xml to --save-dir; "
            "--output is not supported.\n"
        )
        return 2

    memory_dir = Path(args.save_dir).expanduser() if args.save_dir else library.DEFAULT_MEMORY_DIR
    output_dir = memory_dir.resolve()
    # Scoped libraries (--save-dir) must not mix in the global briefing
    # archive: a client-specific or publishable feed pulling unrelated default
    # briefings could publish them publicly. The default library keeps the
    # archive; a scoped one reads only its own directory.
    briefs_dir = (
        library.DEFAULT_BRIEFS_DIR if not args.save_dir else memory_dir / "briefings"
    )
    entries, notes = library.scan_library(memory_dir, briefs_dir)
    feed_author = str(
        config.get("LAST30DAYS_LIBRARY_OWNER") or "last30days research library"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    library_id = library.get_or_create_library_id(output_dir)
    rendered_briefs_dir = output_dir / "briefs"
    has_private_entries = any(
        render.PRIVATE_CORPUS_START in entry.content for entry in entries
    )
    _ensure_output_directory(rendered_briefs_dir, private=has_private_entries)

    def _preserve_hand_written_page(existing_path: Path, generated_marker: str) -> None:
        """Back up any page library feed did not generate before overwriting it."""
        if not existing_path.exists():
            return
        try:
            marker_found = generated_marker in existing_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            marker_found = False
        if marker_found:
            return
        backup = existing_path.with_suffix(existing_path.suffix + ".bak")
        counter = 1
        while backup.exists():
            backup = existing_path.with_suffix(f"{existing_path.suffix}.bak{counter}")
            counter += 1
        existing_path.replace(backup)
        sys.stderr.write(
            f"[last30days] {existing_path.name} was not generated by "
            f"library feed; preserved the original at {backup.name}\n"
        )

    publishable_brief_documents: dict[str, str] = {}
    for entry in entries:
        rendered = html_render.render_library_brief(entry)
        target = rendered_briefs_dir / entry.output_name
        _preserve_hand_written_page(target, html_render.LIBRARY_BRIEF_MARKER)
        save_rendered_output(
            rendered,
            str(target),
            private=render.PRIVATE_CORPUS_START in entry.content,
        )
        publishable_brief_documents[entry.entry_id] = html_render.render_library_brief(
            entry, include_private=False
        )

    current_brief_names = {entry.output_name for entry in entries}
    for path in rendered_briefs_dir.glob("*.html"):
        is_orphan = path.name not in current_brief_names
        if not (is_orphan and library.is_generated_brief_name(path.name)):
            continue
        # A generated-looking name is not proof of ownership; only prune
        # pages that carry the renderer's own marker.
        try:
            generated = html_render.LIBRARY_BRIEF_MARKER in path.read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError):
            generated = False
        if generated:
            path.unlink()

    feed_xml = feed.render_atom(entries, library_id=library_id, author=feed_author)
    index_html = html_render.render_library_index(entries)
    feed_path = output_dir / "feed.xml"
    index_path = output_dir / "index.html"
    _preserve_hand_written_page(feed_path, "urn:last30days:research-library")
    _preserve_hand_written_page(
        index_path, "Generated locally by <strong>last30days</strong>"
    )
    feed_path.write_text(feed_xml, encoding="utf-8")
    index_path.write_text(index_html, encoding="utf-8")

    for note in notes:
        sys.stderr.write(f"[last30days] Library note: {note}\n")
    sys.stderr.write(
        f"[last30days] Library feed generated {len(entries)} brief(s): "
        f"{index_path} and {feed_path}\n"
    )

    if args.publish:
        password = _publish_password_for_args(args, config)
        entry_urls: dict[str, str] = {}
        try:
            brief_results = html_publish.publish_html_documents(
                publishable_brief_documents,
                password=password,
            )
            entry_urls = {
                entry_id: str(result["url"])
                for entry_id, result in brief_results.items()
            }
            if batch_error := getattr(brief_results, "error", None):
                raise batch_error
            published_index = html_render.render_library_index(
                entries,
                entry_urls=entry_urls,
                feed_url=None,
            )
            index_result = html_publish.publish_html(published_index, password=password)
            index_url = str(index_result["url"])
        except (html_publish.HtmlPublishError, KeyError, OSError) as exc:
            sys.stderr.write(f"[last30days] Library publish failed: {exc}\n")
            if entry_urls:
                sys.stderr.write(
                    f"[last30days] Partial publish: {len(entry_urls)} public brief "
                    "page(s) were created before the failure.\n"
                )
            return 1

        # Keep the local artifacts useful as a record of the live publication.
        feed_path.write_text(
            feed.render_atom(
                entries,
                library_id=library_id,
                entry_urls=entry_urls,
                author=feed_author,
            ),
            encoding="utf-8",
        )
        index_path.write_text(
            html_render.render_library_index(entries, entry_urls=entry_urls),
            encoding="utf-8",
        )
        sys.stderr.write(f"[last30days] Published library to {index_url}\n")
        sys.stderr.write(f"[last30days] Local Atom feed: {feed_path}\n")
        print(
            f"Library: {index_url}\nFeed: {feed_path}\n"
            "Atom feed is local; host feed.xml on any static host (for example, GitHub Pages) "
            "to make it subscribable."
        )
        return 0

    print(
        f"Library: {index_path}\nFeed: {feed_path}\n"
        "Atom feed is local; host feed.xml on any static host (for example, GitHub Pages) "
        "to make it subscribable."
    )
    return 0


def _run_library_search(
    args: argparse.Namespace,
    config: dict[str, object],
    query: str,
) -> int:
    """Search saved briefs and store sightings without network access."""
    from lib import library, library_index

    if not query.strip():
        sys.stderr.write("[last30days] library search requires a non-empty query.\n")
        return 2
    if args.publish or args.publish_html:
        sys.stderr.write("[last30days] library search does not publish output.\n")
        return 2
    if args.emit != "compact":
        sys.stderr.write("[last30days] library search currently supports text output only.\n")
        return 2
    if args.output:
        sys.stderr.write(
            "[last30days] library search prints to stdout; --output is not supported.\n"
        )
        return 2

    memory_dir = Path(args.save_dir).expanduser() if args.save_dir else library.DEFAULT_MEMORY_DIR
    try:
        matches, synced = library_index.sync_and_search(
            query,
            memory_dir=memory_dir,
            briefs_dir=(
                memory_dir / "briefings" if args.save_dir else library.DEFAULT_BRIEFS_DIR
            ),
            db_path=(
                memory_dir.resolve() / ".last30days-library.db"
                if args.save_dir else library_index.DEFAULT_LIBRARY_DB
            ),
            # A scoped search must never merge in the shared store: one
            # client's sightings would leak into another client's scope. A
            # scoped store is read only if it exists inside the save dir.
            store_db_path=(
                memory_dir.resolve() / "research.db"
                if args.save_dir else library_index.DEFAULT_STORE_DB
            ),
        )
    except library_index.LibrarySearchUnavailable as exc:
        sys.stderr.write(f"[last30days] Library search unavailable: {exc}.\n")
        return 2
    except (OSError, sqlite3.DatabaseError) as exc:
        sys.stderr.write(f"[last30days] Library search failed: {exc}.\n")
        return 1
    for note in synced.notes:
        sys.stderr.write(f"[last30days] Library note: {note}\n")
    if synced.rebuilt:
        sys.stderr.write("[last30days] Rebuilt a corrupt library search index.\n")
    print(render.render_library_search(query, matches), end="")
    return 0


def main() -> int:
    parser = build_parser()
    # Use parse_known_args so setup sub-flags (--device-auth, --github,
    # --openclaw) pass through without argparse hard-exiting.
    args, extra_argv = parser.parse_known_args()
    if args.record_fixtures:
        with http.recording_requests(Path(args.record_fixtures)):
            return _main(parser, args, extra_argv)
    return _main(parser, args, extra_argv)


def _main(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    extra_argv: list[str],
) -> int:
    if args.debug:
        os.environ["LAST30DAYS_DEBUG"] = "1"

    if args.welcome:
        from lib import setup_wizard
        print(setup_wizard.render_welcome())
        return 0

    topic = " ".join(args.topic).strip()
    original_topic = topic
    _validate_extra_argv(parser, topic, extra_argv)
    if args.publish and topic.lower() != "library feed":
        sys.stderr.write(
            "[last30days] --publish is only supported by the 'library feed' command.\n"
        )
        return 2
    config = env.get_config(policy=_config_policy_for_args(args, topic, extra_argv))
    resolved_corpus_dirs = corpus.resolve_directories(
        args.corpus, config.get("LAST30DAYS_CORPUS_DIRS")
    )
    # EXCLUDE_SOURCES=corpus disables corpus retrieval entirely; the hosted
    # privacy bypass below must use the same predicate, or hosted users with
    # configured-but-excluded dirs silently lose the remote backend.
    excluded_sources = {
        value.strip().lower()
        for value in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        if value.strip()
    }
    if "corpus" in excluded_sources:
        resolved_corpus_dirs = []
    if resolved_corpus_dirs:
        config["_CORPUS_DIRS"] = [str(path) for path in resolved_corpus_dirs]
    if _config_truthy(config.get("LAST30DAYS_CORPUS_IN_EXPORT")):
        config["_CORPUS_IN_EXPORT"] = True
    _propagate_config_to_environ(config)

    # Env-var fallback for --save-dir, mirroring the LAST30DAYS_STORE pattern below.
    # Uses `is None` / `is not None` checks (not truthy `or`) at every layer so that
    # `--save-dir ""`, `LAST30DAYS_MEMORY_DIR=""` (shell-export-empty), and explicit
    # absence each correctly suppress save. An `or` chain would collapse the empty
    # shell-export into the same path as unset, silently falling through to .env.
    if args.save_dir is None:
        env_val = os.environ.get("LAST30DAYS_MEMORY_DIR")
        args.save_dir = env_val if env_val is not None else config.get("LAST30DAYS_MEMORY_DIR")

    # Surface SSH-routing config as an env var so library modules (e.g.
    # youtube_yt) can read it without taking a config dependency. This
    # routes yt-dlp through `ssh <host>` to bypass YouTube's bot-wall on
    # datacenter IPs (see lib/youtube_yt.py for details).
    if config.get("LAST30DAYS_YOUTUBE_SSH_HOST") and "LAST30DAYS_YOUTUBE_SSH_HOST" not in os.environ:
        os.environ["LAST30DAYS_YOUTUBE_SSH_HOST"] = config["LAST30DAYS_YOUTUBE_SSH_HOST"]

    if args.preflight:
        requested_sources = resolve_requested_sources(args.search, config)
        diag = pipeline.diagnose(config, requested_sources, safe=True)
        if args.save_dir or args.preflight_report_on_save_dir:
            preflight = permission_preflight.build(
                config,
                diag,
                planned_save_dir=args.save_dir,
                report_on_save_dir=args.preflight_report_on_save_dir,
            )
        else:
            preflight = diag["permission_preflight"]
        if args.emit == "json":
            print(json.dumps(preflight, indent=2, sort_keys=True))
        else:
            print(permission_preflight.render_text(preflight), end="")
        return 0

    # Handle doctor subcommand: topic-word dispatch mirroring setup (exact
    # match only, so multi-word research topics containing "doctor" still
    # research normally). Aggregates probes/descriptors/prescriptions into
    # one grouped health surface; always exits 0.
    if topic.lower() == "doctor":
        from lib import doctor
        return doctor.run(
            config,
            emit_json=(args.emit == "json" or "--json" in extra_argv),
            cached="--cached" in extra_argv,
            postmortem="--postmortem" in extra_argv,
            probe="--probe" in extra_argv,
        )

    if topic.lower() == "library feed":
        return _run_library_feed(args, config)
    if topic.lower() == "library search" or topic.lower().startswith("library search "):
        return _run_library_search(args, config, topic[len("library search") :].strip())

    # Handle setup subcommand
    if topic.lower() == "setup":
        from lib import setup_wizard
        if "--openclaw" in extra_argv:
            results = setup_wizard.run_openclaw_setup(config)
            print(json.dumps(results))
            return 0
        if any(f in extra_argv for f in ("--github", "--device-auth", "--github-start", "--github-poll")):
            if "--github-start" in extra_argv:
                results = setup_wizard.run_github_start()
            elif "--github-poll" in extra_argv:
                results = setup_wizard.run_github_poll()
            elif "--github" in extra_argv:
                results = setup_wizard.run_github_auth()
            else:
                results = setup_wizard.run_full_device_auth()
            # Persist the returned key so the paid sources activate on the next
            # run, and mask it in stdout so the secret never lands in the host
            # model's captured Bash output.
            api_key = results.get("api_key")
            status = results.get("status")
            if api_key:
                if status == "success":
                    results["persisted"] = setup_wizard.write_api_key(env.CONFIG_FILE, api_key)
                elif status == "already_registered":
                    results["persisted"] = True  # key was already saved
                else:
                    results.setdefault("persisted", False)
                # Mask for EVERY status that carries a key, not just success, so
                # the raw secret never reaches the host model's captured stdout.
                results["api_key"] = setup_wizard.mask_api_key(api_key)
            else:
                results["persisted"] = False
            print(json.dumps(results))
            return 0
        sys.stderr.write("Running auto-setup...\n")
        results = setup_wizard.run_auto_setup(
            config,
            allow_browser_cookies=_setup_allows_browser_cookies(args, extra_argv),
        )
        # Persist FROM_BROWSER only when every service's cookies came from the
        # SAME single browser — then we can fast-path future runs to it. If
        # different services matched different browsers, or none matched, leave
        # FROM_BROWSER unset so the safe default remains no browser-cookie
        # reads. We deliberately do NOT pin "auto" here (it would re-probe
        # Chrome and re-trigger the prompt) nor a single browser (it would
        # silently skip the service that used the other one).
        found_browsers = set(results.get("cookies_found", {}).values())
        from_browser = found_browsers.pop() if len(found_browsers) == 1 else None
        # Pin only a silent winner (firefox/safari). Pinning a Chromium browser
        # would make every steady-state run re-read its Keychain-encrypted store
        # and can re-trigger the "Always Allow" prompt, so Chrome is used for the
        # first-run scan but never pinned.
        if from_browser in {"chrome", "brave", "edge", "vivaldi", "opera", "arc", "chromium"}:
            from_browser = None
        setup_wizard.write_setup_config(env.CONFIG_FILE, from_browser=from_browser)
        results["env_written"] = True
        sys.stderr.write(setup_wizard.get_setup_status_text(results) + "\n")
        return 0

    # Bare --discover (no domain) is global trending, so the dispatch keys on
    # "flag present" (is not None), never on the domain string's truthiness.
    if args.discover is not None:
        if topic:
            sys.stderr.write(
                "[last30days] --discover supplies the domain and cannot be combined "
                "with a positional topic.\n"
            )
            return 2
        if args.drill:
            sys.stderr.write("[last30days] --discover and --drill are mutually exclusive.\n")
            return 2
        return _run_discover(args, config)

    if args.discover_shallow:
        # Without --discover this flag would silently no-op into a full
        # research run - reject it instead of ignoring the requested mode.
        sys.stderr.write(
            "[last30days] --discover-shallow only applies to --discover runs; "
            "add --discover [domain] or drop the flag.\n"
        )
        return 2

    if args.drill:
        if topic:
            sys.stderr.write(
                "[last30days] --drill uses the cached topic and cannot be "
                "combined with a new topic.\n"
            )
            return 2
        if args.publish_html and args.emit != "html":
            sys.stderr.write("[last30days] --publish-html requires --emit=html\n")
            return 2
        if args.dedicated_subreddits:
            config["_dedicated_subreddits"] = [
                value.strip().removeprefix("r/")
                for value in args.dedicated_subreddits.split(",")
                if value.strip()
            ]
        if args.polymarket_keywords:
            config["_polymarket_keywords"] = [
                value.strip().lower()
                for value in args.polymarket_keywords.split(",")
                if value.strip()
            ]
        return _run_drill(args, config)

    if args.verify_freshness and not topic:
        return _run_cached_freshness(args, config)

    if args.lookback_days is None:
        args.lookback_days = 30

    # Reject a misspelled configured register before remote submission or any
    # local source retrieval. Excluded modes resolve to default and remain
    # unaffected by the register setting.
    try:
        _audience_register_for_run(args, config, None)
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2

    # Remote API path: when BOTH LAST30DAYS_API_KEY and LAST30DAYS_API_BASE are
    # set (and --mock is not), the search runs through the configured remote API
    # instead of local sources; no local provider keys are needed (see
    # lib/hosted.py). With either env var unset, behavior below is byte-identical
    # to local-only runs - there is no built-in endpoint.
    if (
        topic
        and resolved_corpus_dirs
        and env.read_secret_env("LAST30DAYS_API_KEY")
        and os.environ.get("LAST30DAYS_API_BASE")
    ):
        sys.stderr.write(
            "[last30days] Local corpus configured; bypassing the hosted backend so files stay on this machine.\n"
        )
    if (
        topic
        and not args.diagnose
        and not args.mock
        and not args.record_fixtures
        and env.read_secret_env("LAST30DAYS_API_KEY")
        and os.environ.get("LAST30DAYS_API_BASE")
        and not resolved_corpus_dirs
    ):
        if _freshness_enabled(args, config):
            if args.verify_freshness is True:
                sys.stderr.write(
                    "[last30days] Freshness verification is not supported by the hosted backend; "
                    "run locally or omit --verify-freshness.\n"
                )
                return 2
            sys.stderr.write(
                "hosted backend does not support freshness verification; skipping\n"
            )
        if args.emit == "json" and args.json_profile == "agent":
            sys.stderr.write(
                "[last30days] --json-profile=agent requires the local Report; "
                "the remote API backend only supports --json-profile=raw.\n"
            )
            return 2
        from lib import hosted
        depth = "deep" if args.deep else "quick" if args.quick else "default"
        try:
            audience = _audience_register_for_run(args, config, None)
        except ValueError as exc:
            sys.stderr.write(f"[last30days] {exc}\n")
            return 2
        hosted_kwargs = {
            "emit": args.emit,
            "save_dir": args.save_dir,
            "save_suffix": args.save_suffix or "",
        }
        if audience.name != "default":
            hosted_kwargs["register"] = audience.name
        return hosted.run_hosted(topic, depth, **hosted_kwargs)

    requested_sources = resolve_requested_sources(args.search, config)
    diag = pipeline.diagnose(config, requested_sources, safe=args.diagnose)

    if args.diagnose:
        print(json.dumps(diag, indent=2, sort_keys=True))
        return 0

    if not topic:
        parser.print_usage(sys.stderr)
        return 2
    if args.publish_html and args.emit != "html":
        sys.stderr.write("[last30days] --publish-html requires --emit=html\n")
        return 2

    synthesis_md = None
    if args.synthesis_file:
        if args.emit == "html":
            synthesis_md = read_synthesis_file(args.synthesis_file)
        else:
            sys.stderr.write("[last30days] Warning: --synthesis-file is only used with --emit=html; ignoring.\n")

    if not os.environ.get("LAST30DAYS_SKIP_PREFLIGHT"):
        from lib import preflight
        refuse_msg = preflight.check_class_1_trap(topic)
        if refuse_msg:
            sys.stderr.write(refuse_msg)
            return 2

    if args.emit == "html" and synthesis_md is not None:
        cached = _load_last_report_cache(
            topic,
            ttl_seconds=_report_cache_ttl_seconds(config),
        )
        if cached is not None:
            cached_report, cached_entity_reports, cache_path = cached
            sys.stderr.write(
                f"[last30days] Reusing cached report data from {cache_path}\n"
            )
            sys.stderr.flush()
            if _freshness_enabled(args, config):
                _verify_report_set(
                    cached_report,
                    cached_entity_reports,
                    allow_network=not args.mock,
                )
                _update_cached_freshness(
                    cache_path,
                    cached_report,
                    cached_entity_reports,
                )
            return _render_save_and_print(
                args, cached_report, cached_entity_reports, synthesis_md, config
            )
        sys.stderr.write(
            "[last30days] No matching cached report data for "
            "--emit=html --synthesis-file; running fresh research.\n"
        )
        sys.stderr.flush()

    progress = ui.ProgressDisplay(topic, show_banner=True)
    progress.start_processing()

    depth = "deep" if args.deep else "quick" if args.quick else "default"
    try:
        x_related = [h.strip() for h in args.x_related.split(",") if h.strip()] if args.x_related else None
        subreddits = [s.strip().removeprefix("r/") for s in args.subreddits.split(",") if s.strip()] if args.subreddits else None
        dedicated_subreddits = [s.strip().removeprefix("r/") for s in args.dedicated_subreddits.split(",") if s.strip()] if args.dedicated_subreddits else None
        tiktok_hashtags = [h.strip().lstrip("#") for h in args.tiktok_hashtags.split(",") if h.strip()] if args.tiktok_hashtags else None
        tiktok_creators = [c.strip().lstrip("@") for c in args.tiktok_creators.split(",") if c.strip()] if args.tiktok_creators else None
        ig_creators = [c.strip().lstrip("@") for c in args.ig_creators.split(",") if c.strip()] if args.ig_creators else None
        # Parse external plan if provided via --plan flag
        external_plan = None
        if args.plan:
            import json as _json
            plan_str = args.plan
            if os.path.isfile(plan_str):
                try:
                    with open(plan_str, encoding="utf-8") as f:
                        plan_str = f.read()
                except (OSError, UnicodeDecodeError) as exc:
                    sys.stderr.write(f"[Planner] Cannot read --plan file: {exc}\n")
                    raise SystemExit(2)
            try:
                external_plan = _json.loads(plan_str)
            except _json.JSONDecodeError as exc:
                sys.stderr.write(f"[Planner] Invalid --plan JSON: {exc}\n")
                # Fail fast instead of silently dropping to the internal planner
                # and burning a paid run the user did not ask for. Mirrors the
                # --plan file-read branch above and parse_competitors_plan.
                raise SystemExit(2)

        # Auto-resolve: use web search to discover subreddits/handles before planning.
        # This is the engine-side equivalent of SKILL.md Steps 0.55/0.75 for platforms
        # without WebSearch (OpenClaw, Codex, raw CLI).
        repos_from_auto_resolve = False
        trustpilot_domain_is_hint = False
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
                # auto_resolve already canonicalized via canonicalize_github_repos(cap=5);
                # mark so we don't re-canonicalize below and clobber its relevance order.
                repos_from_auto_resolve = True
                sys.stderr.write(f"[AutoResolve] GitHub repos: {args.github_repo}\n")
            if resolution.get("trustpilot_domain") and not args.trustpilot_domain:
                # Hint provenance matters: only user-set flags are verbatim-final;
                # a resolved hint retries via the CLI search when it misses.
                args.trustpilot_domain = resolution["trustpilot_domain"]
                trustpilot_domain_is_hint = True
                sys.stderr.write(f"[AutoResolve] Trustpilot domain: {args.trustpilot_domain} (hint)\n")
            if resolution.get("context"):
                # Inject context into external_plan metadata for the planner to use
                if not external_plan:
                    external_plan = None  # planner will use its own, but with context
                # Store context for the planner prompt injection
                config["_auto_resolve_context"] = resolution["context"]
                sys.stderr.write(f"[AutoResolve] Context: {resolution['context'][:80]}...\n")

        github_user = args.github_user.lstrip("@").lower() if args.github_user else None
        github_repos = [r.strip() for r in args.github_repo.split(",") if r.strip() and "/" in r.strip()] if args.github_repo else None
        trustpilot_domain = args.trustpilot_domain.strip() if args.trustpilot_domain else None

        # Only canonicalize when repos came from a user-supplied --github-repo flag.
        # When repos_from_auto_resolve is True, auto_resolve already ran
        # canonicalize_github_repos(cap=5) and ranked by relevance; re-running here
        # with cap=None can re-sort by topic-slug match and lose that ordering.
        if github_repos and not repos_from_auto_resolve:
            from lib import resolve as resolve_lib
            original_github_repos = github_repos[:]
            github_repos = resolve_lib.canonicalize_github_repos(topic, github_repos, cap=None)
            if github_repos != original_github_repos:
                sys.stderr.write(
                    "[GitHub] Canonicalized repos: "
                    f"{','.join(original_github_repos)} -> {','.join(github_repos)}\n"
                )

        # --deep-research: auto-enable perplexity source and set deep flag
        if args.deep_research:
            if not (config.get("PERPLEXITY_API_KEY") or config.get("OPENROUTER_API_KEY")):
                print("Error: --deep-research requires PERPLEXITY_API_KEY or OPENROUTER_API_KEY", file=sys.stderr)
                sys.exit(1)
            config["_deep_research"] = True
            # Auto-enable perplexity in INCLUDE_SOURCES
            include = config.get("INCLUDE_SOURCES") or ""
            if "perplexity" not in include.lower():
                config["INCLUDE_SOURCES"] = f"{include},perplexity" if include else "perplexity"

        comp_enabled, comp_count, comp_explicit = resolve_competitors_args(args)
        comp_plan = parse_competitors_plan(args.competitors_plan)

        # Polymarket disambiguation: if user passed --polymarket-keywords,
        # store on config so the polymarket adapter can filter matches.
        if args.polymarket_keywords:
            keywords = [
                k.strip().lower()
                for k in args.polymarket_keywords.split(",")
                if k.strip()
            ]
            if keywords:
                config["_polymarket_keywords"] = keywords

        # vs-mode: if the topic string contains " vs " / " versus " and the
        # planner can split it into >=2 entities, route through the same
        # N-pass fanout path as --competitors. The first entity becomes the
        # main topic; remaining entities become the competitor list. User's
        # outer --x-handle / --subreddits apply to the first entity unless
        # --competitors-plan covers it.
        from lib import planner as _planner
        vs_entities = _planner._comparison_entities(topic)
        if len(vs_entities) >= 2 and not comp_enabled:
            topic = vs_entities[0]
            comp_enabled = True
            comp_count = len(vs_entities) - 1
            comp_explicit = vs_entities[1:]
            sys.stderr.write(
                f"[Competitors] vs-mode: routing to N-pass fanout: "
                f"{' vs '.join(vs_entities)}\n"
            )

        # Dedicated subs ride the config dict (already threaded to every source
        # fetch) so the keyless Reddit path can pull them floor-exempt without
        # widening pipeline.run / _retrieve_stream signatures.
        if dedicated_subreddits:
            config["_dedicated_subreddits"] = dedicated_subreddits

        def _main_runner() -> schema.Report:
            r = pipeline.run(
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
                as_of_date=args.as_of_date,
                github_user=github_user,
                github_repos=github_repos,
                trustpilot_domain=trustpilot_domain,
                trustpilot_domain_is_hint=trustpilot_domain_is_hint,
                internal_subrun=comp_enabled,
                hiring_signals_mode=args.hiring_signals,
                save_dir=args.save_dir,
                corpus_dirs=args.corpus,
                corpus_all_time=args.corpus_all_time,
            )
            r.artifacts["resolved"] = {
                "entity": topic,
                "x_handle": (args.x_handle or "").lstrip("@"),
                "subreddits": list(subreddits or []),
                "github_user": (github_user or ""),
                "github_repos": list(github_repos or []),
                "trustpilot_domain": (trustpilot_domain or ""),
                "context": config.get("_auto_resolve_context", "") or "",
            }
            return r

        if comp_enabled:
            from lib import competitors as competitors_mod
            from lib import fanout, resolve as resolve_mod

            if comp_explicit:
                discovered = comp_explicit
            else:
                if not resolve_mod._has_backend(config) and not args.mock:
                    sys.stderr.write(
                        "[Competitors] Cannot auto-discover peers without help.\n"
                        "\n"
                        "RECOMMENDED PATH (hosting reasoning models — Claude Code, Codex, "
                        "Hermes, Gemini, any agent with a WebSearch tool): YOU have "
                        "WebSearch. Use it to run full Step 0.55 per entity, then invoke "
                        "the engine with a vs-topic plus --competitors-plan:\n"
                        "  1. WebSearch for '{topic} competitors' or '{topic} alternatives'.\n"
                        "  2. For each peer, WebSearch for handles/subs/github (Step 0.55).\n"
                        "  3. Re-invoke: /last30days '{topic} vs {peer1} vs {peer2}' "
                        "--competitors-plan '{\"Peer1\":{\"x_handle\":\"h1\",\"subreddits\":"
                        "[\"s1\"],...},\"Peer2\":{...}}'.\n"
                        "See SKILL.md 'Competitor mode' for the full protocol.\n"
                        "\n"
                        "HEADLESS / CRON PATH (no hosting model available): set "
                        "BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / PARALLEL_API_KEY / "
                        "PERPLEXITY_API_KEY / OPENROUTER_API_KEY and re-run.\n"
                        "\n"
                        "MINIMUM ESCAPE HATCH: pass --competitors-list 'A,B,C' to skip "
                        "discovery. Without --competitors-plan, peer sub-runs fall back to "
                        "planner defaults and produce visibly thinner data than the main.\n"
                    )
                    return 2
                discovered = competitors_mod.discover_competitors(
                    topic, comp_count, config, lookback_days=args.lookback_days,
                )
                if not discovered:
                    sys.stderr.write(
                        f"[Competitors] No peers discovered for {topic!r}; aborting "
                        "comparison run. Pass --competitors-list to override.\n"
                    )
                    return 2

            sys.stderr.write(
                f"[Competitors] Comparing: {topic} vs " + " vs ".join(discovered) + "\n"
            )

            def _competitor_runner(entity: str) -> schema.Report:
                # Deep-copy config so per-entity auto_resolve context does not
                # leak across sub-runs. Each sub-run writes its own
                # `_auto_resolve_context` into its local config copy.
                entity_config = dict(config)
                plan_entry = comp_plan.get(entity.strip().lower(), {})
                resolved = {
                    "entity": entity,
                    "x_handle": "",
                    "subreddits": [],
                    "github_user": "",
                    "github_repos": [],
                    "trustpilot_domain": "",
                    "context": "",
                }
                # Skip engine-internal auto_resolve when the hosting model
                # pre-resolved via --competitors-plan (saves a redundant
                # round-trip and makes per-entity Step 0.55 purely
                # hosting-model-driven).
                plan_covers_fully = bool(plan_entry.get("x_handle")) and bool(
                    plan_entry.get("subreddits")
                )
                if (
                    not args.mock
                    and not plan_covers_fully
                    and resolve_mod._has_backend(entity_config)
                ):
                    try:
                        r = resolve_mod.auto_resolve(entity, entity_config)
                    except Exception as exc:
                        sys.stderr.write(
                            f"[Competitors] auto_resolve failed for {entity!r}: "
                            f"{type(exc).__name__}: {exc}\n"
                        )
                        r = {}
                    resolved["x_handle"] = r.get("x_handle", "") or ""
                    resolved["subreddits"] = list(r.get("subreddits") or [])
                    resolved["github_user"] = r.get("github_user", "") or ""
                    resolved["github_repos"] = list(r.get("github_repos") or [])
                    resolved["trustpilot_domain"] = r.get("trustpilot_domain", "") or ""
                    resolved["context"] = r.get("context", "") or ""
                kwargs = subrun_kwargs_for(entity, plan_entry, resolved=resolved)
                # Record effective per-entity targeting for the Resolved block.
                resolved_effective = {
                    "entity": entity,
                    "x_handle": kwargs["x_handle"] or "",
                    "subreddits": kwargs["subreddits"] or [],
                    "github_user": kwargs["github_user"] or "",
                    "github_repos": kwargs["github_repos"] or [],
                    "trustpilot_domain": kwargs["trustpilot_domain"] or "",
                    "context": kwargs["_context"],
                }
                if kwargs["_context"]:
                    entity_config["_auto_resolve_context"] = kwargs["_context"]
                sys.stderr.write(
                    f"[Competitors] {entity}: "
                    f"x=@{resolved_effective['x_handle'] or '-'} "
                    f"subs={len(resolved_effective['subreddits'])} "
                    f"gh={resolved_effective['github_user'] or '-'} "
                    f"({'plan' if plan_entry else 'auto'})\n"
                )
                report = pipeline.run(
                    topic=entity,
                    config=entity_config,
                    depth=depth,
                    requested_sources=requested_sources,
                    mock=args.mock,
                    x_handle=kwargs["x_handle"],
                    x_related=kwargs["x_related"],
                    subreddits=kwargs["subreddits"],
                    github_user=kwargs["github_user"],
                    github_repos=kwargs["github_repos"],
                    trustpilot_domain=kwargs["trustpilot_domain"],
                    trustpilot_domain_is_hint=kwargs["_trustpilot_domain_is_hint"],
                    web_backend=args.web_backend,
                    lookback_days=args.lookback_days,
                    as_of_date=args.as_of_date,
                    hiring_signals_mode=args.hiring_signals,
                    internal_subrun=True,
                    save_dir=args.save_dir,
                    corpus_dirs=args.corpus,
                    corpus_all_time=args.corpus_all_time,
                )
                report.artifacts["resolved"] = resolved_effective
                return report

            entity_reports = fanout.run_competitor_fanout(
                main_topic=topic,
                main_runner=_main_runner,
                competitors=discovered,
                competitor_runner=_competitor_runner,
            )
            if len(entity_reports) < 2:
                progress.end_processing()
                sys.stderr.write(
                    f"[Competitors] Fewer than 2 sub-runs survived ({len(entity_reports)}); "
                    "cannot render a comparison. Re-run without --competitors or check the "
                    "warnings above.\n"
                )
                return 1
            report = entity_reports[0][1]
        else:
            entity_reports = None
            report = _main_runner()
    except Exception as exc:
        progress.end_processing()
        progress.show_error(str(exc))
        raise
    if _freshness_enabled(args, config):
        _verify_report_set(report, entity_reports, allow_network=not args.mock)

    _show_runtime_ui(
        report, progress, diag,
        suppress_web_promo=bool(external_plan or comp_plan),
    )
    _write_last_run(original_topic, report, entity_reports=entity_reports)
    # LAST30DAYS_STORE env var = persistence default-on. Read both os.environ
    # (for shell-exported users) and config (for users who set it in
    # ~/.config/last30days/.env, which env.py loads but does not propagate
    # to os.environ). Mirrors the LAST30DAYS_DEBUG / LAST30DAYS_SKIP_PREFLIGHT
    # convention; env-var or config wins, with `--store` flag still working.
    _store_env = (
        os.environ.get("LAST30DAYS_STORE")
        or config.get("LAST30DAYS_STORE")
        or ""
    ).lower()
    if args.store or _store_env in ("1", "true", "yes"):
        counts = persist_report(report, store_db=_scoped_store_db(args))
        sys.stderr.write(
            f"[last30days] Stored {counts['new']} new, {counts['updated']} updated findings\n"
        )
        sys.stderr.flush()

    # Show quality nudge if applicable. Explicit hiring-signal runs are
    # intentionally jobs-focused, so generic source setup advice is noise.
    if not args.hiring_signals:
        try:
            from lib import quality_nudge
            from lib import youtube_yt as _youtube_yt
            # Populate transcript-fetch ratio so quality_nudge can detect the
            # degraded-YouTube failure mode (videos returned but transcripts
            # silently failed - typically a stale yt-dlp binary).
            youtube_items = report.items_by_source.get("youtube") or []
            _yt_fetch_stats = _youtube_yt.get_transcript_fetch_stats()
            instagram_items = report.items_by_source.get("instagram") or []
            research_results = {
                "youtube_videos_count": len(youtube_items),
                "youtube_transcripts_count": sum(
                    1 for it in youtube_items
                    if (it.metadata.get("transcript_highlights") or it.metadata.get("transcript_snippet"))
                ),
                "youtube_error": report.errors_by_source.get("youtube"),
                "x_error": report.errors_by_source.get("x"),
                # Captions-disabled videos can never produce a transcript regardless
                # of yt-dlp version; subtract them from the degraded-ratio
                # denominator so a single uploader-disabled video does not trip the
                # "stale yt-dlp" nudge.
                "youtube_captions_disabled_count": sum(
                    1 for it in youtube_items if it.metadata.get("captions_disabled")
                ),
                # Actual yt-dlp fetch outcomes for this run. The counts above are
                # computed from post-pruning items, so they can't tell "fetches
                # failed (stale binary)" from "fetches succeeded but the videos
                # were pruned downstream"; the latter was producing false
                # stale-yt-dlp nudges (#531).
                "youtube_transcript_fetch_attempts": _yt_fetch_stats["attempts"],
                "youtube_transcript_fetch_failures": _yt_fetch_stats["failures"],
                # Track Instagram returned-zero-items so quality_nudge can detect
                # the silent-failure case (SC configured but the v2 reels endpoint
                # 500'd through both the original query and the hashtag retry).
                "instagram_items_count": len(instagram_items),
            }
            quality = quality_nudge.compute_quality_score(config, research_results)
            if quality.get("nudge_text"):
                sys.stderr.write(f"\n{quality['nudge_text']}\n")
                sys.stderr.flush()
        except Exception:
            pass

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

    return _render_save_and_print(args, report, entity_reports, synthesis_md, config)


if __name__ == "__main__":
    raise SystemExit(main())
