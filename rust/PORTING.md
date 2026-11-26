# Porting Notes: `last30days-core`

Follows the `rust-port` and `rust-best-practices` skills and lints from
https://github.com/morganlinton/BeautifulRust.

## Architecture

End users never install Rust or Cargo. `rust/` compiles to a single standalone
native executable (`last30days-core`) linked only to standard OS libraries.
`skills/last30days/scripts/lib/rust_accel.py` spawns it lazily as a persistent
newline-delimited JSON stdio worker when present, and falls back transparently
to the pure-Python modules when absent or when `LAST30DAYS_ENGINE=python` is set.

## API Mapping

| Python (`skills/last30days/scripts/lib/`) | Rust (`last30days-core`) |
|---|---|
| `cjk.has_cjk`, `cjk.segment` (bigram path) | `cjk::has_cjk`, `cjk::segment` |
| `dedupe.normalize_text`, `get_ngrams`, `jaccard_similarity`, `token_jaccard`, `hybrid_similarity`, `dedupe_items` (pass 2) | `dedupe::normalize_text`, `ngrams`, `jaccard_similarity`, `token_jaccard`, `hybrid_similarity`, `dedupe_kept_indices` |
| `relevance.tokenize`, `PreparedQuery`, `token_overlap_relevance` | `relevance::tokenize`, `PreparedQuery`, `token_overlap_relevance` |
| `dates.recency_score`, `signals.source_quality`, `engagement_raw`, `engagement_score`, `freshness`, `local_relevance`, `annotate_stream` | `signals::recency_score_from_days`, `source_quality`, `engagement_raw`, `engagement_score`, `freshness_from_days`, `local_relevance`, `annotate_stream` |
| `entity_extract.has_anchor_signal`, `extract_text_entities`, `entity_overlap` | `entity::has_anchor_signal`, `extract_text_entities`, `entity_overlap` |
| `cluster.cluster_candidates`, `_mmr_representatives`, `_merge_entity_clusters` | `cluster::cluster_candidates` |
| `library.has_private_corpus`, `is_unbounded_private_brief`, `public_markdown`, `slugify`, `parse_markdown_file` | `markdown::has_private_corpus`, `is_unbounded_private_brief`, `public_markdown`, `slugify`, `parse_markdown_meta`, `sanitize_markdown_batch`, `scan_markdown_files_batch` |
| `library_index._without_library_context`, `_fingerprint` | `markdown::without_library_context`, `indexable_fingerprint_from_public` |

## Test Mapping

| Python test file | Rust test in `crates/last30days-core/tests/parity_tests.rs` |
|---|---|
| `tests/test_cjk.py` | `cjk_has_cjk_matches_python`, `cjk_segment_ascii_chinese_mixed_and_single`, `cjk_chinese_relevance_and_phrase_bonus`, `cjk_chinese_dedupe` |
| `tests/test_dedupe_v3.py` | `dedupe_normalize_text`, `dedupe_ngrams`, `dedupe_jaccard_and_token_jaccard`, `dedupe_kept_indices_behavior` |
| `tests/test_relevance.py` | `relevance_tokenize_and_synonyms`, `relevance_scoring_and_hashtags` |
| `tests/test_signals_v3.py` | `signals_engagement_formulas`, `signals_annotate_stream_orders_relevant_above_noise` |
| `tests/test_cluster_v3.py`, `tests/test_entity_extract.py` | `entity_anchor_and_overlap`, `cluster_cross_source_merge_and_polymarket_isolation`, `cluster_stale_candidate_demoted_below_fresh` |
| `tests/test_library_feed.py`, `tests/test_library_search.py` | `markdown_redacts_private_blocks_and_drills_respecting_fences`, `markdown_unbounded_private_brief_excluded`, `markdown_batch_and_meta_parse` |

## Deliberate Differences

- **Set fingerprinting in `PreparedText`**: Instead of allocating a `String` per
  character trigram and token, `PreparedText` stores sorted, deduplicated 64-bit
  FNV-1a + SplitMix64 fingerprints (`Vec<u64>`) with exact cardinality and
  remaining-element early-exit bounds in the two-pointer merge. Results match
  Python Jaccard ratios while eliminating per-trigram heap churn.
- **Optional `jieba` segmentation**: `last30days-core` implements the default
  dictionary-free CJK bigram path. When optional `jieba` is installed in Python
  and input text contains CJK characters, the Python bridge automatically stays
  on Python so `jieba` behavior is never altered.
- **Exact-URL dedupe and `SourceItem` metadata merging**: Pass 1 of
  `dedupe.dedupe_items` stays in Python so source-specific metadata merges
  (Reddit provenance, Polymarket match bundles, comment unions) remain lossless;
  pass 2 (the quadratic fuzzy similarity loop) runs in Rust.
- **No `unsafe` code**: `unsafe_code = "forbid"` is enforced at workspace level.
  POSIX `O_NOFOLLOW` file open uses `std::os::unix::fs::OpenOptionsExt` without
  any `unsafe` blocks.
- **Parallel library scans**: `scan_markdown_files_batch` and
  `sanitize_markdown_batch` use `rayon` to sanitize and fingerprint saved
  Markdown reports across CPU cores in a single pass instead of running
  `public_markdown` twice per file on one thread.

## Preserved Quirks

- Python banker's rounding (`round_ties_even`) is used for `token_overlap_relevance`
  (2 decimals) and `engagement_score` (nearest integer) so boundary values match.
- Python `min()` / `max()` first-wins tie breaking is preserved in
  `cluster::mmr_representatives` and `cluster::cluster_candidates` (stable sort).
- `dedupe_items` keeps `UrlOnly` items (jobs and matched Polymarket events) and
  empty-text items without inserting them into the fuzzy comparison pool.
- `library.public_markdown` preserves the exact CommonMark backtick/tilde code
  fence tracking rules, marker checks, and private-block trailing-marker newline
  insertion (`\n\n` when preceded by a public marker).

## Verification

Run from `rust/`:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps
cargo build --release
```
