"""Scan saved last30days research artifacts into a deterministic library."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


DEFAULT_MEMORY_DIR = Path.home() / "Documents" / "Last30Days"
DEFAULT_BRIEFS_DIR = Path.home() / ".local" / "share" / "last30days" / "briefs"
LIBRARY_ID_FILENAME = ".last30days-library-id"

_REPORT_TITLE = re.compile(r"^#\s+last30days(?:\s+v[^:]+)?:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_FIRST_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_DATE_RANGE = re.compile(
    r"^-\s*Date range:\s*\d{4}-\d{2}-\d{2}\s+to\s+(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_DATED_FILENAME = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:-\d+)?$")
_RANKED_HEADLINE = re.compile(r"^###\s+1[.)]\s+(.+?)\s*$", re.MULTILINE)
_SCORE_SUFFIX = re.compile(r"\s+\(score\s+[^)]*\)\s*$", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^]]+)]\([^)]+\)")
_LIBRARY_ID = re.compile(r"[0-9a-f]{32}")
_GENERATED_BRIEF_NAME = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{8}-\d{4}-\d{2}-\d{2}\.html"
)
_PRIVATE_CORPUS_BLOCK = re.compile(
    r"<!-- LAST30DAYS_PRIVATE_CORPUS_START -->.*?"
    r"<!-- LAST30DAYS_PRIVATE_CORPUS_END -->\s*",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """Metadata and source content for one saved research artifact."""

    slug: str
    topic: str
    published_date: date
    headline: str
    summary: str
    source_path: Path
    content: str
    source_updated_at: datetime
    source_format: str = "markdown"

    @property
    def entry_id(self) -> str:
        return f"urn:last30days:{self.slug}:{self.identity_hash}:{self.published_date.isoformat()}"

    @property
    def output_name(self) -> str:
        return f"{self.slug}-{self.identity_hash}-{self.published_date.isoformat()}.html"

    @property
    def identity_hash(self) -> str:
        # Include the source filename stem so per-suffix runs of the same
        # topic on the same date (--save-suffix per-client workflow) stay
        # distinct entries instead of collapsing to one.
        seed = f"{self.topic}\n{self.source_path.stem}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "last30days"


def get_or_create_library_id(memory_dir: Path | str) -> str:
    """Return the persisted random namespace for one research library."""
    memory_path = Path(memory_dir).expanduser()
    memory_path.mkdir(parents=True, exist_ok=True)
    id_path = memory_path / LIBRARY_ID_FILENAME
    try:
        library_id = id_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        library_id = uuid.uuid4().hex
        try:
            with id_path.open("x", encoding="utf-8") as handle:
                handle.write(f"{library_id}\n")
        except FileExistsError:
            library_id = id_path.read_text(encoding="utf-8").strip()
    if not _LIBRARY_ID.fullmatch(library_id):
        raise ValueError(f"invalid library ID in {id_path}")
    return library_id


def is_generated_brief_name(name: str) -> bool:
    """Return whether a filename has the exact library-renderer output shape."""
    return _GENERATED_BRIEF_NAME.fullmatch(name) is not None


def scan_library(
    memory_dir: Path | str = DEFAULT_MEMORY_DIR,
    briefs_dir: Path | str = DEFAULT_BRIEFS_DIR,
) -> tuple[list[LibraryEntry], list[str]]:
    """Return valid saved entries and notes for files that could not be read.

    Hand-edited and foreign files are tolerated: a generic Markdown heading is
    enough to include a file, while unreadable or unrecognizable files are
    skipped with a note instead of aborting the entire feed generation.
    """
    entries: dict[str, LibraryEntry] = {}
    notes: list[str] = []
    memory_path = Path(memory_dir).expanduser()
    briefs_path = Path(briefs_dir).expanduser()

    if memory_path.is_dir():
        for path in sorted(memory_path.glob("*.md")):
            try:
                entry = _parse_markdown(path)
                _keep_preferred(entries, entry)
            except (OSError, UnicodeError, ValueError) as exc:
                notes.append(f"Skipped {path}: {exc}")
                continue

    if briefs_path.is_dir():
        for path in sorted(briefs_path.glob("*.json")):
            try:
                entry = _parse_briefing(path)
                _keep_preferred(entries, entry)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                notes.append(f"Skipped {path}: {exc}")
                continue

    ordered = sorted(
        entries.values(),
        key=lambda entry: (entry.published_date, entry.topic.casefold(), entry.source_path.name),
        reverse=True,
    )
    return ordered, notes


def _keep_preferred(entries: dict[str, LibraryEntry], entry: LibraryEntry) -> None:
    existing = entries.get(entry.entry_id)
    if existing is None or entry.source_updated_at > existing.source_updated_at:
        entries[entry.entry_id] = entry


def _parse_markdown(path: Path) -> LibraryEntry:
    content = path.read_text(encoding="utf-8")
    public_content = _PRIVATE_CORPUS_BLOCK.sub("", content)
    title_match = _REPORT_TITLE.search(public_content) or _FIRST_TITLE.search(public_content)
    if not title_match:
        raise ValueError("no Markdown title found")
    topic = _clean_inline(title_match.group(1))
    if not topic:
        raise ValueError("empty Markdown title")
    published_date = _markdown_date(public_content, path)
    headline = _markdown_headline(public_content) or topic
    summary = _markdown_summary(public_content) or headline
    return LibraryEntry(
        slug=slugify(topic),
        topic=topic,
        published_date=published_date,
        headline=headline,
        summary=summary,
        source_path=path,
        content=content,
        source_updated_at=_source_updated_at(path),
    )


def _markdown_date(content: str, path: Path) -> date:
    if match := _DATE_RANGE.search(content):
        return date.fromisoformat(match.group(1))
    if match := _DATED_FILENAME.search(path.stem):
        return date.fromisoformat(match.group(1))
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def _markdown_headline(content: str) -> str:
    if match := _RANKED_HEADLINE.search(content):
        return _clean_inline(_SCORE_SUFFIX.sub("", match.group(1)))
    return ""


def _markdown_summary(content: str) -> str:
    learned = re.search(
        r"^##\s+What I learned\s*$\n+(.+?)(?=\n#{1,3}\s|\n---|\Z)",
        content,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if learned:
        for paragraph in re.split(r"\n\s*\n", learned.group(1)):
            cleaned = _clean_inline(paragraph)
            if cleaned:
                return cleaned[:500]
    evidence = re.search(r"^\s*-\s*Evidence:\s*(.+?)\s*$", content, re.MULTILINE | re.IGNORECASE)
    if evidence:
        return _clean_inline(evidence.group(1))[:500]
    return ""


def _parse_briefing(path: Path) -> LibraryEntry:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("briefing JSON is not an object")
    is_weekly = data.get("type") == "weekly" or path.stem.endswith("-weekly")
    raw_date = path.stem[:10] if is_weekly else data.get("date") or path.stem[:10]
    try:
        published_date = date.fromisoformat(str(raw_date))
    except ValueError as exc:
        raise ValueError("briefing has no valid date") from exc
    topic = "Weekly research briefing" if is_weekly else "Daily research briefing"
    top = data.get("top_finding") if isinstance(data.get("top_finding"), dict) else {}
    headline = str(top.get("title") or topic)
    summary = _briefing_summary(data, headline)
    markdown = _briefing_markdown(data, topic, published_date, summary)
    return LibraryEntry(
        slug=slugify(topic),
        topic=topic,
        published_date=published_date,
        headline=headline,
        summary=summary,
        source_path=path,
        content=markdown,
        source_updated_at=_source_updated_at(path),
        source_format="json",
    )


def _source_updated_at(path: Path) -> datetime:
    seconds, nanoseconds = divmod(path.stat().st_mtime_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=nanoseconds // 1_000
    )


def _briefing_summary(data: dict[str, object], fallback: str) -> str:
    total_new = data.get("total_new")
    total_topics = data.get("total_topics")
    if total_new is not None and total_topics is not None:
        return f"{total_new} new findings across {total_topics} monitored topics. {fallback}"[:500]
    topics = data.get("topics")
    if isinstance(topics, list):
        return f"Updates across {len(topics)} monitored topics. {fallback}"[:500]
    return fallback[:500]


def _briefing_markdown(data: dict[str, object], topic: str, published_date: date, summary: str) -> str:
    lines = [f"# {topic}", "", f"- Date: {published_date.isoformat()}", "", summary]
    if data.get("type") == "weekly" and data.get("week_of"):
        lines[3:3] = [f"- Week of: {data['week_of']}"]
    topics = data.get("topics")
    if isinstance(topics, list):
        lines.extend(["", "## Topics", ""])
        for item in topics:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Untitled topic")
            count = item.get("new_count", item.get("this_week_count", 0))
            lines.append(f"- **{name}** — {count} new findings")
    return "\n".join(lines).strip() + "\n"


def _clean_inline(value: str) -> str:
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = re.sub(r"^\s*>\s?", "", value)
    value = re.sub(r"(?<!\w)(\*\*|__)(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", value)
    value = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", value)
    value = re.sub(r"(?<!\w)`(?=\S)(.+?)(?<=\S)`(?!\w)", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()
