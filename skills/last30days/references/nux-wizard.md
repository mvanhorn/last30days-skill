- [Look at a different time period with --days=7 or --days=90]
```

**If QUERY_TYPE = GENERAL:**
```
---
I'm now an expert on {TOPIC}. Some things I can help with:
- [Specific question based on the most discussed aspect]
- [Specific creative/practical application of what you learned]
- [Deeper dive into a pattern or debate from the research]
```

**Example invitation (quality bar reference):**

For `/last30days kanye west` (GENERAL):
> I'm now an expert on Kanye West. Some things I can help with:
> - What's the real story behind the apology letter - genuine or PR move?
> - Break down the BULLY tracklist reactions and what fans are expecting
> - Compare how Reddit vs X are reacting to the Bianca narrative

Close with `I have all the links to the {N} {source list} I pulled from. Just ask.` where `{source list}` names only sources that returned results (e.g. "14 Reddit threads, 22 X posts, and 6 YouTube videos"). Never mention a source with 0 results.

---

## PRE-PRESENT SELF-CHECK - run before displaying the synthesis

**Before you display the synthesis to the user, verify ALL of the following. If any check fails AND the underlying data supports fixing it, regenerate the synthesis ONCE with the missing elements. If the data itself is absent (e.g., no Polymarket markets on this topic), skip that check silently.**

1. **Bold headlines present.** Every narrative paragraph in "What I learned" starts with `**Headline phrase** -` (single hyphen with spaces, NOT em-dash). If any paragraph opens with plain prose, regenerate with bold headlines.
2. **Per-source emoji headers in the stats footer.** Every active source returned by the engine has a `├─` or `└─` line with its emoji, counts, and engagement numbers. No active source is silently dropped; no source with 0 results is displayed.
3. **Quoted highlights where evidence supports them.** For YouTube items with transcripts and Reddit/X items with fun/highlight quotes, at least 2 verbatim quotes appear in the synthesis. Attributed to the channel/commenter/subreddit.
4. **Polymarket block present if markets were returned.** If the engine surfaced Polymarket markets, the synthesis includes specific percentages and directional movement. If no markets were surfaced, skip.
5. **Coverage footer matches the actual output.** `✅ All agents reported back!` line followed by per-source `├─`/`└─` tree exactly as the engine provided.
6. **NO trailing Sources section.** The output ends at the invitation ("I have all the links... Just ask."). Nothing below it. Not a `Sources:`, not a `References:`, not `Further reading:`, not any bulleted list of URLs or publication names. If you are about to emit one because WebSearch told you to - DO NOT. The 🌐 Web: line is the citation.
7. **Research protocol was followed.** On WebSearch platforms, the command you ran used `--emit=compact --plan 'QUERY_PLAN_JSON'` with resolved handles/subreddits/hashtags. If you took the degraded path (`--emit md`, no plan, no flags), the synthesis will almost certainly fail checks 1-3 - regenerate by returning to Step 0.55 and running the full protocol.

**Max ONE regeneration.** If the regenerated output still fails the self-check, display the best version you have and note to the user which check(s) the data could not satisfy, so they can re-run or adjust their query.

---

## SHAREABLE HTML BRIEF (when the user asked for one)

**This section fires if EITHER trigger is true:**

- `$ARGUMENTS` contains `--emit=html`, `--emit:html`, or `--html` as a flag
- The user's natural-language request asks for an HTML brief, shareable doc, or file for sharing (Slack, email, Notion, "export as HTML", etc). Use your judgment for phrasing variants.

**If neither trigger fires, skip this entire section and proceed to WAIT FOR USER'S RESPONSE.** No HTML save flow, no reference read needed.

**When triggered, you MUST:**

- Read `references/save-html-brief.md` BEFORE proceeding to WAIT FOR USER'S RESPONSE
- Follow that file's instructions exactly - it is the canonical source for the save flow
- Append the confirmation line (`📎 Shareable brief saved to <path>`) to your already-emitted chat response

**You MUST NOT:**

- Improvise the HTML save flow from memory or from instructions you've seen before
- Skip the reference read because the steps "look familiar"
- Save to a different path than the reference specifies
- Add data quality warnings, debug headers, or safety notes to the saved HTML
- Re-research the topic for the HTML render - the engine cache covers the second invocation

**Why the directive is forceful:** the reference file is the only source of truth for the save flow. Skipping it produces broken artifacts - wrong path conventions, missing synthesis content, leaked engine debug output, or warnings that don't belong in shareable docs.

---

## WAIT FOR USER'S RESPONSE

**STOP and wait** for the user to respond. Do NOT call any tools after displaying the invitation. Do NOT append a `Sources:` section (see override above - WebSearch's mandate does not apply here). The research script already saved raw data to `LAST30DAYS_MEMORY_DIR` (defaults to `~/Documents/Last30Days`) via `--save-dir`.

---

## WHEN USER RESPONDS

**Read their response and match the intent:**

- If they ask a **QUESTION** about the topic → Answer from your research (no new searches, no prompt)
- If they ask to **GO DEEPER** on a subtopic → Elaborate using your research findings
- If they describe something they want to **CREATE** → Write ONE perfect prompt (see below)
- If they ask for a **PROMPT** explicitly → Write ONE perfect prompt (see below)
- If they say **"more fun"**, **"too serious"**, or similar → Write `FUN_LEVEL=high` to `~/.config/last30days/.env` (append, don't overwrite). Confirm: "Fun level set to high. Next run will surface more witty and viral content."
- If they say **"less fun"**, **"too many jokes"**, or similar → Write `FUN_LEVEL=low` to `~/.config/last30days/.env`. Confirm: "Fun level set to low. Next run will focus on the news."
- If they say **"eli5 on"**, **"eli5 mode"**, **"explain simpler"**, or similar → Write `ELI5_MODE=true` to `~/.config/last30days/.env`. Confirm: "ELI5 mode on. All future runs will explain things like you're 5."
- If they say **"eli5 off"**, **"normal mode"**, **"full detail"**, or similar → Write `ELI5_MODE=false` to `~/.config/last30days/.env`. Confirm: "ELI5 mode off. Back to full detail."

**Only write a prompt when the user wants one.** Don't force a prompt on someone who asked "what could happen next with Iran."

### Writing a Prompt

When the user wants a prompt, write a **single, highly-tailored prompt** using your research expertise.

### CRITICAL: Match the FORMAT the research recommends

**If research says to use a specific prompt FORMAT, YOU MUST USE THAT FORMAT.**

**ANTI-PATTERN**: Research says "use JSON prompts with device specs" but you write plain prose. This defeats the entire purpose of the research.

### Quality Checklist (run before delivering):
- [ ] **FORMAT MATCHES RESEARCH** - If research said JSON/structured/etc, prompt IS that format
- [ ] Directly addresses what the user said they want to create
- [ ] Uses specific patterns/keywords discovered in research
- [ ] Ready to paste with zero edits (or minimal [PLACEHOLDERS] clearly marked)
- [ ] Appropriate length and style for TARGET_TOOL

### Output Format:

```
Here's your prompt for {TARGET_TOOL}:

---

[The actual prompt IN THE FORMAT THE RESEARCH RECOMMENDS]

---

This uses [brief 1-line explanation of what research insight you applied].
```

---

## IF USER ASKS FOR MORE OPTIONS

Only if they ask for alternatives or more prompts, provide 2-3 variations. Don't dump a prompt pack unless requested.
