---
title: "Claude Code flattens every link in a reply to `label (url)` while a markdown list streams; reports are list-free on Claude Code"
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

# Claude Code flattens links while a list streams

## Symptom

In Claude Code, the top part of a long /last30days report showed every citation as `label (https://...)` instead of a clickable label. Lower down, the same report showed clickable labels. One line appeared twice where the two styles met. It happened on some runs and not others, in the same terminal.

First recorded on 2026-10-11 (Claude Code 2.1.296, Ghostty) and attributed to the background-session reattach preview. That preview does render links off, but it was not the cause of the intermittent case. Re-diagnosed on 2026-11-09 against Claude Code 2.1.321.

## Cause

The skill emitted correct markdown: every citation was `[label](url)`.

While a reply streams, Claude Code re-parses the partial text on each update and checks that the parser's tokens reproduce the input exactly. When they do not, it marks the reply link-less for the rest of the stream and redraws all of it with links as `label (url)`.

The check fails when the text so far ends in a space inside a list item, for example `1. The ` or `- a` followed by a new `- `. The markdown parser trims that trailing space from the list's raw text, so the round trip differs. Whether a streamed chunk ends on such a space is chance.

When the reply completes, the final render has links on again, but only the visible screen is repainted. Rows already in scrollback keep the flattened form. The repeated line is where the two renders meet.

Every standard report ends with a numbered KEY PATTERNS list and a bulleted invitation, so every report was exposed.

## Fix

On Claude Code (`CLAUDECODE` set), `references/synthesis.md` tells the model to write no markdown list: numbered markers as an escaped `1\.` and bullets as a typed `• `, one per line. Claude Code draws these as `1.` and `•`, and its parser sees ordinary paragraph lines. Other hosts keep normal lists, because renderers that reflow markdown would merge the lines.

Keep LAW 8's hidden-link default for Claude Code. Switching to plain labels would hide the symptom by removing clickable links from every report.

## Evidence

A real report was replayed through interactive Claude Code sessions in a hidden pseudo-terminal, counting hyperlink escape sequences against flattened `(https://` draws in the raw output. The list format flattened links in 2 of 9 runs; the list-free format in 0 of 15. Both failures began while the KEY PATTERNS list was streaming.

## Guardrails

- `tests/test_claude_code_list_free_report.py` pins the list-free rule and its Claude Code scope.
- `tests/test_codex_host_contract.py::test_law8_keeps_claude_code_hidden_link_despite_reattach_preview` fails if Claude Code is moved to plain labels.
- This is a workaround. If Claude Code stops disabling links mid-stream, the rule can go. To re-test, replay a report with real lists through a pseudo-terminal and look for `(https://` outside the echoed prompt.
- If a whole live render is flattened from the first line, check terminal hyperlink support (`FORCE_HYPERLINK`, OSC 8) instead.
