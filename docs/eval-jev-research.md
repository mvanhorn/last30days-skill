# Jev evaluation guide

The developer runner compares the production deterministic, incumbent, and Jev rerankers on identical packets. It does not run Search or adaptive follow-ups. The production default uses Jev when a key is configured, unless `off` is selected. Evaluation runs still require explicit call authorization. Live runs are manual, as with the repo's [search-quality evaluator](search-quality-eval.md); offline tests join the existing [CI eval harness](reference/eval.md).

## Files and purpose

| File | Role |
|---|---|
| `fixtures/jev_eval.json` | Ten authored synthetic cases: three calibration and seven holdout. Contains inputs and labels; providers receive inputs only. |
| `docs/jev-research-matrix-summary.json` | Historical aggregates for 24 configurations. Not runtime configuration or a CI baseline. |
| `docs/jev-live-validation.json` | Historical provider checks, costs, timings, and failures. Not runtime configuration. |

JSON matches the repo's existing fixture/baseline format. The existing `evaluate_search_quality.py` also writes `metrics.json` beside `summary.md`. The two checked-in result exports are new evidence artifacts; upstream does not require them. Use [the results note](jev-research-results.md) for interpretation.

## Offline checks

From the checkout root:

```sh
uv run python skills/last30days/scripts/evaluate_jev_research.py \
  --mode offline --split holdout --output /tmp/jev-offline-new.json
uv run pytest tests/eval/test_jev_research_eval.py \
  tests/eval/test_adaptive_policy_eval.py
```

Offline mode runs the real deterministic ranker. It does not simulate Jev. Output paths must be new.

## Live holdout through both Jev providers

Run only with authorization for provider calls. If the ignored checkout `.env` holds credentials, run `export LAST30DAYS_CONFIG_DIR="$PWD"`; otherwise use the normal credential configuration. Do not put keys in command arguments.

```sh
uv run python skills/last30days/scripts/evaluate_jev_research.py \
  --mode live --execute --split holdout --arms deterministic,jev \
  --jev-provider typesafe --max-calls 5 --timeout 120 \
  --output /tmp/jev-typesafe-holdout-new.json
uv run python skills/last30days/scripts/evaluate_jev_research.py \
  --mode live --execute --split holdout --arms deterministic,jev \
  --jev-provider openrouter --max-calls 5 --timeout 120 \
  --output /tmp/jev-openrouter-holdout-new.json
```

Typesafe needs `TYPESAFE_API_KEY`; OpenRouter needs `OPENROUTER_API_KEY`. Each command checks seven cases with at most five transport attempts. Empty/unavailable cases need no calls. Missing credentials fail before paid calls. The runner disables browser-cookie reads.

To include the configured incumbent ranker, use `--arms deterministic,incumbent,jev --max-calls 10` with a new output path. This also requires the incumbent's credentials and an available reasoning model. Failed attempts count against the cap; no hidden retry budget is added. This is an evaluator control, not a production Jev call quota.

## Scoring and limits

- nDCG@5 measures ordering against authored labels. P@5 always divides by five. Recall and facet coverage use fixture labels, not a judge's completeness claim.
- Repeated URLs count once. Failed arms score zero in applicable denominators. Empty/unavailable cases have explicit denominators and undefined nDCG/recall where no relevant evidence exists.
- Reports include input/code hashes, rankings, requested/returned models, transport attempts, timing, and numeric usage/cost. Missing cost remains `null`. Credentials and provider error text are excluded. Output is reserved before calls and saved after each case/arm.
- The fixtures cover entity collisions, duplicates, dates, contradictory experience, hostile instructions, non-Latin text, and unavailable collection. Product claims and `example.com` URLs are invented. Do not cite them as real evidence or tune thresholds on the holdout.
- This runner reproduces the small fixed-packet evaluation. The larger matrix exports aggregates only; its raw packets, labels, and orchestration are not in this checkout. Neither test establishes final-answer quality or production latency percentiles.

Controller tests use controlled typed responses to check limits and policy behavior. They are not live Jev quality measurements. Keep live quality scores separate from deterministic CI gates.
