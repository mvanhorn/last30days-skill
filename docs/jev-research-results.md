# Jev evaluation results - 2026-09-28

Fast Search + Jev + fixed follow-up improved ranking on the tested public technical topics. Use the configured Jev key for ranking; request bounded follow-ups only when needed. Source-support errors rule out using it as the sole citation or completion gate.

The current default is `auto`: a Typesafe key enables direct Jev, otherwise an OpenRouter key enables that route. Explicit `off` disables it. The measurements below predate this default change; they do not measure its effect on all user workflows.

## Public-query comparison

Ten topics, 24 configurations, three retrieval passes: **720 component trials**. Six failed trials stayed in the quality denominator at zero. These reuse measured components; they are not 720 independent full-engine runs.

| Search / ranker / follow-up | nDCG@5 | Estimated USD/query | Median component seconds |
|---|---:|---:|---:|
| Fast / Jev / fixed | 0.780 | $0.00327 | 1.10 |
| Web / Jev / fixed | 0.771 | $0.01529 | 1.15 |
| Fast / incumbent / fixed | 0.724 | $0.01140 | 23.59 |
| Fast / Jev / adaptive Jev | 0.703 | $0.00233 | 1.21 |
| Fast / deterministic / fixed | 0.616 | $0.00300 | 0.79 |
| Fast / deterministic / none | 0.511 | $0.00100 | 0.26 |

Jev's gain over deterministic fixed follow-up was 0.164 nDCG@5; paired topic-bootstrap 95% interval: [0.096, 0.246]. Fast/Jev/fixed had the highest point estimate. Its lead over web/Jev/fixed and adaptive Jev was not statistically resolved.

**Limits:** 218 excerpt records, 148 per-topic unique URLs, agent labels rather than human gold. A second reader agreed on 13/21 relevance grades and 18/21 facet labels before adjudication; the other labels lack a second reading. Mean top-five facet coverage for the best flow was 0.5. Times exclude planning, social collection, enrichment, and final synthesis. Costs are estimates at recorded rates, not invoices.

Topics: Firefox, Pixel updates, Home Assistant Zigbee, SteamOS, Blender Cycles, PostgreSQL minor releases, Rust/Cargo, OBS Studio, Sony headphone firmware, and Chrome Manifest V3. Window: August 29 to September 28, 2026. Models: direct Typesafe `jev-1.13.0` and incumbent `grok-4.3`. This batch does not measure OpenRouter or final-answer quality.

## Other evaluations and retained failures

| Evaluation | Result and limit |
|---|---|
| 30 fresh runs on the same ten topics | All passed. Fast/Jev/fixed: 0.777 nDCG@5, 1.149 s median, $0.00327/query. Not new-topic holdouts. |
| 50 constructed source-support checks, repeated three times | Jev agreement 150/150; incumbent 143/150. Both had 0/120 false approvals on negative checks. |
| 12 retrospective source-support checks, repeated three times | Jev agreement 27/36; incumbent 32/36. **False approvals: Jev 6/18, incumbent 3/18.** Jev's errors were two distinct mistakes repeated three times. Neither support test estimates population error rates. |
| Fresh adaptive coverage claims | Two unsupported claims out of nine. Fixed follow-up makes no completeness claim. |
| Earlier synthetic ranking holdout | Five nonempty cases: deterministic 0.8838, incumbent 1.0000, Jev 0.9928 nDCG@5. Two empty/unavailable cases also tested. Ten live calls, no errors. Incumbent 5.02-9.40 s; Jev 123-154 ms; cost unreported. |
| Four paired Search requests | Fast 345/279 ms; web 303/299 ms. Each pair shared 3/5 URLs. No consistent latency advantage or independent relevance labels. |
| Claude Code workflow probe | Three Search and five Jev calls; follow-ups added nine URLs. One conservative coverage miss, no false closure. Review was not blind. |
| Home Assistant production smoke | Three Fast Search calls, two Jev reranks; fixed receipt passed with no coverage claim. 1.287 s, estimated $0.003160. Wiring proof, not quality proof. |

The retrospective errors included accepting a major-version beta as evidence of a minor release and a generic explanation as evidence of a recent breakage. Retain separate citation review.

The matrix, fresh runs, support checks, and Home Assistant smoke used 2,170 API requests: $5.50538 known estimated cost, $5.53135 conservative bound including an unknown-cost timeout reservation. Review and labeling are excluded. Counts above overlap; do not sum rows as independent evidence.

## Both-provider live validation

**11 final pipeline cases passed across nine combinations**, on Rust and Home Assistant. Each provider covered fast/reranking only, fast/fixed, web/fixed, fast/adaptive, and a second-topic fixed run. A no-Jev baseline passed. Checks covered provider identity, search mode, budgets, ranking, and follow-up receipts.

Five initial OpenRouter pipeline runs failed because the configured reasoning model was unavailable (HTTP 404). All Jev and Search calls succeeded. All five repeats passed with the explicit reasoning-model pin `openai/gpt-4.1-mini`; that validation left the reasoning-model defaults unchanged. The 16 total pipeline executions include these failures and repeats.

| Fixed-packet holdout route | Nonempty cases | nDCG@5 | Median judge request |
|---|---:|---:|---:|
| Deterministic | 5 | 0.8838 | No call |
| Typesafe | 5 | 0.9928 | 145 ms |
| OpenRouter | 5 | 0.9928 | 171 ms |

Both routes also passed two empty/unavailable cases. Ten live judge calls had no errors. Returned models: `jev-1.13.0` and `typesafe/jev-1.13-20260917`. These reuse synthetic holdouts. An earlier control confused “announced” with “released”; the corrected question passed. No production threshold changed.

This validation used **99 API requests**, including probes, failures, and repeats. Known cost estimate: **$0.08612**; six failed reasoning requests have unreported cost. Integration times exclude host planning and final synthesis. OpenRouter cases also ran the separate fun scorer, so they cannot rank Jev provider speed.

A September 29 live recheck passed five ranking requests per Jev provider plus two no-candidate cases each. Typesafe scored 0.9928 nDCG@5 and OpenRouter 1.0000 on the same five nonempty holdouts; these reused fixtures do not rank provider quality. One separate public-query Fast Search adapter check returned three results. These eleven requests are separate from the historical counts above.

## Code verification - 2026-09-29

**5,305 tests and 105 subtests passed; five opt-in live checks skipped; zero failures or deselections.** Coverage: **90.25%**, against an 84% floor, including scripts and tests but excluding vendor code. The final run took 343.74 seconds and reported 103 SQLite resource warnings. The full `uv run pytest --cov` suite used a local socket guard to refuse external connections and permit loopback fixtures. This verifies the current code, including key-driven defaults, partial-result reporting, adaptive status output, and ranking after slow retrieval. It does not refresh the live measurements above.

The test-quality cleanup first passed all **216 affected tests**, then the full suite above. Four deliberate faults were rejected: dropped candidates, duplicate candidates, swapped scores, and repeated pivot queries. The cleanup removed duplicate and literal-value assertions and added rounded-response acceptance checks. The full run includes the cleaned tests.

Final cleanup passed Ruff correctness checks on all 30 changed Python files, plus Python 3.12 syntax, shell syntax, JSON parsing, and diff-whitespace checks. After removing unused bindings, 421 affected tests and 28 subtests passed. The repo has no configured Python linter; this check used Ruff `F,E9` without adding lint configuration.

The review fixed a slow HTTP error-body deadline defect. Tests cover missing-key bypass before optional work, private-corpus exclusion, provider selection, malformed responses, atomic fallback, bounded follow-ups, and error-body deadlines. They do not prove model resistance to hostile text. Caller deadlines cannot cancel a remote request already sent.

## Evidence and reruns

- [Matrix JSON](jev-research-matrix-summary.json): all 24 configurations, paired comparisons, fresh-run aggregates, audit counts, and source hashes. An aggregate export; raw packets, labels, and orchestration are not included, so it does not independently reproduce the matrix.
- [Live validation JSON](jev-live-validation.json): per-case checks, counts, models, timings, and cost scope. Historical evidence, not runtime configuration or a CI baseline.
- [Evaluation guide](eval-jev-research.md): reproducible fixed-packet commands and fixture limits.

New-topic labels and matched full-workflow tests can extend these results. They can improve route guidance while the current key-driven ranking feature remains usable.
