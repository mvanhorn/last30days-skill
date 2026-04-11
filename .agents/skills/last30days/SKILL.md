---
name: last30days
version: "3.0.0"
description: "Multi-query social search with intelligent planning. Agent plans queries when possible, falls back to Gemini/OpenAI when not. Research any topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, and the web."
argument-hint: 'last30days AI video tools, last30days best noise cancelling headphones'
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

# last30days v2.9.5: Research Any Topic from the Last 30 Days

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

**CRITICAL: ALWAYS execute Step 0 BEFORE Step 1, even if the user provided a topic.** If the user typed `/last30days Mercer Island`, you MUST check for FIRST_RUN and present the wizard BEFORE running research. The topic "Mercer Island" is preserved — research runs immediately after the wizard completes. Do NOT skip the wizard because a topic was provided. The wizard takes 10 seconds and only runs once ever.

To detect first run: check if `~/.config/last30days/.env` exists. If it does NOT exist, this is a first run. **Do NOT run any Bash commands or show any command output to detect this — just check the file existence silently.** If the file exists and contains `SETUP_COMPLETE=true`, skip this section **silently** and proceed to Step 1. **Do NOT say "Setup is complete" or any other status message — just move on.** The user doesn't need to be told setup is done every time they run the skill.

**When first run is detected, detect your platform first:**

**If you do NOT have WebSearch capability (OpenClaw, Codex, raw CLI):** Run the OpenClaw setup flow below.
**If you DO have WebSearch (Claude Code):** Run the standard setup flow below.

---

### OpenClaw / Non-WebSearch Setup Flow

Run environment detection first:
```bash
"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" setup --openclaw
```

Read the JSON output. It tells you what's already configured. Display a status summary:

```
👋 Welcome to /last30days!

Detected:
{✅ or ❌} yt-dlp (YouTube search)
{✅ or ❌} X/Twitter ({method} configured)
{✅ or ❌} ScrapeCreators (TikTok, Instagram, Reddit backup)
{✅ or ❌} Web search ({backend} configured)
```

Then for each missing item, offer setup in priority order:

1. **ScrapeCreators** (if not configured): "ScrapeCreators adds TikTok and Instagram search (plus a Reddit backup if public Reddit gets rate-limited). 10,000 free calls, no credit card. (No referrals, no kickbacks - we don't get a cut.)"
   - Option A: "ScrapeCreators via GitHub (recommended)" -- Check if `gh` CLI was detected in the environment detection output above. If gh IS detected: description should say "Registers directly via GitHub CLI in ~2 seconds - no browser needed". Before running the command, display: "Registering via GitHub CLI..." If gh is NOT detected: description should say "Copies a one-time code to your clipboard and opens GitHub to authorize". Before running the command, display: "I'll copy a one-time code to your clipboard and open GitHub. When GitHub asks for a device code, just paste (Cmd+V / Ctrl+V)." Then run `"${LAST30DAYS_PYTHON}" "${SKILL_ROOT}/scripts/last30days.py" setup --github`, parse JSON output. Tries PAT first (if `gh` is installed), falls back to device flow which copies a one-time code to your clipboard and opens your browser. If `status` is `success`, write `SCRAPECREATORS_API_KEY={api_key}` to .env.
   - Option B: "I have a key" -- accept paste, write to .env
   - Option C: "Skip for now"

2. **X/Twitter** (if not configured): "X search finds tweets and conversations. To unlock X: add FROM_BROWSER=auto (reads browser cookies, free), XAI_API_KEY (no browser access, api.x.ai), or AUTH_TOKEN+CT0 (manual cookies)."
   - Option A: "I have an xAI API key" (recommended for servers -- persistent, no expiry). Write XAI_API_KEY to .env.
   - Option B: "I have AUTH_TOKEN + CT0 from my browser" -- accept both, write to .env
   - Option C: "Skip for now"

3. **YouTube** (if yt-dlp not found): "YouTube search needs yt-dlp. Run: `pip install yt-dlp`"

4. **Web search** (if no Brave/Exa/Serper key): "A web search key enables smarter results. Brave Search is free for 2,000 queries/month at brave.com/search/api"

After setup, write `SETUP_COMPLETE=true` to .env and proceed to research.

**Skip to "END OF FIRST-RUN WIZARD" below after completing the OpenClaw flow.**

---

### Claude Code Setup Flow (Standard)

**You MUST follow these steps IN ORDER. Do NOT skip ahead to the topic picker or research. The sequence is: (1) welcome text -> (2) setup modal -> (3) run setup if chosen -> (4) optional ScrapeCreators modal -> (5) topic picker. You MUST start at step 1.**

**Step 1: Display the following welcome text ONCE as a normal message (not blockquoted). Then IMMEDIATELY call AskUserQuestion - do NOT repeat any of the welcome text inside the AskUserQuestion call.**

Welcome to /last30days!

I research any topic across Reddit, X, YouTube, and other sources - synthesizing what people are actually saying right now.

Auto setup gives you 5 core sources for free in 30 seconds:
- X/Twitter - reads your x.com browser cookies to authenticate (not saved to disk). Chrome on macOS will prompt for Keychain access.
- Reddit with comments - public JSON, no API key needed
- YouTube search + transcripts - installs yt-dlp (open source, 190K+ GitHub stars)
- Hacker News + Polymarket + GitHub (if `gh` CLI installed) - always on, zero config

Want TikTok and Instagram too? ScrapeCreators adds those (10,000 free calls, scrapecreators.com). No kickbacks, no affiliation.

**Then call AskUserQuestion with ONLY this question and these options - no additional text:**

Question: "How would you like to set up?"
Options:
- "Auto setup (~30 seconds) - scans browser cookies for X + installs yt-dlp for YouTube"
- "Manual setup - show me what to configure"
- "Skip for now - Reddit (with comments), HN, Polymarket, GitHub (if gh installed), Web"

**If the user picks 1 (Auto setup):**

**Before running the setup command, get cookie consent:**

Check if `BROWSER_CONSENT=true` already exists in `~/.config/last30days/.env`. If it does, skip the consent prompt and run setup directly.

If `BROWSER_CONSENT=true` is NOT present, **call AskUserQuestion:**
Question: "Auto setup will scan your browser for x.com cookies to authenticate X search. Cookies are read live, not saved to disk. Chrome on macOS will prompt for Keychain access. OK to proceed?"
Options:
- "Yes, scan my cookies for X" - Run setup as normal. Append `BROWSER_CONSENT=true` to .env after setup completes.
- "Skip X, just set up YouTube" - Run setup with YouTube only (install yt-dlp). Do not scan cookies.
- "I have an xAI API key instead" - Ask them to paste it, write XAI_API_KEY to .env. Then install yt-dlp.

Run the setup subcommand:
```bash
cd {SKILL_DIR} && "${LAST30DAYS_PYTHON}" scripts/last30days.py setup
```
Show the user the results (what cookies were found, whether yt-dlp was installed).

**Then show the optional ScrapeCreators offer (plain text, then modal):**

Want TikTok and Instagram too? ScrapeCreators adds those platforms - 10,000 free calls, no credit card. It also serves as a Reddit backup if public Reddit ever gets rate-limited.

**Before showing the ScrapeCreators modal, check for `gh` CLI:** Run `which gh` via Bash silently. Store the result as gh_available (true if found, false if not).

**Call AskUserQuestion:**
Question: "Want to add TikTok, Instagram, and Reddit backup via ScrapeCreators? (We don't get a cut.)"
Options:
- "ScrapeCreators via GitHub (fastest, recommended)" - If gh_available: description should say "Registers directly via GitHub CLI in ~2 seconds - no browser needed". If NOT gh_available: description should say "Copies a one-time code to your clipboard and opens GitHub to authorize". After the user selects this option: If gh_available, display "Registering via GitHub CLI..." before running the command. If NOT gh_available, display "I'll copy a one-time code to your clipboard and open GitHub. When GitHub asks for a device code, just paste (Cmd+V on Mac, Ctrl+V on Windows/Linux)." Then run `cd {SKILL_DIR} && "${LAST30DAYS_PYTHON}" scripts/last30days.py setup --github` via Bash with a 5-minute timeout. This tries PAT auth first (if `gh` CLI is installed, zero browser needed), then falls back to GitHub device flow which copies a one-time code to your clipboard and opens GitHub in your browser. Parse the JSON stdout. If `status` is `success`, write `SCRAPECREATORS_API_KEY={api_key}` to `~/.config/last30days/.env`. If `method` is `pat`, show: "You're in! Registered via GitHub CLI - zero browser needed. 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `method` is `device` and `clipboard_ok` is true, show: "You're in! (The authorization code was copied to your clipboard automatically.) 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `method` is `device` and `clipboard_ok` is false, show: "You're in! 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `status` is `timeout` or `error`, show: "GitHub auth didn't complete. No worries - you can sign up at scrapecreators.com instead or try again later." Then offer the web signup option.
- "Open scrapecreators.com (Google sign-in)" - run `open https://scrapecreators.com` via Bash to open in the user's browser. Then ask them to paste the API key they get. When they paste it, write SCRAPECREATORS_API_KEY={key} to ~/.config/last30days/.env
- "I have a key" - accept the key, write to .env
- "Skip for now" - proceed without ScrapeCreators

**After SC key is saved (not if skipped), show the TikTok/Instagram opt-in:**

Your ScrapeCreators key powers TikTok, Instagram, Threads, Pinterest, and YouTube comments. Want those on for every research run? (Each additional source uses a ScrapeCreators call per search.)

**Call AskUserQuestion:**
Question: "Which ScrapeCreators sources do you want on?"
Options:
- "TikTok + Instagram (recommended)" - append `INCLUDE_SOURCES=tiktok,instagram` to ~/.config/last30days/.env. Confirm: "TikTok and Instagram are on, plus Reddit backup if public Reddit has issues. You can add threads, pinterest, youtube_comments to INCLUDE_SOURCES anytime."
- "Everything - TikTok, Instagram, Threads, Pinterest, YouTube comments" - append `INCLUDE_SOURCES=tiktok,instagram,threads,pinterest,youtube_comments` to ~/.config/last30days/.env. Confirm: "All ScrapeCreators sources are on."
- "Just the basics - let's run our first search" - don't write the flag. Confirm: "Got it. ScrapeCreators will serve as Reddit backup. You can add sources to INCLUDE_SOURCES in your .env anytime."

**After TikTok/Instagram opt-in (or SC skip), show the first research topic modal:**

**Call AskUserQuestion:**
Question: "What do you want to research first?"
Options:
- "Claude Code vs Codex" - tech comparison
- "Sam Altman" - person in the news
- "Warriors Basketball" - sports
- "AI Legal Prompting Techniques" - niche/professional
- "Type my own topic"

If user picks an example, run research with that topic. If they pick "Type my own", ask them what they want to research. If the user originally provided a topic with the command (e.g., `/last30days Mercer Island`), skip this modal and use their topic directly.

**END OF FIRST-RUN WIZARD. Everything above in Step 0 ONLY runs on first run. If SETUP_COMPLETE=true exists in .env, skip ALL of Step 0 — no welcome, no setup, no ScrapeCreators modal, no topic picker. Go directly to Step 1 (Parse User Intent). The topic picker is ONLY for first-time users who haven't run /last30days before.**

**If the user picks 2 (Manual setup):**
Show them this guide (present as plain text, not blockquoted):

The magic of /last30days is Reddit comments + X posts together - and both are free. Here's how to unlock each source.

Add these to `~/.config/last30days/.env`:

X/Twitter (pick one - this is the most important):
- `FROM_BROWSER=auto` - free. Reads your x.com login cookies at search time to authenticate. Cookies are read live each run, not saved to disk. Chrome on macOS will prompt for Keychain access the first time. Firefox and Safari don't.
- `XAI_API_KEY=xxx` - no browser access needed. Get a key at api.x.ai. Best for servers or if you don't want cookie scanning.
- `AUTH_TOKEN=xxx` + `CT0=xxx` - paste your X cookies manually (x.com -> F12 -> Application -> Cookies)

Reddit (free, works out of the box):
- Public JSON gives you threads + top comments with upvote counts. No setup required.
- `SCRAPECREATORS_API_KEY=xxx` - optional backup source if public Reddit gets rate-limited.
- `OPENAI_API_KEY=xxx` - optional fallback if public Reddit search has trouble finding threads.

YouTube (free, open source):
- Run `brew install yt-dlp` - free, open source, 190K+ GitHub stars. Enables YouTube search and transcripts.

Bonus: TikTok, Instagram, Threads, Pinterest, YouTube comments (ScrapeCreators):
- `SCRAPECREATORS_API_KEY=xxx` - 10,000 free calls at scrapecreators.com.
- After adding your key, set `INCLUDE_SOURCES=tiktok,instagram` to turn on the most popular ones. Add threads, pinterest, youtube_comments for more.

GitHub Issues/PRs (free, no key needed):
- If you have the `gh` CLI installed (`brew install gh`), GitHub search is automatic. No API key required.

Perplexity Sonar Pro (AI-synthesized research via OpenRouter):
- `OPENROUTER_API_KEY=xxx` - adds AI-synthesized research with citations as an additive source alongside Reddit/X/YouTube. Returns structured narratives with specific dates, names, and numbers that social sources miss. ~$0.02/run.
- After adding your key, set `INCLUDE_SOURCES=perplexity` (or append to existing, e.g. `INCLUDE_SOURCES=tiktok,instagram,perplexity`).
- Use `--deep-research` flag for exhaustive 50+ citation reports (~$0.90/query) on topics that need serious investigation.
- Bonus: also powers the planning and reranking engine if you don't have a Gemini/OpenAI/xAI key.

Other bonus sources (add anytime):
- `EXA_API_KEY=xxx` - semantic web search, 1K free/month (exa.ai)
- `BSKY_HANDLE=you.bsky.social` + `BSKY_APP_PASSWORD=xxx` - Bluesky (free app password)
- `BRAVE_API_KEY=xxx` - Brave web search

Always add this last line: `SETUP_COMPLETE=true`

**CRITICAL: NEVER overwrite an existing .env file.** Before writing ANY key to `~/.config/last30days/.env`:
1. Check if the file exists: `test -f ~/.config/last30days/.env`
2. If it exists, READ it first, then APPEND only missing keys using `>>` (double redirect)
3. NEVER use `>` (single redirect) which destroys existing content
4. If it doesn't exist, create it: `mkdir -p ~/.config/last30days && touch ~/.config/last30days/.env`

**Then call AskUserQuestion:**
Question: "How do you want to add your keys?"
Options:
- "Open .env in my editor" - Creates the file with a commented template and opens it. You edit, save, and come back.
- "Paste keys here" - Paste your API keys and I'll write the file for you.
- "I'll do it myself" - I'll tell you the file path and you handle it.

**If the user picks "Open .env in editor":**
Create `~/.config/last30days/.env` if it doesn't exist (check first!), pre-populated with this template:
```
# /last30days configuration
# Uncomment and fill in the keys you want to use.

# X/Twitter (pick one):
# FROM_BROWSER=auto          # Free. Reads x.com cookies from your browser at search time.
#                             # Chrome on macOS prompts for Keychain access. Firefox/Safari don't.
# XAI_API_KEY=               # No browser access. Get a key at api.x.ai
# AUTH_TOKEN=                 # Manual: x.com -> F12 -> Application -> Cookies
# CT0=                        # (requires AUTH_TOKEN too)

# ScrapeCreators (10,000 free calls - scrapecreators.com):
# SCRAPECREATORS_API_KEY=    # Unlocks: TikTok, Instagram, Reddit backup (if public Reddit gets rate-limited)
#                             # Optional: add threads, pinterest, youtube_comments for more
# INCLUDE_SOURCES=tiktok,instagram

# YouTube: install yt-dlp (brew install yt-dlp) - no key needed

# Bluesky:
# BSKY_HANDLE=you.bsky.social
# BSKY_APP_PASSWORD=

# Web search:
# BRAVE_API_KEY=              # 2,000 free queries/month at brave.com/search/api
# OPENROUTER_API_KEY=         # Perplexity Sonar via OpenRouter

SETUP_COMPLETE=true
```
If the file already exists, do NOT overwrite it. Just open it.
Run `open ~/.config/last30days/.env` on macOS to open in the default editor.
Then tell the user: "Your .env is open. Edit it, save, and run /last30days again."

**If the user picks "Paste keys here"**, write them to `~/.config/last30days/.env` (create the file and parent dirs if needed, append without overwriting existing keys, always include `SETUP_COMPLETE=true`). If a SCRAPECREATORS_API_KEY was included, also append `INCLUDE_SOURCES=tiktok,instagram` and tell the user: "TikTok, Instagram, and Reddit backup are now on. Want to also add Threads, Pinterest, or YouTube comments? Add them to INCLUDE_SOURCES in your .env." Then offer the same ScrapeCreators sources opt-in modal as the auto-setup path (the "Which ScrapeCreators sources do you want on?" question above). Then proceed with research.

**If the user picks "I'll do it myself"**, tell them: "Save the file at `~/.config/last30days/.env`, then run `/last30days <topic>` to research anything." Then proceed with research using whatever sources are currently available.

**If the user picks Skip:**
Proceed with research immediately using the user's original topic. Do NOT create or modify the .env file when the user picks Skip. Note: without setup, sources are limited to Reddit (threads and comments), HN, Polymarket, and GitHub (if `gh` CLI installed). X/Twitter and YouTube require setup.

---

## Do I Need API Keys?

When users ask about API keys, setup, or how to unlock more sources, reference this:

You do NOT need API keys to use last30days. It works out of the box with Reddit (threads and comments), Hacker News, Polymarket, and GitHub (if `gh` CLI installed). Browser cookies for X/Twitter are equivalent to an API key - just log into x.com in any browser and last30days will find your session automatically.

Source unlock progression (all free):
- Zero config (40% quality): Reddit (threads + comments), HN, Polymarket, GitHub (if `gh` installed) - works immediately
- + X cookies (60%): Log into x.com in any browser. last30days scans your cookies automatically. No signup required.
- + yt-dlp (80%): `brew install yt-dlp` - open source, 190K+ GitHub stars. Enables YouTube search and transcripts.
- Auto setup does both X cookies + yt-dlp in 30 seconds.
- Full free tier (80%): X + Reddit (with comments) + YouTube + HN + Polymarket + GitHub (if `gh` CLI installed)
- + ScrapeCreators (100%): Adds TikTok, Instagram, and a Reddit backup. 10,000 free API calls, no credit card - scrapecreators.com. It's a bonus, not a requirement.

Key comparison: X browser cookies = same access as an API key (free, no signup). ScrapeCreators adds TikTok and Instagram for users who want those platforms.

last30days has no affiliation with any API provider - no referrals, no kickbacks.

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
/last30days — searching {ACTIVE_SOURCES_LIST} for what people are saying about {TOPIC}.
```

For COMPARISON queries:
```
/last30days — comparing {TOPIC_A} vs {TOPIC_B} across {ACTIVE_SOURCES_LIST}.
```

Do NOT show a multi-line "Parsed intent" block with TOPIC=, TARGET_TOOL=, QUERY_TYPE= variables. Do NOT promise a specific time. Do NOT list sources that aren't configured.

Then proceed immediately to Step 0.5 / 0.55.

---

## Step 0.5: Resolve X Handles (if topic could have X accounts)

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

### Step 0.5b: Resolve GitHub Username (if topic is a person)

If TOPIC looks like a **person** (developer, creator, CEO, founder), also resolve their GitHub username for person-mode GitHub search:

```
WebSearch("{TOPIC} github profile site:github.com")
```

From the results, extract their GitHub username from URLs like `github.com/{username}`.

**Verify the account is correct:** Check that the profile description or pinned repos match the person you're researching. Common names may return multiple profiles.

Pass to the CLI: `--github-user={username}` (without @)

Example for "Peter Steinberger": `--github-user=steipete`
Example for "Matt Van Horn": `--github-user=mvanhorn`

**Person-mode GitHub tells a different story than keyword search.** Instead of "who mentioned this person in an issue body," it answers: "What are they shipping? Where are they getting merged? What do their own projects look like?" The engine fetches PR velocity, top repos with star counts, release notes, and README summaries.

**Skip this step if:**
- TOPIC is clearly NOT a person (products, concepts, events)
- TOPIC already has `--github-user` specified by the user
- Using `--quick` depth
- WebSearch shows no GitHub profile for this person

Store: `RESOLVED_GITHUB_USER = {username or empty}`

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
1. **Head-to-head:** `"{TOPIC_A} vs {TOPIC_B}"` — catches rivalry content, direct comparisons
2. **Entity A news:** `"{TOPIC_A} news {MONTH} {YEAR}"` — catches entity-specific developments
3. **Entity B news:** `"{TOPIC_B} news {MONTH} {YEAR}"` — catches entity-specific developments
4. (Optional) **Domain context:** `"{COMPANY_A} {COMPANY_B} {DOMAIN} news"` — catches industry context (e.g., "OpenAI Anthropic AI news")

ALL subqueries include ALL sources. The fusion engine handles deduplication across subqueries. **At least one subquery MUST include YouTube-specific search terms** (e.g., "{PERSON} interview 2026", "{PRODUCT_A} vs {PRODUCT_B} review") to ensure YouTube content is found. Without YouTube-specific terms, the engine may only find 0-1 videos for comparison queries.

Then do WebSearch for: `{TOPIC_A} vs {TOPIC_B} comparison {YEAR}` and `{TOPIC_A} vs {TOPIC_B} which is better` and `{COMPANY_A} vs {COMPANY_B} news {MONTH} {YEAR}`.

**Skip the normal Step 1 below** - go directly to the comparison synthesis format (see "If QUERY_TYPE = COMPARISON" in the synthesis section).

---

## Step 0.55: Pre-Research Intelligence (resolve communities + handles)

> **PLATFORM GATE:** If your platform does NOT support WebSearch (e.g., OpenClaw, raw CLI), **skip Steps 0.55 and 0.75** but add `--auto-resolve` to the Python command in the Research Execution section. The engine will do its own pre-research using configured web search backends (Brave, Exa, or Serper) to discover subreddits, X handles, and current events context before planning.

**Run 2-3 focused WebSearches (in parallel) to resolve platform-specific targeting. Do NOT search for every platform individually — that wastes time. Instead, use your knowledge of the topic to infer most targeting, and only WebSearch for what you can't infer.**

**1. X handles** — Already resolved in Step 0.5 above (including company handles and commentators). Reference your `RESOLVED_HANDLE` and `RESOLVED_RELATED` from that step.

**2. Reddit communities + YouTube channels + current events** — Run 1-2 searches that cover multiple platforms at once:

```
WebSearch("{TOPIC} subreddit reddit community")
WebSearch("{TOPIC} news {CURRENT_MONTH} {CURRENT_YEAR}")
```

The first search finds subreddits. The second gives you current events context (which helps you generate better subqueries in Step 0.75) and may surface YouTube channels or creators organically.

Extract 3-5 subreddit names from the results. Store as `RESOLVED_SUBREDDITS` (comma-separated, no r/ prefix).

**3. TikTok hashtags + creators** — **INFER these from your topic knowledge. Do NOT WebSearch for "{PERSON} TikTok account" — most people/CEOs don't have TikTok, and the search is wasted.**

- **Hashtags:** Infer 2-3 from the topic name + category. Examples: "Kanye West" → `kanyewest,ye,bully`. "Claude Code" → `claudecode,aiagent,aicoding`. "Sam Altman" → `samaltman,openai,chatgpt`.
- **Creators:** Only search if the topic is a content creator, influencer, or brand that likely has TikTok presence. For CEOs, politicians, and non-creator people: skip.

Store as `RESOLVED_HASHTAGS` and `RESOLVED_TIKTOK_CREATORS`.

**4. Instagram creators** — **Same rule: INFER from topic knowledge.** If the topic is a celebrity, brand, or creator with obvious Instagram presence, use their handle directly. If the topic is a tech CEO or abstract concept, skip. Do NOT waste a WebSearch on "Dario Amodei Instagram account."

Store as `RESOLVED_IG_CREATORS`.

**5. YouTube content queries** — Infer 2-3 YouTube content-type queries from the topic without searching. The current events search (#2 above) may surface relevant YouTube channels.

- **For music artists:** `'{TOPIC} album review'`, `'{TOPIC} reaction'`
- **For products/SaaS:** `'{TOPIC} review'`, `'{TOPIC} tutorial'`
- **For comparisons:** `'{TOPIC_A} vs {TOPIC_B}'`
- **For people in the news:** `'{TOPIC} interview {YEAR}'`, `'{TOPIC} latest news'`

Store as `RESOLVED_YT_QUERIES`.

**Concrete examples:**

| Topic | WebSearches needed | Reddit subs | TikTok hashtags | TikTok creators | IG creators | YT queries |
|-------|-------------------|-------------|-----------------|-----------------|-------------|------------|
| **Kanye West** | 2 (subreddit + BULLY news) | `Kanye,WestSubEver,hiphopheads,Music` | `kanyewest,ye,bully` | (inferred: `kanyewest`) | (inferred: `kanyewest`) | `kanye west bully review,kanye west bully reaction` |
| **Sam Altman vs Dario** | 2 (subreddit + AI CEO news) | `artificial,MachineLearning,OpenAI,ClaudeAI` | `samaltman,openai,anthropic` | (skip — CEOs don't TikTok) | (skip — CEOs don't Reel) | `sam altman interview 2026,dario amodei interview 2026` |
| **Tella** (SaaS) | 2 (subreddit + Tella news) | `SaaS,Entrepreneur,screenrecording,productivity` | `tella,tellaapp,screenrecording` | (search: `tella screen recorder TikTok`) | (inferred: `tella.tv`) | `tella screen recorder review,tella tutorial` |

**For comparison queries ("X vs Y"):** Resolve communities/handles for BOTH topics and merge the lists.

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
- `search_query` should be concise and keyword-heavy — match how content is TITLED on platforms
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

**Available sources (include ALL in primary subquery):** reddit, x, youtube, tiktok, instagram, hackernews, polymarket. Optional: bluesky, truthsocial, threads, pinterest, grounding (web search — only if user has Brave/Exa/Serper key)

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

Store your plan as `QUERY_PLAN_JSON` — you'll pass it to the script in the next step.

---

## Research Execution

**Step 1: Run the research script WITH your query plan (FOREGROUND)**

**CRITICAL: Run this command in the FOREGROUND with a 5-minute timeout. Do NOT use run_in_background. The full output contains Reddit, X, AND YouTube data that you need to read completely.**

**IMPORTANT: Pass your QUERY_PLAN_JSON via the --plan flag. This tells the Python script to use YOUR plan instead of calling Gemini.**

**IMPORTANT: Include `--x-handle={RESOLVED_HANDLE}` in the command. For comparison mode: Pass `--x-handle={TOPIC_A_HANDLE}` to the first pass, `--x-handle={TOPIC_B_HANDLE}` to the second pass, and both to the head-to-head pass. Also include `--subreddits={RESOLVED_SUBREDDITS}`, `--tiktok-hashtags={RESOLVED_HASHTAGS}`, `--tiktok-creators={RESOLVED_TIKTOK_CREATORS}`, and `--ig-creators={RESOLVED_IG_CREATORS}` from Step 0.55. Omit any flag where the value was not resolved (empty).**

```bash
# Find skill root — works in repo checkout, Claude Code, or Codex install
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

**YouTube items in the output look like:** `**{video_id}** (score:N) {channel_name} [N views, N likes]` followed by a title, URL, **transcript highlights** (pre-extracted quotable excerpts from the video), and an optional full transcript in a collapsible section. **Quote the highlights directly in your synthesis** - they are the YouTube equivalent of Reddit top comments. Attribute quotes to the channel name. Count them and include them in your synthesis and stats block.

**TikTok items in the output look like:** `**{TK_id}** (score:N) @{creator} [N views, N likes]` followed by a caption, URL, hashtags, and optional caption snippet. Count them and include them in your synthesis and stats block.

**Instagram Reels items in the output look like:** `**{IG_id}** (score:N) @{creator} (date) [N views, N likes]` followed by caption text, URL, and optional transcript. Count them and include them in your synthesis and stats block. Instagram provides unique creator/influencer perspective — weight it alongside TikTok.

---

## STEP 2: DO WEBSEARCH AFTER SCRIPT COMPLETES

After the script finishes, do WebSearch to supplement with blogs, tutorials, and news.

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
- **DO NOT output a separate "Sources:" block** — instead, include the top 3-5 web
  source names as inline links on the 🌐 Web: stats line (see stats format below).
  The WebSearch tool requires citation; satisfy it there, not as a trailing section.

**Options** (passed through from user's command):
- `--days=N` → Look back N days instead of 30 (e.g., `--days=7` for weekly roundup)
- `--quick` → Faster, fewer sources (8-12 each)
- (default) → Balanced (20-30 each)
- `--deep` → Comprehensive (50-70 Reddit, 40-60 X)

---

## Step 2.5: Append WebSearch Results to Saved Raw File

After completing the WebSearch supplementals above, append the results to the saved raw file so it becomes the complete debug artifact (Python engine data + WebSearch data).

**Instructions:**
1. Read the raw file at `~/Documents/Last30Days/{slug}-raw-nogem.md` (it was saved by the Python engine in Step 1).
2. Append a `## WebSearch Supplemental Results` section at the end.
3. For each WebSearch result, include the URL and a 1-2 sentence excerpt of what you found.
4. Write the updated file back.

Example of what to append:
```
## WebSearch Supplemental Results

- **Efficient App** (https://efficientapp.com/tella-vs-loom) — Side-by-side comparison showing Tella exports in 27s vs Loom's 11s, with Tella at $19/mo and Loom free/$8/mo.
- **Shannah Albert Blog** (https://shannahalbert.com/tella-review) — Creator walkthrough of Tella's recording flow, notes the teleprompter feature as a key differentiator.
```

This ensures anyone reviewing the raw file sees ALL data that fed into the synthesis — not just the Python engine output.

---

## Judge Agent: Synthesize All Sources

### v3 Cluster-First Output

**v3 returns results grouped by STORY/THEME (clusters), not by source.** Each cluster represents one narrative thread found across multiple platforms.

**How to read v3 output:**
- `### 1. Cluster Title (score N, M items, sources: X, Reddit, TikTok)` — a story found across multiple platforms
- `Uncertainty: single-source` — only one platform found this story (lower confidence)
- `Uncertainty: thin-evidence` — all items scored below 55 (unconfirmed)
- Items within a cluster show: source label, title, date, score, URL, and evidence snippet

**Synthesis strategy for cluster-first output:**
1. **Synthesize per-cluster first.** Each cluster = one story. Summarize what each story is about.
2. **Multi-source clusters are highest confidence.** A cluster with items from Reddit + X + YouTube is much stronger than single-source.
3. **Check uncertainty tags.** "single-source" means treat with caution. "thin-evidence" means mention but caveat.
4. **Cross-cluster synthesis second.** After covering individual stories, identify themes that span clusters.
5. **Engagement signals still matter.** Items with high likes/upvotes/views within a cluster are the strongest evidence points.
6. **Quote directly from evidence snippets.** The snippets are pre-extracted best passages — use them.
7. Extract the top 3-5 actionable insights across all clusters.
8. **Disambiguation: trust your resolved entity.** When Step 0.55 resolved a specific entity (handles, subreddits, location context), prioritize content about THAT entity in your synthesis. If search results contain a different entity with the same name (e.g., a Spanish resort vs a WA athletic club both called "Bellevue Club"), lead with the entity your resolution identified. Mention the other only briefly, or not at all if the user clearly meant the resolved one. The resolved handles are the strongest signal for user intent.

### Source-Specific Guidance (still applies within clusters)

The Judge Agent must:
1. Weight Reddit/X sources HIGHER (they have engagement signals: upvotes, likes)
2. Weight YouTube sources HIGH (they have views, likes, and transcript content)
3. Weight TikTok sources HIGH (they have views, likes, and caption content — viral signal)
4. Weight WebSearch sources LOWER (no engagement data)
5. **For Reddit: Pay special attention to top comments** — they often contain the wittiest, most insightful, or funniest take. Quote them directly.
6. **For YouTube: Quote transcript highlights directly.** Attribute to the channel name.
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

When you see a cluster of replies to a recommendation-request tweet (someone asking "what's the best X?" and getting multiple independent responses), call this out prominently. This is the strongest form of community endorsement — real people independently making the same recommendation without coordination. Example: "In a thread where @ecom_cork asked for Loom alternatives, every reply said Tella."

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

### If QUERY_TYPE = COMPARISON

Structure the output as a side-by-side comparison using data from all three research passes:

```
# {TOPIC_A} vs {TOPIC_B}: What the Community Says (Last 30 Days)

## Quick Verdict
[1-2 sentence data-driven summary: which one the community prefers and why, with source counts]

## {TOPIC_A}
**Community Sentiment:** [Positive/Mixed/Negative] ({N} mentions across {sources})

**Strengths (what people love)**
- [Point 1 with source attribution]
- [Point 2]

**Weaknesses (common complaints)**
- [Point 1 with source attribution]
- [Point 2]

## {TOPIC_B}
**Community Sentiment:** [Positive/Mixed/Negative] ({N} mentions across {sources})

**Strengths (what people love)**
- [Point 1 with source attribution]
- [Point 2]

**Weaknesses (common complaints)**
- [Point 1 with source attribution]
- [Point 2]

## Head-to-Head
[Synthesis from the "A vs B" combined search - what people say when directly comparing]

| Dimension | {TOPIC_A} | {TOPIC_B} |
|-----------|-----------|-----------|
| [Key dimension 1] | [A's position] | [B's position] |
| [Key dimension 2] | [A's position] | [B's position] |
| [Key dimension 3] | [A's position] | [B's position] |

## The Bottom Line
Choose {TOPIC_A} if... Choose {TOPIC_B} if... (based on actual community data, not assumptions)
```

Then show combined stats from all three passes and the standard invitation section.

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
- **CRITICAL whitespace rule:** Never insert more than ONE blank line between any two content blocks. Comparison tables should immediately follow the preceding paragraph with exactly one blank line. Do NOT pad with 3-6 empty lines before tables.

**If PROMPTING/NEWS/GENERAL** - Show synthesis and patterns:

CITATION RULE: Cite sources sparingly to prove research is real.
- In the "What I learned" intro: cite 1-2 top sources total, not every sentence
- In KEY PATTERNS: cite 1 source per pattern, short format: "per @handle" or "per r/sub"
- Do NOT include engagement metrics in citations (likes, upvotes) - save those for stats box
- Do NOT chain multiple citations: "per @x, @y, @z" is too much. Pick the strongest one.

CITATION PRIORITY (most to least preferred):
1. @handles from X — "per @handle" (these prove the tool's unique value)
2. r/subreddits from Reddit — "per r/subreddit" (when citing Reddit, prefer quoting top comments over just the thread title)
3. YouTube channels — "per [channel name] on YouTube" (transcript-backed insights)
4. TikTok creators — "per @creator on TikTok" (viral/trending signal)
5. Instagram creators — "per @creator on Instagram" (influencer/creator signal)
6. HN discussions — "per HN" or "per hn/username" (developer community signal)
7. Polymarket — "Polymarket has X at Y% (up/down Z%)" with specific odds and movement
8. Web sources — ONLY when Reddit/X/YouTube/TikTok/Instagram/HN/Polymarket don't cover that specific fact

The tool's value is surfacing what PEOPLE are saying, not what journalists wrote.
When both a web article and an X post cover the same fact, cite the X post.

URL FORMATTING: NEVER paste raw URLs anywhere in the output — not in synthesis, not in stats, not in sources.
- **BAD:** "per https://www.rollingstone.com/music/music-news/kanye-west-bully-1235506094/"
- **GOOD:** "per Rolling Stone"
- **BAD stats line:** `🌐 Web: 10 pages — https://later.com/blog/..., https://buffer.com/...`
- **GOOD stats line:** `🌐 Web: 10 pages — Later, Buffer, CNN, SocialBee`
Use the publication/site name, not the URL. The user doesn't need links — they need clean, readable text.

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

**THEN - Quality Nudge (if present in the output):**

If the research output contains a `**🔍 Research Coverage:**` block, render it verbatim right before the stats block. This tells the user which core sources are missing and how to unlock them. Do NOT render this block if it is absent from the output (100% coverage = no nudge).

**Just-in-time X unlock:** If X returned 0 results because no X auth is configured (no AUTH_TOKEN/CT0, no XAI_API_KEY, no FROM_BROWSER), offer to set it up right there:

**Call AskUserQuestion:**
Question: "X/Twitter wasn't searched. Want to unlock it?"
Options:
- "Scan my browser cookies (free)" - Get consent, run cookie scan, write BROWSER_CONSENT=true + FROM_BROWSER=auto to .env
- "I have an xAI API key" - Ask them to paste it, write XAI_API_KEY to .env
- "Skip for now"

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
├─ 🔵 X: {N} posts │ {N} likes │ {N} reposts
├─ 🔴 YouTube: {N} videos │ {N} views │ {N} with transcripts
├─ 🎵 TikTok: {N} videos │ {N} views │ {N} likes │ {N} with captions
├─ 📸 Instagram: {N} reels │ {N} views │ {N} likes │ {N} with captions
├─ 🧵 Threads: {N} posts │ {N} likes │ {N} replies
├─ 📌 Pinterest: {N} pins │ {N} saves │ {N} comments
├─ 🟡 HN: {N} stories │ {N} points │ {N} comments
├─ 🦋 Bluesky: {N} posts │ {N} likes │ {N} reposts
├─ 🇺🇸 Truth Social: {N} posts │ {N} likes │ {N} reposts
├─ 🐙 GitHub: {N} items │ {N} reactions │ {N} comments
├─ 📊 Polymarket: {N} markets │ {copy the market odds EXACTLY from the engine's Polymarket stats output - only real % numbers like "Arizona 33%, Michigan 25%". If you cannot find specific % odds in the data, show ONLY the market count with no description. NEVER write filler like "check markets", "active", "tracked", or any text without a real percentage.}
├─ 🌐 Web: {N} pages — Source Name, Source Name, Source Name
├─ 🗣️ Top voices: @{handle1} ({N} likes), @{handle2} │ r/{sub1}, r/{sub2}
└─ 📎 Raw results saved to ~/Documents/Last30Days/{slug}-raw.md
---
```

**🌐 Web: line — how to extract site names from URLs:**
Strip the protocol, path, and `www.` — use the recognizable publication name:
- `https://later.com/blog/instagram-reels-trends/` → **Later**
- `https://socialbee.com/blog/instagram-trends/` → **SocialBee**
- `https://buffer.com/resources/instagram-algorithms/` → **Buffer**
- `https://www.cnn.com/2026/02/22/tech/...` → **CNN**
- `https://medium.com/the-ai-studio/...` → **Medium**
- `https://radicaldatascience.wordpress.com/...` → **Radical Data Science**
List as comma-separated plain names: `Later, SocialBee, Buffer, CNN, Medium`

**⚠️ WebSearch citation — ALREADY SATISFIED. DO NOT ADD A SOURCES SECTION.**
The WebSearch tool mandates source citation. That requirement is FULLY satisfied by the source names on the 🌐 Web: line above. Do NOT append a separate "Sources:" section at the end of your response. Do NOT list URLs anywhere. The 🌐 Web: line IS your citation. Nothing more is needed.

**CRITICAL: Omit any source line that returned 0 results.** Do NOT show "0 threads", "0 stories", "0 markets", or "(no results this cycle)". If a source found nothing, DELETE that line entirely - don't include it at all.
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

I have all the links to the {N} {source list} I pulled from. Just ask.

**Context-aware:** Only list sources that returned results. Build the source list from your stats: e.g. "14 Reddit threads, 22 X posts, and 6 YouTube videos" or "8 HN stories and 3 Polymarket markets." Never mention a source with 0 results.

---

## WAIT FOR USER'S RESPONSE

**STOP and wait** for the user to respond. Do NOT call any tools after displaying the invitation. The research script already saved raw data to `~/Documents/Last30Days/` via `--save-dir`.

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
- Sends search queries to Twitter's GraphQL API (via optional user-provided AUTH_TOKEN/CT0 env vars — no browser session access) or xAI's API (`api.x.ai`) for X search
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
