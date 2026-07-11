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
# LAST30DAYS_LIBRARY_OWNER=Your Name                              # Optional Atom feed author
# LAST30DAYS_LIBRARY_CONTEXT=off                                  # Disable prior-run context (default: on)
```

The engine's `.env` reader doesn't expand `$HOME` — only the tilde, via `Path().expanduser()` downstream. Use `~/...` or an absolute path; **don't** write the literal string `$HOME/...` into your `.env` (it gets stored verbatim and breaks path resolution).

**Per-run overrides:**

- `--save-dir <path>` - one-off output location. **Flag wins over env var.** If neither flag nor env var is set, the engine does not write a file (DB persistence is independent — see `LAST30DAYS_STORE` below).
- `--output <file>` - write the rendered output to an exact file path, using the format selected by `--emit`.
- `--json-profile {agent,raw}` - select the research JSON shape used with `--emit=json`. `agent` is the default, versioned workflow contract; `raw` preserves the full internal `Report` dump for debugging and power users. See the [JSON export reference](docs/reference/json-export.md).
- `--register {default,exec,dev,creator,eli5}` - shape a standard single-topic Markdown or HTML research brief for its audience. `exec` is decisions-first with five core findings and numbers up top; `dev` gives GitHub, code, and technical signals more room; `creator` leads with hooks, Best Takes, community reactions, and virality metrics; `eli5` keeps the established evidence layout and asks the synthesizing agent for accessible language. Registers do not change retrieval, JSON exports, discovery, drill, library feed/search, or comparison output.
- `--discover <domain>` - topic-less trending discovery. Sweeps rising/top-week Reddit listings (category-mapped communities, with r/all as the uncategorized fallback), Hacker News front/best stories, Digg AI 1000 clusters when `digg-pp-cli` is on PATH, and broad X activity when an X backend is authenticated, then returns 5-10 engagement-velocity-ranked topics. Run without a positional topic; it is mutually exclusive with `--drill`. `--emit=json` uses the separate versioned discovery contract documented in the [JSON export reference](docs/reference/json-export.md).
- `--drill <target>` - deep follow-up over the fresh `~/.config/last30days/last-report.json` cache. Accepts a 1-based index (`--drill "cluster 3"` or `--drill "3"`) or a fuzzy cluster title/entity description. It re-fetches only sources that contributed to the matched cluster, enables their deep comment/transcript enrichment paths, merges/dedupes the evidence, and replaces the cache so drills can chain. Run it without a positional topic; if the cache is absent or expired, run a normal research pass first.
- `--save-suffix <name>` - distinguish runs of the same topic (e.g. per client: `--save-suffix=acme`).
- `--no-browser-cookies` - hard-disable browser-cookie extraction for this run, even when `FROM_BROWSER` is configured. MCP and folder-mode hosts use this for safe defaults.
- `--publish-html` - with `--emit=html`, publish the rendered HTML to `ht-ml.app` after local output/save-dir writes. This is explicit opt-in only; pages are public by default.
- `library feed` - scan `LAST30DAYS_MEMORY_DIR` plus `~/.local/share/last30days/briefs/`, then write a self-contained `index.html`, valid Atom `feed.xml`, and browser-ready pages under `briefs/`. The index is reverse-chronological and grouped by topic. For direct engine use: `python3 skills/last30days/scripts/last30days.py library feed`; use `--save-dir <path>` to scan and write another library directory.
- `library feed --publish` - publish each rendered brief and the HTML index through `ht-ml.app`. The generated `feed.xml` remains a first-class local artifact because this HTML host does not serve Atom with an XML content type. Host the output directory on any static host (for example, GitHub Pages) to make `feed.xml` subscribable. Publishing is explicit opt-in and pages are public by default; public pages may be crawled or indexed.
- `library search "<query>"` - incrementally sync `LAST30DAYS_MEMORY_DIR` and `~/.local/share/last30days/briefs/` through the shared library scanner, then run offline SQLite FTS5 across those briefs plus dated per-run sightings in `~/.local/share/last30days/research.db`. Results are grouped by topic run. The sibling search index lives at `~/.local/share/last30days/library.db`; hand edits, renames, and deletes are picked up on sync, and a corrupt index is rebuilt automatically.
- `LAST30DAYS_LIBRARY_OWNER=<name>` - optional feed-level Atom author. Defaults to `last30days research library`.
- `LAST30DAYS_LIBRARY_CONTEXT=on|off` - controls passive prior-run context on fresh research reports. It defaults to `on`; matching saved research appears in a short `From your library` section. Set `off` to skip the local index read and leave reports unchanged. Mock runs, eval replays, and internal fan-out subruns do not load library context, keeping fixtures deterministic.
- `--publish-password <password>` - optional shared password for `--publish-html` or `library feed --publish`. Prefer `LAST30DAYS_PUBLISH_PASSWORD=<password>` instead so the password is not visible in the process list or shell history. Use a unique non-personal password; never reuse the user's own password. The provider's update key is treated as secret and is not written to stdout, HTML, raw output, or `.publish.json` metadata.
- `--preflight` - print a human-readable permission preflight. It reports config source, project config trust/ignore state, browser-cookie plan, planned writes, optional commands, source availability, and endpoint overrides without reading browser cookies, writing setup/config/report files, or running research. Add `--emit=json` for the separate machine-readable preflight contract (`--json-profile` does not change it); use `--diagnose` when you need the full source diagnostic JSON.
- `--welcome` - print the first-run welcome text (engine-owned; the skill relays it verbatim on first run). Safe: prints and exits, no reads or writes.
- `--record-fixtures <dir>` - developer-only, hidden flag that records scrubbed source responses for the offline research-quality eval harness. It writes `<dir>/http.json`; see the [eval reference](docs/reference/eval.md) before recording or committing fixtures.
- `setup --github-start` / `setup --github-poll` - the two-command ScrapeCreators GitHub device-auth split. `--github-start` submits the device flow, copies the code to the clipboard, opens the browser, and returns the code immediately (foreground); `--github-poll` waits for you to authorize and persists the key. `setup --github` still runs both in one shot for back-compat.

The footer line `📎 Raw results saved to ${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}/<slug>-raw.md` is the canonical pointer; if it shows backslashes on Windows update past v3.1.1.

Every completed research pass writes a structured `last-report.json` cache beside `last-run.json`. HTML follow-up renders use it so `--emit=html --synthesis-file` can reuse report metadata/footer without fetching sources again; `--drill <target>` uses it as the grounded starting point for targeted re-research. Reuse is intentionally short-lived: `LAST30DAYS_REPORT_CACHE_TTL_SECONDS` defaults to `3600` (one hour). Set it to another integer number of seconds to tune the window, or `0` to disable report-cache reuse and drill follow-ups.

---

## First-run onboarding

On the very first `/last30days` run (no `~/.config/last30days/.env`, or `SETUP_COMPLETE` not set), the skill runs a consent-driven onboarding the model drives in chat. It takes one of two forms depending on the host:

- **Claude Code Modal Flow** - the restored v3.0.0 guided NUX, used on hosts with `AskUserQuestion` (Claude Code). A welcome message, then modals for Auto/Manual/Skip setup, cookie consent, the ScrapeCreators signup offer, a TikTok/Instagram `INCLUDE_SOURCES` opt-in, and a first-topic picker.
- **Non-Modal Prose Flow** - the same work done conversationally on hosts without modals (OpenClaw, Codex, Cursor, Gemini CLI, Grok, raw CLI).

Both share the same consent points:

1. **Browser cookies** - the model asks before reading anything. On yes it runs `setup --allow-browser-cookies`, which extracts Firefox/Safari cookies (never Chrome unless `FROM_BROWSER=auto` or a named Chromium browser is explicitly configured) to unlock X/Twitter and other logged-in sources, and installs yt-dlp + the keyless Digg CLI. On no it runs setup without `--allow-browser-cookies` (or with `FROM_BROWSER=off`), which skips all cookie reads and still installs the tools.
2. **Full Disk Access (macOS)** - if a cookie read is permission-denied, the model surfaces the System Settings > Privacy & Security > Full Disk Access fix and offers one retry.
3. **ScrapeCreators GitHub signup** - offered on every first run (10,000 free calls). On consent it runs `setup --github`, which opens a browser for GitHub device-auth (or registers instantly via the `gh` CLI when installed) and, on success, **persists `SCRAPECREATORS_API_KEY` automatically** (0o600, masked in output) so TikTok, Instagram, and the SC Reddit/YouTube backups activate on the next run. Decline anytime; you can run it later by asking to set up ScrapeCreators. The Step 5 opt-in has two tiers, both comment-enabled: **Recommended** (TikTok + Instagram posts AND top comments, plus YouTube comments — `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments`) and **Everything**, which also adds Threads + Pinterest. Comments are on by default; Threads and Pinterest are the only opt-in extras.

Re-run onboarding by deleting `~/.config/last30days/.env`. The mechanical work lives in `scripts/lib/setup_wizard.py`; the consent conversation and both host flows are specified in `skills/last30days/SKILL.md` Step 0. The original v3.0.0 wizard is captured at `docs/reference/old-nux-wizard-v3.0.0.md`.

---

## API keys (`.env`)

The skill reads keys from a `.env` file. Two locations are supported:

1. **`~/.config/last30days/.env`** at the user level (global default) - loaded by default.
2. **`.claude/last30days.env`** in the current project directory (project-scoped) - loaded only when trusted by setting `LAST30DAYS_TRUST_PROJECT_CONFIG=1` in the process environment or global config.

Override the global location with `LAST30DAYS_CONFIG_DIR=/path` (or `LAST30DAYS_CONFIG_DIR=""` for no-config mode). File permissions should be `600` on POSIX hosts - the engine warns on every run if they aren't.

The project-scoped file is useful for **intentional per-client setups**: drop a `.claude/last30days.env` into each client folder (`SCRAPECREATORS_API_KEY`, `INCLUDE_SOURCES`, `LAST30DAYS_MEMORY_DIR`, `BSKY_HANDLE`, etc), then opt in with `LAST30DAYS_TRUST_PROJECT_CONFIG=1` from your shell or `~/.config/last30days/.env`. Folder-mode hosts such as Codex desktop do not trust hidden project config by default, and discovery stops at the git root so unrelated parent folders cannot silently influence runs.

**`LAST30DAYS_API_KEY`** + **`LAST30DAYS_API_BASE`** - optional remote-API backend. Set BOTH to route research through a remote API endpoint instead of running the local sources: `LAST30DAYS_API_BASE` is the endpoint (there is no built-in default), and `LAST30DAYS_API_KEY` is the bearer key for it. When both are set (and `--mock` is not passed), the engine submits the topic to that endpoint, polls with progress on stderr, and prints the server's report; none of the per-source keys below are used for that run. Non-default `--register` selections are forwarded with the request so server-side synthesis uses the same audience preset. Leave either unset to run local sources exactly as normal. Unlike the other keys here, these two are read only from the **process environment** (export them in your shell or host config) - they are deliberately not loaded from the `.env` files above, so a project-scoped `.env` can never silently redirect research to a remote endpoint. The remote endpoint does not return the local `Report` needed for the versioned agent JSON profile; use `--emit=json --json-profile=raw` for its existing server-response JSON contract.

**Source-by-source** - what each key unlocks:

| Source | Key(s) | Required for | Free tier |
|---|---|---|---|
| Reddit (public) | none (default); `SCRAPECREATORS_API_KEY` + `LAST30DAYS_REDDIT_BACKEND=scrapecreators` to pin SC primary with public fallback | always on; SC pin requires `SCRAPECREATORS_API_KEY` | yes |
| Hacker News | none | always on | yes |
| Polymarket | none | always on | yes |
| StockTwits | none | auto-on for ticker/crypto topics only (gated by symbol detection); never registered for non-financial topics | yes (public API, ~200 req/hr per IP) |
| GitHub | `gh` CLI installed (uses your GitHub auth) | always on if `gh` present | yes |
| YouTube | `yt-dlp` CLI installed; `SCRAPECREATORS_API_KEY` adds a server-side transcript fallback used only when yt-dlp fails (429 / bot-gate) | always on if `yt-dlp` present; SC transcript fallback default-on when key set (no credit spent unless yt-dlp fails) | yes |
| YouTube comments | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `youtube_comments` (**on by default** — written by the Step 5 Recommended tier) | top comments (by likes) on the top ~3 videos by engagement | ~3 calls/run; 10K free calls |
| TikTok comments | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok_comments` (**on by default** — Step 5 Recommended tier) | top comments (by `digg_count`) on the top ~3 TikTok posts | ~3 calls/run; 10K free calls |
| Instagram comments | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram_comments` (**on by default** — Step 5 Recommended tier) | top comments (by `comment_like_count`) on the top ~3 Instagram posts, via `/v2/instagram/post/comments` | ~3 calls/run; 10K free calls |
| Digg | `digg-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only`; binary defaults to `$HOME/.local/bin` — Hermes/OpenClaw agent subprocesses must inherit that dir on PATH for Digg to activate; prior pp-digg installs use the same path) | always on if `digg-pp-cli` on PATH | yes (free, keyless, read-only) |
| arXiv | `arxiv-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install arxiv --cli-only`) | always on if `arxiv-pp-cli` on PATH; fires on research/technical topics and stays quiet otherwise (relevance + 365-day recency gating) | yes (free, keyless) |
| Techmeme | `techmeme-pp-cli` on PATH (auto-installed via `... install techmeme --cli-only`) | always on if `techmeme-pp-cli` on PATH; searches Techmeme's live archive and keeps only headlines dated within the research window (undated headlines flow through as low-confidence) | yes (free, keyless) |
| Trustpilot | `trustpilot-pp-cli` on PATH (NOT auto-installed; install on demand via `npx -y @mvanhorn/printing-press-library@0.1.16 install trustpilot --cli-only`) + `INCLUDE_SOURCES` contains `trustpilot` | **opt-in, off by default**; when enabled, activates only on company/brand topics — or on any topic when `--trustpilot-domain=<domain>` pins the review page explicitly (bypasses the brand-shape gate; also the per-entity `trustpilot_domain` key in `--competitors-plan`). Bare company names auto-resolve to the review-page domain via the CLI's search. The session warms once before the search fan-out; a stale session does a ~10s headless-Chrome WAF-cookie harvest (set `LAST30DAYS_TRUSTPILOT_NO_BROWSER=1` to disable in cron/CI) | yes (no API key; cookie-replay after the one-time harvest) |
| X / Twitter | one of: `AUTH_TOKEN` + `CT0` (browser cookies, Bird CLI), `XAI_API_KEY`, `XQUIK_API_KEY`, `SCRAPECREATORS_API_KEY`, or `FROM_BROWSER` (cookie-jar auth) | X items in results | cookie-jar / Bird = free; Xquik / xAI / ScrapeCreators = key-based |
| TikTok | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok` | TikTok items | 10K free calls |
| Instagram | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram` | Instagram Reels | 10K free calls; raise `LAST30DAYS_TRANSCRIPT_TIMEOUT` (default 30s) if SC is slow on your network |
| Threads | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `threads` | Threads items | 10K free calls |
| Pinterest | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `pinterest` | Pinterest items | 10K free calls |
| LinkedIn | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `linkedin` | LinkedIn posts + articles (articles rank as high signal on person topics) | 10K free calls; power-user opt-in, not offered during first-run onboarding |
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
# Unset = no browser-cookie reads. FROM_BROWSER=auto tries Firefox/Safari and
# the Chromium family (Chrome, Brave, Edge, Vivaldi, Opera, Arc, Chromium); it
# only prompts for macOS Keychain access on the browser that actually holds your
# X cookies. Or name a single browser, e.g. brave/edge. On Windows only Firefox
# is supported.
# FROM_BROWSER=firefox

# Bluesky
BSKY_HANDLE=<your-handle>.bsky.social
BSKY_APP_PASSWORD=<your-app-password>
```

After editing: `chmod 600 ~/.config/last30days/.env` (or `chmod 600 .claude/last30days.env` if using the project-scoped variant).

**Troubleshooting:** if a source you expected to see isn't appearing in results, run `python3 scripts/last30days.py --preflight` for a human permission summary or `python3 scripts/last30days.py --diagnose` for full JSON diagnostics. Both are safe: they report source availability, config source, browser-cookie plan, external command availability, write destinations, and ignored untrusted project config without reading browser cookies or running live provider probes.

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

Effective credential priority is: process env > trusted project config
(`.claude/last30days.env`) > global config (`~/.config/last30days/.env`) >
macOS Keychain > `pass`(1). The SessionStart status hook also checks for
Keychain item **presence** under `last30days-<KEY>` without reading secret
values, so a Keychain-only setup is treated as configured instead of showing the
first-run welcome again.

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

#### Reusing existing macOS Keychain items

If you already have keys stored under another Keychain naming convention, you
can reference them without copying the secret by setting non-secret alias
metadata in `LAST30DAYS_KEYCHAIN_ALIASES`. The loader still checks
`last30days-<KEY>` first; aliases are fallback lookups only.

```bash
# ~/.config/last30days/.env
LAST30DAYS_KEYCHAIN_ALIASES={"XAI_API_KEY":{"account":"keychain-user","service":"existing-xai-api-key"},"BRAVE_API_KEY":"existing-brave-api-key"}
```

Each JSON key must be one of the supported env-var names (`XAI_API_KEY`,
`SCRAPECREATORS_API_KEY`, `BRAVE_API_KEY`, etc). A string value means "use this
service name with the current user account"; an object can specify both
`account` and `service`. Lists are allowed for fallback order:

```bash
LAST30DAYS_KEYCHAIN_ALIASES={"XAI_API_KEY":[{"account":"keychain-user","service":"existing-xai-api-key"},{"service":"last-resort-xai"}]}
```

The alias value contains no secret material; it is safe to keep in `.env` as
configuration. The secret itself remains in its original Keychain item and is
read directly by the engine process.

Write `LAST30DAYS_KEYCHAIN_ALIASES` as a single-line JSON value in `.env`.
Multiline JSON formatting is not supported because `.env` files are parsed
line-by-line.

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

### Audience register (`LAST30DAYS_REGISTER`)

The default standard brief stays balanced and byte-compatible with prior releases. To keep a named audience preset across runs, set one of the supported values:

```bash
LAST30DAYS_REGISTER=exec  # default | exec | dev | creator | eli5
```

An explicit `--register` wins over `LAST30DAYS_REGISTER`; the environment/config value defaults to `default`. Presets are intentionally named and bounded - arbitrary prompt or template files are not accepted. Existing `ELI5_MODE=true` configurations continue to resolve to the `eli5` register when no explicit register is selected, but new configuration should use `LAST30DAYS_REGISTER=eli5`.

---

## Reasoning provider priority

`/last30days` needs one reasoning model for planning + reranking when you don't pass `--plan` yourself. Auto-detect priority (set `LAST30DAYS_REASONING_PROVIDER=<name>` to pin one):

1. **Gemini** - `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`
2. **OpenAI** - `OPENAI_API_KEY` only. Codex ChatGPT auth at `~/.codex/auth.json` is intentionally not used as an OpenAI provider credential.
3. **xAI** - `XAI_API_KEY`
4. **OpenRouter** - `OPENROUTER_API_KEY` (Sonar fallback for the Perplexity source / `--deep-research`; also usable as a reasoning provider)
5. **Local / deterministic** - always available, lowest quality

When you invoke `/last30days` from Claude Code, Codex, or Gemini, the host model **is** the reasoning provider for plan + synthesis - you don't need any of the keys above unless you also run the script headlessly (cron, CI, watchlist).

---

## Web search backend priority

The search-source preference ladder, strict best-to-floor:

1. **Host web search** - whatever web-search capability the agent session already has: built-in search, a deferred web-search tool that must be loaded first, or an installed connector such as Brave, Firecrawl, Exa, Serper, or another provider. Best results; used automatically on hosts that have it. A failed lookup for one specific tool name is not fatal when another web-search capability is available. Signalled to the engine via `LAST30DAYS_NATIVE_SEARCH=1` (the skill sets this for you when your agent session has web search) so the engine does not run a worse search underneath it.
2. **Paid engine backend** - one of `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY`, auto-detected in that order. Override per-run with `--web-backend=<name>`.
3. **Keyless engine floor** - zero-key web search (DuckDuckGo, plus an optional SearXNG instance) and zero-key page fetch (Jina Reader). Runs only when the agent session has **no** host web search **and** no paid key is set, so headless/cron and hosts without a search tool still get general-web coverage. Force it explicitly with `--web-backend=keyless`.

Relevant env vars:

| Var | Effect |
| --- | --- |
| `LAST30DAYS_NATIVE_SEARCH=1` | Tells the engine your agent session has host-side web search; suppresses the keyless floor. Set automatically by the skill when web search is available. Leave unset when the agent has no web-search tool so the floor runs. |
| `LAST30DAYS_SEARXNG_URL=<base-url>` | Optional. A SearXNG instance used as the keyless-search fallback rung when DuckDuckGo returns nothing. |
| `LAST30DAYS_TRUSTPILOT_NO_BROWSER=1` | Optional. Truthy value disables the Trustpilot source's one-time headless-Chrome WAF-cookie harvest, so an automated/headless run (cron, CI, the eval harness) never spawns a browser. Trustpilot still degrades to empty gracefully. |

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

## Health check (`doctor`)

One command answers "what's broken, what's serving, and what do I run to fix it" — per source: a rollup tier (ok / warn / off / error), the specific probe state, the backend the next run will use (for chained sources), and an exact fix on any non-ok tier:

```bash
python3 skills/last30days/scripts/last30days.py doctor            # grouped text report
python3 skills/last30days/scripts/last30days.py doctor --json     # machine contract
python3 skills/last30days/scripts/last30days.py doctor --cached   # serve the cached report while fresh
```

Slash-command form: `/last30days doctor`. Reporting problems is a successful run — the exit code is always 0, no browser cookies are read, no network calls are made, and no secret values appear anywhere (key presence is booleans only). Backends within a chained source are probed sequentially with a 5-second budget per binary probe, so a chained source's worst-case check time is additive across its backends (only reached when several binaries hang at once).

Every live run writes its JSON result to `~/.config/last30days/doctor-cache.json` (beside `last-run.json`; honors `LAST30DAYS_CONFIG_DIR`). `doctor --cached` returns that stored report when it is younger than the TTL, and falls through to a live run — rewriting the cache — when it is stale, absent, or corrupt. The cache also self-invalidates on configuration change: the payload carries a schema stamp plus a fingerprint of non-secret config signals (which credentials are present as booleans, the `LAST30DAYS_X_BACKEND` / `LAST30DAYS_REDDIT_BACKEND` pin values, and `INCLUDE_SOURCES`), so adding or removing a key, changing a pin, or toggling an opt-in source makes the next `--cached` call run live — no raw secret ever enters the fingerprint or the file. Every report also carries `from_cache` (true/false) and `generated_at` (when the report was built), in the `--json` top level and as a final `generated: … (cached|live)` text line, so you can always tell how old a cached answer is. A failed cache write is never fatal — doctor prints a one-line stderr warning and continues. An explicit `doctor` without `--cached` always runs live and refreshes the cache.

| Var | Effect |
| --- | --- |
| `LAST30DAYS_DOCTOR_TTL` | Freshness window for `doctor --cached`, in **seconds**. Defaults to `900` (15 minutes). `0` makes every `--cached` call run live. |
| `LAST30DAYS_X_BACKEND` | Pins the X backend (`xai` / `bird` / `xurl` / `xquik`); doctor renders the pin and predicts "will use" accordingly. |
| `LAST30DAYS_REDDIT_BACKEND` | `scrapecreators` makes ScrapeCreators the primary Reddit backend; doctor renders Reddit's conditional routing with the pin applied. |

Web search has **no** env pin — pin it per-run with `--web-backend=<name>` only (see [Web search backend priority](#web-search-backend-priority)).

### Strict exit for degraded runs

By default a research run exits `0` even when a source failed mid-run (rate-limited, auth-failed, unreachable, timeout, schema-drift) — the report still renders, with the failure annotated in the per-source footer and a partial-coverage warning. Wrappers that need to distinguish degraded coverage from success (cron briefs, CI, downstream agents) can opt in:

| Var | Effect |
| --- | --- |
| `LAST30DAYS_STRICT_EXIT` | Truthy (`1`/`true`/`yes`/`on`): the engine exits `3` when any source outcome is neither `ok`, `no-results`, nor `skipped-unconfigured`. A one-line `strict-exit: degraded sources: ...` note goes to stderr. Default (unset): exit `0`, unchanged behavior. |

Exit codes with the flag on: `0` clean run, `3` completed-but-degraded (report was produced), non-zero others unchanged (hard failures). Same hybrid pattern as `LAST30DAYS_DEBUG` — works shell-exported or in `.env`.

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

**Codex note:** the repository includes `.codex-plugin/plugin.json` so Codex can treat the existing
`skills/last30days/SKILL.md` tree as plugin metadata without maintaining a separate Codex copy.
The Codex marketplace catalog points at the repository root URL: Codex clones the repo, reads the
root `.codex-plugin/plugin.json`, and loads skills from `./skills/`. The Agent Skills install
command documented in the README remains the broadest cross-host path.

**Grok note:** the repository includes `.grok-plugin/plugin.json` and `.grok-plugin/marketplace.json`
so xAI's Grok Build CLI (`grok`) can install last30days as a native plugin. Grok also reads the
Claude Code manifests for compatibility; the native pair is the first-class lane. The Grok
marketplace catalog uses a bare Git URL source (no commit pin) so `grok plugin marketplace add
mvanhorn/last30days-skill` tracks HEAD — the same pattern as the Codex catalog. `npx skills add`
remains a valid cross-host fallback.

### 1. Trusted per-client `.claude/last30days.env`

When each client has its own working directory, drop a `.claude/last30days.env` into the client folder and opt in with `LAST30DAYS_TRUST_PROJECT_CONFIG=1` from your shell or global `~/.config/last30days/.env`. The skill loads the project file only after that trust signal. Typical contents:

```bash
LAST30DAYS_MEMORY_DIR=C:\Users\<you>\Clients\acme\Research\Last30Days
SCRAPECREATORS_API_KEY=<acme-scoped-key-or-shared>
INCLUDE_SOURCES=tiktok,instagram
BSKY_HANDLE=<acme-bluesky-handle>.bsky.social
```

`cd` into the client folder, run `/last30days <topic>` as normal, no wrappers. Combine with `--save-suffix=<client-slug>` per run if you also need to differentiate filenames within that folder.

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
