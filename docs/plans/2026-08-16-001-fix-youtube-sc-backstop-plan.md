---
title: Fix YouTube ScrapeCreators Search Backstop Thinness Floor - Plan
type: fix
date: 2026-08-16
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-plan-bootstrap
---

# Fix YouTube ScrapeCreators Search Backstop Thinness Floor - Plan

**Issue:** #977 — ScrapeCreators YouTube *search* backstop never fires when yt-dlp is bot-gated but returns 1-2 items.

## Goal Capsule

Make the YouTube lane honest and resilient: when yt-dlp returns a suspiciously thin result set (fewer than 3 items), trigger the ScrapeCreators (SC) search backstop and merge its results without discarding yt-dlp's items. A bot-gated lane that returns 1-2 items today reads as a healthy run; after this fix it is backfilled or at least visibly degraded.

- Objective: trigger the SC YouTube search backstop below a thinness floor, merge results by `video_id`, and emit a stderr degradation note.
- Authority: single-issue fix scoped to issue #977; no env-var, CLI, or config surface changes.
- Stop condition: the backstop fires on thin runs; a healthy run spends no credits; full test suite stays green at the 84% coverage floor.
- Execution profile: small, bounded, local to the YouTube source lane.
- Tail ownership: this plan; the PR ships through the standard pipeline.

## Product Contract

### Summary

The SC YouTube search backstop in `skills/last30days/scripts/lib/pipeline.py` fires only when yt-dlp returns zero items or fails. When YouTube bot-gates yt-dlp, searches often succeed but return 1-2 stale items, so the degraded lane is reported as a clean success. Fix: fire the backstop below a thinness floor and merge, never replace.

### Problem Frame

YouTube's bot-gate can throttle yt-dlp without zeroing the result set — observed runs return 1-2 items while per-video transcript fetches fail with `Sign in to confirm you're not a bot` (issue #977 repro). The gate `if (result is None or not result.get("items")) and sc_token:` (`pipeline.py:4378`) treats 1 item as a healthy lane. Downstream, `Source Coverage` shows `YouTube: 1 item` with no error, so the run reads clean while the lane is degraded.

### Requirements

- **R1. Thinness-floor backstop trigger.** The SC YouTube search backstop must fire when yt-dlp returns fewer than 3 items in addition to the existing zero-or-failure trigger. A run with no SC key behaves exactly as today.
- **R2. Merge, never replace.** When the backstop fires, its items are merged into the yt-dlp result set, deduplicated by `video_id`. The backstop must never discard items yt-dlp already returned.
- **R3. Degradation visibility.** When the backstop fires below the floor, the run emits a stderr note stating that the free lane was thin and was backfilled. The lane must not read as silently clean.
- **R4. Credit-spend bound.** The backstop spends at most one SC search call per thin subquery, only when an SC key is configured. Any per-video transcript spend inside `search_youtube_sc` stays bounded by its existing transcript-limit and credit-discipline guards.

### Success Criteria

- A mocked run in which yt-dlp returns 1 item triggers the SC backstop call.
- A mocked run in which yt-dlp returns the floor or more items makes no SC call.
- A rescued run reports the union of both lanes' items with no duplicates.
- The full pytest suite passes at the 84% coverage floor.

### Scope Boundaries

- **In scope:** the YouTube branch of `pipeline._retrieve_stream_impl`, a `_merge_youtube_items` helper, dispatch-level tests mirroring `tests/test_reddit_dispatch.py`, and a changelog fragment.
- **Session-settled (user-directed — chosen over bundling multiple issue fixes: single-issue PR, reviewable).** No other issues (#983 Groq, #928 web backends) are in scope.
- **Deferred to follow-up work.** Env-var tunability of the floor (mirroring `LAST30DAYS_REDDIT_SC_MIN_ITEMS`), composite degradation signals from transcript-fetch stats, adjacent issue #468 (YouTube relevance over-pruning), SKILL.md / CONFIGURATION.md documentation.

## Planning Contract

- **KTD1. Absolute named thinness floor, default 3.** Add `_YT_SC_MIN_ITEMS = 3` as a module constant in `pipeline.py` and trigger on `len(result["items"]) < _YT_SC_MIN_ITEMS`. Chosen over (a) an error-signal-only trigger, which misses the observed case where the bot-gate surfaces in transcript fetches while searches succeed; (b) an env var `LAST30DAYS_YOUTUBE_SC_MIN_ITEMS` mirroring the Reddit floor, which defers the fix behind config and expands the user-facing surface. Rationale for default-on: the observed degenerate case is indistinguishable from a healthy thin run, the cost is bounded (one SC search per thin subquery plus the per-video transcript spend inside `search_youtube_sc`, guarded by its transcript-limit and credit-discipline rules), and a key must be present. Cost rationale is documented in a comment on the constant per the confidence-floor learning (`docs/solutions/design-patterns/ranked-output-confidence-floor-honest-empty-state.md`). The value is a code-level constant, deliberately tunable in source (unlike the Reddit floor's env var).
- **KTD2. Union-merge keyed by `video_id`, free-first.** Mirror `_merge_reddit_items` (`pipeline.py:3955`): start with yt-dlp items, append unseen SC items. This follows the repo's fallback rule to "degrade toward no penalty, never bury good signal" (`docs/solutions/logic-errors/entity-grounding-full-phrase-false-demotion.md`) and the Reddit thinness-floor precedent (`pipeline.py:4132-4192`).
- **KTD3. Degradation note via `sys.stderr.write`.** Mirror the Reddit below-floor message (`pipeline.py:4163-4166`). A backfilled lane stays observable, so degraded output never reads as clean (`docs/solutions/architecture-patterns/discovery-checkpoint-protocol-design-conventions.md`).

### Assumptions

- Floor value 3: healthy multi-query yt-dlp runs return at least 3 in-window videos; the observed bot-gated case returns 1-2.
- SC YouTube search costs ~1 credit per call and only runs with a key configured.
- `youtube_failure` semantics are unchanged: an existing yt-dlp search error still marks the source outcome, even when the lane is rescued.

## Implementation Units

### U1. Thinness-floor backstop with union merge

**Goal:** Fire the SC YouTube search backstop below the thinness floor, merge results by `video_id`, and log the degradation note.

**Requirements:** R1, R2, R3, R4

**Dependencies:** none

**Files:**
- `skills/last30days/scripts/lib/pipeline.py` (modify)
- `tests/test_youtube_dispatch.py` (create)

**Approach:**
1. Add module constant `_YT_SC_MIN_ITEMS = 3` near the other youtube-lane constants with a comment stating the credit-spend rationale and that it is deliberately tunable.
2. Change the backstop gate at `pipeline.py:4378` from `(result is None or not result.get("items"))` to also fire when `len(result.get("items") or []) < _YT_SC_MIN_ITEMS`, keeping the existing `and sc_token` guard.
3. Add `_merge_youtube_items(free, sc)` beside `_merge_reddit_items`, keyed by `video_id`, free-first with dedupe.
4. Capture the free (yt-dlp) items before the SC call. After a successful SC call, set the result items to the merged list. On SC failure (exception or empty result), keep the captured free items and the existing `youtube_failure` handling instead of falling through to the empty-item path, so the backstop never discards items yt-dlp already returned (R2).
5. When firing below the floor and the free lane returned 1..floor-1 items, write a stderr note mirroring the Reddit message, interpolating the `_YT_SC_MIN_ITEMS` value (e.g. `[YouTube] yt-dlp returned N items (below the {_YT_SC_MIN_ITEMS}-item floor); backfilling with ScrapeCreators`). The pre-existing zero-item trigger stays silent, exactly as today.

**Patterns to follow:** Reddit thinness floor (`pipeline.py:4132-4192`), `_merge_reddit_items` (`pipeline.py:3955`), dispatch-level test style of `tests/test_reddit_dispatch.py` (patch `lib.pipeline.youtube_yt.<fn>` and call `pipeline._retrieve_stream(..., source="youtube", ...)` directly).

**Test scenarios:**
- Regression: yt-dlp returns 1 item -> SC search is called and the merged result contains that item plus SC items.
- Guard: yt-dlp returns 3 items -> SC search is not called and no credit is spent.
- Guard: yt-dlp returns 0 items -> SC search is called; no degradation note is emitted (today's behavior preserved).
- Backstop result empty: SC search returns 0 items -> the yt-dlp items are preserved.
- Backstop throws: SC search raises while a thin yt-dlp result is present -> the yt-dlp items are preserved and the source outcome carries the failure.
- Dedupe: yt-dlp returns 1 item and SC returns 3 with one shared `video_id` -> merged set has 3 distinct items.
- Keyless: no SC token in config -> SC search is never called regardless of thinness.
- Failure preservation: yt-dlp search errored and returned 1 item -> the source outcome still carries the failure after the backstop fires.
- Degradation note: a below-floor backfill with 1..floor-1 free items emits the note; a zero-item run does not.
- Full production path: drive `pipeline._retrieve_stream` with `source="youtube"` (as `test_reddit_dispatch.py` does), patching `lib.pipeline.which` to a truthy yt-dlp path and no-oping `lib.pipeline.youtube_yt.enrich_with_comments` (or pinning `env.is_youtube_comments_available` to False) in every returning-items scenario, so the below-floor trigger binds deterministically on any machine and cannot become a never-binds gate.

**Verification:** Content checks pass (see Verification Contract); targeted youtube and dispatch tests green.

### U2. Changelog fragment

**Goal:** Add the required changelog entry for the fix.

**Requirements:** none (process gate per repo conventions)

**Dependencies:** U1

**Files:**
- `changelog.d/977.fixed.md` (create)

**Approach:** One or two sentences describing the fix, per `changelog.d/README.md` (name = `<number>.<type>.md`, type suffix `fixed`). Do not edit `CHANGELOG.md` or any version manifest.

**Patterns to follow:** `changelog.d/README.md`.

**Test expectation:** none — the changelog-guard workflow and `tests/test_changelog_workflow.py` validate the fragment format.

## Verification Contract

- Full suite with coverage floor (`[tool.coverage.report] fail_under`, do not reduce): `uv run pytest --cov`.
- Targeted lanes after changes: `uv run pytest tests/test_youtube_dispatch.py tests/test_youtube_yt.py tests/test_youtube_backfill.py tests/test_youtube_transcript_fallback_spend.py tests/test_reddit_dispatch.py`.
- `git diff --check`.
- `changelog-guard.yml` requires the `changelog.d/977.fixed.md` fragment; no `CHANGELOG.md` or lockstep version edits.

## Definition of Done

- U1: trigger, merge, and degradation note implemented; backstop fires below the floor and never discards yt-dlp items; dispatch and guard tests pass.
- U2: `changelog.d/977.fixed.md` present and formatted per `changelog.d/README.md`.
- Full pytest suite green at or above the 84% coverage floor.
- No abandoned-attempt code left in the diff; no `CHANGELOG.md` or version-manifest edits.