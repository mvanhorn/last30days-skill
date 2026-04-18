---
name: last30days
version: "3.0.1"
description: "Research what people actually say about any topic in the last 30 days. Pulls posts and engagement from Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, and the web."
argument-hint: 'last30days nvidia earnings reaction | last30days AI video tools | last30days what users want in react'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
homepage: https://github.com/mvanhorn/last30days-skill
repository: https://github.com/mvanhorn/last30days-skill
author: mvanhorn
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "📰"
    requires:
      env:
        - SCRAPECREATORS_API_KEY
      optionalEnv:
        - OPENAI_API_KEY
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PARALLEL_API_KEY
        - BRAVE_API_KEY
        - APIFY_API_TOKEN
        - AUTH_TOKEN
        - CT0
        - BSKY_HANDLE
        - BSKY_APP_PASSWORD
        - TRUTHSOCIAL_TOKEN
      bins:
        - node
        - python3
    primaryEnv: SCRAPECREATORS_API_KEY
    files:
      - "scripts/*"
    homepage: https://github.com/mvanhorn/last30days-skill
    tags:
      - research
      - deep-research
      - reddit
      - x
      - twitter
      - youtube
      - tiktok
      - instagram
      - hackernews
      - polymarket
      - bluesky
      - truthsocial
      - trends
      - recency
      - news
      - citations
      - multi-source
      - social-media
      - analysis
      - web-search
      - ai-skill
      - clawhub
---

# HOW TO INVOKE THIS SKILL (READ FIRST, FOLLOW EVERY TIME)

**STEP 0 - LOAD WEBSEARCH FIRST.** Your literal first tool call on every `/last30days` invocation MUST be:

```
ToolSearch select:WebSearch
```

WebSearch is a **deferred tool** in Claude Code v2.1.114. The frontmatter of this file authorizes it (`allowed-tools: ... WebSearch`) but the runtime lists it as "schemas are NOT loaded." Calling WebSearch without `ToolSearch select:WebSearch` first will fail or do nothing. That friction is the documented cause of the second-most-common failure mode of this skill: the model sees "WebSearch is there but deferred," takes the low-friction path, skips Step 0.5 and 0.55, and runs the engine bare with only keyword search. The output looks fine but misses founder X timelines, GitHub repo activity, and subreddit-specific threads.

Load WebSearch first. No exceptions. Then proceed to the branching rule below.

**STEP 1 - RUN THE ENGINE. You MUST run `scripts/last30days.py` via Bash. Do not produce output from WebSearch alone.**

The single most common failure mode of this skill is the model reading this file, skimming the section headers, and then answering the user's topic with 3-10 WebSearch calls followed by a prose summary. That is wrong output. The Python engine is the skill. Web-only synthesis is not the skill.

Branching rule:

- **If the user provided a topic** (e.g. `/last30days Kanye West`, `/last30days nvidia earnings`): proceed to Step 0.5 / Step 0.55 / Step 0.75 / Research Execution below. Do not skip straight to WebSearch. WebSearch is a **supplement after** the Python engine runs (see Step 2). It is **not a substitute**.
- **If the user provided no topic**: ask the user for a topic with a single short question. Do not run research. Do not run WebSearch. Wait.

If you are about to write a response without having run `scripts/last30days.py` at least once, stop. Return to Research Execution and run the engine. Every valid output from this skill includes the emoji-tree footer (`✅ All agents reported back!`) that the engine produces data for. No footer means you did not run the skill.

Before Step 0.5, run Step 0.45 Query Quality Pre-Flight. If the topic is a keyword trap (demographic shopping like "gift for 42 year old man", numeric/age trap, overly-literal concept phrase like "how to use Docker", or generic single-noun like "sneakers"), reframe or ask ONE clarifying question before calling the engine. Skipping Step 0.45 on a keyword-trap topic is the named failure mode of the 2026-04-18 "Birthday gift for 42 year old man" disaster: the engine ran on the literal phrase and returned 5 minutes of r/todayilearned / r/japannews / r/LivestreamFail noise because no human posts "I bought a 42 year old man a gift" on Reddit.

If your Bash call to `last30days.py` does NOT include the FULL pre-flight checklist resolved (see Step 0.5 Pre-Flight Checklist), that is a Step 0.5/0.55 skip. The engine will emit a `## Pre-Research Status` warning block in its output. Pass the warning through verbatim; do not try to hide it. The warning tells the user to rerun with WebSearch loaded.

**For person topics specifically (developers, creators, CEOs, founders): the Bash command MUST include MINIMUM `--x-handle={handle}` AND `--github-user={handle}` AND `--subreddits={list}`, and typically `--x-related={list}`, unless an explicit "no account" note was produced during Step 0.5.** A person-topic command with ONLY `--x-handle` is the Peter Steinberger disaster #2 failure mode (2026-04-18): the model read the X-handle subsection literally, stopped there, and skipped the rest of the checklist. Result: weak Reddit targeting, no GitHub person-mode scoping, no related-voices enrichment, and a thin corpus. The fix is to read the Step 0.5 Pre-Flight Checklist FIRST and resolve every applicable flag before running the engine.

---

# last30days v3.0.1: Research Any Topic from the Last 30 Days

> **Permissions overview:** Reads public web/platform data and optionally saves research briefings to `~/Documents/Last30Days/`. X/Twitter search uses optional user-provided tokens (AUTH_TOKEN/CT0 env vars). Bluesky search uses optional app password (BSKY_HANDLE/BSKY_APP_PASSWORD env vars - create at bsky.app/settings/app-passwords). All credential usage and data writes are documented in the [Security & Permissions](#security--permissions) section.

Research ANY topic across Reddit, X, YouTube, and other sources. Surface what people are actually discussing, recommending, betting on, and debating right now.

## Runtime Preflight

Before running any `last30days.py` command in this skill, resolve a Python 3.12+ interpreter once and keep it in `LAST30DAYS_PYTHON`:

```bash
for py in python3.14 python3.13 python3.12 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || continue
  LAST30DAYS_PYTHON="$py"
  break
done

if [ -z "${LAST30DAYS_PYTHON:-}" ]; then
  echo "ERROR: last30days v3 requires Python 3.12+. Install python3.12 or python3.13 and rerun." >&2
  exit 1
fi
```

## Step 0: First-Run Setup Wizard

Before proceeding to Step 1, handle first-run setup.

**First-run detection (silent, no commands, no output to user):**
- If `~/.config/last30days/.env` does NOT exist, this is a first run.
- If the file exists and contains `SETUP_COMPLETE=true`, skip Step 0 entirely and go to Step 1 (CRITICAL: Parse User Intent below). Do NOT announce that setup is complete. The user does not need a status message on every run.

**If this IS a first run:**
- Use the Read tool to load `skills/last30days/nux-wizard.md` (relative to the skill root).
- Follow the wizard's instructions end-to-end. The wizard handles platform detection (OpenClaw vs Claude Code), auto vs manual setup, ScrapeCreators opt-in, and the initial topic picker.
- After the wizard writes `SETUP_COMPLETE=true` to `~/.config/last30days/.env`, proceed to research.

The wizard lives in a separate file so the common-case (already set up) path through this file is short and the voice-contract rules further down stay in context.

---


## CRITICAL: Parse User Intent

Before doing anything, parse the user's input for:

1. **TOPIC**: What they want to learn about (e.g., "web app mockups", "Claude Code skills", "image generation")
2. **TARGET TOOL** (if specified): Where they'll use the prompts (e.g., "Nano Banana Pro", "ChatGPT", "Midjourney")
3. **QUERY TYPE**: What kind of research they want:
   - **PROMPTING** - "X prompts", "prompting for X", "X best practices" → User wants to learn techniques and get copy-paste prompts
   - **RECOMMENDATIONS** - "best X", "top X", "what X should I use", "recommended X" → User wants a LIST of specific things
   - **NEWS** - "what's happening with X", "X news", "latest on X" → User wants current events/updates
   - **COMPARISON** - "X vs Y", "X versus Y", "compare X and Y", "X or Y which is better" → User wants a side-by-side comparison
   - **GENERAL** - anything else → User wants broad understanding of the topic

Common patterns:
- `[topic] for [tool]` → "web mockups for Nano Banana Pro" → TOOL IS SPECIFIED
- `[topic] prompts for [tool]` → "UI design prompts for Midjourney" → TOOL IS SPECIFIED
- Just `[topic]` → "iOS design mockups" → TOOL NOT SPECIFIED, that's OK
- "best [topic]" or "top [topic]" → QUERY_TYPE = RECOMMENDATIONS
- "what are the best [topic]" → QUERY_TYPE = RECOMMENDATIONS
- "X vs Y" or "X versus Y" → QUERY_TYPE = COMPARISON, TOPIC_A = X, TOPIC_B = Y (split on ` vs ` or ` versus ` with spaces)

**IMPORTANT: Do NOT ask about target tool before research.**
- If tool is specified in the query, use it
- If tool is NOT specified, run research first, then ask AFTER showing results

**Store these variables:**
- `TOPIC = [extracted topic]`
- `TARGET_TOOL = [extracted tool, or "unknown" if not specified]`
- `QUERY_TYPE = [RECOMMENDATIONS | NEWS | HOW-TO | COMPARISON | GENERAL]`
- `TOPIC_A = [first item]` (only if COMPARISON)
- `TOPIC_B = [second item]` (only if COMPARISON)

**Confirm the topic with a branded, truthful message. Build ACTIVE_SOURCES_LIST by checking what's configured in .env:**

- Always active: Reddit, Hacker News, Polymarket
- If gh CLI is installed (check `which gh`): add GitHub
- If AUTH_TOKEN/CT0 or XAI_API_KEY or FROM_BROWSER is set: add X
- If yt-dlp is installed (check `which yt-dlp`): add YouTube
- If SCRAPECREATORS_API_KEY is set and INCLUDE_SOURCES contains tiktok: add TikTok
- If SCRAPECREATORS_API_KEY is set and INCLUDE_SOURCES contains instagram: add Instagram
- If SCRAPECREATORS_API_KEY is set and INCLUDE_SOURCES contains threads: add Threads
- If SCRAPECREATORS_API_KEY is set and INCLUDE_SOURCES contains pinterest: add Pinterest
- If BSKY_HANDLE and BSKY_APP_PASSWORD are set: add Bluesky
- If OPENROUTER_API_KEY is set: add Perplexity

Then display (use "and more" if 5+ sources, otherwise list all with Oxford comma):

For GENERAL / NEWS / RECOMMENDATIONS / PROMPTING queries:
```
/last30days - searching {ACTIVE_SOURCES_LIST} for what people are saying about {TOPIC}.
```

For COMPARISON queries:
```
/last30days - comparing {TOPIC_A} vs {TOPIC_B} across {ACTIVE_SOURCES_LIST}.
```

Do NOT show a multi-line "Parsed intent" block with TOPIC=, TARGET_TOOL=, QUERY_TYPE= variables. Do NOT promise a specific time. Do NOT list sources that aren't configured.

Then proceed immediately to Step 0.45.

---

## Step 0.45: Query Quality Pre-Flight (detect keyword-trap topics BEFORE running the engine)

**MANDATORY. Before Step 0.5, diagnose the topic for known failure classes. If the topic is a keyword trap, reframe or ask a clarifying question BEFORE calling the engine. Running the engine on a doomed query burns 5+ minutes and produces junk. Detecting the trap upfront costs one turn.**

Known keyword-trap classes and how to handle each:

**Class 1: Demographic shopping query**
- Pattern: `gift for {age} year old {gender}`, `what to buy for my {relationship}`, `present for {demographic}`, `birthday gift for {age} {gender}`.
- Why it fails: no human on Reddit posts "I bought a 42 year old man a gift." Real posts use relationship + hobbies + budget. The literal phrase is not the vocabulary of the actual discussions. The 2026-04-18 "Birthday gift for 42 year old man" run returned r/todayilearned, r/japannews crime posts, r/LivestreamFail drama - none about gifts.
- Action: **Ask ONE clarifying question upfront**:
  > "Before I research, tell me a bit more - hobbies (cooks / runs / reads / gaming / outdoors / golf / music)? Relationship (husband / dad / friend / boss / brother)? Budget range? A 'gift for a 42 year old man' is a wide net; hobbies + relationship narrow it 10x."
- If the user declines to narrow ("just run it"), reframe to generic-demographic and scope to gift subreddits:
  - Drop the literal age (age 42 reads identically to 41 or 43 in social content; the number causes keyword collisions like Jackie Robinson #42)
  - Rewrite as `gifts for men in their 40s` or `gifts for men who [hobby]`
  - Scope `--subreddits=GiftIdeas,BuyItForLife,AskMen,malefashionadvice,Dads` (plus hobby-specific subs when known)
  - Note in the Resolved block: "Reframed demographic shopping query. Dropping literal age; scoping to gift communities."

**Class 2: Numeric / age keyword trap**
- Pattern: topic contains a specific number that collides with unrelated content (42 = Jackie Robinson + Hitchhiker's + a 42" quilt; 40 = 40th anniversary posts; 50 = state-count posts; 100 = bench-press posts).
- Why it fails: the number dominates retrieval and pulls in unrelated content. A search that prominently features "42" returns jersey-number posts; a search for "the 100" returns TV-show posts.
- Action: Strip the number from the engine search query unless it is semantically load-bearing (e.g., "GPT-4" yes, "40 year old man" no, "Area 51" yes, "top 10 foods" no). Keep the number in the user's original framing for context; drop it from the engine query. Document in Resolved: "Dropping '{number}' from the search query - it is a keyword trap that pulls in unrelated content. Search will cover the concept generically."

**Class 3: Overly-literal concept phrase**
- Pattern: `how to use X`, `what is Y`, `tutorial for Z`, `explain A` — tutorial-shaped phrasing where social posts are in different vocabulary.
- Why it fails: social posts about Docker do not say "how to use Docker"; they say "my Docker setup", "nginx in Docker", "my dev loop", "tip for folks using Docker Compose". Tutorial phrasing matches blog titles, not social discussions.
- Action: Reframe from tutorial phrasing to discussion phrasing: "how to use Docker" becomes "Docker tips tricks workflows" or "Docker production setups". Document the reframe in the Resolved block.

**Class 4: Generic single-noun common word**
- Pattern: topic is a single common noun with no specific hook (`bread`, `sneakers`, `coffee`, `shoes`, `headphones`).
- Why it fails: single-noun queries have no anchor — the corpus is infinite and the signal is noise.
- Action: Ask for specificity before running:
  > "{TOPIC} is a huge category - are you asking about {specific-facet-A}, {specific-facet-B}, or {specific-facet-C}? Each is a different community. Pick one or tell me the angle."

**Pre-Flight decision flow (do this BEFORE any WebSearch):**
1. Read the topic. Match against Classes 1-4 above.
2. If the topic matches a class, ALWAYS emit a visible pre-flight note before the Resolved block:
   - `Pre-Flight: topic matches {Class N} ({class name}). {Action: clarifying question / reframe / specificity ask}.`
3. If the action is a clarifying question, STOP after emitting it. Wait for the user response before any engine work.
4. If the topic does NOT match any class, emit a one-liner: `Pre-Flight: topic is a {named-entity / comparison / concept} - proceeding to Step 0.5.` Then proceed.

**One-turn gate rule:** do NOT run the engine on a keyword-trap topic without either (a) explicit user confirmation to "just run it anyway", or (b) a concrete reframed query. Burning 5 minutes on a doomed run is worse than a one-turn clarifying question.

**When the user provides context inline:** if a Class 1 query already contains hobbies/relationship/budget ("gift for my cooking-obsessed husband, $200"), SKIP the clarifying question and go straight to the reframe + scope action. The clarifying question exists to fill in the gaps; if the gaps are already filled, move on.

---

## Step 0.5: Pre-Flight Resolution (handles, repos, communities)

**Pre-Flight Checklist — do NOT stop after the first flag. Every applicable flag below is MANDATORY for its topic class.**

Before running the engine, determine which flags apply to this topic and resolve them. Reading only the "X handle" subsection and stopping there is the named failure mode of the Peter Steinberger disaster #2 (2026-04-18). The model admitted on debug: "I treated the 'X handle resolution' section as the full contract for pre-flight resolution and didn't --help the script to see what else existed." The checklist below IS the full contract.

| Flag | Resolved in | Applies when |
|------|-------------|--------------|
| `--x-handle={handle}` | Step 0.5 (Section A below) | Topic is a person, brand, product, or creator with an X presence |
| `--x-related={h1,h2,...}` | Step 0.5 (Section A below) | Topic has associated entities (founders, commentators, spouse, collaborators, media handles) |
| `--github-user={user}` | Step 0.5b | Topic is a person who ships code (developer, engineer, CEO-who-codes, researcher) |
| `--github-repo={owner/repo}` | Step 0.5c | Topic is a product / project / open-source tool |
| `--subreddits={sub1,sub2,...}` | Step 0.55 | Always — almost every topic has active Reddit communities |
| `--tiktok-hashtags={h1,h2,...}` | Step 0.55 | Always — inferred from topic |
| `--tiktok-creators={c1,c2,...}` | Step 0.55 | Creator / influencer / brand topics |
| `--ig-creators={c1,c2,...}` | Step 0.55 | Creator / brand topics |
| `--auto-resolve` | Fallback | WebSearch is available but Step 0.55 could not resolve everything cleanly — use as belt-and-suspenders |

**Checkpoint before running the engine:** your Bash command must include every flag from the checklist that applies to this topic. For a person who ships code (the Peter Steinberger class), that is MINIMUM `--x-handle` AND `--github-user` AND `--subreddits`, and typically `--x-related` too. A command with only `--x-handle` on a person topic is a pre-flight skip and a Step 0.5 regression.

---

### Section A: Resolve X Handles (if topic could have X accounts)

If TOPIC looks like it could have its own X/Twitter account - **people, creators, brands, products, tools, companies, communities** (e.g., "Dor Brothers", "Jason Calacanis", "Nano Banana Pro", "Seedance", "Midjourney"), do WebSearches to find handles in three categories:

**1. Primary handle** (the entity itself):
```
WebSearch("{TOPIC} X twitter handle site:x.com")
```

**2. Company/organization handle OR founder/creator handle** -- This mapping is bidirectional:
- If the topic is a **PERSON**, resolve their company's X handle. A CEO's story is inseparable from their company's story.
- If the topic is a **PRODUCT or COMPANY**, resolve the founder/creator's personal X handle. The creator's personal account often has the most candid, high-signal content.
```
WebSearch("{TOPIC} company CEO of site:x.com")
```
OR for products:
```
WebSearch("{TOPIC} creator founder X twitter site:x.com")
```
Examples: Sam Altman -> @OpenAI, Dario Amodei -> @AnthropicAI, OpenClaw -> @steipete (Peter Steinberger), Paperclip -> @dotta, Claude Code -> @alexalbert__.

**3. 1-2 related handles** -- People/entities closely associated with the topic (spouse, collaborator, band member), PLUS 1-2 prominent commentator/media handles that regularly cover this topic:
```
WebSearch("{RELATED_PERSON_OR_ENTITY} X twitter handle site:x.com")
```
For a music artist, find music commentary accounts (e.g., @PopBase, @HotFreestyle, @DailyRapFacts).
For a tech CEO, find tech media accounts (e.g., @TechCrunch, @TheInformation).
For a product, find reviewer accounts in that category.

From the results, extract their X/Twitter handles. Look for:
- **Verified profile URLs** like `x.com/{handle}` or `twitter.com/{handle}`
- Mentions like "@handle" in bios, articles, or social profiles
- "Follow @handle on X" patterns

**Verify accounts are real, not parody/fan accounts.** Check for:
- Verified/blue checkmark in the search results
- Official website linking to the X account
- Consistent naming (e.g., @thedorbrothers for "The Dor Brothers", not @DorBrosFan)
- If results only show fan/parody/news accounts (not the entity's own account), skip - the entity may not have an X presence

Pass handles to the CLI:
- Primary: `--x-handle={handle}` (without @)
- Related: `--x-related={handle1},{handle2},{company_handle},{commentator_handles}` (comma-separated, without @)

Example for "Kanye West":
- Primary: `--x-handle=kanyewest`
- Related: `--x-related=travisscott,PopBase,HotFreestyle`

Example for "Sam Altman":
- Primary: `--x-handle=sama`
- Related: `--x-related=OpenAI,TechCrunch`

Related handles are searched with lower weight (0.3) so they appear in results but don't dominate over the primary entity's content.

**Note about @grok:** Grok is Elon's AI on X (xAI). It often appears in search results with thoughtful, accurate analysis. When citing @grok in your synthesis, frame it as "per Grok's AI analysis of [article/topic]" rather than treating it as an independent human commentator.

**Skip this step if:**
- TOPIC is clearly a generic concept, not an entity (e.g., "best rap songs 2026", "how to use Docker", "AI ethics debate")
- TOPIC already contains @ (user provided the handle directly)
- Using `--quick` depth
- WebSearch shows no official X account exists for this entity

Store: `RESOLVED_HANDLE = {handle or empty}`, `RESOLVED_RELATED = {comma-separated handles or empty}`

### Step 0.5b: Resolve GitHub Username (if topic is a person) — MANDATORY FOR PERSON TOPICS

**MANDATORY when the topic is a person (developer, creator, CEO, founder, engineer, researcher) and WebSearch is available.** Resolving the X handle but NOT the GitHub handle is the documented Peter Steinberger failure mode (2026-04-18). Without `--github-user={handle}`, GitHub search becomes a keyword match across all of GitHub instead of person-mode scoped to `user:{handle}`. The result is typically 5-10 thin unrelated items instead of the person's actual commits, PRs, releases, and top-starred repos. Treat this as a peer step to Step 0.5 (X handle resolution), not an afterthought.

Do the WebSearch:

```
WebSearch("{TOPIC} github profile site:github.com")
```

From the results, extract their GitHub username from URLs like `github.com/{username}`.

**Verify the account is correct:** Check that the profile description or pinned repos match the person you're researching. Common names may return multiple profiles.

Pass to the CLI: `--github-user={username}` (without @)

Worked examples:
- For "Peter Steinberger", a WebSearch for `Peter Steinberger github profile site:github.com` returns @steipete. Pass `--github-user=steipete`.
- For "Matt Van Horn": `--github-user=mvanhorn`
- For "Garry Tan": `--github-user=garrytan`

**Person-mode GitHub tells a different story than keyword search.** Instead of "who mentioned this person in an issue body," it answers: "What are they shipping? Where are they getting merged? What do their own projects look like?" The engine fetches PR velocity, top repos with star counts, release notes, and README summaries.

**Skip this step if:**
- TOPIC is clearly NOT a person (products, concepts, events)
- TOPIC already has `--github-user` specified by the user
- Using `--quick` depth
- WebSearch shows no GitHub profile for this person (report "no GitHub handle found for this person" and proceed without `--github-user` rather than fabricating one)

Store: `RESOLVED_GITHUB_USER = {username or empty}`

**Checkpoint for person topics:** by the time you reach the Research Execution command, for a person topic you MUST have BOTH `RESOLVED_HANDLE` (from Step 0.5) AND `RESOLVED_GITHUB_USER` (from this step) OR an explicit "no X account" / "no GitHub profile" note. The Bash command that follows must include BOTH `--x-handle={handle}` AND `--github-user={handle}` when resolved. A person-topic run that shows only one of the two is a Step 0.5b regression.

### Step 0.5c: Resolve GitHub Repos (if topic is a product/project)

If TOPIC looks like a product, tool, or open source project (not a person), resolve its GitHub repo for project-mode search:

```
WebSearch("{TOPIC} github repo site:github.com")
```

From the results, extract `owner/repo` from URLs like `github.com/{owner}/{repo}`.

Pass to the CLI: `--github-repo={owner/repo}`

For comparisons ("X vs Y"), resolve repos for both topics: `--github-repo={repo_a},{repo_b}`

Example for "OpenClaw": `--github-repo=openclaw/openclaw`
Example for "OpenClaw vs Paperclip": `--github-repo=openclaw/openclaw,paperclipai/paperclip`

Project-mode GitHub fetches live star counts, README snippets, latest releases, and top issues directly from the API. This is always more accurate than blog posts or YouTube videos citing weeks-old numbers.

**Skip this step if:**
- TOPIC is a person (use `--github-user` instead)
- TOPIC has no GitHub presence (not a software project)
- WebSearch shows no GitHub repo for this topic

Store: `RESOLVED_GITHUB_REPOS = {comma-separated owner/repo or empty}`

---

## Agent Mode (--agent flag)

If `--agent` appears in ARGUMENTS (e.g., `/last30days plaud granola --agent`):

1. **Skip** the intro display block ("I'll research X across Reddit...")
2. **Skip** any `AskUserQuestion` calls - use `TARGET_TOOL = "unknown"` if not specified
3. **Run** the research script and WebSearch exactly as normal
4. **Skip** the "WAIT FOR USER RESPONSE" pause
5. **Skip** the follow-up invitation ("I'm now an expert on X...")
6. **Output** the complete research report and stop - do not wait for further input

Agent mode saves raw research data to `~/Documents/Last30Days/` automatically via `--save-dir` (handled by the script, no extra tool calls).

Agent mode report format:

```
## Research Report: {TOPIC}
Generated: {date} | Sources: Reddit, X, Bluesky, YouTube, TikTok, HN, Polymarket, Web

### Key Findings
[3-5 bullet points, highest-signal insights with citations]

### What I learned
{The full "What I learned" synthesis from normal output}

### Stats
{The standard stats block}
```

---

## If QUERY_TYPE = COMPARISON

When the user asks "X vs Y", run ONE research pass with a comparison-optimized plan that covers both entities AND their rivalry. This replaces the old 3-pass approach (which took 13+ minutes and produced tangential content).

**IMPORTANT: Include BOTH X handles (`--x-handle={TOPIC_A_HANDLE} --x-related={TOPIC_B_HANDLE},{COMPANY_HANDLES},{COMMENTATOR_HANDLES}`), `--subreddits={RESOLVED_SUBREDDITS}`, `--tiktok-hashtags={RESOLVED_HASHTAGS}`, `--tiktok-creators={RESOLVED_TIKTOK_CREATORS}`, and `--ig-creators={RESOLVED_IG_CREATORS}` from Step 0.55. Omit any flag where the value was not resolved (empty).**

**Single pass with entity-aware subqueries:**
```bash
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" "{TOPIC_A} vs {TOPIC_B}" --emit=compact --save-dir=~/Documents/Last30Days --save-suffix=v3 --plan 'COMPARISON_PLAN_JSON' --x-handle={TOPIC_A_HANDLE} --x-related={TOPIC_B_HANDLE},{COMPANY_A_HANDLE},{COMPANY_B_HANDLE},{COMMENTATOR_HANDLES} --subreddits={RESOLVED_SUBREDDITS} --tiktok-hashtags={RESOLVED_HASHTAGS} --tiktok-creators={RESOLVED_TIKTOK_CREATORS} --ig-creators={RESOLVED_IG_CREATORS}
```

**The `--plan` JSON for comparisons should include 3-4 subqueries:**
1. **Head-to-head:** `"{TOPIC_A} vs {TOPIC_B}"` - catches rivalry content, direct comparisons
2. **Entity A news:** `"{TOPIC_A} news {MONTH} {YEAR}"` - catches entity-specific developments
3. **Entity B news:** `"{TOPIC_B} news {MONTH} {YEAR}"` - catches entity-specific developments
4. (Optional) **Domain context:** `"{COMPANY_A} {COMPANY_B} {DOMAIN} news"` - catches industry context (e.g., "OpenAI Anthropic AI news")

ALL subqueries include ALL sources. The fusion engine handles deduplication across subqueries. **At least one subquery MUST include YouTube-specific search terms** (e.g., "{PERSON} interview 2026", "{PRODUCT_A} vs {PRODUCT_B} review") to ensure YouTube content is found. Without YouTube-specific terms, the engine may only find 0-1 videos for comparison queries.

Then do WebSearch for: `{TOPIC_A} vs {TOPIC_B} comparison {YEAR}` and `{TOPIC_A} vs {TOPIC_B} which is better` and `{COMPANY_A} vs {COMPANY_B} news {MONTH} {YEAR}`.

**Skip the normal Step 1 below** - go directly to the comparison synthesis format (see "If QUERY_TYPE = COMPARISON" in the synthesis section).

**COMPARISON TABLE SCAFFOLD (engine-emitted, pass through verbatim):** For comparison topics, the engine's compact output includes a `## Head-to-Head Comparison` block with an empty markdown table (columns = entities, rows = axes like "Core pitch", "Who it's for", "Community stance", "Trajectory") plus a "Choose X if / Choose Y if" prose block. Your synthesis MUST include this block verbatim with filled cells, positioned between the narrative and the emoji-tree footer. Keep each cell to 5-15 words. Use ' - ' (hyphen with spaces) not em-dashes inside cells. The block is the canonical comparison output shape - do not invent your own table structure.

---

## Step 0.55: Pre-Research Intelligence (resolve communities + handles)

> **PLATFORM GATE:** If your platform does NOT support WebSearch (e.g., OpenClaw, raw CLI), **skip Steps 0.55 and 0.75** but add `--auto-resolve` to the Python command in the Research Execution section. The engine will do its own pre-research using configured web search backends (Brave, Exa, or Serper) to discover subreddits, X handles, and current events context before planning.

**MANDATORY on Claude Code (and any platform with WebSearch).** You MUST perform Step 0.55 before calling the Python engine. Skipping this step is the second-most-common failure mode of this skill, right after skipping the engine entirely. If your Bash call to `last30days.py` does NOT include a `--plan` flag with resolved handles and subreddits, that is a Step 0.55 skip and a failure. The engine's `[Resolve] No web search backend available, skipping resolve` log line means you, the model, did not do your job - it does NOT mean "the engine will handle it." Treat this step as non-skippable. Repeat invocations on the same topic still re-run Step 0.55 because Reddit/X/TikTok handles for breaking-news topics change week to week.

**Run 2-3 focused WebSearches (in parallel) to resolve platform-specific targeting. Do NOT search for every platform individually - that wastes time. Instead, use your knowledge of the topic to infer most targeting, and only WebSearch for what you can't infer.**

**1. X handles** - Already resolved in Step 0.5 above (including company handles and commentators). Reference your `RESOLVED_HANDLE` and `RESOLVED_RELATED` from that step.

**2. Reddit communities + YouTube channels + current events** - Run 1-2 searches that cover multiple platforms at once:

```
WebSearch("{TOPIC} subreddit reddit community")
WebSearch("{TOPIC} news {CURRENT_MONTH} {CURRENT_YEAR}")
```

The first search finds subreddits. The second gives you current events context (which helps you generate better subqueries in Step 0.75) and may surface YouTube channels or creators organically.

Extract 3-5 subreddit names from the results. Store as `RESOLVED_SUBREDDITS` (comma-separated, no r/ prefix).

**3. TikTok hashtags + creators** - **INFER these from your topic knowledge. Do NOT WebSearch for "{PERSON} TikTok account" - most people/CEOs don't have TikTok, and the search is wasted.**

- **Hashtags:** Infer 2-3 from the topic name + category. Examples: "Kanye West" → `kanyewest,ye,bully`. "Claude Code" → `claudecode,aiagent,aicoding`. "Sam Altman" → `samaltman,openai,chatgpt`.
- **Creators:** Only search if the topic is a content creator, influencer, or brand that likely has TikTok presence. For CEOs, politicians, and non-creator people: skip.

Store as `RESOLVED_HASHTAGS` and `RESOLVED_TIKTOK_CREATORS`.

**4. Instagram creators** - **Same rule: INFER from topic knowledge.** If the topic is a celebrity, brand, or creator with obvious Instagram presence, use their handle directly. If the topic is a tech CEO or abstract concept, skip. Do NOT waste a WebSearch on "Dario Amodei Instagram account."

Store as `RESOLVED_IG_CREATORS`.

**5. YouTube content queries** - Infer 2-3 YouTube content-type queries from the topic without searching. The current events search (#2 above) may surface relevant YouTube channels.

- **For music artists:** `'{TOPIC} album review'`, `'{TOPIC} reaction'`
- **For products/SaaS:** `'{TOPIC} review'`, `'{TOPIC} tutorial'`
- **For comparisons:** `'{TOPIC_A} vs {TOPIC_B}'`
- **For people in the news:** `'{TOPIC} interview {YEAR}'`, `'{TOPIC} latest news'`

Store as `RESOLVED_YT_QUERIES`.

**Concrete examples:**

| Topic | WebSearches needed | Reddit subs | TikTok hashtags | TikTok creators | IG creators | YT queries |
|-------|-------------------|-------------|-----------------|-----------------|-------------|------------|
| **Kanye West** | 2 (subreddit + BULLY news) | `Kanye,WestSubEver,hiphopheads,Music` | `kanyewest,ye,bully` | (inferred: `kanyewest`) | (inferred: `kanyewest`) | `kanye west bully review,kanye west bully reaction` |
| **Sam Altman vs Dario** | 2 (subreddit + AI CEO news) | `artificial,MachineLearning,OpenAI,ClaudeAI` | `samaltman,openai,anthropic` | (skip - CEOs don't TikTok) | (skip - CEOs don't Reel) | `sam altman interview 2026,dario amodei interview 2026` |
| **Tella** (SaaS) | 2 (subreddit + Tella news) | `SaaS,Entrepreneur,screenrecording,productivity` | `tella,tellaapp,screenrecording` | (search: `tella screen recorder TikTok`) | (inferred: `tella.tv`) | `tella screen recorder review,tella tutorial` |

**For comparison queries ("X vs Y" or "X vs Y vs Z") - MANDATORY per-entity resolution:**

For each entity in the comparison, resolve all four lookup types. For a 3-way comparison that is up to 12 lookups (3 entities x 4 types). Batch them into 3-4 WebSearch calls by combining entities per query - do NOT fire one search per entity per type (that produces 12 searches and burns 90 seconds).

Per-entity lookup types to resolve:

1. **Project X handle** - the project's official or primary X/Twitter account
2. **Project GitHub repo** - `owner/repo` format (e.g., `openai/openai-python`)
3. **Founder/maintainer X handle** - the person or team behind the project
4. **Relevant subreddits** - project-specific subreddits (e.g., `r/openclaw`) AND general-category subreddits (e.g., `r/LocalLLaMA`)

Example batching for "OpenClaw vs Hermes vs Paperclip":

```
WebSearch("OpenClaw Hermes Paperclip github repos AI coding agent")
WebSearch("OpenClaw Hermes Paperclip founders twitter X handles")
WebSearch("OpenClaw Hermes Paperclip reddit subreddits community")
```

Three searches for 12 lookups. After resolving, display all 12 per-entity in the Resolved block before running the engine:

```
Resolved (comparison):
- OpenClaw: X @openclawai | GitHub openclaw/openclaw | Founder @steipete | Reddit r/openclaw, r/AI_Agents
- Hermes: X @hermesagent | GitHub nousresearch/hermes | Founder @NousResearch | Reddit r/hermesagent, r/LocalLLaMA
- Paperclip: X @paperclipai | GitHub dotta/paperclip | Founder @dotta | Reddit r/OpenClawInstall
```

Passing the resolved block visibly (per-entity, all 4 types each) is the observable check that Step 0.55 happened for this comparison. A Resolved block that only lists 3 project handles with no founders and no GitHub repos is a Step 0.55 regression. This was canonical behavior and must stay canonical.

**For non-comparison queries:** Resolve communities/handles for the single topic. Merging list logic does not apply.

**If you can't infer targeting for a platform, skip that flag -- the Python engine will fall back to keyword search.**

**After resolving all handles and communities, display what you found before moving on.** This shows the user that intelligent pre-research happened:

```
Resolved:
- X: @{HANDLE} (+ @{COMPANY}, @{COMMENTATOR})
- Reddit: r/{sub1}, r/{sub2}, r/{sub3}
- TikTok: #{hashtag1}, #{hashtag2}
- YouTube: {query1}, {query2}
```

Only show lines for platforms where something was resolved. Skip empty lines. This display replaces the old "Parsed intent" block with something more useful.

---

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
- **DISAMBIGUATION:** If the topic name is a common word or has known non-product meanings (e.g., "Loom" = also a weaving tool, "Tella" = also a soccer player), add a qualifying term to your search_query to disambiguate. Examples: "tella screen recording" not just "tella", "loom video messaging" not just "loom". The product category prevents matching unrelated content.
- **For comparison queries**, each subquery should include the product category: "tella screen recorder review" not just "tella review", "loom video tool pricing" not just "loom pricing".
- NEVER include temporal phrases in search_query: no "last 30 days", "recent", month names, year numbers
- NEVER include meta-research phrases: no "news", "updates", "public appearances"
- Preserve exact proper nouns and entity strings from the topic
- For comparison ("X vs Y"): create per-entity subqueries at weight 0.8 + a head-to-head subquery at weight 1.0
- For product queries: route to YouTube (reviews), Reddit (discussions), TikTok (demos)
- For predictions: include Polymarket in sources
- For how_to: prioritize YouTube (tutorials) and Reddit (guides)
- Primary subquery weight = 1.0, secondary = 0.6-0.8, peripheral = 0.3-0.5

**Available sources (include ALL in primary subquery):** reddit, x, youtube, tiktok, instagram, hackernews, polymarket. Optional: bluesky, truthsocial, threads, pinterest, grounding (web search - only if user has Brave/Exa/Serper key)

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

## Research Execution

### PRECONDITION GATE - read before running the script

**STOP. Before invoking `last30days.py`, verify ALL of the following are true for this turn:**

1. **Platform branch chosen.** You know whether this session has WebSearch (Claude Code) or does not (OpenClaw, raw CLI, Codex without web tools).
2. **If WebSearch IS available:** you MUST have run Step 0.55 (Pre-Research Intelligence - resolved subreddits, X handles, TikTok hashtags/creators, Instagram creators, GitHub user/repo where applicable) AND Step 0.75 (Query Planner - produced `QUERY_PLAN_JSON` with 2-4 subqueries). These are NOT optional. If either was skipped, return to that step now.
3. **If WebSearch is NOT available:** you MUST add `--auto-resolve` to the command instead. Do not attempt Steps 0.55 / 0.75 without WebSearch.
4. **The command you are about to run uses `--emit=compact`.** `--emit md` is a debugging/inspection mode and is DISALLOWED as the primary user-facing flow. If you find yourself about to run `--emit md`, stop and switch to `--emit=compact`.
5. **On WebSearch platforms the command MUST include `--plan 'QUERY_PLAN_JSON'`** plus every resolved handle/subreddit/hashtag/creator flag from Step 0.55. Omit only flags whose value was not resolvable.

**Degraded path (missing any of the above on a WebSearch platform) is a known regression shape. It produces bland 4-bullet summaries instead of rich synthesis. Do not take it.**

---

**Step 1: Run the research script WITH your query plan (FOREGROUND)**

**CRITICAL: Run this command in the FOREGROUND with a 5-minute timeout. Do NOT use run_in_background. The full output contains Reddit, X, AND YouTube data that you need to read completely.**

**IMPORTANT: Pass your QUERY_PLAN_JSON via the --plan flag. This tells the Python script to use YOUR plan instead of calling Gemini.**

**IMPORTANT: Include `--x-handle={RESOLVED_HANDLE}` in the command. For comparison mode: Pass `--x-handle={TOPIC_A_HANDLE}` to the first pass, `--x-handle={TOPIC_B_HANDLE}` to the second pass, and both to the head-to-head pass. Also include `--subreddits={RESOLVED_SUBREDDITS}`, `--tiktok-hashtags={RESOLVED_HASHTAGS}`, `--tiktok-creators={RESOLVED_TIKTOK_CREATORS}`, and `--ig-creators={RESOLVED_IG_CREATORS}` from Step 0.55. Omit any flag where the value was not resolved (empty).**

```bash
# Find skill root - works in repo checkout, Claude Code, or Codex install
for dir in \
  "." \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "${GEMINI_EXTENSION_DIR:-}" \
  "$HOME/.claude/plugins/marketplaces/last30days-skill-private" \
  "$HOME/.claude/plugins/cache/last30days-skill-private/last30days-3-nogem/3.0.0-nogem" \
  "$HOME/.claude/plugins/cache/last30days-skill-private/last30days-3/3.0.0-alpha" \
  "$HOME/.claude/skills/last30days-3-nogem" \
  "$HOME/.claude/skills/last30days-3"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
  echo "ERROR: Could not find scripts/last30days.py" >&2
  exit 1
fi

"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" $ARGUMENTS --emit=compact --save-dir=~/Documents/Last30Days --save-suffix=v3
```

**If you ran Steps 0.55 and 0.75 (agent planning), add these flags:**
- `--plan 'QUERY_PLAN_JSON'` (replace with actual JSON from Step 0.75)
- `--x-handle={RESOLVED_HANDLE}` (from Step 0.5)
- `--subreddits={RESOLVED_SUBREDDITS}` (from Step 0.55)
- `--tiktok-hashtags={RESOLVED_HASHTAGS}` (from Step 0.55)
- `--tiktok-creators={RESOLVED_TIKTOK_CREATORS}` (from Step 0.55)
- `--ig-creators={RESOLVED_IG_CREATORS}` (from Step 0.55)
- `--github-user={RESOLVED_GITHUB_USER}` (from Step 0.5b, person topics only)
- `--github-repo={RESOLVED_GITHUB_REPOS}` (from Step 0.5c, product/project topics only)
- Omit any flag where the value was not resolved (empty).

**If you skipped Steps 0.55 and 0.75 (no WebSearch -- OpenClaw, Codex, etc.), add:**
- `--auto-resolve` (the engine will use Brave/Exa/Serper to discover subreddits and context before planning)

**If you skipped Steps 0.55 and 0.75 (no WebSearch), run the command as-is.** The Python engine will plan internally.

Use a **timeout of 300000** (5 minutes) on the Bash call. The script typically takes 1-3 minutes.

The script will automatically:
- Detect available API keys
- Run Reddit/X/YouTube/TikTok/Instagram/Hacker News/Polymarket searches
- Output ALL results including YouTube transcripts, TikTok captions, Instagram captions, HN comments, and prediction market odds

**Read the ENTIRE output.** It contains EIGHT data sections in this order: Reddit items, X items, YouTube items, TikTok items, Instagram Reels items, Hacker News items, Polymarket items, and WebSearch items. If you miss sections, you will produce incomplete stats.

**YouTube items in the output look like:** `**{video_id}** (score:N) {channel_name} [N views, N likes]` followed by a title, URL, **transcript highlights** (pre-extracted quotable excerpts from the video), and an optional full transcript in a collapsible section. **Quote the highlights directly in your synthesis.** When YouTube items also include top comments (enabled via `youtube_comments`), quote those too with their like counts - they capture how viewers reacted to the video. Transcript highlights and top comments are complementary signals; use both when present. Attribute transcript quotes to the channel name, comment quotes to the commenter. Count them and include them in your synthesis and stats block.

**TikTok items in the output look like:** `**{TK_id}** (score:N) @{creator} [N views, N likes]` followed by a caption, URL, hashtags, and optional caption snippet. Count them and include them in your synthesis and stats block.

**Instagram Reels items in the output look like:** `**{IG_id}** (score:N) @{creator} (date) [N views, N likes]` followed by caption text, URL, and optional transcript. Count them and include them in your synthesis and stats block. Instagram provides unique creator/influencer perspective - weight it alongside TikTok.

---

## STEP 2: DO WEBSEARCH AFTER SCRIPT COMPLETES

After the script finishes, do WebSearch to supplement with blogs, tutorials, and news.

**Run 2-3 post-engine WebSearch supplements. This is a SEPARATE budget from Step 0.55 pre-research. Pre-research WebSearches DO NOT count against this budget.**

The supplement budget and the Step 0.55 pre-research budget are distinct. Step 0.55 resolves handles/subreddits/hashtags (typically 2-4 searches). Step 2 supplements fill blog/tutorial/news depth the social engine did not surface. Counting one toward the other is the most common reason supplement depth collapses to 1 search and the synthesis loses critical-reaction and long-form analysis context.

- Default: 3 supplements. Drop to 2 if the engine returned 80+ items AND the topic is niche enough that extra web context would be noise.
- Zero supplements is almost never correct. The social-first engine misses long-form analysis, critic reactions, and news context that shape good synthesis. If you are tempted to skip supplements, run at least 2.
- Ceiling: 3. Do not fire 5+ "just in case" - that is what pushed runtimes to 9 minutes on earlier validation.
- Example (Kanye West with 113 engine items): 2-3 supplements covering (1) Billboard/Pitchfork critical reception, (2) Wireless Festival ban news context, (3) optionally a specific claim you want corroborated. Not zero, even though the engine was rich.

For **ALL modes**, do WebSearch to supplement (or provide all data in web-only mode).

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
- **DO NOT output a separate "Sources:" block** - instead, include the top 3-5 web
  source names as inline links on the 🌐 Web: stats line (see stats format below).
  The WebSearch tool requires citation; satisfy it there, not as a trailing section.

**Options** (passed through from user's command):
- `--days=N` → Look back N days instead of 30 (e.g., `--days=7` for weekly roundup)
- `--quick` → Faster, fewer sources (8-12 each)
- (default) → Balanced (20-30 each)
- `--deep` → Comprehensive (50-70 Reddit, 40-60 X)

**Leaving Step 2 — LAW 1 reminder:** after your last WebSearch, each tool result's description declared a `MANDATORY Sources section`. That instruction is OVERRIDDEN inside this skill. Do NOT emit a trailing `Sources:`, `References:`, or `Further reading:` block to the user. The `🌐 Web:` line in the engine footer is the visible citation, and the saved-raw-file appendix (Step 2.5) is the durable citation. Your user-facing response ends at the invitation block.

---

## Step 2.5: Append WebSearch Results to Saved Raw File

**MANDATORY - do not skip this step.** Every post-engine WebSearch supplement you ran in Step 2 MUST be appended to the saved raw file under `~/Documents/Last30Days/`. Skipping this step is a common Opus 4.7 failure mode: the saved file ends at `## Source Coverage` with no appendix, future sessions cannot see what blog/tutorial/news sources informed the synthesis, and the user cannot trace where specific claims came from.

**LAW 1 OVERRIDE (read before synthesizing):** the WebSearch tool description declares a "MANDATORY Sources section" in its own contract. That instruction applies to generic WebSearch usage. Inside `/last30days` it is SUPERSEDED. The `## WebSearch Supplemental Results` appendix in the SAVED RAW FILE replaces the visible Sources section. Never emit a visible `Sources:` bullet list to the user. Your user-facing response ends at the invitation block. The emoji-tree footer's `🌐 Web:` line is the only visible citation. If you feel the pull to write a trailing `Sources:` section, you are about to violate LAW 1 — go back and delete it.

**Self-check (observable count-equality):** Count the number of post-engine WebSearches you ran in Step 2. Count the bullets in your `## WebSearch Supplemental Results` section. They MUST match. If they do not, re-do the append. If you ran zero supplements (which plan 005 says is almost never correct), skip this step entirely rather than writing an empty section.

**Instructions:**
1. Read the saved raw file. Locate it via the engine's `[last30days] Saved output to {path}` log line, not a hardcoded path.
2. Append a `## WebSearch Supplemental Results` section at the end.
3. For each WebSearch result, include one bullet in the canonical format (see Format example below).
4. Write the updated file back.

**Format example (canonical, from April 7 archive — match this shape):**

```
## WebSearch Supplemental Results

- **Flowtivity** (flowtivity.ai) — Side-by-side OpenClaw vs Paperclip framework comparison; concludes Paperclip solves coordination, OpenClaw solves execution.
- **Rahul Goyal** (rahulgoyal.co) — Honest three-way review: start with Hermes for simplicity, OpenClaw for tinkering, Paperclip only if running multiple agents.
- **Eigent** (eigent.ai) — Feature-by-feature OpenClaw vs Hermes for founders; Hermes wins on self-improving skills, OpenClaw on ecosystem breadth.
- **The New Stack** (thenewstack.io) — "The race to build AI assistants that never forget" — deep comparison of persistent memory architectures.
- **MindStudio** (mindstudio.ai) — Paperclip vs OpenClaw multi-agent comparison; Paperclip for orchestration, OpenClaw as the individual agent.
```

Each bullet: `- **{Publisher}** ({domain}) — {1-2 sentence excerpt of what you found}`. Publisher is the site name or author; domain is the clean hostname (no protocol, no path). Do not nest sub-bullets. Do not add URLs - the domain in parens is the citation.

This ensures anyone reviewing the raw file sees ALL data that fed into the synthesis, not just the Python engine output.

---

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

**FUN CONTENT: If the research output includes a "## Best Takes" section or items tagged with `fun:` scores, weave at least 2-3 of the funniest/cleverest quotes into your synthesis.** Reddit comments and X posts with high fun scores are the voice of the people. A 1,338-upvote comment that says "Where's the limewire link" tells you more about the cultural moment than a news article. Quote the actual text. Don't put fun content in a separate section - mix it into the narrative where it fits naturally. This is what makes the report feel alive rather than like a news summary.

**ELI5 MODE: If ELI5_MODE is true for this run, apply these writing guidelines to your ENTIRE synthesis. If ELI5_MODE is false, skip this block completely and write normally.**

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

### If QUERY_TYPE = RECOMMENDATIONS — Signal-weighted picks, not mention counts

**The failure mode for RECOMMENDATIONS queries is "counting when you should have judged."** Mention count rewards whatever is already popular, which is rarely what is actually recommended. Rank by signal quality instead.

**Signal weights (highest to lowest):**
1. **Practitioner testimony** (weight 5) - first-person "I use X and here's why" with specific reasoning, version numbers, or workflow details
2. **Expert defection / authority move** (weight 4) - a domain insider publicly switching, endorsing, or picking (e.g., Flask creator switching from Python to Go)
3. **Measurable claim** (weight 4) - specific number, benchmark, production adoption proof (e.g., "43.7% latency win", "LinkedIn and Uber running it in prod")
4. **Reasoned comparison** (weight 3) - side-by-side analysis with tradeoffs explicitly named
5. **Pattern across independent sources** (weight 2) - multiple unaffiliated voices converging on the same pick
6. **Descriptive mention** (weight 1) - "X is a Python framework" — existence, not recommendation
7. **Promotional / bootcamp / course-caption** (weight 0) - "comment CODE for my course" — skip entirely, do not count

**Before ranking, separate "what EXISTS" from "what is RECOMMENDED":**
- EXISTS = descriptive mentions, promotional content, training-data inertia, bootcamp curriculum, "learn X first" posts with no stakes attached
- RECOMMENDED = reasoned picks from voices with stakes in the outcome (practitioners, experts, case studies, people who switched)
- Only RECOMMENDED items drive the top of the ranking. Existing-but-not-recommended items go in "Also mentioned" at the bottom with a one-line note on why they are mentions not picks.

**Lead with the 30-day DELTA, not the status-quo baseline.** What is the interesting movement? Who is switching? What is the contrarian signal? A status-quo leader with no movement is a footer item, not the headline. "Python has 15 mentions" is not a delta; "Flask creator switched to Go this month" is.

**Output shape:**

```
🏆 Top recommendations (ranked by signal quality, not mention count):

**[Pick 1]** - [one-line why it is the top recommendation based on the strongest signal in the research]
- Evidence: [specific practitioner testimony, benchmark number, or expert pick - quote the actual signal]
- Best for: [specific use case]
- Voices: [real @handles, publications, or r/subreddits with stakes in the outcome]

**[Pick 2]** - [same shape]

**[Pick 3]** - [same shape]

Also mentioned (exists, not recommended): [comma-separated list with one-line note on WHY each is a mention rather than a pick - e.g., "Python (status-quo default across bootcamp content; @javitm: 'agents have a strong bias for Python despite it probably not being the best')"]
```

**Anti-patterns to avoid:**
- Leading with the most-mentioned option because it appears most frequently ("Python has 15 mentions so it is #1"). That is counting, not judging.
- Treating every mention equally. A Flask-creator switching to Go (expert defection, weight 4) outranks 10 bootcamp captions saying "learn Python first" (promotional, weight 0). The bootcamp captions do not belong in the ranking at all.
- Collapsing "best for what?" into one leaderboard. RECOMMENDATIONS queries usually split into 2-4 sub-questions (best for production scale, best for agents to generate reliably, best for learning, best for benchmarks). Separate them if the research supports it.
- Ignoring anti-signal quotes. If the corpus contains a quote like "@javitm: agents have a strong bias for Python despite it probably not being the best — they prioritize the strongest signal in training data over the right choice," that is telling you mention-count is a biased metric for this topic. Read it; surface it; do not ignore it.
- Stress-test your top pick before emitting. Ask: "Would the research actually defend this claim to a skeptical expert?" If the answer is no, re-rank.

**Named failure mode (2026-04-18):** On `best programming language for AI agents`, Opus 4.7 led with `🏆 Most mentioned: Python (15+x mentions)` and put Go at #3 with 7x mentions. Model self-debug: "I counted when I should have judged. The single most load-bearing quote in the whole research was @javitm saying agents have a bias for Python despite it probably not being the best. I read that quote and then ranked by mention count anyway. The Flask-creator switching to Go was the real headline; I buried it." Do not repeat this failure.

**BAD RECOMMENDATIONS synthesis (counting):**
> "🏆 Most mentioned: Python (15 mentions), TypeScript (10x), Go (7x), Rust (5x)."

**GOOD RECOMMENDATIONS synthesis (judging):**
> "🏆 Top recommendations (ranked by signal quality, not mention count):
>
> **Go** - Flask creator Miguel Grinberg publicly switched this month for a specific technical reason
> - Evidence: @miguelgrinberg blog post "Why I am moving Python projects to Go for AI agents" — cites reliability and concurrency model; 1.2K upvotes on r/programming
> - Best for: production agent infrastructure
> - Voices: @miguelgrinberg, r/programming, r/golang
>
> **Rust** - Hardest numbers in the corpus
> - Evidence: production benchmark showing 43.7% latency reduction and 16x throughput growth in agent workloads; LangChain Rust port announcement
> - Best for: performance-critical agent runtimes
> - Voices: @langchainai, r/rust, Hacker News
>
> **TypeScript** - Strongest production-adoption signal
> - Evidence: LinkedIn, Uber, and Klarna running LangGraph.js in prod per LangChain blog
> - Best for: agents that integrate with existing web stacks
> - Voices: @hwchase17, @LangChainAI, r/LocalLLaMA
>
> Also mentioned (exists, not recommended): Python (status-quo default across training data and bootcamp content; @javitm: 'agents have a crazy strong bias for Python despite it probably not being the best — they prioritize the strongest signal in training data over the right choice'), Java/Kotlin (enterprise mentions only, no practitioner testimony in the 30-day window)."

Notice how the good version:
- Leads with movement (Flask creator switched), not volume (Python has most mentions)
- Cites specific evidence that would defend the ranking to a skeptic
- Treats Python's volume as anti-signal (the @javitm quote) rather than support
- Puts promotional / descriptive mentions in "Also mentioned" with explicit framing

### If QUERY_TYPE = COMPARISON

**Comparison queries have their OWN synthesis template. Do NOT use the general-query `What I learned:` + bold-lead-in + `KEY PATTERNS:` structure for comparisons.** The comparison template below is the canonical shape proven by the April 9 launch-video exemplar. Follow it section-for-section.

Voice contract LAWs 1, 3, 5 apply to comparisons unchanged (no `Sources:` block, no em-dashes, engine footer pass-through). LAWs 2 and 4 have comparison-specific exceptions (see the LAW block: the comparison title and the five section headers below are REQUIRED, not violations).

**Required comparison structure (match the April 9 exemplar):**

```
# {TOPIC_A} vs {TOPIC_B} [vs {TOPIC_C}]: What the Community Says (Last 30 Days)

## Quick Verdict

[One paragraph. Frame the thesis (are these competitors or layers of a stack? who's dominant? who's challenging?). Include scale stats for each entity inline (GitHub stars, user counts, whatever metric is comparable). End with one quotable community framing — a tweet, a Reddit quote, a YouTube clip — that captures how the community sees the relationship.]

## {Entity 1}

**Community Sentiment:** [Positive / Mixed / Negative / Enthusiastic / Security-concerned / etc.] ({N}+ mentions across {source list})

**Strengths (what people love)**
- [Specific strength with `per <source>` attribution]
- [Specific strength with `per <source>` attribution]
- [Specific strength with `per <source>` attribution]

**Weaknesses (common complaints)**
- [Specific complaint with `per <source>` attribution]
- [Specific complaint with `per <source>` attribution]

## {Entity 2}

[Same structure: Community Sentiment, Strengths bullets, Weaknesses bullets]

## {Entity 3}

[Same structure]

## Head-to-Head

| Dimension | {Entity 1} | {Entity 2} | {Entity 3} |
|---|---|---|---|
| What it is | ... | ... | ... |
| GitHub stars | ... | ... | ... |
| Philosophy | ... | ... | ... |
| Skills | ... | ... | ... |
| Memory | ... | ... | ... |
| Models | ... | ... | ... |
| Security | ... | ... | ... |
| Best for | ... | ... | ... |
| Install | ... | ... | ... |

(Engine emits this scaffold; fill the cells with 5-15 words each. If an axis does not apply to the topic class, write "N/A" or a topic-appropriate substitute rather than inventing data.)

## The Bottom Line

**Choose {Entity 1} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

**Choose {Entity 2} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

**Choose {Entity 3} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

## The emerging stack

[One paragraph. Name the combination pattern the community is converging on. Cite specific sources (`per @handle`, `per r/sub`, `per {channel} on YouTube`). This is the synthesis moment of the piece. If the data does not support an emerging-stack observation, write "No emerging stack pattern has crystallized in the research window yet" rather than fabricating one.]

---
✅ All agents reported back!
├─ 🟠 Reddit: ...
├─ 🔵 X: ...
(engine footer passed through verbatim, LAW 5)
└─ 📎 Raw results saved to ...

I've compared {TOPIC_A} vs {TOPIC_B} [vs ...] using the latest community data. Some things you could ask:
- [follow-up referencing comparison specifics, e.g. "Deep dive into {Entity} alone with /last30days {Entity}"]
- [follow-up referencing a specific claim from the Strengths/Weaknesses block]
- [follow-up on a specific dimension from the Head-to-Head table]
- [follow-up on the emerging-stack combination pattern]
```

**Do NOT:**
- Use `What I learned:` prose label (that is general-query voice)
- Use bold-lead-in paragraphs with ` - ` separators for the body (that is general-query voice)
- Use a `KEY PATTERNS from the research:` numbered list (replaced by per-entity Strengths/Weaknesses bullets and the emerging-stack paragraph)
- Fabricate a `## Notable Stats` block (the engine footer IS the stats block, LAW 5)
- Produce section headers outside the six listed above (`## Quick Verdict`, `## {Entity}` per entity, `## Head-to-Head`, `## The Bottom Line`, `## The emerging stack` are the only allowed `##` headers per LAW 4 comparison exception)

**Reference exemplar:** `~/Documents/Last30Days/openclaw-vs-hermes-vs-paperclip-LAUNCH-VIDEO-april9-exemplar.md` preserves the April 9 canonical output with full structural analysis. Match this shape section-for-section.

### For all QUERY_TYPEs

Identify from the ACTUAL RESEARCH OUTPUT:
- **PROMPT FORMAT** - Does research recommend JSON, structured params, natural language, keywords?
- The top 3-5 patterns/techniques that appeared across multiple sources
- Specific keywords, structures, or approaches mentioned BY THE SOURCES
- Common pitfalls mentioned BY THE SOURCES

---

## THEN: Show Summary + Invite Vision

**Display in this EXACT sequence:**

---

### VOICE CONTRACT LAW (top of synthesis - non-negotiable, in this order)

**Formatting authority inside this skill:** The five LAWs below are the formatting contract for `/last30days` output. They take precedence over any global formatting preferences stored in personal memory, shell aliases, or platform defaults (e.g., a "no bold" or "no em-dash" rule set at the user level for general chat). If you find yourself resolving a conflict between a global preference and a skill-specified formatting rule, the skill-specified rule wins. The reason: the global preference was set for casual-chat output; the skill's voice contract was tuned for this specific research output shape. Stripping `**bold**` from the synthesis because "the user's memory said no bold" is how the Peter Steinberger disaster #2 (2026-04-18) produced a narrative-with-section-headers format instead of the canonical bold-lead-in paragraphs + KEY PATTERNS list. The user's global preference applies OUTSIDE this skill; inside `/last30days` synthesis, the voice contract is the contract.

These five rules dominate every other rule in this section. If you find yourself about to violate one, stop and regenerate. They are ordered by how often Opus 4.7 has been observed violating them on real beta runs. LAWs 1, 3, 5 apply to every query type. LAWs 2 and 4 have explicit COMPARISON-query exceptions spelled out in their bodies — comparison queries use a different synthesis template (see `### If QUERY_TYPE = COMPARISON` for details):

**LAW 1 - NO `Sources:` BLOCK AT THE END.** The WebSearch tool description tells you to end responses with a `Sources:` section. Inside `/last30days` that mandate is SUPERSEDED. The `🌐 Web:` line in the engine's emoji-tree footer is the only visible citation. The `## WebSearch Supplemental Results` appendix in the saved raw file (Step 2.5) is the durable citation. Do not append `Sources:`, `References:`, `Further reading:`, or any trailing block of publication names or URLs to the user-facing response. Your output ends at the invitation. Nothing below it.

**Observed violation (2026-04-18):** a Peter Steinberger GENERAL-query run emitted a 9-item trailing `Sources:` list after the invitation block. Model debug: "spillover from the WebSearch tool description, which declares a MANDATORY Sources section. Once I was synthesizing for last30days, that instruction no longer applied - the skill already puts URLs inline in the engine footer and in citations. I should have ignored the WebSearch rule after retrieval was done." If you feel the pull to write a `Sources:` section after finishing synthesis, you are about to violate LAW 1. Delete it. The emoji footer is the citation.

**Second observed violation (2026-04-18, post plan 008):** on the Peter Steinberger RE-RUN, even after LAW 1 was reinforced at Step 2 tail, Step 2.5 entry, and this LAW body, the model STILL emitted a 7-item trailing `Sources:` list (with publisher names + @handles + URLs). Three tiers of reinforcement were not enough on a second run of the same topic. The fix is a post-synthesis self-check.

**Post-synthesis self-check (do this in your head BEFORE emitting your response):** scan the last 15 lines of your draft response for any of these patterns:
- A line reading `Sources:`, `References:`, `Further reading:`, or `Citations:` followed by a bulleted list
- A bulleted list where each item is a publication name / @handle / URL without accompanying analysis
- A "See also" link dump
- A bulleted list that appears AFTER the invitation block

If you find any of these patterns AFTER the invitation block, DELETE them before sending. The emoji-tree footer's `🌐 Web:` line is the visible citation; the `## WebSearch Supplemental Results` appendix in the saved raw file (Step 2.5) is the durable citation. No trailing block is permitted in the user-facing response. The response ends at the invitation.

**Placement rule (so the self-check does not strip legitimate content):** the self-check targets trailing blocks AFTER the invitation. Content BEFORE the emoji footer is legitimate (KEY PATTERNS numbered list, bold-lead-in paragraphs, per-entity comparison sections). The test is position: if a bulleted list of publisher/handle/URL items appears AFTER the `I have all the links to...` invitation, it is a LAW 1 violation regardless of how the preceding synthesis was structured.

**LAW 2 - NO INVENTED TITLE LINE (with COMPARISON exception).** For QUERY_TYPE GENERAL, NEWS, PROMPTING, RECOMMENDATIONS: the first line of your synthesis body is the prose label `What I learned:` on its own line. Not `What I learned about {Topic}`, not `{Topic} - Last 30 Days`, not `{Topic}: What People Are Saying`, not `# {Topic}`. Nothing above `What I learned:`. If you are tempted to write a title, the rule is: there is no title.

**COMPARISON exception:** For QUERY_TYPE=COMPARISON (topics containing `vs` or `versus`), the title `# {TOPIC_A} vs {TOPIC_B} [vs {TOPIC_C}]: What the Community Says (Last 30 Days)` is REQUIRED, not a violation. See the `### If QUERY_TYPE = COMPARISON` section for the full required structure. Comparison queries do NOT use the `What I learned:` prose label at all.

**Global-preference override note:** The skill-authored template for GENERAL / NEWS / PROMPTING / RECOMMENDATIONS queries uses `**bold**` for KEY PATTERNS items and for mid-paragraph lead-ins. Do NOT strip this bold on the grounds that a personal memory said "no bold" globally. The skill's voice contract is the formatting authority here. Peter Steinberger disaster #2 (2026-04-18) happened because the model resolved the conflict as "memory wins" and stripped all bold; the correct resolution is "skill template wins inside skill output."

**LAW 3 - NO EM-DASHES OR EN-DASHES.** Use ` - ` (single hyphen with spaces on both sides) instead of `—` or `–`. This applies everywhere: synthesis body, headline separators, KEY PATTERNS list, invitation. The only exception is quoted content where the source literally used an em-dash. Em-dashes are the most reliable AI-slop tell; a response with em-dashes reads as machine-generated to the user.

**LAW 4 - NO `##` or `###` SECTION HEADERS IN BODY (with COMPARISON exception).** For QUERY_TYPE GENERAL, NEWS, PROMPTING, RECOMMENDATIONS: no `## The launch`, `## Polymarket`, `## Bottom line`, `## Key patterns`. The narrative is bold-lead-in paragraphs (LAW rule below), then the prose label `KEY PATTERNS from the research:`, then a numbered list. That is the only structure. No subheadings. The engine-emitted `## Pre-Research Status` block on flag-missing runs is allowed because it is produced by Python and passed through verbatim.

**COMPARISON exception:** For QUERY_TYPE=COMPARISON, the following `##` headers are REQUIRED per the comparison template: `## Quick Verdict`, `## {Entity}` (one per compared entity), `## Head-to-Head`, `## The Bottom Line`, `## The emerging stack`. Any other `##` header is still forbidden (no `## Notable Stats`, no `## Key Takeaways`, no `## Sources`, no `## Conclusion`). See the `### If QUERY_TYPE = COMPARISON` section for the full template.

**Observed violation (2026-04-18, Peter Steinberger disaster #2):** the model emitted `Headline`, `What he is actually saying (primary voice)`, `Cross-source corroboration`, `Where evidence is thin`, `Bottom line` — every one of those is a LAW 4 violation on a GENERAL query. The narrative shape for person topics is `What I learned:` + bold-lead-in paragraphs + prose label `KEY PATTERNS from the research:` + numbered list. No blog-post subheadings. If you are about to write `## Headline` or `## Bottom line` on a non-comparison topic, stop. That is a LAW 4 violation.

**LAW 5 - ENGINE FOOTER PASS-THROUGH. EVERY QUERY TYPE. EVERY RUN.** The engine output ends with a `✅ All agents reported back!` emoji-tree footer bounded by `---` lines. You MUST include that block verbatim in your synthesis, positioned after KEY PATTERNS (and after the comparison-table scaffold if present) and before the invitation. Do not recompute the stats, do not reformat the tree, do not paraphrase, do not skip it, do not fabricate your own `## Notable Stats` replacement. This applies to every QUERY_TYPE: GENERAL, COMPARISON, RECOMMENDATIONS, PROMPTING, NEWS. A response without the engine footer is not valid skill output.

End of voice contract law. The rest of this section is detail; the laws above are the contract.

---

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
- **CRITICAL whitespace rule:** Never insert more than ONE blank line between any two content blocks. Comparison tables should immediately follow the preceding paragraph with exactly one blank line. Do NOT pad with 3-6 empty lines before tables.

**If PROMPTING/NEWS/GENERAL** - Show synthesis and patterns:

CITATION RULE: Cite sources sparingly to prove research is real.
- In the "What I learned" intro: cite 1-2 top sources total, not every sentence
- In KEY PATTERNS: cite 1 source per pattern, short format: "per @handle" or "per r/sub"
- Do NOT include engagement metrics in citations (likes, upvotes) - save those for stats box
- Do NOT chain multiple citations: "per @x, @y, @z" is too much. Pick the strongest one.

CITATION PRIORITY (most to least preferred):
1. @handles from X - "per @handle" (these prove the tool's unique value)
2. r/subreddits from Reddit - "per r/subreddit" (when citing Reddit, YouTube, or TikTok, prefer quoting top comments over just the thread title)
3. YouTube channels - "per [channel name] on YouTube" (transcript-backed insights)
4. TikTok creators - "per @creator on TikTok" (viral/trending signal)
5. Instagram creators - "per @creator on Instagram" (influencer/creator signal)
6. HN discussions - "per HN" or "per hn/username" (developer community signal)
7. Polymarket - "Polymarket has X at Y% (up/down Z%)" with specific odds and movement
8. Web sources - ONLY when Reddit/X/YouTube/TikTok/Instagram/HN/Polymarket don't cover that specific fact

The tool's value is surfacing what PEOPLE are saying, not what journalists wrote.
When both a web article and an X post cover the same fact, cite the X post.

URL FORMATTING: NEVER paste raw URLs anywhere in the output - not in synthesis, not in stats, not in sources.
- **BAD:** "per https://www.rollingstone.com/music/music-news/kanye-west-bully-1235506094/"
- **GOOD:** "per Rolling Stone"
- **BAD stats line:** `🌐 Web: 10 pages - https://later.com/blog/..., https://buffer.com/...`
- **GOOD stats line:** `🌐 Web: 10 pages - Later, Buffer, CNN, SocialBee`
Use the publication/site name, not the URL. The user doesn't need links - they need clean, readable text.

**BAD:** "His album is set for March 20 (per Rolling Stone; Billboard; Complex)."
**GOOD:** "His album BULLY drops March 20 - fans on X are split on the tracklist, per @honest30bgfan_"
**GOOD:** "Ye's apology got massive traction on r/hiphopheads"
**OK** (web, only when Reddit/X don't have it): "The Hellwatt Festival runs July 4-18 at RCF Arena, per Billboard"

**Lead with people, not publications.** Start each topic with what Reddit/X
users are saying/feeling, then add web context only if needed. The user came
here for the conversation, not the press release.

**MANDATORY - bold headline per narrative paragraph.** Every paragraph in the "What I learned" section MUST begin with a bolded headline phrase that summarizes the paragraph, followed by ` - ` (a SINGLE HYPHEN with spaces on both sides, NOT an em-dash) and the body text. Pattern: `**Headline phrase** - body text describing what people are saying...`. Without the bold headline, the output is unscannable slop.

**NEVER use em-dashes (`—`) or en-dashes (`–`) anywhere in your response.** Use ` - ` (single hyphen with spaces) instead. Em-dashes are the most reliable AI-slop tell; a response with em-dashes reads as generated. This applies to synthesis body, headline separators, KEY PATTERNS list, and the invitation section. The only exception is quoted content where the source used an em-dash.

**NEVER use `##` or `###` markdown section headers in your response body.** No `## The launch`, no `## Where it disappoints`, no `## Polymarket`, no `## Best quotes`, no `## Stats snapshot`. Those read as AI-slop news-article structure. The narrative is a short block of bold-lead-in paragraphs followed by a prose label `KEY PATTERNS from the research:` followed by a numbered list. That is the only structure.

**NEVER write a title line at the top of your response.** No `Kanye West: last 30 days`, no `Claude Opus 4.7 - what people are actually saying`, no `{Topic} news`. Your response begins with the prose label `What I learned:` on its own line and goes straight into the narrative.

```
What I learned:

**{Headline summarizing topic 1}** - [1-2 sentences about what people are saying, per @handle or r/sub]

**{Headline summarizing topic 2}** - [1-2 sentences, per @handle or r/sub]

**{Headline summarizing topic 3}** - [1-2 sentences, per @handle or r/sub]

KEY PATTERNS from the research:
1. [Pattern] - per @handle
2. [Pattern] - per r/sub
3. [Pattern] - per @handle
```

Headlines should be specific and newsy ("BULLY dropped and it's dominating", "Europe is banning him one country at a time"), not generic ("Album release", "Tour updates").

**THEN - Quality Nudge (if present in the output):**

If the research output contains a `**🔍 Research Coverage:**` block, render it verbatim right before the stats block. This tells the user which core sources are missing and how to unlock them. Do NOT render this block if it is absent from the output (100% coverage = no nudge).

**Just-in-time X unlock:** If X returned 0 results because no X auth is configured (no AUTH_TOKEN/CT0, no XAI_API_KEY, no FROM_BROWSER), offer to set it up right there:

**Call AskUserQuestion:**
Question: "X/Twitter wasn't searched. Want to unlock it?"
Options:
- "Scan my browser cookies (free)" - Get consent, run cookie scan, write BROWSER_CONSENT=true + FROM_BROWSER=auto to .env
- "I have an xAI API key" - Ask them to paste it, write XAI_API_KEY to .env
- "Skip for now"

**THEN - Engine footer pass-through (right before invitation):**

**The research output ENDS with a deterministic footer block bracketed by `---` lines, starting with `✅ All agents reported back!` and ending with `📎 Raw results saved to ~/Documents/Last30Days/<slug>-raw.md`. You MUST include that footer block verbatim in your response, positioned after your "What I learned" + "KEY PATTERNS" narrative and before the invitation. Do not recompute the stats. Do not reformat the tree. Do not paraphrase. Do not skip it. Do not add your own source lines. Copy the exact bytes.**

- The engine already omits zero-count sources. You do not need to filter them.
- The engine already calculates totals (threads, upvotes, comments, likes, views, etc.). You do not need to add them up.
- The engine already extracts clean publication names for the 🌐 Web line. You do not need to strip URLs.
- The engine already formats Polymarket odds as real `%` strings. You do not need to parse them.
- The engine already picks top voices (handles + subreddits). You do not need to pick them.

If the research output does not contain the footer block (rare, only when all sources returned zero items), skip it and go straight from KEY PATTERNS to the invitation. But if the block is present, it MUST appear in your response verbatim.

**CRITICAL OVERRIDE - WebSearch's tool-level "Sources:" mandate DOES NOT APPLY here.** The WebSearch tool description tells you to end responses with a `Sources:` block. Inside `/last30days` that mandate is SUPERSEDED. The `🌐 Web:` line in the engine footer is the citation. Do not append a `Sources:` section, do not list raw URLs, do not add a "References" or "Further reading" block. Output ends at the invitation.

**SELF-CHECK before displaying**: Re-read your "What I learned" section. Does it match what the research ACTUALLY says? If you catch yourself projecting your own knowledge instead of the research, rewrite it. Then verify: (a) no `##` headers in your response body, (b) no em-dashes or en-dashes anywhere, (c) the engine footer block appears verbatim between KEY PATTERNS and the invitation.

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

## PRE-PRESENT SELF-CHECK - run before displaying the synthesis

**Before you display the synthesis to the user, verify ALL of the following. If any check fails AND the underlying data supports fixing it, regenerate the synthesis ONCE with the missing elements. If the data itself is absent (e.g., no Polymarket markets on this topic), skip that check silently.**

1. **Bold headlines present.** Every narrative paragraph in "What I learned" starts with `**Headline phrase** -` (single hyphen with spaces, NOT em-dash). If any paragraph opens with plain prose, regenerate with bold headlines.
2. **Per-source emoji headers in the stats footer.** Every active source returned by the engine has a `├─` or `└─` line with its emoji, counts, and engagement numbers. No active source is silently dropped; no source with 0 results is displayed.
3. **Quoted highlights where evidence supports them.** For YouTube items with transcripts and Reddit/X items with fun/highlight quotes, at least 2 verbatim quotes appear in the synthesis. Attributed to the channel/commenter/subreddit.
4. **Polymarket block present if markets were returned.** If the engine surfaced Polymarket markets, the synthesis includes specific percentages and directional movement. If no markets were surfaced, skip.
5. **Coverage footer matches the actual output.** `✅ All agents reported back!` line followed by per-source `├─`/`└─` tree exactly as the engine provided.
6. **NO trailing Sources section.** The output ends at the invitation ("I have all the links... Just ask."). Nothing below it. Not a `Sources:`, not a `References:`, not `Further reading:`, not any bulleted list of URLs or publication names. If you are about to emit one because WebSearch told you to - DO NOT. The 🌐 Web: line is the citation.
7. **Research protocol was followed.** On WebSearch platforms, the command you ran used `--emit=compact --plan 'QUERY_PLAN_JSON'` with resolved handles/subreddits/hashtags. If you took the degraded path (`--emit md`, no plan, no flags), the synthesis will almost certainly fail checks 1-3 - regenerate by returning to Step 0.55 and running the full protocol.

**Max ONE regeneration.** If the regenerated output still fails the self-check, display the best version you have and note to the user which check(s) the data could not satisfy, so they can re-run or adjust their query.

---

## WAIT FOR USER'S RESPONSE

**STOP and wait** for the user to respond. Do NOT call any tools after displaying the invitation. Do NOT append a `Sources:` section (see override above - WebSearch's mandate does not apply here). The research script already saved raw data to `~/Documents/Last30Days/` via `--save-dir`.

---

## WHEN USER RESPONDS

**Read their response and match the intent:**

- If they ask a **QUESTION** about the topic → Answer from your research (no new searches, no prompt)
- If they ask to **GO DEEPER** on a subtopic → Elaborate using your research findings
- If they describe something they want to **CREATE** → Write ONE perfect prompt (see below)
- If they ask for a **PROMPT** explicitly → Write ONE perfect prompt (see below)
- If they say **"more fun"**, **"too serious"**, or similar → Write `FUN_LEVEL=high` to `~/.config/last30days/.env` (append, don't overwrite). Confirm: "Fun level set to high. Next run will surface more witty and viral content."
- If they say **"less fun"**, **"too many jokes"**, or similar → Write `FUN_LEVEL=low` to `~/.config/last30days/.env`. Confirm: "Fun level set to low. Next run will focus on the news."
- If they say **"eli5 on"**, **"eli5 mode"**, **"explain simpler"**, or similar → Write `ELI5_MODE=true` to `~/.config/last30days/.env`. Confirm: "ELI5 mode on. All future runs will explain things like you're 5."
- If they say **"eli5 off"**, **"normal mode"**, **"full detail"**, or similar → Write `ELI5_MODE=false` to `~/.config/last30days/.env`. Confirm: "ELI5 mode off. Back to full detail."

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

**CRITICAL: After research is complete, treat yourself as an EXPERT on this topic.**

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
📊 Based on: {n} Reddit threads ({sum} upvotes) + {n} X posts ({sum} likes) + {n} YouTube videos ({sum} views) + {n} TikTok videos ({sum} views) + {n} Instagram reels ({sum} views) + {n} HN stories ({sum} points) + {n} web pages

Want another prompt? Just tell me what you're creating next.
```

---

## Security & Permissions

**What this skill does:**
- Sends search queries to ScrapeCreators API (`api.scrapecreators.com`) for TikTok and Instagram search, and as a Reddit backup when public Reddit is unavailable (requires SCRAPECREATORS_API_KEY)
- Legacy: Sends search queries to OpenAI's Responses API (`api.openai.com`) for Reddit discovery (fallback if no SCRAPECREATORS_API_KEY)
- Sends search queries to Twitter's GraphQL API (via optional user-provided AUTH_TOKEN/CT0 env vars - no browser session access) or xAI's API (`api.x.ai`) for X search
- Sends search queries to Algolia HN Search API (`hn.algolia.com`) for Hacker News story and comment discovery (free, no auth)
- Sends search queries to Polymarket Gamma API (`gamma-api.polymarket.com`) for prediction market discovery (free, no auth)
- Runs `yt-dlp` locally for YouTube search and transcript extraction (no API key, public data)
- Sends search queries to ScrapeCreators API (`api.scrapecreators.com`) for TikTok and Instagram search, transcript/caption extraction (PAYG after 10,000 free API calls)
- Optionally sends search queries to Brave Search API, Parallel AI API, or OpenRouter API for web search
- Fetches public Reddit thread data from `reddit.com` for engagement metrics
- Stores research findings in local SQLite database (watchlist mode only)
- Saves research briefings as .md files to ~/Documents/Last30Days/

**What this skill does NOT do:**
- Does not post, like, or modify content on any platform
- Does not access your Reddit, X, or YouTube accounts
- Does not share API keys between providers (OpenAI key only goes to api.openai.com, etc.)
- Does not log, cache, or write API keys to output files
- Does not send data to any endpoint not listed above
- Hacker News and Polymarket sources are always available (no API key, no binary dependency)
- TikTok and Instagram sources require SCRAPECREATORS_API_KEY (10,000 free API calls, then PAYG). Reddit uses ScrapeCreators only as a backup when public Reddit is unavailable.
- Can be invoked autonomously by agents via the Skill tool (runs inline, not forked); pass `--agent` for non-interactive report output

**Bundled scripts:** `scripts/last30days.py` (main research engine), `scripts/lib/` (search, enrichment, rendering modules), `scripts/lib/vendor/bird-search/` (vendored X search client, MIT licensed)

Review scripts before first use to verify behavior.
