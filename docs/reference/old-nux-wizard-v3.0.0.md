# Original v3.0.0 First-Run NUX Wizard (reference capture)

Captured verbatim from `SKILL.md` at git commit `0a9ff16` (v3.0.0, 2026-04-08),
the first-run setup wizard Matt built. Preserved here for provenance and as the
source for the restored modal NUX (see docs/plans/2026-06-22-001-feat-restore-nux-wizard-plan.md).
This is a historical snapshot - the live wizard in SKILL.md Step 0 uses the CURRENT
source inventory (Digg, youtube_comments, SC backups) and omits Threads/Pinterest.

```markdown
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
python3 "${SKILL_ROOT}/scripts/last30days.py" setup --openclaw
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
   - Option A: "ScrapeCreators via GitHub (recommended)" -- Check if `gh` CLI was detected in the environment detection output above. If gh IS detected: description should say "Registers directly via GitHub CLI in ~2 seconds - no browser needed". Before running the command, display: "Registering via GitHub CLI..." If gh is NOT detected: description should say "Copies a one-time code to your clipboard and opens GitHub to authorize". Before running the command, display: "I'll copy a one-time code to your clipboard and open GitHub. When GitHub asks for a device code, just paste (Cmd+V / Ctrl+V)." Then run `python3 "${SKILL_ROOT}/scripts/last30days.py" setup --github`, parse JSON output. Tries PAT first (if `gh` is installed), falls back to device flow which copies a one-time code to your clipboard and opens your browser. If `status` is `success`, write `SCRAPECREATORS_API_KEY={api_key}` to .env.
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
cd {SKILL_DIR} && python3 scripts/last30days.py setup
```
Show the user the results (what cookies were found, whether yt-dlp was installed).

**Then show the optional ScrapeCreators offer (plain text, then modal):**

Want TikTok and Instagram too? ScrapeCreators adds those platforms - 10,000 free calls, no credit card. It also serves as a Reddit backup if public Reddit ever gets rate-limited.

**Before showing the ScrapeCreators modal, check for `gh` CLI:** Run `which gh` via Bash silently. Store the result as gh_available (true if found, false if not).

**Call AskUserQuestion:**
Question: "Want to add TikTok, Instagram, and Reddit backup via ScrapeCreators? (We don't get a cut.)"
Options:
- "ScrapeCreators via GitHub (fastest, recommended)" - If gh_available: description should say "Registers directly via GitHub CLI in ~2 seconds - no browser needed". If NOT gh_available: description should say "Copies a one-time code to your clipboard and opens GitHub to authorize". After the user selects this option: If gh_available, display "Registering via GitHub CLI..." before running the command. If NOT gh_available, display "I'll copy a one-time code to your clipboard and open GitHub. When GitHub asks for a device code, just paste (Cmd+V on Mac, Ctrl+V on Windows/Linux)." Then run `cd {SKILL_DIR} && python3 scripts/last30days.py setup --github` via Bash with a 5-minute timeout. This tries PAT auth first (if `gh` CLI is installed, zero browser needed), then falls back to GitHub device flow which copies a one-time code to your clipboard and opens GitHub in your browser. Parse the JSON stdout. If `status` is `success`, write `SCRAPECREATORS_API_KEY={api_key}` to `~/.config/last30days/.env`. If `method` is `pat`, show: "You're in! Registered via GitHub CLI - zero browser needed. 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `method` is `device` and `clipboard_ok` is true, show: "You're in! (The authorization code was copied to your clipboard automatically.) 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `method` is `device` and `clipboard_ok` is false, show: "You're in! 10,000 free calls. TikTok, Instagram, and Reddit backup are now active." If `status` is `timeout` or `error`, show: "GitHub auth didn't complete. No worries - you can sign up at scrapecreators.com instead or try again later." Then offer the web signup option.
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
```
