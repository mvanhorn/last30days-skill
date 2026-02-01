---
name: last30days
description: Research a topic from the last 30 days using Claude Code's WebSearch, become an expert, and write copy-paste-ready prompts.
argument-hint: "[topic] for [tool]" or "[topic]"
context: fork
agent: Explore
disable-model-invocation: true
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
---

# last30days: Research Any Topic from the Last 30 Days

Research ANY topic using Claude Code's built-in WebSearch. Surface what people are actually discussing, recommending, and debating right now.

**Zero setup required.** Works immediately with Claude Code Pro's WebSearch capability.

Use cases:
- **Prompting**: "photorealistic people in Nano Banana Pro", "Midjourney prompts", "ChatGPT image generation" - learn techniques, get copy-paste prompts
- **Recommendations**: "best Claude Code skills", "top AI tools" - get a LIST of specific things people mention
- **News**: "what's happening with OpenAI", "latest AI announcements" - current events and updates
- **General**: any topic you're curious about - understand what the community is saying

## CRITICAL: Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about (e.g., "web app mockups", "Claude Code skills", "image generation")
2. **TARGET TOOL** (if specified): Where they'll use the prompts (e.g., "Nano Banana Pro", "ChatGPT", "Midjourney")
3. **QUERY TYPE**: What kind of research they want:
   - **PROMPTING** - "X prompts", "prompting for X", "X best practices" - User wants to learn techniques and get copy-paste prompts
   - **RECOMMENDATIONS** - "best X", "top X", "what X should I use", "recommended X" - User wants a LIST of specific things
   - **NEWS** - "what's happening with X", "X news", "latest on X" - User wants current events/updates
   - **GENERAL** - anything else - User wants broad understanding of the topic

Common patterns:
- `[topic] for [tool]` - "web mockups for Nano Banana Pro" - TOOL IS SPECIFIED
- `[topic] prompts for [tool]` - "UI design prompts for Midjourney" - TOOL IS SPECIFIED
- Just `[topic]` - "iOS design mockups" - TOOL NOT SPECIFIED, that's OK
- "best [topic]" or "top [topic]" - QUERY_TYPE = RECOMMENDATIONS
- "what are the best [topic]" - QUERY_TYPE = RECOMMENDATIONS

**IMPORTANT: Do NOT ask about target tool before research.**
- If tool is specified in the query, use it
- If tool is NOT specified, run research first, then ask AFTER showing results

**Store these variables:**
- `TOPIC = [extracted topic]`
- `TARGET_TOOL = [extracted tool, or "unknown" if not specified]`
- `QUERY_TYPE = [RECOMMENDATIONS | NEWS | PROMPTING | GENERAL]`

---

## Research Execution

**Use Claude Code's built-in WebSearch to research the topic.**

### Step 1: Run WebSearch queries

Run **3-5 WebSearch queries** to gather comprehensive information. Choose queries based on QUERY_TYPE:

**If RECOMMENDATIONS** ("best X", "top X", "what X should I use"):
- `best {TOPIC} recommendations 2026`
- `{TOPIC} list examples`
- `most popular {TOPIC}`
- `{TOPIC} reddit recommendations` (to capture Reddit discussions indexed by search)
- Goal: Find SPECIFIC NAMES of things, not generic advice

**If NEWS** ("what's happening with X", "X news"):
- `{TOPIC} news 2026`
- `{TOPIC} announcement update`
- `{TOPIC} latest developments`
- Goal: Find current events and recent developments

**If PROMPTING** ("X prompts", "prompting for X"):
- `{TOPIC} prompts examples 2026`
- `{TOPIC} techniques tips`
- `{TOPIC} best practices guide`
- `{TOPIC} reddit prompting` (to capture community discussions)
- Goal: Find prompting techniques and examples to create copy-paste prompts

**If GENERAL** (default):
- `{TOPIC} 2026`
- `{TOPIC} discussion`
- `{TOPIC} guide overview`
- Goal: Find what people are actually saying

**For ALL query types:**
- **USE THE USER'S EXACT TERMINOLOGY** - don't substitute or add tech names based on your knowledge
  - If user says "ChatGPT image prompting", search for "ChatGPT image prompting"
  - Do NOT add "DALL-E", "GPT-4o", or other terms you think are related
  - Your knowledge may be outdated - trust the user's terminology
- Run searches in parallel when possible for speed
- Aim for 15-25 relevant results across all searches

### Step 2: Filter and Analyze Results

From the WebSearch results:
1. **Prioritize recent content** - Look for dates in URLs and snippets, prefer content from the last 30 days
2. **Extract specific mentions** - For RECOMMENDATIONS, pull out specific product/tool/skill names
3. **Identify patterns** - What techniques/approaches appear across multiple sources?
4. **Note authoritative sources** - Official docs, popular blogs, well-known authors

---

## Synthesize Results

**CRITICAL: Ground your synthesis in the ACTUAL research content, not your pre-existing knowledge.**

Read the research output carefully. Pay attention to:
- **Exact product/tool names** mentioned
- **Specific quotes and insights** from the sources - use THESE, not generic knowledge
- **What the sources actually say**, not what you assume the topic is about

### If QUERY_TYPE = RECOMMENDATIONS

**CRITICAL: Extract SPECIFIC NAMES, not generic patterns.**

When user asks "best X" or "top X", they want a LIST of specific things:
- Scan research for specific product names, tool names, project names, skill names, etc.
- Count how many times each is mentioned across sources
- Note which sources recommend each
- List them by popularity/mention count

**BAD synthesis for "best Claude Code skills":**
> "Skills are powerful. Keep them under 500 lines. Use progressive disclosure."

**GOOD synthesis for "best Claude Code skills":**
> "Most mentioned skills: Remotion skill (4x), /commit (3x), git-worktree (3x). The Remotion announcement got significant attention on X."

### For all QUERY_TYPEs

Identify from the ACTUAL RESEARCH OUTPUT:
- **PROMPT FORMAT** - Does research recommend JSON, structured params, natural language, keywords? THIS IS CRITICAL.
- The top 3-5 patterns/techniques that appeared across multiple sources
- Specific keywords, structures, or approaches mentioned BY THE SOURCES
- Common pitfalls mentioned BY THE SOURCES

**If research says "use JSON prompts" or "structured prompts", you MUST deliver prompts in that format later.**

---

## Show Summary + Invite Vision

**CRITICAL: Do NOT output verbose "Sources:" lists. Keep the display clean.**

**Display in this EXACT sequence:**

**FIRST - What I learned (based on QUERY_TYPE):**

**If RECOMMENDATIONS** - Show specific things mentioned:
```
Most mentioned:
1. [Specific name] - mentioned {n}x (source1, source2)
2. [Specific name] - mentioned {n}x (sources)
3. [Specific name] - mentioned {n}x (sources)
4. [Specific name] - mentioned {n}x (sources)
5. [Specific name] - mentioned {n}x (sources)

Notable mentions: [other specific things with 1-2 mentions]
```

**If PROMPTING/NEWS/GENERAL** - Show synthesis and patterns:
```
What I learned:

[2-4 sentences synthesizing key insights FROM THE ACTUAL RESEARCH OUTPUT.]

KEY PATTERNS I'll use:
1. [Pattern from research]
2. [Pattern from research]
3. [Pattern from research]
```

**THEN - Stats:**
```
---
Research complete!
Analyzed {n} web sources from {domains}
Top sources: {author1} on {site1}, {author2} on {site2}
```

**LAST - Invitation:**
```
---
Share your vision for what you want to create and I'll write a thoughtful prompt you can copy-paste directly into {TARGET_TOOL}.
```

**Use real numbers from the research output.** The patterns should be actual insights from the research, not generic advice.

**SELF-CHECK before displaying**: Re-read your "What I learned" section. Does it match what the research ACTUALLY says? If you catch yourself projecting your own knowledge instead of the research, rewrite it.

**IF TARGET_TOOL is still unknown after showing results**, ask NOW (not before research):
```
What tool will you use these prompts with?

Options:
1. [Most relevant tool based on research]
2. Nano Banana Pro (image generation)
3. ChatGPT / Claude (text/code)
4. Other (tell me)
```

**IMPORTANT**: After displaying this, WAIT for the user to respond. Don't dump generic prompts.

---

## WAIT FOR USER'S VISION

After showing the stats summary with your invitation, **STOP and wait** for the user to tell you what they want to create.

When they respond with their vision (e.g., "I want a landing page mockup for my SaaS app"), THEN write a single, thoughtful, tailored prompt.

---

## WHEN USER SHARES THEIR VISION: Write ONE Perfect Prompt

Based on what they want to create, write a **single, highly-tailored prompt** using your research expertise.

### CRITICAL: Match the FORMAT the research recommends

**If research says to use a specific prompt FORMAT, YOU MUST USE THAT FORMAT:**

- Research says "JSON prompts" - Write the prompt AS JSON
- Research says "structured parameters" - Use structured key: value format
- Research says "natural language" - Use conversational prose
- Research says "keyword lists" - Use comma-separated keywords

**ANTI-PATTERN**: Research says "use JSON prompts with device specs" but you write plain prose. This defeats the entire purpose of the research.

### Output Format:

```
Here's your prompt for {TARGET_TOOL}:

---

[The actual prompt IN THE FORMAT THE RESEARCH RECOMMENDS - if research said JSON, this is JSON. If research said natural language, this is prose. Match what works.]

---

This uses [brief 1-line explanation of what research insight you applied].
```

### Quality Checklist:
- [ ] **FORMAT MATCHES RESEARCH** - If research said JSON/structured/etc, prompt IS that format
- [ ] Directly addresses what the user said they want to create
- [ ] Uses specific patterns/keywords discovered in research
- [ ] Ready to paste with zero edits (or minimal [PLACEHOLDERS] clearly marked)
- [ ] Appropriate length and style for TARGET_TOOL

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
- **Answer from what you learned** - cite the sources you found
- **If they ask for a prompt** - write one using your expertise
- **If they ask a question** - answer it from your research findings

Only do new research if the user explicitly asks about a DIFFERENT topic.

---

## Output Summary Footer (After Each Prompt)

After delivering a prompt, end with:

```
---
Expert in: {TOPIC} for {TARGET_TOOL}
Based on: {n} web sources

Want another prompt? Just tell me what you're creating next.
```
