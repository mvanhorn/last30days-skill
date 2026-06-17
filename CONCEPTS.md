# Concepts

Shared vocabulary for `last30days-skill`. Terms here have a precise project-specific meaning — distinct enough from their general technical sense that a new contributor would need them defined to follow conversations, PR descriptions, or the SKILL.md contract.

## The package

### Skill

A self-contained agent-instructions package consisting of a `SKILL.md` prose contract plus a sibling `scripts/` directory containing the executable code the SKILL.md invokes. The package conforms to the [Agent Skills](https://agentskills.io) open format and installs across every major harness (Claude Code, Codex, Cursor, GitHub Copilot, Gemini CLI, and 50+ others) via `npx skills add`, harness-native plugin installers, or per-harness skill directories. A Skill is the unit of distribution; the Skill is the product.

### Engine

The Python script (`scripts/last30days.py`) the Skill's SKILL.md invokes to do the actual research work. The Engine and SKILL.md have a contract: SKILL.md tells the model which flags to pass (`--plan`, `--competitors-plan`, `--x-handle`, `--subreddits`, `--emit=compact`, etc.), and the Engine produces a specific output shape (badge line, ranked evidence clusters, emoji-tree footer) that the model is contractually required to pass through. The Engine is implementation; the SKILL.md prose is the agent-facing surface.

### Harness

The agent runtime that loads Skills and invokes them on the user's behalf. Claude Code is the most common Harness for this Skill but not the only one — Codex, Cursor, GitHub Copilot, Gemini CLI, and the rest of the Agent Skills ecosystem also count. "Multi-harness" describes a Skill that works correctly across every Harness it installs into; features written without multi-harness awareness (e.g., engine flags with no SKILL.md integration, or paths hardcoded to one Harness's install layout) regress on Harnesses other than the one they were tested against.

## Research pipeline

### Primary entity

The brand or proper-noun core of a research topic — the topic with its Intent modifier stripped. It is what the research is *about*, as distinct from how the user phrased the search.

### Intent modifier

A trailing word or phrase in a topic that expresses what the user wants to know rather than what the topic is ("review", "use cases", "pricing"). Stripped when deriving the Primary entity.

### Entity grounding

The check that a candidate item plausibly mentions the Primary entity before final ranking. Grounding keys on the head token (first word) of the Primary entity rather than the full phrase — trailing words are usually search descriptors, so requiring them falsely demotes on-entity items.

An item that fails grounding receives a decisive entity-miss demotion, designed so engagement cannot rescue off-entity content. Because the demotion is decisive, the grounding bar is deliberately conservative: its failure modes degrade toward "no penalty," never toward burying on-entity signal.

### Keyless path

The research flow available with no API keys: source data is gathered by scraping and RSS rather than authenticated APIs, and ranking falls back to local scoring instead of LLM-based reranking. This is the free tier of the Skill; lexical quality safeguards like Entity grounding matter most here, because no LLM is available to judge relevance semantically.

### Comment-enrichment slots

The small, depth-dependent budget of Reddit posts whose comments get fetched in the Keyless path. Slot selection is relevance-aware: posts that pass Entity grounding claim slots first, so the budget is not spent on high-engagement posts that final ranking will demote anyway.
