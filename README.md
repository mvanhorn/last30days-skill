# /last30days

English | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

<p align="center">
  <img src="media/pr-assets/last30days-ad.gif" width="720" alt="last30days - an AI agent-led search engine that searches people, not editors" />
</p>

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending #1 Repository Of The Day" />
  </a>
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
  <br/>
  <a href="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml">
    <img src="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml/badge.svg" alt="Validate status" />
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

Zero config. Reddit, HN, Polymarket, and GitHub work immediately. Run it once and the setup wizard unlocks X, YouTube, TikTok, arXiv, Techmeme, and more in 30 seconds.

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
| **Reddit** | The unfiltered take. Top comments with real upvote counts, free, no API key. The real opinions that Google buries. |
| **X / Twitter** | The hot take, the expert thread, the breaking reaction. First to know, first to argue. |
| **YouTube** | The 45-minute deep dive. Full transcripts searched for the 5 quotable sentences that matter. |
| **TikTok** | The creator reaching 3.6M people with a take you'll never find on Google. |
| **Instagram Reels** | The influencer perspective with spoken-word transcripts. The visual culture signal. |
| **Hacker News** | The developer consensus. 825 points, 899 comments. Where technical people actually argue. |
| **Polymarket** | Not opinions. Odds. Backed by real money. 96% confidence on album sales. 4% on an acquisition. |
| **GitHub** | For people: PR velocity, top repos by stars, release notes. For topics: issues and discussions. |
| **Digg** | Story clusters from Digg's AI and Tech sections, with attributable inline quotes from X (no X auth required). Auto-enabled when `digg-pp-cli` is on PATH. |
| **arXiv** | The papers behind the hype. New research in the window, free, no API key. Auto-enabled when `arxiv-pp-cli` is on PATH (first-run setup installs it). |
| **Techmeme** | The tech-news editorial layer, date-windowed to your 30 days. Free, no API key. Auto-enabled when `techmeme-pp-cli` is on PATH (first-run setup installs it). |
| **LinkedIn** | The professional signal. Posts and articles, with articles weighted as high signal. |
| **Meta Ads** | What a brand is paying to say. Live Meta ad creatives launched this month, with copy, promo codes, and spoken transcripts. |
| **StockTwits** | Trader sentiment. Auto-activates when your topic is a ticker or crypto. |
| **Threads** | The post-Twitter text layer. Conversations from creators and brands. |
| **Pinterest** | Visual discovery. Pins, saves, and comments on products and ideas. |
| **Xiaohongshu (RED)** | Chinese lifestyle, product, and creator signals. Requested explicitly with `--search xhs` when a logged-in x-mcp browser plugin or `xiaohongshu-mcp` service is running locally. |
| **Bluesky** | The decentralized social layer. AT Protocol posts from the post-Twitter migration. |
| **Perplexity** | Controlled Agent API synthesis, OpenRouter Sonar fallback, raw Search API rows, and explicit Deep Research. |
| **DEV Community (dev.to)** | Developer articles and top comments through the public, keyless API. Opt in with `--devto-tags`, `--search devto`, or `INCLUDE_SOURCES=devto`. |
| **Truth Social** | Posts searched with `TRUTHSOCIAL_TOKEN`; available when authenticated. |
| **Telegram** | Public posts from named channels. Opt-in; requires `SCRAPECREATORS_API_KEY` and a channel list. No keyword discovery. |
| **Trustpilot** | Company TrustScore, review count, and an AI summary of customer feedback. Opt-in; requires `trustpilot-pp-cli` on PATH and a usable session. |
| **Amazon** | Product ratings, prices, and customer reviews. Opt-in; requires an authenticated `brightdata` CLI and uses your Bright Data credits. |
| **DripStack** | Financial newsletters and analyst writeups through a public, keyless API. Opt in with `--search dripstack` or `INCLUDE_SOURCES=dripstack`. |
| **Jobs / careers pages** | Public job postings as evidence of a company's focus. Selected with `--hiring-signals`, `--search jobs`, or the research plan. |
| **Local corpus** | Your local Markdown and text files, plus PDFs when `pdftotext` is on PATH. Opt in with `--corpus` or `LAST30DAYS_CORPUS_DIRS`. |
| **Web** | The editorial coverage, the blog comparisons. One signal of many, not the only one. |

Ask `/last30days` to include the sources you need. Availability depends on credentials, installed tools, the topic, and explicit opt-ins. See [source requirements](CONFIGURATION.md#source-requirements) for configuration.

A Reddit thread with 1,500 upvotes is a stronger signal than a blog post nobody read. A TikTok with 3.6M views tells you more about what's culturally relevant than a press release. Polymarket odds backed by $66K in volume are harder to argue with than a pundit's guess.

The synthesis ranks by what real people actually engaged with. Social relevancy, not SEO relevancy.

## What people actually use it for

**Before a meeting.** `/last30days Peter Steinberger` - joined OpenAI's Codex team, fighting Anthropic's ban on third-party agents, 23 PRs merged at 85% merge rate on GitHub, building LobsterOS for cross-device agent control. r/ClaudeCode: "Ever since OpenClaw released, it was widely known that if you run it through anything other than the API, you were gonna get banned eventually" (227 upvotes). That's not on LinkedIn.

**To read hiring signals.** `/last30days Listen Labs --hiring-signals` - current jobs and careers pages become cited evidence for focus shifts: hiring into enterprise security, customer success, infrastructure, or product expansion. The report says what the hiring appears to signal, not what the roadmap will ship.

**To find the topic before it peaks.** Ask `/last30days what's exploding in AI agents?` and the skill switches to discovery mode: the engine sweeps Reddit category listings, Hacker News front/best stories, Digg's AI and Tech feeds, and X when authenticated; your agent judges the nominations (names, junk filtering, content-worthiness) and writes podcast / X-article angles; then you get 5-10 velocity-ranked topics. Every result includes cross-source numbers, a momentum label, and a ready-to-run `/last30days "<topic>"` follow-up.

**When something drops.** `/last30days Kanye West` - UK blocked his visa, Wireless Festival canceled, sponsors fled. But BULLY debuted #2 on Billboard. Fantano came back from his "Yay sabbatical" to review it (653K views). SoFi Homecoming brought out Lauryn Hill and Travis Scott for 44 songs. Polymarket: "Will Kanye tweet again?" 86% Yes. 23 Reddit threads, 17 YouTube videos, 86K upvotes.

**To compare tools.** `/last30days OpenClaw vs Hermes vs Paperclip` - "These aren't competitors, they're layers." OpenClaw is the executor (351K GitHub stars, live), Hermes is the self-improving brain (31K stars), Paperclip is the org chart (49K stars). Star counts pulled live from the GitHub API, not stale blog posts. Side-by-side table with architecture, memory, security, best-for. Per @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**To understand the world.** `/last30days Iran vs USA` - Day 38 of the war. Trump's Tuesday deadline for Iran to reopen the Strait of Hormuz. Two US warplanes downed. Oil at $126/barrel. The IEA called it "the largest supply disruption in the history of the global oil market." Polymarket: ceasefire by Dec 31 at 74%. 27 X posts, 10 YouTube videos, 20 prediction markets.

**Before a trip.** `/last30days Universal Epic Universe` - Expansion already under construction. "Project 680" permit filed. Fireworks show confirmed by infrastructure but unannounced. Wait times: Mine-Cart Madness averaging 148 minutes. No annual pass yet, and locals are frustrated. Stardust Racers down for refurbishment through April 5.

**To learn something fast.** `/last30days Nano Banana Pro prompting` - JSON-structured prompts are replacing tag soup. @pictsbyai's nested format prevents "concept bleeding." Edit-first workflow beats regeneration. Then it writes you a production prompt using exactly what the community said works.

## What's new

See the [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) for the current published version and [CHANGELOG.md](CHANGELOG.md) for version-by-version changes. Unreleased changes on the default branch are recorded in [changelog.d/](changelog.d/).

This README describes the current source tree; your installed version may differ.

## Install

| Surface | Install | Updates |
|---------|---------|---------|
| **Claude Code** (recommended) | `/plugin marketplace add mvanhorn/last30days-skill` | Auto via marketplace, or `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` then `grok plugin install last30days` | `grok plugin update last30days` |
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

### Quick Try-Link

[Try Last30Days Research Skill in Telegram or WhatsApp](https://app.clawmama.run/skills/2ne05f/hermes?utm_source=github&utm_medium=issue&utm_campaign=skill_outreach_mvanhorn_last30days_skill)

The native plugin and the `npx skills` install can coexist. Note that Claude Code does not dedupe across install methods: if you have both the marketplace plugin and the `npx skills` copy active, `/last30days` will show two entries. Use one install method per machine.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) installs last30days as a native plugin. Direct install tracks the repository:

```bash
grok plugin install mvanhorn/last30days-skill
```

Or add this repo as a marketplace source, then install by plugin name:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Add `--trust` to skip the install confirmation. Update with `grok plugin update last30days`. Grok also reads the Claude Code manifests for compatibility; the native `.grok-plugin/` pair is the first-class lane (and what an official [xAI marketplace](https://github.com/xai-org/plugin-marketplace) listing points at). `npx skills add` remains a valid cross-host fallback. On Grok Bot, X works out of the box through the bot's built-in X tools, then the bot's X connector, with the official X API (`X_BEARER_TOKEN`) as backup.

### Codex, Cursor, Copilot, Gemini CLI, and other Agent Skills hosts

Install via the open [Agent Skills](https://agentskills.io) CLI — supports 50+ harnesses including `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`, and more (full list on the [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

The `-g` (global) flag installs to your user directory so the skill is available across all projects. Without `-g`, `npx skills` installs project-locally into `./.skills/` (committed with the repo). For a research-the-world tool, global is what you want.

Codex desktop and other folder-mode hosts can work in ordinary folders as well as Git repos. To inspect what a run would read and write without starting research, run the bundled `scripts/last30days.py --preflight` from the loaded skill directory; in a source checkout, the equivalent command is `python3 skills/last30days/scripts/last30days.py --preflight`. It shows the config source, browser-cookie plan, planned writes, optional commands, and ignored project config without reading cookies, writing files, or running research. First-run setup does not require it.

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

Reddit (with comments), Hacker News, Polymarket, and GitHub work immediately. Zero configuration. Run `/last30days` once and the setup wizard unlocks more sources in 30 seconds, including the free arXiv and Techmeme CLIs.

## Bring your own keys

These platforms don't have relationships with each other. X doesn't know what Reddit thinks. YouTube doesn't see TikTok. But you can bring your own API keys and browser tokens, and suddenly you have access to all of them at once.

| Sources | What you need | Cost |
|---------|---------------|------|
| Reddit (with comments) + HN + Polymarket + GitHub + StockTwits | Nothing | Free |
| arXiv + Techmeme | Free CLIs, auto-installed by first-run setup | Free |
| X / Twitter | Set `X_BEARER_TOKEN` for the official X API (recent posts, about the last week, unless your X developer project has full-archive access; a backup on Grok Bot after the bot's built-in X tools, opt-in elsewhere with `LAST30DAYS_X_BACKEND=xapi`), or log into x.com in any browser, or set `XQUIK_API_KEY` / `XAI_API_KEY` | X API credits come from your X developer project; browser cookies are free; other keys are provider-specific |
| YouTube | `brew install yt-dlp` | Free |
| Bluesky | App password from bsky.app | Free |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube comments | ScrapeCreators key | 10,000 free calls, then PAYG |
| Xiaohongshu (RED) | Run a logged-in x-mcp browser plugin or `xiaohongshu-mcp` service and opt in with `--search xhs` per run or `INCLUDE_SOURCES=xiaohongshu` in `.env`; last30days auto-probes `http://localhost:18060` then `http://host.docker.internal:18060`, or use `XIAOHONGSHU_API_BASE` for a custom URL | No last30days API key; depends on your local browser-session service |
| DripStack (premium financial newsletters) | Opt-in: `--search dripstack` per run, or `INCLUDE_SOURCES=dripstack` in `.env` | No key; free public search API |
| Perplexity Agent API / Search API / Deep Research | Perplexity key, or OpenRouter key as Sonar fallback | Pay as you go; a direct key enables Agent API and background Deep Research |
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

**Where research files are saved.** `LAST30DAYS_MEMORY_DIR` defaults to `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Override by setting that env var to any path in your shell, or `--save-dir <path>` per run. Use `--output <file>` when you need the rendered result at an exact path, using the format selected by `--emit`. Use `--save-suffix=<name>` to keep multiple variations of the same topic separate (e.g. per client). Each `--save-dir` run produces `<slug>-raw[-suffix].md`. Optionally run `python3 skills/last30days/scripts/last30days.py --preflight` to review planned writes without starting research.

**Structured output for agents and workflows.** Ask `/last30days` for machine-readable JSON to receive the stable, versioned agent profile. For direct engine use in scripts or development, run `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; add `--json-profile=raw` only when you need the unversioned internal `Report` dump. See the [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Topic-less discovery.** Ask `/last30days what's trending in AI agents?` to get a ranked discovery brief instead of researching a topic you already know - on an agent host this runs the three-command host-judged protocol (the model names topics, filters junk, scores worthiness, and writes the content angles). For direct engine use in scripts or cron, run `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (one-shot: deterministic topic names, no angles); add `--emit=json` for the versioned discovery contract. Discovery is mutually exclusive with a positional topic and `--drill`.

**Trend monitoring across runs.** The default mode produces a fresh markdown snapshot per run. To accumulate findings over time, add `--store` to persist into a SQLite database, then use [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) for scheduled runs (with optional Slack / webhook delivery on new findings) and [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) for daily / weekly digests. The full cadence pattern is in [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**A subscribable research library.** Ask `/last30days` to build your library feed, or use `python3 skills/last30days/scripts/last30days.py library feed` directly for scripting and development. It turns saved briefs into `index.html`, a local Atom `feed.xml`, and readable brief pages. Add `--publish` only when you want the HTML index and brief pages hosted; publishing is explicit opt-in and public by default. To make the Atom feed subscribable, host the generated output directory on a static host such as GitHub Pages.

**Search everything you've researched.** Ask `/last30days search my library for MCP servers` or `/last30days have I researched MCP servers before?`. For direct engine use, run `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. Search is offline and deterministic: it incrementally indexes the same saved briefs used by the library feed, merges matching per-run store sightings, and groups results by topic and date. Fresh runs also surface a compact **From your library** section when prior research overlaps the current topic; set `LAST30DAYS_LIBRARY_CONTEXT=off` to disable that passive context.

Per-client wrapper scripts, custom category-peer subreddits, and the experimental beta channel for in-progress customizations are also documented in [CONFIGURATION.md](CONFIGURATION.md).

## Showcase: community research feeds

Published a recurring AI update, market watch, or wonderfully narrow obsession with last30days? Share the public library URL—or the Atom URL after hosting `feed.xml` on a static host—in [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). Community feeds will be linked here as their owners submit them; the thread is the collection point in the meantime.

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

MIT license. No tracking. No analytics. Your research stays on your machine (see Data handling below).

For test commands, see [CONTRIBUTING.md](CONTRIBUTING.md#tests).

Built with Python 3.12+, yt-dlp, Node.js (vendored Bird client for X search), and ScrapeCreators API. v3 engine architecture by [@j-sperling](https://github.com/j-sperling).

See [CONTRIBUTING.md](CONTRIBUTING.md) to open a PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list of community contributors, and [CHANGELOG.md](CHANGELOG.md) for version history.

## Data handling

By default the skill runs entirely on your machine — no data leaves except to the search APIs you call (Reddit, ScrapeCreators, etc.).

**Reranking.** When you configure a reasoning provider (`OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_GENAI_API_KEY`), the engine sends your research topic and retrieved content snippets to that provider for relevance scoring. This is opt-in: if you set no reasoning key, the engine falls back to a local scoring model and no external reranking call is made. See [CONFIGURATION.md](CONFIGURATION.md) for provider priority.

**Local store.** When `LAST30DAYS_STORE=1` is set, findings are persisted to a local SQLite database named `research.db` inside the effective save directory (`--save-dir` takes precedence over `LAST30DAYS_MEMORY_DIR`). If no save directory is in effect, the database is `~/.local/share/last30days/research.db`. Enabling the store does not upload its database to a remote service. See [CONFIGURATION.md](CONFIGURATION.md#--store-flag) for retention details and purge instructions, including stopping database users before deletion.

## Star History

<a href="https://star-history.dera.page/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
