# last30days — X search + funny + laziness: consolidated bug inventory

**Date:** 2026-06-17
**Engine version under test:** 3.4.0 (current `main`, commit fce934b)
**Source evidence:** five live `/last30days` debug sessions, captured per-session in `sessions/`.
**Every line/behavior claim below was re-verified against current `main`, not taken on faith from the transcripts.**

This is the grounding document for the fix plan. It examines each problem separately,
states the verified root cause with file/line, and rates fleet impact (the fixes ship to
100k+ users — most of whom do NOT have working browser-cookie X auth, so keyless paths and
honesty matter most at scale).

---

## A. X search bugs

### A1 — `from:{handle}` AND-bug (the headline X bug)  ·  fleet impact: HIGH (every person/entity topic with X auth)
**Symptom:** Passing `--x-handle=mvanhorn` (or xuezhao, steipete, …) returns ~0 of the person's own tweets. The "X" column fills with unrelated keyword collisions instead.
**Verified root cause:** `bird_x.search_handles._search_one_handle` builds
`from:{handle} {core_topic} since:{from_date}` whenever `core_topic` is truthy
(`bird_x.py` ~373). X search is literal AND — so it only matches the person's tweets that *also contain the topic words* (usually their own name), which they never tweet. The unfiltered `from:{handle} since:` branch only fires when `topic is None`, and the caller (`pipeline.py:861`) always passes a truthy `topic`. So the real-timeline path is effectively unreachable.
**Proven fix exists in-code:** running `from:{handle} since:` (no AND) returned 40 real tweets in-session.
**User requirement (2026-06-17):** X must surface tweets **FROM** the person (engagement-weighted) — this is the `from:` lane, fixed to drop the topic-AND for person/handle topics.

### A2 — No mention/“about” lane at all  ·  fleet impact: HIGH
**Symptom:** Tweets *mentioning* the person (`@handle`) or *to* them never get collected as a category.
**Verified root cause:** the pipeline only runs `from:` (author) searches and keyword subqueries. There is no `@handle` / `to:handle` mention query anywhere (`bird_x.search_handles` does `from:` only; no other call site builds a mention query).
**User requirement (2026-06-17):** X must ALSO surface tweets **TO/ABOUT** the person (engagement-weighted) — a new mention lane, weighted by likes/reposts, deduped against the `from:` lane.

### A3 — Handle search is silent on success  ·  fleet impact: MED (observability; caused 3 wrong diagnoses in-session)
**Verified root cause:** `_search_one_handle` only `_log()`s on timeout / OSError / non-zero / invalid-JSON. A successful OR empty handle search emits zero log lines (`bird_x.py` ~382-405). The only `[Bird] Searching:` lines in task logs are the Phase-1 keyword subqueries, so the `from:` search looks like it never ran.
**Fix:** log the query + result count on success, like the keyword path.

### A4 — `--diagnose` false-green for X  ·  fleet impact: HIGH (fleet-wide trust bug)
**Symptom:** `--diagnose` reports `bird_authenticated: true` / `bird_username: "env AUTH_TOKEN"` even when X is effectively returning nothing, and labels the source `env AUTH_TOKEN` when the real lane is live browser-cookie extraction (`_AUTH_TOKEN_SOURCE: browser`).
**Verified root cause:** `pipeline.diagnose` reads `env.get_x_source_status` → `bird_status["authenticated"]` (`env.py:864`), a static credential-presence check, not a runtime probe.
**Fix:** diagnose should do a real 1-tweet probe and report the true auth lane (browser vs env vs keychain). At scale most users have NO working X auth; the footer/coverage must say "X: 0 — no working auth" honestly instead of showing green.

### A5 — Off-topic X pollution  ·  **CORRECTED at execution (2026-06-17)**
**Symptom:** "compound interest / compound nevus" junk filled the X column in the Matt-vs-Trevin run even though bird was failing the whole run.
**Original (transcript) hypothesis — DISPROVEN against current code:** the debug session blamed Digg's X-post enrichment side channel. False: Digg-enriched X posts live in `metadata["posts"]` and render ONLY as Digg-cluster quotes via `render._digg_posts_for` (returns `[]` for non-digg sources). Nothing adds them to `items_by_source["x"]`. Digg does NOT pollute the X column — a red herring.
**Actual verified root cause:** the X-column "compound" junk came from the A6 strongest-token fallback querying a bare generic token, whose results ARE parsed into X items. **A6 is the real fix; the planned "entity-filter Digg X posts" unit (U1) was dropped as a non-occurring path.**
**Residual minor note:** Digg-cluster X-post *quotes* could be tangentially off-topic, but they render as Digg quotes (not X items) and come from already-topic-matched clusters — low-severity, defer.

### A6 — Strongest-token fallback collapses to a bare generic token  ·  fleet impact: MED
**Verified root cause:** the last-chance retry picks `strongest = max(candidates, key=len)` then queries `f"{strongest} since:{from_date}"` (`bird_x.py:339-341`). For "trevin chow ai agents compound" it picks the longest token — `compound` — and dumps generic "compound" results into the shared X pool.
**Fix:** don't collapse to a single generic token; keep an entity anchor (handle/name) in the retry, or drop the subquery rather than over-broaden.

### A7 — Name-collision / disambiguation gap  ·  fleet impact: MED-HIGH (every mid-profile person)
**Symptom:** "Kevin Rose" pulled Kevin Warsh (Fed chair), Leon Rose (Knicks), Kevin Durant, Kevin Hart — 55 items, ~zero on-topic. "Lan Xuezhao" pulled Lanzhou noodle + cdrama edits.
**Root cause:** bare-name keyword subqueries are too collision-prone for mid-profile people; the engine has no disambiguation anchor (handle/domain/context) baked into the subqueries by default.
**Note:** larger relevance problem than the deterministic A1-A6 bugs. Candidate for a follow-up scope rather than the first PR. **Call out for the plan.**

### NOT bugs — stop re-chasing (verified):
- **Bird is not broken.** Vendored `scripts/lib/vendor/bird-search/` v0.8.0, MIT, zero deps; works against live cookies. Peter deprecating the public `@steipete/bird` package is irrelevant — it's vendored.
- **`@steipete/sweet-cookie` is optional**, never a required dep (the "not installed" line is from the optional browser-cookie lane).
- **The parser is correct.** Metrics nest under `item["engagement"]` and `item["date"]`, not top-level — earlier "metrics = None" was a debug print-key mistake.

---

## B. Funny things not showing up

### B1 — Best Takes renders empty in normal use (the structural root cause)  ·  fleet impact: HIGH
**Symptom:** Across the Kanye and Steinberger runs, no `## Best Takes` section appeared; the funniest lines ("Is anyone surprised? It's called TurkiYe", "I bet one of his kids will be a bully") never reached the synthesis.
**Verified root cause (two compounding):**
1. `rerank.score_fun` LLM-scores only when a reasoning `provider` exists in the **engine subprocess** (`pipeline.py:534`, `provider=reasoning_provider`). In normal `/last30days` usage the engine subprocess has NO paid reasoning provider (the hosting model is the planner but can't be called back into the subprocess), so fun scores come from the heuristic fallback (~38). `_render_best_takes` requires `fun_score >= _BEST_TAKE_FUNNY_FLOOR (40)` AND effective ≥ threshold (70 at medium) — so Best Takes returns `[]`. The vote-weighting shipped in #592 is moot without LLM fun scoring.
2. `_render_candidate` (the compact EVIDENCE block) renders top comments ONLY for the representative items of the top-`cluster_limit` (8) clusters (`render.py:166-171, 1208`). Funny comments on lower-ranked or non-representative items are never in the synthesis block at all.

### B2 — The fun judgment is in the wrong place  ·  design finding
The hosting model (Claude) is an excellent fun judge and *does* have an API. The engine subprocess is the one that can't LLM-score. So the fix direction is to **move fun SELECTION to the hosting model**: have the engine surface a comment-rich, vote-scored "Best Takes candidates" / "Top Community Comments" block (across more than the top-8 representative items) inside the EVIDENCE envelope, and have SKILL.md make weaving 2-3 of the funniest a hard gate. Vote scores (the #592 signal) become the ranking input the model selects from. **Soft vs hard fork — call out for the plan:** engine-surfaces-candidates + model-selects (structural) vs. just lowering the Best Takes heuristic floor (weak).

---

## C. The AI is lazy / not reading what it finds

### C1 — Compact stdout treated as the whole dataset  ·  fleet impact: HIGH (every run)
**Symptom:** The model synthesized a news-shaped report off the compact EVIDENCE block and never opened the saved raw `.md` until prodded — missing comments, the subject's own quotes, a BULLY DELUXE release two days out, a Dutch court win, an allegation.
**Root cause (behavioral + structural):** the compact EVIDENCE block is a lossy index (top-8 clusters, representative items, truncated). The richer per-source comment/quote layer lives deeper in the saved raw file, which the model treats as optional. SKILL.md's "weave the funniest takes" / PRE-PRESENT SELF-CHECK exist but are skipped as formalities.
**Fix direction:** (a) structural — get the high-value layer (top comments with votes, the subject's own posts) INTO the synthesis-facing block so the model can't miss it; (b) behavioral — turn the self-check into an enforced gate (e.g. require ≥2 verbatim attributed quotes, lead with most-recent dated/upcoming event, test the thesis against highest-engagement items).

### C2 — Fabricated / reconstructed citation URL  ·  fleet impact: HIGH (correctness; a wrong link looks authoritative)
**Symptom:** In the Steinberger run the model linked @OtsileKole to a status ID that belonged to a different account — reconstructed from memory instead of copied from the raw file (a LAW 8 violation).
**Fix:** every URL in output copied verbatim from the raw data; plain-text fallback if not found; never reconstruct a status ID.

### C3 — Meta-commentary about tooling leaks into the deliverable  ·  fleet impact: MED (output quality)
**Symptom:** "the social-listening engine struck out … 'Kevin Rose' collided with Kevin Warsh …" — narrating the engine's own failure inside the user-facing report.
**Fix:** the synthesis presents what's true about the subject and quietly drops junk; engine-health notes belong in the footer/diagnostics, not the prose.

---

## D. Formatting integrity (user constraint #4)

### D1 — Cascading-repeat mangling of the "What I learned" block  ·  fleet impact: unknown
**Symptom:** In the Kanye run the synthesis block repeated the same paragraphs many times with progressively growing left-indentation — a badly corrupted render.
**Status:** likely a model-output / terminal-streaming artifact rather than an engine-code bug (it's in the model's emitted prose, not the engine stdout). **Needs reproduction before claiming an engine cause — open question.**
**Hard constraint on ALL fixes:** changes to the engine's emitted output (especially adding a comments/Best-Takes-candidates block to the EVIDENCE envelope) must NOT break the envelope markers, the PASS-THROUGH FOOTER, the badge, or the `What I learned:` contract. Format is already fragile; every output-touching unit must preserve it and be diffed against a real run.

---

## Priority for the fleet (100k+ users, most without working X auth)

1. **A5 Digg X-pollution** + **A6 fallback drift** — hits everyone, no X auth needed.
2. **B1/B2 funny (Best Takes dark) + C1 laziness** — the product's headline value ("funniest comments on the whole internet"), every run, keyless.
3. **A4 diagnose false-green** — fleet-wide honesty.
4. **A1 from:-AND-bug + A2 mention lane** — the explicit FROM+ABOUT-with-weight requirement; deterministic, high correctness win for the X-auth subset.
5. **A3 silent logging** — cheap observability, do alongside.
6. **A7 disambiguation** + **D1 formatting cascade** — likely follow-up scope (bigger/uncertain).
