# One-Shot Research Mode

Research ANY topic across Reddit, X, YouTube, Polymarket, and the web. Surface what people are actually discussing, recommending, betting on, and debating right now.

## Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about
2. **TARGET TOOL** (if specified): Where they'll use the prompts
3. **QUERY TYPE**:
   - **PROMPTING** — "X prompts", "prompting for X" → copy-paste prompts
   - **RECOMMENDATIONS** — "best X", "top X" → list of specific things
   - **NEWS** — "what's happening with X" → current events
   - **GENERAL** — anything else → broad understanding

**Do NOT ask about target tool before research.** Run research first, ask after.

**Display your parsing** before calling tools:

```
I'll research {TOPIC} across Reddit, X, YouTube, Polymarket, and the web.

Parsed intent:
- TOPIC = {TOPIC}
- TARGET_TOOL = {TARGET_TOOL or "unknown"}
- QUERY_TYPE = {QUERY_TYPE}

Research typically takes 2-8 minutes. Starting now.
```

---

## Step 0.5: Resolve X Handle (if topic could have an X account)

If TOPIC looks like it could have its own X account - **people, creators, brands, products, tools, companies** (e.g., "Dor Brothers", "Nano Banana Pro", "Seedance"), do ONE quick WebSearch:

```
WebSearch("{TOPIC} X twitter handle site:x.com")
```

Extract their X handle from results (look for `x.com/{handle}` URLs or "@handle" mentions). **Verify it's the real account, not a parody/fan page** - check for verified status, official website links, consistent naming. If found, pass as `--x-handle={handle}` (no @). Skip if TOPIC is a generic concept (not an entity), already has @, uses `--quick`, or no official account exists.

---

## Research Execution

**Step 1: Run the research script (FOREGROUND)**

```bash
python3 "${SKILL_ROOT}/scripts/last30days.py" "$ARGUMENTS" --emit=compact --store 2>&1  # Add --x-handle=HANDLE if resolved
```

Use a **timeout of 300000** (5 minutes). The `--store` flag persists findings for watchlist/briefing integration.

The script auto-detects: API keys, Bird CLI, yt-dlp, web search backends.

**Read the ENTIRE output.** It contains Reddit, X, YouTube, AND web sections.

---

**Step 2: WebSearch (supplement)**

After the script finishes, use your WebSearch tool for additional coverage.

Choose queries based on QUERY_TYPE:

- **RECOMMENDATIONS**: `best {TOPIC} recommendations`, `{TOPIC} list examples`
- **NEWS**: `{TOPIC} news 2026`, `{TOPIC} announcement update`
- **PROMPTING**: `{TOPIC} prompts examples 2026`, `{TOPIC} techniques tips`
- **GENERAL**: `{TOPIC} 2026`, `{TOPIC} discussion`

Rules:
- **USE THE USER'S EXACT TERMINOLOGY**
- EXCLUDE reddit.com, x.com, twitter.com (covered by script)
- Do NOT output "Sources:" list

---

## Synthesis

**Judge Agent rules:**
1. Weight Reddit/X HIGHER (engagement signals)
2. Weight YouTube HIGH (views + transcript content)
3. Weight web LOWER (no engagement data)
4. Identify cross-source patterns (strongest signals)
5. Extract top 3-5 actionable insights
6. **Prediction markets are high-signal when relevant** - real money on outcomes cuts through opinion. Prefer structural/long-term markets (championship > regular season, regime change > near-term deadline). When the topic is an outcome in a multi-outcome market, call out that specific outcome's odds and movement. Weave odds into narrative: "Polymarket has X at Y% (up/down Z%)"
7. **Treat YouTube transcript text as auto-generated captions, not ground truth.** If a quoted phrase looks odd, domain-incorrect, or suspiciously homophonic, do not build your synthesis around it unless the broader transcript or another source confirms it.

**Ground synthesis in ACTUAL research, not pre-existing knowledge.**

### Citation Rules

- Cite sparingly: 1-2 sources per topic
- Priority: @handles > r/subreddits > YouTube channels > web sources
- Use publication names, never raw URLs
- Lead with people, not publications

---

## Display Results

**1. "What I learned"** (format depends on QUERY_TYPE)

**If RECOMMENDATIONS** — show specific items with sources:
```
Most mentioned:

[Name] - {n}x mentions
Use Case: [what it does]
Sources: @handle1, r/sub, blog.com
```

**If PROMPTING/NEWS/GENERAL** — show synthesis:
```
What I learned:

**{Topic 1}** — [1-2 sentences, per @handle or r/sub]

KEY PATTERNS:
1. [Pattern] — per @handle
2. [Pattern] — per r/sub
```

**2. Stats box** (calculate from actual output):
```
---
All agents reported back!
|- Reddit: {N} threads | {N} upvotes | {N} comments
|- X: {N} posts | {N} likes | {N} reposts
|- YouTube: {N} videos | {N} views | {N} with transcripts
|- Polymarket: {N} markets | {summary of up to 5 most relevant market odds}
|- Web: {N} pages (supplementary)
|- Top voices: @{handle1}, @{handle2} | r/{sub1}, r/{sub2}
---
```

**CRITICAL: Omit any source line that returned 0 results.** Do NOT show "0 threads", "0 stories", "0 markets", or "(no results this cycle)". If a source found nothing, DELETE that line entirely.

**3. Invitation** with 2-3 specific follow-up suggestions based on research.

---

## Follow-Up

After research, you are an **EXPERT** on this topic.

- **QUESTION** → Answer from research (no new searches)
- **GO DEEPER** → Elaborate from findings
- **CREATE/PROMPT** → Write ONE prompt using research insights
- **Different topic** → Run new research

When writing prompts, match the FORMAT the research recommends (JSON, structured, etc.).
