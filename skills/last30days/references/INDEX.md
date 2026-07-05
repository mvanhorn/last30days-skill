# last30days Skill: References Index

> **Navigation guide** for the 8 reference files extracted from SKILL.md.
> The core workflow is in `SKILL.md` (66KB). These references contain detailed guides
> loaded on demand via the agent's skill loading mechanism.

## Runtime Workflow (loaded per step)

| Step | File | Size | When to load |
|------|------|------|-------------|
| Step 0 | [`setup-wizard.md`](setup-wizard.md) | 25KB | First-run only; skip if `SETUP_COMPLETE=true` |
| Step 0.45 | [`query-quality-preflight.md`](query-quality-preflight.md) | 7KB | When topic looks like a keyword trap |
| Step 0.5 | [`pre-flight-resolution.md`](pre-flight-resolution.md) | 11KB | For named-entity topics (handles/repos) |
| Step 0.55 | [`pre-research-intelligence.md`](pre-research-intelligence.md) | 16KB | For named-entity topics (communities/handles) |
| Step 0.75 | [`query-plan-generation.md`](query-plan-generation.md) | 5KB | For named-entity topics (JSON query plan) |
| Synthesis | [`synthesis-template.md`](synthesis-template.md) | 43KB | When synthesizing engine results |
| Optional | [`save-html-brief.md`](save-html-brief.md) | 15KB | When emitting HTML briefs |

## Reference Documentation

| File | Size | Content |
|------|------|---------|
| [`laws-and-examples.md`](laws-and-examples.md) | 22KB | LAW 1-8 detailed explanations, violation history, worked examples |

## Total

- **8 files, ~143KB** extracted from the original 192KB SKILL.md
- **Core SKILL.md** is now **66KB** (under Hermes 100KB limit)
- **Original SKILL.md** was **192KB** (2088 lines)

## Hermes Note

On Hermes Agent, load reference files via `skill_view(name='last30days', file_path='references/<name>')`.
The core SKILL.md contains short summaries and pointers to each reference file.
