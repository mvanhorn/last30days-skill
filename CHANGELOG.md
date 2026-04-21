# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.10] - 2026-04-21

### Added

- **Commenter handles on evidence lines.** Top-comment rendering now includes the commenter's handle - `u/author` for Reddit, `@handle` for TikTok/YouTube/Instagram/Bluesky/X/Threads. The enrichment adapters already captured `author`; the render layer just was not using it. Evidence lines change from `- Comment (6822 upvotes): Finally, John Apple` to `- u/Cyrisaurus (6822 upvotes): Finally, John Apple`. Person-level citations make synthesis-side inline markdown links per LAW 8 much more natural. Both the compact and full render paths are covered.

### Fixed

- **TikTok author preference.** `_fetch_post_comments` in `scripts/lib/tiktok.py` preferred `user.nickname` over `user.unique_id`, so the engine captured display names ("Moosa Noormahomed") instead of @handles ("moosanoormahomed"). Flipped to prefer `unique_id`. Nickname still wins as a fallback when `unique_id` is missing. Display names can contain emoji, spaces, and non-Latin characters that do not round-trip to a profile URL; the @handle is the stable identifier.

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
