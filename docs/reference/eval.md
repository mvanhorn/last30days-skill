# Research-quality eval harness

The eval suite measures the quality properties that ordinary unit tests do not: whether ranked evidence is grounded in retrieved inputs, stays inside the requested window, forms coherent clusters, accounts for every usable fixture source, and remains deterministic.

It runs the production pipeline offline. Recorded HTTP exchanges replay at `lib/http.py`; CLI-backed adapters such as yt-dlp, Digg, arXiv, Techmeme, and Trustpilot replay their parsed result at the source-module seam. Planning is supplied by each fixture manifest, and normalization, date filtering, scoring, fusion, clustering, source outcomes, and the versioned agent JSON export all run normally. The harness never calls an LLM or the network.

## Run it

From the repository root:

```bash
uv run pytest tests/eval -x -s
```

The `-s` keeps the score table visible. To print only the scored run and return a nonzero exit when a floor is missed:

```bash
uv run python tests/eval/harness.py
```

CI runs the pytest command in the `eval` job of `.github/workflows/validate.yml`, so every pull request gets a score table and a hard baseline check.

## Metrics

| Metric | Deterministic definition |
|---|---|
| Citation grounding | Fraction of exported result URLs that occur in the recorded fixture inputs. |
| Recency compliance | Fraction of ranked source items whose known publication date is inside the report's inclusive date window. Undated evidence is not falsely classified as stale. |
| Cluster coherence | Fraction of within-cluster candidate pairs meeting the production entity-overlap threshold (`0.45`). Singleton clusters are coherent by definition. |
| Coverage | Fraction of fixture sources represented by usable report items or an explicit `Report.source_status` outcome. |
| Determinism | `schema.to_dict()` equality for two runs with fixed time and identical recorded inputs. |

Aggregate floors live in `tests/eval/baseline.json`. The fixture matrix covers a tech product, a person, a comparison, breaking events, a niche technical topic, and a non-English CJK topic.

## Add or refresh a fixture

Fixture directories contain:

- `manifest.json`: topic archetype, fixed `as_of_date`, sources, safe dummy config, and a deterministic external query plan.
- `http.json`: scrubbed HTTP exchanges and any CLI-backed source exchanges.

Use the direct engine invocation below only for development/fixture capture; `/last30days <topic>` remains the product interface:

```bash
python3 skills/last30days/scripts/last30days.py \
  "<topic>" \
  --quick \
  --as-of 2026-07-10 \
  --search grounding,hackernews \
  --plan /tmp/eval-plan.json \
  --record-fixtures tests/eval/fixtures/<fixture-name>
```

`--record-fixtures` is intentionally hidden from `--help`. It records the live run's shared HTTP traffic and the bounded CLI-adapter seams, scrubs credential-shaped query/body/response fields, and writes `http.json`. It does not create the manifest because archetype, fixed date, source contract, and query plan are review decisions.

Before committing a recording:

1. Inspect `http.json` for cookies, keys, tokens, personal identifiers, and unnecessary long bodies.
2. Truncate content to the smallest structure that exercises the adapter and pipeline.
3. Replace irrelevant real usernames with obvious fixture identities.
4. Add the manifest and run both commands above with networking unavailable.

The replay is fail-closed: an unrecorded request or an unused recorded exchange fails the run.

## Fixture flags

- `expects_clusters` (bool): fixtures whose topic historically forms multi-member clusters set this true; if cluster formation regresses to singletons on such a fixture, coherence scores 0.0 instead of a vacuous 1.0. Sparse topics (niche, non-english-cjk, tech-product) set it false because singletons are their legitimate shape.
- Post-ranking enrichment (YouTube transcripts, Digg posts) is recorded and replayed by merging recorded `metadata` onto freshly computed items by item_id, so normalization/scoring/dedupe regressions stay visible to the eval rather than being overwritten by fixture state.
- Post-rerank GitHub star enrichment records its repo->stars map and replays via `github.apply_star_map`, keeping runs offline even when `GITHUB_TOKEN` is set in CI. GitHub project-mode (`--github-repo`) and person-mode (`--github-user`) runs are not yet fixture-recordable; the network guard fails loudly if a fixture attempts them.

## Known seams

- Module-backed sources (yt-dlp, digg-pp-cli and other CLI adapters) record post-parse items at the module boundary, so replay does not re-exercise their parsing/normalization code the way HTTP-backed sources do (those replay raw responses through the real pipeline). A normalization regression in a module adapter is covered by that adapter's unit tests, not the eval. Recording raw CLI stdout is a possible future upgrade.
- Cluster coherence shares `entity_extract` with production clustering. The pinned-predicate test (`test_entity_overlap_predicate_pinned`) guards against the shared predicate drifting permissive, and per-fixture floors in baseline.json catch a single archetype collapsing even when the cross-fixture average stays green.

## Move a baseline

Baseline edits are explicit quality-policy changes, not snapshot refreshes. Move a floor only when an intentional product change makes the old threshold invalid or when a new fixture legitimately changes the measured distribution.

Include in the review:

1. The old and new score tables.
2. The reason the metric changed.
3. A focused test proving the intended behavior.
4. An explanation for any lower floor; never lower a floor solely to make CI green.

`test_intentional_out_of_window_regression_fails_recency_floor` is the standing negative control: it injects stale ranked evidence and proves the baseline check detects the regression.
