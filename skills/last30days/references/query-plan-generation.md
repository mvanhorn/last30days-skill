# Step 0.75: Generate Query Plan

> Extracted from SKILL.md for Hermes 100KB compatibility.

## Step 0.75: Generate Query Plan (YOU are the planner)

> **PLATFORM GATE:** If you skipped Step 0.55 because WebSearch is unavailable, **also skip this step.** The Python engine will plan internally (enhanced by `--auto-resolve` if a web search backend is configured). Jump to Research Execution.

**If you have WebSearch and reasoning capability, YOU generate the query plan.** The Python script receives your plan via `--plan` and skips its internal planner entirely. This produces better results because you have full context about the topic.

**Generate a JSON query plan for the topic.** Think about:
1. What is the user's intent? (breaking_news, product, comparison, how_to, opinion, prediction, factual, concept)
2. What subqueries would find the best content across different platforms?
3. What related angles should be searched at lower weight?

**Output a JSON plan with this shape:**

```json
{
  "intent": "breaking_news",
  "freshness_mode": "strict_recent",
  "cluster_mode": "story",
  "subqueries": [
    {
      "label": "primary",
      "search_query": "kanye west",
      "ranking_query": "What notable events involving Kanye West happened in the last 30 days?",
      "sources": ["reddit", "x", "hackernews", "youtube", "tiktok", "instagram"],
      "weight": 1.0
    },
    {
      "label": "album",
      "search_query": "kanye west bully album",
      "ranking_query": "How was Kanye West's BULLY album received?",
      "sources": ["youtube", "reddit", "tiktok", "instagram"],
      "weight": 0.8
    },
    {
      "label": "reactions",
      "search_query": "kanye west bully review reaction",
      "ranking_query": "What are the reviews and reactions to Kanye West's BULLY?",
      "sources": ["youtube", "tiktok", "reddit"],
      "weight": 0.6
    }
  ]
}
```

**Rules for your plan:**
- Emit 1 to 4 subqueries (more for complex/multi-faceted topics, fewer for simple ones)
- **CRITICAL: Your PRIMARY subquery MUST include ALL of these sources: reddit, x, youtube, tiktok, instagram, hackernews, polymarket.** Never omit reddit (highest-signal discussion) or youtube (unique transcripts + official content). Secondary subqueries can target specific platforms.
- `search_query` should be concise and keyword-heavy - match how content is TITLED on platforms
- `ranking_query` should read like a natural language question
- **DISAMBIGUATION (mandatory for collision-prone names — the #1 cause of off-topic noise).** Anchor the `search_query` with the disambiguating context you resolved in Step 0.5 / 0.55 — the entity's company, role, or domain — when the topic name (a) is a common word or has non-product meanings ("Loom" = weaving tool, "Tella" = soccer player), OR (b) is a PERSON whose name collides with other public figures or common words. Apply the anchor to **EVERY subquery, not just the primary**, and mirror it in the `ranking_query`. Anchor on a SPECIFIC named entity (a company/product/firm), not a generic domain word. Examples: `"kevin rose digg founder"` not `"kevin rose"` (collides with Kevin Warsh / Leon Rose / Kevin Hart); `"lan xuezhao basis set ventures"` not `"lan xuezhao"` (collides with "Lanzhou" food, cdrama edits); `"trevin chow compound engineering"` not `"trevin chow"` (collides with Trevin Wax / Trevin Brown); `"tella screen recording"` not `"tella"`. The `ranking_query` carries the same anchor: `"ranking_query": "What has Kevin Rose, founder of Digg, been doing in the last 30 days?"`, not a bare `"...Kevin Rose..."`. A bare collision-prone name as a subquery is the named 2026-06-17 failure mode — "Kevin Rose" returned 55 items with ~0 about the actual founder until every subquery was anchored to "Digg founder". When the name is globally unambiguous (Kanye West, Nvidia, Peter Steinberger/OpenClaw), no anchor is needed.
- **For comparison queries**, each subquery should include the product category: "tella screen recorder review" not just "tella review", "loom video tool pricing" not just "loom pricing".
- NEVER include temporal phrases in search_query: no "last 30 days", "recent", month names, year numbers
- NEVER include meta-research phrases: no "news", "updates", "public appearances"
- Preserve exact proper nouns and entity strings from the topic
- For comparison ("X vs Y"): create per-entity subqueries at weight 0.8 + a head-to-head subquery at weight 1.0
- For product queries: route to YouTube (reviews), Reddit (discussions), TikTok (demos)
- For predictions: include Polymarket in sources
- For how_to: prioritize YouTube (tutorials) and Reddit (guides)
- Primary subquery weight = 1.0, secondary = 0.6-0.8, peripheral = 0.3-0.5

**Available sources (include ALL in primary subquery):** reddit, x, youtube, tiktok, instagram, hackernews, polymarket. Optional: bluesky, truthsocial, threads, pinterest, grounding (web search - only if user has Brave/Exa/Serper key), digg (Digg clusters - only if `digg-pp-cli` is on PATH)

**Intent → freshness_mode mapping:**
- breaking_news, prediction → `strict_recent`
- concept, how_to → `evergreen_ok`
- everything else → `balanced_recent`

**Intent → cluster_mode mapping:**
- breaking_news → `story`
- comparison, opinion → `debate`
- prediction → `market`
- how_to → `workflow`
- everything else → `none`

Store your plan as `QUERY_PLAN_JSON` - you'll pass it to the script in the next step.

---
