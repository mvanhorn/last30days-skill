# First-run setup and source repair

Read when either root setup or repair gate applies. For first-run setup, follow Step 0 below. After onboarding, resume the original requested route, including discovery; do not replace it with ordinary topic research.

**Repair entry (including when `SETUP_COMPLETE=true`):** For a requested repair or a doctor's host-specific prescription, go directly to the applicable **Manual Setup Guide** subsection below. Skip Step 0, its welcome, offers, source-tier prompts, and topic picker. The Manual Setup Guide's completion tail applies only to onboarding: during repair, do not write `SETUP_COMPLETE=true`, and do not start research unless it was requested. Return to the original request after the authorized repair or repair guidance.

Carry out only the authorized repair. Reading this guide or a doctor prescription does not authorize installs, browser launches, cookie reads, or configuration writes. Browser access requires explicit browser-cookie consent. Existing authorization still applies; a refusal, skip, or no answer is not consent. On a `LAST30DAYS_HOST=grok-bot` host, use only **X on a Grok Bot (repair)** for X; never read a browser session or use the Linux / Mac mini repair. Preserve the MacBook exception, human login handoff, and block/challenge/rate-limit stop rules in the applicable subsection.

## Step 0: First-Run Setup Wizard

**CRITICAL: ALWAYS execute Step 0 BEFORE Step 1, even when the user provided a topic.** If the user typed `/last30days Mercer Island`, preserve that topic while handling the first-run choice. The wizard may ask for browser-cookie consent, but declining or skipping X must never stop the requested research.

**RESEARCH CONTINUATION OVERRIDE (dominates every optional onboarding step below):** When the invocation already includes a topic and the user declines X/browser-cookie access inside Auto setup, run the cookie-free setup path, then immediately research that topic with the sources that are available. When the user chooses Skip for now, mark setup complete and immediately research without running setup. In either case, skip the ScrapeCreators offer, source-tier prompt, retry prompt, and first-topic picker until after the useful research response. Do not ask another X question in the same run. After the findings, report X once as an optional omitted source without an unlock pitch, **then RESUME the deferred onboarding in the SAME run: present the Step 4 ScrapeCreators offer, and Step 5 source opt-in if a key gets saved.** Deferred is not dropped — `SETUP_COMPLETE=true` is already written, so a later invocation skips Step 0 entirely and this run is the only chance to make the offer. The first-topic picker stays skipped (a topic was already supplied), and the resume never re-asks X/browser-cookie consent. Browser-cookie reads still require explicit consent; a skip or no answer is never consent.

**You are the conversational driver.** The Python setup script does only mechanical work (cookie reads, tool installs, the GitHub device-auth flow) - it CANNOT prompt the user, because it runs as a non-interactive subprocess. So consent happens HERE, in chat: you ask, the user answers, and you gate each subprocess call on the answer. Do NOT just run `setup` and report the result - that is the silent-onboarding regression this section exists to prevent.

**First-run detection (silent, no commands, no output to user):**
- If `SETUP_COMPLETE=true` is available from process env, project config (`.claude/last30days.env`), global config (`~/.config/last30days/.env`), or the setup check reports configured credentials, skip Step 0 entirely and go to Step 1 (CRITICAL: Parse User Intent below). Do NOT announce that setup is complete. The user does not need a status message on every run.
- Do NOT treat the absence of `~/.config/last30days/.env` alone as a first run. Credentials may live in process env, project config, macOS Keychain (`last30days-<KEY>`), pass(1), or host-provided auth.
- Detect first-run from key presence and setup status, not by dumping files. Do not print `.env` contents or credential values.
- `--preflight` is an opt-in inspector (what this run would read or write, ignored project config). Do not run it as a required first-run step.
- If no setup marker or credential source is present, this is a first run.

**Modal requirement:** preserve the guided welcome → Auto/Manual/Skip → cookie consent → ScrapeCreators offer → source opt-in → first-topic picker flow. Do not replace the modal flow with silent setup or prose-only onboarding.

**Platform split - run exactly ONE branch:**
- **If you HAVE WebSearch and AskUserQuestion (Claude Code):** run the **Claude Code Modal Flow** immediately below.
- **If you do NOT (OpenClaw, Codex, Cursor, Gemini CLI, raw CLI):** run the **Non-Modal Prose Flow** further down. It does the same work conversationally, without modals.
- **If you are running as a Grok Bot** (the Grok Bot host rule in HOW TO INVOKE): run the **Grok Bot Prose Flow**, the third branch, below the Non-Modal flow. The X connector comes first, any backup key is written only through the engine, and no browser session is read. Cursor stays in the Non-Modal Prose Flow.

---

### Claude Code Modal Flow

**Follow these steps IN ORDER unless the Research Continuation Override routes a waiting topic directly to research.** The normal sequence is: (1) welcome (built into the setup modal) → (2) setup modal → (3) run setup if chosen → (4) ScrapeCreators offer modal → (5) source opt-in modal → (6) first-topic picker. Start at step 1.

**Step 1 - Welcome.** The welcome pitch is delivered INSIDE the Step 2 setup modal, NOT as a separate message. Claude Code folds Bash/tool output behind "ctrl+o to expand", so a separate welcome message - or a `--welcome` command run - gets buried and the user never sees it. The AskUserQuestion modal is the only always-fully-visible surface, so the pitch lives in its question text. Do NOT run a separate `--welcome` command in this modal flow, and do NOT try to print the welcome as a chat message before the modal; go straight to Step 2. (The `--welcome` command still exists for the Non-Modal Prose Flow below, where there is no modal.)

**Step 2 - Welcome + setup choice (one modal).** Call AskUserQuestion with EXACTLY this question and these options. Reproduce the question verbatim, including the welcome pitch on the first lines:

Question:
"Welcome to /last30days! I research any topic across Reddit, X, YouTube, TikTok, Digg, arXiv, Techmeme, HN, Polymarket & more - pulling what people actually said in the last 30 days.

How would you like to set up?"

Options:
- "Auto setup (~30s)" - description: "Scan browser cookies for X + install yt-dlp (YouTube), Digg, arXiv, Techmeme. Reddit/HN/Polymarket/GitHub/Web work out of the box. Add TikTok + Instagram after via ScrapeCreators (10k free calls)."
- "Manual setup" - description: "Show me each source and credential to configure by hand."
- "Skip for now" - description: "Just the free no-setup sources: Reddit (with comments), HN, Polymarket, GitHub, Web."

**Step 3 - Run setup based on the choice.**

**If the user picks Skip for now:** write `SETUP_COMPLETE=true` and `BROWSER_CONSENT=false` to `~/.config/last30days/.env` (preserve other keys, replace an existing consent marker, and create the file with mode `0600` if absent) so the wizard does NOT re-fire on every subsequent run and later runs keep browser reads disabled. Do not run any `setup` command - the always-on sources (Reddit, HN, Polymarket, GitHub, Web) need no setup. If the invocation already includes a topic, research it immediately, then resume Step 4 (and Step 5 if a key is saved) in the same run after the findings; Step 6 stays skipped because the topic was supplied. Otherwise continue to Step 6.

**If the user picks Auto setup:**

Get cookie consent first. Check if `BROWSER_CONSENT=true` already exists in `~/.config/last30days/.env`; if so, skip the consent prompt and run `setup --allow-browser-cookies` directly. Otherwise **call AskUserQuestion:**
Question: "Auto setup installs the free CLIs either way - yt-dlp (YouTube), Digg, arXiv, and Techmeme. May I read your browser's x.com cookies for X/Twitter and other logged-in sources now and on later research runs? On macOS I check Chrome and other Chromium browsers first, then Firefox and Safari. On Windows and Linux I use Firefox, with Windows Firefox as a fallback under WSL. Your existing browser selection takes precedence. Setup remembers successful browser names, never cookie values. macOS Keychain may prompt again on later runs; choose Always Allow if you want continuing OS access. You can stop native browser and CDP reads by setting BROWSER_CONSENT=false or FROM_BROWSER=off in ~/.config/last30days/.env. Include X?"
Options (give each option the description shown):
- "Yes - X cookies + all CLIs" - description: "Read x.com cookies for X/Twitter search AND install yt-dlp (YouTube), Digg, arXiv, and Techmeme." Run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --allow-browser-cookies`. The engine saves `BROWSER_CONSENT=true`; do not append a duplicate marker.
- "Skip X - just the CLIs" - description: "No cookie reads. Still installs yt-dlp (YouTube), Digg, arXiv, and Techmeme." Run `FROM_BROWSER=off "${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup`. The engine saves `BROWSER_CONSENT=false`, so the refusal also applies on future runs. If the invocation already includes a topic, immediately research it with `--no-browser-cookies`, then resume Step 4 (and Step 5 if a key is saved) in the same run after the findings; Step 6 stays skipped because the topic was supplied.
- "xAI API key for X instead" - description: "Use an api.x.ai key for X search (no cookie read), plus install yt-dlp (YouTube), Digg, arXiv, and Techmeme." Ask them to paste it, write `XAI_API_KEY` to `.env`, then run `FROM_BROWSER=off "${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup`.

**Grok CLI is an opt-in backup, not a setup-time recommendation.** Do NOT check for grok first or offer it as a primary option during setup. A leftover `~/.grok/auth.json` must never steal the X lane. If the user mentions having a Grok account, tell them: "You can use the Grok CLI by pinning `LAST30DAYS_X_BACKEND=grok` in your `.env` after running `grok login`. This is opt-in because a leftover grok login should not take over X automatically." Do not call it free — it needs a Grok plan.

The consented `setup --allow-browser-cookies` run extracts cookies using the platform order explained in the prompt. Unless `FROM_BROWSER` is already configured, setup saves the browsers that supplied complete cookies as a comma-separated `FROM_BROWSER` list, including Chromium browsers. It normalizes Windows Firefox's `firefox-wsl` source label to `firefox`. It never saves cookie values. Later research reads those browser sessions again; macOS Keychain may prompt again. `BROWSER_CONSENT=false` or `FROM_BROWSER=off` disables native browser and CDP reads. A later consented setup replaces a saved refusal, but an explicit `FROM_BROWSER=off` still blocks reads until changed. The `agentcookie` sidecar is controlled separately by `AGENTCOOKIE=off`.

Setup also best-effort installs yt-dlp (YouTube), the free keyless Digg CLI (`digg-pp-cli` via `@mvanhorn/printing-press-library install digg --cli-only`; Digg activates only when the binary is on the **agent subprocess PATH**, typically `$HOME/.local/bin`; setup reports honestly if installed off-PATH; recommend-only if `npx` is unavailable), plus the free keyless arXiv and Techmeme CLIs. Show the user what was found and installed - including whether Digg landed on PATH (active) or off-PATH (installed but not yet active).

**Extras-host X login (Linux / Mac mini / Darwin agentcookie sink — a MacBook SKIPS this, and so does a `LAST30DAYS_HOST=grok-bot` host, which never runs it).** On a MacBook the Keychain/Firefox/Safari extract above is all X needs. On an extras host the local Chrome store can't be decrypted, so that extract finds nothing and X stays empty unless you also capture a LIVE login over CDP. After the cookie-consent setup run: run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/box_chrome_login.py"` — it prints the exact host-correct command (or launches it with `--exec`), and on a MacBook it prints "no launch needed" and spawns nothing. When `box-chrome` is on PATH it launches a throwaway profile on the last30days extras port **18800** (the last30days convention `SAND_CHROME_REMOTE_DEBUG_PORT=18800`, not box-chrome's default): `CHROME_USER_DATA_DIR=/tmp/last30days-x-chrome SAND_CHROME_REMOTE_DEBUG_PORT=18800 box-chrome --new-window https://x.com/login`. Wait for the x.com login page, then HAND THE DESKTOP to the human to type — do NOT fill or drive the form. After they sign in, append `BROWSER_CDP_URL=http://127.0.0.1:18800` to `.env` (never `AUTH_TOKEN`/`CT0`; keep `AGENTCOOKIE=off` during the harvest), then re-run `setup --allow-browser-cookies` so extras CDP reads the live pair. Full steps and the block/rate-limit stop rule are in **X on Linux / Mac mini** below. This extras-host login only runs on the consented "Yes - X cookies" path; the Research Continuation Override never skips it when the user said yes to X.

**macOS Full Disk Access remediation (Safari fallback only).** Chrome and Firefox need no Full Disk Access; only the Safari fallback does. After the `setup` run, inspect its stderr. If it contains `Permission denied reading Cookies.binarycookies` and the platform is macOS, the OS blocked the Safari read - surface the fix instead of swallowing it: `macOS blocked the Safari cookie read. If your x.com login is in Chrome, you don't need this. To use Safari: System Settings > Privacy & Security > Full Disk Access > enable your terminal (or the Claude app), then I can retry.` Offer ONE retry only when no research topic is waiting. If a topic is already waiting or the user skips, continue immediately with available sources.

**Step 4: ScrapeCreators offer (every first run unless the Research Continuation Override already started a waiting topic).** Show this as plain text, then a modal:

ScrapeCreators adds TikTok and Instagram - posts AND top comments - plus YouTube comments, all on by default. 10,000 free calls, no credit card. Your key also backfills Reddit **search** when the free path returns fewer than 5 items (set `LAST30DAYS_REDDIT_SC_MIN_ITEMS=0` to backfill only when it finds nothing; Reddit comments already come free via shreddit), and backstops YouTube transcripts if yt-dlp gets throttled. (We don't get a cut.) You can widen coverage even further in the next step.

Before the modal, run `which gh` via Bash silently; store as gh_available.

**Call AskUserQuestion:**
Question: "Want to add TikTok and Instagram? Your key also backfills empty Reddit search and backs up YouTube when yt-dlp is throttled. (We don't get a cut.)"
Options:
- "ScrapeCreators via GitHub (recommended - most free calls)" - description: "Opens GitHub - we copy your code to your clipboard automatically, so you just paste it (Cmd+V), ~20-30s. Grants the full 10,000 free calls - more than the web signup." (Recommend this over the web option because the GitHub path grants more free calls.) This is a **two-command flow** - `--github-start` returns the code fast (foreground), then `--github-poll` waits for you to authorize. The code comes back in the command output, so it can't be missed:
   1. **Run `--github-start` in the FOREGROUND** (it returns in ~1-2s, it does NOT block-poll): `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --github-start`. It submits the device flow, copies the code to the clipboard, opens the browser, and returns a JSON blob plus a plain `Your GitHub code: XXXX-XXXX` line on stdout.
      - If the returned `status == "already_registered"` (a key was already saved): tell the user "You're already set up - your existing ScrapeCreators key is active" and STOP (do not run poll).
      - If `status == "error"`: show the message and offer the web option below.
   2. **SHOW THE CODE.** Read the `user_code` from the output and output ONE chat message: "Enter this code on the GitHub page: **XXXX-XXXX** - it's already on your clipboard, so just paste (Cmd+V) and click Continue." (If the output said the clipboard copy failed, tell them to type it instead.) The code is right there in step 1's output - surfacing it is the whole point.
   3. **Run `--github-poll`** (background with a 5-minute timeout, or foreground): `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --github-poll`. Parse the **LAST** JSON line of its stdout for the final status:
      - `status == "success"`: the engine persisted the key (`"persisted": true`, MASKED `api_key` - never ask for or echo the raw key); confirm "You're in! 10,000 free calls. TikTok, Instagram, empty-path Reddit search backup, and YouTube transcript fallback are now active."
      - `status == "success"` but `"persisted": false` (key write failed): do NOT claim sources are active - tell the user signup worked but saving the key failed, and have them add `SCRAPECREATORS_API_KEY=<key>` to `~/.config/last30days/.env` manually.
      - `status == "error"` **with `message == "Authorized but failed to fetch API key"`** (exact match; often also `"reason": "no_api_key"`): GitHub authorized fine - do NOT say auth failed. This usually means your GitHub is **already linked** to a ScrapeCreators account. Tell the user: "GitHub authorized, but I couldn't auto-grab your ScrapeCreators key - your GitHub is probably already linked to an account. Get your key at scrapecreators.com and paste it here, or Skip." Then accept a pasted key (write `SCRAPECREATORS_API_KEY` to `.env`) or offer the web/skip options.
      - `status == "error"` **with `message` containing `ScrapeCreators profile failed`** (or `"reason": "upstream_error"`): GitHub authorized fine - do NOT say auth failed, and do **NOT** say the GitHub is already linked. ScrapeCreators returned a server error after auth. Tell the user: "GitHub authorized, but ScrapeCreators had a server error minting your key - this is on their side, not yours. Sign up or log in at scrapecreators.com, grab a key there, and paste it, or Skip / retry later." Accept a pasted key or offer web/skip. Do not imply they already have a working linked account.
      - `status == "timeout"`, or any other `status == "error"` message: show "GitHub auth didn't complete - no worries, sign up at scrapecreators.com or try again later," then offer the web option below.
   - **One-shot fallback:** hosts that prefer a single call can still run `setup --github` (foreground), which chains start+poll; tell the user first that a code will appear on their clipboard to paste.
- "Open scrapecreators.com (Google sign-in)" - run `open https://scrapecreators.com` via Bash, then ask them to paste the API key. Write `SCRAPECREATORS_API_KEY={key}` to `~/.config/last30days/.env`.
- "I have a key" - accept the key, write to `.env`.
- "Skip for now" - proceed without ScrapeCreators. No TikTok/Instagram, no empty-path Reddit search backup, and no YouTube transcript fallback when yt-dlp is throttled (your free sources still work, including keyless Reddit comments via shreddit).

**Step 5: Source opt-in (only if a ScrapeCreators key was saved, not if skipped).** Comments are the DEFAULT, never an opt-in - there is no posts-only tier. Plain text then modal:

Your key is set. On by default: TikTok + Instagram (posts AND top comments), and YouTube comments. Reddit search stays on the free keyless path (ScrapeCreators backfills search when it returns fewer than 5 items); Reddit comments stay free via shreddit. Want the widest net?

**Call AskUserQuestion:**
Question: "Which ScrapeCreators sources?"
Options:
- "TikTok + Instagram + all comments (recommended)" - the default: posts AND top comments (ranked by votes) for TikTok + Instagram, plus YouTube comments. Append `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments` to `~/.config/last30days/.env` (the list must include `tiktok,instagram` so they are not treated as excluded). Confirm: "TikTok, Instagram, and top YouTube/TikTok/Instagram comments are on."
- "Everything (also Threads + Pinterest)" - everything above plus Threads and Pinterest searches. Most coverage, most credits. Append `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments,threads,pinterest`. Confirm: "Everything's on: posts + comments for TikTok/Instagram/YouTube, plus Threads and Pinterest."

**Step 6: First-topic picker.** Once `SETUP_COMPLETE=true` is written, **call AskUserQuestion:**
Question: "What do you want to research first?"
Options:
- "Claude Code vs Codex" - tech comparison
- "Sam Altman" - person in the news
- "Warriors Basketball" - sports
- "AI Legal Prompting Techniques" - niche/professional
- "Type my own topic"

If the user picks an example, run research with it. If "Type my own", ask what they want. **If the user already supplied a topic with the command (e.g. `/last30days Mercer Island`), SKIP this picker and use their topic directly.**

**END OF FIRST-RUN WIZARD.** Everything in the Modal Flow ONLY runs on first run. If `SETUP_COMPLETE=true` exists, skip ALL of it - no welcome, no modals, no topic picker - and go straight to research (Parse User Intent).

**If the user picked Manual setup** at Step 2, follow the **Manual Setup Guide** below instead of the Auto branch (the guide writes `SETUP_COMPLETE=true` itself), then continue to Step 6.

---

### Non-Modal Prose Flow

For hosts without interactive modal prompts (OpenClaw, Codex, Cursor, Gemini CLI, raw CLI). Same work, done conversationally. Run in order; wait where it says to wait.

**1. Welcome.** Run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" --welcome` and show its stdout to the user VERBATIM (do not summarize or reformat). The welcome is engine-owned so it renders the same everywhere.

**2. Cookie consent (ask BEFORE reading anything).** First check if `BROWSER_CONSENT=true` already exists in `~/.config/last30days/.env` (e.g. granted in a prior Claude Code session); if so, skip this prompt and run `setup --allow-browser-cookies` directly. Otherwise ask. Example: `May I read your browser cookies for X/Twitter and other logged-in sources now and on later research runs? On macOS I check Chrome and other Chromium browsers first, then Firefox and Safari. On Windows and Linux I use Firefox, with Windows Firefox as a fallback under WSL. Your existing browser selection takes precedence. Setup remembers successful browser names, never cookie values. macOS Keychain may prompt again on later runs; choose Always Allow if you want continuing OS access. You can stop native browser and CDP reads by setting BROWSER_CONSENT=false or FROM_BROWSER=off in ~/.config/last30days/.env. (yes / no)` **Wait for the answer.**
   - On **yes** → run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --allow-browser-cookies` (the engine saves `BROWSER_CONSENT=true`; do not append a duplicate marker). Unless `FROM_BROWSER` is already configured, setup saves the browsers that supplied complete cookies as a comma-separated `FROM_BROWSER` list, including Chromium browsers. It normalizes Windows Firefox's `firefox-wsl` source label to `firefox`. It never saves cookie values. Later research reads those browser sessions again; macOS Keychain may prompt again. `BROWSER_CONSENT=false` or `FROM_BROWSER=off` disables native browser and CDP reads. A later consented setup replaces a saved refusal, but an explicit `FROM_BROWSER=off` still blocks reads until changed. The `agentcookie` sidecar is controlled separately by `AGENTCOOKIE=off`. Setup also best-effort installs yt-dlp (YouTube), the free keyless Digg CLI (`digg-pp-cli` via `@mvanhorn/printing-press-library install digg --cli-only`; activates only when on the agent subprocess PATH, typically `$HOME/.local/bin`; reports honestly if off-PATH; recommend-only if `npx` is unavailable), plus the free keyless arXiv and Techmeme CLIs.
     - **Extras hosts (Linux / Mac mini / Darwin agentcookie sink) — a MacBook SKIPS this, and so does a `LAST30DAYS_HOST=grok-bot` host.** On these hosts the Chrome cookie store can't be decrypted, so the extract above finds nothing and X stays empty unless you capture a LIVE login over CDP. Run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/box_chrome_login.py"` (prints the host-correct command; `--exec` launches it; a MacBook prints "no launch needed" and spawns nothing). When `box-chrome` is on PATH it launches a throwaway profile on the last30days extras port **18800** (`SAND_CHROME_REMOTE_DEBUG_PORT=18800`, not box-chrome's default): `CHROME_USER_DATA_DIR=/tmp/last30days-x-chrome SAND_CHROME_REMOTE_DEBUG_PORT=18800 box-chrome --new-window https://x.com/login`. Wait for the x.com login page, then HAND THE DESKTOP to the human to type — do NOT drive the form. After they sign in, append `BROWSER_CDP_URL=http://127.0.0.1:18800` to `.env` (never `AUTH_TOKEN`/`CT0`; keep `AGENTCOOKIE=off` during the harvest) and re-run `setup --allow-browser-cookies`. Full steps and the block/rate-limit stop rule: **X on Linux / Mac mini** below. This extras-host login only runs on the consented **yes** path; a waiting topic never skips it when the user said yes to X.
   - On **no** → run `FROM_BROWSER=off "${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup`. Skips all cookie reads; still installs yt-dlp (YouTube), Digg, arXiv, and Techmeme, still writes `SETUP_COMPLETE`. The engine saves `BROWSER_CONSENT=false`, so later runs also skip native browser and CDP reads. If the invocation already includes a topic, immediately research it with `--no-browser-cookies`, then resume the deferred onboarding in the same run after the findings: the ScrapeCreators offer (step 4) and source tier (step 4b) if a key is saved. Do not re-ask cookie consent as part of the resume.

**3. Full Disk Access remediation (macOS only).** After `setup`, inspect stderr. If it contains `Permission denied reading Cookies.binarycookies` on macOS, surface: `macOS blocked the cookie read. To enable X/Twitter: System Settings > Privacy & Security > Full Disk Access > enable your terminal (or the Claude app), then I can retry.` Offer ONE retry only when no research topic is waiting. If a topic is already waiting or the user skips, continue immediately with available sources.

**4. ScrapeCreators signup offer (every first run, consent BEFORE launching the browser).** Explain it grants 10,000 free calls that add TikTok and Instagram, plus optional backups: Reddit search backfill when the free path returns fewer than 5 items (`LAST30DAYS_REDDIT_SC_MIN_ITEMS=0` limits it to empty runs; SC-primary is an opt-in env knob - see Reddit backend pin below), and a YouTube transcript fallback when yt-dlp is rate-limited or bot-gated. GitHub signup grants the full 10,000 free calls (more than the web form), and it opens a GitHub authorization page where you enter a short code. Ask, e.g.: `Want to unlock TikTok, Instagram, and more? I can sign you up for ScrapeCreators with GitHub (10,000 free calls, ~20-30s) - it opens a browser and you enter a short code. (yes / no)` **Wait for the answer.**
   - On **yes** → two commands. FIRST run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --github-start` in the FOREGROUND - it returns in ~1-2s with a `Your GitHub code: XXXX-XXXX` line plus a JSON blob, copies the code to the clipboard, and opens the browser. Read the `user_code` from that output and immediately tell the user: the code, that it's on their clipboard so they can just paste it (Cmd+V) on the GitHub page - do not make them hunt for it. (If `status == "already_registered"`, stop here - their existing key is active. If the output said the clipboard copy failed, tell them to type the code.) THEN run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" setup --github-poll` (background with a 5-min timeout, or foreground) and parse the **LAST** JSON line of its stdout for the final status. On success the engine persists the key automatically and returns `"persisted": true` with a MASKED `api_key` (never ask for or echo the raw key). Confirm the paid sources are active.
   - On **success but `"persisted": false`** (auth completed yet the key write failed) → do NOT claim sources are active. Tell the user signup worked but saving failed, and have them add `SCRAPECREATORS_API_KEY=<key>` to `~/.config/last30days/.env` manually (the raw key is masked in output, so re-run `setup --github` or retrieve it from scrapecreators.com to get the value).
   - On **`status == "error"` with `message == "Authorized but failed to fetch API key"`** (exact match; often `"reason": "no_api_key"`) → GitHub authorized fine, so do NOT say auth failed. This usually means the GitHub account is already linked to a ScrapeCreators account. Tell the user: "GitHub authorized, but I couldn't auto-grab your ScrapeCreators key - your GitHub is probably already linked to an account. Get your key at scrapecreators.com and paste it, or Skip." Accept a pasted key or offer web/skip.
   - On **`status == "error"` with `message` containing `ScrapeCreators profile failed`** (or `"reason": "upstream_error"`) → GitHub authorized fine; do NOT say auth failed, and do **NOT** say the account is already linked. ScrapeCreators returned a server error after auth. Tell the user: "GitHub authorized, but ScrapeCreators had a server error minting your key - this is on their side, not yours. Sign up or log in at scrapecreators.com, grab a key there, and paste it, or Skip / retry later." Accept a pasted key or offer web/skip.
   - On **timeout, or any other error** → tell the user it didn't complete and offer to retry or the web signup at scrapecreators.com.
   - On **no** → note they can run it later by asking to set up ScrapeCreators, then continue.

**4b. Source tier (only if a key was saved).** Comments are the default, never opt-in. Your key runs TikTok + Instagram posts AND top comments, plus YouTube comments. Reddit stays on the free keyless path (ScrapeCreators search backfill when it returns fewer than 5 items; comments via shreddit). Ask whether they want the widest net, e.g.: `Recommended is TikTok + Instagram + all comments (posts and top comments for TikTok/Instagram plus YouTube comments). Or Everything - also Threads + Pinterest (more credits). (recommended / everything)` **Wait for the answer.**
   - On **recommended** → append `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments` to `~/.config/last30days/.env` (include `tiktok,instagram` so they are not treated as excluded). Confirm posts + top comments for TikTok/Instagram/YouTube are on.
   - On **everything** → append `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments,threads,pinterest`. Confirm Threads and Pinterest are on too.

**5. Complete.** Once `SETUP_COMPLETE=true` is written, proceed to research. Setup stdout is what this run installed, not the runtime source list; the engine diagnostic at research confirmation is authoritative. For Codex desktop, Cursor, Gemini CLI, and raw folder-mode hosts, hidden `.claude/last30days.env` project config is ignored unless `LAST30DAYS_TRUST_PROJECT_CONFIG=1` is set from the process environment or global config; only report a project file as active when the engine reports it as the config source.

---

### Grok Bot Prose Flow

For a Grok Bot host. The Grok Bot host rule in HOW TO INVOKE applies throughout: `LAST30DAYS_HOST=grok-bot` is exported in every shell that runs the engine, including every command below. Same conversational shape as the Non-Modal Prose Flow, no modals, and the X connector comes first: on this host the engine never reads a browser session and X runs only through official access. Run in order; wait where it says to wait.

**1. Host key + welcome.** Persist the host signal so later runs and `doctor` see it: if `~/.config/last30days/.env` is missing, `mkdir -p ~/.config/last30days && touch ~/.config/last30days/.env`; then append one line `LAST30DAYS_HOST=grok-bot` (append-only with `>>`; never `>`). Then run `"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" --welcome` and show its stdout VERBATIM.

**2. X connector (primary - check this BEFORE offering any key).** Look for an X post-search tool from the X connector in this session: the "X for Grok Bot" plugin (search posts, read timelines, check mentions), for example `search_posts_all`. Any of its search tools counts; use the one that searches posts by query.
   - **Present** → tell the user: `X search runs through your X connector on the credits included with Grok Bot, with full 30-day coverage - nothing to configure.` Export `LAST30DAYS_X_HOST_LANE=1` next to `LAST30DAYS_HOST=grok-bot` in every engine shell for this session, and on every research run follow the X connector recipe in Research Execution: one `topic` call sized 10 / 30 / 60 by depth (`--quick` / default / `--deep`) with `-is:retweet` and the window; per `--x-handle`, a `from` call of 8 and a `mention` call of 5; per `--x-related` handle, a `related` call of 3; written to a `last30days-x-posts/1` envelope (`generated_at`, `topic`, `window`, `status`, `calls` tagged `topic` / `from` / `mention` / `related`, each post exactly `id`, `author_handle`, `created_at`, `text`, `likes`, `reposts`, `replies`, `quotes` and nothing else); if the tool rejects the window or count parameters, omit them and write `status: partial` with `error: window-unsupported`; pass the file as `--x-posts <file>`. Skip step 3.
   - **Absent** → continue to step 3.

**3. Backup key (only when the connector is absent).** Say, then WAIT: `This session has no X connector, so X needs a key. Best fix: add the "X for Grok Bot" plugin and connect X inside Grok Bot (it provisions an X developer account for you, with credits included). Otherwise paste an X_BEARER_TOKEN from the X developer console - recent posts, about the last week, unless your X developer project has full-archive access - or an XAI_API_KEY from console.x.ai. Or skip X for now. (bearer / xai / skip)`
   - On a pasted key → persist it ONLY through the engine's key-write path, feeding stdin from a single-quoted heredoc. Never an ad-hoc shell write of the value, never interpolate it into a command line:

     ```bash
     "${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" setup --store-key X_BEARER_TOKEN <<'KEY_EOF'
     {PASTED_VALUE}
     KEY_EOF
     ```

     Use `setup --store-key XAI_API_KEY` for an xAI key. The engine prints `X_BEARER_TOKEN=****` (or `XAI_API_KEY=****`) plus a JSON `persisted` line; never echo the value back, and confirm only in the masked `NAME=****` form. Running it again with a new value replaces the stored one (that is how a rejected key is rotated). A `"persisted": false` means the write failed: say so and do not claim X is active.
   - On **skip** → append `X_DECLINED=grok-bot` to `~/.config/last30days/.env` (append-only) so later runs stay quiet about X: no unlock pitch, no second key question. If the invocation already includes a topic, research it right after step 4 and resume steps 5-6 after the findings.

**4. Setup (free CLIs, no browser reads).** Run `"${LAST30DAYS_PYTHON:-python3}" "${SKILL_DIR}/scripts/last30days.py" setup` (the plain form; on this host it reads nothing from a browser). It best-effort installs yt-dlp (YouTube), the Digg CLI, arXiv, and Techmeme and writes `SETUP_COMPLETE=true`. Show what was installed, including whether Digg landed on PATH.

**5. ScrapeCreators offer and source tier.** Run steps 4 and 4b of the Non-Modal Prose Flow exactly as written there (GitHub device-code signup with `setup --github-start` then `setup --github-poll`; the engine persists the key and masks it).

**6. Complete.** Confirm `SETUP_COMPLETE=true` is in `~/.config/last30days/.env` (append it if `setup` did not run) and proceed to research (with the host signal and, when the connector is present, `LAST30DAYS_X_HOST_LANE=1` exported). Setup stdout is what this run installed, not the runtime source list; the engine diagnostic at research confirmation is authoritative.

---

### Manual Setup Guide

Shown when a Claude Code user picks "Manual setup", or for anyone who wants to configure by hand. Present as plain text (not blockquoted).

The magic of /last30days is Reddit comments + X posts together - and both are free. Add these to `~/.config/last30days/.env`:

**X/Twitter (pick one - the most important source):**
- `X_BEARER_TOKEN=xxx` - the official X API v2 (`api.x.com`) with an app-only bearer from the X developer console. Recent posts, about a week back on the Basic tier, unless your X developer project has full-archive access. Persist it with `setup --store-key X_BEARER_TOKEN` (value on stdin, masked in output). Outside a Grok Bot host also add `LAST30DAYS_X_BACKEND=xapi` so the engine selects it (`doctor` says so when the bearer is set without it).
- **Grok CLI (no X credential):** install with `curl -fsSL https://x.ai/cli/install.sh | bash`, then `grok login`. No X account, no cookies, no API key. Needs a Grok plan; calls draw on it.
- `FROM_BROWSER=auto` - free. Reads your x.com login cookies live at search time: Firefox and Safari first, then Chromium browsers on macOS; Firefox only on Windows and Linux, with a Windows Firefox fallback under WSL. Cookie values are never saved to disk. Chromium reads may prompt for macOS Keychain access again; choose Always Allow for continuing OS access. A saved `BROWSER_CONSENT=false` blocks these reads until you consent again. Set `BROWSER_CONSENT=false` or `FROM_BROWSER=off` to disable native browser and CDP reads.
- `XAI_API_KEY=xxx` - no browser access needed. Get a key at api.x.ai. Best for servers.
- `XQUIK_API_KEY=xxx` - keyless-style X via Xquik.
- `AUTH_TOKEN=xxx` + `CT0=xxx` - paste your X cookies manually (x.com → F12 → Application → Cookies).

**X on a Grok Bot (repair).** On a `LAST30DAYS_HOST=grok-bot` host X runs only through official access, so if X returns nothing there: add the "X for Grok Bot" plugin and connect X inside Grok Bot (the X connector lane; full 30-day coverage on the included credits), or add `X_BEARER_TOKEN` (recent posts, about the last week, unless your X developer project has full-archive access) or `XAI_API_KEY` from console.x.ai through `setup --store-key <NAME>`, or, when the footer reports X API credits exhausted (`payment-required`), top up credits in the X developer console. Nothing in the Linux / Mac mini repair section below applies on a Grok Bot.

**X on Linux / Mac mini (repair).** These hosts (never a `LAST30DAYS_HOST=grok-bot` host) can't decrypt a local Chrome cookie store, so if X returns nothing there, feed bird a cookie pair one of these ways (a MacBook does NOT do any of this — it uses its Keychain / Firefox / Safari extract; do not launch box-chrome on a MacBook):
- **agentcookie sidecar:** install the `agentcookie` CLI so the engine can read your `auth_token`/`ct0` from it automatically. Nothing to configure; `AGENTCOOKIE=off` disables it.
- **Live Chrome login over CDP (the proven extras-host path).** The engine reads a live signed-in Chrome over the DevTools Protocol, but you must LAUNCH that Chrome on the extras port and log in first — `setup --allow-browser-cookies` alone opens no window. Steps (a MacBook skips all of this):
  1. Set `AGENTCOOKIE=off` for this harvest so a sidecar can't mix in a different pair (leave it unset again after success).
  2. Launch a throwaway login Chrome on the last30days extras port **18800** — this port is the last30days convention (`SAND_CHROME_REMOTE_DEBUG_PORT=18800`), NOT box-chrome's built-in default (`9222` + the display number). **Launch via the host `box-chrome` wrapper** (which sets `--class=box-chrome`); do NOT launch raw `google-chrome-stable`, and in particular do NOT launch raw Chrome with a custom `--class` — a raw Chrome with `--class=l30d-…` failed where `box-chrome` (class `box-chrome`) succeeded. Do NOT rely on any `GrokAgent` user-agent token: it is not required and may be disabled on the host (`/tmp/sand-ua-token-disabled`), so never tell users it must be present. Run `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/box_chrome_login.py"` to print the exact host-correct command (add `--exec` to launch it); on a MacBook it prints "no launch needed" and spawns nothing. When `box-chrome` is on PATH the command is:
     ```
     mkdir -p /tmp/last30days-x-chrome
     CHROME_USER_DATA_DIR=/tmp/last30days-x-chrome SAND_CHROME_REMOTE_DEBUG_PORT=18800 box-chrome --new-window https://x.com/login
     ```
     If `box-chrome` is missing, do NOT invent a google-chrome flag soup (a raw Chrome with a custom `--class` is what failed) — sign into x.com in a Chrome that already exposes a remote-debugging port and pin `BROWSER_CDP_URL` to that endpoint instead.
  3. Do NOT fill the login form and do NOT drive the page (no Playwright/Puppeteer/computerUse/xdotool, no typing credentials). Wait until the x.com login page is actually visible, THEN hand the desktop / computer-preview to the human: "Log into X in this Chrome window." (A HUD over the login is OK only as a handoff, never to click the page.)
  4. After they hand back, confirm the window is signed in (x.com/home). Append `BROWSER_CDP_URL=http://127.0.0.1:<port>` to `~/.config/last30days/.env` (append-only), using the debug port the Chrome actually listens on — `18800` if you launched with the command above, or the real port otherwise (a live harvest pinned `http://127.0.0.1:9334`). Do NOT write `AUTH_TOKEN` or `CT0` — the pair is read live each run. Then run `setup --allow-browser-cookies`; extras CDP reads the live pair. Pinning `BROWSER_CDP_URL` after login is also the guard if a stale/logged-out Chrome happens to answer on `18800`.
  5. If X shows a block / challenge / rate-limit, STOP — tell them to wait and retry later. Do not keep launching Chromes.
- **`XAI_API_KEY=xxx`** - key-based X, no browser at all.
- **Grok CLI** - run `grok login`, then pin `LAST30DAYS_X_BACKEND=grok` (pin-only; a leftover grok login never auto-steals the X lane). Needs a Grok plan.

**Reddit (free, works out of the box):**
- Free keyless discovery (Reddit search + shreddit) gives threads + top comments with upvote counts. No setup required.
- `SCRAPECREATORS_API_KEY=xxx` - optional Reddit search backfill when the free path returns **fewer than 5 items** (default floor). A run makes at most one ScrapeCreators Reddit backfill per distinct query, date window, and subreddit set, and a backfill is several API calls (searches plus comment fetches, roughly 10 at default depth). Set `LAST30DAYS_REDDIT_SC_MIN_ITEMS=0` to backfill only when the free path returns nothing, or `LAST30DAYS_REDDIT_BACKEND=scrapecreators` to make ScrapeCreators primary (see Reddit backend pin).

**YouTube (free, open source):**
- Run `brew install yt-dlp` (or `pip install yt-dlp`) - enables YouTube search + transcripts.
- `SCRAPECREATORS_API_KEY=xxx` - optional server-side transcript fallback, used only when yt-dlp is rate-limited/bot-gated.

**Digg (free, keyless):**
- Run `npx @mvanhorn/printing-press-library install digg --cli-only` - installs the Digg CLI for trending news, GitHub stars, and pipeline feeds. Activates when `digg-pp-cli` is on your PATH (typically `$HOME/.local/bin`).

**GitHub Issues/PRs (free, no key needed):**
- If the `gh` CLI is installed and authed (`brew install gh && gh auth login`), GitHub search is automatic. No API key required.

**Bonus: TikTok, Instagram, YouTube comments (ScrapeCreators):**
- `SCRAPECREATORS_API_KEY=xxx` - 10,000 free calls at scrapecreators.com.
- After adding your key, set `INCLUDE_SOURCES=tiktok,instagram` to turn on the popular ones. (Threads, Pinterest, LinkedIn, and Meta Ads are also available via `INCLUDE_SOURCES=threads,pinterest,linkedin,meta_ads` for power users.)

**Other optional sources (add anytime):**
- `PERPLEXITY_API_KEY=xxx` - preferred Agent/Search API path with citations; set `INCLUDE_SOURCES=perplexity`. Existing `OPENROUTER_API_KEY` installs keep the synchronous Sonar fallback.
- `XIAOHONGSHU_API_BASE=http://localhost:18060` - Xiaohongshu/RED via a logged-in x-mcp browser plugin or `xiaohongshu-mcp` service; optional unless the local service runs on a custom URL. Opt in per run with `--search xhs`, or persistently via `INCLUDE_SOURCES=xiaohongshu`.
- DripStack (premium financial newsletter search) is opt-in only: per run with `--search dripstack`, or persistently via `INCLUDE_SOURCES=dripstack`. Free public search API, no key; never active without the opt-in.
- Telegram (public channels) is opt-in via `--telegram-sources=handle1,handle2` (auto-activates for that run) or persistently via `TELEGRAM_SOURCES=handles` + `INCLUDE_SOURCES=telegram`. Requires `SCRAPECREATORS_API_KEY`. Named public channels only; no keyword discovery.
- `BSKY_HANDLE=you.bsky.social` + `BSKY_APP_PASSWORD=xxx` - Bluesky (free app password).
- `BRAVE_API_KEY=xxx` or `EXA_API_KEY=xxx` - web search backends.

**CRITICAL: NEVER overwrite an existing `.env`.** Before writing ANY key:
1. Check if the file exists: `test -f ~/.config/last30days/.env`
2. If it exists, READ it, then APPEND only missing keys with `>>` (double redirect).
3. NEVER use `>` (single redirect) - it destroys existing content.
4. If it doesn't exist: `mkdir -p ~/.config/last30days && touch ~/.config/last30days/.env`

Always add this last line: `SETUP_COMPLETE=true`. Then proceed to research.

The setup wizard's mechanical work lives in a Python module so it runs across all hosts (Claude Code, Codex, Cursor, etc.) while you drive the consent conversation above. The common-case (already set up) path through this file stays short.

---
