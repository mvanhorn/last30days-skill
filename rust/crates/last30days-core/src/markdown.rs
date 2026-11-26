//! Privacy redaction, context stripping, and parallel batch fingerprinting for
//! saved last30days Markdown reports.
//!
//! Mirrors the hot paths of `skills/last30days/scripts/lib/library.py` and
//! `skills/last30days/scripts/lib/library_index.py`.

use std::fmt::Write as _;
use std::fs::File;
use std::io::Read as _;
use std::path::Path;
use std::sync::LazyLock;

use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::CoreError;

/// Marker preceding a public drill follow-up section.
pub const PUBLIC_DRILL_MARKER: &str = "<!-- LAST30DAYS_PUBLIC_DRILL -->";
/// Marker preceding a public freshness verification table.
pub const PUBLIC_FRESHNESS_MARKER: &str = "<!-- LAST30DAYS_PUBLIC_FRESHNESS -->";
/// Marker preceding a public ranked clusters section.
pub const PUBLIC_CLUSTERS_MARKER: &str = "<!-- LAST30DAYS_PUBLIC_CLUSTERS -->";
/// Opening marker for an explicit private corpus block.
pub const PRIVATE_CORPUS_START: &str = "<!-- LAST30DAYS_PRIVATE_CORPUS_START -->";
/// Closing marker for an explicit private corpus block.
pub const PRIVATE_CORPUS_END: &str = "<!-- LAST30DAYS_PRIVATE_CORPUS_END -->";
/// Opening marker for inherited library context injected into saved reports.
pub const LIBRARY_CONTEXT_START: &str = "<!-- last30days:library-context:start -->";
/// Closing marker for inherited library context injected into saved reports.
pub const LIBRARY_CONTEXT_END: &str = "<!-- last30days:library-context:end -->";
/// Version prefix embedded in SHA-256 index fingerprints.
pub const INDEX_FINGERPRINT_VERSION: &str = "last30days-library-index/v3";

/// Output of sanitizing and fingerprinting one Markdown document in a batch.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SanitizedDocument {
    /// Whether the document is an unbounded private brief (excluded from public index).
    pub unbounded_private: bool,
    /// Publicly shareable Markdown after redaction (empty when `unbounded_private` is `true`).
    pub public_markdown: String,
    /// SHA-256 fingerprint of the indexable (library-context-stripped) public content.
    pub indexable_fingerprint: String,
}

/// Input item for [`sanitize_markdown_batch`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarkdownBatchItem {
    /// Raw saved Markdown text.
    pub content: String,
    /// Optional source format (`"markdown"`, `"finished-brief"`, `"json"`).
    pub source_format: Option<String>,
}

/// Output item for [`sanitize_markdown_batch`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarkdownBatchResult {
    /// Whether `is_unbounded_private_brief` returned `true`.
    pub unbounded_private: bool,
    /// Publicly shareable Markdown (`public_markdown(content)`).
    pub public_markdown: String,
    /// SHA-256 fingerprint of `_indexable_content(content)`.
    pub indexable_fingerprint: String,
}

static REPORT_TITLE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?im)^#\s+last30days(?:\s+v[^:]+)?:\s*(.+?)\s*$")
        .expect("REPORT_TITLE_RE is a valid literal regex")
});

static BADGE_LINE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^🌐\s*last30days\b[^\r\n]*$").expect("BADGE_LINE_RE is a valid literal regex")
});

static CORPUS_FOOTER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\bYour files\b").expect("CORPUS_FOOTER_RE is a valid literal regex")
});

static BOLD_FROM_YOUR_FILES_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?im)^[ \t]*(?:\*\*|__)?From your files:?(?:\*\*|__)?:?[ \t]*$")
        .expect("BOLD_FROM_YOUR_FILES_RE is a valid literal regex")
});

static PRIVATE_FRESHNESS_ROW_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?im)^\| \*\*contradicted\*\* \| [^\r\n]*(?:\]\(corpus://| \(Newer corpus item disagrees\))[^\r\n]*(?:\r?\n|$)",
    )
    .expect("PRIVATE_FRESHNESS_ROW_RE is a valid literal regex")
});

static LEGACY_CORPUS_SIGNAL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?im)^#{1,3}[ \t]+From your files:?[ \t]*\r?$|^- (?:Sources|Re-researched sources):[^\r\n]*\bYour files\b|^[0-9]+\. \[[^\]\r\n]*\b(?:corpus|your files)\b[^\]\r\n]*\]",
    )
    .expect("LEGACY_CORPUS_SIGNAL_RE is a valid literal regex")
});

static RANKED_CLUSTER_HEADING_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<prefix>### [0-9]+\. )(?P<title>[^\r\n]*)$")
        .expect("RANKED_CLUSTER_HEADING_RE is a valid literal regex")
});

static RANKED_CLUSTER_STATS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r" \(score -?[0-9]+, [0-9]+ items?, sources: [^()\r\n]*\)$")
        .expect("RANKED_CLUSTER_STATS_RE is a valid literal regex")
});

static RANKED_REPRESENTATIVE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[0-9]+\. \[(?P<sources>[^\]\r\n]+)\] (?P<title>[^\r\n]+)$")
        .expect("RANKED_REPRESENTATIVE_RE is a valid literal regex")
});

static RANKED_FRESHNESS_BADGE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r" \[freshness:[^\]]+\]$")
        .expect("RANKED_FRESHNESS_BADGE_RE is a valid literal regex")
});

/// Case-insensitive ASCII substring search without allocating a lowercased copy.
#[inline]
fn contains_ascii_ignore_case(haystack: &str, needle: &[u8]) -> bool {
    let bytes = haystack.as_bytes();
    if needle.is_empty() {
        return true;
    }
    if bytes.len() < needle.len() {
        return false;
    }
    bytes
        .windows(needle.len())
        .any(|w| w.eq_ignore_ascii_case(needle))
}

/// Splits `text` into lines keeping their original line endings, matching
/// Python `str.splitlines(keepends=True)` across all 11 Unicode line breaks:
/// `\r\n`, `\n`, `\r`, `\x0b`, `\x0c`, `\x1c`, `\x1d`, `\x1e`, U+0085,
/// U+2028, and U+2029.
fn splitlines_keepends(text: &str) -> Vec<&str> {
    if text.is_empty() {
        return Vec::new();
    }
    let bytes = text.as_bytes();
    let len = bytes.len();
    let mut lines = Vec::new();
    let mut start = 0;
    let mut i = 0;
    while i < len {
        let b = bytes[i];
        let step = match b {
            b'\r' => {
                if i + 1 < len && bytes[i + 1] == b'\n' {
                    2
                } else {
                    1
                }
            }
            b'\n' | 0x0B | 0x0C | 0x1C | 0x1D | 0x1E => 1,
            // U+0085 NEXT LINE (UTF-8: C2 85)
            0xC2 if i + 1 < len && bytes[i + 1] == 0x85 => 2,
            // U+2028 LINE SEPARATOR (E2 80 A8) and U+2029 PARAGRAPH SEPARATOR (E2 80 A9)
            0xE2 if i + 2 < len
                && bytes[i + 1] == 0x80
                && (bytes[i + 2] == 0xA8 || bytes[i + 2] == 0xA9) =>
            {
                3
            }
            _ => 0,
        };
        if step > 0 {
            i += step;
            lines.push(&text[start..i]);
            start = i;
        } else {
            i += 1;
        }
    }
    if start < len {
        lines.push(&text[start..]);
    }
    lines
}

#[inline]
fn trim_line_end(line: &str) -> &str {
    line.trim_end_matches(['\r', '\n'])
}

/// Tracks `CommonMark` backtick/tilde fenced code block state across lines without regex.
#[inline]
fn fence_step(line_text: &str, fence: Option<(u8, usize)>) -> Option<(u8, usize)> {
    let bytes = line_text.as_bytes();
    let mut indent = 0;
    while indent < 3 && indent < bytes.len() && bytes[indent] == b' ' {
        indent += 1;
    }
    let rest = &bytes[indent..];
    match fence {
        None => {
            let first = *rest.first()?;
            if first != b'`' && first != b'~' {
                return None;
            }
            let count = rest.iter().take_while(|&&b| b == first).count();
            if count < 3 {
                return None;
            }
            if first == b'`' && rest[count..].contains(&b'`') {
                return None;
            }
            Some((first, count))
        }
        Some((ch, min_len)) => {
            let count = rest.iter().take_while(|&&b| b == ch).count();
            if count >= min_len && rest[count..].iter().all(|&b| b == b' ' || b == b'\t') {
                None
            } else {
                Some((ch, min_len))
            }
        }
    }
}

#[inline]
fn is_top_level_heading(line_text: &str) -> bool {
    let b = line_text.as_bytes();
    (b.len() >= 2 && b[0] == b'#' && (b[1] == b' ' || b[1] == b'\t'))
        || (b.len() >= 3 && b[0] == b'#' && b[1] == b'#' && (b[2] == b' ' || b[2] == b'\t'))
}

#[inline]
fn is_h1_to_h3_heading(line_text: &str) -> bool {
    is_top_level_heading(line_text) || {
        let b = line_text.as_bytes();
        b.len() >= 4
            && b[0] == b'#'
            && b[1] == b'#'
            && b[2] == b'#'
            && (b[3] == b' ' || b[3] == b'\t')
    }
}

#[inline]
fn is_drill_heading(line_text: &str) -> bool {
    line_text
        .strip_prefix("## Drill Follow-up")
        .is_some_and(|rest| rest.bytes().all(|b| b == b' ' || b == b'\t'))
}

#[inline]
fn is_drill_deeper_heading(line_text: &str) -> bool {
    line_text
        .strip_prefix("### Deeper")
        .is_some_and(|rest| rest.bytes().all(|b| b == b' ' || b == b'\t'))
}

#[inline]
fn is_drill_new_items(line_text: &str) -> bool {
    line_text
        .strip_prefix("- ")
        .and_then(|s| s.strip_suffix(" new items after dedupe"))
        .is_some_and(|digits| !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()))
}

#[inline]
fn is_drill_sources(line_text: &str) -> bool {
    line_text.starts_with("- Re-researched sources:")
}

#[inline]
fn is_from_your_files_h12(line_text: &str) -> bool {
    let b = line_text.as_bytes();
    let hashes = b.iter().take_while(|&&c| c == b'#').count();
    if hashes != 1 && hashes != 2 {
        return false;
    }
    let rest = &line_text[hashes..];
    let after_ws = rest.trim_start_matches([' ', '\t']);
    if after_ws.len() == rest.len() {
        return false;
    }
    let trimmed = after_ws.trim_end_matches([' ', '\t']);
    let body = trimmed.strip_suffix(':').unwrap_or(trimmed);
    body.eq_ignore_ascii_case("from your files")
}

#[inline]
fn is_from_your_files_h3(line_text: &str) -> bool {
    let Some(rest) = line_text.strip_prefix("###") else {
        return false;
    };
    let after_ws = rest.trim_start_matches([' ', '\t']);
    if after_ws.len() == rest.len() {
        return false;
    }
    let trimmed = after_ws.trim_end_matches([' ', '\t']);
    let body = trimmed.strip_suffix(':').unwrap_or(trimmed);
    body.eq_ignore_ascii_case("from your files")
}

#[inline]
fn is_from_your_files_bold(line_text: &str) -> bool {
    let trimmed = line_text.trim_matches([' ', '\t']);
    let after_open = if let Some(s) = trimmed.strip_prefix("**") {
        s
    } else if let Some(s) = trimmed.strip_prefix("__") {
        s
    } else {
        return false;
    };
    if after_open.len() < "from your files".len() {
        return false;
    }
    if !after_open.as_bytes()[.."from your files".len()].eq_ignore_ascii_case(b"from your files") {
        return false;
    }
    let mut rest = &after_open["from your files".len()..];
    if let Some(s) = rest.strip_prefix(':') {
        rest = s;
    }
    if let Some(s) = rest.strip_prefix("**") {
        rest = s;
    } else if let Some(s) = rest.strip_prefix("__") {
        rest = s;
    }
    if let Some(s) = rest.strip_prefix(':') {
        rest = s;
    }
    rest.is_empty()
}

#[inline]
fn is_ranked_clusters_heading(line_text: &str) -> bool {
    line_text
        .strip_prefix("## Ranked Evidence Clusters")
        .is_some_and(|rest| rest.bytes().all(|b| b == b' ' || b == b'\t'))
}

#[inline]
fn brief_clusters_heading_name(line_text: &str) -> Option<&'static str> {
    for name in ["Ranked Storylines", "Topic Tensions", "Source Clusters"] {
        if let Some(rest) = line_text
            .strip_prefix("## ")
            .and_then(|s| s.strip_prefix(name))
        {
            if rest.bytes().all(|b| b == b' ' || b == b'\t') {
                return Some(name);
            }
        }
    }
    None
}

#[inline]
fn is_freshness_heading(line_text: &str) -> bool {
    line_text
        .strip_prefix("## Freshness Verification")
        .is_some_and(|rest| rest.bytes().all(|b| b == b' ' || b == b'\t'))
}

#[derive(Clone, Copy)]
enum SectionBoundary {
    TopLevel,
    H1ToH3,
}

impl SectionBoundary {
    #[inline]
    fn matches(self, line_text: &str) -> bool {
        match self {
            Self::TopLevel => is_top_level_heading(line_text),
            Self::H1ToH3 => is_h1_to_h3_heading(line_text),
        }
    }
}

fn find_section_end(lines: &[&str], start: usize, boundary: SectionBoundary) -> usize {
    let mut fence = None;
    for (offset, line) in lines[start..].iter().enumerate() {
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            continue;
        }
        if boundary.matches(text) {
            return start + offset;
        }
        fence = fence_step(text, None);
    }
    lines.len()
}

/// Returns `true` when `markdown` carries local-corpus markers or citations.
pub fn has_private_corpus(markdown: &str) -> bool {
    if markdown.contains(PRIVATE_CORPUS_START) {
        return true;
    }
    if contains_ascii_ignore_case(markdown, b"corpus://") {
        return true;
    }
    if contains_ascii_ignore_case(markdown, b"your files")
        && (CORPUS_FOOTER_RE.is_match(markdown)
            || BOLD_FROM_YOUR_FILES_RE.is_match(markdown)
            || LEGACY_CORPUS_SIGNAL_RE.is_match(markdown))
    {
        return true;
    }
    if contains_ascii_ignore_case(markdown, b"corpus")
        && (LEGACY_CORPUS_SIGNAL_RE.is_match(markdown)
            || PRIVATE_FRESHNESS_ROW_RE.is_match(markdown))
    {
        return true;
    }
    false
}

fn is_structured_engine_report(markdown: &str) -> bool {
    (markdown.contains(PRIVATE_CORPUS_START) && markdown.contains(PRIVATE_CORPUS_END))
        || markdown.contains(PUBLIC_CLUSTERS_MARKER)
        || markdown.contains("## Ranked Evidence Clusters")
        || markdown.contains("<!-- EVIDENCE FOR SYNTHESIS")
        || markdown.contains("# END OF last30days CANONICAL OUTPUT")
        || REPORT_TITLE_RE.is_match(markdown)
}

/// Returns `true` when a synthesized chat brief carries unbounded local-corpus content.
pub fn is_unbounded_private_brief(markdown: &str, source_format: Option<&str>) -> bool {
    if let Some(fmt) = source_format {
        if fmt != "finished-brief" {
            return false;
        }
    }
    if is_structured_engine_report(markdown) {
        return false;
    }
    markdown.contains('🌐') && BADGE_LINE_RE.is_match(markdown) && has_private_corpus(markdown)
}

fn strip_private_corpus_blocks(markdown: &str) -> String {
    if !markdown.contains(PRIVATE_CORPUS_START) {
        return markdown.to_owned();
    }
    let mut out = String::with_capacity(markdown.len());
    let mut cursor = 0;
    while let Some(rel_start) = markdown[cursor..].find(PRIVATE_CORPUS_START) {
        let block_start = cursor + rel_start;
        out.push_str(&markdown[cursor..block_start]);

        let search_from = block_start + PRIVATE_CORPUS_START.len();
        let block_end = if let Some(rel_end) = markdown[search_from..].find(PRIVATE_CORPUS_END) {
            let after_end = search_from + rel_end + PRIVATE_CORPUS_END.len();
            let ws_len = markdown[after_end..]
                .char_indices()
                .find(|&(_, ch)| !ch.is_whitespace())
                .map_or(markdown.len() - after_end, |(i, _)| i);
            after_end + ws_len
        } else {
            markdown.len()
        };

        let mut marker_end = block_start;
        while marker_end > 0 {
            let prev = markdown.as_bytes()[marker_end - 1];
            if matches!(prev, b' ' | b'\t' | b'\r' | b'\n') {
                marker_end -= 1;
            } else {
                break;
            }
        }
        let prefix = &markdown[..marker_end];
        if prefix.ends_with(PUBLIC_DRILL_MARKER)
            || prefix.ends_with(PUBLIC_CLUSTERS_MARKER)
            || prefix.ends_with(PUBLIC_FRESHNESS_MARKER)
        {
            out.push_str("\n\n");
        }
        cursor = block_end;
    }
    out.push_str(&markdown[cursor..]);
    out
}

fn strip_unmarked_drills(markdown: &str) -> String {
    if !markdown.contains("## Drill Follow-up") {
        return markdown.to_owned();
    }
    let mut lines = splitlines_keepends(markdown);
    loop {
        let mut fence = None;
        let mut unmarked_idx = None;
        for (idx, line) in lines.iter().enumerate() {
            let text = trim_line_end(line);
            if fence.is_some() {
                fence = fence_step(text, fence);
                continue;
            }
            if is_drill_heading(text) {
                let prev_text = if idx > 0 {
                    trim_line_end(lines[idx - 1])
                } else {
                    ""
                };
                if prev_text != PUBLIC_DRILL_MARKER && prev_text != PRIVATE_CORPUS_START {
                    unmarked_idx = Some(idx);
                    break;
                }
            }
            fence = fence_step(text, None);
        }
        let Some(start_idx) = unmarked_idx else {
            return lines.concat();
        };
        let mut end = lines.len();
        let mut inner_fence = None;
        for idx in (start_idx + 1)..lines.len() {
            let text = trim_line_end(lines[idx]);
            if inner_fence.is_some() {
                inner_fence = fence_step(text, inner_fence);
                continue;
            }
            if is_drill_deeper_heading(text)
                && idx + 3 < lines.len()
                && trim_line_end(lines[idx + 1])
                    .bytes()
                    .all(|b| b == b' ' || b == b'\t')
                && is_drill_new_items(trim_line_end(lines[idx + 2]))
                && is_drill_sources(trim_line_end(lines[idx + 3]))
            {
                end = idx + 4;
            }
            inner_fence = fence_step(text, None);
        }
        lines.drain(start_idx..end);
    }
}

fn strip_from_your_files_sections(markdown: &str) -> String {
    if !contains_ascii_ignore_case(markdown, b"from your files") {
        return markdown.to_owned();
    }
    let lines = splitlines_keepends(markdown);
    let mut out = String::with_capacity(markdown.len());
    let mut fence = None;
    let mut skip_until: Option<SectionBoundary> = None;

    for line in lines {
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            if skip_until.is_none() {
                out.push_str(line);
            }
            continue;
        }
        if let Some(boundary) = skip_until {
            if boundary.matches(text) {
                skip_until = None;
            } else {
                fence = fence_step(text, None);
                continue;
            }
        }
        if is_from_your_files_h12(text) || is_from_your_files_bold(text) {
            skip_until = Some(SectionBoundary::TopLevel);
            continue;
        }
        if is_from_your_files_h3(text) {
            skip_until = Some(SectionBoundary::H1ToH3);
            continue;
        }
        fence = fence_step(text, None);
        out.push_str(line);
    }
    out
}

fn public_legacy_cluster_lines(heading_line: &str, body_lines: &[&str]) -> String {
    let heading_text = trim_line_end(heading_line);
    let ending = &heading_line[heading_text.len()..];
    let Some(heading_caps) = RANKED_CLUSTER_HEADING_RE.captures(heading_text) else {
        let mut s = String::from(heading_line);
        for l in body_lines {
            s.push_str(l);
        }
        return s;
    };

    let mut preamble: Vec<&str> = Vec::new();
    let mut rep_blocks: Vec<(String, String, Vec<&str>)> = Vec::new();
    let mut fence = None;

    for &line in body_lines {
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            if let Some((_, _, block)) = rep_blocks.last_mut() {
                block.push(line);
            } else {
                preamble.push(line);
            }
            continue;
        }
        if let Some(rep_caps) = RANKED_REPRESENTATIVE_RE.captures(text) {
            let sources = rep_caps["sources"].to_owned();
            let title = rep_caps["title"].to_owned();
            rep_blocks.push((sources, title, vec![line]));
            continue;
        }
        fence = fence_step(text, None);
        if let Some((_, _, block)) = rep_blocks.last_mut() {
            block.push(line);
        } else {
            preamble.push(line);
        }
    }

    let had_representatives = !rep_blocks.is_empty();
    let mut public_reps: Vec<(String, Vec<&str>)> = Vec::new();
    for (sources_str, title, block) in rep_blocks {
        let has_private = sources_str.split(',').any(|s| {
            let trimmed = s.trim();
            trimmed.eq_ignore_ascii_case("corpus") || trimmed.eq_ignore_ascii_case("your files")
        });
        if has_private {
            continue;
        }
        public_reps.push((title, block));
    }

    let raw_body: String = body_lines.concat();
    let mut cleaned_body = preamble.concat();
    for (_, block) in &public_reps {
        for &l in block {
            cleaned_body.push_str(l);
        }
    }
    if raw_body.ends_with("\n\n") && !cleaned_body.is_empty() && !cleaned_body.ends_with("\n\n") {
        let trimmed = cleaned_body.trim_end_matches(['\r', '\n']);
        cleaned_body = format!("{trimmed}\n\n");
    }

    for (raw_title, _) in &public_reps {
        let stripped = RANKED_FRESHNESS_BADGE_RE.replace(raw_title, "");
        let title = stripped.trim();
        if !title.is_empty() {
            let stats = RANKED_CLUSTER_STATS_RE
                .find(&heading_caps["title"])
                .map_or("", |m| m.as_str());
            return format!(
                "{}{}{}{}{}",
                &heading_caps["prefix"], title, stats, ending, cleaned_body
            );
        }
    }
    if had_representatives {
        return String::new();
    }
    if LEGACY_CORPUS_SIGNAL_RE.is_match(&cleaned_body)
        || contains_ascii_ignore_case(&cleaned_body, b"corpus://")
        || BOLD_FROM_YOUR_FILES_RE.is_match(&cleaned_body)
    {
        return String::new();
    }
    cleaned_body
}

fn rewrite_ranked_clusters_sections(markdown: &str) -> String {
    if !markdown.contains("## Ranked Evidence Clusters") {
        return markdown.to_owned();
    }
    let lines = splitlines_keepends(markdown);
    let mut out = String::with_capacity(markdown.len());
    let mut fence = None;
    let mut idx = 0;

    while idx < lines.len() {
        let line = lines[idx];
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            out.push_str(line);
            idx += 1;
            continue;
        }
        if is_ranked_clusters_heading(text) {
            let end = find_section_end(&lines, idx + 1, SectionBoundary::TopLevel);
            let prev_text = if idx > 0 {
                trim_line_end(lines[idx - 1])
            } else {
                ""
            };
            if prev_text == PUBLIC_CLUSTERS_MARKER {
                for l in &lines[idx..end] {
                    out.push_str(l);
                }
                idx = end;
                continue;
            }
            out.push_str(line);
            let mut sec_fence = None;
            let mut cluster_heading: Option<&str> = None;
            let mut cluster_body: Vec<&str> = Vec::new();
            for &sec_line in &lines[idx + 1..end] {
                let sec_text = trim_line_end(sec_line);
                if sec_fence.is_some() {
                    sec_fence = fence_step(sec_text, sec_fence);
                    if cluster_heading.is_some() {
                        cluster_body.push(sec_line);
                    } else {
                        out.push_str(sec_line);
                    }
                    continue;
                }
                if RANKED_CLUSTER_HEADING_RE.is_match(sec_text) {
                    if let Some(prev_heading) = cluster_heading {
                        out.push_str(&public_legacy_cluster_lines(prev_heading, &cluster_body));
                    }
                    cluster_heading = Some(sec_line);
                    cluster_body.clear();
                    continue;
                }
                sec_fence = fence_step(sec_text, None);
                if cluster_heading.is_some() {
                    cluster_body.push(sec_line);
                } else {
                    out.push_str(sec_line);
                }
            }
            if let Some(prev_heading) = cluster_heading {
                out.push_str(&public_legacy_cluster_lines(prev_heading, &cluster_body));
            }
            idx = end;
            continue;
        }
        fence = fence_step(text, None);
        out.push_str(line);
        idx += 1;
    }
    out
}

fn rewrite_brief_clusters_sections(markdown: &str) -> String {
    let lines = splitlines_keepends(markdown);
    let mut out = String::with_capacity(markdown.len());
    let mut fence = None;
    let mut idx = 0;

    while idx < lines.len() {
        let line = lines[idx];
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            out.push_str(line);
            idx += 1;
            continue;
        }
        if let Some(name) = brief_clusters_heading_name(text) {
            let end = find_section_end(&lines, idx + 1, SectionBoundary::TopLevel);
            let prev_text = if idx > 0 {
                trim_line_end(lines[idx - 1])
            } else {
                ""
            };
            if prev_text == PUBLIC_CLUSTERS_MARKER {
                for l in &lines[idx..end] {
                    out.push_str(l);
                }
                idx = end;
                continue;
            }
            out.push_str(line);
            let is_storylines = name == "Ranked Storylines";
            let mut sec_fence = None;
            for &sec_line in &lines[idx + 1..end] {
                let sec_text = trim_line_end(sec_line);
                if sec_fence.is_some() {
                    sec_fence = fence_step(sec_text, sec_fence);
                    out.push_str(sec_line);
                    continue;
                }
                let drop_label = if is_storylines {
                    RANKED_CLUSTER_HEADING_RE.is_match(sec_text)
                } else {
                    sec_text.starts_with("- **")
                };
                if drop_label {
                    continue;
                }
                sec_fence = fence_step(sec_text, None);
                out.push_str(sec_line);
            }
            idx = end;
            continue;
        }
        fence = fence_step(text, None);
        out.push_str(line);
        idx += 1;
    }
    out
}

fn strip_unmarked_freshness_sections(markdown: &str) -> String {
    if !markdown.contains("## Freshness Verification") {
        return markdown.to_owned();
    }
    let lines = splitlines_keepends(markdown);
    let mut out = String::with_capacity(markdown.len());
    let mut fence = None;
    let mut idx = 0;

    while idx < lines.len() {
        let line = lines[idx];
        let text = trim_line_end(line);
        if fence.is_some() {
            fence = fence_step(text, fence);
            out.push_str(line);
            idx += 1;
            continue;
        }
        if is_freshness_heading(text)
            && idx + 3 < lines.len()
            && trim_line_end(lines[idx + 1])
                .bytes()
                .all(|b| b == b' ' || b == b'\t')
            && trim_line_end(lines[idx + 2]) == "| Verdict | Claim | Evidence | Checked |"
            && trim_line_end(lines[idx + 3]) == "| --- | --- | --- | --- |"
        {
            let end = find_section_end(&lines, idx + 4, SectionBoundary::TopLevel);
            let prev_text = if idx > 0 {
                trim_line_end(lines[idx - 1])
            } else {
                ""
            };
            if prev_text == PUBLIC_FRESHNESS_MARKER {
                for l in &lines[idx..end] {
                    out.push_str(l);
                }
            }
            idx = end;
            continue;
        }
        fence = fence_step(text, None);
        out.push_str(line);
        idx += 1;
    }
    out
}

/// Redacts private corpus sections and unmarked local-corpus rows from `markdown`.
///
/// Mirrors `library.py:public_markdown`.
pub fn public_markdown(markdown: &str) -> String {
    if is_unbounded_private_brief(markdown, None) {
        return String::new();
    }
    let corpus_document = has_private_corpus(markdown);
    let mut current = strip_private_corpus_blocks(markdown);
    current = strip_unmarked_drills(&current);
    current = strip_from_your_files_sections(&current);
    if corpus_document {
        current = rewrite_ranked_clusters_sections(&current);
        if current.starts_with("# Production Brief: ") {
            current = rewrite_brief_clusters_sections(&current);
        }
        current = strip_unmarked_freshness_sections(&current);
    }
    if contains_ascii_ignore_case(&current, b"| **contradicted** | ") {
        PRIVATE_FRESHNESS_ROW_RE
            .replace_all(&current, "")
            .into_owned()
    } else {
        current
    }
}

/// Strips marked and legacy `## From your library` context sections.
///
/// Mirrors `library_index.py:_without_library_context`.
pub fn without_library_context(content: &str) -> String {
    let stripped_marked = if content.contains(LIBRARY_CONTEXT_START) {
        strip_marked_library_context(content)
    } else {
        content.to_owned()
    };
    if stripped_marked.contains("## From your library") {
        strip_legacy_library_context(&stripped_marked)
    } else {
        stripped_marked
    }
}

static MARKED_LIBRARY_CONTEXT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ms)^<!-- last30days:library-context:start -->\s*$.*?^<!-- last30days:library-context:end -->\s*$\n?",
    )
    .expect("MARKED_LIBRARY_CONTEXT_RE is a valid literal regex")
});

fn strip_marked_library_context(content: &str) -> String {
    MARKED_LIBRARY_CONTEXT_RE
        .replace_all(content, "")
        .into_owned()
}

fn strip_legacy_library_context(content: &str) -> String {
    let lines = splitlines_keepends(content);
    let mut out = String::with_capacity(content.len());
    let mut skipping = false;
    for line in lines {
        let text = trim_line_end(line);
        if skipping {
            let starts_h2 = text
                .strip_prefix("##")
                .is_some_and(|rest| rest.chars().next().is_some_and(char::is_whitespace));
            if starts_h2 {
                skipping = false;
                out.push_str(line);
            }
            continue;
        }
        if text
            .strip_prefix("## From your library")
            .is_some_and(|rest| rest.chars().all(char::is_whitespace))
        {
            skipping = true;
            continue;
        }
        out.push_str(line);
    }
    out
}

/// Computes the SHA-256 fingerprint of indexable content matching `library_index._fingerprint`.
pub fn indexable_fingerprint_from_public(public_md: &str) -> String {
    let indexable = without_library_context(public_md);
    let mut hasher = Sha256::new();
    hasher.update(INDEX_FINGERPRINT_VERSION.as_bytes());
    hasher.update(b"\0");
    hasher.update(indexable.as_bytes());
    let digest = hasher.finalize();
    let mut hex = String::with_capacity(64);
    for byte in digest {
        let _ = write!(&mut hex, "{byte:02x}");
    }
    hex
}

/// Sanitizes and fingerprints a single Markdown document in one pass.
pub fn sanitize_markdown_one(content: &str, source_format: Option<&str>) -> MarkdownBatchResult {
    let unbounded_private = is_unbounded_private_brief(content, source_format);
    let public = if unbounded_private {
        String::new()
    } else {
        public_markdown(content)
    };
    let fingerprint = indexable_fingerprint_from_public(&public);
    MarkdownBatchResult {
        unbounded_private,
        public_markdown: public,
        indexable_fingerprint: fingerprint,
    }
}

/// Maximum Markdown file size accepted during library scans (10 MiB).
pub const MAX_LIBRARY_FILE_BYTES: u64 = 10 * 1024 * 1024;

/// File descriptor input for [`scan_markdown_files_batch`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarkdownFileRequest {
    /// Absolute path to a regular non-symlink Markdown file.
    pub path: String,
    /// Filename stem (without `.md`).
    pub stem: String,
    /// Source format (`"markdown"` or `"finished-brief"`).
    pub source_format: String,
    /// Expected `(dev, ino)` from caller's `lstat` on POSIX, if available.
    pub expected_dev_ino: Option<(u64, u64)>,
    /// Fallback `YYYY-MM-DD` date derived from file mtime in local timezone.
    pub fallback_date: String,
}

/// Metadata and index fingerprint extracted from one saved Markdown file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarkdownFileMeta {
    /// Path echoed from the request.
    pub path: String,
    /// Extracted topic string.
    pub topic: String,
    /// URL-safe slug derived from `topic`.
    pub slug: String,
    /// 8-hex-char identity hash of `"{topic}\n{stem}"`.
    pub identity_hash: String,
    /// Resolved `YYYY-MM-DD` published date.
    pub published_date: String,
    /// Full deterministic entry URN.
    pub entry_id: String,
    /// Extracted headline.
    pub headline: String,
    /// Extracted summary.
    pub summary: String,
    /// Source format echoed from request.
    pub source_format: String,
    /// Whether the document is an unbounded private brief.
    pub unbounded_private: bool,
    /// SHA-256 fingerprint of indexable public content (without library context).
    pub indexable_fingerprint: String,
    /// Whether `public_markdown(content) != content`.
    pub redacted: bool,
}

/// Per-file outcome returned by [`scan_markdown_files_batch`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
#[non_exhaustive]
pub enum MarkdownScanOutcome {
    /// File was safely read, sanitized, and parsed.
    Ok {
        /// Parsed metadata and fingerprint.
        meta: MarkdownFileMeta,
    },
    /// File was skipped (unreadable, symlink, oversized, invalid UTF-8, or missing title).
    Skipped {
        /// Path echoed from request.
        path: String,
        /// Human-readable skip reason.
        reason: String,
    },
}

static TOPIC_META_COMMENT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^<!--\s*last30days-topic:\s*(.+?)\s*-->$")
        .expect("TOPIC_META_COMMENT_RE is a valid literal regex")
});

static FIRST_TITLE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^#\s+(.+?)\s*$").expect("FIRST_TITLE_RE is a valid literal regex")
});

static COMPARISON_TITLE_SUFFIX_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i):\s*What the Community Says\s*\(/Last30Days\)\s*$")
        .expect("COMPARISON_TITLE_SUFFIX_RE is a valid literal regex")
});

static BADGE_SYNCED_DATE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\bsynced\s+(\d{4}-\d{2}-\d{2})\b")
        .expect("BADGE_SYNCED_DATE_RE is a valid literal regex")
});

static BADGE_VERSION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^v\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9._-]+)?$")
        .expect("BADGE_VERSION_RE is a valid literal regex")
});

static BADGE_MONTH_DAY_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})\b")
        .expect("BADGE_MONTH_DAY_YEAR_RE is a valid literal regex")
});

static BADGE_ISO_DATE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(\d{4}-\d{2}-\d{2})\b").expect("BADGE_ISO_DATE_RE is a valid literal regex")
});

static DATE_RANGE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?im)^-\s*Date range:\s*\d{4}-\d{2}-\d{2}\s+to\s+(\d{4}-\d{2}-\d{2})\s*$")
        .expect("DATE_RANGE_RE is a valid literal regex")
});

static DATED_FILENAME_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"-(\d{4}-\d{2}-\d{2})(?:-\d+)?$")
        .expect("DATED_FILENAME_RE is a valid literal regex")
});

static RANKED_HEADLINE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^###\s+1[.)]\s+(.+?)\s*$")
        .expect("RANKED_HEADLINE_RE is a valid literal regex")
});

static SCORE_SUFFIX_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\s+\(score\s+[^)]*\)\s*$").expect("SCORE_SUFFIX_RE is a valid literal regex")
});

static MARKDOWN_LINK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\[([^\]]+)]\([^)]+\)").expect("MARKDOWN_LINK_RE is a valid literal regex")
});

static PROSE_LABEL_LINE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)^(?:\*\*|__)?(?:what i learned|key patterns(?:\s+from the research)?|best takes|tl;?dr|summary|verdict|bottom line)(?:\*\*|__)?:?(?:\*\*|__)?$",
    )
    .expect("PROSE_LABEL_LINE_RE is a valid literal regex")
});

static EVIDENCE_LINE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?im)^[ \t]*-\s*Evidence:\s*(.+?)\s*$")
        .expect("EVIDENCE_LINE_RE is a valid literal regex")
});

static LEARNED_HEADING_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?im)^(?:##\s+What I learned|(?:\*\*|__)?What I learned:?(?:\*\*|__)?)[ \t]*$")
        .expect("LEARNED_HEADING_RE is a valid literal regex")
});

/// Sanitizes and fingerprints a batch of Markdown documents in parallel using Rayon.
pub fn sanitize_markdown_batch(items: &[MarkdownBatchItem]) -> Vec<MarkdownBatchResult> {
    items
        .par_iter()
        .map(|item| sanitize_markdown_one(&item.content, item.source_format.as_deref()))
        .collect()
}

/// Computes the URL-safe slug matching `library.slugify`.
pub fn slugify(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut prev_dash = true;
    for ch in value.to_lowercase().chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() {
            out.push(ch);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    if out.is_empty() {
        "last30days".to_owned()
    } else {
        out
    }
}

/// Computes the 8-hex-char identity hash matching `LibraryEntry.identity_hash`.
pub fn identity_hash(topic: &str, stem: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(topic.as_bytes());
    hasher.update(b"\n");
    hasher.update(stem.as_bytes());
    let digest = hasher.finalize();
    let mut hex = String::with_capacity(8);
    for byte in &digest[..4] {
        let _ = write!(&mut hex, "{byte:02x}");
    }
    hex
}

fn month_number(raw: &str) -> Option<u32> {
    let lower = raw.to_ascii_lowercase();
    let prefix = if lower.starts_with("sept") {
        "sept"
    } else if lower.len() >= 3 {
        &lower[..3]
    } else {
        return None;
    };
    match prefix {
        "jan" => Some(1),
        "feb" => Some(2),
        "mar" => Some(3),
        "apr" => Some(4),
        "may" => Some(5),
        "jun" => Some(6),
        "jul" => Some(7),
        "aug" => Some(8),
        "sep" | "sept" => Some(9),
        "oct" => Some(10),
        "nov" => Some(11),
        "dec" => Some(12),
        _ => None,
    }
}

fn format_valid_ymd(year: i64, month: u32, day: u32) -> Option<String> {
    let candidate = format!("{year:04}-{month:02}-{day:02}");
    crate::signals::parse_ymd_days(&candidate).ok()?;
    Some(candidate)
}

fn parse_badge_date(text: &str) -> Option<String> {
    if let Some(last) = BADGE_MONTH_DAY_YEAR_RE.captures_iter(text).last() {
        if let (Some(month), Ok(day), Ok(year)) = (
            month_number(&last[1]),
            last[2].parse::<u32>(),
            last[3].parse::<i64>(),
        ) {
            if let Some(valid) = format_valid_ymd(year, month, day) {
                return Some(valid);
            }
        }
    }
    if let Some(last) = BADGE_ISO_DATE_RE.captures_iter(text).last() {
        if crate::signals::parse_ymd_days(&last[1]).is_ok() {
            return Some(last[1].to_owned());
        }
    }
    None
}

fn parse_badge_line(content: &str) -> (String, Option<String>) {
    if !content.contains('🌐') {
        return (String::new(), None);
    }
    let Some(m) = BADGE_LINE_RE.find(content) else {
        return (String::new(), None);
    };
    let line = m.as_str().trim();
    let parts: Vec<&str> = line
        .split('·')
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .collect();
    let mut topic = String::new();
    let mut published: Option<String> = None;
    if let Some(caps) = BADGE_SYNCED_DATE_RE.captures(line) {
        if crate::signals::parse_ymd_days(&caps[1]).is_ok() {
            published = Some(caps[1].to_owned());
        }
    } else {
        for &segment in parts.iter().skip(1) {
            if published.is_none() {
                if let Some(parsed) = parse_badge_date(segment) {
                    published = Some(parsed);
                    continue;
                }
            }
            if topic.is_empty()
                && !BADGE_VERSION_RE.is_match(segment)
                && !matches!(
                    segment.to_ascii_lowercase().as_str(),
                    "quick" | "default" | "deep" | "cached"
                )
            {
                segment.clone_into(&mut topic);
            }
        }
    }
    (topic, published)
}

/// Strips inline Markdown emphasis/code/links and collapses whitespace (`library._clean_inline`).
pub(crate) fn clean_inline(value: &str) -> String {
    let no_links = MARKDOWN_LINK_RE.replace_all(value, "$1");
    // Strip leading blockquote marker `^\s*>\s?`
    let trimmed_start = no_links.trim_start();
    let no_quote = if let Some(rest) = trimmed_start.strip_prefix('>') {
        rest.strip_prefix(|c: char| c.is_whitespace())
            .unwrap_or(rest)
    } else {
        no_links.as_ref()
    };
    // Match Python's exact three left-to-right regex passes so nested emphasis
    // such as `**__Rust__**` produces `_Rust_` identically in both engines.
    let s1 = strip_delimited_any(no_quote, &["**", "__"]);
    let s2 = strip_delimited_any(&s1, &["*", "_"]);
    let s3 = strip_delimited_any(&s2, &["`"]);
    let mut collapsed = String::with_capacity(s3.len());
    for word in s3.split_whitespace() {
        if !collapsed.is_empty() {
            collapsed.push(' ');
        }
        collapsed.push_str(word);
    }
    collapsed
}

/// Mirrors `(?<!\w)(d1|d2)(?=\S)(.+?)(?<=\S)\1(?!\w)` in a single left-to-right pass.
fn strip_delimited_any(input: &str, delims: &[&str]) -> String {
    if !delims.iter().any(|d| input.contains(d)) {
        return input.to_owned();
    }
    let mut out = String::with_capacity(input.len());
    let mut cursor = 0;

    while cursor < input.len() {
        let next_open = delims
            .iter()
            .filter_map(|&delim| input[cursor..].find(delim).map(|rel| (cursor + rel, delim)))
            .min_by_key(|&(pos, _)| pos);
        let Some((open, delim)) = next_open else {
            out.push_str(&input[cursor..]);
            break;
        };
        let dlen = delim.len();
        let prev_is_word = input[..open]
            .chars()
            .next_back()
            .is_some_and(crate::cjk::is_word_char);
        let after_open = open + dlen;
        let next_is_non_ws = input[after_open..]
            .chars()
            .next()
            .is_some_and(|c| !c.is_whitespace());

        if !prev_is_word && next_is_non_ws {
            // Find earliest valid matching closing delimiter on the same line (no `.` across `\n`).
            let line_end = input[after_open..]
                .find('\n')
                .map_or(input.len(), |i| after_open + i);
            let mut search = after_open;
            let mut matched_close: Option<usize> = None;
            while search < line_end {
                let Some(rel_close) = input[search..line_end].find(delim) else {
                    break;
                };
                let close = search + rel_close;
                if close > after_open {
                    let before_close_non_ws = input[after_open..close]
                        .chars()
                        .next_back()
                        .is_some_and(|c| !c.is_whitespace());
                    let after_close = close + dlen;
                    let after_close_is_word = input[after_close..]
                        .chars()
                        .next()
                        .is_some_and(crate::cjk::is_word_char);
                    if before_close_non_ws && !after_close_is_word {
                        matched_close = Some(close);
                        break;
                    }
                }
                search = close + 1;
            }
            if let Some(close) = matched_close {
                out.push_str(&input[cursor..open]);
                out.push_str(&input[after_open..close]);
                cursor = close + dlen;
                continue;
            }
        }
        // Advance by one character past `open` so an alternative delimiter
        // inside a failed multi-char opener (e.g. `**` followed by `__`) is not skipped.
        let step = input[open..].chars().next().map_or(1, char::len_utf8);
        out.push_str(&input[cursor..open + step]);
        cursor = open + step;
    }
    out
}

fn brief_prose_paragraph(content: &str) -> String {
    let mut in_fence = false;
    let mut para: Vec<&str> = Vec::new();
    for raw_line in splitlines_keepends(content) {
        let stripped = raw_line.trim();
        if stripped.starts_with("```") {
            in_fence = !in_fence;
            if !para.is_empty() {
                break;
            }
            continue;
        }
        if in_fence {
            continue;
        }
        if stripped.is_empty() {
            if !para.is_empty() {
                break;
            }
            continue;
        }
        if stripped.starts_with("🌐 last30days")
            || stripped.starts_with("<!--")
            || stripped.starts_with('#')
            || stripped.starts_with("---")
            || stripped.starts_with('|')
            || PROSE_LABEL_LINE_RE.is_match(stripped)
        {
            if !para.is_empty() {
                break;
            }
            continue;
        }
        para.push(stripped.trim_end_matches('\\'));
    }
    clean_inline(&para.join(" "))
}

fn truncate_chars(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

fn brief_prose_headline(content: &str) -> String {
    let text = brief_prose_paragraph(content);
    if text.is_empty() {
        return String::new();
    }
    // Split on `(?<=[.!?])\s+` (first sentence).
    let mut split_at = text.len();
    let mut prev_punct = false;
    for (idx, ch) in text.char_indices() {
        if prev_punct && ch.is_whitespace() {
            split_at = idx;
            break;
        }
        prev_punct = matches!(ch, '.' | '!' | '?');
    }
    truncate_chars(&text[..split_at], 140)
}

fn brief_prose_summary(content: &str) -> String {
    let text = brief_prose_paragraph(content);
    if text.is_empty() {
        String::new()
    } else {
        truncate_chars(&text, 500)
    }
}

fn markdown_headline(content: &str) -> String {
    if let Some(caps) = RANKED_HEADLINE_RE.captures(content) {
        let without_score = SCORE_SUFFIX_RE.replace(&caps[1], "");
        return clean_inline(&without_score);
    }
    String::new()
}

fn markdown_summary(content: &str) -> String {
    if let Some(m) = LEARNED_HEADING_RE.find(content) {
        let after_heading = &content[m.end()..];
        if let Some(nl_pos) = after_heading.find('\n') {
            let mut body_start = m.end() + nl_pos + 1;
            while body_start < content.len() && content.as_bytes()[body_start] == b'\n' {
                body_start += 1;
            }
            let body = &content[body_start..];
            // Stop at `\n#{1,3}\s`, `\n---`, or `\n````.
            let mut body_end = body.len();
            let mut offset = 0;
            for line in splitlines_keepends(body) {
                let text = trim_line_end(line);
                if offset > 0
                    && (is_h1_to_h3_heading(text)
                        || text.starts_with("---")
                        || text.starts_with("```"))
                {
                    body_end = offset;
                    break;
                }
                offset += line.len();
            }
            let section = &body[..body_end];
            // Split paragraphs on blank lines (`\n\s*\n`).
            let mut para = String::new();
            for line in section.lines() {
                if line.trim().is_empty() {
                    if !para.is_empty() {
                        let cleaned = clean_inline(&para);
                        if !cleaned.is_empty() && !PROSE_LABEL_LINE_RE.is_match(&cleaned) {
                            return truncate_chars(&cleaned, 500);
                        }
                        para.clear();
                    }
                } else {
                    if !para.is_empty() {
                        para.push('\n');
                    }
                    para.push_str(line);
                }
            }
            if !para.is_empty() {
                let cleaned = clean_inline(&para);
                if !cleaned.is_empty() && !PROSE_LABEL_LINE_RE.is_match(&cleaned) {
                    return truncate_chars(&cleaned, 500);
                }
            }
        }
    }
    if let Some(caps) = EVIDENCE_LINE_RE.captures(content) {
        return truncate_chars(&clean_inline(&caps[1]), 500);
    }
    String::new()
}

fn markdown_date(content: &str, stem: &str, fallback_date: &str) -> String {
    if let Some(caps) = DATE_RANGE_RE.captures(content) {
        if crate::signals::parse_ymd_days(&caps[1]).is_ok() {
            return caps[1].to_owned();
        }
    }
    if let Some(caps) = DATED_FILENAME_RE.captures(stem) {
        if crate::signals::parse_ymd_days(&caps[1]).is_ok() {
            return caps[1].to_owned();
        }
    }
    fallback_date.to_owned()
}

/// Parses metadata and index fingerprint from one Markdown document's text.
///
/// # Errors
///
/// Returns [`CoreError::MissingMarkdownTitle`] or [`CoreError::EmptyMarkdownTitle`]
/// when the document has no extractable title.
pub fn parse_markdown_meta(
    content: &str,
    path: &str,
    stem: &str,
    source_format: &str,
    fallback_date: &str,
) -> Result<MarkdownFileMeta, CoreError> {
    let unbounded_private = is_unbounded_private_brief(content, Some(source_format));
    let public_content = if unbounded_private {
        String::new()
    } else {
        public_markdown(content)
    };
    let parse_source = if unbounded_private {
        content
    } else {
        &public_content
    };
    let redacted = !unbounded_private && public_content != content;

    let meta_topic = TOPIC_META_COMMENT_RE
        .captures(content)
        .map(|c| c[1].to_owned());
    let (badge_topic, badge_date) = parse_badge_line(parse_source);
    let report_title = REPORT_TITLE_RE
        .captures(parse_source)
        .map(|c| c[1].to_owned());
    let first_title = FIRST_TITLE_RE
        .captures(parse_source)
        .map(|c| c[1].to_owned());

    let topic = if let Some(t) = meta_topic {
        clean_inline(&t)
    } else if source_format == "finished-brief" && !badge_topic.is_empty() {
        clean_inline(&badge_topic)
    } else if let Some(t) = report_title {
        clean_inline(&t)
    } else if !badge_topic.is_empty() {
        clean_inline(&badge_topic)
    } else if let Some(t) = first_title {
        let stripped = COMPARISON_TITLE_SUFFIX_RE.replace(&t, "");
        clean_inline(&stripped)
    } else {
        return Err(CoreError::MissingMarkdownTitle);
    };

    if topic.is_empty() {
        return Err(CoreError::EmptyMarkdownTitle);
    }

    let published_date =
        badge_date.unwrap_or_else(|| markdown_date(parse_source, stem, fallback_date));

    let (headline, summary) = if unbounded_private {
        (
            topic.clone(),
            "Private local-corpus brief (stored locally only).".to_owned(),
        )
    } else {
        let hl = {
            let m = markdown_headline(parse_source);
            if m.is_empty() {
                let p = brief_prose_headline(parse_source);
                if p.is_empty() { topic.clone() } else { p }
            } else {
                m
            }
        };
        let sum = {
            let m = markdown_summary(parse_source);
            if m.is_empty() {
                let p = brief_prose_summary(parse_source);
                if p.is_empty() { hl.clone() } else { p }
            } else {
                m
            }
        };
        (hl, sum)
    };

    let slug = slugify(&topic);
    let id_hash = identity_hash(&topic, stem);
    let entry_id = format!("urn:last30days:{slug}:{id_hash}:{published_date}");
    let indexable_fingerprint = indexable_fingerprint_from_public(&public_content);

    Ok(MarkdownFileMeta {
        path: path.to_owned(),
        topic,
        slug,
        identity_hash: id_hash,
        published_date,
        entry_id,
        headline,
        summary,
        source_format: source_format.to_owned(),
        unbounded_private,
        indexable_fingerprint,
        redacted,
    })
}

fn read_regular_markdown_file(
    path: &Path,
    expected_dev_ino: Option<(u64, u64)>,
) -> Result<String, String> {
    let lmeta = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if lmeta.file_type().is_symlink() {
        return Err(format!("Refusing to follow symlink: {}", path.display()));
    }
    if !lmeta.file_type().is_file() {
        return Err(format!("Not a regular file: {}", path.display()));
    }

    let mut opts = std::fs::OpenOptions::new();
    opts.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        // O_NOFOLLOW prevents TOCTOU symlink swaps; O_NONBLOCK prevents blocking on a swapped FIFO.
        opts.custom_flags(unix_open_flags());
    }
    let file = opts.open(path).map_err(|e| e.to_string())?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.file_type().is_file() {
        return Err(format!("Not a regular file: {}", path.display()));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt as _;
        if (meta.dev(), meta.ino()) != (lmeta.dev(), lmeta.ino()) {
            return Err(format!("File changed during open: {}", path.display()));
        }
        if let Some((exp_dev, exp_ino)) = expected_dev_ino {
            if (meta.dev(), meta.ino()) != (exp_dev, exp_ino) {
                return Err(format!("File changed during open: {}", path.display()));
            }
        }
    }
    #[cfg(not(unix))]
    let _ = expected_dev_ino;

    if meta.len() > MAX_LIBRARY_FILE_BYTES {
        return Err(format!("file exceeds {MAX_LIBRARY_FILE_BYTES} byte limit"));
    }

    let mut raw = Vec::new();
    let limit_plus_one = MAX_LIBRARY_FILE_BYTES + 1;
    let mut handle = File::take(file, limit_plus_one);
    handle.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    if u64::try_from(raw.len()).unwrap_or(u64::MAX) > MAX_LIBRARY_FILE_BYTES {
        return Err(format!("file exceeds {MAX_LIBRARY_FILE_BYTES} byte limit"));
    }

    let text = String::from_utf8(raw).map_err(|e| e.to_string())?;
    if text.contains('\r') {
        Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
    } else {
        Ok(text)
    }
}

#[cfg(unix)]
const fn unix_open_flags() -> i32 {
    #[cfg(target_os = "macos")]
    {
        // O_NOFOLLOW (0x0100) | O_NONBLOCK (0x0004)
        0x0100 | 0x0004
    }
    #[cfg(target_os = "linux")]
    {
        #[cfg(any(target_arch = "aarch64", target_arch = "arm"))]
        {
            // O_NOFOLLOW (0o100000) | O_NONBLOCK (0o4000)
            0o100_000 | 0o4000
        }
        #[cfg(not(any(target_arch = "aarch64", target_arch = "arm")))]
        {
            // O_NOFOLLOW (0o400000) | O_NONBLOCK (0o4000)
            0o400_000 | 0o4000
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        0
    }
}

/// Reads, sanitizes, extracts metadata, and fingerprints a batch of saved Markdown
/// files in parallel using Rayon.
pub fn scan_markdown_files_batch(requests: &[MarkdownFileRequest]) -> Vec<MarkdownScanOutcome> {
    requests
        .par_iter()
        .map(|req| {
            let path = Path::new(&req.path);
            let content = match read_regular_markdown_file(path, req.expected_dev_ino) {
                Ok(text) => text,
                Err(reason) => {
                    return MarkdownScanOutcome::Skipped {
                        path: req.path.clone(),
                        reason,
                    };
                }
            };
            match parse_markdown_meta(
                &content,
                &req.path,
                &req.stem,
                &req.source_format,
                &req.fallback_date,
            ) {
                Ok(meta) => MarkdownScanOutcome::Ok { meta },
                Err(err) => MarkdownScanOutcome::Skipped {
                    path: req.path.clone(),
                    reason: err.to_string(),
                },
            }
        })
        .collect()
}
