//! Query-centric token-overlap relevance scoring.
//!
//! Mirrors `skills/last30days/scripts/lib/relevance.py`.

use std::collections::BTreeSet;

use crate::cjk;
use crate::dedupe;

/// Shared relevance floor below which posts are treated as off-topic.
pub const RELEVANCE_FLOOR: f64 = 0.1;

/// Cap applied when only generic low-signal tokens overlapped (`0.09`).
pub const GENERIC_ONLY_CAP: f64 = 0.09;

/// English and Hebrew stopwords excluded from relevance tokenization.
pub const STOPWORDS: &[&str] = &[
    "the",
    "a",
    "an",
    "to",
    "for",
    "how",
    "is",
    "in",
    "of",
    "on",
    "and",
    "with",
    "from",
    "by",
    "at",
    "this",
    "that",
    "it",
    "my",
    "your",
    "i",
    "me",
    "we",
    "you",
    "what",
    "are",
    "do",
    "can",
    "its",
    "be",
    "or",
    "not",
    "no",
    "so",
    "if",
    "but",
    "about",
    "all",
    "just",
    "get",
    "has",
    "have",
    "was",
    "will", // Hebrew function words
    "את",
    "של",
    "על",
    "עם",
    "אל",
    "כי",
    "לא",
    "הוא",
    "היא",
    "הם",
    "הן",
    "אנו",
    "אנחנו",
    "זה",
    "זו",
    "זאת",
    "כל",
    "יש",
    "אין",
    "כבר",
    "רק",
    "גם",
    "כן",
    "אם",
    "או",
    "אבל",
    "כך",
    "מה",
    "מי",
    "איך",
    "למה",
    "כמה",
    "היה",
    "הייתה",
    "היו",
    "יהיה",
    "יהיו",
    // Hebrew single-letter prefixes
    "ה",
    "ב",
    "ל",
    "מ",
    "כ",
    "ו",
    "ש",
];

/// Generic query words that cannot carry relevance on their own.
pub const LOW_SIGNAL_QUERY_TOKENS: &[&str] = &[
    "advice",
    "animation",
    "animations",
    "best",
    "chance",
    "chances",
    "code",
    "compare",
    "comparison",
    "differences",
    "explain",
    "guide",
    "guides",
    "how",
    "latest",
    "news",
    "odds",
    "opinion",
    "opinions",
    "prediction",
    "predictions",
    "probability",
    "probabilities",
    "prompt",
    "prompting",
    "prompts",
    "rate",
    "review",
    "reviews",
    "thoughts",
    "tip",
    "tips",
    "tutorial",
    "tutorials",
    "update",
    "updates",
    "use",
    "using",
    "versus",
    "vs",
    "worth",
    // Planner ranking-query scaffolding
    "30",
    "current",
    "days",
    "describing",
    "especially",
    "evidence",
    "exist",
    "follow",
    "hands",
    "last",
    "matter",
    "most",
    "new",
    "people",
    "real",
    "recent",
    "relevant",
    "running",
    "up",
    "world",
];

/// Returns synonym expansions for `token`, matching `relevance.SYNONYMS`.
pub fn synonyms_for(token: &str) -> &'static [&'static str] {
    match token {
        "hip" | "hop" => &["rap", "hiphop"],
        "rap" => &["hip", "hop", "hiphop"],
        "hiphop" => &["rap", "hip", "hop"],
        "js" => &["javascript"],
        "javascript" => &["js"],
        "ts" => &["typescript"],
        "typescript" => &["ts"],
        "ai" => &["artificial", "intelligence"],
        "ml" => &["machine", "learning"],
        "react" => &["reactjs"],
        "reactjs" => &["react"],
        "svelte" => &["sveltejs"],
        "sveltejs" => &["svelte"],
        "vue" => &["vuejs"],
        "vuejs" => &["vue"],
        _ => &[],
    }
}

/// Returns `true` if `token` is a relevance stopword.
#[inline]
pub fn is_stopword(token: &str) -> bool {
    STOPWORDS.contains(&token) || cjk::is_chinese_stopword(token)
}

/// Returns `true` if `token` is a low-signal generic query word.
#[inline]
pub fn is_low_signal(token: &str) -> bool {
    LOW_SIGNAL_QUERY_TOKENS.contains(&token)
}

/// Lowercases, segments, removes stopwords and single-char tokens, and expands synonyms.
pub fn tokenize(text: &str) -> BTreeSet<String> {
    let mut base = BTreeSet::new();
    for word in cjk::segment(text) {
        if word.chars().nth(1).is_some() && !is_stopword(&word) {
            base.insert(word);
        }
    }
    let mut expanded = base.clone();
    for token in &base {
        for &syn in synonyms_for(token) {
            expanded.insert(syn.to_owned());
        }
    }
    expanded
}

/// Precomputed query representation reused across items in a stream.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedQuery {
    raw: String,
    q_tokens: BTreeSet<String>,
    informative_q_tokens: BTreeSet<String>,
    normalized_phrase: String,
    phrase_has_cjk: bool,
    phrase_no_spaces: String,
    phrase_word_count: usize,
}

impl PreparedQuery {
    /// Builds a [`PreparedQuery`] from a raw search or ranking query.
    pub fn new(query: &str) -> Self {
        let q_tokens = tokenize(query);
        let informative: BTreeSet<String> = q_tokens
            .iter()
            .filter(|t| !is_low_signal(t))
            .cloned()
            .collect();
        let informative_q_tokens = if informative.is_empty() {
            q_tokens.clone()
        } else {
            informative
        };
        let normalized_phrase = dedupe::normalize_text(query);
        let phrase_has_cjk = cjk::has_cjk(&normalized_phrase);
        let phrase_no_spaces = if phrase_has_cjk {
            normalized_phrase.replace(' ', "")
        } else {
            String::new()
        };
        let phrase_word_count = normalized_phrase.split_whitespace().count();
        Self {
            raw: query.to_owned(),
            q_tokens,
            informative_q_tokens,
            normalized_phrase,
            phrase_has_cjk,
            phrase_no_spaces,
            phrase_word_count,
        }
    }

    /// Returns the original raw query string.
    pub fn raw(&self) -> &str {
        &self.raw
    }

    /// Returns the expanded query token set.
    pub fn tokens(&self) -> &BTreeSet<String> {
        &self.q_tokens
    }

    /// Returns the informative (non-generic) query token set.
    pub fn informative_tokens(&self) -> &BTreeSet<String> {
        &self.informative_q_tokens
    }

    /// Returns the normalized phrase used for exact phrase bonus checks.
    pub fn normalized_phrase(&self) -> &str {
        &self.normalized_phrase
    }
}

#[inline]
#[expect(
    clippy::as_conversions,
    clippy::cast_precision_loss,
    reason = "token counts are bounded by item length and convert cleanly to f64"
)]
fn usize_to_f64(value: usize) -> f64 {
    value as f64
}

/// Rounds `value` to 2 decimal places using banker's rounding (matches Python `round(x, 2)`).
#[inline]
pub(crate) fn round_2(value: f64) -> f64 {
    (value * 100.0).round_ties_even() / 100.0
}

/// Computes a query-centric relevance score in `[0.0, 1.0]` (`0.5` for empty queries).
pub fn token_overlap_relevance(query: &PreparedQuery, text: &str, hashtags: &[String]) -> f64 {
    let combined_owned;
    let combined: &str = if hashtags.is_empty() {
        text
    } else {
        let mut buf = String::with_capacity(
            text.len() + 1 + hashtags.iter().map(|h| h.len() + 1).sum::<usize>(),
        );
        buf.push_str(text);
        for tag in hashtags {
            buf.push(' ');
            buf.push_str(tag);
        }
        combined_owned = buf;
        &combined_owned
    };

    let mut t_tokens = tokenize(combined);

    if !hashtags.is_empty() {
        for tag in hashtags {
            let tag_lower = tag.to_lowercase();
            for qt in &query.q_tokens {
                if tag_lower.contains(qt.as_str()) && qt.as_str() != tag_lower.as_str() {
                    t_tokens.insert(qt.clone());
                }
            }
        }
    }

    if query.q_tokens.is_empty() {
        return 0.5;
    }

    let overlap = query.q_tokens.intersection(&t_tokens).count();
    if overlap == 0 {
        return 0.0;
    }

    let informative_hits = query.informative_q_tokens.intersection(&t_tokens).count();
    let coverage = usize_to_f64(overlap) / usize_to_f64(query.q_tokens.len());
    let informative_overlap =
        usize_to_f64(informative_hits) / usize_to_f64(query.informative_q_tokens.len());
    let precision_denom = t_tokens.len().min(query.q_tokens.len() + 4).max(1);
    let precision = usize_to_f64(overlap) / usize_to_f64(precision_denom);

    let mut phrase_bonus = 0.0;
    if !query.normalized_phrase.is_empty() {
        let normalized_text = dedupe::normalize_text(combined);
        let mut contained = normalized_text.contains(&query.normalized_phrase);
        if !contained && query.phrase_has_cjk {
            let stripped_text = normalized_text.replace(' ', "");
            contained = stripped_text.contains(&query.phrase_no_spaces);
        }
        if contained {
            phrase_bonus = if query.phrase_word_count > 1 {
                0.12
            } else {
                0.16
            };
        }
    }

    let base = 0.55 * coverage.powf(1.35) + 0.25 * informative_overlap + 0.20 * precision;

    if !query.informative_q_tokens.is_empty() && informative_hits == 0 {
        return round_2(GENERIC_ONLY_CAP.min(base));
    }

    round_2((base + phrase_bonus).min(1.0))
}
