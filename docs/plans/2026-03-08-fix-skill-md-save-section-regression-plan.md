---
title: "fix: Remove re-introduced Save Research to Documents section from SKILL.md"
type: fix
status: completed
date: 2026-03-08
---

# fix: Remove re-introduced Save Research to Documents section from SKILL.md

## Overview

PR merges on March 7 regressed SKILL.md by re-introducing the "Save Research to Documents" section that v2.9.4 (`6d5acb9`) intentionally removed. The save logic was moved into the Python script via `--save-dir` flag, but the merged branches (PR #48 Xiaohongshu, upstream merge) were forked before v2.9.4 and brought the old SKILL.md content back via merge resolution.

**Result:** The skill now saves research AFTER the "I'm now an expert" invitation via a separate Bash tool call, which causes extra cogitation time, "(No output)" noise, and sometimes hallucinated follow-up messages - the exact UX problems v2.9.4 fixed.

## Root Cause

1. Commit `6d5acb9` (v2.9.4, March 6) removed the ~45-line "Save Research to Documents" section and added `--save-dir=~/Documents/Last30Days` to the bash command
2. PR #48 (Xiaohongshu) branch contained commit `9950d01` with the OLD save-via-Bash version
3. Merging PR #48 on March 7 re-introduced the save section
4. Upstream merge (`28dff6e`) compounded it

## Three changes needed

All in `SKILL.md`:

### 1. Fix agent mode line (line 142)

**Current (broken):**
```
Agent mode still saves the research briefing to `~/Documents/Last30Days/` using the same logic as interactive mode (see "Save Research to Documents" section).
```

**Should be:**
```
Agent mode saves raw research data to `~/Documents/Last30Days/` automatically via `--save-dir` (handled by the script, no extra tool calls).
```

### 2. Add `--save-dir` back to bash command (line 186)

**Current (broken):**
```bash
python3 "${SKILL_ROOT}/scripts/last30days.py" "$ARGUMENTS" --emit=compact --no-native-web
```

**Should be:**
```bash
python3 "${SKILL_ROOT}/scripts/last30days.py" "$ARGUMENTS" --emit=compact --no-native-web --save-dir=~/Documents/Last30Days
```

### 3. Delete the entire "Save Research to Documents" section (lines 517-563)

Remove from `## Save Research to Documents` through the `---` separator before `## WAIT FOR USER'S RESPONSE`. This is ~47 lines.

### 4. Update "STOP and wait" line (line 567)

**Current:**
```
**STOP and wait** for the user to respond.
```

**Should be (matching v2.9.4):**
```
**STOP and wait** for the user to respond. Do NOT call any tools after displaying the invitation. The research script already saved raw data to `~/Documents/Last30Days/` via `--save-dir`.
```

## Post-edit steps

1. Run `bash $HOME/last30days-skill-private/scripts/sync.sh` to deploy to `~/.claude/skills/`
2. Push to `origin/main` and `upstream/main`
3. Verify with `diff` that installed skill matches repo

## Acceptance Criteria

- [ ] "Save Research to Documents" section is gone from SKILL.md
- [ ] `--save-dir=~/Documents/Last30Days` is in the bash command
- [ ] Agent mode line references `--save-dir`, not the deleted section
- [ ] "STOP and wait" line includes the "do NOT call any tools" reinforcement
- [ ] `sync.sh` deployed successfully
- [ ] `diff` between repo and `~/.claude/skills/last30days/SKILL.md` shows no differences
- [ ] Pushed to origin and upstream
