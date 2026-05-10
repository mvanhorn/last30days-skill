# ADR 002: Search-quality eval is manual by default

Date: 2026-05-10  
Status: Accepted

## Decision

`skills/last30days/scripts/evaluate_search_quality.py` is a manual evaluation tool by default, not a CI gate on every push or pull request.

## Context

The search-quality eval compares a baseline revision with a candidate revision across reviewer topics. Its useful modes can require live API access, network calls, external provider behavior, and optional LLM judging. Running it for every PR would add cost, latency, secrets management, and non-determinism to normal CI.

The deterministic overlap metrics are valuable regression signals, but they are not the same as user-facing correctness. The LLM-judged metrics are helpful for review, but only as strong as the judged pool and model behavior for that run.

## Consequences

- Standard PR CI should keep using deterministic tests and contract checks.
- Search-quality eval remains available for maintainers and contributors when a change affects retrieval, ranking, grounding, or synthesis quality.
- Quality regressions are not automatically caught by this eval on every PR; reviewers should request a manual eval when the change warrants it.
- A future `workflow_dispatch` workflow is the preferred middle ground if maintainers want GitHub-triggered evals without making every PR pay the live API cost.
- Revisit this decision when the eval can compute meaningful Jaccard/retention metrics against static fixtures without live API calls.

## Links

- `docs/search-quality-eval.md`
- `skills/last30days/scripts/evaluate_search_quality.py`
