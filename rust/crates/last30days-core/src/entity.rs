//! Text entity extraction and overlap scoring for cross-source clustering.
//!
//! Mirrors `skills/last30days/scripts/lib/entity_extract.py`.

use std::collections::BTreeSet;

use crate::cjk;

/// Stopwords ignored during text entity extraction.
pub const ENTITY_STOPWORDS: &[&str] = &[
    "the", "a", "an", "to", "for", "how", "is", "in", "of", "on", "and", "with", "from", "by",
    "at", "this", "that", "it", "what", "are", "do", "can", "his", "her", "he", "she", "its",
    "was", "has", "new", "just", "says", "said", "will", "about", "after", "now", "all", "been",
    "here", "not", "out", "up", "more", "also", "but", "who", "year", "first", "make", "being",
    "making", "over", "into", "than", "they", "their", "would", "could", "get", "got", "some",
    "like", "back", "going", "breaking", "https", "http", "www", "com",
];

/// Returns `true` when `word` carries an anchor signal: leading uppercase,
/// all cased characters uppercase (matching Python `str.isupper()`), or any digit.
pub fn has_anchor_signal(word: &str) -> bool {
    let mut chars = word.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if first.is_uppercase() {
        return true;
    }
    let mut has_cased = first.is_lowercase();
    let mut all_upper = false;
    if first.is_numeric() {
        return true;
    }
    if !has_cased {
        all_upper = true;
    }
    for ch in chars {
        if ch.is_numeric() {
            return true;
        }
        if ch.is_uppercase() {
            has_cased = true;
        } else if ch.is_lowercase() {
            has_cased = true;
            all_upper = false;
        }
    }
    has_cased && all_upper
}

/// Extracts significant lowercased entity words used by clustering and evaluation.
pub fn extract_text_entities(text: &str) -> BTreeSet<String> {
    let mut cleaned = String::with_capacity(text.len());
    for ch in text.chars() {
        if cjk::is_word_char(ch) || ch.is_whitespace() {
            cleaned.push(ch);
        } else {
            cleaned.push(' ');
        }
    }
    let mut entities = BTreeSet::new();
    for word in cleaned.split_whitespace() {
        let lower = word.to_lowercase();
        let char_len = word.chars().count();
        if char_len <= 2 || ENTITY_STOPWORDS.contains(&lower.as_str()) {
            continue;
        }
        if has_anchor_signal(word) || char_len >= 4 {
            entities.insert(lower);
        }
    }
    entities
}

#[inline]
#[expect(
    clippy::as_conversions,
    clippy::cast_precision_loss,
    reason = "entity set sizes are small and convert cleanly to f64"
)]
fn ratio(num: usize, den: usize) -> f64 {
    (num as f64) / (den as f64)
}

/// Computes the Szymkiewicz-Simpson overlap coefficient for two entity sets.
pub fn entity_overlap(entities_a: &BTreeSet<String>, entities_b: &BTreeSet<String>) -> f64 {
    if entities_a.is_empty() || entities_b.is_empty() {
        return 0.0;
    }
    let shared = entities_a.intersection(entities_b).count();
    let min_len = entities_a.len().min(entities_b.len());
    ratio(shared, min_len)
}
