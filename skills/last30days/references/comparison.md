# Comparison research and synthesis

Read for explicit comparison intent before engine execution and synthesis. The root output laws define the comparison exceptions.

## If QUERY_TYPE = COMPARISON

When the user asks "X vs Y" (or "X vs Y vs Z"), the engine fans out N full `pipeline.run()` calls in parallel — one per entity. This restored the old N-pass architecture (reverted the one-pass latency optimization that removed per-entity depth); parallel execution keeps wall clock ≈ a single pass.

**When host web search is available, per-entity resolution is mandatory.** For each entity, resolve the full Step 0.55 stack (X handle, subreddits, GitHub user/repos, news context). Then assemble a `--competitors-plan` JSON mapping each peer to its targeting, and invoke the engine once with the vs-topic string. When host web search is unavailable, skip Steps 0.55 and 0.75 as the root requires. Invoke the no-web-search command below without a host query plan; the engine resolves the main entity and each peer independently when a configured resolver exists, then plans each sub-run internally. Without a configured resolver, keyword search still runs, but entity targeting can be thinner. A Grok Bot X connector can still supply per-entity post envelopes through `--competitors-plan` without supplying a query plan.

Peer targeting entries accept `x_handle`, `x_related`, `subreddits`, `github_user`, `github_repos`, `trustpilot_domain`, `context`, and `x_posts`. Use arrays for related handles, subreddits, and repositories. Other peer targeting keys are ignored; do not invent fields for dedicated subreddits, TikTok, or Instagram.

**Output shape per run:**
- For `--emit=compact` / `--emit=md`, there is no separate merged Markdown raw file. The main topic saves to `{main-slug}-raw.md`; each peer saves to `{peer-slug}-raw.md`.
- For `--emit=html`, the main saved artifact is the merged comparison HTML at `{main-slug}-vs-{peer-slug}-raw-html[...].html`; each peer may also save its own per-entity HTML artifact.
- The engine logs every written file as `[last30days] Saved output to {path}` and, for comparison runs, follows with `[last30days] Comparison artifact set: main={path}; peers={path, ...}`. Treat that log line as authoritative instead of recomputing paths from slugs.
- Stdout shows a merged comparison with the `## Head-to-Head` scaffold + per-entity Resolved Entities block.

On hosts with web search, generate `QUERY_PLAN_JSON` through Step 0.75 for **TOPIC_A only**, not the whole vs-string. The main entity receives that `--plan`; peer sub-runs plan independently. The competitors plan supplies peer targeting, not peer query plans.

Build `COMPETITORS_PLAN_JSON` as valid JSON keyed by the exact peer names, with JSON-encoded string values. For example, a peer entry can contain `{"Peer Widget":{"x_handle":"peer_widget","subreddits":["peer_widget"],"context":"The product says \"zero setup\""}}`. On a Grok Bot with the X connector, include every entity's `x_posts` envelope path, including the main entity, and matching `x_handle` and `x_related` fields for its handle lanes. Pass the main entity's handle and related handles through the outer flags as well. Encode quotes, backslashes, and newlines in names and fetched context; never paste raw values between JSON quotes. Use a quoted heredoc delimiter that is absent from the data. The same delimiter rule applies to the topic and main-targeting heredocs.

For a two-entity comparison, omit ` vs {TOPIC_C}` from the topic heredoc in either command. For more entities, include each name up to the engine's comparison limit.

### With host web search

```bash
(
# SKILL_DIR is already bound to the directory containing the loaded root SKILL.md.
# Never rebind it to this reference directory or search other installations.

if [ ! -f "$SKILL_DIR/scripts/last30days.py" ]; then
  echo "ERROR: scripts/last30days.py not found under SKILL_DIR=$SKILL_DIR" >&2
  echo "Re-check SKILL_DIR against the root SKILL.md loaded by the host; never use a reference directory." >&2
  exit 1
fi

COMPARISON_TOPIC=$(cat <<'TOPIC_EOF'
{TOPIC_A} vs {TOPIC_B} vs {TOPIC_C}
TOPIC_EOF
)
TOPIC_A_HANDLE=$(cat <<'HANDLE_EOF'
{TOPIC_A_HANDLE}
HANDLE_EOF
)
TOPIC_A_RELATED=$(cat <<'RELATED_EOF'
{TOPIC_A_RELATED}
RELATED_EOF
)
TOPIC_A_SUBS=$(cat <<'SUBS_EOF'
{TOPIC_A_SUBS}
SUBS_EOF
)
MAIN_TARGETING=()
[ -n "$TOPIC_A_HANDLE" ] && MAIN_TARGETING+=(--x-handle="$TOPIC_A_HANDLE")
[ -n "$TOPIC_A_RELATED" ] && MAIN_TARGETING+=(--x-related="$TOPIC_A_RELATED")
[ -n "$TOPIC_A_SUBS" ] && MAIN_TARGETING+=(--subreddits="$TOPIC_A_SUBS")

# BSD/macOS mktemp requires XXXXXX at the end of the template.
QUERY_PLAN_FILE=$(mktemp "${TMPDIR:-/tmp}/last30days-plan.XXXXXX")
COMPETITORS_PLAN_FILE=$(mktemp "${TMPDIR:-/tmp}/last30days-competitors.XXXXXX")
trap 'rm -f "$QUERY_PLAN_FILE" "$COMPETITORS_PLAN_FILE"' EXIT
cat >| "$QUERY_PLAN_FILE" <<'QUERY_PLAN_EOF'
{QUERY_PLAN_JSON}
QUERY_PLAN_EOF
# >| not >: mktemp already created the file, so a plain > is refused under
# `set -o noclobber` (leaving the plan empty -> deterministic fallback).
cat >| "$COMPETITORS_PLAN_FILE" <<'PLAN_EOF'
{COMPETITORS_PLAN_JSON}
PLAN_EOF
"${LAST30DAYS_PYTHON}" -m json.tool "$COMPETITORS_PLAN_FILE" >/dev/null || exit 2

"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" "$COMPARISON_TOPIC" \
  --emit=compact \
  --plan "$QUERY_PLAN_FILE" \
  --save-dir="${LAST30DAYS_MEMORY_DIR}" \
  --save-suffix=v3 \
  "${MAIN_TARGETING[@]}" \
  --competitors-plan "$COMPETITORS_PLAN_FILE"
)
```

For any additional outer targeting flag, capture its value through a quoted heredoc and add it to `MAIN_TARGETING` with a quoted variable expansion. Omit unresolved flags. Never paste a topic, handle, subreddit, or fetched context directly into shell command words. The quoted heredocs keep shell-active text literal; `json.tool` rejects malformed peer JSON before the research run.

### Without host web search

On a Grok Bot with the official X connector, follow `grok-bot-x.md` to fetch and write one envelope per entity before this command. Replace `COMPETITORS_PLAN_JSON` with JSON keyed by every entity name, including the main one, containing each envelope's absolute path as `x_posts`. When an envelope has `from` or `mention` calls, its plan entry must also contain the matching `x_handle`; when it has `related` calls, include their handles as an `x_related` array. For example, `{"Main Brand":{"x_posts":"/tmp/main.json","x_handle":"mainbrand"},"Peer Brand":{"x_posts":"/tmp/peer.json","x_handle":"peerbrand"}}`. Set `TOPIC_A_HANDLE` below to the main entity's matching handle and `TOPIC_A_RELATED` to its comma-separated related handles, or leave them empty when no handles are known. The main plan entry validates its envelope, while the outer flags target its pipeline run. With no known handle, fetch only a topic call and omit handle fields. Run the command in the same shell as the envelope setup so its parent cleanup trap remains active. Other hosts use this command without a competitors plan.

```bash
(
COMPARISON_TOPIC=$(cat <<'TOPIC_EOF'
{TOPIC_A} vs {TOPIC_B} vs {TOPIC_C}
TOPIC_EOF
)
MAIN_TARGETING=()
COMPETITORS_PLAN_ARGS=()
if [ "${LAST30DAYS_HOST:-}" = "grok-bot" ] && [ "${LAST30DAYS_X_HOST_LANE:-}" = "1" ]; then
  TOPIC_A_HANDLE=$(cat <<'HANDLE_EOF'
{TOPIC_A_HANDLE}
HANDLE_EOF
)
  TOPIC_A_RELATED=$(cat <<'RELATED_EOF'
{TOPIC_A_RELATED}
RELATED_EOF
)
  [ -n "$TOPIC_A_HANDLE" ] && MAIN_TARGETING+=(--x-handle="$TOPIC_A_HANDLE")
  [ -n "$TOPIC_A_RELATED" ] && MAIN_TARGETING+=(--x-related="$TOPIC_A_RELATED")
  COMPETITORS_PLAN_FILE=$(mktemp "${TMPDIR:-/tmp}/last30days-competitors.XXXXXX")
  trap 'rm -f "$COMPETITORS_PLAN_FILE"' EXIT
  cat >| "$COMPETITORS_PLAN_FILE" <<'PLAN_EOF'
{COMPETITORS_PLAN_JSON}
PLAN_EOF
  "${LAST30DAYS_PYTHON}" -m json.tool "$COMPETITORS_PLAN_FILE" >/dev/null || exit 2
  COMPETITORS_PLAN_ARGS=(--competitors-plan "$COMPETITORS_PLAN_FILE")
fi
"${LAST30DAYS_PYTHON}" "${SKILL_DIR}/scripts/last30days.py" "$COMPARISON_TOPIC" \
  --emit=compact --auto-resolve \
  --save-dir="${LAST30DAYS_MEMORY_DIR}" --save-suffix=v3 \
  "${MAIN_TARGETING[@]}" \
  "${COMPETITORS_PLAN_ARGS[@]}"
)
```

This branch has no host-authored `--plan`. The only no-web-search `--competitors-plan` is the Grok Bot connector handoff above. If the user supplied explicit targeting flags, pass those values through quoted variables as above. Do not fabricate a host query plan or pretend that empty `## Resolved Entities` fields prove Step 0.55 was skipped on a host that cannot run it.

Topic A (the main topic, first in the vs-string) uses outer `--x-handle`, `--x-related`, `--subreddits`, `--github-user`, `--github-repo`, `--trustpilot-domain`, `--tiktok-*`, `--ig-creators` as usual. Topics B and C get their targeting from `--competitors-plan` entries (keyed by entity name, case-insensitive) — a main-topic entry does not override those outer targeting flags, so the main topic's Trustpilot domain must ride the outer flag. Per-entity `x_posts` entries are the explicit exception: they can supply the main entity's connector envelope as well as peer envelopes.

**Step 0.55 for N entities when host web search is available.** The same pre-research protocol that applies to a single-entity topic applies to EACH entity in a vs-run. For N=3, that means 3 WebSearches for X handles, 3 for subreddits, 3 for GitHub, 3 for news context — or equivalent batched queries. On that host path, a `## Resolved Entities` block with dashes for any entity means you skipped Step 0.55 for that one. Re-run with a corrected plan.

**When host web search is available, do WebSearch supplements** for: `{TOPIC_A} vs {TOPIC_B} comparison {YEAR}` and `{TOPIC_A} vs {TOPIC_B} which is better` — these catch rivalry articles that per-entity passes might not surface.

**Use `RESOLVED_POSITIONING` per entity (Step 0.55 item 6) in two ways.** First, ground each entity's `What it is` cell in its CURRENT fetched pitch - describe the entity as it pitches itself today, never from memory. Second, if an entity's month of evidence directly bears on its pitch - SUPPORTS a specific claim, CUTS AGAINST one, or the conversation is squarely ABOUT the pitched ground - say so in ONE prose sentence inside that entity's section of the comparison synthesis (right after the Community Sentiment line - the template marks the slot), anchored to the real item with its engagement. When the pulse is orthogonal to the pitch (on-entity but about something the pitch doesn't speak to), say NOTHING about the pitch: omission is the correct output, and a manufactured connection is worse than silence. Match altitude: test SPECIFIC claims ("zero-config", "fastest", an uptime number) against specific threads; never grade a broad tagline ("financial infrastructure") against an individual thread - it is too broad to hit or miss. Keep claims windowed - "this month's conversation" - never trend verbs like "losing the narrative" that one 30-day window cannot support. If positioning was not actually fetched this run for an entity, skip both uses for that entity - never supply a pitch from memory.

**Use the applicable comparison invocation above in place of the research runbook's Step 1 command.** Run the runbook's Step 2 supplements when host web search is available and Step 2.5 appendix (comparison runs append to every per-entity file), then write the body with the comparison template below instead of the general template in `synthesis.md`.

**COMPARISON TABLE SCAFFOLD (engine-emitted, pass through verbatim):** For comparison topics, the engine's compact output includes a `## Head-to-Head` block with an empty markdown table (columns = entities, rows = axes like "What it is", "Philosophy", "Best for"). Your synthesis MUST include this block verbatim with filled cells, positioned between the narrative and the emoji-tree footer. Keep each cell to 5-15 words. Use ' - ' (hyphen with spaces) not em-dashes inside cells.


### If QUERY_TYPE = COMPARISON

**Comparison queries have their OWN synthesis template. Do NOT use the general-query `What I learned:` + bold-lead-in + `KEY PATTERNS:` structure for comparisons.** The comparison template below is the canonical shape proven by the April 9 launch-video exemplar. Follow it section-for-section.

Voice contract LAWs 1, 3, 5 apply to comparisons unchanged (omit unnecessary trailing source lists, no em-dashes, engine footer pass-through), subject to higher-priority host/tool requirements and user instructions. LAWs 2 and 4 have comparison-specific exceptions (see the LAW block: the comparison title and the five section headers below are REQUIRED, not violations).

**Required comparison structure (match the April 9 exemplar):**

```
🌐 last30days v{VERSION} · synced {YYYY-MM-DD}

# {TOPIC_A} vs {TOPIC_B} [vs {TOPIC_C}]: What the Community Says (/Last30Days)

## Quick Verdict

[One paragraph. Frame the thesis (are these competitors or layers of a stack? who's dominant? who's challenging?). Include scale stats for each entity inline (GitHub stars, user counts, whatever metric is comparable). End with one quotable community framing — a tweet, a Reddit quote, a YouTube clip — that captures how the community sees the relationship.]

## {Entity 1}

**Community Sentiment:** [Positive / Mixed / Negative / Enthusiastic / Security-concerned / etc.] ({N}+ mentions across {source list})

[Optional pitch-vs-pulse sentence - ONLY if `RESOLVED_POSITIONING` was captured for this entity AND the month's evidence directly supports a specific claim, cuts against one, or is squarely about the pitched ground: one windowed prose sentence anchored to a real item with engagement. Otherwise omit entirely - silence, not a placeholder.]

**Strengths (what people love)**
- [Specific strength with `per <source>` attribution]
- [Specific strength with `per <source>` attribution]
- [Specific strength with `per <source>` attribution]

**Weaknesses (common complaints)**
- [Specific complaint with `per <source>` attribution]
- [Specific complaint with `per <source>` attribution]

## {Entity 2}

[Same structure: Community Sentiment, Strengths bullets, Weaknesses bullets]

## {Entity 3}

[Same structure]

## Head-to-Head

| Dimension | {Entity 1} | {Entity 2} | {Entity 3} |
|---|---|---|---|
| What it is | ... | ... | ... |
| GitHub stars | ... | ... | ... |
| Philosophy | ... | ... | ... |
| Skills | ... | ... | ... |
| Memory | ... | ... | ... |
| Models | ... | ... | ... |
| Security | ... | ... | ... |
| Best for | ... | ... | ... |
| Install | ... | ... | ... |

(Engine emits this scaffold; fill the cells with 5-15 words each. If an axis does not apply to the topic class, write "N/A" or a topic-appropriate substitute rather than inventing data. Ground the `What it is` row in `RESOLVED_POSITIONING` when captured - each entity described as it pitches itself today, fetched this run, never from memory.)

## The Bottom Line

**Choose {Entity 1} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

**Choose {Entity 2} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

**Choose {Entity 3} if** [specific use case, comfort profile, tradeoff]. [One supporting sentence with attribution.]

## The emerging stack

[One paragraph. Name the combination pattern the community is converging on. Cite specific sources (`per @handle`, `per r/sub`, `per {channel} on YouTube`). This is the synthesis moment of the piece. If the data does not support an emerging-stack observation, write "No emerging stack pattern has crystallized in the research window yet" rather than fabricating one.]

---
✅ All agents reported back!
├─ 🟠 Reddit: ...
├─ 🔵 X: ...
(engine footer passed through when emitted, LAW 5; include its saved-file pointer only if emitted)

I've compared {TOPIC_A} vs {TOPIC_B} [vs ...] using the latest community data. Some things you could ask:
- [follow-up referencing comparison specifics, e.g. "Deep dive into {Entity} alone with /last30days {Entity}"]
- [follow-up referencing a specific claim from the Strengths/Weaknesses block]
- [follow-up on a specific dimension from the Head-to-Head table]
- [follow-up on the emerging-stack combination pattern]
```

**Do NOT:**
- Use `What I learned:` prose label (that is general-query voice)
- Use bold-lead-in paragraphs with ` - ` separators for the body (that is general-query voice)
- Use a `KEY PATTERNS from the research:` numbered list (replaced by per-entity Strengths/Weaknesses bullets and the emerging-stack paragraph)
- Fabricate a `## Notable Stats` block (the engine footer IS the stats block, LAW 5)
- Produce section headers outside the six listed above (`## Quick Verdict`, `## {Entity}` per entity, `## Head-to-Head`, `## The Bottom Line`, `## The emerging stack` are the only allowed `##` headers per LAW 4 comparison exception)

**Reference exemplar:** `$LAST30DAYS_MEMORY_DIR/openclaw-vs-hermes-vs-paperclip-LAUNCH-VIDEO-april9-exemplar.md` preserves the April 9 canonical output with full structural analysis. Match this shape section-for-section.
