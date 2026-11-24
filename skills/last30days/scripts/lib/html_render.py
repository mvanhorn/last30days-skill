"""HTML rendering for shareable last30days reports."""

from __future__ import annotations

import html
import re
import secrets
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import date
from urllib.parse import urlsplit

from . import registers, render, schema
from .library import LibraryEntry, public_markdown


PROSE_LABELS = [
    ("What I learned:", "What I learned"),
    ("KEY PATTERNS from the research:", "Key patterns from the research"),
]

# The invitation closes a report, so it runs to the end of the text. Reports
# saved before the closing "Just ask." line was dropped stop at that line.
INVITATION_PATTERN = re.compile(
    r"^(?:---\nI'm now an expert|I've compared ).*?(?:Just ask\.$|\Z)",
    re.MULTILINE | re.DOTALL,
)
EVIDENCE_BLOCK_PATTERN = re.compile(r"<!-- EVIDENCE FOR SYNTHESIS.*?<!-- END EVIDENCE FOR SYNTHESIS -->", re.DOTALL)
PASS_THROUGH_FOOTER_PATTERN = re.compile(
    r"^<!-- PASS-THROUGH FOOTER[^\n]*-->[ \t]*\n(.*?)^<!-- END PASS-THROUGH FOOTER -->[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_PASS_THROUGH_FOOTER_START = re.compile(r"^<!-- PASS-THROUGH FOOTER[^\n]*-->[ \t]*$")
_PASS_THROUGH_FOOTER_END = re.compile(r"^<!-- END PASS-THROUGH FOOTER -->[ \t]*$")
_SYNTHESIS_PASS_THROUGH_MARKER = re.compile(
    r"<!--\s*(?:END\s+)?PASS-THROUGH FOOTER(?:(?!-->)[^\n])*?-->",
    re.IGNORECASE,
)
# Line-by-line on purpose: no row inside the match may itself open or close a
# fence, so an earlier ordinary ```text block cannot be stitched to the real
# footer with the prose between them swallowed into the footer box.
_FOOTER_ROWS = r"(?:(?!```)[^\n]*\n)*?"
FENCED_ENGINE_FOOTER_PATTERN = re.compile(
    r"^```text\n(---\n"
    + _FOOTER_ROWS
    + r"[^\n]*All agents reported back![^\n]*\n"
    + _FOOTER_ROWS
    + r"---)\n```$",
    re.MULTILINE,
)
CANONICAL_BOUNDARY_PATTERN = re.compile(r"\n?---\n# END OF last30days CANONICAL OUTPUT.*$", re.DOTALL)
# render_for_html emits metadata as <!-- META: ... --> so it survives the
# markdown converter (which escapes raw HTML inside paragraphs). Promoted to
# a styled <div class="meta"> at the block level.
META_MARKER_PATTERN = re.compile(r"<!--\s*META:((?:(?!-->).)*?)-->")
_STANDALONE_META_RAW_PATTERN = re.compile(r"<!--\s*META:((?:(?!-->).)*?)-->")
_STANDALONE_META_PROMOTE_PATTERNS = (
    re.compile(r"<p>\s*&lt;!--\s*META:((?:(?!--&gt;).)*?)--&gt;\s*</p>"),
    re.compile(r"&lt;!--\s*META:((?:(?!--&gt;).)*?)--&gt;"),
    re.compile(r"<p>\s*<!--\s*META:((?:(?!-->).)*?)-->\s*</p>"),
    re.compile(r"<!--\s*META:((?:(?!-->).)*?)-->"),
)
_OPEN_PRE_RE = re.compile(r"<pre(?:\s[^>]*)?>", re.IGNORECASE)
_CLOSE_PRE_RE = re.compile(r"</pre\s*>", re.IGNORECASE)
_OPEN_CODE_RE = re.compile(r"<code(?:\s[^>]*)?>", re.IGNORECASE)
_CLOSE_CODE_RE = re.compile(r"</code\s*>", re.IGNORECASE)
_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"__(?:TITLE|CSS|BODY|COLOPHON)__")

CSS = """
:root {
  --bg: #0e0e10;
  --bg-elev: #18181b;
  --fg: #fafafa;
  --fg-muted: #a1a1aa;
  --fg-subtle: #71717a;
  --accent: #a855f7;
  --accent-soft: #c4b5fd;
  --border: #27272a;
  --code-bg: #1a1a1d;
  --max-w: 720px;
}

@media (prefers-color-scheme: light) {
  :root {
    --bg: #ffffff;
    --bg-elev: #fafafa;
    --fg: #18181b;
    --fg-muted: #52525b;
    --fg-subtle: #71717a;
    --accent: #7c3aed;
    --accent-soft: #6d28d9;
    --border: #e4e4e7;
    --code-bg: #f4f4f5;
  }
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, system-ui, sans-serif;
  font-size: 17px;
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

body {
  max-width: var(--max-w);
  margin: 0 auto;
  padding: 4rem 1.5rem 6rem;
}

.badge {
  display: inline-block;
  padding: 0.4rem 0.85rem;
  margin-bottom: 2.5rem;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg-muted);
  letter-spacing: 0;
}

.badge .accent { color: var(--accent); }

.meta {
  margin: -1.5rem 0 2.5rem;
  color: var(--fg-subtle);
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
  font-size: 13px;
  letter-spacing: 0.01em;
}

h1 {
  margin: 0 0 1.5rem;
  color: var(--fg);
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: 0;
}

h2,
.prose-label {
  margin: 2.75rem 0 1.25rem;
  color: var(--fg);
  font-size: 20px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: 0;
}

.badge + h2,
.badge + .prose-label { margin-top: 0.5rem; }

h3 {
  margin: 2rem 0 0.85rem;
  color: var(--fg);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0;
}

p {
  margin: 0 0 1.4rem;
  color: var(--fg-muted);
}

p strong,
li strong,
td strong {
  color: var(--fg);
  font-weight: 600;
}

a {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s ease;
}

a:hover { border-bottom-color: var(--accent); }

ul,
ol {
  margin: 0 0 1.6rem;
  padding-left: 1.5rem;
  color: var(--fg-muted);
}

li {
  margin: 0.6rem 0;
  padding-left: 0.4rem;
}

li::marker {
  color: var(--accent);
  font-weight: 600;
}

blockquote {
  margin: 1.5rem 0;
  padding-left: 1rem;
  border-left: 3px solid var(--accent);
  color: var(--fg-muted);
}

hr {
  margin: 2.5rem 0;
  border: 0;
  border-top: 1px solid var(--border);
}

code {
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
  font-size: 0.92em;
  background: var(--code-bg);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  color: var(--accent-soft);
}

pre {
  margin: 1.4rem 0;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  overflow-x: auto;
  font-size: 14px;
  line-height: 1.6;
}

pre code {
  background: none;
  padding: 0;
  color: var(--fg);
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.5rem 0;
  font-size: 15px;
}

th,
td {
  text-align: left;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

th {
  color: var(--fg-muted);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0;
  text-transform: uppercase;
}

td { color: var(--fg-muted); }
td:first-child { color: var(--fg); font-weight: 500; }

.engine-footer {
  margin: 3rem 0 2.5rem;
  padding: 1.25rem 1.5rem;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--fg-muted);
}

.engine-footer pre {
  margin: 0;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
  font-size: 13.5px;
  font-weight: 400;
  line-height: 1.75;
  color: inherit;
  white-space: pre-wrap;
  word-break: break-word;
}

.colophon {
  margin-top: 4rem;
  padding-top: 2rem;
  border-top: 1px solid var(--border);
  color: var(--fg-subtle);
  font-size: 13px;
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
  line-height: 1.7;
}

.colophon .rerun {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  margin-left: 0.25rem;
  background: var(--code-bg);
  border-radius: 4px;
  color: var(--accent-soft);
  font-size: 0.95em;
}

.library-hero {
  padding: 1.5rem 0 2.5rem;
  border-bottom: 1px solid var(--border);
}

.library-hero h1 { margin-bottom: 0.65rem; }
.library-hero p { max-width: 42rem; color: var(--fg-muted); }
.library-hero .subscribe { font-weight: 700; }

.library-topic { margin-top: 3rem; }

.library-topic-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
}

.library-topic-heading h2 { margin-bottom: 0.65rem; }
.library-topic-heading a { font-size: 0.85rem; }

.library-entry {
  display: grid;
  grid-template-columns: 7rem minmax(0, 1fr);
  column-gap: 1.25rem;
  padding: 1.35rem 0;
  border-bottom: 1px solid var(--border);
}

.library-entry time {
  grid-row: 1 / span 2;
  color: var(--fg-subtle);
  font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.8rem;
}

.library-entry h3 { margin: 0 0 0.35rem; font-size: 1.1rem; }
.library-entry p { margin: 0; color: var(--fg-muted); }
.library-empty { padding: 3rem 0; }

@media print {
  :root {
    --bg: #ffffff;
    --bg-elev: #f5f5f5;
    --fg: #000000;
    --fg-muted: #1f2937;
    --fg-subtle: #4b5563;
    --accent: #6d28d9;
    --accent-soft: #6d28d9;
    --border: #d4d4d8;
    --code-bg: #f4f4f5;
  }

  @page { size: A4; margin: 1.5cm 2cm; }

  body {
    max-width: none;
    padding: 0;
    font-size: 11pt;
  }

  a {
    color: inherit;
    border-bottom: 0;
    text-decoration: underline;
  }

  a[href]::after {
    content: " (" attr(href) ")";
    font-size: 0.85em;
    color: var(--fg-subtle);
  }

  .engine-footer { page-break-inside: avoid; }
}

@media (max-width: 600px) {
  body {
    padding: 2.5rem 1.25rem 4rem;
    font-size: 16px;
  }

  h1 { font-size: 25px; }
  .badge { font-size: 12px; }
  th, td { padding: 0.65rem 0.5rem; }
  .library-entry { grid-template-columns: 1fr; }
  .library-entry time { grid-row: auto; margin-bottom: 0.5rem; }
}
""".strip()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>last30days · __TITLE__</title>
<style>
__CSS__
</style>
</head>
<body>
__BODY__
__COLOPHON__
</body>
</html>
"""


def render_html(
    report: schema.Report,
    *,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
    register: str = "default",
) -> str:
    prepared_synthesis, synthesis_bounds = _wrap_untrusted_synthesis(synthesis_md)
    md = render.render_for_html(
        report,
        synthesis_md=prepared_synthesis,
        save_path=save_path,
        fun_level=fun_level,
        register=register,
    )
    md = _strip_evidence_block(md)
    md = _strip_canonical_boundary(md)
    md = _promote_prose_labels(md)
    body = _markdown_to_html(md, untrusted_bounds=synthesis_bounds)
    body = _wrap_engine_footer(body)
    body = _promote_meta_marker(body)
    colophon = _build_colophon(report)
    return _wrap_in_template(body, colophon, report.topic)


def render_html_comparison(
    entity_reports: list[tuple[str, schema.Report]],
    *,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
) -> str:
    _ = fun_level
    prepared_synthesis, synthesis_bounds = _wrap_untrusted_synthesis(synthesis_md)
    md = render.render_for_html_comparison(
        entity_reports,
        synthesis_md=prepared_synthesis,
        save_path=save_path,
    )
    md = _strip_evidence_block(md)
    md = _strip_canonical_boundary(md)
    md = _promote_prose_labels(md)
    body = _markdown_to_html(md, untrusted_bounds=synthesis_bounds)
    body = _wrap_engine_footer(body)
    body = _promote_meta_marker(body)
    topic = " vs ".join(label for label, _ in entity_reports)
    colophon = _build_colophon(entity_reports[0][1], topic=topic)
    return _wrap_in_template(body, colophon, topic)


LIBRARY_BRIEF_MARKER = "<!-- generated by last30days library feed -->"


def render_library_brief(entry: LibraryEntry, *, include_private: bool = True) -> str:
    """Render a scanned Markdown or JSON briefing as a safe standalone page."""
    md = entry.content if include_private else public_markdown(entry.content)
    md = _strip_invitation(md)
    md = _strip_canonical_boundary(md)
    if entry.source_format == "finished-brief":
        md = _promote_prose_labels(md)
    body = _markdown_to_html(md)
    body = _wrap_engine_footer(body)
    colophon = (
        '<footer class="colophon">'
        f"Saved research · {html.escape(entry.published_date.isoformat())} · "
        f"{html.escape(entry.topic)}"
        "</footer>"
    )
    rendered = scrub_publishable_digit_runs(
        _wrap_in_template(body, colophon, entry.headline)
    )
    # Ownership marker consumed by the library-feed prune: a generated-looking
    # filename alone must never be grounds for deletion.
    head, sep, tail = rendered.rpartition("</body>")
    if sep:
        return f"{head}{LIBRARY_BRIEF_MARKER}\n{sep}{tail}"
    return rendered


def render_library_index(
    entries: Sequence[LibraryEntry],
    *,
    entry_urls: Mapping[str, str] | None = None,
    feed_url: str | None = "feed.xml",
) -> str:
    """Render the reverse-chronological, topic-grouped research index."""
    urls = entry_urls or {}
    grouped: OrderedDict[str, list[LibraryEntry]] = OrderedDict()
    for entry in entries:
        grouped.setdefault(entry.topic, []).append(entry)

    parts = [
        '<header class="library-hero">',
        '<span class="badge">RESEARCH LIBRARY</span>',
        '<h1>What the community is learning</h1>',
    ]
    safe_feed_url = _safe_library_href(feed_url) if feed_url is not None else None
    if safe_feed_url is None:
        parts.append('<p>Saved last30days briefs, newest first.</p>')
    else:
        parts.extend([
            '<p>Saved last30days briefs, newest first. Follow the Atom feed to keep up.</p>',
            f'<p><a class="subscribe" href="{safe_feed_url}">Subscribe via Atom</a></p>',
        ])
    parts.append('</header>')
    if not entries:
        parts.append('<section class="library-empty"><h2>No saved briefs yet</h2><p>Run last30days research and this library will fill itself.</p></section>')
    for topic, topic_entries in grouped.items():
        latest = topic_entries[0]
        latest_url = urls.get(latest.entry_id, f"briefs/{latest.output_name}")
        safe_latest_url = _safe_library_href(latest_url)
        parts.extend([
            '<section class="library-topic">',
            '<div class="library-topic-heading">',
            f'<h2>{html.escape(topic)}</h2>',
        ])
        if safe_latest_url is not None:
            parts.append(f'<a href="{safe_latest_url}">Latest</a>')
        parts.append('</div>')
        for entry in topic_entries:
            url = urls.get(entry.entry_id, f"briefs/{entry.output_name}")
            safe_url = _safe_library_href(url)
            headline_html = html.escape(entry.headline)
            if safe_url is not None:
                title_html = f'<h3><a href="{safe_url}">{headline_html}</a></h3>'
            else:
                title_html = f'<h3>{headline_html}</h3>'
            parts.extend([
                '<article class="library-entry">',
                f'<time datetime="{entry.published_date.isoformat()}">{entry.published_date.isoformat()}</time>',
                title_html,
                f'<p>{html.escape(entry.summary)}</p>',
                '</article>',
            ])
        parts.append('</section>')
    colophon = '<footer class="colophon">Generated locally by <strong>last30days</strong>.</footer>'
    rendered = _wrap_in_template("\n".join(parts), colophon, "Research library")
    return scrub_publishable_digit_runs(rendered)


_HREF_PATTERN = re.compile(r'(?P<prefix>\bhref\s*=\s*)(?P<quote>["\'])(?P<url>.*?)(?P=quote)', re.IGNORECASE)
_LONG_DIGIT_RUN = re.compile(r"\d{13,19}")


def scrub_publishable_digit_runs(html_content: str) -> str:
    """Defuse payment-card-shaped digit runs before hosted publishing.

    ht-ml.app rejects pages containing 13-19 digit runs during its safety scan.
    Social post IDs commonly have that shape. Link targets are percent-encoded
    so they still resolve; visible occurrences are shortened for readability.
    """
    def scrub_href(match: re.Match[str]) -> str:
        url = _LONG_DIGIT_RUN.sub(
            lambda digits: "".join(f"%{ord(char):02X}" for char in digits.group(0)),
            match.group("url"),
        )
        return f'{match.group("prefix")}{match.group("quote")}{url}{match.group("quote")}'

    with_safe_hrefs = _HREF_PATTERN.sub(scrub_href, html_content)
    return _LONG_DIGIT_RUN.sub(
        lambda digits: f"{digits.group(0)[:6]}…{digits.group(0)[-4:]}",
        with_safe_hrefs,
    )


def _strip_evidence_block(md: str) -> str:
    return EVIDENCE_BLOCK_PATTERN.sub("", md)


def _strip_invitation(md: str) -> str:
    return INVITATION_PATTERN.sub("", md)


def _strip_synthesis_invitation(synthesis_md: str | None) -> str | None:
    """Strip the invitation before the engine footer is appended after it."""
    if synthesis_md is None:
        return None
    return _strip_invitation(synthesis_md)


def _prepare_synthesis_md(synthesis_md: str | None) -> str | None:
    cleaned = _strip_synthesis_invitation(synthesis_md)
    if cleaned is None:
        return None
    cleaned = _strip_canonical_boundary(cleaned)
    while True:
        updated = _SYNTHESIS_PASS_THROUGH_MARKER.sub("", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned


def _wrap_untrusted_synthesis(
    synthesis_md: str | None,
) -> tuple[str | None, tuple[str, str] | None]:
    cleaned = _prepare_synthesis_md(synthesis_md)
    if cleaned is None or not cleaned.strip():
        return cleaned, None
    nonce = secrets.token_hex(16)
    start_marker = f"__LAST30DAYS_SYNTHESIS_START_{nonce}__"
    end_marker = f"__LAST30DAYS_SYNTHESIS_END_{nonce}__"
    return f"{start_marker}\n{cleaned.strip()}\n{end_marker}", (start_marker, end_marker)


def _strip_canonical_boundary(md: str) -> str:
    return CANONICAL_BOUNDARY_PATTERN.sub("", md)


def _promote_prose_labels(md: str) -> str:
    for source, normalized in PROSE_LABELS:
        md = re.sub(
            # The terminal report ends the label line with a `\` hard break.
            rf"^{re.escape(source)}[ \t]*\\?[ \t]*$",
            f"## {normalized}",
            md,
            flags=re.MULTILINE,
        )
    return md


def _strip_html_comments_outside_code(md: str) -> str:
    lines = md.splitlines(keepends=True)
    out: list[str] = []
    prose_chunk: list[str] = []
    in_code = False

    def flush_prose() -> None:
        if prose_chunk:
            joined = "".join(prose_chunk)
            out.append(re.sub(r"<!--(?!\s*META:).*?-->", "", joined, flags=re.DOTALL))
            prose_chunk.clear()

    for line in lines:
        if line.strip().startswith("```"):
            if not in_code:
                flush_prose()
                in_code = True
                out.append(line)
            else:
                out.append(line)
                in_code = False
            continue
        if in_code:
            out.append(line)
        else:
            prose_chunk.append(line)
    flush_prose()
    return "".join(out)


def _markdown_to_html(
    md: str,
    *,
    untrusted_bounds: tuple[str, str] | None = None,
) -> str:
    md, footers = _protect_engine_footers(md, untrusted_bounds=untrusted_bounds)
    global _ENGINE_FOOTER_STORE
    _ENGINE_FOOTER_STORE = {}
    md = _strip_html_comments_outside_code(md)
    lines = md.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None
    in_code = False
    code_lines: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(part.strip() for part in paragraph).strip()
            if text:
                out.append(f"<p>{_inline_markdown(text)}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if in_code:
            if stripped.startswith("```"):
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                code_lines.append(line)
            index += 1
            continue

        # A single trailing backslash is a markdown hard break: the Claude Code
        # terminal report ends each pseudo-list line but the last with one.
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            stripped = stripped[:-1].rstrip()
            line = stripped

        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            in_code = True
            code_lines = []
            index += 1
            continue

        if stripped in footers:
            flush_paragraph()
            close_list()
            footer = html.escape(footers[stripped], quote=False)
            out.append(f'<div class="engine-footer"><pre>{footer}</pre></div>')
            index += 1
            continue

        meta_match = _STANDALONE_META_RAW_PATTERN.fullmatch(stripped)
        if meta_match:
            flush_paragraph()
            close_list()
            meta_text = html.escape(html.unescape(meta_match.group(1).strip()))
            out.append(f'<div class="meta">{meta_text}</div>')
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            index += 1
            continue

        if stripped == "---":
            flush_paragraph()
            close_list()
            out.append("<hr>")
            index += 1
            continue

        if index + 1 < len(lines) and _is_table_row(stripped) and _is_table_separator(lines[index + 1].strip()):
            flush_paragraph()
            close_list()
            table_lines = [stripped]
            index += 2
            while index < len(lines) and _is_table_row(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            out.append(_render_table(table_lines))
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)), 3)
            out.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_list()
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote>{_inline_markdown(' '.join(quote_lines))}</blockquote>")
            continue

        # `•` and an escaped `1\.` are the list-free markers the skill writes
        # in chat on Claude Code; an export copies that text and wants lists.
        unordered = re.match(r"^[-*•]\s+(.+)$", stripped)
        ordered = re.match(r"^\d+(?:[.)]|\\\.)\s+(.+)$", stripped)
        if unordered or ordered:
            flush_paragraph()
            next_type = "ul" if unordered else "ol"
            if list_type != next_type:
                close_list()
                out.append(f"<{next_type}>")
                list_type = next_type
            item = unordered.group(1) if unordered else ordered.group(1)
            out.append(f"<li>{_inline_markdown(item)}</li>")
            index += 1
            continue

        if stripped.startswith("🌐 last30days"):
            flush_paragraph()
            close_list()
            badge_text = _inline_markdown(stripped.removeprefix("🌐").strip())
            out.append(f'<div class="badge"><span class="accent">🌐</span> {badge_text}</div>')
            index += 1
            continue

        paragraph.append(line)
        index += 1

    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    close_list()
    return "\n".join(out).strip()


def _protect_engine_footers(
    md: str,
    *,
    untrusted_bounds: tuple[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    footers: dict[str, str] = {}
    nonce = secrets.token_hex(16)
    lines = md.splitlines()
    out: list[str] = []
    index = 0
    in_code = False
    in_untrusted = False
    start_marker, end_marker = untrusted_bounds if untrusted_bounds is not None else (None, None)

    def next_token(content: str) -> str:
        token = f"__LAST30DAYS_ENGINE_FOOTER_{nonce}_{len(footers)}__"
        footers[token] = content.strip("\n")
        return token

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if start_marker is not None and stripped == start_marker:
            in_untrusted = True
            index += 1
            continue
        if end_marker is not None and stripped == end_marker:
            in_untrusted = False
            if in_code:
                # Close any unclosed code fence left inside untrusted synthesis
                # so it cannot swallow the real engine footer appended after it.
                out.append("```")
                in_code = False
            index += 1
            continue

        if in_untrusted:
            if stripped.startswith("```"):
                in_code = not in_code
            out.append(line)
            index += 1
            continue

        if in_code:
            out.append(line)
            if stripped.startswith("```"):
                in_code = False
            index += 1
            continue

        if stripped == "```text":
            end_index = index + 1
            while end_index < len(lines) and not lines[end_index].strip().startswith("```"):
                end_index += 1
            if end_index < len(lines) and lines[end_index].strip() == "```":
                block = "\n".join(lines[index : end_index + 1])
                match = FENCED_ENGINE_FOOTER_PATTERN.fullmatch(block)
                if match:
                    out.append("")
                    out.append(next_token(match.group(1)))
                    out.append("")
                    index = end_index + 1
                    continue
            out.append(line)
            in_code = True
            index += 1
            continue

        if stripped.startswith("```"):
            out.append(line)
            in_code = True
            index += 1
            continue

        if _PASS_THROUGH_FOOTER_START.fullmatch(line):
            end_index = index + 1
            while end_index < len(lines) and not _PASS_THROUGH_FOOTER_END.fullmatch(lines[end_index]):
                end_index += 1
            if end_index < len(lines):
                body_text = "\n".join(lines[index + 1 : end_index])
                if "All agents reported back!" in body_text:
                    out.append("")
                    out.append(next_token(body_text))
                    out.append("")
                    index = end_index + 1
                    continue

        out.append(line)
        index += 1

    return "\n".join(out), footers


def _update_pre_code_depth(line: str, pre_depth: int, code_depth: int) -> tuple[int, int]:
    pre_depth = max(0, pre_depth + len(_OPEN_PRE_RE.findall(line)) - len(_CLOSE_PRE_RE.findall(line)))
    code_depth = max(0, code_depth + len(_OPEN_CODE_RE.findall(line)) - len(_CLOSE_CODE_RE.findall(line)))
    return pre_depth, code_depth


def _wrap_engine_footer(body: str) -> str:
    if not _ENGINE_FOOTER_STORE:
        return body

    out: list[str] = []
    pre_depth = 0
    code_depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if pre_depth == 0 and code_depth == 0 and stripped in _ENGINE_FOOTER_STORE:
            footer = html.escape(_ENGINE_FOOTER_STORE[stripped], quote=False)
            out.append(f'<div class="engine-footer"><pre>{footer}</pre></div>')
            continue
        out.append(line)
        pre_depth, code_depth = _update_pre_code_depth(line, pre_depth, code_depth)
    return "\n".join(out)


def _promote_meta_marker(body: str) -> str:
    """Promote standalone ``<!-- META: ... -->`` markers into ``<div class="meta">``.

    Only standalone block-level lines outside ``<pre>``/``<code>`` elements are
    promoted so markers inside attributes (such as ``href="..."``), inline
    text, or code blocks can never break out of their HTML context.
    """
    out: list[str] = []
    pre_depth = 0
    code_depth = 0
    for line in body.splitlines():
        if pre_depth == 0 and code_depth == 0:
            stripped = line.strip()
            promoted: str | None = None
            for pattern in _STANDALONE_META_PROMOTE_PATTERNS:
                match = pattern.fullmatch(stripped)
                if match:
                    text = html.escape(html.unescape(match.group(1).strip()))
                    promoted = f'<div class="meta">{text}</div>'
                    break
            if promoted is not None:
                out.append(promoted)
                continue
        out.append(line)
        pre_depth, code_depth = _update_pre_code_depth(line, pre_depth, code_depth)
    return "\n".join(out)


_ENGINE_FOOTER_STORE: dict[str, str] = {}

# Schemes allowed in synthesized markdown links. The HTML artifact is opened in
# a browser, so a permissive link parser exposes a stored-XSS vector: a
# `[label](javascript:...)` or `[label](data:text/html,...)` link surviving the
# LLM synthesis would render as a clickable script payload in the saved file.
# Restrict link URLs to a small allowlist of schemes and accept relative URLs
# (no scheme, no `:` before the first `/` or `?`, and no protocol-relative `//`).
# Anything else is rendered as plain bracketed text so the label remains readable.
_SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_SAFE_LIBRARY_LINK_SCHEMES = frozenset({"http", "https"})
_INVALID_HOSTNAME_CHARS = frozenset(' "\'<>\\&;')


def _is_safe_url_candidate(
    url: str,
    allowed_schemes: frozenset[str],
) -> bool:
    # Reject any control characters (e.g. `java\x0Dscript:` or leading `\t`
    # stripped by browser URL parsers before scheme detection).
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    stripped = url.strip()
    if not stripped:
        return False
    # Reject protocol-relative (`//evil.example`) and backslash-led URLs (`\\`,
    # `\/`, `/\`) that browsers can resolve against an external host.
    if stripped.startswith(("//", "\\", "/\\")):
        return False
    # No scheme — relative URL or fragment. Allow.
    colon = stripped.find(":")
    if colon == -1:
        return True
    slash = stripped.find("/")
    question = stripped.find("?")
    hash_ = stripped.find("#")
    # The first `:` that comes after a `/`, `?`, or `#` is path/query/fragment,
    # not a scheme separator. e.g. `/path:with:colons` is a relative URL.
    earlier = [pos for pos in (slash, question, hash_) if 0 <= pos < colon]
    if earlier:
        return True
    scheme = stripped[:colon].lower()
    if scheme not in allowed_schemes:
        return False
    if scheme in {"http", "https"}:
        if stripped[colon : colon + 3] != "://":
            return False
        try:
            parsed = urlsplit(stripped)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError:
            return False
        if not hostname or any(ch in _INVALID_HOSTNAME_CHARS for ch in hostname):
            return False
    return True


def _is_safe_link_url(
    url: str,
    *,
    allowed_schemes: frozenset[str] = _SAFE_LINK_SCHEMES,
) -> bool:
    """Return True if a link URL is safe to render as an ``<a href>``.

    A URL is safe if it either:
    - has no scheme (relative URL, fragment, or path-only, excluding
      protocol-relative ``//`` and backslash-led paths), or
    - uses a scheme in ``allowed_schemes`` (default: http, https, mailto).

    The scheme check is case-insensitive and tolerant of surrounding ASCII
    spaces while rejecting all control characters.

    Precondition: ``url`` must already have been through ``html.escape`` (as it
    is in ``_inline_markdown`` and ``_safe_library_href``). Checks the escaped
    string as well as up to two rounds of ``html.unescape`` so entity-encoded
    schemes like ``javascript&#58;alert(1)`` or ``&#106;avascript:alert(1)``
    are also rejected even if a downstream consumer decodes entities.
    """
    if not isinstance(url, str):
        return False
    candidate = url
    for _ in range(3):
        if not _is_safe_url_candidate(candidate, allowed_schemes):
            return False
        decoded = html.unescape(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return True


def _safe_library_href(url: str) -> str | None:
    """Validate and HTML-escape a library index URL, or return None if unsafe."""
    if not isinstance(url, str):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return None
    stripped = url.strip()
    if not stripped:
        return None
    escaped = html.escape(stripped, quote=True)
    if not _is_safe_link_url(escaped, allowed_schemes=_SAFE_LIBRARY_LINK_SCHEMES):
        return None
    return escaped


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=True)
    nonce = secrets.token_hex(12)
    code_prefix = f"__CODE_{nonce}_"
    code_tokens: dict[str, str] = {}

    def code_replace(match: re.Match[str]) -> str:
        token = f"{code_prefix}{len(code_tokens)}__"
        code_tokens[token] = f"<code>{match.group(1)}</code>"
        return token

    escaped = re.sub(r"`([^`]+)`", code_replace, escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)

    def link_replace(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        # Never allow an inline code span inside a link destination to expand
        # into `<code>...</code>` inside `href="..."`.
        if code_prefix in url or not _is_safe_link_url(url):
            return f"[{label}]({url})"
        return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'

    escaped = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link_replace, escaped)
    if code_tokens:
        token_re = re.compile(rf"{re.escape(code_prefix)}\d+__")
        escaped = token_re.sub(
            lambda match: code_tokens.get(match.group(0), match.group(0)),
            escaped,
        )
    return escaped


def _is_table_row(line: str) -> bool:
    return "|" in line and len(_split_table_cells(line)) >= 2


def _is_table_separator(line: str) -> bool:
    cells = _split_table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_table(rows: list[str]) -> str:
    header = _split_table_cells(rows[0])
    body_rows = [_split_table_cells(row) for row in rows[1:]]
    out = ["<table>", "<thead>", "<tr>"]
    out.extend(f"<th>{_inline_markdown(cell)}</th>" for cell in header)
    out.extend(["</tr>", "</thead>", "<tbody>"])
    for row in body_rows:
        out.append("<tr>")
        out.extend(f"<td>{_inline_markdown(cell)}</td>" for cell in row)
        out.append("</tr>")
    out.extend(["</tbody>", "</table>"])
    return "\n".join(out)


def _build_colophon(report: schema.Report, *, topic: str | None = None) -> str:
    display_topic = topic or report.topic
    generated = html.escape(_generated_date(report))
    version = render._skill_version()
    escaped_topic = html.escape(display_topic)
    rerun = html.escape(f"/last30days {display_topic}")
    return (
        '<div class="colophon">\n'
        f"  Generated {generated} by /last30days v{html.escape(version)} · topic: {escaped_topic}<br>\n"
        f'  Re-run for fresh data: <span class="rerun">{rerun}</span>\n'
        "</div>"
    )


def _generated_date(report: schema.Report) -> str:
    if report.generated_at:
        return report.generated_at[:10]
    return date.today().strftime("%Y-%m-%d")


def _wrap_in_template(body: str, colophon: str, title: str) -> str:
    replacements = {
        "__TITLE__": html.escape(title),
        "__CSS__": CSS,
        "__BODY__": body,
        "__COLOPHON__": colophon,
    }
    return _TEMPLATE_PLACEHOLDER_PATTERN.sub(
        lambda match: replacements[match.group(0)],
        HTML_TEMPLATE,
    )
