---
title: "fix: per-entity resolution, default-2, and stale-path guard for --competitors"
type: fix
status: active
date: 2026-04-22
origin: docs/plans/2026-04-22-002-feat-competitors-flag-comparison-fanout-plan.md
---

# fix: per-entity resolution, default-2, and stale-path guard for --competitors

## Overview

Three test runs of v3.0.11 `--competitors` surfaced four real bugs plus one product tweak. This plan fixes all of them in a single follow-up:

1. Competitor sub-runs get no Step 0.55 resolution (no X handle, no subreddits, no GitHub repo). Drake / Kendrick / Travis ran with deterministic-fallback single-word queries while Kanye had the full targeting package. User called it "lazy" and was right.
2. Two of three test windows (Linear, Coinbase) never invoked the new flag at all. They loaded SKILL.md from `plugins/marketplaces/last30days-skill/` (a Claude-Code-managed git clone pinned to origin/main, which predates PR #308) instead of `plugins/cache/last30days-skill/last30days/3.0.11/`, so `--help` showed no `--competitors` flag and the model fell back to the manual comparison path.
3. Each competitor sub-run emits a scary `[Planner] No --plan passed... deterministic fallback` stderr line because LAW 7 targets the hosting-model path, not internal fan-out sub-runs.
4. Default competitor count is 3 (→ 4-way comparison). User wants default 2 (→ 3-way: original + 2 peers). Flag keeps `--competitors=N` to customize.

## Problem Frame

The 3 test runs (Kanye, Linear, Coinbase) showed a pattern:

| Window | Loaded SKILL.md from | Invoked --competitors? | Per-entity resolution? | Outcome |
|--------|----------------------|-----------------------|------------------------|---------|
| Kanye  | cache/3.0.11/ (correct) | Yes | Only for main topic (Kanye) | Drake/Kendrick/Travis thin; Reddit 403 fallbacks |
| Linear | marketplaces/ (stale) | No — fell back to manual comparison | No | Thin run with noisy subreddits |
| Coinbase | marketplaces/ (stale) | No — fell back to manual comparison | Main only; keyword-search poisoned pool | Top subs: r/survivor, r/Airpodsmax (noise) |

Root causes:
- **Per-entity resolution gap:** `scripts/lib/fanout.py` calls `pipeline.run()` with topic + depth + web_backend + lookback_days only. It does not call `resolve.auto_resolve()` per entity, so sub-runs have no X handle, subreddit, or GitHub targeting. The original plan (`2026-04-22-002`) acknowledged this as a deliberate v1 simplification ("competitor sub-runs use planner defaults"). In practice this produces visibly asymmetric output and triggers downstream retrieval issues (403 fallbacks, keyword-search noise).
- **Stale-path loading:** Claude Code's skill loader alphabetizes `find` results with `marketplaces/` before `cache/`, and the model reads the first plausible SKILL.md it sees. SKILL.md line 823's `SKILL_ROOT` resolver is the correct path but only fires in engine-invocation blocks, not in the skill-load step.
- **LAW 7 in sub-runs:** LAW 7 exists because the *hosting reasoning model* is supposed to pass `--plan`. For competitor sub-runs, there is no hosting-model planning — it's an engine-internal fan-out. The warning is a false positive there.

## Requirements Trace

- R1. Default `--competitors` count is 2 peers (3-way comparison: original + 2).
- R2. Each competitor sub-run performs Step 0.55 resolution (X handle, subreddits, GitHub user/repos, news context) before its pipeline runs — not just the main topic.
- R3. Sub-runs do not emit the LAW 7 `No --plan passed` warning; they are internal fan-out, not hosting-model calls.
- R4. The rendered comparison output includes a visible "Resolved entities" block showing per-entity handles/subs/github for debug transparency (answers "did it resolve everyone?" without the user having to read stderr).
- R5. SKILL.md has a canonical-path self-check at the top: if the reader loaded it from anywhere other than `plugins/cache/last30days-skill/last30days/{VERSION}/`, re-read from the versioned path before proceeding.
- R6. Version bumps to 3.0.12; CHANGELOG entry; `scripts/sync.sh` deploys.

## Scope Boundaries

- No new discovery strategy. The web-search + regex extraction in `scripts/lib/competitors.py` stays as-is.
- No new CLI flags beyond the behavior changes above. Specifically: no per-entity override flags like `--competitor-handles`. The hosting-model escape hatch remains `--competitors-list`.
- No changes to the explicit `A vs B` comparison path (topic-string parsing in `planner._comparison_entities`).
- No marketplace-clone auto-restore fix — that's Claude Code harness behavior. This plan only guards against the symptom on the skill side.

### Deferred to Separate Tasks

- Caching of per-entity resolution results: separate follow-up once hit rate justifies it.
- Fan-out rate-limiting tuning (currently `max_workers=len(entities)+1`, capped at 6): defer until we see real-world quota exhaustion.
- Pre-flight cost hint when N ≥ 4 (noted in `2026-04-22-002` risks): defer.

## Context & Research

### Relevant Code and Patterns

- `scripts/last30days.py:205-219` — `--competitors` / `--competitors-list` argparse definition (const=3 today; changing to 2).
- `scripts/last30days.py:220-290` — `resolve_competitors_args()` validator; update `COMPETITORS_DEFAULT`.
- `scripts/last30days.py:438-520` — main() fan-out orchestration; currently passes only topic/depth to each `_competitor_runner`.
- `scripts/lib/fanout.py:40-95` — `run_competitor_fanout()` signature. The `competitor_runner` callable is where per-entity resolution needs to happen.
- `scripts/lib/resolve.py:179-258` — `auto_resolve()` is the exact per-entity resolver to reuse. Already does X handle + subreddits + GitHub user/repos + news context in parallel via ThreadPoolExecutor.
- `scripts/lib/planner.py:80-135` — `plan_query()` emits the LAW 7 stderr. A `quiet: bool` keyword or `internal_subrun: bool` flag will suppress it.
- `scripts/lib/pipeline.py:162-220` — `pipeline.run()` signature. Needs a new keyword to propagate quiet-mode down to the planner.
- `scripts/lib/render.py:render_comparison_multi` — where the "Resolved entities" block is inserted.
- `SKILL.md` line 823 — canonical `SKILL_ROOT` resolver already exists but fires in engine bash, not at skill-load time.

### Institutional Learnings

- `docs/plans/2026-04-22-002-feat-competitors-flag-comparison-fanout-plan.md` acknowledged the per-entity-resolution gap as a v1 tradeoff. This plan closes that gap.
- Kanye run stderr: `[Planner] No --plan passed... deterministic fallback` × 3 (once per competitor sub-run). That's the LAW 7 noise R3 targets.
- Linear / Coinbase runs loaded `plugins/marketplaces/last30days-skill/CLAUDE.md` as the first hit. That's the stale-path issue R5 targets.

### External References

- None. All patterns are in-repo.

## Key Technical Decisions

- **Per-entity resolve happens inside fanout, not in SKILL.md.** The user-facing promise of `--competitors` is "one flag, engine does the work." Pushing resolution onto the hosting model creates another path-of-least-resistance trap (model skips it, output looks lazy). Auto-resolve inside each sub-run when a web backend is available makes the feature self-contained.
- **Stale-path guard is a SKILL.md self-check, not a code change.** We cannot stop Claude Code from auto-restoring the marketplace clone. But we can put a 3-line banner at the top of SKILL.md that forces any path-mismatched read to re-read from the versioned cache. Both the marketplace copy (once main catches up) and the cache copy carry the guard.
- **LAW 7 suppression is opt-in via `internal_subrun=True` keyword.** Do not remove the warning from the default path — it's load-bearing for the hosting-model contract. Add an explicit bypass for engine-internal fan-out only.
- **Default 2, hard max 6 unchanged.** "Original + 2" matches the Kanye/Drake/Kendrick mental model from the feature description. Still allow `--competitors=N` from 1 to 6.
- **Resolved block is inside the EVIDENCE envelope, not above it.** Keeps the rendered output structure stable for the synthesis contract (LAW 1–8). The block is context, not output.
- **Skip auto-resolve when `--mock` or no web backend.** Mirrors the existing `resolve.auto_resolve()` fast-fail and keeps the mock test path deterministic.

## Open Questions

### Resolved During Planning

- **Where does per-entity resolve live?** Inside `fanout.run_competitor_fanout`, not in `main()`. Each sub-run calls `auto_resolve()` just before `pipeline.run()`.
- **Should the hosting model still be able to override?** Yes — `--competitors-list` remains the escape hatch. When an explicit list is passed, the engine still does auto-resolve per entity; the user's list just skips discovery.
- **Should sub-runs run auto-resolve in parallel with each other?** Yes. The existing `ThreadPoolExecutor` in fanout already parallelizes sub-runs; auto-resolve happens inside each sub-run's thread, so resolve calls for different entities run concurrently.
- **Default count:** 2 peers (3-way). Confirmed.

### Deferred to Implementation

- Whether to expose a `--no-auto-resolve-competitors` flag for power users who want the fast, shallow behavior. Probably not needed v2; ship auto-resolve always-on and revisit if someone complains about cost.
- Whether to surface the per-entity resolution context back into the main topic's planner (cross-entity context sharing). Stays deferred.
- Whether the Resolved block should be collapsible or always inline. Start inline; revisit based on output length feedback.

## Implementation Units

- [ ] **Unit 1: Default `--competitors` to 2 peers**

**Goal:** Change the bare `--competitors` default from 3 to 2 per user feedback. `--competitors=N` still overrides; range 1..6 unchanged.

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `scripts/last30days.py` (`COMPETITORS_DEFAULT`, `--competitors` const, stderr messages if any reference 3)
- Modify: `SKILL.md` Competitor mode section ("discovered 2-6" wording, bare-flag default line)
- Modify: `README.md` auto-discovered example line (if it references count)
- Test: `tests/test_cli_competitors.py`

**Approach:**
- Change `COMPETITORS_DEFAULT = 3` → `2` in `scripts/last30days.py`.
- Change argparse `--competitors` `const=3` → `const=2`.
- Update any SKILL.md / README copy referencing "3 peers" to "2 peers" (default) or "2-6 peers" (range).

**Patterns to follow:**
- Existing default constants in `scripts/last30days.py` argparse block.

**Test scenarios:**
- Happy path: bare `--competitors` yields count=2, enabled=True, empty explicit_list.
- Edge case: `--competitors=3` still works (explicit override).
- Edge case: existing `test_bare_flag_defaults_to_three` test is updated to `test_bare_flag_defaults_to_two` and asserts count=2.
- Edge case: `--competitors=5` with a `--competitors-list` of length 2 still logs the mismatch warning and uses the list.

**Verification:**
- `pytest tests/test_cli_competitors.py -v` passes with the updated default.

- [ ] **Unit 2: Per-entity Step 0.55 resolution inside fanout**

**Goal:** Each competitor sub-run auto-resolves its own X handle, subreddits, GitHub user/repos, and news context via `resolve.auto_resolve()` before its `pipeline.run()` call — just like the main topic.

**Requirements:** R2

**Dependencies:** None (but Unit 3 should land together so sub-runs don't emit LAW 7 stderr while the resolution context is being passed)

**Files:**
- Modify: `scripts/lib/fanout.py`
- Modify: `scripts/last30days.py` (`_competitor_runner` closure builds the resolved args)
- Test: `tests/test_competitor_fanout.py`
- Test: `tests/test_competitors_resolve_integration.py` (new; covers the auto-resolve path)

**Approach:**
- `_competitor_runner(entity)` in main() does:
  1. Call `resolve.auto_resolve(entity, config)` when `not args.mock` and a web backend is configured (reuse `_has_backend`).
  2. Extract resolved x_handle, subreddits, github_user, github_repos, context.
  3. Pass them to `pipeline.run()` for that sub-run.
  4. Inject resolved context into a per-entity config copy (so `_auto_resolve_context` does not leak across sub-runs — deep-copy the config or use a local dict).
  5. Store the resolved block on the Report's `artifacts` so the renderer can surface it (Unit 4).
- When `args.mock` is True or no backend is available, skip auto-resolve (fall through to planner defaults, matching the existing `auto_resolve()` early-return contract).
- Update `fanout.run_competitor_fanout` docstring to note that auto-resolve happens inside the caller-provided runner.

**Execution note:** Start with a failing integration test that exercises two-entity fanout + auto-resolve via a mocked `resolve.auto_resolve` and asserts that `pipeline.run` receives the resolved x_handle/subreddits for each entity.

**Patterns to follow:**
- `scripts/last30days.py` main topic branch (`if args.auto_resolve and not external_plan`) already calls `resolve.auto_resolve` and propagates results — mirror the shape for competitors.
- Config isolation: `scripts/lib/pipeline.py:162-220` reads config as-is; use `dict(config)` to avoid cross-sub-run mutation of `_auto_resolve_context`.

**Test scenarios:**
- Happy path: 3 entities, mocked `auto_resolve` returns distinct handles per entity; `pipeline.run` receives `x_handle=@drake` for Drake, `x_handle=@kendricklamar` for Kendrick, etc.
- Happy path: the main topic still uses the user-supplied `--x-handle` / `--subreddits` overrides (not overwritten by auto-resolve for the main). Competitors use their own auto-resolved values.
- Edge case: `--mock` skips auto-resolve entirely for all sub-runs (no `resolve.auto_resolve` calls).
- Edge case: `resolve.auto_resolve` returns empty dicts for one entity (low-signal topic) — the sub-run still executes with planner defaults; doesn't crash.
- Edge case: no web backend configured — auto-resolve returns empty for every entity, sub-runs fall through to planner defaults, no stack trace.
- Error path: `resolve.auto_resolve` raises — the sub-run logs a warning and continues with planner defaults (does not fail the whole comparison).
- Integration: config `_auto_resolve_context` from entity A does not leak into entity B's `pipeline.run`. Assert each sub-run gets its own context string.

**Verification:**
- New integration test passes.
- End-to-end smoke (mock mode + explicit list): each sub-run's stderr shows `[AutoResolve]` lines per entity with distinct values.

- [ ] **Unit 3: Suppress LAW 7 warning for engine-internal sub-runs**

**Goal:** The `[Planner] No --plan passed... deterministic fallback` warning does not fire during competitor sub-runs. LAW 7 is load-bearing for hosting-model contracts and must stay on the default path; this is an opt-in bypass for internal fan-out only.

**Requirements:** R3

**Dependencies:** Unit 2 (so the sub-run call site is already being modified)

**Files:**
- Modify: `scripts/lib/planner.py` (`plan_query` signature + conditional stderr)
- Modify: `scripts/lib/pipeline.py` (`run` signature + propagation)
- Modify: `scripts/last30days.py` or `scripts/lib/fanout.py` (pass `internal_subrun=True` for competitor runners)
- Test: `tests/test_planner_v3.py` (or new `tests/test_planner_quiet_mode.py`)
- Test: `tests/test_competitor_fanout.py` (assert sub-runs don't emit LAW 7 stderr)

**Approach:**
- Add a keyword `internal_subrun: bool = False` to `planner.plan_query`. When True, skip the two `print(..., file=sys.stderr)` blocks that emit the LAW 7 banner and the `[Planner] No --plan passed` capability message.
- Add the same keyword to `pipeline.run()`; pass through to `plan_query`.
- In main()/fanout, set `internal_subrun=True` for every competitor sub-run's pipeline.run call. The main topic's pipeline.run keeps the default (LAW 7 stays on for the hosting-model path).
- Also suppress the LAW 7-triggered degraded-run warning block in the render layer for sub-reports when the envelope is going to be merged into a comparison output (or accept that the block is per-entity and surfaces once per entity).

**Patterns to follow:**
- Existing keyword-only parameters on `pipeline.run` (`mock`, `x_handle`, etc.).
- `planner.plan_query` signature is already keyword-only.

**Test scenarios:**
- Happy path: `plan_query(..., internal_subrun=True, provider=None, model=None)` returns the deterministic fallback plan WITHOUT writing the LAW 7 stderr block.
- Happy path: `plan_query(...)` with default `internal_subrun=False` still writes the LAW 7 warning (unchanged behavior).
- Integration: end-to-end competitor fanout; assert captured stderr contains zero occurrences of `No --plan passed` and zero of `YOU ARE the planner`.
- Integration: main topic is not part of competitor mode; if the user invokes bare `/last30days OpenAI` without `--plan`, LAW 7 stderr fires exactly once (regression test).

**Verification:**
- Running the Kanye-style smoke test shows zero `[Planner] No --plan passed` lines for Drake / Kendrick / Travis sub-runs.

- [ ] **Unit 4: "Resolved entities" block in comparison output**

**Goal:** The rendered comparison output includes a visible block listing per-entity handles, subreddits, GitHub user, and resolved context. Answers "did it resolve everyone?" at a glance without reading stderr.

**Requirements:** R4

**Dependencies:** Unit 2 (needs resolved data on report artifacts)

**Files:**
- Modify: `scripts/lib/render.py` (`render_comparison_multi` and `render_comparison_multi_context`)
- Test: `tests/test_render_comparison_multi.py`

**Approach:**
- When each entity's `Report.artifacts` contains a `resolved` dict (populated by Unit 2), `render_comparison_multi` emits a `## Resolved Entities` block early in the EVIDENCE envelope:
  ```
  ## Resolved Entities
  - **Kanye West**: X @kanyewest | Subs r/Kanye, r/hiphopheads | GitHub: — | Context: BULLY released, UK ban…
  - **Drake**: X @Drake | Subs r/DrakeTheType, r/hiphopheads | GitHub: — | Context: ICEMAN rollout…
  - **Kendrick Lamar**: X @kendricklamar | Subs r/KendrickLamar | GitHub: — | Context: Grammy wins, dormant…
  ```
- Missing fields render as `—` not empty.
- When no entity has a `resolved` payload (mock mode, no web backend), omit the block entirely rather than emit an empty section.
- Context strings are truncated at 120 chars to keep the block scannable.

**Patterns to follow:**
- Existing `render_comparison_multi` envelope structure (lines ~395-480 in render.py).
- Existing per-entity evidence block format (`## {label}`) for consistency.

**Test scenarios:**
- Happy path: 3 entities each with a `resolved` artifact → block lists all 3 with their fields.
- Happy path: 2 entities, one with full resolution, one with partial (x_handle only) → missing fields render as `—`.
- Edge case: no entity has a resolved artifact → block is omitted entirely.
- Edge case: context string > 120 chars → truncated with ellipsis.
- Integration: rendered output passes through the same EVIDENCE envelope comments and synthesis contract (LAW 1–8 unchanged).

**Verification:**
- Snapshot tests confirm the block appears in the right spot with the right formatting.
- End-to-end smoke shows a realistic 3-entity Resolved block in the rendered output.

- [ ] **Unit 5: SKILL.md canonical-path self-check**

**Goal:** A top-of-file SKILL.md directive forces any reader (Claude Code, Codex, Hermes, Gemini) to verify they loaded from `plugins/cache/last30days-skill/last30days/{VERSION}/SKILL.md` before proceeding. If loaded from `marketplaces/` or any other path, re-read from the pinned versioned cache.

**Requirements:** R5

**Dependencies:** None

**Files:**
- Modify: `SKILL.md` (prepend a STEP 0 block before the existing STEP 0 / LAW list)

**Approach:**
- Add a numbered first step at the top (before or bundled with existing "STEP 0: ToolSearch preload"):
  ```
  ## STEP 0: Canonical Path Self-Check (must run first)

  Before reading anything else below, verify you loaded this SKILL.md from
  the versioned cache, not the marketplace clone:

      CANONICAL=$HOME/.claude/plugins/cache/last30days-skill/last30days/
      CANONICAL_LATEST=$(ls -d "$CANONICAL"*/ 2>/dev/null | sort -V | tail -1)

  If the SKILL.md you just read is not under $CANONICAL_LATEST, STOP. Re-read
  $CANONICAL_LATEST/SKILL.md and restart from here. Marketplace clones
  (`plugins/marketplaces/last30days-skill/`) are pinned to origin/main and
  can be stale; the versioned cache is the ground truth.
  ```
- Reinforce in the existing LAW 7 block that `--help` output must be read from the same pinned `SKILL_ROOT` to avoid flag-list skew.

**Patterns to follow:**
- Existing STEP 0 ToolSearch preload (top of SKILL.md) for tone / imperative voice.
- Existing `SKILL_ROOT` resolver snippet (line ~823).

**Test scenarios:**
- Test expectation: none — SKILL.md is documentation; no unit test, verified by follow-up user invocation.

**Verification:**
- In a fresh Claude Code window, `/last30days Test --competitors` loads SKILL.md, the model executes the STEP 0 self-check, and (if it had loaded from marketplaces/) switches to the cache path before running `--help` or the engine. Observable via the model's announced reasoning / task list.

- [ ] **Unit 6: Version bump, CHANGELOG, sync**

**Goal:** Ship 3.0.12 and deploy to all local targets.

**Requirements:** R6

**Dependencies:** Units 1-5

**Files:**
- Modify: `.claude-plugin/plugin.json` (version 3.0.11 → 3.0.12)
- Modify: `CHANGELOG.md`
- Run: `bash scripts/sync.sh`

**Approach:**
- CHANGELOG entry under `## [3.0.12]` dated 2026-04-22 covering the four fixes (Fixed: per-entity resolution; Fixed: LAW 7 sub-run noise; Changed: default count 3→2; Added: Resolved entities block; Added: canonical-path self-check in SKILL.md).
- `sync.sh` deploys to `~/.claude/plugins/cache/last30days-skill-private/...`, `~/.agents/`, `~/.codex/`, Hermes.
- Manual hot-copy to `~/.claude/plugins/cache/last30days-skill/last30days/3.0.12/` so the public `/last30days` slash command picks up the new version before PR merge (matches the 3.0.11 testing pattern).

**Test scenarios:**
- Test expectation: none — packaging only. Verification is by inspection.

**Verification:**
- `grep version .claude-plugin/plugin.json` returns `3.0.12`.
- `sync.sh` exits 0 with "Import check: OK" for each target.
- Hot-copied 3.0.12 directory contains the new files and `/last30days` picks up the new version (highest-version resolver).

## System-Wide Impact

- **Interaction graph:** Fanout sub-runs now call `resolve.auto_resolve` per entity. Each sub-run is independent; no shared mutable state with other sub-runs or with the main topic.
- **Error propagation:** `auto_resolve` failures inside a sub-run log a warning and degrade to planner defaults; do not propagate up to abort the comparison. Same contract as today for the main topic.
- **State lifecycle risks:** Config dict is mutated by `auto_resolve` (via `config["_auto_resolve_context"]`). Must deep-copy per sub-run or scope context to a local mapping — otherwise two sub-runs' context strings race.
- **API surface parity:** `pipeline.run` gains a keyword (`internal_subrun`); callers that don't pass it get the existing behavior. `planner.plan_query` gains the same. Backward compatible.
- **Integration coverage:** New integration test for the fanout + auto-resolve + render chain. Existing snapshot tests update to include the Resolved block.
- **Unchanged invariants:** Single-entity `/last30days` invocations (no `--competitors`) behave identically. Explicit `A vs B` comparison topics behave identically. LAW 7 still fires on the default hosting-model path. `render_compact` path is untouched.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Auto-resolving per competitor triples the WebSearch call volume (4 queries × 3 competitors = 12 extra web searches). | Fast-fail when no backend; user can pass `--competitors-list` to skip discovery but still get auto-resolve. Cost note in CHANGELOG. |
| Config mutation across sub-runs via `_auto_resolve_context`. | Unit 2 deep-copies config per sub-run before each `auto_resolve` + `pipeline.run` call. Integration test asserts no cross-entity leak. |
| LAW 7 suppression leaks onto the hosting-model path via a wrong default. | Default `internal_subrun=False`. Only fanout's competitor sub-runs set True. Unit test asserts bare-topic invocation still emits LAW 7. |
| SKILL.md STEP 0 banner gets ignored by the model (same failure mode as line 823 today). | Put it in the guaranteed-read top band (before LAW 1, above all other content), imperative voice, concrete `STOP` verb. Still not bulletproof but strictly better than current. |
| Default count change breaks assumptions in downstream tools or existing user muscle memory. | Changelog calls it out as Changed; `--competitors=3` still works for users who want the old default. |

## Documentation / Operational Notes

- Beta channel first: merge behind `/last30days-beta` via the private repo before cherry-picking to public. Follows the same process as 3.0.11.
- Version 3.0.12 is a fix release; no marketing post required.
- After merge, add a line to the PR description pointing at this plan.

## Sources & References

- Origin plan: `docs/plans/2026-04-22-002-feat-competitors-flag-comparison-fanout-plan.md`
- Related PR: #308 (v3.0.11 shipping --competitors)
- Test windows that surfaced the bugs: Kanye, Linear, Coinbase (2026-04-22 session)
- Related code: `scripts/lib/fanout.py`, `scripts/lib/resolve.py` (`auto_resolve`), `scripts/lib/planner.py` (`plan_query`), `scripts/lib/render.py` (`render_comparison_multi`)
