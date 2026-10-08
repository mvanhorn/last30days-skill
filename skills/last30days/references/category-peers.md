# Category-peer expansion

Read for an identifiable product in a recognizable category during Step 0.55. Eligibility and the requirement to expand remain in the research runbook.

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

The parenthetical `(+ ai_image_generation peers)` is the observable contract of the new Resolved block format. See the Step 0.55 category-peer self-check in the research runbook.
