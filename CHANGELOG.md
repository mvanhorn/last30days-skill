# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.11.0] - 2026-07-05

### Added

- `last30days doctor`: a unified health command that aggregates every source's probe state into a single grouped report with copy-pasteable fix prescriptions. Layered design: dependency probes (missing/broken/timeout detection), backend-chain descriptors (predict-then-report, never a network call), a centralized prescription registry shared by doctor and quality nudges, and an aggregator with grouped rendering. Replaces the fragmented health knowledge previously spread across `--diagnose`, `--preflight`, `lib/health.py`, and post-run nudges. ([#753](https://github.com/mvanhorn/last30days-skill/pull/753))

### Fixed

- Techmeme: `search` results are now windowed to each record's own ISO date instead of stamping every record with today's date, so years-old archive headlines can no longer surface as current news. Dated in-window records take result-cap slots first; undated records (old `techmeme-pp-cli` binary or upstream markup change) degrade gracefully with a logged upgrade hint. The sync machinery is removed because `search` never read the local cache. ([#752](https://github.com/mvanhorn/last30days-skill/pull/752))
- LinkedIn now renders in the emoji-tree footer (👔 with likes/comments), the `## Stats` engagement summary, and with the correct "LinkedIn" label. Previously LinkedIn items were counted in `## Stats` but silently dropped from the footer because `_FOOTER_SOURCES`, `ENGAGEMENT_DISPLAY`, and `SOURCE_LABELS` all omitted the source - an 8-item LinkedIn run looked like the source never ran. ([#758](https://github.com/mvanhorn/last30days-skill/pull/758))

## [3.10.0] - 2026-07-04

### Added

- Instagram comments as a first-class ScrapeCreators source: `instagram.enrich_with_comments` fetches top comments via `GET /v2/instagram/post/comments` (ranked by `comment_like_count`), gated by `SCRAPECREATORS_API_KEY` + `instagram_comments` in `INCLUDE_SOURCES`. Full vote-weighting parity with YouTube/TikTok - a dedicated `_instagram_engagement` gives IG posts the same top-comment ranking carve-out, and IG comments render with a "likes" label. ([#751](https://github.com/mvanhorn/last30days-skill/pull/751))
- Comments are now on by default: the first-run Step 5 Recommended tier enables top comments for TikTok, Instagram, and YouTube (`INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments`); the Everything tier adds Threads + Pinterest. Comments were previously an opt-in "Everything" feature. ([#751](https://github.com/mvanhorn/last30days-skill/pull/751))

### Changed

- The cross-platform "Top Community Comments" list now selects **round-robin by within-platform rank** (every platform's #1, then #2, then #3) instead of a global vote-magnitude sort, so the top-3-of-each-platform outranks the 4th-of-any and each platform's #1 is guaranteed a slot - a viral platform can no longer sweep the list. The list also drops the per-platform absolute vote floor so a less-watched video's high-signal low-vote comment still surfaces (the per-candidate card keeps its floor). ([#751](https://github.com/mvanhorn/last30days-skill/pull/751))

### Fixed

- First-run wizard: the welcome pitch is embedded directly in the setup modal (the only always-visible surface) instead of a separate `--welcome` message that Claude Code folds behind "ctrl+o to expand"; the cookie-consent and ScrapeCreators-offer copy now name every installed CLI (yt-dlp, Digg, arXiv, Techmeme) and describe the key's real reach (auto Reddit enrichment + YouTube search backstop), with the GitHub device code auto-copied to the clipboard. ([#750](https://github.com/mvanhorn/last30days-skill/pull/750))

## [3.9.4] - 2026-07-04

### Fixed

- First-run wizard: the welcome message and the ScrapeCreators GitHub device code are now engine-driven instead of model-authored, because a real cold run showed the model skipping the welcome and never surfacing the device code no matter how forceful the SKILL.md prose. The welcome is printed by a new `last30days.py --welcome` command that Step 1 relays verbatim (single source of truth; it can't be skipped or drift), and the GitHub device flow is split into `setup --github-start` (submits, copies the code to the clipboard, prints it to stdout, opens the browser, returns immediately) and `setup --github-poll` (waits for authorization and persists the key). The one-shot `setup --github` still chains both. The code now always appears in the command output, and the "on your clipboard" claim is only made when the copy actually succeeded. ([#748](https://github.com/mvanhorn/last30days-skill/pull/748))

## [3.9.3] - 2026-07-04

### Added

- Optional remote research API backend (env-driven). When both `LAST30DAYS_API_KEY` and `LAST30DAYS_API_BASE` are set in the process environment (never read from `.env`), a search runs through the configured remote endpoint (submit -> poll with stderr progress -> render) instead of local sources; with either unset, behavior is byte-identical to local-only. Opt-in and inert by default (no built-in endpoint); the key is confined to the `Authorization` header and never logged or persisted. Handles the clarify gate and 401/402/429 paths. ([#747](https://github.com/mvanhorn/last30days-skill/pull/747))

### Fixed

- First-run wizard: the welcome message is now mandated before the setup modal (it was being skipped), the Auto-setup option lists every installed CLI (yt-dlp, Digg, arXiv, Techmeme, not just two), and the ScrapeCreators GitHub signup reliably surfaces the device code with an "it's on your clipboard, just paste" hint as a required step instead of leaving the user staring at a spinner. ([#746](https://github.com/mvanhorn/last30days-skill/pull/746))
- ScrapeCreators GitHub signup: an already-linked account whose `.env` is cold no longer fails with the misleading "GitHub auth didn't complete." The `Authorized but failed to fetch API key` case now gets an honest branch (auth worked; the account is likely already linked -- get your key from scrapecreators.com and paste it), and `fetch_api_key` logs the `/profile` response field names (never values) so a full auto-fetch can follow. ([#746](https://github.com/mvanhorn/last30days-skill/pull/746))

## [3.9.2] - 2026-07-03

### Fixed

- Trustpilot source returned 0 items on company topics: the engine passed raw topic names to a domain-keyed CLI (`info ThriftBooks` -> HTTP 404) and parallel subqueries raced concurrent Chrome WAF-cookie harvests. Company names now resolve to their Trustpilot review-page domain via the CLI's search (per-topic cache; name-match mandatory, ambiguous cases fall back rather than misattributing another company's reviews), a new `--trustpilot-domain` flag pins the domain explicitly (verbatim, bypasses the brand-shape gate, per-entity `trustpilot_domain` in `--competitors-plan`), the WAF session warms once per 240s window behind a lock at first fetch, Trustpilot is capped to one fetch per run and excluded from the thin-source retry, and headless `--auto-resolve` fills a verified domain hint. SKILL.md Step 0.5d documents the resolution flow. ([#745](https://github.com/mvanhorn/last30days-skill/pull/745))

## [3.9.1] - 2026-07-03

### Fixed

- First-run setup wizard: the browser-cookie scan now tries Chrome/Chromium first (Keychain, no Full Disk Access) before Safari, so macOS users logged into X in Chrome authenticate in ~2s instead of hitting the Safari Full Disk Access dead-end. The winning browser is pinned for later runs only when it is Firefox/Safari, so Chrome never re-triggers the Keychain prompt. Consent copy leads with Chrome and the one-time "Always Allow" cue. ([#744](https://github.com/mvanhorn/last30days-skill/pull/744))
- ScrapeCreators GitHub signup now surfaces the device code immediately (emitted to stdout so a backgrounded caller shows it at once, instead of a spinner until the process exits), validates the `XXXX-XXXX` code shape before copying/labeling it, short-circuits an already-registered account without a fresh device dance, and masks the API key on every status (not just success). Removed the false "GitHub CLI ~2 seconds, no browser" promise. ([#744](https://github.com/mvanhorn/last30days-skill/pull/744))
- ScrapeCreators source opt-in is now two real tiers. The Step 5 choices were previously identical — a key auto-ran TikTok, Instagram, Threads, and YouTube comments regardless of `INCLUDE_SOURCES`, and Pinterest's opt-in silently ignored a persisted `INCLUDE_SOURCES`. Threads, YouTube comments, and Pinterest are now genuine `INCLUDE_SOURCES` opt-ins: **Recommended** = TikTok + Instagram + the rate-limit backups; **Everything** = also Threads, Pinterest, and YouTube/TikTok/Instagram comments. "ScrapeCreators backups" is now defined inline (keeps Reddit/YouTube working at rate limits). ([#744](https://github.com/mvanhorn/last30days-skill/pull/744))

## [3.9.0] - 2026-07-03

### Added

- StockTwits as a source, gated to ticker/crypto topics only. Surfaces a retail sentiment ratio (self-reported Bullish/Bearish tags) and message volume on a resolved symbol. Inert on non-financial topics: an unambiguous finance-vocabulary gate (cashtags, "stock", "earnings", "dividend", "crypto", named coins) keeps it from injecting stock chatter into general runs, and it degrades to an empty lane if the public API fails without touching other sources. ([#658](https://github.com/mvanhorn/last30days-skill/pull/658), thanks @wtiwana)
- LinkedIn as a source via ScrapeCreators, surfacing articles as high-signal results with date-range filtering, gated behind `INCLUDE_SOURCES`. ([#702](https://github.com/mvanhorn/last30days-skill/pull/702))
- arXiv and Techmeme sources (default-on) plus Trustpilot (opt-in). ([#709](https://github.com/mvanhorn/last30days-skill/pull/709))

### Fixed

- Runtime preflight now auto-provisions a uv-managed CPython 3.12 on hosts that have `uv` but no system Python 3.12+ (most agent sandboxes), instead of hard-failing the version gate. The install is bounded by a 30s HTTP timeout, matches an existing managed `>=3.12` interpreter before downloading, and announces the one-time ~28MB download on stderr rather than installing silently; hosts without `uv` still get the original clear error. Setup invocations now honor `LAST30DAYS_PYTHON` so first-run setup works on the same hosts. ([#738](https://github.com/mvanhorn/last30days-skill/pull/738), thanks @buntysomroy; setup-interpreter fix adapted from #699 by @SeanGearin)
- Setup wizard summary now displays the install status of the arXiv/Techmeme pp_sources CLIs, so users can see whether they landed on PATH. ([#741](https://github.com/mvanhorn/last30days-skill/pull/741), thanks @23241a6749)
- `--diagnose` / `--preflight` no longer falsely reports X as unreachable when X auth comes from `FROM_BROWSER` browser cookies. These modes run in `plan_only` and skip cookie extraction for privacy (no Keychain access), so X was dropped from `available_sources` even though a real run authenticates fine. A new side-effect-free `env.x_pending_browser_auth` predicate now reports X as available-pending-browser-auth (and surfaces an `x_pending_browser_auth` flag in `--diagnose`) by keying only on the already-resolved browser list — no cookie is read. Covers every configured browser, including Chrome. ([#692](https://github.com/mvanhorn/last30days-skill/issues/692); first reported and fixed by @23241a6749 in #700)

### Internal

- Tightened Hermes `.skillignore` regression coverage: the test now fails if an ignored path is deleted without updating the ignore list, or if a runtime-contract file is accidentally ignored. ([#739](https://github.com/mvanhorn/last30days-skill/pull/739), thanks @SyntaxSawdust)

## [3.8.3] - 2026-06-25

### Added

- Free Reddit gets dedicated-subreddit lanes: entity-home subs (e.g. r/Kanye for "Kanye West", via the new `--dedicated-subreddits` flag) are pulled in full from top+hot+new listings and exempt from the relevance floor, since the whole sub is the topic. Fixes the over-aggressive floor that dropped on-topic posts whose titles lacked the entity name.
- `reddit_arctic` resolves upvote counts for threads found only via RSS search (which carries no score) using the free, keyless arctic-shift archive — batched, paced, cached, and graceful-degrading. Reddit now gets headlines-with-points and best-comments-with-points entirely for free, at parity with ScrapeCreators.
- `LAST30DAYS_REDDIT_SC_MIN_ITEMS` (default 0 = unchanged empty-only behavior): set above 0 to let the ScrapeCreators backup backfill a thin free Reddit run instead of sitting idle. Backfilled items merge deduped by post id.

### Removed

- The permanently-403 `search.json` Tier 0 is gone from the keyless Reddit path; discovery is RSS breadth + shreddit listing partials (real scores) + the dedicated-sub lanes, with no wasted 403 calls.

## [3.8.2] - 2026-06-25

### Added

- Advisory Semgrep SAST scan runs on every push/PR as part of the Security workflow, catching source-level security bugs using Semgrep CE community rules ([#563](https://github.com/mvanhorn/last30days-skill/issues/563))
- Scheduled OSV-Scanner vulnerability-drift workflow scans repository lockfiles weekly and uploads SARIF results to GitHub code scanning, catching newly disclosed CVEs in the dependency tree even between PRs ([#571](https://github.com/mvanhorn/last30days-skill/issues/571))
- `LAST30DAYS_REDDIT_BACKEND=scrapecreators` makes ScrapeCreators the primary Reddit backend with the public path as fallback. Users with a ScrapeCreators key who were getting shallow public data will now get full nested comment trees by setting this flag ([#589](https://github.com/mvanhorn/last30days-skill/issues/589))
- MCP Go tests (`mcp/`) now run in CI on every push/PR alongside the Python test suite, so MCP server regressions are caught before merge ([#621](https://github.com/mvanhorn/last30days-skill/issues/621))
- PR dependency review gate blocks merges that introduce new vulnerable dependencies ([#551](https://github.com/mvanhorn/last30days-skill/issues/551))

### Changed

- Citations are now renderer-aware (LAW 8). On hidden-link hosts (Claude Code) every citation stays an inline `[name](url)` link as before; on visible-URL hosts (Codex, Cursor, Gemini CLI, raw CLI) citations render as plain source labels so the narrative no longer turns into `label (https://...)` URL soup. The host is detected deterministically from the `CLAUDECODE` environment variable, and full URLs remain reachable through the engine footer and the saved raw file.

### Fixed

- The query-plan invocation guidance now warns against wrapping the heredoc in `bash -lc '...'` / `zsh -lc '...'`, whose single quotes terminate at the first apostrophe in a ranking string and abort the engine run with `unmatched "` on Codex. The quoted `<<'PLAN_EOF'` heredoc is already apostrophe-safe; the `-lc` wrapper was the hazard.
- Firefox profile detection on Linux now checks `$XDG_CONFIG_HOME/mozilla/firefox` (or its default `~/.config/mozilla/firefox`) in addition to `~/.mozilla/firefox`, fixing cookie extraction on distros that honour the XDG Base Directory Specification ([#667](https://github.com/mvanhorn/last30days-skill/issues/667))

## [3.8.1] - 2026-06-22

### Added
- **Restored the v3.0.0 first-run NUX wizard (Claude Code Modal Flow).** Step 0 now restores the original guided, `AskUserQuestion`-driven onboarding that eroded over time: a welcome message, an Auto/Manual/Skip setup modal, a cookie-consent modal, the ScrapeCreators signup offer, a TikTok/Instagram `INCLUDE_SOURCES` opt-in, and a first-topic picker. It is gated to hosts with modals; hosts without (OpenClaw, Codex, Cursor, Gemini CLI) get the equivalent **Non-Modal Prose Flow**. Digg is threaded into the install messaging alongside yt-dlp everywhere it appears, the ScrapeCreators credit count is `10,000 free calls`, and the flow is locked against re-erosion by `tests/test_onboarding_contract.py`. Builds on the consent-driven foundation from #659/#660. Original wizard captured at `docs/reference/old-nux-wizard-v3.0.0.md`.
- **Consent-driven first-run onboarding.** Step 0 now drives an in-chat consent flow instead of a silent `setup` run: the model asks before reading browser cookies (decline runs with `FROM_BROWSER=off` — still installs yt-dlp + Digg), surfaces the macOS Full Disk Access fix when a cookie read is permission-denied, and offers the ScrapeCreators GitHub signup on every first run. A successful `setup --github` now **persists `SCRAPECREATORS_API_KEY` automatically** (`setup_wizard.write_api_key`, 0o600) and masks the key in stdout so the secret never lands in the host model's captured output. Follows the first-run gate fix (#659).

### Fixed
- **First-run setup no longer runs silently.** The prior Step 0 told the model to run `setup` and "follow the wizard's prompts end-to-end", but the wizard has no prompts — so onboarding extracted cookies, installed tools, and wrote `SETUP_COMPLETE` with zero interaction and never offered the ScrapeCreators signup. Reproduced 2026-06-22 (Fredy Montero, fresh macOS).

## [3.8.0] - 2026-06-21

### Added

- **Single X source with backend failover.** X is now one source backed by an ordered chain of interchangeable backends (xai, bird, xurl, xquik) with runtime failover, rather than separate sources. The key-based xquik backend reaches parity with bird, gaining the X-quality ranking and FROM/ABOUT handle lanes, so hosts that cannot supply browser cookies (OpenClaw, CI/cron, headless harnesses) get real X coverage from an xquik key alone. Handle lanes run via the first handle-capable backend in the chain even when a non-capable backend (xai/xurl) is primary. (#622)

## [3.7.1] - 2026-06-21

### Fixed

- GitHub repo stars are no longer mislabeled as "reactions" in the report footer. Repo cards use a distinct `stars` engagement key, velocity cards use `merged_prs`, and genuine issue/PR reaction counts keep their own `reactions` key. (#645, closes #642)
- Hacker News returned zero stories on every run: the Algolia query sent `points>2`, which the HN index no longer accepts as a filterable attribute, so every request 400'd. Dropped the server-side `points` filter; low-engagement demotion still happens at parse time. (#639)
- Polymarket surfaced off-topic markets and rendered a mangled footer. The relevance filter was fed the per-subquery string instead of the stable topic, and market labels were truncated mid-article into fragments like "an Anthropic Claude model score at: an 19%". Now filters on the stable topic and cleans the labels. (#640)

## [3.7.0] - 2026-06-20

### Added

- **Direct Perplexity API support.** When `PERPLEXITY_API_KEY` is set it is preferred over OpenRouter for the Perplexity source, unlocking first-party Search API results and async Deep Research. Adds `LAST30DAYS_PERPLEXITY_MODE=sonar|search|both` plus model, search-context, domain/language/country, recency, and reasoning-effort knobs. OpenRouter stays the Sonar compatibility fallback when no direct key is set. Async Deep Research preserves request id, status, idempotency key, poll count, lifecycle timestamps, and failure metadata in raw artifacts. (#629, by @sk-holmes)

### Changed

- `check-config.sh` now parses env files in pure bash (no `sed` / `tr`), which also fixes the YouTube-availability hint breaking in minimal environments that lack those tools. (#629)

## [3.6.1] - 2026-06-20

### Added

- **ScrapeCreators transcript fallback.** When `SCRAPECREATORS_API_KEY` is set, YouTube transcripts fall back to the ScrapeCreators transcript endpoint after the keyless yt-dlp cascade fails (fetched server-side, so no 429 / cookies / PO tokens). yt-dlp stays primary and a credit is only spent on a genuine failure, never on success and never on a video proven to have no captions. With a key, yt-dlp also fails over fast (one short-timeout attempt) so a 429 hands off to ScrapeCreators in roughly 17s instead of roughly 90s. (#637, idea from #595)
- **YouTube comments default-on.** Comment enrichment now activates whenever a ScrapeCreators key is present (bounded to the top ~3 videos by engagement, ~3 credits per run) instead of requiring `INCLUDE_SOURCES=youtube_comments`. Suppress with `EXCLUDE_SOURCES=youtube_comments`. TikTok/Instagram comments remain `INCLUDE_SOURCES` opt-ins. (#637)

### Fixed

- **Salvage partial YouTube transcripts on non-zero yt-dlp exit.** With the default `en,es,pt` languages an English video wrote `en.vtt` then 429'd on `es`/`pt`, and the already-written transcript was discarded and retried back into the rate limit. Any VTT on disk is now read before the failure is classified, which fixes the dominant `0/N transcripts` case. (#636)
- **Windows transcript crash on subprocess timeout.** Guarded the SIGKILL escalation path in `run_with_timeout` against `os.killpg` / `os.getpgid` raising `AttributeError` on Windows (they are POSIX-only), mirroring the primary path's guard. (#638, reported in #588)

## [3.6.0] - 2026-06-18

### Added

- **First-party X posts are no longer buried.** A post authored by one of the run's resolved handles (`--x-handle`, `--x-related`, the GitHub user) is now treated as first-class evidence: it is exempt from the entity-miss demotion (a post never repeats its own author's name, so the body-text grounding check used to zero out the subject's own highest-signal posts) and gets a small authorship credit. Third-party collision-noise suppression is unchanged.
- **Engagement rescue for on-topic X posts.** A high-engagement X post that is first-party or entity-grounded gets a `final_score` floor scaled by its engagement percentile within the run's X pool, so a viral on-topic post can't sit at ~0. Off-topic name-collision posts are explicitly excluded.
- **First-party interaction signal.** A first-party post directed at another account (a reply / leading @mention) is floated into the visible band regardless of like-count and tagged `interaction:→@handle` in the EVIDENCE block, so the synthesis reads it as a relationship signal rather than low-engagement noise. New **LAW 10** in SKILL.md teaches the model to surface first-party posts and read the interaction tag.

### Changed

- The X FROM lane (the subject's own timeline) now pulls up to 8 posts per handle (was 3); the about/related lanes stay modest.

### Fixed

- Secrets `.env` and its parent config directory are now auto-tightened to `0o600`/`0o700` after creation, and `check-config.sh`'s `check_perms` now auto-fixes loose permissions with `chmod 600` instead of warning only ([#573](https://github.com/mvanhorn/last30days-skill/issues/573))

## [3.5.0] - 2026-06-18

### Added

- **X surfaces tweets FROM and ABOUT a person, both engagement-weighted.** The handle search now pulls the person's real timeline (`from:handle since:`, topic used for ranking only — never AND'd into the query, which previously matched only tweets where they wrote their own name and returned ~0), and a new mention lane (`@handle since:`) surfaces what others say to/about them, excluding their own tweets and deduping against the FROM lane ([#610](https://github.com/mvanhorn/last30days-skill/pull/610)).
- **`## Top Community Comments` block.** The engine now surfaces vote-ranked community comments across all candidates (not just the top-cluster representatives), per-platform-normalized, into the EVIDENCE-for-synthesis block, so the funniest/sharpest crowd reactions reach the synthesizing model even when no LLM fun-scorer is available. Paired with a new SKILL.md **LAW 9** that requires weaving ≥2 verbatim attributed comments, copying URLs verbatim, and never narrating the tooling in the deliverable ([#608](https://github.com/mvanhorn/last30days-skill/pull/608)).

### Fixed

- **`--diagnose` honesty.** X status now reflects a real 1-tweet probe (downgrades from green when X is effectively dead; fail-open on a transient timeout) and reports the true auth lane (browser / env / keychain) instead of a hardcoded `env AUTH_TOKEN`. Handle/mention searches log query + result count on success, not only on failure ([#609](https://github.com/mvanhorn/last30days-skill/pull/609)).
- **X column de-pollution.** The last-chance keyword retry no longer collapses a multi-word subquery to a bare generic token (e.g. `compound`); it keeps an entity anchor ([#607](https://github.com/mvanhorn/last30days-skill/pull/607)).
- **Mandatory person-aware subquery disambiguation.** Collision-prone person names (Kevin Rose vs Kevin Warsh, Lan Xuezhao vs Lanzhou) must anchor every subquery with the resolved company/role/domain context ([#611](https://github.com/mvanhorn/last30days-skill/pull/611)).

## [3.4.0] - 2026-06-18

### Added

- **Crowd-vote weighting in the fun judge (Best Takes).** The fun judge now factors how many upvotes/likes each top comment earned. Comment vote counts are fed into the LLM prompt (as traction, not funniness), and Best-Takes selection ranks by an effective score — `fun_score` plus a bounded, per-platform-normalized, relevance-confidence-scaled crowd nudge — so genuinely funny, crowd-loved, on-topic comments surface while off-topic virality and high-voted-but-unfunny rants are excluded. `FUN_LEVEL=medium` stays the default and applies the signal as a meaningful factor ([#592](https://github.com/mvanhorn/last30days-skill/pull/592)).
- **Digg added to first-run setup.** The free, keyless `digg-pp-cli` is now auto-installed during the first-run wizard (best-effort via the Printing Press installer, with a recommend-only fallback), so the already-built Digg AI-news source activates automatically for new users instead of silently never appearing ([#590](https://github.com/mvanhorn/last30days-skill/pull/590)).

- **`LAST30DAYS_YOUTUBE_SSH_HOST` transcript routing** — yt-dlp transcript fetch runs on the remote SSH host via a mktemp + cat pipeline ([#422](https://github.com/mvanhorn/last30days-skill/pull/422)).
- Browser-cookie auth for X/Twitter now covers the full Chromium family on macOS - Brave, Microsoft Edge, Vivaldi, Opera, Arc, and Chromium - alongside the existing Chrome, Firefox, and Safari. They all share Chrome's v10 AES-128-CBC decryption, differing only in profile path and Keychain service name, so they run through one shared decryption core. The profile finder probes both the modern `Default/Network/Cookies` layout (Chromium >= 96) and the legacy flat `Default/Cookies`, and Chrome now resolves through that same finder so it picks up the modern layout too. Set `FROM_BROWSER=auto` to try every browser, or `FROM_BROWSER=<name>` (e.g. `brave`, `edge`, `arc`) to target one. Verified end-to-end on real Brave and Edge installs ([#572](https://github.com/mvanhorn/last30days-skill/pull/572)).
- **First-party positioning research + pitch-vs-pulse synthesis (company / product / service topics).** A new mandatory research step captures each entity's current stated positioning from first-party sources (homepage, docs, pricing) rather than from memory. The fetched pitch grounds `What it is` descriptions (entities described as they pitch themselves today), helps reject unrelated brand-name noise, and feeds an evidence-triggered prose beat: when the month's conversation directly supports a specific claim, cuts against one, or is squarely about the pitched ground, the synthesis says so anchored to the top thread — and stays silent when the pulse is orthogonal to the pitch, because a manufactured connection is worse than omission. Claims are tested at matched altitude (specific claims against specific threads; broad taglines are never graded against individual items), and statements stay windowed to the 30 days — no trend verdicts. Scoped to entities with an identifiable first party: people are always excluded (even founders whose companies qualify), as are events, abstract concepts, and ownerless topics like Bitcoin; the beat requires positioning fetched during the run, never from memory.

### Changed

- Updated "Unlock X" promo message to mention Chrome/macOS support and Windows Firefox-only limitation instead of generic "Firefox or Safari" ([#387](https://github.com/mvanhorn/last30days-skill/issues/387))

### Fixed

- **SSH routing failures no longer present as "0 results"** — `search_youtube` surfaces non-zero SSH exit codes as an explicit `error` field ([#422](https://github.com/mvanhorn/last30days-skill/pull/422)).
- `extract_browser_credentials()` silently ignored Brave even though the lower-level `cookie_extract` layer already supported it: `FROM_BROWSER=brave` fell back to Firefox/Safari and `FROM_BROWSER=auto` never tried Brave. The env wiring now passes Brave - and the rest of the Chromium family - through to the extractor ([#572](https://github.com/mvanhorn/last30days-skill/pull/572)).
- Chromium cookie extraction now fetches the macOS Keychain key lazily - only when an encrypted cookie actually needs decrypting. Previously the key was fetched as soon as the cookie DB existed, so `FROM_BROWSER=auto` could trigger a Keychain prompt for every installed Chromium browser. Now only the browser that actually holds the requested cookie prompts ([#572](https://github.com/mvanhorn/last30days-skill/pull/572)).
- YouTube transcript budget prioritises recent videos (by a combination of views and recency) instead of views alone, preventing transcript slots from being consumed by old high-view-count videos that would be discarded by strict_recent freshness pruning ([#531](https://github.com/mvanhorn/last30days-skill/issues/531))
- YouTube items with successfully extracted transcripts are no longer pruned by title-only relevance scoring; the transcript content proves substantive topical coverage even when the video title has low lexical overlap with the query ([#468](https://github.com/mvanhorn/last30days-skill/issues/468))
- First-run setup wizard in SKILL.md now references the existing Python setup wizard (`last30days.py setup`) instead of the missing `nux-wizard.md` file, so first-run setup actually runs on new installs. ([#574](https://github.com/mvanhorn/last30days-skill/issues/574))
- `check-config.sh` no longer exits 1 on the ScrapeCreators-configured path when no prior run exists (empty `LAST_RUN_LINE`) — swapped `&&` guard for an `if` block that always exits cleanly ([#463](https://github.com/mvanhorn/last30days-skill/issues/463))
- `check-config.sh` no longer exits 1 when a `.env` value contains an unbalanced quote — replaced `xargs` (which interprets quotes) with `sed` for whitespace trimming in `load_env_vars` ([#506](https://github.com/mvanhorn/last30days-skill/issues/506))
- X/Twitter `.env` template now includes `CT0` alongside `AUTH_TOKEN` in the example skeleton ([CONFIGURATION.md](CONFIGURATION.md)), and the just-in-time unlock wizard offers AUTH_TOKEN/CT0 cookie entry ([#396](https://github.com/mvanhorn/last30days-skill/issues/396))
- `check-config.sh` no longer counts X as an active source when only `AUTH_TOKEN` is set without `CT0` — both cookies are now required to credit X in the source count ([#396](https://github.com/mvanhorn/last30days-skill/issues/396))
- Firefox cookie extraction now falls back to scanning non-default profiles when the default profile has no matching X cookies, fixing multi-profile setups where login lives on a non-default profile ([#498](https://github.com/mvanhorn/last30days-skill/issues/498))
- `subproc.py` `run_with_timeout()` now guards `os.killpg` / `os.getpgid` with `hasattr`, preventing an uncaught `AttributeError` crash when a subprocess times out on Windows where these functions don't exist ([#527](https://github.com/mvanhorn/last30days-skill/issues/527))
- Entity-grounding rerank demotion now keys on the head token of the primary entity instead of requiring the full multi-word phrase as a contiguous substring. A high-engagement on-entity item (e.g. a 323-pt HN thread titled "Stripe is friendly to 'friendly fraud'") is no longer demoted to score 0 on a `Stripe payments` query just because it lacks the trailing search-hint word. The intended demotion still fires for items that never name the brand at all. The keyless Reddit comment-enrichment slot selection (`_slot_priority`), which mirrors this signal, was updated to the same head-token grounding so the two paths stay consistent.
- `--plan` / `--competitors-plan` file reads now specify `encoding="utf-8"` and catch `UnicodeDecodeError`, preventing crashes on non-ASCII content like accented entity names on Windows (cp1252). `check_perms()` in `check-config.sh` now skips the POSIX 600-permission check on MSYS/MinGW/Cygwin where `stat` runs in noacl mode. `skill_meta.py` `read_skill_version()` now passes `encoding="utf-8"` so SKILL.md emoji doesn't break version detection on Windows. ([#549](https://github.com/mvanhorn/last30days-skill/issues/549))


## [3.3.2] - 2026-06-06

### Fixed

- YouTube transcript extraction now falls back through `en,es,pt` (configurable via `LAST30DAYS_YT_SUB_LANGS`) instead of English-only, so non-English videos with auto-captions in any of those three languages now contribute transcripts to the brief ([#469](https://github.com/mvanhorn/last30days-skill/issues/469))
- Keyless Reddit comment enrichment now spends its limited slots on entity-matching posts first (mirroring rerank's entity-miss demotion signal) instead of raw upvote order, so off-topic high-upvote threads from broad subreddits no longer consume the comment budget only to be demoted afterward ([#484](https://github.com/mvanhorn/last30days-skill/pull/484))

## [3.3.1] - 2026-05-30

### Fixed

- Removed the redundant `commands/last30days.md` wrapper so the plugin exposes only the skill ([#461](https://github.com/mvanhorn/last30days-skill/issues/461)). Previously the plugin shipped both a command wrapper and the skill under the same name, so `/last30` surfaced two `last30days` entries with two different descriptions. The skill already carries its own `argument-hint`, so the `/last30days <topic>` picker UX is unchanged.
- Corrected the README install note that claimed Claude Code dedupes the slash command across install methods; it does not, so having both the marketplace plugin and the `npx skills` copy active shows two entries.

## [3.3.0] - 2026-05-17

A week-long shipping cycle: ~75 PRs merged plus 7 community fixes salvaged through PR triage. Big themes: install story modernized for the multi-harness world (Claude Code, Codex, Cursor, Gemini CLI, Copilot, Windsurf, and 50+ Agent Skills hosts), new emit and source modes, and a substantial reliability sweep across Reddit, X, Windows, YouTube, and the planner.

### Added

**Emit modes and sources**

- `--emit=html` for shareable, print-friendly HTML research briefs ([#332](https://github.com/mvanhorn/last30days-skill/pull/332)).
- **Digg AI 1000 source**, auto-enabled when `digg-pp-cli` is on PATH ([#370](https://github.com/mvanhorn/last30days-skill/pull/370)). Surfaces curated story clusters from the AI 1000 leaderboard and pulls attributable X-post quotes into the brief.

**Configuration knobs**

- `EXCLUDE_SOURCES` env var — the inverse of `INCLUDE_SOURCES`, honored in source count and pipeline filter ([#399](https://github.com/mvanhorn/last30days-skill/pull/399)).
- `LAST30DAYS_YOUTUBE_SSH_HOST` — opt-in SSH routing for `yt-dlp` through a residential-IP host, for users on datacenter VPS hit by YouTube's bot-wall ([#376](https://github.com/mvanhorn/last30days-skill/pull/376)). Host validated against `^[a-zA-Z0-9._-]+$` to reject SSH option-injection. Transcript path unchanged (uses HTTP fallback).
- macOS Keychain as a credential source — reads from the system keychain when env vars and config files aren't set ([#407](https://github.com/mvanhorn/last30days-skill/pull/407)).
- Configuration enablement: env-var defaults and source-resilience patterns across the config layer ([#344](https://github.com/mvanhorn/last30days-skill/pull/344)).

**Pipeline and storage**

- Reddit URL auto-enrichment from web search via the public JSON API ([#366](https://github.com/mvanhorn/last30days-skill/pull/366)).
- Per-run finding sightings recorded in the SQLite store ([#373](https://github.com/mvanhorn/last30days-skill/pull/373)).
- Brave browser support for X/Twitter cookie extraction ([#320](https://github.com/mvanhorn/last30days-skill/pull/320)).

**Tests and CI**

- Full pytest suite restored to CI; 13 rotted tests repaired ([#416](https://github.com/mvanhorn/last30days-skill/pull/416)).
- `greptile.json` added with `triggerOnUpdates` + `statusCheck` ([#418](https://github.com/mvanhorn/last30days-skill/pull/418)).
- Advisory security workflow ([#368](https://github.com/mvanhorn/last30days-skill/pull/368)).
- Parallel grounding backend test coverage ([#355](https://github.com/mvanhorn/last30days-skill/pull/355)).

**Docs**

- New `CONFIGURATION.md` with README pointers ([#339](https://github.com/mvanhorn/last30days-skill/pull/339)).
- `docs/solutions/` learning capture for release-time consistency-test cascades ([#413](https://github.com/mvanhorn/last30days-skill/pull/413)) and the eval-not-in-CI design decision ([#417](https://github.com/mvanhorn/last30days-skill/pull/417)).

### Changed

**Install story modernized**

- `npx skills add` is now the canonical install path for every harness ([#405](https://github.com/mvanhorn/last30days-skill/pull/405)). README and SKILL.md flipped to recommend `npx skills add . -g -y` over per-harness manual instructions. Surfaces Gemini CLI, Copilot, Windsurf, and 50+ other Agent Skills hosts that the install pattern reaches.
- README dropped the Gemini CLI native-extension install path (now covered by `npx skills add`).
- `hooks.json` made polyglot for Gemini CLI + Claude Code compatibility ([#318](https://github.com/mvanhorn/last30days-skill/pull/318)).

**Skill semantics and multi-harness reframe**

- `AGENTS.md` is now canonical; `CLAUDE.md` points at it ([#410](https://github.com/mvanhorn/last30days-skill/pull/410)). Reframes the project as a multi-harness Agent Skills package rather than a Claude-Code-specific tool.
- SKILL.md path resolution rewritten: STEP 0 narrows to a Claude-Code-marketplaces-only stale-clone guard; Step 1 walks a single `SKILL_DIR` substitution pattern ([#400](https://github.com/mvanhorn/last30days-skill/pull/400), [#409](https://github.com/mvanhorn/last30days-skill/pull/409)). Removes ~80 lines of bash and fixes a real spec-vs-engine divergence where the previous resolver could pick a different install than the SKILL.md the model loaded from.
- SKILL.md version regex consolidated into `lib/skill_meta.py` ([#412](https://github.com/mvanhorn/last30days-skill/pull/412)).
- `--plan` / `--competitors-plan` invocation templates switched from inline single-quoted JSON to heredoc-written tmpfiles ([#404](https://github.com/mvanhorn/last30days-skill/pull/404), fixes [#403](https://github.com/mvanhorn/last30days-skill/issues/403)). Apostrophes in resolved context strings ("McDonald's", "people's choice") no longer break shell parsing.
- `POSTS_PER_CLUSTER` raised 3→5 and render-side display limit 2→3 to match the per-source enrichment caps used by Reddit, HN, YouTube, TikTok, and GitHub. The previous caps routinely truncated cluster context.
- Digg AI 1000 renamed to "Digg" in user-facing output ([#372](https://github.com/mvanhorn/last30days-skill/pull/372)) — footer line, source label, inline-quote suffix, why_relevant, container attribution. Internal references retain the upstream product name.
- GitHub repo resolution canonicalized for ambiguous product comparisons ([#302](https://github.com/mvanhorn/last30days-skill/pull/302)).

**Dependencies and tooling**

- Dropped `requests` runtime dependency. All providers route through stdlib `urllib` via the `lib/http` wrapper ([#393](https://github.com/mvanhorn/last30days-skill/pull/393)).
- Migrated to `gemini-3.1-flash-lite` GA model ([#378](https://github.com/mvanhorn/last30days-skill/pull/378)).
- Aligned Codex/Claude plugin manifests + added Codex `AGENTS.md` ([#321](https://github.com/mvanhorn/last30days-skill/pull/321)).
- pytest dev dep bumped 9.0.2 → 9.0.3 ([#414](https://github.com/mvanhorn/last30days-skill/pull/414)).

### Removed

- **BREAKING for Codex native-plugin users:** `.codex-plugin/plugin.json` and the matching SKILL_ROOT resolver branch in SKILL.md Step 1 ([#400](https://github.com/mvanhorn/last30days-skill/pull/400)). Codex users should install via `npx skills add mvanhorn/last30days-skill` or copy the skill to `~/.codex/skills/last30days/`.
- **`skills/last30days/scripts/sync.sh`** — maintainer dev-deploy script ([#405](https://github.com/mvanhorn/last30days-skill/pull/405)). Replaced by `npx skills add . -g -y` (live-symlink into every detected harness's skill dir — better than sync.sh's copy model since edits propagate live). Hermes uses `hermes skills install mvanhorn/last30days-skill --force`; OpenClaw uses `clawhub install last30days-official`.
- Orphaned `SPEC.md` and `TASKS.md` ([#419](https://github.com/mvanhorn/last30days-skill/pull/419)).

### Fixed

**Reddit**

- `lstrip("r/")` mangled subreddits starting with `r` (`r/robotics` → `obotics`, `r/ruby` → `uby`); replaced with `removeprefix("r/")` at 4 sites (Alex Key, salvaged from #288).
- Browser-like User-Agent + `Accept-Language`/`Accept-Encoding`/`Connection` headers + gzip decompression to fix `urllib` 403s on Reddit's public JSON endpoint (Franco Carballar, salvaged from #199).
- HTTP 402 re-raised across all three ScrapeCreators paths (`_global_search`, `_subreddit_search`, `fetch_post_comments`) so the OpenAI/public-JSON fallback chain triggers when credits are exhausted (Jonathan Oppenheim, salvaged from #170).

**Authentication and credentials**

- Restored multi-key rotation for `SCRAPECREATORS_API_KEY` accidentally dropped in v3.0.6 (Eric Oberhofer, salvaged from #287). Comma-separated keys round-robin via `random.choice` per run.

**Windows compatibility**

- `os.killpg` in `_cleanup_children()` guarded with `hasattr(os, "killpg")`, falls back to `os.kill(SIGTERM)` (gujishh, salvaged from #226).
- POSIX-style secret-permission warning skipped on Windows ([#357](https://github.com/mvanhorn/last30days-skill/pull/357)).
- Render uses forward slashes in save-path footer for Windows ([#338](https://github.com/mvanhorn/last30days-skill/pull/338)).

**xAI / X / xurl**

- `parse_x_response` now raises `http.HTTPError` on empty output, missing JSON, or decode failure — surfaces in `errors_by_source` instead of silently returning an empty result list (Kaustav Mishra, salvaged from #155).
- `xurl` treats `PermissionError` from PATH lookup as unavailable ([#322](https://github.com/mvanhorn/last30days-skill/pull/322)).

**YouTube**

- SC YouTube + multi-token HN searches unblocked ([#388](https://github.com/mvanhorn/last30days-skill/pull/388)).
- Transcript-fetch ratio surfaced + degraded-run nudge for stale `yt-dlp` ([#340](https://github.com/mvanhorn/last30days-skill/pull/340)).

**bird_x / HTTP**

- Subprocess retry on non-JSON stdout to handle X anti-bot HTML interstitials ([#383](https://github.com/mvanhorn/last30days-skill/pull/383)).
- HTTP retry budget expanded + exponential backoff on DNS resolution failure ([#382](https://github.com/mvanhorn/last30days-skill/pull/382)).
- Parallel AI search aligned with current API schema ([#341](https://github.com/mvanhorn/last30days-skill/pull/341)).
- Parallel web backend routed through grounding ([#354](https://github.com/mvanhorn/last30days-skill/pull/354)).

**Planner and sources**

- `xquik` registered in `SOURCE_CAPABILITIES` ([#336](https://github.com/mvanhorn/last30days-skill/pull/336), fixes [#319](https://github.com/mvanhorn/last30days-skill/issues/319)).
- Honor explicit optional source requests ([#356](https://github.com/mvanhorn/last30days-skill/pull/356)).
- ScrapeCreators source-gating aligned between code and docs ([#415](https://github.com/mvanhorn/last30days-skill/pull/415)).
- OpenClaw works without ScrapeCreators key ([#392](https://github.com/mvanhorn/last30days-skill/pull/392), by @thinkun).

**Render, version display, hosting paths**

- Hardcoded `v3.0.0` in render replaced with dynamic `_skill_version()` ([#365](https://github.com/mvanhorn/last30days-skill/pull/365)).
- Comparison HTML artifacts saved correctly ([#389](https://github.com/mvanhorn/last30days-skill/pull/389)).
- `OPENROUTER_DEFAULT` model ID corrected ([#323](https://github.com/mvanhorn/last30days-skill/pull/323)).
- OpenClaw poll-timing initialized once ([#358](https://github.com/mvanhorn/last30days-skill/pull/358)).
- Prefer sandboxed Safari cookie path ([#343](https://github.com/mvanhorn/last30days-skill/pull/343)).
- Preserve clean mode for last-run state ([#334](https://github.com/mvanhorn/last30days-skill/pull/334)).
- Replaced hardcoded `/Users/mvanhorn/...` paths in `test-v1-vs-v2.sh` with portable env-var overrides (Dave Morin, salvaged from #297).

**Hooks**

- `check-config.sh` path-quoting fix for paths with spaces ([#337](https://github.com/mvanhorn/last30days-skill/pull/337)).
- Replaced unsafe `eval` with `declare` in `check-config.sh` ([#364](https://github.com/mvanhorn/last30days-skill/pull/364)).

**Sync and version metadata**

- `sync.sh` pointed at this repo's plugin cache, not the private repo's ([#402](https://github.com/mvanhorn/last30days-skill/pull/402)).
- Sync cache target bumped to 3.2.1 to match SKILL.md ([#397](https://github.com/mvanhorn/last30days-skill/pull/397)).
- ScrapeCreators free-tier credit count corrected to 100 in docs ([#369](https://github.com/mvanhorn/last30days-skill/pull/369), fixes [#367](https://github.com/mvanhorn/last30days-skill/issues/367)).
- Gemini extension version synced ([#349](https://github.com/mvanhorn/last30days-skill/pull/349)).
- Various stale path/link fixes ([#345](https://github.com/mvanhorn/last30days-skill/pull/345), [#346](https://github.com/mvanhorn/last30days-skill/pull/346), [#347](https://github.com/mvanhorn/last30days-skill/pull/347), [#348](https://github.com/mvanhorn/last30days-skill/pull/348), [#351](https://github.com/mvanhorn/last30days-skill/pull/351)).

### Contributors

First-time contributors whose fixes shipped in this release (most via PR triage salvage — fix re-applied directly to main with co-author credit when path migration made the original branch un-rebaseable):

- Dave Morin — portable test-harness paths
- Alex Key — `removeprefix("r/")` for subreddit names
- Eric Oberhofer — multi-key rotation restored
- gujishh — Windows process cleanup
- Franco Carballar — Reddit browser-like headers
- Jonathan Oppenheim — Reddit 402 fallback chain
- Kaustav Mishra — xAI error surfacing
- [@thinkun](https://github.com/thinkun) ([#363](https://github.com/mvanhorn/last30days-skill/pull/363)) — OpenClaw ScrapeCreators-key-optional fix

Full PR list at [github.com/mvanhorn/last30days-skill/releases/tag/v3.3.0](https://github.com/mvanhorn/last30days-skill/releases/tag/v3.3.0).

## [3.2.0] - 2026-05-09

### Added

- Add `--emit=html` for shareable, print-friendly HTML research briefs.
- **Digg AI 1000 source** (auto-enabled when `digg-pp-cli` is on PATH). Surfaces curated story clusters from the AI 1000 leaderboard and pulls attributable X-post quotes into the brief as `[@handle](xUrl) via Digg AI 1000: ...` lines. Footer line: `⛏️ Digg AI 1000: N clusters │ K posts │ M authors`. No X auth required for the inline quotes since they flow through Digg's read-only endpoints.

## [3.1.1] - 2026-04-24

### Fixed

- **Codex plugin layout.** Move the canonical runtime payload under `skills/last30days/` and update Codex/Claude plugin metadata and tests for the relocated engine path.
- **Claude Code cache resolution.** Resolve Claude plugin installs to `skills/last30days/scripts/last30days.py` after the plugin-layout restructure.

## [3.1.0] - 2026-04-22

Consolidates the 3.0.10 to 3.0.14 dev cycle (commenter handles, `--competitors`, per-entity Step 0.55, vs-mode N passes, comparison title attribution) and republishes the OpenClaw bundle, which had been frozen on ClawHub at `3.0.0-open` since April 8.

### Added

- **OpenClaw republish.** `clawhub install last30days-official` now resolves to `3.1.0-open`, matching current main. Closes [#307](https://github.com/mvanhorn/last30days-skill/issues/307), [#195](https://github.com/mvanhorn/last30days-skill/issues/195), [#236](https://github.com/mvanhorn/last30days-skill/issues/236). The ClawHub bundle had shipped a broken `env.py get_config()` and stale SKILL.md path references since April; both are fixed at source on main and the republish carries the fixes to installers.

### Fixed

- **Claude Code plugin manifest path-escape.** The `.claude-plugin/plugin.json` `skills` key was removed in commit `93fbed2` but never shipped in a tagged release. Installing via `/plugin install last30days-skill` could hit `/doctor`'s `Path escapes plugin directory: ./ (skills)` error. This release ships the fix. Closes [#306](https://github.com/mvanhorn/last30days-skill/issues/306).
- **Broken README link.** The README's "source of truth" link pointed at root `SKILL.md`, which is no longer maintained after the plugin-layout restructure. Fixed to point at `skills/last30days/SKILL.md`.

### Dev cycle journal (3.0.10 - 3.0.14, not separately tagged)

Individual changelog entries for 3.0.10 through 3.0.14 below document the incremental work consolidated into this release.

## [3.0.14] - 2026-04-22

### Changed

- **Comparison-mode title attribution.** The synthesis title for vs-mode and `--competitors` outputs changes from `What the Community Says (Last 30 Days)` to `What the Community Says (/Last30Days)`. Surfaces the slash-command identity instead of restating the date range. Three SKILL.md occurrences updated; pure documentation change.

## [3.0.13] - 2026-04-22

### Changed

- **vs mode runs N full passes in parallel, one per entity.** Architectural revert of the 3-pass → 1-pass latency optimization from an earlier version. `/last30days "OpenAI vs Anthropic vs xAI"` now runs three full `pipeline.run()` calls in parallel via the same fanout `--competitors` uses, producing three `*-raw.md` save files plus a merged comparison output. Each entity gets its own Step 0.55-grade targeting, own primary X handle weight, own subreddit scoping — apples-to-apples depth instead of the one-pool merged retrieval the single-pass path produced. Parallel execution keeps wall clock ≈ single pass.
- **`--competitors` is now a SKILL.md-level shortcut for vs-mode with auto-discovery.** The hosting reasoning model (Claude Code, Codex, Hermes, Gemini, any agent with WebSearch) performs discovery and Step 0.55 per entity via its own WebSearch tool, then invokes the engine with a vs-topic and `--competitors-plan` JSON. The engine flag remains for headless/cron use with BRAVE/EXA/SERPER/PARALLEL/OPENROUTER keys (engine-internal `auto_resolve` stays as fallback).
- **LAW 7-style stderr for `--competitors` with no backend** now leads with the hosting-model path (WebSearch + Step 0.55 + `--competitors-plan`) instead of `BRAVE_API_KEY`. API-key framing moved to a secondary "headless" section.

### Added

- **`--competitors-plan` JSON flag** for per-entity Step 0.55 targeting. Schema: `{entity_name: {x_handle?, x_related?, subreddits?, github_user?, github_repos?, context?}}`. Accepts inline JSON or a file path (matches `--plan`). When present for an entity, skips engine-internal `auto_resolve` and uses the provided values; missing fields fall back to `auto_resolve` (if backend) or planner defaults. Case-insensitive entity matching. The `subrun_kwargs_for` helper is the single source of truth for per-entity kwargs — no closure-default fallthrough from main scope.
- **Per-entity save files** when `--save-dir` is set on a vs-mode or `--competitors` run. Each entity's sub-run produces its own `{slug}-raw.md` with a single-row Resolved Entities block — matches historical vs-mode behavior (N passes → N save files).
- **`--polymarket-keywords "kw1,kw2"`** to filter Polymarket matches for ambiguous single-token topics (e.g., "Warriors" → `nba,gsw,golden-state` kills Glasgow Warriors rugby and Honor of Kings Rogue Warriors noise).

### Fixed

- **BRAVE/SERPER footer nudge suppressed** when `--plan` or `--competitors-plan` is present. The nudge told Claude Code users to set an API key when they already have WebSearch via the hosting model. Nudge still fires for true headless runs (no `--plan`, no backend) where the advice is correct.
- **Override-leak regression testing.** 3.0.12 already fixed the main-topic `--subreddits` / `--x-handle` / `--github-*` from leaking into peer sub-runs via explicit per-entity kwargs scrubbing. This release adds a 4-test regression suite (`test_competitor_subrun_isolation.py`) locking in the invariant.

## [3.0.12] - 2026-04-22

### Fixed

- **Per-entity Step 0.55 resolution for competitor sub-runs.** In 3.0.11, only the main topic got X handle / subreddit / GitHub resolution; competitor sub-runs ran with planner defaults and produced visibly thinner evidence (Reddit 403 fallbacks, single-word queries). Each competitor sub-run now calls `resolve.auto_resolve()` inside `fanout.run_competitor_fanout` when a web backend is available, mirroring the main topic's pre-flight resolution. Per-entity X handle, subreddit list, GitHub user/repos, and news context are threaded into each sub-run's `pipeline.run()` call. Deep-copied config per sub-run prevents `_auto_resolve_context` cross-leak. Surfaces in a new `## Resolved Entities` output block so the resolution coverage is visible without reading stderr.
- **LAW 7 false-positive on internal fan-out sub-runs.** Each competitor sub-run was emitting the `[Planner] No --plan passed... YOU ARE the planner` stderr warning. LAW 7 targets the hosting-reasoning-model path, not engine-internal fan-out. New `internal_subrun=True` keyword on `planner.plan_query` and `pipeline.run` suppresses the warning for sub-runs only; the default path is unchanged.
- **Marketplace-stale SKILL.md trap.** Added a STEP 0 canonical-path self-check at the top of SKILL.md. Two of three 2026-04-22 test runs loaded SKILL.md from `plugins/marketplaces/last30days-skill/` (Claude-Code-managed git clone pinned to origin/main, lagging the versioned cache), then ran `--help` against the same stale path, did not see `--competitors`, and fell back to a manual comparison plan. The STEP 0 block forces any reader to verify they loaded from `plugins/cache/last30days-skill/last30days/{VERSION}/SKILL.md` and re-read from the versioned cache if not.

### Changed

- **Default `--competitors` count is now 2 (3-way total: original + 2 peers).** Previously 3. `--competitors=N` still customizes (range 1..6). Matches the feature description's canonical example (`Kanye vs Drake vs Kendrick`).

### Added

- **`## Resolved Entities` block** in `render_comparison_multi` output. Shows per-entity X handle, subreddits, GitHub user/repos, and truncated context for every entity in the comparison. Block is omitted entirely when no entity has a resolved payload (mock mode, no backend).

## [3.0.11] - 2026-04-22

### Added

- **`--competitors` flag for auto-discovered comparison fan-out.** Pass `--competitors` on a single-entity topic and the engine discovers 2-6 peer entities via web search, then runs the full pipeline on each in parallel and emits one N-way comparison. `last30days Kanye West --competitors` resolves Drake, Kendrick Lamar, and one more peer. `last30days OpenAI --competitors` resolves Anthropic, xAI, Google Gemini. `--competitors=N` controls count, `--competitors-list="A,B,C"` skips discovery and uses the explicit list. Discovery mirrors the `auto_resolve` pattern (Brave / Exa / Serper / Parallel) with deterministic text extraction - no internal LLM call. Sub-runs inherit the main `--quick`/`--deep`/`--days`, run in a `ThreadPoolExecutor`, and degrade gracefully when at least 2 entities survive. Output reuses the existing 9-axis `## Head-to-Head` scaffold.

## [3.0.10] - 2026-04-21

### Added

- **Commenter handles on evidence lines.** Top-comment rendering now includes the commenter's handle - `u/author` for Reddit, `@handle` for TikTok/YouTube/Instagram/Bluesky/X/Threads. The enrichment adapters already captured `author`; the render layer just was not using it. Evidence lines change from `- Comment (6822 upvotes): Finally, John Apple` to `- u/Cyrisaurus (6822 upvotes): Finally, John Apple`. Person-level citations make synthesis-side inline markdown links per LAW 8 much more natural. Both the compact and full render paths are covered.

### Fixed

- **TikTok author preference.** `_fetch_post_comments` in `scripts/lib/tiktok.py` preferred `user.nickname` over `user.unique_id`, so the engine captured display names ("Moosa Noormahomed") instead of @handles ("moosanoormahomed"). Flipped to prefer `unique_id`. Nickname still wins as a fallback when `unique_id` is missing. Display names can contain emoji, spaces, and non-Latin characters that do not round-trip to a profile URL; the @handle is the stable identifier.
- **Single plugin payload layout.** The canonical runtime moved to `skills/last30days/` for both Claude Code and Codex plugin loading. Root-level `SKILL.md`, `scripts/`, `agents/`, and `assets/` are no longer maintained as duplicate copies.

### Behavior fallback

- When an author is empty, `[deleted]`, or `[removed]`, the render falls back to the legacy `Comment (...)` shape - no `u/` or `@` prefix with an empty handle is ever emitted.

## [3.0.9] - 2026-04-18 - The Self-Debug Release

### Highlights

v3.0.9 adds the engine-side Class 1 keyword-trap refuse-gate ("birthday gift for 40 year old" now gets a clarifying question, not 5 minutes of junk), promotes TikTok and YouTube top comments to the same first-class rendering Reddit's got, lands Hermes AI Agent as a first-class deploy target, and moves the SKILL.md formatting contract from line 1094 to the top of the file.

"The Self-Debug Release" refers to how the fixes in 3.0.6-3.0.9 were written: 5 separate Opus 4.7 instances each debugged their own failed outputs. Three converged on "SKILL.md is too big and the LAWs are too deep." Two converged on "the engine should refuse demographic-shopping queries." I shipped exactly what they said. Validation: 5/5 canonical compliance.

### Added

- **Engine Class 1 keyword-trap refuse-gate** (`scripts/lib/preflight.py`, new). Pattern-matches demographic-shopping queries at main() front-door. Exit code 2 with structured REFUSE message. Escape hatch: `LAST30DAYS_SKIP_PREFLIGHT=1`. 29 tests in `tests/test_preflight.py`.
- **TikTok + YouTube top comments** rendered with same `💬 Top comment` prominence as Reddit's. Shipped in [#260](https://github.com/mvanhorn/last30days-skill/pull/260); enrichment fixed in [#265](https://github.com/mvanhorn/last30days-skill/pull/265).
- **Hermes AI Agent as a deploy target** - thanks @stephenmcconnachie ([#228](https://github.com/mvanhorn/last30days-skill/pull/228)). `scripts/sync.sh` detects `~/.hermes/skills/research` and deploys automatically.
- **Multi-key SCRAPECREATORS_API_KEY rotation** - thanks @zaydiscold ([#268](https://github.com/mvanhorn/last30days-skill/pull/268)). Set `SCRAPECREATORS_API_KEY_1`, `_2`, etc. Engine rotates on rate-limit.
- **Offline quality evaluation fixture** - thanks @j-sperling ([#233](https://github.com/mvanhorn/last30days-skill/pull/233)). `eval_topics.json` lets contributors run quality regressions without burning live API credits.
- **END-OF-CANONICAL-OUTPUT boundary** in `render_compact()`. Engine now emits an explicit pass-through instruction so re-synthesis requires actively ignoring a visible boundary.
- **LAW 1 verbatim-pattern override.** LAW 1 now quotes the exact WebSearch tool-result reminder ("CRITICAL REQUIREMENT: MUST include Sources: section") and declares it OVERRIDDEN inside last30days output.

### Changed

- **SKILL.md restructure.** VOICE CONTRACT LAWs and BADGE MANDATORY block moved from line 1094 to lines 75-150. Grounded in 3 separate Opus 4.7 self-debugs.
- **Engine emits the badge as stdout.** `🌐 last30days v3.0.9 · synced YYYY-MM-DD` is the first line of every compact emit. Pass-through is now the default-correct behavior.
- **Reddit client HTTP consolidation** - thanks @iliaal ([#207](https://github.com/mvanhorn/last30days-skill/pull/207)). Migrated to `http.get(params=...)` helper.
- **ScrapeCreators header consolidation** - thanks @iliaal ([#209](https://github.com/mvanhorn/last30days-skill/pull/209)). `_sc_headers` refactored into `http.scrapecreators_headers`.
- **Simpler Hermes sync.** `scripts/sync.sh` Hermes branch now always uses main SKILL.md (previously had a `.hermes-plugin/SKILL.md` fallback that created a wrong-file-capture hazard).

### Fixed

- **Peter Steinberger trailing Sources leak.** 2026-04-18 validation failure where the model appended a TechCrunch / TED / Fortune / Wikipedia Sources list after the invitation. Now structurally prevented at three layers: engine emits the canonical body, LAW 1 quotes the exact WebSearch reminder, closing boundary names the anti-pattern.
- **Wrong-file SKILL.md capture.** Deleted `.agents/skills/last30days/SKILL.md` (1382 lines, April 13 snapshot) and `.hermes-plugin/SKILL.md` (269 lines). One SKILL.md per plugin now, at the plugin root.
- **GitHub date parsing garbage** - thanks @iliaal ([#208](https://github.com/mvanhorn/last30days-skill/pull/208)). `_parse_date` now rejects invalid input cleanly.
- **Windows Bird X stability** - thanks @Chelebii ([#227](https://github.com/mvanhorn/last30days-skill/pull/227)).
- **Linux `check_perms` false-warn** - thanks @george231224 ([#216](https://github.com/mvanhorn/last30days-skill/pull/216)). Uses GNU stat first.
- **UTF-8 saved output** - thanks @Gujiassh ([#225](https://github.com/mvanhorn/last30days-skill/pull/225)).
- **Version metadata alignment** - thanks @Gujiassh ([#217](https://github.com/mvanhorn/last30days-skill/pull/217)) and @shalomma ([#229](https://github.com/mvanhorn/last30days-skill/pull/229)).
- **`--days` alias backcompat** - thanks @BryanTegomoh ([#230](https://github.com/mvanhorn/last30days-skill/pull/230)).
- **`INCLUDE_SOURCES` env default** - thanks @hnshah ([#223](https://github.com/mvanhorn/last30days-skill/pull/223)).
- **Bird X all-None engagement** - thanks @j-sperling ([#234](https://github.com/mvanhorn/last30days-skill/pull/234)).

### Contributors

@j-sperling, @stephenmcconnachie, @zaydiscold, @iliaal, @Chelebii, @Gujiassh, @hnshah, @george231224, @shalomma, @BryanTegomoh for PRs since v3.0.0. @uppinote20, @zerone0x, @thinkun, @thomasmktong, @fanispoulinakisai-boop, @pejmanjohn, @zl190, @Jah-yee, @dannyshmueli, @Cody-Coyote for issues and PRs that shaped the v3 roadmap.

### Recovery

```
/plugin update last30days
/reload-plugins
```

Verify: `cat ~/.claude/plugins/cache/last30days-skill/last30days/*/.claude-plugin/plugin.json | grep version` returns `"version": "3.0.9"`.

Smoke test: `/last30days birthday gift for 40 year old` should ask a clarifying question before running.

## [3.0.5] - 2026-04-15

### Added

- **`/last30days` slash command for plugin users.** New `commands/last30days.md` registers a Claude Code slash command. Users type `/last30days <topic>` and Claude Code's autocomplete prefix-matches it to the canonical `/last30days:last30days` form (the same way `/ce:plan` resolves to `/compound-engineering:ce-plan`). The command delegates to the existing `last30days` skill body — no skill behavior changes.

### Removed

- **`skills/last30days-nux/`** — byte-identical duplicate of root `SKILL.md` that created confusing `/last30days:last30days-nux` autocomplete entries via Claude Code's plugin namespacing. The root `SKILL.md` remains the canonical skill source.

### Recovery

```
/plugin update last30days
/reload-plugins
```

Then type `/last30days <topic>` to invoke the skill via slash command. Natural-language invocation ("search the last 30 days for X") continues to work unchanged.

## [3.0.4] - 2026-04-15

### Fixed

- **Cleared `/doctor` path-escape error on Claude Code v2.1.109+.** `.claude-plugin/plugin.json` previously declared `"skills": ["./"]`. That value shipped unchanged from v2.1.0 through v3.0.3 and worked on older Claude Code, but current versions reject `./` with `Path escapes plugin directory: ./ (skills)`. The `"skills"` key is now omitted entirely, matching the pattern used by every other plugin in the Claude Code marketplace ecosystem. Claude Code auto-discovers `skills/*/SKILL.md` when the key is absent.

### Recovery

If `/doctor` reports a path-escape error for last30days, run `/plugin update last30days` then `/reload-plugins`. If errors persist, uninstall and reinstall the plugin.

## [3.0.3] - 2026-04-15

### Fixed

- **Restored `skills/` and `.claude-plugin/` to the plugin install tarball.** v3.0.1 added `.gitattributes` rules that excluded both directories from `git archive` output to shrink the claude.ai `.skill` bundle. Claude Code's `/plugin install` fetches the same archive, so users installing v3.0.1 or v3.0.2 received a tarball with no plugin manifest and no skill files. `git archive v3.0.0` contained 8 files under those paths; `v3.0.1` and `v3.0.2` contained 0. This release reverts those `.gitattributes` lines.
- **Reverted `plugin.json` `"skills"` field to `["./"]`.** v3.0.2 changed this to `["skills"]` based on a misdiagnosis — the manifest change had no effect because the manifest wasn't in the tarball at all. The historical `["./"]` value shipped in every release from v2.1.0 through v3.0.0 without issues and is restored here.

### Recovery

Users on v3.0.1 or v3.0.2: run `/plugin update last30days` then `/reload-plugins`. If autoUpdate is enabled, the next session start will pull v3.0.3 automatically. Users on cached v3.0.0 or earlier installs were unaffected.

### Notes

- The claude.ai `.skill` bundle built by `scripts/build-skill.sh` still works — the archive grew from 89 to 97 files, well under the 200-file cap.
- claude.ai-specific exclusions (avoiding duplicate `SKILL.md` files in the bundle) should move into `scripts/build-skill.sh` rather than `.gitattributes` in a future release, since `.gitattributes` cannot distinguish between the two distribution channels.

## [3.0.2] - 2026-04-15

### Fixed

- **`/last30days` slash command now registers on Claude Code v2.1.105+.** `.claude-plugin/plugin.json` declared `"skills": ["./"]`, which newer Claude Code rejects with `Path escapes plugin directory: ./ (skills)`. The skill silently failed to register, so `/last30days <query>` returned "Unknown command" even though `/plugin list` showed the plugin as installed. Fix: `"skills": ["skills"]` so the loader scans the real skill subdirectory.
- **Version drift between manifests.** `.claude-plugin/marketplace.json` was pinned to `3.0.0` while `.claude-plugin/plugin.json` advertised `3.0.1`. The `/plugin` resolver used the marketplace version and could install stale cached metadata alongside the correct build. Both manifests now agree on `3.0.2`.

### Recovery

If `/last30days` stopped working for you, run `/plugin update last30days` then `/reload-plugins`. If `/doctor` still reports errors, uninstall and reinstall the plugin from the marketplace.

## [3.0.1] - 2026-04-14

### Fixed

- **Skill upload packaging** - `scripts/build-skill.sh` produces a claude.ai-upload-ready `.skill` file that fits under the 200-file cap. Previously, zipping the repo hit 406 files and the "Upload skill" UI rejected it outright.
- **SKILL.md description length** - trimmed from 228 to 167 chars (Anthropic caps descriptions at 200).

### Removed

- Unused root `vendor/` directory (215 files from an accidental commit in PR #48 - the real vendored X client lives at `scripts/lib/vendor/bird-search/`).
- Legacy top-level `plans/` directory (superseded by `docs/plans/`; both plans described work that was already shipped in v3).

### Added

- `.gitattributes` with `export-ignore` entries so `git archive` drops tests, docs, fixtures, assets, historical manifests, and internal skill subdirs. Mirrors Anthropic's canonical `package_skill.py` exclusions.
- `scripts/build-skill.sh` - one-command path to produce `dist/last30days.skill` with a single top-level `last30days/` folder, defensive `=200` file check, and dirty-tree refusal.
- `README.md` section documenting the claude.ai skill upload workflow.

## [3.0.0] - 2026-04-11

### Highlights

Intelligent search, fun judge, cross-source cluster merging, single-pass comparisons, and OpenClaw as a first-class citizen. The v3 engine doesn't just search for your topic -- it figures out *where* to search before the search begins. Engine architecture by @j-sperling.

### Added

- **Intelligent pre-research** -- Resolves X handles, subreddits, TikTok hashtags, and YouTube channels via a new Python brain before any API calls fire. Bidirectional: person to company, product to founder.
- **Fun judge / Best Takes** -- Second parallel LLM judge scores humor, cleverness, and virality. Surfaces the best reactions in a dedicated output section.
- **Cross-source cluster merging** -- Entity-based overlap detection merges the same story across Reddit, X, YouTube into one cluster instead of three separate items.
- **Single-pass comparisons** -- "X vs Y" runs one pass with entity-aware subqueries instead of three serial passes. 3 minutes instead of 12+.
- **GitHub as a source** -- Stars, reactions, and comments from repos and issues.
- **OpenClaw first-class citizen** -- Auto-resolve for engine-side pre-research. Device auth for frictionless ScrapeCreators signup.
- **Per-author cap** -- Max 3 items per author prevents single-voice dominance.
- **Entity disambiguation** -- Synthesis trusts resolved handles over keyword matches.
- **Perplexity Sonar Pro as additive source** -- AI-synthesized research with citations via OpenRouter. Opt-in via `INCLUDE_SOURCES=perplexity`. Returns structured narratives that complement social data.
- **Perplexity Deep Research** -- `--deep-research` flag for exhaustive 50+ citation reports (~$0.90/query). Premium opt-in for serious investigation.
- **OpenRouter as reasoning provider** -- One OPENROUTER_API_KEY powers planning, reranking, and Perplexity search. Auto-detected after Gemini/OpenAI/xAI.
- **Parallel AI grounding backend** -- `--web-backend parallel` or auto-detected via PARALLEL_API_KEY.
- **Grounding in planner** -- Grounding source properly registered in SOURCE_CAPABILITIES instead of force-injected.

### Changed

- YouTube transcript candidate pool widened 3x past music videos to reach talk/review content with captions
- Reddit comment enrichment sorted by total engagement (upvotes + comments), not just upvotes
- Polymarket display shows % odds only; dollar volumes removed
- 852 tests passing

### Fixed

- Marketplace validation: duplicate `name: last30days` collision in `skills/last30days/SKILL.md` caused strict validators to reject the plugin. Resolved by renaming the internal v3 architecture spec to `last30days-v3-spec` with `user-invocable: false`. Fixed in #214 (reported by @Cody-Coyote in #204).
- Stale README link to the deleted `skills/last30days-v3/` path from the v3 directory rename. Fixed in #214.
- OpenAI Codex CLI discoverability: added `.agents/skills/last30days/SKILL.md` as a real file (Codex's loader skips symlinked files) plus `.codex-plugin/plugin.json` as the namespace marker. The skill now registers as `last30days:last30days` when Codex runs in a checkout of the repo. Fixed in #219 (inspired by @Jah-yee in #153 and @dannyshmueli on X).

### Contributors

- @j-sperling -- v3 engine architecture, Python pre-research brain
- @hnshah -- Watchlist features
- @Cody-Coyote -- Marketplace validation bug report (#204)
- @Jah-yee -- Codex CLI integration inspiration (#153)

## [2.9.4] - 2026-03-06

### Changed

- Move save into Python script via `--save-dir` flag - raw research data saved during the existing script Bash call, zero extra tool calls after invitation
- Remove entire "Save Research to Documents" section from SKILL.md (~45 lines removed)
- No more `📎` footer, no Bash heredoc, no `(No output)`, no multi-minute cogitation after research

## [2.9.3] - 2026-03-06

### Fixed

- **Critical:** Switch save from `run_in_background` to foreground Bash - background callbacks caused model to re-engage, hallucinate fake user messages, and generate unsolicited multi-paragraph responses
- Save uses foreground `cat >` heredoc (executes sub-second, no callback, no delayed notification)

## [2.9.2] - 2026-03-06

### Fixed

- Save research silently using background Bash heredoc instead of Write tool (eliminates "Wrote N lines..." clutter)
- Suppress follow-up text after background save completes (no more "Research briefing saved..." noise)
- Add `📎` footer line for save path instead of verbose confirmation

## [2.9.1] - 2026-03-05

### Highlights

Auto-save research briefings to the default memory directory as topic-named .md files. Every run now builds a personal research library automatically - no more manual copy-paste.

### Added

- Auto-save complete research briefings (synthesis, stats, follow-up suggestions) to the default memory directory after every run
- Kebab-case filename generation from topic (e.g., "Claude Code skills" -> `claude-code-skills.md`)
- Duplicate topic handling: appends date suffix instead of overwriting (e.g., `claude-code-skills-2026-03-05.md`)
- Agent mode (`--agent`) also saves research files
- Brief confirmation after save with the saved file path

### Credits

- [@devin_explores](https://x.com/devin_explores) -- Inspired this feature by sharing their workflow of saving every last30days run into organized .md files ([PR #51](https://github.com/mvanhorn/last30days-skill/pull/51))

## [2.9.0] - 2026-03-05

### Highlights

ScrapeCreators Reddit as the default backend (one `SCRAPECREATORS_API_KEY` covers Reddit + TikTok + Instagram), smart subreddit discovery with relevance-weighted scoring, and top comments elevated with 10% scoring weight and prominent display.

### Added

- ScrapeCreators Reddit backend (`scripts/lib/reddit.py`) — keyword search, subreddit discovery, comment enrichment, all via `api.scrapecreators.com`
- Smart subreddit discovery with relevance-weighted scoring: frequency × recency × topic-word match, replacing pure frequency count
- `UTILITY_SUBS` blocklist to filter noise subreddits (r/tipofmytongue, r/whatisthisthing, etc.) from discovery results
- Top comment scoring: 10% weight in engagement formula via `log1p(top_comment_score)`
- Top comment rendering: `💬 Top comment` lines with upvote counts in compact and full report output
- Comment excerpt length increased from 300 → 400 chars; `comment_insights` limit raised from 7 → 10

### Changed

- `primaryEnv` switched from `OPENAI_API_KEY` to `SCRAPECREATORS_API_KEY` — one key now powers Reddit, TikTok, and Instagram
- Reddit engagement scoring formula: `0.55/0.40/0.05` (score/comments/ratio) → `0.50/0.35/0.05/0.10` (score/comments/ratio/top-comment)
- SKILL.md synthesis instructions updated to emphasize quoting top comments

### Fixed

- Utility subreddit noise in discovery (e.g., r/tipofmytongue appearing for unrelated topics)
- Reddit search no longer requires `OPENAI_API_KEY` — ScrapeCreators API handles search directly

## [2.8.0] - 2026-03-04

### Highlights

Instagram Reels as the 8th signal source, TikTok migrated from Apify to ScrapeCreators API, and SKILL.md quality improvements. One API key (`SCRAPECREATORS_API_KEY`) now covers both TikTok and Instagram.

### Added

- Instagram Reels as 8th research source via ScrapeCreators API — keyword search, engagement metrics (views, likes, comments), spoken-word transcript extraction (`scripts/lib/instagram.py`)
- `InstagramItem` dataclass, normalization, scoring (45% relevance / 25% recency / 30% engagement), deduplication, cross-source linking, and rendering
- Instagram in SKILL.md: stats template (`📸 Instagram:`), citation priority, item format description, output footer
- URL-to-name extraction examples in SKILL.md for cleaner web source display
- `--search=instagram` flag support

### Changed

- TikTok backend migrated from Apify to ScrapeCreators API (`api.scrapecreators.com`)
- `APIFY_API_TOKEN` replaced by `SCRAPECREATORS_API_KEY` in config
- SKILL.md version bumped to v2.8
- WebSearch citation instruction strengthened to prevent trailing Sources: blocks
- Security section updated: Apify → ScrapeCreators references

### Fixed

- Web stats line showing full URLs instead of plain domain names
- Trailing "Sources:" block appearing after skill invitation (WebSearch tool mandate conflict)
- Instagram/TikTok not running in web-only mode when `--search=instagram` used without Reddit/X
- `$ARGUMENTS` quoting in SKILL.md for correct flag forwarding

## [2.1.0] - 2026-02-15

### Highlights

Three headline features: watchlists for always-on bots, YouTube transcripts as a 4th source, and Codex CLI compatibility. Plus bundled X search with no external CLI needed.

### Added

- Open-class skill with watchlists, briefings, and history modes (SQLite-backed, FTS5 full-text search, WAL mode) (`feat(open)`)
- YouTube as a 4th research source via yt-dlp -- search, view counts, and auto-generated transcript extraction (`feat: Add YouTube`)
- OpenAI Codex CLI compatibility -- install to `~/.agents/skills/last30days`, invoke with `$last30days` (`feat: Add Codex CLI`)
- Bundled X search -- vendored subset of Bird's Twitter GraphQL client (MIT, originally by @steipete), no external CLI needed (`v2.1: Bundle Bird X search`)
- Native web search backends: Parallel AI, Brave Search, OpenRouter/Perplexity Sonar Pro (`feat(engine)`)
- `--diagnose` flag for checking available sources and authentication status
- `--store` flag for SQLite accumulation (open variant)
- Conversational first-run experience (NUX) with dynamic source status (`feat(nux)`)

### Changed

- Smarter query construction -- strips noise words, auto-retries with shorter queries when X returns 0 results
- Two-phase search architecture -- Phase 1 discovers entities (@handles, r/subreddits), Phase 2 drills into them
- Reddit JSON enrichment -- real upvotes, comments, and upvote ratio from reddit.com/.json endpoint
- Engagement-weighted scoring: relevance 45%, recency 25%, engagement 30% (log1p dampening)
- Model auto-selection with 7-day cache and fallback chain (gpt-4.1 -> gpt-4o -> gpt-4o-mini)
- `--days=N` configurable lookback flag (thanks @jonthebeef, [#18](https://github.com/mvanhorn/last30days-skill/pull/18))
- Model fallback for unverified orgs (thanks @levineam, [#16](https://github.com/mvanhorn/last30days-skill/pull/16))
- Marketplace plugin support via `.claude-plugin/plugin.json` (inspired by @galligan, [#1](https://github.com/mvanhorn/last30days-skill/pull/1))

### Fixed

- YouTube timeout increased to 90s, Reddit 429 rate limit fail-fast
- YouTube soft date filter -- keeps evergreen content instead of filtering to 0 results
- Eager import crash in `__init__.py` that broke Codex environments
- Reddit future timeout (same pattern as YouTube timeout bug)
- Process cleanup on timeout/kill -- tracks child PIDs for clean shutdown
- Windows Unicode fix for cp1252 emoji crash (thanks @JosephOIbrahim, [#17](https://github.com/mvanhorn/last30days-skill/pull/17))
- X search returning 0 results on popular topics due to over-specific queries

### New Contributors

- @JosephOIbrahim -- Windows Unicode fix ([#17](https://github.com/mvanhorn/last30days-skill/pull/17))
- @levineam -- Model fallback for unverified orgs ([#16](https://github.com/mvanhorn/last30days-skill/pull/16))
- @jonthebeef -- `--days=N` configurable lookback ([#18](https://github.com/mvanhorn/last30days-skill/pull/18))

### Credits

- @galligan -- Marketplace plugin inspiration
- @hutchins -- Pushed for YouTube feature

## [1.0.0] - 2026-01-15

Initial public release. Reddit + X search via OpenAI Responses API and xAI API.

[3.0.9]: https://github.com/mvanhorn/last30days-skill/compare/v3.0.5...v3.0.9
[2.9.1]: https://github.com/mvanhorn/last30days-skill/compare/v2.9.0...v2.9.1
[2.9.0]: https://github.com/mvanhorn/last30days-skill/compare/v2.8.0...v2.9.0
[2.8.0]: https://github.com/mvanhorn/last30days-skill/compare/v2.6.0...v2.8.0
[2.1.0]: https://github.com/mvanhorn/last30days-skill/compare/v1.0.0...v2.1.0
[1.0.0]: https://github.com/mvanhorn/last30days-skill/releases/tag/v1.0.0
