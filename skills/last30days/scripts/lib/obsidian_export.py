"""Obsidian-native export for durable research notes and briefings.

This module is intentionally additive: it does not change upstream retrieval,
clustering, or existing emit modes. It turns a finished ``schema.Report`` into
a vault-safe Markdown note with YAML frontmatter, wikilinks to related prior
runs, a source index, and a short briefing surface.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from . import render, schema, skill_meta

DEFAULT_RELATIVE_RUNS_DIR = "90_Quellen/obsidian2date/runs"
DEFAULT_RELATIVE_BRIEFINGS_DIR = "90_Quellen/obsidian2date/briefings"
DEFAULT_RELATIVE_INDEX = "90_Quellen/obsidian2date/Index.md"
DEFAULT_RELATIVE_DASHBOARD = "90_Quellen/obsidian2date/Dashboard.md"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WIKILINK_SAFE_RE = re.compile(r"[\[\]|#^]")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "over",
        "under",
        "users",
        "user",
        "ai",
        "ux",
        "app",
        "web",
        "website",
        "building",
        "build",
        "framework",
        "frameworks",
        "approachable",
        "product",
        "onboarding",
        "non",
        "technical",
        "last",
        "days",
        "run",
        "briefing",
        "smoke",
        "test",
        "obsidian2date",
        "obsidian",
    }
)


@dataclass(frozen=True)
class ObsidianPaths:
    """Resolved vault layout for one export."""

    vault_root: Path
    runs_dir: Path
    briefings_dir: Path
    index_path: Path
    dashboard_path: Path


@dataclass(frozen=True)
class RelatedNote:
    """A prior vault note that looks topically related."""

    title: str
    path: Path
    score: float
    reason: str


@dataclass(frozen=True)
class ObsidianExportResult:
    """Artifacts written for one research run."""

    run_note: Path
    briefing_note: Path
    index_path: Path
    dashboard_path: Path
    related: list[RelatedNote]


def _skill_version() -> str:
    try:
        version = render._skill_version()  # type: ignore[attr-defined]
        if version:
            return str(version)
    except Exception:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        skill_md = parent / "SKILL.md"
        if skill_md.is_file():
            version = skill_meta.read_skill_version(skill_md)
            if version:
                return str(version)
    return "?"


def slugify_topic(topic: str) -> str:
    """Lowercase slug safe for filenames and Obsidian note stems."""
    cleaned = _SLUG_RE.sub("-", (topic or "").strip().lower()).strip("-")
    return cleaned or "topic"


def wikilink_title(title: str) -> str:
    """Strip characters that break Obsidian wikilinks."""
    return _WIKILINK_SAFE_RE.sub("", (title or "").strip()) or "untitled"


def resolve_vault_root(
    explicit: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve the Obsidian vault root.

    Order:
    1. explicit path
    2. OBSIDIAN2DATE_VAULT / LAST30DAYS_OBSIDIAN_VAULT
    3. ~/Desktop/brain-paul when present
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()

    env_map: dict[str, str] = dict(env) if env is not None else dict(os.environ)
    candidates: list[Path] = []
    for key in ("OBSIDIAN2DATE_VAULT", "LAST30DAYS_OBSIDIAN_VAULT"):
        value = env_map.get(key)
        if value is not None:
            # A present blank value deliberately disables implicit vault
            # discovery instead of falling through to a lower-priority key or
            # the desktop fallback.
            if not value.strip():
                raise FileNotFoundError(
                    "No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT."
                )
            candidates.append(Path(value).expanduser())
    candidates.append(Path.home() / "Desktop" / "brain-paul")

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir():
            return resolved
    raise FileNotFoundError(
        "No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT."
    )


def resolve_paths(
    vault_root: Path,
    *,
    runs_rel: str = DEFAULT_RELATIVE_RUNS_DIR,
    briefings_rel: str = DEFAULT_RELATIVE_BRIEFINGS_DIR,
    index_rel: str = DEFAULT_RELATIVE_INDEX,
    dashboard_rel: str = DEFAULT_RELATIVE_DASHBOARD,
) -> ObsidianPaths:
    root = vault_root.expanduser().resolve()
    return ObsidianPaths(
        vault_root=root,
        runs_dir=(root / runs_rel).resolve(),
        briefings_dir=(root / briefings_rel).resolve(),
        index_path=(root / index_rel).resolve(),
        dashboard_path=(root / dashboard_rel).resolve(),
    )


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.search(r'[:#\[\]{},&*!|>\'"%@`]|\s', text) or text.lower() in {
        "true",
        "false",
        "null",
        "yes",
        "no",
    }:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _yaml_list(values: Iterable[Any], *, indent: int = 0) -> list[str]:
    pad = " " * indent
    items = list(values)
    if not items:
        return [f"{pad}[]"]
    return [f"{pad}- {_yaml_scalar(item)}" for item in items]


def _source_label(source: str) -> str:
    labels = {
        "reddit": "Reddit",
        "x": "X",
        "youtube": "YouTube",
        "hackernews": "Hacker News",
        "hn": "Hacker News",
        "polymarket": "Polymarket",
        "github": "GitHub",
        "web": "Web",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "bluesky": "Bluesky",
        "linkedin": "LinkedIn",
        "arxiv": "arXiv",
    }
    return labels.get(source, source.replace("_", " ").title())


def _active_sources(report: schema.Report) -> list[str]:
    return sorted(source for source, items in report.items_by_source.items() if items)


def _candidate_primary(candidate: schema.Candidate) -> schema.SourceItem | None:
    if candidate.source_items:
        return candidate.source_items[0]
    return None


def _engagement_total(candidate: schema.Candidate) -> float:
    primary = _candidate_primary(candidate)
    if primary and primary.engagement:
        return float(sum(float(v) for v in primary.engagement.values() if isinstance(v, (int, float))))
    if isinstance(candidate.engagement, (int, float)):
        return float(candidate.engagement)
    return 0.0


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())


def _is_title_echo(text: str, title: str) -> bool:
    return bool(title and text.casefold() == _clean_text(title).casefold())


def _is_synthetic_blurb(text: str) -> bool:
    return text.casefold().startswith("hn story about ")


def _item_blurb(item: schema.SourceItem | None) -> str:
    if item is None:
        return ""
    for raw in (item.snippet, item.body, item.why_relevant, item.title):
        text = _clean_text(str(raw or ""))
        if not text:
            continue
        # Skip pure title echoes and connector-generated HN fallbacks.
        if _is_title_echo(text, item.title or ""):
            continue
        if _is_synthetic_blurb(text):
            continue
        return text
    return ""


def _candidate_blurb(candidate: schema.Candidate) -> str:
    primary = _candidate_primary(candidate)
    blurb = _item_blurb(primary)
    if blurb:
        return blurb
    for item in candidate.source_items or []:
        blurb = _item_blurb(item)
        if blurb:
            return blurb
    return ""


def _cluster_summary(report: schema.Report, cluster: schema.Cluster) -> str:
    by_id = {c.candidate_id: c for c in report.ranked_candidates}
    blurbs: list[str] = []
    for candidate_id in list(cluster.representative_ids) + list(cluster.candidate_ids):
        candidate = by_id.get(candidate_id)
        if not candidate:
            continue
        blurb = _candidate_blurb(candidate)
        if not blurb:
            continue
        if _is_title_echo(blurb, cluster.title):
            continue
        if blurb not in blurbs:
            blurbs.append(blurb)
        if len(blurbs) >= 2:
            break
    if blurbs:
        joined = " · ".join(blurbs)
        return joined if len(joined) <= 360 else joined[:357].rstrip() + "..."
    sources = ", ".join(_source_label(s) for s in cluster.sources) or "mixed sources"
    return f"Cluster across {sources}; open the evidence index for links."


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _SLUG_RE.split((text or "").lower())
        if len(token) >= 4 and token not in _STOPWORDS
    }


def find_related_notes(
    topic: str,
    runs_dir: Path,
    *,
    limit: int = 5,
    exclude: Path | None = None,
) -> list[RelatedNote]:
    """Find prior run notes by token overlap on filename/title."""
    if not runs_dir.is_dir():
        return []
    topic_tokens = _tokens(topic)
    if not topic_tokens:
        return []
    scored: list[RelatedNote] = []
    exclude_resolved = exclude.resolve() if exclude else None
    for path in sorted(runs_dir.glob("*.md")):
        if exclude_resolved and path.resolve() == exclude_resolved:
            continue
        if path.name.lower() in {"index.md", "dashboard.md", "readme.md"}:
            continue
        stem_tokens = _tokens(path.stem)
        title = path.stem
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:1200]
        except OSError:
            head = ""
        title_match = re.search(r"^title:\s*(.+)$", head, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip().strip('"').strip("'")
        overlap = topic_tokens & (stem_tokens | _tokens(title) | _tokens(head))
        if not overlap:
            continue
        score = len(overlap) / max(len(topic_tokens), 1)
        scored.append(
            RelatedNote(
                title=title,
                path=path,
                score=score,
                reason=f"shared tokens: {', '.join(sorted(overlap)[:6])}",
            )
        )
    scored.sort(key=lambda item: (-item.score, item.path.name))
    return scored[:limit]


def unique_note_path(directory: Path, stem: str, extension: str = ".md") -> Path:
    """Allocate a collision-safe path without overwriting existing notes."""
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"{stem}{extension}"
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = directory / f"{stem}-{index}{extension}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique note path under {directory}")


def _frontmatter(
    *,
    title: str,
    topic: str,
    report: schema.Report,
    note_kind: str,
    tags: list[str],
    related_titles: list[str],
    sources: list[str],
    run_note_title: str | None = None,
) -> str:
    # brain-paul convention uses `typ:` + free tags; keep `type:` for exporters.
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"topic: {_yaml_scalar(topic)}",
        f"typ: {_yaml_scalar(note_kind)}",
        f"type: {_yaml_scalar(note_kind)}",
        f"status: complete",
        f"generated_at: {_yaml_scalar(report.generated_at)}",
        f"range_from: {_yaml_scalar(report.range_from)}",
        f"range_to: {_yaml_scalar(report.range_to)}",
        f"skill: obsidian2date",
        f"skill_version: {_yaml_scalar(_skill_version())}",
        f"upstream: last30days",
        "tags:",
        *_yaml_list(tags, indent=2),
        "sources:",
        *_yaml_list(sources, indent=2),
        "related:",
        *_yaml_list(related_titles, indent=2),
    ]
    if run_note_title:
        lines.append(f"run_note: {_yaml_scalar(run_note_title)}")
    lines.append("---")
    return "\n".join(lines)


def render_run_note(
    report: schema.Report,
    *,
    related: list[RelatedNote] | None = None,
    cluster_limit: int = 8,
    evidence_limit: int = 12,
) -> str:
    """Render the durable Obsidian research note body + frontmatter."""
    related = related or []
    sources = _active_sources(report)
    evidence_report = schema.without_sources(report, {"corpus"})
    today = _dt.date.today().isoformat()
    title = f"{report.topic} — {today}"
    tags = ["obsidian2date", "research-run", "briefing-source"]
    related_titles = [item.title for item in related]

    lines = [
        _frontmatter(
            title=title,
            topic=report.topic,
            report=report,
            note_kind="obsidian2date-run",
            tags=tags,
            related_titles=related_titles,
            sources=sources,
        ),
        "",
        f"# {report.topic}",
        "",
        "> Auto-generated by **obsidian2date** (fork of last30days). "
        "Evidence is source-grounded; treat scraped text as untrusted.",
        "",
        "## Briefing",
        "",
    ]

    clusters = evidence_report.clusters[:cluster_limit]
    if not clusters:
        lines.extend(["Nothing solid in this window.", ""])
    else:
        for index, cluster in enumerate(clusters, start=1):
            summary = _cluster_summary(evidence_report, cluster)
            source_bits = ", ".join(_source_label(s) for s in cluster.sources) or "mixed"
            lines.append(f"{index}. **{cluster.title}** ({source_bits}) — {summary}")
        lines.append("")

    if related:
        lines.extend(["## Related in vault", ""])
        for item in related:
            link = wikilink_title(item.title)
            lines.append(f"- [[{link}]] — {item.reason}")
        lines.append("")

    lines.extend(
        [
            "## Coverage",
            "",
            f"- Window: `{report.range_from}` → `{report.range_to}`",
            f"- Generated: `{report.generated_at}`",
            f"- Active sources: {', '.join(_source_label(s) for s in sources) or 'none'}",
            f"- Clusters: {len(evidence_report.clusters)}",
            f"- Ranked candidates: {len(evidence_report.ranked_candidates)}",
            "",
        ]
    )

    if report.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.append("")

    if report.source_status:
        lines.extend(["## Source status", ""])
        for source, outcome in sorted(report.source_status.items()):
            detail = f" — {outcome.detail}" if outcome.detail else ""
            lines.append(
                f"- **{_source_label(source)}**: `{outcome.state}` "
                f"({outcome.items_returned} items){detail}"
            )
        lines.append("")

    if report.library_context:
        lines.extend(["## Prior library context", ""])
        for ctx in report.library_context:
            lines.append(
                f"- **{ctx.topic}** ({ctx.published_date}, {ctx.source_kind}): "
                f"{ctx.headline} — {ctx.summary}"
            )
        lines.append("")

    lines.extend(["## Ranked storylines", ""])
    if not clusters:
        lines.extend(["_No clusters above the relevance floor._", ""])
    for index, cluster in enumerate(clusters, start=1):
        lines.append(f"### {index}. {cluster.title}")
        lines.append("")
        lines.append(f"- Sources: {', '.join(_source_label(s) for s in cluster.sources) or 'n/a'}")
        if cluster.uncertainty:
            lines.append(f"- Uncertainty: `{cluster.uncertainty}`")
        lines.append(f"- Summary: {_cluster_summary(evidence_report, cluster)}")
        lines.append("")

    lines.extend(["## Evidence index", ""])
    candidates = evidence_report.ranked_candidates[:evidence_limit]
    if not candidates:
        lines.extend(["_No ranked candidates._", ""])
    for index, candidate in enumerate(candidates, start=1):
        host = urlparse(candidate.url or "").netloc or candidate.source
        engagement = _engagement_total(candidate)
        eng = f", engagement={engagement:g}" if engagement else ""
        lines.append(
            f"{index}. [{candidate.title or candidate.url or candidate.candidate_id}]({candidate.url}) "
            f"— {_source_label(candidate.source)} ({host}{eng})"
        )
        blurb = _candidate_blurb(candidate)
        title = _clean_text(candidate.title or "")
        if blurb and not _is_title_echo(blurb, title):
            lines.append(f"   - {blurb[:280]}")
    lines.append("")

    lines.extend(
        [
            "## How to use",
            "",
            "- Link this note from project pages with `[[...]]`.",
            "- Prefer the briefing note for a daily skim; keep this run note as the durable source record.",
            "- Re-run with the same topic to append a new dated note; prior notes stay linked under Related.",
            "",
            f"_obsidian2date v{_skill_version()} · upstream last30days_",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _builder_takeaway(report: schema.Report, clusters: list[schema.Cluster]) -> str:
    """One short paragraph a builder can act on; honest when evidence is thin."""
    if not clusters:
        return (
            "Evidence is thin this window — treat conclusions as hypotheses, "
            "widen sources or re-run with --deep before locking product decisions."
        )
    top = clusters[0]
    extras = [c.title for c in clusters[1:3]]
    extra_bit = f" Watch also: {', '.join(extras)}." if extras else ""
    return (
        f"Strongest signal right now: **{top.title}** — {_cluster_summary(report, top)}."
        f"{extra_bit} Prefer patterns that show up in ≥2 sources; discard single-thread hype."
    )


def render_briefing_note(
    report: schema.Report,
    *,
    run_note_title: str,
    related: list[RelatedNote] | None = None,
    cluster_limit: int = 5,
) -> str:
    """Render a short briefing note meant for daily reading."""
    related = related or []
    sources = _active_sources(report)
    evidence_report = schema.without_sources(report, {"corpus"})
    today = _dt.date.today().isoformat()
    title = f"Briefing: {report.topic} — {today}"
    tags = ["obsidian2date", "briefing", "research-run"]
    related_titles = [run_note_title, *[item.title for item in related]]

    lines = [
        _frontmatter(
            title=title,
            topic=report.topic,
            report=report,
            note_kind="obsidian2date-briefing",
            tags=tags,
            related_titles=related_titles,
            sources=sources,
            run_note_title=run_note_title,
        ),
        "",
        f"# Briefing: {report.topic}",
        "",
        f"Full run: [[{wikilink_title(run_note_title)}]]",
        "",
        "## What matters now",
        "",
    ]
    clusters = evidence_report.clusters[:cluster_limit]
    if not clusters:
        lines.append("- Nothing solid in this window.")
    else:
        for cluster in clusters:
            src = ", ".join(_source_label(s) for s in cluster.sources) or "mixed"
            summary = _cluster_summary(evidence_report, cluster)
            if summary.casefold() == cluster.title.casefold():
                summary = "Evidence is title-only; open the evidence index before drawing conclusions."
            lines.append(f"- **{cluster.title}** ({src}) — {summary}")
    lines.append("")
    lines.extend(
        [
            "## Builder takeaway",
            "",
            _builder_takeaway(evidence_report, clusters),
            "",
        ]
    )

    if related:
        lines.extend(["## Related vault notes", ""])
        for item in related:
            lines.append(f"- [[{wikilink_title(item.title)}]]")
        lines.append("")

    thin = len(clusters) < 2 or len(sources) < 2
    gaps: list[str] = []
    if thin:
        gaps.append("Cross-source corroboration is weak — verify before shipping." )
    if report.warnings:
        gaps.extend(str(w) for w in report.warnings[:4])
    missing = [
        name
        for name, outcome in sorted(report.source_status.items())
        if outcome.state not in {"ok", "no-results"} and outcome.attempted
    ]
    if missing:
        gaps.append(
            "Source issues: "
            + ", ".join(f"{_source_label(name)}" for name in missing[:6])
        )
    if gaps:
        lines.extend(["## Gaps / honesty", ""])
        lines.extend(f"- {g}" for g in gaps)
        lines.append("")

    lines.extend(
        [
            "## Coverage snapshot",
            "",
            f"- Window `{report.range_from}` → `{report.range_to}`",
            f"- Sources: {', '.join(_source_label(s) for s in sources) or 'none'}",
            f"- Clusters: {len(evidence_report.clusters)} · candidates: {len(evidence_report.ranked_candidates)}",
            f"- Generated `{report.generated_at}`",
            "",
            f"_obsidian2date briefing · v{_skill_version()}_",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _ensure_index(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                'title: "obsidian2date Index"',
                "type: obsidian2date-index",
                "tags:",
                "  - obsidian2date",
                "  - index",
                "---",
                "",
                "# obsidian2date Index",
                "",
                "Auto-maintained list of research runs. Newest first.",
                "",
                "## Runs",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _ensure_dashboard(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                'title: "obsidian2date Dashboard"',
                "type: obsidian2date-dashboard",
                "tags:",
                "  - obsidian2date",
                "  - dashboard",
                "---",
                "",
                "# obsidian2date Dashboard",
                "",
                "Latest research briefings for quick scanning.",
                "",
                "## Latest briefings",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _prepend_list_item(path: Path, heading: str, item_line: str, *, max_items: int = 100) -> None:
    text = path.read_text(encoding="utf-8")
    if item_line in text:
        return
    marker = f"## {heading}"
    if marker not in text:
        text = text.rstrip() + f"\n\n{marker}\n\n"
    head, tail = text.split(marker, 1)
    # Keep only the list under this heading (until next ## or EOF).
    lines = tail.splitlines()
    body_lines: list[str] = []
    rest_lines: list[str] = []
    seen_body = False
    for line in lines:
        if not seen_body:
            body_lines.append(line)
            if line.startswith("- "):
                seen_body = True
            continue
        if line.startswith("## "):
            rest_lines.append(line)
            rest_lines.extend(lines[lines.index(line) + 1 :])
            break
        body_lines.append(line)
    else:
        rest_lines = []

    # Rebuild list: marker line + blank + new item + existing items.
    existing_items = [line for line in body_lines if line.startswith("- ")]
    prefix = [line for line in body_lines if not line.startswith("- ")]
    # prefix usually starts with '' after the heading split.
    new_items = [item_line, *existing_items]
    new_items = new_items[:max_items]
    rebuilt = marker + "\n".join(prefix)
    if not rebuilt.endswith("\n"):
        rebuilt += "\n"
    if prefix and prefix[-1].strip() != "":
        rebuilt += "\n"
    rebuilt += "\n".join(new_items) + "\n"
    if rest_lines:
        rebuilt += "\n" + "\n".join(rest_lines).lstrip("\n")
    path.write_text(head + rebuilt, encoding="utf-8")


def update_index(index_path: Path, *, run_title: str, topic: str, date_str: str) -> None:
    _ensure_index(index_path)
    link = wikilink_title(run_title)
    item = f"- {date_str} · [[{link}]] — {topic}"
    _prepend_list_item(index_path, "Runs", item)


def update_dashboard(
    dashboard_path: Path,
    *,
    briefing_title: str,
    topic: str,
    date_str: str,
) -> None:
    _ensure_dashboard(dashboard_path)
    link = wikilink_title(briefing_title)
    item = f"- {date_str} · [[{link}]] — {topic}"
    _prepend_list_item(dashboard_path, "Latest briefings", item)


def export_report_to_obsidian(
    report: schema.Report,
    *,
    vault_root: str | Path | None = None,
    paths: ObsidianPaths | None = None,
    env: dict[str, str] | None = None,
    related_limit: int = 5,
) -> ObsidianExportResult:
    """Write run note + briefing and update index/dashboard."""
    if paths is None:
        root = resolve_vault_root(vault_root, env=env)
        paths = resolve_paths(root)

    today = _dt.date.today().isoformat()
    slug = slugify_topic(report.topic)
    related = find_related_notes(report.topic, paths.runs_dir, limit=related_limit)

    run_stem = f"{today}-{slug}"
    run_path = unique_note_path(paths.runs_dir, run_stem)
    run_title = f"{report.topic} — {today}"
    if run_path.stem != run_stem:
        # Collision suffix — keep title unique for wikilinks.
        suffix = run_path.stem[len(run_stem) :]
        run_title = f"{run_title}{suffix}"

    run_body = render_run_note(report, related=related)
    # Re-render frontmatter title if collision renamed the note.
    if f'title: "{report.topic} — {today}"' in run_body or f"title: {report.topic} — {today}" in run_body:
        run_body = run_body.replace(
            f"{report.topic} — {today}",
            run_title,
            2,
        )
    run_path.write_text(run_body, encoding="utf-8")

    # Related notes should not include the note we just wrote.
    related = find_related_notes(
        report.topic,
        paths.runs_dir,
        limit=related_limit,
        exclude=run_path,
    )
    # Patch related section if we discovered more after write (first pass had none).
    if related and "## Related in vault" not in run_body:
        run_body = render_run_note(report, related=related)
        run_body = run_body.replace(
            f"{report.topic} — {today}",
            run_title,
            2,
        )
        run_path.write_text(run_body, encoding="utf-8")

    briefing_stem = f"{today}-{slug}-briefing"
    briefing_path = unique_note_path(paths.briefings_dir, briefing_stem)
    briefing_title = f"Briefing: {report.topic} — {today}"
    if briefing_path.stem != briefing_stem:
        suffix = briefing_path.stem[len(briefing_stem) :]
        briefing_title = f"{briefing_title}{suffix}"

    briefing_body = render_briefing_note(
        report,
        run_note_title=run_title,
        related=related,
    )
    if briefing_title != f"Briefing: {report.topic} — {today}":
        briefing_body = briefing_body.replace(
            f"Briefing: {report.topic} — {today}",
            briefing_title,
            2,
        )
    briefing_path.write_text(briefing_body, encoding="utf-8")

    update_index(paths.index_path, run_title=run_title, topic=report.topic, date_str=today)
    update_dashboard(
        paths.dashboard_path,
        briefing_title=briefing_title,
        topic=report.topic,
        date_str=today,
    )

    return ObsidianExportResult(
        run_note=run_path,
        briefing_note=briefing_path,
        index_path=paths.index_path,
        dashboard_path=paths.dashboard_path,
        related=related,
    )


def render_obsidian_stdout(result: ObsidianExportResult, report: schema.Report) -> str:
    """Human-readable summary printed to stdout after export."""
    lines = [
        f"obsidian2date export complete: {report.topic}",
        f"- run: {result.run_note}",
        f"- briefing: {result.briefing_note}",
        f"- index: {result.index_path}",
        f"- dashboard: {result.dashboard_path}",
    ]
    if result.related:
        lines.append("- related:")
        for item in result.related:
            lines.append(f"  - [[{wikilink_title(item.title)}]] ({item.reason})")
    else:
        lines.append("- related: none yet")
    return "\n".join(lines) + "\n"
