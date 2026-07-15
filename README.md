# /last30days

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
| **Digg** | Curated story clusters from Digg's AI 1000 leaderboard (~1000 high-signal AI accounts on X), with attributable inline quotes (no X auth required). Auto-enabled when `digg-pp-cli` is on PATH. |
| **arXiv** | The papers behind the hype. New research in the window, free, no API key. Auto-enabled when `arxiv-pp-cli` is on PATH (first-run setup installs it). |
| **Techmeme** | The tech-news editorial layer, date-windowed to your 30 days. Free, no API key. Auto-enabled when `techmeme-pp-cli` is on PATH (first-run setup installs it). |
| **LinkedIn** | The professional signal. Posts and articles, with articles weighted as high signal. |
| **StockTwits** | Trader sentiment. Auto-activates when your topic is a ticker or crypto. |
| **Threads** | The post-Twitter text layer. Conversations from creators and brands. |
| **Pinterest** | Visual discovery. Pins, saves, and comments on products and ideas. |
| **Xiaohongshu (RED)** | Chinese lifestyle, product, and creator signals. Requested explicitly with `--search xhs` when a logged-in x-mcp browser plugin or `xiaohongshu-mcp` service is running locally. |
| **Bluesky** | The decentralized social layer. AT Protocol posts from the post-Twitter migration. |
| **Perplexity** | Grounded Sonar synthesis, raw Search API rows, and Deep Research. |
| **Web** | The editorial coverage, the blog comparisons. One signal of many, not the only one. |

Community contributors keep adding more. Truth Social and other niche sources are in the engine with more on the way.

A Reddit thread with 1,500 upvotes is a stronger signal than a blog post nobody read. A TikTok with 3.6M views tells you more about what's culturally relevant than a press release. Polymarket odds backed by $66K in volume are harder to argue with than a pundit's guess.

The synthesis ranks by what real people actually engaged with. Social relevancy, not SEO relevancy.

## What people actually use it for

**Before a meeting.** `/last30days Peter Steinberger` - joined OpenAI's Codex team, fighting Anthropic's ban on third-party agents, 23 PRs merged at 85% merge rate on GitHub, building LobsterOS for cross-device agent control. r/ClaudeCode: "Ever since OpenClaw released, it was widely known that if you run it through anything other than the API, you were gonna get banned eventually" (227 upvotes). That's not on LinkedIn.

**To read hiring signals.** `/last30days Listen Labs --hiring-signals` - current jobs and careers pages become cited evidence for focus shifts: hiring into enterprise security, customer success, infrastructure, or product expansion. The report says what the hiring appears to signal, not what the roadmap will ship.

**To find the topic before it peaks.** Ask `/last30days what's exploding in AI agents?` and the skill switches to discovery mode: it sweeps Reddit category listings, Hacker News front/best stories, Digg's AI 1000 feed, and X when authenticated, then returns 5-10 engagement-velocity-ranked topics. Every result includes cross-source numbers, a momentum label, and a ready-to-run `/last30days "<topic>"` follow-up.

**When something drops.** `/last30days Kanye West` - UK blocked his visa, Wireless Festival canceled, sponsors fled. But BULLY debuted #2 on Billboard. Fantano came back from his "Yay sabbatical" to review it (653K views). SoFi Homecoming brought out Lauryn Hill and Travis Scott for 44 songs. Polymarket: "Will Kanye tweet again?" 86% Yes. 23 Reddit threads, 17 YouTube videos, 86K upvotes.

**To compare tools.** `/last30days OpenClaw vs Hermes vs Paperclip` - "These aren't competitors, they're layers." OpenClaw is the executor (351K GitHub stars, live), Hermes is the self-improving brain (31K stars), Paperclip is the org chart (49K stars). Star counts pulled live from the GitHub API, not stale blog posts. Side-by-side table with architecture, memory, security, best-for. Per @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**To understand the world.** `/last30days Iran vs USA` - Day 38 of the war. Trump's Tuesday deadline for Iran to reopen the Strait of Hormuz. Two US warplanes downed. Oil at $126/barrel. The IEA called it "the largest supply disruption in the history of the global oil market." Polymarket: ceasefire by Dec 31 at 74%. 27 X posts, 10 YouTube videos, 20 prediction markets.

**Before a trip.** `/last30days Universal Epic Universe` - Expansion already under construction. "Project 680" permit filed. Fireworks show confirmed by infrastructure but unannounced. Wait times: Mine-Cart Madness averaging 148 minutes. No annual pass yet, and locals are frustrated. Stardust Racers down for refurbishment through April 5.

**To learn something fast.** `/last30days Nano Banana Pro prompting` - JSON-structured prompts are replacing tag soup. @pictsbyai's nested format prevents "concept bleeding." Edit-first workflow beats regeneration. Then it writes you a production prompt using exactly what the community said works.

## What's new

Since the v3.3 announcement in May, as of v3.11.1 (July 2026): 175 merged PRs - 122 of them from 52 community contributors - across 15 releases. This is what landed.

### First-class on OpenAI Codex

/last30days is now a native Codex plugin with guided setup - not a port, a first-class citizen. Renderer-aware citations mean Codex output reads like a brief instead of URL soup (#694), and the same engine runs on Claude Code, Cursor, Copilot, Gemini CLI, Claude Desktop, OpenClaw, and 50+ Agent Skills hosts. Codex plugin manifest by [@rfoust](https://github.com/rfoust) (#686), Codex auth fix by [@tmchow](https://github.com/tmchow) (#698).

### arXiv, Techmeme, and Digg - free, no API keys

arXiv brings the papers behind the hype and Techmeme brings the editorial tech-news layer - free, zero keys, and first-run setup installs their CLIs so they activate automatically (#709). Digg's AI 1000 story clusters arrive without X auth the same way - setup installs the free Digg CLI for you (#590). Trustpilot ships opt-in for consumer-brand research.

### Free Reddit grew real scores and top comments

Reddit's public .json API died; the free path came back stronger. Keyless RSS + shreddit scraping (#457), dedicated-subreddit discovery with real upvote counts via arctic-shift (#696), and a relevance floor so a viral off-topic post can't hijack your brief (#488, thanks [@rzachsmith](https://github.com/rzachsmith)). No API key. Real scores. Top comments included.

### The best comments in every brief

Comments are now a default-on layer across sources: Instagram comments with rank-based diversity so five hot takes don't all come from one post (#751), YouTube comments plus a ScrapeCreators transcript backup for when yt-dlp strikes out (#637), and crowd-voted comments weighted into Best Takes so the community's funniest lines survive scoring (#592, #608).

### One doctor command

Ask for a health check and the doctor runs every source, then prescribes exact fixes - which key is missing, which CLI is off PATH, which cookie expired (#753). No more guessing why X came back thin.

### X search, rebuilt

The X pipeline got a ground-up overhaul: FROM and ABOUT lanes so a person's own posts and the conversation about them both rank (#610), person-aware subquery disambiguation (#611), first-party authorship grounding with interaction-signal ranking (#613), and a single X source with automatic backend failover (#622). Plus an honest `--diagnose` that actually probes auth (#609).

### More sources joined

LinkedIn via ScrapeCreators, with articles as high signal ([@ravstr](https://github.com/ravstr), #702). StockTwits auto-activates for ticker and crypto topics ([@wtiwana](https://github.com/wtiwana), #658). Perplexity grew direct API modes and async Deep Research ([@sk-holmes](https://github.com/sk-holmes), #629).

### Hardened by the community

The security wave was almost entirely community work: stored-XSS fixes in the HTML renderer ([@iliaal](https://github.com/iliaal), [@aaronjmars](https://github.com/aaronjmars)), locked-down cookie temp files, supply-chain-hardened CI with OpenSSF Scorecard and build provenance attestation ([@shaanmajid](https://github.com/shaanmajid), [@hammadxcm](https://github.com/hammadxcm), [@aniruddh909](https://github.com/aniruddh909)), Semgrep and OSV-Scanner scans plus a PR dependency-review gate ([@23241a6749](https://github.com/23241a6749)), a test-coverage floor introduced at 60% and since raised to 84% ([@gourab5139014](https://github.com/gourab5139014)), and a Hermes security scan cleared of every CRITICAL finding (#768).

### Reaches further

Hebrew and non-Latin languages ([@dudyme](https://github.com/dudyme)). CJK-aware tokenization for Chinese sources ([@An-idd](https://github.com/An-idd)). A Windows compatibility wave. Cookie extraction across the full Chromium family - Brave, Edge, Vivaldi, Opera, Arc ([@andrey-esipov](https://github.com/andrey-esipov)) - plus macOS Keychain and Linux pass(1) credential sources. `--as-of` historical lookback ([@chiyi-creator](https://github.com/chiyi-creator)). Auto-provisioned Python 3.12 via uv ([@buntysomroy](https://github.com/buntysomroy)). `--hiring-signals` for reading a company's job pages. Watchlist deltas between runs.

### Still in the box from v3

The v3 foundations are all still here: the pre-research brain that resolves the right handles, subreddits, and hashtags before a single API call fires (built by [@j-sperling](https://github.com/j-sperling)); Best Takes scoring for humor and virality alongside relevance; cross-source cluster merging; single-pass comparisons ("CLI vs MCP" in 3 minutes, not 12); auto-discovered `--competitors` comparisons; GitHub person-mode (`--github-user=steipete`); ELI5 mode ("eli5 on" after any run); and shareable, self-contained HTML briefs (`--emit=html`). Configuration knobs live in [CONFIGURATION.md](CONFIGURATION.md).

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

Add `--trust` to skip the install confirmation. Update with `grok plugin update last30days`. Grok also reads the Claude Code manifests for compatibility; the native `.grok-plugin/` pair is the first-class lane (and what an official [xAI marketplace](https://github.com/xai-org/plugin-marketplace) listing points at). `npx skills add` remains a valid cross-host fallback.

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

Reddit (with comments), Hacker News, Polymarket, and GitHub work immediately. Zero configuration. Run `/last30days` once and the setup wizard unlocks more sources in 30 seconds, including the free arXiv and Techmeme CLIs.

## Bring your own keys

These platforms don't have relationships with each other. X doesn't know what Reddit thinks. YouTube doesn't see TikTok. But you can bring your own API keys and browser tokens, and suddenly you have access to all of them at once.

| Sources | What you need | Cost |
|---------|---------------|------|
| Reddit (with comments) + HN + Polymarket + GitHub + StockTwits | Nothing | Free |
| arXiv + Techmeme | Free CLIs, auto-installed by first-run setup | Free |
| X / Twitter | Log into x.com in any browser, or set `XQUIK_API_KEY` / `XAI_API_KEY` | Browser cookies are free; keys are provider-specific |
| YouTube | `brew install yt-dlp` | Free |
| Bluesky | App password from bsky.app | Free |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube comments | ScrapeCreators key | 10,000 free calls, then PAYG |
| Xiaohongshu (RED) | Run a logged-in x-mcp browser plugin or `xiaohongshu-mcp` service and opt in with `--search xhs` per run or `INCLUDE_SOURCES=xiaohongshu` in `.env`; last30days auto-probes `http://localhost:18060` then `http://host.docker.internal:18060`, or use `XIAOHONGSHU_API_BASE` for a custom URL | No last30days API key; depends on your local browser-session service |
| DripStack (premium financial newsletters) | Opt-in: `--search dripstack` per run, or `INCLUDE_SOURCES=dripstack` in `.env` | No key; free public search API |
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

**Structured output for agents and workflows.** Ask `/last30days` for machine-readable JSON to receive the stable, versioned agent profile. For direct engine use in scripts or development, run `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; add `--json-profile=raw` only when you need the unversioned internal `Report` dump. See the [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Topic-less discovery.** Ask `/last30days what's trending in AI agents?` to get a ranked discovery brief instead of researching a topic you already know. For direct engine use in scripts or development, run `python3 skills/last30days/scripts/last30days.py --discover "AI agents"`; add `--emit=json` for the versioned discovery contract. Discovery is mutually exclusive with a positional topic and `--drill`.

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

MIT license. No tracking. No analytics. Your research stays on your machine. 2,700+ tests.

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
