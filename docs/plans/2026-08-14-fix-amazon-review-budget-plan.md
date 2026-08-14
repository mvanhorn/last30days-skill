# Fix Amazon Review Budget by Starting at Search Time

**Date:** 2026-08-14  
**Status:** Implemented

## Problem (measured 2026-08-14 Bentgo run)

- Full multi-source run. Amazon search listings returned (12 products, stars, rating counts).
- Review lane logged: "pulling up to 50 reviews for 3 products (budget 11s)"
- "lane deadline 11s hit; dropped 3 straggling pull(s)"
- Bright Data timed out after 11s x3. Zero review bodies. Credits spent.
- Isolated Amazon-only re-run got the full 180s, finished in 124s, reviews landed.
- Cause: `_remaining_lane_budget` = min(LANE_DEADLINE=180, max(0, FOREGROUND_CONTRACT=300 - elapsed - RENDER_MARGIN=20)). Enrichment runs after all other sources. Elapsed ~269s → 11s.

## Why the floor-up fix was wrong

`max(120, leftover)` at elapsed=269 means the run goes to ~389–449s. The host Bash contract is 300s (`SKILL.md` 300000ms). That kills the **whole** report. Do not do that.

The measured failure is **when** the review lane starts, not pull quality. Isolated Amazon-only already finishes in 124s of 180s. On a full run, search has already landed products, then reviews wait through every other source plus Phase 2/2b, then get 11s.

## What to build

1. Start `enrich_with_reviews` when Amazon search returns, inside `_retrieve_stream` (amazon branch ~4284), overlapping other source futures. Pass real `elapsed = time.monotonic() - run_started` (thread `run_started` into retrieve). After a 30–90s search, leftover is 190–250s → clamp to 180. Isolated Amazon-only unchanged.

2. Keep finalize (`_finalize_items_by_source` ~2956) as attach-if-missing only. `enrich_source_items` already no-ops if `top_comments` is set. Do not make finalize the only start. Do not enrich inline on the collect loop (that serializes other sources for 124s).

3. Leftover below a useful floor → budget **0**, skip the lane. Do not fire doomed 11s pulls (Bright Data `cli_timeout = max(5, timeout-10)` so budget 11s → CLI timeout 1s, still spends 3 credits). Suggested `MIN_USEFUL_REVIEW_BUDGET = 90`. Crumbs are a skip, not a short timeout.

4. If the lane is skipped or all pulls drop: Amazon `source_status` **PARTIAL** with detail like `review lane timed out` / `review lane skipped (budget 0s)`. Listings stay. Do not flip the whole source to `timeout` (search succeeded). Footer already shows ⚠ when state != ok — do not change render.py.

5. `depth=quick` still 0 pulls. `mock=True` still skips. No env knob. No raising LANE_DEADLINE or FOREGROUND_CONTRACT.

## Out of scope

- Do NOT change footer/render.py.
- Do NOT change X search or Grok auth.
- Do NOT add an env knob.
- Do NOT reorder the whole source schedule (deferred).

## Implementation

### amazon.py

- Added `MIN_USEFUL_REVIEW_BUDGET = 90`
- Changed `_remaining_lane_budget` to return 0 when below floor (not floor up)
- Changed `enrich_with_reviews` to return `(products, status_detail)` tuple where status_detail is:
  - `None` for normal success
  - `"review lane skipped (budget 0s)"` when budget is below floor
  - `"review lane timed out"` when all pulls dropped

### pipeline.py

- Added `run_started` parameter to `_retrieve_stream`, `_retrieve_stream_impl`, and `_retry_thin_sources`
- Updated all call sites to pass `run_started`
- In the Amazon branch of `_retrieve_stream_impl`:
  - Run search as before
  - Calculate `elapsed = time.monotonic() - run_started`
  - Call `enrich_with_reviews` immediately after search returns
  - If enrichment reports a degraded status, add `_source_outcome` with `state=PARTIAL` to the artifact
- Updated finalize comments to note it's now attach-if-missing only

### Tests (test_amazon.py)

Retargeted existing tests:
- `test_lane_budget_shrinks_as_the_run_clock_advances` — now tests floor behavior
- `test_dropped_straggler_keeps_its_product_with_search_stats` — uses patched short LANE_DEADLINE
- `test_exhausted_wall_clock_skips_the_lane_entirely` — unchanged

New tests:
- `test_lane_budget_floor_prevents_doomed_pulls` — verifies elapsed=269 returns 0
- `test_lane_budget_constants_are_sane` — guards against constant drift
- `test_crumb_budget_skips_not_fires_doomed_pulls` — regression test for the Bentgo bug
- `test_early_elapsed_gets_full_budget` — verifies elapsed=40 gets 180s timeout
- `test_all_pulls_dropped_reports_timed_out_status` — verifies PARTIAL status on all-dropped
