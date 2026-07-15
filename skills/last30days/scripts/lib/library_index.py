"""Offline FTS search across the saved research library and store sightings."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from . import library


DEFAULT_LIBRARY_DB = library.DEFAULT_BRIEFS_DIR.parent / "library.db"
DEFAULT_STORE_DB = library.DEFAULT_BRIEFS_DIR.parent / "research.db"
INDEX_FINGERPRINT_VERSION = "last30days-library-index/v2"
LIBRARY_CONTEXT_START = "<!-- last30days:library-context:start -->"
LIBRARY_CONTEXT_END = "<!-- last30days:library-context:end -->"
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MARKED_LIBRARY_CONTEXT = re.compile(
    rf"^{re.escape(LIBRARY_CONTEXT_START)}\s*$.*?"
    rf"^{re.escape(LIBRARY_CONTEXT_END)}\s*$\n?",
    re.MULTILINE | re.DOTALL,
)
_LEGACY_LIBRARY_CONTEXT = re.compile(
    r"^## From your library\s*$.*?(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_PRIVATE_CORPUS_BLOCK = re.compile(
    r"<!-- LAST30DAYS_PRIVATE_CORPUS_START -->.*?"
    r"<!-- LAST30DAYS_PRIVATE_CORPUS_END -->\s*",
    re.DOTALL,
)


class LibrarySearchUnavailable(RuntimeError):
    """Raised when this Python SQLite build cannot provide FTS5."""


@dataclass(frozen=True, slots=True)
class LibrarySearchMatch:
    topic: str
    published_date: date
    headline: str
    snippet: str
    source_kind: str
    rank: float
    source_path: str = ""
    url: str = ""
    engagement: float | None = None

    @property
    def run_key(self) -> tuple[str, date]:
        return self.topic, self.published_date


@dataclass(frozen=True, slots=True)
class SyncResult:
    indexed: int = 0
    removed: int = 0
    unchanged: int = 0
    notes: tuple[str, ...] = ()
    rebuilt: bool = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_documents (
    entry_id TEXT PRIMARY KEY,
    source_path TEXT UNIQUE NOT NULL,
    source_mtime_ns INTEGER NOT NULL,
    source_size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    topic TEXT NOT NULL,
    published_date TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_format TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5(
    entry_id UNINDEXED,
    topic,
    headline,
    summary,
    content,
    tokenize='porter unicode61'
);
"""


def fts5_available() -> bool:
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
    except sqlite3.DatabaseError:
        return False
    return True


def sync_library(
    memory_dir: Path | str = library.DEFAULT_MEMORY_DIR,
    briefs_dir: Path | str = library.DEFAULT_BRIEFS_DIR,
    *,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
) -> SyncResult:
    """Incrementally index the shared ``scan_library`` view of saved research."""
    if not fts5_available():
        raise LibrarySearchUnavailable(
            "library search requires a Python SQLite build with FTS5 support"
        )
    target = Path(db_path).expanduser()
    try:
        return _sync_library(memory_dir, briefs_dir, target)
    except sqlite3.DatabaseError as exc:
        if "fts5" in str(exc).lower() and "malformed" not in str(exc).lower():
            raise LibrarySearchUnavailable(
                "library search requires a Python SQLite build with FTS5 support"
            ) from exc
        if not _is_confirmed_corruption(exc):
            raise
        _remove_database(target)
        return replace(_sync_library(memory_dir, briefs_dir, target), rebuilt=True)


def index_brief(
    path: Path | str,
    *,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
) -> bool:
    """Index one saved artifact, parsing it through ``scan_library``."""
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() == ".json":
        entries, _ = library.scan_library(source.parent / ".missing", source.parent)
    else:
        entries, _ = library.scan_library(source.parent, source.parent / ".missing")
    entry = next((item for item in entries if item.source_path.resolve() == source), None)
    if entry is None:
        return False
    target = Path(db_path).expanduser()
    _ensure_private_directory(target.parent)
    with _connect(target) as conn:
        _upsert_entry(conn, entry)
        conn.commit()
    return True


def search(
    query: str,
    *,
    limit: int = 20,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
    store_db_path: Path | str = DEFAULT_STORE_DB,
) -> list[LibrarySearchMatch]:
    """Search indexed briefs plus dated per-run findings from the research store."""
    expression = _fts_expression(query)
    if not expression or limit <= 0:
        return []
    target = Path(db_path).expanduser()
    brief_matches: list[LibrarySearchMatch] = []
    if target.is_file():
        try:
            with _connect(target) as conn:
                rows = conn.execute(
                    """SELECT d.topic, d.published_date, d.headline,
                              snippet(library_fts, 4, '', '', ' … ', 36) AS snippet,
                              d.source_path, bm25(library_fts) AS rank
                       FROM library_fts
                       JOIN library_documents d ON d.entry_id = library_fts.entry_id
                       WHERE library_fts MATCH ?
                       ORDER BY rank, d.published_date DESC
                       LIMIT ?""",
                    (expression, limit),
                ).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        brief_matches = [
            LibrarySearchMatch(
                topic=str(row["topic"]),
                published_date=date.fromisoformat(str(row["published_date"])),
                headline=str(row["headline"]),
                snippet=_clean_snippet(row["snippet"]),
                source_kind="brief",
                rank=float(row["rank"]),
                source_path=str(row["source_path"]),
            )
            for row in rows
        ]
    store_matches = _search_store_sightings(
        expression, Path(store_db_path).expanduser(), limit
    )
    return _merge_ranked_matches([brief_matches, store_matches], limit=limit)


def sync_and_search(
    query: str,
    *,
    memory_dir: Path | str = library.DEFAULT_MEMORY_DIR,
    briefs_dir: Path | str = library.DEFAULT_BRIEFS_DIR,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
    store_db_path: Path | str = DEFAULT_STORE_DB,
    limit: int = 20,
) -> tuple[list[LibrarySearchMatch], SyncResult]:
    synced = sync_library(memory_dir, briefs_dir, db_path=db_path)
    return search(
        query,
        limit=limit,
        db_path=db_path,
        store_db_path=store_db_path,
    ), synced


def _sync_library(
    memory_dir: Path | str,
    briefs_dir: Path | str,
    db_path: Path,
) -> SyncResult:
    entries, notes = library.scan_library(memory_dir, briefs_dir)
    _ensure_private_directory(db_path.parent)
    indexed = unchanged = 0
    with _connect(db_path) as conn:
        existing = {
            row["entry_id"]: (row["source_mtime_ns"], row["source_size"], row["content_hash"])
            for row in conn.execute(
                "SELECT entry_id, source_mtime_ns, source_size, content_hash FROM library_documents"
            )
        }
        current_ids: set[str] = set()
        # If the FTS table was lost or recreated empty while library_documents
        # survived, the fingerprint check alone would mark everything unchanged
        # and searches would silently return nothing. Verify row counts agree
        # before trusting fingerprints.
        fts_rows = conn.execute("SELECT count(*) FROM library_fts").fetchone()[0]
        fts_trustworthy = fts_rows >= len(existing) if existing else True
        for entry in entries:
            current_ids.add(entry.entry_id)
            stat = entry.source_path.stat()
            fingerprint = _fingerprint(_indexable_content(entry.content))
            if fts_trustworthy and existing.get(entry.entry_id) == (
                stat.st_mtime_ns, stat.st_size, fingerprint
            ):
                unchanged += 1
                continue
            _upsert_entry(conn, entry, fingerprint=fingerprint)
            indexed += 1
        stale_ids = set(existing) - current_ids
        for entry_id in stale_ids:
            conn.execute("DELETE FROM library_fts WHERE entry_id = ?", (entry_id,))
            conn.execute("DELETE FROM library_documents WHERE entry_id = ?", (entry_id,))
        conn.commit()
    return SyncResult(
        indexed=indexed,
        removed=len(stale_ids),
        unchanged=unchanged,
        notes=tuple(notes),
    )


def _connect(path: Path) -> sqlite3.Connection:
    _ensure_private_directory(path.parent)
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    path.chmod(0o600)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
    except Exception:
        conn.close()
        raise
    return conn


def _upsert_entry(
    conn: sqlite3.Connection,
    entry: library.LibraryEntry,
    *,
    fingerprint: str | None = None,
) -> None:
    stat = entry.source_path.stat()
    private_free_content = _PRIVATE_CORPUS_BLOCK.sub("", entry.content)
    indexed_content = _indexable_content(private_free_content)
    headline = entry.headline
    summary = entry.summary
    if private_free_content != entry.content and entry.source_format == "markdown":
        headline = library._markdown_headline(private_free_content) or entry.topic
        summary = library._markdown_summary(private_free_content) or headline
    content_hash = fingerprint or _fingerprint(indexed_content)
    source_path = str(entry.source_path.resolve())
    replaced = conn.execute(
        "SELECT entry_id FROM library_documents WHERE source_path = ? AND entry_id != ?",
        (source_path, entry.entry_id),
    ).fetchall()
    for row in replaced:
        conn.execute("DELETE FROM library_fts WHERE entry_id = ?", (row["entry_id"],))
        conn.execute("DELETE FROM library_documents WHERE entry_id = ?", (row["entry_id"],))
    conn.execute("DELETE FROM library_fts WHERE entry_id = ?", (entry.entry_id,))
    conn.execute(
        """INSERT INTO library_documents
               (entry_id, source_path, source_mtime_ns, source_size, content_hash,
                topic, published_date, headline, summary, source_format)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(entry_id) DO UPDATE SET
               source_path=excluded.source_path,
               source_mtime_ns=excluded.source_mtime_ns,
               source_size=excluded.source_size,
               content_hash=excluded.content_hash,
               topic=excluded.topic,
               published_date=excluded.published_date,
               headline=excluded.headline,
               summary=excluded.summary,
               source_format=excluded.source_format""",
        (
            entry.entry_id,
            source_path,
            stat.st_mtime_ns,
            stat.st_size,
            content_hash,
            entry.topic,
            entry.published_date.isoformat(),
            headline,
            summary,
            entry.source_format,
        ),
    )
    conn.execute(
        "INSERT INTO library_fts(entry_id, topic, headline, summary, content) VALUES (?, ?, ?, ?, ?)",
        (entry.entry_id, entry.topic, headline, summary, indexed_content),
    )


def _search_store_sightings(
    expression: str,
    store_db_path: Path,
    limit: int,
) -> list[LibrarySearchMatch]:
    if not store_db_path.is_file():
        return []
    try:
        with sqlite3.connect(str(store_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT t.name AS topic, rr.run_date,
                          COALESCE(fs.source_title, f.source_title, f.summary) AS headline,
                          snippet(findings_fts, 0, '', '', ' … ', 30) AS snippet,
                          fs.source_url, fs.engagement_score, bm25(findings_fts) AS rank
                   FROM findings_fts
                   JOIN findings f ON f.id = findings_fts.rowid
                   JOIN finding_sightings fs ON fs.finding_id = f.id
                   JOIN research_runs rr ON rr.id = fs.run_id
                   JOIN topics t ON t.id = fs.topic_id
                   WHERE findings_fts MATCH ? AND rr.status = 'completed'
                         AND fs.source != 'corpus'
                   ORDER BY rank, rr.run_date DESC
                   LIMIT ?""",
                (expression, limit),
            ).fetchall()
    except (sqlite3.DatabaseError, OSError):
        return []
    matches: list[LibrarySearchMatch] = []
    for row in rows:
        try:
            published = date.fromisoformat(str(row["run_date"])[:10])
        except ValueError:
            continue
        matches.append(
            LibrarySearchMatch(
                topic=str(row["topic"]),
                published_date=published,
                headline=str(row["headline"] or "Saved finding"),
                snippet=_clean_snippet(row["snippet"]),
                source_kind="store",
                rank=float(row["rank"]),
                url=str(row["source_url"] or ""),
                engagement=(
                    float(row["engagement_score"])
                    if row["engagement_score"] is not None
                    else None
                ),
            )
        )
    return matches


def _fts_expression(query: str) -> str:
    tokens = _TOKEN.findall(query)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _fingerprint(content: str) -> str:
    payload = f"{INDEX_FINGERPRINT_VERSION}\0{content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean_snippet(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:500]


def _indexable_content(content: str) -> str:
    without_private = _PRIVATE_CORPUS_BLOCK.sub("", content)
    without_marked = _MARKED_LIBRARY_CONTEXT.sub("", without_private)
    return _LEGACY_LIBRARY_CONTEXT.sub("", without_marked)


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        directory.chmod(0o700)


def _is_confirmed_corruption(exc: sqlite3.DatabaseError) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "file is not a database",
            "database disk image is malformed",
            "database schema is corrupt",
            "malformed database schema",
        )
    )


def _merge_ranked_matches(
    corpora: list[list[LibrarySearchMatch]],
    *,
    limit: int,
) -> list[LibrarySearchMatch]:
    normalized: list[LibrarySearchMatch] = []
    for matches in corpora:
        for position, match in enumerate(matches, start=1):
            normalized.append(replace(match, rank=-(1.0 / (60 + position))))
    combined = _dedupe_matches(normalized)
    return sorted(
        combined,
        key=lambda match: (
            match.rank,
            -match.published_date.toordinal(),
            match.topic.casefold(),
            match.headline.casefold(),
        ),
    )[:limit]


def _dedupe_matches(matches: list[LibrarySearchMatch]) -> list[LibrarySearchMatch]:
    seen: set[tuple[str, date, str, str]] = set()
    kept: list[LibrarySearchMatch] = []
    for match in matches:
        key = (
            match.topic.casefold(),
            match.published_date,
            match.headline.casefold(),
            match.source_kind,
        )
        if key not in seen:
            seen.add(key)
            kept.append(match)
    return kept


def _remove_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
