---
title: "release: Push V2 to public repo and launch"
type: release
date: 2026-02-07
---

# release: Push V2 to public repo and launch

## Overview

Push all V2 changes from the private development repo to the public GitHub repo, then sync the installed skill. 52 commits need to go from `origin/main` (private) to `upstream/main` (public).

## Current State

- **Private repo:** `https://github.com/mvanhorn/last30days-skill-private` - 52 commits ahead of public
- **Public repo:** `https://github.com/mvanhorn/last30days-skill` - last commit is V1 (`cc892d7`)
- **Upstream remote:** Already configured in private repo
- **Untracked files in private:** test logs, draft plans - these should NOT be pushed

## Steps

### Step 1: Clean up private repo

- [ ] Review untracked files - make sure nothing sensitive gets pushed
- [ ] Decide: commit the untracked docs/plans or leave them out of the push

### Step 2: Push to public

```bash
cd $HOME/last30days-skill-private
git push upstream main
```

This pushes all 52 commits from private `main` to public `main`. Since the private repo was forked from public, history is shared - this is a fast-forward push.

### Step 3: Sync installed skill

```bash
# Update the installed copy that Claude Code actually uses
cd ~/.claude/skills/last30days
git pull origin main
```

### Step 4: Verify

- [ ] Check `https://github.com/mvanhorn/last30days-skill` shows V2 README with new examples
- [ ] Check SKILL.md has the new argument-hint and timing disclaimer
- [ ] Check bird_x.py has the fixed `_extract_core_subject()` and retry logic
- [ ] Run a quick `/last30days` test to confirm installed skill works

## What Gets Published

Key files going public:
- `README.md` - V2 features, 3 new examples (Nano Banana Pro, Kanye, Vibe Motion), speed tradeoff note
- `SKILL.md` - New argument-hint, timing disclaimer, citation rules
- `scripts/lib/bird_x.py` - Fixed query construction + retry logic
- `scripts/lib/http.py` - USER_AGENT bumped to 2.0
- `.claude-plugin/plugin.json` - Marketplace support
- All Bird CLI integration code
- Phase 2 supplemental search code
- Model fallback chain

## Risks

- **Low risk:** This is a fast-forward push, no force push needed
- **Public API keys:** Already confirmed - no `.env` files or secrets in the repo
- **Untracked files:** Won't be pushed unless committed first
