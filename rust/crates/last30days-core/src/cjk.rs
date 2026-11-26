//! CJK-aware tokenization for relevance scoring and near-duplicate detection.
//!
//! Mirrors `skills/last30days/scripts/lib/cjk.py` (dictionary-free bigram path).

/// High-frequency Chinese function words that dilute overlap signal.
pub const CHINESE_STOPWORDS: &[&str] = &[
    "的",
    "了",
    "和",
    "是",
    "在",
    "我",
    "有",
    "也",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "这个",
    "那个",
    "什么",
    "怎么",
    "为什么",
    "以及",
    "或者",
    "但是",
    "因为",
    "所以",
    "如果",
    "可以",
    "这样",
    "那样",
    "他们",
    "我们",
    "你们",
    "它",
    "她",
    "他",
    "吗",
    "呢",
    "吧",
    "啊",
    "哦",
    "嗯",
    "与",
    "及",
    "等",
    "被",
    "把",
    "让",
    "给",
    "向",
    "还",
    "再",
    "又",
    "从",
    "对",
    "为",
    "以",
    "之",
    "其",
    "中",
];

/// Returns `true` if `ch` is a CJK ideograph, Japanese kana, or Korean hangul.
#[inline]
pub fn is_cjk_char(ch: char) -> bool {
    matches!(
        ch,
        '\u{3400}'..='\u{4DBF}'
            | '\u{4E00}'..='\u{9FFF}'
            | '\u{F900}'..='\u{FAFF}'
            | '\u{3040}'..='\u{30FF}'
            | '\u{AC00}'..='\u{D7AF}'
    )
}

/// Returns `true` if `text` contains any CJK, kana, or hangul character.
#[inline]
pub fn has_cjk(text: &str) -> bool {
    !text.is_empty() && text.chars().any(is_cjk_char)
}

/// Returns `true` if `ch` matches Python's Unicode `\w` (alphanumeric or underscore).
#[inline]
pub(crate) fn is_word_char(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

/// Returns `true` if `token` is in [`CHINESE_STOPWORDS`].
#[inline]
pub fn is_chinese_stopword(token: &str) -> bool {
    CHINESE_STOPWORDS.contains(&token)
}

/// Pushes maximal `\w+` runs from `slice` into `out`.
pub(crate) fn push_word_runs(slice: &str, out: &mut Vec<String>) {
    let mut start: Option<usize> = None;
    for (idx, ch) in slice.char_indices() {
        if is_word_char(ch) {
            if start.is_none() {
                start = Some(idx);
            }
        } else if let Some(s) = start.take() {
            out.push(slice[s..idx].to_owned());
        }
    }
    if let Some(s) = start {
        out.push(slice[s..].to_owned());
    }
}

/// Pushes character bigrams (or the single character for a 1-char run) for a CJK run.
fn push_cjk_bigrams(run: &str, out: &mut Vec<String>) {
    let mut iter = run.char_indices();
    let Some((first_idx, _)) = iter.next() else {
        return;
    };
    let Some((second_idx, _)) = iter.next() else {
        out.push(run[first_idx..].to_owned());
        return;
    };
    let mut prev_start = first_idx;
    let mut cur_start = second_idx;
    for (next_idx, _) in iter {
        out.push(run[prev_start..next_idx].to_owned());
        prev_start = cur_start;
        cur_start = next_idx;
    }
    out.push(run[prev_start..].to_owned());
}

/// Tokenizes mixed CJK and Latin text into lowercased tokens in order.
///
/// Non-CJK runs use Unicode `\w+` words; CJK runs use dictionary-free character
/// bigrams (single character for a length-1 run).
pub fn segment(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    let lowered = text.to_lowercase();
    let mut out = Vec::new();
    if !has_cjk(&lowered) {
        push_word_runs(&lowered, &mut out);
        return out;
    }

    let mut pos = 0;
    let mut cjk_start: Option<usize> = None;
    for (idx, ch) in lowered.char_indices() {
        if is_cjk_char(ch) {
            if cjk_start.is_none() {
                if idx > pos {
                    push_word_runs(&lowered[pos..idx], &mut out);
                }
                cjk_start = Some(idx);
            }
        } else if let Some(start) = cjk_start.take() {
            push_cjk_bigrams(&lowered[start..idx], &mut out);
            pos = idx;
        }
    }
    if let Some(start) = cjk_start {
        push_cjk_bigrams(&lowered[start..], &mut out);
    } else if pos < lowered.len() {
        push_word_runs(&lowered[pos..], &mut out);
    }
    out
}

/// Visits every segmented token of an already-lowercased string without allocating
/// an intermediate `Vec<String>`.
pub(crate) fn for_each_segmented_lowered<F>(lowered: &str, mut visit: F)
where
    F: FnMut(&str),
{
    if lowered.is_empty() {
        return;
    }
    let emit_words = |slice: &str, visit: &mut F| {
        let mut start: Option<usize> = None;
        for (idx, ch) in slice.char_indices() {
            if is_word_char(ch) {
                if start.is_none() {
                    start = Some(idx);
                }
            } else if let Some(s) = start.take() {
                visit(&slice[s..idx]);
            }
        }
        if let Some(s) = start {
            visit(&slice[s..]);
        }
    };

    if !has_cjk(lowered) {
        emit_words(lowered, &mut visit);
        return;
    }

    let emit_cjk = |run: &str, visit: &mut F| {
        let mut iter = run.char_indices();
        let Some((first_idx, _)) = iter.next() else {
            return;
        };
        let Some((second_idx, _)) = iter.next() else {
            visit(&run[first_idx..]);
            return;
        };
        let mut prev_start = first_idx;
        let mut cur_start = second_idx;
        for (next_idx, _) in iter {
            visit(&run[prev_start..next_idx]);
            prev_start = cur_start;
            cur_start = next_idx;
        }
        visit(&run[prev_start..]);
    };

    let mut pos = 0;
    let mut cjk_start: Option<usize> = None;
    for (idx, ch) in lowered.char_indices() {
        if is_cjk_char(ch) {
            if cjk_start.is_none() {
                if idx > pos {
                    emit_words(&lowered[pos..idx], &mut visit);
                }
                cjk_start = Some(idx);
            }
        } else if let Some(start) = cjk_start.take() {
            emit_cjk(&lowered[start..idx], &mut visit);
            pos = idx;
        }
    }
    if let Some(start) = cjk_start {
        emit_cjk(&lowered[start..], &mut visit);
    } else if pos < lowered.len() {
        emit_words(&lowered[pos..], &mut visit);
    }
}
