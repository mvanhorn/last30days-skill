//! Reusable local scoring signals for pipeline stages.
//!
//! Mirrors `skills/last30days/scripts/lib/signals.py` and `dates.py:recency_score`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::error::CoreError;
use crate::relevance::{self, PreparedQuery};

/// Freshness decay curve applied to item publication dates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum FreshnessMode {
    /// Full 0..100 recency decay (`int(score)`).
    StrictRecent,
    /// Compressed decay with a 40-point floor (`int(score * 0.6 + 40)`).
    EvergreenOk,
    /// Default balanced decay (`int(score * 0.8 + 10)`).
    #[default]
    BalancedRecent,
}

/// Whether an item's date represents a live repository snapshot rather than an event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum DateKind {
    /// Normal event or publication date.
    #[default]
    Event,
    /// Snapshot date (e.g. a live GitHub repo card), ranked at freshness 50.
    Snapshot,
}

/// Whether an item is exempt from lexical grounding checks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum GroundingPolicy {
    /// Standard lexical relevance scoring.
    #[default]
    Standard,
    /// Exempt from low lexical overlap (minimum 0.8 relevance floor).
    Exempt,
}

/// Whether a GitHub item is a pull request or a repository/issue record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum GithubItemKind {
    /// Issue, discussion, or non-PR record.
    #[default]
    Standard,
    /// Pull request (`metadata.is_pr` is truthy).
    PullRequest,
}

/// Minimal item projection needed to compute local scoring signals.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SignalItem {
    /// Source platform identifier (e.g. `"reddit"`, `"youtube"`, `"github"`).
    pub source: String,
    /// Item title.
    pub title: String,
    /// Item body text.
    pub body: String,
    /// Item snippet text.
    pub snippet: String,
    /// Optional `YYYY-MM-DD` publication date.
    pub published_at: Option<String>,
    /// Event vs. snapshot date semantics.
    #[serde(default)]
    pub date_kind: DateKind,
    /// Optional hashtags attached to the item (`TikTok` / Instagram).
    #[serde(default)]
    pub hashtags: Vec<String>,
    /// Optional labels attached to the item (e.g. `"project-mode"`, `"velocity"`).
    #[serde(default)]
    pub labels: Vec<String>,
    /// Grounding exemption policy.
    #[serde(default)]
    pub grounding: GroundingPolicy,
    /// GitHub item kind for repo-snapshot detection.
    #[serde(default)]
    pub github_kind: GithubItemKind,
    /// Numeric engagement counters (non-numeric or boolean values omitted by caller).
    #[serde(default)]
    pub engagement: BTreeMap<String, f64>,
    /// Whether the original engagement map contained a `"stars"` key (even if null/0).
    #[serde(default)]
    pub has_stars_key: bool,
    /// Raw numeric score of `metadata["top_comments"][0]["score"]` when present.
    pub top_comment_score: Option<f64>,
}

/// Computed local scoring metadata for one item in [`annotate_stream`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AnnotatedSignal {
    /// Original index of the item in the input slice.
    pub index: usize,
    /// Computed query-centric relevance in `[0.0, 1.0]`.
    pub local_relevance: f64,
    /// Computed freshness score in `0..=100`.
    pub freshness: i32,
    /// Absolute cross-stream engagement score in `0..=100`, or `None`.
    pub engagement_score: Option<i32>,
    /// Editorial source quality multiplier.
    pub source_quality: f64,
    /// Composite local ranking score used to sort the stream.
    pub local_rank_score: f64,
}

const VIEW_ENGAGEMENT_LOG_FLOOR: f64 = 2.1;
const VIEW_ENGAGEMENT_LOG_REFERENCE: f64 = 11.5;

/// Returns the editorial signal-to-noise quality score for `source`.
pub fn source_quality(source: &str) -> f64 {
    match source {
        "arxiv" => 0.9,
        "youtube" | "digg" | "techmeme" => 0.85,
        "hackernews" | "amazon" => 0.8,
        "devto" | "trustpilot" => 0.78,
        "corpus" => 0.75,
        "meta_ads" | "jobs" | "v2ex" => 0.72,
        "xiaohongshu" => 0.7,
        "x" => 0.68,
        "bluesky" => 0.66,
        "instagram" | "tiktok" => 0.58,
        "polymarket" => 0.5,
        _ => 0.6,
    }
}

/// Safe `ln(1 + x)` matching `signals.log1p_safe`.
#[inline]
pub fn log1p_safe(value: Option<f64>) -> f64 {
    match value {
        Some(v) if v.is_finite() && v > 0.0 => v.ln_1p(),
        _ => 0.0,
    }
}

fn is_no_engagement_score_source(source: &str) -> bool {
    matches!(
        source,
        "jobs"
            | "meta_ads"
            | "grounding"
            | "perplexity"
            | "stocktwits"
            | "telegram"
            | "arxiv"
            | "techmeme"
            | "dripstack"
            | "corpus"
    )
}

fn is_view_engagement_source(source: &str) -> bool {
    matches!(source, "youtube" | "tiktok" | "instagram")
}

fn engagement_log_reference(source: &str) -> Option<f64> {
    match source {
        "reddit" | "linkedin" | "linkedin_profile" => Some(7.6),
        "hackernews" => Some(6.2),
        "devto" => Some(3.0),
        "youtube" | "tiktok" => Some(10.3),
        "instagram" | "x" | "bluesky" | "truthsocial" | "threads" | "xiaohongshu" | "pinterest"
        | "github" => Some(9.2),
        "polymarket" => Some(15.2),
        "digg" => Some(4.5),
        "v2ex" => Some(5.0),
        "trustpilot" | "amazon" => Some(11.5),
        _ => None,
    }
}

fn weighted_engagement(engagement: &BTreeMap<String, f64>, weights: &[(&str, f64)]) -> Option<f64> {
    let mut any_positive = false;
    let mut sum = 0.0;
    for &(field, weight) in weights {
        let val = log1p_safe(engagement.get(field).copied());
        if val > 0.0 {
            any_positive = true;
            sum += val * weight;
        }
    }
    any_positive.then_some(sum)
}

fn source_engagement_weights(source: &str) -> Option<&'static [(&'static str, f64)]> {
    match source {
        "x" | "threads" => Some(&[
            ("likes", 0.55),
            ("reposts", 0.25),
            ("replies", 0.15),
            ("quotes", 0.05),
        ]),
        "hackernews" => Some(&[("points", 0.55), ("comments", 0.45)]),
        "devto" => Some(&[("reactions", 0.60), ("comments", 0.40)]),
        "v2ex" => Some(&[("replies", 1.0)]),
        "bluesky" => Some(&[
            ("likes", 0.40),
            ("reposts", 0.30),
            ("replies", 0.20),
            ("quotes", 0.10),
        ]),
        "truthsocial" => Some(&[("likes", 0.45), ("reposts", 0.30), ("replies", 0.25)]),
        "pinterest" => Some(&[("saves", 0.70), ("comments", 0.30)]),
        "linkedin" => Some(&[("likes", 0.55), ("comments", 0.30), ("reposts", 0.15)]),
        "linkedin_profile" => Some(&[("likes", 0.55), ("comments", 0.30), ("shares", 0.15)]),
        "xiaohongshu" => Some(&[("likes", 0.50), ("favorites", 0.30), ("comments", 0.20)]),
        "polymarket" => Some(&[("volume", 0.60), ("liquidity", 0.40)]),
        "trustpilot" => Some(&[("reviews", 1.0)]),
        "amazon" => Some(&[("ratings", 1.0)]),
        "meta_ads" => Some(&[("variants", 1.0)]),
        _ => None,
    }
}

fn generic_engagement(engagement: &BTreeMap<String, f64>) -> Option<f64> {
    if engagement.is_empty() {
        return None;
    }
    let mut peak = 0.0_f64;
    let mut sum = 0.0_f64;
    let mut count = 0usize;
    for &v in engagement.values() {
        let logged = log1p_safe(Some(v));
        if logged > 0.0 {
            if logged > peak {
                peak = logged;
            }
            sum += logged;
            count += 1;
        }
    }
    (count > 0).then_some(peak + 0.1 * (sum - peak))
}

/// Computes the raw source-specific log-weighted engagement score for `item`.
pub fn engagement_raw(item: &SignalItem) -> Option<f64> {
    let top_comment = log1p_safe(item.top_comment_score);
    match item.source.as_str() {
        "reddit" => {
            let score = log1p_safe(item.engagement.get("score").copied());
            let comments = log1p_safe(item.engagement.get("num_comments").copied());
            let ratio = item
                .engagement
                .get("upvote_ratio")
                .copied()
                .filter(|v| v.is_finite())
                .unwrap_or(0.0);
            if score == 0.0 && comments == 0.0 && ratio == 0.0 && top_comment == 0.0 {
                return None;
            }
            Some(0.50 * score + 0.35 * comments + 0.05 * (ratio * 10.0) + 0.10 * top_comment)
        }
        "youtube" => {
            let views = log1p_safe(item.engagement.get("views").copied());
            let likes = log1p_safe(item.engagement.get("likes").copied());
            let comments = log1p_safe(item.engagement.get("comments").copied());
            if views == 0.0 && likes == 0.0 && comments == 0.0 && top_comment == 0.0 {
                return None;
            }
            Some(0.45 * views + 0.32 * likes + 0.13 * comments + 0.10 * top_comment)
        }
        "tiktok" | "instagram" => {
            let views = log1p_safe(item.engagement.get("views").copied());
            let likes = log1p_safe(item.engagement.get("likes").copied());
            let comments = log1p_safe(item.engagement.get("comments").copied());
            if views == 0.0 && likes == 0.0 && comments == 0.0 && top_comment == 0.0 {
                return None;
            }
            Some(0.45 * views + 0.27 * likes + 0.18 * comments + 0.10 * top_comment)
        }
        "digg" => {
            if log1p_safe(item.engagement.get("digg_score").copied()) > 0.0 {
                weighted_engagement(
                    &item.engagement,
                    &[
                        ("digg_score", 0.50),
                        ("postCount", 0.30),
                        ("uniqueAuthors", 0.20),
                    ],
                )
            } else {
                weighted_engagement(
                    &item.engagement,
                    &[("postCount", 0.60), ("uniqueAuthors", 0.40)],
                )
            }
        }
        "github" => {
            let is_velocity = item.labels.iter().any(|l| l == "velocity")
                || item.engagement.contains_key("merged_prs")
                || item.engagement.contains_key("total_prs");
            if is_velocity {
                weighted_engagement(
                    &item.engagement,
                    &[("merged_prs", 0.70), ("total_prs", 0.30)],
                )
            } else {
                weighted_engagement(&item.engagement, &[("reactions", 0.60), ("comments", 0.40)])
            }
        }
        other => source_engagement_weights(other).map_or_else(
            || generic_engagement(&item.engagement),
            |weights| weighted_engagement(&item.engagement, weights),
        ),
    }
}

/// Returns `true` when `item` is a GitHub repository snapshot card.
#[inline]
pub fn is_repo_snapshot(item: &SignalItem) -> bool {
    item.source == "github"
        && (item.has_stars_key || item.engagement.contains_key("stars"))
        && item.github_kind != GithubItemKind::PullRequest
}

/// Computes the normalized cross-stream engagement score in `0..=100`, or `None`.
pub fn engagement_score(item: &SignalItem) -> Option<i32> {
    if is_no_engagement_score_source(&item.source) || is_repo_snapshot(item) {
        return None;
    }
    let reference = engagement_log_reference(&item.source)?;
    let raw = engagement_raw(item)?;
    let fraction = if is_view_engagement_source(&item.source) {
        (raw - VIEW_ENGAGEMENT_LOG_FLOOR)
            / (VIEW_ENGAGEMENT_LOG_REFERENCE - VIEW_ENGAGEMENT_LOG_FLOOR)
    } else {
        raw / reference
    };
    let clamped = (100.0 * fraction.clamp(0.0, 1.0)).round_ties_even();
    Some(f64_to_i32_trunc(clamped))
}

#[inline]
#[expect(
    clippy::as_conversions,
    clippy::cast_possible_truncation,
    reason = "values are clamped to [0.0, 100.0] before truncating to i32"
)]
fn f64_to_i32_trunc(value: f64) -> i32 {
    value as i32
}

/// Parses a strict `YYYY-MM-DD` civil date into days since `1970-01-01`.
///
/// Uses Howard Hinnant's Gregorian calendar algorithm and validates month/day
/// ranges including leap years.
///
/// # Errors
///
/// Returns [`CoreError::InvalidDate`] if `date_str` is not a valid `YYYY-MM-DD` date.
pub fn parse_ymd_days(date_str: &str) -> Result<i64, CoreError> {
    let bytes = date_str.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return Err(CoreError::InvalidDate {
            input: date_str.to_owned(),
        });
    }
    let year: i64 = date_str[0..4].parse().map_err(|_| CoreError::InvalidDate {
        input: date_str.to_owned(),
    })?;
    let month: u32 = date_str[5..7].parse().map_err(|_| CoreError::InvalidDate {
        input: date_str.to_owned(),
    })?;
    let day: u32 = date_str[8..10]
        .parse()
        .map_err(|_| CoreError::InvalidDate {
            input: date_str.to_owned(),
        })?;
    if !(1..=12).contains(&month) || day == 0 {
        return Err(CoreError::InvalidDate {
            input: date_str.to_owned(),
        });
    }
    let is_leap = (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap {
                29
            } else {
                28
            }
        }
        _ => unreachable!("month already validated to 1..=12"),
    };
    if day > max_day {
        return Err(CoreError::InvalidDate {
            input: date_str.to_owned(),
        });
    }
    let m = i64::from(month);
    let d = i64::from(day);
    let y = year - i64::from(m <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Ok(era * 146_097 + doe - 719_468)
}

/// Calculates recency score (`0..=100`) relative to `reference_days`.
pub fn recency_score_from_days(
    published_at: Option<&str>,
    max_days: i64,
    reference_days: i64,
) -> i32 {
    let Some(raw) = published_at else {
        return 0;
    };
    let Ok(pub_days) = parse_ymd_days(raw) else {
        return 0;
    };
    let age = reference_days - pub_days;
    if age < 0 {
        return 100;
    }
    if max_days <= 0 || age >= max_days {
        return 0;
    }
    #[expect(
        clippy::as_conversions,
        clippy::cast_precision_loss,
        reason = "age and max_days are small day counts"
    )]
    let fraction = 1.0 - (age as f64) / (max_days as f64);
    f64_to_i32_trunc(100.0 * fraction)
}

/// Computes freshness score (`0..=100`) for `item`.
pub fn freshness_from_days(
    item: &SignalItem,
    mode: FreshnessMode,
    reference_days: i64,
    max_days: i64,
) -> i32 {
    if item.date_kind == DateKind::Snapshot {
        return 50;
    }
    let score = f64::from(recency_score_from_days(
        item.published_at.as_deref(),
        max_days,
        reference_days,
    ));
    match mode {
        FreshnessMode::StrictRecent => f64_to_i32_trunc(score),
        FreshnessMode::EvergreenOk => f64_to_i32_trunc((score * 0.6) + 40.0),
        FreshnessMode::BalancedRecent => f64_to_i32_trunc((score * 0.8) + 10.0),
    }
}

/// Computes local relevance for `item`, including `YouTube`, project-mode, and grounding-exempt floors.
pub fn local_relevance(item: &SignalItem, query: &PreparedQuery) -> f64 {
    let mut combined =
        String::with_capacity(item.title.len() + item.body.len() + item.snippet.len() + 2);
    for part in [
        item.title.as_str(),
        item.body.as_str(),
        item.snippet.as_str(),
    ] {
        if !part.is_empty() {
            if !combined.is_empty() {
                combined.push('\n');
            }
            combined.push_str(part);
        }
    }
    let mut score = relevance::token_overlap_relevance(query, &combined, &item.hashtags);

    if item.source == "youtube"
        && item
            .engagement
            .get("views")
            .copied()
            .filter(|v| v.is_finite())
            .unwrap_or(0.0)
            > 100_000.0
    {
        score = score.max(0.3);
    }
    if item.labels.iter().any(|l| l == "project-mode") {
        score = score.max(0.8);
    }
    if item.grounding == GroundingPolicy::Exempt {
        score = score.max(0.8);
    }
    score
}

/// Annotates every item with local scoring signals and returns them stably sorted
/// descending by `local_rank_score`.
///
/// # Errors
///
/// Returns [`CoreError::InvalidDate`] if `reference_date` is not a valid `YYYY-MM-DD` date.
pub fn annotate_stream(
    items: &[SignalItem],
    ranking_query: &str,
    freshness_mode: FreshnessMode,
    reference_date: &str,
    max_days: i64,
) -> Result<Vec<AnnotatedSignal>, CoreError> {
    let ref_days = parse_ymd_days(reference_date)?;
    let prepared = PreparedQuery::new(ranking_query);
    let mut out = Vec::with_capacity(items.len());

    for (index, item) in items.iter().enumerate() {
        let eng_score = engagement_score(item);
        let rel = local_relevance(item, &prepared);
        let fresh = freshness_from_days(item, freshness_mode, ref_days, max_days);
        let src_qual = source_quality(&item.source);
        let rank_score = 0.65 * rel
            + 0.25 * (f64::from(fresh) / 100.0)
            + 0.10 * (f64::from(eng_score.unwrap_or(0)) / 100.0);
        out.push(AnnotatedSignal {
            index,
            local_relevance: rel,
            freshness: fresh,
            engagement_score: eng_score,
            source_quality: src_qual,
            local_rank_score: rank_score,
        });
    }

    // Python's sorted(..., reverse=True) is stable: equal scores keep original order.
    out.sort_by(|a, b| b.local_rank_score.total_cmp(&a.local_rank_score));
    Ok(out)
}
