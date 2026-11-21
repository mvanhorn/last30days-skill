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
PUBLIC_DRILL_MARKER = "<!-- LAST30DAYS_PUBLIC_DRILL -->"
PUBLIC_FRESHNESS_MARKER = "<!-- LAST30DAYS_PUBLIC_FRESHNESS -->"
PUBLIC_CLUSTERS_MARKER = "<!-- LAST30DAYS_PUBLIC_CLUSTERS -->"
PRIVATE_CORPUS_START = "<!-- LAST30DAYS_PRIVATE_CORPUS_START -->"
PRIVATE_CORPUS_END = "<!-- LAST30DAYS_PRIVATE_CORPUS_END -->"

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
    re.escape(PRIVATE_CORPUS_START) + r".*?" + re.escape(PRIVATE_CORPUS_END) + r"\s*",
    re.DOTALL,
)
_DRILL_HEADING = re.compile(r"^## Drill Follow-up[ \t]*\r?$", re.MULTILINE)
_DRILL_TRAILER = re.compile(
    r"^### Deeper[ \t]*\r?\n[ \t]*\r?\n- [0-9]+ new items after dedupe\r?\n"
    r"- Re-researched sources:[^\r\n]*(?:\r?\n|\Z)",
    re.MULTILINE,
)
_PRIVATE_FRESHNESS_ROW = re.compile(
    r"^\| \*\*contradicted\*\* \| [^\r\n]*"
    r"(?:\]\(corpus://| \(Newer corpus item disagrees\))[^\r\n]*(?:\r?\n|\Z)",
    re.MULTILINE,
)
_LEGACY_CORPUS_SIGNAL = re.compile(
    r"^## From your files[ \t]*\r?$|^- (?:Sources|Re-researched sources):[^\r\n]*\bYour files\b",
    re.MULTILINE,
)
_FROM_YOUR_FILES_SECTION = re.compile(
    r"^##[ \t]+From your files[ \t]*\r?\n.*?(?=^#{1,2}[ \t]|^```text[ \t]*\r?$|^---[ \t]*\r?$|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_FRESHNESS_TABLE = re.compile(
    r"^## Freshness Verification[ \t]*\r?\n[ \t]*\r?\n"
    r"\| Verdict \| Claim \| Evidence \| Checked \|\r?\n"
    r"\| --- \| --- \| --- \| --- \|(?:\r?\n|\Z)"
    r".*?(?=^#{1,2}[ \t]|^```text[ \t]*\r?$|^---[ \t]*\r?$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_RANKED_CLUSTERS = re.compile(
    r"^## Ranked Evidence Clusters[ \t]*\r?\n.*?(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)
_RANKED_CLUSTER = re.compile(
    r"^(?P<prefix>### [0-9]+\. )(?P<title>[^\r\n]*)(?P<ending>\r?\n|\Z)"
    r"(?P<body>.*?)(?=^### [0-9]+\. |\Z)",
    re.MULTILINE | re.DOTALL,
)
_RANKED_CLUSTER_STATS = re.compile(r" \(score -?[0-9]+, [0-9]+ items?, sources: [^()\r\n]*\)$")
_RANKED_REPRESENTATIVE = re.compile(
    r"^[0-9]+\. \[(?P<sources>[^\]\r\n]+)\] (?P<title>[^\r\n]+)",
    re.MULTILINE,
)
_RANKED_FRESHNESS_BADGE = re.compile(r" \[freshness:[^\]]+\]$")
_BRIEF_CLUSTERS = re.compile(
    r"^(?P<section>## (?P<name>Ranked Storylines|Topic Tensions|Source Clusters)[ \t]*\r?\n)"
    r"(?P<body>.*?)(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)
_BRIEF_STORYLINE_TITLE = re.compile(r"^### [0-9]+\. [^\r\n]*(?:\r?\n|\Z)", re.MULTILINE)
_BRIEF_CLUSTER_LABEL = re.compile(r"^- \*\*[^\r\n]*(?:\r?\n|\Z)", re.MULTILINE)


def _public_legacy_cluster(match: re.Match[str]) -> str:
    body = match["body"]
    for representative in _RANKED_REPRESENTATIVE.finditer(body):
        sources = {source.strip().lower() for source in representative["sources"].split(",")}
        if {"corpus", "your files"} & sources:
            continue
        title = _RANKED_FRESHNESS_BADGE.sub("", representative["title"]).strip()
        if title:
            stats = _RANKED_CLUSTER_STATS.search(match["title"])
            return match["prefix"] + title + (stats.group() if stats else "") + match["ending"] + body
    return body


def _public_section_marked(match: re.Match[str], marker: str) -> bool:
    return match.string.endswith((marker + "\n", marker + "\r\n"), 0, match.start())


def _public_brief_clusters(match: re.Match[str]) -> str:
    if _public_section_marked(match, PUBLIC_CLUSTERS_MARKER):
        return match.group()
    labels = _BRIEF_STORYLINE_TITLE if match["name"] == "Ranked Storylines" else _BRIEF_CLUSTER_LABEL
    return match["section"] + labels.sub("", match["body"])


def public_markdown(markdown: str) -> str:
    corpus_document = (
        PRIVATE_CORPUS_START in markdown
        or _LEGACY_CORPUS_SIGNAL.search(markdown)
        or _PRIVATE_FRESHNESS_ROW.search(markdown)
    )
    marker_lines = tuple(
        marker + ending
        for marker in (PUBLIC_DRILL_MARKER, PRIVATE_CORPUS_START)
        for ending in ("\n", "\r\n")
    )
    for heading in _DRILL_HEADING.finditer(markdown):
        preceding = markdown[:heading.start()]
        if preceding.endswith(marker_lines):
            continue
        # Legacy drill summaries did not identify their sources. Use the
        # last generated trailer so a heading inside the excerpt cannot end it.
        end = len(markdown)
        for trailer in _DRILL_TRAILER.finditer(markdown, heading.end()):
            end = trailer.end()
        markdown = preceding + markdown[end:]
        break
    markdown = _PRIVATE_CORPUS_BLOCK.sub("", markdown)
    markdown = _FROM_YOUR_FILES_SECTION.sub("", markdown)
    if corpus_document:
        # The old renderer could keep a drill's local-derived cluster title
        # after dedupe left only public representatives.
        markdown = _RANKED_CLUSTERS.sub(
            lambda match: match.group()
            if _public_section_marked(match, PUBLIC_CLUSTERS_MARKER)
            else _RANKED_CLUSTER.sub(_public_legacy_cluster, match.group()),
            markdown,
        )
        if markdown.startswith("# Production Brief: "):
            markdown = _BRIEF_CLUSTERS.sub(_public_brief_clusters, markdown)
        # Old rendered rows do not identify the claim's source, so a row
        # with a public evidence link can still represent a private claim.
        markdown = _FRESHNESS_TABLE.sub(
            lambda match: match.group() if _public_section_marked(match, PUBLIC_FRESHNESS_MARKER) else "",
            markdown,
        )
    return _PRIVATE_FRESHNESS_ROW.sub("", markdown)


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
    public_content = public_markdown(content)
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
    headline = _briefing_public_headline(data, topic)
    summary = _briefing_summary(data, headline)
    markdown = _briefing_markdown(data, topic, published_date, summary)
    local_title = str(top.get("title") or "")
    if local_title and local_title != headline:
        local_title = local_title.replace("LAST30DAYS_PRIVATE_CORPUS", "LAST30DAYS_PRIVATE-CORPUS")
        markdown += (
            "\n<!-- LAST30DAYS_PRIVATE_CORPUS_START -->\n"
            "## Local briefing highlight\n\n"
            f"{local_title}\n"
            "<!-- LAST30DAYS_PRIVATE_CORPUS_END -->\n"
        )
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


def _briefing_public_headline(data: dict[str, object], fallback: str) -> str:
    top = data.get("top_finding")
    candidates = [top] if isinstance(top, dict) else []
    # Legacy daily archives lack source on top_finding but retain it on findings.
    topics = data.get("topics")
    if isinstance(topics, list):
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            for field in ("findings", "top_findings"):
                findings = topic.get(field)
                if isinstance(findings, list):
                    candidates.extend(findings)

    public_titles = []
    for finding in candidates:
        if not isinstance(finding, dict):
            continue
        source = str(finding.get("source") or "").strip().lower()
        url = str(finding.get("source_url") or "").strip().lower()
        if not source or source == "corpus" or url.startswith("corpus:"):
            continue
        title = finding.get("source_title") or finding.get("title")
        if title:
            engagement = finding.get("engagement_score", finding.get("engagement"))
            score = engagement if isinstance(engagement, (int, float)) else 0
            public_titles.append((score, str(title)))
    return max(public_titles, key=lambda item: item[0])[1] if public_titles else fallback


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
