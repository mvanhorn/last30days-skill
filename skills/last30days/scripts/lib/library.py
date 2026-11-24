"""Scan saved last30days research artifacts into a deterministic library."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]


DEFAULT_MEMORY_DIR = Path.home() / "Documents" / "Last30Days"
DEFAULT_BRIEFS_DIR = Path.home() / ".local" / "share" / "last30days" / "briefs"
FINISHED_SUBDIR = "finished"
LIBRARY_ID_FILENAME = ".last30days-library-id"
MAX_LIBRARY_FILE_BYTES = 10 * 1024 * 1024
_IS_JUNCTION = getattr(os.path, "isjunction", None)
_SUPPORTS_DIR_FD = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and os.open in getattr(os, "supports_dir_fd", set())
    and os.stat in getattr(os, "supports_dir_fd", set())
)
_SUPPORTS_LISTDIR_FD = os.listdir in getattr(os, "supports_fd", set())
_DARWIN_F_GETPATH = 50
_DARWIN_MAXPATHLEN = 1024
PUBLIC_DRILL_MARKER = "<!-- LAST30DAYS_PUBLIC_DRILL -->"
PUBLIC_FRESHNESS_MARKER = "<!-- LAST30DAYS_PUBLIC_FRESHNESS -->"
PUBLIC_CLUSTERS_MARKER = "<!-- LAST30DAYS_PUBLIC_CLUSTERS -->"
PRIVATE_CORPUS_START = "<!-- LAST30DAYS_PRIVATE_CORPUS_START -->"
PRIVATE_CORPUS_END = "<!-- LAST30DAYS_PRIVATE_CORPUS_END -->"

_REPORT_TITLE = re.compile(r"^#\s+last30days(?:\s+v[^:]+)?:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_FIRST_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_COMPARISON_TITLE_SUFFIX = re.compile(r":\s*What the Community Says\s*\(/Last30Days\)\s*$", re.IGNORECASE)
_TOPIC_META_COMMENT = re.compile(r"^<!--\s*last30days-topic:\s*(.+?)\s*-->$", re.MULTILINE)
_BADGE_LINE = re.compile(r"^🌐\s*last30days\b[^\r\n]*$", re.MULTILINE)
_BADGE_SYNCED_DATE = re.compile(r"\bsynced\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_BADGE_VERSION = re.compile(r"^v\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9._-]+)?$")
_BADGE_MONTH_DAY_YEAR = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})\b",
    re.IGNORECASE,
)
_BADGE_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_BADGE_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_UNSUFFIXED_RAW_STEM = re.compile(r"^(.+)-raw(?:-\d{4}-\d{2}-\d{2}(?:-\d+)?)?$")
_PROSE_LABEL_LINE = re.compile(
    r"^(?:\*\*|__)?(?:what i learned|key patterns(?:\s+from the research)?|best takes|tl;?dr|summary|verdict|bottom line)(?:\*\*|__)?:?(?:\*\*|__)?$",
    re.IGNORECASE,
)
_CORPUS_FOOTER_LINE = re.compile(r"\bYour files\b", re.IGNORECASE)
_BOLD_FROM_YOUR_FILES = re.compile(
    r"^\s*(?:\*\*|__)?From your files:?(?:\*\*|__)?:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
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
    re.escape(PRIVATE_CORPUS_START) + r".*?(?:" + re.escape(PRIVATE_CORPUS_END) + r"\s*|\Z)",
    re.DOTALL,
)
_FENCE_OPEN_BACKTICK = re.compile(r"^[ ]{0,3}(`{3,})[^`\r\n]*$")
_FENCE_OPEN_TILDE = re.compile(r"^[ ]{0,3}(~{3,})[^\r\n]*$")
_TOP_LEVEL_HEADING = re.compile(r"^#{1,2}[ \t]")
_H1_TO_H3_HEADING = re.compile(r"^#{1,3}[ \t]")
_DRILL_HEADING = re.compile(r"^## Drill Follow-up[ \t]*$")
_DRILL_DEEPER_HEADING = re.compile(r"^### Deeper[ \t]*$")
_DRILL_NEW_ITEMS = re.compile(r"^- [0-9]+ new items after dedupe$")
_DRILL_SOURCES = re.compile(r"^- Re-researched sources:[^\r\n]*$")
_PRIVATE_FRESHNESS_ROW = re.compile(
    r"^\| \*\*contradicted\*\* \| [^\r\n]*"
    r"(?:\]\(corpus://| \(Newer corpus item disagrees\))[^\r\n]*(?:\r?\n|\Z)",
    re.MULTILINE | re.IGNORECASE,
)
_LEGACY_CORPUS_SIGNAL = re.compile(
    r"^#{1,3}[ \t]+From your files:?[ \t]*\r?$"
    r"|^- (?:Sources|Re-researched sources):[^\r\n]*\bYour files\b"
    r"|^[0-9]+\. \[[^\]\r\n]*\b(?:corpus|your files)\b[^\]\r\n]*\]",
    re.MULTILINE | re.IGNORECASE,
)
_FROM_YOUR_FILES_H12 = re.compile(r"^#{1,2}[ \t]+From your files:?[ \t]*$", re.IGNORECASE)
_FROM_YOUR_FILES_H3 = re.compile(r"^###[ \t]+From your files:?[ \t]*$", re.IGNORECASE)
_FROM_YOUR_FILES_BOLD = re.compile(
    r"^[ \t]*(?:\*\*|__)From your files:?(?:\*\*|__)?:?[ \t]*$",
    re.IGNORECASE,
)
_FRESHNESS_HEADING = re.compile(r"^## Freshness Verification[ \t]*$")
_FRESHNESS_TABLE_HEADER = re.compile(r"^\| Verdict \| Claim \| Evidence \| Checked \|$")
_FRESHNESS_TABLE_SEP = re.compile(r"^\| --- \| --- \| --- \| --- \|$")
_RANKED_CLUSTERS_HEADING = re.compile(r"^## Ranked Evidence Clusters[ \t]*$")
_RANKED_CLUSTER_HEADING = re.compile(r"^(?P<prefix>### [0-9]+\. )(?P<title>[^\r\n]*)$")
_RANKED_CLUSTER_STATS = re.compile(r" \(score -?[0-9]+, [0-9]+ items?, sources: [^()\r\n]*\)$")
_RANKED_REPRESENTATIVE = re.compile(
    r"^[0-9]+\. \[(?P<sources>[^\]\r\n]+)\] (?P<title>[^\r\n]+)$",
)
_RANKED_FRESHNESS_BADGE = re.compile(r" \[freshness:[^\]]+\]$")
_BRIEF_CLUSTERS_HEADING = re.compile(
    r"^## (?P<name>Ranked Storylines|Topic Tensions|Source Clusters)[ \t]*$"
)
_BRIEF_STORYLINE_TITLE = re.compile(r"^### [0-9]+\. [^\r\n]*$")
_BRIEF_CLUSTER_LABEL = re.compile(r"^- \*\*[^\r\n]*$")


def _fence_step(line_text: str, fence: tuple[str, int] | None) -> tuple[str, int] | None:
    """Track CommonMark backtick/tilde fenced code block state across lines."""
    if fence is None:
        match = _FENCE_OPEN_BACKTICK.match(line_text) or _FENCE_OPEN_TILDE.match(line_text)
        if match:
            token = match.group(1)
            return token[0], len(token)
        return None
    char, min_len = fence
    stripped_indent = line_text[:3].lstrip(" ") + line_text[3:]
    count = 0
    while count < len(stripped_indent) and stripped_indent[count] == char:
        count += 1
    if count >= min_len and stripped_indent[count:].strip(" \t") == "":
        return None
    return fence


def _find_section_end(
    lines: list[str],
    start: int,
    boundary: re.Pattern[str],
) -> int:
    """Return index of next non-fenced line matching ``boundary``, or ``len(lines)``."""
    fence: tuple[str, int] | None = None
    for idx in range(start, len(lines)):
        text = lines[idx].rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            continue
        if boundary.match(text):
            return idx
        fence = _fence_step(text, None)
    return len(lines)


def _strip_unmarked_drills(markdown: str) -> str:
    """Strip every non-fenced ``## Drill Follow-up`` not preceded by a known marker."""
    markers = (PUBLIC_DRILL_MARKER, PRIVATE_CORPUS_START)
    lines = markdown.splitlines(keepends=True)
    while True:
        fence: tuple[str, int] | None = None
        unmarked_idx: int | None = None
        for idx, line in enumerate(lines):
            text = line.rstrip("\r\n")
            if fence is not None:
                fence = _fence_step(text, fence)
                continue
            if _DRILL_HEADING.match(text):
                prev_text = lines[idx - 1].rstrip("\r\n") if idx > 0 else ""
                if prev_text not in markers:
                    unmarked_idx = idx
                    break
            fence = _fence_step(text, None)
        if unmarked_idx is None:
            return "".join(lines)
        # Legacy drill summaries did not identify their sources. Use the last
        # non-fenced generated trailer so headings or fenced excerpts cannot end it.
        end = len(lines)
        inner_fence: tuple[str, int] | None = None
        for idx in range(unmarked_idx + 1, len(lines)):
            text = lines[idx].rstrip("\r\n")
            if inner_fence is not None:
                inner_fence = _fence_step(text, inner_fence)
                continue
            if (
                _DRILL_DEEPER_HEADING.match(text)
                and idx + 3 < len(lines)
                and lines[idx + 1].rstrip("\r\n").strip(" \t") == ""
                and _DRILL_NEW_ITEMS.match(lines[idx + 2].rstrip("\r\n"))
                and _DRILL_SOURCES.match(lines[idx + 3].rstrip("\r\n"))
            ):
                end = idx + 4
            inner_fence = _fence_step(text, None)
        del lines[unmarked_idx:end]


def _strip_from_your_files_sections(markdown: str) -> str:
    """Strip unmarked ``From your files`` sections without stopping inside code fences."""
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fence: tuple[str, int] | None = None
    skip_until: re.Pattern[str] | None = None
    for line in lines:
        text = line.rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            if skip_until is None:
                out.append(line)
            continue
        if skip_until is not None:
            if skip_until.match(text):
                skip_until = None
            else:
                fence = _fence_step(text, None)
                continue
        if _FROM_YOUR_FILES_H12.match(text) or _FROM_YOUR_FILES_BOLD.match(text):
            skip_until = _TOP_LEVEL_HEADING
            continue
        if _FROM_YOUR_FILES_H3.match(text):
            skip_until = _H1_TO_H3_HEADING
            continue
        fence = _fence_step(text, None)
        out.append(line)
    return "".join(out)


def _public_legacy_cluster_lines(heading_line: str, body_lines: list[str]) -> str:
    """Redact corpus representatives and corpus-tainted body from one legacy cluster."""
    heading_text = heading_line.rstrip("\r\n")
    ending = heading_line[len(heading_text):]
    heading_match = _RANKED_CLUSTER_HEADING.match(heading_text)
    if heading_match is None:
        return heading_line + "".join(body_lines)

    preamble: list[str] = []
    rep_blocks: list[tuple[re.Match[str], list[str]]] = []
    fence: tuple[str, int] | None = None
    for line in body_lines:
        text = line.rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            if rep_blocks:
                rep_blocks[-1][1].append(line)
            else:
                preamble.append(line)
            continue
        rep_match = _RANKED_REPRESENTATIVE.match(text)
        if rep_match is not None:
            rep_blocks.append((rep_match, [line]))
            continue
        fence = _fence_step(text, None)
        if rep_blocks:
            rep_blocks[-1][1].append(line)
        else:
            preamble.append(line)

    had_representatives = bool(rep_blocks)
    public_reps: list[tuple[re.Match[str], list[str]]] = []
    for rep_match, block_lines in rep_blocks:
        sources = {source.strip().lower() for source in rep_match["sources"].split(",")}
        if {"corpus", "your files"} & sources:
            continue
        public_reps.append((rep_match, block_lines))

    raw_body = "".join(body_lines)
    cleaned_body = "".join(preamble) + "".join("".join(block) for _, block in public_reps)
    if raw_body.endswith("\n\n") and cleaned_body and not cleaned_body.endswith("\n\n"):
        cleaned_body = cleaned_body.rstrip("\r\n") + "\n\n"

    for rep_match, _ in public_reps:
        title = _RANKED_FRESHNESS_BADGE.sub("", rep_match["title"]).strip()
        if title:
            stats = _RANKED_CLUSTER_STATS.search(heading_match["title"])
            return (
                heading_match["prefix"]
                + title
                + (stats.group() if stats else "")
                + ending
                + cleaned_body
            )
    if had_representatives:
        return ""
    if (
        _LEGACY_CORPUS_SIGNAL.search(cleaned_body)
        or "corpus://" in cleaned_body.lower()
        or _BOLD_FROM_YOUR_FILES.search(cleaned_body)
    ):
        return ""
    return cleaned_body


def _rewrite_ranked_clusters_sections(markdown: str) -> str:
    """Rewrite unmarked ``## Ranked Evidence Clusters`` sections using fence-aware boundaries."""
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fence: tuple[str, int] | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        text = line.rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            out.append(line)
            idx += 1
            continue
        if _RANKED_CLUSTERS_HEADING.match(text):
            end = _find_section_end(lines, idx + 1, _TOP_LEVEL_HEADING)
            prev_text = lines[idx - 1].rstrip("\r\n") if idx > 0 else ""
            if prev_text == PUBLIC_CLUSTERS_MARKER:
                out.extend(lines[idx:end])
                idx = end
                continue
            out.append(line)
            # Split clusters on non-fenced `### N. ` headings.
            sec_fence: tuple[str, int] | None = None
            cluster_heading: str | None = None
            cluster_body: list[str] = []
            for sec_line in lines[idx + 1:end]:
                sec_text = sec_line.rstrip("\r\n")
                if sec_fence is not None:
                    sec_fence = _fence_step(sec_text, sec_fence)
                    if cluster_heading is not None:
                        cluster_body.append(sec_line)
                    else:
                        out.append(sec_line)
                    continue
                if _RANKED_CLUSTER_HEADING.match(sec_text):
                    if cluster_heading is not None:
                        out.append(_public_legacy_cluster_lines(cluster_heading, cluster_body))
                    cluster_heading = sec_line
                    cluster_body = []
                    continue
                sec_fence = _fence_step(sec_text, None)
                if cluster_heading is not None:
                    cluster_body.append(sec_line)
                else:
                    out.append(sec_line)
            if cluster_heading is not None:
                out.append(_public_legacy_cluster_lines(cluster_heading, cluster_body))
            idx = end
            continue
        fence = _fence_step(text, None)
        out.append(line)
        idx += 1
    return "".join(out)


def _rewrite_brief_clusters_sections(markdown: str) -> str:
    """Strip legacy Production Brief cluster titles using fence-aware boundaries."""
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fence: tuple[str, int] | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        text = line.rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            out.append(line)
            idx += 1
            continue
        match = _BRIEF_CLUSTERS_HEADING.match(text)
        if match is not None:
            end = _find_section_end(lines, idx + 1, _TOP_LEVEL_HEADING)
            prev_text = lines[idx - 1].rstrip("\r\n") if idx > 0 else ""
            if prev_text == PUBLIC_CLUSTERS_MARKER:
                out.extend(lines[idx:end])
                idx = end
                continue
            out.append(line)
            label_re = (
                _BRIEF_STORYLINE_TITLE
                if match["name"] == "Ranked Storylines"
                else _BRIEF_CLUSTER_LABEL
            )
            sec_fence: tuple[str, int] | None = None
            for sec_line in lines[idx + 1:end]:
                sec_text = sec_line.rstrip("\r\n")
                if sec_fence is not None:
                    sec_fence = _fence_step(sec_text, sec_fence)
                    out.append(sec_line)
                    continue
                if label_re.match(sec_text):
                    continue
                sec_fence = _fence_step(sec_text, None)
                out.append(sec_line)
            idx = end
            continue
        fence = _fence_step(text, None)
        out.append(line)
        idx += 1
    return "".join(out)


def _strip_unmarked_freshness_sections(markdown: str) -> str:
    """Strip unmarked ``## Freshness Verification`` tables using fence-aware boundaries."""
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fence: tuple[str, int] | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        text = line.rstrip("\r\n")
        if fence is not None:
            fence = _fence_step(text, fence)
            out.append(line)
            idx += 1
            continue
        if (
            _FRESHNESS_HEADING.match(text)
            and idx + 3 < len(lines)
            and lines[idx + 1].rstrip("\r\n").strip(" \t") == ""
            and _FRESHNESS_TABLE_HEADER.match(lines[idx + 2].rstrip("\r\n"))
            and _FRESHNESS_TABLE_SEP.match(lines[idx + 3].rstrip("\r\n"))
        ):
            end = _find_section_end(lines, idx + 4, _TOP_LEVEL_HEADING)
            prev_text = lines[idx - 1].rstrip("\r\n") if idx > 0 else ""
            if prev_text == PUBLIC_FRESHNESS_MARKER:
                out.extend(lines[idx:end])
            idx = end
            continue
        fence = _fence_step(text, None)
        out.append(line)
        idx += 1
    return "".join(out)


def has_private_corpus(markdown: str) -> bool:
    """Return whether a document carries local-corpus markers or citations."""
    return bool(
        PRIVATE_CORPUS_START in markdown
        or _LEGACY_CORPUS_SIGNAL.search(markdown)
        or _PRIVATE_FRESHNESS_ROW.search(markdown)
        or "corpus://" in markdown.lower()
        or _CORPUS_FOOTER_LINE.search(markdown)
        or _BOLD_FROM_YOUR_FILES.search(markdown)
    )


def _is_structured_engine_report(markdown: str) -> bool:
    """Return True when markdown carries structured engine report markers.

    Structured engine reports (including saved comparison reports emitted by
    ``render_comparison_multi``) delimit private corpus sections explicitly via
    ``PRIVATE_CORPUS_START``/``PRIVATE_CORPUS_END`` and should be redacted in
    place by ``public_markdown`` rather than excluded wholesale.
    """
    return bool(
        _REPORT_TITLE.search(markdown)
        or (PRIVATE_CORPUS_START in markdown and PRIVATE_CORPUS_END in markdown)
        or PUBLIC_CLUSTERS_MARKER in markdown
        or "## Ranked Evidence Clusters" in markdown
        or "<!-- EVIDENCE FOR SYNTHESIS" in markdown
        or "# END OF last30days CANONICAL OUTPUT" in markdown
    )


def is_unbounded_private_brief(
    markdown: str,
    *,
    source_format: str | None = None,
) -> bool:
    """Return True when a synthesized chat brief carries unbounded local-corpus content.

    Chat synthesis weaves corpus evidence into heading-free prose, so private
    passages cannot be reliably isolated by heading or marker stripping.
    Structured engine reports (including comparisons with bounded corpus blocks)
    are NOT unbounded and keep their in-place redaction behavior.
    """
    if source_format is not None and source_format != "finished-brief":
        return False
    if _is_structured_engine_report(markdown):
        return False
    return bool(_BADGE_LINE.search(markdown) and has_private_corpus(markdown))


def _strip_private_corpus_blocks(markdown: str) -> str:
    """Strip explicit private corpus blocks without splicing an earlier public marker onto the next section."""
    def _replace(match: re.Match[str]) -> str:
        marker_end = match.start()
        while marker_end > 0 and markdown[marker_end - 1] in " \t\r\n":
            marker_end -= 1
        if markdown.endswith(
            (PUBLIC_DRILL_MARKER, PUBLIC_CLUSTERS_MARKER, PUBLIC_FRESHNESS_MARKER),
            0,
            marker_end,
        ):
            return "\n\n"
        return ""

    return _PRIVATE_CORPUS_BLOCK.sub(_replace, markdown)


def public_markdown(markdown: str) -> str:
    if is_unbounded_private_brief(markdown):
        return ""
    corpus_document = has_private_corpus(markdown)
    markdown = _strip_private_corpus_blocks(markdown)
    markdown = _strip_unmarked_drills(markdown)
    markdown = _strip_from_your_files_sections(markdown)
    if corpus_document:
        # The old renderer could keep a drill's local-derived cluster title
        # after dedupe left only public representatives.
        markdown = _rewrite_ranked_clusters_sections(markdown)
        if markdown.startswith("# Production Brief: "):
            markdown = _rewrite_brief_clusters_sections(markdown)
        # Old rendered rows do not identify the claim's source, so a row
        # with a public evidence link can still represent a private claim.
        markdown = _strip_unmarked_freshness_sections(markdown)
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
    source_mtime_ns: int | None = None
    source_size: int | None = None

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


@contextmanager
def _open_dir_fd(
    target: Path | str,
    *,
    dir_fd: int | None = None,
    nofollow: bool = False,
):
    """Open a directory descriptor pinned against concurrent directory swaps on POSIX."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    if nofollow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(target, flags)
    try:
        yield fd
    finally:
        os.close(fd)


def _listdir_pinned(dir_fd: int, dir_path: Path) -> list[str]:
    if _SUPPORTS_LISTDIR_FD:
        return sorted(os.listdir(dir_fd))
    return sorted(os.listdir(dir_path))


def _norm_parts(path: Path) -> tuple[str, ...]:
    if sys.platform == "darwin":
        return tuple(part.casefold() for part in path.parts)
    if sys.platform == "win32":
        return tuple(os.path.normcase(part) for part in path.parts)
    return path.parts


def _same_normalized_path(left: Path, right: Path) -> bool:
    return _norm_parts(left) == _norm_parts(right)


def _path_is_within(child: Path, parent: Path) -> bool:
    c_parts = _norm_parts(child)
    p_parts = _norm_parts(parent)
    return bool(p_parts) and len(c_parts) >= len(p_parts) and c_parts[:len(p_parts)] == p_parts


def _win32_fd_real_path(fd: int) -> Path | None:
    """Canonical path of an open Windows descriptor via ``GetFinalPathNameByHandleW``."""
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_path = kernel32.GetFinalPathNameByHandleW
        get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        get_path.restype = ctypes.c_uint32
        needed = get_path(handle, None, 0, 0)
        if not needed:
            return None
        buf = ctypes.create_unicode_buffer(needed + 1)
        written = get_path(handle, buf, len(buf), 0)
        if not written or written >= len(buf):
            return None
        raw = buf.value
    except (AttributeError, ImportError, OSError, ValueError):
        return None
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(raw) if raw else None


def _fd_real_path(fd: int) -> Path | None:
    """Canonical kernel path of an open descriptor (Windows, macOS, Linux)."""
    if sys.platform == "win32":
        return _win32_fd_real_path(fd)
    target: str | None = None
    if sys.platform == "darwin" and fcntl is not None:
        f_getpath = getattr(fcntl, "F_GETPATH", _DARWIN_F_GETPATH)
        try:
            raw = fcntl.fcntl(fd, f_getpath, b"\x00" * _DARWIN_MAXPATHLEN)
        except (OSError, ValueError):
            return None
        if isinstance(raw, (bytes, bytearray)):
            target = os.fsdecode(bytes(raw).split(b"\x00", 1)[0])
        elif isinstance(raw, str):
            target = raw.split("\x00", 1)[0]
    else:
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return None
    if not target or not target.startswith("/"):
        return None
    return Path(target)


def _checked_subdir_stat(subdir: Path, root: Path) -> os.stat_result | None:
    """Return ``lstat`` for ``subdir`` when it is a non-symlink directory inside ``root``."""
    if _IS_JUNCTION is not None and _IS_JUNCTION(subdir):
        return None
    try:
        st = subdir.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return None
    try:
        root_resolved = root.resolve(strict=True)
        subdir_resolved = subdir.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _path_is_within(subdir_resolved, root_resolved):
        return None
    return st


def _is_safe_subdir(subdir: Path, root: Path) -> bool:
    """Return True when ``subdir`` is a non-symlink directory contained inside ``root``."""
    return _checked_subdir_stat(subdir, root) is not None


def _verify_checked_parent(
    path: Path,
    *,
    root_resolved: Path,
    expected_parent_dir: Path | None = None,
    expected_parent_id: tuple[int, int] | None = None,
) -> None:
    """Verify intermediate directories and parent directory identity have not changed."""
    target_parent = expected_parent_dir if expected_parent_dir is not None else path.parent
    if expected_parent_dir is not None and not _same_normalized_path(path.parent, expected_parent_dir):
        raise OSError(f"File parent does not match checked directory: {path}")

    rel_parts: tuple[str, ...] | None = None
    try:
        rel_parts = target_parent.relative_to(root_resolved).parts
    except ValueError:
        if expected_parent_dir is not None:
            raise OSError(f"Directory escapes library root: {target_parent}") from None

    if rel_parts is not None:
        cur = root_resolved
        for part in rel_parts:
            if part in ("", ".", ".."):
                raise OSError(f"Invalid directory component in {target_parent}")
            cur = cur / part
            if _IS_JUNCTION is not None and _IS_JUNCTION(cur):
                raise OSError(errno.ELOOP, f"Refusing to follow junction directory: {cur}")
            cur_st = cur.lstat()
            if stat.S_ISLNK(cur_st.st_mode):
                raise OSError(errno.ELOOP, f"Refusing to follow symlink directory: {cur}")
            if not stat.S_ISDIR(cur_st.st_mode):
                raise OSError(f"Not a directory: {cur}")

    if _IS_JUNCTION is not None and _IS_JUNCTION(target_parent):
        raise OSError(errno.ELOOP, f"Refusing to follow junction directory: {target_parent}")
    parent_st = target_parent.lstat()
    if stat.S_ISLNK(parent_st.st_mode):
        raise OSError(errno.ELOOP, f"Refusing to follow symlink directory: {target_parent}")
    if not stat.S_ISDIR(parent_st.st_mode):
        raise OSError(f"Not a directory: {target_parent}")
    if (
        expected_parent_id is not None
        and (os.name == "posix" or (expected_parent_id[1] and parent_st.st_ino))
        and (parent_st.st_dev, parent_st.st_ino) != expected_parent_id
    ):
        raise OSError(f"Directory changed during scan: {target_parent}")


def _read_regular_text(
    path: Path,
    *,
    root: Path | None = None,
    max_bytes: int | None = None,
    dir_fd: int | None = None,
    expected_parent_dir: Path | None = None,
    expected_parent_id: tuple[int, int] | None = None,
) -> tuple[str, os.stat_result]:
    """Read UTF-8 text from a regular, non-symlink file within ``root`` and size cap."""
    limit = MAX_LIBRARY_FILE_BYTES if max_bytes is None else max_bytes
    root_resolved: Path | None = None
    if dir_fd is not None:
        entry_name = path.name
        if not entry_name or entry_name in (".", "..") or "/" in entry_name or "\\" in entry_name:
            raise OSError(f"Invalid library entry name: {path}")
        lstat_res = os.lstat(entry_name, dir_fd=dir_fd)
    else:
        if root is not None:
            root_resolved = root.resolve(strict=True)
            _verify_checked_parent(
                path,
                root_resolved=root_resolved,
                expected_parent_dir=expected_parent_dir,
                expected_parent_id=expected_parent_id,
            )
        if _IS_JUNCTION is not None and _IS_JUNCTION(path):
            raise OSError(errno.ELOOP, f"Refusing to follow junction: {path}")
        lstat_res = path.lstat()
    if stat.S_ISLNK(lstat_res.st_mode):
        raise OSError(errno.ELOOP, f"Refusing to follow symlink: {path}")
    if not stat.S_ISREG(lstat_res.st_mode):
        raise OSError(f"Not a regular file: {path}")
    if dir_fd is None and root_resolved is not None:
        resolved_path = path.resolve(strict=True)
        if not _path_is_within(resolved_path, root_resolved):
            raise OSError(f"Path escapes library root: {path}")
        if expected_parent_dir is not None and not _same_normalized_path(
            resolved_path.parent, expected_parent_dir
        ):
            raise OSError(f"Path escapes checked directory: {path}")
    flags = os.O_RDONLY
    for attr in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_BINARY"):
        flags |= getattr(os, attr, 0)
    try:
        if dir_fd is not None:
            fd = os.open(entry_name, flags, dir_fd=dir_fd)
        else:
            fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise OSError(errno.ELOOP, f"Refusing to follow symlink: {path}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"Not a regular file: {path}")
        if (os.name == "posix" or (lstat_res.st_ino and st.st_ino)) and (
            st.st_dev,
            st.st_ino,
        ) != (lstat_res.st_dev, lstat_res.st_ino):
            raise OSError(f"File changed during open: {path}")
        if dir_fd is None and root_resolved is not None:
            _verify_checked_parent(
                path,
                root_resolved=root_resolved,
                expected_parent_dir=expected_parent_dir,
                expected_parent_id=expected_parent_id,
            )
            fd_path = _fd_real_path(fd)
            if fd_path is None:
                raise OSError(f"Unable to verify opened file location: {path}")
            if not _path_is_within(fd_path, root_resolved):
                raise OSError(f"Opened file escapes library root: {path}")
            if expected_parent_dir is not None and not _same_normalized_path(
                fd_path, expected_parent_dir / path.name
            ):
                raise OSError(f"Opened file escapes checked directory: {path}")
        if st.st_size > limit:
            raise ValueError(f"file exceeds {limit} byte limit")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > limit:
            raise ValueError(f"file exceeds {limit} byte limit")
    finally:
        os.close(fd)
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, st


def get_or_create_library_id(memory_dir: Path | str) -> str:
    """Return the persisted random namespace for one research library."""
    memory_path = Path(memory_dir).expanduser()
    memory_path.mkdir(parents=True, exist_ok=True)
    resolved_memory = memory_path.resolve(strict=True)
    id_path = resolved_memory / LIBRARY_ID_FILENAME
    if _SUPPORTS_DIR_FD:
        with _open_dir_fd(resolved_memory) as memory_fd:
            try:
                raw_id, _ = _read_regular_text(id_path, max_bytes=1024, dir_fd=memory_fd)
                library_id = raw_id.strip()
            except FileNotFoundError:
                library_id = uuid.uuid4().hex
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                for attr in ("O_NOFOLLOW", "O_CLOEXEC"):
                    flags |= getattr(os, attr, 0)
                try:
                    fd = os.open(LIBRARY_ID_FILENAME, flags, 0o600, dir_fd=memory_fd)
                except FileExistsError:
                    raw_id, _ = _read_regular_text(id_path, max_bytes=1024, dir_fd=memory_fd)
                    library_id = raw_id.strip()
                else:
                    try:
                        stream = os.fdopen(fd, "w", encoding="utf-8")
                    except BaseException:
                        os.close(fd)
                        raise
                    with stream:
                        stream.write(f"{library_id}\n")
    else:
        memory_stat = _checked_subdir_stat(resolved_memory, resolved_memory)
        memory_id = (memory_stat.st_dev, memory_stat.st_ino) if memory_stat is not None else None
        try:
            raw_id, _ = _read_regular_text(
                id_path,
                root=resolved_memory,
                max_bytes=1024,
                expected_parent_dir=resolved_memory,
                expected_parent_id=memory_id,
            )
            library_id = raw_id.strip()
        except FileNotFoundError:
            library_id = uuid.uuid4().hex
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            for attr in ("O_NOFOLLOW", "O_CLOEXEC"):
                flags |= getattr(os, attr, 0)
            try:
                fd = os.open(id_path, flags, 0o600)
            except FileExistsError:
                raw_id, _ = _read_regular_text(
                    id_path,
                    root=resolved_memory,
                    max_bytes=1024,
                    expected_parent_dir=resolved_memory,
                    expected_parent_id=memory_id,
                )
                library_id = raw_id.strip()
            else:
                try:
                    stream = os.fdopen(fd, "w", encoding="utf-8")
                except BaseException:
                    os.close(fd)
                    raise
                with stream:
                    stream.write(f"{library_id}\n")
    if not _LIBRARY_ID.fullmatch(library_id):
        raise ValueError(f"invalid library ID in {id_path}")
    return library_id


def is_generated_brief_name(name: str) -> bool:
    """Return whether a filename has the exact library-renderer output shape."""
    return _GENERATED_BRIEF_NAME.fullmatch(name) is not None


def _resolve_scan_root(raw_path: Path, notes: list[str]) -> Path | None:
    """Strictly resolve an existing scan root without collapsing missing ``..`` segments."""
    try:
        return raw_path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, OSError) and exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        notes.append(f"Skipped {raw_path}: {exc}")
        return None


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
    memory_path = _resolve_scan_root(Path(memory_dir).expanduser(), notes)
    briefs_path = _resolve_scan_root(Path(briefs_dir).expanduser(), notes)

    if _SUPPORTS_DIR_FD:
        if memory_path is not None:
            try:
                with _open_dir_fd(memory_path) as memory_fd:
                    finished_path = memory_path / FINISHED_SUBDIR
                    try:
                        with _open_dir_fd(
                            FINISHED_SUBDIR, dir_fd=memory_fd, nofollow=True
                        ) as finished_fd:
                            for name in _listdir_pinned(finished_fd, finished_path):
                                if not name.endswith(".md"):
                                    continue
                                path = finished_path / name
                                try:
                                    entry = parse_markdown_file(
                                        path,
                                        source_format="finished-brief",
                                        dir_fd=finished_fd,
                                    )
                                    _keep_preferred(entries, entry)
                                except (OSError, UnicodeError, ValueError) as exc:
                                    notes.append(f"Skipped {path}: {exc}")
                                    continue
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        if exc.errno == errno.ELOOP:
                            notes.append(f"Skipped {finished_path}: refusing to follow symlink directory")
                        elif exc.errno == errno.ENOTDIR:
                            try:
                                if stat.S_ISLNK(os.lstat(FINISHED_SUBDIR, dir_fd=memory_fd).st_mode):
                                    notes.append(
                                        f"Skipped {finished_path}: refusing to follow symlink directory"
                                    )
                            except OSError:
                                pass
                        else:
                            notes.append(f"Skipped {finished_path}: {exc}")

                    for name in _listdir_pinned(memory_fd, memory_path):
                        if not name.endswith(".md"):
                            continue
                        path = memory_path / name
                        try:
                            entry = parse_markdown_file(
                                path,
                                source_format="markdown",
                                dir_fd=memory_fd,
                            )
                            _keep_preferred(entries, entry)
                        except (OSError, UnicodeError, ValueError) as exc:
                            notes.append(f"Skipped {path}: {exc}")
                            continue
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTDIR):
                    notes.append(f"Skipped {memory_path}: {exc}")

        if briefs_path is not None:
            try:
                with _open_dir_fd(briefs_path) as briefs_fd:
                    for name in _listdir_pinned(briefs_fd, briefs_path):
                        if not name.endswith(".json"):
                            continue
                        path = briefs_path / name
                        try:
                            entry = _parse_briefing(path, dir_fd=briefs_fd)
                            _keep_preferred(entries, entry)
                        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                            notes.append(f"Skipped {path}: {exc}")
                            continue
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTDIR):
                    notes.append(f"Skipped {briefs_path}: {exc}")
    else:
        if memory_path is not None:
            memory_stat = _checked_subdir_stat(memory_path, memory_path)
            if memory_stat is not None:
                memory_id = (memory_stat.st_dev, memory_stat.st_ino)
                finished_path = memory_path / FINISHED_SUBDIR
                finished_stat = _checked_subdir_stat(finished_path, memory_path)
                if finished_stat is not None:
                    finished_id = (finished_stat.st_dev, finished_stat.st_ino)
                    for path in sorted(finished_path.glob("*.md")):
                        try:
                            entry = parse_markdown_file(
                                path,
                                source_format="finished-brief",
                                root=memory_path,
                                expected_parent_dir=finished_path,
                                expected_parent_id=finished_id,
                            )
                            _keep_preferred(entries, entry)
                        except (OSError, UnicodeError, ValueError) as exc:
                            notes.append(f"Skipped {path}: {exc}")
                            continue
                elif finished_path.is_symlink() or (
                    _IS_JUNCTION is not None and _IS_JUNCTION(finished_path)
                ):
                    notes.append(f"Skipped {finished_path}: refusing to follow symlink directory")

                for path in sorted(memory_path.glob("*.md")):
                    try:
                        entry = parse_markdown_file(
                            path,
                            source_format="markdown",
                            root=memory_path,
                            expected_parent_dir=memory_path,
                            expected_parent_id=memory_id,
                        )
                        _keep_preferred(entries, entry)
                    except (OSError, UnicodeError, ValueError) as exc:
                        notes.append(f"Skipped {path}: {exc}")
                        continue

        if briefs_path is not None:
            briefs_stat = _checked_subdir_stat(briefs_path, briefs_path)
            if briefs_stat is not None:
                briefs_id = (briefs_stat.st_dev, briefs_stat.st_ino)
                for path in sorted(briefs_path.glob("*.json")):
                    try:
                        entry = _parse_briefing(
                            path,
                            root=briefs_path,
                            expected_parent_dir=briefs_path,
                            expected_parent_id=briefs_id,
                        )
                        _keep_preferred(entries, entry)
                    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                        notes.append(f"Skipped {path}: {exc}")
                        continue

    filtered = _suppress_superseded_raw(entries)
    ordered = sorted(
        filtered.values(),
        key=lambda entry: (entry.published_date, entry.topic.casefold(), entry.source_path.name),
        reverse=True,
    )
    return ordered, notes


def _suppress_superseded_raw(entries: dict[str, LibraryEntry]) -> dict[str, LibraryEntry]:
    """Hide unsuffixed raw dumps when a public finished brief covers the same topic and date."""
    finished_keys = {
        (entry.slug, entry.published_date)
        for entry in entries.values()
        if entry.source_format == "finished-brief"
        and not is_unbounded_private_brief(entry.content)
    }
    if not finished_keys:
        return entries
    kept: dict[str, LibraryEntry] = {}
    for entry_id, entry in entries.items():
        if (
            entry.source_format == "markdown"
            and (entry.slug, entry.published_date) in finished_keys
            and _is_unsuffixed_raw_dump(entry.source_path.stem)
        ):
            continue
        kept[entry_id] = entry
    return kept


def _is_unsuffixed_raw_dump(stem: str) -> bool:
    return _UNSUFFIXED_RAW_STEM.fullmatch(stem) is not None


def _keep_preferred(entries: dict[str, LibraryEntry], entry: LibraryEntry) -> None:
    existing = entries.get(entry.entry_id)
    if existing is None or entry.source_updated_at > existing.source_updated_at:
        entries[entry.entry_id] = entry


def parse_markdown_file(
    path: Path,
    *,
    source_format: str = "markdown",
    root: Path | None = None,
    dir_fd: int | None = None,
    expected_parent_dir: Path | None = None,
    expected_parent_id: tuple[int, int] | None = None,
) -> LibraryEntry:
    """Parse one saved Markdown report or finished brief into a LibraryEntry."""
    content, st = _read_regular_text(
        path,
        root=root,
        dir_fd=dir_fd,
        expected_parent_dir=expected_parent_dir,
        expected_parent_id=expected_parent_id,
    )
    unbounded_private = is_unbounded_private_brief(content, source_format=source_format)
    parse_source = content if unbounded_private else public_markdown(content)
    meta_topic_match = _TOPIC_META_COMMENT.search(content)
    badge_topic, badge_date = _parse_badge_line(parse_source)
    report_title_match = _REPORT_TITLE.search(parse_source)
    first_title_match = _FIRST_TITLE.search(parse_source)

    if meta_topic_match:
        topic = _clean_inline(meta_topic_match.group(1))
    elif source_format == "finished-brief" and badge_topic:
        # Preserve the badge topic so comparison display headings like
        # "# Codex vs Cursor: What the Community Says (/Last30Days)" do not
        # diverge from the raw report's topic and slug.
        topic = _clean_inline(badge_topic)
    elif report_title_match:
        topic = _clean_inline(report_title_match.group(1))
    elif badge_topic:
        topic = _clean_inline(badge_topic)
    elif first_title_match:
        raw_heading = _COMPARISON_TITLE_SUFFIX.sub("", first_title_match.group(1))
        topic = _clean_inline(raw_heading)
    else:
        raise ValueError("no Markdown title found")
    if not topic:
        raise ValueError("empty Markdown title")
    published_date = badge_date or _markdown_date(parse_source, path, st=st)
    if unbounded_private:
        headline = topic
        summary = "Private local-corpus brief (stored locally only)."
    else:
        headline = _markdown_headline(parse_source) or _brief_prose_headline(parse_source) or topic
        summary = _markdown_summary(parse_source) or _brief_prose_summary(parse_source) or headline
    return LibraryEntry(
        slug=slugify(topic),
        topic=topic,
        published_date=published_date,
        headline=headline,
        summary=summary,
        source_path=path,
        content=content,
        source_updated_at=_source_updated_at(path, st=st),
        source_format=source_format,
        source_mtime_ns=st.st_mtime_ns,
        source_size=st.st_size,
    )


def _parse_markdown(path: Path) -> LibraryEntry:
    return parse_markdown_file(path, source_format="markdown")


def _parse_badge_line(content: str) -> tuple[str, date | None]:
    match = _BADGE_LINE.search(content)
    if match is None:
        return "", None
    line = match.group(0).strip()
    parts = [part.strip() for part in line.split("·") if part.strip()]
    topic = ""
    published: date | None = None
    if synced := _BADGE_SYNCED_DATE.search(line):
        try:
            published = date.fromisoformat(synced.group(1))
        except ValueError:
            published = None
    else:
        for segment in parts[1:]:
            if published is None:
                parsed = _parse_badge_date(segment)
                if parsed is not None:
                    published = parsed
                    continue
            if not topic and not _BADGE_VERSION.fullmatch(segment) and segment.lower() not in {"quick", "default", "deep", "cached"}:
                topic = segment
    return topic, published


def _parse_badge_date(text: str) -> date | None:
    month_matches = list(_BADGE_MONTH_DAY_YEAR.finditer(text))
    if month_matches:
        m = month_matches[-1]
        month_num = _BADGE_MONTHS.get(m.group(1).lower()[:4]) or _BADGE_MONTHS.get(m.group(1).lower()[:3])
        if month_num:
            try:
                return date(int(m.group(3)), month_num, int(m.group(2)))
            except ValueError:
                pass
    iso_matches = list(_BADGE_ISO_DATE.finditer(text))
    if iso_matches:
        try:
            return date.fromisoformat(iso_matches[-1].group(1))
        except ValueError:
            pass
    return None


def _brief_prose_paragraph(content: str) -> str:
    in_fence = False
    para: list[str] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if para:
                break
            continue
        if in_fence:
            continue
        if not stripped:
            if para:
                break
            continue
        if (
            stripped.startswith("🌐 last30days")
            or stripped.startswith("<!--")
            or stripped.startswith("#")
            or stripped.startswith("---")
            or stripped.startswith("|")
            or _PROSE_LABEL_LINE.fullmatch(stripped) is not None
        ):
            if para:
                break
            continue
        para.append(stripped.rstrip("\\"))
    return _clean_inline(" ".join(para))


def _brief_prose_headline(content: str) -> str:
    text = _brief_prose_paragraph(content)
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence[:140]


def _brief_prose_summary(content: str) -> str:
    text = _brief_prose_paragraph(content)
    return text[:500] if text else ""


def _markdown_date(content: str, path: Path, *, st: os.stat_result | None = None) -> date:
    if match := _DATE_RANGE.search(content):
        return date.fromisoformat(match.group(1))
    if match := _DATED_FILENAME.search(path.stem):
        return date.fromisoformat(match.group(1))
    stat_res = st if st is not None else path.stat()
    return datetime.fromtimestamp(stat_res.st_mtime).date()


def _markdown_headline(content: str) -> str:
    if match := _RANKED_HEADLINE.search(content):
        return _clean_inline(_SCORE_SUFFIX.sub("", match.group(1)))
    return ""


def _markdown_summary(content: str) -> str:
    learned = re.search(
        r"^(?:##\s+What I learned|(?:\*\*|__)?What I learned:?(?:\*\*|__)?)\s*$\n+(.+?)(?=\n#{1,3}\s|\n---|\n```|\Z)",
        content,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if learned:
        for paragraph in re.split(r"\n\s*\n", learned.group(1)):
            cleaned = _clean_inline(paragraph)
            if cleaned and _PROSE_LABEL_LINE.fullmatch(cleaned) is None:
                return cleaned[:500]
    evidence = re.search(r"^\s*-\s*Evidence:\s*(.+?)\s*$", content, re.MULTILINE | re.IGNORECASE)
    if evidence:
        return _clean_inline(evidence.group(1))[:500]
    return ""


def _parse_briefing(
    path: Path,
    *,
    root: Path | None = None,
    dir_fd: int | None = None,
    expected_parent_dir: Path | None = None,
    expected_parent_id: tuple[int, int] | None = None,
) -> LibraryEntry:
    raw_text, st = _read_regular_text(
        path,
        root=root,
        dir_fd=dir_fd,
        expected_parent_dir=expected_parent_dir,
        expected_parent_id=expected_parent_id,
    )
    data = json.loads(raw_text)
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
        source_updated_at=_source_updated_at(path, st=st),
        source_format="json",
        source_mtime_ns=st.st_mtime_ns,
        source_size=st.st_size,
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


def _source_updated_at(path: Path, *, st: os.stat_result | None = None) -> datetime:
    stat_res = st if st is not None else path.stat()
    seconds, nanoseconds = divmod(stat_res.st_mtime_ns, 1_000_000_000)
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
