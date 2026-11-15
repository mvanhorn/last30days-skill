---
title: Fix the v4.0.1 Grok Bot run findings (#1449)
date: 2026-11-15
type: fix
artifact_contract: ce-unified-plan/v1
execution: code
product_contract_source: ce-plan-bootstrap
origin: https://github.com/mvanhorn/last30days-skill/issues/1449
---

# Fix the v4.0.1 Grok Bot run findings (#1449)

## Goal Capsule

- Objective: a `/last30days` company report shows honest odds, distinct hiring evidence, the month's range of stories, the right top voices, and accurate source-health advice, on every host including Grok Bot.
- Authority: the user asked for the issue to be fixed and shipped as PRs, each at Greptile 5/5 before merge.
- Stop conditions: a unit whose fix changes a documented product contract beyond this plan; a unit that cannot pass the full suite.
- Finisher: this session; each PR group lands independently, then a patch release.

---

## Product Contract

### Summary

Issue #1449 lists eight findings from a v4.0.1 Grok Bot `/last30days Microsoft` run. Research traced each to code (dossiers in session scratch). Seven become fixes in this repo; one (Techmeme pagination past one page) needs a helper change in printing-press-library and is deferred.

### Problem Frame

A reader of a company report trusts four things this run got wrong: Polymarket showed three mutually exclusive price ranges each at about 43%, because empty order books report a meaningless midpoint. Hiring Signals showed the same three roles under every theme, because themes match company boilerplate in job bodies. The chat output's 8 clusters were all one outage story, because the outage split into 6 clusters and nothing diversifies the top 8. The footer's top X voice was a headline reposter, because voices rank by post count. Two source-health issues add noise: YouTube is marked degraded and told to upgrade yt-dlp when a bot check, not a stale binary, cost transcripts, and the host plan's source list is widened with lanes that cannot run (Trustpilot). Techmeme returns only its 10 newest archive-wide hits per query, so most fall outside the window.

### Requirements

Market odds
- R1. A Polymarket outcome price comes from the midpoint only when the book has both a bid and an ask within a $0.10 spread; otherwise the last trade; otherwise the outcome is dropped. Missing book fields keep today's behavior.
- R2. A mutually exclusive (negRisk) range set whose remaining prices still sum well over 1 shows no odds line rather than an impossible distribution: suppress when the kept Yes prices of a negRisk set sum to more than 1.10.

Hiring evidence
- R3. Themes match on title and department, and on body text only after sentences shared by three or more roles are removed.
- R4. Each theme's examples are ranked by match strength, and a role shown under one theme is not repeated as an example under another. Role counts are unchanged by the dedup.

Story range
- R5. The chat output's top clusters are diversified: near-duplicate clusters are penalized, one story takes at most 3 of the 8 slots, and each non-primary subquery that clears the relevance floor gets at least one slot. With no near-duplicates and every floor-clearing non-primary subquery already in the top 8, the order is unchanged.
- R6. The reorder happens on `report.clusters` and sinks every cluster below the relevance floor after all solid clusters, so chat numbering, the saved file, and `--drill N` agree.

Voices
- R7. The footer and Stats top voices rank @handles from every handle source they count today by engagement (sum of log1p of likes, reposts, replies), count at most 3 posts per handle, and drop handles below a minimum engagement, always keeping the subject's own handle. The footer's subreddit part and Stats' non-handle entries keep current behavior.

Source health
- R8. YouTube transcript health is measured against the transcript target, not every surviving video. A bot-check loss gets a cause-specific message (bot-checked, ScrapeCreators rescued N, upgrading yt-dlp will not help) naming `LAST30DAYS_YOUTUBE_SSH_HOST` or the ScrapeCreators key; the stale-yt-dlp advice stays for failures with no bot check.
- R9. The host-plan backfill never adds a lane that cannot run this session (Trustpilot without a browser session); the runbook and CONFIGURATION.md describe backfill honestly instead of saying the plan is never expanded.
- R10. Techmeme queries carry a `date:[M/D/YY TO M/D/YY]` (no space after the colon, the live-verified form) clause for the run window, so the 10 results are in-window; the client-side window stays as a backstop.
- R11. `MIN_HELPER_VERSIONS` gains a Techmeme minimum (2026.9.1, the dates fix), so setup upgrades a helper that emits no dates and doctor's detail line notes it is older than the minimum; a DEGRADED doctor status is deferred.

### Scope Boundaries

- Item 8 of the issue (does setup upgrade helpers) is answered: it does, via the catalog's source commit, for helpers with a minimum version. R11 closes the Techmeme gap.

### Deferred to Follow-Up Work

- Techmeme pagination beyond one page needs a `--start`/`--pages` flag on the helper's `search` in printing-press-library; the engine then falls back to one page on older helpers.
- Re-checking helper versions for users past first-run setup (`SETUP_COMPLETE`), and doctor's `_cli_gated_record` version checks.
- A validated plan field (such as `omit_sources`) to mark a host's omission as deliberate.
- Story clustering's merge rules (first-item comparison, same-source merge ban); R5 handles their symptom in selection.

### Assumptions

- The user was away, so the scoping confirmation was skipped. Inferred bets: R9 keeps the #1323 backfill for runnable lanes rather than honoring every omission (the issue asked for the latter; #1323 added backfill deliberately because hosts forget lanes). R5's slot rules (3 per story, one per subquery) are judgment values.
- Saved claims against a Polymarket market that R1 now drops come back unsupported with "Re-check failed: Polymarket datum ... was not found"; that is acceptable and tested in U1.

---

## Planning Contract

- KTD1. One PR per finding group, so each Greptile review stays focused and a contested change does not hold the others. Groups touch disjoint functions; PR3 and PR5 both edit `pipeline.py` (different functions), so the later one rebases; `changelog.d` fragments are per-PR files.
- KTD2. Polymarket quote quality is fixed in the parser, so the report, footer, and `refetch_datum` claim verification share it.
- KTD3. Cluster diversity reorders `report.clusters` after clustering in the pipeline, not in render, to keep `--drill` indexes aligned (R6).
- KTD4. Hiring boilerplate stripping reuses `jobs_digest`'s shared-sentence approach (`BOILERPLATE_MIN_ROLES`), moving the helper so `hiring_signals` can import it without a cycle.

---

## Implementation Units

### PR1: Polymarket quote quality

Waits on: nothing.

### U1. Book-aware outcome prices and impossible-set suppression

- Goal: R1, R2 (KTD2).
- Files: `skills/last30days/scripts/lib/polymarket.py` (`_parse_outcome_prices`, the multi-market path near the `liquidity > 0` filter), `tests/test_polymarket*.py`, a recorded fixture of the live MSFT weekly-close event.
- Test scenarios: tight book uses midpoint; empty bid with wide ask uses last trade; no trade drops the outcome; missing book fields keep current price; the MSFT fixture shows no impossible distribution; a non-negRisk multi-market event is not subjected to the R2 sum check; a saved claim against the dropped `<$480` market returns the unsupported "was not found" verdict.

### PR2: Hiring Signals evidence

Waits on: nothing.

### U2. Boilerplate-free theme matching with distinct examples

- Goal: R3, R4 (KTD4).
- Files: `skills/last30days/scripts/lib/hiring_signals.py`, `skills/last30days/scripts/lib/jobs_digest.py`, `tests/test_hiring_signals.py`, `tests/test_render_hiring_signals.py`.
- Test scenarios: a role whose only match is shared boilerplate is unthemed; a title match outranks a body match; the same role never appears as an example under two themes; counts unchanged by dedup; generic keywords no longer theme a Logistics Technician; a mega-cap standard run still passes `_apply_hiring_signal_gate` when it has real theme concentration (gate-level test).

### PR3: Story-diverse top clusters

Waits on: nothing.

### U3. Diversity reorder of report clusters

- Goal: R5, R6 (KTD3).
- Files: `skills/last30days/scripts/lib/pipeline.py` (after clustering), a small selection helper module, `tests/` ranking and render tests.
- Test scenarios: six near-identical outage clusters yield at most 3 of the top 8; an earnings subquery cluster above the floor gets a slot; no-duplicate input with every subquery already represented keeps order exactly; a below-floor cluster inside the top 8 sinks, and `--drill N` still returns the cluster shown as N.

### PR4: Top voices by engagement

Waits on: nothing.

### U4. Engagement-ranked voices

- Goal: R7.
- Files: `skills/last30days/scripts/lib/render.py` (footer top voices, `_top_voices_overall`), `tests/test_footer_on_topic_totals.py`, `tests/test_render_v3.py`, `tests/test_jobs_fusion_render.py`.
- Test scenarios: 10 zero-like posts lose to 3 high-engagement posts; per-handle cap of 3; subject handle kept below the floor; names without @ are not ranked by the handle rule.

### PR5: Source health and plan backfill

Waits on: nothing.

### U5. YouTube bot-check aware health

- Goal: R8.
- Files: `skills/last30days/scripts/lib/youtube_yt.py`, `skills/last30days/scripts/last30days.py` (stats pass-through), `skills/last30days/scripts/lib/quality_nudge.py`, their tests.
- Test scenarios: 4 of 6 with target 2 is not degraded; bot check with rescues gives the cause-specific message without upgrade advice; stale-yt-dlp message unchanged when no bot check.

### U6. Backfill only runnable lanes

- Goal: R9.
- Files: `skills/last30days/scripts/lib/pipeline.py` (`_ensure_unplanned_lanes_in_host_plan`), `skills/last30days/references/research-runbook.md`, `CONFIGURATION.md`, `tests/test_external_plan_source_contract.py`.
- Test scenarios: Trustpilot not backfilled when it cannot run; still backfilled when it can; arXiv backfill unchanged.

### U7. Techmeme window and helper minimum

- Goal: R10, R11.
- Files: `skills/last30days/scripts/lib/techmeme.py` (`_build_search_args`), `skills/last30days/scripts/lib/health.py` (`MIN_HELPER_VERSIONS`), `tests/test_techmeme_source.py`, setup and health tests.
- Test scenarios: search args carry the run window's date clause; client-side window still drops out-of-window items; a 2026.6.2 Techmeme helper is flagged below minimum and 2026.9.1 is not.

---

## Verification Contract

| Check | Command | Units |
| --- | --- | --- |
| Full suite | `uv run pytest` | all |
| Live Microsoft run | engine run on `Microsoft` with the engine planner | U1 to U4 |
| YouTube health | stubbed bot-check tests (a residential network may not reproduce the bot check) | U5 |

## Definition of Done

- PR1 to PR5 merged at Greptile 5/5 with green CI, each with a `changelog.d` fragment.
- A live Microsoft run shows sane Polymarket odds, distinct hiring examples, more than one story in the top 8, and an engaged top X voice.
- Issue #1449 commented with what landed and the deferred follow-ups.
