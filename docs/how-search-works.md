# How Reddit & X Search Work in last30days

## Architecture Overview

```
User: /last30days "kanye west"
          ↓
    ┌─────┴─────┐
    ↓           ↓           (concurrent via ThreadPoolExecutor)
 [REDDIT]    [X/TWITTER]
    ↓           ↓
 OpenAI     Bundled Bird or
 API        xAI API
    ↓           ↓
 Parse       Parse
    ↓           ↓
 Enrich      ───┘
 (fetch         ↓
  actual     [MERGE]
  upvotes)      ↓
    ↓        [NORMALIZE → FILTER → SCORE → DEDUPE]
    └───────────↓
          [OUTPUT to SKILL.md agent]
```

Both searches run **in parallel** using Python's `ThreadPoolExecutor(max_workers=2)`.

---

## Reddit Search

### How it works

Reddit search uses the **OpenAI Responses API** with the `web_search` tool, domain-filtered to `reddit.com` only.

**API Call:**
```
POST https://api.openai.com/v1/responses
Authorization: Bearer {OPENAI_API_KEY}
```

**Payload:**
```json
{
  "model": "gpt-5.2",
  "tools": [{
    "type": "web_search",
    "filters": { "allowed_domains": ["reddit.com"] }
  }],
  "input": "Search Reddit for threads about {topic}..."
}
```

The prompt asks the model to:
1. Extract core subject (strip noise words like "best", "tips", "top")
2. Search 3 patterns: `"{topic} site:reddit.com"`, `"reddit {topic}"`, `"{topic} reddit"`
3. Return JSON with `title`, `url`, `subreddit`, `date`, `relevance`
4. URLs must contain `/r/` AND `/comments/` (real threads only)

**Model fallback chain:** `gpt-5.2 → gpt-5.1 → gpt-5 → gpt-4.1 → gpt-4o → gpt-4o-mini`
Triggers on HTTP 400/403 with access error keywords.

### Enrichment (the secret sauce)

After search, each thread gets **enriched** by hitting Reddit's free JSON API:

```
GET https://reddit.com/r/{sub}/comments/{id}/{slug}/.json
```

No API key needed. This returns the actual thread data:

| Data Point | Source |
|---|---|
| Upvotes (score) | Reddit JSON API |
| Comment count | Reddit JSON API |
| Upvote ratio | Reddit JSON API |
| Top 10 comments (text + score) | Reddit JSON API |
| 7 key comment insights | Extracted via heuristics |
| Actual post date | `created_utc` timestamp |

**This is why Reddit results have real engagement metrics** — the enrichment step fetches actual upvote/comment data, not AI estimates.

### Depth settings

| Depth | Threads requested | Timeout |
|---|---|---|
| `--quick` | 15-25 | 90s |
| default | 30-50 | 120s |
| `--deep` | 70-100 | 180s |

---

## X/Twitter Search

X search has **two backends** — the skill auto-detects which to use.

### Priority: Bundled Bird (env auth) → xAI API (paid)

```python
if node_available and AUTH_TOKEN and CT0:
    use bundled Bird    # Free, popup-free, env-authenticated
elif XAI_API_KEY:
    use xAI API         # Paid, uses grok-4-1-fast
else:
    skip X entirely     # No X results
```

### Backend 1: xAI API

**API Call:**
```
POST https://api.x.ai/v1/responses
Authorization: Bearer {XAI_API_KEY}
```

**Payload:**
```json
{
  "model": "grok-4-1-fast",
  "tools": [{ "type": "x_search" }],
  "input": "Search X for posts about {topic} from {from_date} to {to_date}..."
}
```

The prompt asks grok to return JSON with:
- `text`, `url`, `author_handle`, `date`
- `engagement`: `{ likes, reposts, replies, quotes }`
- `why_relevant`, `relevance` score

**Engagement data comes from grok's x_search tool** - it has direct access to X's data.

### Backend 2: Bundled Bird client (free alternative)

The repo vendors a search-only subset of Bird's Twitter GraphQL client and shells out to it with Node.js. No global `bird` install is required. The Python wrapper passes `AUTH_TOKEN` and `CT0` via env, which keeps normal local runs headless and avoids browser-cookie prompts.

**Bundled Bird returns raw X API data** - likes, reposts, replies are real engagement metrics from X's API, not estimates.

| Metric | Bundled Bird | xAI API |
|---|---|---|
| Post text | Real | Real |
| Likes/reposts | Real (X API) | Real (x_search tool) |
| Replies/quotes | Real | Real |
| Author handle | Real | Real |
| Relevance score | Default 0.7 (re-ranked by relevance.py) | AI-assessed 0.0-1.0 |

### Depth settings

| Depth | xAI posts | Bundled Bird results | xAI timeout | Bird timeout |
|---|---|---|---|---|
| `--quick` | 8-12 | 12 | 90s | 30s |
| default | 20-30 | 30 | 120s | 45s |
| `--deep` | 40-60 | 60 | 180s | 60s |

---

## Post-Processing (both sources)

After both searches complete:

1. **Normalize** — consistent formatting, timezone handling
2. **Date filter** — hard filter to requested date range
3. **Score** — relevance scoring (engagement-weighted)
4. **Sort** — highest scores first
5. **Deduplicate** — remove duplicate URLs
6. **Fallback** — if all items filtered out, keep top 3 by relevance

---

## Error Handling

| Layer | Strategy |
|---|---|
| HTTP requests | 3 retries with exponential backoff (1s → 2s → 3s) |
| Model access errors | Automatic fallback to next model in chain |
| Reddit enrichment | Per-item try/catch; keeps unenriched item on failure |
| X source detection | Silent fallback from Bird → xAI → skip |
| Overall pipeline | Errors stored as `reddit_error`/`x_error`, shown to user |

---

## Key Files

| File | Purpose |
|---|---|
| `skills/last30days/scripts/last30days.py` | Main CLI entry point |
| `skills/last30days/scripts/lib/pipeline.py` | Multi-source retrieval orchestration |
| `skills/last30days/scripts/lib/reddit_public.py` | Reddit public JSON search |
| `skills/last30days/scripts/lib/reddit_enrich.py` | Fetch real engagement data from Reddit JSON API |
| `skills/last30days/scripts/lib/xai_x.py` | X search via xAI API |
| `skills/last30days/scripts/lib/bird_x.py` | X search via bundled Bird client (free) |
| `skills/last30days/scripts/lib/providers.py` | Reasoning provider and model selection |
| `skills/last30days/scripts/lib/env.py` | API key loading, source detection |
| `skills/last30days/scripts/lib/http.py` | HTTP transport with retries |
| `skills/last30days/scripts/lib/relevance.py` | Query matching and relevance scoring |
| `skills/last30days/scripts/lib/dedupe.py` | URL-based deduplication |
