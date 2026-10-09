---
title: "A trailing Sources: list comes from the web-search tool's citation contract; isolate the searches instead of out-ranking the contract"
date: "2026-10-09"
category: "design-patterns"
module: "skills/last30days/references/research-runbook"
problem_type: "design_pattern"
component: "synthesis_output"
severity: "medium"
applies_when:
  - "A /last30days report ends with a Sources:, References:, or Citations: list after the footer or invitation"
  - "Someone proposes stronger 'never add Sources' wording or an 'LAW 1 overrides the tool' rule"
---

# Trailing Sources list: isolate host web search, don't argue with the tool

## Symptom

Some runs end with a `Sources:` bullet list between the footer and the invitation, or with a `Sources: a, b, c` line after the invitation. Other runs of the same version end cleanly. Four Opus 5.5 runs on 2026-10-09 (v3.27.1) split two and two, and every one of them used host WebSearch.

## Cause

Claude Code's WebSearch tells whichever agent called it to finish with a sources section, and every result carries a reminder: "You MUST include the sources above in your response." Two layers then gave the model opposite answers:

- The skill (since #1220) defers to host and tool contracts. That is correct, and its tests forbid override wording such as `LAW 1 overrides`.
- The engine's end-of-output boundary text (`render._render_canonical_boundary`) still said LAW 1 overrides the WebSearch reminder.

When instructions conflict like this, the result changes from run to run. The pre-#1220 hard rule had already leaked despite being repeated in four places, and the engine boundary text was itself added after a leak. Wording alone has not fixed this.

## Fix

Remove the trigger instead of out-ranking it. The research runbook's "Isolated web search" block sends every host web search (Step 0.5 and 0.55 resolution, and the Step 2 supplements) through a subagent that returns titles, verbatim URLs, and findings as data. The contract binds the subagent's own reply. The main agent cites the URLs inline and counts the web pages in the invitation's link line. LAW 1 and the engine boundary now give that same default and claim no authority over a tool.

On hosts without subagents the searches run directly, with the same inline-citation default. That path is best effort.

## Guardrails

- `tests/test_skill_instruction_priority.py` fails if any contract document licenses a "separate `Sources:` block", if the engine boundary text claims an override, or if a runbook step that issues a web search doesn't point at the isolated-search block.
- Do not fix a recurrence by adding override wording. Look for a web search that bypasses the isolated-search block.
