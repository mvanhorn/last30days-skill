---
title: "fix: comparison title says (/Last30Days) instead of (Last 30 Days)"
type: fix
status: active
date: 2026-04-22
---

# fix: comparison title says (/Last30Days) instead of (Last 30 Days)

## Overview

User feedback 2026-04-22 on the 3.0.13 release runs (Kanye vs Drake, Mercer Island, Figma): the comparison title currently reads `# Kanye West vs Drake: What the Community Says (Last 30 Days)`. It should read `# Kanye West vs Drake: What the Community Says (/Last30Days)` — attributing the output to the slash command rather than describing the date range generically.

Single-line change in SKILL.md, three occurrences. No code change.

## Requirements Trace

- R1. Comparison title pattern in SKILL.md changes from `(Last 30 Days)` to `(/Last30Days)` so synthesis outputs read `... What the Community Says (/Last30Days)`.
- R2. Both the rule statement (line 113) and the COMPARISON-exception statement (line 131) and the synthesis template example (line 1208) all use the new suffix.
- R3. Version bumps to 3.0.14, CHANGELOG entry, sync, hot-copy. Public cache picks up the new title pattern.

## Scope Boundaries

- No changes to the single-entity output title (no `(/Last30Days)` suffix there — only comparison topics carry it).
- No changes to engine code. Pure SKILL.md content.
- No changes to anything else surfaced in the test runs.

## Key Technical Decisions

- **Replace all three occurrences of the suffix string in one pass.** They are identical strings; changing one without the others would cause synthesis-time confusion when the model reaches a different reference.
- **Ship as 3.0.14, not 3.0.13.x.** Patch-level bump matches the small scope and keeps the release log clean.

## Implementation Units

- [ ] **Unit 1: Replace `(Last 30 Days)` → `(/Last30Days)` in SKILL.md**

**Goal:** All three SKILL.md references to the comparison title use the new suffix.

**Requirements:** R1, R2

**Files:**
- Modify: `SKILL.md`

**Approach:**
- `replace_all` swap of `What the Community Says (Last 30 Days)` → `What the Community Says (/Last30Days)`. Three occurrences, no other strings overlap.

**Test scenarios:**
- Test expectation: none — pure documentation. Verification by inspection + dogfood run.

**Verification:**
- `grep -c "What the Community Says (/Last30Days)" SKILL.md` returns 3.
- `grep -c "What the Community Says (Last 30 Days)" SKILL.md` returns 0.

- [ ] **Unit 2: Version 3.0.14 + CHANGELOG + sync + hot-copy**

**Goal:** Ship 3.0.14 to all local targets.

**Requirements:** R3

**Dependencies:** Unit 1

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Run: `bash scripts/sync.sh`
- Hot-copy: `~/.claude/plugins/cache/last30days-skill/last30days/3.0.14/`

**Approach:**
- CHANGELOG: "Changed: comparison-mode title attribution — `What the Community Says (Last 30 Days)` → `What the Community Says (/Last30Days)`. Surfaces the slash-command identity instead of restating the date range."

**Test scenarios:**
- Test expectation: none — packaging.

**Verification:**
- `grep version .claude-plugin/plugin.json` → 3.0.14.
- Hot-copy contains the updated SKILL.md.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Hosting model has the old title pattern memorized from a prior run and re-emits `(Last 30 Days)`. | SKILL.md is read top-to-bottom each invocation. STEP 0 canonical-path self-check (3.0.12) ensures the model loads the new SKILL.md, not the marketplace stale copy. |

## Sources & References

- 2026-04-22 dogfood runs (Kanye West vs Drake, Mercer Island --competitors, Figma --competitors)
- Related code: `SKILL.md` lines 113, 131, 1208
