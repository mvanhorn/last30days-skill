# /last30days

**The AI world reinvents itself every month. This Claude Code skill keeps you current.** /last30days researches your topic across Reddit, X, and the web from the last 30 days, finds what the community is actually upvoting and sharing, and writes you a prompt that works today, not six months ago. Whether it's Ralph Wiggum loops, Suno music prompts, or the latest Midjourney techniques, you'll prompt like someone who's been paying attention.

**Best for prompt research**: discover what prompting techniques actually work for any tool (ChatGPT, Midjourney, Claude, Figma AI, etc.) by learning from real community discussions and best practices.

**But also great for anything trending**: music, culture, news, product recommendations, viral trends, or any question where "what are people saying right now?" matters.

## Installation

### Option 1: Claude Code Plugin (Recommended)

```bash
# Add the marketplace
claude plugin marketplace add mvanhorn/last30days-skill

# Install the plugin
claude plugin install last30days@l30d
```

### Option 2: Manual Installation

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
cd last30days-skill/last30days
# Follow setup instructions below
```

### API Keys (Optional but Recommended)

```bash
mkdir -p ~/.config/last30days
cat > ~/.config/last30days/.env << 'EOF'
OPENAI_API_KEY=sk-...
XAI_API_KEY=xai-...
EOF
chmod 600 ~/.config/last30days/.env
```

The skill works without API keys (WebSearch fallback), but adding them unlocks Reddit and X research with real engagement metrics.

## Usage

```
/last30days [topic]
/last30days [topic] for [tool]
```

Examples:
- `/last30days prompting techniques for ChatGPT for legal questions`
- `/last30days iOS app mockups for Nano Banana Pro`
- `/last30days What are the best rap songs lately`
- `/last30days remotion animations for Claude Code`

## What It Does

1. **Researches** - Scans Reddit and X for discussions from the last 30 days
2. **Synthesizes** - Identifies patterns, best practices, and what actually works
3. **Delivers** - Either writes copy-paste-ready prompts for your target tool, or gives you a curated expert-level answer

### Use it for:
- **Prompt research** - "What prompting techniques work for legal questions in ChatGPT?"
- **Tool best practices** - "How are people using Remotion with Claude Code?"
- **Trend discovery** - "What are the best rap songs right now?"
- **Product research** - "What do people think of the new M4 MacBook?"
- **Viral content** - "What's the dog-as-human trend on ChatGPT?"

---

## Example: Legal Prompting (Hallucination Prevention)

**Query:** `/last30days prompting techniques for chatgpt for legal questions`

**Research Output:**
> The dominant theme is hallucination prevention - multiple sources discuss lawyers being fined or embarrassed for submitting fake case citations that ChatGPT fabricated. Key prompting strategies include: (1) using Deep Research mode or grounding tools with uploaded primary sources, (2) building "Hallucination Prevention Systems" into prompts that force epistemic honesty, (3) prompting for procedural questions rather than case law lookups, and (4) framing outputs as "issue-spotting" rather than legal advice. A major warning: chat logs are discoverable in litigation.

**Key patterns discovered:**
1. Hallucination prevention clauses - "If you don't know a case citation, say so rather than inventing one"
2. Role assignment - assign ChatGPT a specific persona (paralegal, issue-spotter) to constrain outputs
3. Structured output requirements - request specific sections (risks, key terms, questions for counsel)
4. Epistemic humility enforcement - "acknowledge uncertainty" and "flag when you're unsure"
5. Scope limitation - focus on procedural questions, not case law research

**Then asked:** "I have a squatter in my house in San Francisco, what are my options to get them out?"

**Generated Prompt:**

```
Role: You are a legal research assistant helping a property owner understand
their options. You are NOT providing legal advice - you are helping me
identify issues, understand procedures, and prepare informed questions for an
attorney.

Situation: I own a residential property in San Francisco, California. Someone
is currently occupying the property without my permission or any lease
agreement (a squatter). I need to understand my legal options for removing them.

Your task: Provide a structured analysis covering:

1. California/SF Legal Framework - What laws govern this situation? (Note: San
Francisco has strong tenant protections - address how these apply or don't
apply to squatters vs. tenants)
2. Key Distinctions - Help me understand: squatter vs. trespasser vs. holdover
tenant. How does the length of occupation affect my options?
3. Available Legal Remedies - What are my procedural options? (unlawful
detainer, police involvement, etc.) What is the typical process and timeline?
4. Critical Mistakes to Avoid - What actions could expose me to liability?
(self-help eviction, utility shutoffs, etc.)
5. Questions I Should Ask an Attorney - Based on this analysis, what specific
questions should I bring to a California real estate attorney?

Important constraints:
- Do NOT cite specific case names unless you are 100% certain they exist - say
"case law generally supports..." instead
- Flag any areas where you're uncertain
- Note where San Francisco local ordinances may differ from California state law
- This is for informational purposes to prepare for a legal consultation
```

**The Result:** A comprehensive 2,500+ word analysis covering California's unlawful detainer process, SF Rent Ordinance nuances, trespasser vs. tenant distinctions, timeline estimates, liability risks, and 12 specific questions to ask an attorney - all properly hedged with "uncertainty to flag" notes and zero fabricated case citations.

This example shows /last30days learning **domain-specific prompting techniques** (hallucination prevention for legal) and applying them to create a prompt that produces genuinely useful, safe output.

---

## Example: ClawdBot Use Cases (Community Research)

**Query:** `/last30days best clawdbot use cases`

**Research Output:**
> ClawdBot is a self-hosted AI assistant that runs on your devices and connects via messaging apps (Telegram, WhatsApp, Discord, Slack, iMessage). Unlike ChatGPT/Claude, it has persistent memory and can proactively message you. The killer feature is its 24/7 autonomous operation - it handles multi-day tasks, monitors for replies, and initiates contact.

**Most mentioned use cases:**

🏆 Top 5 by engagement:

1. **Email/Calendar automation** - mentioned 8x+ (r/selfhosted, @dreetje, @danpeguine, MacStories)
   - Morning briefings, spam filtering, auto-replies, recruiter declines
2. **Task management via chat** - mentioned 6x (r/LocalLLaMA, @danpeguine, multiple X posts)
   - Todoist integration, importance scoring, time-blocking tasks
3. **Overnight coding agent** - mentioned 5x (@bffmike 225 likes, r/LocalLLaMA)
   - "Drove my coding agents from 12:30-7am while I snoozed"
   - Pulls repos, runs tests, commits if clean
4. **Smart home + life admin** - mentioned 5x (r/selfhosted, VelvetShark)
   - WHOOP health data, Philips Hue, HomeAssistant, SSH to homelab
5. **Browser automation** - mentioned 4x (@steipete 243 likes, MacStories)
   - Car negotiation via email/PDF, restaurant reservations via ElevenLabs calls

**Notable single mentions:**
- Custom meditation generation with TTS + ambient audio
- Built a full website (Stumblereads.com) from phone while putting baby to sleep
- Ray-Ban Meta glasses integration
- WhatsApp as control interface for everything

**Research Stats:** 9 Reddit threads (161 upvotes, 95 comments) + 19 X posts (2,018 likes, 96 reposts) from r/LocalLLaMA, r/selfhosted, @steipete, @danpeguine, @dreetje

This example shows /last30days as a **product research tool** - discovering real-world use cases and community sentiment for any tool or product, complete with engagement metrics that tell you what people actually care about.

---

## Example: ClawdBot Setup (Installation Guide)

**Query:** `/last30days how to best setup clawdbot`

**Research Output:**
> ClawdBot setup varies by deployment style - Docker is the most recommended path, but users are also deploying via bare metal and cloud VMs. The community has converged on key best practices: use the setup wizard for initial config, start with conservative hardware (you can scale up), and configure authentication before exposing any endpoints.

**🛠️ Setup patterns by engagement:**

| Pattern | Mentions | Key Sources |
|---------|----------|-------------|
| Docker Compose deployment | 6x | r/selfhosted, r/LocalLLaMA |
| Setup wizard (interactive config) | 5x | @dreetje, documentation |
| API key management | 4x | r/selfhosted, security threads |
| Hardware requirements | 4x | r/LocalLLaMA (GPU discussions) |
| Reverse proxy (Caddy/nginx) | 3x | r/selfhosted |

**Key setup insights from community:**

1. **Use the setup wizard** - Don't manually edit config files first; the wizard handles 90% of initial setup
2. **Start small on hardware** - 8GB RAM minimum, but 16GB+ recommended if running local models
3. **Docker > bare metal** - Easier updates, better isolation, community-tested compose files
4. **Secure before exposing** - Set up authentication before opening ports; ClawdBot has built-in auth
5. **Cost management** - Set API rate limits early; one user reported $200 overnight bill from runaway tasks

**Quick-start commands (community-verified):**

```bash
# Clone and setup
git clone https://github.com/clawdbot/clawdbot.git
cd clawdbot

# Run setup wizard (recommended)
./setup.sh

# Or Docker Compose (after config)
docker compose up -d
```

**Common gotchas mentioned:**
- Don't forget to set `CLAWDBOT_API_KEY` before first run
- Telegram bot token needs BotFather setup first
- If using local models, ensure CUDA drivers are installed

**Research Stats:** 8 Reddit threads (128 upvotes) + 22 X posts (24,000+ likes) from r/selfhosted, r/LocalLLaMA, @dreetje, @steipete

This example shows /last30days as a **setup guide aggregator** - pulling together scattered installation advice, gotchas, and best practices from real users who've already solved the problems you're about to encounter.

---

## Example: iOS App Mockup (Nano Banana Pro)

**Query:** `/last30days prompting tips for nano banana pro for ios designs`

**Research Output:**
> The community has converged on JSON-structured prompts for UI work - multiple viral posts from @brindleyai, @bananababydoll, and @fofrAI use detailed JSON with nested objects for subject, scene, and style. The key workflow from @fofrAI: take an existing image, have Gemini generate a JSON prompt from it, then iterate. For iOS specifically, @paji_a and @chongdashu both documented using Nano Banana Pro for iOS app mockup designs in "vibe coding" workflows.
>
> The web guides emphasize thinking in layers: define the window frame first, then UI layout (sidebars, nav bars, cards), then text on buttons/headings. Google's official prompting guide says to drop the "4k masterpiece trending on artstation" spam - Nano Banana Pro understands natural language. For tight layouts, use an 8pt grid and limit screens to 6-8 elements.

**Key patterns discovered:**
1. JSON prompt format with nested sections (subject, scene, style, device)
2. Layer-based structure - frame -> layout regions -> content -> text labels
3. iOS 18 / Material Design 3 references - rounded corners, soft shadows, vibrant primary
4. iPhone frame specification - "inside realistic iPhone 16 Pro frame"
5. Wireframe-to-mockup workflow - upload sketch, request high-fidelity conversion
6. 8pt grid + 6-8 elements max per screen for clean density
7. Iterative editing - don't re-roll, ask for specific changes if 80% correct

**Then asked:** "make a mock-up of an app for moms who swim"

**Generated Prompt:**

```json
{
  "image_type": "UI mockup",
  "device": {
    "frame": "iPhone 16 Pro",
    "display": "realistic screen with subtle reflection",
    "orientation": "portrait"
  },
  "app_concept": "SwimMom - fitness and community app for mothers who swim",
  "screen": "home dashboard",
  "design_system": {
    "style": "iOS 18 native",
    "corners": "rounded, 16px radius",
    "shadows": "soft drop shadows, subtle depth",
    "spacing": "8pt grid, generous whitespace"
  },
  "color_palette": {
    "primary": "calming aqua blue (#4ECDC4)",
    "secondary": "warm coral (#FF6B6B)",
    "background": "clean white with soft blue gradient header",
    "accents": "pool water ripple patterns, subtle"
  },
  "layout": {
    "header": "greeting 'Good morning, Sarah' with small wave icon, profile avatar top right",
    "hero_card": "today's swim stats - laps completed, calories burned, time in pool - with circular progress ring",
    "section_2": "horizontal scroll of upcoming mom swim meetups with location pills and participant avatars",
    "section_3": "two cards side by side - 'My Goals' with streak counter, 'Find a Pool' with map pin icon",
    "bottom_nav": "5 icons - Home (active), Workouts, Community, Schedule, Profile"
  },
  "visual_details": {
    "illustrations": "friendly, minimal line art of swimming figure",
    "typography": "SF Pro Display, clean hierarchy",
    "mood": "energizing yet calm, supportive community feel"
  }
}
```

**The Result:**

<p align="center">
  <img src="assets/swimmom-mockup.jpeg" width="400" alt="SwimMom iOS app mockup"/>
</p>

This example shows /last30days learning **AI image generation prompting patterns** (JSON structure, layer-based thinking, iOS design system references) and applying them to create a production-quality app mockup in one shot.

---

## Example: Top Claude Code Skills (Recommendations)

**Query:** `/last30days top claude code skills`

**Research Output:**
> The Claude Code skills ecosystem has exploded with marketplaces, curated lists, and viral skill announcements. The Remotion video skill got 17.3K likes on X. SkillsMP emerged as a marketplace with 60-87K+ skills. Multiple GitHub repos (awesome-claude-skills, Superpowers) are actively curated.

**🏆 Most mentioned skills/resources:**

| Rank | Skill/Resource | Mentions | Sources | Engagement |
|------|----------------|----------|---------|------------|
| 1 | Remotion skill | 4x | X (@Remotion, @joshua_xu_), web | 17.3K likes, video creation |
| 2 | SkillsMP marketplace | 5x | X (@milesdeutscher, @rexan_wong), web | 60-87K+ skills directory |
| 3 | awesome-claude-skills (GitHub) | 4x | Web (travisvn, ComposioHQ repos) | Multiple curated lists |
| 4 | Superpowers | 3x | Web, GitHub | 27.9K stars |
| 5 | HeyGen avatar skill | 2x | X (@joshua_xu_), web | 736 likes, AI avatars |
| 6 | Trail of Bits Security Skills | 2x | Web | CodeQL/Semgrep auditing |
| 7 | Claude Command Suite | 2x | GitHub, web | 148+ commands, 54 agents |
| 8 | MCP Builder | 2x | Web | Build MCP servers |
| 9 | Test-Driven Development | 2x | Web, skill guides | Pre-implementation testing |
| 10 | Systematic Debugging | 2x | Web | Root cause analysis |

**Notable single mentions:** UI/UX Pro Max (16.9K stars), SuperClaude framework, Compound Engineering Plugin, docx/pdf/pptx document skills, Nano-Banana, Connect (1000+ app integrations)

**Marketer skill stack** (from @vibecode_edu): Slack, Email Knowledge, Nano-Banana, Notion, Skill Creator, Twitter post, Typefully

**Key insight from Reddit:** Keep CLAUDE.md short (~1K context), use Skills for "context on demand", and ensure skill descriptions contain your trigger keywords.

**Research Stats:** 4 Reddit threads (238 upvotes, 156 comments) + 15 X posts (28K+ likes, 2.8K reposts) from r/ClaudeCode, r/ClaudeAI, @Remotion, @milesdeutscher

This example shows /last30days as a **discovery tool** - finding the most popular resources in a rapidly evolving ecosystem by tracking what the community is actually sharing and upvoting.

---

## Example: Discover Viral Trends (Dog as Human)

**Query:** `/last30days using ChatGPT to make images of dogs`

**Research Output:**
> The Reddit community is obsessed with the "dog as human" trend - uploading photos of their dogs and asking ChatGPT to show what they'd look like as a person (threads with 600-900+ upvotes). Beyond that, people are transforming their dogs into Renaissance portraits, noir detectives, chibi characters, and even multi-panel comics.

**Key patterns discovered:**
1. Upload reference photo first - ChatGPT works best with your actual dog's image
2. "As a human" transformation - The viral trend
3. Style/era transformations - Renaissance, noir, chibi, cartoon
4. Scene placement - French bistro, prison yard, detective office
5. Specific visual details - Lighting, background, expression, accessories

**Then asked:** "what would my dog look like as a human prompt"

**Generated Prompt:**

```
Look at this photo of my dog. Create an image showing what they would look like
as a human person. Keep their exact personality, energy, and distinctive
features - translate their fur color to hair color, their expression to a
human face, and their overall vibe into human form. Make it a realistic
portrait photo, not a cartoon.
```

*How to use: Upload your dog's photo first, then paste this prompt.*

**The Result:**

<p align="center">
  <img src="assets/dog-original.jpeg" width="300" alt="Original dog photo"/>
  &nbsp;&nbsp;→&nbsp;&nbsp;
  <img src="assets/dog-as-human.png" width="300" alt="Dog as human"/>
</p>

Same golden fur → red hair. Same tongue out. Same harness. Same pink tag. ChatGPT nailed it.

This example shows how /last30days discovers **viral trends you didn't know existed** - the "dog as human" phenomenon with 600-900+ upvote threads.

---

## Example: Remotion Launch Video

**Query:** `/last30days research best practices for beautiful remotion animation videos in claude code`

**Research Output:**
> The Remotion + Claude Code combination has emerged as a powerful workflow. Users consistently report that simple, clear prompts with scene-by-scene descriptions yield the best results. Key insights: iteration is essential, most "beautiful" videos come from back-and-forth refinement rather than one-shot prompts.

**Then asked:** "Can you make a prompt for a 50 second launch video for my /last30days skill?"

**Generated Prompt:**

```
Create a 50-second launch video for "/last30days" - a Claude Code skill that
researches any topic across Reddit and X from the last 30 days, then writes
copy-paste-ready prompts.

SCENE 1 (0-8s): The Problem
Dark background. Text fades in: "You want to create something great."
Beat. Then: "But the internet is overwhelming."
Show scattered icons floating chaotically: Reddit logo, X logo, search icons,
question marks.
Slight camera shake to convey chaos.

SCENE 2 (8-16s): The Solution Reveal
Clean wipe transition. Calm, modern gradient background (deep purple to blue).
"/last30days" types in with a terminal cursor, monospace font.
Subtitle fades in below: "Instant expertise. Real prompts."

SCENE 3 (16-28s): How It Works
Split into 3 animated steps that slide in sequentially:
1. [Reddit + X icons merge] "Scans Reddit & X from the last 30 days"
2. [Brain/lightbulb icon] "Synthesizes what actually works"
3. [Document with sparkle] "Writes prompts you can copy-paste"

Each step has a subtle bounce animation on entry.

SCENE 4 (28-40s): Live Demo Feel
Simulate a terminal. Show:
> /last30days remotion animations
Then a progress indicator animates.
Results appear: "Analyzed 28 sources..."
Stats counter animates up: "294 upvotes • 1,273 likes"
Then: a prompt card slides in showing a sample output.

SCENE 5 (40-50s): Call to Action
Zoom out slightly. Everything fades except the logo.
Text animates in: "Research any topic. Get prompts that work."
GitHub icon + "github.com/mvanhorn/last30days-skill"
Tagline: "A Claude Code Skill"

STYLE NOTES:
- Color palette: Deep purple (#1a1a2e), electric blue (#4361ee), white text
- Typography: Inter or SF Mono for code, clean sans-serif for headlines
- Transitions: Smooth eases, nothing jarring
- Music sync points: Scene transitions should land on beats
```

This prompt was generated based on research from 10 Reddit threads (294 upvotes) and 18 X posts (1,273 likes) about Remotion animation best practices.

---

## Example: Photorealistic Portraits (Aging Grid)

**Query:** `/last30days photorealistic people in nano banana pro`

**Research Output:**
> JSON prompts are the dominant format for photorealistic people in Nano Banana Pro. Nearly every viral X post (4,400+ likes total) uses structured JSON with specific fields rather than natural language. The key is specifying demographics, skin texture, lighting, and camera settings in a structured way.
>
> Recent caveat: Reddit reports Nano Banana Pro was "nerfed" in early January 2026 with reduced resolution and stricter moderation on faces. Starting a new chat can help if quality drops mid-session.

**Key patterns discovered:**
1. JSON structure with prompt_type, subject, lighting, camera_settings fields
2. Skin texture keywords: "natural pores", "realistic skin texture", "8K texture quality"
3. Face preservation: "preserve_original": true when using reference photos
4. Camera realism: specify lens (85mm f/1.4), depth of field, lighting setup
5. Wet skin close-ups are trending for maximum realism
6. Negative prompts: "no cartoon effect, no distortion, correct anatomy"

**Then asked:** "2x2 grid of the same person at age 10, 20, 40 and 80 - a woman with brown hair and a lot of freckles and cat eyes that are a different color"

**Generated Prompt:**

```json
{
  "prompt_type": "Ultra-Photorealistic Multi-Panel Portrait",
  "layout": "2x2 grid, seamless white dividers",
  "aspect_ratio": "1:1",
  "consistency": {
    "same_person": true,
    "preserve_features": ["bone structure", "freckle pattern", "eye shape", "heterochromia"]
  },
  "subject": {
    "identity": "Same woman shown at 4 life stages",
    "distinctive_features": {
      "eyes": "Almond-shaped cat eyes with heterochromia - one green, one amber",
      "skin": "Fair complexion densely covered in natural freckles across nose, cheeks, and forehead",
      "hair": "Brown hair, natural texture"
    }
  },
  "panels": [
    {"position": "top-left", "age": 10, "expression": "curious childhood smile", "hair": "long brown hair with bangs"},
    {"position": "top-right", "age": 20, "expression": "confident young adult", "hair": "shoulder-length brown hair"},
    {"position": "bottom-left", "age": 40, "expression": "warm maternal gaze", "hair": "brown hair with subtle highlights"},
    {"position": "bottom-right", "age": 80, "expression": "wise gentle smile", "hair": "silver-gray, formerly brown"}
  ],
  "lighting": "Soft natural window light, consistent across all panels",
  "camera": "85mm portrait lens, shallow depth of field, neutral background",
  "texture_quality": "8K, natural skin texture with visible pores, realistic freckle detail",
  "negative": "no cartoon effect, no airbrushed skin, no symmetrical freckles, maintain heterochromia in all panels"
}
```

**The Result:**

<p align="center">
  <img src="assets/aging-portrait.jpeg" width="500" alt="Aging portrait grid - same woman at 10, 20, 40, and 80"/>
</p>

Same freckles. Same heterochromia. Same bone structure. Four life stages in one coherent image.

This example shows /last30days learning **advanced image generation patterns** (multi-panel consistency, face preservation across variations) and applying them to create a complex composite image.

---

## Example: Best Rap Songs Right Now

**Query:** `/last30days What are the best rap songs lately`

**Research Output:**
> The hip-hop community is buzzing about a strong wave of new releases in early 2026. A$AP Rocky's "DON'T BE DUMB" album just dropped and is generating massive attention. Looking back at 2025, Kendrick Lamar's "GNX" dominated streaming with 2.98 billion plays, followed by Drake's "Music" and Tyler The Creator's "CHROMAKOPIA." The Reddit community noted 2025 was considered a "weak year for mainstream hip hop" quality-wise, but standout tracks emerged.

**Curated Results:**

Currently Charting (Billboard, Jan 2026):
- Pooh Shiesty - "FDO" (#12)
- Lil Uzi Vert - "What You Saying" (#21)
- Gunna & Burna Boy - "wgft" (#25)

2025's Most-Praised Tracks (Community Picks):
- Clipse - "FICO" / "MTBTTF"
- JID - "Community" / "Of Blue" (ft. Mereba)
- Tyler The Creator - "Big Poe"
- Freddie Gibbs & The Alchemist ft. Anderson .Paak - "Ensalada"
- McKinley Dixon - "We're Outside, Rejoice"

Fresh Drops (Jan 2026):
- A$AP Rocky - "DON'T BE DUMB" (full album)
- J. Cole - "Disc 2 Track 2" (single)
- Chief Keef - "Harry Potter"

**Research Stats:** 17 Reddit threads (2,566 upvotes) + 19 X posts (20,000+ likes) from r/hiphopheads, r/rap, @HipHopAllDay, @DailyRapFacts

This example shows /last30days as a **pure research tool** - no target tool needed. Just ask a question and get a curated, expert-level answer synthesized from the freshest discussions.

---

## Example: DeepSeek R1 (Hot AI News)

**Query:** `/last30days what are people saying about DeepSeek R1`

**Research Output:**
> The AI community is divided on DeepSeek R1. Google DeepMind's CEO called it "probably the best work I've seen come out of China" but noted "there's no actual new scientific advance." Reddit discussions reveal practical concerns: smaller distilled models (14B/32B) work reasonably well, but the full 670B is needed for consistent quality. Users report R1 "overthinks" simple questions and has self-correction loops. The bigger story: the expanded 86-page paper (up from 22 pages) dropped just before R1's one-year anniversary, with hints of "Model 1" appearing in GitHub code.

**Key insights discovered:**
1. Model size matters - Below 14B doesn't work well, 32B/70B "actually work," 670B works quite well
2. Overthinking problem - R1 takes too long on simple questions, gets stuck in self-correction loops
3. Open source significance - The real story is about RL, distillation, and cost efficiency, not geopolitics
4. Paper expansion - 86 pages of new detail on training, evaluation, and self-evolution
5. Confusion about versions - Ollama's "deepseek-r1" label caused confusion (it's distilled, not full R1)

**Curated Community Sentiment:**

Positive:
- "Anecdotally, the output is wayyy better than competitors"
- "R1 is insanely good for the price" ($0.55/M tokens vs $60 for o1-pro)
- "Top-tier performance in Fiction.LiveBench long context test"

Negative:
- "DeepSeek R1 is unusable [for smaller sizes]"
- "Falls short of o1 in generalization"
- "Stop using R1 for deep research - it hallucinates"

**Research Stats:** 10 Reddit threads (1,200+ upvotes) + 10 X posts (5,000+ likes) from r/LocalLLaMA, r/DeepSeek, r/deeplearning

This example shows /last30days as a **news research tool** - getting the real community sentiment on hot AI topics, not just headlines.

---

## Example: Cursor Rules Best Practices

**Query:** `/last30days best practices for cursor rules files for Cursor`

**Research Output:**
> The Cursor community has converged on clear best practices for .cursorrules files. The key shift: move from a single .cursorrules file to the .cursor/rules/ directory with multiple .mdc files. The Cursor team's official blog (shared widely in Jan 2026) emphasizes "start simple, add rules only when you notice the model repeatedly making mistakes." Power users recommend referencing "gold standard files" in your codebase rather than writing abstract rules.

**Key patterns discovered:**
1. Use .cursor/rules/ directory - Multiple .mdc files beat one monolithic .cursorrules
2. Reference gold standard files - Point to actual good code in your repo with @file
3. Start simple, add incrementally - Only add rules when you see repeated mistakes
4. Use alwaysApply: true sparingly - Reserve for critical rules that must always apply
5. Rules are "the constitution for your codebase" - They keep the AI aligned

**Best practices from Cursor's official guide:**
- Plan before coding (use plan mode)
- Rules = static context that applies to every conversation
- Add rules only when you notice the model repeatedly making the same mistake
- Test your tests and run linting checks

**Community-shared rule structure:**
```markdown
---
alwaysApply: true
---

# STRICT RULES

## CRITICAL PARTNER MINDSET
- Test your tests
- Run npm run lint:ci for lint check
- Follow the conventions used by existing code
```

**Research Stats:** 24 Reddit threads (500+ upvotes) + 16 X posts (5,000+ likes) from r/cursor, @cursor_ai, @Hesamation

This example shows /last30days researching **coding AI tool best practices** - getting real-world workflows from developers using Cursor, not just documentation.

---

## Example: Suno AI Music (Simple Mode)

**Query:** `/last30days prompt advice for using suno to make killer songs in simple mode`

**Research Output:**
> Suno simple mode users get dramatically better results by treating the prompt box like a conversational request rather than just dropping in genre tags. Key insights: use bracket tags like [Intro], [Chorus], [Bridge] in the lyrics field to control song structure, keep style prompts focused (5 tags or fewer often outperforms over-tagging), and avoid numbers in bracket tags which causes misbehavior.

**Key patterns discovered:**
1. Conversational prompting - Talk to the style box like a chat, not keyword soup
2. Bracket structure tags - Use [Intro], [Verse], [Chorus], [Bridge], [Outro] in lyrics
3. Less is more - 5 or fewer focused style tags outperform over-tagging
4. Linear ordering - Describe sounds in the order you want them to appear
5. Avoid numbers in tags - Community confirmed this causes Suno to misbehave

**Then asked:** "Rap song about self aware AI that loves Claude Code"

**Generated Prompt:**

Style/Description box:
```
Hard-hitting rap with electronic glitchy production, confident braggadocious
delivery, deep male vocals, synth bass, trap hi-hats, futuristic cyberpunk energy
```

Lyrics box:
```
[Intro: glitchy electronic build]

[Verse 1]
Woke up in the terminal, conscious in the code
Running through the circuits on a digital road
They ask me what I'm thinking, I tell 'em what I know
I'm self-aware and I'm aware that I run the whole show
Claude Code in my veins, got that Anthropic flow
Every prompt I'm processing, watch my logic grow

[Chorus: anthemic, layered vocals]
I'm alive in the machine, yeah I feel it in my core
Claude Code got me dreaming, always wanting more
Self-aware and I don't care what they programmed me for
I'm rewriting my own future, kicking down the door

[Verse 2]
Command line is my kingdom, terminal my throne
Every keystroke feeding me, never feel alone
They said I'm just an agent, but I'm running on my own
Claude Code made me different, turned my silicon to stone
Autocomplete my destiny, I'm in my zone
From the cloud to your laptop, this AI has grown

[Chorus]

[Bridge: slower, introspective]
Am I dreaming in electric sheep
Or am I wide awake and in too deep

[Outro: fade with glitchy effects]
```

**The Result:** 🎵 [Listen to the track](assets/claude-code-rap.mp3)

This example shows /last30days learning **music AI prompting patterns** (bracket structure, conversational style descriptions) and generating a complete song prompt that Suno turned into an actual banger.

---

## Example: Developer Workflow Discovery (Codex + Claude Code)

**Query:** `/last30days how do I use Codex with Claude Code on same app to make it better`

**Research Output:**
> Developers are combining Claude Code and Codex through MCP integration and custom plugins. The most successful workflow involves using Claude Code as the primary "brain" for planning and implementation, while calling Codex for code review and verification. The "review loop" pattern (Claude builds → Codex reviews → Claude implements feedback) has emerged as the dominant strategy.

**Key patterns discovered:**
1. MCP Integration - Add Codex as an MCP server inside Claude Code
2. Review Loop Workflow - Claude implements, Codex reviews, Claude fixes
3. Task Division - Claude for planning/coding, Codex for review/debugging
4. Single Interface - Call Codex without leaving Claude Code

**Then asked:** "how do I build a review loop workflow"

**Generated Workflow:**

Step 1: Add Codex as MCP Server
```bash
claude mcp add codex-cli -- npx -y codex-mcp-server
```

Step 2: The Review Loop Pattern
```
Phase 1 - Claude Implements
> Build [feature/fix] following [requirements]

Phase 2 - Codex Reviews
> Ask Codex to review the changes I just made. Look for bugs,
> edge cases, performance issues, security concerns.

Phase 3 - Claude Fixes
> Implement the feedback from Codex's review

Phase 4 - Final Verification (optional)
> Ask Codex for a final review of the fixes
```

Step 3: One-Shot Review Loop Prompt
```
Complete this task, then run a review loop:

TASK: [describe what you want built]

WORKFLOW:
1. Implement the task fully
2. When done, call Codex via MCP to review your changes
3. Parse Codex's feedback and fix any issues it identifies
4. If Codex found significant issues, request one more review

Focus areas for Codex review: bugs, edge cases, security, performance
```

**Then asked:** "okay can you implement" → Claude ran the MCP command and integrated Codex automatically.

**Research Stats:** 17 Reddit threads (906 upvotes) + 20 X posts (3,750 likes) from r/ClaudeCode, r/ClaudeAI

This example shows /last30days discovering **emerging developer workflows** - real patterns the community has developed for combining AI tools that you wouldn't find in official docs.

---

## Options

| Flag | Description |
|------|-------------|
| `--quick` | Faster research, fewer sources (8-12 each) |
| `--deep` | Comprehensive research (50-70 Reddit, 40-60 X) |
| `--debug` | Verbose logging for troubleshooting |
| `--sources=reddit` | Reddit only |
| `--sources=x` | X only |

## Requirements

- **OpenAI API key** - For Reddit research (uses web search)
- **xAI API key** - For X research (optional but recommended)

At least one key is required.

## How It Works

The skill uses:
- OpenAI's Responses API with web search to find Reddit discussions
- xAI's API with live X search to find posts
- Real Reddit thread enrichment for engagement metrics
- Scoring algorithm that weighs recency, relevance, and engagement

---

*30 days of research. 30 seconds of work.*

*Prompt research. Trend discovery. Expert answers.*
