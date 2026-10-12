---
title: "Claude Code's background-session reattach preview prints every markdown link as `label (url)`; LAW 8's hidden-link default is still correct"
date: "2026-10-11"
category: "integration-issues"
module: "skills/last30days/SKILL.md (LAW 8)"
problem_type: "integration_issue"
component: "synthesis_output"
severity: "low"
applies_when:
  - "A /last30days report in Claude Code shows full URLs in parentheses after every source name"
  - "The top of a report shows `label (https://...)` while the bottom shows clickable blue labels, with one line printed twice at the boundary"
  - "Someone proposes moving Claude Code to LAW 8's visible-URL (plain-label) regime to hide URLs"
---

# Claude Code reattach preview flattens links

## Symptom

In Claude Code, the top part of a long /last30days report showed every citation as `label (https://...)` instead of a clickable label. That included X, YouTube, Reddit, GitHub, and web links, so the problem was not specific to X. Lower down, the same report showed clickable blue labels (`u/Defkox`, `r/gamedev`, `HN`). One line appeared twice at the point where the two styles met.

Seen on 2026-10-11 with Claude Code 2.1.296 in Ghostty (via cmux), after a `/last30days Reverse Engineer Anything` run.

## Cause

The skill emitted correct markdown. Every citation was `[label](url)`, as LAW 8 requires on hidden-link hosts.

The flattening happens in Claude Code. When a client reattaches to a background session, Claude Code prints a tail of the transcript before the live session redraws. That preview renders assistant markdown with hyperlinks hard-coded off (`linkCap:!1` in the attach-preview renderer, in the module with the `Waiting for session to redraw… Ctrl+Z to detach` string). With hyperlinks off, Claude Code's link renderer falls back to `${text} (${url})`.

The live session then draws below the preview with hyperlinks on, so links are clickable from that point down. The duplicated line is where the preview tail overlaps the redraw.

Claude Code's normal markdown path also drops hyperlinks when one markdown chunk passes 1,000 lines or about 100K characters ("windowed" rendering). A last30days report is far below that limit, so it was not the cause here.

## Fix

No skill change. Keep LAW 8's hidden-link default for Claude Code:

- In the live view, `[label](url)` renders as a clickable label with no visible URL. That is the intended experience, and it lets readers open the source post.
- Switching Claude Code to plain labels would hide URLs in the preview, but it would also remove clickable links from every live report. Readers would then need the saved raw file or a follow-up question to reach a source.

The upstream fix belongs in Claude Code: render the reattach preview with the same hyperlink support as the live view.

## Guardrails

- `tests/test_codex_host_contract.py::test_law8_keeps_claude_code_hidden_link_despite_reattach_preview` fails if Claude Code is named as a visible-URL host or drops out of the hidden-link host list.
- If URLs show up in a Claude Code report, first check whether the session was reattached (top flattened, bottom clickable, one line repeated). If the whole live render is flattened, check terminal hyperlink support (`FORCE_HYPERLINK`, OSC 8) before changing LAW 8.
