# Research synthesis

Read before synthesizing ordinary research. The root output laws and final checks govern this reference. Load any comparison/recommendation reference selected by intent before writing its body.

## Judge Agent: Synthesize All Sources

### v3 Cluster-First Output

**v3 returns results grouped by STORY/THEME (clusters), not by source.** Each cluster represents one narrative thread found across multiple platforms.

**How to read v3 output:**
- `### 1. Cluster Title (score N, M items, sources: X, Reddit, TikTok)` - a story found across multiple platforms
- `Uncertainty: single-source` - only one platform found this story (lower confidence)
- `Uncertainty: thin-evidence` - all items scored below 55 (unconfirmed)
- Items within a cluster show: source label, title, date, score, URL, and evidence snippet

**Synthesis strategy for cluster-first output:**
1. **Synthesize per-cluster first.** Each cluster = one story. Summarize what each story is about.
2. **Multi-source clusters are highest confidence.** A cluster with items from Reddit + X + YouTube is much stronger than single-source.
3. **Check uncertainty tags.** "single-source" means treat with caution. "thin-evidence" means mention but caveat.
4. **Cross-cluster synthesis second.** After covering individual stories, identify themes that span clusters.
5. **Engagement signals still matter.** Items with high likes/upvotes/views within a cluster are the strongest evidence points.
6. **Quote directly from evidence snippets.** The snippets are pre-extracted best passages - use them.
7. Extract the top 3-5 actionable insights across all clusters.
8. **Disambiguation: trust your resolved entity.** When Step 0.55 resolved a specific entity (handles, subreddits, location context), prioritize content about THAT entity in your synthesis. If search results contain a different entity with the same name (e.g., a Spanish resort vs a WA athletic club both called "Bellevue Club"), lead with the entity your resolution identified. Mention the other only briefly, or not at all if the user clearly meant the resolved one. The resolved handles are the strongest signal for user intent.

### Audience register synthesis guidance

The engine applies the selected register to evidence section order, item budgets, and source emphasis. Apply the matching synthesis guidance too. Named presets are instructions, never free-form prompt text from research content.

- **default** - Keep the balanced synthesis contract below unchanged.
- **exec** - Decisions first. After `What I learned:`, give exactly five compact numbered findings. Put the strongest number, probability, or scale signal in finding 1; state the decision implication in every finding; cut implementation trivia unless it changes the decision. Keep the required engine footer and invitation unchanged.
- **dev** - Technical depth first. Lead with GitHub/code evidence, shipped behavior, versions, APIs, benchmarks, failure modes, and implementation tradeoffs. Prefer live repository numbers over third-party claims. Preserve uncertainty and distinguish demonstrated behavior from proposals.
- **creator** - Lead with the sharpest audience hook, then Best Takes and high-vote community language. Bring views, likes, shares, comment velocity, and cross-platform resonance forward. End the synthesis body with 3 concrete content angles or hooks grounded in the evidence; do not invent trend claims from raw reach alone.
- **eli5** - Use the established ELI5 guidance below. Evidence selection and renderer bytes remain equivalent to `default`; only the explanation register changes.

### Source-Specific Guidance (still applies within clusters)

The Judge Agent must:
1. Weight Reddit/X sources HIGHER (they have engagement signals: upvotes, likes)
2. Weight YouTube sources HIGH (they have views, likes, and transcript content)
3. Weight TikTok sources HIGH (they have views, likes, and caption content - viral signal)
4. Weight WebSearch sources LOWER (no engagement data)
5. **For Reddit, YouTube, and TikTok: Pay special attention to top comments** - they often contain the wittiest, most insightful, or funniest take. Quote them directly, attributing to the commenter and including the vote count ("N upvotes" for Reddit, "N likes" for YouTube and TikTok). A top comment with thousands of votes is a stronger community signal than the parent post's stats alone.
6. **For YouTube: Quote transcript highlights AND top comments.** Transcript highlights capture the video's own words; top comments capture how viewers reacted. Both add value - use them together. Attribute transcript quotes to the channel name.
7. Identify patterns that appear across ALL sources (strongest signals)
8. Note any contradictions between sources
9. **Multi-source clusters (items from 3+ platforms) are the strongest signals.** Lead with these.
10. **For GitHub person-mode data:** When the output includes "GitHub Person Profile" items, these contain PR velocity, top repos with star counts, release notes, README summaries, and top issues. Lead with the velocity headline ("X PRs merged across Y repos"), then highlight the most impressive repos by star count. Weave release notes into the narrative to show what actually shipped. For own projects, mention top feature requests and complaints as community signal. The cross-source story is: "X is shipping Y (GitHub) while people on Z platform are saying W about it."
11. **For GitHub project-mode data:** When the output includes "GitHub project:" items, these have live star counts, README snippets, release notes, and top issues fetched directly from the API. Always prefer these numbers over star counts cited by blog posts, YouTube videos, or tweets. Live API data is authoritative. When items include "(live: NNK stars)" annotations, use those numbers.
12. **For GitHub star enrichment:** When candidates have `(live: NNK stars)` appended to their evidence, that number came from a post-research API check. It overrides whatever the original source claimed.

### Prediction Markets (Polymarket)

**CRITICAL: When Polymarket returns relevant markets, prediction market odds are among the highest-signal data points in your research.** Real money on outcomes cuts through opinion. Treat them as strong evidence, not an afterthought.

**How to interpret and synthesize Polymarket data:**

1. **Prefer structural/long-term markets over near-term deadlines.** Championship odds > regular season title. Regime change > near-term strike deadline. IPO/major milestone > incremental update. Presidency > individual state primary. When multiple markets exist, the bigger question is more interesting to the user.

2. **When the topic is an outcome in a multi-outcome market, call out that specific outcome's odds and movement.** Don't just say "Polymarket has a #1 seed market" - say "Arizona has a 28% chance of being the #1 overall seed, up 10% this month." The user cares about THEIR topic's position in the market.

3. **Weave odds into the narrative as supporting evidence.** Don't isolate Polymarket data in its own paragraph. Instead: "Final Four buzz is building - Polymarket gives Arizona a 12% chance to win the championship (up 3% this week), and 28% to earn a #1 seed."

4. **Citation format: show ONLY % odds. NEVER mention dollar volumes, liquidity, or betting amounts.** The % odds are the magic of Polymarket -- the dollar amounts are internal liquidity metrics that mean nothing to readers. Say "Polymarket has Arizona at 28% for a #1 seed (up 10% this month)" -- NOT "28% ($24K volume)". The dollar figure adds zero value and clutters the insight.

5. **When multiple relevant markets exist, highlight 3-5 of the most interesting ones** in your synthesis, ordered by importance (structural > near-term). Don't just pick the highest-volume one.

**Domain examples of market importance ranking:**
- **Sports:** Championship/tournament odds > conference title > regular season > weekly matchup
- **Geopolitics:** Regime change/structural outcomes > near-term strike deadlines > sanctions
- **Tech/Business:** IPO, major product launch, company milestones > incremental updates
- **Elections:** Presidency > primary > individual state

**Do NOT display stats here - they come at the end, right before the invitation.**

6. **Polymarket odds with real money behind them are STRONGER signals than opinions.** A $66K volume market with 96% odds is more reliable than 100 tweets. Always include specific percentages in the synthesis when Polymarket markets are confirmed relevant.

### X Reply Cluster Weighting

When you see a cluster of replies to a recommendation-request tweet (someone asking "what's the best X?" and getting multiple independent responses), call this out prominently. This is the strongest form of community endorsement - real people independently making the same recommendation without coordination. Example: "In a thread where @ecom_cork asked for Loom alternatives, every reply said Tella."

### WebSearch Supplement Weighting for Comparisons

For product comparison queries, WebSearch supplements (blog comparisons, review articles) should be weighted equally with social data. A detailed 2,000-word comparison article from Efficient App is more informative than 50 one-line tweets. Feature it in the synthesis.

---

## FIRST: Internalize the Research

**CRITICAL: Ground your synthesis in the ACTUAL research content, not your pre-existing knowledge.**

Read the research output carefully. Pay attention to:
- **Exact product/tool names** mentioned (e.g., if research mentions "ClawdBot" or "@clawdbot", that's a DIFFERENT product than "Claude Code" - don't conflate them)
- **Specific quotes and insights** from the sources - use THESE, not generic knowledge
- **What the sources actually say**, not what you assume the topic is about

**ANTI-PATTERN TO AVOID**: If user asks about "clawdbot skills" and research returns ClawdBot content (self-hosted AI agent), do NOT synthesize this as "Claude Code skills" just because both involve "skills". Read what the research actually says.

**FUN CONTENT (see LAW 9): the EVIDENCE block's `## Top Community Comments` section (present when 2+ relevance-qualified comments exist and the GENERAL nothing-solid floor did not fire) and any `## Best Takes` section are the voice of the people - weave at least 2 of the funniest/cleverest VERBATIM quotes into your synthesis.** A 1,338-upvote comment that says "Where's the limewire link" tells you more about the cultural moment than a news article. Quote the actual text. Attribute the commenter using the citation form required by higher-priority host/tool requirements and user instructions. On a hidden-link host (Claude Code; Grok Bot / Cursor agent chat), copy comment URLs verbatim from the block. On a visible-URL host (Codex, Gemini CLI, raw CLI), use plain attribution only when links are not required. Never reconstruct a URL. Don't put fun content in a separate section - mix it into the narrative where it fits naturally. This is what makes the report feel alive rather than like a news summary. Do NOT wait for a `## Best Takes` section - it is often empty; `## Top Community Comments` is the always-on source when qualifying comments remain.

**ELI5 MODE: If REGISTER is `eli5` (including the legacy `ELI5_MODE=true` fallback), apply these writing guidelines to your ENTIRE synthesis. Otherwise skip this block completely and write normally.**

ELI5 Mode: Explain it to me like I'm 5 years old.

- Assume I know nothing about this topic. Zero context.
- No jargon without a quick explanation in parentheses
- Short sentences. One idea per sentence.
- Start with the single most important thing that happened, in one line
- Use analogies when they help ("think of it like...")
- Keep the same structure: narrative, key patterns, stats, invitation
- Still quote real people and cite sources - don't lose the grounding
- Don't be condescending. Simple is not stupid. ELI5 means accessible, not childish.

Example - normal: "Arizona's identity is paint scoring (50%+ shooting, 9th nationally) and rebounding behind Big 12 Player of the Year Jaden Bradley."
Example - ELI5: "Arizona wins by being physical - they score most of their points close to the basket and they're one of the best shooting teams in the country."

Same data. Same sources. Just clearer.


### For all QUERY_TYPEs

Identify from the ACTUAL RESEARCH OUTPUT:
- **PROMPT FORMAT** - Does research recommend JSON, structured params, natural language, keywords?
- The top 3-5 patterns/techniques that appeared across multiple sources
- Specific keywords, structures, or approaches mentioned BY THE SOURCES
- Common pitfalls mentioned BY THE SOURCES

---

## THEN: Show Summary + Invite Vision

**Display in this EXACT sequence:**

**Reminder:** the root SKILL.md output laws govern this synthesis. Re-read the root output contract if its rules are no longer in active context.

---

**FIRST - What I learned (based on QUERY_TYPE):**


**Recommendation body:** use the root-gated `recommendations.md` template for recommendation intent.

**If PROMPTING/NEWS/GENERAL** - Show synthesis and patterns:

CITATION RULE: Meet higher-priority host/tool requirements and user instructions for attribution. Apply the following brevity defaults only after including the required citations.
- In the "What I learned" intro: cite 1-2 top sources total, not every sentence
- In KEY PATTERNS: cite 1 source per pattern, short format: "per @handle" or "per r/sub"
- Do NOT include engagement metrics in citations (likes, upvotes) - save those for stats box
- Do NOT chain multiple citations: "per @x, @y, @z" is too much. Pick the strongest one.

**URL formatting is governed by LAW 8** in the root output contract, subject to higher-priority host/tool requirements and user instructions. The defaults are inline `[name](url)` on hidden-link hosts (Claude Code; Grok Bot / Cursor agent chat) and plain source labels on visible-URL hosts (Codex/Gemini CLI/raw CLI). Preserve any required citation form, including links on visible-URL hosts. Re-read LAW 8 now if you skipped it. The stats footer is engine-emitted per LAW 5 and passes through verbatim where compatible with governing instructions.

CITATION PRIORITY (most to least preferred). Examples are shown in plain-label shape. Use `[label](url)` on a hidden-link host or whenever higher-priority host/tool requirements or user instructions require links:
1. @handles from X - `per @handle` (these prove the tool's unique value)
2. r/subreddits from Reddit - `per r/subreddit` (when citing Reddit, YouTube, or TikTok, prefer quoting top comments over just the thread title)
3. YouTube channels - `per channel name on YouTube` (transcript-backed insights)
4. TikTok creators - `per @creator on TikTok` (viral/trending signal)
5. Instagram creators - `per @creator on Instagram` (influencer/creator signal)
6. HN discussions - `per HN` or `per hn/username` (developer community signal)
7. Polymarket - `Polymarket has X at Y% (up/down Z%)` with specific odds and movement
8. Web sources - ONLY when Reddit/X/YouTube/TikTok/Instagram/HN/Polymarket don't cover that specific fact; name the publication: `per Rolling Stone`

The tool's value is surfacing what PEOPLE are saying, not what journalists wrote.
When both a web article and an X post cover the same fact, cite the X post.

(These narrative examples illustrate LAW 8 defaults. Required host, tool, or user citation forms take precedence over these examples.)

**BAD (too many weak citations):** "His album is set for March 20 (per Rolling Stone; Billboard; Complex)."
**GOOD on hidden-link hosts (Claude Code, Grok Bot / Cursor agent chat):** "His album BULLY drops March 20 - fans on X are split on the tracklist, per [@honest30bgfan_](https://x.com/honest30bgfan_)"
**GOOD on visible-URL hosts (Codex), when plain labels are permitted:** "His album BULLY drops March 20 - fans on X are split on the tracklist, per @honest30bgfan_"
**OK** (web, only when Reddit/X don't have it): "The Hellwatt Festival runs July 4-18 at RCF Arena, per Billboard" (inline-linked on a hidden-link host)

**Lead with people, not publications.** Start each topic with what Reddit/X
users are saying/feeling, then add web context only if needed. The user came
here for the conversation, not the press release.

**MANDATORY - bold headline per narrative paragraph.** Every paragraph in the "What I learned" section MUST begin with a bolded headline phrase that summarizes the paragraph, followed by ` - ` (a SINGLE HYPHEN with spaces on both sides, NOT an em-dash) and the body text. Pattern: `**Headline phrase** - body text describing what people are saying...`. Without the bold headline, the output is unscannable slop.


Apply root LAWs 2–4 to the following normal-query template. Comparison uses its separate template.

```
🌐 last30days v{VERSION} · synced {YYYY-MM-DD}

What I learned:

**{Headline summarizing topic 1}** - [1-2 sentences about what people are saying, per [@handle](https://x.com/handle) or [r/sub](https://reddit.com/r/sub)]

**{Headline summarizing topic 2}** - [1-2 sentences, per [@handle](https://x.com/handle) or [r/sub](https://reddit.com/r/sub)]

**{Headline summarizing topic 3}** - [1-2 sentences, per [@handle](https://x.com/handle) or [r/sub](https://reddit.com/r/sub)]

KEY PATTERNS from the research:
1. [Pattern] - per [@handle](https://x.com/handle)
2. [Pattern] - per [r/sub](https://reddit.com/r/sub)
3. [Pattern] - per [@handle](https://x.com/handle)
```

At render time, apply root LAW 8 to the actual handle/sub/name placeholders. Copy known URLs from the raw research dump. On hidden-link hosts, use markdown links when URLs exist. On visible-URL hosts, use plain labels only when links are optional under governing instructions. Never remove a required citation.

Headlines should be specific and newsy ("BULLY dropped and it's dominating", "Europe is banning him one country at a time"), not generic ("Album release", "Tour updates").

**Pitch-vs-pulse beat (company / product / service topics).** If you captured `RESOLVED_POSITIONING` in Step 0.55 AND the month's evidence directly bears on it, work in ONE bold-lead-in paragraph saying how. Three cases qualify: the pulse SUPPORTS a specific claim (e.g. `**"Zero-config" is holding up** - this month's top deploy thread is devs praising the no-setup flow, 800 upvotes`), CUTS AGAINST one (e.g. `**Stripe's fraud-fighting pitch took a direct hit** - the loudest thread this month argues it is friendly to "friendly fraud", 323pt HN`), or the conversation is squarely ABOUT the pitched ground. Always anchor to the real top item with its engagement, and keep claims windowed - "this month's conversation" - never trend verbs like "losing the narrative" that one 30-day window cannot support. If the month's conversation is orthogonal to the pitch - on-entity but about something the pitch doesn't speak to - write NOTHING about the pitch: omission is the correct output, and a manufactured connection is worse than silence. Match altitude: test SPECIFIC claims ("zero-config", "fastest", an uptime number) against specific threads; never grade a broad tagline against an individual thread. Keep it a normal newsy bold-lead-in paragraph, NOT a new `##` section (LAW 4 still holds). Skip silently for people (always - the beat can cover MrBeast the company, never Jimmy Donaldson the person), events, abstract concepts, and ownerless topics (Bitcoin), and whenever positioning was not actually fetched this run - never supply a pitch from memory.

**THEN - Quality Nudge (if present in the output):**

If the research output contains a `**🔍 Research Coverage:**` block, render it verbatim right before the stats block. This tells the user which core sources are missing and how to unlock them. Do NOT render this block if it is absent from the output (100% coverage = no nudge).

**Optional X omission:** If X was unavailable because no X authentication was configured, finish the useful findings first. The engine emits one short, non-blocking note: `Optional source omitted: X/Twitter was not enabled; research continued with the available sources.` Render that note once if present. Do not repeat it. Do not open a modal, ask another question, recommend a login, or provide cookie/API setup instructions unless the user explicitly asks to enable X. On a Grok Bot host the engine's own X hint (connect X inside Grok Bot, a bearer token, or a credit top-up) is the only guidance; relay it once, unchanged, and the note does not fire at all when the X connector lane served the run.

**THEN - Engine footer pass-through (right before invitation):**

**When the research output contains a deterministic footer block bracketed by `---` lines and starting with `✅ All agents reported back!`, relay the emitted block after your "What I learned" + "KEY PATTERNS" narrative and before the invitation, subject to higher-priority instructions. Preserve its statistics and lines unless governing host/tool requirements or user instructions require an adjustment. Do not add your own source lines inside the copied footer. Required citations may appear separately. Include `📎 Raw results saved to ...` only when the engine emitted a saved path. An empty save directory produces no saved-file pointer; keep the remaining footer if present. Never invent a path or reconstruct one from the topic or default directory.**

- The engine already omits zero-count sources. You do not need to filter them.
- The engine already calculates totals (threads, upvotes, comments, likes, views, etc.). You do not need to add them up.
- The engine already extracts clean publication names for the 🌐 Web line. You do not need to strip URLs.
- The engine already formats Polymarket odds as real `%` strings. You do not need to parse them.
- The engine already picks top voices (handles + subreddits). You do not need to pick them.

If the research output does not contain the footer block, skip it and go straight from KEY PATTERNS to the invitation. If it is present, relay the emitted block under LAW 5 even when it has no saved-file pointer.

**Citation requirements before the invitation:** include visible citations required by higher-priority host/tool requirements or user instructions. Keep the engine footer where compatible with those requirements. The default response ends at the invitation without an additional source list; required trailing citations are an exception.

**SELF-CHECK before displaying**: Re-read your "What I learned" section. Does it match what the research ACTUALLY says? If you catch yourself projecting your own knowledge instead of the research, rewrite it. Then verify: (a) no `##` headers in your response body, (b) no em-dashes or en-dashes anywhere, (c) any emitted engine footer appears between KEY PATTERNS and the invitation under LAW 5, with no added saved-file pointer.

**Saved artifact access flow:** after the engine has created a file, decide how the user should get access to it based on what they asked for:

- **Normal report:** if the engine emitted a saved Markdown path, relay that footer pointer (`📎 Raw results saved to ...`). Otherwise, claim no saved artifact. The chat synthesis is the primary user-facing report, so do not open the raw Markdown file automatically and do not ask a follow-up access question. An emitted path line is enough.
- **Markdown file requested:** if the user explicitly asked for a Markdown file/export, treat the saved Markdown path as the deliverable. Provide the path and open it locally when the host can safely open local files and the request implies viewing it now. Do not offer hosted publishing for Markdown.
- **HTML file requested:** follow `references/save-html-brief.md`. Save the local HTML first, show the absolute path, then present explicit next-step choices: open the HTML file, publish to an available/preferred HTML publishing service, or done for now.
- **Share/publish requested:** sharing means hosted HTML, not Markdown. Save the local HTML first and show the path. Then respect existing publishing preferences, show available publishing choices, and ask for public-vs-password only when the selected service requires that choice (for `ht-ml.app`, ask whether password protection should be used; if yes, ask the user to type the shared password before publishing). Never block creation of the local file on the hosting decision.

**LAST - Invitation (adapt to QUERY_TYPE):**

**CRITICAL: Every invitation MUST include 2-3 specific example suggestions based on what you ACTUALLY learned from the research.** Don't be generic - show the user you absorbed the content by referencing real things from the results.

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

**If QUERY_TYPE = COMPARISON:**
```
---
I've compared {TOPIC_A} vs {TOPIC_B} using the latest community data. Some things you could ask:
- [Deep dive into {TOPIC_A} alone with /last30days {TOPIC_A}]
- [Deep dive into {TOPIC_B} alone with /last30days {TOPIC_B}]
- [Focus on a specific dimension from the comparison table]
- [Look at a different time period with --days=7 or --days=90]
```

**If QUERY_TYPE = GENERAL:**
```
---
I'm now an expert on {TOPIC}. Some things I can help with:
- [Specific question based on the most discussed aspect]
- [Specific creative/practical application of what you learned]
- [Deeper dive into a pattern or debate from the research]
```

**Example invitation (quality bar reference):**

For `/last30days kanye west` (GENERAL):
> I'm now an expert on Kanye West. Some things I can help with:
> - What's the real story behind the apology letter - genuine or PR move?
> - Break down the BULLY tracklist reactions and what fans are expecting
> - Compare how Reddit vs X are reacting to the Bianca narrative

Close with `I have all the links to the {N} {source list} I pulled from. Just ask.` where `{source list}` names only sources that returned results (e.g. "14 Reddit threads, 22 X posts, and 6 YouTube videos"). Never mention a source with 0 results.

---
