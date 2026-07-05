# Step 0.55: Pre-Research Intelligence

> Extracted from SKILL.md for Hermes 100KB compatibility.

## Step 0.55: Pre-Research Intelligence (resolve communities + handles)

> **PLATFORM GATE:** If your platform does NOT support WebSearch (e.g., OpenClaw, raw CLI), **skip Steps 0.55 and 0.75** but add `--auto-resolve` to the Python command in the Research Execution section. The engine will do its own pre-research using configured web search backends (Brave, Exa, or Serper) to discover subreddits, X handles, and current events context before planning.

**MANDATORY on Claude Code (and any platform with WebSearch).** You MUST perform Step 0.55 before calling the Python engine. Skipping this step is the second-most-common failure mode of this skill, right after skipping the engine entirely. If your Bash call to `last30days.py` does NOT include a `--plan` flag with resolved handles and subreddits, that is a Step 0.55 skip and a failure. The engine's `[Resolve] No web search backend available, skipping resolve` log line means you, the model, did not do your job - it does NOT mean "the engine will handle it." Treat this step as non-skippable. Repeat invocations on the same topic still re-run Step 0.55 because Reddit/X/TikTok handles for breaking-news topics change week to week.

**Run 2-3 focused WebSearches (in parallel) to resolve platform-specific targeting. Do NOT search for every platform individually - that wastes time. Instead, use your knowledge of the topic to infer most targeting, and only WebSearch for what you can't infer.**

**1. X handles** - Already resolved in Step 0.5 above (including company handles and commentators). Reference your `RESOLVED_HANDLE` and `RESOLVED_RELATED` from that step.

**2. Reddit communities + YouTube channels + current events** - Run 1-2 searches that cover multiple platforms at once:

```
WebSearch("{TOPIC} subreddit reddit community")
WebSearch("{TOPIC} news {CURRENT_MONTH} {CURRENT_YEAR}")
```

The first search finds subreddits. The second gives you current events context (which helps you generate better subqueries in Step 0.75) and may surface YouTube channels or creators organically.

Extract 3-5 subreddit names from the results. Store as `RESOLVED_SUBREDDITS` (comma-separated, no r/ prefix).

**Dedicated vs broad subreddits.** Split the resolved subs into two buckets:
- **Dedicated** = subreddits whose entire purpose IS the topic (the entity's home: `r/Kanye` / `r/WestSubEver` / `r/GoodAssSub` for "Kanye West", `r/OpenClaw` for OpenClaw). Every post there is on-topic. Store as `RESOLVED_DEDICATED_SUBREDDITS` and pass via `--dedicated-subreddits`. The engine pulls these in full (top+hot+new) and skips the relevance floor for them, so an on-topic post whose title lacks the entity name (a "BULLY Deluxe" thread in r/Kanye) is not dropped.
- **Broad** = mixed-content communities where the topic is only sometimes discussed (`r/hiphopheads`, `r/Music`, category peers from 2a). Store as `RESOLVED_SUBREDDITS` and pass via `--subreddits`. These stay relevance-floored.
Label conservatively: only a sub clearly named for / dedicated to the entity goes in the dedicated bucket. Most topics have 0-3 dedicated subs (people and products often have one; generic concepts have none). When unsure, treat it as broad.

**2a. Category-peer expansion (MANDATORY for product topics).** If the topic is a product in a recognizable category (AI image generation, AI video generation, AI coding agents, AI music, AI chat models, SaaS screen recording, prediction markets, etc.), the brand-specific subreddits that WebSearch returned are INSUFFICIENT. Add 2-3 peer subreddits from the category. Peer subs are where cross-product technique discussion actually lives. Missing them is the 2026-04-22 `GPT Image 2` failure mode: the model resolved `r/OpenAI, r/ChatGPT, r/singularity, r/ChatGPTpromptengineering` (all OpenAI-brand) and missed `r/StableDiffusion, r/midjourney, r/dalle2, r/aiArt` where prompting techniques are actually shared. The user had to manually prompt "check image generation reddits too" to get a usable run.

Canonical category peers (single source of truth; `scripts/lib/categories.py` mirrors this for the `--auto-resolve` engine path):

| Category | Trigger keywords | Peer subs (priority order) |
|----------|------------------|---------------------------|
| `ai_image_generation` | image generation, text to image, GPT Image, Nano Banana, Midjourney, Stable Diffusion, DALL-E, Flux.1, Imagen, Seedance, Ideogram, Recraft | `StableDiffusion, midjourney, dalle2, aiArt, PromptEngineering, MediaSynthesis` |
| `ai_video_generation` | video generation, text to video, Sora, Veo 3, Runway Gen, Kling, Pika Labs, Luma Dream Machine, Hailuo | `aivideo, StableDiffusion, runwayml, singularity, MediaSynthesis` |
| `ai_music_generation` | music generation, ai music, Suno, Udio, Riffusion, Stable Audio | `SunoAI, udiomusic, aimusic, artificial` |
| `ai_coding_agent` | Claude Code, Cursor IDE, GitHub Copilot, Windsurf, Aider, Cline, OpenClaw, Hermes Agent, Continue.dev, Codeium, Devin | `ChatGPTCoding, LocalLLaMA, singularity, PromptEngineering` |
| `ai_agent_framework` | agent framework, LangChain, LangGraph, CrewAI, AutoGen, LlamaIndex, DSPy, smolagents | `LangChain, LocalLLaMA, AI_Agents, MachineLearning` |
| `ai_chat_model` | GPT-5/4, Claude Opus/Sonnet/Haiku, Gemini Pro/Flash, Llama 3/4, DeepSeek, Qwen, Mistral Large, Grok | `LocalLLaMA, ChatGPT, ClaudeAI, singularity, artificial` |
| `saas_screen_recording` | screen recording, screen recorder, Loom video, Tella screen, Vidyard | `SaaS, screenrecording, productivity, Entrepreneur` |
| `saas_productivity` | Notion app, Obsidian, Linear app, Asana, ClickUp, productivity app | `productivity, SaaS, ObsidianMD, Notion` |
| `prediction_markets` | Polymarket, Kalshi, prediction market, event contracts, Manifold Markets | `Polymarket, Kalshi, predictionmarkets` |
| `crypto_defi` | DeFi protocol, yield farming, liquidity pool, stablecoin, layer 2, L2 rollup | `defi, ethfinance, CryptoCurrency, ethereum` |

**Merging rule.** Start with WebSearch-returned subs. Append 2-3 category peers in the priority order shown. Dedupe case-insensitively (don't list `midjourney` twice if WebSearch already returned it). Cap total at 10: if adding all peers would exceed the cap, keep every WebSearch-returned sub (they are the freshest signal) and drop peers from the end of the priority list.

**Extrapolation.** If the topic is a product in a category NOT listed in the table (new AI tool, niche SaaS), use the same spirit: pick the 2-3 most active cross-product communities where technique discussion happens. A new image-gen tool still gets `r/StableDiffusion, r/midjourney, r/aiArt`. A new code editor still gets `r/ChatGPTCoding, r/LocalLLaMA`.

**Worked example — the failing query.** Topic: `Prompting GPT Image 2`.

Before (the 2026-04-22 failure mode):
```
Resolved:
- Reddit: r/OpenAI, r/ChatGPT, r/singularity, r/ChatGPTpromptengineering, r/artificial
```

After (with category-peer expansion):
```
Resolved:
- Reddit: r/OpenAI, r/ChatGPT, r/singularity, r/ChatGPTpromptengineering, r/StableDiffusion, r/midjourney, r/dalle2, r/aiArt (+ ai_image_generation peers)
```

The parenthetical `(+ ai_image_generation peers)` is the observable contract of the new Resolved block format. See Step 0.55 self-check below.

**3. TikTok hashtags + creators** - **INFER these from your topic knowledge. Do NOT WebSearch for "{PERSON} TikTok account" - most people/CEOs don't have TikTok, and the search is wasted.**

- **Hashtags:** Infer 2-3 from the topic name + category. Examples: "Kanye West" → `kanyewest,ye,bully`. "Claude Code" → `claudecode,aiagent,aicoding`. "Sam Altman" → `samaltman,openai,chatgpt`.
- **Creators:** Only search if the topic is a content creator, influencer, or brand that likely has TikTok presence. For CEOs, politicians, and non-creator people: skip.

Store as `RESOLVED_HASHTAGS` and `RESOLVED_TIKTOK_CREATORS`.

**4. Instagram creators** - **Same rule: INFER from topic knowledge.** If the topic is a celebrity, brand, or creator with obvious Instagram presence, use their handle directly. If the topic is a tech CEO or abstract concept, skip. Do NOT waste a WebSearch on "Dario Amodei Instagram account."

Store as `RESOLVED_IG_CREATORS`.

**5. YouTube content queries** - Infer 2-3 YouTube content-type queries from the topic without searching. The current events search (#2 above) may surface relevant YouTube channels.

- **For music artists:** `'{TOPIC} album review'`, `'{TOPIC} reaction'`
- **For products/SaaS:** `'{TOPIC} review'`, `'{TOPIC} tutorial'`
- **For comparisons:** `'{TOPIC_A} vs {TOPIC_B}'`
- **For people in the news:** `'{TOPIC} interview {YEAR}'`, `'{TOPIC} latest news'`

Store as `RESOLVED_YT_QUERIES`.

**6. First-party positioning** - **MANDATORY when WebSearch is available, for company / product / service topics.** If the topic (or, in a vs-run, an entity) is a company, product, or service with a public presence, fetch its CURRENT stated positioning. Do **NOT** rely on memory - homepages and positioning go stale as companies rewrite copy and pivot, and a stale claim produces a false gap. Anchor on first-party sources: the homepage tagline, docs, pricing, or a "compare/why-us" page. Fold this into the per-entity passes above where you can (e.g. add `official site` to a query); otherwise run one focused search per entity (`{TOPIC} official site`, `{TOPIC} pricing`). Capture the one-line value prop and any explicit claims ("zero-config", "fastest", "open source"). Store as `RESOLVED_POSITIONING`. This is what the entity *pitches*; the engine's community data is what people *actually talk about*. Use it three ways: ground `What it is` descriptions (describe the entity as it pitches itself TODAY, not as remembered), help reject unrelated brand-name noise (knowing what the entity is makes off-brand matches obvious), and feed the pitch-vs-pulse synthesis beat - a PROSE note that fires only when the month's evidence directly supports, cuts against, or is squarely about the pitch (see the synthesis section; orthogonal evidence gets silence, not a verdict). Skip (and omit `RESOLVED_POSITIONING`) for people, events, abstract concepts, and ownerless topics - they make no comparable public claim. The test is an identifiable first party with a fetchable pitch, and people NEVER pass it - not even founders/creators whose companies would qualify. The lens can apply to MrBeast (a company) but never to Jimmy Donaldson (a person); a person-vs-person run ("Garry Tan vs Sam Altman") gets no positioning research at all. Ownerless topics fail the same test: Bitcoin has no authoritative first party, and a foundation or fan site does not count.

**Concrete examples:**

| Topic | WebSearches needed | Reddit subs | TikTok hashtags | TikTok creators | IG creators | YT queries |
|-------|-------------------|-------------|-----------------|-----------------|-------------|------------|
| **Kanye West** | 2 (subreddit + BULLY news) | `Kanye,WestSubEver,hiphopheads,Music` | `kanyewest,ye,bully` | (inferred: `kanyewest`) | (inferred: `kanyewest`) | `kanye west bully review,kanye west bully reaction` |
| **Sam Altman vs Dario** | 2 (subreddit + AI CEO news) | `artificial,MachineLearning,OpenAI,ClaudeAI` | `samaltman,openai,anthropic` | (skip - CEOs don't TikTok) | (skip - CEOs don't Reel) | `sam altman interview 2026,dario amodei interview 2026` |
| **Tella** (SaaS) | 2 (subreddit + Tella news) | `SaaS,Entrepreneur,screenrecording,productivity` | `tella,tellaapp,screenrecording` | (search: `tella screen recorder TikTok`) | (inferred: `tella.tv`) | `tella screen recorder review,tella tutorial` |

**For comparison queries ("X vs Y" or "X vs Y vs Z") - MANDATORY per-entity resolution:**

For each entity in the comparison, resolve all four lookup types. For a 3-way comparison that is up to 12 lookups (3 entities x 4 types). Batch them into 3-4 WebSearch calls by combining entities per query - do NOT fire one search per entity per type (that produces 12 searches and burns 90 seconds).

Per-entity lookup types to resolve:

1. **Project X handle** - the project's official or primary X/Twitter account
2. **Project GitHub repo** - `owner/repo` format (e.g., `openai/openai-python`)
3. **Founder/maintainer X handle** - the person or team behind the project
4. **Relevant subreddits** - project-specific subreddits (e.g., `r/openclaw`) AND general-category subreddits (e.g., `r/LocalLLaMA`)
5. **Trustpilot domain** (when the entity is a company/brand/service and you want review evidence) - the entity's Trustpilot review-page domain per Step 0.5d; peers carry it as `trustpilot_domain` in their `--competitors-plan` entry, the main topic via the outer `--trustpilot-domain` flag (either pin auto-activates Trustpilot for the run)

Example batching for "OpenClaw vs Hermes vs Paperclip":

```
WebSearch("OpenClaw Hermes Paperclip github repos AI coding agent")
WebSearch("OpenClaw Hermes Paperclip founders twitter X handles")
WebSearch("OpenClaw Hermes Paperclip reddit subreddits community")
```

Three searches for 12 lookups. After resolving, display all 12 per-entity in the Resolved block before running the engine:

```
Resolved (comparison):
- OpenClaw: X @openclawai | GitHub openclaw/openclaw | Founder @steipete | Reddit r/openclaw, r/AI_Agents
- Hermes: X @hermesagent | GitHub nousresearch/hermes | Founder @NousResearch | Reddit r/hermesagent, r/LocalLLaMA
- Paperclip: X @paperclipai | GitHub dotta/paperclip | Founder @dotta | Reddit r/OpenClawInstall
```

Passing the resolved block visibly (per-entity, all 4 types each) is the observable check that Step 0.55 happened for this comparison. A Resolved block that only lists 3 project handles with no founders and no GitHub repos is a Step 0.55 regression. This was canonical behavior and must stay canonical.

**For non-comparison queries:** Resolve communities/handles for the single topic. Merging list logic does not apply.

**If you can't infer targeting for a platform, skip that flag -- the Python engine will fall back to keyword search.**

**Step 0.55 self-check: category-peer coverage.** Before emitting the Resolved block, re-read your resolved subreddit list. Does the topic match any category in the Section 2a table (or fit the spirit of one — AI image gen, AI coding, AI music, etc.)? If YES: does your list include AT LEAST 2 peer subs from that category? If NO, widen the list NOW — do not run the engine yet. The observable contract is the `(+ {category_id} peers)` annotation on the Reddit line in the Resolved block. Its absence on a product-in-a-known-category topic is a Step 0.55 regression — the named 2026-04-22 failure mode. Person topics, music artists, news stories, and topics outside any category are exempt; omit the annotation.

**After resolving all handles and communities, display what you found before moving on.** This shows the user that intelligent pre-research happened:

```
Resolved:
- X: @{HANDLE} (+ @{COMPANY}, @{COMMENTATOR})
- Reddit: r/{sub1}, r/{sub2}, r/{sub3}, r/{peer1}, r/{peer2} (+ {category_id} peers)
- TikTok: #{hashtag1}, #{hashtag2}
- YouTube: {query1}, {query2}
- Trustpilot: {domain}
- Positioning: "{one-line stated value prop}" (first-party)
```

Only show lines for platforms where something was resolved. Skip empty lines. On the Reddit line, the trailing `(+ {category_id} peers)` annotation appears when Step 0.55 Section 2a added category-peer subs. Omit the annotation when the topic had no matching category. The `Positioning:` line appears for company / product / service topics (from Step 0.55 item 6); omit it for people, events, abstract concepts, and ownerless topics. The `Trustpilot:` line appears only when Step 0.5d resolved a domain (company/brand topic with the Trustpilot source active). This display replaces the old "Parsed intent" block with something more useful.

---
