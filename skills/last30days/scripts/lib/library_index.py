"""Offline FTS search across the saved research library and store sightings."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from . import library, private_files
from .private_files import prepare_private_sqlite_files

_IS_JUNCTION = getattr(os.path, "isjunction", None)


DEFAULT_LIBRARY_DB = library.DEFAULT_BRIEFS_DIR.parent / "library.db"
DEFAULT_STORE_DB = library.DEFAULT_BRIEFS_DIR.parent / "research.db"
INDEX_FINGERPRINT_VERSION = "last30days-library-index/v3"
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


def saved_report_index_path(save_dir: Path) -> Path:
    if save_dir == Path(library.DEFAULT_MEMORY_DIR).expanduser().resolve():
        return DEFAULT_LIBRARY_DB
    return save_dir / ".last30days-library.db"


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


@contextmanager
def _managed_connection(conn: sqlite3.Connection):
    """Commit or roll back ``conn`` and always close it on exit."""
    try:
        with conn as active:
            yield active
    finally:
        conn.close()


def fts5_available() -> bool:
    try:
        with _managed_connection(sqlite3.connect(":memory:")) as conn:
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


def _resolve_regular_source_path(path: Path | str) -> Path | None:
    """Strictly resolve an existing regular non-symlink source file path."""
    raw_source = Path(path).expanduser()
    if _IS_JUNCTION is not None and _IS_JUNCTION(raw_source):
        return None
    try:
        lstat_res = raw_source.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(lstat_res.st_mode) or not stat.S_ISREG(lstat_res.st_mode):
        return None
    try:
        canonical_parent = raw_source.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    canonical_source = canonical_parent / raw_source.name
    if _IS_JUNCTION is not None and _IS_JUNCTION(canonical_source):
        return None
    try:
        canonical_stat = canonical_source.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(canonical_stat.st_mode) or not stat.S_ISREG(canonical_stat.st_mode):
        return None
    if (os.name == "posix" or (lstat_res.st_ino and canonical_stat.st_ino)) and (
        canonical_stat.st_dev,
        canonical_stat.st_ino,
    ) != (lstat_res.st_dev, lstat_res.st_ino):
        return None
    return canonical_source


def index_entry(
    path: Path | str,
    *,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
    source_format: str = "markdown",
) -> bool:
    """Index one saved Markdown artifact in O(1) without scanning its directory."""
    if not fts5_available():
        raise LibrarySearchUnavailable(
            "library search requires a Python SQLite build with FTS5 support"
        )
    canonical_source = _resolve_regular_source_path(path)
    if canonical_source is None:
        return False
    try:
        entry = library.parse_markdown_file(canonical_source, source_format=source_format)
    except (OSError, UnicodeError, ValueError):
        return False
    if library.is_unbounded_private_brief(entry.content, source_format=entry.source_format):
        return False
    target = Path(db_path).expanduser()
    _ensure_private_directory(target.parent)
    with _managed_connection(_connect(target)) as conn:
        _upsert_entry(conn, entry)
        conn.commit()
    return True


def index_brief(
    path: Path | str,
    *,
    db_path: Path | str = DEFAULT_LIBRARY_DB,
) -> bool:
    """Index one saved artifact, parsing it through ``scan_library``."""
    source = _resolve_regular_source_path(path)
    if source is None:
        return False
    if source.suffix.lower() == ".json":
        entries, _ = library.scan_library(source.parent / ".missing", source.parent)
    else:
        entries, _ = library.scan_library(source.parent, source.parent / ".missing")
    entry = next((item for item in entries if item.source_path == source), None)
    if entry is None:
        return False
    target = Path(db_path).expanduser()
    _ensure_private_directory(target.parent)
    with _managed_connection(_connect(target)) as conn:
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
            with _managed_connection(_connect(target)) as conn:
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
    with _managed_connection(_connect(db_path)) as conn:
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
            if library.is_unbounded_private_brief(entry.content, source_format=entry.source_format):
                continue
            current_ids.add(entry.entry_id)
            mtime_ns, file_size = _entry_file_identity(entry)
            fingerprint = _fingerprint(_indexable_content(entry.content))
            if fts_trustworthy and existing.get(entry.entry_id) == (
                mtime_ns, file_size, fingerprint
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


def _regular_stat(path: Path) -> os.stat_result:
    if _IS_JUNCTION is not None and _IS_JUNCTION(path):
        raise OSError(errno.ELOOP, f"Refusing to follow junction: {path}")
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise OSError(f"Expected a regular library file: {path}")
    return st


def _entry_file_identity(entry: library.LibraryEntry) -> tuple[int, int]:
    if entry.source_mtime_ns is not None and entry.source_size is not None:
        return entry.source_mtime_ns, entry.source_size
    st = _regular_stat(entry.source_path)
    return st.st_mtime_ns, st.st_size


def _connect(path: Path) -> sqlite3.Connection:
    _ensure_private_directory(path.parent)
    safe_path = prepare_private_sqlite_files(path, create=True)
    conn = sqlite3.connect(str(safe_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


def _upsert_entry(
    conn: sqlite3.Connection,
    entry: library.LibraryEntry,
    *,
    fingerprint: str | None = None,
) -> None:
    mtime_ns, file_size = _entry_file_identity(entry)
    private_free_content = library.public_markdown(entry.content)
    indexed_content = _without_library_context(private_free_content)
    headline = entry.headline
    summary = entry.summary
    if private_free_content != entry.content and entry.source_format in {"markdown", "finished-brief"}:
        headline = library._markdown_headline(private_free_content) or library._brief_prose_headline(private_free_content) or entry.topic
        summary = library._markdown_summary(private_free_content) or library._brief_prose_summary(private_free_content) or headline
    content_hash = fingerprint or _fingerprint(indexed_content)
    if (
        entry.source_mtime_ns is not None
        and entry.source_path.is_absolute()
        and ".." not in entry.source_path.parts
    ):
        source_path = str(entry.source_path)
    else:
        try:
            source_path = str(entry.source_path.resolve(strict=True))
        except (OSError, RuntimeError):
            source_path = str(entry.source_path)
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
            mtime_ns,
            file_size,
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
        path = prepare_private_sqlite_files(store_db_path)
        # Standard SQLite builds reject a UNC server name in a URI authority.
        use_uri = os.name == "posix"
        database = f"{path.as_uri()}?mode=ro" if use_uri else str(path)
        with _managed_connection(sqlite3.connect(database, uri=use_uri)) as conn:
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
                   WHERE findings_fts MATCH ? AND rr.status IN ('completed', 'degraded')
                         AND fs.source != 'corpus' AND f.dismissed = 0
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
    return _without_library_context(library.public_markdown(content))


def _without_library_context(content: str) -> str:
    without_marked = _MARKED_LIBRARY_CONTEXT.sub("", content)
    return _LEGACY_LIBRARY_CONTEXT.sub("", without_marked)


def _harden_private_directory_fd(directory: Path) -> None:
    """Verify ``directory`` is an owned real directory and fchmod it to 0o700."""
    if _IS_JUNCTION is not None and _IS_JUNCTION(directory):
        raise OSError(errno.ELOOP, f"Refusing to follow junction directory: {directory}")
    if os.name != "posix":
        st = directory.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"Expected a real private directory: {directory}")
        return
    flags = os.O_RDONLY
    for attr in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
        flags |= getattr(os, attr, 0)
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise OSError(
                errno.ELOOP, f"Refusing to follow symlink directory: {directory}"
            ) from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(f"Expected a real private directory: {directory}")
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and st.st_uid != geteuid():
            raise OSError(f"Refusing to use private directory owned by another user: {directory}")
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _ensure_private_directory(path: Path) -> Path:
    target = Path(path).expanduser()
    abs_target = target if target.is_absolute() else Path(os.getcwd()) / target
    missing_names: list[str] = []
    current = abs_target
    while True:
        if _IS_JUNCTION is not None and _IS_JUNCTION(current):
            raise OSError(errno.ELOOP, f"Refusing to follow junction directory: {current}")
        try:
            current.lstat()
            break
        except FileNotFoundError:
            if current == current.parent:
                raise
            if current.name in ("", ".", ".."):
                raise OSError(f"Invalid private directory component: {current}")
            missing_names.append(current.name)
            current = current.parent

    # Validate the deepest existing ancestor chain BEFORE creating anything.
    if os.name == "posix":
        verified = private_files._resolve_trusted_parent_dir(current)
    else:
        st = current.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"Expected a directory for private file: {current}")
        verified = current.resolve(strict=True)

    for name in reversed(missing_names):
        verified = verified / name
        if _IS_JUNCTION is not None and _IS_JUNCTION(verified):
            raise OSError(errno.ELOOP, f"Refusing to follow junction directory: {verified}")
        try:
            os.mkdir(verified, 0o700)
        except FileExistsError:
            pass
        _harden_private_directory_fd(verified)
    return verified


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
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        try:
            st = candidate.lstat()
            if not stat.S_ISREG(st.st_mode):
                continue
            candidate.unlink()
        except FileNotFoundError:
            pass
