---
title: "feat: Paperclip Marketing Automation for last30days"
type: feat
status: active
date: 2026-03-07
---

# Paperclip Marketing Automation for last30days

## Overview

Set up a Paperclip "company" that auto-runs marketing for the last30days open-source skill (3,800+ stars). Four agent roles handle daily demo showcases, release announcements, community engagement, and analytics - all using a draft-then-approve workflow through Paperclip's built-in approval gates.

The killer angle: **last30days markets itself by running itself.** The Content Creator agent runs `/last30days [trending topic] --agent` on hot topics daily, then drafts X threads showing the results. Every post is a live demo.

## Problem Statement / Motivation

- All marketing is currently manual - ~60KB of pre-drafted X threads sit in `docs/` unposted
- No automated community monitoring (GitHub issues, contributor shoutouts)
- No metrics tracking (star growth, fork trends, social engagement)
- Solo entrepreneur can't sustain daily content + community management + development
- The tool's best ad is itself running on interesting topics, but that requires daily effort

## Proposed Solution

A Paperclip company called **"last30days Marketing"** with 4 agent roles, all draft-then-approve.

### Company Structure

```
last30days Marketing (Company)
  Mission: "Grow last30days to 10K GitHub stars through daily demo content,
            release marketing, and community engagement"

  Marketing Director (Claude Code agent)
    - Sets daily topic priorities
    - Reviews draft quality before surfacing to human
    - Coordinates cross-agent work

  Content Creator (Python script agent)
    - Runs last30days on trending topics daily
    - Drafts X showcase threads from the results
    - Heartbeat: daily at 8 AM PT

  Release Manager (Bash + Python agent)
    - Watches for new git tags on upstream
    - Drafts release announcement threads
    - Heartbeat: every 6 hours (tag check is cheap)

  Community Manager (Python script agent)
    - Monitors GitHub issues/PRs via gh CLI
    - Drafts welcome messages for new contributors
    - Surfaces popular feature requests
    - Heartbeat: every 4 hours

  Analytics Analyst (Python script agent)
    - Tracks GitHub stars, forks, traffic
    - Tracks X engagement (@slashlast30days)
    - Generates weekly digest
    - Heartbeat: daily at 11 PM PT (collect), weekly Monday 9 AM (digest)
```

### Draft-Then-Approve Flow

```
Agent creates draft
  -> Saved to ~/Documents/Last30Days/drafts/{agent}/{date}-{slug}.md
  -> Paperclip approval gate triggers
  -> Matt reviews in Paperclip UI (approve / reject / edit)
  -> On approve: Python script posts to X API via tweepy
  -> Audit log records everything
```

## Technical Approach

### Phase 1: Infrastructure (Day 1-2)

Set up Paperclip and external API access.

**Files to create:**

- `marketing/paperclip-config.yaml` - Company definition, org chart, budgets
- `marketing/scripts/post_to_x.py` - X API v2 posting via tweepy (draft queue -> X)
- `marketing/scripts/github_monitor.py` - GitHub event monitoring via gh CLI
- `marketing/scripts/metrics_collector.py` - Star/fork/engagement tracking
- `marketing/.env.example` - Required API keys template

**Setup steps:**

1. Install Paperclip locally:
   ```bash
   git clone https://github.com/paperclipai/paperclip
   cd paperclip && pnpm install && pnpm dev
   ```

2. Get X API v2 credentials (developer.x.com) for @slashlast30days
   - Need: API key, API secret, Access token, Access token secret
   - Permissions: Read + Write (posting)

3. GitHub token for monitoring (gh auth already configured)

4. Create the company in Paperclip UI at localhost:3100:
   - Company name: "last30days Marketing"
   - Mission: "Grow last30days to 10K GitHub stars"
   - Monthly budget cap: $50 (mostly API token costs)

### Phase 2: Content Creator Pipeline (Day 3-5)

The core marketing engine. Uses last30days to market itself.

**How it works:**

1. **Topic Discovery** - `marketing/scripts/discover_topics.py`
   - Scrapes trending topics from: Wikipedia pageviews API, HN front page, Reddit r/all
   - Filters for topics that would make compelling demos (tech, culture, sports, geopolitics)
   - Outputs ranked topic list to `~/Documents/Last30Days/topics-queue.json`

2. **Research & Draft** - `marketing/scripts/create_showcase.py`
   - Picks top topic from queue
   - Runs: `python3 scripts/last30days.py "{topic}" --agent --emit=compact --save-dir=~/Documents/Last30Days`
   - Reads the saved research output
   - Drafts a 1-2 tweet thread in the established style (see Style Guide below)
   - Saves draft to `~/Documents/Last30Days/drafts/content/{date}-{slug}.md`

3. **Approval Gate** - Paperclip surfaces draft for review
   - Matt approves/edits in Paperclip UI
   - On approve: triggers `post_to_x.py` with the draft content

**Style Guide (extracted from existing launch threads):**

```
Format: Stats-first hook + key finding + tool credit

Example (from v2.5 launch):
"/last30days Anthropic Pete Hegseth"

14 Reddit threads. 29 X posts (11,559 likes). 20 YouTube videos (739K views).
5 HN stories. 9 Polymarket markets.

[Key finding in 2-3 sentences]

Polymarket: [relevant odds with specific numbers]

[One-liner showing the tool's value]

github.com/mvanhorn/last30days-skill
```

Rules:
- Always lead with the /last30days command that was run
- Always include the stats line (thread/post/video counts)
- Always end with the GitHub link
- Pick topics people care about RIGHT NOW
- Never use em dashes - use hyphens instead
- Keep threads to 1-2 tweets max for daily showcases
- Save longer threads (3-6 tweets) for releases

### Phase 3: Release Manager Pipeline (Day 5-6)

**How it works:**

1. **Tag Watcher** - `marketing/scripts/watch_releases.py`
   - Runs `git -C $HOME/last30days-skill-private fetch upstream --tags` every 6 hours
   - Compares local tags vs upstream tags
   - On new tag: reads CHANGELOG.md diff since last tag

2. **Thread Drafter** - `marketing/scripts/draft_release_thread.py`
   - Reads changelog diff + release-notes.md
   - Drafts a 3-6 tweet thread following the v2.5 launch thread style
   - Includes: version number, headline features, demo queries, contributor shoutouts
   - Saves to `~/Documents/Last30Days/drafts/releases/{tag}.md`

3. **Approval Gate** - Same flow as content pipeline

**Template (from existing docs/v2.5-launch-tweets.md):**

```
Tweet 1: Announcement + 3 headline features + GitHub link
Tweet 2-4: One demo per tweet (command + stats + finding)
Tweet 5: Contributor shoutouts
Tweet 6: Install instructions
```

### Phase 4: Community Manager Pipeline (Day 6-7)

**How it works:**

1. **GitHub Monitor** - `marketing/scripts/github_monitor.py`
   - Runs `gh issue list --repo mvanhorn/last30days-skill --state open --json number,title,author,createdAt,labels`
   - Runs `gh pr list --repo mvanhorn/last30days-skill --state open --json number,title,author,createdAt`
   - Compares against `~/Documents/Last30Days/community/seen.json` to detect new items
   - Categorizes: bug report, feature request, question, PR

2. **Response Drafter** - `marketing/scripts/draft_community_response.py`
   - For new issues: drafts a welcome + triage response
   - For new PRs: drafts a thank-you + initial review comment
   - For merged PRs: drafts a contributor shoutout tweet
   - Saves to `~/Documents/Last30Days/drafts/community/{type}-{number}.md`

3. **Approval Gate** - GitHub responses go through same Paperclip approval
   - Approved responses posted via `gh issue comment` or `gh pr comment`
   - Shoutout tweets posted via `post_to_x.py`

**Response templates:**

```markdown
# New Issue (bug)
Thanks for the report! I'll look into this. Can you share:
- Your OS and Python version
- The exact command you ran
- Whether you have SCRAPECREATORS_API_KEY set

# New Issue (feature request)
Interesting idea! [1-2 sentences acknowledging the value].
Adding this to the backlog for consideration.

# New PR
Thanks for the contribution, @{author}! I'll review this shortly.
[If first-time contributor: Welcome to the project!]

# Merged PR (tweet)
Shoutout to @{author} for [what they did] in last30days v{version}!
[Brief description of the change and why it matters]
github.com/mvanhorn/last30days-skill/pull/{number}
```

### Phase 5: Analytics Pipeline (Day 7-8)

**How it works:**

1. **Metrics Collector** - `marketing/scripts/metrics_collector.py`
   - Daily: GitHub stars, forks, open issues, open PRs (via `gh api`)
   - Daily: X followers, tweet impressions for @slashlast30days (via X API v2)
   - Stores in SQLite at `~/Documents/Last30Days/analytics.db`

2. **Weekly Digest** - `marketing/scripts/weekly_digest.py`
   - Runs Monday 9 AM PT
   - Generates markdown report with week-over-week changes:
     - Star growth (absolute + rate)
     - New forks
     - Issues opened/closed
     - PRs merged
     - Top-performing tweets
     - Notable community interactions
   - Saves to `~/Documents/Last30Days/digests/{date}-weekly.md`
   - Optionally sends via email (SendGrid) or Slack webhook

**Schema for analytics.db:**

```sql
CREATE TABLE daily_metrics (
  date TEXT PRIMARY KEY,
  github_stars INTEGER,
  github_forks INTEGER,
  github_open_issues INTEGER,
  github_open_prs INTEGER,
  x_followers INTEGER,
  x_impressions INTEGER,
  x_engagement_rate REAL
);

CREATE TABLE tweet_performance (
  tweet_id TEXT PRIMARY KEY,
  posted_at TEXT,
  type TEXT,  -- 'showcase', 'release', 'shoutout'
  topic TEXT,
  impressions INTEGER,
  likes INTEGER,
  retweets INTEGER,
  replies INTEGER,
  link_clicks INTEGER
);
```

## Technical Considerations

### API Costs

| Service | Usage | Est. Monthly Cost |
|---------|-------|-------------------|
| Paperclip | Self-hosted | $0 |
| X API v2 | Free tier (posting) | $0 |
| ScrapeCreators | ~30 daily research runs | ~$15 |
| GitHub API | gh CLI, already authed | $0 |
| Claude API | Marketing Director agent | ~$20 |
| **Total** | | **~$35/month** |

### Security

- API keys stored in `marketing/.env` (gitignored, never committed)
- X API tokens scoped to @slashlast30days only (not personal account)
- Paperclip budget cap prevents runaway spend
- All posts go through human approval gate - no autonomous posting
- GitHub token uses existing `gh auth` session

### Failure Modes

- **last30days script fails** - Content Creator skips that day, logs error, tries again tomorrow with new topic
- **X API rate limit** - Queue drafts and retry on next heartbeat cycle
- **Paperclip goes down** - Drafts accumulate in filesystem, nothing posts (safe failure)
- **Bad topic selection** - Marketing Director agent filters topics before research (no politics, no NSFW)
- **Stale approval queue** - If drafts pile up >3 days unapproved, send a nudge notification

### Topic Selection Criteria

The discover_topics.py script should filter for topics that:
1. Are trending NOW (Wikipedia pageview spike, HN front page, Reddit r/all)
2. Would produce interesting multi-source results (not too niche, not too broad)
3. Are safe for a developer tool brand (tech, sports, culture, science - avoid divisive politics)
4. Haven't been covered in the last 7 days (dedup against previous showcases)
5. Span different categories to show the tool's versatility (not all tech, not all sports)

Good examples (from existing launch threads): "Anthropic Pete Hegseth", "Seedance prompting", "Arizona basketball", "Iran war"

## Acceptance Criteria

- [ ] Paperclip running locally with "last30days Marketing" company created
- [ ] Content Creator agent runs last30days daily on a trending topic and produces a draft
- [ ] Release Manager agent detects new git tags and drafts announcement threads
- [ ] Community Manager agent detects new GitHub issues/PRs and drafts responses
- [ ] Analytics agent collects daily metrics and generates weekly digest
- [ ] All drafts go through Paperclip approval gate before posting
- [ ] X API integration posts approved drafts to @slashlast30days
- [ ] GitHub responses posted via gh CLI after approval
- [ ] Monthly budget stays under $50
- [ ] Style of generated tweets matches existing launch thread tone

## Success Metrics

- **Content velocity**: 5-7 showcase tweets/week (up from ~0 currently)
- **Star growth**: Track week-over-week acceleration after content starts
- **Time savings**: <5 min/day reviewing drafts vs 30-60 min/day manual marketing
- **Content quality**: Drafts require minimal editing before approval (>80% approved as-is within 2 weeks)

## Dependencies & Risks

| Dependency | Risk | Mitigation |
|-----------|------|------------|
| Paperclip stability | Early-stage project, may have bugs | Pin to specific commit, keep simple config |
| X API free tier | May get rate-limited or deprecated | Queue-based posting, daily limits |
| Topic discovery quality | Bad topics = bad demos | Human review in approval gate, topic blocklist |
| Claude API for Marketing Director | Cost could spike | Budget cap in Paperclip, simple prompts |
| ScrapeCreators API | PAYG costs scale with usage | Cap at 1 research run/day for showcases |

## File Structure

```
last30days-skill-private/
  marketing/
    README.md                          # Setup instructions
    .env.example                       # Required API keys
    paperclip-config.yaml              # Company definition
    scripts/
      discover_topics.py               # Trending topic discovery
      create_showcase.py               # Research + draft showcase tweet
      draft_release_thread.py          # Release announcement drafter
      github_monitor.py                # GitHub issue/PR monitor
      draft_community_response.py      # Community response drafter
      post_to_x.py                     # X API posting (after approval)
      metrics_collector.py             # Daily metrics collection
      weekly_digest.py                 # Weekly analytics digest
    templates/
      showcase.md                      # Tweet template for daily demos
      release.md                       # Thread template for releases
      community_responses.md           # Response templates by type
```

## Sources & References

- Paperclip GitHub: github.com/paperclipai/paperclip
- Paperclip docs: paperclip.ing
- Existing launch threads: `docs/v2.5-launch-tweets.md`, `docs/v2.1-tweets.md`
- Existing launch copy: `docs/v2.1-launch-copy.md`
- Planned Last30Days.com: `docs/plans/2026-02-20-feat-last30days-com-trending-topics-plan.md`
- last30days `--agent` mode: SKILL.md line 115-142 (non-interactive output for automation)
