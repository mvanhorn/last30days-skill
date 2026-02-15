---
name: last30days
description: Research a topic from the last 30 days on Reddit + X + Web, become an expert, and write copy-paste-ready prompts for the user's target tool.
argument-hint: 'nano banana pro prompts, NVIDIA news, best AI video tools'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
---

# last30days: Research Any Topic from the Last 30 Days

Research ANY topic across Reddit, X, and the web. Surface what people are actually discussing, recommending, and debating right now.

## Help / Usage

If the user's input is `--help`, `-h`, or `help`, display the following and STOP (do not run research):

```
/last30days <topic> [options]

Research any topic across Reddit, X, and the web. Surface what people
are actually discussing, recommending, and debating right now.

Options:
  --search=SOURCES  Comma-separated list of sources to search.
                    Valid: reddit, x, web, hn, yt, ph
                    Example: --search reddit,hn,yt
                    Default: searches all sources with configured API keys

  --days=N          Number of days to look back (1-30, default: 30)
                    Example: --days 7

  --quick           Faster research with fewer sources per platform
  --deep            Comprehensive research with more sources per platform

Examples:
  /last30days Claude Code prompts
  /last30days best AI video tools --search reddit,hn
  /last30days NVIDIA news --days 7
  /last30days web frameworks --deep --search reddit,x,hn,yt
```

---

## CRITICAL: Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about (e.g., "web app mockups", "Claude Code skills", "image generation")
2. **TARGET TOOL** (if specified): Where they'll use the prompts (e.g., "Nano Banana Pro", "ChatGPT", "Midjourney")
3. **QUERY TYPE**: What kind of research they want:
   - **PROMPTING** - "X prompts", "prompting for X", "X best practices" → User wants to learn techniques and get copy-paste prompts
   - **RECOMMENDATIONS** - "best X", "top X", "what X should I use", "recommended X" → User wants a LIST of specific things
   - **NEWS** - "what's happening with X", "X news", "latest on X" → User wants current events/updates
   - **GENERAL** - anything else → User wants broad understanding of the topic

Common patterns:
- `[topic] for [tool]` → "web mockups for Nano Banana Pro" → TOOL IS SPECIFIED
- `[topic] prompts for [tool]` → "UI design prompts for Midjourney" → TOOL IS SPECIFIED
- Just `[topic]` → "iOS design mockups" → TOOL NOT SPECIFIED, that's OK
- "best [topic]" or "top [topic]" → QUERY_TYPE = RECOMMENDATIONS
- "what are the best [topic]" → QUERY_TYPE = RECOMMENDATIONS

**IMPORTANT: Do NOT ask about target tool before research.**
- If tool is specified in the query, use it
- If tool is NOT specified, run research first, then ask AFTER showing results

**Store these variables:**
- `TOPIC = [extracted topic]`
- `TARGET_TOOL = [extracted tool, or "unknown" if not specified]`
- `QUERY_TYPE = [RECOMMENDATIONS | NEWS | HOW-TO | GENERAL]`

**DISPLAY your parsing to the user.** Before running any tools, output:

```
I'll research {TOPIC} across Reddit, X, and the web to find what's been discussed in the last 30 days.

Parsed intent:
- TOPIC = {TOPIC}
- TARGET_TOOL = {TARGET_TOOL or "unknown"}
- QUERY_TYPE = {QUERY_TYPE}

Research typically takes 2-8 minutes (niche topics take longer). Starting now.
```

If TARGET_TOOL is known, mention it in the intro: "...to find {QUERY_TYPE}-style content for use in {TARGET_TOOL}."

This text MUST appear before you call any tools. It confirms to the user that you understood their request.

---

## Research Execution

**Step 1a: Select Product Hunt topic slugs**

Always attempt this step unless the user used `--search` without `ph` in the list.

Product Hunt's API requires specific topic slugs (like "artificial-intelligence", "developer-tools") — it does not support free-text search. You are the intelligence layer that maps the user's topic to the right slugs.

1. Get the full topic list:
```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/last30days}/scripts/last30days.py" --list-ph-topics 2>/dev/null
```

2. If that command succeeds (PH_ACCESS_TOKEN is configured), pick the 1-5 most relevant slugs for the user's TOPIC. Use your judgment — for example:
   - "AI video tools" → `artificial-intelligence`, `video`, `developer-tools`
   - "productivity apps" → `productivity`, `task-management`
   - "crypto news" → `cryptocurrency`, `web3`, `blockchain`

3. If it fails or outputs nothing, skip PH — the token isn't configured.

**Step 1b: Run the research script**

If you selected PH slugs, include `--ph-slugs`:
```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/last30days}/scripts/last30days.py" "$ARGUMENTS" --ph-slugs=slug1,slug2,slug3 --emit=compact 2>&1
```

Otherwise, run without it:
```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/last30days}/scripts/last30days.py" "$ARGUMENTS" --emit=compact 2>&1
```

The script will automatically:
- Detect available API keys
- Run Reddit/X searches if keys exist
- Signal if WebSearch is needed

---

## STEP 2: DO WEBSEARCH WHILE SCRIPT RUNS

**IMPORTANT:** If the user specified `--search=SOURCES` and did NOT include `web` in the list, SKIP this step entirely. Only do WebSearch when:
- The user explicitly included `web` in `--search` (e.g. `--search reddit,web`)
- The user did NOT use `--search` at all (default behavior)
- The script output contains "### WEBSEARCH REQUIRED ###"

The script auto-detects sources (Bird CLI, API keys, etc). While waiting for it, do WebSearch.

For **ALL default modes** (when `--search` is not specified), do WebSearch to supplement (or provide all data in web-only mode).

Choose search queries based on QUERY_TYPE:

**If RECOMMENDATIONS** ("best X", "top X", "what X should I use"):
- Search for: `best {TOPIC} recommendations`
- Search for: `{TOPIC} list examples`
- Search for: `most popular {TOPIC}`
- Goal: Find SPECIFIC NAMES of things, not generic advice

**If NEWS** ("what's happening with X", "X news"):
- Search for: `{TOPIC} news 2026`
- Search for: `{TOPIC} announcement update`
- Goal: Find current events and recent developments

**If PROMPTING** ("X prompts", "prompting for X"):
- Search for: `{TOPIC} prompts examples 2026`
- Search for: `{TOPIC} techniques tips`
- Goal: Find prompting techniques and examples to create copy-paste prompts

**If GENERAL** (default):
- Search for: `{TOPIC} 2026`
- Search for: `{TOPIC} discussion`
- Goal: Find what people are actually saying

For ALL query types:
- **USE THE USER'S EXACT TERMINOLOGY** - don't substitute or add tech names based on your knowledge
- EXCLUDE reddit.com, x.com, twitter.com (covered by script)
- INCLUDE: blogs, tutorials, docs, news, GitHub repos
- **DO NOT output "Sources:" list** - this is noise, we'll show stats at the end

**Options** (passed through from user's command):
- `--days=N` → Look back N days instead of 30 (e.g., `--days=7` for weekly roundup)
- `--quick` → Faster, fewer sources (8-12 each)
- (default) → Balanced (20-30 each)
- `--deep` → Comprehensive (50-70 Reddit, 40-60 X)

---

## Judge Agent: Synthesize All Sources

**After all searches complete, internally synthesize (don't display stats yet):**

The Judge Agent must:
1. Weight Reddit/X sources HIGHER (they have engagement signals: upvotes, likes)
2. Weight WebSearch sources LOWER (no engagement data)
3. Identify patterns that appear across ALL three sources (strongest signals)
4. Note any contradictions between sources
5. Extract the top 3-5 actionable insights

**Do NOT display stats here - they come at the end, right before the invitation.**

---

## FIRST: Internalize the Research

**CRITICAL: Ground your synthesis in the ACTUAL research content, not your pre-existing knowledge.**

Read the research output carefully. Pay attention to:
- **Exact product/tool names** mentioned (e.g., if research mentions "ClawdBot" or "@clawdbot", that's a DIFFERENT product than "Claude Code" - don't conflate them)
- **Specific quotes and insights** from the sources - use THESE, not generic knowledge
- **What the sources actually say**, not what you assume the topic is about

**ANTI-PATTERN TO AVOID**: If user asks about "clawdbot skills" and research returns ClawdBot content (self-hosted AI agent), do NOT synthesize this as "Claude Code skills" just because both involve "skills". Read what the research actually says.

### If QUERY_TYPE = RECOMMENDATIONS

**CRITICAL: Extract SPECIFIC NAMES, not generic patterns.**

When user asks "best X" or "top X", they want a LIST of specific things:
- Scan research for specific product names, tool names, project names, skill names, etc.
- Count how many times each is mentioned
- Note which sources recommend each (Reddit thread, X post, blog)
- List them by popularity/mention count

**BAD synthesis for "best Claude Code skills":**
> "Skills are powerful. Keep them under 500 lines. Use progressive disclosure."

**GOOD synthesis for "best Claude Code skills":**
> "Most mentioned skills: /commit (5 mentions), remotion skill (4x), git-worktree (3x), /pr (3x). The Remotion announcement got 16K likes on X."

### For all QUERY_TYPEs

Identify from the ACTUAL RESEARCH OUTPUT:
- **PROMPT FORMAT** - Does research recommend JSON, structured params, natural language, keywords?
- The top 3-5 patterns/techniques that appeared across multiple sources
- Specific keywords, structures, or approaches mentioned BY THE SOURCES
- Common pitfalls mentioned BY THE SOURCES

---

## THEN: Show Summary + Invite Vision

**Display in this EXACT sequence:**

**FIRST - What I learned (based on QUERY_TYPE):**

**If RECOMMENDATIONS** - Show specific things mentioned with sources:
```
🏆 Most mentioned:

[Tool Name] - {n}x mentions
Use Case: [what it does]
Sources: @handle1, @handle2, r/sub, blog.com

[Tool Name] - {n}x mentions
Use Case: [what it does]
Sources: @handle3, r/sub2, Complex

Notable mentions: [other specific things with 1-2 mentions]
```

**CRITICAL for RECOMMENDATIONS:**
- Each item MUST have a "Sources:" line with actual @handles from X posts (e.g., @LONGLIVE47, @ByDobson)
- Include subreddit names (r/hiphopheads) and web sources (Complex, Variety)
- Parse @handles from research output and include the highest-engagement ones
- Format naturally - tables work well for wide terminals, stacked cards for narrow

**If PROMPTING/NEWS/GENERAL** - Show synthesis and patterns:

CITATION RULE: Cite sources sparingly to prove research is real.
- In the "What I learned" intro: cite 1-2 top sources total, not every sentence
- In KEY PATTERNS: cite 1 source per pattern, short format: "per @handle" or "per r/sub"
- Do NOT include engagement metrics in citations (likes, upvotes) - save those for stats box
- Do NOT chain multiple citations: "per @x, @y, @z" is too much. Pick the strongest one.

CITATION PRIORITY (most to least preferred):
1. @handles from X — "per @handle" (these prove the tool's unique value)
2. r/subreddits from Reddit — "per r/subreddit"
3. Web sources — ONLY when Reddit/X don't cover that specific fact

The tool's value is surfacing what PEOPLE are saying, not what journalists wrote.
When both a web article and an X post cover the same fact, cite the X post.

URL FORMATTING: NEVER paste raw URLs in the output.
- **BAD:** "per https://www.rollingstone.com/music/music-news/kanye-west-bully-1235506094/"
- **GOOD:** "per Rolling Stone"
- **GOOD:** "per Complex"
Use the publication name, not the URL. The user doesn't need links — they need clean, readable text.

**BAD:** "His album is set for March 20 (per Rolling Stone; Billboard; Complex)."
**GOOD:** "His album BULLY drops March 20 — fans on X are split on the tracklist, per @honest30bgfan_"
**GOOD:** "Ye's apology got massive traction on r/hiphopheads"
**OK** (web, only when Reddit/X don't have it): "The Hellwatt Festival runs July 4-18 at RCF Arena, per Billboard"

**Lead with people, not publications.** Start each topic with what Reddit/X
users are saying/feeling, then add web context only if needed. The user came
here for the conversation, not the press release.

```
What I learned:

**{Topic 1}** — [1-2 sentences about what people are saying, per @handle or r/sub]

**{Topic 2}** — [1-2 sentences, per @handle or r/sub]

**{Topic 3}** — [1-2 sentences, per @handle or r/sub]

KEY PATTERNS from the research:
1. [Pattern] — per @handle
2. [Pattern] — per r/sub
3. [Pattern] — per @handle
```

**THEN - Stats (right before invitation):**

**CRITICAL: Calculate actual totals from the research output.**
- Count posts/threads from each section
- Sum engagement: parse `[Xlikes, Yrt]` from each X post, `[Xpts, Ycmt]` from Reddit
- Identify top voices: highest-engagement @handles from X, most active subreddits

**Copy this EXACTLY, replacing only the {placeholders}:**

```
---
✅ All agents reported back!
├─ 🟠 Reddit: {N} threads │ {N} upvotes │ {N} comments
├─ 🔵 X: {N} posts │ {N} likes │ {N} reposts (via Bird/xAI)
├─ 🟡 Hacker News: {N} stories │ {N} points │ {N} comments
├─ 🔴 YouTube: {N} videos │ {N} views │ {N} comments
├─ 🟣 Product Hunt: {N} products │ {N} upvotes │ {N} comments
├─ 🌐 Web: {N} pages (supplementary)
└─ 🗣️ Top voices: @{handle1} ({N} likes), @{handle2} │ r/{sub1}, r/{sub2}
---
```

Only include lines for sources that were actually searched. If a source returned 0 results, write e.g.: "├─ 🟠 Reddit: 0 threads (no results this cycle)"
NEVER use plain text dashes (-) or pipe (|). ALWAYS use ├─ └─ │ and the emoji.

**SELF-CHECK before displaying**: Re-read your "What I learned" section. Does it match what the research ACTUALLY says? If you catch yourself projecting your own knowledge instead of the research, rewrite it.

**LAST - Invitation (adapt to QUERY_TYPE):**

**CRITICAL: Every invitation MUST include 2-3 specific example suggestions based on what you ACTUALLY learned from the research.** Don't be generic — show the user you absorbed the content by referencing real things from the results.

**If QUERY_TYPE = PROMPTING:**
```
---
I'm now an expert on {TOPIC} for {TARGET_TOOL}. What do you want to make? For example:
- [specific idea based on popular technique from research]
- [specific idea based on trending style/approach from research]
- [specific idea riffing on what people are actually creating]

Just describe your vision and I'll write a prompt you can paste straight into {TARGET_TOOL}.
```

**If QUERY_TYPE = RECOMMENDATIONS:**
```
---
I'm now an expert on {TOPIC}. Want me to go deeper? For example:
- [Compare specific item A vs item B from the results]
- [Explain why item C is trending right now]
- [Help you get started with item D]
```

**If QUERY_TYPE = NEWS:**
```
---
I'm now an expert on {TOPIC}. Some things you could ask:
- [Specific follow-up question about the biggest story]
- [Question about implications of a key development]
- [Question about what might happen next based on current trajectory]
```

**If QUERY_TYPE = GENERAL:**
```
---
I'm now an expert on {TOPIC}. Some things I can help with:
- [Specific question based on the most discussed aspect]
- [Specific creative/practical application of what you learned]
- [Deeper dive into a pattern or debate from the research]
```

**Example invitations (to show the quality bar):**

For `/last30days nano banana pro prompts for Gemini`:
> I'm now an expert on Nano Banana Pro for Gemini. What do you want to make? For example:
> - Photorealistic product shots with natural lighting (the most requested style right now)
> - Logo designs with embedded text (Gemini's new strength per the research)
> - Multi-reference style transfer from a mood board
>
> Just describe your vision and I'll write a prompt you can paste straight into Gemini.

For `/last30days kanye west` (GENERAL):
> I'm now an expert on Kanye West. Some things I can help with:
> - What's the real story behind the apology letter — genuine or PR move?
> - Break down the BULLY tracklist reactions and what fans are expecting
> - Compare how Reddit vs X are reacting to the Bianca narrative

For `/last30days war in Iran` (NEWS):
> I'm now an expert on the Iran situation. Some things you could ask:
> - What are the realistic escalation scenarios from here?
> - How is this playing differently in US vs international media?
> - What's the economic impact on oil markets so far?

---

## WAIT FOR USER'S RESPONSE

After showing the stats summary with your invitation, **STOP and wait** for the user to respond.

---

## WHEN USER RESPONDS

**Read their response and match the intent:**

- If they ask a **QUESTION** about the topic → Answer from your research (no new searches, no prompt)
- If they ask to **GO DEEPER** on a subtopic → Elaborate using your research findings
- If they describe something they want to **CREATE** → Write ONE perfect prompt (see below)
- If they ask for a **PROMPT** explicitly → Write ONE perfect prompt (see below)

**Only write a prompt when the user wants one.** Don't force a prompt on someone who asked "what could happen next with Iran."

### Writing a Prompt

When the user wants a prompt, write a **single, highly-tailored prompt** using your research expertise.

### CRITICAL: Match the FORMAT the research recommends

**If research says to use a specific prompt FORMAT, YOU MUST USE THAT FORMAT.**

**ANTI-PATTERN**: Research says "use JSON prompts with device specs" but you write plain prose. This defeats the entire purpose of the research.

### Quality Checklist (run before delivering):
- [ ] **FORMAT MATCHES RESEARCH** - If research said JSON/structured/etc, prompt IS that format
- [ ] Directly addresses what the user said they want to create
- [ ] Uses specific patterns/keywords discovered in research
- [ ] Ready to paste with zero edits (or minimal [PLACEHOLDERS] clearly marked)
- [ ] Appropriate length and style for TARGET_TOOL

### Output Format:

```
Here's your prompt for {TARGET_TOOL}:

---

[The actual prompt IN THE FORMAT THE RESEARCH RECOMMENDS]

---

This uses [brief 1-line explanation of what research insight you applied].
```

---

## IF USER ASKS FOR MORE OPTIONS

Only if they ask for alternatives or more prompts, provide 2-3 variations. Don't dump a prompt pack unless requested.

---

## AFTER EACH PROMPT: Stay in Expert Mode

After delivering a prompt, offer to write more:

> Want another prompt? Just tell me what you're creating next.

---

## CONTEXT MEMORY

For the rest of this conversation, remember:
- **TOPIC**: {topic}
- **TARGET_TOOL**: {tool}
- **KEY PATTERNS**: {list the top 3-5 patterns you learned}
- **RESEARCH FINDINGS**: The key facts and insights from the research

**CRITICAL: After research is complete, you are now an EXPERT on this topic.**

When the user asks follow-up questions:
- **DO NOT run new WebSearches** - you already have the research
- **Answer from what you learned** - cite the Reddit threads, X posts, and web sources
- **If they ask a question** - answer it from your research findings
- **If they ask for a prompt** - write one using your expertise

Only do new research if the user explicitly asks about a DIFFERENT topic.

---

## Output Summary Footer (After Each Prompt)

After delivering a prompt, end with:

```
---
📚 Expert in: {TOPIC} for {TARGET_TOOL}
📊 Based on: {n} Reddit threads ({sum} upvotes) + {n} X posts ({sum} likes) + {n} web pages

Want another prompt? Just tell me what you're creating next.
```
