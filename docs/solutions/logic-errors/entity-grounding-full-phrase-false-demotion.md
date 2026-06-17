---
title: Keyless rerank entity grounding required full multi-word phrase, falsely demoting on-entity items
date: 2026-06-09
category: docs/solutions/logic-errors
module: lib/rerank
problem_type: logic_error
component: search_ranking
severity: high
symptoms:
  - on-entity, high-engagement items that name the brand but omit the trailing descriptor of a multi-word query are demoted in keyless/fallback rerank results
  - observed case is a 323-point HN thread about Stripe scoring 0 on a "Stripe payments" query
  - the entity-miss demotion lands twice (ENTITY_MISS_PENALTY on rerank_score plus a secondary final_score penalty), so a false miss guarantees burial regardless of engagement
  - reddit keyless comment-enrichment slot selection skips the same on-entity threads via an independently duplicated full-phrase check in _slot_priority
root_cause: logic_error
resolution_type: code_fix
related_components:
  - reddit_keyless
  - comment_enrichment
tags:
  - entity-grounding
  - rerank
  - keyless-fallback
  - multi-word-entity
  - substring-match
  - false-demotion
  - reddit-keyless
  - duplicated-logic
---

# Keyless rerank entity grounding required full multi-word phrase, falsely demoting on-entity items

## Problem

The keyless/fallback rerank path's entity-grounding demotion required the FULL multi-word primary-entity phrase as a contiguous substring of the candidate's text (`primary_entity.lower() not in haystack`), so on-entity items that omitted a trailing search descriptor were falsely flagged as entity misses and buried by a deliberately decisive double penalty.

## Symptoms

- On a "Stripe payments" query, a 323-point HN thread titled "Stripe is friendly to 'friendly fraud'" was demoted to score 0 — purely because its text never contained the literal phrase "stripe payments" (the trailing word "payments" was missing).
- The burial is guaranteed by design, not incidental: a flagged entity miss takes −25 `ENTITY_MISS_PENALTY` on `rerank_score` in `_fallback_tuple`, PLUS `ENTITY_MISS_FINAL_PENALTY` applied directly in `_final_score` (added 2026-04-19 after engagement + freshness drowned the diluted penalty). A false positive on the check means confirmed-good signal cannot recover.
- The same over-strict check had been independently re-implemented in `reddit_keyless._slot_priority` (keyless Reddit comment-enrichment slot selection), so scarce comment slots were also steered away from head-token-only posts.

## What Didn't Work

- **Naively relaxing the check** — the full-phrase check existed for a real reason: on 2026-04-19 an off-topic video with zero brand mentions ranked #2 on a Hermes query (documented in the `ENTITY_MISS_FINAL_PENALTY` comment in `skills/last30days/scripts/lib/rerank.py`). Any fix had to keep that demotion firing.
- **Word-boundary matching** — rejected; it re-introduces over-demotion on plurals/possessives/compounds ("stripes", "Stripe's").
- **Graded penalty** (full-phrase = 0, head-only = half, none = full) — rejected; it half-punishes items that are 100% about the entity. Lexical coverage is not topical degree.
- **Any-token grounding** — rejected; "payments" alone would ground completely generic posts.
- **Distinctiveness gate for generic heads** — rejected as complexity to patch a failure mode that is already a safe no-op (see Why This Works).
- **Trusting the docstring** — `reddit_keyless._slot_priority`'s docstring claimed to "mirror rerank's demotion signal," but its inline reimplementation (`entity in _post_text(post).lower()`) had silently drifted from being a mirror into being a second copy of the bug. It was found only by a code-reuse review, not by tests.

## Solution

Ground on the **head token** of the primary entity instead of the full phrase, via one shared helper used by both paths.

**Site 1 — new helper in `skills/last30days/scripts/lib/rerank.py`:**

```python
def _entity_grounded(haystack: str, primary_entity: str) -> bool:
    tokens = primary_entity.lower().split()
    if not tokens:
        return True
    return tokens[0] in haystack
```

`_fallback_tuple` switches from the inline phrase check to the helper:

```python
# before
if haystack.strip() and primary_entity.lower() not in haystack:
# after
if haystack.strip() and not _entity_grounded(haystack, primary_entity):
```

**Site 2 — secondary penalty in `_final_score`: no code change needed.** It keys off the explanation string set by site 1, so it inherits the fix automatically:

```python
if candidate.explanation and "entity-miss" in candidate.explanation:
    base = max(0.0, base - ENTITY_MISS_FINAL_PENALTY)
```

**Site 3 — `skills/last30days/scripts/lib/reddit_keyless.py` `_slot_priority`:** replace the drifted reimplementation with a call to the shared helper:

```python
# before
return entity in _post_text(post).lower()
# after
return rerank._entity_grounded(_post_text(post).lower(), entity)
```

Tests: `tests/test_rerank_v3.py` gained `test_fallback_grounds_on_head_token_not_full_phrase` (the Stripe regression) and `test_fallback_still_demotes_when_head_token_absent_on_multiword_topic` (guards the 2026-04-19 behavior). `tests/test_reddit_keyless.py`'s two old-contract tests were rewritten as `test_slot_priority_grounds_on_head_token_not_full_phrase` and `test_intent_modifier_topic_prioritizes_head_token_match`.

## Why This Works

- **Root cause:** trailing tokens of a multi-word query ("payments" in "Stripe payments") are usually category descriptors the user/planner appended for search, not part of the entity name. Requiring the whole phrase conflates "doesn't repeat my search phrasing" with "isn't about my entity." The brand head token alone is sufficient grounding; items that never name the brand at all still miss the head token and stay demoted — so the original 2026-04-19 fix keeps firing.
- **Asymmetry argument:** the demotion is engineered to be decisive (double penalty across `rerank_score` and `final_score`), so a false entity-miss is fatal-by-design, while a false grounding merely defers the item to normal relevance/freshness/quality ranking. When the punishment is capital, the conviction standard should be conservative.
- **Substring (not word-boundary) is deliberate:** it catches plurals/possessives/compounds ("stripes", "Stripe's"). Degenerate short heads ("X", "Go", "C") make the check vacuously true, which merely **disables** the penalty — reverting to the pre-grounding baseline — rather than burying good items. Every failure mode of this rule degrades toward "no penalty," never toward "bury good signal."
- **Accepted, bounded limitation:** head-collision with a different famous entity ("Hermes Agent" → a "Hermes Birkin" thread now escapes demotion). This is lexically unfixable — any token rule strong enough to kill the collision re-kills the Stripe case; the discriminator is semantic. The LLM rerank path (which receives the full phrase as prompt guidance and judges semantically) covers this when API keys exist; the keyless path accepts the bounded risk.

## Prevention

- **Shared helper as single source of truth:** when one module's behavior must "mirror" another's signal, it must *call* the same function, not re-implement the check. The `reddit_keyless._slot_priority` drift happened precisely because the mirror was a copy. The fix wires it to `rerank._entity_grounded`, and the docstring now states this explicitly: "keying on the same head token keeps the two paths from diverging."
- **Docstrings record deliberate trade-offs:** `_entity_grounded`'s docstring documents WHY head-token (not phrase), why substring (not word-boundary), and the safe-failure direction. Future readers see the rejected alternatives were considered, not overlooked — and won't "tighten" the check into a regression.
- **Both directions pinned by named tests:**
  - `tests/test_rerank_v3.py::test_fallback_grounds_on_head_token_not_full_phrase` — false-demotion regression (the Stripe HN thread must not be flagged).
  - `tests/test_rerank_v3.py::test_fallback_still_demotes_when_head_token_absent_on_multiword_topic` — the fix must not neuter the demotion (guards the 2026-04-19 off-topic-video incident).
  - `tests/test_reddit_keyless.py::test_slot_priority_grounds_on_head_token_not_full_phrase` and `test_intent_modifier_topic_prioritizes_head_token_match` — the mirrored path asserts the same contract.
- **Audit tests when changing a contract:** tests that encode the old behavior as correct must be rewritten to the new contract, not worked around — the two old `test_reddit_keyless.py` tests would have silently re-blessed the bug.
- **For decisive penalties, route through one flag:** the `_final_score` backstop keys off `"entity-miss" in candidate.explanation` rather than re-running the check — so there was exactly one site to fix and the second penalty inherited it for free. Prefer this signal-propagation pattern over duplicating predicate logic at each penalty site.

## Related Issues

- [PR #484](https://github.com/mvanhorn/last30days-skill/pull/484) — "fix(reddit): relevance-aware comment-enrichment slot selection in keyless path" — introduced the `_slot_priority` mirror this fix reroutes through the shared helper.
- [PR #457](https://github.com/mvanhorn/last30days-skill/pull/457) — "fix(reddit): restore free path via keyless RSS + shreddit scrape" — established the keyless Reddit path.
- [PR #488](https://github.com/mvanhorn/last30days-skill/pull/488) (open) — "fix(reddit): relevance floor + relevance-first ranking" — external PR touching the same ranking surface; coordinate before merging both.
- [Issue #468](https://github.com/mvanhorn/last30days-skill/issues/468) (open) — relevance scoring over-pruning on-topic YouTube items; same symptom family in a different source.
- [../architecture/search-quality-eval-manual-by-default-2026-05-10.md](../architecture/search-quality-eval-manual-by-default-2026-05-10.md) — how to validate ranking/grounding changes like this one (manual eval, not CI-gated).
- [../workflow-issues/release-consistency-test-cascade-2026-05-16.md](../workflow-issues/release-consistency-test-cascade-2026-05-16.md) — sibling prevention pattern: lockstep artifacts drift unless mechanically unified.
