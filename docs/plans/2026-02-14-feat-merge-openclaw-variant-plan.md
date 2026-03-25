---
title: "feat: Merge OpenClaw variant into main repo"
type: feat
date: 2026-02-14
---

# feat: Merge OpenClaw Variant into Main Repo

## Overview

Consolidate the `last30days-openclaw` project into `last30days-skill-private` so there's one unified Python engine powering both the main skill (Claude Code / Codex) and an "open" variant with watchlist, briefing, history, and built-in web search. The open variant also gets YouTube and Bird CLI — features the main project already has but openclaw was built before they existed.

## Problem Statement / Motivation

Right now there are two separate repos with diverging codebases:

- **`last30days-skill-private`** (main, Feb 14) — YouTube, vendored Bird, better scoring/normalization, Codex compat. But no built-in web search APIs and no persistence layer.
- **`last30days-openclaw`** (Feb 10) — SQLite store, watchlist, briefings, 3 web search backends (Parallel AI, Brave, OpenRouter). But frozen without YouTube or latest engine improvements.

They share ~80% of the same `scripts/lib/` files but are drifting apart. Maintaining two codebases is unsustainable.

**Goal:** One repo, one Python engine, two SKILL.md variants. Install once, works everywhere.

## Proposed Solution

Use `last30days-skill-private` as the base (it's 4 days newer with better code) and port the OpenClaw-exclusive features in:

### What gets ported from OpenClaw

| File | What it does | Destination |
|------|-------------|-------------|
| `scripts/store.py` | SQLite research accumulator (WAL, FTS5, dedup) | `scripts/store.py` |
| `scripts/watchlist.py` | Topic watchlist CLI (add/remove/list/run) | `scripts/watchlist.py` |
| `scripts/briefing.py` | Morning briefing generator (daily/weekly) | `scripts/briefing.py` |
| `scripts/lib/brave_search.py` | Brave Search API (free tier, 2K/mo) | `scripts/lib/brave_search.py` |
| `scripts/lib/parallel_search.py` | Parallel AI search (LLM-optimized) | `scripts/lib/parallel_search.py` |
| `scripts/lib/openrouter_search.py` | OpenRouter/Sonar Pro search | `scripts/lib/openrouter_search.py` |
| `references/research.md` | One-shot research instructions | `variants/open/references/research.md` |
| `references/watchlist.md` | Watchlist mode instructions | `variants/open/references/watchlist.md` |
| `references/briefing.md` | Briefing mode instructions | `variants/open/references/briefing.md` |
| `references/history.md` | History query instructions | `variants/open/references/history.md` |

### What gets upgraded in the ported code

- **`store.py`**: No changes needed — it's self-contained SQLite, works as-is
- **`watchlist.py`**: Remove OpenClaw cron-specific code, make cron setup generic (launchd on macOS, systemd on Linux, or manual cron)
- **`briefing.py`**: No changes needed
- **`scripts/lib/env.py`**: Merge OpenClaw's web search key support (`PARALLEL_API_KEY`, `BRAVE_API_KEY`, `OPENROUTER_API_KEY`) and `has_web_search_keys()` / `get_web_search_source()` functions into main's env.py. Drop the OpenClaw config loader (`~/.openclaw/openclaw.json`) — just use env vars and `~/.config/last30days/.env`
- **`scripts/last30days.py`**: Add OpenClaw's `_search_web()` function so the script can do web search natively when API keys are available (instead of always delegating to the assistant)

### What gets DROPPED from OpenClaw

| File | Why |
|------|-----|
| `scripts/cron_setup.py` | Too OpenClaw-platform-specific. Replace with generic scheduling docs. |
| OpenClaw config loader in `env.py` | `~/.openclaw/openclaw.json` path is platform-specific. Use env vars instead. |
| `.clawhubignore` | OpenClaw marketplace artifact, not needed in unified repo |

### New file: Open variant SKILL.md

Create `variants/open/SKILL.md` — the multi-mode skill with command routing:

```
variants/open/
├── SKILL.md              # Router: watch, briefing, history, or one-shot
├── references/
│   ├── research.md       # One-shot research instructions
│   ├── watchlist.md      # Watchlist management instructions
│   ├── briefing.md       # Briefing mode instructions
│   └── history.md        # History query instructions
└── context.md            # Agent memory (user preferences, source quality)
```

The open variant's SKILL.md points to `{baseDir}/scripts/last30days.py` (same engine) but adds the router and reference file system. It also adds the `--store` flag for persistence.

### How YouTube and Bird CLI get added to the open variant

They're already in `scripts/lib/youtube_yt.py` and `scripts/lib/vendor/bird/`. The open variant's SKILL.md just needs to mention YouTube in its description and the research.md reference file gets the YouTube stats line in the output format. No code changes needed — the Python engine already supports all four sources.

## Technical Considerations

### File Structure After Merge

```
last30days-skill-private/
├── SKILL.md                          # Main skill (Claude Code / Codex)
├── agents/openai.yaml                # Codex discovery (existing)
├── variants/
│   └── open/
│       ├── SKILL.md                  # Open variant with routing
│       ├── references/
│       │   ├── research.md
│       │   ├── watchlist.md
│       │   ├── briefing.md
│       │   └── history.md
│       └── context.md
├── scripts/
│   ├── last30days.py                 # Unified engine (+ native web search)
│   ├── store.py                      # SQLite accumulator (from openclaw)
│   ├── watchlist.py                  # Watchlist CLI (from openclaw, genericized)
│   ├── briefing.py                   # Briefing generator (from openclaw)
│   └── lib/
│       ├── ... (existing files)
│       ├── brave_search.py           # NEW from openclaw
│       ├── parallel_search.py        # NEW from openclaw
│       ├── openrouter_search.py      # NEW from openclaw
│       ├── youtube_yt.py             # Existing
│       └── vendor/bird/              # Existing
└── README.md                         # Updated with open variant docs
```

### Installation for open variant users

```bash
# Claude Code (main skill — unchanged)
git clone https://github.com/mvanhorn/last30days-skill.git ~/.claude/skills/last30days

# Open variant (with watchlist, briefings, history)
git clone https://github.com/mvanhorn/last30days-skill.git ~/.claude/skills/last30days
# Then in Claude Code settings, point skill to variants/open/SKILL.md
# OR symlink:
ln -sf ~/.claude/skills/last30days/variants/open/SKILL.md ~/.claude/skills/last30days-open/SKILL.md
```

### env.py merge strategy

Main's `env.py` is the base. Add from OpenClaw:
- Three new key names: `PARALLEL_API_KEY`, `BRAVE_API_KEY`, `OPENROUTER_API_KEY`
- `has_web_search_keys()` function
- `get_web_search_source()` function — returns `'parallel'`, `'brave'`, or `'openrouter'`
- `get_available_sources()` update to include web-search-capable modes

### last30days.py merge strategy

Main's `last30days.py` is the base. Add from OpenClaw:
- `_search_web()` function that calls the appropriate web search backend
- `--store` CLI flag to persist findings to SQLite
- `--diagnose` CLI flag for source availability diagnostics
- Web results integration into the existing report pipeline (normalize → score → dedupe → render)

Keep main's:
- YouTube integration
- Phase 2 supplemental search
- 3-tier Reddit fallback
- Better error handling
- Minimum result guarantee

### Portable path resolution (already done)

The main SKILL.md already has portable path resolution (from Codex compat work):
```bash
for dir in "." "${CLAUDE_PLUGIN_ROOT:-}" "$HOME/.claude/skills/last30days" ...
```

The open variant's SKILL.md uses `{baseDir}` which resolves to the skill root. Both approaches work — we just need to make sure the open variant's references use `{baseDir}` consistently.

## Acceptance Criteria

- [x] `scripts/store.py` ported and working (SQLite creates on first use)
- [x] `scripts/watchlist.py` ported with generic scheduling (no OpenClaw cron dependency)
- [x] `scripts/briefing.py` ported and generates daily/weekly briefings
- [x] `scripts/lib/brave_search.py` ported and functional
- [x] `scripts/lib/parallel_search.py` ported and functional
- [x] `scripts/lib/openrouter_search.py` ported and functional
- [x] `scripts/lib/env.py` updated with web search key support
- [x] `scripts/last30days.py` has native `_search_web()` + `--store` + `--diagnose`
- [x] `variants/open/SKILL.md` exists with command routing (watch, briefing, history, research)
- [x] `variants/open/references/*.md` — all 4 reference files ported
- [x] Open variant mentions YouTube in description and research output format
- [x] Open variant uses same portable path resolution as main
- [x] Main SKILL.md behavior is unchanged (zero regressions)
- [x] `python3 scripts/last30days.py "test topic" --mock --emit=compact` still works
- [x] `python3 scripts/last30days.py "test topic" --diagnose` shows source availability
- [x] README documents open variant installation and usage

## Dependencies & Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| OpenClaw's store.py has import dependencies we don't have | Low | store.py uses only stdlib (sqlite3, json, datetime). Self-contained. |
| Web search backends need API keys to test | Medium | Each has a `--mock` or dry-run path. Test with real keys if available, mock otherwise. |
| watchlist.py depends on OpenClaw cron API | High | Known — strip cron_setup.py dependency, replace with generic docs for launchd/systemd/crontab. |
| Open variant SKILL.md is too long (>500 lines) | Medium | Use reference file pattern (already planned). Router SKILL.md stays under 100 lines. |
| env.py merge introduces regressions | Low | Main's env.py is well-tested. Additive changes only — new keys, new functions. |
| Two SKILL.md files = maintenance burden | Low | They serve different purposes. Main is simple one-shot. Open adds routing. Core engine is shared. |

## Files to Create/Modify

### New Files
- `variants/open/SKILL.md` — Open variant router (~100 lines)
- `variants/open/references/research.md` — One-shot research instructions (from openclaw, updated with YouTube)
- `variants/open/references/watchlist.md` — Watchlist management instructions (from openclaw)
- `variants/open/references/briefing.md` — Briefing mode instructions (from openclaw)
- `variants/open/references/history.md` — History query instructions (from openclaw)
- `variants/open/context.md` — Agent memory template
- `scripts/store.py` — SQLite accumulator (from openclaw, as-is)
- `scripts/watchlist.py` — Watchlist CLI (from openclaw, genericized)
- `scripts/briefing.py` — Briefing generator (from openclaw, as-is)
- `scripts/lib/brave_search.py` — Brave Search API (from openclaw)
- `scripts/lib/parallel_search.py` — Parallel AI search (from openclaw)
- `scripts/lib/openrouter_search.py` — OpenRouter/Sonar Pro search (from openclaw)

### Modified Files
- `scripts/lib/env.py` — Add web search key support (~30 lines added)
- `scripts/last30days.py` — Add `_search_web()`, `--store`, `--diagnose` (~80 lines added)
- `README.md` — Add open variant section (~20 lines)

### Total scope: ~12 new files (mostly copied), ~130 lines of new code in existing files.

## References

### Internal
- OpenClaw plan: `$HOME/last30days-openclaw/docs/plans/2026-02-10-feat-openclaw-last30days-skill-plan.md` (989 lines, comprehensive spec)
- Codex compat plan: `docs/plans/2026-02-14-feat-codex-skill-compatibility-plan.md` (portable paths, platform-neutral text)
- OpenClaw source: `$HOME/last30days-openclaw/`

### Key files to port
- `store.py`: `$HOME/last30days-openclaw/scripts/store.py` (20KB, SQLite with FTS5)
- `watchlist.py`: `$HOME/last30days-openclaw/scripts/watchlist.py` (10KB)
- `briefing.py`: `$HOME/last30days-openclaw/scripts/briefing.py` (8KB)
- `brave_search.py`: `$HOME/last30days-openclaw/scripts/lib/brave_search.py` (6KB)
- `parallel_search.py`: `$HOME/last30days-openclaw/scripts/lib/parallel_search.py` (4KB)
- `openrouter_search.py`: `$HOME/last30days-openclaw/scripts/lib/openrouter_search.py` (7KB)
- `env.py` (openclaw version): `$HOME/last30days-openclaw/scripts/lib/env.py` (9KB — has web search key functions)
