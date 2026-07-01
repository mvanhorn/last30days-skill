# /last30days

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending #1 Repository Of The Day" />
  </a>
  <br/>
  <img src="https://img.shields.io/badge/coverage-%E2%89%A560%25-brightgreen" alt="Coverage ≥60%" />
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
</p>

**An AI agent-led search engine scored by upvotes, likes, and real money - not editors.**

This README tracks the current v3 pipeline. The runtime skill spec lives in [skills/last30days/SKILL.md](skills/last30days/SKILL.md), which is the source of truth for the latest command and setup behavior.

**Claude Code (recommended — auto-updates via marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, or any of 50+ [Agent Skills](https://agentskills.io) hosts:**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` installs globally for your user, available across all projects. Drop it to scope per-project.)

More install options (claude.ai web, OpenClaw, manual) in the [Install](#install) section below.

Zero config. Reddit, HN, Polymarket, and GitHub work immediately. Run it once and the setup wizard unlocks X, YouTube, TikTok, and more in 30 seconds.

---

Reddit upvotes. X likes. YouTube transcripts. TikTok engagement. Polymarket odds backed by real money and insider information. That's millions of people voting with their attention and their wallets every day. /last30days searches all of it in parallel, scores it by what real people actually engage with, and an AI agent judge synthesizes it into one brief.

Google aggregates editors. /last30days searches people.

You can't get this search anywhere else because no single AI has access to all of it. Google search doesn't touch Reddit comments or X posts. ChatGPT has a deal with Reddit but can't search X or TikTok. Gemini has YouTube but not Reddit. Claude has none of them natively. Each platform is a walled garden with its own API, its own tokens, its own auth. But you can bring your own keys and browser sessions, and suddenly an AI agent can search all of them at once, score them against each other, and tell you what actually matters.

That's the unlock. Not one better search engine. A dozen disconnected platforms, bridged by an agent.

```
/last30days Peter Steinberger
```

You have a meeting tomorrow. You Google them. You get their LinkedIn from 2023. /last30days gives you what they're actually doing this month: joined OpenAI to work on Codex, fighting Anthropic's ban on third-party agents, shipping 23 PRs at 85% merge rate, building "LobsterOS" for cross-device agent control, and r/ClaudeCode hit 569 upvotes debating whether he's a hero or "insufferable." Scattered across X posts, Reddit threads, YouTube transcripts, and GitHub commits. None of it was on Google.

## Why this exists

I built it to keep up in AI. Everything changes every day and the Reddit and X nerds are always on top of it first. I needed better prompts, and the training data was always months behind what the community had already figured out.

But it turned into something bigger. Now I run it before a sales call to know the last 30 days truth about a business. Before a meeting to read someone's recent tweets and podcast transcripts. Before a Disney World trip to know which rides are closed and what the community says about Genie+. Before I build anything to know what problems people are actually hitting.

If you're meeting with a CEO, have you read all their tweets and YouTube transcripts from the last 30 days? I have.

## Sources, scored by the people

| Source | What the people tell you |
|--------|--------------------------|
| **Reddit** | The unfiltered take. Top comments with upvote counts, free via public JSON. The real opinions that Google buries. |
| **X / Twitter** | The hot take, the expert thread, the breaking reaction. First to know, first to argue. |
| **YouTube** | The 45-minute deep dive. Full transcripts searched for the 5 quotable sentences that matter. |
| **TikTok** | The creator reaching 3.6M people with a take you'll never find on Google. |
| **Instagram Reels** | The influencer perspective with spoken-word transcripts. The visual culture signal. |
| **Hacker News** | The developer consensus. 825 points, 899 comments. Where technical people actually argue. |
| **Polymarket** | Not opinions. Odds. Backed by real money. 96% confidence on album sales. 4% on an acquisition. |
| **GitHub** | For people: PR velocity, top repos by stars, release notes. For topics: issues and discussions. |
| **Digg** | Curated story clusters from Digg's AI 1000 leaderboard (~1000 high-signal AI accounts on X), with attributable inline quotes (no X auth required). Auto-enabled when `digg-pp-cli` is on PATH. |
| **Threads** | The post-Twitter text layer. Conversations from creators and brands. |
| **Pinterest** | Visual discovery. Pins, saves, and comments on products and ideas. |
| **Bluesky** | The decentralized social layer. AT Protocol posts from the post-Twitter migration. |
| **Perplexity** | Grounded Sonar synthesis, raw Search API rows, and Deep Research. |
| **Web** | The editorial coverage, the blog comparisons. One signal of many, not the only one. |

Community contributors keep adding more. Truth Social, Xiaohongshu (RED), and others are in the engine with more on the way.

A Reddit thread with 1,500 upvotes is a stronger signal than a blog post nobody read. A TikTok with 3.6M views tells you more about what's culturally relevant than a press release. Polymarket odds backed by $66K in volume are harder to argue with than a pundit's guess.

The synthesis ranks by what real people actually engaged with. Social relevancy, not SEO relevancy.

## What people actually use it for

**Before a meeting.** `/last30days Peter Steinberger` - joined OpenAI's Codex team, fighting Anthropic's ban on third-party agents, 23 PRs merged at 85% merge rate on GitHub, building LobsterOS for cross-device agent control. r/ClaudeCode: "Ever since OpenClaw released, it was widely known that if you run it through anything other than the API, you were gonna get banned eventually" (227 upvotes). That's not on LinkedIn.

**To read hiring signals.** `/last30days Listen Labs --hiring-signals` - current jobs and careers pages become cited evidence for focus shifts: hiring into enterprise security, customer success, infrastructure, or product expansion. The report says what the hiring appears to signal, not what the roadmap will ship.

**When something drops.** `/last30days Kanye West` - UK blocked his visa, Wireless Festival canceled, sponsors fled. But BULLY debuted #2 on Billboard. Fantano came back from his "Yay sabbatical" to review it (653K views). SoFi Homecoming brought out Lauryn Hill and Travis Scott for 44 songs. Polymarket: "Will Kanye tweet again?" 86% Yes. 23 Reddit threads, 17 YouTube videos, 86K upvotes.

**To compare tools.** `/last30days OpenClaw vs Hermes vs Paperclip` - "These aren't competitors, they're layers." OpenClaw is the executor (351K GitHub stars, live), Hermes is the self-improving brain (31K stars), Paperclip is the org chart (49K stars). Star counts pulled live from the GitHub API, not stale blog posts. Side-by-side table with architecture, memory, security, best-for. Per @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**To understand the world.** `/last30days Iran vs USA` - Day 38 of the war. Trump's Tuesday deadline for Iran to reopen the Strait of Hormuz. Two US warplanes downed. Oil at $126/barrel. The IEA called it "the largest supply disruption in the history of the global oil market." Polymarket: ceasefire by Dec 31 at 74%. 27 X posts, 10 YouTube videos, 20 prediction markets.

**Before a trip.** `/last30days Universal Epic Universe` - Expansion already under construction. "Project 680" permit filed. Fireworks show confirmed by infrastructure but unannounced. Wait times: Mine-Cart Madness averaging 148 minutes. No annual pass yet, and locals are frustrated. Stardust Racers down for refurbishment through April 5.

**To learn something fast.** `/last30days Nano Banana Pro prompting` - JSON-structured prompts are replacing tag soup. @pictsbyai's nested format prevents "concept bleeding." Edit-first workflow beats regeneration. Then it writes you a production prompt using exactly what the community said works.

## What v3 Changed

### Shareable HTML briefs

Ask for an HTML brief and the skill saves a self-contained, dark-mode, print-friendly file you can drop into Slack, email, or Notion. No raw markdown leaks. Inline CSS, system-font fallbacks behind Inter and JetBrains Mono. No JavaScript. Works offline.

```
/last30days OpenClaw --emit=html
```

or just ask in plain language:

```
/last30days OpenClaw, give me a shareable HTML brief
/last30days Cursor IDE for slack
/last30days Anthropic earnings export as html
```

The skill emits the synthesis in chat as usual AND saves a brief to `${LAST30DAYS_MEMORY_DIR}/{topic}-brief.html` (defaults to `~/Documents/Last30Days/`). The chat response ends with the file path so you can `open` it or drag it into a message.

What's in the file: badge, inline metadata line, the model's synthesis verbatim with all citations, the engine footer (✅ All agents reported back! tree), and a colophon noting the topic + how to re-run. Data quality warnings (degraded run, thin evidence, etc.) stay in the engine's stderr logs; they never leak into the shareable artifact.

For direct CLI use without the model in the loop, the engine also accepts `--synthesis-file PATH` to convert any markdown synthesis to HTML.

### Intelligent search: the killer feature

The v3 engine doesn't just search for your topic. It figures out *where* to search before the search begins. Type "OpenClaw" and the engine resolves @steipete (Peter Steinberger, the creator), r/openclaw, r/ClaudeCode, and the right YouTube channels and TikTok hashtags - all via a new Python pre-research brain built by [@j-sperling](https://github.com/j-sperling). The old engine searched keywords. The new engine understands your topic first, then searches the right people and communities.

This is why v3 finds content v2 never could. "Paperclip" resolves @dotta. "Dave Morin" resolves @davemorin plus @OpenClaw plus the TWiST podcast. "Peter Steinberger" resolves @steipete on X and steipete on GitHub. Bidirectional: person to company, product to founder, name to GitHub profile. The right subreddits, the right handles, the right hashtags - resolved before a single API call fires.

### Best Takes

Reddit and X people are funny. The old engine buried their best stuff because it scored for relevance, not cleverness. v3 has a second judge that scores every result for humor, wit, and virality alongside the relevance score. Tommy Lloyd's "My Michael Jordan is Steve Kerr" scores low on relevance to "Arizona Basketball" but off the charts on fun. Now every brief ends with a "Best Takes" section - the cleverest one-liners, the most viral quotes, the reactions that make you want to share the research. Built in, not a toggle.

### Cross-source cluster merging

When the same story appears on Reddit, X, and YouTube, v3 merges them into one cluster instead of showing three separate items. Entity-based overlap detection catches matches even when the titles use different words.

### Single-pass comparisons

"CLI vs MCP" used to run three serial passes (12+ minutes). v3 runs one pass with entity-aware subqueries for both sides simultaneously. Same depth, 3 minutes.

### Auto-discovered competitor comparisons

`/last30days OpenAI --competitors` tells the hosting reasoning model to discover the top 2 peers via WebSearch (Anthropic, xAI), run Step 0.55 per entity, and invoke the engine with `"OpenAI vs Anthropic vs xAI"` and a per-entity `--competitors-plan` JSON. The engine fans out 3 full pipelines in parallel, saves a `*-raw.md` file per entity, and merges them into a 3-way comparison. Same mechanics power `/last30days "OpenAI vs Anthropic vs xAI"` directly.

### GitHub person-mode

When the topic is a person, the engine switches from keyword search to author-scoped queries. Instead of "who mentioned this name in an issue body," it answers: what are they shipping and where is it landing?

`/last30days Peter Steinberger --github-user=steipete` shows 22 PRs merged across 3 repos at 85% merge rate. Own projects with README summaries, star counts, and top feature requests. Release notes for what shipped this month. The synthesizer weaves it into the narrative alongside X posts and Reddit threads.

### ELI5 mode

Say "eli5 on" after any research run. The synthesis rewrites in plain language. No jargon. Same data, same sources, same citations - just clearer. "Arizona wins by being physical" instead of "Arizona's identity is paint scoring (50%+ shooting, 9th nationally)." Say "eli5 off" to go back.

### Everything else in v3

- **Free Reddit comments.** Public JSON gives you threads + top comments with upvote counts. No API key, no ScrapeCreators. Just works.
- **YouTube transcripts that actually work.** Widened candidate pool 3x past music videos to reach talk/review content with captions.
- **TikTok, Instagram, Threads.** All three activate automatically once `SCRAPECREATORS_API_KEY` is set — same key, same per-call cost. Suppress any of them with `EXCLUDE_SOURCES=tiktok,instagram,threads` (any comma-separated subset).
- **Pinterest.** Per-query opt-in (visual pins, narrow utility): the model passes `--search=pinterest` for the runs that need it. Requires `SCRAPECREATORS_API_KEY`.
- **YouTube comments + transcript fallback.** Both activate automatically once `SCRAPECREATORS_API_KEY` is set, the same default-on backup tier. Transcripts only fall back to ScrapeCreators when yt-dlp fails (no credit spent on success); comments are bounded to the top few videos (~3 extra calls per run). Suppress comments with `EXCLUDE_SOURCES=youtube_comments`. **TikTok comments** stay opt-in via `INCLUDE_SOURCES=tiktok_comments`. Surface top comments with vote counts the same way Reddit does.
- **Perplexity Sonar / Search API / Deep Research.** Grounded web search via direct Perplexity (`PERPLEXITY_API_KEY`) or OpenRouter Sonar fallback (`OPENROUTER_API_KEY`). Add one of those keys plus `INCLUDE_SOURCES=perplexity` (it's a separate paid API - opt-in keeps you from being surprise-billed). Direct Perplexity can return Sonar synthesis, raw ranked Search API rows, or both.
- **Polymarket noise filtering.** Common-word disambiguation prevents "Apple" from matching "Will Apple release a car?"
- **Resilient Reddit.** Timeout budgets and runtime fallback. One slow thread doesn't kill the whole run.
- **Fun judge v2.** Humor scoring baked into the narrative. Reddit's cleverest one-liners mixed into the synthesis where they fit, not dumped in a separate section.
- **Polymarket odds, not dollars.** The % odds are the magic. Dollar volumes removed from display.
- **Per-author cap.** Max 3 items per author prevents any single voice from dominating your brief.
- **Entity disambiguation.** When the engine resolves handles, the synthesis trusts them. No more Mallorca resorts winning over Washington athletic clubs.
- **OpenClaw first-class citizen.** Auto-resolve for engine-side pre-research. Device auth for frictionless ScrapeCreators signup.
- **1,012 tests passing.**

## Install

| Surface | Install | Updates |
|---------|---------|---------|
| **Claude Code** (recommended) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto via marketplace, or `claude plugin update last30days@last30days-skill` |
| **Codex, Cursor, Copilot, Gemini CLI, or any of 50+ [Agent Skills](https://agentskills.io) hosts** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) and upload via claude.ai > Customize > Skills > + > Create skill > Upload a skill | Re-download and re-upload |
| **Claude Desktop** | [Download the `.mcpb` for your platform](https://github.com/mvanhorn/last30days-skill/releases/latest) and drag into Settings > Extensions | Re-download and drag the new bundle in |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (recommended)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Recommended because the Claude Code marketplace handles updates for you — the plugin cache is versioned and auto-refreshes when a new release publishes. Run `claude plugin update last30days@last30days-skill` to force a check.

If you'd rather use the agent-skills install path on Claude Code, that's also supported:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

The native plugin and the `npx skills` install can coexist. Note that Claude Code does not dedupe across install methods: if you have both the marketplace plugin and the `npx skills` copy active, `/last30days` will show two entries. Use one install method per machine.

### Codex, Cursor, Copilot, Gemini CLI, and other Agent Skills hosts

Install via the open [Agent Skills](https://agentskills.io) CLI — supports 50+ harnesses including `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`, and more (full list on the [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

The `-g` (global) flag installs to your user directory so the skill is available across all projects. Without `-g`, `npx skills` installs project-locally into `./.skills/` (committed with the repo). For a research-the-world tool, global is what you want.

Codex desktop and other folder-mode hosts can work in ordinary folders as well as Git repos. Before first research, ask the host agent to run the bundled `scripts/last30days.py --preflight` from the loaded skill directory; in a source checkout, the equivalent command is `python3 skills/last30days/scripts/last30days.py --preflight`. It shows the config source, browser-cookie plan, planned writes, optional commands, and ignored project config without reading cookies, writing files, or running research.

By default this installs for whichever harness `npx skills` detects. To target a specific one (or multiple):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Update later with:

```bash
npx skills update last30days -g
```

Or update everything you've installed globally via `npx skills`:

```bash
npx skills update -g
```

List and remove with `npx skills list -g` and `npx skills remove last30days -g`.

### claude.ai (web)

1. [Download `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) from the latest release
2. Go to [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Click the `+` button in the Skills panel > click on `Create skill` > `Upload a skill` and browse/drop the file in

Enable "Code execution and file creation" under Capabilities first — skills won't run without it.

### Claude Desktop

Claude Desktop installs `/last30days` as an MCP server via a `.mcpb` bundle (a one-click Model Context Protocol package).

1. Go to the [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) and download the `.mcpb` for your platform:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Open Claude Desktop, go to Settings > Extensions, and drag the file in.
3. When prompted, paste API keys for the sources you want to enable. Every field is optional — the engine degrades to web-only mode if you skip them all. Keys are stored in your OS keychain.
4. Restart Claude Desktop. Ask Claude to "research Peter Steinberger" or any topic and it will call the `research` tool.

**Host requirement:** Python 3.12+ on PATH. The bundle ships the engine source but uses your local Python interpreter. Install from [python.org](https://www.python.org/downloads/) on Windows; macOS and most Linux distros ship a compatible version.

**Keys don't sync with the Code skill.** Claude Desktop and Claude Code maintain separate credential stores by design. If you already configured `~/.config/last30days/.env` for the Code skill, you'll re-enter the same keys here once.

Windows support is deferred until per-platform manifest entry points are sorted out; track in a follow-up issue.

### OpenClaw

```bash
clawhub install last30days-official
```

For X/Twitter action workflows outside `/last30days` research, such as posting
tweets or replies, follower export, media handling, monitors, and giveaway
draws, use [TweetClaw](https://github.com/Xquik-dev/tweetclaw) as the companion
OpenClaw plugin. TweetClaw is maintained by Xquik-dev and is listed only as an
optional companion path, not a last30days dependency or endorsement.

### Manual (developer)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

The symlink keeps the install in sync with your working tree as you edit — no re-copy needed. For `claude.ai`, build the `.skill` file from source: `bash skills/last30days/scripts/build-skill.sh` produces `dist/last30days.skill`.

Reddit (with comments), Hacker News, Polymarket, and GitHub work immediately. Zero configuration. Run `/last30days` once and the setup wizard unlocks more sources in 30 seconds.

## Bring your own keys

These platforms don't have relationships with each other. X doesn't know what Reddit thinks. YouTube doesn't see TikTok. But you can bring your own API keys and browser tokens, and suddenly you have access to all of them at once.

| Sources | What you need | Cost |
|---------|---------------|------|
| Reddit (with comments) + HN + Polymarket + GitHub | Nothing | Free |
| X / Twitter | Log into x.com in any browser, or set `XQUIK_API_KEY` / `XAI_API_KEY` | Browser cookies are free; keys are provider-specific |
| YouTube | `brew install yt-dlp` | Free |
| Bluesky | App password from bsky.app | Free |
| TikTok + Instagram + Threads + Pinterest + YouTube comments | ScrapeCreators key | 10,000 free calls, then PAYG |
| Perplexity Sonar / Search API / Deep Research | Perplexity key, or OpenRouter key as Sonar fallback | Pay as you go |
| Web search | Brave Search key | 2,000 free queries/month |

### macOS Keychain (optional)

On macOS you can store keys in the system Keychain instead of a `.env` file. The skill picks them up automatically as the lowest-priority source — `.env` files and process environment still win on collision.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Items are stored under service name `last30days-<KEY>` for the current user. On non-Darwin platforms the loader is a no-op, so there is no behaviour change for Linux/Windows users.

Already have keys under different Keychain service names? Set the non-secret `LAST30DAYS_KEYCHAIN_ALIASES` mapping described in [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) instead of copying secrets.

See [CONFIGURATION.md](CONFIGURATION.md) for the full per-source key matrix, reasoning provider priority, and web-search backend priority.

## Configuration

Two things you'll likely want to know on day one:

**Where research files are saved.** `LAST30DAYS_MEMORY_DIR` defaults to `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Override by setting that env var to any path in your shell, or `--save-dir <path>` per run. Use `--output <file>` when you need the rendered result at an exact path, using the format selected by `--emit`. Use `--save-suffix=<name>` to keep multiple variations of the same topic separate (e.g. per client). Each `--save-dir` run produces `<slug>-raw[-suffix].md`. Run `python3 skills/last30days/scripts/last30days.py --preflight` to review planned writes before a research run.

**Trend monitoring across runs.** The default mode produces a fresh markdown snapshot per run. To accumulate findings over time, add `--store` to persist into a SQLite database, then use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) for scheduled runs (with optional Slack / webhook delivery on new findings) and [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) for daily / weekly digests. The full cadence pattern is in [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

Per-client wrapper scripts, custom category-peer subreddits, and the experimental beta channel for in-progress customizations are also documented in [CONFIGURATION.md](CONFIGURATION.md).

## How it works

1. **You type a topic.** Person, company, product, technology, "X vs Y." Anything.
2. **The agent resolves who matters.** Finds X handles (including founders), GitHub repos, subreddits, TikTok hashtags, YouTube channels. For "Kanye West" it knows r/hiphopheads, @kanyewest, and "bully review" on YouTube. For "OpenClaw" it resolves openclaw/openclaw on GitHub and fetches live star counts.
3. **All sources searched in parallel.** Multi-query expansion. Results scored by engagement, relevance, freshness.
4. **The depth nobody else has.** Full YouTube transcripts from reaction videos. Top Reddit comments with upvote counts. TikTok captions. Polymarket odds. Not just titles and links.
5. **Same story, merged.** Wireless Festival announced on Reddit, discussed on X, ticket prices on TikTok = one cluster, not three separate items.
6. **Synthesized into one brief.** Grounded in specific data. Cited by source. Ranked by what people actually engage with. Not "here's what I found." It's "here's what matters."
7. **Then it becomes your expert.** After one run, your Claude session knows everything the community knows. Ask follow-up questions. Have it write prompts, draft emails, plan trips, architect systems - all grounded in what's real right now.

## What people are saying

> "I found a Claude Code skill that researches any topic across Reddit, X, YouTube, and HN from the last 30 days. Then writes the prompts for you. I've been manually searching Reddit and X for research before every piece of content I write. Tab by tab. Thread by thread. That's the part that takes 90 minutes. This eliminates it." -@itsjasonai

> "This one skill replaced my entire research workflow. You give it a topic, it scrapes Reddit, X, and the web for what people are actually talking about. Not old blog posts. Real conversations from the last 30 days." -@itswilsoncharles

> "5 of the 10 trending repos on GitHub today are Claude tools. #1: mvanhorn/last30days-skill" -@yieldhunter95

## Open source

MIT license. No tracking. No analytics. Your research stays on your machine. 1,012 tests.

Built with Python 3.12+, yt-dlp, Node.js (vendored Bird client for X search), and ScrapeCreators API. v3 engine architecture by [@j-sperling](https://github.com/j-sperling).

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list of community contributors and [CHANGELOG.md](CHANGELOG.md) for version history.

## Star History

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
