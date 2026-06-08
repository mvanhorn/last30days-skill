---
title: Search-quality eval is manual by default, not a CI gate on every PR
date: 2026-05-10
category: docs/solutions/architecture
module: skills/last30days/scripts/evaluate_search_quality.py
problem_type: design_decision
component: ci_policy
severity: low
applies_when:
  - a contributor proposes wiring search-quality eval into PR CI
  - a change affects retrieval, ranking, grounding, or synthesis quality and a reviewer asks "why aren't we testing this in CI?"
  - someone is deciding whether a new evaluator-style script belongs in the default CI workflow
related_components:
  - search_quality_evaluation
  - ci_workflow
  - llm_judging
tags:
  - ci-policy
  - eval
  - design-decision
  - cost-vs-signal
  - non-determinism
  - manual-gates
---

# Search-quality eval is manual by default, not a CI gate on every PR

## Context

`skills/last30days/scripts/evaluate_search_quality.py` compares a baseline revision against a candidate revision across a fixed pool of reviewer topics. It produces two flavors of metrics: deterministic overlap (Jaccard, retention) and LLM-judged quality scores. The natural impulse on seeing an evaluator script is to wire it into CI on every PR — "regression catcher, run it automatically." We deliberately don't.

Three properties of this particular evaluator make CI-on-every-PR the wrong default:

1. **Live API access.** The candidate revision typically needs the engine to actually run, which means real ScrapeCreators calls, real reddit fetches, real YouTube searches. CI runs would either need production credentials or a record/replay fixture set that drifts almost immediately as external APIs change shape.

2. **Cost and latency.** A full eval pass runs the pipeline N times across reviewer topics. Multiplied by every PR (including doc-only PRs), the spend is meaningful and the wall-clock pushes CI from ~30s to many minutes.

3. **Non-determinism in the judging path.** The LLM-judged metrics are valuable for review but depend on judge-model behavior on a given day. A flaky eval that fails 1 PR in 20 because the judge re-scored an item differently is a worse CI signal than no eval at all — it teaches contributors to retry rather than read the result.

The deterministic overlap metrics are useful regression signals but they are not the same as user-facing correctness. A change that improves overlap can degrade synthesis quality; a change that drops overlap can be a deliberate improvement. So even the deterministic side isn't safe to auto-fail on.

## Guidance

### 1. Keep search-quality eval available, just not automatic

The script stays runnable by maintainers and contributors. The pattern is:

```bash
LAST30DAYS_PYTHON=python3.13 \
  python3 skills/last30days/scripts/evaluate_search_quality.py \
  --baseline main --candidate HEAD
```

Reviewers can request a manual eval run when a PR is in the retrieval/ranking/synthesis path and the risk warrants it. Contributors can run it locally before submitting if they want signal upfront.

### 2. Standard PR CI gates remain deterministic and contract-shaped

`pytest` (offline-safe), plugin-contract checks, version-consistency contracts, ruff/lint. Anything that returns the same answer twice for the same input. Quality-of-output assessment lives outside that loop.

### 3. The middle ground is `workflow_dispatch`, not auto-PR-gating

If maintainers want a GitHub-triggered eval that doesn't make every PR pay the live-API cost, the right shape is a manually-dispatched workflow (or a label-triggered one) — not a `pull_request:` workflow that runs unconditionally. That keeps the cost knob in human hands.

### 4. Revisit if the eval can ever be made offline-deterministic

The blocker is the live-API + non-determinism combination. If a future iteration of the script can compute meaningful Jaccard/retention metrics against static fixtures (no live API calls, no LLM judging), the decision flips and it becomes a candidate for default CI. The decision below tracks that condition; revisit when it's met.

## What this means in practice

- Don't merge PRs that wire `evaluate_search_quality.py` into the default `validate.yml` workflow.
- Do merge PRs that add `workflow_dispatch` triggers or label-gated runs.
- When reviewing a retrieval/ranking change, request a manual eval if the diff suggests it could regress quality — don't expect CI to catch it.

## Links

- `skills/last30days/scripts/evaluate_search_quality.py` — the evaluator script
- `docs/search-quality-eval.md` — user-facing usage documentation
- `.github/workflows/validate.yml` — the default CI workflow (deterministic gates only)

---

*Adapted from a draft ADR proposed by @hnshah in [#374](https://github.com/mvanhorn/last30days-skill/pull/374), restructured into the `docs/solutions/` convention. The original ADR text correctly identified the constraint; this version adds the "why workflow_dispatch is the middle ground" framing and the revisit-condition.*
