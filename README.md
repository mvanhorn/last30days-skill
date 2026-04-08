# /last30days

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

Claude Code:
```
/plugin marketplace add mvanhorn/last30days-skill
```

OpenClaw:
```
clawhub install last30days-official
```

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
| **Threads** | The post-Twitter text layer. Conversations from creators and brands. |
| **Pinterest** | Visual discovery. Pins, saves, and comments on products and ideas. |
| **Bluesky** | The decentralized social layer. AT Protocol posts from the post-Twitter migration. |
| **Perplexity** | Grounded web search with citations via Sonar Pro. |
| **Web** | The editorial coverage, the blog comparisons. One signal of many, not the only one. |

Community contributors keep adding more. Truth Social, Xiaohongshu (RED), and others are in the engine with more on the way.

A Reddit thread with 1,500 upvotes is a stronger signal than a blog post nobody read. A TikTok with 3.6M views tells you more about what's culturally relevant than a press release. Polymarket odds backed by $66K in volume are harder to argue with than a pundit's guess.

The synthesis ranks by what real people actually engaged with. Social relevancy, not SEO relevancy.

## What people actually use it for

**Before a meeting.** `/last30days Peter Steinberger` - joined OpenAI's Codex team, fighting Anthropic's ban on third-party agents, 23 PRs merged at 85% merge rate on GitHub, building LobsterOS for cross-device agent control. r/ClaudeCode: "Ever since OpenClaw released, it was widely known that if you run it through anything other than the API, you were gonna get banned eventually" (227 upvotes). That's not on LinkedIn.

**When something drops.** `/last30days Kanye West` - UK blocked his visa, Wireless Festival canceled, sponsors fled. But BULLY debuted #2 on Billboard. Fantano came back from his "Yay sabbatical" to review it (653K views). SoFi Homecoming brought out Lauryn Hill and Travis Scott for 44 songs. Polymarket: "Will Kanye tweet again?" 86% Yes. 23 Reddit threads, 17 YouTube videos, 86K upvotes.

**To compare tools.** `/last30days OpenClaw vs Hermes vs Paperclip` - "These aren't competitors, they're layers." OpenClaw is the executor (351K GitHub stars, live), Hermes is the self-improving brain (31K stars), Paperclip is the org chart (49K stars). Star counts pulled live from the GitHub API, not stale blog posts. Side-by-side table with architecture, memory, security, best-for. Per @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**To understand the world.** `/last30days Iran vs USA` - Day 38 of the war. Trump's Tuesday deadline for Iran to reopen the Strait of Hormuz. Two US warplanes downed. Oil at $126/barrel. The IEA called it "the largest supply disruption in the history of the global oil market." Polymarket: ceasefire by Dec 31 at 74%. 27 X posts, 10 YouTube videos, 20 prediction markets.

**Before a trip.** `/last30days Universal Epic Universe` - Expansion already under construction. "Project 680" permit filed. Fireworks show confirmed by infrastructure but unannounced. Wait times: Mine-Cart Madness averaging 148 minutes. No annual pass yet, and locals are frustrated. Stardust Racers down for refurbishment through April 5.

**To learn something fast.** `/last30days Nano Banana Pro prompting` - JSON-structured prompts are replacing tag soup. @pictsbyai's nested format prevents "concept bleeding." Edit-first workflow beats regeneration. Then it writes you a production prompt using exactly what the community said works.

## What's New in v3

### Intelligent search: the killer feature

The v3 engine doesn't just search for your topic. It figures out *where* to search before the search begins. Type "OpenClaw" and the engine resolves @steipete (Peter Steinberger, the creator), r/openclaw, r/ClaudeCode, and the right YouTube channels and TikTok hashtags - all via a new Python pre-research brain built by [@j-sperling](https://github.com/j-sperling). The old engine searched keywords. The new engine understands your topic first, then searches the right people and communities.

This is why v3 finds content v2 never could. "Paperclip" resolves @dotta. "Dave Morin" resolves @davemorin plus @OpenClaw plus the TWiST podcast. "Peter Steinberger" resolves @steipete on X and steipete on GitHub. Bidirectional: person to company, product to founder, name to GitHub profile. The right subreddits, the right handles, the right hashtags - resolved before a single API call fires.

### Best Takes

Reddit and X people are funny. The old engine buried their best stuff because it scored for relevance, not cleverness. v3 has a second judge that scores every result for humor, wit, and virality alongside the relevance score. Tommy Lloyd's "My Michael Jordan is Steve Kerr" scores low on relevance to "Arizona Basketball" but off the charts on fun. Now every brief ends with a "Best Takes" section - the cleverest one-liners, the most viral quotes, the reactions that make you want to share the research. Built in, not a toggle.

### Cross-source cluster merging

When the same story appears on Reddit, X, and YouTube, v3 merges them into one cluster instead of showing three separate items. Entity-based overlap detection catches matches even when the titles use different words.

### Single-pass comparisons

"CLI vs MCP" used to run three serial passes (12+ minutes). v3 runs one pass with entity-aware subqueries for both sides simultaneously. Same depth, 3 minutes.

### GitHub person-mode

When the topic is a person, the engine switches from keyword search to author-scoped queries. Instead of "who mentioned this name in an issue body," it answers: what are they shipping and where is it landing?

`/last30days Peter Steinberger --github-user=steipete` shows 22 PRs merged across 3 repos at 85% merge rate. Own projects with README summaries, star counts, and top feature requests. Release notes for what shipped this month. The synthesizer weaves it into the narrative alongside X posts and Reddit threads.

### ELI5 mode

Say "eli5 on" after any research run. The synthesis rewrites in plain language. No jargon. Same data, same sources, same citations - just clearer. "Arizona wins by being physical" instead of "Arizona's identity is paint scoring (50%+ shooting, 9th nationally)." Say "eli5 off" to go back.

### Everything else in v3

- **Free Reddit comments.** Public JSON gives you threads + top comments with upvote counts. No API key, no ScrapeCreators. Just works.
- **YouTube transcripts that actually work.** Widened candidate pool 3x past music videos to reach talk/review content with captions.
- **Threads, Pinterest, YouTube comments.** Opt-in sources via ScrapeCreators. Set `INCLUDE_SOURCES=tiktok,instagram` and add threads, pinterest, youtube_comments for more.
- **Perplexity Sonar.** Grounded web search with citations via OpenRouter. Add `OPENROUTER_API_KEY` to unlock.
- **Polymarket noise filtering.** Common-word disambiguation prevents "Apple" from matching "Will Apple release a car?"
- **Resilient Reddit.** Timeout budgets and runtime fallback. One slow thread doesn't kill the whole run.
- **Fun judge v2.** Humor scoring baked into the narrative. Reddit's cleverest one-liners mixed into the synthesis where they fit, not dumped in a separate section.
- **Polymarket odds, not dollars.** The % odds are the magic. Dollar volumes removed from display.
- **Per-author cap.** Max 3 items per author prevents any single voice from dominating your brief.
- **Entity disambiguation.** When the engine resolves handles, the synthesis trusts them. No more Mallorca resorts winning over Washington athletic clubs.
- **OpenClaw first-class citizen.** Auto-resolve for engine-side pre-research. Device auth for frictionless ScrapeCreators signup.
- **1,012 tests passing.**

## Install

### Claude Code
```
/plugin marketplace add mvanhorn/last30days-skill
```

### OpenClaw
```bash
clawhub install last30days-official
```

### Manual
```bash
git clone https://github.com/mvanhorn/last30days-skill.git ~/.claude/skills/last30days
```

Reddit (with comments), Hacker News, Polymarket, and GitHub work immediately. Zero configuration. Run `/last30days` once and the setup wizard unlocks more sources in 30 seconds.

## Bring your own keys

These platforms don't have relationships with each other. X doesn't know what Reddit thinks. YouTube doesn't see TikTok. But you can bring your own API keys and browser tokens, and suddenly you have access to all of them at once.

| Sources | What you need | Cost |
|---------|---------------|------|
| Reddit (with comments) + HN + Polymarket + GitHub | Nothing | Free |
| X / Twitter | Log into x.com in any browser | Free |
| YouTube | `brew install yt-dlp` | Free |
| Bluesky | App password from bsky.app | Free |
| TikTok + Instagram + Threads + Pinterest + YouTube comments | ScrapeCreators key | 10,000 free calls |
| Perplexity Sonar | OpenRouter key | Pay as you go |
| Web search | Brave Search key | 2,000 free queries/month |

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

See [CHANGELOG.md](CHANGELOG.md) for version history.

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
