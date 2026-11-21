"""Detect, normalize, and atomically persist finished last30days chat briefs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import html_render, library, private_files


FINISHED_SUBDIR = "finished"

_BADGE_LINE = library._BADGE_LINE
_RAW_DUMP_MARKERS = (
    "<!-- EVIDENCE FOR SYNTHESIS",
    "# END OF last30days CANONICAL OUTPUT",
)
_SYNCED_DATE = library._BADGE_SYNCED_DATE
_VERSION_TOKEN = re.compile(r"\bv(\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9._-]+)?)\b")
_FIRST_HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.MULTILINE)
_CORPUS_FOOTER_LINE = re.compile(r"\bYour files\b", re.IGNORECASE)


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
    return bool(
        library.PRIVATE_CORPUS_START in markdown
        or library._LEGACY_CORPUS_SIGNAL.search(markdown)
        or library._PRIVATE_FRESHNESS_ROW.search(markdown)
        or "corpus://" in markdown
        or _CORPUS_FOOTER_LINE.search(markdown)
    )


def normalize_brief_text(content: str) -> str:
    """Normalize line endings and trailing whitespace for stable hashing and storage."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized + "\n"


def content_hash(content: str) -> str:
    """Return SHA-256 hex digest of normalized brief content."""
    return hashlib.sha256(normalize_brief_text(content).encode("utf-8")).hexdigest()


def parse_badge_metadata(
    badge_line: str,
    body: str,
    *,
    fallback_date: date | None = None,
) -> tuple[str, date, str]:
    """Extract (topic, published_date, version) from a brief badge and body."""
    version_match = _VERSION_TOKEN.search(badge_line)
    version = version_match.group(1) if version_match else ""

    parts = [part.strip() for part in badge_line.split("·") if part.strip()]
    topic = ""
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
            if not topic and not _is_meta_segment(segment):
                topic = segment

    if not topic:
        topic = _topic_from_body(body) or "last30days"

    if published is None:
        published = _parse_date_segment(body) or fallback_date or date.today()

    return topic, published, version


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
    topic, published, version = parse_badge_metadata(
        badge_line,
        body_after_badge,
        fallback_date=fallback_date,
    )
    return CapturedBrief(
        topic=topic,
        slug=library.slugify(topic),
        published_date=published,
        version=version,
        content=final_content,
        content_hash=content_hash(final_content),
        is_private=has_private_corpus(final_content),
    )


def save_finished_brief(brief: CapturedBrief, save_dir: Path | str) -> SaveResult:
    """Atomically write a finished brief into <save_dir>/finished/, deduping by hash."""
    base_dir = Path(save_dir).expanduser()
    finished_dir = base_dir / FINISHED_SUBDIR
    _ensure_finished_dir(finished_dir, private=brief.is_private)

    stem_prefix = f"{brief.slug}-{brief.published_date.isoformat()}"
    candidates = [finished_dir / f"{stem_prefix}.md"]
    candidates.extend(finished_dir / f"{stem_prefix}-{idx}.md" for idx in range(2, 100))

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
        if candidate.exists():
            continue
        _write_atomic(candidate, brief.content, private=brief.is_private)
        return SaveResult(status="saved", path=candidate, brief=brief)

    raise RuntimeError(
        f"save_finished_brief: could not allocate unique filename for {stem_prefix} in {finished_dir}"
    )


def _ensure_finished_dir(path: Path, *, private: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if private and os.name == "posix":
        path.chmod(0o700)


def _write_atomic(path: Path, content: str, *, private: bool) -> None:
    if private:
        private_files.replace_private_text(path, content)
        return
    staged = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    try:
        with staged:
            staged.write(content)
        if os.name == "posix":
            os.chmod(staged.name, 0o644)
        os.replace(staged.name, path)
    finally:
        Path(staged.name).unlink(missing_ok=True)


def _is_meta_segment(segment: str) -> bool:
    lowered = segment.lower()
    if _VERSION_TOKEN.fullmatch(segment):
        return True
    if lowered in {"quick", "default", "deep", "cached"}:
        return True
    return _SYNCED_DATE.search(segment) is not None


def _topic_from_body(body: str) -> str:
    for match in _FIRST_HEADING.finditer(body):
        heading = library._clean_inline(match.group(1))
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
