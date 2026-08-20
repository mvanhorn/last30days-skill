---
title: "Wall-clock budget over ThreadPoolExecutor is illusory - non-daemon workers are joined at interpreter shutdown"
category: logic-errors
module: discover-enrichment
date: 2026-07-12
problem_type: logic_error
component: background_job
severity: high
symptoms:
  - "CLI process stayed alive after the enrichment budget expired, even though the hung topic had already been dropped"
  - "as_completed(futures, timeout=budget) returned control on time, but one still-running sub-run kept the interpreter from exiting"
  - "shutdown(wait=False, cancel_futures=True) cancelled unstarted futures but could not stop an already-running worker thread"
root_cause: thread_violation
resolution_type: code_fix
tags:
  - threadpoolexecutor
  - daemon-threads
  - wall-clock-budget
  - interpreter-shutdown
  - semaphore
  - concurrency
  - timeout
  - python
related_components:
  - testing_framework
---

# Wall-clock budget over ThreadPoolExecutor is illusory - non-daemon workers are joined at interpreter shutdown

## Problem

Discovery enrichment fans out one research sub-run per nominated topic under a wall-clock batch budget (`ENRICH_BUDGET_SECONDS`, `skills/last30days/scripts/lib/pipeline.py`), but the first implementation built the budget on `ThreadPoolExecutor` - whose worker threads are non-daemon and joined at interpreter shutdown - so the budget bounded the answer without bounding the process. One hung sub-run could keep the whole CLI alive indefinitely after its topic had already been downgraded to nomination-only.

## Symptoms

- The CLI process stays alive past the enrichment budget - potentially indefinitely - whenever one sub-run hangs (e.g. a network fetch that stalls without tripping a timeout). The batch "finishes", results are assembled, but the process will not exit because Python joins the executor's non-daemon threads at interpreter shutdown.
- Only visible under a genuinely hung worker. All unit tests passed: the slow-topic test observed exactly what `as_completed(timeout=...)` guarantees - the fast topic returned, the slow one was dropped from results - and the test process exited fine because the "slow" worker was merely sleeping a few seconds, not hung. The defect lives entirely in process-lifetime behavior that a result-oriented test never touches.
- Caught by code review on PR #816 (Greptile P1 "Enrichment Budget Keeps Running") before release; shipped fixed in v3.14.0.

## What Didn't Work

The first version of `enrich_nominations()`:

```python
executor = ThreadPoolExecutor(max_workers=max_workers)
futures = {executor.submit(_run_one, n): n for n in nominations}
try:
    for future in as_completed(futures, timeout=budget_seconds):
        ...collect result...
finally:
    executor.shutdown(wait=False, cancel_futures=True)
```

This looks like budget enforcement, and every knob in it does something real - just not the thing needed. Precisely why each one fails to stop a thread that is already running:

- `as_completed(futures, timeout=budget)` bounds only the consumer. When the timeout expires it raises `TimeoutError` in the collecting loop; it sends nothing to the workers. Python threads cannot be killed from outside, so a running sub-run keeps running.
- `cancel_futures=True` cancels only futures still sitting in the executor's queue - futures whose callable has not started. A future in the RUNNING state is not cancellable (`Future.cancel()` returns False for it); the worker executing it is untouched.
- `shutdown(wait=False)` merely skips joining the threads at shutdown-call time. It does not detach them. CPython's `ThreadPoolExecutor` creates its worker threads non-daemon and (since Python 3.9) registers a `threading._register_atexit` hook that joins every worker at interpreter shutdown. So even after `shutdown(wait=False)`, interpreter exit blocks until the hung worker returns - which for a stalled fetch may be never.

Net effect: the budget expired, the slow topic was correctly reported as nomination-only, and the process then sat there anyway, held open by the very thread the budget had supposedly abandoned.

## Solution

Replace the executor with plain daemon threads, a semaphore for the concurrency cap, a queue for results, and a monotonic deadline in the consumer. From `skills/last30days/scripts/lib/pipeline.py`, `enrich_nominations()`:

```python
# Daemon threads + a semaphore instead of ThreadPoolExecutor: executor
# threads are non-daemon and joined at interpreter shutdown, so one hung
# sub-run could keep the whole process alive long after its topic was
# downgraded to nomination-only. Daemon workers make the wall-clock budget
# real - stragglers cannot delay process exit. Abandonment is safe because
# internal_subrun passes write nothing to disk (no save, no library sync,
# no store), and every fetch layer inside run() carries its own timeout.
enriched: dict[str, EnrichedTopic] = {}
results_queue: queue.Queue[tuple[Nomination, schema.Report | None, Exception | None]] = queue.Queue()
slots = threading.Semaphore(max(1, max_workers))

def _worker(nomination: Nomination) -> None:
    with slots:
        try:
            results_queue.put((nomination, _run_one(nomination), None))
        except Exception as exc:  # noqa: BLE001 - containment is the contract
            results_queue.put((nomination, None, exc))

for nomination in nominations:
    threading.Thread(
        target=_worker,
        args=(nomination,),
        name=f"discover-enrich-{nomination.name[:32]}",
        daemon=True,
    ).start()

deadline = time.monotonic() + max(1.0, budget_seconds)
pending = len(nominations)
while pending and (remaining := deadline - time.monotonic()) > 0:
    try:
        nomination, report, exc = results_queue.get(timeout=min(remaining, 0.5))
    except queue.Empty:
        continue
    pending -= 1
    ...record EnrichedTopic success or error...
# Budget expired (or all done): unfinished topics fall through below as
# nomination-only; their daemon workers are abandoned and cannot block exit.
```

Topics still unfinished when the loop exits fall through with `error="enrichment budget exhausted"` and survive as nomination-only entries; the batch preserves nomination order and never raises. Defaults live beside the function: `ENRICH_MAX_WORKERS = 3`, `ENRICH_BUDGET_SECONDS = 240.0`.

Three tests in `tests/test_discover_enrich.py` pin the contract:

- `test_enrich_workers_are_daemon_threads` asserts `threading.current_thread().daemon` from inside every worker - the daemon property is tested directly, not inferred from process behavior.
- `test_enrich_concurrency_capped_by_semaphore` runs 6 nominations with `max_workers=2` and asserts peak in-flight workers never exceeds 2.
- `test_enrich_budget_expiry_drops_slow_topic_to_nomination_only` runs a fast and a 5-second topic under `budget_seconds=1.0` and asserts the fast one returns enriched while the slow one drops to nomination-only with a budget error.

## Why This Works

- Daemon threads are not joined at interpreter exit. CPython's shutdown sequence waits only for non-daemon threads; a daemon worker mid-fetch simply dies with the process. That is what makes the wall-clock budget real: expiry means the process can exit now, not "after the straggler finishes".
- The monotonic deadline bounds the consumer independently of worker behavior. `results_queue.get(timeout=min(remaining, 0.5))` wakes at least twice a second to re-check the deadline, so the collecting loop exits within ~0.5s of budget expiry no matter what any worker is doing.
- The semaphore preserves the executor's one useful property. `threading.Semaphore(max(1, max_workers))` acquired inside each worker caps in-flight sub-runs at `max_workers`, so upstream APIs see the same low parallelism as before; threads beyond the cap exist but block on the semaphore, costing almost nothing.
- The write-free precondition is what makes abandonment safe, and it is documented in the code comment where the next editor will see it: enrichment sub-runs are `internal_subrun=True` passes that write nothing to disk - no save, no library sync, no store - and every fetch layer inside `run()` carries its own timeout. Killing such a worker at process exit can corrupt nothing. A worker that mutates shared state (files, databases, caches) must not be abandoned this way; it needs cooperative cancellation instead.

The general lesson: `as_completed(timeout=...)` plus `shutdown(wait=False, cancel_futures=True)` is answer-bounding, not process-bounding. If the requirement is "this batch may not extend the life of the process", non-daemon executor threads cannot deliver it (CPython behavior since 3.9, when executor threads moved from atexit-daemon handling to `threading._register_atexit` joining), and no combination of executor knobs changes that.

## Prevention

- Any "budget" or "timeout" over threaded work must state what happens to a RUNNING straggler. If the design doc or comment only says what happens to the result, the process-lifetime question is unanswered - and the default answer (non-daemon threads joined at exit) is usually wrong for a CLI.
- Prefer explicit daemon threads for abandonable work. When stragglers are safe to drop, `threading.Thread(daemon=True)` + semaphore + queue + monotonic deadline is barely more code than an executor and actually enforces the budget. Reserve `ThreadPoolExecutor` for work you intend to wait for.
- Require the write-free precondition in a comment next to the daemon flag. Daemon abandonment is only safe for workers that mutate no shared state and hold no resources needing cleanup. State the precondition where the code is (as `enrich_nominations()` does), so a future change that adds a disk write inside the worker trips over the warning.
- Test daemon-ness explicitly. Process-hang bugs are invisible to result-oriented unit tests - the passing slow-topic test proved the wrong thing. Assert `threading.current_thread().daemon` inside the worker (see `test_enrich_workers_are_daemon_threads` in `tests/test_discover_enrich.py`); it is a one-line assertion that pins the property the budget depends on.
- Per-request timeouts inside workers remain the first line of defense. Daemon abandonment is the backstop for the pathological case; every network call inside a worker should still carry its own timeout so hung workers are rare, not routine.
- In review, treat `shutdown(wait=False, cancel_futures=True)` in a `finally` as a signal to ask the straggler question. It is the idiom people reach for when they want abandonment, and it does not provide it.

## Related Issues

- [Ranked-output confidence floor + honest empty state](../design-patterns/ranked-output-confidence-floor-honest-empty-state.md) - sibling learning from the same PR #816 discovery rebuild: the ranking-quality half vs this doc's process-lifetime half. Both live in `skills/last30days/scripts/lib/pipeline.py`.
- [argparse optional-value flag dispatch](../conventions/argparse-optional-value-flag-dispatch-truthiness.md) - third lesson from the same PR #816: bare-flag vs flag-absent conflation, another defect class invisible to result-oriented tests.
- [PR #816](https://github.com/mvanhorn/last30days-skill/pull/816) - the discovery rebuild that replaced the executor with daemon threads + semaphore + result queue + monotonic deadline in `enrich_nominations()` (released v3.14.0).
- Note: `skills/last30days/scripts/lib/pipeline.py` still uses `ThreadPoolExecutor` at other call sites where work is genuinely waited for; the daemon-thread pattern was applied only to `enrich_nominations()`, whose stragglers are abandonable. Apply the straggler question, not the pattern, when touching those.
