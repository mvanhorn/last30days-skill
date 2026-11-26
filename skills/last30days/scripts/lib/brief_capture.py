"""Detect, normalize, and atomically persist finished last30days chat briefs."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import html_render, library


FINISHED_SUBDIR = "finished"
_LOCK_FILENAME = ".capture.lock"

_BADGE_LINE = library._BADGE_LINE
_RAW_DUMP_MARKERS = (
    "<!-- EVIDENCE FOR SYNTHESIS",
    "# END OF last30days CANONICAL OUTPUT",
)
_SYNCED_DATE = library._BADGE_SYNCED_DATE
_VERSION_TOKEN = re.compile(r"\bv(\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9._-]+)?)\b")
_FIRST_HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.MULTILINE)
_RAW_SAVED_PATH = re.compile(r"Raw results saved to\s+([^\r\n]+?\.md)\s*$", re.MULTILINE)
_RAW_STEM_PREFIX = re.compile(r"^(.+?)-raw(?:-[^\r\n]+)?$")
_TOPIC_META_COMMENT = library._TOPIC_META_COMMENT


@dataclass(frozen=True, slots=True)
class CapturedBrief:
    """Normalized finished brief ready to persist to the local research library."""

    topic: str
    slug: str
    published_date: date
    version: str
    content: str
    content_hash: str
    is_private: bool


@dataclass(frozen=True, slots=True)
class SaveResult:
    """Outcome of persisting a captured finished brief."""

    status: str  # "saved" | "duplicate"
    path: Path
    brief: CapturedBrief


def has_private_corpus(markdown: str) -> bool:
    """Return whether a brief contains local-corpus content or citations."""
    return library.has_private_corpus(markdown)


def normalize_brief_text(content: str) -> str:
    """Normalize line endings and trailing whitespace for stable hashing and storage."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized + "\n"


def content_hash(content: str) -> str:
    """Return SHA-256 hex digest of normalized brief content."""
    return hashlib.sha256(normalize_brief_text(content).encode("utf-8")).hexdigest()


def _topic_from_raw_footer(body: str) -> tuple[str, str]:
    """Recover full untruncated (topic, slug) from the footer's saved raw path if available."""
    match = _RAW_SAVED_PATH.search(body)
    if match is None:
        return "", ""
    raw_str = match.group(1).strip().strip("`\"'")
    if not raw_str:
        return "", ""
    raw_path = Path(raw_str).expanduser()
    # Read only the header of an existing local raw report to recover exact topic text.
    try:
        if raw_path.is_file():
            with raw_path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(1024)
            title = library._REPORT_TITLE.search(head)
            if title:
                full_topic = library._clean_inline(title.group(1))
                if full_topic:
                    return full_topic, library.slugify(full_topic)
    except OSError:
        pass
    # Fallback when the raw file is gone: recover full slug from its filename stem.
    stem_match = _RAW_STEM_PREFIX.match(raw_path.stem)
    if stem_match:
        recovered_slug = library.slugify(stem_match.group(1))
        if recovered_slug:
            return "", recovered_slug
    return "", ""


def _looks_truncated_topic(topic: str) -> bool:
    stripped = topic.strip()
    return stripped.endswith("...") or stripped.endswith("…")


def _inject_topic_comment(content: str, badge_line: str, full_topic: str) -> str:
    if _TOPIC_META_COMMENT.search(content):
        return content
    safe_topic = full_topic.replace("-->", "- - >").replace("\n", " ").strip()
    if not safe_topic:
        return content
    marker = f"<!-- last30days-topic: {safe_topic} -->"
    if content.startswith(badge_line + "\n"):
        return badge_line + "\n" + marker + "\n" + content[len(badge_line) + 1 :]
    return marker + "\n" + content


def parse_badge_metadata(
    badge_line: str,
    body: str,
    *,
    fallback_date: date | None = None,
) -> tuple[str, str, date, str]:
    """Extract (topic, slug, published_date, version) from a brief badge and body."""
    version_match = _VERSION_TOKEN.search(badge_line)
    version = version_match.group(1) if version_match else ""

    parts = [part.strip() for part in badge_line.split("·") if part.strip()]
    badge_topic = ""
    published: date | None = None

    if synced := _SYNCED_DATE.search(badge_line):
        published = _safe_iso_date(synced.group(1))
    else:
        for segment in parts[1:]:
            if published is None:
                parsed = _parse_date_segment(segment)
                if parsed is not None:
                    published = parsed
                    continue
            if not badge_topic and not _is_meta_segment(segment):
                badge_topic = segment

    raw_topic, raw_slug = _topic_from_raw_footer(body)

    if raw_topic:
        topic = raw_topic
        slug = raw_slug or library.slugify(topic)
    elif badge_topic and not _looks_truncated_topic(badge_topic):
        topic = badge_topic
        slug = library.slugify(topic)
    elif badge_topic and _looks_truncated_topic(badge_topic):
        topic = badge_topic
        slug = raw_slug or library.slugify(topic)
    else:
        topic = _topic_from_body(body) or "last30days"
        slug = raw_slug or library.slugify(topic)

    if published is None:
        published = _parse_date_segment(body) or fallback_date or date.today()

    return topic, slug, published, version


def extract_finished_brief(
    text: str,
    *,
    fallback_date: date | None = None,
) -> CapturedBrief | None:
    """Extract a completed last30days brief from an assistant response, or None."""
    if not text or "All agents reported back!" not in text or "last30days" not in text:
        return None
    if any(marker in text for marker in _RAW_DUMP_MARKERS):
        return None

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    badge_match = _BADGE_LINE.search(normalized)
    if badge_match is None:
        return None

    footer_match = html_render.FENCED_ENGINE_FOOTER_PATTERN.search(
        normalized, pos=badge_match.end()
    )
    if footer_match is None:
        return None

    slice_text = normalized[badge_match.start() : footer_match.end()]
    slice_text = html_render.INVITATION_PATTERN.sub("", slice_text)
    final_content = normalize_brief_text(slice_text)

    badge_line = badge_match.group(0).strip()
    body_after_badge = final_content[len(badge_line) :]
    topic, slug, published, version = parse_badge_metadata(
        badge_line,
        body_after_badge,
        fallback_date=fallback_date,
    )

    # Carry the complete topic in an HTML comment whenever the badge truncated
    # or omitted it, so library scanning and raw-report supersession match the
    # full query identity instead of a 60-char prefix.
    badge_topic_only, _ = library._parse_badge_line(badge_line)
    if topic and topic != badge_topic_only:
        final_content = normalize_brief_text(
            _inject_topic_comment(final_content, badge_line, topic)
        )

    return CapturedBrief(
        topic=topic,
        slug=slug,
        published_date=published,
        version=version,
        content=final_content,
        content_hash=content_hash(final_content),
        is_private=has_private_corpus(final_content),
    )


@contextlib.contextmanager
def _finished_dir_lock(finished_dir: Path) -> Iterator[None]:
    """Serialize duplicate checks and exclusive filename allocation across processes."""
    lock_path = finished_dir / _LOCK_FILENAME
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if os.name == "posix":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def save_finished_brief(brief: CapturedBrief, save_dir: Path | str) -> SaveResult:
    """Exclusively write a finished brief into <save_dir>/finished/, deduping by hash."""
    base_dir = Path(save_dir).expanduser()
    finished_dir = base_dir / FINISHED_SUBDIR
    _ensure_finished_dir(finished_dir, private=brief.is_private)

    stem_prefix = f"{library.filename_slug(brief.slug)}-{brief.published_date.isoformat()}"
    candidates = [finished_dir / f"{stem_prefix}.md"]
    candidates.extend(finished_dir / f"{stem_prefix}-{idx}.md" for idx in range(2, 100))

    with _finished_dir_lock(finished_dir):
        for existing in candidates:
            if not existing.exists():
                continue
            try:
                existing_text = existing.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if content_hash(existing_text) == brief.content_hash:
                return SaveResult(status="duplicate", path=existing, brief=brief)

        for candidate in candidates:
            if _write_exclusive(candidate, brief.content, private=brief.is_private):
                return SaveResult(status="saved", path=candidate, brief=brief)

    raise RuntimeError(
        f"save_finished_brief: could not allocate unique filename for {stem_prefix} in {finished_dir}"
    )


def _ensure_finished_dir(path: Path, *, private: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if private and os.name == "posix":
        path.chmod(0o700)


def _write_exclusive(path: Path, content: str, *, private: bool) -> bool:
    """Write full content to a hidden temp file, then link to ``path`` without overwriting.

    Readers scanning ``*.md`` never observe a partial file, and concurrent
    writers cannot clobber an existing ``path`` because ``os.link`` fails with
    ``FileExistsError`` if the target already exists.
    """
    mode = 0o600 if private else 0o644
    staged_fd, staged_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    staged_path = Path(staged_name)
    try:
        if os.name == "posix":
            os.fchmod(staged_fd, mode)
        with os.fdopen(staged_fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
        try:
            os.link(staged_path, path)
            return True
        except FileExistsError:
            return False
        except OSError as exc:
            # Filesystems without hard-link support (e.g. some FUSE/FAT mounts):
            # caller already holds _finished_dir_lock, so re-check before rename.
            if exc.errno not in {
                errno.EPERM,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
                errno.EXDEV,
                errno.EINVAL,
            }:
                raise
            if path.exists():
                return False
            os.replace(staged_path, path)
            return True
    finally:
        staged_path.unlink(missing_ok=True)


def _is_meta_segment(segment: str) -> bool:
    lowered = segment.lower()
    if _VERSION_TOKEN.fullmatch(segment):
        return True
    if lowered in {"quick", "default", "deep", "cached"}:
        return True
    return _SYNCED_DATE.search(segment) is not None


def _topic_from_body(body: str) -> str:
    for match in _FIRST_HEADING.finditer(body):
        raw_heading = library._COMPARISON_TITLE_SUFFIX.sub("", match.group(1))
        heading = library._clean_inline(raw_heading)
        lowered = heading.lower()
        if not heading or lowered in {"what i learned", "key patterns", "from your files"}:
            continue
        if lowered.startswith("what i learned about "):
            return heading[len("what i learned about ") :].strip()
        return heading
    return ""


def _parse_date_segment(text: str) -> date | None:
    return library._parse_badge_date(text)


def _safe_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
