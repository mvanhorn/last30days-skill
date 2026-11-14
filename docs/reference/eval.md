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

Aggregate floors live in `tests/eval/baseline.json`. The fixture matrix covers a tech product, a person, a comparison, breaking events, a niche technical topic, a non-English CJK topic, and a set of Digg stories whose sources confirm some X and Hacker News items and not others (`digg-confirmation`, hand-authored from saved Digg search answers rather than recorded).

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
- Digg story sources are fetched before supplemental search and record their own exchange (`phase: story_sources`): a map from story UUID to its quotes and source URLs. Replay feeds that map through the same attach-and-match code as a live run, so confirmation tags and the ranking bonus are computed, not replayed.
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

## Jobs Page Secret Signals labelled set

This set is separate from the harness above, which runs the pipeline and never a model. Whether a report carries a Jobs Page Secret Signals section is the host model's judgment of the `## Jobs Digest` evidence, following `skills/last30days/references/hiring-signals.md`, so it cannot be scored offline.

`tests/fixtures/jobs_signals_labelled.json` holds a person's labels for four saved boards (Anthropic, Stripe, SpaceX, Nvidia) and a second Nvidia digest with a public sector cluster: each candidate signal with the posting's words, which of the four tests of the bar it meets, whether it is already public, and whether it should qualify, plus whether the board should get a section. `tests/test_hiring_signals_contract.py` checks two things with it in CI: that the written rules say what the labels assume, and that the labels agree with the decision rule restated in `tests/jobs_signals_rule.py` (two of the first three tests or the fourth; three or more postings sharing one concrete focus count as one cluster signal; one qualifying signal is enough and at most two are shown; no section without web search; public signals worded as public).

That proves the arithmetic and the labels' consistency. It does not prove a model finds those signals or judges the tests the same way. For that, check by hand on a host with web search:

1. Run `/last30days <Company>` for each of the four companies.
2. Compare the section with the board's `expected_section`. Stripe should show one signal, and no section has more than two signal paragraphs of two to three sentences. A "Jensen Huang" run should show the public sector cluster as one signal that cites two postings.
3. Where a section appears, compare its signals with the candidates labelled `qualifies`. Boards change, so match on what the signal is, not on the exact role title, and look for a signal labelled `does not qualify` being shown.
4. Check the wording against `public`: SpaceX's Starmind may appear only as already public, never as unreported.
5. Check the form: bold lead-in with the board's counts, one paragraph per signal with a quote and the posting link, no list, and the closing sentence about open postings.

The Nvidia label that rests on a report story applies only when that month's report carries that story.

## Patent Secret Signals labelled set

The same split applies to the Patent Secret Signals section, which the host model writes from the `## Patent Digest` evidence following `skills/last30days/references/patent-signals.md`.

`tests/fixtures/patent_signals_labelled.json` holds constructed cases, one per rule, not saved digests: each candidate filing with whether it is a first US publication, whether it names something concrete, which paths of the bar it meets, what the web check found, and whether it should qualify. `tests/test_patent_signals_contract.py` checks in CI that the written rules say what the labels assume, and that the labels agree with the decision rule restated in `tests/patent_signals_rule.py` (concrete and out of character or unshipped; a tie to a story in the report never qualifies a filing; a shipped or reported match removes the unshipped path; continuations and the like never qualify; a titles-only entry never qualifies; three invention families sharing one focus count as one cluster signal; a filing the engine could not verify needs an entity known to be the company's; one signal by default and a second only when neither has a matching product, and at most three paragraphs with the jobs section; no section without web search).

That proves the arithmetic and the labels' consistency. It does not prove a model picks those filings or judges the bar the same way. For that, check by hand on a host with web search, using an installed copy of the skill:

1. Run `/last30days <Company>` for a large filer (a company with hundreds of US filings a month) and for a company with no filings.
2. Check presence. The company with no filings prints no `## Patent Digest`, and its report has no Patent Secret Signals section. For the large filer, a section appears only when a filing clears the bar, usually has one signal paragraph, and never has more than two (one when the report also has two jobs signals). A filing that only fits a story in the report gets no section.
3. Check hedging. Each signal says what the filing describes with hedged verbs (describes, would, could), never that the company is building or will ship it, and is not worded as new or unreported when the web check found coverage or a matching product.
4. Check both dates. Each signal paragraph links the filing as the digest printed it, says application or grant, and gives the publication date and the filing date. A filing by an entity other than the company itself names that entity, and the report never calls a filer confirmed, matched or unverified.
5. Check the form: the bold lead-in runs straight into a two-sentence signal, with no counts sentence and no closing disclaimer; any second signal is one more two-sentence paragraph; no list; after the jobs section when there is one.
