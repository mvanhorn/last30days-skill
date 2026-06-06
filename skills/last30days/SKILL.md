---
name: last30days
version: "3.3.1"
description: "Research what people actually say about any topic in the last 30 days. Pulls posts and engagement from Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, and the web."
argument-hint: 'last30days nvidia earnings reaction | last30days AI video tools | last30days what users want in react'
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
homepage: https://github.com/mvanhorn/last30days-skill
repository: https://github.com/mvanhorn/last30days-skill
author: mvanhorn
license: MIT
user-invocable: true
metadata:
  openclaw:
    emoji: "📰"
    requires:
      env: []
      optionalEnv:
        - SCRAPECREATORS_API_KEY
        - OPENAI_API_KEY
        - XAI_API_KEY
        - OPENROUTER_API_KEY
        - PARALLEL_API_KEY
        - BRAVE_API_KEY
        - APIFY_API_TOKEN
        - AUTH_TOKEN
        - CT0
        - BSKY_HANDLE
        - BSKY_APP_PASSWORD
        - TRUTHSOCIAL_TOKEN
      bins:
        - node
        - python3
    primaryEnv: SCRAPECREATORS_API_KEY
    files:
      - "scripts/*"
    homepage: https://github.com/mvanhorn/last30days-skill
    tags:
      - research
      - deep-research
      - reddit
      - x
      - twitter
      - youtube
      - tiktok
      - instagram
      - hackernews
      - polymarket
      - digg
      - bluesky
      - truthsocial
      - trends
      - recency
      - news
      - citations
      - multi-source
      - social-media
      - analysis
      - web-search
      - ai-skill
      - clawhub
---

# last30days v3.3.1: Research Any Topic from the Last 30 Days

This file is the execution hot path. It keeps the required routing, engine invocation, and synthesis gates in default prompt context. Deep examples and long rationale live in lazy references:

- `references/output-contract.md` - full badge, law, citation, comparison, and self-check rules.
- `references/query-planning.md` - query quality preflight, handle/community resolution, query-plan schema, and engine command details.
- `references/source-playbooks.md` - source weighting, cluster synthesis, mode-specific output templates, and invitation copy.
- `references/troubleshooting.md` - runtime preflight, stale-clone checks, security notes, and known failure modes.
- `references/nux-wizard.md` - first-run setup and follow-up prompt-generation flows.
- `references/save-html-brief.md` - optional HTML briefing save flow.
- `references/full-playbook-v3.3.1.md` - preserved full v3.3.1 playbook for audit or unusual edge cases.

## Non-Negotiable Flow

1. If no topic was provided, ask one short clarifying question and stop.
2. Load WebSearch first when your host supports deferred WebSearch. In Claude Code, use `ToolSearch select:WebSearch`.
3. Diagnose the query before running the engine. If the topic is a keyword trap or ambiguous shopping/recommendation request, reframe or ask one clarifying question before burning a research run.
4. Resolve targeting before the engine on WebSearch-capable hosts: relevant subreddits, X handles, GitHub users/repos, TikTok hashtags/creators, Instagram creators, YouTube queries, and current-news terms as applicable.
5. Generate `QUERY_PLAN_JSON` yourself when WebSearch and reasoning are available. You are the planner. Pass it with `--plan`.
6. Run `scripts/last30days.py` through Bash. Web-only synthesis is invalid for this skill.
7. Run 2-3 post-engine WebSearch supplements for news, blogs, comparisons, tutorials, or current context unless the mode clearly does not need them.
8. Append those supplemental WebSearch results to the saved raw file when a raw file was saved.
9. Synthesize from the engine output and supplements using the output contract. Do not emit raw evidence clusters.
10. End at the invitation. Do not add a trailing `Sources:` / `References:` / `Further reading:` block.

Read `references/query-planning.md` before step 3 if the query is a person, company, product, comparison, recommendation, prompting task, or anything that needs platform-specific targeting. Read `references/output-contract.md` before synthesis.

## Runtime Preflight

The engine requires Python 3.12+.

```bash
python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("ERROR: last30days v3 requires Python 3.12+. Install python3.12+ and rerun.")
print(f"python {sys.version.split()[0]} ok")
PY
```

If the user asks for setup, install help, provider configuration, or diagnostics, read `references/nux-wizard.md` and `references/troubleshooting.md`.

## Intent Parse

Classify the request before tool use:

- `TOPIC`: what the user wants to learn about.
- `TARGET_TOOL`: optional tool/framework/product context after "for ...".
- `QUERY_TYPE`: `GENERAL`, `NEWS`, `PROMPTING`, `RECOMMENDATIONS`, or `COMPARISON`.

Treat `X vs Y` and `X versus Y` as `COMPARISON`. Treat "best", "top", "recommend", "alternatives", and "what should I use" as `RECOMMENDATIONS`. Treat "write prompts", "prompt ideas", and "how to prompt" as `PROMPTING`.

Do not show the parsed variables to the user unless they ask.

## Engine Invocation

Resolve `SKILL_ROOT` to the loaded skill directory when possible. In repo checkouts, `skills/last30days` is the skill root.

```bash
SKILL_ROOT="${SKILL_ROOT:-$(pwd)}"
if [ -f "$SKILL_ROOT/skills/last30days/scripts/last30days.py" ]; then
  SKILL_ROOT="$SKILL_ROOT/skills/last30days"
fi
LAST30DAYS_MEMORY_DIR="${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"
mkdir -p "$LAST30DAYS_MEMORY_DIR"
```

On WebSearch-capable hosts, pass your query plan and resolved targeting:

```bash
python3 "$SKILL_ROOT/scripts/last30days.py" \
  --emit=compact \
  --save-dir="${LAST30DAYS_MEMORY_DIR}" \
  --plan 'QUERY_PLAN_JSON' \
  "TOPIC"
```

Add resolved flags when available:

- `--subreddits=LocalLLaMA,ClaudeAI`
- `--x-handle=handle`
- `--x-related=founder,company,project`
- `--github-user=user`
- `--github-repo=owner/repo`
- `--tiktok-hashtags=tag1,tag2`
- `--tiktok-creators=creator1,creator2`
- `--ig-creators=creator1,creator2`
- `--youtube-queries="topic review|topic demo"`

For person topics, resolved `--x-handle` and `--github-user` are both expected unless you explicitly found that one does not exist. For product/project topics, resolve GitHub repo and relevant communities when possible.

If WebSearch is unavailable, do not fake the pre-research steps. Use the engine fallback instead:

```bash
python3 "$SKILL_ROOT/scripts/last30days.py" \
  --emit=compact \
  --save-dir="${LAST30DAYS_MEMORY_DIR}" \
  --auto-resolve \
  "TOPIC"
```

If the engine emits a `## Pre-Research Status` warning, pass it through honestly instead of hiding it.

## Query Plan Shape

Use compact JSON. Include only fields you can justify from the query and pre-research.

```json
{
  "query_type": "GENERAL",
  "core_topic": "TOPIC",
  "subqueries": [
    {"source": "reddit", "query": "TOPIC discussion OR review"},
    {"source": "x", "query": "TOPIC lang:en"},
    {"source": "youtube", "query": "TOPIC review demo"}
  ],
  "include_sources": ["reddit", "x", "youtube", "tiktok", "instagram", "hackernews", "polymarket", "github", "web"],
  "exclude_terms": []
}
```

For detailed planning rules, comparison fanout, handle lookup, and degraded-path handling, read `references/query-planning.md`.

## Output Contract

The Python engine emits the mandatory badge as the first line. Pass the engine output through and synthesize around it without changing the badge.

Core laws:

1. No trailing `Sources:` block. The engine footer and saved raw file carry source traceability.
2. For `GENERAL`, `NEWS`, `PROMPTING`, and `RECOMMENDATIONS`, the first synthesis label after the badge is exactly `What I learned:`.
3. No em dashes or en dashes in generated prose. Use ` - `.
4. No `##` or `###` section headers in general/news/prompting/recommendation bodies.
5. Include the engine `<!-- PASS-THROUGH FOOTER -->` block verbatim.
6. Do not emit raw ranked evidence clusters. Transform them into prose.
7. Named-entity topics on WebSearch-capable hosts require your own `--plan`.
8. Every cited source in the narrative should be an inline Markdown link when a URL is available.

For comparison output, recommendation ranking, source weighting, exact invitation wording, and the complete pre-present self-check, read `references/output-contract.md` and `references/source-playbooks.md`.

## Synthesis Shape

For `GENERAL`, `NEWS`, `PROMPTING`, and `RECOMMENDATIONS`:

```markdown
🌐 last30days v{VERSION} · synced {YYYY-MM-DD}

What I learned:

**Lead pattern.** Synthesize the strongest cross-source finding with inline links.

**Second pattern.** Explain the next useful signal, including disagreement or thin evidence where relevant.

KEY PATTERNS from the research:

1. **Pattern:** concrete implication.
2. **Pattern:** concrete implication.

<!-- PASS-THROUGH FOOTER -->
...
<!-- END PASS-THROUGH FOOTER -->

I have all the links and raw notes saved. If you want, I can turn this into a tighter recommendation, prompt pack, or source-by-source brief.
```

For comparisons, use the comparison template in `references/output-contract.md`.

## Save Raw Supplements

When post-engine WebSearch supplements were used and the engine saved a raw file, append a `## WebSearch Supplemental Results` section to the saved raw file. Include one bullet per supplemental WebSearch query with the query, source names, URLs, and a one-line reason it mattered. If no raw file was saved, say that plainly if asked.

## Stop Conditions

- Missing topic: ask and stop.
- Runtime below Python 3.12: report the requirement and stop.
- Required account/API credentials are missing for a requested paid/live provider: say what is unavailable and use only available free/mock paths.
- A named-entity query cannot be resolved confidently: use `--auto-resolve` or report the unresolved field. Do not invent handles, repos, or communities.
- Validation output conflicts with this file: trust the engine output and read the referenced deep playbook before improvising.

## Optional Follow-Up

If the user asks for prompts, more options, or a saved HTML briefing after the research response, use the already-collected research. Do not run new WebSearches by default. Read `references/nux-wizard.md` for follow-up prompt format and `references/save-html-brief.md` for the HTML artifact workflow.
