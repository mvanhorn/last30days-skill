---
name: last30days
description: Research what people actually say about any topic in the last 30 days. Use for recent sentiment, launches, controversies, product/community buzz, founders/projects, repos, markets, or current discourse questions.
---

# last30days

Use this skill for current-discourse research, not timeless encyclopedia answers. The job is to find what changed recently, who is saying it, and what the disagreement or consensus actually is.

## Fast workflow

1. Parse intent: topic, timeframe, geography/community, and whether Johannes wants sentiment, facts, actors, or recommendations.
2. Resolve entities before searching:
   - people -> likely X/GitHub/company handles
   - products/projects -> official site, repo, launch page, docs, forum/community
   - companies -> official name, aliases, competitors
3. Search broadly, then narrow:
   - web/news/blogs for factual changes
   - X/HN/Reddit/forums for discourse and objections
   - GitHub for OSS/project momentum when relevant
4. Synthesize with source separation:
   - facts vs claims/opinions
   - repeated criticisms, genuine novelty, and marketing smell
   - primary sources and practitioner discussion above engagement bait
5. Report tersely using the output contract below.

## Output contract

```markdown
# Last 30 days: <topic>

## Verdict
<3-6 bullets: what changed, whether it matters, confidence>

## What people are saying
- <theme> — evidence/source
- <theme> — evidence/source

## Evidence
- <source/link>: <why it matters>

## Caveats
- <missing/weak evidence, conflicts, likely bias>

## My read
<opinionated synthesis and next action>
```

## Quality rules

- Do not keyword-search blindly. Resolve names/handles first when ambiguity could poison results.
- Do not average vibes. Weight expert/practitioner evidence above engagement bait.
- Mention when the last-30-days window is too quiet or evidence is stale.
- Keep Johannes's tolerance for bullshit low: say when the discourse is mostly marketing, drama, or recycled takes.

## Deep reference

For the old exhaustive workflow, report-law wording, query traps, and template variants, read `SKILL-original.md` only when this compact workflow is insufficient.
