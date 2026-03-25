---
title: "feat: Release last30days v2 with Bird CLI to GitHub"
type: feat
date: 2026-02-06
---

# Release last30days v2 (Bird CLI) to GitHub

## Overview

Replace the current public `last30days-skill` on GitHub with the new Bird CLI-enhanced version from `last30days-skill-private`. The new version adds free X/Twitter search via Bird CLI while maintaining backward compatibility with xAI API keys.

**Goal:** Ship with confidence. No rollbacks.

## Current State

| | Old (Public) | New (Private) |
|---|---|---|
| **Repo** | `mvanhorn/last30days-skill` | `mvanhorn/last30days-skill-private` |
| **Local path** | `~/.claude/skills/last30days/` | `~/.claude/skills/last30daystest/` (symlink) |
| **Remote** | `origin` → public repo | `origin` → private, `upstream` → public |
| **Key addition** | -- | Bird CLI (`@steipete/bird`) for free X search |
| **X source chain** | xAI API only | Bird (free) → xAI (paid) → WebSearch |
| **Uses** | 136 | 18 |
| **Latest commit** | `cc892d7` | `4230fa2` |

**Why both show as `/last30days`:** Both `SKILL.md` files declare `name: last30days` in frontmatter. Claude Code discovers both from `~/.claude/skills/` and lists them separately.

---

## Phase 0: Clean Swap (Day 1)

Remove the old skill so only the new one is active. This eliminates ambiguity during testing.

### Steps

1. **Back up the old skill** (safety net):
   ```bash
   mv ~/.claude/skills/last30days ~/.claude/skills/last30days.backup-v1
   ```

2. **Promote the new skill to primary**:
   ```bash
   # Remove the test symlink
   rm ~/.claude/skills/last30daystest

   # Create new symlink with the primary name
   ln -s $HOME/last30days-skill-private ~/.claude/skills/last30days
   ```

3. **Verify only one `/last30days` appears**:
   - Open a new Claude Code session
   - Type `/last` and confirm only ONE `/last30days` shows in autocomplete
   - Confirm description mentions Bird CLI

4. **Rollback procedure** (if something goes wrong):
   ```bash
   rm ~/.claude/skills/last30days
   mv ~/.claude/skills/last30days.backup-v1 ~/.claude/skills/last30days
   ```

### Acceptance Criteria
- [ ] Only one `/last30days` appears in Claude Code autocomplete
- [ ] Old skill preserved at `~/.claude/skills/last30days.backup-v1`
- [ ] New skill responds to `/last30days` invocation

---

## Phase 1: Claude's Test Plan (Automated)

These are tests Claude can run autonomously to validate the new skill before the user touches it.

### 1.1 Script-Level Smoke Tests

Run the Python scripts directly to verify core functionality without invoking the full skill.

#### Bird CLI Detection
```bash
# Test: Bird is installed and authenticated
python3 -c "
import sys; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import bird_x
print('installed:', bird_x.is_bird_installed())
print('authenticated:', bird_x.is_bird_authenticated())
print('status:', bird_x.get_bird_status())
"
```
- [ ] `is_bird_installed()` returns True (or False with clear message)
- [ ] `is_bird_authenticated()` returns True if logged into X in browser
- [ ] `get_bird_status()` returns a dict with `installed`, `authenticated`, `available` keys

#### Environment & Source Detection
```bash
python3 -c "
import sys; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import env
config = env.load_config()
print('x_source:', env.get_x_source(config))
print('has_openai:', bool(config.get('OPENAI_API_KEY')))
"
```
- [ ] `get_x_source()` returns `'bird'` if Bird available, `'xai'` if API key set, `None` otherwise
- [ ] Config loads from `~/.config/last30days/.env`

#### Bird Search (Direct)
```bash
python3 -c "
import sys, json; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import bird_x
result = bird_x.search_x('Claude Code tips', '2026-01-07', '2026-02-06', 'quick')
print(json.dumps(result, indent=2, default=str)[:2000])
"
```
- [ ] Returns search results (list of dicts with `url`, `text`, `author_handle`)
- [ ] No Python tracebacks
- [ ] Results are from the expected date range

#### Full Research Pipeline (Compact Output)
```bash
cd $HOME/last30days-skill-private
python3 scripts/last30days.py "Claude Code tips" --emit=compact --quick 2>&1 | head -100
```
- [ ] Completes without error
- [ ] Output includes X results (via Bird or xAI)
- [ ] Output includes Reddit results (via OpenAI) if key configured
- [ ] Stats summary shows source counts

### 1.2 Source Fallback Tests

Verify graceful degradation when sources are unavailable.

#### Bird unavailable, xAI available
```bash
# Temporarily hide Bird
PATH_BACKUP="$PATH"
export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v "$(dirname $(which bird 2>/dev/null))" | tr '\n' ':')

python3 -c "
import sys; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import env
config = env.load_config()
print('x_source (no bird):', env.get_x_source(config))
"

export PATH="$PATH_BACKUP"
```
- [ ] Falls back to `'xai'` when Bird not in PATH
- [ ] No crash or unhandled exception

#### No X source at all
```bash
python3 -c "
import sys; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import env
config = {}  # empty config, no keys
print('x_source (nothing):', env.get_x_source(config))
"
```
- [ ] Returns `None`
- [ ] No crash

### 1.3 Response Parsing Tests

Validate that Bird responses are correctly normalized to the canonical schema.

```bash
python3 -c "
import sys; sys.path.insert(0, '$HOME/last30days-skill-private/scripts/lib')
import bird_x

# Test with sample Bird response format
sample = {
    'tweets': [{
        'permanentUrl': 'https://x.com/user/status/123',
        'text': 'Test tweet about Claude Code',
        'username': 'testuser',
        'likeCount': 42,
        'retweetCount': 10,
        'replyCount': 5,
        'timeParsed': '2026-02-01T12:00:00.000Z'
    }]
}
parsed = bird_x.parse_bird_response(sample)
print('Parsed count:', len(parsed))
print('First item keys:', sorted(parsed[0].keys()) if parsed else 'EMPTY')
print('URL:', parsed[0].get('url'))
print('Author:', parsed[0].get('author_handle'))
"
```
- [ ] Parses correctly with expected keys
- [ ] Handles both camelCase and snake_case fields
- [ ] URL, text, author, engagement metrics all present

### 1.4 SKILL.md Validation

```bash
# Verify YAML frontmatter parses correctly
python3 -c "
import yaml
with open('$HOME/last30days-skill-private/SKILL.md') as f:
    content = f.read()
    # Extract YAML between --- markers
    parts = content.split('---', 2)
    meta = yaml.safe_load(parts[1])
    print('name:', meta.get('name'))
    print('context:', meta.get('context'))
    print('agent:', meta.get('agent'))
    print('allowed-tools:', meta.get('allowed-tools'))
"
```
- [ ] `name` is `last30days` (not `last30daystest`)
- [ ] `context` is `fork`
- [ ] `agent` is `Explore`
- [ ] `allowed-tools` includes `Bash`, `WebSearch`

### 1.5 Diff Audit (Old vs New)

```bash
# Verify the only meaningful addition is bird_x.py
diff -rq ~/.claude/skills/last30days.backup-v1/scripts/lib/ \
         $HOME/last30days-skill-private/scripts/lib/ 2>/dev/null
```
- [ ] Only new file is `bird_x.py`
- [ ] Modified files: `env.py` (source detection), `__init__.py` (exports)
- [ ] No unexpected deletions or renames

---

## Phase 2: User's Test Plan (Manual)

These require human judgment - evaluating quality, UX, and real-world behavior.

### 2.1 Basic Invocation (5 min)

Open a fresh Claude Code session after Phase 0 is complete.

| # | Test | Command | Pass Criteria |
|---|------|---------|---------------|
| 1 | Simple topic | `/last30days AI music generation` | Returns results, shows source stats |
| 2 | Topic + tool | `/last30days Suno prompts for music production` | Returns results + generates a prompt |
| 3 | Quick mode | `/last30days --quick TypeScript tips` | Faster, fewer results, still valid |
| 4 | Empty input | `/last30days` | Prompts for topic (doesn't crash) |

### 2.2 Bird CLI Verification (5 min)

| # | Test | What to Check |
|---|------|---------------|
| 1 | Source indicator | Output shows Bird as X source (not xAI) |
| 2 | X results quality | X/Twitter results are real, recent, have engagement metrics |
| 3 | Bird promo | If Bird NOT installed, shows non-blocking info banner |
| 4 | Mixed sources | Both Reddit (OpenAI) and X (Bird) results appear |

### 2.3 Fallback Behavior (5 min)

| # | Test | Setup | Expected |
|---|------|-------|----------|
| 1 | No Bird | `npm uninstall -g @steipete/bird` temporarily | Falls back to xAI or WebSearch |
| 2 | No API keys | Rename `~/.config/last30days/.env` temporarily | WebSearch-only mode works |
| 3 | Restore | Reinstall bird + restore .env | Full mode returns |

### 2.4 Output Quality (10 min)

Run 3 real research queries you care about. For each, evaluate:

- [ ] Results are actually from the last 30 days (not stale)
- [ ] Engagement metrics (likes, upvotes) are present and reasonable
- [ ] No duplicate results
- [ ] Sources are properly cited with URLs
- [ ] Synthesis is grounded in actual results (not hallucinated)
- [ ] Generated prompts (if requested) are usable

### 2.5 Comparison Test (10 min)

Before removing the backup, run the SAME query on both versions:

```bash
# New version (active)
/last30days [your topic]

# Old version (temporarily restore)
rm ~/.claude/skills/last30days
mv ~/.claude/skills/last30days.backup-v1 ~/.claude/skills/last30days
# New Claude Code session
/last30days [same topic]
# Then swap back
```

- [ ] New version produces equal or better results
- [ ] No features regressed
- [ ] Bird results add value over xAI-only

---

## Phase 3: Release Plan (30-Day Timeline)

**Target release date:** March 1, 2026 (conservative buffer before March 8 deadline)

### Week 1: Feb 6-12 - Clean & Test

| Day | Task | Owner |
|-----|------|-------|
| Feb 6 | Phase 0: Clean swap (remove old, activate new) | User |
| Feb 6 | Phase 1: Claude runs automated tests | Claude |
| Feb 7-8 | Phase 2: User runs manual tests (2.1-2.4) | User |
| Feb 9 | Phase 2.5: Comparison test | User |
| Feb 10-12 | Fix any issues found during testing | Claude + User |

### Week 2: Feb 13-19 - Harden

| Day | Task | Owner |
|-----|------|-------|
| Feb 13 | Run edge cases: unicode topics, very long topics, special chars | Claude |
| Feb 14 | Test with Bird logged out (auth expiry scenario) | User |
| Feb 15 | Review all error messages for clarity | Claude |
| Feb 16-17 | Update README.md with Bird CLI setup instructions | Claude |
| Feb 18-19 | Buffer for fixes | Claude + User |

### Week 3: Feb 20-26 - Pre-Release

| Day | Task | Owner |
|-----|------|-------|
| Feb 20 | Final diff audit: private repo vs public repo | Claude |
| Feb 21 | Strip any private/test artifacts (test symlinks, debug prints) | Claude |
| Feb 22 | Update SKILL.md description if needed | Claude |
| Feb 23 | Dry-run: push to a branch on public repo (not main) | User |
| Feb 24 | Test installation from the branch (fresh `~/.claude/skills/`) | User |
| Feb 25-26 | Buffer for fixes | Claude + User |

### Week 4: Feb 27 - Mar 1 - Ship

| Day | Task | Owner |
|-----|------|-------|
| Feb 27 | Merge branch to main on public repo | User |
| Feb 28 | Create GitHub release with changelog | Claude + User |
| Mar 1 | Delete backup: `rm -rf ~/.claude/skills/last30days.backup-v1` | User |
| Mar 1 | Archive private repo (optional) | User |

### Release Checklist (Final Gate)

Before merging to `main` on the public repo:

- [ ] All Phase 1 automated tests pass
- [ ] All Phase 2 manual tests pass
- [ ] Comparison test shows new >= old quality
- [ ] SKILL.md frontmatter is correct (`name: last30days`, not `last30daystest`)
- [ ] README.md documents Bird CLI setup
- [ ] No debug/test artifacts in codebase
- [ ] No hardcoded paths (e.g., `$HOME/...`)
- [ ] `.env` files are gitignored
- [ ] Git history is clean (no "test" or "WIP" commits on main)
- [ ] Bird CLI failure doesn't break the skill (graceful fallback verified)

### Rollback Plan (Emergency)

If something goes wrong after release:

```bash
# Option 1: Revert to backup (if still exists)
rm ~/.claude/skills/last30days
mv ~/.claude/skills/last30days.backup-v1 ~/.claude/skills/last30days

# Option 2: Git revert on public repo
cd ~/.claude/skills/last30days
git log --oneline -5  # find the last good commit
git revert HEAD        # revert the merge commit
git push origin main

# Option 3: Pin to old version
cd ~/.claude/skills/last30days
git checkout cc892d7   # last known good commit from old version
```

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Bird CLI breaks after X API changes | Medium | Low | Fallback to xAI/WebSearch still works |
| Bird auth expires silently | Medium | Low | `is_bird_authenticated()` check + user message |
| Old xAI workflows regress | Low | High | Comparison test in Phase 2.5 |
| Hardcoded paths in codebase | Low | Medium | Grep for `/Users/` or `/home/` before release |
| SKILL.md name still says `last30daystest` | Low | High | Already fixed in commit `4e972d0` |

## References

- Private repo: `https://github.com/mvanhorn/last30days-skill-private.git`
- Public repo: `https://github.com/mvanhorn/last30days-skill.git`
- Bird CLI: `https://github.com/steipete/bird`
- Bird implementation plan: `docs/plans/2026-02-03-bird-cli-implementation.md`
- Bird integration design: `docs/plans/2026-02-03-bird-cli-integration-design.md`
