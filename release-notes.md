The AI world reinvents itself every month. This skill keeps you current.

`/last30days` researches your topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, GitHub, and 5+ more sources from the last 30 days, finds what the community is actually upvoting, sharing, betting on, and saying on camera, and writes you a grounded narrative with real citations.

## v3 is the intelligent search release

v3 is a ground-up engine rewrite by [@j-sperling](https://github.com/j-sperling). The old engine searched keywords. The new engine understands your topic first, then searches the right people and communities.

Type "OpenClaw" and v3 resolves @steipete, r/openclaw, r/ClaudeCode, and the right YouTube channels and TikTok hashtags before a single API call fires. Type "Peter Steinberger" and it resolves his X handle and GitHub profile, switches to person mode, and shows what he shipped this month at 85% merge rate across 22 PRs. None of that was on Google.

## Headline features

### Intelligent pre-research

The killer feature. A new Python pre-research brain resolves X handles, GitHub repos, subreddits, TikTok hashtags, and YouTube channels before searching. Bidirectional: person to company, product to founder, name to GitHub profile. The right subreddits, the right handles, the right hashtags, all resolved before a single API call.

### Best Takes

A second LLM judge scores every result for humor, wit, and virality alongside relevance. Every brief now ends with a Best Takes section surfacing the cleverest one-liners and most viral quotes. The Reddit and X people are funny, and the old engine buried their best stuff.

### Cross-source cluster merging

When the same story hits Reddit, X, and YouTube, v3 merges them into one cluster instead of three duplicates. Entity-based overlap detection catches matches even when the titles use different words.

### Single-pass comparisons

"X vs Y" used to run three serial passes (12+ minutes). v3 runs one pass with entity-aware subqueries for both sides at once. Same depth, 3 minutes.

### GitHub person-mode and project-mode

When the topic is a person, the engine switches from keyword search to author-scoped queries. PR velocity, top repos by stars, release notes for what shipped this month, woven into the narrative alongside X posts and Reddit threads.

When the topic is a project, it pulls live star counts, READMEs, releases, and top issues from the GitHub API. No stale blog posts.

### ELI5 mode

Say "eli5 on" after any research run. The synthesis rewrites in plain language. No jargon. Same data, same sources, same citations, just clearer. Say "eli5 off" to go back.

### 13+ sources

v3 adds Threads, Pinterest, Perplexity, Bluesky, and Parallel AI grounding to the existing Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, GitHub, and Web lineup. Perplexity Deep Research (`--deep-research`) gives you 50+ citation reports for serious investigation.

### Per-author cap and entity disambiguation

Max 3 items per author prevents single-voice dominance. Synthesis trusts resolved handles over fuzzy keyword matches.

## Install

Claude Code:

```
/plugin marketplace add mvanhorn/last30days-skill
```

OpenClaw:

```
clawhub install last30days-official
```

OpenAI Codex CLI: run `codex` from a checkout of this repo and v3's skill at `.agents/skills/last30days/SKILL.md` will be discovered automatically. Or copy `SKILL.md` to `~/.agents/skills/last30days/SKILL.md` for a global install.

Zero config. Reddit, Hacker News, Polymarket, and GitHub work immediately. Run it once and the setup wizard unlocks X, YouTube, TikTok, and more in 30 seconds.

## v3 Community

v3 was shaped by community contributors whose PRs and issues inspired core features. Their code wasn't merged directly (v3 was a ground-up rewrite), but their ideas drove what shipped.

Thanks to @uppinote20, @zerone0x, @thinkun, @thomasmktong, @fanispoulinakisai-boop, @pejmanjohn, @zl190, and @hnshah. See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list.

Contributors who shaped the release itself:

- @Jah-yee (#153) surfaced the need for a real Codex CLI integration, which shipped in #219
- @Cody-Coyote (#204) reported the marketplace validation bug that needed fixing before v3 could ship cleanly
- @dannyshmueli pushed for v3 and Codex family support publicly on X

## What's New

### Added

- Intelligent pre-research brain resolving X handles, subreddits, TikTok hashtags, and YouTube channels before searching
- Fun judge and Best Takes section scoring humor, wit, and virality
- Cross-source cluster merging with entity-based overlap detection
- Single-pass comparisons for "X vs Y" queries
- GitHub as a first-class source with person-mode and project-mode
- Perplexity Sonar Pro via OpenRouter (`INCLUDE_SOURCES=perplexity`)
- Perplexity Deep Research (`--deep-research` flag)
- Parallel AI grounding backend (`--web-backend parallel`)
- OpenRouter as a reasoning provider (auto-detected after Gemini / OpenAI / xAI)
- Per-author cap (max 3 items per author)
- Entity disambiguation trusting resolved handles over keyword matches
- OpenAI Codex CLI integration via `.agents/skills/last30days/SKILL.md` and `.codex-plugin/plugin.json`
- ELI5 mode

### Changed

- YouTube transcript candidate pool widened 3x to reach talk and review content with captions
- Reddit comment enrichment sorted by total engagement (upvotes + comments), not just upvotes
- Polymarket display shows % odds only, dollar volumes removed
- 852 tests passing

### Fixed

- Marketplace validation: duplicate `name: last30days` collision in `skills/last30days/SKILL.md` that caused strict validators to reject the plugin. Resolved by renaming the internal v3 architecture spec to `last30days-v3-spec` in #214
- Stale README link to the deleted `skills/last30days-v3/` path from the v3 directory rename. Fixed in #214
- Codex CLI discovery: added the real `.agents/skills/last30days/SKILL.md` (regular file, not a symlink, since Codex's loader skips symlinked files) and `.codex-plugin/plugin.json` namespace marker in #219

## Credits

- [@steipete](https://github.com/steipete) for Bird CLI (vendored X search) and yt-dlp/summarize inspiration for YouTube transcripts
- [@galligan](https://github.com/galligan) for marketplace plugin inspiration
- [@hutchins](https://x.com/hutchins) for pushing the YouTube feature

30 days of research. 30 seconds of work. Thirteen sources. Zero stale prompts.
