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

`.env` files don't travel between machines or harnesses, so set `LAST30DAYS_MEMORY_DIR` explicitly in `~/.config/last30days/.env` once per host. The `/last30days` slash command resolves its directory through the engine: explicit `--save-dir` > process environment > trusted project `.claude/last30days.env` > global `.env` > `~/Documents/Last30Days`. An explicitly empty value disables research file saves and the saved-file appendix. Library and topic-queue commands use the same resolution; discovery captures the result once and reuses it for every protocol leg. **Bare engine invocations** — `python3 scripts/last30days.py ...` from cron jobs, scripts, or agents that bypass the wrapper — keep their existing no-save default when neither a flag nor configuration supplies a directory.

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
- `--resolve-save-dir` - print the absolute save directory selected for the skill and exit without research, browser-cookie reads, credential-store lookups, or file writes. It honors `--save-dir`, process environment, and trusted config files, preserving empty values. For memory directories, whole unresolved host placeholders such as `${user_config.memory_dir}` are absent at each configuration layer: a process placeholder falls back to trusted project then global configuration, and a project placeholder preserves the global value, including an empty value that disables saving. The bare engine uses the same placeholder precedence. Explicit `--save-dir` values remain literal paths. Only this query supplies the skill's `~/Documents/Last30Days` fallback; it does not change the bare research engine's no-save default. Shell wrappers capture this output without sourcing `.env` files.
- `--output <file>` - write the rendered output to an exact file path, using the format selected by `--emit`.
- `--json-profile {agent,raw}` - select the research JSON shape used with `--emit=json`. `agent` is the default, versioned workflow contract; `raw` preserves the full internal `Report` dump for debugging and power users. See the [JSON export reference](docs/reference/json-export.md).
- `--corpus <dir>` - add a local `.md`/`.txt` directory as a private ranked source; repeat the flag for multiple directories. PDFs are extracted only when `pdftotext` is on PATH and otherwise skip with a note. File modification time supplies recency, so the normal research window applies.
- `--corpus-all-time` - include relevant registered files whose modification time is older than the current research window. Without this flag, a 30-day run includes only files modified in those 30 days.
- `--perplexity-search-type {fast,web}` - search type for direct Perplexity Search API and Agent `web_search` requests on this run; overrides `LAST30DAYS_PERPLEXITY_SEARCH_TYPE`. It does not enable the Perplexity source and needs a direct `PERPLEXITY_API_KEY`. When the hosted backend is configured and a direct `PERPLEXITY_API_KEY` exists, this flag makes the run execute locally with one stderr notice; without a direct key the run stays hosted and prints a note that the flag was not applied; a value set only in `LAST30DAYS_PERPLEXITY_SEARCH_TYPE` keeps hosted routing and prints a note that it was not applied.
- `--register {default,exec,dev,creator,eli5}` - shape a standard single-topic Markdown or HTML research brief for its audience. `exec` is decisions-first with five core findings and numbers up top; `dev` gives GitHub, code, and technical signals more room; `creator` leads with hooks, Best Takes, community reactions, and virality metrics; `eli5` keeps the established evidence layout and asks the synthesizing agent for accessible language. Registers do not change retrieval, JSON exports, discovery, drill, library feed/search, or comparison output.
- `--discover [domain]` - trending discovery, two-stage: a river-listing sweep NOMINATES candidate topics, then each nomination gets a full research pass (Reddit with comments, X, YouTube, Techmeme, arXiv, HN, Polymarket, web) before ranking. Bare `--discover` (no domain) is **global trending**: every feed's own hot list (r/all rising/top-week, Hacker News front/best, Digg clusters when `digg-pp-cli` is on PATH) with no keyword gate; with a domain, the sweep is category-scoped and keyword-gated, and broad X activity joins when an X backend is authenticated. Every topic must clear a confidence floor (cross-source confirmation or a genuinely strong single-source spike); when nothing clears it the run reports "Nothing solid this window" instead of ranked noise. Run without a positional topic; it is mutually exclusive with `--drill`. `--emit=json` uses the separate versioned discovery contract (now with `outcome`, `weak_signal`, per-topic `top_comment` and `corroboration_count`) documented in the [JSON export reference](docs/reference/json-export.md).
- `--discover-shallow` - skip discovery's per-topic research passes and rank on listing evidence only. Faster and thinner; the confidence floor still applies. An explicit `--search` source list bounds both the sweep and the research passes. On a protocol run (below), adding it to the `--nominate-only` leg marks the bundle quick-tier so the resume leg uses the faster shallow research pass.
- `--nominate-only` - leg 1 of the three-command host-judged discovery protocol (agent hosts; SKILL.md drives it - one-shot `--discover` stays the scripting/cron form with deterministic topic names and no angles). With `--discover [domain]`: sweep the listings, write the nominations bundle (`discover-nominations.json` in the save dir, TTL one hour) for host judgment, print a judging digest, and stop - no enrichment, no queue writes. A zero-nomination sweep prints the nothing-solid brief directly.
- `--judgments <path>` - leg 2: resume from the nominations bundle, applying the host judgments file (`{"bundle_id": "...", "judgments": [{"id", "name", "junk", "worthiness"}, ...]}`, bound to the bundle by `bundle_id`). Runs the per-topic research passes (deep tier by default; budget tunable via `LAST30DAYS_ENRICH_BUDGET_SECONDS` below), writes the pending report (`discover-pending.json`), and prints per-topic angle inputs. Requires `--discover`.
- `--finalize` - leg 3: apply optional host angles to the pending report, render the final discovery brief, save artifacts, and record the topic queue (retries are idempotent - the pending file stays in place within its TTL). Offline; requires `--discover`.
- `--angles <path>` - optional host angles file for `--discover --finalize` (`{"bundle_id": "...", "angles": [{"id", "podcast", "x_article"}, ...]}`, sentences capped at 200 chars); omitting it ships the brief without angle lines. All three protocol legs must share one `--save-dir` (handoff files live there, else in `~/.config/last30days/`); contract failures (missing/stale/unbound handoff files) exit 2 with the remedy on stderr, and `--mock` protocol legs require `--save-dir` to stay side-effect-free.
- `--drill <target>` - deep follow-up over the fresh `~/.config/last30days/last-report.json` cache. Accepts a 1-based index (`--drill "cluster 3"` or `--drill "3"`) or a fuzzy cluster title/entity description. It re-fetches only sources that contributed to the matched cluster, enables their deep comment/transcript enrichment paths, merges/dedupes the evidence, and replaces the cache so drills can chain. Run it without a positional topic; if the cache is absent or expired, run a normal research pass first.
- `--verify-freshness` - opt into an act-time verification pass for conservatively extracted, source-grounded claims (Polymarket odds/end dates, GitHub stars, StockTwits sentiment ratios, and explicit status assertions). With a topic, verification runs after research; without a topic, it re-verifies the fresh `last-report.json` cache without repeating research. Verdicts are `current`, `stale`, `contradicted`, or `unsupported` and include evidence timestamps. Set `LAST30DAYS_VERIFY_FRESHNESS=on` in `.env` to make the pass default for normal research runs.
- `--save-suffix <name>` - distinguish runs of the same topic (e.g. per client: `--save-suffix=acme`).
- `--no-browser-cookies` - hard-disable browser-cookie extraction for this run, even when `FROM_BROWSER` is configured. MCP and folder-mode hosts use this for safe defaults.
- `--publish-html` - with `--emit=html`, publish the rendered HTML to `ht-ml.app` after local output/save-dir writes. This is explicit opt-in only; pages are public by default.
- `library feed` - scan `LAST30DAYS_MEMORY_DIR` plus `~/.local/share/last30days/briefs/`, then write a self-contained `index.html`, valid Atom `feed.xml`, and browser-ready pages under `briefs/`. The index is reverse-chronological and grouped by topic. For direct engine use: `python3 skills/last30days/scripts/last30days.py library feed`; use `--save-dir <path>` to scan and write another library directory.
- `library feed --publish` - publish each rendered brief and the HTML index through `ht-ml.app`. The generated `feed.xml` remains a first-class local artifact because this HTML host does not serve Atom with an XML content type. Host the output directory on any static host (for example, GitHub Pages) to make `feed.xml` subscribable. Publishing is explicit opt-in and pages are public by default; public pages may be crawled or indexed.
- `library search "<query>"` - incrementally sync `LAST30DAYS_MEMORY_DIR` and `~/.local/share/last30days/briefs/` through the shared library scanner, then run offline SQLite FTS5 across those briefs plus dated per-run sightings in `~/.local/share/last30days/research.db`. Results are grouped by topic run. The sibling search index lives at `~/.local/share/last30days/library.db`; hand edits, renames, and deletes are picked up on sync, and a corrupt index is rebuilt automatically.
- `LAST30DAYS_LIBRARY_OWNER=<name>` - optional feed-level Atom author. Defaults to `last30days research library`.
- `LAST30DAYS_LIBRARY_CONTEXT=on|off` - controls passive prior-run context on fresh research reports. It defaults to `on`; matching saved research appears in a short `From your library` section. Set `off` to skip the local index read and leave reports unchanged. Mock runs, eval replays, and internal fan-out subruns do not load library context, keeping fixtures deterministic.
- `--publish-password <password>` - optional shared password for `--publish-html` or `library feed --publish`. Prefer `LAST30DAYS_PUBLISH_PASSWORD=<password>` instead so the password is not visible in the process list or shell history. Use a unique non-personal password; never reuse the user's own password. The provider's update key is treated as secret and is not written to stdout, HTML, raw output, or `.publish.json` metadata.
- `--preflight` - optional permission inspector. It reports config source, project config trust/ignore state, browser-cookie plan, planned writes, optional commands, source availability, and endpoint overrides without reading browser cookies, writing setup/config/report files, or running research. First-run setup does not require it. Add `--emit=json` for the separate machine-readable preflight contract (`--json-profile` does not change it); use `--diagnose` when you need the full source diagnostic JSON. `doctor` is the health surface.
- `--welcome` - print the first-run welcome text (engine-owned; the skill relays it verbatim on first run). Safe: prints and exits, no reads or writes.
- `--record-fixtures <dir>` - developer-only, hidden flag that records scrubbed source responses for the offline research-quality eval harness. It writes `<dir>/http.json`; see the [eval reference](docs/reference/eval.md) before recording or committing fixtures.
- `setup --github-start` / `setup --github-poll` - the two-command ScrapeCreators GitHub device-auth split. `--github-start` submits the device flow, copies the code to the clipboard, opens the browser, and returns the code immediately (foreground); `--github-poll` waits for you to authorize and persists the key. `setup --github` still runs both in one shot for back-compat.

The footer line `📎 Raw results saved to <resolved memory directory>/<slug>-raw.md` is the canonical pointer; if it shows backslashes on Windows update past v3.1.1. An explicitly disabled save produces no saved-file pointer.

Every completed research pass writes a structured `last-report.json` cache beside `last-run.json`. HTML follow-up renders use it so `--emit=html --synthesis-file` can reuse report metadata/footer without fetching sources again; `--drill <target>` uses it as the grounded starting point for targeted re-research; bare `--verify-freshness` updates only the cached report's claim verdicts. Reuse is intentionally short-lived: `LAST30DAYS_REPORT_CACHE_TTL_SECONDS` defaults to `3600` (one hour). Set it to another integer number of seconds to tune the window, or `0` to disable report-cache reuse and post-run follow-ups.

---

## First-run onboarding

When no credential source shows completed setup, the skill runs consent-driven onboarding that the model drives in chat. A missing global `SETUP_COMPLETE=true` marker first loads the setup reference to check process environment, project config, and other supported credential sources; it does not by itself start onboarding. The flow takes one of three forms depending on the host:

- **Claude Code Modal Flow** - the restored v3.0.0 guided NUX, used on hosts with `AskUserQuestion` (Claude Code). A welcome message, then modals for Auto/Manual/Skip setup, cookie consent, the ScrapeCreators signup offer, a TikTok/Instagram `INCLUDE_SOURCES` opt-in, and a first-topic picker.
- **Non-Modal Prose Flow** - the same work done conversationally on hosts without modals (OpenClaw, Codex, Cursor, Gemini CLI, Grok, raw CLI).
- **Grok Bot Prose Flow** - the prose flow on a Grok Bot host (`LAST30DAYS_HOST=grok-bot`, persisted to `.env` by this setup). It has no browser-session step: X runs through the bot's built-in X tools with nothing to set up, then the X for Grok Bot connector, with `X_BEARER_TOKEN` or `XAI_API_KEY` as backups (see [Grok Bot](#grok-bot) under Per-client patterns).

The Modal and Non-Modal flows share the same consent points:

1. **Browser cookies** - the model asks before reading anything and explains that permission covers later research runs. On yes it runs `setup --allow-browser-cookies`. On macOS, the scan checks Chrome and other Chromium browsers first, then Firefox and Safari, unless you already configured `FROM_BROWSER`. On Windows and Linux, this native cookie scan supports Firefox only; under WSL2, it also falls back to Windows Firefox. Unless you already configured `FROM_BROWSER`, setup saves the browser names that supplied complete cookies in `FROM_BROWSER` so later research can read those signed-in sessions again; it never saves the cookie values. If different services use different browsers, it saves their comma-separated names. Chromium reads on macOS may prompt for Keychain access again on later runs; choose **Always Allow** if you want to grant continuing OS access. Setup also installs yt-dlp + the keyless Digg CLI. On no it runs setup without `--allow-browser-cookies` (or with `FROM_BROWSER=off`), which skips all cookie reads and still installs the tools. The engine saves the decision as `BROWSER_CONSENT=true` or `false`; refusal blocks native browser and CDP reads on later runs, even if an older `FROM_BROWSER` selection remains. To disable these reads later, set `BROWSER_CONSENT=false` or `FROM_BROWSER=off` in `~/.config/last30days/.env`. A later consented setup can replace the refusal. An explicit `FROM_BROWSER=off` remains a separate denial until you change it. The `agentcookie` sidecar has its own `AGENTCOOKIE=off` control.
2. **Full Disk Access (macOS)** - if a cookie read is permission-denied, the model surfaces the System Settings > Privacy & Security > Full Disk Access fix and offers one retry.
3. **ScrapeCreators GitHub signup** - offered on every first run (10,000 free calls). On consent it runs `setup --github`, which opens a browser for GitHub device-auth (or registers instantly via the `gh` CLI when installed) and, on success, **persists `SCRAPECREATORS_API_KEY` automatically** (0o600, masked in output) so TikTok, Instagram, empty-path Reddit search backup, and the YouTube transcript fallback activate on the next run. Decline anytime; you can run it later by asking to set up ScrapeCreators. The Step 5 opt-in has two tiers, both comment-enabled: **Recommended** (TikTok + Instagram posts AND top comments, plus YouTube comments — `INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments`) and **Everything**, which also adds Threads + Pinterest. Comments are on by default; Threads and Pinterest are the only opt-in extras.

To revisit setup, ask the skill to set up a source; deleting the global `.env` alone does not force onboarding when another credential source remains active. The mechanical work lives in `scripts/lib/setup_wizard.py`; the consent conversation and the host flows are specified in Step 0 of `skills/last30days/references/setup-wizard.md`, which the root `SKILL.md` routes to when needed. The original v3.0.0 wizard is captured at `docs/reference/old-nux-wizard-v3.0.0.md`.

---

## MCP host controls

These variables configure the local Go MCP server and are read from its process environment, not from the engine's `.env` files:

| Env var | Default | Accepted values | Behavior and precedence |
|---|---|---|---|
| `LAST30DAYS_PYTHON` | unset | An executable name or path | Selects the Python 3.12+ interpreter used by the MCP server. A caller-supplied `RunOptions.PythonPath` remains the test/caller override; otherwise this variable must resolve to an executable. When it is unset, the server looks up `python3` on `PATH`. An empty or unresolvable value is an error rather than a fallback. |
| `LAST30DAYS_MCP_ALLOW_BROWSER_COOKIES` | unset (deny) | `1`, `true`, `yes`, or `on`, case-insensitive | A recognized truthy value removes the MCP layer's default `--no-browser-cookies` flag. Every other value keeps that denial. This switch grants no consent by itself: browser-cookie access still requires the engine's separately recorded consent and `FROM_BROWSER` configuration. |
| `LAST30DAYS_MCP_TIMEOUT` | `600` seconds (10 minutes) | A positive Go duration such as `10m`, `90s`, or `1m30s`, or a positive integer number of seconds such as `600` | Caps each research subprocess. The default accommodates deep discovery enrichment. Invalid, zero, and negative values use the default. An explicit caller timeout takes precedence. Source and enrichment deadlines can finish earlier; increasing this cap also extends how long a hung subprocess can occupy an MCP slot. |

## API keys (`.env`)

The skill reads keys from a `.env` file. Two locations are supported:

1. **`~/.config/last30days/.env`** at the user level (global default) - loaded by default.
2. **`.claude/last30days.env`** in the current project directory (project-scoped) - loaded only when trusted by setting `LAST30DAYS_TRUST_PROJECT_CONFIG=1` in the process environment or global config.

Override the global location with `LAST30DAYS_CONFIG_DIR=/path` (or `LAST30DAYS_CONFIG_DIR=""` for no-config mode). File permissions should be `600` on POSIX hosts - the engine warns on every run if they aren't.

**File syntax.** One `KEY=value` per line; whitespace around the key and value is trimmed. A leading `export ` is accepted and ignored (`export RATE=1` sets `RATE`). A line starting with `#` is a comment, and a `#` preceded by whitespace after an unquoted value starts a trailing comment (`RATE=1  # req/sec` stores `1`). A `#` glued to the value is literal (`TOKEN=abc#123` stores `abc#123`). Wrap a value in matching `"` or `'` to keep spaces or a `#` verbatim (`NAME="Jane # Doe"` stores `Jane # Doe`); no backslash escapes or `$VAR` expansion are processed. An empty value is ignored (except for `LAST30DAYS_YT_PLAYER_CLIENT=` and `LAST30DAYS_MEMORY_DIR=`, where empty is a deliberate disable), so a secret can never be set to `""`.

The project-scoped file is useful for **intentional per-client setups**: drop a `.claude/last30days.env` into each client folder (`SCRAPECREATORS_API_KEY`, `INCLUDE_SOURCES`, `LAST30DAYS_MEMORY_DIR`, `BSKY_HANDLE`, etc), then opt in with `LAST30DAYS_TRUST_PROJECT_CONFIG=1` from your shell or `~/.config/last30days/.env`. Folder-mode hosts such as Codex desktop do not trust hidden project config by default, and discovery stops at the git root so unrelated parent folders cannot silently influence runs. An untrusted repo's `.claude/last30days.env` is not read.

**`LAST30DAYS_API_KEY`** + **`LAST30DAYS_API_BASE`** - optional remote-API backend. Set BOTH to route research through a remote API endpoint instead of running the local sources: `LAST30DAYS_API_BASE` is the endpoint (there is no built-in default), and `LAST30DAYS_API_KEY` is the bearer key for it. When both are set (and `--mock` is not passed), the engine submits the topic to that endpoint, polls with progress on stderr, and prints the server's report; none of the per-source keys below are used for that run. A configured local corpus is the privacy exception: the engine bypasses the hosted backend and runs locally rather than forwarding file-derived input. The `--perplexity-search-type` flag also runs locally when a direct `PERPLEXITY_API_KEY` exists, because the hosted backend cannot apply it. Non-default `--register` selections are forwarded with the request so server-side synthesis uses the same audience preset. Leave either unset to run local sources exactly as normal. Unlike the other keys here, these two are read only from the **process environment** (export them in your shell or host config) - they are deliberately not loaded from the `.env` files above, so a project-scoped `.env` can never silently redirect research to a remote endpoint. The remote endpoint does not return the local `Report` needed for the versioned agent JSON profile; use `--emit=json --json-profile=raw` for its existing server-response JSON contract.

**`BRIGHTDATA_API_KEY`** - optional, for the `amazon` source. The Bright Data CLI normally owns its own auth via `brightdata login`, so this is only needed if you would rather keep an explicit key in `.env` or the keychain. It is resolved through the standard config layering and passed to the CLI through the child process environment, never on the command line (where it would be readable from `/proc/<pid>/cmdline` by other local users on a shared host).

**`LAST30DAYS_AMAZON_DOMAIN`** - optional, default `https://www.amazon.com`. The marketplace the `amazon` source searches; set it to `https://www.amazon.co.uk`, `https://www.amazon.de`, and so on. Product URLs are validated against this host, so records from other marketplaces are rejected.

**`LAST30DAYS_META_ADS_COUNTRY`** - optional, default `US`. The Ad Library country the `meta_ads` source queries, as a two-letter code. The endpoint accepts exactly one country per call, so this selects it rather than widening it. There is deliberately no durable env form of the advertiser-page override: a page id belongs to one topic, and env values are inherited by every competitor sub-run in a comparison, which would render one brand's ads as every peer's. Use `--meta-ads-page` per run instead.

### Local corpus (your files)

Register persistent directories with `LAST30DAYS_CORPUS_DIRS`. Separate paths with `:` on macOS/Linux (the platform path separator is `;` on Windows):

```bash
# ~/.config/last30days/.env
LAST30DAYS_CORPUS_DIRS=~/notes:~/meeting-transcripts
# LAST30DAYS_CORPUS_IN_EXPORT=1  # explicit agent-JSON opt-in; off by default
```

The slash-command experience remains primary: ask `/last30days` to include your registered notes. For direct engine scripting or development, the equivalent one-off invocation is:

```bash
python3 skills/last30days/scripts/last30days.py "MCP servers" \
  --corpus ~/notes --corpus ~/meeting-transcripts
```

**Privacy:** corpus files are read locally, never sent through a source HTTP client, never forwarded to `LAST30DAYS_API_BASE`, never included in remote reranker/fun-scoring prompts, and do not consume network-source concurrency or retry budget. Matches appear in a badged **From your files** section. Corpus candidates are removed from `--publish-html`, `library feed --publish`, and the versioned agent JSON export by default, including corpus-derived cluster titles and source outcomes. Set `LAST30DAYS_CORPUS_IN_EXPORT=1` only when you intentionally want corpus results in the agent JSON written to local stdout/files. The unversioned `--json-profile=raw` debug dump remains a full local report and can contain corpus text; do not redirect it to an external system unless that is intentional. Extracted text is cached by file mtime in `~/.config/last30days/corpus-cache.json` with mode `0600`; a corpus-bearing `last-report.json` cache is also tightened to `0600`. Delete either cache at any time to clear it.

**Source-by-source** - what each key unlocks:

| Source | Key(s) | Required for | Free tier |
|---|---|---|---|
| Local corpus | `--corpus <dir>` or `LAST30DAYS_CORPUS_DIRS` | private `.md`/`.txt`; `.pdf` when `pdftotext` is on PATH | yes (offline) |
| Reddit (public) | none (default free keyless path). With `SCRAPECREATORS_API_KEY`: search backfill when the free path returns fewer than 5 items by default (`LAST30DAYS_REDDIT_SC_MIN_ITEMS=<N>` changes the floor, `LAST30DAYS_REDDIT_SC_MIN_ITEMS=0` backfills only when it returns nothing); `LAST30DAYS_REDDIT_BACKEND=scrapecreators` pins SC primary with free fallback. `LAST30DAYS_REDDIT_KEYLESS_RATE` paces unauthenticated reddit.com requests (default `1` req/sec) | always on; SC knobs require `SCRAPECREATORS_API_KEY` | yes |
| Hacker News | none | always on | yes |
| Polymarket | none | always on | yes |
| StockTwits | none | auto-on for ticker/crypto topics only (gated by symbol detection); never registered for non-financial topics | yes (public API, ~200 req/hr per IP) |
| DripStack | none | opt-in only: per run with `--search dripstack`, or persistently with `INCLUDE_SOURCES=dripstack` in `.env`. Searches premium financial newsletters and analyst writeups via a free, public search API — no key needed. Never active without the opt-in. | yes when opted in (public API, no auth) |
| GitHub | `gh` CLI installed (uses your GitHub auth) | always on if `gh` present | yes |
| YouTube | `yt-dlp` CLI installed; `SCRAPECREATORS_API_KEY` adds a server-side transcript fallback used only when yt-dlp fails (429 / bot-gate) | always on if `yt-dlp` present; SC transcript fallback default-on when key set (no credit spent unless yt-dlp fails) | yes |
| YouTube comments | `yt-dlp` CLI installed — **free and keyless, no API key and no opt-in needed**. Falls back to `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` containing `youtube_comments` only when yt-dlp is absent. Suppress with `EXCLUDE_SOURCES=youtube_comments`. | top comments (by likes) on the top ~3 videos by engagement | yes — free via yt-dlp (no credits spent) |
| TikTok comments | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok_comments` (**on by default** — Step 5 Recommended tier) | top comments (by `digg_count`) on the top ~3 TikTok posts | ~3 calls/run; 10K free calls |
| Instagram comments | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram_comments` (**on by default** — Step 5 Recommended tier) | top comments (by `comment_like_count`) on the top ~3 Instagram posts, via `/v2/instagram/post/comments` | ~3 calls/run; 10K free calls |
| Digg | `digg-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only`; binary defaults to `$HOME/.local/bin` — Hermes/OpenClaw agent subprocesses must inherit that dir on PATH for Digg to activate; prior pp-digg installs use the same path) | always on if `digg-pp-cli` on PATH | yes (free, keyless, read-only) |
| arXiv | `arxiv-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install arxiv --cli-only`) | always on if `arxiv-pp-cli` on PATH; fires on research/technical topics and stays quiet otherwise (relevance + 365-day recency gating) | yes (free, keyless) |
| Techmeme | `techmeme-pp-cli` on PATH (auto-installed via `... install techmeme --cli-only`) | always on if `techmeme-pp-cli` on PATH; searches Techmeme's live archive and keeps only headlines dated within the research window (undated headlines flow through as low-confidence) | yes (free, keyless) |
| Trustpilot | `trustpilot-pp-cli` on PATH (NOT auto-installed; install on demand via `npx -y @mvanhorn/printing-press-library@0.1.16 install trustpilot --cli-only`) + (`INCLUDE_SOURCES` contains `trustpilot` **or** an explicit `--trustpilot-domain` / plan-level `trustpilot_domain`) | **opt-in, off by default**; `--trustpilot-domain=<domain>` (and per-entity `trustpilot_domain` in `--competitors-plan`) auto-activates the source for that run and bypasses the brand-shape gate. Persist with `INCLUDE_SOURCES=trustpilot` to skip per-run auto-enable. `EXCLUDE_SOURCES=trustpilot` still wins. Bare company names auto-resolve to the review-page domain via the CLI's search only when the source is already active. The session warms once before the search fan-out; a stale session does a ~10s headless-Chrome WAF-cookie harvest (set `LAST30DAYS_TRUSTPILOT_NO_BROWSER=1` to disable in cron/CI) | yes (no API key; cookie-replay after the one-time harvest) |
| Amazon | `brightdata` CLI on PATH **and logged in** (NOT auto-installed: `npm i -g @brightdata/cli` then `brightdata login`) + (`INCLUDE_SOURCES` contains `amazon` **or** `--search` includes `amazon`) | product records with live rating, rating count, and price, plus a capped sample of recent written reviews woven as buyer voice; the emoji footer shows each product's all-time-vs-last-30-days drift | **opt-in, off by default**. Free tier is 5,000 requests/month (~$7.50 equivalent); a typical run spends 4 (1 product search + up to 3 review pulls) regardless of how many reviews come back, since billing is per request. Past the free tier it bills the account balance at $1.50 per 1,000 records (~$0.32 for a default run). `--amazon-query=<keyword>` sets the product keyword when it differs from the topic; `LAST30DAYS_AMAZON_DOMAIN` selects a non-US marketplace. `EXCLUDE_SOURCES=amazon` wins. Never auto-fires: the model requests it per run or the user enables it durably |
| X / Twitter | one of: `X_BEARER_TOKEN` (official X API v2; opt-in outside Grok Bot: `LAST30DAYS_X_BACKEND=xapi`; covers recent posts, about the last week, unless your X developer project has full-archive access), a signed-in `grok` CLI (opt-in: `LAST30DAYS_X_BACKEND=grok`), `AUTH_TOKEN` + `CT0` (browser cookies, Bird CLI), `XAI_API_KEY`, `XQUIK_API_KEY`, or `FROM_BROWSER` (cookie-jar auth). On a Grok Bot host the bot's built-in X tools (then the X connector) serve X first; see [Grok Bot](#grok-bot) | X items in results | X API bearer = your X developer project's credits; grok = Grok plan, opt-in only; cookie-jar / Bird = free; Xquik / xAI = key-based |
| TikTok | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok` | TikTok items | 10K free calls |
| Instagram | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram` | Instagram Reels | 10K free calls; raise `LAST30DAYS_TRANSCRIPT_TIMEOUT` (default 30s) if SC is slow on your network |
| Threads | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `threads` | Threads items | 10K free calls |
| Pinterest | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `pinterest` | Pinterest items | 10K free calls |
| LinkedIn | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `linkedin` | LinkedIn posts + articles (articles rank as high signal on person topics) | 10K free calls; power-user opt-in, not offered during first-run onboarding |
| Meta Ads | `SCRAPECREATORS_API_KEY` + (`INCLUDE_SOURCES` contains `meta_ads` **or** `--search` includes `meta_ads`) | **opt-in, off by default**; a brand's live Meta Ad Library creatives that *launched* inside the window, with ad copy, launch date, placements, CTA, landing product, any promo code, and spoken transcripts for the newest video ads. The 📣 footer names the advertiser page that was resolved, plus how much the brand is still running from before. Paid message only, never audience reaction: Meta publishes reach and spend for political ads alone, so commercial creatives carry no engagement. `--meta-ads-page=<page_id>` skips name-based resolution when it picks the wrong company or the brand advertises under product-line page names; `LAST30DAYS_META_ADS_COUNTRY` selects a non-US Ad Library (one country per call). `EXCLUDE_SOURCES=meta_ads` wins. Never auto-fires and never inferred from topic shape: on a non-brand topic, resolution returns unrelated advertisers | 10K free calls; a default run spends at most 7 (1 resolve, plus 1 more only when the first search finds no name match, + up to 2 creative pages + up to 3 transcripts), billed per request. Power-user opt-in, not offered during first-run onboarding |
| Telegram | `SCRAPECREATORS_API_KEY` + (`--telegram-sources=<handles>` **or** `TELEGRAM_SOURCES=<handles>` + `INCLUDE_SOURCES` contains `telegram`) | **opt-in, off by default**; public channel posts only (no keyword discovery). `--telegram-sources=aipost,durov` (or `TELEGRAM_SOURCES` env) auto-activates for that run like `--trustpilot-domain`. Accepts bare handle, `@handle`, `t.me/URL`, or `t.me/s/URL`; rejects joinchat links and numeric -100 IDs. `INCLUDE_SOURCES=telegram` or `--search telegram` without a channel list does not fetch. `EXCLUDE_SOURCES=telegram` wins. `TELEGRAM_MAX_PAGES` overrides page cap (quick=1, default=3, deep=6). Never on Recommended onboarding tier. | 1 credit per live posts page; 10K free calls |
| Xiaohongshu (RED) | logged-in x-mcp browser plugin or `xiaohongshu-mcp` service; optional `XIAOHONGSHU_API_BASE` for custom URLs | requested-only via `--search xhs` or `--search xiaohongshu`; auto-probes `http://localhost:18060` then `http://host.docker.internal:18060` | no last30days API key; depends on your local browser-session service |
| Bluesky | `BSKY_HANDLE` + `BSKY_APP_PASSWORD` | Bluesky items | yes (app password at bsky.app) |
| TruthSocial | `TRUTHSOCIAL_TOKEN` | TruthSocial items | yes |
| Web search | one of: `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY` | `--auto-resolve` and Step 2 supplements | Brave has a free tier; native WebSearch on Claude Code / Codex / Gemini works as a fallback |
| Perplexity Agent API / Search API / Deep Research | `PERPLEXITY_API_KEY` (preferred) or `OPENROUTER_API_KEY` (Sonar fallback) | `INCLUDE_SOURCES=perplexity`; `--deep-research` uses background Agent API with a direct key or synchronous Sonar through OpenRouter | no |
| Caption-free transcription | `GROQ_API_KEY` (free tier, preferred) or `OPENAI_API_KEY` (paid backstop); requires `ffmpeg` | Whisper transcription for audio/video without captions (groundwork: module shipped, not yet auto-invoked by the engine) | Groq free tier is generous; needs ffmpeg installed |
| Jobs / careers pages | none for public ATS pages; web backend improves fallback discovery | `--hiring-signals` and strong Hiring Signals in standard company reports | yes |
| Apify (alternate scraper) | `APIFY_API_TOKEN` | fallback for Reddit/TikTok/Instagram when ScrapeCreators is exhausted | yes (limited) |

**Reddit keyless pacing.** Unauthenticated reddit.com requests (site search, listing partials, shreddit) share one token bucket. The default is `1` request per second with a burst of 2, slow enough that engine fan-out does not trip HTTP 429 on a typical home IP. Set `LAST30DAYS_REDDIT_KEYLESS_RATE` to a float req/sec to trade wall-clock for coverage: higher finishes faster and loses more sub-requests to 429; lower is safer and slower. Invalid or non-positive values fall back to `1`. A 429'd search or listing sub-request is retried once after a short jittered pause, still through the limiter. Identical reddit.com requests within one command (subreddit listings, listing feeds, comment pages, which repeat across subqueries) are fetched once and memoized, so a typical four-subquery run issues roughly a quarter of the requests it used to. Comment enrichment covers 4 / 8 / 12 threads per subquery at quick / default / deep depth. This does not change ScrapeCreators routing (`LAST30DAYS_REDDIT_BACKEND` / `LAST30DAYS_REDDIT_SC_MIN_ITEMS`).

**YouTube transcript tuning.** `LAST30DAYS_YT_SUB_LANGS` controls the comma-separated caption-language priority passed to yt-dlp and defaults to `en,es,pt`. The ScrapeCreators transcript fallback tries at most the first three distinct languages in that order, with a shared 30-second deadline per video; it stops after the first usable transcript. `LAST30DAYS_YT_PLAYER_CLIENT` defaults to `android` so yt-dlp can pass YouTube's web bot-gate without cookies (search, transcripts, and comments); set it empty to disable. When `SCRAPECREATORS_API_KEY` is available, yt-dlp uses one fast attempt before the paid fallback; set `LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT` to the number of seconds allowed for that attempt when a throttled host needs longer than the 12-second default. A VTT completed before the timeout is reused rather than discarded. `LAST30DAYS_YT_SEARCH_TIMEOUT` sets the per-search yt-dlp deadline (default 120s). Comparison-mode fan-out also caps concurrent yt-dlp processes process-wide and caches identical searches within a run so redundant `ytsearch` calls do not self-throttle the same IP.

**X backend priority (bird first).** The default X backend chain is bird (browser cookies) → xai (API key) → xurl (OAuth2 CLI) → xquik (API key). Cookies beat `XAI_API_KEY` when both are present. A leftover grok login never steals the X lane; see below. `xapi` (the official X API v2 with `X_BEARER_TOKEN`) is opt-in on these hosts (`LAST30DAYS_X_BACKEND=xapi`), so an ambient bearer never spends X API credits when the free path comes back empty. **Grok Bot exception.** On a Grok Bot host (`LAST30DAYS_HOST=grok-bot`) the unpinned chain is the official chain instead: xapi (`X_BEARER_TOKEN`) → xai (`XAI_API_KEY`) → xurl (the X API through X's CLI). When the bot fetched X itself (its built-in X tools, or the X connector), those results come first and the chain is not called at all; see [Grok Bot](#grok-bot) under Per-client patterns.

**xAI backend errors.** For an `xai:` authentication or model failure, check `XAI_API_KEY` and its chat/model permissions in console.x.ai. Set `LAST30DAYS_X_MODEL` to a model available to that key if the configured model is unavailable. For HTTP 402, check xAI billing and credits; for HTTP 429, wait for the xAI rate limit to reset; for a timeout, retry later. Logging into x.com or replacing `X_BEARER_TOKEN` does not repair the xAI API backend.

**Grok CLI (opt-in backup).** Install the Grok CLI (`curl -fsSL https://x.ai/cli/install.sh | bash`) and run `grok login`, and X can work with no X account, no browser cookies, and no `XAI_API_KEY`. However, grok is **opt-in only**: a leftover `~/.grok/auth.json` must never steal the X lane. Pin `LAST30DAYS_X_BACKEND=grok` to enable it. It is not "free" in the way the cookie path is: calls draw on your Grok plan, and depth costs several calls per run because the underlying tool caps each search at 10 posts. Results are validated before use — every returned post's ID is decoded to confirm it falls inside the requested date range, because the retrieval is performed by a language model and can otherwise return confident, well-formed posts that were never searched for.

The Grok backend currently supports only `grok 1.0.46 (2765805b9442)`, optionally followed by `[stable]`, as reported by `grok --version`. Research runs with a generated agent profile that removes local tools, skill discovery, and inherited MCP access while retaining hosted X search. Other builds fail before credentials are staged or research starts; each build needs a tool-boundary audit. If your CLI build is unsupported, remove the `LAST30DAYS_X_BACKEND=grok` pin to use the normal X backend chain, or select another configured X backend. Updating Grok alone does not establish compatibility.

**X on cookie-less hosts.** Bird (the free X source) scrapes X using your logged-in browser cookies (`AUTH_TOKEN`/`CT0`), which agent hosts like OpenClaw, CI, or headless runs often can't supply — and scraping carries some account risk. On those, set `XQUIK_API_KEY` (or `XAI_API_KEY`) for full, ranked X coverage from a single API key: the same engagement-based ranking, first-party authorship, and handle (from/mentions) lanes the native X source gets. The official X API is the other keyed option: set `X_BEARER_TOKEN` and pin `LAST30DAYS_X_BACKEND=xapi`; it serves the same lanes but covers recent posts, about the last week, unless your X developer project has full-archive access. `--diagnose` reports whether the key is working (and flags an unpaid key as `payment-required`).

**Extra bird cookie lookups on Linux and Mac mini.** On a MacBook the X cookie path is unchanged (Firefox/Safari/Chrome extract, gated by `FROM_BROWSER`). On **extra hosts** the engine adds two more ways to hand bird a complete `auth_token`+`ct0` pair, tried in order (first COMPLETE pair wins; no half-pair merge; nothing is ever written to the `.env` and cookie values are never printed):

1. an explicit env `AUTH_TOKEN`+`CT0` (never overwritten);
2. the [`agentcookie`](https://github.com/) sidecar CLI — `agentcookie cookies --domain .x.com --json` — a soft dependency (absent = skipped; `AGENTCOOKIE=off` disables it) that delivers cookies on Linux, where the on-disk Chrome store can't be decrypted here;
3. a live signed-in Chrome/Chromium session over the DevTools Protocol (`Network.getAllCookies`), after explicit browser-cookie consent;
4. the mainline browser extract, when `FROM_BROWSER` already lists a browser (on a Mac mini with a browser opted in, this native read runs *before* the CDP read).

A host counts as an "extra host" when ANY of these hold: `AGENTCOOKIE=on` (explicit opt-in, any OS); the platform is Linux; a Darwin **Mac mini** (`sysctl -n hw.model` prefix `Macmini`); or a Darwin **agentcookie sink** role. The host is never inferred from the home directory, PATH, or Hermes/OpenClaw env — only those signals. A plain MacBook does no agentcookie spawn and opens no CDP socket unless `AGENTCOOKIE=on`.

CDP endpoint resolution (extra hosts only, no port scan): `BROWSER_CDP_URL` if set, else port `18800` when it answers as Chrome, else `9222` + the X display number. Port `18800` is the last30days extras **NUX convention** — the agent launches a throwaway login Chrome with `SAND_CHROME_REMOTE_DEBUG_PORT=18800` (see SKILL.md's "X on Linux / Mac mini"), so it is not confused with a daily Chrome profile on `9222`+display (box-chrome's own built-in default). `18800` is tried first but falls through when it yields no complete pair, so a logged-out Chrome there never shadows a logged-in profile; pin `BROWSER_CDP_URL` if a stale session answers there. A Node `--inspect` endpoint is rejected; a Chrome page target is required.

CDP reads require `BROWSER_CONSENT=true`, or an explicit Chromium browser / `auto` selection in `FROM_BROWSER` when no consent marker exists. A running debugging browser or `BROWSER_CDP_URL` alone grants no access. `BROWSER_CONSENT=false` and `FROM_BROWSER=off` each block CDP before endpoint discovery. The `agentcookie` sidecar remains separately controlled by `AGENTCOOKIE`.

**Example `.env` skeleton** (placeholders only - replace with your own values):

```bash
# Reasoning + planning (one provider; see priority below)
GOOGLE_API_KEY=<your-gemini-key>

# Web search backend (one is enough; Brave is the cheapest)
BRAVE_API_KEY=<your-brave-key>

# Optional sources
SCRAPECREATORS_API_KEY=<your-scrapecreators-key>
INCLUDE_SOURCES=tiktok,instagram
# LAST30DAYS_REDDIT_KEYLESS_RATE=1  # keyless reddit.com req/sec; lower = fewer 429s, slower runs
# Xiaohongshu is requested-only: run with --search xhs after starting a local
# browser-session service. Defaults probe localhost, then host.docker.internal.
# XIAOHONGSHU_API_BASE=http://localhost:18060
# Add perplexity to INCLUDE_SOURCES when you want the paid Perplexity source.
# PERPLEXITY_API_KEY=<your-perplexity-key>
# INCLUDE_SOURCES=tiktok,instagram,perplexity
# LAST30DAYS_PERPLEXITY_MODE=agent  # agent | search | both; sonar is a legacy alias
# LAST30DAYS_PERPLEXITY_AGENT_MODEL=perplexity/sonar
# LAST30DAYS_PERPLEXITY_AGENT_MAX_STEPS=5
# LAST30DAYS_PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS=4096  # required for anthropic/*

# X authentication (one option only)
AUTH_TOKEN=<your-auth-token>
CT0=<your-ct0-token>
# OR the official X API v2 bearer. Default on Grok Bot; elsewhere also pin
# LAST30DAYS_X_BACKEND=xapi. Covers recent posts, about the last week, unless
# your X developer project has full-archive access.
# X_BEARER_TOKEN=<your-x-api-bearer-token>
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

Perplexity is a paid opt-in source. A direct `PERPLEXITY_API_KEY` enables the Agent API, Search API, and background Deep Research. Existing `OPENROUTER_API_KEY` installs remain compatible through synchronous Sonar: `perplexity/sonar-pro` for normal synthesis and `perplexity/sonar-deep-research` for `--deep-research`. Search API and Agent API features still require the direct key.

`LAST30DAYS_PERPLEXITY_MODE` controls normal `perplexity` source runs:

| Value | Behavior | Calls |
|---|---|---|
| `agent` (default) | Direct key: controlled Agent API synthesis with required `web_search`. OpenRouter-only: synchronous Sonar fallback. | at most one paid synthesis call per last30days run |
| `sonar` | Direct key: deprecated alias for `agent`. OpenRouter-only: synchronous Sonar fallback. | at most one paid synthesis call per last30days run |
| `search` | Direct key: raw ranked Search API rows. OpenRouter-only: falls back to synchronous Sonar. | at most one paid call per last30days run |
| `both` | Direct key: Agent synthesis plus Search rows. OpenRouter-only: falls back to synchronous Sonar. | direct: at most two paid calls; OpenRouter: at most one |

With a direct key, normal `agent` mode uses the controlled `last30days-controlled-web-search/v1` profile: `perplexity/sonar`, a bounded `max_steps`, a local instruction, and only the configured `web_search` tool. It forces that tool for citation-critical grounding. It does not enable sandbox, file, finance, MCP, or function tools. OpenRouter fallback keeps the older OpenAI-compatible Sonar request and does not claim Agent API controls.

The engine routes every normal Perplexity mode through one whole-topic planner subquery per command, including competitor fanout, and does not repeat it during thin-source retries. A generic source-fetch override cannot raise this paid-call cap.

`LAST30DAYS_PERPLEXITY_AGENT_PRESET` is a separate explicit opt-in for a mutable Perplexity preset (`fast`, `low`, `medium`, or `high`). Presets can change their model, prompt, tools, cost, and output behavior. The engine still supplies its configured `web_search` tool so date, domain, location, result-count, and context constraints merge with the preset; other preset tools can remain enabled. Do not set this variable when you need the controlled profile. The engine never selects a preset automatically for normal runs.

`--deep-research` requires a normal positional topic and ignores `LAST30DAYS_PERPLEXITY_MODE`. With a direct key it starts at most one Agent API background run with the explicit dynamic `high` preset. With only OpenRouter it preserves the older synchronous `perplexity/sonar-deep-research` fallback. It cannot be combined with discovery, drill, cached-only, competitor, or vs-mode. This is a separate paid action. The engine caps it at one planner subquery and does not repeat it during thin-source retries. Direct background runs merge the configured `web_search` constraints with the preset, but the provider controls its other tools and can change them. A local timeout stops waiting but does not stop a direct remote run. Direct artifacts retain the served model, response ID, provider status, incomplete reason, poll count, timeout, and safe error metadata; OpenRouter artifacts retain the served model, response ID, usage, and citation count. Neither stores request headers or raw tool traces.

Perplexity-specific env vars:

| Env var | Default | Applies to | Notes |
|---|---|---|---|
| `LAST30DAYS_PERPLEXITY_MODE` | `agent` | normal Perplexity source runs | `agent`, `search`, or `both`; `sonar` remains a deprecated alias for `agent`. |
| `LAST30DAYS_PERPLEXITY_AGENT_MODEL` | `perplexity/sonar` | controlled Agent profile | Explicit Agent model for normal synthesis. |
| `LAST30DAYS_PERPLEXITY_AGENT_MAX_STEPS` | `5` | controlled Agent profile | Clamped to the last30days safety range 1..15. |
| `LAST30DAYS_PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS` | `4096` for `anthropic/*` models | controlled Agent profile | Required for explicit Anthropic models; clamped to the last30days safety range 1..32768. |
| `LAST30DAYS_PERPLEXITY_AGENT_TIMEOUT_SECONDS` | `120` | controlled Agent profile | Synchronous request timeout, clamped to 1..600 seconds. |
| `LAST30DAYS_PERPLEXITY_AGENT_PRESET` | unset | normal Agent runs | Explicit mutable preset only: `fast`, `low`, `medium`, or `high`. It replaces the controlled profile for that run. |
| `LAST30DAYS_PERPLEXITY_MAX_RESULTS` | `10` | Search API and all Agent `web_search` requests | Clamped to 1..20. |
| `LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE` | provider default | Search API and all Agent `web_search` requests | `low`, `medium`, or `high`; omitted unless set. |
| `LAST30DAYS_PERPLEXITY_DOMAIN_FILTER` | unset | Search API and all Agent `web_search` requests | Comma-separated domains, max 20. |
| `LAST30DAYS_PERPLEXITY_SEARCH_TYPE` | provider default | Search API and all Agent `web_search` requests | `fast` or `web`. Direct `PERPLEXITY_API_KEY` only; OpenRouter Sonar ignores it. Does not enable the source. `--perplexity-search-type` overrides it. |
| `LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER` | unset | Search API only | Comma-separated ISO 639-1 language codes. Agent API has no equivalent. |
| `LAST30DAYS_PERPLEXITY_COUNTRY` | unset | Search API and all Agent `web_search` requests | Two-letter country code such as `US`. |
| `LAST30DAYS_PERPLEXITY_RECENCY_FILTER` | unset | Search API and all Agent `web_search` requests | `hour`, `day`, `week`, `month`, or `year`; exact date filters take precedence. |
| `LAST30DAYS_PERPLEXITY_REASONING_EFFORT` | unset | controlled Agent profile | `minimal`, `low`, `medium`, or `high`. |
| `LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS` | `600` | direct Agent API background Deep Research | Wall-clock polling deadline; remote work can continue after a local timeout. OpenRouter fallback is synchronous. |
| `LAST30DAYS_PERPLEXITY_MODEL` / `LAST30DAYS_PERPLEXITY_SEARCH_MODE` | unset | legacy Sonar config | Retained for config-file compatibility. They do not select an Agent API preset or search mode. |

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
macOS Keychain > `pass`(1). A Keychain-only setup still counts as configured:
the engine resolves `last30days-<KEY>` at runtime. First-run Step 0 in
`skills/last30days/references/setup-wizard.md` must not treat a missing `.env` as unconfigured.

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

#### Disabling the Keychain source

Set `LAST30DAYS_SKIP_KEYCHAIN=1` to switch the Keychain source off entirely,
making the loader a no-op on macOS as well:

```bash
LAST30DAYS_SKIP_KEYCHAIN=1 uv run pytest tests/test_footer_nudge_suppression.py
```

Scope it to the tests that need a sealed Keychain rather than the whole suite:
the full run should keep exercising the positive-path Keychain tests.

This exists mainly for tests and reproductions that assert on
"no credentials configured" behaviour. Clearing `os.environ` and pointing
`LAST30DAYS_CONFIG_DIR` at nothing is not sufficient on a machine with items
stored under `last30days-<KEY>`: Keychain is a third, independent source, so a
stored key can quietly satisfy a lookup the test expected to fail — and the
test then fails on a contributor's Mac while passing in Linux CI, where the
loader already no-ops.

Unlike `LAST30DAYS_KEYCHAIN_ALIASES`, this switch is read from the process
environment only and never from a `.env` file. It gates a credential source
consulted *while* the config is being assembled, so a file-sourced value would
be read too late to take effect.

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

Accepts the same comma-separated names and aliases as `--search` (`web` → grounding, `hn` → hackernews, `bsky` → bluesky, `xhs` → xiaohongshu). Precedence: an explicit `--search` on the command line always wins; `LAST30DAYS_DEFAULT_SEARCH` applies only when the flag is omitted; when neither is set, per-query behavior is unchanged. `INCLUDE_SOURCES` / `EXCLUDE_SOURCES` keep their existing additive/subtractive roles on whichever set is selected.

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
4. **OpenRouter** - `OPENROUTER_API_KEY` (reasoning provider, auto-resolve, and synchronous Sonar fallback for the Perplexity source)
5. **Local / deterministic** - always available, lowest quality

When you invoke `/last30days` from Claude Code, Codex, or Gemini, the host model **is** the reasoning provider for plan + synthesis - you don't need any of the keys above unless you also run the script headlessly (cron, CI, watchlist).

### Agent-hosted runs must pass `--plan`

When the engine detects an agent host, a research run without `--plan` exits 2 before live diagnostics or retrieval and does not call the internal planner. It prints a `[Planner] LAW 7` message telling the host model to write the plan and pass it with `--plan` (Step 0.75 and Research Execution in `references/research-runbook.md`). The engine treats a run as agent-hosted when any of these variables is set in the process environment (a `.env` line never counts):

| Variable | Set by |
|---|---|
| `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` | Claude Code / Claude Agent SDK |
| `CODEX_THREAD_ID`, `CODEX_SESSION_ID`, `CODEX_SANDBOX` | Codex |
| `LAST30DAYS_HOST` | Hosts that self-identify (e.g. `grok-bot`) |
| `LAST30DAYS_HOST_AGENT=1` | Any other agent runtime, explicitly |

Runs that may skip the plan stay unaffected: `--mock`, `--hiring-signals`, `--auto-resolve` (the no-WebSearch path), comparison runs (`A vs B`, `--competitors*`), the hosted API path, and non-research commands (`doctor`, `setup`, `--diagnose`, `--preflight`, discovery legs, `--drill`, `--verify-freshness` without a topic, `library`/`queue` commands). The watchlist runner and the MCP `research` tool opt their engine calls back in automatically.

| Variable | Effect |
|---|---|
| `LAST30DAYS_ALLOW_ENGINE_PLAN=1` | Lift the gate for a headless or cron run launched from an agent shell: the engine plans internally with the reasoning provider above (or the deterministic fallback). Process environment only. |

Outside a detected agent host nothing changes. The engine still plans internally and prints the LAW 7 reminder whenever it ends on the deterministic fallback, including when the internal planner fails (for example an HTTP 402 from a provider with no credits).

### Provider endpoint overrides

Point a provider at a gateway (LiteLLM, an enterprise proxy, a self-hosted OpenAI-compatible server) without a code change:

| Var | Default |
| --- | --- |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1/responses` |
| `XAI_BASE_URL` | `https://api.x.ai/v1/responses` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1/chat/completions` |

These redirect the request that carries the provider's `Authorization: Bearer <key>` header, so they are a credential boundary, not just a URL setting:

- **`https://` is required for remote hosts.** An `http://` override to anything other than loopback is refused with a stderr warning and the vendor endpoint is used instead - otherwise the API key would go over the wire in cleartext.
- **`http://` on loopback is allowed** (`localhost`, `127.0.0.0/8`, `::1`) so a local gateway or SSH tunnel keeps working. These provider requests bypass HTTP proxy settings for the request, keeping the bearer token on the local connection.
- A host or API root ending in a version segment (for example, `/v1` or `/api/v1`) gets the provider's route appended. A complete custom gateway route is used unchanged, including its query string.
- **`--preflight` reports all three** under `endpoint_overrides` / `ignored_endpoint_overrides`, so you can see before a run whether a config file is redirecting a key. Rejected values appear only by variable name, never by URL or embedded credentials.
- A per-project `.claude/last30days.env` can set them only when project config is trusted (`LAST30DAYS_TRUST_PROJECT_CONFIG=1`); untrusted project files are ignored and listed by `--preflight`.

---

## Web search backend priority

The search-source preference ladder, strict best-to-floor:

1. **Host web search** - whatever web-search capability the agent session already has: built-in search, a deferred web-search tool that must be loaded first, or an installed connector such as Brave, Firecrawl, Exa, Serper, or another provider. Best results; used automatically on hosts that have it. A failed lookup for one specific tool name is not fatal when another web-search capability is available. Signalled to the engine via `LAST30DAYS_NATIVE_SEARCH=1` (the skill sets this for you when your agent session has web search) so the engine does not run a worse search underneath it.
2. **Paid engine backend** - one of `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY`, auto-detected in that order. Override per-run with `--web-backend=<name>`.
3. **Explicit hosted MCP** - `--web-backend=parallel-mcp` opts this run into the anonymous `https://search.parallel.ai/mcp` server. Search objectives and queries reach Parallel; the option is never auto-selected. The free path needs no key, while an existing `PARALLEL_API_KEY` is sent as optional Bearer authentication for higher limits.
4. **Keyless engine floor** - zero-key web search (DuckDuckGo, plus an optional SearXNG instance) and zero-key page fetch (Jina Reader). Runs only when the agent session has **no** host web search **and** no paid key is set, so headless/cron and hosts without a search tool still get general-web coverage. Force it explicitly with `--web-backend=keyless`.

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

### `--x-posts` flag

`--x-posts <path>` hands the engine an X result the hosting model fetched itself (on Grok Bot, through the X for Grok Bot connector or the bot's built-in X tools); it replaces the engine's X fetch for that run and works on any host. The value is a file path only (inline JSON exits `2`): a regular `.json` file in the `last30days-x-posts/1` shape, never read from inside the config dir or a credential store.

| Field | Meaning |
| --- | --- |
| `schema`, `generated_at`, `topic`, `window {from, to}`, `provider`, `status` | Envelope header. `status` is `ok`, `partial`, or `error`; `error` is a short category (`credits`, `not-connected`, `unavailable`, `window-unsupported`), never raw tool output. `topic` must match the run topic and `generated_at` must be under 6 hours old, or the run fails closed with exit `2`. `provider` `x-native` (Grok Bot's built-in X tools) makes the footer read "via Grok Bot X"; any other value reads "via X connector". |
| `calls[]` | One entry per query (merge a query's pages into one entry): `lane` (`topic`, `from`, `mention`, `related`), `handles` (a subset of the run's `--x-handle` / `--x-related` handles), and `posts`. |
| `posts[]` | Flat rows with exactly eight fields: `id`, `author_handle`, `created_at`, `text`, `likes`, `reposts`, `replies`, `quotes`. Any other key is ignored and counted. |

Limits: 8 MiB, strict UTF-8, at most 20 calls, 500 rows per call, 1,000 rows in total, 10,000 characters of text per row. Rows are rebuilt from validated parts: the citation is always `https://x.com/<handle>/status/<id>` (a row-supplied URL is never used), rows without an id or text, outside the window, or whose date disagrees with the id are dropped and counted, and an id sequence that looks generated rejects the whole file. The envelope is single-serve for the run, and its topic rows count against twice the per-stream limit (12 / 24 / 40 at quick / default / deep), the same budget as the engine's two X fetches. The hosted backend (`LAST30DAYS_API_BASE`) rejects the flag with exit `2`. Comparison runs take the per-entity `x_posts` field of `--competitors-plan` instead; a bare `--x-posts` on a comparison run exits `2`.

```bash
python3 skills/last30days/scripts/last30days.py "<topic>" --x-posts /tmp/x-posts.json
```

### `setup --store-key`

`setup --store-key <NAME>` persists one credential to the global `.env` (mode `600`) from a single line on stdin, without echoing it: stdout shows `NAME=****` plus a JSON line `{"persisted": true, "key": "NAME"}`. `NAME` must be one of the credential names the engine loads from `.env` (for example `X_BEARER_TOKEN`, `XAI_API_KEY`, `SCRAPECREATORS_API_KEY`); an unknown name or an empty value exits `2`. Running it again with a new value replaces the stored one (rotating a rejected credential); other lines in the file are untouched.

```bash
printf '%s\n' "$TOKEN" | python3 skills/last30days/scripts/last30days.py setup --store-key X_BEARER_TOKEN
```

---

## Health check (`doctor`)

One command answers "what could be on, what's turned on, what's working, and what isn't" — a four-state audit (WORKING / TURNED ON - UNVERIFIED / NOT WORKING / COULD BE ON), one line per source, with a CLI-health block for sources that need a downloaded binary, indented backup/comment sub-lanes, the backend the next run will use (for chained sources), and an exact fix on anything that isn't working:

```bash
python3 skills/last30days/scripts/last30days.py doctor              # four-state audit (text)
python3 skills/last30days/scripts/last30days.py doctor --json       # machine contract
python3 skills/last30days/scripts/last30days.py doctor --cached     # serve the cached report while fresh
python3 skills/last30days/scripts/last30days.py doctor --postmortem # what actually broke on the last run
python3 skills/last30days/scripts/last30days.py doctor --probe      # bounded live test (free/CLI sources)
```

Slash-command form: `/last30days doctor`. Reporting problems is a successful run — the exit code is always 0, no browser cookies are read, and no secret values appear anywhere (key presence is booleans only). Backends within a chained source are probed sequentially with a 5-second budget per binary probe, so a chained source's worst-case check time is additive across its backends (only reached when several binaries hang at once).

`doctor --postmortem` reads the last run's `last-report.json` (any age, labeled) and reports what actually happened per source — Failed / Partial / Succeeded / Skipped, with details and fix hints — so a run that returned less than expected can be diagnosed after the fact. It makes no network calls.

**Network note:** plain `doctor` with a fresh run, `--cached`, and `--json` make **no** network calls. `doctor --probe` — and a plain `doctor` when there is **no** fresh run to learn from — run a **bounded** live test to verify WORKING instead of guessing. The probe is scoped to free HTTP endpoints (Reddit, Hacker News, Polymarket, GitHub) plus keyless CLIs; credit-gated sources (X, TikTok, Instagram, Threads, …) are never probed, so no ScrapeCreators credits are spent and no auth rate limits are tripped. Each source is probed concurrently under a per-source deadline so a slow source can never hang the command.

Every live run writes its JSON result to `~/.config/last30days/doctor-cache.json` (beside `last-run.json`; honors `LAST30DAYS_CONFIG_DIR`). `doctor --cached` returns that stored report when it is younger than the TTL, and falls through to a live run — rewriting the cache — when it is stale, absent, or corrupt. The cache also self-invalidates on configuration change: the payload carries a schema stamp plus a fingerprint of non-secret config signals (which credentials are present as booleans, the `LAST30DAYS_X_BACKEND` / `LAST30DAYS_REDDIT_BACKEND` pin values, and `INCLUDE_SOURCES`), so adding or removing a key, changing a pin, or toggling an opt-in source makes the next `--cached` call run live — no raw secret ever enters the fingerprint or the file. Every report also carries `from_cache` (true/false) and `generated_at` (when the report was built), in the `--json` top level and as a final `generated: … (cached|live)` text line, so you can always tell how old a cached answer is. A failed cache write is never fatal — doctor prints a one-line stderr warning and continues. An explicit `doctor` without `--cached` always runs live and refreshes the cache.

| Var | Effect |
| --- | --- |
| `LAST30DAYS_DOCTOR_TTL` | Freshness window for `doctor --cached`, in **seconds**. Defaults to `900` (15 minutes). `0` makes every `--cached` call run live. |
| `LAST30DAYS_DOCTOR_PROBE_TIMEOUT` | Per-source deadline (**seconds**) for `doctor --probe` live checks. Defaults to `10`. Caps each concurrent probe so a slow source cannot hang the command. |
| `LAST30DAYS_HOST` | Host self-identification. `grok-bot` switches X to the official chain (xapi → xai → xurl) and turns off browser-session discovery; any other value, or unset, leaves every host exactly as today. Persisted to `.env` by first-run setup on Grok Bot and exported per invocation by the skill; doctor prints the resolved value. The engine never infers the host any other way. |
| `X_BEARER_TOKEN` | App-only bearer for the official X API v2 (`xapi`). First rung of the chain on Grok Bot; opt-in elsewhere via `LAST30DAYS_X_BACKEND=xapi`. Full-archive search is tried first, then recent search, so coverage is recent posts, about the last week, unless your X developer project has full-archive access (the outcome detail says `window truncated to 7 days` when the fallback ran, and `search stopped at the lane deadline; results may be incomplete` when the shared 150s lane budget cut a search short). Exhausted credits (HTTP 402) report as `payment-required`. Doctor checks presence only, never the network. Loaded from `.env`, Keychain, or `pass` like the other keys. |
| `LAST30DAYS_X_HOST_LANE` | `1` declares that the hosting model fetched X itself (on Grok Bot, through its built-in X tools or the X connector) and is passing the posts, so `--diagnose` and planning list `x` as available and the run expects `--x-posts`. Read from the process environment only: a `.env` line is ignored (doctor says so), so a stale declaration never outlives the session. Export it only when the host fetch returned posts. A run with the signal but no `--x-posts` records X as `error` ("connector result not passed"). |
| `LAST30DAYS_X_BACKEND` | Pins the X backend (`bird` / `xai` / `xurl` / `xquik` / `grok` / `xapi`); doctor renders the pin and predicts "will use" accordingly. The unpinned auto chain is bird → xai → xurl → xquik (grok and xapi are opt-in only). Pin `grok` to enable it; a leftover `~/.grok/auth.json` is never auto-selected. Pin `xapi` to use `X_BEARER_TOKEN` on an ordinary host. On a Grok Bot host the unpinned chain is the official chain (xapi → xai → xurl) and this pin is the only way to select a backend outside it; the pin keeps its exclusive, no-failover meaning there, and doctor names the pinned backend. |
| `AGENTCOOKIE` | `on` opts any host (incl. a MacBook) into the extra bird cookie lookups (agentcookie sidecar + live Chrome CDP); `off` disables the agentcookie sidecar reader. Unset uses host detection (Linux / Mac mini / Darwin sink get the extras). See "Extra bird cookie lookups" above. |
| `BROWSER_CONSENT` | Browser-cookie decision saved by setup: `true` permits consented browser discovery; `false` blocks native browser and CDP reads across later runs. Native extraction still requires `FROM_BROWSER`; CDP permits a prior explicit Chromium / `auto` selection when this marker is absent. `FROM_BROWSER=off` always blocks both readers. |
| `BROWSER_CDP_URL` | Explicit Chrome DevTools endpoint (e.g. `http://127.0.0.1:18800`) for the extra-host CDP cookie lookup. Preferred over the `18800` / `9222`+`$DISPLAY` defaults. Extra hosts only; setting an endpoint does not grant browser-cookie consent. |
| `LAST30DAYS_REDDIT_BACKEND` | `scrapecreators` makes ScrapeCreators the primary Reddit backend; doctor renders Reddit's conditional routing with the pin applied. |
| `LAST30DAYS_REDDIT_SC_MIN_ITEMS` | Integer thinness floor for ScrapeCreators Reddit **search** backfill. Default `5`: backfill when the free path returns fewer than 5 items, spending ScrapeCreators credits. Set another positive count to move the floor, or `0` to backfill only when the free path returns nothing. Unset or blank means `5`; a malformed value behaves like `0` (spends nothing extra). A run makes at most one ScrapeCreators Reddit backfill per distinct query, date window, and subreddit set, and each backfill is several API calls (searches plus comment fetches, roughly 10 at default depth). A failed backfill after free items only adds a detail note. Merged results dedupe by post id. Requires `SCRAPECREATORS_API_KEY`. Ignored when `LAST30DAYS_REDDIT_BACKEND=scrapecreators` (SC is already primary). |

Web search has **no** env pin — pin it per-run with `--web-backend=<name>` only (see [Web search backend priority](#web-search-backend-priority)).

### Strict exit for degraded runs

By default a research run exits `0` even when a source failed mid-run (rate-limited, auth-failed, unreachable, timeout, schema-drift) — the report still renders, with the failure annotated in the per-source footer and a partial-coverage warning. Wrappers that need to distinguish degraded coverage from success (cron briefs, CI, downstream agents) can opt in:

| Var | Effect |
| --- | --- |
| `LAST30DAYS_STRICT_EXIT` | Truthy (`1`/`true`/`yes`/`on`): the engine exits `3` when any source outcome is neither `ok`, `no-results`, nor `skipped-unconfigured` (so `partial`, `auth-failed`, `payment-required` for exhausted credits, `rate-limited`, and the other failure states all count as degraded). A one-line `strict-exit: degraded sources: ...` note goes to stderr. Default (unset): exit `0`, unchanged behavior. |

Exit codes with the flag on: `0` clean run, `3` completed-but-degraded (report was produced), non-zero others unchanged (hard failures). Same hybrid pattern as `LAST30DAYS_DEBUG` — works shell-exported or in `.env`.

---

## Debug mode (`--debug`)

Add `--debug` to any run to emit verbose `[DEBUG]` log lines to stderr from the source modules (X API, HTTP, etc.). Helpful for diagnosing API errors or unexpected behavior.

**Always-on alternative:** set `LAST30DAYS_DEBUG=true` in your `.env` or export it from your shell. The flag still works as before; the env var is purely additive — works whether shell-exported or set in `.env`.

---

## Trend monitoring (`--store` + watchlist + briefings)

The default behavior - one slug-named file per topic, overwritten on rerun - is the snapshot mode. For continuous monitoring, the repo ships three components most users miss:

### `--store` flag

Adding `--store` to any run persists every finding to a SQLite database (default at `~/.local/share/last30days/research.db`). Findings dedupe on the `source_url` column (UNIQUE constraint), so the same URL across runs updates the existing row instead of creating a duplicate. The markdown file still saves; the SQLite is the time-series substrate.

**Always-on alternative:** set `LAST30DAYS_STORE=1` in your `.env` instead of remembering `--store` on every invocation. The flag still works as before; the env var is purely additive. Same hybrid pattern as `LAST30DAYS_DEBUG` — works whether shell-exported or in `.env`.

Relevant tables: `topics`, `research_runs`, `findings`, `settings`. Schema: [`scripts/store.py`](skills/last30days/scripts/store.py).

### Discovery topic queue (`LAST30DAYS_DISCOVERY_QUEUE`)

`--discover` runs remember what they surfaced (table `discovery_topics` in the same research.db). Re-surfaced topics get a `**Pipeline:**` line on their card ("surfaced 2nd time", "marked covered") so the discovery brief doubles as a podcast / X-article content pipeline. On by default for real runs; `--mock` runs never write. With `--save-dir`, queue rows land in that directory's scoped `research.db`, never the global one.

| Var | Effect |
| --- | --- |
| `LAST30DAYS_DISCOVERY_QUEUE` | Set to `off` to disable queue writes and card annotations. Any other value (or unset) keeps the queue on. Works shell-exported or in `.env`. |
| `LAST30DAYS_ENRICH_BUDGET_SECONDS` | Wall-clock budget (seconds) for the deep-tier per-topic research batch on the discovery resume leg (`--discover --judgments <file>`). Default `450`; unset/invalid/non-positive values fall back to it. The one-shot `--discover` path keeps its fixed quick-tier 240s budget regardless. Works shell-exported or in `.env`. |

Manage the queue from the engine CLI:

```bash
# Uncovered surfaced topics (name, domain, surface_count, last_surfaced, status)
python3 skills/last30days/scripts/last30days.py queue list

# Mark a topic done after you record the episode / publish the article.
# Requires the exact topic name; unknown names exit 2 instead of no-opping.
python3 skills/last30days/scripts/last30days.py queue cover "Gemma 4 chat templates"
```

Both respect `--save-dir` scoping.

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

The daily budget defaults to USD 5.00 and applies before each `run-one` or `run-all` topic. It stops subsequent runs after recorded spending reaches the limit; it cannot cap an already-running request or spending outside this local store. Days use UTC. Free sources consume no budget. Watchlist runs record provider-reported OpenRouter account charges (`usage.cost`) and synchronous Perplexity Agent USD charges (`usage.cost.total_cost`), including charges incurred before a later error or local timeout. No model price estimates are substituted for missing billing data.

If a paid backend does not report a supported USD amount, its cost is **unknown**. This includes direct OpenAI, Gemini, and xAI calls, the official X API (including xurl), ScrapeCreators, paid web-search APIs, transcription, BrightData, Grok CLI, hosted research, and unfinished remote background operations. The output retains any known subtotal as `budget_used` and reports `budget_unknown_runs`; subsequent topics stop while today's store contains unknown spending. A provider's free allowance is not assumed to make an unreported charge zero. Existing zero-cost records and direct engine `--store` runs without accounting also remain unknown. For unattended runs that must continue below the limit, use free sources and providers with supported cost reporting, and inspect the provider's own billing limits for protection outside this guard.

`LAST30DAYS_USAGE_JOURNAL` is an internal subprocess variable managed by `watchlist.py`; do not configure it yourself. Watchlist creates an owner-only temporary journal, records requests before transport, persists the totals even on failure, and removes the journal after the run. It contains provider names, amounts, and token counts, without credentials, queries, or response text. Watchlist disables child `LAST30DAYS_STORE` persistence so the parent records each run once.

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

Section 2a of `skills/last30days/references/research-runbook.md` and `skills/last30days/references/category-peers.md` document the merging rule the skill applies when your topic matches a category.

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

Pass as `--competitors-plan @client/competitors-plan.json` (or as a string). See `skills/last30days/references/comparison.md` for the full schema.

### Grok Bot

On a Grok Bot host the bot exports `LAST30DAYS_HOST=grok-bot` on every engine call (first-run setup also persists it to `.env`), and X search runs through official channels only, in this order:

1. **Built-in X tools (primary).** Grok Bot's own X search (the `x` tools), available to every Grok Bot user with nothing to set up and no X API credits used. The bot fetches the posts itself (recency-sorted pages to the depth count, plus one relevance-sorted page for each of 10 equal slices of the window (5 on `--quick`) on the first query so busy topics still surface the month's most-engaged posts), writes them to a `last30days-x-posts/1` file with `"provider": "x-native"`, exports `LAST30DAYS_X_HOST_LANE=1`, and passes the file with `--x-posts <path>` (see [`--x-posts` flag](#--x-posts-flag)). The footer reads "via Grok Bot X".
2. **X connector.** When the built-in tools are missing or return nothing, the "X for Grok Bot" plugin (connect your X account in Grok Bot settings; it provisions an X developer account for you) serves the same file with `"provider": "x-connector"`, and the footer reads "via X connector". `--diagnose` lists `x` as available whenever the lane signal is set; a run with the signal but no `--x-posts` file records X as `error` ("connector result not passed"), so the bot exports the signal only when a fetch returned posts.
3. **`X_BEARER_TOKEN` (backup).** An app-only bearer from the X developer console, funded by your own X developer project. The engine tries full-archive search first and falls back to recent search, so coverage is recent posts, about the last week, unless your X developer project has full-archive access; the outcome detail says `window truncated to 7 days` when the fallback ran. Exhausted credits (HTTP 402) report as `payment-required`, which `LAST30DAYS_STRICT_EXIT` treats as degraded.
4. **`XAI_API_KEY` (backup).** xAI's licensed X search from console.x.ai: full window, topic search only (no from/mention handle lanes).

Persist either key without echoing it:

```bash
printf '%s\n' "$TOKEN" | python3 skills/last30days/scripts/last30days.py setup --store-key X_BEARER_TOKEN
```

Browser sessions are not read on this host, and no login window is opened; `setup` still installs the free CLIs. Doctor prints the resolved host value, so a missing `LAST30DAYS_HOST` export is visible at a glance.

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
