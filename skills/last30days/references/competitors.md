# Competitor research

Read when competitor mode is selected. The root also requires `comparison.md`; retain each entity's own targeting and plan.

### Competitor mode (`--competitors`)

`--competitors` is a SKILL.md-level shortcut for vs-mode with auto-discovery. When host web search is available, YOU (the hosting reasoning model) do the discovery and Step 0.55, then invoke the web-search vs-topic path in `comparison.md`.

**With host web search, use the four-step protocol:**
1. **Discover peers** via WebSearch: `"{topic} competitors"` / `"{topic} alternatives"`. Pick N=2 by default (match the flag's default), N=argument value if the user passed `--competitors=N`.
2. **Run Step 0.55 for the main topic AND each peer** — same protocol you use for a single-entity topic, just N times. X handle, subreddits, GitHub, news context, per entity.
3. **Build the vs-topic string**: `"{main} vs {peer1} vs {peer2}"`.
4. **Invoke the engine** with the vs-topic and a JSON-encoded `--competitors-plan` file covering both peers, plus outer `--x-handle`/`--subreddits`/`--github-*` values captured and passed as quoted shell data for the main topic. Main-topic plan entries do not override those outer targeting flags. A per-entity `x_posts` envelope entry is the explicit exception, including for the main entity.

**Without host web search:** if the user named peers, use the no-web-search comparison command in `comparison.md`. For a bare `--competitors` request, use the research runbook's quoted `RESEARCH_TOPIC` heredoc and append `--competitors=N --auto-resolve` to its engine command, so the engine discovers peers through a configured search backend. Use `N=2` when the user did not specify a count. If the engine reports that peer discovery is unavailable, ask the user to name peers; do not invent them or claim that the keyless general-web floor discovered competitors. Apply the root's no-web-search rule: skip host Steps 0.55 and 0.75 and do not pass a host `--plan`.

**Flag surface (engine):**
- `--competitors` (bare) - signals the hosting model to discover 2 peers (3-way total).
- `--competitors=N` - N peers (1..6; out-of-range clamps with stderr warning).
- `--competitors-list="A,B,C"` - minimum escape hatch; names only, no per-entity targeting. Peer sub-runs fall back to planner defaults (visibly thinner data).
- `--competitors-plan <JSON file path>` - full per-entity targeting; implies vs-mode; preferred when host web search is available. Use the quoted-heredoc file recipe in `comparison.md`.
- `--polymarket-keywords "kw1,kw2"` - disambiguate Polymarket for ambiguous single-token topics ("Warriors" → `nba,gsw,golden-state`).
- `--hiring-signals` - deep-dive into public jobs/careers evidence for company focus signals. Use signal language only: leaning into, investing in, increasing focus, priority shift. Do NOT claim exact roadmap predictions from job postings.

**Why --competitors-plan over --competitors-list on web-search hosts:** without per-entity handles/subs, peer sub-runs can produce visibly thinner evidence than the main topic. The Resolved Entities block in stdout makes the gap visible. Dashes on a host with web search mean Step 0.55 needs another pass; dashes on a host without it may mean no resolver was configured.

**Engine-internal auto-resolve:** if the engine detects BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / PARALLEL_API_KEY / PERPLEXITY_API_KEY / OPENROUTER_API_KEY, it runs its own per-entity `resolve.auto_resolve()` before each sub-run. A hosting model with web search does not need those keys for its own Step 0.55; hosts without web search and headless cron/CI runs use this engine fallback.

**Output:** for Markdown/compact runs, one `{slug}-raw.md` per entity in `--save-dir` plus the merged comparison on stdout. For HTML runs, the main saved artifact is merged comparison HTML and peer artifacts remain per-entity. Always use the `[last30days] Comparison artifact set: main=...; peers=...` log line as the source of truth. Synthesis contract identical to the vs-mode protocol in `comparison.md`.
