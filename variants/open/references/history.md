# History & Knowledge Query

Query the accumulated findings database.

## Commands

| Command | Action |
|---|---|
| `history "topic"` | Show findings for a topic |
| `history "topic" --since=7d` | Findings from last N days |
| `history --search "query"` | Full-text search across all findings |
| `history --trending` | Topics with most recent activity |
| `history --stats` | Watchlist health dashboard |

## Topic History

```bash
python3 "${SKILL_ROOT}/scripts/store.py" query "TOPIC" [--since DAYS]
```

Display findings grouped by date (newest first):
```
**[Topic Name]** — N findings since [date]

[DATE]
- [Reddit] Title (score pts, N comments) — r/subreddit
- [X] Tweet text... (N likes) — @handle
- [YouTube] Video title (N views) — channel

[EARLIER DATE]
- ...
```

Mark updated findings (engagement changed since first seen).

## Full-Text Search

```bash
python3 "${SKILL_ROOT}/scripts/store.py" search "QUERY"
```

Uses FTS5 with BM25 ranking. Show results across all topics:
```
Search: "QUERY" — N results

1. [Reddit] Title — r/subreddit (topic: AI video)
   ...snippet with **highlighted** matches...

2. [X] Tweet text — @handle (topic: NVIDIA)
   ...snippet...
```

## Trending Topics

```bash
python3 "${SKILL_ROOT}/scripts/store.py" trending
```

Show topics ranked by recent activity:
```
Trending topics (last 7 days):

1. AI video tools — 12 new findings, engagement up 45%
2. NVIDIA news — 8 new findings, engagement steady
3. Claude Code — 3 new findings, engagement down 20%
```

## Stats Dashboard

```bash
python3 "${SKILL_ROOT}/scripts/store.py" stats
```

Display as a health dashboard:
```
Watchlist Health
- Active topics: N
- Total findings: N
- Database size: N KB

Research Runs (7 days)
- Successful: N
- Failed: N
- Cost: $X.XX

Source Breakdown
- Reddit: N findings
- X: N findings
- YouTube: N findings
- Web: N findings
```

## No Data Handling

If no findings exist:
```
No research history yet.

To start building knowledge:
1. Run research: /last30days "your topic"
2. Or add a watchlist topic: /last30days watch add "topic"
```
