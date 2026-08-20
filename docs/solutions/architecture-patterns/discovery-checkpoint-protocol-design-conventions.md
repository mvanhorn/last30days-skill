---
title: "Checkpointed discovery protocol: five design conventions for host-side LLM judgment"
date: 2026-07-21
category: architecture-patterns
module: discovery-checkpoint-protocol
problem_type: architecture_pattern
component: tooling
severity: high
applies_when:
  - "The product's primary consumer is a frontier reasoning model invoking the tool as an agent skill, not a traditional programmatic API client"
  - "A pipeline stage needs semantic judgment (naming, classification, worthiness scoring) that only an LLM can supply"
  - "Building a keyless or free-tier path where a silent heuristic fallback would degrade output quality without disclosing that an API key was assumed"
  - "A CLI or script needs to persist state across multiple invocations while the host model performs judgment in between (checkpoint-and-resume design)"
  - "An existing skill law or convention already establishes that host-side reasoning replaces engine-side API keys, and a new pipeline stage needs the same treatment"
symptoms:
  - "v3.17.0 silently fell back to deterministic topic naming and junk heuristics for keyless users when the engine's own LLM judge was unavailable"
  - "An API key was the de facto front door to real discovery judgment, contradicting the skill's keyless-path promise and its own LAW 7 host-is-the-reasoning-model precedent"
root_cause: wrong_api
resolution_type: code_fix
tags:
  - "discovery-protocol"
  - "host-judged-protocol"
  - "checkpoint-files"
  - "law-11"
  - "keyless-path"
  - "nominations-bundle"
  - "provenance-enforcement"
  - "bundle-id-ttl"
related_components:
  - "skills/last30days/scripts/lib/discovery_handoff.py"
  - "skills/last30days/scripts/last30days.py"
  - "skills/last30days/SKILL.md"
  - "tests/test_discover_handoff.py"
  - "tests/test_discover_mode.py"
---

# Checkpointed discovery protocol: five design conventions for host-side LLM judgment

## Context

An Agent Skill's primary consumer is a frontier reasoning model: the engine
(`skills/last30days/scripts/last30days.py`) is invoked by Claude Code, Codex,
Gemini, or another agent runtime that read SKILL.md. v3.17.0 (PR #852) forgot
that and shipped judgment as an engine-side LLM pass: `lib/discovery_judge.py`
(since deleted by PR #856; the path is historical) resolved a reasoning
provider across Gemini/OpenAI/xAI/OpenRouter keys and,
per its own contract, never raised - "No provider, a failed call, or a
malformed payload logs a warning and returns None, and the caller falls back"
to deterministic heuristics. Every keyless user silently got the degraded
branch: heuristic topic names like "120k 1,600 ESP32s" and zero content
angles, with no signal that a better path existed.

PR #856 (v3.18.0) deleted the engine judge outright (CHANGELOG.md:18-20) and
replaced it with a three-command host-judged protocol, mandated by SKILL.md
LAW 11 "YOU ARE THE JUDGE" (skills/last30days/SKILL.md:233): the pipeline
pauses at its judgment points and persists versioned checkpoint files that
the hosting model judges between invocations. Leg 1 (`--discover
--nominate-only`) sweeps and writes the nominations bundle; the host writes a
judgments file; leg 2 (`--discover --judgments <file>`) resumes, deep-enriches,
and writes the pending report; the host writes an angles file; leg 3
(`--discover --finalize [--angles <file>]`) renders offline. The contracts
live in `skills/last30days/scripts/lib/discovery_handoff.py` (module
docstring, lines 1-18), the leg handlers in
`skills/last30days/scripts/last30days.py:1716-1986`, and the resumed pipeline
math in `skills/last30days/scripts/lib/pipeline.py:1525-1697`.

This doc records the five conventions that make a checkpoint protocol safe:
identity/TTL binding, the lossless-state-vs-capped-digest split, fail-closed
parsing of empty state, provenance enforcement across invocations, and
guarded writes plus stale-sibling invalidation.

## Guidance

### 1. Checkpoints are identity-bound and time-bound

Every host-authored file must echo the checkpoint's identity. Leg 1 mints a
random `bundle_id` (`discovery_handoff.py:270`), prints it in the digest, and
both host files (judgments, angles) must carry it back.
`_require_bundle_binding` (`discovery_handoff.py:638-673`) enforces the echo
and its error names BOTH ids and the cheap remedy:

```python
raise HandoffContractError(
    f"The {label} file is bound to bundle_id {file_bundle_id!r} but the "
    f"{noun} is {bundle.bundle_id!r}. {location_label}:\n"
    f"{_searched_lines(searched)}\n"
    f"Correct the bundle_id field in your {label} file to "
    f"{bundle.bundle_id!r} and re-run this same leg."
)
```

WHY the remedy split matters: a mismatched echo means the host copied the
wrong id into an otherwise-current file, so the fix is edit-one-field and
retry THIS leg - never the expensive re-sweep (`_RESWEEP_REMEDY`,
`discovery_handoff.py:43`) or resume (`_RESUME_REMEDY`, lines 48-51)
remedies, which belong to missing/stale state. On the finalize leg the
message deliberately names the pending report, not the bundle, so the host's
retry is not misdirected (lines 653-664). `HandoffContractError` maps to
exit 2 in one place (`last30days.py:1984-1986`).

Time binding is a dedicated module constant with a deliberate non-reuse
comment (`discovery_handoff.py:32-36`):

```python
# How long a nominations bundle stays valid. Deliberately a module constant
# and NOT the LAST30DAYS_REPORT_CACHE_TTL_SECONDS env knob: a user who
# lowered the report-cache TTL for drill freshness must not shrink the
# window a host has to author judgments.
DISCOVERY_HANDOFF_TTL_SECONDS = 3600.0
```

Staleness is checked in the shared envelope validator via
`env.is_timestamp_fresh` (`discovery_handoff.py:430-436`, `env.py:121`), and
the pending report gets a FRESH TTL clock stamped at leg-2 write time
(`last30days.py:1854-1856`) because leg 2 started a new authoring window.
WHY: an unrelated cache knob silently shrinking the host's judging window is
exactly the class of cross-feature coupling a checkpoint file must not have.

### 2. One checkpoint, two audiences, hard split: lossless resume state vs capped fenced digest

The nominations bundle serves the engine and the host, and the two halves
have opposite rules.

Engine half: the FULL judge pool with complete seed items, serialized
losslessly (`schema.py:848-852` states the contract;
`schema.nomination_to_dict`, `schema.py:917-932`, round-trips every item).
Leg 2's floor/velocity/entity math must score identically to a
single-process run: `_floor_survivor_records` is "Shared verbatim by
run_discover (one-shot) and run_discover_resume (protocol leg 2) so floor
semantics can never drift between the paths" (`pipeline.py:1199-1215`), and
velocity scores against the bundle's momentum window, never the resume-time
clock (`pipeline.py:1558-1561`). A capped bundle would silently starve
downgraded-topic scoring: host-junk rows, and heuristic-junk fallback rows
below the seed-source floor, never
get an enrichment pass, so their weak-signal velocity and the
seed-source-corroboration floor count are computed purely from bundle seed
items (`pipeline.py:1584-1593`, `rerank.py:110-136`) - truncate the items and
those rows under-count sources and engagement with no error anywhere. Parity
is test-pinned: `tests/test_discover_handoff.py:232`
(`test_parity_floor_and_velocity_inputs_survive_round_trip`) asserts
velocity, engagement totals, source sets, and entity-disambiguation inputs
(title + snippet) recompute identically after the round trip.

Host half: `build_host_digest` (`discovery_handoff.py:915-976`) is capped
(`_DIGEST_TITLE_MAX_CHARS`/`_DIGEST_SNIPPET_MAX_CHARS`/`_DIGEST_COMMENT_MAX_CHARS`,
lines 66-68) and its evidence lines ride inside the untrusted-content fence:

```python
if evidence_lines:
    lines.append("")
    lines.append(rerank._fenced_untrusted_content("\n".join(evidence_lines)))
```

That is the exact fence the rerank judge uses (`rerank.py:305-312`), and the
deleted engine judge fenced the same evidence the same way (v3.17.0's
`discovery_judge.py` imported `_fenced_untrusted_content` for both of its
prompts). Dropping the fencing during the rewrite was a caught regression;
the fence is now pinned by
`tests/test_discover_handoff.py:755`
(`test_digest_fences_untrusted_evidence_like_the_engine_judge`): scraped
titles/snippets/comments inside the fence, structural lines (ids, sources,
signal, bundle path) outside it. Host-supplied text going the other
direction is capped too - names at 96 chars, angles at 200
(`discovery_handoff.py:53-62`), "ported from the retired engine-judge pass"
because names become search queries and angles render verbatim on cards.

### 3. Engine-written checkpoints parse strict-at-top, lenient-per-row, but FAIL CLOSED on structurally empty state

The readers are strict at the top level (readable, JSON object, right kind,
right schema version, bundle_id present, within TTL - all in
`_parse_handoff_envelope`, `discovery_handoff.py:381-437`) and lenient per
row: one corrupt nomination row is warned and skipped, never fatal
(`discovery_handoff.py:469-487`). But leniency has a floor. A non-list
`nominations` value raises (`discovery_handoff.py:460-466`), and zero valid
parsed rows raises too (`discovery_handoff.py:506-514`):

```python
if not nominations:
    # Leg 1 never writes an empty bundle (a zero-nomination sweep
    # short-circuits with no bundle file), so an empty or all-invalid
    # nominations array is corrupt state: fail closed, never hand the
    # resume leg a silently empty pool.
    raise HandoffContractError(...)
```

WHY: without this, a corrupt bundle flows into leg 2 as an empty pool, which
floors to zero survivors and renders an authoritative-looking "Nothing solid
this window" brief - a green result manufactured from broken state. PR
#856's review caught this
empty-pool-renders-authoritative-empty-result failure; it is now pinned by
`tests/test_discover_handoff.py:439` (all rows malformed) and `:457` (empty
list). The invariant that makes fail-closed valid: leg 1 short-circuits a
zero-nomination sweep to the nothing-solid brief and writes NO bundle
(`last30days.py:1748-1750`), so an empty pool on disk is always corruption,
never a legitimate outcome.

### 4. Cross-invocation state carries provenance, and the resume legs enforce it

Three kinds of provenance ride the checkpoint files:

Mock parity. Both checkpoints stamp `mock` at write time
(`discovery_handoff.py:307`, `last30days.py:1861`), and every resume leg
runs `_require_discover_mock_parity` (`last30days.py:1506-1532`): mock-born
state is rejected by a real run and real state by a `--mock` run, in both
cases exit 2 with a fix-the-flag or fresh-sweep remedy. WHY: "mock-born
state finalized by a real run would fake a real brief from fixture data, and
real state finalized by --mock would silently drop the round's queue write."

Sweep coverage. Leg 1 serializes the sweep's per-source outcome map into the
bundle (`discovery_handoff.py:308-311`), leg 2 restores it into its report
(`pipeline.py:1681-1692`), and leg 3 inherits it through the pending report,
so degraded coverage "survives the protocol instead of silently reading as
clean" (`discovery_handoff.py:115-118`). Every leg terminal - one-shot and
all three legs - exits through the ONE shared strict-exit helper,
`_discovery_strict_exit_code` (`last30days.py:1481-1503`, called at
1703, 1750, 1797, 1839, 1895, 1967), which turns `LAST30DAYS_STRICT_EXIT`
plus any non-clean source outcome into exit 3. The PR #856 review validated
this as a P1: before the fix, protocol legs silently exited 0 on degraded
sweeps because the status map was dropped between legs.

Store scoping. An explicit `--save-dir` is the SOLE handoff store.
`_search_paths` (`discovery_handoff.py:211-227`) returns "ONLY the save dir
when one was supplied, else the config dir", mirroring `_scoped_store_db`
(`last30days.py:432-437`): "a handoff file in the config dir must never
silently satisfy a save-dir run." The second validated P1 of the review:
with a fallback chain, a missing pending file in the save dir would let a
bare `--finalize` quietly consume the config-dir store's pending report and
finalize another store's run.

### 5. Guard the write after the expensive work, and invalidate stale siblings on fresh rounds

Both engine checkpoint writes happen after minutes of paid-for work (a sweep;
a deep enrichment pass), so an OSError there is converted to the typed
contract error, never a traceback (`discovery_handoff.py:329-334` for the
bundle; `last30days.py:1869-1877` for the pending report):

```python
except OSError as exc:
    # A locked/read-only/full disk is the protocol's clean exit-2 path,
    # never a traceback.
    raise HandoffContractError(
        f"Could not write nominations bundle {path}: {exc}"
    ) from exc
```

This is the repo's guarded-write convention (same shape as the discovery
queue's guarded end-of-run write) applied to checkpoints, and it is pinned by
`tests/test_discover_handoff.py:472`.

Fresh rounds invalidate stale siblings. A new leg-1 bundle starts a NEW
protocol round, so any pending report left by a prior round is deleted
alongside it (`last30days.py:1783-1788`); a leg 2 that ends nothing-solid
wrote no pending file this round, so it also unlinks any stale one
(`last30days.py:1830-1837`). WHY: without the unlinks, an unbound bare
`--finalize` inside the TTL could re-serve the PREVIOUS round's report as if
it belonged to the current sweep. The deliberate exception proves the rule:
a SUCCESSFUL finalize leaves the pending file in place
(`last30days.py:1905-1911`) so a retry with a corrected angles file keeps
working, and idempotency comes from replaying the leg-2 `run_ref` into the
queue (`last30days.py:1958-1964`) rather than from deleting state.

## Why This Matters

The architecture smell this pattern removes: an external LLM API call inside
an engine whose invoker IS an LLM. That shape fails three ways at once. It
adds cost (a second metered model where a capable one is already in the
loop). It forks quality silently between keyed and keyless users - v3.17.0's
judge never raised on a missing provider, so keyless users got heuristic
names and no angles with zero indication anything was degraded, on the
skill's PRIMARY invocation path. And it produces a strictly worse judge: the
budget-priced engine-side model (flash-lite class, batched, no session
context) judged evidence the frontier host model could have judged directly.

The checkpoint protocol is the general remedy shape: pause the pipeline at
each judgment point, persist versioned, identity-bound, TTL-bound state, let
the host judge between invocations, and validate every resume so stale or
mismatched state becomes a clean exit 2 with a named remedy instead of
silent wrong output. LAW 11's framing (SKILL.md:233) is the contract in one
line: "You do not need an API key ... you ARE the reasoning model." The
one-shot path prints a loud note pointing at the protocol
(`pipeline.py:1421-1433`) precisely so a reasoning-model host can never
mistake heuristic output for a capability ceiling.

The five conventions are what make the pause safe. Splitting one pipeline
into three processes creates every classic distributed-state hazard in
miniature - stale state, cross-round state, cross-store state, fixture/real
crosses, silently-empty state, lost coverage warnings - and each convention
above closes one of them.

## When to Apply

Apply this pattern when:

- A CLI or engine embedded in an Agent Skill needs semantic judgment
  (naming, junk filtering, scoring, prose authoring) in the middle of an
  otherwise deterministic pipeline - the host model is the judge; checkpoint
  around the judgment points.
- You are about to add an LLM provider key, client, or "reasoning provider"
  resolution to an engine whose invoker is already a reasoning model - that
  is the smell; reach for the protocol instead.
- An existing engine-side LLM pass has a "silent heuristic fallback" - the
  keyless majority is getting invisible degraded output today.

Do NOT apply it when:

- No reasoning model is in the loop. The one-shot cron/scripted path keeps
  the single-process pipeline deliberately (`run_discover`,
  `pipeline.py:1384`, and the degradation rule in SKILL.md:398): a
  checkpoint pause with nobody to judge is just a hang.
- The judgment is expressible as a deterministic rule - the junk-shape
  heuristics and the confidence floor stayed engine-side because they need
  no model at all.

## Examples

The three-command sequence as SKILL.md ships it (skills/last30days/SKILL.md:318-399),
one identical `--save-dir` threaded through all three legs:

```bash
# Leg 1 - sweep and nominate (global trending; domain runs pass the domain
# phrase as the --discover argument on this leg only):
python3 scripts/last30days.py --discover --nominate-only \
  --save-dir="$HOME/Documents/Last30Days"
# stdout: judging digest + bundle path + bundle_id. Host reads the bundle
# file, then writes judgments.json:
#   {"bundle_id": "<echoed>", "judgments": [
#     {"id": "n1", "name": "Gemma 4 chat templates", "junk": false, "worthiness": 85},
#     {"id": "n2", "name": "Beginner asks how to deploy", "junk": true, "worthiness": 10}]}

# Leg 2 - resume with judgments; deep per-topic research (several minutes):
python3 scripts/last30days.py --discover --judgments judgments.json \
  --save-dir="$HOME/Documents/Last30Days"
# stdout ends with angle inputs keyed by surviving id. Host writes
# angles.json: {"bundle_id": "<same>", "angles": [
#   {"id": "n1", "podcast": "<hook>", "x_article": "<hook>"}]}

# Leg 3 - finalize offline: apply angles, render, record the topic queue.
python3 scripts/last30days.py --discover --finalize --angles angles.json \
  --emit=compact --save-dir="$HOME/Documents/Last30Days"
```

Failure-mode walkthrough (mismatched then stale checkpoint):

1. The host echoes a bundle_id from an earlier round into judgments.json and
   runs leg 2. `_require_bundle_binding` raises; the CLI prints
   `[last30days] The judgments file is bound to bundle_id 'aaaa...' but the
   current nominations bundle is 'bbbb...'` plus the searched location, and
   exits 2. Remedy as printed: correct the `bundle_id` field and re-run leg 2.
   The expensive sweep is NOT redone - the bundle on disk is still current.
2. The host instead waits 90 minutes before judging. The envelope check
   (`discovery_handoff.py:430-436`) finds `generated_at` outside the 3600s
   TTL and exits 2: the bundle "is stale ... the momentum window it captured
   has moved on. Run a fresh `--discover --nominate-only` re-sweep." Here the
   expensive leg IS the remedy, because the state itself expired - the
   protocol never asks for the expensive path when a cheap edit fixes the
   problem, and never accepts cheap edits when the data has aged out.

The deterministic end-to-end twin of the whole sequence is pinned in CI:
`tests/test_discover_mode.py:2439`
(`test_discovery_cli_full_mock_protocol_three_legs_end_to_end`).

## Related

- `docs/solutions/architecture-patterns/discovery-topic-queue-design-conventions.md` -
  same feature family, the queue side: the persistent topic queue leg 3
  writes into (idempotently, under the leg-2 `run_ref`), including the
  guarded-write convention this protocol reuses.
- `docs/solutions/design-patterns/ranked-output-confidence-floor-honest-empty-state.md` -
  the confidence-floor semantics the protocol preserves verbatim across the
  process split (`_floor_survivor_records` shared by both paths), including
  seed-source corroboration for junk shapes.
- `docs/solutions/logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md` -
  the enrichment wall-clock budget pattern leg 2's deep tier extends
  (`RESUME_DEEP_ENRICH_BUDGET_SECONDS` 450s via
  `LAST30DAYS_ENRICH_BUDGET_SECONDS`, `pipeline.py:1492-1508`; workers stay
  daemon threads and never touch disk - the pending report is ONE post-loop
  write from the main thread, `last30days.py:1867-1871`).
- PR #856 (protocol, engine-judge removal), PR #852 (the v3.17.0 engine
  judge this replaced), CHANGELOG.md v3.18.0 / v3.17.0 entries.
- SKILL.md LAW 11 and the Step 1 DISCOVERY branch (skills/last30days/SKILL.md:233, 314-399).
