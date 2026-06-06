
WebSearch is a **deferred tool** in Claude Code v2.1.114. The frontmatter of this file authorizes it (`allowed-tools: ... WebSearch`) but the runtime lists it as "schemas are NOT loaded." Calling WebSearch without `ToolSearch select:WebSearch` first will fail or do nothing. That friction is the documented cause of the second-most-common failure mode of this skill: the model sees "WebSearch is there but deferred," takes the low-friction path, skips Step 0.5 and 0.55, and runs the engine bare with only keyword search. The output looks fine but misses founder X timelines, GitHub repo activity, and subreddit-specific threads.

Load WebSearch first. No exceptions. Then proceed to the branching rule below.

## Claude Code Stale-Clone Self-Check

If the `SKILL.md` path you loaded contains `/.claude/plugins/marketplaces/`, run this before continuing:

```bash
CLAUDE_CACHE_LATEST=$(find "$HOME/.claude/plugins/cache/last30days-skill/last30days" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -1)
CLAUDE_CACHE_SKILL_MD=""
if [ -n "$CLAUDE_CACHE_LATEST" ]; then
  if [ -f "$CLAUDE_CACHE_LATEST/skills/last30days/SKILL.md" ]; then
    CLAUDE_CACHE_SKILL_MD="$CLAUDE_CACHE_LATEST/skills/last30days/SKILL.md"
  elif [ -f "$CLAUDE_CACHE_LATEST/SKILL.md" ]; then
    CLAUDE_CACHE_SKILL_MD="$CLAUDE_CACHE_LATEST/SKILL.md"
  fi
fi
echo "CLAUDE_CACHE_SKILL_MD=$CLAUDE_CACHE_SKILL_MD"
```

If `CLAUDE_CACHE_SKILL_MD` is non-empty, stop and re-read that cached `SKILL.md` before proceeding. The stale path is Claude Code's `~/.claude/plugins/marketplaces/last30days-skill/` clone, which can lag the versioned cache after session start. Other install paths are valid.

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

# last30days v3.3.1: Research Any Topic from the Last 30 Days

> **Permissions overview:** Reads public web/platform data and optionally saves research briefings to `LAST30DAYS_MEMORY_DIR` (defaults to `~/Documents/Last30Days`). X/Twitter search uses optional user-provided tokens (AUTH_TOKEN/CT0 env vars). Bluesky search uses optional app password (BSKY_HANDLE/BSKY_APP_PASSWORD env vars - create at bsky.app/settings/app-passwords). All credential usage and data writes are documented in the [Security & Permissions](#security--permissions) section.

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

LAST30DAYS_MEMORY_DIR="${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"
```

## Configuration

Set `LAST30DAYS_MEMORY_DIR` before invoking the skill to choose where raw research files are saved. If it is not set, the skill defaults to `~/Documents/Last30Days`.

## Step 0: First-Run Setup Wizard

Before proceeding to Step 1, handle first-run setup.

**First-run detection (silent, no commands, no output to user):**
- If `~/.config/last30days/.env` does NOT exist, this is a first run.
- If the file exists and contains `SETUP_COMPLETE=true`, skip Step 0 entirely and go to Step 1 (CRITICAL: Parse User Intent below). Do NOT announce that setup is complete. The user does not need a status message on every run.

**If this IS a first run:**
- Run the engine setup path with the resolved interpreter: `"${LAST30DAYS_PYTHON}" "$SKILL_ROOT/scripts/last30days.py" setup`.
- For OpenClaw server-side setup, add `--openclaw`. For GitHub/ScrapeCreators auth, use `setup --github` or `setup --device-auth` only when the user asked for that setup path.
- After setup writes `SETUP_COMPLETE=true` to `~/.config/last30days/.env`, proceed to research.

The implementation lives in `scripts/lib/setup_wizard.py`. Post-research invitations and prompt follow-ups live in `references/nux-wizard.md`; do not read that file for first-run setup.

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
- If digg-pp-cli is installed (check `which digg-pp-cli`): add Digg
- If AUTH_TOKEN/CT0 or XAI_API_KEY or FROM_BROWSER is set, or xurl CLI is installed and authenticated: add X
- If yt-dlp is installed (check `which yt-dlp`): add YouTube
- If SCRAPECREATORS_API_KEY is set: add TikTok, Instagram, Threads (suppress any of these via EXCLUDE_SOURCES)
- If SCRAPECREATORS_API_KEY is set and the user explicitly requested pinterest for this query (e.g. via `--search=pinterest`): add Pinterest
- If BSKY_HANDLE and BSKY_APP_PASSWORD are set: add Bluesky
- If OPENROUTER_API_KEY is set and INCLUDE_SOURCES contains perplexity: add Perplexity
- If EXCLUDE_SOURCES is set (comma-separated, case-insensitive): drop any matching source from the list above before displaying

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

---

## Security & Permissions

**What this skill does:**
- Sends search queries to ScrapeCreators API (`api.scrapecreators.com`) for TikTok and Instagram search, and as a Reddit backup when public Reddit is unavailable (requires SCRAPECREATORS_API_KEY)
- Legacy: Sends search queries to OpenAI's Responses API (`api.openai.com`) for Reddit discovery (fallback if no SCRAPECREATORS_API_KEY)
- Sends search queries to Twitter's GraphQL API (via optional user-provided AUTH_TOKEN/CT0 env vars - no browser session access), xAI's API (`api.x.ai`), or the official X API v2 via xurl CLI (OAuth2, auto-detected when installed and authenticated) for X search
- Sends search queries to Algolia HN Search API (`hn.algolia.com`) for Hacker News story and comment discovery (free, no auth)
- Sends search queries to Polymarket Gamma API (`gamma-api.polymarket.com`) for prediction market discovery (free, no auth)
- Runs `yt-dlp` locally for YouTube search and transcript extraction (no API key, public data)
- Sends search queries to ScrapeCreators API (`api.scrapecreators.com`) for TikTok and Instagram search, transcript/caption extraction (PAYG after 100 free credits)
- Optionally sends search queries to Brave Search API, Parallel AI API, or OpenRouter API for web search
- Fetches public Reddit thread data from `reddit.com` for engagement metrics
- Stores research findings in local SQLite database (watchlist mode only)
- Saves research briefings as .md files to `LAST30DAYS_MEMORY_DIR` (defaults to `~/Documents/Last30Days`)

**What this skill does NOT do:**
- Does not post, like, or modify content on any platform
- Does not access your Reddit, X, or YouTube accounts
- Does not share API keys between providers (OpenAI key only goes to api.openai.com, etc.)
- Does not log, cache, or write API keys to output files
- Does not send data to any endpoint not listed above
- Hacker News and Polymarket sources are always available (no API key, no binary dependency)
- TikTok and Instagram sources require SCRAPECREATORS_API_KEY (100 free credits one-time, then PAYG). Reddit uses ScrapeCreators only as a backup when public Reddit is unavailable.
- Can be invoked autonomously by agents via the Skill tool (runs inline, not forked); pass `--agent` for non-interactive report output

**Bundled scripts:** `scripts/last30days.py` (main research engine), `scripts/lib/` (search, enrichment, rendering modules), `scripts/lib/vendor/bird-search/` (vendored X search client, MIT licensed)

Review scripts before first use to verify behavior.
