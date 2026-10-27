---
title: "Parallel comparison sub-runs each took a full YouTube budget on one shared yt-dlp gate, so every lane timed out"
date: 2026-10-25
category: logic-errors
module: lib/youtube_yt
problem_type: logic_error
component: background_job
symptoms:
  - "Every entity in an \"X vs Y vs Z\" comparison reported `youtube timed out after 180s`"
  - "yt-dlp was current and fetched transcripts fine when called directly"
  - "Single-topic runs of the same entities returned YouTube results"
root_cause: concurrency
resolution_type: code_fix
severity: medium
tags:
  - yt-dlp
  - comparison-mode
  - competitor-fanout
  - semaphore
  - concurrency
  - timeout
  - budget
related_components:
  - testing_framework
---

# Parallel comparison sub-runs each took a full YouTube budget on one shared yt-dlp gate, so every lane timed out

## Problem

Comparison mode runs one `pipeline.run()` per entity in parallel. Each entity's YouTube lane asked for the same yt-dlp work a single-topic run does, and all of that work waits on one process-wide two-slot gate. With three entities the queued work outlasted the fixed lane wait, so YouTube came back empty for every entity (#1335).

## Symptoms

- Each entity's report carried `youtube timed out after 180s`, the detail `pipeline.py` writes when a stream future is still running at the wait (`skills/last30days/scripts/lib/pipeline.py:2754`).
- yt-dlp itself was healthy. #1327 had already stopped the footer from telling users to update it.
- Single-topic runs of the same topics returned videos.

## What Didn't Work

- **Raising the lane wait.** `STREAM_FUTURE_TIMEOUT_SECONDS = 180.0` (`skills/last30days/scripts/lib/pipeline.py:149`) looks like the knob, but the research runbook runs the engine in the foreground with a 300-second Bash timeout (`skills/last30days/references/research-runbook.md:566`, `:636`). A longer YouTube wait gets the whole run killed instead of one lane.
- **Raising the concurrency cap.** `_YTDLP_MAX_CONCURRENT = 2` (`skills/last30days/scripts/lib/youtube_yt.py:82`) was added in #901 to stop comparison fan-out from hammering YouTube. Widening it brings back the rate limits it was added to prevent.
- **Blaming unfair semaphore ordering.** Even perfect scheduling cannot fit the work. At default depth one entity queues 2 searches, up to 6 transcript attempts (target 2, three attempts per target), and 3 comment fetches. In a one-off simulation with 30s searches and 8s transcripts and comments, three entities came to about 396 slot-seconds over 2 slots, roughly 198s against a 180s wait.

## Solution

Split one single-topic budget across the entities instead of giving each entity a full one:

- The CLI records the entity count on the shared config before fan-out: `config["_comparison_entity_count"] = 1 + len(discovered)` (`skills/last30days/scripts/last30days.py:4440`). The main runner and each peer's `dict(config)` copy both see it, because they read the config at call time.
- `pipeline._comparison_entity_count(config)` (`skills/last30days/scripts/lib/pipeline.py:5508`) reads it, defaulting to 1.
- `youtube_yt.comparison_share(total, entity_count)` (`skills/last30days/scripts/lib/youtube_yt.py:520`) returns `ceil(total / n)`, at least 1, and leaves 0 and single-topic totals unchanged. It scales the search-query cap, the transcript target in retrieval, the post-ranking backfill, and the ScrapeCreators thin-result fallback, plus the number of comment-enriched videos.
- The YouTube fetch memo key includes the count, so a comparison sub-run never reuses a full-budget result.

For three entities at default depth this gives 1 search, a transcript target of 1 (3 attempts), and 1 comment video per entity. In this session's one-off simulation (not committed), every lane then finished in 89 to 97 seconds. Single-topic runs still do 2/6/3.

## Why This Works

The constraint is the total slot time all entities queue on the shared gate, not any one entity's work. The lane wait and the gate are both fixed by things outside the YouTube code (the host's foreground timeout and YouTube's rate limits), so the only free variable is how much work each sub-run asks for. Dividing the single-topic budget keeps the comparison's total close to one single-topic run, so it fits where a single-topic run already fits.

## Prevention

- Any per-run budget for work behind a process-wide limit, such as the yt-dlp gate or a paid-call cap, must be scaled by `_comparison_entity_count`. Otherwise parallel comparison sub-runs multiply it. A new yt-dlp-backed enrichment should take an `entity_count` and pass its budget through `comparison_share`.
- Assert call counts at the lane boundary for both cases. `tests/test_youtube_comparison_budget.py` pins 2/6/3 searches, transcripts, and comments for a single topic and 1/3/1 for three entities, and checks that every comparison sub-run's config carries the count.
- Known limits: at the comparison ceiling every entity still keeps the 1/1/1 floor. Discover enrichment sub-runs share the gate without this split. If the first two searches each run to the 120-second yt-dlp search timeout (`_SEARCH_TIMEOUT`, `skills/last30days/scripts/lib/youtube_yt.py:79`), the third entity can still miss the wait.

## Related Issues

- #1335 (the issue; fix on branch `fix/comparison-youtube-budget`, unmerged as of this writing), #901 (the yt-dlp concurrency cap), #1327 (footer copy for this timeout)
- `docs/solutions/logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md`, another wall-clock budget over parallel sub-runs (discover enrichment) with a different root cause
