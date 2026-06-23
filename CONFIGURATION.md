# Configuration

Everything you can tune in `/last30days` without editing the engine source.
Three layers, in order of how often you'll touch them:

1. **Per-run flags** - what you pass on the command line.
2. **Environment variables and `.env`** - what's enabled across all runs.
3. **Optional trend-monitoring stack** - SQLite store, watchlist, briefings.

Per-client patterns and the experimental beta channel are at the bottom.

> Skip ahead: [Where output is saved](#where-output-is-saved) - [API keys](#api-keys-env) - [Reasoning provider](#reasoning-provider-priority) - [Web search backend](#web-search-backend-priority) - [Trend monitoring](#trend-monitoring-store--watchlist--briefings) - [Per-client patterns](#per-client-patterns) - [Beta channel](#beta-channel)

## Why this document exists

This is a focused **configuration reference** maintained alongside the engine. The runtime contract (the voice rules, the planner protocol, the LAWs the synthesizing model follows) lives in [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md) - that file is authoritative when the two ever differ. This file's job is narrower: surface every knob a user or operator can turn, in one place, kept current with the code so client-facing setups stay reliable. New configuration knobs added to the engine should be reflected here in the same PR.

---

## Where output is saved

| Platform | Default path | Override |
|---|---|---|
| Linux / macOS | `LAST30DAYS_MEMORY_DIR` defaults to `~/Documents/Last30Days/` | set `LAST30DAYS_MEMORY_DIR=/path` |
| Windows | `LAST30DAYS_MEMORY_DIR` defaults to `C:\Users\<you>\Documents\Last30Days\` | set `LAST30DAYS_MEMORY_DIR=C:\path` |

Each run produces one file per topic, slug-named:
`<slug>-raw[-suffix].md`. Same topic + same suffix on the same day overwrites; same topic + same suffix on different days appends a date stamp.

### Recommended `.env` entry

`.env` files don't travel between machines or harnesses, so set `LAST30DAYS_MEMORY_DIR` explicitly in `~/.config/last30days/.env` once per host. The `/last30days` slash command works without it (the SKILL.md wrapper has its own default), but **bare engine invocations** — `python3 scripts/last30days.py ...` from cron jobs, scripts, or agents that bypass the wrapper — silently no-op the file save unless the engine sees the env var. Mirrors the `LAST30DAYS_STORE` env-or-flag convention.

```bash
# ~/.config/last30days/.env  (pick ONE — uncomment the line that matches your OS)
LAST30DAYS_MEMORY_DIR=~/Documents/Last30Days                      # POSIX — defaults to this path when unset
# LAST30DAYS_MEMORY_DIR=C:\Users\<user>\Documents\Last30Days      # Windows
```

The engine's `.env` reader doesn't expand `$HOME` — only the tilde, via `Path().expanduser()` downstream. Use `~/...` or an absolute path; **don't** write the literal string `$HOME/...` into your `.env` (it gets stored verbatim and breaks path resolution).

**Per-run overrides:**

- `--save-dir <path>` - one-off output location. **Flag wins over env var.** If neither flag nor env var is set, the engine does not write a file (DB persistence is independent — see `LAST30DAYS_STORE` below).
- `--output <file>` - write the rendered output to an exact file path, using the format selected by `--emit`.
- `--save-suffix <name>` - distinguish runs of the same topic (e.g. per client: `--save-suffix=acme`).

The footer line `📎 Raw results saved to ${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}/<slug>-raw.md` is the canonical pointer; if it shows backslashes on Windows update past v3.1.1.

---

## First-run onboarding

On the very first `/last30days` run (no `~/.config/last30days/.env`, or `SETUP_COMPLETE` not set), the skill runs a consent-driven onboarding the model drives in chat. It has three consent points:

1. **Browser cookies** - the model asks before reading anything. On yes it extracts Firefox/Safari cookies (never Chrome, to avoid a macOS Keychain prompt) to unlock X/Twitter and other logged-in sources, and installs yt-dlp + the keyless Digg CLI. On no it runs setup with `FROM_BROWSER=off` (skips all cookie reads, still installs the tools).
2. **Full Disk Access (macOS)** - if a cookie read is permission-denied, the model surfaces the System Settings > Privacy & Security > Full Disk Access fix and offers one retry.
3. **ScrapeCreators GitHub signup** - offered on every first run. On consent it runs `setup --github`, which opens a browser for GitHub device-auth and, on success, **persists `SCRAPECREATORS_API_KEY` automatically** (0o600, masked in output) so TikTok, Instagram, Threads, Pinterest, X, and YouTube comments/transcripts activate on the next run. Decline anytime; you can run it later by asking to set up ScrapeCreators.

Re-run onboarding by deleting `~/.config/last30days/.env`. The mechanical work lives in `scripts/lib/setup_wizard.py`; the consent conversation is specified in `skills/last30days/SKILL.md` Step 0.

---

## API keys (`.env`)

The skill reads keys from a `.env` file. Two locations are supported, in priority order:

1. **`.claude/last30days.env`** in the current project directory (project-scoped) - takes precedence when present.
2. **`~/.config/last30days/.env`** at the user level (global default) - the fallback.

Override the global location with `LAST30DAYS_CONFIG_DIR=/path` (or `LAST30DAYS_CONFIG_DIR=""` for no-config mode). File permissions should be `600` on POSIX hosts - the engine warns on every run if they aren't.

The project-scoped file is the cleanest pattern for **per-client setups**: drop a `.claude/last30days.env` into each client folder (`SCRAPECREATORS_API_KEY`, `INCLUDE_SOURCES`, `LAST30DAYS_MEMORY_DIR`, `BSKY_HANDLE`, etc), `cd` into that folder, and the skill picks up that client's configuration automatically. No wrapper scripts needed for the common case.

**Source-by-source** - what each key unlocks:

| Source | Key(s) | Required for | Free tier |
|---|---|---|---|
| Reddit (public) | none | always on | yes |
| Hacker News | none | always on | yes |
| Polymarket | none | always on | yes |
| GitHub | `gh` CLI installed (uses your GitHub auth) | always on if `gh` present | yes |
| YouTube | `yt-dlp` CLI installed; `SCRAPECREATORS_API_KEY` adds a server-side transcript fallback used only when yt-dlp fails (429 / bot-gate) | always on if `yt-dlp` present; SC transcript fallback default-on when key set (no credit spent unless yt-dlp fails) | yes |
| YouTube comments | `SCRAPECREATORS_API_KEY` (default-on; suppress via `EXCLUDE_SOURCES=youtube_comments`) | top comments on the top ~3 videos by engagement | ~3 calls/run; 10K free calls |
| Digg | `digg-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only`; binary defaults to `$HOME/.local/bin` — Hermes/OpenClaw agent subprocesses must inherit that dir on PATH for Digg to activate; prior pp-digg installs use the same path) | always on if `digg-pp-cli` on PATH | yes (free, keyless, read-only) |
| X / Twitter | one of: `AUTH_TOKEN` + `CT0` (browser cookies, Bird CLI), `XAI_API_KEY`, `XQUIK_API_KEY`, `SCRAPECREATORS_API_KEY`, or `FROM_BROWSER` (cookie-jar auth) | X items in results | cookie-jar / Bird = free; Xquik / xAI / ScrapeCreators = key-based |
| TikTok | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok` | TikTok items | 10K free calls |
| Instagram | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram` | Instagram Reels | 10K free calls; raise `LAST30DAYS_TRANSCRIPT_TIMEOUT` (default 30s) if SC is slow on your network |
| Threads | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `threads` | Threads items | 10K free calls |
| Pinterest | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `pinterest` | Pinterest items | 10K free calls |
| Bluesky | `BSKY_HANDLE` + `BSKY_APP_PASSWORD` | Bluesky items | yes (app password at bsky.app) |
| TruthSocial | `TRUTHSOCIAL_TOKEN` | TruthSocial items | yes |
| Web search | one of: `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY` | `--auto-resolve` and Step 2 supplements | Brave has a free tier; native WebSearch on Claude Code / Codex / Gemini works as a fallback |
| Perplexity Sonar / Search API / Deep Research | `PERPLEXITY_API_KEY` (preferred) or `OPENROUTER_API_KEY` (Sonar fallback) | `INCLUDE_SOURCES=perplexity`; `--deep-research` flag (~$0.90/query) | no |
| Caption-free transcription | `GROQ_API_KEY` (free tier, preferred) or `OPENAI_API_KEY` (paid backstop); requires `ffmpeg` | Whisper transcription for audio/video without captions (groundwork: module shipped, not yet auto-invoked by the engine) | Groq free tier is generous; needs ffmpeg installed |
| Jobs / careers pages | none for public ATS pages; web backend improves fallback discovery | `--hiring-signals` and strong Hiring Signals in standard company reports | yes |
| Apify (alternate scraper) | `APIFY_API_TOKEN` | fallback for Reddit/TikTok/Instagram when ScrapeCreators is exhausted | yes (limited) |

**X on cookie-less hosts.** Bird (the free X source) scrapes X using your logged-in browser cookies (`AUTH_TOKEN`/`CT0`), which agent hosts like OpenClaw, CI, or headless runs often can't supply — and scraping carries some account risk. On those, set `XQUIK_API_KEY` (or `XAI_API_KEY`) for full, ranked X coverage from a single API key: the same engagement-based ranking, first-party authorship, and handle (from/mentions) lanes the native X source gets. `--diagnose` reports whether the key is working (and flags an unpaid key).

**Example `.env` skeleton** (placeholders only - replace with your own values):

```bash
# Reasoning + planning (one provider; see priority below)
GOOGLE_API_KEY=<your-gemini-key>

# Web search backend (one is enough; Brave is the cheapest)
BRAVE_API_KEY=<your-brave-key>

# Optional sources
SCRAPECREATORS_API_KEY=<your-scrapecreators-key>
INCLUDE_SOURCES=tiktok,instagram
# Add perplexity to INCLUDE_SOURCES when you want the paid Perplexity source.
# PERPLEXITY_API_KEY=<your-perplexity-key>
# INCLUDE_SOURCES=tiktok,instagram,perplexity
# LAST30DAYS_PERPLEXITY_MODE=sonar  # sonar | search | both
# LAST30DAYS_PERPLEXITY_MODEL=sonar-pro  # sonar | sonar-pro | sonar-reasoning-pro

# X authentication (one option only)
AUTH_TOKEN=<your-auth-token>
CT0=<your-ct0-token>
# OR xAI API key (paid)
# XAI_API_KEY=<your-xai-key>
# OR Xquik key-based X search
# XQUIK_API_KEY=<your-xquik-key>
# OR cookie-jar (free; logs in via your browser session).
# Unset = Firefox + Safari (silent). FROM_BROWSER=auto also tries the Chromium
# family (Chrome, Brave, Edge, Vivaldi, Opera, Arc, Chromium); it only prompts
# for macOS Keychain access on the browser that actually holds your X cookies.
# Or name a single browser, e.g. brave/edge. On Windows only Firefox is supported.
# FROM_BROWSER=firefox

# Bluesky
BSKY_HANDLE=<your-handle>.bsky.social
BSKY_APP_PASSWORD=<your-app-password>
```

After editing: `chmod 600 ~/.config/last30days/.env` (or `chmod 600 .claude/last30days.env` if using the project-scoped variant).

**Troubleshooting:** if a source you expected to see isn't appearing in results, run `python3 scripts/last30days.py --diagnose`. It prints a per-source availability report (which keys were detected, which CLIs are installed, which backends are reachable) without running a full search.

### Perplexity source modes

Perplexity is a paid opt-in source. A direct `PERPLEXITY_API_KEY` unlocks first-party Perplexity features. `OPENROUTER_API_KEY` remains a Sonar compatibility fallback only; Perplexity Search API and async Deep Research call Perplexity directly.

`LAST30DAYS_PERPLEXITY_MODE` controls normal `perplexity` source runs:

| Value | Behavior | Calls |
|---|---|---|
| `sonar` (default) | Sonar synthesis plus citations. | one Sonar call |
| `search` | Raw ranked Search API rows; best when you want source aggregation over prose. | one Search API call |
| `both` | Sonar synthesis plus raw ranked Search API rows, deduped by URL. | one Search API call and one Sonar call |

`--deep-research` ignores `LAST30DAYS_PERPLEXITY_MODE` and uses `sonar-deep-research`. With `PERPLEXITY_API_KEY`, it submits to Perplexity's async Sonar endpoint and polls with a hard wall-clock timeout. The async request uses a deterministic idempotency key derived from the request body. If the request is still running at timeout, fails remotely, or polling hits a transport/rate-limit error after the async id exists, the raw artifact records the async request id, idempotency key, last status, lifecycle timestamps returned by Perplexity, poll count, and timeout/error fields so you can inspect or resume by id outside the run. With only `OPENROUTER_API_KEY`, it keeps the OpenRouter synchronous fallback.

Perplexity-specific env vars:

| Env var | Default | Applies to | Notes |
|---|---|---|---|
| `LAST30DAYS_PERPLEXITY_MODE` | `sonar` | normal Perplexity source runs | `sonar`, `search`, or `both`; `search` and `both` require `PERPLEXITY_API_KEY`. |
| `LAST30DAYS_PERPLEXITY_MODEL` | `sonar-pro` | direct Sonar only | Supported: `sonar`, `sonar-pro`, `sonar-reasoning-pro`. `--deep-research` forces `sonar-deep-research`. |
| `LAST30DAYS_PERPLEXITY_MAX_RESULTS` | `10` | Search API | Clamped to Perplexity's 1..20 range. |
| `LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE` | provider default | Search API | `low`, `medium`, or `high`; omitted unless set. |
| `LAST30DAYS_PERPLEXITY_SEARCH_MODE` | provider default | direct Sonar | `web`, `academic`, or `sec`. |
| `LAST30DAYS_PERPLEXITY_DOMAIN_FILTER` | unset | Search API and direct Sonar | Comma-separated domains, max 20. |
| `LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER` | unset | Search API and direct Sonar | Comma-separated ISO 639-1 language codes, max 20. |
| `LAST30DAYS_PERPLEXITY_COUNTRY` | unset | Search API | Two-letter country code such as `US`. |
| `LAST30DAYS_PERPLEXITY_RECENCY_FILTER` | unset | Search API and direct Sonar | `hour`, `day`, `week`, `month`, or `year`. |
| `LAST30DAYS_PERPLEXITY_REASONING_EFFORT` | unset | direct Sonar | `minimal`, `low`, `medium`, or `high`. |
| `LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS` | `600` | direct async Deep Research | Wall-clock polling deadline. |

### Encrypted credential sources (Keychain / pass)

If you'd rather not keep keys in a plaintext `.env`, the loader has two
encrypted sources that decrypt secrets transiently at call time (never written
to disk, never logged). Both are **lowest-priority and additive** — an explicit
`.env` or process-env value always overrides them, so you can mix and match. The
`pass` source is only consulted for keys still missing after the higher-priority
sources, so a box that merely has `pass` installed pays no decrypt cost when
everything is already in `.env`.

| Platform | Source | Store keys with | Lookup convention |
|---|---|---|---|
| macOS | Keychain | `scripts/setup-keychain.sh` | service name `last30days-<KEY>` |
| Linux / Unix (anywhere `pass` exists, incl. macOS) | [`pass`(1)](https://www.passwordstore.org/) | `scripts/setup-pass.sh` | pass path `last30days/<KEY>` |

```bash
# macOS Keychain
./scripts/setup-keychain.sh                 # interactive; --list / --delete KEY

# pass(1) — Linux/Unix analog
./scripts/setup-pass.sh                      # interactive; --list / --delete KEY
./scripts/setup-pass.sh SCRAPECREATORS_API_KEY   # just one key
```

The `pass` source honors `PASSWORD_STORE_DIR`. If your store organizes secrets
under a different prefix, point the loader at it with `LAST30DAYS_PASS_PREFIX`
(works from your `.env` too, and must match where `setup-pass.sh` wrote them).
The prefix is used verbatim, so keep the trailing separator:

```bash
export LAST30DAYS_PASS_PREFIX="secrets/last30days/"   # default: last30days/
```

Both sources cover the same key set as the `.env` skeleton above.

### Bluesky app-password format and search host

`BSKY_APP_PASSWORD` should be a 19-char app password in `xxxx-xxxx-xxxx-xxxx` format (lowercase alphanumeric, three hyphens). Generate one at <https://bsky.app/settings/app-passwords>. The AT Protocol's `createSession` endpoint also accepts your main account login password, but that's bad hygiene — main passwords have no scope (an app password can be limited to non-DM access) and can't be revoked individually.

The skill defaults to `api.bsky.app` for `searchPosts`, which is the canonical authenticated AppView. The previous default `public.api.bsky.app` is the unauthenticated public mirror and is currently blocked by BunnyCDN for `searchPosts` regardless of auth header (verified 2026-05-04). If Bluesky migrates infrastructure again, override the host without a code change by setting `BSKY_SEARCH_HOST` in your `.env`:

```bash
BSKY_SEARCH_HOST=api.bsky.app   # default — change only if Bluesky moves
```

### Default source set (`LAST30DAYS_DEFAULT_SEARCH`)

By default the engine decides the source set per query (everything available, minus `EXCLUDE_SOURCES`). To pin a **fixed** source set for every run without passing `--search` each time — and without patching `SKILL.md`, which a release would overwrite — set:

```bash
LAST30DAYS_DEFAULT_SEARCH=reddit,x,youtube,hn
```

Accepts the same comma-separated names and aliases as `--search` (`web` → grounding, `hn` → hackernews, `bsky` → bluesky). Precedence: an explicit `--search` on the command line always wins; `LAST30DAYS_DEFAULT_SEARCH` applies only when the flag is omitted; when neither is set, per-query behavior is unchanged. `INCLUDE_SOURCES` / `EXCLUDE_SOURCES` keep their existing additive/subtractive roles on whichever set is selected.

---

## Reasoning provider priority

`/last30days` needs one reasoning model for planning + reranking when you don't pass `--plan` yourself. Auto-detect priority (set `LAST30DAYS_REASONING_PROVIDER=<name>` to pin one):

1. **Gemini** - `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`
2. **OpenAI** - `OPENAI_API_KEY` (or Codex auth at `~/.codex/auth.json`)
3. **xAI** - `XAI_API_KEY`
4. **OpenRouter** - `OPENROUTER_API_KEY` (Sonar fallback for the Perplexity source / `--deep-research`; also usable as a reasoning provider)
5. **Local / deterministic** - always available, lowest quality

When you invoke `/last30days` from Claude Code, Codex, or Gemini, the host model **is** the reasoning provider for plan + synthesis - you don't need any of the keys above unless you also run the script headlessly (cron, CI, watchlist).

---

## Web search backend priority

The search-source preference ladder, strict best-to-floor:

1. **Host-native search** - Claude Code's `WebSearch`, and the equivalents on Codex / Gemini. Best results; used automatically on hosts that have it. Signalled to the engine via `LAST30DAYS_NATIVE_SEARCH=1` (the skill sets this for you when your host has a native search tool) so the engine does not run a worse search underneath it.
2. **Paid engine backend** - one of `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY`, auto-detected in that order. Override per-run with `--web-backend=<name>`.
3. **Keyless engine floor** - zero-key web search (DuckDuckGo, plus an optional SearXNG instance) and zero-key page fetch (Jina Reader). Runs only when the host has **no** native search **and** no paid key is set, so headless/cron and hosts without a built-in search tool still get general-web coverage. Force it explicitly with `--web-backend=keyless`.

Relevant env vars:

| Var | Effect |
| --- | --- |
| `LAST30DAYS_NATIVE_SEARCH=1` | Tells the engine your host has native search; suppresses the keyless floor. Set automatically by the skill on capable hosts. Leave unset on hosts without a native search tool so the floor runs. |
| `LAST30DAYS_SEARXNG_URL=<base-url>` | Optional. A SearXNG instance used as the keyless-search fallback rung when DuckDuckGo returns nothing. |

Privacy note: the keyless floor sends the query (to DuckDuckGo / your SearXNG instance) and any fetched URL (to Jina Reader) to those third parties. It is intended for public-research use; results may be cached snapshots. It never runs when native search or a paid backend is in play.

Visible quality difference between hosts with vs without native search or a configured backend. If your client setup produces thinner results than yours, this is usually why.

---

### `--hiring-signals` flag

Use `--hiring-signals` for a focused company hiring-signal report:

```bash
python3 skills/last30days/scripts/last30days.py "Listen Labs" --hiring-signals
```

The engine treats public jobs/careers postings as evidence of focus or priority shifts, not exact roadmap predictions. Standard company runs may include Hiring Signals automatically when multiple current roles support the same interpretation; weak or unavailable hiring evidence is omitted.

---

## Trend monitoring (`--store` + watchlist + briefings)

The default behavior - one slug-named file per topic, overwritten on rerun - is the snapshot mode. For continuous monitoring, the repo ships three components most users miss:

### `--store` flag

Adding `--store` to any run persists every finding to a SQLite database (default at `~/.local/share/last30days/research.db`). Findings dedupe on the `source_url` column (UNIQUE constraint), so the same URL across runs updates the existing row instead of creating a duplicate. The markdown file still saves; the SQLite is the time-series substrate.

**Always-on alternative:** set `LAST30DAYS_STORE=1` in your `.env` instead of remembering `--store` on every invocation. The flag still works as before; the env var is purely additive. Same hybrid pattern as `LAST30DAYS_DEBUG` — works whether shell-exported or in `.env`.

Relevant tables: `topics`, `research_runs`, `findings`, `settings`. Schema: [`scripts/store.py`](skills/last30days/scripts/store.py).

### `watchlist.py` - recurring topics

[`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) manages topics that should be researched on a schedule. Subcommands: `add`, `remove`, `list`, `run-one`, `run-all`, `config`. Built-in delivery to Slack incoming webhooks (`hooks.slack.com/...`) or any HTTPS endpoint, fired only when new findings appear.

Two-step flow (the watchlist holds the topic; an external scheduler invokes the run):

```bash
# 1. Add the topic to the watchlist
#    Default schedule daily 8am; --weekly switches to Mondays 8am
python3 scripts/watchlist.py add "british airways middle east" --weekly

# 2. Configure delivery and budget (optional)
python3 scripts/watchlist.py config delivery "https://hooks.slack.com/services/..."
python3 scripts/watchlist.py config budget 5.00

# 3. Trigger via cron / Task Scheduler / GitHub Actions
python3 scripts/watchlist.py run-one "british airways middle east"
# or run every enabled topic, gated by daily_budget
python3 scripts/watchlist.py run-all
```

The schedule field stored on each topic is metadata - the actual cron / Task Scheduler invocation is your responsibility. Watchlist runs hardcode `--quick` and `--lookback-days 90` when spawning the underlying engine.

### `briefing.py` - daily / weekly digests

[`scripts/briefing.py`](skills/last30days/scripts/briefing.py) reads the SQLite store and emits structured data the agent then synthesizes into prose. Modes: `generate` (daily), `generate --weekly`, `show [--date DATE]` (display a saved briefing). Briefs save to `~/.local/share/last30days/briefs/`.

### Recommended cadence pattern

| Step | Cadence | Command |
|---|---|---|
| Baseline | one-time per topic | `/last30days "<topic>" --days=30 --store` |
| Add to watchlist | one-time per topic | `python3 scripts/watchlist.py add "<topic>" --weekly` |
| Recurring run | daily or weekly (external scheduler) | `python3 scripts/watchlist.py run-all` |
| Digest | weekly | `python3 scripts/briefing.py generate --weekly` |

---

## Per-client patterns

The skill is built to flex around different client environments. Four patterns that compose well:

### 1. Per-client `.claude/last30days.env` (preferred when you cd into client folders)

The simplest pattern when each client has its own working directory: drop a `.claude/last30days.env` into the client folder. The skill picks it up automatically (see [API keys](#api-keys-env) for the lookup priority). Typical contents:

```bash
LAST30DAYS_MEMORY_DIR=C:\Users\<you>\Clients\acme\Research\Last30Days
SCRAPECREATORS_API_KEY=<acme-scoped-key-or-shared>
INCLUDE_SOURCES=tiktok,instagram
BSKY_HANDLE=<acme-bluesky-handle>.bsky.social
```

`cd` into the client folder, run `/last30days <topic>` as normal, no flags or wrappers. Combine with `--save-suffix=<client-slug>` per run if you also need to differentiate filenames within that folder.

### 2. Per-client save dir + suffix wrapper

For workflows where you don't `cd` into a client folder (running from anywhere, scripted batches), a tiny shell function isolates each client's research without engine changes.

PowerShell example:

```powershell
function Run-L30D-Client {
    param([string]$ClientSlug, [Parameter(ValueFromRemainingArguments=$true)]$Args)
    $env:LAST30DAYS_MEMORY_DIR = "C:\Users\$env:USERNAME\Clients\$ClientSlug\Research\Last30Days"
    /last30days @Args --save-suffix=$ClientSlug
}
# Usage: Run-L30D-Client acme "british airways middle east"
```

Bash example:

```bash
l30d-client() {
    local client=$1; shift
    LAST30DAYS_MEMORY_DIR="$HOME/Clients/$client/Research/Last30Days" \
        /last30days "$@" --save-suffix="$client"
}
# Usage: l30d-client acme "british airways middle east"
```

### 3. Custom category-peer subreddits

[`scripts/lib/categories.py`](skills/last30days/scripts/lib/categories.py) holds a table of `(category_id, trigger_keywords, peer_subreddits)`. If a client lives in a vertical that isn't covered (legal-tech, real-estate-tech, B2B HR SaaS), add a row. Pure data, no logic.

Section 2a of `SKILL.md` documents the merging rule the skill applies when your topic matches a category.

### 4. Pre-built `--competitors-plan` JSON

For competitor-vs-comparisons that recur, a pre-written JSON skeleton per client industry saves real time:

```json
{
  "Competitor B": {
    "x_handle": "competitor_b_handle",
    "subreddits": ["sub1", "sub2"],
    "github_user": "competitor-b-org",
    "context": "Founded 2019, focused on ..."
  },
  "Competitor C": { ... }
}
```

Pass as `--competitors-plan @client/competitors-plan.json` (or as a string). See `SKILL.md` section "If QUERY_TYPE = COMPARISON" for the full schema.

---

## Beta channel

Experimental customizations live on a private companion repo (`mvanhorn/last30days-skill-private`) installed as `/last30days-beta`. Never ship beta-only changes to the public marketplace without a review PR against the public repo. Workflow guide: `BETA.md` in the private repo.

This is the right home for client-specific changes you don't intend to upstream - custom category rows, internal subreddit lists, per-vertical plan templates.

---

## Cross-references

- The CLI flag surface: `python3 scripts/last30days.py --help`
- The skill contract (voice, LAWs, pre-flight protocol): [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md)
- Shared package vocabulary and engine/harness terminology: [`CONCEPTS.md`](CONCEPTS.md)
- Contributor guidance: [`CONTRIBUTORS.md`](CONTRIBUTORS.md)
