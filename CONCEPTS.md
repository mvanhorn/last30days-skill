# Concepts

Shared vocabulary for `last30days-skill`. Terms here have a precise project-specific meaning — distinct enough from their general technical sense that a new contributor would need them defined to follow conversations, PR descriptions, or the SKILL.md contract.

## The package

### Skill

A self-contained agent-instructions package consisting of a `SKILL.md` prose contract plus a sibling `scripts/` directory containing the executable code the SKILL.md invokes. The package conforms to the [Agent Skills](https://agentskills.io) open format and installs across every major harness (Claude Code, Codex, Cursor, GitHub Copilot, Gemini CLI, and 50+ others) via `npx skills add`, harness-native plugin installers, or per-harness skill directories. A Skill is the unit of distribution; the Skill is the product.

### Engine

The Python script (`scripts/last30days.py`) the Skill's SKILL.md invokes to do the actual research work. The Engine and SKILL.md have a contract: SKILL.md tells the model which flags to pass (`--plan`, `--competitors-plan`, `--x-handle`, `--subreddits`, `--emit=compact`, etc.), and the Engine produces a specific output shape (badge line, ranked evidence clusters, emoji-tree footer) that the model is contractually required to pass through. The Engine is implementation; the SKILL.md prose is the agent-facing surface.

### Harness

The agent runtime that loads Skills and invokes them on the user's behalf. Claude Code is the most common Harness for this Skill but not the only one — Codex, Cursor, GitHub Copilot, Gemini CLI, and the rest of the Agent Skills ecosystem also count. "Multi-harness" describes a Skill that works correctly across every Harness it installs into; features written without multi-harness awareness (e.g., engine flags with no SKILL.md integration, or paths hardcoded to one Harness's install layout) regress on Harnesses other than the one they were tested against.

## Distribution

### Beta channel

A parallel install of the Skill, sourced from the private `mvanhorn/last30days-skill-private` repo and installed as `/last30days-beta` rather than `/last30days`. The Beta channel exists so experimental changes can be tested by real users before they ship to the public `/last30days`. Promotion from Beta to public happens via a review PR against this (public) repo — Beta-only changes never ship to public without that PR. The Beta channel workflow guide lives in `BETA.md` in the private repo.
