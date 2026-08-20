# Concepts

Shared vocabulary for `last30days-skill`. Terms here have a precise project-specific meaning — distinct enough from their general technical sense that a new contributor would need them defined to follow conversations, PR descriptions, or the SKILL.md contract.

## The package

### Skill

A self-contained agent-instructions package consisting of a `SKILL.md` prose contract plus a sibling `scripts/` directory containing the executable code the SKILL.md invokes. The package conforms to the [Agent Skills](https://agentskills.io) open format and installs across every major harness (Claude Code, Codex, Cursor, GitHub Copilot, Gemini CLI, and 50+ others) via `npx skills add`, harness-native plugin installers, or per-harness skill directories. A Skill is the unit of distribution; the Skill is the product.

### Engine

The Python script (`scripts/last30days.py`) the Skill's SKILL.md invokes to do the actual research work. The Engine and SKILL.md have a contract: SKILL.md tells the model which flags to pass (`--plan`, `--competitors-plan`, `--x-handle`, `--subreddits`, `--emit=compact`, etc.), and the Engine produces a specific output shape (badge line, ranked evidence clusters, emoji-tree footer) that the model is contractually required to pass through. The Engine is implementation; the SKILL.md prose is the agent-facing surface.

### Harness

The agent runtime that loads Skills and invokes them on the user's behalf. Claude Code is the most common Harness for this Skill but not the only one — Codex, Cursor, GitHub Copilot, Gemini CLI, and the rest of the Agent Skills ecosystem also count. "Multi-harness" describes a Skill that works correctly across every Harness it installs into; features written without multi-harness awareness (e.g., engine flags with no SKILL.md integration, or paths hardcoded to one Harness's install layout) regress on Harnesses other than the one they were tested against.

## Research pipeline

### Primary entity

The brand or proper-noun core of a research topic — the topic with its Intent modifier stripped. It is what the research is *about*, as distinct from how the user phrased the search.

### Intent modifier

A trailing word or phrase in a topic that expresses what the user wants to know rather than what the topic is ("review", "use cases", "pricing"). Stripped when deriving the Primary entity.

### Entity grounding

The check that a candidate item plausibly mentions the Primary entity before final ranking. Grounding keys on the head token (first word) of the Primary entity rather than the full phrase — trailing words are usually search descriptors, so requiring them falsely demotes on-entity items.

An item that fails grounding receives a decisive entity-miss demotion, designed so engagement cannot rescue off-entity content. Because the demotion is decisive, the grounding bar is deliberately conservative: its failure modes degrade toward "no penalty," never toward burying on-entity signal.

### Keyless path

The research flow available with no API keys: source data is gathered by scraping and RSS rather than authenticated APIs, and ranking falls back to local scoring instead of LLM-based reranking. This is the free tier of the Skill; lexical quality safeguards like Entity grounding matter most here, because no LLM is available to judge relevance semantically.

### Comment-enrichment slots

The small, depth-dependent budget of Reddit posts whose comments get fetched in the Keyless path. Slot selection is relevance-aware: posts that pass Entity grounding claim slots first, so the budget is not spent on high-engagement posts that final ranking will demote anyway.

## Discovery

### Discovery

The topic-less research mode: instead of researching a named topic, it finds what is worth researching. On a reasoning-model host it runs as a three-leg host-judged protocol: leg 1 sweeps the river listings and writes a nominations bundle, the host judges every Nomination (name, junk, worthiness) into a judgments file, leg 2 resumes from the bundle and runs the Enrichment passes, and leg 3 applies host-written content angles and renders the brief. Headless/cron runs keep the one-shot form - same sweep and enrichment, deterministic heuristics in place of the judge, no angles. Either way every surviving topic must clear the Confidence floor before it is shown. Global Discovery (no domain given) sweeps every river feed's own hot list with no keyword gate; domain Discovery scopes and keyword-gates the sweep.

### Nomination

A named candidate topic produced by Discovery's listing sweep: clustered items from the river feeds, given a short searchable name plus a Junk shape flag and a content-worthiness score that blends into its seed rank. On protocol runs the hosting model judges all three via the judgments file - the engine's deterministic heuristics only fill rows the host left absent; on headless one-shot runs deterministic distillation supplies the name and junk flag and no worthiness signal exists. A Nomination is only a candidate - its blended seed rank decides which topics deserve an Enrichment pass and the display order of survivors; the Confidence floor judgment and the displayed velocity score are computed from the enriched evidence, never the seed score. The Nomination's name doubles as its Enrichment pass search query and its research handoff, so naming happens before enrichment, never at render time.

### Handoff checkpoint

The persisted state that lets Discovery's protocol pause for host judgment and resume in a later invocation: the nominations bundle (the full judge pool with its seed evidence, written by leg 1, awaiting the host's judgments) and the pending report (the enriched, floored, ranked round written by leg 2, awaiting the host's angles). A checkpoint is identity-bound - every host-written file must echo the checkpoint's bundle id, and a mismatch is a fix-the-id-and-retry error, never a redo of the expensive leg - and time-bound by its own TTL so a stale round is rejected rather than resumed. Checkpoints also carry provenance the resume legs enforce: mock and real state never cross, degraded sweep coverage survives into later legs instead of reading as clean, and an explicitly scoped store is the only place its checkpoints are looked for. Structurally empty checkpoint state is treated as corruption and fails closed - it never becomes an authoritative-looking empty result.

### Enrichment pass

A full research-pipeline run executed on one Nomination's topic name during Discovery. This is what gives a trend card the whole multi-source corpus (community comments, prediction markets, keyword-driven sources that have no hot-list of their own) instead of thin listing evidence. Enrichment passes run in parallel against a wall-clock budget; a pass that fails or outruns the budget downgrades its topic to nomination-only evidence, never fails the run.

### Confidence floor

The absolute evidence bar every Discovery topic must clear before it may rank: an engagement junk-gate first, then either independent cross-source corroboration or a genuinely strong single-source spike. Topics with a Junk shape get a stricter read: the single-source spike bypass is off, and their corroboration is counted against the seed listing sources the sweep actually found - never the enriched corpus, because an Enrichment pass makes almost any topic look multi-source. The floor is absolute, not relative to the current pool - a relative bar would degrade with the pool, which is the failure it exists to prevent. Its thresholds are deliberately tunable; the behavior contract is only that sub-floor evidence never ranks.

### Nothing-solid

The honest empty outcome of a Discovery run in which zero topics cleared the Confidence floor. A first-class result, not an error: the run reports that nothing in the window was strong enough to call a trend, and names the closest sub-floor candidate (the weak signal, preferring a non-junk-shaped one) so the user knows where the signal petered out. Rendering junk instead of Nothing-solid is the named failure this outcome replaced.

### Junk shape

A classification applied to a Nomination whose leading item reads as a help-me question, beginner ask, or personal musing rather than a story - the post shapes that engagement alone cannot distinguish from news. Its force depends on who flagged it: a host junk verdict is authoritative and excludes the Nomination from Enrichment passes outright (it can still appear as the weak signal in a Nothing-solid brief), while a heuristic junk shape on a row the host never judged only removes the Confidence floor's single-source bypass, so that topic surfaces solely with independent seed-source corroboration.

### Topic queue

The persistent memory of what Discovery has surfaced: each surfaced topic is recorded per research store, so later runs can annotate repeats ("surfaced Nth time") and the user can mark stories Covered. On by default for every real Discovery run, with an engine toggle to disable; mock runs never write it.

Identity in the queue is annotate-only: a new topic name that closely matches an earlier row (exact normalized match, else entity overlap) annotates the rendered card but never merges or rewrites rows - a false match costs one noisy line, never a hidden story. Queue annotations always describe the state before the current run, and a failed queue write degrades to a warning; it must never destroy a finished run's output.

### Covered

The user-set status on a Topic queue row meaning "I already produced content for this story." Set by marking a topic covered by its exact name; surfaced is the only other status. A resurfacing never un-covers a row, and a new name that fuzzily matches a Covered row is born Covered - so the mark survives the judge (now the hosting model) renaming the same story across runs instead of silently re-pitching it.

## Flagged ambiguities

- "Enrichment" is used for two distinct things: Comment-enrichment slots (fetching comments for already-ranked Reddit posts in the Keyless path) and Discovery's Enrichment pass (a full research run per Nomination). Context disambiguates; prefer the full term when writing.
