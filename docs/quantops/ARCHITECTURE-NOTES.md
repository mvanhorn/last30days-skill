# QuantOps UAE/Arabic Architecture Notes

Phase 0 recon for the QuantOps UAE/Arabic extension. This document describes the current `last30days` runtime contract and the safest additive hook points for the planned `gintent`, `reviews`, and `adlibrary` signal layer.

## Runtime Contract

`skills/last30days/SKILL.md` is the source of truth for how the skill is invoked and how the host model must synthesize output. The engine is not the final user-facing writer by itself: the host model must run the engine, consume the evidence envelope/footer, and write the answer using the contract in `SKILL.md`.

Important runtime rules from the skill file:

- For named entities and local businesses, the host model must do pre-research and pass structured hints with `--plan` or `--auto-resolve` instead of calling the engine bare.
- Engine output must be treated as evidence for synthesis. The host model should not paste a raw evidence dump or add a trailing `Sources:` block.
- The first user-visible line must include the `last30days` badge with the engine version and synced date.
- Output has to preserve inline citations/links and footer details from the engine where relevant.
- Degraded or fallback runs must be disclosed, especially when pre-research was skipped or the planner fell back.

The QuantOps layer should therefore add evidence and metadata, not bypass the skill contract or create a parallel report format.

## Source Registration

Current source availability is centralized in `skills/last30days/scripts/lib/pipeline.py`:

- `available_sources(config, requested_sources)` decides which source names can run.
- `SEARCH_ALIAS` maps user-facing aliases to canonical names.
- `MOCK_AVAILABLE_SOURCES` defines sources available in mock/test mode.
- `_retrieve_stream(...)` dispatches each source name to the implementation module.
- `_normalize_score_dedupe(...)` calls `normalize.normalize_source_items(...)`, `signals.annotate_stream(...)`, `signals.prune_low_relevance(...)`, and dedupe/snippet extraction.

Current normalization is centralized in `skills/last30days/scripts/lib/normalize.py`. `normalize_source_items(...)` maps canonical source strings to per-source normalizers and raises `ValueError` for unknown sources.

Worked example: Pinterest is a narrow opt-in source.

- `pipeline.SEARCH_ALIAS` includes a short alias only when needed.
- `pipeline.available_sources(...)` adds `pinterest` only when explicitly requested and `env.is_pinterest_available(config)` is true.
- `pipeline._retrieve_stream(...)` calls `pinterest.search_pinterest(...)` and `pinterest.parse_pinterest_response(...)`.
- `normalize.normalize_source_items(...)` registers `"pinterest": _normalize_pinterest`.
- `render.py`, `signals.py`, and tests know how to label and score the source.

QuantOps should follow this shape, but keep new source implementations under a new namespace such as `skills/last30days/scripts/lib/quantops/`. `pipeline.py` should import a small namespace facade instead of scattering business logic across the core pipeline.

## Default-Off Gates

The task requires the new sources to be default-off. The existing opt-in pattern is `INCLUDE_SOURCES`, parsed from config in `env.get_config()` and read in `pipeline.available_sources(...)`.

Recommended gates:

- `gintent`: available only when `gintent` is in `INCLUDE_SOURCES`.
- `reviews`: available only when `reviews` is in `INCLUDE_SOURCES`.
- `adlibrary`: available only when `adlibrary` is in `INCLUDE_SOURCES`.
- Arabic expansion/language tagging: enabled only when `QUANTOPS_ARABIC=1`.

This means a normal existing run must produce the same available source set unless the user explicitly opts in.

`CONFIGURATION.md` and the example env file should be updated in the implementation phase to document the flags and keep the no-secrets rule clear.

## v3 Pre-Research And Arabic Hook Point

Named-entity resolution currently flows through:

- The host model's `SKILL.md`-required pre-research and `--plan` handoff.
- CLI `--auto-resolve`, implemented in `skills/last30days/scripts/lib/resolve.py`.
- `resolve.auto_resolve(...)`, which searches for subreddits, X handles, GitHub profiles/repos, and current context when a web backend is available.
- Planner context injection via `pipeline.run(...)`, where `_auto_resolve_context` is passed into `planner.plan_query(...)`.

The safest Arabic hook point is before planning/retrieval, not after ranking:

- Add a QuantOps query-expansion helper that returns Arabic/English variants and language tags only when `QUANTOPS_ARABIC=1`.
- Thread those variants into source-specific query builders or supplemental subqueries without changing default planner behavior.
- Store language metadata on `SourceItem.metadata`, for example `{"language": "ar", "query_variant": "...", "quantops": {...}}`.

This keeps Arabic behavior additive and reversible. It also avoids weakening the current `SKILL.md` requirement that named UAE businesses receive pre-research before the engine run.

## Scoring And Second Signal Class

The current scoring stack is social-evidence oriented:

- `schema.SourceItem` carries raw `engagement`, `engagement_score`, `source_quality`, `local_relevance`, `freshness`, and `local_rank_score`.
- `signals.engagement_raw(...)` computes per-source engagement, then `signals.normalize(...)` maps it onto `0..100`.
- `signals.annotate_stream(...)` combines relevance, freshness, and engagement into `local_rank_score`.
- `fusion.weighted_rrf(...)`, `rerank.rerank_candidates(...)`, and `rerank.score_fun(...)` produce global ranking and final candidate scores.

A source can technically have no engagement score: generic engagement returns `None` when no engagement data exists, and `annotate_stream(...)` uses `(eng_score or 0)` in local ranking. However, sources listed in `_SOCIAL_SOURCES` face stricter pruning when engagement is missing or zero. QuantOps sources should not be added to `_SOCIAL_SOURCES` unless they truly behave like social feeds.

For QuantOps, `gintent`, `reviews`, and `adlibrary` are better modeled as a clean second signal class, for example `commercial_signal`, stored in metadata and optionally surfaced through a later `--signal-confidence` flag. That signal should not pretend to be social engagement. It can include fields like:

- `signal_type`: `intent`, `review`, or `ad`
- `confidence`: numeric confidence derived from count, freshness, source coverage, and match quality
- `market`: `UAE`, city, or emirate when known
- `language`: `ar`, `en`, or unknown
- `evidence_count`: count of matching records or observations

Phase 4 can promote this metadata into report-level confidence without changing the existing social engagement semantics.

## Warnings And Degraded Runs

Warnings and degraded-state messages currently land in three places:

- `pipeline._warnings(...)` attaches report warnings like thin evidence, source concentration, failed sources, or no usable items.
- `render.collect_html_warnings(...)` and related helpers keep HTML data-quality warnings on stderr rather than embedding them in the artifact.
- `last30days.py` computes `quality_nudge.compute_quality_score(...)` after the run and writes `quality["nudge_text"]` to stderr.

Additional diagnostics already print to stderr in source-specific and planner paths, for example `[Planner] ...`, `[Resolve] ...`, `[Pipeline] Phase 2 ...`, and source fallback failures.

QuantOps should use the same pattern:

- Source fetch failures should populate `bundle.errors_by_source[source]` so `pipeline._warnings(...)` can surface "Some sources failed".
- Thin or fixture-only commercial evidence should become report warnings or artifact metadata, not hidden stdout prose.
- Manual `adlibrary` fixture imports must be labeled as fixture/manual data in metadata and warnings when surfaced.

## Proposed Phase 1-4 Plan

Phase 1 - Arabic expansion and tagging, default-off:

- Add `skills/last30days/scripts/lib/quantops/__init__.py`.
- Add `skills/last30days/scripts/lib/quantops/arabic.py`.
- Touch `skills/last30days/scripts/lib/env.py` to load `QUANTOPS_ARABIC`.
- Touch `skills/last30days/scripts/lib/pipeline.py` only where query variants or metadata need to be threaded.
- Add tests such as `tests/test_quantops_arabic.py`.
- Add small fixtures under `tests/fixtures/quantops/`.

Phase 2 - `gintent` and `reviews` sources, default-off:

- Add `skills/last30days/scripts/lib/quantops/gintent.py`.
- Add `skills/last30days/scripts/lib/quantops/reviews.py`.
- Touch `skills/last30days/scripts/lib/pipeline.py` for source availability and `_retrieve_stream` dispatch.
- Touch `skills/last30days/scripts/lib/normalize.py` for `gintent` and `reviews` normalizers.
- Touch `skills/last30days/scripts/lib/signals.py` to set source quality and avoid social engagement pruning.
- Touch `skills/last30days/scripts/lib/render.py` only for labels/footer display if needed.
- Add tests such as `tests/test_quantops_gintent.py`, `tests/test_quantops_reviews.py`, and pipeline availability tests.

Phase 3 - `adlibrary` source and manual fixture import, default-off:

- Add `skills/last30days/scripts/lib/quantops/adlibrary.py`.
- Add fixture parser/import helpers that reject unlabeled fake data and mark manual data clearly.
- Touch `pipeline.py`, `normalize.py`, `signals.py`, and `render.py` as in Phase 2.
- Add tests such as `tests/test_quantops_adlibrary.py` plus fixture-validation tests.

Phase 4 - Surface signal confidence:

- Add a CLI flag such as `--signal-confidence` in `skills/last30days/scripts/last30days.py`.
- Add report artifact or candidate metadata aggregation for QuantOps confidence.
- Touch `render.py` to include confidence only when the flag is enabled.
- Add tests for hidden-by-default behavior and enabled confidence output.

Documentation/config updates during implementation:

- Touch `CONFIGURATION.md` to document `INCLUDE_SOURCES=gintent,reviews,adlibrary` and `QUANTOPS_ARABIC=1`.
- Touch the repository env example file if present in the upstream branch; if absent, add the documented variables to the existing configuration reference instead of inventing secrets.
- Preserve MIT attribution and keep all new modules additive.
