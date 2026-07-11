"""Deterministic, local-only document corpus source.

The corpus adapter deliberately has no HTTP dependency. It scans explicitly
registered directories, extracts small text documents (and PDFs only when the
local ``pdftotext`` binary is available), and returns normalized ``SourceItem``
objects for the shared relevance/fusion pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any, Iterable

from . import entity_extract, log, relevance, schema

SOURCE = "corpus"
SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
IGNORED_DIRECTORIES = {".git", "node_modules"}
MAX_FILES = 500
MAX_TEXT_CHARS = 1_000_000
MAX_CACHE_TEXT_CHARS = MAX_TEXT_CHARS
MAX_CACHE_BYTES = 50 * 1024 * 1024
MAX_CACHE_ENTRIES = 2_000
CACHE_FILENAME = "corpus-cache.json"
CACHE_SCHEMA_VERSION = "last30days-corpus-cache/v2"

_CACHE_LOCK = threading.Lock()


@dataclass
class CorpusScanResult:
    """One bounded scan, including non-fatal extraction notes."""

    items: list[schema.SourceItem]
    notes: list[str] = field(default_factory=list)
    files_scanned: int = 0
    cache_hits: int = 0


def resolve_directories(
    cli_directories: Iterable[str] | None,
    configured: str | Iterable[str] | None,
) -> list[Path]:
    """Merge repeatable CLI paths with ``os.pathsep``-separated config paths."""
    raw: list[str] = [str(value) for value in (cli_directories or []) if str(value).strip()]
    if isinstance(configured, str):
        raw.extend(value for value in configured.split(os.pathsep) if value.strip())
    elif configured:
        raw.extend(str(value) for value in configured if str(value).strip())

    resolved: list[Path] = []
    seen: set[str] = set()
    for value in raw:
        path = Path(value.strip()).expanduser().resolve()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _safe_error(exc: BaseException) -> str:
    """Describe an error without str(exc), which embeds absolute paths.

    These notes travel into source_status detail and render in coverage
    diagnostics outside the private corpus block.
    """
    reason = getattr(exc, "strerror", None)
    return str(reason) if reason else exc.__class__.__name__


def search(
    topic: str,
    directories: Iterable[Path | str],
    *,
    from_date: str,
    to_date: str,
    all_time: bool = False,
    limit: int = 12,
    cache_dir: Path | None = None,
) -> CorpusScanResult:
    """Search registered directories without making any network calls."""
    roots = resolve_directories([str(path) for path in directories], None)
    notes: list[str] = []
    cache_path = cache_dir / CACHE_FILENAME if cache_dir is not None else None
    with _CACHE_LOCK:
        cache = _load_cache(cache_path)
    cache_entries = cache.setdefault("entries", {})
    cache_entry_sizes = {
        path: _cache_entry_fragment_size(path, value)
        for path, value in cache_entries.items()
    }

    candidates: list[tuple[float, int, schema.SourceItem]] = []
    seen_files: set[str] = set()
    files_scanned = 0
    cache_hits = 0
    pdf_available = which("pdftotext")
    pdf_unavailable_noted = False

    readable_roots: list[Path] = []
    for root in roots:
        if not root.is_dir():
            notes.append(f"Skipped corpus root '{Path(root).name}': not a readable directory")
            continue
        readable_roots.append(root)

    per_root_limit, extra_slots = divmod(MAX_FILES, len(readable_roots) or 1)
    scan_limit_reached = False
    for root_index, root in enumerate(readable_roots):
        root_limit = per_root_limit + (1 if root_index < extra_slots else 0)
        root_files_scanned = 0
        for path in _iter_files(root, notes=notes):
            if root_files_scanned >= root_limit:
                scan_limit_reached = True
                break
            key = os.path.normcase(str(path))
            if key in seen_files:
                continue
            seen_files.add(key)
            root_files_scanned += 1
            files_scanned += 1

            try:
                stat = path.stat()
            except OSError as exc:
                notes.append(f"Skipped {_display_path(path, root)}: {_safe_error(exc)}")
                continue
            published_at = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).date().isoformat()
            if not all_time and not (from_date <= published_at <= to_date):
                continue

            cached = cache_entries.get(str(path))
            if (
                isinstance(cached, dict)
                and cached.get("mtime_ns") == stat.st_mtime_ns
                and cached.get("size") == stat.st_size
                and isinstance(cached.get("text"), str)
            ):
                text = cached["text"]
                cache_hits += 1
            else:
                if path.suffix.lower() == ".pdf" and not pdf_available:
                    if not pdf_unavailable_noted:
                        notes.append("Skipped PDF files because pdftotext is not on PATH")
                        pdf_unavailable_noted = True
                    continue
                try:
                    text = _extract_text(path, pdftotext=pdf_available)
                except (OSError, subprocess.SubprocessError) as exc:
                    notes.append(f"Skipped {_display_path(path, root)}: {_safe_error(exc)}")
                    continue
                _cache_entry_put(cache_entries, cache_entry_sizes, str(path), {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "text": text[:MAX_CACHE_TEXT_CHARS],
                })

            title = _path_title(path)
            score = _match_score(topic, f"{title}\n{text}")
            if score < 0.15:
                continue
            relative_path = str(path.relative_to(root))
            path_digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
            item = schema.SourceItem(
                item_id=f"C{path_digest[:12]}",
                source=SOURCE,
                title=title,
                body=text,
                url=f"corpus://{path_digest}",
                container=str(path.parent),
                published_at=published_at,
                date_confidence="high",
                relevance_hint=score,
                why_relevant=f"Matched local file {relative_path}",
                # Leave empty so extract_best_snippet derives the matching
                # window; a file-prefix snippet is preserved verbatim and can
                # show unrelated intro text (and draw entity-miss demotion).
                snippet="",
                metadata={
                    "path": str(path),
                    "relative_path": relative_path,
                    "extension": path.suffix.lower(),
                    "local_only": True,
                },
            )
            candidates.append((score, stat.st_mtime_ns, item))
    if scan_limit_reached:
        notes.append(f"Stopped after the {MAX_FILES}-file corpus scan limit")

    cache["schema_version"] = CACHE_SCHEMA_VERSION
    cache["entries"] = _bounded_entries(cache_entries)
    with _CACHE_LOCK:
        _write_cache(cache_path, cache, notes)

    candidates.sort(key=lambda row: (-row[0], -row[1], row[2].title.casefold()))
    items = [item for _score, _mtime, item in candidates[: max(0, limit)]]
    log.source_log(
        "Corpus",
        f"scanned {files_scanned} file(s), {cache_hits} cache hit(s), {len(items)} match(es)",
        tty_only=False,
    )
    return CorpusScanResult(
        items=items,
        notes=notes,
        files_scanned=files_scanned,
        cache_hits=cache_hits,
    )


def _display_path(path: Path | str, root: Path | None = None) -> str:
    """Render a note-safe path: never the absolute local path.

    Corpus notes flow into source_status detail and the Partial Coverage
    block, which render OUTSIDE the private corpus markers - an absolute
    path like /home/user/private/notes/foo.md must not escape there.
    """
    candidate = Path(path)
    if root is not None:
        try:
            return str(Path(root).name / candidate.relative_to(root))
        except ValueError:
            pass
    return candidate.name


def _iter_files(root: Path, notes: list[str] | None = None) -> Iterable[Path]:
    # Bounded newest-first selection: keep only the newest MAX_FILES paths in a
    # heap while walking, so registering a huge tree does not materialize every
    # path before the caller's extraction cap applies.
    import heapq

    heap: list[tuple[int, str]] = []
    walk_errors = 0

    def _on_walk_error(error: OSError) -> None:
        nonlocal walk_errors
        walk_errors += 1
        if notes is not None and walk_errors <= 3:
            unreadable = _display_path(error.filename, root) if error.filename else Path(root).name
            notes.append(f"corpus: could not read {unreadable}: {error.strerror}")

    for current, directory_names, file_names in os.walk(
        root, followlinks=False, onerror=_on_walk_error
    ):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES and not name.startswith(".")
        )
        current_path = Path(current)
        for name in sorted(file_names):
            if name.startswith("."):
                continue
            path = current_path / name
            if path.suffix.lower() in SUPPORTED_SUFFIXES and not path.is_symlink():
                entry = (_safe_mtime_ns(path), str(path))
                if len(heap) < MAX_FILES:
                    heapq.heappush(heap, entry)
                else:
                    heapq.heappushpop(heap, entry)
    if notes is not None and walk_errors > 3:
        notes.append(f"corpus: {walk_errors - 3} more unreadable directories suppressed")
    ordered = sorted(heap, key=lambda item: (-item[0], item[1].casefold()))
    for _mtime, raw_path in ordered:
        yield Path(raw_path)


def _safe_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _extract_text(path: Path, *, pdftotext: str | None) -> str:
    if path.suffix.lower() == ".pdf":
        if not pdftotext:
            return ""
        completed = subprocess.run(
            [pdftotext, str(path), "-"],
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
        return completed.stdout[:MAX_TEXT_CHARS]
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(MAX_TEXT_CHARS)


def _path_title(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ")
    return " ".join(title.split()) or path.name


def _match_score(topic: str, text: str) -> float:
    lexical = relevance.token_overlap_relevance(topic, text)
    topic_entities = entity_extract.extract_text_entities(topic)
    text_entities = entity_extract.extract_text_entities(text)
    entity_score = entity_extract.entity_overlap(topic_entities, text_entities)
    return round(max(lexical, entity_score * 0.9), 4)


def _load_cache(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload.get("entries"), dict):
        payload["entries"] = {}
    payload["entries"] = _bounded_entries(payload["entries"])
    return payload


def _bounded_entries(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, dict):
        return {}
    ordered = sorted(
        (
            (path, value)
            for path, value in entries.items()
            if (
                isinstance(path, str)
                and isinstance(value, dict)
                and isinstance(value.get("text"), str)
            )
        ),
        key=lambda row: int(row[1].get("mtime_ns") or 0),
        reverse=True,
    )
    base_bytes = len(
        json.dumps(
            {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    used_bytes = base_bytes
    bounded: dict[str, Any] = {}
    for path, value in ordered[:MAX_CACHE_ENTRIES]:
        normalized = {
            "mtime_ns": value.get("mtime_ns"),
            "size": value.get("size"),
            "text": value["text"][:MAX_CACHE_TEXT_CHARS],
        }
        fragment = json.dumps({path: normalized}, ensure_ascii=False).encode("utf-8")
        fragment_bytes = len(fragment) - 2 + (2 if bounded else 0)
        if used_bytes + fragment_bytes > MAX_CACHE_BYTES:
            continue
        bounded[path] = normalized
        used_bytes += fragment_bytes
    return bounded


def _cache_entry_fragment_size(path: str, value: dict[str, Any]) -> int:
    return len(json.dumps({path: value}, ensure_ascii=False).encode("utf-8")) - 2


def _cache_entry_put(
    entries: dict[str, Any],
    sizes: dict[str, int],
    path: str,
    value: dict[str, Any],
) -> None:
    entries[path] = value
    sizes[path] = _cache_entry_fragment_size(path, value)
    while (
        len(entries) > MAX_CACHE_ENTRIES
        or _cache_payload_size(sizes) > MAX_CACHE_BYTES
    ):
        oldest = min(
            entries,
            key=lambda candidate: (
                int(entries[candidate].get("mtime_ns") or 0),
                candidate,
            ),
        )
        del entries[oldest]
        del sizes[oldest]


def _cache_payload_size(sizes: dict[str, int]) -> int:
    base_bytes = len(
        json.dumps(
            {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    separators = max(0, len(sizes) - 1) * 2
    return base_bytes + sum(sizes.values()) + separators


def _write_cache(path: Path | None, payload: dict[str, Any], notes: list[str]) -> None:
    if path is None:
        return
    try:
        _ensure_private_directory(path.parent)
        payload["entries"] = _bounded_entries(payload.get("entries", {}))
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            temporary.unlink()
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        notes.append(f"Corpus cache unavailable: {_safe_error(exc)}")


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        directory.chmod(0o700)
