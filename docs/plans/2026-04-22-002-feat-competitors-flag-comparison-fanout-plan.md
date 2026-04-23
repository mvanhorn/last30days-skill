---
title: "feat: --competitors flag for auto-discovered comparison fan-out"
type: feat
status: active
date: 2026-04-22
---

# feat: --competitors flag for auto-discovered comparison fan-out

## Overview

Add a `--competitors` flag to the last30days engine that auto-discovers 2-4 peer entities for the topic, runs the full retrieval pipeline on each in parallel, and renders a multi-entity comparison. Invoking `last30days Kanye West --competitors` should resolve to "Kanye vs Drake vs Kendrick Lamar" and emit a comparison report covering all three. Invoking `last30days OpenAI --competitors` should resolve to "OpenAI vs Anthropic vs xAI vs Gemini" and emit a four-way comparison.

Discovery mirrors the existing `resolve.auto_resolve()` pattern used for X handles and subreddits at pipeline start — web search (Brave / Exa / Serper) plus deterministic extraction. Not an internal LLM call.

## Problem Frame

Users who want a comparison today must type "OpenAI vs Anthropic vs xAI" themselves. The `planner._comparison_entities()` path already handles explicit multi-entity topics and `render._render_comparison_scaffold()` already emits a 9-axis comparison table. What is missing is the discovery half — a user who types a single entity with `--competitors` should get the comparison for free.

This is also the natural next step after the Step 0.55 category-peer subreddit work (PR #305, merged 2026-04-22). That feature widens the subreddit set within a single topic; this feature widens the entity set into peer entities.

## Requirements Trace

- R1. New `--competitors` boolean flag that triggers competitor discovery and multi-entity fan-out.
- R2. New `--competitors-list="A,B,C"` to explicitly skip discovery (mirrors `--plan`, `--subreddits`, `--x-handle` overrides).
- R3. New `--competitors=N` short form to set competitor count inline (N in 1..6).
- R4. Default count is 3 competitors (original + 3 = 4-way comparison).
- R5. Competitor retrieval depth inherits the main run's depth (`--quick` / `--deep`); all entities run in parallel so wall clock stays close to a single run.
- R6. Discovery mirrors `resolve.auto_resolve()`: web search for peers, deterministic text extraction. No internal LLM dependency.
- R7. If no web search backend is configured and no `--competitors-list` was passed, engine emits a LAW 7-style stderr telling the host agent to pass `--competitors-list` and exits non-zero.
- R8. Output rendering is a single comparison report covering all entities, reusing the existing 9-axis scaffold from `render._render_comparison_scaffold()` where applicable.

## Scope Boundaries

- Synthesis prompt changes beyond wiring N reports into the existing comparison scaffold are out of scope.
- `--competitors` does not replace the existing explicit "A vs B vs C" topic parsing in `planner._comparison_entities()`; both paths coexist.
- No caching layer for discovery results in v1.
- No UI/SKILL.md rewrite of the entire comparison section; only the new flag is documented.
- No new web search backend.

### Deferred to Separate Tasks

- Caching of competitor lookups: separate follow-up once hit rate justifies it.
- Disambiguation UX for topics with multiple common entities ("Amazon" the company vs the river): separate brainstorm.

## Context & Research

### Relevant Code and Patterns

- `scripts/last30days.py:168-249` — `build_parser()` argparse definitions. Existing depth flags (`--quick`, `--deep`) and override flags (`--plan`, `--subreddits`, `--x-handle`, `--auto-resolve`) set the convention to mirror.
- `scripts/lib/resolve.py:179-258` — `auto_resolve()` is the reference pattern: web search fan-out via `ThreadPoolExecutor`, per-query extraction functions, graceful empty-dict return when no backend is available.
- `scripts/lib/resolve.py:98-140` — `_extract_x_handle()` and sibling extractors show the deterministic text-mining style competitor extraction should mirror.
- `scripts/lib/pipeline.py:162-220` — `pipeline.run()` signature is the fan-out target. One call per entity, each returning a `schema.Report`.
- `scripts/lib/planner.py:430-564` — Existing comparison-intent handling and `_comparison_entities()` entity extraction. The new flag feeds the same mental model but populates entities from discovery instead of from the topic string.
- `scripts/lib/render.py:333-392` — `_render_comparison_scaffold()` already emits a 9-axis markdown comparison table. The new multi-report renderer should reuse this helper by assembling a synthetic "A vs B vs C" topic header for it.
- `scripts/lib/grounding.py` + `scripts/lib/providers.py` — Web search backend resolution (Brave / Exa / Serper). Reused as-is.

### Institutional Learnings

- No existing `docs/solutions/` entries for competitor discovery or multi-entity fan-out.
- Recent plan `docs/plans/2026-04-22-001-fix-category-peer-subreddit-resolution-plan.md` established the precedent of deterministic peer expansion; this plan extends that idea from subreddits to entities.

### External References

- None gathered — local patterns are strong. `resolve.auto_resolve()` is a direct template.

## Key Technical Decisions

- **Discovery mirrors auto_resolve, not plan_query.** Web search + regex extraction, not an LLM call. Matches the user's explicit direction ("use the python brain the same way it searches for X handles"). Cheaper, no provider credential requirement, deterministic.
- **Orchestration lives in `last30days.py` main, not inside `pipeline.run()`.** The fan-out is a top-level concern — one pipeline run per entity, each independent. Keeps `pipeline.run()` single-entity and unchanged except for sharing a `ThreadPoolExecutor` factory.
- **Sub-runs inherit main depth and run in parallel.** Wall clock ≈ single run; token cost scales linearly with N. User-controlled via the existing `--quick`/`--deep` flags.
- **New module `scripts/lib/competitors.py` instead of adding to `resolve.py`.** Keeps resolve focused on single-entity entity-bundle discovery (handles/subreddits/github); competitors.py owns peer-entity discovery. Similar shape, different responsibility.
- **Multi-report render is additive in `render.py`.** New `render_comparison_multi(reports: list[Report]) -> str` composes a synthetic "A vs B vs C" topic and delegates to the existing scaffold + synthesis path where possible. No rewrite of the single-entity render path.
- **Default count = 3 competitors (4-way comparison).** Hard cap at 6.
- **LAW 7-style stderr when no backend and no list.** Matches how `planner.plan_query()` already tells the hosting agent to pass `--plan`.

## Open Questions

### Resolved During Planning

- **Discovery mechanism:** Web search via `grounding.web_search()`, not an internal LLM. User confirmed the auto_resolve pattern is the target.
- **Default competitor count:** 3 (original + 3 = 4-way).
- **Sub-run depth:** Inherit main depth, parallel execution.
- **Flag naming:** `--competitors` (standard argparse double-dash). `--competitors=N` for inline count. `--competitors-list="A,B,C"` to skip discovery.

### Deferred to Implementation

- Exact extraction heuristics for competitor names across Brave / Exa / Serper result shapes. The SERP text varies (listicles, comparison pages, "vs" pages); the initial implementation will start with listicle parsing plus a "X vs Y" pattern match, and harden against real results in the test phase.
- Handling of topic ambiguity ("Amazon", "Apple"). Initial behavior: trust whatever web search returns for the topic verbatim; disambiguation is a separate concern.
- Merge strategy when two entities return overlapping URLs (e.g., an "OpenAI vs Anthropic" article shows up in both runs). Likely dedupe at the clustering step, but defer the exact policy until we see how often it happens.
- Whether to expose competitor discovery artifacts (the raw web search results) as a debug emit. Follow the existing `--debug` conventions.

## Implementation Units

- [ ] **Unit 1: CLI flag parsing and validation**

**Goal:** Add `--competitors`, `--competitors=N`, and `--competitors-list` to the argparse surface, validate values, and thread them into the main orchestration.

**Requirements:** R1, R2, R3, R4

**Dependencies:** None

**Files:**
- Modify: `scripts/last30days.py`
- Test: `tests/test_cli_competitors.py`

**Approach:**
- Add three mutually cooperative flags near line 205 in `build_parser()`:
  - `--competitors` with `nargs="?"` and `const=3` so bare `--competitors` defaults to 3, `--competitors=4` is honored, and `--competitors=0` is rejected
  - `--competitors-list` free-text CSV
- Normalize in `main()`: if `--competitors-list` is present, skip discovery and use the list. If `--competitors` is set and no list, trigger discovery with count = the flag value. Clamp count to 1..6 with a stderr warning at boundary.
- Thread the resulting entity list into the orchestrator added in Unit 3.

**Patterns to follow:**
- `--plan` argument at `scripts/last30days.py:187` — same skip-discovery-when-explicit shape.
- `--subreddits` / `--x-handle` at `scripts/last30days.py:180,189` — same override semantics.

**Test scenarios:**
- Happy path: bare `--competitors` parses to count=3, empty list.
- Happy path: `--competitors=4` parses to count=4.
- Happy path: `--competitors-list="A,B,C"` parses to count=3, list=["A","B","C"], and is preferred over any discovery signal.
- Edge case: `--competitors=0` and `--competitors=-1` are rejected with a clear error.
- Edge case: `--competitors=99` clamps to 6 with a stderr warning.
- Edge case: `--competitors` combined with `--competitors-list` uses the list and logs that discovery was skipped.
- Edge case: `--competitors-list` value with whitespace ("A, B , C") normalizes correctly.

**Verification:**
- Running the binary with each flag variation produces the expected post-parse state without calling out to the network.

- [ ] **Unit 2: `scripts/lib/competitors.py` discovery module**

**Goal:** Discover peer entities for a topic using web search + deterministic extraction, mirroring `resolve.auto_resolve()`.

**Requirements:** R6, R7

**Dependencies:** None (pure module; wired by Unit 3)

**Files:**
- Create: `scripts/lib/competitors.py`
- Test: `tests/test_competitors.py`

**Approach:**
- Public entry point `discover_competitors(topic: str, count: int, config: dict) -> list[str]`.
- Early return `[]` when `_has_backend(config)` is false (reuse the helper from `resolve.py`; factor if needed).
- Fan out 2-3 web searches in a `ThreadPoolExecutor`:
  - `"{topic} competitors"`
  - `"{topic} alternatives"`
  - `"{topic} vs"` (captures "X vs Y" articles)
- Feed results into a deterministic `_extract_peer_entities(results, topic)` that:
  - Mines titles and snippets for capitalized noun phrases other than the topic itself
  - Scores by frequency across results
  - Filters stopwords and the topic's own tokens
  - Returns top `count` unique entities ordered by score
- Emit a single-line stderr log mirroring the `resolve._log` format.

**Patterns to follow:**
- `scripts/lib/resolve.py:179-258` for the function shape, executor usage, and empty-result fallback.
- `scripts/lib/resolve.py:98-140` for extractor style (small, deterministic, no external state).

**Test scenarios:**
- Happy path: canned SERP fixtures for "OpenAI" return ["Anthropic", "xAI", "Google"] or close peers in the top 3.
- Happy path: canned SERP fixtures for "Kanye West" return rap peers (Drake, Kendrick) in the top 3.
- Edge case: empty SERP results return `[]` without raising.
- Edge case: extractor filters out the topic itself (case- and punctuation-insensitive).
- Edge case: near-duplicate entities ("OpenAI" vs "Open AI") dedupe to one slot.
- Error path: web search backend raises — the failure is logged and the function returns `[]`.
- Edge case: count=1 returns a single-element list; count=6 returns up to six entities.

**Verification:**
- Unit tests pass with fixtures committed under `tests/fixtures/competitors-*.json`.
- Manual run against a live backend for one topic confirms sensible output (recorded as a notes file, not a test assertion).

- [ ] **Unit 3: Parallel fan-out orchestrator**

**Goal:** Run `pipeline.run()` once per entity (topic + discovered competitors) in parallel, collect `schema.Report` per entity, and hand them to the comparison renderer.

**Requirements:** R5, R7

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `scripts/last30days.py`
- Possibly create: `scripts/lib/fanout.py` if the orchestrator grows past ~60 lines
- Test: `tests/test_competitor_fanout.py`

**Approach:**
- After arg parsing and before the existing `pipeline.run()` call, branch on `args.competitors`:
  - If a list was provided or discovery returned entities, build `entities = [topic, *competitors]`.
  - Spawn one `pipeline.run()` per entity via `ThreadPoolExecutor(max_workers=len(entities))`, passing the same `config`, `depth`, and all sub-run-relevant args (mock, plan, etc.). Respect `--plan` — if a plan is passed it applies to the main topic only; competitors use the internal planner fallback for v1.
  - Collect `{entity: Report}` mapping. A per-entity failure logs a stderr warning and drops that entity from the comparison; the run continues as long as 2 entities succeed.
  - If fewer than 2 entities survive, exit with a clear error.
- LAW 7-style stderr:
  - If `args.competitors` is set, no list was passed, no web search backend is configured, emit a LAW 7 stderr message pointing to the `--competitors-list` override and exit non-zero. Reuse the tone from `planner.plan_query()` fallback (`scripts/lib/planner.py:125-135`).

**Execution note:** Start with a failing integration test that exercises the full main → orchestrator → mocked pipeline.run path; the orchestrator is where bugs hide.

**Patterns to follow:**
- `scripts/lib/resolve.py:225-239` for ThreadPoolExecutor + as_completed + per-future error handling.
- `scripts/lib/pipeline.py:310+` for how ThreadPoolExecutor is already used inside a single run (same idiom, outer layer).

**Test scenarios:**
- Happy path: main + 2 competitors, all three `pipeline.run()` calls succeed (mocked), orchestrator returns 3 Reports.
- Happy path: discovery returns the competitor list; orchestrator fans out accordingly.
- Edge case: one of three competitor pipelines raises — the run continues with the surviving 2 and emits a warning.
- Edge case: all competitors fail but the main topic succeeds — orchestrator exits non-zero with a clear error rather than silently degrading to a single-entity render.
- Edge case: `--competitors` set, no backend, no list — orchestrator emits the LAW 7 stderr and exits non-zero before any pipeline call.
- Integration: wall-clock time for 3 mocked pipelines in parallel is close to the slowest single run, not the sum (timing assertion with generous margin).

**Verification:**
- End-to-end test with mocked `pipeline.run()` and mocked competitors discovery produces 3 Reports and hands them to a stubbed renderer.

- [ ] **Unit 4: Multi-report comparison renderer**

**Goal:** Compose N `schema.Report`s into a single comparison-mode output, reusing the existing 9-axis scaffold.

**Requirements:** R8

**Dependencies:** Unit 3

**Files:**
- Modify: `scripts/lib/render.py`
- Test: `tests/test_render_comparison_multi.py`

**Approach:**
- Add `render_comparison_multi(reports: list[schema.Report], *, emit: str) -> str`.
- Build a synthetic comparison topic: `f"{entity_a} vs {entity_b} vs {entity_c}"`.
- Reuse `_render_comparison_scaffold()` for the table skeleton. Each entity column is populated from its own Report's top clusters and citations.
- For the narrative synthesis block, concatenate per-entity highlights, clearly labeled by entity, under a shared "Comparison" header.
- Preserve existing emit modes (`compact`, `md`, `json`, `context`). In `json` emit, return a `{"entities": [...], "reports": [...]}` shape; single-Report consumers remain unaffected because the single-report render path is untouched.

**Patterns to follow:**
- `scripts/lib/render.py:333-392` (`_parse_comparison_entities`, `_render_comparison_scaffold`) — the scaffold is the contract.
- `scripts/lib/render.py` single-report rendering — for per-entity narrative blocks.

**Test scenarios:**
- Happy path: 3 Reports with distinct clusters render into a 3-column table and a "Comparison" section that mentions each entity at least once.
- Happy path: 2 Reports render as a 2-column table without breaking the scaffold.
- Edge case: a Report with an empty cluster list renders as "(no significant discussion this month)" in its column rather than crashing.
- Edge case: Reports with overlapping URLs (same article cited by two entities) dedupe citations at the footer but keep both column entries.
- Emit variants: `--emit=compact`, `--emit=md`, `--emit=json`, `--emit=context` each produce valid output with all entities represented.
- Integration: end-to-end snapshot test using fixture Reports, checked against a stored expected output (with a clear update path when the scaffold intentionally evolves).

**Verification:**
- Snapshot tests pass. Manual review of one real 3-way comparison confirms readability.

- [ ] **Unit 5: Docs, SKILL.md mention, and sync**

**Goal:** Document the new flag so the hosting agent and human users both know it exists, and run the sync script.

**Requirements:** R1-R8 (surfaces them to users)

**Dependencies:** Units 1-4

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md` (brief flag reference)
- Modify: `CHANGELOG.md`
- Run: `bash scripts/sync.sh`

**Approach:**
- Add a compact "Competitor mode" subsection under the existing comparison docs in `SKILL.md`. Document the flag, the default count, the override flag, and the LAW 7 fallback stderr.
- Keep `README.md` addition to a single example line.
- CHANGELOG entry mirrors the voice of recent entries (imperative, outcome-first).
- Sync via `scripts/sync.sh` per CLAUDE.md rules so `~/.claude/`, `~/.agents/`, `~/.codex/` pick up the new SKILL.md.

**Test scenarios:**
- Test expectation: none — documentation and sync only. Verification is by inspection and by running `sync.sh` and confirming target directories updated.

**Verification:**
- `sync.sh` completes without errors.
- `SKILL.md` rendered preview mentions `--competitors` in the comparison section.

## System-Wide Impact

- **Interaction graph:** `last30days.py main()` now orchestrates multiple `pipeline.run()` calls instead of one. No other callers of `pipeline.run()` are affected (it remains single-entity).
- **Error propagation:** Per-entity failures degrade gracefully as long as ≥2 entities survive; fewer survivors exits non-zero. Discovery failure with `--competitors` and no list is fatal.
- **State lifecycle risks:** Each sub-run uses its own `pipeline.run()` state; no shared mutable config. The `config` dict is read-only in `pipeline.run()` today — verify before committing to shared-reference passing, else deep-copy per sub-run.
- **API surface parity:** `--competitors` coexists with the existing explicit "A vs B vs C" topic parsing in `planner._comparison_entities()`. Both produce comparable output formats; the only difference is where the entity list came from.
- **Integration coverage:** The fan-out orchestrator crosses CLI → discovery → N pipelines → render; integration tests in Unit 3 and Unit 4 must exercise the full path end to end, not just unit-level.
- **Unchanged invariants:** `pipeline.run()` signature and single-entity semantics are unchanged. The single-entity render path in `render.py` is unchanged. No changes to `planner.plan_query()`. No changes to existing flags.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Competitor discovery returns garbage entities for niche topics. | `--competitors-list` override lets the user (or hosting agent) correct it. Unit tests with edge-case fixtures. Log discovery output to stderr under `--debug`. |
| Token cost scales linearly with N sub-runs. | Default count capped at 3, hard max 6, inherit `--quick` to let users throttle. Wall clock stays parallel. Emit a cost hint to stderr when N ≥ 4. |
| Merge conflicts against the single-entity render path during refactoring. | Keep the multi-report renderer strictly additive; do not modify the single-Report code path. |
| Config dict mutation inside sub-runs could leak state between entities. | Verify read-only usage before sharing references. If any sub-component mutates, deep-copy per sub-run before spawning threads. |
| A SERP extractor that works on Brave fixtures breaks on Exa/Serper result shapes. | Test fixtures for all three backends. Extractor operates on a normalized shape from `grounding.web_search()` (already the case), not raw provider output. |
| Hosting agent (Claude Code, Codex) unaware of the new flag when it could usefully pass `--competitors-list`. | SKILL.md updated in Unit 5 documents the flag in the same style as `--plan` and `--auto-resolve`. |

## Documentation / Operational Notes

- Beta channel first: per `CLAUDE.md`, experimental changes go to `mvanhorn/last30days-skill-private` on the `/last30days-beta` command. Land this on the private repo first, shake out on real topics for a day or two, then cherry-pick to public.
- After land-merge: run `scripts/sync.sh` to deploy SKILL.md + scripts to `~/.claude/`, `~/.agents/`, `~/.codex/`.
- Release notes entry in CHANGELOG.md follows the v3.0.9 voice — outcome-first, one paragraph.

## Sources & References

- Related code: `scripts/lib/resolve.py:179` (`auto_resolve`), `scripts/lib/pipeline.py:162` (`pipeline.run`), `scripts/lib/planner.py:80` (`plan_query` LAW 7 fallback), `scripts/lib/render.py:333` (comparison scaffold)
- Related PRs: #305 (Step 0.55 category-peer subreddit expansion — the precedent for deterministic peer expansion, merged 2026-04-22)
- Related plan: `docs/plans/2026-04-22-001-fix-category-peer-subreddit-resolution-plan.md`
