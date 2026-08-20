---
title: "Persistent discovery topic queue: five interlocking design conventions"
date: 2026-07-20
category: architecture-patterns
module: discovery-topic-queue
problem_type: architecture_pattern
component: database
severity: high
applies_when:
  - "Adding default-on local persistence (SQLite, JSON state) hooked onto the end of an expensive pipeline run"
  - "Building a fuzzy identity layer over LLM-named entities whose names drift across runs"
  - "Reading a feature toggle in an engine where .env-file values only reach code through env.get_config's keys allowlist"
  - "Recording per-item state in a loop where a later item could fuzzy-match a row written earlier in the same run"
  - "Persisting user-set status (covered, dismissed, read) that must survive entity renames"
tags:
  - "discovery-topic-queue"
  - "fuzzy-matching"
  - "sqlite-persistence"
  - "env-allowlist-opt-out"
  - "two-phase-write"
  - "covered-status-inheritance"
  - "guarded-write-hook"
  - "scoped-db"
  - "llm-naming-drift"
related_components:
  - "skills/last30days/scripts/store.py"
  - "skills/last30days/scripts/last30days.py"
  - "skills/last30days/scripts/lib/env.py"
  - "tests/test_store.py"
  - "tests/test_discover_mode.py"
---


# Persistent discovery topic queue: five interlocking design conventions

## Context

PR #852 shipped a persistent topic queue for `/last30days discover`: every real
discovery run records which topics it surfaced into a `discovery_topics` table in
research.db, so the podcast/X-article pipeline remembers what it has already seen
("surfaced 3rd time") and what the user already produced content for ("marked
covered"). This is the design record for that queue - five conventions that were
each load-bearing in review, two of them caught as real bugs (one P0). The
seed-source corroboration change that landed in the same PR is documented
separately in
`docs/solutions/design-patterns/ranked-output-confidence-floor-honest-empty-state.md`
(section 2b); this doc does not cover it.

## Guidance

### 1. Default-on, disabled only via the config allowlist - never bare os.environ

The queue records every real (non-mock) run by default; the literal value `off`
disables it. The knob is registered in `env.get_config`'s keys allowlist
(`skills/last30days/scripts/lib/env.py:482`):

```python
# Discovery topic queue (podcast/X-article pipeline memory). Default
# ON; the literal value "off" disables queue writes and annotations.
('LAST30DAYS_DISCOVERY_QUEUE', None),
```

and read from the resolved config dict, never `os.environ`
(`skills/last30days/scripts/last30days.py:1312-1314`):

```python
queue_setting = str(config.get("LAST30DAYS_DISCOVERY_QUEUE") or "").strip().lower()
if queue_setting == "off" or not report.topics:
    return report
```

WHY: `.env`-file users' values only reach the engine through the `get_config`
allowlist merge - a bare `os.environ` read silently ignores them, a documented
invisible-failure class in this repo. Scoped runs (`--save-dir`) write the scoped
research.db via `store.scoped_db(_scoped_store_db(args))`
(`last30days.py:432-437`, `store.py:41-53`), never the global one; `--mock` runs
stay 100% side-effect-free (`last30days.py:1505`).

### 2. Annotate-only fuzzy matching - a match stamps context, it never merges rows

`store.match_discovery_topic` tries exact normalized-name match first, then the
best entity-overlap candidate - the better of full `entity_key` token overlap and
anchor-token overlap - at a conservative floor
(`skills/last30days/scripts/store.py:810`, `898-938`):

```python
DISCOVERY_QUEUE_OVERLAP_THRESHOLD = 0.6
...
if best is not None and best_overlap >= DISCOVERY_QUEUE_OVERLAP_THRESHOLD:
    return dict(best)
```

A fuzzy match only annotates the rendered card - the `Pipeline: surfaced Nth
time, marked covered` line (`skills/last30days/scripts/lib/render.py:153-168`) -
and never merges or rewrites queue rows (`store.py:806-809`, `906-907`).

WHY: with annotate-only semantics a false-positive match costs one noisy line on
one card; a false merge would silently collapse two distinct stories into one
row and hide one of them forever. The threshold is tunable precisely because
mislabeling is recoverable and data loss is not.

### 3. Two-phase hook: match ALL topics before recording ANY

`_annotate_and_record_discovery_queue` computes priors for every topic first,
then records surfacings, inside one `store.scoped_db` block
(`skills/last30days/scripts/last30days.py:1323-1345`):

```python
with store.scoped_db(_scoped_store_db(args)):
    store.init_db()
    # Phase 1: match EVERY topic before recording ANY. Interleaving
    # match+record in one loop lets topic N fuzzy-match a same-anchor
    # sibling row this very run recorded seconds earlier, falsely
    # annotating a first-ever topic as "surfaced 2nd time".
    priors = [store.match_discovery_topic(topic.name) for topic in report.topics]
    # Phase 2: record this run's surfacings. ...
    for topic, prior in zip(report.topics, priors):
```

WHY: one report often contains same-anchor siblings ("Gemma 4 chat templates" /
"Gemma 4 tool calling fixes"). Interleaved match+record lets topic N fuzzy-match
the row topic N-1 wrote seconds earlier, falsely annotating a first-ever topic
as a repeat. Caught in review; regression-tested.

### 4. Covered inheritance: fresh rows born covered, existing rows never mutated

`record_discovery_surfacing(inherit_covered_at=...)` makes a fresh row start in
`covered` status when its fuzzy-matched prior is covered; the `ON CONFLICT`
update path deliberately never touches `status`/`covered_at`
(`skills/last30days/scripts/store.py:842-895`):

```python
status = "covered" if inherit_covered_at else "surfaced"
...
ON CONFLICT(normalized_name) DO UPDATE SET
    surface_count = surface_count + 1,
    last_surfaced = excluded.last_surfaced,
    last_run_ref = excluded.last_run_ref,
    domain = CASE WHEN excluded.domain <> '' THEN excluded.domain ELSE domain END
```

The caller passes it when a topic's prior is covered
(`last30days.py:1334-1344`). Locked by the flip-flop regression test
`test_covered_status_survives_judge_rename_across_runs`
(`tests/test_store.py:1082-1101`) and by
`tests/test_store.py:1060-1079` (ON CONFLICT ignores `inherit_covered_at`).

WHY: the LLM judge renames the same story across runs; without inheritance a
rename forks a fresh uncovered row and the user's covered mark silently
evaporates. Without the never-mutate rule, a stale inherit could flip a row the
user just changed.

### 5. Guarded, synchronous end-of-run write - never crash a finished pipeline

The hook call in `_run_discover` is wrapped so a broken queue db degrades to a
stderr warning and an unannotated report
(`skills/last30days/scripts/last30days.py:1505-1515`):

```python
if not args.mock:
    try:
        report = _annotate_and_record_discovery_queue(report, args, config)
    except (sqlite3.Error, OSError) as exc:
        # A broken queue db (locked, read-only dir, corrupt) must never
        # destroy a finished multi-minute pipeline run: warn and render
        # the report without queue annotations (fields keep defaults).
        sys.stderr.write(
            f"[last30days] Warning: discovery queue unavailable ({exc}); "
            "continuing without queue annotations.\n"
        )
```

WHY: unguarded, a locked/read-only/corrupt research.db raises AFTER the
multi-minute research pipeline finished and discards all of its output - the PR
#852 code review's P0, empirically reproduced. The write also runs synchronously
after the pipeline returns (`last30days.py:1308-1310` docstring): it touches
disk, so the abandon-on-timeout daemon-thread pattern is forbidden here (see
`docs/solutions/logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md`).

## Why This Matters

Ranked by blast radius when a convention is violated:

- Unguarded end-of-run write (5): the whole run's output is destroyed by a
  bookkeeping failure, and only in degraded environments (locked db, read-only
  dir), so it ships green and detonates on exactly the machines you cannot see.
  This was the review's P0.
- Interleaved match+record (3): the queue's core promise ("first time you've
  seen this") is wrong on day one - a first-ever topic gets annotated "surfaced
  2nd time" by its same-run sibling, and no cross-run test catches it because
  the corruption happens inside a single run.
- Bare os.environ read (1): `.env`-file users cannot turn the queue off; the
  toggle works in the maintainer's shell and fails invisibly for everyone
  configuring via file.
- Merging on fuzzy match (2): a 0.6-overlap false positive stops being one
  noisy line and becomes a hidden story - unrecoverable data loss from a
  heuristic.
- Mutating rows or skipping inheritance (4): user covered marks flip-flop with
  judge naming drift, so the queue re-pitches stories the user already produced,
  which is the exact failure the queue exists to prevent.

## When to Apply

- Any default-on local persistence bolted onto the end of an expensive pipeline:
  the write must be guarded (degrade to a warning) and synchronous if it touches
  disk.
- Any fuzzy identity layer over LLM-named entities: keep matching annotate-only,
  batch all matches before any writes in a run, and inherit user-set status onto
  fresh rows instead of mutating existing ones.
- Any new engine toggle in this repo: register it in `env.get_config`'s keys
  allowlist and read it from the config dict, never bare `os.environ`.

## Examples

Covered flip-flop, the archetype 3-run scenario (mirrors
`tests/test_store.py:1082-1101`):

1. Run 1 surfaces "Gemma 4 chat templates"; the user records an episode and
   runs `queue cover "Gemma 4 chat templates"` (row status: covered).
2. Run 2's judge names the same story "Gemma 4 template fixes". Exact match
   misses; fuzzy match (anchor overlap `gemma`/`4` at >= 0.6) finds the covered
   prior, so the new row is recorded born covered and the card renders
   `Pipeline: surfaced 2nd time, marked covered` instead of pitching it fresh.
3. Run 3 resurfaces "Gemma 4 template fixes"; it exact-matches its own covered
   row (`covered_at` still the run-1 date). Without convention 4, run 2 would
   have forked an uncovered row and run 3 would re-pitch a story the user
   already covered.

Queue failure behavior: with research.db locked by another process, a discovery
run still prints the full rendered report; stderr shows
`[last30days] Warning: discovery queue unavailable (database is locked);
continuing without queue annotations.` and the cards simply lack Pipeline lines.

## Related

- PR #852 - judged topic names, junk gate, angles, topic queue (this design).
- `docs/solutions/design-patterns/ranked-output-confidence-floor-honest-empty-state.md`
  section 2b - the seed-source corroboration rule from the same PR (not covered
  here).
- `docs/solutions/logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md`
  - why abandon-on-timeout daemon threads are forbidden for disk writers.
