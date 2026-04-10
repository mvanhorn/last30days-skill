# Morning Briefing

Synthesize accumulated findings into a formatted briefing.

## Commands

| Command | Action |
|---|---|
| `briefing` | Generate today's briefing |
| `briefing --weekly` | Weekly digest with trends |
| `briefing --since YYYY-MM-DD` | Briefing since specific date |

## Generate Briefing

```bash
python3 "${SKILL_ROOT}/scripts/briefing.py" generate [--weekly] [--since DATE]
```

The script returns JSON with per-topic findings, staleness info, and cost data.

## Staleness Check

Before synthesizing, check each topic's freshness:
- **Fresh** (< 12h): show normally
- **Aging** (12-36h): note when last run was
- **Stale** (> 36h): warn user, suggest running `watch run-one "topic"`

## Daily Briefing Format

```
Good morning! Here's your research briefing for [DATE].

TL;DR: [One sentence about the top finding across all topics]

---

**[Topic 1]** (N new findings)
Top signal: [Highest engagement finding with source]
Also trending: [2nd finding], [3rd finding]

**[Topic 2]** (N new findings)
Top signal: [Highest engagement finding]
Also trending: [2nd finding]

---
Cost: $X.XX / $Y.YY budget | N topics active | N findings today
```

## Weekly Digest Format

```
Weekly digest for week of [DATE]:

**[Topic 1]**
This week: N findings (up/down X% from last week)
Trending up: [engagement increasing]
Key voices: @handle1, r/sub1

**[Topic 2]**
This week: N findings
Trending down: [engagement decreasing]
```

## Synthesis Rules

- Lead with people, not publications
- 3-5 topics max per briefing
- 2-3 findings per topic
- Include cost/budget footer
- Note any failed or stale topics

## No Data Handling

If no topics or no findings:
```
No briefing data available.

To get started:
1. Add a topic: /last30days watch add "your topic"
2. Run research: /last30days watch run-all
3. Generate briefing: /last30days briefing
```
