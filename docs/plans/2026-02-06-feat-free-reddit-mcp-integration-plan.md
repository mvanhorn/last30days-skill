---
title: "feat: Free Reddit via MCP — Remove OpenAI Key Requirement"
type: feat
date: 2026-02-06
---

# feat: Free Reddit via MCP — Remove OpenAI Key Requirement

## Overview

Replace the OpenAI Responses API (paid) for Reddit searching with a **free, zero-config Reddit MCP server**, eliminating the need for an `OPENAI_API_KEY` to get Reddit results in the last30days skill. This work happens in a **new forked repo** to keep the current release branch clean.

## Problem Statement

Today, the last30days skill requires an OpenAI API key (`OPENAI_API_KEY`) to search Reddit. This is the most expensive dependency in the stack — OpenAI charges per-call for the Responses API with web search. Users without an OpenAI key get zero Reddit results and fall back to WebSearch-only mode, which loses the engagement metrics (upvotes, comments) that make last30days uniquely valuable.

**Goal:** Make Reddit search completely free with no API key registration required.

## Research Findings

### Option Analysis

Five approaches were evaluated. Two stand out:

| Option | Free? | Search? | Engagement? | Setup | Notes |
|--------|-------|---------|-------------|-------|-------|
| **reddit-mcp-buddy** (Node.js) | Yes (anonymous mode) | Yes | Yes | One CLI command | 371 stars, 3-tier auth, most popular |
| **mcp-server-reddit** (Python) | Yes (redditwarp) | **No** | Yes | pip install | Recommended by ClaudeLog, but browse-only |
| **.json URL trick** (curl) | Yes | Yes | Yes | Zero | ~10 req/min, no dependencies |
| Reddit OAuth (PRAW) | Free but needs registration | Yes | Yes | App registration | 100 req/min, most reliable |
| RSS feeds | Yes | Basic | **No** | Zero | Least useful, no metrics |

### Top Contender: reddit-mcp-buddy

**GitHub:** [karanb192/reddit-mcp-buddy](https://github.com/karanb192/reddit-mcp-buddy)
- 371 stars, actively maintained (last update: Jan 29, 2026)
- **Anonymous mode** — zero credentials, ~10 req/min
- **MCP tools:** `search_reddit`, `browse_subreddit`, `get_post_details`, `user_analysis`, `reddit_explain`
- **Install:** `claude mcp add --transport stdio reddit-mcp-buddy -s user -- npx -y reddit-mcp-buddy`
- Returns full engagement metrics (score, num_comments, upvote_ratio)
- TypeScript/Node.js (npx, no Python dependency)

### Strong Alternative: .json URL trick

- Append `.json` to any Reddit URL → full JSON response
- Search: `https://www.reddit.com/search.json?q=TOPIC&sort=relevance&t=month&limit=100`
- Zero dependencies, zero auth, zero setup
- Returns same data as official API (score, num_comments, created_utc, upvote_ratio)
- ~10 req/min rate limit (sufficient for skill use)
- Can be called via `curl` or Python `urllib`

### Also Considered

- **Hawstein/mcp-server-reddit** (134 stars, Python, no auth) — **No search tool**, only browse subreddits. Cannot replace OpenAI's keyword search capability.
- **Arindam200/reddit-mcp** (262 stars, PRAW) — Has search but **requires Reddit OAuth credentials**. Not truly zero-config.
- **adhikasp/mcp-reddit** (348 stars) — Hot threads only, no search. Last updated Dec 2024.

## Proposed Solution

### Approach A: MCP-First with .json Fallback (Recommended)

Add a new module `mcp_reddit.py` that:

1. **Detects** if a Reddit MCP server is configured in the user's Claude Code session
2. **If MCP available:** Calls MCP `search_reddit` tool via the skill's `allowed-tools` (the skill already allows tools — MCP tools become available when configured)
3. **If no MCP:** Falls back to Reddit's `.json` URL endpoints via `urllib` (stdlib, no dependencies)
4. **Normalize** MCP/JSON results to the existing `RedditItem` schema
5. **Enrich** with `reddit_enrich.py` (already works — fetches real thread data)

This gives users three tiers of Reddit access:
- **Tier 1 (best):** Reddit MCP installed → full search + engagement via MCP tools
- **Tier 2 (good):** No MCP, no keys → `.json` URL search + enrichment
- **Tier 3 (existing):** OpenAI key present → existing `openai_reddit.py` still works (backward compat)

### Approach B: .json-Only (Simpler)

Skip MCP entirely. Add a `reddit_json.py` module that uses `https://www.reddit.com/search.json` directly. Simpler but misses the MCP ecosystem integration.

### Approach C: MCP-Only (Cleaner)

Require MCP setup. Simpler code but adds a user setup step (`claude mcp add ...`).

**Recommendation: Approach A** — MCP-first with .json fallback gives zero-config Reddit search for everyone while rewarding users who set up MCP with a better experience.

## Technical Approach

### Architecture

```
User invokes /last30days "topic"
  │
  ├─ env.py detects sources:
  │   ├─ MCP reddit available? → mcp_reddit.py
  │   ├─ No MCP, no key? → reddit_json.py (.json URL trick)
  │   └─ OPENAI_API_KEY set? → openai_reddit.py (existing, backward compat)
  │
  ├─ X search (Bird CLI / xAI — unchanged)
  │
  └─ Normalize → Score → Dedupe → Render (unchanged)
```

### New Files

#### `scripts/lib/reddit_json.py`
- Uses `urllib.request` (stdlib) to call Reddit's `.json` search endpoint
- URL: `https://www.reddit.com/search.json?q={topic}&sort=relevance&t=month&limit=100`
- Custom `User-Agent` header (required by Reddit)
- Parses response into the same format as `openai_reddit.py` output
- Handles pagination via `after` token if depth=deep (multiple requests)
- Rate limiting: 1 second delay between requests

#### `scripts/lib/mcp_reddit.py`
- Detects MCP availability (check if Reddit MCP tools exist in session)
- Formats MCP tool calls for `search_reddit` with topic + date filters
- Normalizes MCP response to match existing `RedditItem` schema
- Falls back to `reddit_json.py` if MCP unavailable

#### Modified Files

- **`scripts/lib/env.py`** — Add Reddit source detection: MCP → .json → OpenAI
- **`scripts/last30days.py`** — Route Reddit search through new priority chain
- **`scripts/lib/normalize.py`** — Add normalizer for `.json` endpoint response format
- **`SKILL.md`** — Document MCP setup as optional enhancement

### MCP Integration Design

The MCP approach has a subtle challenge: the last30days skill runs as a **Python subprocess** (`python3 last30days.py`), but MCP tools are available to **Claude's session**, not to subprocesses.

**Two paths to solve this:**

**Path 1: SKILL.md orchestration** — The SKILL.md workflow calls the MCP `search_reddit` tool directly (before or in parallel with the Python script), saves the MCP response to a temp file, and the Python script reads it:

```
SKILL.md workflow:
1. Call MCP search_reddit → save to /tmp/last30days_mcp_reddit.json
2. Call python3 last30days.py --reddit-from=/tmp/last30days_mcp_reddit.json --emit=compact
3. Python script reads pre-fetched Reddit data instead of calling OpenAI
```

**Path 2: .json only for subprocess** — The Python script uses `.json` URLs directly (no MCP needed in subprocess). MCP is a bonus for users who want Claude to also browse specific threads interactively.

**Recommendation: Path 2 for MVP, Path 1 as enhancement.** The `.json` approach is self-contained, testable, and doesn't require SKILL.md workflow changes. MCP can be layered on later.

### Source Priority Chain (updated env.py)

```python
def get_reddit_source(config: dict) -> str:
    """Returns: 'mcp', 'json', 'openai', or 'none'"""
    # 1. Check for pre-fetched MCP data (from SKILL.md)
    if os.path.exists(MCP_REDDIT_CACHE_PATH):
        return 'mcp'
    # 2. Always available — no key needed
    # (reddit .json endpoints are free)
    return 'json'
    # 3. OpenAI key present → legacy path
    # if config.get('OPENAI_API_KEY'):
    #     return 'openai'
```

For MVP, the `.json` path is **always available** so it becomes the default. OpenAI path remains as opt-in for users who want higher rate limits.

### Data Mapping: .json → RedditItem

Reddit `.json` search returns `data.children[].data` with:

```json
{
  "title": "Post title",
  "permalink": "/r/subreddit/comments/abc123/...",
  "subreddit": "ClaudeAI",
  "score": 42,
  "num_comments": 15,
  "upvote_ratio": 0.95,
  "created_utc": 1738800000,
  "selftext": "Post body...",
  "url": "https://...",
  "author": "username"
}
```

Maps cleanly to existing `RedditItem`:

| .json field | RedditItem field | Notes |
|-------------|------------------|-------|
| `title` | `title` | Direct |
| `permalink` | `url` | Prepend `https://www.reddit.com` |
| `subreddit` | `subreddit` | Direct |
| `score` | `engagement.score` | Direct |
| `num_comments` | `engagement.num_comments` | Direct |
| `upvote_ratio` | `engagement.upvote_ratio` | Direct |
| `created_utc` | `date` | Convert epoch → YYYY-MM-DD |
| `selftext` | (used for relevance) | AI relevance scoring needed |
| `author` | (not in current schema) | Ignore for now |

### Relevance Scoring Without AI

The current `openai_reddit.py` gets relevance scores **from OpenAI** (the AI judges how relevant each result is). With `.json` endpoints, we lose that AI relevance judgment.

**Options:**
1. **Keyword matching** — Score based on how many query terms appear in title + selftext. Simple but effective.
2. **TF-IDF-like** — Weight rarer query terms higher. More accurate but more code.
3. **Let Claude judge** — Pass results to Claude in SKILL.md and have Claude score relevance. Most accurate but changes the workflow.
4. **Skip relevance, rely on Reddit's sort** — Reddit's `sort=relevance` already ranks by relevance. Trust it and use position-based scoring (first result = 1.0, last = 0.5).

**Recommendation: Option 4 for MVP.** Reddit's search relevance ranking is already good. Use position-based relevance (1.0 → 0.5 linear decay over result set) combined with the existing engagement-based scoring. This requires zero external dependencies and no AI calls.

## Implementation Phases

### Phase 0: Fork Repository

- [ ] Create new repo `last30days-free-reddit` (or branch in private repo)
- [ ] `git clone $HOME/last30days-skill-private $HOME/last30days-free-reddit`
- [ ] Create feature branch: `feat/free-reddit`
- [ ] Verify all existing tests pass on the new branch

### Phase 1: reddit_json.py — Core .json Search

- [ ] Create `scripts/lib/reddit_json.py`
  - `search_reddit_json(topic: str, depth: str, date_from: str, date_to: str) -> list[dict]`
  - Build search URL with `q`, `sort=relevance`, `t=month`, `limit` (25/50/100 by depth)
  - Custom `User-Agent: last30days-skill/2.0 (by /u/last30days-bot)`
  - Parse `data.children[].data` → list of raw dicts
  - Handle pagination for deep mode (follow `after` token, max 3 pages)
  - Rate limit: `time.sleep(1.0)` between requests
  - Error handling: 429 (rate limit), 403 (blocked), network errors → return empty + error msg
- [ ] Create `tests/test_reddit_json.py`
  - Mock HTTP responses using fixture files
  - Test: basic search parsing, pagination, rate limit handling, error cases
- [ ] Create `fixtures/reddit_json_search_sample.json`
  - Real `.json` search response (sanitized)

### Phase 2: Normalize + Score .json Results

- [ ] Add `normalize_reddit_json()` to `scripts/lib/normalize.py`
  - Convert `.json` response format → `RedditItem` schema
  - `created_utc` (epoch) → `YYYY-MM-DD` string
  - `permalink` → full URL
  - Position-based relevance: `1.0 - (index / total * 0.5)` → range [1.0, 0.5]
  - Date confidence: `"high"` (Reddit provides exact timestamps)
- [ ] Update `scripts/lib/score.py` if needed
  - `.json` results already have real engagement metrics — existing scoring formula works as-is
  - No penalty needed (unlike WebSearch which lacks engagement)
- [ ] Add tests for normalization + scoring of `.json` data

### Phase 3: Integration into Orchestrator

- [ ] Update `scripts/lib/env.py`
  - Add `get_reddit_source(config) -> str` returning `'json'`, `'openai'`, or `'none'`
  - Priority: `.json` always available (default), `'openai'` if key present and user prefers
  - Add `REDDIT_SOURCE` config option: `auto` (default), `json`, `openai`
  - Update `get_available_sources()` to always include Reddit (since `.json` is free)
  - Update `get_missing_keys()` — Reddit no longer shows as "missing"
- [ ] Update `scripts/last30days.py`
  - Add `_search_reddit_json()` function alongside existing `_search_reddit()`
  - Route based on `get_reddit_source()`: json → `_search_reddit_json()`, openai → `_search_reddit()`
  - Skip `reddit_enrich.py` for `.json` results (already have real engagement metrics!)
  - Update progress/stats output to show source: "Reddit (free)" vs "Reddit (OpenAI)"
- [ ] Update SKILL.md promo messaging
  - Remove "Add OPENAI_API_KEY for Reddit" messaging
  - Instead: "Reddit search included free! Add OpenAI key for AI-enhanced relevance scoring."
- [ ] Integration tests: full pipeline with `.json` mock data

### Phase 4: Testing & Polish

- [ ] Run all existing tests — ensure backward compatibility
- [ ] Manual test: invoke `/last30days` with NO API keys → should get Reddit + WebSearch results
- [ ] Manual test: invoke with OPENAI_API_KEY → should still use OpenAI path (backward compat)
- [ ] Manual test: compare result quality — `.json` vs OpenAI for same topic
- [ ] Update README.md — document free Reddit access
- [ ] Update SPEC.md — document new source priority chain

### Phase 5 (Future): MCP Enhancement Layer

- [ ] Detect Reddit MCP in Claude's session
- [ ] SKILL.md pre-fetches via MCP `search_reddit` → saves to temp file
- [ ] Python script reads pre-fetched MCP data via `--reddit-from=` flag
- [ ] MCP results get AI-judged relevance (since Claude sees them)
- [ ] Better than `.json` position-based relevance

## Acceptance Criteria

### Functional
- [ ] `/last30days "any topic"` returns Reddit results with **zero API keys configured**
- [ ] Reddit results include real engagement metrics (score, comments, upvote_ratio)
- [ ] Results are properly scored, deduped, and rendered (same quality as OpenAI path)
- [ ] Existing OpenAI path still works when key is present
- [ ] Existing X search (Bird CLI / xAI) is unchanged
- [ ] Stats box shows correct source indicator ("Reddit (free)" or "Reddit (OpenAI)")

### Non-Functional
- [ ] No external Python packages (stdlib only — `urllib.request`, `json`, `time`)
- [ ] Respects Reddit rate limits (~10 req/min for unauthenticated)
- [ ] Graceful degradation if Reddit blocks requests (429/403 → empty results + error msg)
- [ ] All new code has unit tests with fixtures (no live API calls in tests)

### Quality Gates
- [ ] All existing tests pass (zero regressions)
- [ ] New tests cover: search parsing, normalization, scoring, error handling, pagination
- [ ] Manual smoke test passes with zero API keys

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Reddit blocks `.json` endpoints | Low | High | Fall back to OpenAI path; monitor for 429s |
| `.json` rate limit too restrictive | Medium | Medium | 1s delay between requests; cache results |
| Relevance quality lower without AI scoring | Medium | Medium | Reddit's `sort=relevance` is decent; can add keyword scoring later |
| Reddit changes `.json` response format | Low | Medium | Schema validation in normalize; fixture-based tests catch changes |
| Node.js MCP server dependency conflicts | N/A (Phase 5) | Low | MCP is optional enhancement, not required |

## Dependencies

- **None** — this feature uses only Python stdlib and Reddit's public `.json` endpoints
- Phase 5 (future) would add optional dependency on an MCP server

## Repository Setup

```bash
# Fork into new working repo
cp -r $HOME/last30days-skill-private $HOME/last30days-free-reddit
cd $HOME/last30days-free-reddit

# Create feature branch
git checkout -b feat/free-reddit

# Verify existing tests
python3 -m pytest tests/ -v
```

The new repo is disposable — once the feature is validated, changes merge back into `last30days-skill-private` via PR or cherry-pick.

## References

### Internal
- `scripts/lib/openai_reddit.py` — Current Reddit search (to be replaced/supplemented)
- `scripts/lib/env.py:get_available_sources()` — Source detection logic to update
- `scripts/lib/normalize.py` — Add `.json` normalizer alongside existing
- `scripts/lib/reddit_enrich.py` — May be skippable for `.json` results (already have engagement)
- `scripts/lib/schema.py:RedditItem` — Target schema (unchanged)

### External
- [Reddit .json search endpoint](https://www.reddit.com/search.json?q=test&sort=relevance&t=month&limit=25)
- [Simon Willison — Scraping Reddit via JSON API](https://til.simonwillison.net/reddit/scraping-reddit-json)
- [karanb192/reddit-mcp-buddy](https://github.com/karanb192/reddit-mcp-buddy) — Best MCP option for Phase 5
- [Hawstein/mcp-server-reddit](https://github.com/Hawstein/mcp-server-reddit) — ClaudeLog-recommended MCP (no search though)
- [Reddit API Rate Limits Guide](https://painonsocial.com/blog/reddit-api-rate-limits-guide)

### Research Sources
- last30days skill output — community recommendations for Reddit search tools
- GitHub search — 10+ Reddit MCP repos evaluated
- npm/PyPI registries — package availability confirmed
- ClaudeLog — [Reddit MCP reference](https://claudelog.com/claude-code-mcps/reddit-mcp/)
