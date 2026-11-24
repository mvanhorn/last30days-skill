#!/usr/bin/env python3
# fmt: off
# ruff: noqa: E402
"""last30days CLI."""

from __future__ import annotations

import argparse
import atexit
import datetime
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

MIN_PYTHON = (3, 12)


def ensure_supported_python(version_info: tuple[int, int, int] | object | None = None) -> None:
    if version_info is None:
        version_info = sys.version_info
    major, minor, micro = tuple(version_info[:3])
    if (major, minor) >= MIN_PYTHON:
        return
    req = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    sys.stderr.write(
        f"last30days v4 requires Python {req}+.\n"
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

from lib import company_resolve, competitors as competitors_mod, corpus, dates, discovery_handoff, env, fetch_memo, freshness, health, html_render, http, patents, permission_preflight, person, pipeline, proxy, reddit, reddit_arctic, reddit_discover, reddit_envelope, reddit_health, reddit_search, reddit_session, registers, render, report_checklist, schema, shortform_envelope, subproc, ui, web_search_keyless, x_envelope, yahoo_finance
from lib.private_files import replace_private_text

atexit.register(subproc.cleanup_children)


def _on_sigterm(signum, frame) -> None:
    """SIGTERM handler: clean descendant groups, then die as SIGTERM.

    Every run_with_timeout child runs in its own pgid (lib/subproc.py via
    os.setsid), so a group kill aimed at the engine can never reach them;
    only the lib.subproc registry can. atexit never runs on a signal death,
    so without this handler an MCP timeout would orphan node bird-search,
    yt-dlp, and the digg CLI. The MCP server SIGTERMs the engine group
    first, giving this handler room to killpg() each registered child
    group before the SIGKILL backstop. Restoring the default disposition
    and re-raising preserves killed-by-SIGTERM semantics for the parent.
    """
    subproc.cleanup_children()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _install_sigterm_handler() -> None:
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError, RuntimeError):
        pass


def parse_meta_ads_page(raw: str) -> str:
    """Extract an Ad Library page id from a flag value, or "" if there is none.

    Accepts a bare numeric id or any Ad Library URL carrying
    ``view_all_page_id``. A ``facebook.com/<vanity>`` URL is deliberately
    rejected rather than guessed at: a vanity handle is not a page id, and one
    live check resolved a brand-looking handle to a private person's profile.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if re.fullmatch(r"\d{5,20}", value):
        return value
    match = re.search(r"view_all_page_id=(\d{5,20})", value)
    return match.group(1) if match else ""


def parse_search_flag(raw: str, flag_name: str = "--search") -> list[str]:
    sources = []
    for source in raw.split(","):
        source = source.strip().lower()
        if not source:
            continue
        normalized = pipeline.SEARCH_ALIAS.get(source, source)
        if normalized not in pipeline.KNOWN_SEARCH_SOURCES:
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


def add_deep_research_source(
    requested_sources: list[str] | None,
) -> list[str] | None:
    """Add Perplexity without replacing the default-source sentinel.

    ``None`` means that the planner can use the normal configured source set.
    Deep Research enables Perplexity through ``INCLUDE_SOURCES`` separately, so
    converting this sentinel to ``["perplexity"]`` would suppress every normal
    source.
    """
    if requested_sources is None:
        return None
    if "perplexity" in requested_sources:
        return requested_sources
    return [*requested_sources, "perplexity"]


def enable_deep_research_source(config: dict) -> None:
    """Enable the exact Perplexity token or reject a hard exclusion."""
    excluded = {
        token.strip().lower()
        for token in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        if token.strip()
    }
    if "perplexity" in excluded:
        raise ValueError(
            "--deep-research conflicts with EXCLUDE_SOURCES=perplexity"
        )

    include = str(config.get("INCLUDE_SOURCES") or "")
    tokens = [token.strip() for token in include.split(",") if token.strip()]
    if "perplexity" not in {token.lower() for token in tokens}:
        tokens.append("perplexity")
        config["INCLUDE_SOURCES"] = ",".join(tokens)


def plan_has_explicit_trustpilot_domain(comp_plan: dict | None) -> bool:
    """True when any --competitors-plan entry pins a trustpilot_domain."""
    if not comp_plan:
        return False
    for entry in comp_plan.values():
        if not isinstance(entry, dict):
            continue
        domain = entry.get("trustpilot_domain")
        if isinstance(domain, str) and domain.strip():
            return True
    return False


def activate_trustpilot_for_explicit_domain(
    config: dict,
    requested_sources: list[str] | None,
    *,
    reason: str,
) -> list[str] | None:
    """Activate the opt-in Trustpilot source when the user pinned a domain.

    Passing ``--trustpilot-domain`` (or a plan-level ``trustpilot_domain``) is
    unambiguous intent — silently ignoring it when Trustpilot is not in
    ``INCLUDE_SOURCES`` / ``--search`` is the #873 failure mode. Auto-resolve
    hints must not call this helper.

    ``EXCLUDE_SOURCES=trustpilot`` still wins. Mutates ``config`` in place and
    returns the (possibly extended) ``requested_sources`` list.
    """
    excluded = {
        token.strip().lower()
        for token in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        if token.strip()
    }
    if "trustpilot" in excluded:
        sys.stderr.write(
            f"[Trustpilot] {reason} ignored: trustpilot is in EXCLUDE_SOURCES\n"
        )
        return requested_sources

    include = str(config.get("INCLUDE_SOURCES") or "")
    tokens = [token.strip() for token in include.split(",") if token.strip()]
    if "trustpilot" not in {token.lower() for token in tokens}:
        tokens.append("trustpilot")
        config["INCLUDE_SOURCES"] = ",".join(tokens)
        sys.stderr.write(
            f"[Trustpilot] {reason} activated trustpilot source "
            "(add to INCLUDE_SOURCES permanently to skip this auto-enable)\n"
        )

    if requested_sources is not None and "trustpilot" not in requested_sources:
        requested_sources = [*requested_sources, "trustpilot"]
    return requested_sources


def activate_telegram_for_explicit_sources(
    config: dict,
    requested_sources: list[str] | None,
    *,
    channels: str,
) -> list[str] | None:
    """Activate the opt-in Telegram source when the user pinned channel(s).

    Passing ``--telegram-sources`` is unambiguous intent — silently ignoring it
    when Telegram is not in ``INCLUDE_SOURCES`` / ``--search`` is the same
    failure mode as #873 (Trustpilot). Auto-activate the source.

    ``EXCLUDE_SOURCES=telegram`` still wins. Mutates ``config`` in place and
    returns the (possibly extended) ``requested_sources`` list.
    """
    excluded = {
        token.strip().lower()
        for token in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        if token.strip()
    }
    if "telegram" in excluded:
        sys.stderr.write(
            f"[Telegram] --telegram-sources={channels} ignored: telegram is in EXCLUDE_SOURCES\n"
        )
        return requested_sources

    config["TELEGRAM_SOURCES"] = channels

    include = str(config.get("INCLUDE_SOURCES") or "")
    tokens = [token.strip() for token in include.split(",") if token.strip()]
    if "telegram" not in {token.lower() for token in tokens}:
        tokens.append("telegram")
        config["INCLUDE_SOURCES"] = ",".join(tokens)
        sys.stderr.write(
            f"[Telegram] --telegram-sources={channels} activated telegram source "
            "(add to INCLUDE_SOURCES permanently to skip this auto-enable)\n"
        )

    if requested_sources is not None and "telegram" not in requested_sources:
        requested_sources = [*requested_sources, "telegram"]
    return requested_sources


def slugify(value: str, max_length: int = 180) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if len(slug) > max_length:
        # Filenames built from long topics can exceed the OS 255-byte limit
        # (macOS errno 63). Truncate and append a hash of the full value so
        # distinct long topics still get distinct, deterministic names.
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
        slug = f"{slug[:max_length].rstrip('-')}-{digest}"
    return slug or "last30days"


def sanitize_suffix(suffix: str) -> str:
    """Sanitize a user-provided ``--save-suffix`` into a path-safe token.

    The suffix is glued directly into the saved-report filename, so restrict it
    to the same ``[a-z0-9-]`` class as the topic slug. This neutralizes path
    separators and parent refs (``/``, ``..``) so a suffix can never escape the
    save directory, while leaving ordinary values ('v3', 'gemini', a client
    slug) unchanged. Unlike ``slugify`` there is no fallback token: a suffix
    that sanitizes to nothing simply drops, yielding no suffix part.
    """
    return re.sub(r"[^a-z0-9]+", "-", suffix.lower()).strip("-")


def _report_has_private_corpus(report: schema.Report) -> bool:
    items_by_source = getattr(report, "items_by_source", {})
    if isinstance(items_by_source, dict) and items_by_source.get("corpus"):
        return True
    if isinstance(report, schema.Report) and schema.partition_freshness_verdicts(report, {"corpus"})[1]:
        return True
    candidates = getattr(report, "ranked_candidates", ())
    if not isinstance(candidates, (list, tuple)):
        return False
    return any(schema.candidate_has_corpus(candidate) for candidate in candidates)


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
    render_fn: Callable[[Path], str] | None = None,
) -> Path:
    from datetime import datetime
    if save_dir is None or str(save_dir) == "" or pipeline.is_blank_save_dir(save_dir):
        raise ValueError("save_output requires a non-empty save_dir")
    path = Path(save_dir).expanduser().resolve()
    slug = slugify(topic_override or report.topic)
    extension = "json" if emit == "json" else "html" if emit == "html" else "md"
    raw_label = "raw-html" if emit == "html" else "raw"
    safe_suffix = sanitize_suffix(suffix)
    suffix_part = f"-{safe_suffix}" if safe_suffix else ""
    base = path / f"{slug}-{raw_label}{suffix_part}.{extension}"
    date_str = datetime.now().strftime('%Y-%m-%d')
    candidates = [base]
    candidates.append(path / f"{slug}-{raw_label}{suffix_part}-{date_str}.{extension}")
    for i in range(1, 100):
        candidates.append(path / f"{slug}-{raw_label}{suffix_part}-{date_str}-{i}.{extension}")
    # Markdown saves keep the complete debug artifact. JSON and HTML preserve
    # their requested wire format so file extensions match their content.
    # When render_fn is supplied, content is produced after O_EXCL allocates
    # the candidate. This lets the footer cite the file actually written
    # without racing a separate filesystem probe.
    if render_fn is None:
        if rendered_content is not None:
            static_content = rendered_content
        elif emit in {"json", "html"}:
            static_content = emit_output(
                report,
                emit,
                synthesis_md=synthesis_md,
                json_profile=json_profile,
                register=register,
            )
        else:
            static_content = render.render_full(report)
    private_corpus = _report_has_private_corpus(report) or bool(private)
    _ensure_output_directory(path, private=private_corpus)
    for candidate in candidates:
        try:
            fd = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600 if private_corpus else 0o644,
            )
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as f:
                content = render_fn(candidate) if render_fn is not None else static_content
                f.write(content.encode("utf-8"))
        except BaseException:
            # Deferred rendering happens after the candidate is reserved. Do
            # not leave an empty or partial report if rendering or writing fails.
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if candidate.suffix.lower() == ".md":
            try:
                from lib import library_index

                save_root = candidate.parent.resolve()
                db_path = library_index.saved_report_index_path(save_root)
                # A scoped save must sync a per-directory index with the
                # same paths scoped search uses; syncing the shared DB
                # from one scope's scan would prune other scopes' rows.
                if not _is_scoped_save_dir(save_root):
                    library_index.sync_library(save_root)
                else:
                    library_index.sync_library(save_root, save_root / "briefings", db_path=db_path)
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


def comparison_label_key(label: str) -> str:
    """Normalize an entity label for duplicate detection.

    Comparison labels double as keys in the fan-out's results dict, so two
    entities differing only in case, surrounding space, or a repeated space
    collide there while still looking distinct on the command line. Spaces
    are collapsed, never stripped: "Open AI" stays distinct from "OpenAI".
    """
    return " ".join(label.split()).casefold()


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
    safe_suffix = sanitize_suffix(suffix)
    suffix_part = f"-{safe_suffix}" if safe_suffix else ""
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


def _validate_save_dir(save_dir: Path | str | None) -> int | None:
    if pipeline.is_blank_save_dir(save_dir):
        sys.stderr.write(
            "[last30days] --save-dir / LAST30DAYS_MEMORY_DIR cannot be whitespace-only.\n"
        )
        return 2
    return None


def _is_scoped_save_dir(save_dir: Path | str | None) -> bool:
    """True when ``save_dir`` is set and resolves to a dir other than DEFAULT_MEMORY_DIR."""
    return pipeline.is_scoped_save_dir(save_dir)


def _scoped_store_db(args: argparse.Namespace) -> Path | None:
    """Scoped runs write findings inside the save dir, matching scoped reads."""
    return pipeline.scoped_store_db_path(getattr(args, "save_dir", None))


def _research_store_enabled(args: argparse.Namespace, config: dict[str, object]) -> bool:
    store_setting = os.environ.get("LAST30DAYS_STORE") or config.get("LAST30DAYS_STORE") or ""
    return args.store or str(store_setting).lower() in {"1", "true", "yes"}


def _add_preflight_library_writes(
    preflight: dict, args: argparse.Namespace, config: dict[str, object],
) -> None:
    from lib import library_index

    on_save = bool(args.preflight_report_on_save_dir and not args.save_dir)
    save_dir = args.preflight_report_on_save_dir if on_save else args.save_dir
    local, conditional = preflight["local_writes"], preflight["conditional_writes"]
    context_db = None if args.mock else pipeline.library_context_index_path(config, save_dir)
    if context_db is not None:
        write = {"kind": "library_index_on_save" if on_save else "library_index", "path": str(context_db)}
        (conditional if on_save else local).append(write)
    if save_dir and not pipeline.is_blank_save_dir(save_dir) and args.emit != "html":
        markdown_db = library_index.saved_report_index_path(Path(save_dir).expanduser().resolve())
        if markdown_db != context_db:
            conditional.append({"kind": "library_index_on_markdown_save", "path": str(markdown_db)})


def _add_preflight_research_writes(
    preflight: dict, args: argparse.Namespace, config: dict[str, object],
) -> None:
    if args.preflight_engine_cache_dir:
        preflight["conditional_writes"].append({
            "kind": "engine_cache_on_first_use", "path": args.preflight_engine_cache_dir,
        })
    if args.no_research_persistence:
        return
    if (
        env.hosted_backend_configured()
        and not config.get("_CORPUS_DIRS")
        and not args.mock and not args.record_fixtures and not args.deep_research
        and (args.perplexity_search_type is None or not config.get("PERPLEXITY_API_KEY"))
    ):
        return
    mcp_preflight = args.preflight_engine_cache_dir is not None
    research_writes = preflight["conditional_writes" if mcp_preflight else "local_writes"]
    kind_suffix = "_on_save" if mcp_preflight else ""
    if env.CONFIG_DIR is not None:
        for filename in ("last-run.json", "last-report.json"):
            research_writes.append({
                "kind": "research_cache" + kind_suffix, "path": str(env.CONFIG_DIR / filename),
            })
        if config.get("_CORPUS_DIRS"):
            research_writes.append({
                "kind": "corpus_cache" + kind_suffix, "path": str(env.CONFIG_DIR / corpus.CACHE_FILENAME),
            })
    _add_preflight_library_writes(preflight, args, config)
    if not _research_store_enabled(args, config):
        return
    import store

    store_args = argparse.Namespace(save_dir=args.preflight_report_on_save_dir) if mcp_preflight else args
    db_path = _scoped_store_db(store_args) or store.DB_PATH
    research_writes.append({"kind": "research_store" + kind_suffix, "path": str(db_path)})


def persist_report(report: schema.Report, store_db: Path | None = None) -> dict[str, int]:
    import store

    private_corpus = _report_has_private_corpus(report)
    with store.scoped_db(store_db):
        if private_corpus:
            store.ensure_private_db_files()
        store.init_db()
        if private_corpus:
            store.ensure_private_db_files()
        topic_row = store.add_topic(report.topic, update_existing=False)
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
    parser.add_argument("--research-only", action="store_true",
                        help="Treat the topic as research text, never as an administrative command")
    parser.add_argument("--emit", default="compact", choices=["compact", "json", "context", "md", "html", "brief"])
    parser.add_argument(
        "--register",
        choices=registers.REGISTER_NAMES,
        default=None,
        help="Audience synthesis preset for the standard brief (default, exec, dev, creator, eli5)",
    )
    clarify_group = parser.add_mutually_exclusive_group()
    clarify_group.add_argument(
        "--clarify-angle",
        default=None,
        help="Hosted backend only: answer its clarification with this angle (an option's query)",
    )
    clarify_group.add_argument(
        "--clarify-broadly",
        action="store_true",
        help="Hosted backend only: answer its clarification by running the topic as asked",
    )
    parser.add_argument(
        "--json-profile",
        default="agent",
        choices=["agent", "raw"],
        help="JSON export profile for --emit=json (default: agent)",
    )
    parser.add_argument("--require-local-report", action="store_true", help=argparse.SUPPRESS)
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
        "--followup-topic",
        metavar="TOPIC",
        help="Require the cached report for a drill or post-run freshness check to match TOPIC",
    )
    parser.add_argument(
        "--followup-run",
        metavar="ID",
        help="Require the cache ID reported by the original research or most recent drill",
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
    parser.add_argument(
        "--nominate-only",
        action="store_true",
        help=(
            "Leg 1 of the host-judged discovery protocol: sweep, write the "
            "nominations bundle for host judgment, and stop (no judging, no "
            "enrichment). Requires --discover"
        ),
    )
    parser.add_argument(
        "--judgments",
        metavar="PATH",
        help=(
            "Leg 2 of the discovery protocol: resume from the nominations "
            "bundle, applying the host judgments file at PATH. Requires "
            "--discover"
        ),
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help=(
            "Leg 3 of the discovery protocol: apply host angles, render the "
            "final discovery brief, and record the topic queue. Requires "
            "--discover"
        ),
    )
    parser.add_argument(
        "--angles",
        metavar="PATH",
        help=(
            "Optional host angles file for --discover --finalize (omitting it "
            "ships the brief without angle lines)"
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
    parser.add_argument(
        "--include-research-backend", action="store_true",
        help="Include the selected research backend in --diagnose JSON",
    )
    parser.add_argument("--preflight", action="store_true",
                        help="Print a safe human-readable permission preflight")
    parser.add_argument("--welcome", action="store_true",
                        help="Print the first-run welcome text (engine-owned; relay verbatim)")
    parser.add_argument("--preflight-report-on-save-dir", help=argparse.SUPPRESS)
    parser.add_argument("--preflight-engine-cache-dir", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser-cookies", action="store_true",
                        help="Disable browser-cookie extraction even when FROM_BROWSER is configured")
    parser.add_argument("--save-dir", help="Optional directory for saving the rendered output")
    parser.add_argument("--no-research-persistence", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--resolve-save-dir", action="store_true",
        help="Print the skill save directory from flags/config, then exit without research",
    )
    parser.add_argument(
        "--resolve-browser-consent", action="store_true",
        help="Print whether an explicit browser-cookie grant is active, without reading cookies",
    )
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
    parser.add_argument("--cached-report", action="store_true",
                        help="Render --emit=html --synthesis-file from cached research only, without replaying temporary plans")
    parser.add_argument("--publish-html", action="store_true",
                        help="Publish --emit=html output to ht-ml.app (explicit opt-in; public by default)")
    parser.add_argument("--publish", action="store_true",
                        help="With 'library feed', publish the HTML index and briefs (explicit opt-in; public by default); feed.xml remains local")
    parser.add_argument("--publish-password",
                        help="Optional shared password for --publish-html or 'library feed --publish'; prefer LAST30DAYS_PUBLISH_PASSWORD to avoid exposing secrets in process lists")
    parser.add_argument("--store", action="store_true", help="Persist ranked findings to the SQLite research store")
    parser.add_argument("--x-handle", help="X handle for targeted supplemental search")
    parser.add_argument("--x-related", help="Comma-separated related X handles (searched with lower weight)")
    parser.add_argument(
        "--ticker",
        metavar="SYMBOL:COMPANY[,SYMBOL:COMPANY]",
        help=(
            "Stock ticker of the listed company behind the topic, with that company's "
            "name, as the hosting model found it by web search (e.g., \"NVDA:Nvidia\", "
            "or \"TSLA:Tesla,SPCX:SpaceX\" for a person who leads two). At most two "
            "pairs. Each is checked against Yahoo Finance before a share-price line is "
            "shown; when it is passed, no other ticker lookup is used."
        ),
    )
    parser.add_argument(
        "--company",
        metavar="NAME[:SITE][,NAME[:SITE]]",
        help=(
            "The company behind the topic, listed or not, with its own website, as "
            "the hosting model found them by web search: the company a person leads "
            "or founded (Palmer Luckey: \"Anduril:anduril.com\"), or the maker of a "
            "product. At most two; the jobs lane reads the board of the first that "
            "has one. The site may be left out (\"Anduril\")."
        ),
    )
    parser.add_argument(
        "--jobs-board",
        dest="jobs_board",
        metavar="URL",
        help=(
            "The company's job-board URL as the hosting model found it by web search "
            "(e.g., https://job-boards.greenhouse.io/andurilindustries; Greenhouse, "
            "Ashby, Lever, Workable, SmartRecruiters or Workday). The engine reads "
            "that board and gives a digest only when the board names the company or "
            "the official site links to it."
        ),
    )
    parser.add_argument(
        "--official-site",
        metavar="DOMAIN",
        help=(
            "The topic's own website (e.g., typesafe.ai). The engine reads the X and "
            "GitHub accounts the homepage links and uses them when --x-handle / "
            "--github-user are not set. Keyless."
        ),
    )
    parser.add_argument(
        "--no-x-handle",
        metavar="REASON",
        help=(
            "Record that the topic has no resolvable X account, and why (e.g., "
            "\"official site links no X account; x.com-filtered search found none\"). "
            "Satisfies the agent-hosted X handle decision check without --x-handle."
        ),
    )
    parser.add_argument(
        "--x-posts",
        dest="x_posts",
        metavar="PATH",
        help=(
            "Path to a last30days-x-posts/1 JSON envelope of posts the hosting "
            "model fetched through its X connector; replaces the engine's X "
            "fetch for this run. A file path only (never inline JSON); on a "
            "comparison run use the per-entity x_posts field of --competitors-plan."
        ),
    )
    parser.add_argument(
        "--reddit-threads",
        dest="reddit_threads",
        metavar="PATH",
        help=(
            "Path to a last30days-reddit-threads/1 JSON envelope of Reddit "
            "thread URLs or ids the hosting model found with its own web "
            "search ('-' reads stdin). Only each entry's url or id is read; "
            "the engine rehydrates every thread and holds it to the window "
            "and the topic. At most 50 threads; single-topic runs only."
        ),
    )
    parser.add_argument(
        "--shortform-urls",
        dest="shortform_urls",
        metavar="PATH",
        help=(
            "Path to a last30days-shortform-urls/1 JSON envelope of TikTok and "
            "Instagram post URLs and TikTok creator profile URLs the hosting "
            "model found with its own web search ('-' reads stdin). Only each "
            "entry's url is read; used only by the keyless TikTok/Instagram "
            "lane (no ScrapeCreators key, or credits exhausted), which dates, "
            "hydrates and topic-checks every post itself. At most 40 URLs; "
            "single-topic runs only."
        ),
    )
    parser.add_argument("--web-backend", default="auto",
                        choices=["auto", "brave", "exa", "serper", "parallel", "parallel-mcp", "keyless", "none"],
                        help="Web search backend (default: auto; parallel-mcp explicitly opts into the "
                             "anonymous hosted MCP; keyless forces the zero-key floor)")
    parser.add_argument("--perplexity-search-type", choices=["web", "fast"],
                        help="Search backend for direct Perplexity Search API and Agent web_search; overrides LAST30DAYS_PERPLEXITY_SEARCH_TYPE. Does not enable the paid source or select an Agent preset.")
    parser.add_argument("--deep-research", action="store_true",
                        help="Use at most one Perplexity Deep Research run. Direct PERPLEXITY_API_KEY uses the Agent API background path; OPENROUTER_API_KEY keeps the synchronous Sonar fallback; cannot be combined with competitor or vs-mode.")
    parser.add_argument("--hiring-signals", action="store_true",
                        help="Analyze public jobs/careers postings as evidence-backed company focus signals.")
    parser.add_argument("--plan", help="JSON query plan (skips internal LLM planner). Can be a JSON string, a file path, or - for stdin.")
    parser.add_argument("--save-suffix", help="Suffix for saved output filename (e.g., 'gemini' → kanye-west-raw-gemini.md)")
    parser.add_argument("--subreddits", help="Comma-separated broad/category subreddit names to search (e.g., SaaS,Entrepreneur)")
    parser.add_argument("--dedicated-subreddits", help="Comma-separated entity-home subreddit names (e.g., Kanye,WestSubEver). Pulled in full (top+hot+new) and exempt from the relevance floor since the whole sub is the topic.")
    parser.add_argument("--devto-tags", help="Comma-separated dev.to tags, lowercase alphanumeric (e.g., claudecode,ai)")
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
    parser.add_argument("--max-results", dest="max_results", type=int,
                        help="Override the final ranked-pool cap (pool_limit/rerank_limit) from the depth profile. "
                             "Use for high-volume topics where the default (deep=60) under-covers. See issue #716.")
    parser.add_argument(
        "--source",
        dest="harvest_source",
        default=None,
        help="Transcript source for 'library harvest' (auto, all, claude, codex, antigravity, gemini, cursor)",
    )
    parser.add_argument(
        "--since",
        dest="harvest_since",
        default=None,
        help="Only harvest finished briefs on or after YYYY-MM-DD (for 'library harvest')",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Preview 'library harvest' without writing files",
    )
    parser.add_argument("--max-per-source", dest="max_per_source", type=int,
                        help="Override the per-stream cap (per_stream_limit) applied to each (source, subquery) before "
                             "pooling. Raising it increases unique-item yield when one source has many relevant items. "
                             "See issue #716.")
    parser.add_argument("--max-source-fetches", dest="max_source_fetches", type=int,
                        help="Override the per-source fetch cap (MAX_SOURCE_FETCHES, default x=2) that limits how many "
                             "subqueries actually fetch a capped source. Raise it so every X subquery in a multi-angle "
                             "--plan runs instead of just the first two. See issue #716.")
    parser.add_argument("--auto-resolve", action="store_true",
                        help="Use web search to discover subreddits/handles before planning (for platforms without WebSearch)")
    parser.add_argument("--github-user", help="GitHub username for person-mode search (e.g., steipete)")
    parser.add_argument("--github-repo", help="Comma-separated owner/repo for project-mode search (e.g., openclaw/openclaw,paperclipai/paperclip)")
    parser.add_argument(
        "--employer",
        metavar="NAME[:DOMAIN]",
        help=(
            "Person run: the person's current employer, as \"Name\" or \"Name:domain\" "
            "(e.g., \"Northwind Freight:northwind.example\"). Its name and domain become "
            "identity anchors. Not --company, which researches a company the person leads."
        ),
    )
    parser.add_argument(
        "--linkedin",
        metavar="URL",
        help=(
            "Person run: the person's LinkedIn profile URL "
            "(https://www.linkedin.com/in/<name>), trusted as an identity anchor."
        ),
    )
    parser.add_argument(
        "--email",
        metavar="ADDR",
        help=(
            "Person run: the person's email address. A work domain becomes an identity "
            "anchor; a personal-mail domain (gmail and similar) does not. Never logged."
        ),
    )
    parser.add_argument(
        "--trustpilot-domain",
        help=(
            "Trustpilot review-page domain for the topic (e.g., www.thriftbooks.com). "
            "Used verbatim, bypasses the brand-shape gate, and auto-activates the "
            "opt-in Trustpilot source for this run (unless EXCLUDE_SOURCES=trustpilot). "
            "Find the domain with `trustpilot-pp-cli search '<name>'`."
        ),
    )
    parser.add_argument(
        "--amazon-query",
        help=(
            "Product keyword the amazon source searches, when that source is active. "
            "Defaults to the topic. Supply it whenever the topic is not the product: "
            "a person topic searches their company's product line "
            "(--amazon-query='June Oven'), and a brand searches brand-plus-category "
            "(--amazon-query='Weber grill', not 'Weber' -- a bare brand keyword lands "
            "on an ad-heavy page that can miss the brand's own bestsellers). "
            "Requires the brightdata CLI on PATH and logged in."
        ),
    )
    parser.add_argument(
        "--meta-ads-page",
        help=(
            "Meta Ad Library page id for the topic's advertiser, when the meta_ads "
            "source is active. Skips name-based page resolution and its discovery "
            "credit. Accepts a bare numeric page id (e.g. 123456789012345) or an Ad "
            "Library URL carrying view_all_page_id. A facebook.com vanity URL is not "
            "a page id and is rejected. Use it when a brand advertises under product "
            "names, or when resolution picked the wrong company."
        ),
    )
    parser.add_argument(
        "--telegram-sources",
        help=(
            "Comma-separated list of public Telegram channel handles or t.me URLs. "
            "Auto-activates the opt-in Telegram source for this run. "
            "Accepts: bare handle (aipost), @handle (@aipost), "
            "t.me URL (https://t.me/aipost), or preview URL (https://t.me/s/aipost). "
            "Rejects joinchat links and numeric -100 supergroup IDs."
        ),
    )
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

    Accepts inline JSON or a file path. Returns {} on None/empty.
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
        "x_posts", "polymarket_hints",
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
            **{k: v for k, v in entry.items() if k in known_fields},
            "_name": entity.strip(),
        }
        if "polymarket_hints" in entry:
            from lib import planner as _plan_validator
            try:
                hints = _plan_validator.polymarket_hints(entry["polymarket_hints"])
            except ValueError as exc:
                sys.stderr.write(f"[CompetitorsPlan] Entry for {entity!r}: {exc}\n")
                raise SystemExit(2)
            normalized[entity.strip().lower()]["polymarket_hints"] = hints
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


COMPETITORS_MIN = competitors_mod.COMPETITORS_MIN
COMPETITORS_MAX = competitors_mod.COMPETITORS_MAX
COMPETITORS_DEFAULT = competitors_mod.COMPETITORS_DEFAULT


# The lanes that use the resolved company. With every one of them in
# EXCLUDE_SOURCES nothing reads the answer, so the topic is not sent.
_COMPANY_LANES = frozenset({yahoo_finance.SOURCE, "jobs", patents.SOURCE})


def _resolved_company(
    entity: str, *, mock: bool, config: dict, host_ticker: bool = False,
    patent_lane: bool = False,
) -> dict:
    """Company fields for one entity's ``resolved`` artifact. Mock runs, and
    runs that exclude every lane the company feeds, make no request.

    ``patent_lane`` says this run would search the entity's patent filings.
    Only the main topic of some runs is (see ``_patent_lane_wanted``); for
    every other entity the patent lane reads nothing, so it does not count
    among the lanes that must be excluded, and the company's other names,
    which only that lane reads, are not asked for.

    When the Wikidata lookup failed (not when it answered with nothing),
    Yahoo's symbol search is asked for the topic instead, and
    ``company_lookup`` says how that went. When Wikidata answered with
    nothing and ``EXA_API_KEY`` is set, an official-site search can still
    fill ``company`` and ``company_site``.

    With ``host_ticker`` the host passed ``--ticker``, which is then the only
    ticker source: Wikidata is asked for the company and its site but not for
    a listing, and a failed lookup is not followed by Yahoo's symbol search."""
    lanes = _COMPANY_LANES if patent_lane else _COMPANY_LANES - {patents.SOURCE}
    if mock or lanes <= env.exclude_sources(config):
        return {}
    if host_ticker:
        return dict(
            company_resolve.resolve_company(
                entity, listing=False, names=patent_lane, config=config,
            )
        )
    company = company_resolve.resolve_company(
        entity, names=patent_lane, config=config,
    )
    if isinstance(company, company_resolve.FailedLookup):
        backup = yahoo_finance.backup_company(entity, config)
        if backup is None:
            outcome = "backup not run"
        else:
            outcome = "yahoo backup used" if backup else "no backup match"
        company = {**(backup or {}), "company_lookup": f"wikidata failed; {outcome}"}
    if company.get("company"):
        ticker = f", ticker {company['ticker']}" if company.get("ticker") else ""
        if company.get("company_source") == yahoo_finance.BACKUP_SOURCE:
            ticker += " (Yahoo match)"
        elif company.get("company_source") == company_resolve.EXA_COMPANY_SOURCE:
            ticker += " (Exa site)"
        sys.stderr.write(f"[Resolve] {entity}: company {company['company']}{ticker}\n")
    if company.get("ticker") or company.get("ticker_candidates"):
        company["ticker_source"] = company.get("company_source") or "wikidata"
    return company


def _patent_lane_wanted(
    *, comparison: bool, mock: bool, hiring_signals: bool, depth: str, config: dict,
) -> bool:
    """Whether this run searches the main topic's patent filings, given a
    company. Never for a comparison (no entity of one gets a digest), a mock
    run, a ``--hiring-signals`` run, quick depth, or with the lane excluded
    (``EXCLUDE_SOURCES`` or ``LAST30DAYS_NO_PATENT_DIGEST``)."""
    if comparison or mock or hiring_signals or depth == "quick":
        return False
    return not patents.is_excluded(config)


def _company_for_patents(company: dict, host_names: list[str]) -> dict:
    """The company whose patent filings the run searches, or {} for none.

    The company the resolver named, with the other names it knows. When it
    named none, the first company the host passed with ``--company``, under
    that name alone. A ``--ticker`` name and ``--official-site`` are not
    used: neither says the topic is that company."""
    if company.get("company"):
        return dict(company)
    return {"company": host_names[0]} if host_names else {}


# ``--company`` takes as many names as ``--ticker`` takes pairs.
MAX_HOST_COMPANIES = yahoo_finance.MAX_HOST_TICKERS


def _host_companies(raw: str | None) -> list[dict[str, str]]:
    """The entries of a ``--company`` value, separated by commas, in the order
    given: ``Name`` or ``Name:site``, the shape of ``--ticker``'s pairs.

    The name is everything before the first colon, so it cannot hold one;
    the site is a bare domain or a URL, normalized to its homepage the way
    ``--official-site`` is. Each entry is ``name`` and ``site`` ('' when none
    was passed). An entry with no name is ignored, and a site that is not a
    domain is dropped while its name stands; each says so on stderr. Only
    the first ``MAX_HOST_COMPANIES`` companies are read.
    """
    from lib import resolve as resolve_lib

    companies: list[dict[str, str]] = []
    for entry in (raw or "").split(","):
        if not entry.strip():
            continue
        name, colon, site = entry.partition(":")
        name = " ".join(name.split())[:80]
        shown = " ".join(entry.split())[:120]
        if not name:
            sys.stderr.write(
                f"[Resolve] --company ignoring {shown!r}: no company name before the colon\n"
            )
            continue
        url = resolve_lib._official_site_url(site) if colon else ""
        if site.strip() and not url:
            sys.stderr.write(
                f"[Resolve] --company {name}: {' '.join(site.split())[:80]!r} is not a "
                "domain or URL; using the name without a site\n"
            )
        earlier = next((c for c in companies if c["name"] == name), None)
        if earlier is not None:
            earlier["site"] = earlier["site"] or url
            continue
        companies.append({"name": name, "site": url})
    if len(companies) > MAX_HOST_COMPANIES:
        sys.stderr.write(
            f"[Resolve] --company takes {MAX_HOST_COMPANIES} names; ignoring "
            f"{', '.join(c['name'] for c in companies[MAX_HOST_COMPANIES:])}\n"
        )
    return companies[:MAX_HOST_COMPANIES]


def _host_company_names(raw: str | None) -> list[str]:
    """The company names of a ``--company`` value, in the order given."""
    return [company["name"] for company in _host_companies(raw)]


def _company_signal(
    company: dict,
    host_tickers: list[dict] | None,
    official_site: str,
    host_names: list[str],
    jobs_board: str = "",
) -> str:
    """What makes this run about a company, or '' when nothing does.

    Any one is enough for the engine to add the jobs lane to a host plan that
    does not name it: the topic's official site, a company the host named
    with ``--company``, a usable ``--ticker`` pair, a ``--jobs-board`` URL
    (the host found a board, so the lane must read it), or the company the
    topic (or the person it names) resolved to.
    """
    if (official_site or "").strip():
        return "--official-site"
    if host_names:
        return "--company"
    if any(not pair.get("reason") and pair.get("company") for pair in host_tickers or []):
        return "--ticker"
    if (jobs_board or "").strip():
        return "--jobs-board"
    if company.get("company"):
        return f"the company {company['company']}"
    return ""


def _host_company_sites(
    companies: list[dict[str, str]] | list[str], *, mock: bool, config: dict,
) -> dict[str, str]:
    """The confirmed official site of each ``--company`` company, by name.

    The site the host passed with the name (``Name:site``) is used as it is:
    the host's web search found it, as it finds ``--official-site``. A name
    passed alone is asked of the company resolver (Wikidata, no key; Exa
    official-site fallback when ``EXA_API_KEY`` is set and Wikidata names
    none), which answers only for the names it is certain about.

    The jobs lane reads that site's careers page instead of guessing a
    domain from the name, and only such a confirmed site can tie a board to
    the company (it links the board, or the board states the name the site
    states). A mock run and a run with the jobs lane excluded ask the
    resolver nothing.
    """
    sites: dict[str, str] = {}
    lookups = not (mock or "jobs" in env.exclude_sources(config))
    for company in companies:
        if isinstance(company, str):
            company = {"name": company, "site": ""}
        name, site = company["name"], company.get("site") or ""
        if not site and lookups:
            site = str(
                company_resolve.resolve_company(
                    name, listing=False, config=config,
                ).get("company_site") or ""
            )
            if site:
                sys.stderr.write(f"[Resolve] --company {name}: official site {site}\n")
        if site:
            sites[name] = site
    return sites


def _company_for_jobs(
    company: dict,
    host_tickers: list[dict] | None,
    topic: str,
    official_site: str = "",
    host_names: list[str] | None = None,
    host_sites: dict[str, str] | None = None,
) -> dict:
    """The company the jobs lane searches for the main topic.

    The company Wikidata resolved, as it is; when that company is the topic
    and came with no site, the topic's official site is added as
    ``topic_site``. When Wikidata named none (it
    failed, or the topic is a person it is not certain about):

    - the topic's own official site, when the run knows one, is added as
      ``topic_site``, so the lane reads that site's careers page instead of
      guessing a domain from the topic's name;
    - with no official site, the site the host passed for a ``--company``
      entry that is the topic itself is the ``topic_site``;
    - the companies the host named that are not the topic itself are added
      as ``host_companies``: those of ``--company`` (``host_names``), then
      those of its usable ``--ticker`` pairs, each in the order given and a
      company named both ways once. The jobs lane tries each after the
      topic's own name finds no board, and uses the first one that has a
      board.
    """
    if company.get("company"):
        if (
            official_site.strip() and not company.get("company_site")
            and yahoo_finance._same_company_name(topic, str(company["company"]))
        ):
            # A company named with no site (the Yahoo fallback after a
            # failed Wikidata lookup) is the topic itself, so the site the
            # run knows is its site.
            return {**company, "topic_site": official_site.strip()}
        return company
    found = dict(company)
    if official_site.strip():
        found["topic_site"] = official_site.strip()
    else:
        own = next(
            (site for name, site in (host_sites or {}).items()
             if site and yahoo_finance._same_company_name(topic, name)),
            "",
        )
        if own:
            found["topic_site"] = own
    names: list[str] = []
    named = list(host_names or []) + [
        pair.get("company") or "" for pair in host_tickers or [] if not pair.get("reason")
    ]
    for name in named:
        if not name or yahoo_finance._same_company_name(topic, name):
            continue
        if any(yahoo_finance._same_company_name(earlier, name) for earlier in names):
            continue
        names.append(name)
    if names:
        found["host_companies"] = names
        sites = {name: site for name, site in (host_sites or {}).items() if name in names}
        if sites:
            # The confirmed official site of a company the host named.
            found["host_company_sites"] = sites
    return found if found != dict(company) else company


def truncate_comparison_entities(entities: list[str], *, warn: bool = True) -> list[str]:
    """Cap a vs-entity list at COMPARISON_ENTITY_MAX; optionally warn on stderr."""
    ceiling = competitors_mod.COMPARISON_ENTITY_MAX
    if len(entities) <= ceiling:
        return list(entities)
    kept = entities[:ceiling]
    dropped = entities[ceiling:]
    if warn:
        sys.stderr.write(
            f"[Competitors] vs-topic has {len(entities)} entities; "
            f"using first {ceiling}, dropped: {', '.join(dropped)}\n"
        )
    return kept


def apply_vs_competitor_routing(
    topic: str,
    *,
    competitors_flag: int | None,
    comp_enabled: bool,
    comp_count: int,
    comp_explicit: list[str],
    comp_plan: dict[str, dict] | None = None,
) -> tuple[str, bool, int, list[str]]:
    """Apply vs-string / plan routing on top of resolve_competitors_args.

    Precedence for *who* runs:
      1. ``--competitors-list`` (explicit peers; topic unchanged)
      2. Pure discover-N (``--competitors`` without list or plan) — topic
         unchanged, even if it contains ``vs``
      3. vs-string split (first entity becomes main topic) — used for bare
         vs-topics and vs-topic + ``--competitors-plan``
      4. ``--competitors-plan`` keys as peers when there is no vs-string
         (including when ``--competitors N`` is also set)
    """
    from lib import planner as _planner

    if comp_explicit:
        return topic, True, len(comp_explicit), list(comp_explicit)

    # Preserve discover-N semantics: numeric flag without plan/list must not
    # rewrite a vs-string into named peers.
    if competitors_flag is not None and not comp_plan:
        return topic, True, comp_count, []

    vs_entities = truncate_comparison_entities(
        _planner._comparison_entities(topic, uncapped=True),
        warn=True,
    )
    if len(vs_entities) >= 2:
        main, peers = vs_entities[0], vs_entities[1:]
        sys.stderr.write(
            f"[Competitors] vs-mode: routing to N-pass fanout: "
            f"{main} vs {' vs '.join(peers)}\n"
        )
        return main, True, len(peers), peers

    if comp_plan:
        plan_peers = [
            (entry.get("_name") or key)
            for key, entry in comp_plan.items()
        ]
        plan_peers = [name for name in plan_peers if name]
        if len(plan_peers) > COMPETITORS_MAX:
            sys.stderr.write(
                f"[Competitors] --competitors-plan has {len(plan_peers)} entries, "
                f"clamping to {COMPETITORS_MAX}.\n"
            )
            plan_peers = plan_peers[:COMPETITORS_MAX]
        return topic, True, len(plan_peers), plan_peers

    return topic, comp_enabled, comp_count, comp_explicit


def resolve_competitors_args(args: argparse.Namespace) -> tuple[bool, int, list[str]]:
    """Normalize competitors flags into (enabled, count, explicit_list).

    - (False, 0, []) when neither flag, list, nor plan is set.
    - An explicit ``--competitors-list`` always wins; count is derived from list length.
    - ``--competitors-plan`` alone enables mode with an empty peer list; vs-routing
      fills peers from the vs-string or plan keys.
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
    plan_present = bool(getattr(args, "competitors_plan", None))

    if not list_present and not flag_present and not plan_present:
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

    if flag_present:
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

    # plan_present alone: enable; peers filled by apply_vs_competitor_routing.
    return True, 0, []


def _missing_sources_for_promo(diag: dict[str, object]) -> str | None:
    available = set(diag.get("available_sources") or [])
    missing = []
    if "reddit" not in available:
        missing.append("reddit")
    # X is optional. A successful run without X must reach the research output
    # without an authentication or browser-cookie promo in front of it.
    # The web promo nudges toward a paid backend for higher-quality web search.
    # Grounding is now available keyless on non-native hosts, so key the promo on
    # the absence of a *paid* backend, not on grounding availability. Suppress it
    # entirely on native-search hosts, where the model's own search is better and
    # setting a paid engine key would be the wrong advice.
    if not diag.get("native_web_backend") and not diag.get("native_search"):
        missing.append("web")
    if not missing:
        return None
    return missing[0]


def _optional_x_omission_text(
    diag: dict[str, object],
    requested_sources: list[str] | None,
) -> str | None:
    """Return a non-blocking post-result note for a default run without X.

    Explicit ``--search`` runs already define their intended source boundary,
    so they do not need an omission note. Doctor/diagnose remains the place for
    X setup or repair instructions.
    """
    if requested_sources is not None:
        return None
    available = set(diag.get("available_sources") or [])
    if "x" in available:
        return None
    return (
        "Optional source omitted: X/Twitter was not enabled; research "
        "continued with the available sources."
    )


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
                # Subquery sources, not source_weights: the sanitizer weights
                # every eligible source, so weights alone would print
                # "Digg: 0 clusters" for a source that was never searched.
                *(
                    source
                    for subquery in report.query_plan.subqueries
                    for source in subquery.sources
                ),
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


REPORT_CACHE_VERSION = "last30days-report-cache/v2"
DEFAULT_REPORT_CACHE_TTL_SECONDS = 3600


class _CachedReport(NamedTuple):
    report: schema.Report
    entity_reports: list[tuple[str, schema.Report]] | None
    path: Path
    fingerprint: bytes
    x_envelope_sha256: str | None


def _last_report_cache_path() -> Path | None:
    if env.CONFIG_DIR is None:
        return None
    return env.CONFIG_DIR / "last-report.json"


@contextmanager
def _report_cache_lock(cache_path: Path) -> Iterator[None]:
    # The lock file stays in place so replacing the report cannot split writers across different inodes.
    fd = os.open(cache_path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
    finally:
        os.close(fd)


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
    *,
    x_envelope_sha256: str | None = None,
    research_options: dict[str, object] | None = None,
    expected_cache_fingerprint: bytes | None = None,
) -> bool:
    # The stored digest binds research replay to the host evidence it used.
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
        run_id = uuid.uuid4().hex
        cache_payload = {
            "schema": REPORT_CACHE_VERSION,
            "run_id": run_id,
            "topic": topic,
            "timestamp": payload["timestamp"],
            "comparison": bool(entity_reports),
            "x_envelope_sha256": x_envelope_sha256 or None,
            "research_options": (
                research_options if research_options is not None
                else _cache_research_options(build_parser().parse_args([]))
            ),
            "reports": [
                {"entity": label, "report": schema.to_dict(cached_report)}
                for label, cached_report in cached_reports
            ],
        }
        report_cache_path = target / "last-report.json"
        report_json = json.dumps(cache_payload, indent=2)
        run_json = json.dumps(payload, indent=2)
        with _report_cache_lock(report_cache_path):
            if (
                expected_cache_fingerprint is not None
                and hashlib.sha256(report_cache_path.read_bytes()).digest()
                != expected_cache_fingerprint
            ):
                return False
            replace_private_text(report_cache_path, report_json)
            replace_private_text(target / "last-run.json", run_json)
        sys.stderr.write(f"[last30days] Follow-up cache ID: {run_id}\n")
        return True
    except Exception as exc:
        # Never fatal, but never silent either (#787's lesson): callers that
        # promise cache state (drill chaining) branch on the return value.
        sys.stderr.write(f"[last30days] warning: could not write run cache: {exc}\n")
        return False


def _load_last_report_cache(
    topic: str | None,
    ttl_seconds: int = DEFAULT_REPORT_CACHE_TTL_SECONDS,
    *,
    x_envelope_sha256: str | None = None,
    research_options: dict[str, object] | None = None,
    requested_window: tuple[str, str] | None = None,
    use_cached_envelopes: bool = False,
    run_id: str | None = None,
) -> _CachedReport | None:
    cache_path = _last_report_cache_path()
    if cache_path is None or not cache_path.exists():
        return None
    try:
        raw = cache_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("report cache payload must be a JSON object")
        if payload.get("schema") != REPORT_CACHE_VERSION:
            return None
        if not _is_report_cache_fresh(payload.get("timestamp"), ttl_seconds):
            return None
        if run_id is not None and payload.get("run_id") != run_id:
            return None
        # Explicit follow-ups consume the saved snapshot without needing its
        # original envelope files. Research replay still matches their digest.
        cached_digest = payload.get("x_envelope_sha256") or None
        if cached_digest is not None and not isinstance(cached_digest, str):
            return None
        if (not use_cached_envelopes or x_envelope_sha256 is not None) and cached_digest != x_envelope_sha256:
            return None
        cached_options = payload.get("research_options") or {}
        if not isinstance(cached_options, dict):
            return None
        if any(cached_options.get(key) != value for key, value in (research_options or {}).items()):
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
        if requested_window is not None and any(
            (report.range_from, report.range_to) != requested_window
            for _, report in entity_reports
        ):
            return None
        comparison = payload.get("comparison")
        if comparison and (len(entity_reports) < 2 or len(entity_reports) != len(reports_payload)):
            return None
        return _CachedReport(
            entity_reports[0][1], entity_reports if comparison else None,
            cache_path, hashlib.sha256(raw).digest(), cached_digest,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(
            f"[last30days] Could not read report cache {cache_path}: "
            f"{type(exc).__name__}: {exc}\n"
        )
        return None


def _cache_research_options(
    args: argparse.Namespace, config: dict[str, object] | None = None, *, supplied_only: bool = False,
    resolved_corpus_dirs: list[Path] | None = None,
    external_plan: dict | None = None,
) -> dict[str, object]:
    names = (
        "search", "quick", "deep", "mock", "web_backend", "perplexity_search_type",
        "hiring_signals", "x_handle", "x_related", "official_site", "no_x_handle",
        "subreddits", "dedicated_subreddits", "devto_tags", "tiktok_hashtags",
        "tiktok_creators", "ig_creators", "max_results", "max_per_source",
        "max_source_fetches", "auto_resolve", "github_user", "github_repo", "ticker",
        "company", "jobs_board",
        "trustpilot_domain", "amazon_query", "meta_ads_page", "telegram_sources",
        "competitors", "competitors_list", "polymarket_keywords", "corpus_all_time", "drill",
        "employer", "linkedin",
    )
    options = {
        name: getattr(args, name, None) for name in names
        if not supplied_only or name in getattr(args, "_explicit_options", ())
    }
    if not supplied_only or "email" in getattr(args, "_explicit_options", ()):
        # The person anchor counts toward the cache match, but the address is
        # stored only as a hash.
        email = (getattr(args, "email", None) or "").strip().lower()
        options["email_sha256"] = hashlib.sha256(email.encode("utf-8")).hexdigest() if email else None
    if not supplied_only or "corpus" in getattr(args, "_explicit_options", ()):
        if resolved_corpus_dirs is None:
            resolved_corpus_dirs = corpus.resolve_directories(
                args.corpus, (config or {}).get("LAST30DAYS_CORPUS_DIRS")
            )
            if "corpus" in {
                value.strip().lower()
                for value in str((config or {}).get("EXCLUDE_SOURCES") or "").split(",")
            }:
                resolved_corpus_dirs = []
        options["corpus_sha256"] = hashlib.sha256(json.dumps([
            os.path.normcase(str(path)) for path in resolved_corpus_dirs
        ]).encode("utf-8")).hexdigest()
    for name in ("plan", "competitors_plan"):
        raw = getattr(args, name, None)
        if not supplied_only:
            options[name] = None
        if raw:
            try:
                if name == "plan" and external_plan is not None:
                    content = json.dumps(external_plan, sort_keys=True)
                else:
                    content = Path(raw).read_text(encoding="utf-8") if os.path.isfile(raw) else raw
                    content = json.dumps(json.loads(content), sort_keys=True)
            except (OSError, ValueError):
                content = raw
            options[name] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not supplied_only:
        rendering_keys = {
            "FUN_LEVEL", "ELI5_MODE", "LAST30DAYS_REGISTER", "LAST30DAYS_MEMORY_DIR",
            "LAST30DAYS_REPORT_CACHE_TTL_SECONDS", "LAST30DAYS_STORE", "LAST30DAYS_DEBUG",
            "LAST30DAYS_STRICT_EXIT", "LAST30DAYS_VERIFY_FRESHNESS", "LAST30DAYS_PUBLISH_PASSWORD",
            "LAST30DAYS_CORPUS_DIRS",
        }
        research_keys = {
            "INCLUDE_SOURCES", "EXCLUDE_SOURCES", "TELEGRAM_SOURCES", "BSKY_HANDLE",
            "BSKY_SEARCH_HOST", "FROM_BROWSER", "BROWSER_CONSENT", "BROWSER_CDP_URL",
            "DEGRADED_TRANSCRIPT_THRESHOLD",
        }
        relevant_config = {}
        for key, value in (config or {}).items():
            if key.startswith("_") or key in rendering_keys:
                continue
            if key.endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET", "_COOKIE")) or key == "CT0":
                if value:
                    relevant_config[key] = True
            elif key in research_keys or key.startswith("LAST30DAYS_") or key.endswith(("_BASE_URL", "_MODEL_PIN")):
                relevant_config[key] = value
        if env.x_api_opt_in():
            relevant_config[env.X_API_OPT_IN_VAR] = True
        options["config_sha256"] = hashlib.sha256(
            json.dumps(relevant_config, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    return options


def _config_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _freshness_enabled(args: argparse.Namespace, config: dict[str, object]) -> bool:
    if args.verify_freshness is not None:
        return bool(args.verify_freshness)
    return _config_truthy(config.get("LAST30DAYS_VERIFY_FRESHNESS"))


def _update_cached_freshness(cached: _CachedReport) -> bool:
    """Rewrite cached report bodies without extending the research-cache TTL."""
    try:
        with _report_cache_lock(cached.path):
            raw = cached.path.read_bytes()
            if hashlib.sha256(raw).digest() != cached.fingerprint:
                return False
            payload = json.loads(raw.decode("utf-8"))
            existing = payload.get("reports") or []
            if cached.entity_reports:
                cached_reports = cached.entity_reports
            else:
                label = (
                    str(existing[0].get("entity") or cached.report.topic)
                    if existing and isinstance(existing[0], dict)
                    else cached.report.topic
                )
                cached_reports = [(label, cached.report)]
            payload["reports"] = [
                {"entity": label, "report": schema.to_dict(report)}
                for label, report in cached_reports
            ]
            replace_private_text(cached.path, json.dumps(payload, indent=2))
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


def _load_followup_report(
    args: argparse.Namespace,
    config: dict[str, object],
) -> _CachedReport | None:
    cached = _load_last_report_cache(
        args.followup_topic,
        ttl_seconds=_report_cache_ttl_seconds(config),
        use_cached_envelopes=True,
        run_id=args.followup_run,
    )
    if cached is None:
        scope = ""
        if args.followup_run:
            scope = " for the requested run"
        elif args.followup_topic:
            scope = " for the requested topic"
        sys.stderr.write(f"[last30days] No fresh cached report{scope}; run a research pass first.\n")
    return cached


def _validate_publish_html(args: argparse.Namespace) -> int | None:
    if args.publish_html and args.emit != "html":
        sys.stderr.write("[last30days] --publish-html requires --emit=html\n")
        return 2
    return None


def _validate_comparison_emit(args: argparse.Namespace, *, is_comparison: bool) -> int | None:
    if is_comparison and args.emit == "brief":
        sys.stderr.write(
            "[last30days] --emit=brief is not supported for comparison or competitor runs; "
            "use --emit=compact, md, context, json, or html.\n"
        )
        return 2
    return None


def _resolve_synthesis_md(args: argparse.Namespace) -> str | None:
    if not args.synthesis_file:
        return None
    if args.emit == "html":
        return read_synthesis_file(args.synthesis_file)
    sys.stderr.write(
        "[last30days] Warning: --synthesis-file is only used with --emit=html; ignoring.\n"
    )
    return None


def _run_cached_freshness(
    args: argparse.Namespace,
    config: dict[str, object],
) -> int:
    publish_exit = _validate_publish_html(args)
    if publish_exit is not None:
        return publish_exit
    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    synthesis_md = _resolve_synthesis_md(args)
    cached = _load_followup_report(args, config)
    if cached is None:
        return 2
    report, entity_reports, cache_path = cached.report, cached.entity_reports, cached.path
    comp_emit_exit = _validate_comparison_emit(args, is_comparison=bool(entity_reports))
    if comp_emit_exit is not None:
        return comp_emit_exit
    _verify_report_set(report, entity_reports, allow_network=not args.mock)
    if _update_cached_freshness(cached):
        sys.stderr.write(f"[last30days] Updated freshness verdicts in {cache_path}\n")
    else:
        sys.stderr.write("[last30days] warning: freshness cache update failed\n")
    return _render_save_and_print(args, report, entity_reports, synthesis_md, config)


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

    cached = _load_followup_report(args, config)
    if cached is None:
        return 2
    report = cached.report
    if cached.entity_reports:
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
    local_only = sources == ["corpus"]
    drill_config = _drill_config(config, sources)
    diag = pipeline.diagnose(drill_config, sources, safe=args.mock or local_only)
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
        _verify_report_set(merged, None, allow_network=not args.mock and not local_only)
    else:
        merged.freshness_verdicts = []
    is_bound = args.followup_topic is not None or args.followup_run is not None
    if _write_last_run(
        report.topic, merged,
        x_envelope_sha256=cached.x_envelope_sha256,
        research_options=_cache_research_options(args, config),
        expected_cache_fingerprint=cached.fingerprint if is_bound else None,
    ):
        sys.stderr.write(f"[last30days] Updated drill cache in {cached.path}\n")
    else:
        sys.stderr.write("[last30days] warning: drill cache update failed\n")

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

    synthesis_md = _resolve_synthesis_md(args)
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
    safe_suffix = sanitize_suffix(suffix)
    suffix_part = f"-{safe_suffix}" if safe_suffix else ""
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


def _pre_run_prior_state(
    prior: dict[str, object] | None, run_ref: str
) -> dict[str, object] | None:
    """Reconstruct the queue state a topic had BEFORE this run identity
    recorded it.

    A row whose last_run_ref equals THIS run's run_ref was stamped by this
    very run's own earlier attempt (a finalize retry), so its surface_count
    already includes this run's surfacing: subtract it and keep the prior's
    covered state (covered_at intact) so the retry renders exactly like the
    first attempt did. Only when nothing remains after the subtraction AND
    the row was never covered is the topic genuinely first-ever (no prior).
    """
    if not prior or prior.get("last_run_ref") != run_ref:
        return prior
    previously = max(0, int(prior["surface_count"]) - 1)
    if previously == 0 and prior["status"] != "covered":
        return None
    adjusted = dict(prior)
    adjusted["surface_count"] = previously
    return adjusted


def _annotate_and_record_discovery_queue(
    report: schema.DiscoveryReport,
    args: argparse.Namespace,
    config: dict[str, object],
    run_ref: str | None = None,
) -> schema.DiscoveryReport:
    """Stamp queue annotations onto report topics, then record this surfacing.

    Order matters: annotations describe the queue state BEFORE this run, so
    each topic is matched first and recorded second. The queue is on by
    default; the resolved config value LAST30DAYS_DISCOVERY_QUEUE == "off"
    (env var or .env, via env.get_config) disables it. Scoped runs
    (--save-dir) write the scoped research.db, never the global one. Runs
    synchronously after the pipeline returns - this writes disk, so the
    abandon-on-timeout daemon-thread pattern is forbidden here.

    ``run_ref`` overrides the run identity: the finalize leg passes the
    pending report's leg-2 run_ref through so a finalize retry records (and
    annotates) as the SAME run - store.record_discovery_surfacing skips the
    double-count, and rows this very run identity stamped are not "prior"
    state, so retries render identically instead of claiming a resurfacing.
    """
    queue_setting = str(config.get("LAST30DAYS_DISCOVERY_QUEUE") or "").strip().lower()
    if queue_setting == "off" or not report.topics:
        return report

    import dataclasses

    import store

    run_ref = run_ref or f"discover:{report.domain or 'trending'}:{report.generated_at}"
    as_of = (report.generated_at or "")[:10] or report.range_to
    annotated: list[schema.DiscoveryTopic] = []
    with store.scoped_db(_scoped_store_db(args)):
        store.init_db()
        # Phase 1: match EVERY topic before recording ANY. Interleaving
        # match+record in one loop lets topic N fuzzy-match a same-anchor
        # sibling row this very run recorded seconds earlier, falsely
        # annotating a first-ever topic as "surfaced 2nd time".
        # A row stamped by THIS run identity is this run's own earlier
        # attempt (finalize retry), not prior state: reconstruct the pre-run
        # state (count minus this run's own surfacing, covered state kept)
        # so retries render identically for topics WITH history too.
        priors = [
            _pre_run_prior_state(prior, run_ref)
            for prior in (
                store.match_discovery_topic(topic.name) for topic in report.topics
            )
        ]
        # Phase 2: record this run's surfacings. A topic whose (possibly
        # fuzzy) prior row is covered inherits that covered state, so a
        # user's covered mark survives judge naming drift instead of
        # silently forking into a fresh uncovered row.
        for topic, prior in zip(report.topics, priors):
            inherit_covered_at = None
            if prior and prior["status"] == "covered":
                inherit_covered_at = prior["covered_at"] or prior["last_surfaced"]
            store.record_discovery_surfacing(
                topic.name,
                domain=report.domain,
                run_ref=run_ref,
                as_of=as_of,
                inherit_covered_at=inherit_covered_at,
            )
    for topic, prior in zip(report.topics, priors):
        if prior:
            topic = dataclasses.replace(
                topic,
                previously_surfaced_count=prior["surface_count"],
                last_surfaced=prior["last_surfaced"],
                covered=prior["status"] == "covered",
            )
        annotated.append(topic)
    return dataclasses.replace(report, topics=annotated)


def _record_discovery_queue_safely(
    report: schema.DiscoveryReport,
    args: argparse.Namespace,
    config: dict[str, object],
    run_ref: str | None = None,
) -> schema.DiscoveryReport:
    """Annotate + record the discovery queue, degrading a broken research.db
    (locked, read-only dir, corrupt) to a stderr warning: a queue failure
    must never destroy a finished pipeline run or the protocol's final
    brief. Shared verbatim by the one-shot and finalize paths."""
    try:
        return _annotate_and_record_discovery_queue(
            report, args, config, run_ref=run_ref,
        )
    except (sqlite3.Error, OSError) as exc:
        sys.stderr.write(
            f"[last30days] Warning: discovery queue unavailable ({exc}); "
            "continuing without queue annotations.\n"
        )
        return report


def _emit_and_save_discovery_report(
    report: schema.DiscoveryReport,
    args: argparse.Namespace,
    domain: str,
) -> None:
    """Render a discovery report per --emit, honor --output/--save-dir, and
    print it. Shared verbatim by the one-shot and finalize paths."""
    if args.emit == "json":
        payload = schema.to_dict(report) if args.json_profile == "raw" else schema.to_discovery_export(report)
        rendered = json.dumps(payload, indent=2, sort_keys=True)
    else:
        rendered = render.render_discovery(report)

    if args.output:
        output_path = save_rendered_output(rendered, args.output)
        sys.stderr.write(f"[last30days] Saved output to {output_path}\n")
    if args.save_dir and not pipeline.is_blank_save_dir(args.save_dir):
        save_path = _save_discovery_output(
            rendered,
            domain=domain or "trending",
            emit=args.emit,
            save_dir=args.save_dir,
            suffix=args.save_suffix or "",
        )
        sys.stderr.write(f"[last30days] Saved output to {save_path}\n")
    print(rendered)


def _discovery_strict_exit_code(
    source_status: dict[str, schema.SourceOutcome],
    config: dict[str, object],
) -> int:
    """The ONE LAST30DAYS_STRICT_EXIT evaluation for every discovery
    invocation - the one-shot and all three protocol legs (issue #384's
    discovery counterpart). Rendering/output already happened by the time
    this runs; only the exit code shifts to 3 when strict exit is on and any
    source outcome is neither clean nor an expected skip."""
    strict = str(config.get("LAST30DAYS_STRICT_EXIT") or "").strip().lower()
    if strict not in {"1", "true", "yes", "on"}:
        return 0
    degraded = sorted(
        source for source, outcome in (source_status or {}).items()
        if outcome.state not in _STRICT_EXIT_OK_STATES
    )
    if not degraded:
        return 0
    sys.stdout.flush()
    sys.stderr.write(
        f"{health.STRICT_EXIT_PREFIX}{', '.join(degraded)}\n"
    )
    sys.stderr.flush()
    return health.STRICT_EXIT_CODE


def _require_discover_mock_parity(
    loaded_mock: bool,
    args_mock: bool,
    *,
    label: str,
    path: Path | None,
) -> None:
    """A protocol leg's --mock flag must match the loaded handoff file's
    stamped provenance: mock-born state finalized by a real run would fake a
    real brief from fixture data, and real state finalized by --mock would
    silently drop the round's queue write. Mismatch is a contract failure
    (exit 2 via HandoffContractError)."""
    if bool(loaded_mock) == bool(args_mock):
        return
    location = str(path) if path is not None else "(unknown path)"
    if loaded_mock:
        raise discovery_handoff.HandoffContractError(
            f"{label} {location} is mock-born (a --mock leg wrote it): "
            "mock-born state cannot be finalized by a real run. Re-run this "
            "leg with --mock, or start a fresh real `--discover "
            "--nominate-only` sweep."
        )
    raise discovery_handoff.HandoffContractError(
        f"{label} {location} was written by a real run: real state "
        "cannot be finalized by a --mock run. Drop --mock, or start a fresh "
        "`--discover --nominate-only --mock` sweep."
    )


def _run_queue_list(args: argparse.Namespace, config: dict[str, object]) -> int:
    """List uncovered surfaced topics from the persistent discovery queue."""
    import store

    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    db_path = _scoped_store_db(args)
    if not Path(db_path or store.DB_PATH).exists():
        print("Discovery queue is empty - no discovery run has recorded topics yet.")
        return 0
    with store.scoped_db(db_path):
        rows = store.list_discovery_queue(status="surfaced")
        if not rows:
            # An existing db with zero queue rows (e.g. created via --store)
            # means no discovery run has recorded anything - only claim
            # "every topic is covered" when covered rows actually exist.
            if store.list_discovery_queue():
                print("Discovery queue is empty - every surfaced topic is marked covered.")
            else:
                print("Discovery queue is empty - no discovery run has recorded topics yet.")
            return 0

    headers = ("name", "domain", "surface_count", "last_surfaced", "status")
    table = [
        (
            str(row["name"]),
            str(row["domain"] or "-"),
            str(row["surface_count"]),
            str(row["last_surfaced"]),
            str(row["status"]),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in table))
        for column in range(len(headers))
    ]
    lines = [
        "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)).rstrip(),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    lines.extend(
        "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))).rstrip()
        for row in table
    )
    print("\n".join(lines))
    return 0


def _run_queue_cover(
    args: argparse.Namespace,
    config: dict[str, object],
    name: str,
) -> int:
    """Mark a queued discovery topic covered; unknown names error loudly."""
    import store

    if not name:
        sys.stderr.write(
            "[last30days] queue cover requires a topic name: "
            'queue cover "<topic name>".\n'
        )
        return 2
    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    db_path = _scoped_store_db(args)
    if not Path(db_path or store.DB_PATH).exists():
        sys.stderr.write(
            f"[last30days] No queued topic named {name!r}: the discovery queue "
            "is empty (no discovery run has recorded topics yet).\n"
        )
        return 2
    with store.scoped_db(db_path):
        row = store.mark_discovery_covered(
            name, as_of=datetime.date.today().isoformat()
        )
    if row is None:
        sys.stderr.write(
            f"[last30days] No queued topic named {name!r}. Covering requires "
            "the exact topic name; run 'queue list' to see queued names.\n"
        )
        return 2
    print(f"Marked covered: {row['name']} (covered {row['covered_at']})")
    return 0


def _resolve_discovery_source_boundary(
    args: argparse.Namespace, config: dict[str, object],
) -> tuple[list[str] | None, list[str] | None] | None:
    """Resolve the discovery sweep's source lists from the user's boundary.

    Returns ``(listing_sources, enrichment_boundary)`` - the discovery-capable
    subset for the sweep, and the user's ORIGINAL boundary honored by the
    per-topic research passes (which reach beyond the listing feeds - e.g.
    Techmeme, arXiv, YouTube, Polymarket); both None mean every available
    source. Returns None (after writing the exit-2 error) when the configured
    boundary leaves nothing to sweep: silently widening to all feeds would
    query sources the user filtered out.
    """
    requested_sources = resolve_requested_sources(args.search, config)
    enrich_requested_sources = list(requested_sources) if requested_sources else None
    if requested_sources:
        discovery_sources = [
            source for source in requested_sources
            if source in pipeline.DISCOVERY_SOURCES
        ]
        if not discovery_sources:
            origin = "--search" if args.search is not None else "LAST30DAYS_DEFAULT_SEARCH"
            sys.stderr.write(
                f"[last30days] {origin} has no discovery-capable sources "
                f"(unsupported: {', '.join(requested_sources)}); discovery "
                f"sweeps use: {', '.join(pipeline.DISCOVERY_SOURCES)}. Pass "
                "--search with one of those (or clear the source filter) to "
                "run a sweep.\n"
            )
            return None
        requested_sources = discovery_sources
    return requested_sources, enrich_requested_sources


def _discover_subreddits(args: argparse.Namespace) -> list[str] | None:
    return (
        [value.strip().removeprefix("r/") for value in args.subreddits.split(",") if value.strip()]
        if args.subreddits else None
    )


def _discover_domain(args: argparse.Namespace) -> str:
    """The whitespace-normalized discovery domain; empty = global trending."""
    return " ".join(str(args.discover or "").split())


def _run_discover(args: argparse.Namespace, config: dict[str, object]) -> int:
    domain = _discover_domain(args)
    # Empty domain = global trending: sweep every river feed's hot list with no
    # keyword gate. The confidence floor is what keeps junk out, not a keyword.
    # (--as-of and HTML rejection live in _main's shared --discover dispatch,
    # so every leg - one-shot or protocol - applies the same guards.)
    if args.synthesis_file:
        sys.stderr.write("[last30days] Warning: --synthesis-file is not used by discovery mode.\n")

    boundary = _resolve_discovery_source_boundary(args, config)
    if boundary is None:
        return 2
    requested_sources, enrich_requested_sources = boundary
    subreddits = _discover_subreddits(args)
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

    # Persistent topic queue: annotate this report from prior surfacings, then
    # record this run's surfacings - BEFORE rendering/export so the Pipeline
    # line and the JSON queue fields see the annotations. Mock runs stay 100%
    # side-effect-free.
    if not args.mock:
        report = _record_discovery_queue_safely(report, args, config)

    _emit_and_save_discovery_report(report, args, domain)
    return _discovery_strict_exit_code(report.source_status, config)


def _discover_handoff_state_dir(args: argparse.Namespace) -> Path | None:
    """One resolver for every protocol leg's handoff files: the save dir when
    given (mirroring _scoped_store_db's scoping), else the config dir - the
    same base _last_report_cache_path uses. args.save_dir is read AFTER the
    LAST30DAYS_MEMORY_DIR fallback in _main resolved it."""
    return discovery_handoff.handoff_state_dir(
        getattr(args, "save_dir", None), env.CONFIG_DIR
    )


def _run_discover_nominate(args: argparse.Namespace, config: dict[str, object]) -> int:
    """Protocol leg 1: sweep the listings, build the full judge pool, write
    the nominations bundle, and print the host-facing judging digest.

    No stage-1 judge, enrichment, confidence floor, or queue writes happen on
    this leg - the host judges from the bundle and leg 2 (--judgments)
    resumes from it. A zero-nomination sweep short-circuits to the existing
    nothing-solid brief with NO bundle written: there is nothing to judge.
    Writing a fresh bundle starts a NEW protocol round, so any pending
    report left by a prior round is deleted alongside it.
    """
    domain = _discover_domain(args)
    boundary = _resolve_discovery_source_boundary(args, config)
    if boundary is None:
        return 2
    requested_sources, enrich_requested_sources = boundary
    lookback_days = args.lookback_days or 30
    try:
        result = pipeline.run_discover_nominate(
            domain=domain,
            config=config,
            depth="deep" if args.deep else "quick" if args.quick else "default",
            requested_sources=requested_sources,
            mock=args.mock,
            subreddits=_discover_subreddits(args),
            lookback_days=lookback_days,
            as_of_date=args.as_of_date,
        )
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2

    if not result.pool:
        print(render.render_discovery(pipeline.nominate_nothing_solid_report(result)))
        return _discovery_strict_exit_code(result.source_status, config)

    entries = [
        discovery_handoff.PoolEntry(
            nomination=nomination,
            cluster_id=cluster_id,
            # No provider runs on this leg, so the nomination's name and junk
            # flag ARE the topic_shape heuristics - stored on the row as
            # leg 2's fallback for anything the host leaves unjudged.
            heuristic_name=nomination.name,
            heuristic_junk=nomination.junk_shape,
        )
        for nomination, cluster_id in result.pool
    ]
    bundle = discovery_handoff.write_nominations_bundle(
        entries,
        domain=result.plan.domain,
        tier="shallow" if args.discover_shallow else "deep",
        from_date=result.from_date,
        to_date=result.to_date,
        lookback_days=lookback_days,
        enrichment_source_boundary=enrich_requested_sources,
        requested_sources=requested_sources,
        # The sweep's finalized per-source outcomes ride the bundle so legs
        # 2-3 report degraded coverage instead of silently reading clean; the
        # mock stamp keeps mock-born and real state from cross-finalizing.
        source_status=result.source_status,
        warnings=result.warnings,
        mock=args.mock,
        # Same resolution as _discover_handoff_state_dir: save dir when
        # given, else the config dir.
        save_dir=getattr(args, "save_dir", None),
        config_dir=env.CONFIG_DIR,
    )
    # A fresh bundle starts a NEW protocol round: a pending report left by a
    # prior round is cross-round state a bare --finalize could silently
    # consume - delete it (missing file is a no-op).
    state_dir = _discover_handoff_state_dir(args)
    if state_dir is not None:
        discovery_handoff.pending_report_path(state_dir).unlink(missing_ok=True)
    print(discovery_handoff.build_host_digest(bundle))
    print(
        "\nJudgments file schema (leg 2): "
        f'{{"bundle_id": "{bundle.bundle_id}", "judgments": '
        '[{"id": "n1", "name": "<short topic name>", "junk": false, '
        '"worthiness": 0-100}, ...]}. '
        "Then resume with: --discover --judgments <path>."
    )
    return _discovery_strict_exit_code(result.source_status, config)


def _run_discover_resume(args: argparse.Namespace, config: dict[str, object]) -> int:
    """Protocol leg 2: resume from the nominations bundle, apply the host
    judgments file, run the deep per-topic research pass, and persist the
    ranked result as the pending report for leg 3 (--finalize).

    Contract failures (missing/stale bundle, judgments not bound to it, a
    bundle whose mock provenance disagrees with this run's --mock flag, an
    unwritable pending-report path) raise HandoffContractError and map to
    exit 2 in _run_discover_protocol_leg. Zero floor survivors renders the
    nothing-solid brief right here (clearing any stale prior-round pending
    file): no pending file, no leg 3. No queue writes and no artifact saves
    happen on this leg - the topic queue and the rendered brief belong to
    leg 3.
    """
    save_dir = getattr(args, "save_dir", None)
    bundle = discovery_handoff.read_nominations_bundle(
        save_dir=save_dir, config_dir=env.CONFIG_DIR,
    )
    _require_discover_mock_parity(
        bundle.mock, args.mock,
        label="Nominations bundle", path=bundle.path,
    )
    judgments = discovery_handoff.read_judgments(
        args.judgments, bundle, save_dir=save_dir, config_dir=env.CONFIG_DIR,
    )
    result = pipeline.run_discover_resume(
        bundle, judgments, config=config, mock=args.mock,
    )
    report = result.report

    if not report.topics:
        # Nothing cleared the floor: the honest brief ends the protocol here.
        # This round wrote no pending file, so a stale one from an earlier
        # round must not survive to feed a bare --finalize (missing file is
        # a no-op).
        state_dir = _discover_handoff_state_dir(args)
        if state_dir is not None:
            discovery_handoff.pending_report_path(state_dir).unlink(missing_ok=True)
        print(render.render_discovery(report))
        return _discovery_strict_exit_code(report.source_status, config)

    state_dir = _discover_handoff_state_dir(args)
    if state_dir is None:
        # Unreachable in practice - reading the bundle above required one of
        # these locations - but kept as a loud contract error, not an assert.
        raise discovery_handoff.HandoffContractError(
            "No handoff location available to write the pending report: "
            "pass --save-dir or configure ~/.config/last30days/."
        )
    pending_path = discovery_handoff.pending_report_path(state_dir)
    payload = {
        "kind": schema.DISCOVERY_PENDING_KIND,
        "schema_version": schema.DISCOVERY_PENDING_SCHEMA_VERSION,
        "bundle_id": bundle.bundle_id,
        # Fresh TTL clock: leg 3 measures staleness from THIS resume run,
        # not from the leg-1 sweep.
        "generated_at": report.generated_at,
        # Same run_ref format the queue records (leg 3 replays it verbatim).
        "run_ref": f"discover:{report.domain or 'trending'}:{report.generated_at}",
        # Leg-2 provenance: leg 3 refuses to finalize across the mock/real
        # boundary in either direction.
        "mock": bool(args.mock),
        # Full schema round-trip (the _write_last_run precedent): leg 3
        # rebuilds the report from this dict instead of re-running anything.
        "report": schema.to_dict(report),
        "angle_inputs": result.angle_inputs,
    }
    # ONE post-loop write from the main thread; enrichment workers are daemon
    # threads and never touch disk.
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # A locked/read-only/full disk is the protocol's clean exit-2 path,
        # never a traceback (same contract as the bundle write).
        raise discovery_handoff.HandoffContractError(
            f"Could not write pending discovery report {pending_path}: {exc}"
        ) from exc

    print(
        f"Judged discovery resume: {len(report.topics)} topic"
        f"{'s' if len(report.topics) != 1 else ''} cleared the floor "
        f"(bundle_id {bundle.bundle_id})."
    )
    print(f"Pending report: {pending_path}")
    print("\nAngle inputs by nomination id:")
    print(json.dumps(result.angle_inputs, indent=2))
    print(
        "\nWrite the angles file (leg 3): "
        f'{{"bundle_id": "{bundle.bundle_id}", "angles": '
        '[{"id": "n1", "podcast": "<one-sentence hook>", '
        '"x_article": "<one-sentence hook>"}, ...]} - one row per topic id '
        "above.\n"
        "Then finalize with: --discover --finalize --angles <path>."
    )
    return _discovery_strict_exit_code(report.source_status, config)


def _run_discover_finalize(args: argparse.Namespace, config: dict[str, object]) -> int:
    """Protocol leg 3: load the leg-2 pending report, apply host angles,
    render the final brief, save discovery artifacts, and record the topic
    queue. The cheap offline leg - no sweep, no enrichment, no providers,
    no network; everything renders from the pending report. (HTML/--as-of
    rejection lives in _main's shared --discover dispatch.)

    Contract failures (missing/stale/mismatched pending report or angles)
    raise HandoffContractError and map to exit 2 in
    _run_discover_protocol_leg. The pending file is deliberately LEFT IN
    PLACE on success: a finalize retry with a corrected angles file must
    keep working within the TTL, and the queue records under the pending
    report's leg-2 run_ref, so retries never double-count a surfacing.
    Mock finalize renders identically but writes no queue rows.
    """
    import dataclasses

    save_dir = getattr(args, "save_dir", None)
    pending = discovery_handoff.read_pending_report(
        save_dir=save_dir, config_dir=env.CONFIG_DIR,
    )
    _require_discover_mock_parity(
        pending.mock, args.mock,
        label="Pending discovery report", path=pending.path,
    )
    angles = discovery_handoff.read_angles(
        args.angles, pending, save_dir=save_dir, config_dir=env.CONFIG_DIR,
    )
    try:
        report = schema.discovery_report_from_dict(pending.report)
    except (KeyError, TypeError, ValueError) as exc:
        # The envelope validated but the report body is structurally
        # incomplete: a contract failure with the resume remedy, never a
        # traceback out of the finalize leg.
        raise discovery_handoff.HandoffContractError(
            f"Pending discovery report {pending.path} carries a malformed "
            f"report body ({type(exc).__name__}: {exc}). "
            f"{discovery_handoff._RESUME_REMEDY}"
        ) from exc

    if angles:
        # Host angles are keyed by nomination id; the pending report's
        # angle_inputs mapping carries each surviving id's applied topic
        # name, which is how angles land on the right DiscoveryTopic.
        angles_by_name = {
            name: host
            for nomination_id, host in angles.items()
            if (name := (pending.angle_inputs.get(nomination_id) or {}).get("name"))
        }
        report = dataclasses.replace(report, topics=[
            dataclasses.replace(
                topic,
                podcast_angle=host.podcast,
                x_article_angle=host.x_article,
            )
            if (host := angles_by_name.get(topic.name)) is not None
            else topic
            for topic in report.topics
        ])

    # Persistent topic queue: the protocol's ONE queue write happens here,
    # under the leg-2 run identity (pending.run_ref) so finalize retries are
    # idempotent. Mock runs stay 100% side-effect-free.
    if not args.mock:
        report = _record_discovery_queue_safely(
            report, args, config, run_ref=pending.run_ref or None,
        )

    _emit_and_save_discovery_report(report, args, report.domain)
    return _discovery_strict_exit_code(report.source_status, config)


def _run_discover_protocol_leg(
    args: argparse.Namespace, config: dict[str, object]
) -> int:
    """Route one validated protocol invocation to its leg. Contract failures
    (unreadable/stale/mismatched handoff files) map to stderr + exit 2 here,
    so the leg bodies (U3-U5) raise HandoffContractError freely."""
    try:
        if args.nominate_only:
            return _run_discover_nominate(args, config)
        # --judgments dispatch keys on flag presence (is not None), matching
        # the --discover convention: never on the path string's truthiness.
        if args.judgments is not None:
            return _run_discover_resume(args, config)
        return _run_discover_finalize(args, config)
    except discovery_handoff.HandoffContractError as exc:
        sys.stderr.write(f"[last30days] {exc.message}\n")
        return 2


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
    sys.stdout.flush()
    sys.stderr.write(
        f"{health.STRICT_EXIT_PREFIX}{', '.join(degraded)}\n"
    )
    sys.stderr.flush()
    return health.STRICT_EXIT_CODE


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
    publish_exit = _validate_publish_html(args)
    if publish_exit is not None:
        return publish_exit
    comp_emit_exit = _validate_comparison_emit(args, is_comparison=bool(entity_reports))
    if comp_emit_exit is not None:
        return comp_emit_exit
    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
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
        # Bind the render to the path save_output actually allocates so the
        # saved report and stdout agree even when collision fallback is used.
        def _render_with_actual_path(actual_path: Path) -> str:
            nonlocal rendered
            display = compute_output_path_display(str(actual_path))
            if entity_reports:
                rendered = emit_comparison_output(
                    entity_reports,
                    args.emit,
                    fun_level=fun_level,
                    save_path=display,
                    synthesis_md=synthesis_md,
                    json_profile=args.json_profile,
                )
            else:
                rendered = emit_output(
                    report,
                    args.emit,
                    fun_level=fun_level,
                    save_path=display,
                    synthesis_md=synthesis_md,
                    json_profile=args.json_profile,
                    register=audience.name,
                )
            if args.emit not in {"json", "html"}:
                # Markdown saves keep the complete debug artifact (all clusters
                # and per-source items), matching the render_fn-less path in
                # save_output and the comparison peer saves. Saving the compact
                # stdout render instead made most collected evidence
                # unrecoverable from the raw file (#923). The stdout re-render
                # above still runs so the visible footer cites the real path,
                # and the saved artifact carries the same citation.
                return render.render_full(report, save_path=display)
            return rendered

        save_path = save_output(
            report,
            args.emit,
            args.save_dir,
            suffix=args.save_suffix or "",
            synthesis_md=synthesis_md,
            topic_override=comparison_topic(entity_reports) if is_comparison_html else None,
            json_profile=args.json_profile,
            register=audience.name,
            private=private_saved_format,
            render_fn=_render_with_actual_path,
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
            published_reports = [entity for _label, entity in entity_reports] if entity_reports else [report]
            publish_rendered = rendered
            if any(schema.report_has_corpus(entity) for entity in published_reports):
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
    checklist = _report_checklist_for_stdout(args, rendered, report, entity_reports, audience.name)
    if checklist:
        print(rendered + checklist, end="")
    else:
        print(rendered)
    return _strict_exit_code(report, entity_reports, config)


def _report_checklist_for_stdout(
    args: argparse.Namespace,
    rendered: str,
    report: schema.Report,
    entity_reports: list[tuple[str, schema.Report]] | None,
    register: str,
) -> str:
    """The format checklist that follows compact stdout, or "" for any other
    output. It is added here, at the print, so no saved or ``--output`` file,
    no other emit mode and no renderer ever carries it. A hiring-signals run
    writes its own scoped report, which the checklist does not describe."""
    if args.emit != "compact" or getattr(args, "hiring_signals", False):
        return ""
    return report_checklist.build(
        rendered,
        comparison=bool(entity_reports),
        recommendation=report_checklist.is_recommendation_run(
            report.query_plan.intent, report.topic, report.query_plan.raw_topic,
        ),
        register=register,
    )


def _propagate_config_to_environ(config: dict[str, object]) -> None:
    """Push relevant env keys to os.environ so provider modules can read them.

    The env.get_config() function reads from a .env file, but providers.py
    reads from os.environ directly. Without this, OPENAI_BASE_URL and
    XAI_BASE_URL overrides are silently ignored. This is a no-op for
    keys that are already set in process env.
    """
    for key in ("OPENAI_BASE_URL", "XAI_BASE_URL", "OPENROUTER_BASE_URL", "SCRAPECREATORS_BASE_URL"):
        val = config.get(key)
        if val and not os.environ.get(key):
            os.environ[key] = val


_NON_AUTO_SETUP_FLAGS = frozenset({
    "--openclaw",
    "--github",
    "--github-start",
    "--github-poll",
    "--device-auth",
    "--reddit-session",
    "--no-reddit-session",
})

_SETUP_NO_SECRET_STORE_FLAGS = frozenset({
    "--github",
    "--github-start",
    "--github-poll",
    "--device-auth",
    "--reddit-session",
    "--no-reddit-session",
})


def _setup_is_non_auto(extra_argv: list[str]) -> bool:
    store_key_present, _, remaining = _split_store_key(extra_argv)
    if store_key_present:
        return True
    install_target, uninstall_target, remaining = _split_hook_flags(remaining)
    if install_target is not None or uninstall_target is not None:
        return True
    return any(flag in _NON_AUTO_SETUP_FLAGS for flag in remaining)


def _setup_skips_secret_stores(extra_argv: list[str]) -> bool:
    store_key_present, _, remaining = _split_store_key(extra_argv)
    if store_key_present:
        return True
    install_target, uninstall_target, remaining = _split_hook_flags(remaining)
    if install_target is not None or uninstall_target is not None:
        return True
    # Mirror setup dispatch precedence: --openclaw runs before --reddit-session
    # and --github*/--device-auth, and reports stored provider keys.
    if "--openclaw" in remaining:
        return False
    return any(flag in _SETUP_NO_SECRET_STORE_FLAGS for flag in remaining)


def _is_library_command(command_topic: str) -> bool:
    return (
        command_topic == "library feed"
        or command_topic == "library harvest"
        or command_topic == "library search"
        or command_topic.startswith("library search ")
    )


def _is_queue_command(command_topic: str) -> bool:
    return (
        command_topic == "queue list"
        or command_topic == "queue cover"
        or command_topic.startswith("queue cover ")
    )


def _is_admin_command(command_topic: str) -> bool:
    return (
        command_topic in {"setup", "doctor"}
        or _is_library_command(command_topic)
        or _is_queue_command(command_topic)
    )


def _setup_has_fresh_browser_consent(args: argparse.Namespace, extra_argv: list[str]) -> bool:
    return (
        not args.no_browser_cookies
        and not args.diagnose
        and not args.preflight
        and not _setup_is_non_auto(extra_argv)
        and "--allow-browser-cookies" in extra_argv
    )


USE_RECORDED_BROWSER_CONSENT_FLAG = "--use-recorded-browser-consent"

SETUP_PASSTHROUGH_FLAGS = {
    "--allow-browser-cookies",
    USE_RECORDED_BROWSER_CONSENT_FLAG,
    "--device-auth",
    "--github",
    "--github-start",
    "--github-poll",
    "--install-hooks",
    "--openclaw",
    "--reddit-session",
    "--no-reddit-session",
    "--store-key",
    "--uninstall-hooks",
}

INSTALL_HOOKS_FLAG = "--install-hooks"
UNINSTALL_HOOKS_FLAG = "--uninstall-hooks"


def _split_hook_flags(
    extra_argv: list[str],
) -> tuple[str | None, str | None, list[str]]:
    """Pull optional-value ``--install-hooks [HOST]`` / ``--uninstall-hooks [HOST]``.

    Returns ``(install_target, uninstall_target, remaining)``. Each target is
    ``None`` when absent, ``""`` when passed bare (meaning all detected local
    harnesses), or the explicit host string when supplied. Callers MUST check
    ``is not None``, never truthiness.
    """
    install_target: str | None = None
    uninstall_target: str | None = None
    remaining: list[str] = []
    i = 0
    while i < len(extra_argv):
        arg = extra_argv[i]
        if arg in (INSTALL_HOOKS_FLAG, UNINSTALL_HOOKS_FLAG):
            value = ""
            if i + 1 < len(extra_argv) and not extra_argv[i + 1].startswith("-"):
                value = extra_argv[i + 1]
                i += 2
            else:
                i += 1
            if arg == INSTALL_HOOKS_FLAG:
                install_target = value
            else:
                uninstall_target = value
            continue
        if arg.startswith(INSTALL_HOOKS_FLAG + "="):
            install_target = arg[len(INSTALL_HOOKS_FLAG) + 1 :]
            i += 1
            continue
        if arg.startswith(UNINSTALL_HOOKS_FLAG + "="):
            uninstall_target = arg[len(UNINSTALL_HOOKS_FLAG) + 1 :]
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return install_target, uninstall_target, remaining


def _run_hook_setup(
    install_target: str | None,
    uninstall_target: str | None,
) -> int:
    from lib import hook_installer

    if install_target is not None and uninstall_target is not None:
        sys.stderr.write(
            "[last30days] pass --install-hooks or --uninstall-hooks, not both.\n"
        )
        return 2
    try:
        # Bare --install-hooks / --uninstall-hooks uses "" -> "all", so dispatch
        # checks `is not None` rather than string truthiness.
        if install_target is not None:
            result = hook_installer.install_hooks(install_target or "all")
        else:
            result = hook_installer.uninstall_hooks(uninstall_target or "all")
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

STORE_KEY_FLAG = "--store-key"


def _split_store_key(extra_argv: list[str]) -> tuple[bool, str, list[str]]:
    """Pull ``--store-key <NAME>`` / ``--store-key=<NAME>`` out of ``extra_argv``.

    Returns ``(present, name, remaining)``. ``name`` is "" when the flag has
    no value; ``remaining`` is every other passthrough token, so the regular
    allowlist check still applies to them.
    """
    present = False
    name = ""
    remaining: list[str] = []
    i = 0
    while i < len(extra_argv):
        arg = extra_argv[i]
        if arg == STORE_KEY_FLAG:
            present = True
            if i + 1 < len(extra_argv) and not extra_argv[i + 1].startswith("-"):
                name = extra_argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        if arg.startswith(STORE_KEY_FLAG + "="):
            present = True
            name = arg[len(STORE_KEY_FLAG) + 1:]
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return present, name, remaining


# One credential line: longer than any real token, short enough that a
# misdirected stream on stdin cannot grow memory.
STORE_KEY_MAX_BYTES = 64 * 1024


def _run_store_key(name: str) -> int:
    """``setup --store-key <NAME>``: persist one allowlisted credential from stdin.

    Reads exactly one line from stdin (bounded to ``STORE_KEY_MAX_BYTES``),
    strips whitespace, and writes it to the global ``.env`` as a 0o600 secret
    through ``setup_wizard.write_api_key``. An existing line for the same
    name is replaced, so a rejected credential can be rotated by running the
    command again. The value never reaches stdout or stderr: stdout carries
    ``NAME=****`` plus a JSON line ``{"persisted": bool, "key": NAME}``. A
    name outside ``env.KEYCHAIN_KEYS`` or an empty value exits 2 without
    echoing anything.
    """
    from lib import setup_wizard

    if name not in env.KEYCHAIN_KEYS:
        # Do not enumerate the allowlist here: on an official-only host a
        # failure hint must not name the legacy credential keys.
        sys.stderr.write(
            "[last30days] setup --store-key: unknown or missing key name "
            "(must be a credential name the engine loads from its .env; "
            "see CONFIGURATION.md).\n"
        )
        return 2
    value = sys.stdin.readline(STORE_KEY_MAX_BYTES).strip()
    if not value:
        sys.stderr.write(
            f"[last30days] setup --store-key {name}: empty value on stdin; "
            "pipe the credential as a single line.\n"
        )
        return 2
    persisted = bool(
        setup_wizard.write_api_key(env.CONFIG_FILE, value, key_name=name, replace=True)
    )
    print(f"{name}=****")
    print(json.dumps({"persisted": persisted, "key": name}))
    return 0 if persisted else 1

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


def _looks_inline_json(value: str) -> bool:
    """True when a --x-posts argument is JSON text rather than a path."""
    stripped = value.strip()
    return stripped.startswith(("{", "[")) or "\n" in value


def _comparison_requested(args: argparse.Namespace, topic: str) -> bool:
    """Whether this invocation is a comparison run (vs-topic or competitor flags)."""
    from lib import planner as _planner

    return any(
        value is not None
        for value in (args.competitors, args.competitors_list, args.competitors_plan)
    ) or len(_planner._comparison_entities(topic, uncapped=True)) >= 2


PERSON_FLAGS = (("--employer", "employer"), ("--linkedin", "linkedin"), ("--email", "email"))


def _person_inputs_gate(args: argparse.Namespace, topic: str) -> int | None:
    """Reject person flags that cannot define a person run; exit 2 or None.

    A person run needs a single topic: comparison runs would split the
    run's lookup caps across entities, so they take no person flags.
    Malformed values fail here, before any config probe or paid call.
    """
    passed = [flag for flag, dest in PERSON_FLAGS if getattr(args, dest) is not None]
    if not passed:
        return None
    if not topic:
        sys.stderr.write(f"[last30days] {', '.join(passed)} requires a person topic.\n")
        return 2
    if _comparison_requested(args, topic):
        sys.stderr.write(
            f"[last30days] {', '.join(passed)} applies to a single-person run, not a "
            "comparison run. Research each person separately.\n"
        )
        return 2
    try:
        person.build_person_context(
            topic, employer=args.employer, linkedin=args.linkedin, email=args.email,
        )
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2
    return None


# Wall time for the whole person resolve step, shared by every call in it.
PERSON_RESOLVE_SECONDS = 60.0
PERSON_NO_X_REASON = "person run: no X account linked from the person's LinkedIn profile or employer bio"


def _resolve_person(
    ctx: "person.PersonContext", config: dict, requested_sources: list[str] | None,
) -> "person.PersonContext":
    """Run the LinkedIn resolution and the employer bio lane concurrently under
    one shared deadline; return the context with whatever resolved in time.

    Daemon threads, not an executor: a hung lane must not hold the process
    open past the deadline. The LinkedIn lane works on its own copy of the
    context, so a straggler can never write to the one the run continues with.
    """
    import copy
    import queue
    import threading

    from lib import employer_site, person_linkedin

    deadline = time.monotonic() + PERSON_RESOLVE_SECONDS
    person_linkedin.anchor_supplied_url(ctx)
    linkedin_ctx = copy.deepcopy(ctx)
    results: "queue.Queue[tuple[str, object]]" = queue.Queue()
    run_id = uuid.uuid4().hex

    def _linkedin() -> None:
        try:
            value: object = person_linkedin.resolve_linkedin(
                linkedin_ctx, config, deadline_monotonic=deadline, run_id=run_id,
                requested_sources=requested_sources,
            )
        except Exception as exc:  # noqa: BLE001 - a lane failure is a status
            sys.stderr.write(f"[Person] LinkedIn resolution failed: {type(exc).__name__}\n")
            value = None
        results.put(("linkedin", value))

    def _bio() -> None:
        try:
            value: object = employer_site.find_bio(
                ctx.full_name, domain=ctx.employer_domain or None,
                employer_name=ctx.employer_name or None, email_domain=ctx.email_domain or None,
                deadline_monotonic=deadline, site_search=None,
            )
        except Exception as exc:  # noqa: BLE001 - a lane failure is a status
            sys.stderr.write(f"[Person] Employer bio lane failed: {type(exc).__name__}\n")
            value = {"status": "failed", "url": "", "title": "", "bio": "", "links": [], "domain": ""}
        results.put(("bio", value))

    for name, target in (("linkedin", _linkedin), ("bio", _bio)):
        threading.Thread(target=target, name=f"person-{name}", daemon=True).start()

    pending = {"linkedin", "bio"}
    resolved, bio = None, None
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            name, value = results.get(timeout=remaining)
        except queue.Empty:
            break
        pending.discard(name)
        if name == "linkedin":
            resolved = value
        else:
            bio = value

    if isinstance(resolved, person.PersonContext):
        ctx = resolved
    elif "linkedin" not in pending:
        ctx.set_linkedin_status(person.LINKEDIN_FAILED)
    if isinstance(bio, dict):
        ctx.bio = bio
    if pending:
        ctx.timed_out = True
        if "linkedin" in pending and ctx.linkedin_status is None:
            ctx.set_linkedin_status(person.LINKEDIN_FAILED)
        sys.stderr.write(
            f"[Person] Resolve step hit its {PERSON_RESOLVE_SECONDS:g}s deadline; "
            f"continuing without: {', '.join(sorted(pending))}\n"
        )
    return ctx


def _person_resolve_step(
    args: argparse.Namespace, topic: str, config: dict, requested_sources: list[str] | None,
) -> "person.PersonContext | None":
    """Build and resolve the person context; fill empty handle slots.

    Returns None (and changes nothing) when no person flag was passed. Mock
    runs build the context but make no lookups.
    """
    ctx = person.build_person_context(
        topic, employer=args.employer, linkedin=args.linkedin, email=args.email,
    )
    if ctx is None:
        return None
    if not args.mock:
        ctx = _resolve_person(ctx, config, requested_sources)
    args.x_handle, args.github_user = person.apply_derived_handles(
        ctx, x_handle=args.x_handle, github_user=args.github_user,
    )
    for slot, label in (("x", "X handle"), ("github", "GitHub user")):
        if slot in ctx.derived_handles:
            sys.stderr.write(f"[Person] {label} from the person's own links: @{ctx.derived_handles[slot]}\n")
    if not args.x_handle and not args.no_x_handle:
        args.no_x_handle = PERSON_NO_X_REASON
    sys.stderr.write(
        f"[Person] LinkedIn: {ctx.linkedin_status or 'not checked'}; "
        f"anchors: {len(ctx.anchors)}; bio page: {(ctx.bio or {}).get('status') or 'not checked'}\n"
    )
    return ctx


LAW7_HOST_PLAN_EXIT = 2
X_HANDLE_DECISION_EXIT = 2


def _x_handle_decision_signal(
    args: argparse.Namespace, topic: str, external_plan: object,
    available_sources: list[str],
) -> str:
    """Agent-host marker when a hosted entity run plans X with no handle decision.

    An entity topic's own posts are the primary X evidence, and only a
    resolved handle lets X search them. On hosted runs the host model resolves
    handles (the engine's auto-resolve stands down for --plan), so a skipped
    lookup used to cost nothing and went unnoticed. Returns "" when the run
    is exempt or already decided (--x-handle or --no-x-handle), or when X
    will not run (unavailable, --search without x, or EXCLUDE_SOURCES=x).
    """
    if external_plan is None or args.mock or args.quick:
        return ""
    if args.x_handle or args.no_x_handle:
        return ""
    if "@" in topic or _comparison_requested(args, topic):
        return ""
    if not _looks_like_entity_topic(topic):
        return ""
    if "x" not in available_sources:
        return ""
    if not pipeline._external_plan_names_source(external_plan, "x"):
        return ""
    return env.agent_host_signal()


def _x_handle_decision_message(signal: str) -> str:
    return (
        "[Resolve] X handle decision missing: this run is agent-hosted "
        f"({signal} is set), the topic names a person, company, or product, and "
        "the plan searches X, but the command has no --x-handle. Without one X "
        "cannot search the subject's own posts. Resolve it first "
        "(references/research-runbook.md Step 0.5, Section A): pass "
        "--official-site=<domain> so the engine reads the handle from the "
        "homepage links, or find it with a search restricted to x.com through "
        "your search tool's domain filter, then re-run with --x-handle=<handle>. "
        "If both came up empty, or the topic is a theme rather than an entity, "
        "re-run with --no-x-handle=\"<reason>\".\n"
    )


def _law7_host_plan_message(signal: str) -> str:
    return (
        "[Planner] LAW 7: no --plan passed, and this run is agent-hosted "
        f"({signal} is set). The engine stopped before retrieval and did not "
        "call its internal planner. If you are the reasoning model hosting "
        "this skill, YOU ARE the planner; no API key or provider is needed. "
        "Write the JSON query plan (references/research-runbook.md Step 0.75) "
        "and pass it on stdin with a "
        "quoted heredoc: --plan - <<'PLAN_EOF' ... PLAN_EOF "
        "(see Research Execution in that "
        "runbook). With no "
        "web-search tool, pass --auto-resolve instead. For headless or cron "
        f"runs under an agent, set {env.ALLOW_ENGINE_PLAN_VAR}=1 to let the "
        "engine plan internally.\n"
    )


def _law7_host_plan_signal(args: argparse.Namespace, topic: str) -> str:
    """Identify a blocked agent run before configuration probes run."""
    if not topic:
        return ""
    if args.plan or args.mock or args.hiring_signals or args.auto_resolve:
        return ""
    if _comparison_requested(args, topic):
        return ""
    if env.engine_plan_allowed():
        return ""
    return env.agent_host_signal()


def _law7_host_plan_gate(args: argparse.Namespace, topic: str) -> int | None:
    """Stop a plan-less agent research run after dispatch and cache exemptions."""
    signal = _law7_host_plan_signal(args, topic)
    if not signal:
        return None
    sys.stderr.write(_law7_host_plan_message(signal))
    return LAW7_HOST_PLAN_EXIT


def _read_x_envelope(
    path: str,
    topic: str,
    args: argparse.Namespace,
    *,
    x_handle: str | None,
    x_related: list[str] | None,
) -> x_envelope.Envelope:
    """Validate a host-fetched X envelope against this run's window and topic."""
    from_date, to_date = dates.get_date_range(
        args.lookback_days or 30, as_of_date=args.as_of_date
    )
    return x_envelope.read(
        path,
        (from_date, to_date),
        topic,
        handles=[x_handle] if x_handle else [],
        related=[h for h in (x_related or []) if h and h.strip()],
    )


def _attach_entity_envelopes(comp_plan: dict[str, dict], args: argparse.Namespace) -> None:
    """Validate every per-entity ``x_posts`` path in a --competitors-plan.

    Each envelope is checked against its own entity (topic) and that entry's
    ``x_handle``/``x_related`` handles, and stored on the entry as
    ``_x_envelope`` for the entity sub-run. Raises EnvelopeContractError.
    """
    for entry in comp_plan.values():
        raw = entry.get("x_posts")
        if not raw:
            continue
        if not isinstance(raw, str) or _looks_inline_json(raw):
            raise x_envelope.EnvelopeContractError(
                f"--competitors-plan entry {entry.get('_name', '')!r}: x_posts must "
                "be a file path to a last30days-x-posts/1 envelope, never inline JSON. "
                "Rewrite the plan entry, or drop its x_posts field."
            )
        related = entry.get("x_related") if isinstance(entry.get("x_related"), list) else None
        entry["_x_envelope"] = _read_x_envelope(
            raw, str(entry.get("_name") or ""), args,
            x_handle=entry.get("x_handle") if isinstance(entry.get("x_handle"), str) else None,
            x_related=[str(h) for h in related] if related else None,
        )


def _combine_envelope_digests(main_sha256: str | None, entity_sha256: dict[str, str]) -> str | None:
    """One digest binding the last-report cache to every envelope a run uses.

    A single top-level envelope is bound by its own file digest; per-entity
    comparison envelopes are folded, name-sorted, into one digest. Both the
    cache write (validated envelopes) and the cache lookup (planned paths)
    must go through here so a comparison cache can be reused.
    """
    parts: list[str] = []
    if main_sha256:
        parts.append(main_sha256)
    for name in sorted(entity_sha256):
        parts.append(f"{name}:{entity_sha256[name]}")
    if not parts:
        return None
    if len(parts) == 1 and main_sha256:
        return main_sha256
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _x_envelope_digest(
    main: x_envelope.Envelope | None, comp_plan: dict[str, dict] | None
) -> str | None:
    """Digest of the validated envelopes this run used (cache write side)."""
    entity_sha256 = {
        name: entry["_x_envelope"].sha256
        for name, entry in (comp_plan or {}).items()
        if entry.get("_x_envelope") is not None
    }
    return _combine_envelope_digests(main.sha256 if main is not None else None, entity_sha256)


def _envelope_digest(
    x_main: x_envelope.Envelope | None,
    comp_plan: dict[str, dict] | None,
    reddit_threads: reddit_envelope.Envelope | None = None,
    shortform_urls: shortform_envelope.Envelope | None = None,
) -> str | None:
    """One digest binding the report cache to every host envelope of the run.

    Without a --reddit-threads or --shortform-urls envelope this is the X
    digest unchanged; with one, its file digest is folded in, so a different
    thread or URL list never replays a report built from another.
    """
    x_digest = _x_envelope_digest(x_main, comp_plan)
    if reddit_threads is None and shortform_urls is None:
        return x_digest
    parts = [f"x:{x_digest}"] if x_digest else []
    if reddit_threads is not None:
        parts.append(f"reddit:{reddit_threads.sha256}")
    if shortform_urls is not None:
        parts.append(f"shortform:{shortform_urls.sha256}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _validate_extra_argv(parser: argparse.ArgumentParser, topic: str, extra_argv: list[str]) -> None:
    if not extra_argv:
        return
    if topic.lower() == "setup":
        if USE_RECORDED_BROWSER_CONSENT_FLAG in extra_argv and len(extra_argv) > 1:
            parser.error(f"{USE_RECORDED_BROWSER_CONSENT_FLAG} must be the only setup option")
        # --store-key and --install-hooks/--uninstall-hooks carry optional
        # value tokens validated later by their handlers, not here.
        _, _, extra_argv = _split_store_key(extra_argv)
        _, _, extra_argv = _split_hook_flags(extra_argv)
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


def _has_invalid_cli_combination(args: argparse.Namespace, topic: str) -> bool:
    """True when parsed research flags are guaranteed to exit 2 before retrieval."""
    if args.discover is not None:
        return bool(
            topic
            or args.drill
            or args.as_of_date
            or args.emit == "html"
            or args.publish_html
            or args.deep_research
            or (args.nominate_only and (args.judgments is not None or args.finalize))
            or (args.judgments is not None and args.finalize)
            or (args.angles is not None and not args.finalize)
        )
    if (
        args.discover_shallow
        or args.nominate_only
        or args.judgments is not None
        or args.finalize
        or args.angles is not None
    ):
        return True
    if args.drill:
        return bool(topic or args.deep_research or (args.publish_html and args.emit != "html"))
    if args.deep_research and (not topic or _comparison_requested(args, topic)):
        return True
    if not topic:
        return True
    if (args.clarify_angle is not None or args.clarify_broadly) and not _will_tentatively_use_hosted_api(args, topic):
        return True
    if args.publish_html and args.emit != "html":
        return True
    return False


def _will_tentatively_use_hosted_api(args: argparse.Namespace, topic: str) -> bool:
    """True when process env and CLI flags may route a research topic to the hosted backend.

    Corpus flags/env vars do not disqualify hosted mode here because
    ``EXCLUDE_SOURCES=corpus`` in ``.env`` can disable the corpus and keep the
    run hosted. Local fallback reloads full config once ``uses_hosted_api`` is
    confirmed False.
    """
    if not topic or not env.hosted_backend_configured():
        return False
    if (
        args.cached_report
        or args.mock
        or args.record_fixtures
        or args.deep_research
        or args.discover is not None
        or args.drill
    ):
        return False
    if args.perplexity_search_type is not None and env.read_secret_env("PERPLEXITY_API_KEY"):
        return False
    return True


def _config_policy_for_args(args: argparse.Namespace, topic: str, extra_argv: list[str]) -> env.ConfigLoadPolicy:
    normalized_topic = "" if args.research_only or args.cached_report else topic.lower()
    reuse_browser_consent = normalized_topic == "setup" and USE_RECORDED_BROWSER_CONSENT_FLAG in extra_argv
    is_library_command = _is_library_command(normalized_topic)
    # Queue commands are local SQLite reads/writes: like library commands they
    # must never trigger browser-cookie extraction or Keychain prompts.
    is_queue_command = _is_queue_command(normalized_topic)
    is_cached_verification = (
        bool(getattr(args, "verify_freshness", None))
        and not topic
        and not args.drill
        and args.discover is None
    )
    is_inspection = bool(args.diagnose or args.preflight or normalized_topic == "doctor")
    is_invalid = _has_invalid_cli_combination(args, topic)
    tentative_hosted = _will_tentatively_use_hosted_api(args, topic)
    law7_blocked = bool(_law7_host_plan_signal(args, topic))
    if args.no_browser_cookies:
        browser_mode = "off"
    elif (
        is_inspection or args.cached_report
        or is_library_command or is_queue_command or is_cached_verification
    ):
        # doctor is plan-only like --diagnose: it must never read cookies.
        # Cache-only freshness verification hits only point APIs (Polymarket,
        # GitHub, StockTwits) - no cookie-backed source, so no Keychain prompt.
        browser_mode = "plan_only"
    elif normalized_topic == "setup":
        browser_mode = (
            "read"
            if not _setup_is_non_auto(extra_argv)
            and (reuse_browser_consent or _setup_has_fresh_browser_consent(args, extra_argv))
            else "off"
        )
    elif (
        args.mock
        or args.finalize
        or is_invalid
        or tentative_hosted
    ):
        # Mock runs, offline discovery finalize, empty/invalid invocations, and
        # tentative hosted-API runs never read local browser cookies.
        browser_mode = "off"
    elif law7_blocked:
        # Hosted and cached paths need config before dispatch, but no browser read.
        browser_mode = "plan_only"
    else:
        browser_mode = "read"

    # Skip Keychain / pass app-credential lookups on modes that do not need
    # local provider keys (mock, offline, cached, library, queue, invalid, or
    # hosted API runs). Hosted runs that fall back locally reload with stores on.
    if args.mock:
        load_secret_stores = False
    elif is_inspection:
        load_secret_stores = True
    elif (
        args.cached_report
        or is_library_command
        or is_queue_command
        or is_cached_verification
    ):
        load_secret_stores = False
    elif normalized_topic == "setup":
        load_secret_stores = not _setup_skips_secret_stores(extra_argv)
    elif (
        args.finalize
        or is_invalid
        or (tentative_hosted and args.perplexity_search_type is None)
    ):
        load_secret_stores = False
    elif law7_blocked and not (args.emit == "html" and args.synthesis_file and not args.deep_research):
        load_secret_stores = False
    else:
        load_secret_stores = True

    return env.ConfigLoadPolicy(
        browser_cookies=browser_mode,
        inspect_ignored_project_config=is_inspection,
        require_recorded_browser_consent=reuse_browser_consent,
        load_secret_stores=load_secret_stores,
    )


def _preflight_has_perplexity_key_for_research(
    args: argparse.Namespace,
    topic: str,
    extra_argv: list[str],
    config: dict[str, Any],
) -> bool:
    """Whether a real research run with ``args`` would see a direct PERPLEXITY_API_KEY.

    ``--preflight`` inspects with ``load_secret_stores=True``, whereas a real
    research run on an agent host without ``--plan`` skips Keychain/``pass``
    before hosted dispatch.
    """
    if not config.get("PERPLEXITY_API_KEY"):
        return False
    research_args = argparse.Namespace(**vars(args))
    research_args.preflight = False
    research_args.diagnose = False
    research_policy = _config_policy_for_args(
        research_args, topic or "preflight", extra_argv
    )
    if research_policy.load_secret_stores:
        return True
    no_store_policy = env.ConfigLoadPolicy(
        browser_cookies="off",
        allow_project_config=research_policy.allow_project_config,
        inspect_ignored_project_config=False,
        load_secret_stores=False,
    )
    return bool(env.get_config(policy=no_store_policy).get("PERPLEXITY_API_KEY"))


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

    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    scoped = _is_scoped_save_dir(args.save_dir)
    memory_dir = Path(args.save_dir).expanduser() if args.save_dir else library.DEFAULT_MEMORY_DIR
    output_dir = memory_dir.resolve()
    # Scoped libraries (--save-dir) must not mix in the global briefing
    # archive: a client-specific or publishable feed pulling unrelated default
    # briefings could publish them publicly. The default library keeps the
    # archive; a scoped one reads only its own directory.
    briefs_dir = (
        memory_dir / "briefings" if scoped else library.DEFAULT_BRIEFS_DIR
    )
    entries, notes = library.scan_library(memory_dir, briefs_dir)
    feed_author = str(
        config.get("LAST30DAYS_LIBRARY_OWNER") or "last30days research library"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    library_id = library.get_or_create_library_id(output_dir)
    rendered_briefs_dir = output_dir / "briefs"
    private_entries = {
        entry.entry_id for entry in entries if library.public_markdown(entry.content) != entry.content
    }
    _ensure_output_directory(rendered_briefs_dir, private=bool(private_entries))

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

    publishable_entries: list[library.LibraryEntry] = []
    publishable_brief_documents: dict[str, str] = {}
    omitted_private_briefs = 0
    for entry in entries:
        rendered = html_render.render_library_brief(entry)
        target = rendered_briefs_dir / entry.output_name
        _preserve_hand_written_page(target, html_render.LIBRARY_BRIEF_MARKER)
        save_rendered_output(
            rendered,
            str(target),
            private=entry.entry_id in private_entries,
        )
        if library.is_unbounded_private_brief(entry.content, source_format=entry.source_format):
            omitted_private_briefs += 1
            continue
        publishable_entries.append(entry)
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
        if omitted_private_briefs:
            sys.stderr.write(
                f"[last30days] Omitted {omitted_private_briefs} private local-corpus "
                "finished brief(s) from public publishing.\n"
            )
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
                publishable_entries,
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
                publishable_entries,
                library_id=library_id,
                entry_urls=entry_urls,
                author=feed_author,
            ),
            encoding="utf-8",
        )
        index_path.write_text(
            html_render.render_library_index(publishable_entries, entry_urls=entry_urls),
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

    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    scoped = _is_scoped_save_dir(args.save_dir)
    memory_dir = Path(args.save_dir).expanduser() if args.save_dir else library.DEFAULT_MEMORY_DIR
    store_db = (
        pipeline.scoped_store_db_path(args.save_dir)
        or library_index.DEFAULT_STORE_DB
    )
    try:
        matches, synced = library_index.sync_and_search(
            query,
            memory_dir=memory_dir,
            briefs_dir=(
                memory_dir / "briefings" if scoped else library.DEFAULT_BRIEFS_DIR
            ),
            db_path=(
                memory_dir.resolve() / ".last30days-library.db"
                if scoped else library_index.DEFAULT_LIBRARY_DB
            ),
            # A scoped search must never merge in the shared store: one
            # client's sightings would leak into another client's scope. A
            # scoped store is read only if it exists inside the save dir.
            store_db_path=store_db,
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


def _run_library_harvest(
    args: argparse.Namespace,
    config: dict[str, object],
) -> int:
    """Scan local agent transcripts offline and archive finished briefs."""
    from lib import brief_capture, library, library_index, transcript_harvest

    if args.publish or args.publish_html:
        sys.stderr.write("[last30days] library harvest does not publish output.\n")
        return 2
    if args.output:
        sys.stderr.write(
            "[last30days] library harvest writes into <save_dir>/finished/; "
            "--output is not supported.\n"
        )
        return 2

    since_date = None
    if args.harvest_since is not None:
        try:
            since_date = datetime.date.fromisoformat(args.harvest_since.strip())
        except ValueError:
            sys.stderr.write(
                f"[last30days] invalid --since date '{args.harvest_since}'; expected YYYY-MM-DD.\n"
            )
            return 2

    save_dir_exit = _validate_save_dir(args.save_dir)
    if save_dir_exit is not None:
        return save_dir_exit
    if args.save_dir == "":
        sys.stderr.write(
            "[last30days] saving is disabled (--save-dir / LAST30DAYS_MEMORY_DIR is empty); "
            "pass --save-dir <dir> to run library harvest.\n"
        )
        return 2
    memory_dir = Path(args.save_dir).expanduser() if args.save_dir else library.DEFAULT_MEMORY_DIR

    try:
        result = transcript_harvest.harvest_transcripts(
            save_dir=memory_dir,
            source=args.harvest_source or "auto",
            since=since_date,
            dry_run=bool(args.dry_run),
        )
    except ValueError as exc:
        sys.stderr.write(f"[last30days] {exc}\n")
        return 2

    if not args.dry_run and result.saved > 0:
        try:
            save_root = memory_dir.resolve()
            db_path = library_index.saved_report_index_path(save_root)
            if not _is_scoped_save_dir(save_root):
                library_index.sync_library(save_root)
            else:
                library_index.sync_library(save_root, save_root / "briefings", db_path=db_path)
        except (library_index.LibrarySearchUnavailable, OSError, sqlite3.DatabaseError):
            pass

    for note in result.notes:
        sys.stderr.write(f"[last30days] Harvest note: {note}\n")

    finished_dir = memory_dir.resolve() / brief_capture.FINISHED_SUBDIR
    saved_label = "Would save" if result.dry_run else "Saved"
    print(
        f"Scanned files: {result.scanned_files}\n"
        f"Matched briefs: {result.matched_briefs}\n"
        f"{saved_label}: {result.saved}\n"
        f"Duplicates: {result.duplicates}\n"
        f"Destination: {finished_dir}"
    )
    return 0


def _looks_like_entity_topic(topic: str) -> bool:
    """Whether a topic names a person, company, or product rather than a theme.

    Keys on brevity, not capitalization. People type lowercase: "bentgo",
    "peter steinberger" and "getenergy.com" are entity searches every bit as
    much as their title-cased forms, and requiring a capital meant the most
    common real-world spelling never resolved a handle.

    A short topic is an entity search; a longer one is a theme. "Peter
    Steinberger", "bentgo" and "getenergy.com" qualify; "best AI coding tools
    2026" and "how to build agents that scale" do not. Question-shaped topics
    are themes regardless of length.

    Used only to decide whether resolving an X handle is worth one web search,
    so a false negative costs the old behavior and a false positive costs a
    single search.
    """
    text = (topic or "").strip()
    if not text or text.endswith("?"):
        return False
    words = [w for w in re.findall(r"[A-Za-z0-9_.@'-]+", text) if w]
    if not words or len(words) > 4:
        return False
    if any(w.startswith("@") for w in words):
        return True
    # A theme reads as a phrase built from common words; an entity does not.
    common = {
        "best", "top", "how", "why", "what", "when", "vs", "versus", "guide",
        "tips", "review", "reviews", "news", "latest", "update", "updates",
        "trends", "tools", "and", "or", "for", "the", "with", "about",
    }
    return not any(w.lower() in common for w in words)


def main() -> int:
    command_started = time.monotonic()
    # Windows: Printing Press CLIs install off PATH; resolve them before any gate runs (#1434).
    health.add_managed_bin_dir_to_path()
    parser = build_parser()
    # Use parse_known_args so setup sub-flags (--device-auth, --github,
    # --openclaw) pass through without argparse hard-exiting.
    args, extra_argv = parser.parse_known_args()
    if args.as_of_date and dates.is_today(args.as_of_date):
        # Naming today is a live run: every lane takes its live path.
        args.as_of_date = None
    option_argv = sys.argv[1:sys.argv.index("--")] if "--" in sys.argv else sys.argv[1:]
    option_tokens = {token.split("=", 1)[0] for token in option_argv}
    args._explicit_options = {
        action.dest for action in parser._actions
        if any(option in option_tokens for option in action.option_strings)
    }
    if args.no_research_persistence:
        if (
            not args.research_only or not args.topic or args.discover is not None
            or args.drill or args.cached_report or args.synthesis_file
            or args.output or args.publish_html or args.record_fixtures
        ):
            parser.error("--no-research-persistence requires --research-only and a topic without output or cache modes")
        args.save_dir = ""
    if args.record_fixtures and (args.diagnose or args.preflight):
        # Reject before recording_requests() starts so read-only inspection
        # never creates or truncates a fixture file on exit.
        flag = "--preflight" if args.preflight else "--diagnose"
        sys.stderr.write(f"[last30days] --record-fixtures cannot be combined with {flag}.\n")
        return 2
    try:
        if args.record_fixtures:
            with http.recording_requests(Path(args.record_fixtures)):
                return _main(parser, args, extra_argv, command_started=command_started)
        return _main(parser, args, extra_argv, command_started=command_started)
    finally:
        _report_proxy_session()


def _report_proxy_session() -> None:
    """One stderr line with what the API-key proxy charged this run, if used."""
    session = proxy.session_id()
    if session:
        calls, credits = proxy.session_summary()
        sys.stderr.write(
            f"[last30days] API key session {session}: {proxy.format_credits(credits)} "
            f"credits across {calls} paid call{'' if calls == 1 else 's'}\n"
        )
        sys.stderr.flush()


def _quality_research_results(report, diag, yt_fetch_stats):
    youtube_items = report.items_by_source.get("youtube") or []
    instagram_items = report.items_by_source.get("instagram") or []
    x_outcome = report.source_status.get("x")
    x_degraded_error = None
    if (
        report.items_by_source.get("x")
        and x_outcome is not None
        and x_outcome.detail
        and x_outcome.detail.startswith("X served via ")
        and " after xai:" in x_outcome.detail
    ):
        x_degraded_error = x_outcome.detail
    return {
        "active_sources": diag.get("available_sources") or [],
        "youtube_videos_count": len(youtube_items),
        "youtube_transcripts_count": sum(
            1 for it in youtube_items
            if (it.metadata.get("transcript_highlights") or it.metadata.get("transcript_snippet"))
        ),
        "youtube_error": report.errors_by_source.get("youtube"),
        "instagram_error": report.errors_by_source.get("instagram"),
        "x_error": report.errors_by_source.get("x"),
        "x_degraded_error": x_degraded_error,
        "youtube_captions_disabled_count": sum(
            1 for it in youtube_items if it.metadata.get("captions_disabled")
        ),
        "youtube_transcript_fetch_attempts": yt_fetch_stats["attempts"],
        "youtube_transcript_fetch_failures": yt_fetch_stats["failures"],
        "youtube_transcript_target": yt_fetch_stats.get("target", 0),
        "youtube_bot_checked": yt_fetch_stats.get("blocked", 0),
        "youtube_sc_rescued": yt_fetch_stats.get("sc_rescued", 0),
        "instagram_items_count": len(instagram_items),
    }


def _unsupported_hosted_options(args: argparse.Namespace) -> list[str]:
    explicit_options = getattr(args, "_explicit_options", ())
    options = (
        ("--days/--lookback-days", args.lookback_days is not None),
        ("--as-of", args.as_of_date is not None or "as_of_date" in explicit_options),
        ("--plan", args.plan is not None),
        ("--auto-resolve", args.auto_resolve),
        ("--official-site", args.official_site is not None),
        ("--x-handle", args.x_handle is not None),
        ("--no-x-handle", args.no_x_handle is not None),
        ("--x-related", args.x_related is not None),
        ("--search", args.search is not None),
        ("--web-backend", args.web_backend != "auto"),
        ("--hiring-signals", args.hiring_signals),
        ("--company", args.company is not None),
        ("--ticker", args.ticker is not None),
        ("--jobs-board", args.jobs_board is not None),
        ("--subreddits", args.subreddits is not None),
        ("--dedicated-subreddits", args.dedicated_subreddits is not None),
        ("--devto-tags", args.devto_tags is not None),
        ("--tiktok-hashtags", args.tiktok_hashtags is not None),
        ("--tiktok-creators", args.tiktok_creators is not None),
        ("--ig-creators", args.ig_creators is not None),
        ("--github-user", args.github_user is not None),
        ("--github-repo", args.github_repo is not None),
        ("--employer", args.employer is not None),
        ("--linkedin", args.linkedin is not None),
        ("--email", args.email is not None),
        ("--trustpilot-domain", args.trustpilot_domain is not None),
        ("--amazon-query", args.amazon_query is not None),
        ("--meta-ads-page", args.meta_ads_page is not None),
        ("--telegram-sources", args.telegram_sources is not None),
        ("--polymarket-keywords", args.polymarket_keywords is not None),
        ("--competitors", args.competitors is not None),
        ("--competitors-list", args.competitors_list is not None),
        ("--competitors-plan", args.competitors_plan is not None),
        ("--max-results", args.max_results is not None),
        ("--max-per-source", args.max_per_source is not None),
        ("--max-source-fetches", args.max_source_fetches is not None),
        ("--corpus-all-time", args.corpus_all_time),
        ("--store", args.store),
        ("--output", args.output is not None),
        ("--synthesis-file", args.synthesis_file is not None),
        ("--publish-html", args.publish_html),
        (f"--emit={args.emit}", args.emit not in {"compact", "md", "json"}),
    )
    return [flag for flag, requested in options if requested]


def _main(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    extra_argv: list[str],
    *,
    command_started: float | None = None,
) -> int:
    if command_started is None:
        command_started = time.monotonic()
    finalization_deadline = command_started + pipeline.FINALIZATION_WORK_CUTOFF_SECONDS
    if args.include_research_backend and not args.diagnose:
        parser.error("--include-research-backend requires --diagnose")
    if args.record_fixtures and (args.diagnose or args.preflight):
        flag = "--preflight" if args.preflight else "--diagnose"
        sys.stderr.write(f"[last30days] --record-fixtures cannot be combined with {flag}.\n")
        return 2
    if args.debug:
        os.environ["LAST30DAYS_DEBUG"] = "1"

    if args.cached_report and (
        args.emit != "html" or not args.synthesis_file or not args.topic or args.deep_research
        or args.plan or args.competitors_plan or args.x_posts or args.reddit_threads
        or args.shortform_urls
        or args.discover is not None or args.drill or args.diagnose or args.preflight
        or args.welcome or args.resolve_save_dir or args.resolve_browser_consent
        or args.verify_freshness is True
    ):
        parser.error("--cached-report requires a topic, --emit=html, and --synthesis-file without research input files, --verify-freshness, or other command modes")
    topic = " ".join(args.topic).strip()
    cached_followup = (
        not topic and (args.drill or args.verify_freshness is True)
        and args.discover is None
        and not (
            args.diagnose or args.preflight or args.welcome
            or args.resolve_save_dir or args.resolve_browser_consent
        )
    )
    if args.followup_topic is not None and (not cached_followup or not args.followup_topic.strip()):
        parser.error("--followup-topic requires a nonempty topic and --drill or post-run --verify-freshness")
    if args.followup_run is not None and (not cached_followup or not re.fullmatch(r"[0-9a-f]{32}", args.followup_run)):
        parser.error("--followup-run requires a cache ID and --drill or post-run --verify-freshness")
    if cached_followup and any(
        value is not None
        for value in (args.x_posts, args.reddit_threads, args.shortform_urls, args.plan, args.competitors_plan)
    ):
        parser.error("cached follow-ups use saved evidence and do not accept new plans or envelope files")
    if args.welcome:
        from lib import setup_wizard
        print(setup_wizard.render_welcome())
        return 0
    person_exit = _person_inputs_gate(args, topic)
    if person_exit is not None:
        return person_exit

    original_topic = topic
    requested_window = (
        dates.get_date_range(
            30 if args.lookback_days is None else args.lookback_days,
            as_of_date=args.as_of_date,
        )
        if not args.cached_report or args.lookback_days is not None or args.as_of_date is not None else None
    )
    command_topic = "" if args.research_only or args.cached_report else topic.lower()
    _validate_extra_argv(parser, command_topic, extra_argv)
    if args.resolve_save_dir:
        try:
            print(env.resolve_memory_dir(args.save_dir))
        except ValueError as exc:
            sys.stderr.write(f"[last30days] {exc}\n")
            return 2
        return 0
    if args.resolve_browser_consent:
        granted = not args.no_browser_cookies and env.has_recorded_browser_consent()
        print("granted" if granted else "not-granted")
        return 0
    if args.x_posts is not None and _looks_inline_json(args.x_posts):
        sys.stderr.write(
            "[last30days] --x-posts accepts a file path only (inline JSON is not "
            "accepted); write the envelope to a .json file and pass its path.\n"
        )
        return 2
    if args.reddit_threads is not None and reddit_envelope.looks_inline_json(args.reddit_threads):
        sys.stderr.write(
            "[last30days] --reddit-threads accepts a file path or '-' only (inline "
            "JSON is not accepted); write the envelope to a .json file and pass its path.\n"
        )
        return 2
    if args.shortform_urls is not None and shortform_envelope.looks_inline_json(args.shortform_urls):
        sys.stderr.write(
            "[last30days] --shortform-urls accepts a file path or '-' only (inline "
            "JSON is not accepted); write the envelope to a .json file and pass its path.\n"
        )
        return 2
    if args.publish and command_topic != "library feed":
        sys.stderr.write(
            "[last30days] --publish is only supported by the 'library feed' command.\n"
        )
        return 2
    if (
        args.harvest_source is not None
        or args.harvest_since is not None
        or args.dry_run
    ) and command_topic != "library harvest":
        sys.stderr.write(
            "[last30days] --source, --since, and --dry-run are only supported by the 'library harvest' command.\n"
        )
        return 2
    if (args.preflight or args.diagnose) and _is_admin_command(command_topic):
        flag = "--preflight" if args.preflight else "--diagnose"
        sys.stderr.write(
            f"[last30days] {flag} cannot be combined with the '{command_topic}' "
            "command. Omit it to run the command, or pass --research-only to "
            "inspect a research topic with that name.\n"
        )
        return 2
    if command_topic == "setup":
        # Persisting a credential or managing local hook configs needs no
        # config load (no Keychain / pass probes, no cookie policy), so these
        # dispatch before get_config.
        store_key_present, store_key_name, _ = _split_store_key(extra_argv)
        if store_key_present:
            return _run_store_key(store_key_name)
        install_target, uninstall_target, _ = _split_hook_flags(extra_argv)
        if install_target is not None or uninstall_target is not None:
            return _run_hook_setup(install_target, uninstall_target)

    config_policy = _config_policy_for_args(args, topic, extra_argv)
    hosted_deferred_local_load = (
        not args.diagnose
        and not args.preflight
        and not _is_admin_command(command_topic)
        and not args.mock
        and not args.finalize
        and not _has_invalid_cli_combination(args, topic)
        and _will_tentatively_use_hosted_api(args, topic)
    )
    config = env.get_config(policy=config_policy)
    if args.no_research_persistence:
        config["_no_research_persistence"] = True
    if args.perplexity_search_type is not None:
        config["LAST30DAYS_PERPLEXITY_SEARCH_TYPE"] = args.perplexity_search_type
    # One memo per command: comparison mode runs pipeline.run per entity in
    # parallel, so the reset must not live inside the pipeline.
    http.reset_reddit_keyless_memo()
    reddit.reset_scrapecreators_memo()
    reddit_arctic.reset_memo()
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()
    reddit_health.reset_run_state()
    reddit_session.reset_run_state()
    web_search_keyless.reset_rung_health()
    is_recordable_research = (
        bool(topic) or bool(args.drill) or (args.discover is not None and not args.finalize)
    )
    if (
        args.record_fixtures
        and is_recordable_research
        and not _is_admin_command(command_topic)
        and not (args.diagnose or args.preflight or args.welcome or args.cached_report)
        and reddit_session.gate(config)[0]
    ):
        # Recorded fixtures are shared artifacts; a logged-in session's pages
        # (and its cookie) must never reach one.
        sys.stderr.write(
            "[last30days] --record-fixtures refuses to run while the logged-in Reddit "
            "session is on. Re-run with --no-browser-cookies, or set "
            "REDDIT_BROWSER_CONSENT=false, to record without it.\n"
        )
        return 2
    fetch_memo.reset()
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
    external_plan = None
    if args.plan:
        import json as _json
        plan_str = args.plan
        if plan_str == "-":
            try:
                stdin_buffer = getattr(sys.stdin, "buffer", None)
                plan_str = (
                    stdin_buffer.read().decode("utf-8")
                    if stdin_buffer is not None else sys.stdin.read()
                )
            except (OSError, UnicodeDecodeError) as exc:
                sys.stderr.write(f"[Planner] Cannot read --plan stdin: {exc}\n")
                raise SystemExit(2)
        elif os.path.isfile(plan_str):
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
        from lib import planner as _plan_validator
        try:
            _plan_validator.validate_external_plan(external_plan)
        except ValueError as exc:
            sys.stderr.write(f"[Planner] Invalid --plan schema: {exc}.\n")
            raise SystemExit(2)

    research_options = _cache_research_options(
        args, config, supplied_only=args.cached_report,
        resolved_corpus_dirs=resolved_corpus_dirs, external_plan=external_plan,
    )
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
    # Surface YOUTUBE_PROXY / YT_PROXY so youtube_yt can read them without a
    # config dependency. Shell-set values take precedence over .env values.
    if config.get("YOUTUBE_PROXY") and "YOUTUBE_PROXY" not in os.environ:
        os.environ["YOUTUBE_PROXY"] = config["YOUTUBE_PROXY"]
    if config.get("YT_PROXY") and "YT_PROXY" not in os.environ:
        os.environ["YT_PROXY"] = config["YT_PROXY"]

    if args.preflight:
        save_dir_exit = _validate_save_dir(args.save_dir)
        if save_dir_exit is not None:
            return save_dir_exit
        requested_sources = resolve_requested_sources(args.search, config)
        preflight_config = pipeline.with_plan_source_omissions(config, external_plan)
        diag = pipeline.diagnose(preflight_config, requested_sources, safe=True)
        hosted_bypassed = bool(
            args.cached_report
            or args.mock
            or args.record_fixtures
            or args.discover is not None
            or args.drill
            or (
                args.perplexity_search_type is not None
                and _preflight_has_perplexity_key_for_research(args, topic, extra_argv, config)
            )
        )
        preflight = permission_preflight.build(
            preflight_config,
            diag,
            planned_save_dir=args.save_dir,
            report_on_save_dir=(
                None if args.no_research_persistence else args.preflight_report_on_save_dir
            ),
            requested_sources=requested_sources,
            deep_research=bool(getattr(args, "deep_research", False)),
            local_only_run=hosted_bypassed,
        )
        _add_preflight_research_writes(preflight, args, config)
        if args.emit == "json":
            print(json.dumps(preflight, indent=2, sort_keys=True))
        else:
            print(permission_preflight.render_text(preflight), end="")
        return 0

    # Handle doctor subcommand: topic-word dispatch mirroring setup (exact
    # match only, so multi-word research topics containing "doctor" still
    # research normally). Aggregates probes/descriptors/prescriptions into
    # one grouped health surface; always exits 0.
    if command_topic == "doctor":
        from lib import doctor
        return doctor.run(
            config,
            emit_json=(args.emit == "json" or "--json" in extra_argv),
            cached="--cached" in extra_argv,
            postmortem="--postmortem" in extra_argv,
            probe="--probe" in extra_argv,
        )

    if command_topic == "library feed":
        return _run_library_feed(args, config)
    if command_topic == "library harvest":
        return _run_library_harvest(args, config)
    if command_topic == "library search" or command_topic.startswith("library search "):
        return _run_library_search(args, config, topic[len("library search") :].strip())

    if command_topic == "queue list":
        return _run_queue_list(args, config)
    if command_topic == "queue cover" or command_topic.startswith("queue cover "):
        return _run_queue_cover(args, config, topic[len("queue cover") :].strip())

    # Handle setup subcommand
    if command_topic == "setup":
        from lib import setup_wizard
        if "--openclaw" in extra_argv:
            results = setup_wizard.run_openclaw_setup(config)
            print(json.dumps(results))
            return 0
        if "--reddit-session" in extra_argv or "--no-reddit-session" in extra_argv:
            if "--reddit-session" in extra_argv and "--no-reddit-session" in extra_argv:
                sys.stderr.write("[last30days] pass --reddit-session or --no-reddit-session, not both.\n")
                return 2
            consent = "--reddit-session" in extra_argv
            if consent and args.no_browser_cookies:
                sys.stderr.write("[last30days] --no-browser-cookies refuses the Reddit login read.\n")
                return 2
            results = setup_wizard.run_reddit_session_setup(config, env.CONFIG_FILE, consent=consent)
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
        reuse_browser_consent = USE_RECORDED_BROWSER_CONSENT_FLAG in extra_argv
        if reuse_browser_consent and config.get("_BROWSER_COOKIE_MODE") != "read":
            print("not-granted")
            return 0
        fresh_browser_consent = _setup_has_fresh_browser_consent(args, extra_argv)
        sys.stderr.write("Running auto-setup...\n")
        results = setup_wizard.run_auto_setup(
            config,
            allow_browser_cookies=reuse_browser_consent or fresh_browser_consent,
        )
        # Keep only successful browsers, including distinct service winners;
        # "auto" would also probe browsers that did not supply any cookies.
        found_browsers = dict.fromkeys(
            "firefox" if browser == "firefox-wsl" else browser
            for browser in results.get("cookies_found", {}).values()
        )
        from_browser = ",".join(found_browsers) or None
        denied_browsers = (
            results.get("x_cookie_access_denied", [])
            if results.get("browser_cookie_scan_attempted") else []
        )
        prior_denial = config.get("LAST30DAYS_X_COOKIE_ACCESS_DENIED")
        denial_marker = (
            ",".join(denied_browsers) or ("none" if prior_denial else None)
            if results.get("browser_cookie_scan_attempted") else None
        )
        results["env_written"] = setup_wizard.write_setup_config(
            env.CONFIG_FILE,
            from_browser=from_browser,
            x_cookie_access_denied=denial_marker,
            browser_consent=None if args.diagnose or reuse_browser_consent else fresh_browser_consent,
        )
        if not results["env_written"]:
            sys.stderr.write("Setup configuration could not be fully saved; some settings may already be saved.\n")
            return 1
        sys.stderr.write(setup_wizard.get_setup_status_text(results) + "\n")
        return 0

    # Reject whitespace-only save dirs for commands that consume save_dir
    # (discover, drill, cached freshness, hosted/local research), while letting
    # diagnostic/setup commands (doctor, --diagnose, setup) run and report the
    # misconfiguration.
    if not args.diagnose:
        save_dir_exit = _validate_save_dir(args.save_dir)
        if save_dir_exit is not None:
            return save_dir_exit

    # Bare --discover (no domain) is global trending, so the dispatch keys on
    # "flag present" (is not None), never on the domain string's truthiness.
    if args.deep_research and not topic and not args.diagnose:
        sys.stderr.write(
            "[last30days] --deep-research requires a normal positional topic; "
            "it cannot be combined with discovery, drill, or cached-only modes.\n"
        )
        return 2

    if args.discover is not None and not args.diagnose:
        if topic:
            sys.stderr.write(
                "[last30days] --discover supplies the domain and cannot be combined "
                "with a positional topic.\n"
            )
            return 2
        if args.drill:
            sys.stderr.write("[last30days] --discover and --drill are mutually exclusive.\n")
            return 2
        # Shared guards for EVERY discover invocation - the one-shot and all
        # three protocol legs - hoisted here so no leg can drift: discovery
        # sweeps live listings (never --as-of) and has no HTML pipeline yet.
        if args.as_of_date:
            sys.stderr.write(
                "[last30days] --as-of cannot be used with --discover because discovery "
                "sweeps current live listings.\n"
            )
            return 2
        if args.emit == "html" or args.publish_html:
            sys.stderr.write("[last30days] discovery mode does not support HTML publishing yet.\n")
            return 2
        # The three protocol legs are one-leg-per-invocation: each pairing
        # below asks for two legs at once, so name the combination and stop.
        # (--judgments/--angles dispatch on presence, never path truthiness.)
        for first, second, conflict in (
            ("--nominate-only", "--judgments", args.nominate_only and args.judgments is not None),
            ("--nominate-only", "--finalize", args.nominate_only and args.finalize),
            ("--judgments", "--finalize", args.judgments is not None and args.finalize),
        ):
            if conflict:
                sys.stderr.write(
                    f"[last30days] {first} and {second} are mutually exclusive: "
                    "each runs a different leg of the discovery protocol.\n"
                )
                return 2
        if args.angles is not None and not args.finalize:
            sys.stderr.write(
                "[last30days] --angles only applies to --discover --finalize "
                "runs; add --finalize or drop the flag.\n"
            )
            return 2
        protocol_leg = (
            args.nominate_only or args.judgments is not None or args.finalize
        )
        if protocol_leg and args.mock and not args.save_dir:
            # Truthiness is right here: an empty --save-dir/env value means
            # "no save dir", and handoff state would land in the real config
            # dir - a side effect mock runs must never have.
            sys.stderr.write(
                "[last30days] mock protocol legs require --save-dir to stay "
                "side-effect-free: --mock with --nominate-only/--judgments/"
                "--finalize would otherwise write handoff state into the real "
                "config dir.\n"
            )
            return 2
        # --discover --judgments runs a multi-wave enrichment batch with its own
        # LAST30DAYS_ENRICH_BUDGET_SECONDS (default 450s) budget; injecting the
        # 280s foreground cutoff here aborts transcript backfill and reranking
        # for every topic finalizing past 280s.
        if args.judgments is None:
            config = {**config, "_finalization_deadline": finalization_deadline}
        if protocol_leg:
            return _run_discover_protocol_leg(args, config)
        return _run_discover(args, config)

    if args.discover_shallow and not args.diagnose:
        # Without --discover this flag would silently no-op into a full
        # research run - reject it instead of ignoring the requested mode.
        sys.stderr.write(
            "[last30days] --discover-shallow only applies to --discover runs; "
            "add --discover [domain] or drop the flag.\n"
        )
        return 2

    # Same orphan rule for every protocol-leg flag: without --discover each
    # would silently no-op into a normal research run.
    if not args.diagnose:
        for flag_label, present in (
            ("--nominate-only", args.nominate_only),
            ("--judgments", args.judgments is not None),
            ("--finalize", args.finalize),
        ):
            if present:
                sys.stderr.write(
                    f"[last30days] {flag_label} only applies to --discover runs; "
                    "add --discover [domain] or drop the flag.\n"
                )
                return 2
        if args.angles is not None:
            sys.stderr.write(
                "[last30days] --angles only applies to --discover --finalize runs; "
                "add --discover --finalize or drop the flag.\n"
            )
            return 2

    if args.drill and not args.diagnose:
        if topic:
            sys.stderr.write(
                "[last30days] --drill uses the cached topic and cannot be "
                "combined with a new topic.\n"
            )
            return 2
        publish_exit = _validate_publish_html(args)
        if publish_exit is not None:
            return publish_exit
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
        return _run_drill(args, {**config, "_finalization_deadline": finalization_deadline})

    if args.verify_freshness and not topic and not args.diagnose:
        publish_exit = _validate_publish_html(args)
        if publish_exit is not None:
            return publish_exit
        return _run_cached_freshness(args, config)

    if args.deep_research and not args.diagnose:
        if _comparison_requested(args, topic):
            sys.stderr.write(
                "Error: --deep-research cannot be combined with competitor or vs-mode. "
                "It permits one paid Deep Research run per user action; run each topic "
                "separately.\n"
            )
            return 2
        if not (
            config.get("PERPLEXITY_API_KEY")
            or config.get("OPENROUTER_API_KEY")
        ):
            print(
                "Error: --deep-research requires PERPLEXITY_API_KEY or "
                "OPENROUTER_API_KEY",
                file=sys.stderr,
            )
            return 1
        config["_deep_research"] = True
        try:
            enable_deep_research_source(config)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

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
    # to local-only runs - there is no built-in endpoint. LAST30DAYS_API_MODE=proxy
    # keeps the run local and sends only paid provider calls through the API
    # key's metered proxy (lib/proxy.py).
    if topic and env.proxy_mode_requested() and not proxy.configured():
        sys.stderr.write(
            "[last30days] LAST30DAYS_API_MODE=proxy needs LAST30DAYS_API_KEY and an https "
            "LAST30DAYS_API_BASE; paid sources without their own key stay off.\n"
        )
    if (
        topic
        and resolved_corpus_dirs
        and env.hosted_backend_configured()
    ):
        sys.stderr.write(
            "[last30days] Local corpus configured; bypassing the hosted backend so files stay on this machine.\n"
        )
    # An explicit --perplexity-search-type is per-invocation intent the hosted
    # backend cannot honor, so it runs locally, but only when a direct
    # PERPLEXITY_API_KEY can apply it; otherwise the switch would trade hosted
    # coverage for nothing. Key on the parsed CLI flag only: a value from
    # LAST30DAYS_PERPLEXITY_SEARCH_TYPE must never move routing.
    elif (
        topic
        and args.perplexity_search_type is not None
        and config.get("PERPLEXITY_API_KEY")
        and not args.diagnose
        and not args.mock
        and not args.record_fixtures
        and not args.deep_research
        and env.hosted_backend_configured()
    ):
        sys.stderr.write(
            "[last30days] --perplexity-search-type set; bypassing the hosted backend "
            "because it does not apply the Perplexity search type.\n"
        )
    uses_hosted_api = (
        env.hosted_backend_configured()
        and not args.cached_report
        and not args.mock
        and not args.record_fixtures
        and not resolved_corpus_dirs
        and not args.deep_research
        and (args.perplexity_search_type is None or not config.get("PERPLEXITY_API_KEY"))
    )
    # Validate hosted-only clarification flags before reloading local cookies or
    # Keychain/pass secrets on a local bypass.
    if (args.clarify_angle is not None or args.clarify_broadly) and not (
        topic and not args.diagnose and uses_hosted_api
    ):
        sys.stderr.write(
            "[last30days] --clarify-angle and --clarify-broadly answer a hosted backend "
            "clarification, so they apply only to a hosted run.\n"
        )
        return 2
    law7_blocked = bool(_law7_host_plan_signal(args, topic))
    may_reuse_html_cache = (
        args.emit == "html"
        and bool(args.synthesis_file)
        and not args.deep_research
        and not args.cached_report
    )
    target_browser_mode: env.BrowserCookieMode = (
        "off" if (args.no_browser_cookies or law7_blocked) else "read"
    )
    if (
        hosted_deferred_local_load
        and topic
        and not args.diagnose
        and not uses_hosted_api
        and (not law7_blocked or may_reuse_html_cache)
        and (
            config_policy.browser_cookies != target_browser_mode
            or not config_policy.load_secret_stores
        )
    ):
        # Confirmed local fallback (corpus or direct Perplexity key); reload
        # secret stores (and browser cookies only when research can run, never
        # on a LAW-7-blocked HTML cache-reuse lookup).
        config = env.get_config(
            policy=env.ConfigLoadPolicy(
                browser_cookies=target_browser_mode,
                load_secret_stores=True,
            )
        )
        if args.no_research_persistence:
            config["_no_research_persistence"] = True
        if args.perplexity_search_type is not None:
            config["LAST30DAYS_PERPLEXITY_SEARCH_TYPE"] = args.perplexity_search_type
        if resolved_corpus_dirs:
            config["_CORPUS_DIRS"] = [str(path) for path in resolved_corpus_dirs]
        if _config_truthy(config.get("LAST30DAYS_CORPUS_IN_EXPORT")):
            config["_CORPUS_IN_EXPORT"] = True
        _propagate_config_to_environ(config)
        research_options = _cache_research_options(
            args, config, supplied_only=args.cached_report,
            resolved_corpus_dirs=resolved_corpus_dirs, external_plan=external_plan,
        )
    if topic and not args.diagnose and uses_hosted_api:
        if args.require_local_report:
            sys.stderr.write(
                "[last30days] Watchlist requires structured local reports. "
                "Unset LAST30DAYS_API_BASE in the watchlist environment to run locally.\n"
            )
            return 2
        unsupported = _unsupported_hosted_options(args)
        if unsupported:
            sys.stderr.write(
                "[last30days] The hosted backend cannot apply these options: "
                f"{', '.join(unsupported)}. Run locally to use them.\n"
            )
            return 2
        if _comparison_requested(args, topic):
            sys.stderr.write(
                "[last30days] The hosted backend cannot run comparisons. "
                "Run locally to research each entity.\n"
            )
            return 2
        if args.perplexity_search_type is not None:
            sys.stderr.write(
                "hosted backend does not apply --perplexity-search-type and no direct "
                "PERPLEXITY_API_KEY is configured to run it locally; skipping\n"
            )
        elif config.get("LAST30DAYS_PERPLEXITY_SEARCH_TYPE"):
            sys.stderr.write(
                "hosted backend does not apply LAST30DAYS_PERPLEXITY_SEARCH_TYPE; skipping\n"
            )
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
        if env.reddit_consent_state(config) == "granted":
            sys.stderr.write(
                "[last30days] The logged-in Reddit session is not used with the hosted "
                "backend; no reddit.com cookie is read.\n"
            )
        if args.x_posts is not None:
            # The envelope is a local-engine contract; the remote API has no
            # lane to receive it.
            sys.stderr.write(
                "[last30days] --x-posts is not supported by the hosted backend; "
                "run locally or omit --x-posts.\n"
            )
            return 2
        if args.reddit_threads is not None:
            # Host thread refs are a local-engine contract too.
            sys.stderr.write(
                "[last30days] --reddit-threads is not supported by the hosted backend; "
                "run locally or omit --reddit-threads.\n"
            )
            return 2
        if args.shortform_urls is not None:
            sys.stderr.write(
                "[last30days] --shortform-urls is not supported by the hosted backend; "
                "run locally or omit --shortform-urls.\n"
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
        if args.clarify_angle is not None:
            hosted_kwargs["clarify"] = {"angle": args.clarify_angle}
        elif args.clarify_broadly:
            hosted_kwargs["clarify"] = {"run_broadly": True}
        return hosted.run_hosted(topic, depth, **hosted_kwargs)

    if args.lookback_days is None:
        args.lookback_days = 30
    requested_sources = resolve_requested_sources(args.search, config)
    if args.deep_research:
        requested_sources = add_deep_research_source(requested_sources)
    # Explicit --trustpilot-domain is user intent: activate the opt-in source
    # before diagnose/run so the flag cannot silently no-op (#873). Auto-resolve
    # hints are applied later and must not call this path.
    cli_trustpilot_domain = (
        args.trustpilot_domain.strip() if args.trustpilot_domain else ""
    )
    if cli_trustpilot_domain:
        requested_sources = activate_trustpilot_for_explicit_domain(
            config,
            requested_sources,
            reason=f"--trustpilot-domain={cli_trustpilot_domain}",
        )
    # Explicit --telegram-sources is user intent: activate the opt-in source
    # before diagnose/run so the flag cannot silently no-op (same pattern as
    # Trustpilot #873). Sets TELEGRAM_SOURCES in config for pipeline.
    cli_telegram_sources = (
        args.telegram_sources.strip() if args.telegram_sources else ""
    )
    if cli_telegram_sources:
        requested_sources = activate_telegram_for_explicit_sources(
            config,
            requested_sources,
            channels=cli_telegram_sources,
        )
    # --devto-tags activates the off-by-default dev.to source for this run
    # (the --trustpilot-domain pattern): carried on config for the dispatch,
    # and appended to an explicit or default --search list so it is not
    # filtered back out. EXCLUDE_SOURCES=devto still wins in the pipeline.
    cli_devto_tags = [
        t.strip() for t in (args.devto_tags or "").split(",") if t.strip()
    ]
    if cli_devto_tags:
        config["_devto_tags"] = cli_devto_tags
        if requested_sources is not None and "devto" not in requested_sources:
            requested_sources = [*requested_sources, "devto"]
    # Host-fetched X envelope: validated before diagnose so a present
    # envelope plans X in (available_sources) and a bad one fails closed here.
    x_posts_envelope: x_envelope.Envelope | None = None
    if args.x_posts is not None:
        if not topic:
            sys.stderr.write("[last30days] --x-posts requires a research topic.\n")
            return 2
        if _comparison_requested(args, topic):
            sys.stderr.write(
                "[last30days] --x-posts applies to a single-topic run; on a "
                "comparison run pass each entity's envelope through the "
                "x_posts field of its --competitors-plan entry.\n"
            )
            return 2
        try:
            x_posts_envelope = _read_x_envelope(
                args.x_posts, topic, args,
                x_handle=args.x_handle,
                x_related=args.x_related.split(",") if args.x_related else None,
            )
        except x_envelope.EnvelopeContractError as exc:
            sys.stderr.write(f"[last30days] {exc.message}\n")
            return 2
    # Host-found Reddit thread refs: validated before any research so a bad
    # file fails closed here and the report cache binds to its digest.
    reddit_threads_envelope: reddit_envelope.Envelope | None = None
    reddit_excluded = "reddit" in {
        token.strip().lower() for token in str(config.get("EXCLUDE_SOURCES") or "").split(",")
    }
    if args.reddit_threads is not None and (
        reddit_excluded or (requested_sources is not None and "reddit" not in requested_sources)
    ):
        # Reddit is not part of this run: the file is never read, so it can
        # neither stop the run nor change the report cache key.
        sys.stderr.write("[last30days] --reddit-threads ignored: Reddit is not searched this run.\n")
        args.reddit_threads = None
    if args.reddit_threads is not None:
        if not topic:
            sys.stderr.write("[last30days] --reddit-threads requires a research topic.\n")
            return 2
        if _comparison_requested(args, topic):
            sys.stderr.write(
                "[last30days] --reddit-threads applies to a single-topic run; "
                "omit it on a comparison run.\n"
            )
            return 2
        try:
            reddit_threads_envelope = reddit_envelope.read(args.reddit_threads, topic)
        except reddit_envelope.EnvelopeContractError as exc:
            sys.stderr.write(f"[last30days] {exc.message}\n")
            return 2
    # Host-found TikTok / Instagram URLs: validated up front like the Reddit
    # refs. Only the keyless short-form lane reads them.
    shortform_urls_envelope: shortform_envelope.Envelope | None = None
    if args.shortform_urls is not None:
        excluded = {
            token.strip().lower() for token in str(config.get("EXCLUDE_SOURCES") or "").split(",")
        }
        searched = [
            name for name in ("tiktok", "instagram")
            if name not in excluded and (requested_sources is None or name in requested_sources)
        ]
        if not searched:
            sys.stderr.write(
                "[last30days] --shortform-urls ignored: TikTok and Instagram are not searched this run.\n"
            )
            args.shortform_urls = None
    if args.shortform_urls is not None:
        if not topic:
            sys.stderr.write("[last30days] --shortform-urls requires a research topic.\n")
            return 2
        if _comparison_requested(args, topic):
            sys.stderr.write(
                "[last30days] --shortform-urls applies to a single-topic run; "
                "omit it on a comparison run.\n"
            )
            return 2
        try:
            shortform_urls_envelope = shortform_envelope.read(args.shortform_urls, topic)
        except shortform_envelope.EnvelopeContractError as exc:
            sys.stderr.write(f"[last30days] {exc.message}\n")
            return 2
        if all(
            (env.get_tiktok_token(config) if name == "tiktok" else env.get_instagram_token(config))
            for name in searched
        ):
            sys.stderr.write(
                "[last30days] --shortform-urls: ScrapeCreators is configured, so it stays the "
                "TikTok/Instagram path; the host URLs are used only if it runs out of credits.\n"
            )
    if args.diagnose:
        diag = pipeline.diagnose(
            pipeline.with_plan_source_omissions(config, external_plan),
            requested_sources, safe=True,
            x_envelope=x_posts_envelope is not None,
        )
        if args.include_research_backend:
            diag["research_backend"] = "hosted" if uses_hosted_api else "local"
        print(json.dumps(diag, indent=2, sort_keys=True))
        return 0

    # Per-entity host-fetched X envelopes are validated here, on the main
    # thread and BEFORE the report-cache lookup, so a bad or stale one fails
    # closed (exit 2) instead of silently dropping that entity inside the
    # fan-out or being served from a cache built while it was still valid.
    comp_plan = parse_competitors_plan(args.competitors_plan)
    try:
        _attach_entity_envelopes(comp_plan, args)
    except x_envelope.EnvelopeContractError as exc:
        sys.stderr.write(f"[last30days] {exc.message}\n")
        return 2

    if not topic:
        parser.print_usage(sys.stderr)
        return 2
    publish_exit = _validate_publish_html(args)
    if publish_exit is not None:
        return publish_exit
    comp_emit_exit = _validate_comparison_emit(
        args, is_comparison=_comparison_requested(args, topic)
    )
    if comp_emit_exit is not None:
        return comp_emit_exit

    synthesis_md = _resolve_synthesis_md(args)

    if not os.environ.get("LAST30DAYS_SKIP_PREFLIGHT"):
        from lib import preflight
        refuse_msg = preflight.check_class_1_trap(topic)
        if refuse_msg:
            sys.stderr.write(refuse_msg)
            return 2

    if (
        args.emit == "html"
        and synthesis_md is not None
        and not args.deep_research
    ):
        cached = _load_last_report_cache(
            topic,
            ttl_seconds=_report_cache_ttl_seconds(config),
            x_envelope_sha256=_envelope_digest(
                x_posts_envelope, comp_plan, reddit_threads_envelope, shortform_urls_envelope,
            ),
            research_options=research_options,
            requested_window=requested_window,
            use_cached_envelopes=args.cached_report,
        )
        if cached is not None:
            cached_report = cached.report
            cached_entity_reports = cached.entity_reports
            cache_path = cached.path
            sys.stderr.write(
                f"[last30days] Reusing cached report data from {cache_path}\n"
            )
            sys.stderr.flush()
            if not args.cached_report and _freshness_enabled(args, config):
                _verify_report_set(
                    cached_report,
                    cached_entity_reports,
                    allow_network=not args.mock,
                )
                _update_cached_freshness(cached)
            return _render_save_and_print(
                args, cached_report, cached_entity_reports, synthesis_md, config
            )
        if args.cached_report:
            sys.stderr.write("[last30days] No matching cached report data; rerun research with recreated plans before exporting HTML.\n")
            return 2
        sys.stderr.write(
            "[last30days] No matching cached report data for "
            "--emit=html --synthesis-file; running fresh research.\n"
        )
        sys.stderr.flush()

    # LAW 7: an agent host that skipped --plan stops before live diagnostics,
    # auto-resolve, the internal planner, or any source retrieval can spend.
    law7_exit = _law7_host_plan_gate(args, topic)
    if law7_exit is not None:
        return law7_exit

    diag = pipeline.diagnose(
        pipeline.with_plan_source_omissions(config, external_plan),
        requested_sources, safe=args.mock,
        x_envelope=x_posts_envelope is not None,
        probe_x=(
            (external_plan is None or pipeline._external_plan_names_source(external_plan, "x"))
            and not (args.hiring_signals and requested_sources is None)
            and not any(entry.get("_x_envelope") for entry in comp_plan.values())
        ),
    )
    if args.deep_research:
        from lib import perplexity

        # Deep Research defaults to a 600s Perplexity wait; anchor the command
        # finalization cutoff past that timeout plus a finalization reserve.
        research_finalization_deadline = (
            command_started
            + perplexity.deep_timeout_seconds(config)
            + pipeline.DEEP_RESEARCH_RECEIPT_SECONDS
            + pipeline.FINALIZATION_WORK_CUTOFF_SECONDS
        )
    else:
        research_finalization_deadline = finalization_deadline
    config = {**config, "_finalization_deadline": research_finalization_deadline}

    # Competitor sub-runs shallow-copy this config. The shared object makes the
    # paid Perplexity cap command-wide and thread-safe across that fanout. Keep
    # this runtime-only object out of the safe diagnose configuration contract.
    config["_perplexity_paid_budget"] = pipeline.PaidSourceBudget()

    progress = ui.ProgressDisplay(topic, show_banner=True)
    progress.start_processing()

    depth = "deep" if args.deep else "quick" if args.quick else "default"
    # CLI overrides for the depth profile's result caps (issue #716). Stashed on
    # config so pipeline.run() can apply them without widening its signature; the
    # comparison path inherits them via `entity_config = dict(config)`.
    if args.max_results is not None:
        config["_max_results"] = args.max_results
    if args.max_per_source is not None:
        config["_max_per_source"] = args.max_per_source
    if args.max_source_fetches is not None:
        config["_max_source_fetches"] = args.max_source_fetches
    try:
        x_related = [h.strip() for h in args.x_related.split(",") if h.strip()] if args.x_related else None
        subreddits = [s.strip().removeprefix("r/") for s in args.subreddits.split(",") if s.strip()] if args.subreddits else None
        dedicated_subreddits = [s.strip().removeprefix("r/") for s in args.dedicated_subreddits.split(",") if s.strip()] if args.dedicated_subreddits else None
        tiktok_hashtags = [h.strip().lstrip("#") for h in args.tiktok_hashtags.split(",") if h.strip()] if args.tiktok_hashtags else None
        tiktok_creators = [c.strip().lstrip("@") for c in args.tiktok_creators.split(",") if c.strip()] if args.tiktok_creators else None
        ig_creators = [c.strip().lstrip("@") for c in args.ig_creators.split(",") if c.strip()] if args.ig_creators else None
        # Auto-resolve: use web search to discover subreddits/handles before planning.
        # This is the engine-side equivalent of SKILL.md Steps 0.55/0.75 for platforms
        # without WebSearch (OpenClaw, Codex, raw CLI).
        repos_from_auto_resolve = False
        trustpilot_domain_is_hint = False
        x_handle_from_auto = False
        github_user_from_auto = False
        auto_official_site = ""
        # Resolve automatically for entity-shaped topics even without the flag.
        # A person or company topic whose handle the user did not supply is the
        # case where first-party evidence is hardest to protect: the handle is
        # absent from the topic and may never appear in retrieved mentions, so
        # nothing downstream can identify the subject's own posts. One web
        # search closes that. If it returns nothing, pipeline.run skips the X
        # relevance floor entirely — a noisier report beats losing evidence.
        # Skipped when a handle was already supplied, when an external plan
        # owns resolution, or in mock runs.
        # A person run resolves who the person is before the X-handle gate:
        # a handle on their profile or bio page changes which lanes run.
        person_ctx = _person_resolve_step(args, topic, config, requested_sources)
        if person_ctx is not None:
            config["_person"] = person_ctx

        auto_resolve_topic = topic
        if args.competitors_list is None and (args.competitors is None or comp_plan):
            from lib import planner as _planner
            vs_entities = _planner._comparison_entities(topic, uncapped=True)
            if len(vs_entities) >= 2:
                auto_resolve_topic = vs_entities[0]
        if (
            not args.auto_resolve
            and not external_plan
            and not args.x_handle
            and not args.mock
            and _looks_like_entity_topic(auto_resolve_topic)
        ):
            args.auto_resolve = True
            sys.stderr.write(
                "[AutoResolve] entity-shaped topic with no --x-handle; "
                "resolving the subject's handle so its own posts are not pruned\n"
            )

        if args.auto_resolve and not external_plan:
            from lib import resolve
            resolution = resolve.auto_resolve(auto_resolve_topic, config)
            if resolution.get("subreddits") and not subreddits:
                subreddits = resolution["subreddits"]
                sys.stderr.write(f"[AutoResolve] Subreddits: {', '.join(subreddits)}\n")
            # A person run takes handles only from the host or the person's own
            # profile and bio page; one picked by counting mentions may be a
            # namesake's.
            if resolution.get("x_handle") and not args.x_handle and person_ctx is None:
                args.x_handle = resolution["x_handle"]
                x_handle_from_auto = True
                sys.stderr.write(f"[AutoResolve] X handle: @{args.x_handle}\n")
            # Empty x_handle is intentional: do not invent a lexical stand-in.
            # pipeline.run treats an unidentified subject as "skip the X floor".
            if resolution.get("github_user") and not args.github_user and person_ctx is None:
                args.github_user = resolution["github_user"]
                github_user_from_auto = True
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
            if resolution.get("trustpilot_domain") and person_ctx is None:
                # The brand's review-page domain is its own site. A person run
                # skips it: a host whose label matches the bare name may be a
                # namesake's site.
                auto_official_site = resolution["trustpilot_domain"]
            if resolution.get("context"):
                # Inject context into external_plan metadata for the planner to use
                if not external_plan:
                    external_plan = None  # planner will use its own, but with context
                # Store context for the planner prompt injection
                config["_auto_resolve_context"] = resolution["context"]
                sys.stderr.write(f"[AutoResolve] Context: {resolution['context'][:80]}...\n")

        # The brand's homepage links its own X and GitHub accounts: the most
        # reliable first-party source, and keyless. A user flag wins over the
        # homepage; the homepage wins over a handle auto-resolve picked by
        # counting mentions in search results.
        official_site = (args.official_site or "").strip() or auto_official_site
        if official_site and not args.mock:
            from lib import resolve as resolve_lib
            socials = resolve_lib.official_site_socials(official_site)
            if socials["x_handle"] and (not args.x_handle or x_handle_from_auto):
                args.x_handle = socials["x_handle"]
                sys.stderr.write(f"[Resolve] X handle from {official_site}: @{args.x_handle}\n")
            if socials["github_user"] and (not args.github_user or github_user_from_auto):
                args.github_user = socials["github_user"]
                sys.stderr.write(f"[Resolve] GitHub user from {official_site}: @{args.github_user}\n")
        if args.no_x_handle and not args.x_handle:
            sys.stderr.write(f"[Resolve] No X handle: {args.no_x_handle.strip()}\n")
        x_decision_signal = _x_handle_decision_signal(
            args, topic, external_plan, diag.get("available_sources") or [],
        )
        if x_decision_signal:
            sys.stderr.write(_x_handle_decision_message(x_decision_signal))
            raise SystemExit(X_HANDLE_DECISION_EXIT)

        github_user = args.github_user.lstrip("@").lower() if args.github_user else None
        github_repos = [r.strip() for r in args.github_repo.split(",") if r.strip() and "/" in r.strip()] if args.github_repo else None
        trustpilot_domain = args.trustpilot_domain.strip() if args.trustpilot_domain else None

        comp_enabled, comp_count, comp_explicit = resolve_competitors_args(args)
        # comp_plan was parsed, and its per-entity envelopes validated, before
        # the report-cache lookup above.

        # Plan-level trustpilot_domain pins are the same user intent as the CLI
        # flag (already activated above). Auto-resolve hints must not activate.
        if plan_has_explicit_trustpilot_domain(comp_plan):
            requested_sources = activate_trustpilot_for_explicit_domain(
                config,
                requested_sources,
                reason="competitors-plan trustpilot_domain",
            )

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

        # Product keyword for the amazon source. Carried on config rather than
        # threaded through the run signature (the _polymarket_keywords idiom):
        # it is one optional string consumed in exactly two places.
        if getattr(args, "amazon_query", None):
            config["_amazon_query"] = args.amazon_query.strip()
            # Unlike --trustpilot-domain, this flag deliberately does NOT
            # auto-activate its source: the lane spends metered credits, so
            # turning it on stays an explicit request. But silence is the
            # wrong failure mode -- a model that resolves the keyword and
            # forgets the --search token would otherwise get no signal at
            # all that the flag did nothing.
            _amazon_requested = (
                (requested_sources and "amazon" in requested_sources)
                or "amazon" in str(config.get("INCLUDE_SOURCES") or "").lower()
            )
            if not _amazon_requested:
                sys.stderr.write(
                    "[Amazon] --amazon-query was set but the amazon source was not "
                    "requested; add it to --search (e.g. --search reddit,x,amazon) "
                    "or set INCLUDE_SOURCES=amazon. Ignoring the keyword.\n"
                )

        # Advertiser page override for the meta_ads source. Same shape as
        # --amazon-query (config-carried, warn-not-activate) and for the same
        # reason: the lane spends metered credits per call.
        if getattr(args, "meta_ads_page", None):
            page_id = parse_meta_ads_page(args.meta_ads_page)
            if not page_id:
                sys.stderr.write(
                    "[Meta Ads] --meta-ads-page must be a numeric Ad Library page id "
                    "or an Ad Library URL containing view_all_page_id; a facebook.com "
                    "vanity URL is not a page id. Ignoring the override.\n"
                )
            else:
                config["_meta_ads_page"] = page_id
                _meta_ads_requested = (
                    (requested_sources and "meta_ads" in requested_sources)
                    or "meta_ads" in str(config.get("INCLUDE_SOURCES") or "").lower()
                )
                if not _meta_ads_requested:
                    sys.stderr.write(
                        "[Meta Ads] --meta-ads-page was set but the meta_ads source "
                        "was not requested; add it to --search (e.g. --search "
                        "reddit,x,meta_ads) or set INCLUDE_SOURCES=meta_ads. "
                        "Ignoring the page.\n"
                    )

        # vs-mode / plan routing: split a vs-topic into main + peers unless
        # discover-N or an explicit --competitors-list already decided who runs.
        topic, comp_enabled, comp_count, comp_explicit = apply_vs_competitor_routing(
            topic,
            competitors_flag=args.competitors,
            comp_enabled=comp_enabled,
            comp_count=comp_count,
            comp_explicit=comp_explicit,
            comp_plan=comp_plan,
        )
        # Which company the topic names, from Wikidata. Like the homepage
        # lookup above it needs no key and does not depend on who planned the
        # run, so it happens with or without a host plan. Each entity of a
        # comparison is resolved on its own, after the split.
        # ``--ticker`` is the host's own answer for the main topic. Passed, it
        # is the only ticker source, checked against Yahoo after the run.
        host_tickers = (
            yahoo_finance.parse_host_tickers(args.ticker)
            if (args.ticker or "").strip() and not args.mock else None
        )
        patent_lane_wanted = _patent_lane_wanted(
            comparison=comp_enabled, mock=args.mock, hiring_signals=args.hiring_signals,
            depth=depth, config=config,
        )
        main_company = _resolved_company(
            topic, mock=args.mock, config=config, host_ticker=host_tickers is not None,
            patent_lane=patent_lane_wanted,
        )
        # The jobs lane reads the company from here; each comparison peer
        # replaces it with its own.
        host_companies = _host_companies(args.company)
        host_names = [company["name"] for company in host_companies]
        # The company's patent filings over the run's own window, fetched
        # beside the pipeline and collected when it returns.
        patent_lane = None
        if patent_lane_wanted:
            patent_lane = patents.start(
                _company_for_patents(main_company, host_names),
                *dates.get_date_range(args.lookback_days, as_of_date=args.as_of_date),
                config,
            )
        config["_company"] = _company_for_jobs(
            main_company, host_tickers, topic,
            official_site=official_site, host_names=host_names,
            host_sites=(
                {} if main_company.get("company")
                else _host_company_sites(host_companies, mock=args.mock, config=config)
            ),
        )
        # What makes the run about a company: with any of it, a host plan
        # that does not name the jobs lane gets it on its primary subquery.
        config["_company_signal"] = _company_signal(
            main_company, host_tickers, official_site, host_names,
            jobs_board=args.jobs_board or "",
        )
        if (args.jobs_board or "").strip():
            # The board URL the host found by web search; the jobs lane reads
            # it first and checks whose board it is.
            config["_jobs_board"] = args.jobs_board.strip()
        if comp_enabled:
            config["_perplexity_paid_budget"] = pipeline.PaidSourceBudget(
                owner=topic,
            )

        # Plan alone with zero peers (empty/invalid JSON object, or all entries
        # skipped) must not fall through to discover-N with a misleading abort.
        if (
            comp_enabled
            and not comp_explicit
            and args.competitors is None
            and args.competitors_plan
        ):
            sys.stderr.write(
                "[Competitors] --competitors-plan has no usable peer entries "
                "(and the topic is not a vs-comparison). Pass a non-empty plan, "
                "a vs-topic, --competitors-list, or --competitors N.\n"
            )
            return 2

        # Dedicated subs ride the config dict (already threaded to every source
        # fetch) so the keyless Reddit path can pull them floor-exempt without
        # widening pipeline.run / _retrieve_stream signatures.
        if dedicated_subreddits:
            config["_dedicated_subreddits"] = dedicated_subreddits

        def _main_runner(run_config: dict[str, object] | None = None) -> schema.Report:
            main_config = dict(config if run_config is None else run_config)
            r = pipeline.run(
                topic=topic,
                config=main_config,
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
                x_posts=(
                    comp_plan.get(topic.strip().lower(), {}).get("_x_envelope")
                    if comp_enabled else x_posts_envelope
                ),
                reddit_threads=None if comp_enabled else reddit_threads_envelope,
                shortform_urls=None if comp_enabled else shortform_urls_envelope,
            )
            patents.collect(patent_lane, r)
            r.artifacts["resolved"] = {
                "entity": topic,
                "x_handle": (args.x_handle or "").lstrip("@"),
                "subreddits": list(subreddits or []),
                "github_user": (github_user or ""),
                "github_repos": list(github_repos or []),
                "trustpilot_domain": (trustpilot_domain or ""),
                "context": main_config.get("_auto_resolve_context", "") or "",
                **main_company,
            }
            # The share-price window of a listed company. A mock run resolved
            # no company, so it never reaches the price CLI, and neither does
            # a run with EXCLUDE_SOURCES=yahoo_finance.
            if host_tickers is None:
                yahoo_finance.attach_market_data(r, main_company, main_config)
                return r
            checked = yahoo_finance.attach_host_tickers(r, host_tickers, main_config)
            # The company Wikidata named stays; the host's fills an empty one.
            host_company = checked.pop("company", "")
            r.artifacts["resolved"].update(checked)
            if host_company:
                r.artifacts["resolved"].setdefault("company", host_company)
            if checked.get("tickers"):
                sys.stderr.write(
                    f"[Resolve] {topic}: ticker {', '.join(checked['tickers'])} (host search)\n"
                )
            return r

        if comp_enabled:
            from lib import competitors as competitors_mod
            from lib import fanout, resolve as resolve_mod

            peers_auto_discovered = not comp_explicit
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
                        "See the skill's references/competitors.md for the full protocol.\n"
                        "\n"
                        "HEADLESS / CRON PATH (no hosting model available): set "
                        "BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / PARALLEL_API_KEY "
                        "and re-run.\n"
                        "\n"
                        "MINIMUM ESCAPE HATCH: pass --competitors-list 'A,B,C' to skip "
                        "discovery. Without --competitors-plan, peer sub-runs fall back to "
                        "planner defaults and produce visibly thinner data than the main.\n"
                    )
                    return 2
                discovered = competitors_mod.discover_competitors(
                    topic, comp_count, config, lookback_days=args.lookback_days,
                    is_company=(
                        not main_company.get("company_via_person")
                        and main_company.get("company_peer_discovery")
                        != company_resolve.COMPANY_PEER_KEYWORD
                        and bool(
                            main_company.get("company")
                            or config.get("_company_signal")
                        )
                    ),
                )
                if not discovered:
                    sys.stderr.write(
                        f"[Competitors] No peers discovered for {topic!r}; aborting "
                        "comparison run. Pass --competitors-list to override.\n"
                    )
                    return 2

            # run_competitor_fanout keys its results by label, so two
            # submissions sharing one collapse to a single report while the
            # returned list still carries two entries. That yields a
            # comparison of an entity against itself, and it hides a failed
            # main topic from the survivor check below: the duplicate peer's
            # report answers for the label the main run was supposed to fill.
            distinct_peers: list[str] = []
            claimed_labels = {comparison_label_key(topic)}
            for peer in discovered:
                key = comparison_label_key(peer)
                if key in claimed_labels:
                    sys.stderr.write(
                        f"[Competitors] Dropping {peer!r}: duplicates the main "
                        "topic or an earlier peer.\n"
                    )
                    continue
                claimed_labels.add(key)
                distinct_peers.append(peer)
            if not distinct_peers:
                sys.stderr.write(
                    f"[Competitors] No peer distinct from {topic!r} remains; "
                    "there is nothing to compare against. Pass "
                    "--competitors-list with distinct entities.\n"
                )
                return 2
            discovered = distinct_peers
            config["_comparison_entity_count"] = 1 + len(discovered)
            # Each entity's jobs lane leaves every entity's name out of the
            # story terms it searches its board for.
            config["_comparison_entities"] = [topic, *discovered]
            # Snapshot an immutable-by-convention base dict before fan-out
            # spawns worker threads so no thread iterates or mutates a shared
            # config concurrently.
            peer_base_config = dict(config)
            main_run_config = dict(peer_base_config)

            sys.stderr.write(
                f"[Competitors] Comparing: {topic} vs " + " vs ".join(discovered) + "\n"
            )

            def _competitor_runner(entity: str) -> schema.Report:
                # Resolution belongs to each entity, not the shared command.
                entity_config = dict(peer_base_config)
                entity_config.pop("_auto_resolve_context", None)
                # The main topic's company flags and run-scoped transient keys
                # are not a peer's.
                entity_config.pop("_company", None)
                entity_config.pop("_company_signal", None)
                entity_config.pop("_jobs_board", None)
                entity_config.pop("_jobs_story_terms", None)
                entity_config.pop("_financial_topic", None)
                entity_config.pop("_person", None)
                entity_config.pop("_x_envelope", None)
                entity_config.pop("_x_lane_missing", None)
                entity_config.pop("_reddit_envelope", None)
                entity_config.pop("_shortform_envelope", None)
                # The Amazon keyword is entity-SPECIFIC, unlike the depth caps
                # this shallow copy exists to inherit. Leaving the main topic's
                # keyword in place would search Weber SKUs for a Traeger peer,
                # render a rival's products as that peer's buyer evidence, and
                # multiply the metered spend by the number of entities. Drop it
                # so each peer derives its own keyword from its own topic; a
                # per-entity keyword can ride in the --competitors-plan entry.
                entity_config.pop("_amazon_query", None)
                # dev.to tags describe the main topic; a peer falls back to
                # the slug of its own name instead of a rival's tags.
                entity_config.pop("_devto_tags", None)
                # An advertiser page is per-entity state by definition: left in
                # place it would render one brand's ads as every peer's.
                entity_config.pop("_meta_ads_page", None)
                entity_config.pop("_dedicated_subreddits", None)
                # Auto-discovered peers come from external search titles (e.g. Exa),
                # never from user input, so an r/<name> mention in a peer title
                # must not grant access to private or quarantined subreddits.
                if peers_auto_discovered:
                    entity_config["_reddit_named_subreddits"] = frozenset()
                else:
                    entity_config.pop("_reddit_named_subreddits", None)
                # Polymarket keywords and hints name the main topic's subject;
                # a peer searches on its own name plus its own plan entry hints.
                entity_config.pop("_polymarket_keywords", None)
                entity_config.pop("_polymarket_hints", None)
                plan_entry = comp_plan.get(entity.strip().lower(), {})
                if plan_entry.get("polymarket_hints"):
                    entity_config["_polymarket_hints"] = plan_entry["polymarket_hints"]
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
                    **_resolved_company(entity, mock=args.mock, config=entity_config),
                }
                entity_config["_company"] = resolved_effective
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
                    x_posts=plan_entry.get("_x_envelope"),
                )
                report.artifacts["resolved"] = resolved_effective
                yahoo_finance.attach_market_data(report, resolved_effective, entity_config)
                return report

            entity_reports = fanout.run_competitor_fanout(
                main_topic=topic,
                main_runner=lambda: _main_runner(main_run_config),
                competitors=discovered,
                competitor_runner=_competitor_runner,
            )
            # run_competitor_fanout drops a failed sub-run from the list, and
            # the render takes entity_reports[0] as the comparison's subject.
            # Without this check, a main topic that raised while >=2 peers
            # succeeded silently promoted a competitor to be the subject: the
            # report was headed by that peer, saved under its slug, and the
            # topic the user actually asked about went unmentioned.
            survived = {label for label, _ in entity_reports}
            dropped = [
                label for label in (topic, *discovered) if label not in survived
            ]
            if topic not in survived:
                progress.end_processing()
                sys.stderr.write(
                    f"[Competitors] The main topic {topic!r} failed; "
                    f"{len(entity_reports)} competitor sub-run(s) survived. "
                    "Refusing to render a comparison headed by a competitor. "
                    "Check the warnings above.\n"
                )
                return 1
            if len(entity_reports) < 2:
                progress.end_processing()
                sys.stderr.write(
                    f"[Competitors] Fewer than 2 sub-runs survived ({len(entity_reports)}); "
                    "cannot render a comparison. Re-run without --competitors or check the "
                    "warnings above.\n"
                )
                return 1
            report = entity_reports[0][1]
            if dropped:
                # A narrower comparison than the user asked for is a result
                # they need to see, not a silent substitution.
                report.warnings.append(
                    "Comparison is incomplete: "
                    f"{len(dropped)} of {len(discovered) + 1} entities failed and "
                    f"were dropped ({', '.join(dropped)})."
                )
        else:
            entity_reports = None
            report = _main_runner()
    except pipeline.NoRequestedSourceAvailable as exc:
        # A setup problem, not a crash: say what to do instead of a traceback
        progress.end_processing()
        sys.stderr.write(f"{exc}\n")
        return 2
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
    if not args.no_research_persistence:
        _write_last_run(
            original_topic, report, entity_reports=entity_reports,
            x_envelope_sha256=_envelope_digest(
                x_posts_envelope, comp_plan, reddit_threads_envelope, shortform_urls_envelope,
            ),
            research_options=research_options,
        )
        if _research_store_enabled(args, config):
            store_db = _scoped_store_db(args)
            reports_to_store = (
                [item for _, item in entity_reports] if entity_reports else [report]
            )
            counts = {"new": 0, "updated": 0}
            for current in reports_to_store:
                current_counts = persist_report(current, store_db=store_db) or {}
                counts["new"] += int(current_counts.get("new") or 0)
                counts["updated"] += int(current_counts.get("updated") or 0)
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
            research_results = _quality_research_results(
                report, diag, _youtube_yt.get_transcript_fetch_stats()
            )
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

    exit_code = _render_save_and_print(args, report, entity_reports, synthesis_md, config)
    if args.emit in {"compact", "md", "brief"}:
        x_omission = _optional_x_omission_text(diag, requested_sources)
        if x_omission:
            sys.stderr.write(f"\n{x_omission}\n")
            sys.stderr.flush()
    return exit_code


if __name__ == "__main__":
    _install_sigterm_handler()
    raise SystemExit(main())
