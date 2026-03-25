---
title: "fix: Eliminate save-related output noise after research"
type: fix
status: active
date: 2026-03-06
---

# fix: Eliminate save-related output noise after research

## Problem Statement

After research completes, the auto-save feature adds 3-5 lines of unwanted output below the clean invitation block. Every approach tried so far has made things worse:

| Version | Approach | Lines added | Side effects |
|---------|----------|-------------|--------------|
| v2.9.1 | `Write` tool | ~5 | Shows "Wrote 43 lines to..." |
| v2.9.2 | `run_in_background: true` | ~5 | Background callback triggers 2-4 min cogitation, model hallucinates fake "Human:" messages and generates unsolicited multi-paragraph responses |
| v2.9.3 | Foreground `cat >` heredoc | ~3 | Shows `(No output)`, still triggers 2-4 min cogitation |

**Current v2.9.3 noise (3 lines):**
```
⏺ Bash(mkdir -p ~/Documents/Last30Days && cat > ...)
  ⎿  (No output)
✻ Churned for 2m 38s
```

The root cause is that ANY tool call after the invitation creates unavoidable Claude Code UI chrome, and the model cogitates for minutes regardless of foreground vs background.

## Proposed Solutions

### Option A: Remove auto-save entirely (Recommended)

Remove the entire "Save Research to Documents" section from SKILL.md. The research lives in the conversation. Zero extra tool calls, zero extra lines, zero cogitation.

**Changes to `SKILL.md`:**
- Delete the "Save Research to Documents" section (~45 lines)
- Update agent mode reference (line 126) - remove save mention
- Update security section (line 612) - change "Saves research briefings" to past tense or conditional
- Remove `📎` footer line from invitation format
- Simplify "WAIT FOR USER'S RESPONSE" section

**What users lose:** Auto-saved .md files in `~/Documents/Last30Days/`
**What users gain:** Clean output ending exactly at the invitation block

### Option B: Move save into Python script

Add `--save-dir` flag to `last30days.py`. The script saves its raw output during its existing Bash call (which already runs). Zero extra tool calls.

**Changes:**
- `scripts/last30days.py` - add `--save-dir` argument, write raw output to file at end of execution
- `SKILL.md` - add `--save-dir=~/Documents/Last30Days` to the script invocation, remove save section

**What users lose:** Saved file contains raw research data, not Claude's synthesis
**What users gain:** Zero extra lines, data is still preserved

### Option C: Opt-in save via follow-up command

Remove auto-save. Add a note to the invitation: "Say 'save' to save this research." When user says "save", run the Bash heredoc then.

**Changes to `SKILL.md`:**
- Delete auto-save section
- Add "save" as a recognized intent in "WHEN USER RESPONDS" section
- Save logic only runs when explicitly requested

**What users lose:** Nothing - save is still available on demand
**What users gain:** Clean output by default, save when they want it

## Acceptance Criteria

- [ ] Research output ends at the invitation block with zero tool calls after it
- [ ] No `(No output)` line visible after invitation
- [ ] No multi-minute cogitation after invitation
- [ ] No hallucinated fake user messages
- [ ] Version bumped to v2.9.4
- [ ] CHANGELOG updated
- [ ] Synced to ~/.claude, ~/.agents, ~/.codex via sync.sh

## Context

- Source: `$HOME/last30days-skill-private/SKILL.md`
- Deployed to: `~/.claude/skills/last30days/SKILL.md`
- Sync command: `bash scripts/sync.sh`
- Current version: v2.9.3
