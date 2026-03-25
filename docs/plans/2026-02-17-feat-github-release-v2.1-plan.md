---
title: "feat: Create GitHub Release for v2.1"
type: feat
date: 2026-02-17
---

# Create GitHub Release for v2.1

## Overview

The last30days skill is at v2.1 with 2,747 stars and 317 forks, but has **zero git tags and zero GitHub Releases**. The code is already live on the public repo (`upstream/main`). All marketing copy is written. This plan creates a proper v2.1.0 GitHub Release from existing materials.

## Problem Statement

GitHub Releases provide:
- A landing page for each version with formatted release notes
- Discoverability (GitHub shows releases in the sidebar, feeds them to the Explore page)
- A stable reference point for users (`git checkout v2.1.0`)
- A "Full Changelog" diff link
- RSS feed for watchers
- New contributor callouts (community goodwill)

Currently users have no way to reference a specific version of the skill. The README says "v2.1" but there's nothing in git to anchor that.

## Proposed Solution

Create the first GitHub Release (v2.1.0) on the **public** repo using existing marketing copy. Use an annotated tag (not lightweight) for proper `git describe` support and fork propagation.

## Implementation Steps

### Phase 1: Tag and Release

#### 1. Create CHANGELOG.md

Create `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Source material already exists:

- `README.md` "What's New in V2.1" and "What's New in V2" sections
- `docs/v2.1-tweets.md` (raw verified test results, feature descriptions)
- `docs/v2.1-launch-copy.md` (feature copy, social posts)
- `docs/pr-credits.md` (contributor credits)
- Git log (`git log --oneline` for commit references)

Structure:
```markdown
# Changelog

## [2.1.0] - 2026-02-15

### Highlights

30 days of research. 30 seconds of work. Four sources. Zero stale prompts.

Three headline features...

### Added
- Open-class skill with watchlists (SQLite-backed, FTS5)
- YouTube as 4th research source via yt-dlp (search + transcript extraction)
- OpenAI Codex CLI compatibility ($last30days invocation)
- Bundled X search (vendored Bird GraphQL client, no external CLI)
- Native web search backends (Parallel AI, Brave, OpenRouter/Perplexity Sonar)
- Briefing and history modes (open variant)
- --diagnose flag for source status checking
- --store flag for SQLite accumulation

### Changed
- Smarter query construction (strips noise words, auto-retry)
- Two-phase search (Phase 2 entity-aware drill-down)
- Reddit JSON enrichment (real upvotes/comments from reddit.com/.json)
- Engagement-weighted scoring (relevance 45%, recency 25%, engagement 30%)
- Model auto-selection with 7-day cache

### Fixed
- YouTube timeout increased to 90s
- Reddit 429 rate limit fail-fast
- YouTube soft date filter (keeps evergreen content)
- Eager import crash in __init__.py (Codex compatibility)

### New Contributors
- @JosephOIbrahim - Windows Unicode fix
- @levineam - Model fallback for unverified orgs
- @jonthebeef - --days=N configurable lookback flag

### Credits
- @steipete - Bird CLI (vendored X search) and yt-dlp inspiration
- @galligan - Marketplace plugin inspiration
- @hutchins - Pushed for YouTube feature

## [1.0.0] - 2026-01-15

Initial public release. Reddit + X search via OpenAI and xAI APIs.
```

#### 2. Create annotated tag on the public repo

```bash
cd $HOME/last30days-skill-private

# Tag on the current HEAD (which matches upstream/main)
git tag -a v2.1.0 -m "Release v2.1.0: Watchlists, YouTube transcripts, Codex CLI, bundled X search"

# Push to public repo
git push upstream v2.1.0
```

Use annotated tag (not lightweight) because:
- Stores tagger metadata and date
- Works with `git describe`
- Propagates to forks (317 forks)
- Supports GPG signing if desired later

#### 3. Craft release notes

Assemble from existing copy. The release body should follow this structure:

```markdown
## Highlights

The AI world reinvents itself every month. This skill keeps you current.

`/last30days` researches your topic across **Reddit, X, YouTube, and the web**
from the last 30 days, finds what the community is actually upvoting, sharing,
and saying on camera, and writes you a prompt that works today.

**Three headline features in v2.1:**

1. **Open-class skill with watchlists** - Track competitors, people, topics on a schedule.
   Pair with Open Claw for automated briefings. SQLite-backed with FTS5 search.

2. **YouTube transcripts as a 4th source** - When yt-dlp is installed, searches YouTube,
   grabs view counts, and reads the actual transcripts. A 20-minute review has 10x the
   signal of one X post.

3. **Codex CLI compatibility** - Same skill, same engine, same four sources.
   Install to `~/.agents/skills/last30days` and invoke with `$last30days`.

Plus: **Bundled X search** - Vendored Bird GraphQL client. No external CLI needed.
Just Node.js 22+ and browser cookies.

## Real Results (verified 2/15)

| Topic | Reddit | X | YouTube | Web |
|-------|--------|---|---------|-----|
| Nano Banana Pro | - | 32 posts, 164 likes | 5 videos, 98K views | - |
| Seedance 2.0 | 21 threads | 33 posts | 20 videos | 5 pages |
| OpenClaw use cases | 35 threads, 1,130 upvotes | 23 posts | 20 videos, 1.57M views | - |
| YouTube thumbnails | 7 threads, 654 upvotes | 32 posts | 18 videos, 6.15M views | - |

## What's New

### Added
- Open-class skill with watchlist, briefing, and history modes
- YouTube search + transcript extraction via yt-dlp
- OpenAI Codex CLI compatibility
- Bundled Twitter/X search (vendored Bird GraphQL)
- Native web search (Parallel AI, Brave, OpenRouter)
- `--diagnose` and `--store` flags
- Conversational first-run experience (NUX)

### Changed
- Two-phase search architecture (entity-aware drill-down)
- Reddit JSON enrichment for real engagement metrics
- Smarter query construction with auto-retry
- Engagement-weighted scoring algorithm

### Fixed
- YouTube/Reddit timeout resilience
- Reddit 429 rate limit fail-fast
- Eager import crash in Codex environments

## New Contributors

- @JosephOIbrahim - Windows Unicode fix
- @levineam - Model fallback for unverified orgs
- @jonthebeef - `--days=N` configurable lookback

## Credits

- @steipete - Bird CLI and yt-dlp/summarize inspiration
- @galligan - Marketplace plugin inspiration
- @hutchins - Pushed for YouTube feature

## Install

```bash
# Claude Code
claude install-skill https://github.com/mvanhorn/last30days-skill

# Manual
git clone https://github.com/mvanhorn/last30days-skill ~/.claude/skills/last30days
```

**Full Changelog**: https://github.com/mvanhorn/last30days-skill/commits/v2.1.0
```

#### 4. Create the GitHub Release

```bash
gh release create v2.1.0 \
  --repo mvanhorn/last30days-skill \
  --verify-tag \
  --title "v2.1.0 - Watchlists, YouTube Transcripts, Codex CLI" \
  -F release-notes.md
```

No binary assets needed - this is an interpreted skill, and GitHub auto-generates source archives.

### Phase 2: Future Automation (Optional)

#### 5. Add `.github/release.yml` for auto-generated notes on future releases

```yaml
changelog:
  exclude:
    labels:
      - ignore-for-release
  categories:
    - title: "Breaking Changes"
      labels: [breaking-change]
    - title: "Features"
      labels: [enhancement, feature]
    - title: "Bug Fixes"
      labels: [bug, fix]
    - title: "Documentation"
      labels: [documentation]
    - title: "Other Changes"
      labels: ["*"]
```

This enables `gh release create v2.2.0 --generate-notes` for future releases with automatic PR categorization.

## Acceptance Criteria

- [x] `CHANGELOG.md` exists in repo root following Keep a Changelog format
- [x] Annotated tag `v2.1.0` exists on the public repo
- [x] GitHub Release `v2.1.0` is published at `github.com/mvanhorn/last30days-skill/releases`
- [x] Release notes include: highlights, real results table, feature list, contributor credits, install instructions
- [x] Release appears in the repo sidebar on GitHub

## Key Decisions

1. **v2.1.0 only** - Don't retroactively create v1.0.0 or v2.0.0 tags. The git history doesn't have clean boundary commits for those versions, and retroactive tags add complexity without value.

2. **Release on the public repo** (`upstream`), not the private repo. Users see `mvanhorn/last30days-skill`.

3. **CHANGELOG.md as source of truth** - The release notes are derived from CHANGELOG.md, not the other way around. Future releases update CHANGELOG.md first, then create the release.

4. **Update star count** - The existing copy says "1.5K stars" but the repo now has 2,747. Update in release notes.

## References

- Existing copy: `docs/v2.1-launch-copy.md`, `docs/v2.1-tweets.md`
- Contributors: `docs/pr-credits.md`
- README features: `README.md` "What's New" sections
- GitHub Releases docs: https://docs.github.com/en/repositories/releasing-projects-on-github
- Keep a Changelog: https://keepachangelog.com/en/1.1.0/
