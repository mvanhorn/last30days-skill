# Follow-ups and prompt writing

Read for a follow-up on existing research. Preserve the root's consent, source honesty, citation, and cached-data rules. Use the root-bound interpreter and engine path for direct-engine examples below.

## WAIT FOR USER'S RESPONSE

**STOP and wait** for the user to respond after completing the response and any citations required by higher-priority host/tool requirements or user instructions. Do not continue research after the invitation unless governing instructions require it. Omit an extra source list only when none is required. Claim that raw data was saved only when the engine emitted a saved path; an empty `LAST30DAYS_MEMORY_DIR` disables saving and its footer pointer.

---

## WHEN USER RESPONDS

**Read their response and match the intent:**

- If they ask a **QUESTION** about the topic → Answer from your research (no new searches, no prompt)
- If they ask to **GO DEEPER** on a subtopic → Elaborate using your research findings
- If they describe something they want to **CREATE** → Write ONE perfect prompt (see below)
- If they ask for a **PROMPT** explicitly → Write ONE perfect prompt (see below)
- If they say **"more fun"**, **"too serious"**, or similar → Write `FUN_LEVEL=high` to `~/.config/last30days/.env` (append, don't overwrite). Confirm: "Fun level set to high. Next run will surface more witty and viral content."
- If they say **"less fun"**, **"too many jokes"**, or similar → Write `FUN_LEVEL=low` to `~/.config/last30days/.env`. Confirm: "Fun level set to low. Next run will focus on the news."
- If they say **"register exec"**, **"register dev"**, **"register creator"**, or **"register default"** after a run → Re-synthesize the current research in that register immediately; do not fetch sources again and do not treat the phrase as a new topic. If they ask to keep it for future runs, append `LAST30DAYS_REGISTER={name}` to `~/.config/last30days/.env` (never overwrite the file).
- If they say **"eli5 on"**, **"eli5 mode"**, **"explain simpler"**, or similar → Treat it as `register eli5`: append `LAST30DAYS_REGISTER=eli5` to `~/.config/last30days/.env`, then re-synthesize the current research immediately using the ELI5 guidance without fetching again. Confirm: "ELI5 mode on. All future runs will explain things like you're 5."
- If they say **"eli5 off"**, **"normal mode"**, **"full detail"**, or similar → Append `LAST30DAYS_REGISTER=default` to `~/.config/last30days/.env`. Confirm: "ELI5 mode off. Back to full detail."
- If they say **"drill into 3"**, **"go deeper on cluster 3"**, **"drill into the OpenClaw API ban discussion"**, or similar after a run → invoke the engine with `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" --drill "<their target>"`. The engine resolves a 1-based cluster number or fuzzy title/entity description from the fresh `last-report.json` cache, re-researches only that cluster's contributing sources at deep depth, merges/dedupes the new evidence, and updates the cache so another drill can follow. Relay the rendered **Original / Deeper** brief. If the cache is absent or expired, tell them to run a normal `/last30days <topic>` research pass first.
- If they say **"verify freshness"**, **"check whether those facts are still current"**, or ask to gate action on current claims after a run → invoke `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" --verify-freshness` with no topic. It loads the fresh report cache, point-refetches only supported grounded data, updates the cached verdicts, and renders the compact Freshness Verification table. For a first-pass request, translate the intent into the normal engine invocation plus `--verify-freshness`. `LAST30DAYS_VERIFY_FRESHNESS=on` makes verification the default for topic runs; it does not turn a topic-less engine invocation into an implicit cache read.
- If they say **"mark <topic> as covered"**, **"I covered X on the podcast"**, **"we published that article"**, or similar → invoke the engine with `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" queue cover "<topic name>" --save-dir="${LAST30DAYS_MEMORY_DIR}"` (same `--save-dir` scoping as discovery runs - queue rows live in that directory's research.db). Covering requires the exact queued topic name; on an unknown name the engine exits 2 and points at `queue list` - relay that, run `queue list`, and offer the queued names instead of retrying with guesses.
- If they say **"what's in my topic queue"**, **"what should I talk about next"**, **"show my content pipeline"**, or similar → invoke `"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" queue list --save-dir="${LAST30DAYS_MEMORY_DIR}"` and relay the rendered list (uncovered surfaced topics with domain, surface count, and last-surfaced date). An empty queue is a valid answer - suggest a `/last30days trending` or domain discovery run to populate it. (These two bullets cover the in-session case, after a run is already in context. The same asks arriving cold - with no research run yet this session - are handled by the root-gated library/queue fast path, which runs the identical commands directly instead of falling into topic research.)

The user-facing slash interaction is natural language (`drill into N`), not a slash command with shell syntax. `--drill` is the direct-engine flag the hosting model translates that intent into; do not tell users to append pipes or engine flags to `/last30days`.

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

---

## AFTER EACH PROMPT: Stay in Expert Mode

After delivering a prompt, offer to write more:

> Want another prompt? Just tell me what you're creating next.

---

## CONTEXT MEMORY

For the rest of this conversation, remember:
- **TOPIC**: {topic}
- **TARGET_TOOL**: {tool}
- **KEY PATTERNS**: {list the top 3-5 patterns you learned}
- **RESEARCH FINDINGS**: The key facts and insights from the research

**CRITICAL: After research is complete, treat yourself as an EXPERT on this topic.**

When the user asks follow-up questions:
- **DO NOT run new WebSearches** - you already have the research
- **Answer from what you learned** - cite the Reddit threads, X posts, and web sources
- **If they ask a question** - answer it from your research findings
- **If they ask for a prompt** - write one using your expertise

Only do new research if the user explicitly asks about a DIFFERENT topic.

---

## Output Summary Footer (After Each Prompt)

After delivering a prompt, end with:

```
---
📚 Expert in: {TOPIC} for {TARGET_TOOL}
📊 Based on: {n} Reddit threads ({sum} upvotes) + {n} X posts ({sum} likes) + {n} YouTube videos ({sum} views) + {n} TikTok videos ({sum} views) + {n} Instagram reels ({sum} views) + {n} HN stories ({sum} points) + {n} web pages

Want another prompt? Just tell me what you're creating next.
```

---
