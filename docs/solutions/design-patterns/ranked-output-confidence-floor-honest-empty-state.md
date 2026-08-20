---
title: "Ranked-output features need an explicit confidence floor with an honest empty state"
date: 2026-07-12
last_updated: 2026-07-20
category: design-patterns
module: discover-trending
problem_type: design_pattern
component: tooling
severity: medium
applies_when:
  - "Any feature that ranks and displays top-N results from variable-quality inputs (search, trending, recommendations, discovery)"
  - "Quiet or over-broad query domains where feeds return thin or noisy data"
  - "A gate measures corroboration or independence downstream of a stage of the same pipeline that amplifies that signal (enrichment, fan-out, retrieval expansion)"
symptoms:
  - "Top-N ranker emits near-zero-engagement items (e.g., five 1-like tweets) as a trend list because top-N has no notion of 'none of this is good enough'"
resolution_type: code_fix
tags:
  - ranking
  - confidence-floor
  - empty-state
  - top-n
  - discovery
  - trending
  - signal-quality
  - corroboration
  - "seed-sources"
  - "junk-shape"
  - "source-independence"
related_components:
  - "skills/last30days/scripts/lib/rerank.py"
  - "skills/last30days/scripts/lib/pipeline.py"
  - "tests/test_discover_floor.py"
---

# Ranked-output features need an explicit confidence floor with an honest empty state

## Context

The `--discover` trending feature sweeps listing feeds (r/all, Hacker News front page, Digg, X), clusters what it finds into candidate topics, and emits the top topics by an engagement-velocity score. The selection was purely relative: `run_discover()` in `skills/last30days/scripts/lib/pipeline.py` computed `topic_limit = max(5, min(10, limit))` and took the top N clusters by score, regardless of whether the Nth-best (or even the 1st-best) was any good.

On quiet or overly broad domains the feeds return thin, noisy data, and a relative ranker has no way to say so. The named failure (2026-07-12): `/last30days --discover "sports"` returned five single-source tweets with 1 like each - a Wii Sports nostalgia thread, a kids-travel-sports burnout post, a manga review that mentioned sports - presented with straight-faced ranks 1 through 5 as a "trend list". Every stage worked as designed. The sweep ran, the clusterer clustered, the scorer scored. The problem is structural: a top-N ranker ranks noise against noise when inputs are thin. Relative ranking cannot express "nothing here is good enough to show a user"; that requires an absolute gate the pipeline did not have.

## Guidance

The pattern shipped in PR #816 (merged, v3.14.0) has four parts. Apply all four when building any ranked-output surface.

### 1. Put an absolute floor in front of the relative ranker

Before a candidate is allowed to compete on relative score, it must clear an absolute evidence bar. The floor lives in `skills/last30days/scripts/lib/rerank.py`:

```python
FLOOR_MIN_ENGAGEMENT = 25.0
FLOOR_MIN_SOURCES = 2
FLOOR_SINGLE_SOURCE_ENGAGEMENT = 200.0


def passes_discovery_floor(
    *,
    source_count: int,
    engagement_total: float,
    item_count: int,
    junk_shape: bool = False,
    seed_source_count: int | None = None,
) -> bool:
    """Whether a discovery topic's evidence is strong enough to show a user.

    Below this floor the honest output is "nothing solid this window", not a
    ranked list of whatever survived the sweep.
    """
    if item_count <= 0 or engagement_total < FLOOR_MIN_ENGAGEMENT:
        return False
    if junk_shape:
        corroboration = seed_source_count if seed_source_count is not None else source_count
        return corroboration >= FLOOR_MIN_SOURCES
    if source_count >= FLOOR_MIN_SOURCES:
        return True
    return engagement_total >= FLOOR_SINGLE_SOURCE_ENGAGEMENT
```

(The `junk_shape` / `seed_source_count` branch landed in PR #852 - see section 2b.) The first check is the junk gate: `FLOOR_MIN_ENGAGEMENT = 25.0` means a 1-like tweet can never rank, no matter how empty the field is. The floor is judged per topic inside `run_discover()` (`skills/last30days/scripts/lib/pipeline.py`), before the topic is appended and before `topic_limit` is consulted - sub-floor evidence never enters the ranked list at all.

### 2. Make the clearing criteria composite: corroboration OR a genuinely strong spike

A single threshold is either too strict (kills real single-source stories) or too loose (lets corroborated-but-tiny noise through). The floor uses two independent ways to clear, after the junk gate:

- Cross-source corroboration: appearing on `FLOOR_MIN_SOURCES = 2` or more independent feeds clears with only modest engagement. Two feeds independently surfacing the same story is signal in itself.
- A strong single-source spike: `FLOOR_SINGLE_SOURCE_ENGAGEMENT = 200.0`. A 1,600-point single-source HN thread is a real story; a 30-upvote single-source meme is not.

The regression tests in `tests/test_discover_floor.py` pin both edges of this policy directly (`test_passes_discovery_floor_policy`): `floor(source_count=2, engagement_total=30, item_count=2)` clears, `floor(source_count=1, engagement_total=100, item_count=3)` does not, `floor(source_count=1, engagement_total=1600, item_count=1)` does.

### 2b. Count corroboration on the layer your own pipeline does not amplify

PR #852 added a stricter path for junk-shaped topics (help-me posts, beginner asks, musings - flagged by the stage-1 judge or the `topic_shape` heuristics): they lose the single-source engagement bypass entirely (a 226-comment "help me choose" thread is a busy support thread, not a story) and must clear `FLOOR_MIN_SOURCES` via corroboration alone.

The subtle half of that change is WHICH source count the corroboration check reads. The original design counted sources in the topic's enriched corpus - and the adversarial code review proved that check would never bind: the enrichment stage deliberately fans every nominated topic out to Reddit, X, YouTube, and the web, so a single-subreddit junk thread enriches into 4-6 "sources" of mentions of itself. A gate reading the post-fan-out count is checking that enrichment works, not that the topic is corroborated. The shipped gate counts distinct sources among the nomination's own seed listing items - what the river sweep actually found - which enrichment cannot inflate (`skills/last30days/scripts/lib/pipeline.py`, floor call site):

```python
junk_shape=nomination.junk_shape,
# Junk corroboration counts distinct SEED listing sources, never
# the enriched corpus - a successful enrichment pass is
# multi-source for almost any topic, so it would never bind.
seed_source_count=len({item.source for item in nomination.items}),
```

The two archetypes, side by side:

| Topic | Seed listing sources | Enriched corpus sources | Enriched-count gate (never binds) | Seed-count gate (shipped) |
|---|---|---|---|---|
| Single-subreddit help-me thread (junk shape) | 1 | 4-6 | passes | fails |
| Real story swept from Reddit AND Hacker News | 2 | 4-6 | passes | passes |

Generalized rule: when a gate requires corroboration or independence, measure it on the signal layer your own system does not amplify - corroboration is evidence only when the corroborating signals could have failed to appear. This applies to any "N independent confirmations" threshold downstream of your own search fan-out, enrichment, crawling, or retrieval expansion. It does NOT apply when the downstream layer is genuinely independent evidence your pipeline cannot manufacture (human review verdicts, third-party confirmations) - there, the enriched layer is exactly what to count.

Testing note: a unit test that feeds the gate's parameters directly cannot catch a never-binds design. At least one test must drive the full production path with the amplifier running and assert the gate still fires - `test_junk_corroboration_counts_seed_sources_not_enriched_corpus` in `tests/test_discover_floor.py` mocks enrichment to return a rich multi-source corpus and asserts the single-seed-source junk topic still fails, with the unit-level matrix in `test_passes_discovery_floor_junk_params` pinning that a high enriched `source_count` cannot rescue `seed_source_count=1`.

### 3. Make honest emptiness a first-class outcome, and name the nearest miss

When zero topics survive the floor, the pipeline does not error, does not pad, and does not lower the bar. `run_discover()` sets `outcome = "ok" if topics else "nothing-solid"` on the `DiscoveryReport`, and while filtering it remembers the highest-scoring sub-floor candidate as `weak_signal` so the empty result can still say what came closest:

```python
if not rerank.passes_discovery_floor(
    source_count=len(sources),
    engagement_total=native_total,
    item_count=len(evidence_items),
    junk_shape=nomination.junk_shape,
    # Junk corroboration counts distinct SEED listing sources, never
    # the enriched corpus - a successful enrichment pass is
    # multi-source for almost any topic, so it would never bind.
    seed_source_count=len({item.source for item in nomination.items}),
):
    # Sub-floor evidence never ranks; remember what came closest so a
    # nothing-solid brief can still name the strongest weak signal.
    # Junk-shaped failures are tracked separately: the brief prefers
    # the strongest NON-junk failure and names a junk one only when
    # every failure is junk-shaped (never empty when failures exist).
    if nomination.junk_shape:
        if junk_weak_signal is None or score > junk_weak_signal[0]:
            junk_weak_signal = (score, nomination.name)
    elif weak_signal is None or score > weak_signal[0]:
        weak_signal = (score, nomination.name)
    continue
```

The renderer (`render_discovery()` in `skills/last30days/scripts/lib/render.py`) presents this as a deliberate answer, not a failure:

```python
if report.outcome == "nothing-solid":
    lines.extend([
        "**Nothing solid this window.** No topic cleared the confidence "
        "floor - not enough cross-source confirmation or engagement to "
        "call anything a trend, and ranked noise would be worse than an "
        "honest empty result.",
        "",
    ])
    if report.weak_signal:
        lines.extend([
            f"Closest weak signal: {report.weak_signal} (sub-floor; "
            "single-source or too little engagement).",
            "",
        ])
```

Naming the weak signal matters: it tells the user the sweep actually ran and looked at real data, and it gives them a thread to pull ("closest weak signal: X" often suggests the narrower query that would work). There is also a soft middle state - if some topics clear but fewer than five, `run_discover()` emits a warning ("Fewer than five topic clusters cleared the confidence floor this window") rather than padding the list to the minimum.

### 4. Pin the failing corpus as a regression test

The exact junk corpus that produced the bad output is frozen in `test_junk_corpus_returns_nothing_solid_not_ranked_noise` in `tests/test_discover_floor.py`: five single-source 1-like tweets on the "sports" domain, asserting `report.topics == []`, `report.outcome == "nothing-solid"`, a non-None `weak_signal`, and a "confidence floor" warning. Sibling tests pin the other side so the floor cannot silently become a wall: a 1,084-point HN thread ranks (`test_strong_single_source_spike_clears_floor`), a mixed corpus keeps the strong story and drops the junk (`test_mixed_corpus_emits_only_floor_clearing_topics`), and enriched topics are judged on their enriched evidence, not their thin seed (`test_enriched_evidence_is_judged_not_seed_evidence`).

## Why This Matters

Trust in a ranked surface is asymmetric. Users cannot see the corpus behind the ranking; they can only judge the output. One junk trend list - five 1-like tweets dressed up with ranks, velocity scores, and momentum labels - teaches a user that the feature is garbage, and they generalize instantly: if it confidently ranked noise once, every future list is suspect. The presentation makes it worse, because ranking machinery (rank numbers, scores, "why spiking" prose) signals confidence the evidence never had.

Honest emptiness does the opposite. "Nothing solid this window" plus a named weak signal tells the user three things at once: the sweep ran, the bar is real, and here is roughly where the signal petered out. That preserves trust in every future non-empty list (topics that do appear are known to have cleared an absolute bar - the renderer even badges cross-source topics with "confirmed across N sources") and it invites a narrower, more productive follow-up query instead of a shrug. The empty state is a feature, not an apology.

## When to Apply

Any top-N surface over variable-quality inputs, where the input pool can be thin, noisy, or empty and the ranker will still dutifully order whatever it gets:

- Search and retrieval results ("no good matches" beats ten irrelevant hits)
- Trending / discovery feeds (this case)
- Recommendation lists ("nothing new worth recommending" beats recycled filler)
- Leaderboards and "top contributors" style rankings over sparse activity
- LLM-generated shortlists, digests, and "best of" summaries, where the model will fill N slots on request regardless of evidence quality

The tell that you need this pattern: the code computes `top N by score` with no branch that can produce an empty result from a non-empty input. If the only way to get an empty list is an empty corpus, the ranker cannot say "nothing here is good enough" - and someday the corpus will be five 1-like tweets.

Design notes when applying it:

- The floor must be absolute (engagement counts, source counts, item counts), not relative (percentile of the current pool). A relative floor degrades with the pool, which is exactly the failure being prevented.
- Prefer composite clearing criteria over a single threshold: independent corroboration OR a strong single-signal spike. Tune the constants to the domain and keep them named and commented as deliberately tunable (see the comment block above the constants in `skills/last30days/scripts/lib/rerank.py`).
- The empty state must name the nearest miss. A bare "no results" reads as breakage; "nothing cleared the bar, closest was X" reads as judgment.

## Examples

Before (v3.13.x behavior, reconstructed from the pinned regression corpus): `--discover "sports"` on a quiet window returned a ranked list built from this corpus -

```
x: "Wii Sports nostalgia thread about sports"        1 like, single source
x: "kids travel sports burnout post"                 1 like, single source
x: "motorsports vs stick and ball sports"            1 like, single source
x: "midjourney skateboarder sports prompt"           1 like, single source
x: "manga review mentioning sports matches"          1 like, single source
```

- rendered as topics 1-5 with velocity scores, because `topic_limit = max(5, min(10, limit))` took the top N unconditionally.

After (v3.14.0, PR #816): the same corpus produces `outcome="nothing-solid"`, an empty `topics` list, and the renderer's explicit empty state ("**Nothing solid this window.** No topic cleared the confidence floor ... Closest weak signal: ... (sub-floor; single-source or too little engagement)."). Verified live in the implementing session: `--discover "sports"` returned nothing-solid, while global trending (no domain) returned six real cross-source topics with community quotes - the floor removed the junk without starving the healthy path.

The strong-corpus side, from `tests/test_discover_floor.py`: a single 1,084-point, 577-comment HN thread clears the floor alone via the single-source-spike branch (`engagement_total >= FLOOR_SINGLE_SOURCE_ENGAGEMENT`) and ranks as a real topic; a 25-upvote single-source Reddit post stays buried. The decision logic, in full, is small enough to quote:

```python
if item_count <= 0 or engagement_total < FLOOR_MIN_ENGAGEMENT:
    return False
if junk_shape:
    corroboration = seed_source_count if seed_source_count is not None else source_count
    return corroboration >= FLOOR_MIN_SOURCES
if source_count >= FLOOR_MIN_SOURCES:
    return True
return engagement_total >= FLOOR_SINGLE_SOURCE_ENGAGEMENT
```

A handful of lines of gate, placed before the ranker, are the difference between a feature that fills five slots no matter what and one whose non-empty answers can be believed.

## Related

- [Entity grounding: full-phrase false demotion](../logic-errors/entity-grounding-full-phrase-false-demotion.md) - sibling ranking-quality fix in the same rerank module, opposite failure direction (false demotion of good signal vs. junk promotion). Together they bracket the two ways a ranker fails.
- [Search-quality eval: manual by default](../architecture/search-quality-eval-manual-by-default-2026-05-10.md) - how to validate a ranking-threshold change like this floor: manual eval run plus deterministic regression tests, not CI-gated quality scoring.
- [Non-daemon executor threads defeat wall-clock budgets](../logic-errors/non-daemon-executor-threads-defeat-wall-clock-budget.md) - sibling learning from the same PR #816 rebuild: the process-lifetime half (enrichment budget enforcement) vs this doc's ranking-quality half.
- [argparse optional-value flag dispatch](../conventions/argparse-optional-value-flag-dispatch-truthiness.md) - third lesson from the same PR #816: the CLI flag semantics that route into this feature.
- [PR #816](https://github.com/mvanhorn/last30days-skill/pull/816) - the discovery rebuild that introduced `passes_discovery_floor()` and the nothing-solid empty state (released v3.14.0).
- [PR #852](https://github.com/mvanhorn/last30days-skill/pull/852) - the discovery content pipeline that added the junk-shape branch and seed-source corroboration (section 2b).
