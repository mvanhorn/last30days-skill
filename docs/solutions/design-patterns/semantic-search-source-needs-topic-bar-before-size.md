---
title: "A source whose search always answers needs its own topic bar before size counts"
date: 2026-11-12
category: design-patterns
module: digg
problem_type: design_pattern
component: tooling
severity: medium
applies_when:
  - "A source's search is semantic and returns a full page for any query, including topics it has no coverage of"
  - "A source's items carry a size signal (score, source count) that the ranker rewards"
  - "A retrieval or filtering rule is being judged only by fixture-replay evals"
  - "Someone proposes keeping items that mention the topic only in a summary, or stretching a source's engagement scale"
tags:
  - digg
  - semantic-search
  - topic-bar
  - relevance
  - empty-state
  - dedupe-order
  - live-comparison
  - engagement-saturation
related_components:
  - "skills/last30days/scripts/lib/digg.py"
  - "skills/last30days/scripts/lib/pipeline.py"
  - "skills/last30days/scripts/lib/signals.py"
  - "tests/test_digg_bar.py"
---

# A source whose search always answers needs its own topic bar before size counts

## Context

Digg search through `digg-pp-cli` is semantic. It returns a full page of stories for any query, whether or not Digg covered the topic. A 30-day search for "Ghostty" returned 20 stories, none about Ghostty, led by a Netflix renewal. The engine's general relevance prune did not remove them, so they reached reports as Digg evidence.

The opposite failure sat next to it. A search for "OpenAI" asked only for the most relevant stories and returned a largest Digg Score of 226, while the month's three biggest OpenAI stories scored 1286, 473 and 239 and were never fetched.

Both were fixed in PR #1419 (released v3.38.0) with a follow-up in PR #1421 (released v3.38.1). The tests pin the resulting behavior. This document records what the tests cannot: the measurements and the rejected alternatives.

## Guidance

### 1. Judge topic first, with a test written for the source

`digg.apply_bar` in `skills/last30days/scripts/lib/digg.py` sorts each story into prominent, ordinary or dropped. The story's title must carry the topic before its size is looked at. Prominent additionally needs a Digg Score of at least `PROMINENT_MIN_SCORE` (40) and at least `PROMINENT_MIN_SOURCES` (5) sources. A story that fails the title test is dropped however large it is.

The general prune is the wrong tool here because it scores title and summary text together, and a semantic search engine returns stories whose summaries share vocabulary with any query.

### 2. A mention in the summary is not enough

The first version kept a story as ordinary when only its summary named the topic. A live run for "Taylor Swift" ranked a story about a tour by The Weeknd and a film roundup first, because both mention her in passing and both were large. Summary-only stories are now dropped.

Two narrower allowances survived live checking, both in `_title_points_at`:

- A two-word subject passes when the title has one of its words and both words appear somewhere in the title and summary together. They do not have to appear as a phrase. "Tesla earnings" keeps a story titled "Tesla beats on revenue" whose summary says "earnings".
- A subject of three or more words is treated as a description, and a telling word in the title is enough.

The word count is taken after `_topic_subjects` removes question and filler words from the topic, not from the topic as typed. "latest Tesla earnings" is judged as the two-word subject "Tesla earnings".

### 3. Run the bar before near-duplicates are removed

Deduplication keeps the earlier of two similar items. When the bar ran after it, a small story the bar would have dropped could be the copy that survived, and the prominent story it resembled was lost. The bar is now passed as the `before_dedupe` step of `_normalize_score_dedupe` in `skills/last30days/scripts/lib/pipeline.py`, uncapped, and the per-search limit (`digg_stream_limit`) is applied afterwards. Any filter that decides which items are worth keeping belongs ahead of a step that discards one item in favor of another.

### 4. Ask for the biggest stories as well as the closest ones

At default and deep depth a research run makes two searches per query, most relevant and highest Digg Score, and merges them (`_merge_search_results`). The second call has its own shorter limit (`SCORE_SEARCH_TIMEOUT`, 15 seconds) and is skipped when fewer than `SCORE_SEARCH_MARGIN` seconds remain, so a slow second call near the deadline cannot cost the run the first call's results. Digg stalled for 30 seconds twice during testing, which is where these numbers come from.

### 5. Say why the source is empty

When the bar drops every story, the source reports "N of M stories dropped for not naming the topic" and is not retried. Zero stories with that note is the correct result for "Ghostty", and it reads as a judgment and not as a broken source.

### 6. Check a retrieval rule against live runs, not only replayed fixtures

Fixture-replay evals return recorded, already-parsed items, so they cannot show what a live search returns for a new topic. The summary-only problem in section 2 passed every test and every eval. It appeared only in a side-by-side live run of the previous release against the branch across six topics. Before merging a change to what a source keeps, run the old and new engine on a handful of live topics, including one the source has no coverage of, and read the top results.

## Why This Matters

Measured at default depth on Digg, Hacker News and X, previous release against v3.38.0:

| Topic | Stories kept before | Stories kept after | Note |
|---|---|---|---|
| OpenAI | 12 | 20 | largest Digg Score 226 before, 1286 after |
| Ghostty | 12 | 0 | all 20 fetched stories dropped |
| Taylor Swift | 12 | 14 | every kept title names her |
| Tesla earnings | not measured | 8 | 41 of 49 dropped |

Without the bar, a report on a topic Digg never covered cites a dozen unrelated stories with real scores attached. That is worse than no Digg section, because the scores make the stories look vetted.

## When to Apply

- Adding any source whose search is semantic or otherwise never returns an empty page.
- Changing what `apply_bar` keeps. Rerun the live comparison first, and keep "Taylor Swift" and a no-coverage topic in the set.
- Adding a filter near deduplication in the pipeline.

## Known limits and a rejected change

- No alias matching. A story titled "ChatGPT ..." does not count for the topic "OpenAI".
- A name that ends in a word the bar treats as filler is judged on what remains. "Hacker News" is judged as "hacker". Keeping the filler word would break "OpenAI News", the more common shape, so this was left as it is.
- The biggest story does not rank first overall. The Digg engagement reference in `skills/last30days/scripts/lib/signals.py` is 4.5, the value for a story with Digg Score 226, so scores of 226 and 1286 earn nearly the same engagement credit. On "OpenAI" the 1286 story ranked 11th in the merged results. Raising the reference to 6.0 was tried: that story moved to 6th, but Digg's share of the top 15 fell from 9 to 5 because every ordinary Digg story lost credit. The change was reverted. Anyone revisiting it should measure both numbers.
- Reading the sources of prominent stories did not produce more cross-source confirmations in these runs. None of the eight largest "OpenAI" stories cited an item the other sources had returned.

## Examples

"Ghostty", previous release: 12 Digg stories in the report, first one a Netflix renewal.

"Ghostty", v3.38.1: no Digg stories, with the note "20 of 20 stories dropped for not naming the topic".

The bar's behavior is pinned in `tests/test_digg_bar.py` and in `DiggBarRunTests` in `tests/test_pipeline_v3.py`.

## Related

- [Ranked-output features need an explicit confidence floor with an honest empty state](ranked-output-confidence-floor-honest-empty-state.md) - the same idea for discovery topics: an absolute gate ahead of the ranker, and an empty state that explains itself. That floor measures evidence strength. This bar measures whether the item is about the topic at all.
- [Entity grounding: full-phrase false demotion](../logic-errors/entity-grounding-full-phrase-false-demotion.md) - the general relevance check this bar sits beside, and the opposite failure (good items demoted).
- [Search-quality eval: manual by default](../architecture/search-quality-eval-manual-by-default-2026-05-10.md) - why ranking changes are validated by manual runs.
- [PR #1419](https://github.com/mvanhorn/last30days-skill/pull/1419) and [PR #1421](https://github.com/mvanhorn/last30days-skill/pull/1421).
