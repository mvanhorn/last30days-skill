//! Ported behavior parity tests mapping back to Python's unit test suites.

use std::collections::{BTreeMap, BTreeSet};

use last30days_core::cjk;
use last30days_core::cluster::{ClusterCandidate, ClusterPlan, WindowState, cluster_candidates};
use last30days_core::dedupe::{
    self, DedupeEntry, DedupeMode, dedupe_kept_indices, hybrid_similarity, jaccard_similarity,
    ngrams, normalize_text, token_jaccard,
};
use last30days_core::entity::{entity_overlap, extract_text_entities, has_anchor_signal};
use last30days_core::markdown::{
    self, MarkdownBatchItem, PRIVATE_CORPUS_END, PRIVATE_CORPUS_START, PUBLIC_CLUSTERS_MARKER,
    PUBLIC_DRILL_MARKER, PUBLIC_FRESHNESS_MARKER,
};
use last30days_core::relevance::{PreparedQuery, token_overlap_relevance, tokenize};
use last30days_core::signals::{
    DateKind, FreshnessMode, GithubItemKind, GroundingPolicy, SignalItem, annotate_stream,
    engagement_raw, engagement_score,
};

fn str_set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

fn approx_eq(a: f64, b: f64, eps: f64) {
    assert!(
        (a - b).abs() <= eps,
        "expected {a} to be within {eps} of {b}"
    );
}

// ---------------------------------------------------------------------------
// Ported from tests/test_cjk.py
// ---------------------------------------------------------------------------

#[test]
fn cjk_has_cjk_matches_python() {
    assert!(cjk::has_cjk("国产大模型"), "Chinese text has CJK");
    assert!(cjk::has_cjk("GPT4很强"), "mixed text has CJK");
    assert!(!cjk::has_cjk("hello world"), "ASCII text has no CJK");
    assert!(!cjk::has_cjk(""), "empty string has no CJK");
}

#[test]
fn cjk_segment_ascii_chinese_mixed_and_single() {
    assert_eq!(
        cjk::segment("best react hooks"),
        vec!["best", "react", "hooks"],
        "ASCII segmentation"
    );
    let zh = cjk::segment("大模型");
    assert!(zh.contains(&"大模".to_owned()), "missing bigram 大模");
    assert!(zh.contains(&"模型".to_owned()), "missing bigram 模型");

    let mixed = cjk::segment("GPT4很强 react");
    assert!(mixed.contains(&"gpt4".to_owned()), "missing gpt4");
    assert!(mixed.contains(&"react".to_owned()), "missing react");
    assert!(mixed.contains(&"很强".to_owned()), "missing 很强");

    assert_eq!(cjk::segment("中"), vec!["中"], "single CJK char");
}

#[test]
fn cjk_chinese_relevance_and_phrase_bonus() {
    let q = PreparedQuery::new("国产大模型 测评");
    let score = token_overlap_relevance(&q, "这是国产大模型的最新测评", &[]);
    assert!(score > 0.5, "expected score > 0.5, got {score}");

    let unrelated = token_overlap_relevance(&q, "今天天气很好适合出门散步", &[]);
    approx_eq(unrelated, 0.0, 1e-9);

    let contiguous = token_overlap_relevance(&q, "国产大模型测评合集", &[]);
    let scattered = token_overlap_relevance(&q, "测评了很多东西也聊到国产大模型", &[]);
    assert!(
        contiguous > scattered,
        "contiguous {contiguous} should beat scattered {scattered}"
    );

    let en_q = PreparedQuery::new("react hooks");
    approx_eq(
        token_overlap_relevance(&en_q, "react myreacthooks bundle", &[]),
        0.58,
        1e-9,
    );
    approx_eq(
        token_overlap_relevance(&en_q, "react unrelated bundle", &[]),
        0.58,
        1e-9,
    );
}

#[test]
fn cjk_chinese_dedupe() {
    let near = hybrid_similarity("国产大模型最新测评对比", "国产大模型测评对比最新");
    assert!(near > 0.5, "reordered Chinese should be near-dup: {near}");
    let distinct = hybrid_similarity("国产大模型测评", "今天天气很好出门散步");
    assert!(
        distinct < 0.3,
        "distinct Chinese should have low sim: {distinct}"
    );
}

// ---------------------------------------------------------------------------
// Ported from tests/test_dedupe_v3.py
// ---------------------------------------------------------------------------

#[test]
fn dedupe_normalize_text() {
    assert_eq!(normalize_text("Hello World"), "hello world", "lowercases");
    assert_eq!(
        normalize_text("it's a test!"),
        "it s a test",
        "strips punctuation"
    );
    assert_eq!(normalize_text("a   b\t\nc"), "a b c", "collapses ws");
    assert_eq!(normalize_text(""), "", "empty");
}

#[test]
fn dedupe_ngrams() {
    assert_eq!(
        ngrams("abcde", 3).unwrap(),
        str_set(&["abc", "bcd", "cde"]),
        "simple trigrams"
    );
    assert_eq!(ngrams("ab", 3).unwrap(), str_set(&["ab"]), "short text");
    assert!(ngrams("", 3).unwrap().is_empty(), "empty text");
    assert_eq!(
        ngrams("A!B", 3).unwrap(),
        str_set(&["a b"]),
        "normalizes before ngrams"
    );
    assert!(ngrams("abc", 0).is_err(), "zero n-gram rejected");
}

#[test]
fn dedupe_jaccard_and_token_jaccard() {
    approx_eq(
        jaccard_similarity(&str_set(&["a", "b"]), &str_set(&["a", "b"])),
        1.0,
        1e-9,
    );
    approx_eq(
        jaccard_similarity(&str_set(&["a"]), &str_set(&["b"])),
        0.0,
        1e-9,
    );
    approx_eq(
        jaccard_similarity(&str_set(&["a", "b", "c"]), &str_set(&["b", "c", "d"])),
        0.5,
        1e-9,
    );
    approx_eq(
        jaccard_similarity(&BTreeSet::new(), &str_set(&["a"])),
        0.0,
        1e-9,
    );
    approx_eq(token_jaccard("hello world", "hello world"), 1.0, 1e-9);
    approx_eq(token_jaccard("alpha beta", "gamma delta"), 0.0, 1e-9);
    approx_eq(token_jaccard("the big cat", "a big dog"), 1.0 / 3.0, 1e-9);
    approx_eq(
        token_jaccard("x shared alpha", "x shared beta"),
        1.0 / 3.0,
        1e-9,
    );
    approx_eq(token_jaccard("x alpha", "x beta"), 0.0, 1e-9);
}

#[test]
fn dedupe_kept_indices_behavior() {
    let entries = vec![
        DedupeEntry {
            mode: DedupeMode::Fuzzy,
            text: "OpenClaw vs NanoClaw comparison review".to_owned(),
        },
        DedupeEntry {
            mode: DedupeMode::Fuzzy,
            text: "OpenClaw vs NanoClaw comparison review thread".to_owned(),
        },
        DedupeEntry {
            mode: DedupeMode::UrlOnly,
            text: "OpenClaw vs NanoClaw comparison review".to_owned(),
        },
        DedupeEntry {
            mode: DedupeMode::Fuzzy,
            text: String::new(),
        },
    ];
    let kept = dedupe_kept_indices(&entries, 0.7).unwrap();
    assert_eq!(
        kept,
        vec![0, 2, 3],
        "fuzzy dup dropped, url_only & empty kept"
    );

    let analysis = vec![
        DedupeEntry {
            mode: DedupeMode::Fuzzy,
            text: "OpenClaw security analysis".to_owned(),
        },
        DedupeEntry {
            mode: DedupeMode::Fuzzy,
            text: "OpenClaw security review".to_owned(),
        },
    ];
    assert_eq!(
        dedupe_kept_indices(&analysis, 1.0).unwrap(),
        vec![0, 1],
        "threshold 1.0 keeps non-exact"
    );
    assert_eq!(
        dedupe_kept_indices(&analysis, 0.3).unwrap(),
        vec![0],
        "threshold 0.3 collapses similar"
    );
    assert!(
        dedupe_kept_indices(&analysis, 1.5).is_err(),
        "out-of-range threshold rejected"
    );
    // Verify hybrid_similarity matches max(ngram, token).
    let a = "OpenClaw security issues discussion";
    let b = "OpenClaw security issues thread";
    let expected =
        jaccard_similarity(&ngrams(a, 3).unwrap(), &ngrams(b, 3).unwrap()).max(token_jaccard(a, b));
    approx_eq(dedupe::hybrid_similarity(a, b), expected, 1e-9);
}

// ---------------------------------------------------------------------------
// Ported from tests/test_relevance.py
// ---------------------------------------------------------------------------

#[test]
fn relevance_tokenize_and_synonyms() {
    let tokens = tokenize("how to use the AI tools");
    assert!(!tokens.contains("how"), "stopword removed");
    assert!(!tokens.contains("the"), "stopword removed");
    assert!(tokens.contains("ai"), "ai kept");
    assert!(tokens.contains("artificial"), "ai synonym expanded");
    assert!(tokens.contains("intelligence"), "ai synonym expanded");
    assert!(
        tokenize("js framework").contains("javascript"),
        "js synonym"
    );
    assert!(
        tokenize("svelte app").contains("sveltejs"),
        "svelte synonym"
    );
    assert!(tokenize("vue components").contains("vuejs"), "vue synonym");
    assert!(
        !tokenize("a b c python").contains("a"),
        "single char dropped"
    );
}

#[test]
fn relevance_scoring_and_hashtags() {
    let q_claude = PreparedQuery::new("claude code");
    assert!(
        token_overlap_relevance(&q_claude, "Claude Code tricks and tips", &[]) > 0.7,
        "exact match high"
    );
    let q_empty = PreparedQuery::new("");
    approx_eq(
        token_overlap_relevance(&q_empty, "Some video", &[]),
        0.5,
        1e-9,
    );
    let q_stop = PreparedQuery::new("the a is");
    approx_eq(
        token_overlap_relevance(&q_stop, "some content", &[]),
        0.5,
        1e-9,
    );

    let q_py = PreparedQuery::new("python tutorial");
    approx_eq(
        token_overlap_relevance(&q_py, "Python Tutorial for Beginners", &[]),
        1.0,
        1e-9,
    );

    let q_odds = PreparedQuery::new("anthropic odds");
    let generic = token_overlap_relevance(&q_odds, "Republican house odds update", &[]);
    let informative = token_overlap_relevance(&q_odds, "Anthropic valuation market", &[]);
    assert!(generic < 0.1, "generic-only capped below floor: {generic}");
    assert!(
        informative > generic,
        "informative {informative} > generic {generic}"
    );

    let tags = vec!["claudecode".to_owned(), "ai".to_owned()];
    let no_hash = token_overlap_relevance(&q_claude, "random video about stuff", &[]);
    let with_hash = token_overlap_relevance(&q_claude, "random video about stuff", &tags);
    assert!(
        with_hash > no_hash,
        "hashtags boost relevance: {with_hash} > {no_hash}"
    );
}

// ---------------------------------------------------------------------------
// Ported from tests/test_signals_v3.py
// ---------------------------------------------------------------------------

fn make_signal_item(source: &str, title: &str, body: &str) -> SignalItem {
    SignalItem {
        source: source.to_owned(),
        title: title.to_owned(),
        body: body.to_owned(),
        snippet: String::new(),
        published_at: Some("2026-03-15".to_owned()),
        date_kind: DateKind::Event,
        hashtags: Vec::new(),
        labels: Vec::new(),
        grounding: GroundingPolicy::Standard,
        github_kind: GithubItemKind::Standard,
        engagement: BTreeMap::new(),
        has_stars_key: false,
        top_comment_score: None,
    }
}

#[test]
fn signals_engagement_formulas() {
    let mut reddit = make_signal_item("reddit", "Title", "Body");
    reddit.engagement.insert("score".to_owned(), 99.0);
    reddit.engagement.insert("num_comments".to_owned(), 20.0);
    reddit.engagement.insert("upvote_ratio".to_owned(), 0.8);
    reddit.top_comment_score = Some(10.0);
    let expected = 0.50 * 99.0_f64.ln_1p()
        + 0.35 * 20.0_f64.ln_1p()
        + 0.05 * (0.8 * 10.0)
        + 0.10 * 10.0_f64.ln_1p();
    approx_eq(engagement_raw(&reddit).unwrap(), expected, 1e-9);

    let mut yt = make_signal_item("youtube", "Title", "Body");
    yt.engagement.insert("views".to_owned(), 10_000.0);
    yt.engagement.insert("likes".to_owned(), 500.0);
    yt.engagement.insert("comments".to_owned(), 30.0);
    yt.top_comment_score = Some(500.0);
    let expected_yt = 0.45 * 10_000.0_f64.ln_1p()
        + 0.32 * 500.0_f64.ln_1p()
        + 0.13 * 30.0_f64.ln_1p()
        + 0.10 * 500.0_f64.ln_1p();
    approx_eq(engagement_raw(&yt).unwrap(), expected_yt, 1e-6);

    let mut hn = make_signal_item("hackernews", "Show HN", "Desc");
    hn.engagement.insert("points".to_owned(), 150.0);
    hn.engagement.insert("comments".to_owned(), 45.0);
    let expected_hn = 0.55 * 150.0_f64.ln_1p() + 0.45 * 45.0_f64.ln_1p();
    approx_eq(engagement_raw(&hn).unwrap(), expected_hn, 1e-9);

    let mut pm = make_signal_item("polymarket", "Title", "Body");
    pm.engagement.insert("volume".to_owned(), 1000.0);
    pm.engagement.insert("liquidity".to_owned(), 250.0);
    let expected_pm = 0.60 * 1000.0_f64.ln_1p() + 0.40 * 250.0_f64.ln_1p();
    approx_eq(engagement_raw(&pm).unwrap(), expected_pm, 1e-9);

    // GitHub repo snapshot returns None for engagement_score
    let mut gh_repo = make_signal_item("github", "Repo", "Desc");
    gh_repo.engagement.insert("stars".to_owned(), 5000.0);
    gh_repo.has_stars_key = true;
    assert_eq!(
        engagement_score(&gh_repo),
        None,
        "repo snapshot has no engagement score"
    );
}

#[test]
fn signals_annotate_stream_orders_relevant_above_noise() {
    let mut relevant = make_signal_item(
        "reddit",
        "Deploy to Fly.io with MCP in 60 seconds",
        "Deploy to Fly.io guide with concrete steps.",
    );
    relevant.engagement.insert("score".to_owned(), 2.0);
    relevant.engagement.insert("upvote_ratio".to_owned(), 0.8);

    let mut noisy = make_signal_item(
        "reddit",
        "BATTLEFIELD 6 GAME UPDATE 1.2.2.0",
        "Patch notes and gameplay discussion.",
    );
    noisy.engagement.insert("score".to_owned(), 5000.0);
    noisy.engagement.insert("num_comments".to_owned(), 1200.0);
    noisy.engagement.insert("upvote_ratio".to_owned(), 0.95);
    noisy.top_comment_score = Some(400.0);

    let ranked = annotate_stream(
        &[noisy, relevant],
        "How do I deploy on Fly.io?",
        FreshnessMode::EvergreenOk,
        "2026-03-16",
        30,
    )
    .unwrap();
    assert_eq!(ranked[0].index, 1, "relevant item must rank first");
}

// ---------------------------------------------------------------------------
// Ported from tests/test_cluster_v3.py & test_entity_extract.py
// ---------------------------------------------------------------------------

fn make_cand(id: &str, source: &str, title: &str, snippet: &str, score: f64) -> ClusterCandidate {
    ClusterCandidate {
        candidate_id: id.to_owned(),
        title: title.to_owned(),
        snippet: snippet.to_owned(),
        sources: vec![source.to_owned()],
        final_score: score,
        window_state: WindowState::InWindow,
    }
}

#[test]
fn entity_anchor_and_overlap() {
    assert!(has_anchor_signal("Rust"), "leading capital");
    assert!(has_anchor_signal("GPT4"), "all caps + digit");
    assert!(has_anchor_signal("v2"), "digit");
    assert!(!has_anchor_signal("plain"), "lowercase no digit");
    let a = extract_text_entities("Kanye West Wireless Festival 2026");
    let b = extract_text_entities("BREAKING: Kanye West UK comeback Wireless Festival");
    assert!(entity_overlap(&a, &b) >= 0.45, "shared event entities");
}

#[test]
fn cluster_cross_source_merge_and_polymarket_isolation() {
    let plan = ClusterPlan {
        intent: "breaking_news".to_owned(),
        cluster_mode: "story".to_owned(),
        raw_topic: "test".to_owned(),
    };

    let cands = vec![
        make_cand(
            "c1",
            "reddit",
            "Kanye West to headline all three nights of Wireless Festival 2026",
            "Big announcement for Wireless.",
            80.0,
        ),
        make_cand(
            "c2",
            "x",
            "BREAKING: Kanye West is making his massive UK comeback at Wireless Festival this July",
            "Ye returns to UK.",
            75.0,
        ),
        make_cand(
            "c3",
            "youtube",
            "Kanye West BULLY Album Review - Knox Hill Reacts",
            "Full album reaction and breakdown.",
            70.0,
        ),
    ];
    let clusters = cluster_candidates(&cands, &plan);
    assert_eq!(clusters.len(), 2, "c1 and c2 merge, c3 stays separate");
    let wireless = clusters
        .iter()
        .find(|cl| cl.candidate_ids.len() == 2)
        .expect("merged wireless cluster");
    assert_eq!(
        wireless.sources,
        vec!["reddit", "x"],
        "sorted merged sources"
    );
    assert_ne!(
        wireless.uncertainty.as_deref(),
        Some("single-source"),
        "multi-source cluster is not single-source"
    );

    // Polymarket isolation
    let poly_cands = vec![
        make_cand(
            "p1",
            "reddit",
            "Sam Altman personal rivalry with Elon Musk escalates",
            "The feud between Sam Altman and Elon Musk continues.",
            80.0,
        ),
        make_cand(
            "p2",
            "polymarket",
            "Sam Altman equity stake in OpenAI valued at $500M",
            "Will Sam Altman receive equity in OpenAI restructuring?",
            75.0,
        ),
    ];
    let poly_clusters = cluster_candidates(&poly_cands, &plan);
    assert_eq!(
        poly_clusters.len(),
        2,
        "Polymarket cluster must not merge with news cluster"
    );
}

#[test]
fn cluster_stale_candidate_demoted_below_fresh() {
    let plan = ClusterPlan {
        intent: "breaking_news".to_owned(),
        cluster_mode: "story".to_owned(),
        raw_topic: "test".to_owned(),
    };
    let mut stale = make_cand(
        "stale",
        "reddit",
        "Model launch reactions discussion",
        "Model launch reactions discussion",
        95.0,
    );
    stale.window_state = WindowState::OutOfWindow;
    let fresh = make_cand(
        "fresh",
        "x",
        "Model launch reactions update",
        "Model launch reactions update",
        50.0,
    );
    let clusters = cluster_candidates(&[stale, fresh], &plan);
    assert_eq!(clusters.len(), 1, "similar items cluster together");
    assert_eq!(
        clusters[0].representative_ids[0], "fresh",
        "fresh leads cluster"
    );
    assert_eq!(
        clusters[0].title, "Model launch reactions update",
        "fresh supplies title"
    );
}

// ---------------------------------------------------------------------------
// Ported from tests/test_library_feed.py & test_library_search.py
// ---------------------------------------------------------------------------

#[test]
fn markdown_redacts_private_blocks_and_drills_respecting_fences() {
    let md = format!(
        "# last30days v4.6.0: Test Topic\n\n\
         {PRIVATE_CORPUS_START}\n## From your files\nSecret note.\n{PRIVATE_CORPUS_END}\n\n\
         ## Public Section\n\n\
         ```markdown\n## From your files\nCode fence example stays intact.\n```\n\n\
         {PUBLIC_DRILL_MARKER}\n## Drill Follow-up\n\nPublic drill stays.\n\n\
         ## Drill Follow-up\n\nLegacy unmarked drill gets stripped.\n\n\
         ### Deeper\n\n- 3 new items after dedupe\n- Re-researched sources: Reddit\n\n\
         {PUBLIC_FRESHNESS_MARKER}\n## Freshness Verification\n\n\
         | Verdict | Claim | Evidence | Checked |\n\
         | --- | --- | --- | --- |\n\
         | **supported** | Public claim | https://example.com | 2026-11-20 |\n\
         | **contradicted** | Private claim | [notes](corpus://secret.md) | 2026-11-20 |\n\n\
         {PUBLIC_CLUSTERS_MARKER}\n## Ranked Evidence Clusters\n\n\
         ### 1. Public headline (score 80, 1 item, sources: Reddit)\n"
    );

    let cleaned = markdown::public_markdown(&md);
    assert!(!cleaned.contains("Secret note."), "private block stripped");
    assert!(
        cleaned.contains("Code fence example stays intact."),
        "fenced heading preserved"
    );
    assert!(
        cleaned.contains("Public drill stays."),
        "marked public drill preserved"
    );
    assert!(
        !cleaned.contains("Legacy unmarked drill gets stripped."),
        "unmarked drill stripped"
    );
    assert!(
        cleaned.contains("Public claim"),
        "public freshness row preserved"
    );
    assert!(
        !cleaned.contains("corpus://secret.md"),
        "private freshness row stripped"
    );
}

#[test]
fn markdown_unbounded_private_brief_excluded() {
    let brief = "🌐 last30days · Secret Topic · Nov 20, 2026 · v4.6.0\n\n\
                 Based on Your files, here is the private summary.\n";
    assert!(
        markdown::is_unbounded_private_brief(brief, Some("finished-brief")),
        "unbounded private finished brief detected"
    );
    assert_eq!(
        markdown::public_markdown(brief),
        "",
        "unbounded private brief redacts to empty"
    );
}

#[test]
fn markdown_batch_and_meta_parse() {
    let doc = "# last30days v4.6.0: OpenClaw\n\n\
               - Date range: 2026-06-10 to 2026-07-01\n\n\
               ## Ranked Evidence Clusters\n\n\
               ### 1. MCP servers need permission boundaries (score 42, 2 items, sources: Reddit)\n\
               1. [reddit] A useful thread\n\
                  - URL: https://example.com/openclaw\n\
                  - Evidence: MCP servers should isolate tools and credentials.\n";
    let meta = markdown::parse_markdown_meta(
        doc,
        "/tmp/openclaw-raw.md",
        "openclaw-raw",
        "markdown",
        "2026-07-01",
    )
    .unwrap();
    assert_eq!(meta.topic, "OpenClaw", "topic");
    assert_eq!(meta.slug, "openclaw", "slug");
    assert_eq!(meta.published_date, "2026-07-01", "date");
    assert_eq!(
        meta.headline, "MCP servers need permission boundaries",
        "headline"
    );
    assert_eq!(
        meta.summary, "MCP servers should isolate tools and credentials.",
        "summary"
    );
    assert!(!meta.unbounded_private, "public report");
    assert_eq!(meta.indexable_fingerprint.len(), 64, "sha256 hex length");

    let batch = markdown::sanitize_markdown_batch(&[MarkdownBatchItem {
        content: doc.to_owned(),
        source_format: Some("markdown".to_owned()),
    }]);
    assert_eq!(batch.len(), 1, "batch length");
    assert_eq!(
        batch[0].indexable_fingerprint, meta.indexable_fingerprint,
        "fingerprint matches"
    );
}

#[test]
fn markdown_nested_inline_emphasis_matches_python_clean_inline() {
    let cases = [
        ("# **__Rust__**\n", "_Rust_", "rust"),
        ("# __**Rust**__\n", "*Rust*", "rust"),
        (
            "# **bold** and __bold2__\n",
            "bold and bold2",
            "bold-and-bold2",
        ),
        ("# snake_case_name\n", "snake_case_name", "snake-case-name"),
        (
            "# **__nested__** and *italic* and `code`\n",
            "_nested_ and italic and code",
            "nested-and-italic-and-code",
        ),
    ];
    for (doc, expected_topic, expected_slug) in cases {
        let meta = markdown::parse_markdown_meta(
            doc,
            "/tmp/sample.md",
            "sample",
            "markdown",
            "2026-11-26",
        )
        .unwrap();
        assert_eq!(meta.topic, expected_topic, "topic mismatch for {doc:?}");
        assert_eq!(meta.slug, expected_slug, "slug mismatch for {doc:?}");
    }
}

#[test]
fn markdown_unicode_line_breaks_redact_private_sections() {
    let breaks = [
        "\u{2028}", "\u{2029}", "\u{0085}", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
    ];
    for brk in breaks {
        let doc = format!("Intro{brk}## From your files\nSecret note\n## Public\nVisible\n");
        let redacted = markdown::public_markdown(&doc);
        assert!(
            !redacted.contains("Secret note"),
            "private section leaked after break {brk:?}: {redacted:?}"
        );
        assert!(
            redacted.contains("## Public\nVisible\n"),
            "public section lost after break {brk:?}: {redacted:?}"
        );
        assert_eq!(redacted, format!("Intro{brk}## Public\nVisible\n"));
    }
}

#[test]
fn markdown_nbsp_from_your_files_label_in_legacy_cluster_is_redacted() {
    let doc = "## Ranked Evidence Clusters\n### 1. Notes\n\u{00a0}**From your files**\nSecret\n";
    let redacted = markdown::public_markdown(doc);
    assert!(
        !redacted.contains("Secret"),
        "private note leaked when preceded by NBSP: {redacted:?}"
    );
    assert!(
        !redacted.contains("From your files"),
        "private label leaked when preceded by NBSP: {redacted:?}"
    );
}
