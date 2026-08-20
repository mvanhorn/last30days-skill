# Retrieve-Judge-Retry for X Search

**Date:** 2026-08-14  
**Status:** Completed

## Problem Statement

X search results become off-topic when multi-word search queries are phrase-quoted. The Rome failure (2026-08-14) demonstrated this:

1. Planner generated `search_query: "Rome Italy"` (phrase-quoted)
2. Phrase-quoting returned thin hits with engagement bait (pretty-cities, geopolitics accounts)
3. `entity_extract` ranked off-topic handles (PrettyCitiesX, visegrad24) by frequency
4. `pipeline.py` promoted those handles to the FROM lane
5. FROM lane filled the 40-slot X budget with off-topic timelines

## Solution

Implement retrieve-judge-retry for X search:

1. **Query Compilation (R2):** X now uses `raw_topic` like Reddit/YouTube, not the planner's `search_query`
2. **Fanout Queries (R3):** Multi-word topics use unquoted AND as first variant; phrase-quote only for proper names
3. **Corpus Judging (R7):** New `x_judge.py` module evaluates corpus on-topic ratio after retrieval
4. **Retry (R1):** If off-topic flood detected (ratio < 0.4), retry ONCE with simplified keyword query inside the X stream (not `_retry_thin_sources`)
5. **Split FROM Promotion (R4):**
   - Explicit handles (--x-handle): always FROM, no AND topic
   - Extracted handles: FROM only if ≥2 on-topic hits AND ≥50% ratio, and they DO AND the topic
6. **First-Party Exemption (R5):** Floor immunity stays conservative (explicit handles only)
7. **Status Reporting (R6):** Off-topic floods emit artifact warning, not `record_failure(PARTIAL)`

## Implementation Details

### New Module: `x_judge.py`

- `judge_x_corpus(items, topic, ranking_query)`: Returns on_topic_ratio, is_off_topic_flood, on_topic_items, handle_stats
- `promotable_handles(items, topic, extracted_handles, explicit_handles)`: Returns (explicit_promotable, extracted_promotable)
- `should_retry_x_search(items, topic, depth)`: Returns True if retry warranted
- `prune_off_topic_items(items, topic)`: Returns only on-topic items

### Key Changes

- `grok_x._fanout_queries()`: No phrase-quote for place/disambiguation strings
- `grok_x._is_proper_name()`: Detects title-cased proper names for phrase-quoting
- `grok_x.search_handles()`: Added `and_topic` parameter (default False)
- `pipeline._fetch_x_backend()`: Accepts query directly, not subquery
- `pipeline._retrieve_stream_impl()`: X source uses raw_topic, judges corpus, retries if needed
- `pipeline._run_supplemental_searches()`: Uses `promotable_handles` for split FROM logic

### Tests

- `tests/test_x_judge.py`: New test file for x_judge module
- `tests/test_grok_x.py`: Added fanout and and_topic tests
- `tests/test_pipeline_v3.py`: Updated fixtures to have promotable content

## Success Criteria

- [ ] `_fanout_queries("Rome Italy")` has no `"Rome Italy"` variant
- [ ] `search_name("Peter Steinberger")` still phrase-quotes
- [ ] `search_handles(["steipete"], "topic")` does not AND topic by default
- [ ] `search_handles(["visegrad24"], "Rome", and_topic=True)` does AND Rome
- [ ] Explicit --x-handle always gets FROM lane
- [ ] Off-topic handles (visegrad24) not promoted to FROM lane
- [ ] On-topic handles (mamboitaliano__) promoted to FROM lane
- [ ] X source status is artifact warning, not PARTIAL failure
- [ ] All tests pass

## Out of Scope

- No live Grok calls in tests
- No auth/doctor touch
- No collision lexicon (AS Roma / Odunze still appear; judge + ranking_query drop them)
- bird_x quote-preserving `build_topic_query` (follow-up if needed)
