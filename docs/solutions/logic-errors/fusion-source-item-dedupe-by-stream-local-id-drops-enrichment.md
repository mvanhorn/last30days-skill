---
title: "Fusion de-duplicated source items by per-stream id, silently dropping the enriched copy of a thread"
date: 2026-08-31
category: docs/solutions/logic-errors
module: lib/fusion
problem_type: logic_error
component: search_ranking
symptoms:
  - "`## Top Community Comments` is absent from `--emit=compact` output on a multi-subquery run even though the saved raw file shows Reddit threads carrying 10 enriched comments"
  - "the same thread appears twice in `items_by_source` with the same item id (`R1`) and different metadata: one copy has `top_comments`, the other does not"
  - "a fused candidate's `source_items` holds the bare copy of a thread; the copy that won a comment-enrichment slot in another subquery stream is gone"
root_cause: data_integrity
resolution_type: code_fix
severity: high
tags:
  - fusion
  - dedupe
  - item-id-collision
  - top-comments
  - reddit
  - enrichment
  - subquery-streams
---

# Fusion de-duplicated source items by per-stream id, silently dropping the enriched copy of a thread

## Problem

`weighted_rrf` merges the same thread arriving from several subquery streams into one candidate, but it recognised repeat source items by `(source, item_id)`. Keyless adapters assign ids per stream (`R1`, `R2`, … per call of `reddit_keyless.search_and_enrich`; `X1`, … for X), so the second copy of a thread from another stream collided with the first copy's id and was thrown away. When the discarded copy was the one that had won a comment-enrichment slot in its own stream, the candidate kept a thread with no comments, and `## Top Community Comments` had nothing to render. On the 2026-08-31 Kanye West run the engine had collected the 3,293-upvote "Vladimir Putin doesn't care about black people" comment and never showed it.

## Symptoms

- `--emit=compact` output has no `## Top Community Comments` block while the saved raw file's "All Items by Source" lists the thread twice: `**R1** (score:1)` with no comments and `**R1** (score:0)` with "Top comment" lines.
- `last-report.json` shows `ranked_candidates[i].source_items[0].metadata.top_comments` empty for a thread whose `items_by_source` copy carries 10 comments.
- Several `item_id` values collide inside one source's `items_by_source` list (five `R` ids collided in that run; X ids collide the same way).

## What Didn't Work

- Looking for the bug in the renderer. `_render_top_comments` reads `candidate.source_items[*].metadata.top_comments`; it was correct, it simply never received the enriched copy.
- Expecting the report-finalize dedupe to fix it. `_finalize_items_by_source` (`skills/last30days/scripts/lib/pipeline.py:3035`) runs `dedupe.dedupe_items` (`skills/last30days/scripts/lib/dedupe.py:112`), which keys on title-plus-body text similarity, not URL. The enriched copy's body includes comment text, so the two copies scored below the 0.7 similarity threshold and both survived; and when text dedupe does collapse near-duplicates it keeps the first, higher-ranked item, which is the bare one.
- Giving keyless Reddit posts stable ids (the post's base36 id) was planned and then skipped: it fixes one source, and X ids collide identically. Keying on identity at the fusion seam fixes every source at once.

## Solution

Identity is the normalized URL, and a repeat copy merges into the retained copy instead of being dropped.

- `weighted_rrf` keys `seen_source_items` on `(source, candidate_key(item))` where `candidate_key` (`skills/last30days/scripts/lib/fusion.py:44`) is the normalized URL with a `source:item_id` fallback (`fusion.py:396`, `fusion.py:422`). On a hit it calls `merge_source_items` (`fusion.py:62`), which takes the enrichment lists the existing copy lacks or that are longer (`top_comments`, `comment_insights`, transcript fields), the larger numeric engagement counters, and the longer body and snippet.
- `collapse_duplicate_urls` (`fusion.py:91`) applies the same merge to a flat item list, and `_finalize_items_by_source` runs it before the text-similarity dedupe (`pipeline.py:3049`), so `items_by_source`, the stats footer, and the raw file hold one copy per thread with the enrichment attached.

Before (per-stream id as identity):

```python
source_item_key = (item.source, item.item_id)
if source_item_key not in seen_source_items[key]:
    seen_source_items[key].add(source_item_key)
    candidate.source_items.append(item)
```

After (normalized URL as identity, merge on repeat):

```python
source_item_key = (item.source, candidate_key(item))
existing_item = seen_source_items[key].get(source_item_key)
if existing_item is None:
    seen_source_items[key][source_item_key] = item
    candidate.source_items.append(item)
else:
    merge_source_items(existing_item, item)
```

Fixed in #1083 (merged 2026-08-31).

## Why This Works

A thread's URL is the same in every stream; its per-stream id is not. Keying on the URL makes the second copy a recognised repeat rather than a new item, and merging instead of discarding means whichever stream spent its scarce enrichment slot on the thread contributes that work to the candidate. Cross-source copies of one URL (a Reddit thread and an X post linking it) keep separate source items because the key still includes `source`. The existing regression test `test_weighted_rrf_merges_duplicate_urls` in `tests/test_fusion_v3.py` pins that behaviour; the new tests pin the same-source case (`tests/test_fusion_v3.py:544`, `:575`, `:584`) and the finalize path (`tests/test_pipeline_finalize.py:19`).

## Prevention

- Never de-duplicate across subquery streams by an adapter-assigned id. Ids like `R{n}` (`skills/last30days/scripts/lib/reddit_keyless.py:397`, `skills/last30days/scripts/lib/reddit_rss.py:236`) are positions within one call, not identities. Use `fusion.candidate_key` for identity and `fusion.merge_source_items` for the merge.
- When two copies of one item can differ in enrichment, dedupe must merge, not pick. A dedupe that keeps the first or highest-ranked copy silently loses whatever the other copy was enriched with; assert in tests that the retained copy carries the union.
- The cheapest detector is in `last-report.json`: count duplicate URLs per source in `items_by_source`, and compare `metadata.top_comments` between `items_by_source` and the corresponding `ranked_candidates[*].source_items`. Any thread with comments in the former and none in the latter is this class of bug.
- Multi-subquery plans are the trigger. A single-subquery run never produces two copies, so a fix verified only on `--quick` or one-subquery runs can miss it; the live check is a four-subquery plan on a busy topic with a dedicated subreddit.

## Related Issues

- #1083 (the fix), #906 and #985 (closed by the same change)
- `docs/solutions/logic-errors/entity-grounding-full-phrase-false-demotion.md` (sibling keyless-ranking logic error)
- `docs/solutions/design-patterns/ranked-output-confidence-floor-honest-empty-state.md` (the comments block's honest empty state)
