//! Within-source near-duplicate detection using character n-grams and token Jaccard.
//!
//! Mirrors `skills/last30days/scripts/lib/dedupe.py`. Uses dual 64-bit FNV-1a
//! fingerprints stored in sorted deduplicated vectors for fast exact set
//! intersections without per-ngram heap allocations.

use std::collections::BTreeSet;

use rayon::prelude::*;
use serde::{Deserialize, Serialize};

use crate::cjk;
use crate::error::CoreError;

/// English stopwords excluded from token Jaccard comparisons.
pub const ENGLISH_STOPWORDS: &[&str] = &[
    "the", "a", "an", "to", "for", "how", "is", "in", "of", "on", "and", "with", "from", "by",
    "at", "this", "that", "it", "what", "are", "do", "can",
];

/// Default n-gram width used by [`PreparedText`] and [`hybrid_similarity`].
pub const DEFAULT_NGRAM_SIZE: usize = 3;

/// Controls whether a candidate item participates in fuzzy text deduplication.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum DedupeMode {
    /// Keep the item without comparing its text against prior kept items.
    UrlOnly,
    /// Compare the item's text against prior kept fuzzy items.
    Fuzzy,
}

/// One item passed into [`dedupe_kept_indices`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DedupeEntry {
    /// Whether this entry participates in fuzzy text deduplication.
    pub mode: DedupeMode,
    /// Combined item text (`title`, `body`, `author`, `container`).
    pub text: String,
}

/// Returns `true` if `token` is a dedupe stopword (English or Chinese).
#[inline]
pub fn is_stopword(token: &str) -> bool {
    ENGLISH_STOPWORDS.contains(&token) || cjk::is_chinese_stopword(token)
}

/// Lowercases `text`, replaces non-word/non-whitespace characters with spaces,
/// and collapses runs of whitespace into a single ASCII space.
pub fn normalize_text(text: &str) -> String {
    let lowered = text.to_lowercase();
    let mut out = String::with_capacity(lowered.len());
    let mut in_space = true;
    for ch in lowered.chars() {
        if cjk::is_word_char(ch) {
            out.push(ch);
            in_space = false;
        } else if !in_space {
            out.push(' ');
            in_space = true;
        }
    }
    if out.ends_with(' ') {
        out.pop();
    }
    out
}

/// Extracts character n-grams of width `n` from normalized `text`.
///
/// # Errors
///
/// Returns [`CoreError::InvalidNgramSize`] if `n` is `0`.
pub fn ngrams(text: &str, n: usize) -> Result<BTreeSet<String>, CoreError> {
    if n == 0 {
        return Err(CoreError::InvalidNgramSize);
    }
    let norm = normalize_text(text);
    let mut set = BTreeSet::new();
    if norm.is_empty() {
        return Ok(set);
    }
    let offsets: Vec<usize> = norm.char_indices().map(|(i, _)| i).collect();
    if offsets.len() < n {
        set.insert(norm);
        return Ok(set);
    }
    for i in 0..=offsets.len() - n {
        let start = offsets[i];
        let end = if i + n < offsets.len() {
            offsets[i + n]
        } else {
            norm.len()
        };
        set.insert(norm[start..end].to_owned());
    }
    Ok(set)
}

/// Computes Jaccard similarity over two ordered string sets.
pub fn jaccard_similarity(left: &BTreeSet<String>, right: &BTreeSet<String>) -> f64 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    let intersection = left.intersection(right).count();
    let union = left.len() + right.len() - intersection;
    if union == 0 {
        return 0.0;
    }
    ratio(intersection, union)
}

/// Computes token Jaccard similarity between two raw texts.
pub fn token_jaccard(text_a: &str, text_b: &str) -> f64 {
    let a = PreparedText::new(text_a);
    let b = PreparedText::new(text_b);
    sorted_jaccard(&a.tokens, &b.tokens)
}

/// Computes the maximum of character trigram Jaccard and token Jaccard similarity.
pub fn hybrid_similarity(text_a: &str, text_b: &str) -> f64 {
    let a = PreparedText::new(text_a);
    let b = PreparedText::new(text_b);
    a.similarity(&b)
}

/// 64-bit FNV-1a hash with `SplitMix64` finalizer for low-collision, cache-dense fingerprints.
#[inline]
fn hash_64(bytes: &[u8]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

    let mut h = FNV_OFFSET;
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(FNV_PRIME);
    }
    h ^= h >> 30;
    h = h.wrapping_mul(0xbf58_476d_1ce4_e5b9);
    h ^= h >> 27;
    h = h.wrapping_mul(0x94d0_49bb_1331_11eb);
    h ^ (h >> 31)
}

#[inline]
#[expect(
    clippy::as_conversions,
    clippy::cast_precision_loss,
    reason = "set cardinalities are bounded by text length and convert cleanly to f64 for Jaccard ratio"
)]
fn ratio(num: usize, den: usize) -> f64 {
    (num as f64) / (den as f64)
}

/// Exact Jaccard similarity on two sorted, deduplicated fingerprint slices.
pub(crate) fn sorted_jaccard(left: &[u64], right: &[u64]) -> f64 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    let mut i = 0;
    let mut j = 0;
    let mut inter = 0usize;
    while i < left.len() && j < right.len() {
        match left[i].cmp(&right[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                inter += 1;
                i += 1;
                j += 1;
            }
        }
    }
    let union = left.len() + right.len() - inter;
    if union == 0 {
        return 0.0;
    }
    ratio(inter, union)
}

#[inline]
#[expect(
    clippy::as_conversions,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::cast_precision_loss,
    reason = "threshold is validated in [0.0, 1.0] and total length is a small non-negative integer"
)]
fn min_required_intersection(total_len: usize, threshold: f64) -> usize {
    let raw = (threshold * (total_len as f64)) / (1.0 + threshold);
    (raw.floor() as usize).saturating_sub(1)
}

/// Exact threshold check for sorted Jaccard with cardinality and remaining-element early exits.
#[inline]
fn sorted_jaccard_at_least(left: &[u64], right: &[u64], threshold: f64) -> bool {
    if left.is_empty() || right.is_empty() {
        return threshold <= 0.0;
    }
    let len_l = left.len();
    let len_r = right.len();
    let min_len = len_l.min(len_r);
    let max_len = len_l.max(len_r);
    // Exact upper bound: |A ∩ B| / |A ∪ B| <= min(|A|, |B|) / max(|A|, |B|).
    if ratio(min_len, max_len) < threshold {
        return false;
    }
    let total = len_l + len_r;
    let min_needed = min_required_intersection(total, threshold);

    let mut i = 0;
    let mut j = 0;
    let mut inter = 0usize;
    while i < len_l && j < len_r {
        // Exact early exit: even if every remaining element matched, we cannot reach min_needed.
        if inter + (len_l - i).min(len_r - j) < min_needed {
            return false;
        }
        match left[i].cmp(&right[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                inter += 1;
                i += 1;
                j += 1;
            }
        }
    }
    let union = total - inter;
    if union == 0 {
        return threshold <= 0.0;
    }
    ratio(inter, union) >= threshold
}

/// Precomputed trigram and token fingerprint sets for fast repeated similarity checks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedText {
    ngrams: Vec<u64>,
    tokens: Vec<u64>,
}

impl PreparedText {
    /// Builds a [`PreparedText`] from raw item text.
    pub fn new(raw: &str) -> Self {
        let norm = normalize_text(raw);
        let mut ngrams = Vec::new();
        if !norm.is_empty() {
            if norm.is_ascii() {
                let bytes = norm.as_bytes();
                if bytes.len() < DEFAULT_NGRAM_SIZE {
                    ngrams.push(hash_64(bytes));
                } else {
                    ngrams.reserve(bytes.len() - DEFAULT_NGRAM_SIZE + 1);
                    for window in bytes.windows(DEFAULT_NGRAM_SIZE) {
                        ngrams.push(hash_64(window));
                    }
                }
            } else {
                let offsets: Vec<usize> = norm.char_indices().map(|(i, _)| i).collect();
                if offsets.len() < DEFAULT_NGRAM_SIZE {
                    ngrams.push(hash_64(norm.as_bytes()));
                } else {
                    ngrams.reserve(offsets.len() - DEFAULT_NGRAM_SIZE + 1);
                    for i in 0..=offsets.len() - DEFAULT_NGRAM_SIZE {
                        let start = offsets[i];
                        let end = if i + DEFAULT_NGRAM_SIZE < offsets.len() {
                            offsets[i + DEFAULT_NGRAM_SIZE]
                        } else {
                            norm.len()
                        };
                        ngrams.push(hash_64(&norm.as_bytes()[start..end]));
                    }
                }
            }
            ngrams.sort_unstable();
            ngrams.dedup();
        }

        let mut tokens = Vec::new();
        cjk::for_each_segmented_lowered(&norm, |tok| {
            if tok.chars().nth(1).is_some() && !is_stopword(tok) {
                tokens.push(hash_64(tok.as_bytes()));
            }
        });
        tokens.sort_unstable();
        tokens.dedup();

        Self { ngrams, tokens }
    }

    /// Computes hybrid similarity (`max(trigram_jaccard, token_jaccard)`) against `other`.
    pub fn similarity(&self, other: &Self) -> f64 {
        sorted_jaccard(&self.ngrams, &other.ngrams).max(sorted_jaccard(&self.tokens, &other.tokens))
    }

    /// Returns `true` if [`Self::similarity`] is at least `threshold`.
    #[inline]
    pub fn similarity_at_least(&self, other: &Self, threshold: f64) -> bool {
        // Check smaller token set first; if tokens clear threshold, skip trigrams completely.
        sorted_jaccard_at_least(&self.tokens, &other.tokens, threshold)
            || sorted_jaccard_at_least(&self.ngrams, &other.ngrams, threshold)
    }
}

/// Returns the indices of entries that survive fuzzy near-duplicate removal.
///
/// Mirrors pass 2 of `dedupe.py:dedupe_items`: entries marked [`DedupeMode::UrlOnly`]
/// or with empty text are always kept without entering the fuzzy comparison pool;
/// remaining entries are kept only when their similarity to every previously kept
/// fuzzy entry is strictly below `threshold`.
///
/// # Errors
///
/// Returns [`CoreError::InvalidThreshold`] if `threshold` is non-finite or outside `[0.0, 1.0]`.
pub fn dedupe_kept_indices(
    entries: &[DedupeEntry],
    threshold: f64,
) -> Result<Vec<usize>, CoreError> {
    if !threshold.is_finite() || !(0.0..=1.0).contains(&threshold) {
        return Err(CoreError::InvalidThreshold { value: threshold });
    }
    let prepared: Vec<Option<PreparedText>> = if entries.len() >= 64 {
        entries
            .par_iter()
            .map(|entry| {
                if entry.mode == DedupeMode::UrlOnly || entry.text.is_empty() {
                    None
                } else {
                    Some(PreparedText::new(&entry.text))
                }
            })
            .collect()
    } else {
        entries
            .iter()
            .map(|entry| {
                if entry.mode == DedupeMode::UrlOnly || entry.text.is_empty() {
                    None
                } else {
                    Some(PreparedText::new(&entry.text))
                }
            })
            .collect()
    };

    let mut kept = Vec::with_capacity(entries.len());
    let mut kept_prepared: Vec<PreparedText> = Vec::with_capacity(entries.len());

    for (idx, maybe_prep) in prepared.into_iter().enumerate() {
        let Some(prep) = maybe_prep else {
            kept.push(idx);
            continue;
        };
        let is_dup = if kept_prepared.len() >= 128 {
            kept_prepared
                .par_iter()
                .any(|existing| prep.similarity_at_least(existing, threshold))
        } else {
            kept_prepared
                .iter()
                .any(|existing| prep.similarity_at_least(existing, threshold))
        };
        if !is_dup {
            kept.push(idx);
            kept_prepared.push(prep);
        }
    }
    Ok(kept)
}
