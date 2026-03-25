---
title: "feat: Add visible query parsing display before research starts"
type: feat
date: 2026-02-06
---

# feat: Add Visible Query Parsing Display

## Overview

The last30days skill parses user intent (TOPIC, QUERY_TYPE, TARGET_TOOL) internally but never shows the user what it understood. The agent jumps straight from the user's `/last30days kanye west` into running tools with a generic "I'll start the research script and web searches in parallel."

Users expect to see a reformulation of their query — confirming what the agent understood before it starts searching. This builds trust and lets users course-correct before waiting for results.

## Problem Statement

Current behavior:
```
User: /last30days kanye west

Agent: I'll start the research script and web searches in parallel.
[immediately runs bash + WebSearch]
```

Expected behavior:
```
User: /last30days kanye west

Agent: 🔍 **kanye west** · News
Searching Reddit, X, and the web for the latest on kanye west...

[then runs bash + WebSearch]
```

The "Parse User Intent" section in SKILL.md tells the agent to store variables internally but never instructs it to **display** them.

## Proposed Solution

Add an explicit "Display your parsing" instruction between the "Parse User Intent" section and "Research Execution" section in SKILL.md. One new block of text — no code changes, no script changes.

## Acceptance Criteria

- [ ] Agent displays parsed TOPIC and QUERY_TYPE before running any tools
- [ ] Display is concise (1-2 lines, not a verbose block)
- [ ] Agent still runs script + WebSearch in parallel after displaying
- [ ] No changes to Python scripts — SKILL.md only

## Implementation

### SKILL.md Change

**File:** `$HOME/last30days-skill-private/SKILL.md`

After the "Store these variables" block (line ~38) and before "Research Execution" (line ~42), add:

```markdown
**DISPLAY your parsing to the user.** Before running any tools, output a single line:

🔍 **{TOPIC}** · {QUERY_TYPE}
Searching Reddit, X, and the web for {natural language description of what you'll look for}...

Example outputs:
- 🔍 **kanye west** · News — Searching Reddit, X, and the web for the latest kanye west news and discussions...
- 🔍 **best MCP servers** · Recommendations — Searching Reddit, X, and the web for the most recommended MCP servers...
- 🔍 **nano banana pro prompting** · Prompting — Searching Reddit, X, and the web for nano banana pro prompting techniques and tips...
- 🔍 **open claw** · General — Searching Reddit, X, and the web for what people are saying about open claw...

If TARGET_TOOL is known, mention it: "...for nano banana pro prompting techniques to use in ChatGPT..."

This text MUST appear before you call any tools. It confirms to the user that you understood their request.
```

### Sync

After editing SKILL.md:
```bash
cp $HOME/last30days-skill-private/SKILL.md ~/.claude/skills/last30days/SKILL.md
```

## Test Plan

Run in a NEW Claude Code session:
1. `/last30days kanye west` — should display: 🔍 **kanye west** · News
2. `/last30days best MCP servers` — should display: 🔍 **best MCP servers** · Recommendations
3. `/last30days nano banana pro prompting for ChatGPT` — should display with tool mention

## Files to Modify

| File | Change |
|------|--------|
| `SKILL.md` | Add display instruction between Parse User Intent and Research Execution |
