---
title: "feat: vs mode runs N full passes and --competitors is vs with auto-discovery"
type: feat
status: active
date: 2026-04-22
origin: docs/plans/2026-04-22-004-fix-competitors-hosting-model-resolve-and-leak-plan.md.superseded
---

# feat: vs mode runs N full passes and --competitors is vs with auto-discovery

## Overview

Architectural unification driven by user correction 2026-04-22: vs mode and `--competitors` are the same thing. A user typing `/last30days OpenAI vs Anthropic vs xAI` should get a full single-entity last30days pass for each of the three entities — three full pipelines, three saved `*-raw.md` files, merged into one comparison output. A user typing `/last30days OpenAI --competitors` should get the same output after the hosting model auto-picks 2 peers; i.e., `--competitors` is a thin shortcut that expands "topic + `--competitors`" into "topic vs peer1 vs peer2" and then runs the unified vs pipeline.

Current state diverges from this:

- **vs mode today**: one `pipeline.run()` with a comparison-optimized plan that merges all entities' targeting into a single retrieval pool. Lower-weight `--x-related` for peers, merged subreddits, cross-entity keyword noise. One saved file.
- **`--competitors` today (3.0.12)**: N parallel `pipeline.run()` calls via `scripts/lib/fanout.py`, but per-entity Step 0.55 depends on an engine-side web backend key Matt doesn't have. Silently degrades to planner defaults for peers. One saved file (main topic only). Override-leak from main into peers.

After this plan:

- **vs mode**: N parallel `pipeline.run()` calls, one per entity, each with its own full Step 0.55-grade targeting, each saving its own `*-raw.md`. Merged into one comparison output.
- **`--competitors`**: SKILL.md shortcut. Hosting model discovers N peers, builds `"topic vs peer1 vs peer2"`, and invokes the same vs pipeline. No separate orchestration path.
- **Same fanout machinery (`scripts/lib/fanout.py`)** serves both. One fix, both behaviors improve.

## Problem Frame

The product insight from 2026-04-22 test runs is simple: the user wants three full last30days reports plus a comparison merge. Not one comparison pass with N-way targeting merged into a single retrieval pool. Not one save file. Not "main gets Step 0.55, peers get planner defaults." Three full passes. Three save files. Merged output.

The historical vs mode did that (it ran as 3 passes, saving 3 files). SKILL.md §551 currently says:

> "When the user asks 'X vs Y', run ONE research pass with a comparison-optimized plan that covers both entities AND their rivalry. This replaces the old 3-pass approach (which took 13+ minutes and produced tangential content)."

That change was a latency optimization that removed the user-visible behavior the user wants. The fix is to revert the architectural direction: N passes per entity, in parallel rather than serial (parallelism lowers wall-clock to ~1× a single pass, not N×), with per-entity save files.

The 3.0.11 `--competitors` flag already introduced parallel N-pass machinery (`fanout.run_competitor_fanout`). The 3.0.12 follow-up tried to wire per-entity Step 0.55 into it but failed when no web backend was configured. The elegant move: stop maintaining two architectures. vs-mode and `--competitors` both use `fanout.py`. `--competitors` becomes a SKILL.md-level shortcut that discovers 2 peers and hands off to vs-mode.

Four 2026-04-22 test receipts (Warriors, Seattle, Arizona Wildcats, Kanye West) all confirmed the user's pain points:

- Peers thin because they ran without per-entity handle/sub targeting.
- Only one `*-raw.md` per run — no per-entity audit.
- Kanye peers leaked main topic's `--subreddits`.
- Engine footer nudging `BRAVE_API_KEY` to Claude Code users who already have WebSearch.
- Polymarket noise on ambiguous topics (Warriors → Glasgow rugby; Arizona → Diamondbacks).

This plan closes all of them by unifying the architecture and making hosting-model-driven Step 0.55 per entity the canonical path.

## Requirements Trace

- R1. vs mode (any topic containing ` vs ` / ` versus `) runs N full `pipeline.run()` calls in parallel, one per entity. Each sub-run uses its entity's own Step 0.55 targeting (from the hosting model's pre-resolution, passed via a new `--competitors-plan` JSON).
- R2. `--competitors` (and `--competitors=N`) becomes a SKILL.md-level shortcut: the hosting model (a) discovers N peers via WebSearch, (b) runs Step 0.55 per entity (main + peers), (c) rewrites the topic to `"main vs peer1 vs peer2"`, (d) invokes the engine with `--competitors-plan` containing each entity's targeting.
- R3. New `--competitors-plan` JSON flag. Schema: `{entity_name: {x_handle, x_related, subreddits, github_user, github_repos, context}}`. Implies vs mode when present with a single-entity topic. Applies per-entity targeting to each sub-run. Accepts inline JSON or a file path (matches `--plan`).
- R4. Each entity's sub-run saves its own `*-raw.md` file when `--save-dir` is in use. Example: `/last30days "Kanye West vs Drake vs Kendrick Lamar" --save-dir=~/Documents/Last30Days` produces `kanye-west-raw.md`, `drake-raw.md`, `kendrick-lamar-raw.md`. Same filenames a single-entity run of each topic would produce. Matches historical vs-mode behavior.
- R5. Each per-entity saved file includes its own single-row `## Resolved Entities` block so the audit survives. The merged comparison stdout still shows the full 3-row block.
- R6. Override-leak fix: no main-topic flags (`--subreddits`, `--x-handle`, `--x-related`, `--tiktok-*`, `--ig-creators`, `--github-*`) leak into peer sub-runs. Every per-entity kwarg is scrubbed at the sub-run call site.
- R7. LAW 7-style stderr for `--competitors` invocations with no list, no plan, no backend is reframed for hosting-model context: leads with "use your WebSearch to discover peers, resolve Step 0.55 per entity, re-invoke with `topic vs peer1 vs peer2 --competitors-plan '...'`." Does not lead with BRAVE_API_KEY.
- R8. Footer nudge `💡 You can unlock native grounded web search with BRAVE_API_KEY...` is suppressed when `--plan` or `--competitors-plan` was passed.
- R9. Polymarket disambiguation: support `--polymarket-keywords "kw1,kw2"` to filter market matches; auto-skip Polymarket when topic is single-token-ambiguous and no override is provided.
- R10. Default `--competitors` count stays 2 peers (3-way comparison). Unchanged from 3.0.12.

## Scope Boundaries

- No changes to single-entity `pipeline.run()` semantics. Each sub-run in vs mode behaves identically to a bare `/last30days {entity}` invocation.
- No changes to the planner's comparison-intent logic for single-entity-containing topics. The `_should_force_deterministic_plan` shortcut for vs-topics routes to fanout, not to its current single-pipeline path.
- No new emit modes. Comparison output format unchanged.
- No removal of `--competitors-list`. Stays as a minimum escape hatch (names-only, no per-entity targeting) for scripted headless use.
- No removal of engine-internal `resolve.auto_resolve()` in fanout. Remains as headless / cron fallback for users with BRAVE/EXA/SERPER/PARALLEL/OPENROUTER keys. The dominant Claude Code path bypasses it via `--competitors-plan`.

### Deferred to Separate Tasks

- Explicit "head-to-head" rivalry pass in vs-mode (a supplemental subquery like `"A vs B"` that catches rivalry articles missing from pure entity-scoped passes). Start with N independent passes; add a head-to-head supplemental pass if the rivalry-content gap shows up in dogfood.
- Cache layer for hosting-model pre-resolution.
- Cross-source disambiguation (not just Polymarket).
- Latency knob for users who want the old one-pass vs behavior (probably not needed; parallel N-pass is ~1× wall clock).

## Context & Research

### Relevant Code and Patterns

- `scripts/last30days.py` — main(), `_main_runner`, `_competitor_runner`, the competitor enable/discovery branch. Primary file.
- `scripts/lib/fanout.py` — existing orchestrator (3.0.11). Reused as-is; `competitor_runner` closure is where per-entity kwargs apply.
- `scripts/lib/planner.py` — `_should_force_deterministic_plan` detects vs-topics via regex. Current path synthesizes ONE comparison plan; new path routes to fanout.
- `scripts/lib/render.py` — `render_comparison_multi` (3.0.12) + `_render_resolved_entities_block`. Both reused. `render_full` needs a per-entity variant when saving sub-run files.
- `scripts/last30days.py` `save_output` — where raw files are written. Needs to iterate per entity when competitor_reports artifact present.
- `scripts/lib/quality_nudge.py` — BRAVE/SERPER nudge emission.
- `scripts/lib/polymarket.py` — source adapter for `--polymarket-keywords` and ambiguous-topic auto-skip.
- SKILL.md §551 "If QUERY_TYPE = COMPARISON" and §679 per-entity Step 0.55 protocol — the hosting-model contract that drives per-entity pre-resolution for both vs mode and `--competitors`.

### Institutional Learnings

- 3.0.11 plan (`2026-04-22-002`): built fanout.
- 3.0.12 plan (`2026-04-22-003`): tried engine-internal per-entity auto_resolve; failed without backend keys.
- 3.0.13 plan draft (`2026-04-22-004-...superseded`): proposed `--competitors-plan` JSON + vs-mode-shortcut path but kept them separate. User's 2026-04-22 correction unifies them.
- 2026-04-22 test receipts: Warriors, Seattle, Arizona Wildcats, Kanye West runs all reproduced the per-entity resolve gap.
- User's architectural steer: "vs mode should work that way too" + "--competitors is just vs mode with auto-discovery." This plan encodes that.

### External References

- None. All patterns in-repo.

## Key Technical Decisions

- **Unify vs-mode and --competitors on one orchestrator.** `fanout.run_competitor_fanout` serves both. vs-mode is "topic contains ' vs '" detection → fanout. `--competitors` is "SKILL.md shortcut → hosting model rewrites topic to vs form → fanout." One code path.
- **Per-entity targeting via `--competitors-plan` JSON.** Schema `{entity_name: {x_handle, x_related, subreddits, github_user, github_repos, context}}`. Mirrors `--plan`. Applies to both vs-mode and `--competitors` paths. Hosting model passes it after running Step 0.55 per entity.
- **N save files, one per entity.** Each sub-run writes a `{entity-slug}-raw.md` file when `--save-dir` is set. Matches historical vs-mode behavior. Single-entity runs unchanged.
- **Revert the "one pass for latency" optimization that removed per-entity passes.** Parallel execution via `ThreadPoolExecutor` means wall-clock is ~max(per-entity-latency), not sum. The old latency concern (13+ minutes for 3 serial passes) does not apply to a parallel fan-out.
- **Override-leak fix at the call site.** `_subrun_kwargs(entity, plan_entry)` helper returns fully explicit per-entity kwargs; no closure-default fallthrough from main scope.
- **LAW 7 stderr reframed, not just updated.** Current message treats BRAVE_API_KEY as the solution. New message treats hosting-model Step 0.55 as the solution, with backend keys listed only as the headless fallback.
- **Polymarket disambiguation is additive and conservative.** `--polymarket-keywords` is explicit; auto-skip only fires for a known-ambiguous single-token list.

## Open Questions

### Resolved During Planning

- **vs mode N passes or single-pass?** N passes. User's architectural correction.
- **Should --competitors still be an engine flag at all?** Yes, kept for headless / cron contexts with backend keys. Dominant Claude Code path is SKILL.md shortcut → vs-mode fanout. Engine flag stays as compatibility surface.
- **`--competitors-plan` JSON or multi-flag?** JSON. Matches `--plan`.
- **Default count?** 2 peers → 3-way comparison. Unchanged.
- **Saved-file naming?** `{entity-slug}-raw.md` per entity, same as single-entity runs would produce.

### Deferred to Implementation

- Exact trace of override-leak path (closure capture vs shared config vs Reddit adapter fallback). Test-first per Unit 2; patch at the right layer.
- Heuristic for single-token-ambiguous Polymarket auto-skip. Start with a short hard-coded list; iterate.
- Whether to include a head-to-head rivalry supplemental pass in vs-mode. Ship N-independent passes first; revisit after dogfood if rivalry content is missing.
- Exact filename convention when the comparison merged output is saved (if saved at all). Not blocking — per-entity files are the primary save artifact.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
User invokes:
   /last30days "OpenAI vs Anthropic vs xAI"
   OR
   /last30days OpenAI --competitors     (hosting model rewrites to vs form)
   OR
   /last30days OpenAI --competitors-list "Anthropic,xAI"
   OR
   /last30days "OpenAI vs Anthropic vs xAI" --competitors-plan '{...per-entity...}'

           ↓

scripts/last30days.py main():
   - Detect: topic has " vs " OR --competitors enabled
   - If --competitors and no list/plan: emit LAW 7-style stderr with hosting-model instruction
   - If --competitors with list or discovery: rewrite topic to vs form, continue
   - Parse --competitors-plan JSON, map to entities

           ↓

fanout.run_competitor_fanout (shared path):
   - For each entity (main + peers):
       - entity_config = dict(config)  [deep copy to prevent leak]
       - kwargs = _subrun_kwargs(entity, plan_entry)  [explicit; no main-topic leak]
       - If plan_entry missing a field AND backend available: auto_resolve() fill
       - pipeline.run(topic=entity, **kwargs, internal_subrun=True)
   - Parallel ThreadPoolExecutor
   - Collect per-entity Reports
   - Attach resolved targeting to each Report.artifacts["resolved"]

           ↓

scripts/last30days.py after fanout:
   - If --save-dir: save each entity's Report as {entity-slug}-raw.md
     Each file includes its own single-row Resolved Entities block
   - emit_comparison_output → render_comparison_multi (merged stdout)
     Includes full N-row Resolved Entities block
```

## Implementation Units

- [ ] **Unit 1: vs-topic detection routes to fanout (not single-pipeline)**

**Goal:** A topic containing ` vs ` / ` versus ` triggers `fanout.run_competitor_fanout` with the parsed entities. Each entity runs a full `pipeline.run()`. Replace the current single-pipeline-with-comparison-plan behavior.

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `scripts/last30days.py` (main() — detect vs-topic, route to fanout)
- Modify: `scripts/lib/planner.py` (remove / bypass the `_should_force_deterministic_plan` special case for vs topics; vs topics no longer go through `plan_query` as a single comparison plan)
- Test: `tests/test_vs_mode_fanout.py` (new)

**Approach:**
- Parse the incoming topic: if it contains ` vs ` or ` versus ` (case-insensitive), split into entities (reuse `planner._comparison_entities`-style logic or move that utility into main()).
- When vs-entities are detected, route to the same fanout branch `--competitors` uses today. The entity list comes from the topic string; no discovery step needed.
- Each entity runs `pipeline.run()` with its own plan (either from `--competitors-plan[entity]` or from the engine's per-entity fallback path).
- For back-compat, if the user passes both a vs-topic AND `--plan`, honor `--plan` for the main (first) entity and use per-entity defaults for peers unless `--competitors-plan` is also provided.

**Execution note:** Start with an integration test that runs `"A vs B"` via mock mode and asserts fanout was called with two entities + two pipeline.run calls.

**Patterns to follow:**
- 3.0.11 fanout wiring in `scripts/last30days.py`'s `--competitors` branch.
- `planner._comparison_entities` for the split logic.

**Test scenarios:**
- Happy path: topic `"A vs B"` → two pipeline.run calls, two Reports returned, merged render.
- Happy path: topic `"A vs B vs C"` → three pipeline.run calls.
- Happy path: topic `"A versus B"` → matches the same regex, two pipelines.
- Edge case: topic `"OpenAI vs"` (trailing empty entity) → treated as single-entity `"OpenAI"`, not vs mode.
- Edge case: topic contains "vs." (dot, no trailing space) → existing regex tolerates it; verify.
- Edge case: topic `"A vs B"` plus `--plan` → plan applies to first entity only, peers use per-entity defaults.
- Integration: full vs-mode run end-to-end in mock mode; verify rendered output, stderr has one `[Competitors] Comparing: A vs B vs ...` line.

**Verification:**
- Test assertions pass.
- Mock-mode smoke of `/last30days "OpenAI vs Anthropic"` shows fanout invocation, per-entity Reports, merged comparison output.

- [ ] **Unit 2: `--competitors-plan` JSON flag + `_subrun_kwargs` helper + override-leak fix**

**Goal:** New JSON flag threads per-entity targeting into each sub-run's `pipeline.run()`. A `_subrun_kwargs(entity, plan_entry)` helper is the single source of truth for per-entity kwargs, eliminating override-leak.

**Requirements:** R3, R6

**Dependencies:** None (can land alongside or before Unit 1)

**Files:**
- Modify: `scripts/last30days.py` (argparse + parse + `_competitor_runner` + `_subrun_kwargs` helper)
- Possibly modify: `scripts/lib/fanout.py` (no signature change expected; the competitor_runner contract is unchanged)
- Test: `tests/test_cli_competitors.py` (extend)
- Test: `tests/test_competitors_plan_threading.py` (new)
- Test: `tests/test_competitor_subrun_isolation.py` (new, regression)

**Approach:**
- Add `--competitors-plan` argparse flag. Accepts inline JSON or file path (mirror `--plan`).
- Validation: top-level dict; each value is a dict; unknown fields log warnings; malformed input exits 2. Case-insensitive entity matching.
- Schema: `{entity_name: {x_handle?, x_related?, subreddits?, github_user?, github_repos?, context?}}`.
- Build `_subrun_kwargs(entity, plan_entry)` — returns an explicit dict with every per-entity flag. No closure-default fallthrough. This is the leak fix.
- `_competitor_runner(entity)`:
  1. Get `plan_entry` from `--competitors-plan` if present.
  2. Build base kwargs with `_subrun_kwargs(entity, plan_entry)`.
  3. Fill missing fields via `resolve.auto_resolve(entity, entity_config)` only if backend is configured (3.0.12 fallback path).
  4. Call `pipeline.run(topic=entity, internal_subrun=True, **kwargs)`.
  5. Attach `resolved` dict to `report.artifacts`.
- Verify no per-entity flag from main() leaks via closure. The helper is the only source of per-entity values.

**Execution note:** Test-first for the override-leak regression. Use the Kanye 2026-04-22 receipt as the failing test input (main `--subreddits=Kanye,hiphopheads` + `--competitors-list "Drake"` → assert Drake's pipeline.run receives `subreddits=None`).

**Patterns to follow:**
- `--plan` parsing block in `scripts/last30days.py`.
- 3.0.12's `entity_config = dict(config)` deep-copy pattern.

**Test scenarios:**
- Happy path: `--competitors-plan '{"Drake":{"x_handle":"Drake","subreddits":["Drizzy"]}}'` → Drake's pipeline.run receives `x_handle="Drake"`, `subreddits=["Drizzy"]`. No auto_resolve call for Drake.
- Happy path: plan covers 2 of 3 entities, backend configured → covered skip auto_resolve; third falls back.
- Happy path: plan file path accepted like `--plan`.
- Happy path: case-insensitive entity match.
- Edge case: unknown fields → warn, ignore.
- Edge case: plan entry for entity not in list → warn, ignore.
- Error path: malformed JSON → exit 2.
- Error path: top-level JSON is list → exit 2.
- Regression (leak): main `--subreddits=A,B` + `--competitors-list "X"` + no plan → X's pipeline.run gets `subreddits=None`.
- Regression (leak): same for `--x-handle`, `--x-related`, `--tiktok-hashtags`, `--tiktok-creators`, `--ig-creators`, `--github-user`, `--github-repo`.
- Regression (leak): main `--x-handle=kanye` + plan `{"Drake":{"x_handle":"Drake"}}` → Drake's sub-run gets `x_handle="Drake"`, NOT `"kanye"`.

**Verification:**
- All regression tests pass.
- Smoke run (mock mode + plan): stderr shows per-entity `[Competitors] {entity}: x=... subs=...` line; no leak from main topic's flags.

- [ ] **Unit 3: Per-entity save files**

**Goal:** When `--save-dir` is set in a vs-mode or `--competitors` run, each entity's sub-run saves its own `{entity-slug}-raw.md` file — same format as a single-entity run would produce.

**Requirements:** R4, R5

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `scripts/last30days.py` (`save_output` iteration after fanout)
- Modify: `scripts/lib/render.py` (`render_full` includes single-row Resolved Entities block when that entity's `artifacts["resolved"]` is present)
- Test: `tests/test_save_raw_per_entity.py` (new)

**Approach:**
- After fanout completes, iterate `report.artifacts["competitor_reports"]` (or equivalent). For each `(entity, entity_report)`:
  - Call `save_output(entity_report, emit="md", save_dir=args.save_dir, suffix=args.save_suffix)`.
  - Uses entity's `slugify(entity)` for the filename. Same pattern a single-entity run uses.
- Each saved file invokes `render_full` (or the save-variant). `render_full` now checks for `report.artifacts["resolved"]` and prepends a single-row Resolved Entities block.
- Stderr logs one `[last30days] Saved output to <path>` line per entity.
- Single-entity runs unchanged (no extra files, render_full unchanged for them).

**Patterns to follow:**
- Existing `save_output` invocation in main() for single-entity runs.
- `slugify(topic)` for filename.
- 3.0.12's `_render_resolved_entities_block` (reused, single-row mode).

**Test scenarios:**
- Happy path: `/last30days "A vs B vs C" --save-dir=/tmp/x` → `/tmp/x/a-raw.md`, `/tmp/x/b-raw.md`, `/tmp/x/c-raw.md` exist.
- Happy path: `--competitors-list "Drake,Kendrick" --save-dir=/tmp/x` on topic Kanye → three files: `kanye-west-raw.md`, `drake-raw.md`, `kendrick-lamar-raw.md`.
- Happy path: each file includes a single-row Resolved Entities block for its entity.
- Happy path: single-entity run with `--save-dir` → one file, no Resolved block (unchanged).
- Edge case: `--save-suffix=v3` → all N files get the suffix.
- Edge case: one entity sub-run failed → its file is NOT saved; the others are.
- Integration: `ls {save-dir}/*-raw.md` returns N files after a vs-mode run.

**Verification:**
- Test assertions pass.
- Manual vs-mode smoke saves N files.

- [ ] **Unit 4: LAW 7-style stderr reframe + footer-nudge suppression**

**Goal:** The `--competitors`-with-no-backend stderr tells the hosting model to do Step 0.55 per entity and pass `--competitors-plan`. The BRAVE/SERPER footer nudge is suppressed when `--plan` or `--competitors-plan` is present.

**Requirements:** R7, R8

**Dependencies:** Unit 2 (flag must exist)

**Files:**
- Modify: `scripts/last30days.py` (the `[Competitors] --competitors requires...` stderr block)
- Modify: `scripts/lib/quality_nudge.py` (or wherever footer nudge emits; verify during implementation)
- Test: `tests/test_competitors_no_backend_message.py` (new)
- Test: `tests/test_footer_nudge_suppression.py` (new)

**Approach:**
- Rewrite stderr in this order:
  1. "If you are the hosting reasoning model (Claude Code, Codex, Hermes, Gemini, or any agent with WebSearch), the recommended path: (a) discover N peers via WebSearch, (b) run Step 0.55 for main + each peer, (c) re-invoke as `/last30days 'topic vs peer1 vs peer2' --competitors-plan '{...}'`. See SKILL.md 'Competitor mode'."
  2. "Headless / cron path: set BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / PARALLEL_API_KEY / OPENROUTER_API_KEY and re-run."
  3. "Minimum escape hatch: `--competitors-list 'A,B,C'` skips discovery but does not pre-resolve peers."
- Suppress footer nudge when `external_plan` OR `competitors_plan` was passed.

**Test scenarios:**
- Happy path: `--competitors` with no backend, no list, no plan → stderr leads with "If you are the hosting reasoning model" and references `--competitors-plan` before naming API keys.
- Happy path: `--plan` passed → footer nudge does NOT fire.
- Happy path: `--competitors-plan` passed → footer nudge does NOT fire.
- Happy path: `--competitors-list` only (no plan, no backend) → footer nudge still fires (hosting model didn't fully engage).
- Happy path: no `--competitors`, no `--plan` → footer nudge unchanged.

**Verification:**
- Tests pass.

- [ ] **Unit 5: Polymarket disambiguation guard**

**Goal:** `--polymarket-keywords "kw1,kw2"` filters market matches; auto-skip Polymarket on single-token-ambiguous topics without override.

**Requirements:** R9

**Dependencies:** None

**Files:**
- Modify: `scripts/last30days.py` (argparse)
- Modify: `scripts/lib/polymarket.py`
- Test: `tests/test_polymarket_disambiguation.py` (new)

**Approach:**
- Add `--polymarket-keywords "kw1,kw2"`. When provided, Polymarket adapter filters market titles to those whose normalized text contains at least one keyword.
- Auto-skip: if topic is one token AND matches a known-ambiguous list (US state names, US city names, common sports/color/animal words) AND no `--polymarket-keywords`, skip Polymarket with stderr note.
- SKILL.md update (small): mention `--polymarket-keywords` in Step 0.55 instructions for ambiguous topics.

**Test scenarios:**
- Happy path: topic "Warriors", no override → Polymarket skipped; stderr note.
- Happy path: topic "Warriors", `--polymarket-keywords "nba,gsw"` → Polymarket runs, filtered.
- Happy path: topic "OpenAI" → Polymarket runs as before.
- Happy path: topic "Arizona Wildcats" (multi-token) → Polymarket runs as before.
- Edge case: `--polymarket-keywords ""` → treated as empty, no filter.

**Verification:**
- Warriors smoke → Polymarket footer absent or filtered.

- [ ] **Unit 6: SKILL.md rewrite — vs mode is the canonical path, `--competitors` is a shortcut**

**Goal:** SKILL.md documents the unified architecture. vs mode runs N full passes. `--competitors` is a SKILL.md-level shortcut that discovers 2 peers and invokes vs mode with `--competitors-plan`.

**Requirements:** R1, R2, R10 (surfaces them)

**Dependencies:** Units 1-4

**Files:**
- Modify: `SKILL.md` (§551 "If QUERY_TYPE = COMPARISON" rewrite; Competitor mode subsection rewrite)
- Modify: `README.md` (one-line example)

**Approach:**
- Rewrite §551 to describe the N-pass architecture: "When the user asks 'X vs Y' (or 'X vs Y vs Z'), run Step 0.55 per entity, then invoke the engine. The engine fans out N full pipelines in parallel. Each entity gets its own single-entity-grade coverage. Wall clock is close to a single run."
- Remove the "ONE research pass with a comparison-optimized plan that replaces the old 3-pass approach" language.
- Add a `--competitors-plan` JSON example.
- Rewrite the Competitor mode subsection: "`--competitors` is a shortcut. The hosting model: (1) runs WebSearch to discover N=2 peers, (2) runs Step 0.55 for main + each peer, (3) rewrites topic to `'main vs peer1 vs peer2'`, (4) invokes engine with `--competitors-plan '{...}'`. Engine flag `--competitors` and `--competitors-list` remain for headless fallback."
- Cross-reference §679 (per-entity Step 0.55 protocol).
- Warning: a thin `## Resolved Entities` block (dashes for any entity) means the hosting model skipped Step 0.55 for that one.

**Patterns to follow:**
- Existing §679 per-entity Step 0.55 protocol for tone.
- 3.0.12 Competitor mode prose for terseness.

**Test scenarios:**
- Test expectation: none — documentation. Verification is dogfood.

**Verification:**
- `/last30days "OpenAI vs Anthropic vs xAI"` in a fresh Claude Code window produces 3 save files with populated Resolved blocks and non-dash per-entity targeting.
- `/last30days OpenAI --competitors` produces same after discovery step.

- [ ] **Unit 7: Version 3.0.13, CHANGELOG, sync, hot-copy**

**Goal:** Ship 3.0.13 to all local targets.

**Requirements:** Closes R1-R10

**Dependencies:** Units 1-6

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Run: `bash scripts/sync.sh`
- Hot-copy: `~/.claude/plugins/cache/last30days-skill/last30days/3.0.13/`

**Approach:**
- CHANGELOG: group the changes. "Changed: vs mode now runs N full passes in parallel, one per entity — reverting the one-pass optimization to restore per-entity depth. Added: --competitors-plan JSON for per-entity Step 0.55 targeting (applies to vs mode and --competitors). Changed: --competitors is now a SKILL.md shortcut for vs-with-discovery. Added: per-entity *-raw.md save files. Fixed: override-leak from main to peer sub-runs. Changed: LAW 7 stderr framing for hosting-model context. Changed: BRAVE/SERPER footer nudge suppressed when --plan / --competitors-plan present. Added: --polymarket-keywords + auto-skip for ambiguous topics."
- Beta channel first per CLAUDE.md.
- Hot-copy so public `/last30days` picks up 3.0.13.

**Test scenarios:**
- Test expectation: none — packaging.

**Verification:**
- `grep version .claude-plugin/plugin.json` → 3.0.13.
- `sync.sh` exits 0.
- Hot-copy contains the new files.

## System-Wide Impact

- **Interaction graph:** vs-mode and `--competitors` share one orchestrator (`fanout.run_competitor_fanout`). `_subrun_kwargs` is the single source of per-entity kwargs. Save loop iterates per entity.
- **Error propagation:** Per-entity sub-run failure → logged, dropped, continue (3.0.11 behavior unchanged). `--competitors-plan` JSON parse errors exit 2 (same shape as `--plan`).
- **State lifecycle risks:** `entity_config = dict(config)` deep-copy pattern extends to every per-entity flag (Unit 2 fix). No cross-entity context leak.
- **API surface parity:** `--competitors-plan` is additive. `--competitors`, `--competitors-list`, `--plan` unchanged. `--polymarket-keywords` additive. vs-mode keeps its topic-string surface.
- **Integration coverage:** New vs-mode-fanout integration test. New override-leak regression test. New plan-threading test. New nudge-suppression test. New per-entity-save test. New Polymarket disambiguation test.
- **Unchanged invariants:** `pipeline.run()` signature unchanged. Single-entity render path unchanged. LAW 7 on the default path unchanged (still fires when a single-entity run lacks `--plan`).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| vs-mode N-pass latency feels slower for users who remember the one-pass shortcut. | Parallel execution keeps wall-clock ~= max(per-entity-latency), not sum. `--quick` on a vs-topic still applies to each sub-run. CHANGELOG calls out the revert + parallelism. |
| API cost scales linearly with N (per source). | Default count 2 caps it. Hard max 6 on `--competitors`. vs-mode users opted into N entities explicitly. |
| Rivalry content ("A vs B" articles) missed in N-independent passes. | Deferred to separate task (head-to-head supplemental pass). Start shipping and observe whether this is actually a gap. |
| Hosting model skips `--competitors-plan` and uses `--competitors-list` only. | Unit 4 stderr reframe steers explicitly. SKILL.md Unit 6 makes the plan-path canonical. Thin Resolved block in output makes skipped-Step-0.55 visible. |
| Override-leak fix misses a subtle closure path. | Unit 2 is test-first with the Kanye receipt as the failing input. Regression test asserts every per-entity flag is None unless plan provides it. |

## Documentation / Operational Notes

- Beta channel first per CLAUDE.md.
- After merge: hot-copy to `~/.claude/plugins/cache/last30days-skill/last30days/3.0.13/`.
- CHANGELOG explicitly frames the vs-mode change as an architectural revert-with-parallelism, not a regression to the old serial N-pass.

## Sources & References

- Superseded plan: `docs/plans/2026-04-22-004-fix-competitors-hosting-model-resolve-and-leak-plan.md.superseded`
- Previous plan (3.0.12): `docs/plans/2026-04-22-003-fix-competitors-per-entity-resolution-plan.md`
- Initial plan (3.0.11): `docs/plans/2026-04-22-002-feat-competitors-flag-comparison-fanout-plan.md`
- 2026-04-22 test session receipts (Warriors, Seattle, Arizona Wildcats, Kanye West)
- SKILL.md §551 + §679 — the per-entity Step 0.55 protocol the hosting model uses for both paths
- Related code: `scripts/lib/fanout.py`, `scripts/last30days.py` `_competitor_runner`, `scripts/lib/planner.py` vs-topic special-case, `scripts/lib/render.py` `_render_resolved_entities_block`, `scripts/lib/polymarket.py`, `scripts/lib/quality_nudge.py`
- Related PRs: #308 (3.0.11), #309 (3.0.12)
