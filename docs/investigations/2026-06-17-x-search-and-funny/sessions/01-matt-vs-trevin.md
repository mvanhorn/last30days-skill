# Session 01 — `/last30days Matt Van Horn vs Trevin Chow`

**What this session proved:** the X handle search runs but is poisoned by the topic-AND, fails silently, and the X column is actually Digg-side-channel pollution.

## Key evidence (verbatim engine/log lines)
- Engine fired keyword X searches only in the visible log: `[Bird] Searching: matt van horn printing press since:2026-05-19`. No `from:mvanhorn` line — because `search_handles` logs nothing on success (→ A3).
- GitHub person-mode worked (`[GitHub] Person-mode search for @mvanhorn`), so per-entity targeting was wired; X was the broken lane.
- The 13 "X posts" were `@imAbhishek9596`, `@lebojoycechauke`, etc. — none authored by @mvanhorn.
- Final verified mechanism: handle search built `from:mvanhorn matt van horn since:...` (topic AND'd onto the timeline → ~0). The clean `from:mvanhorn since:...` returned 40 real tweets.
- The off-topic "compound interest / compound nevus" X items came in through Digg's X-enrichment side channel (`[Digg] post-dedupe enriched ... clusters with X posts`), NOT bird — bird was failing the whole run under the 2-entity parallel fanout (`Bird search failed`).
- Strongest-token fallback collapsed `trevin chow ai agents compound` → bare token `compound`.

## Red herrings burned (NOT bugs)
- "sweet-cookie missing = broken" — optional browser-cookie helper, never required.
- "auth dead" — standalone test failed only because `get_config()` + `set_credentials()` weren't called to inject cookies.
- "metrics = None parser bug" — print read wrong keys; parser nests under `engagement`/`date`.

## Bugs surfaced → inventory IDs
A1 (from-AND), A3 (silent log), A4 (diagnose false-green), A5 (Digg pollution), A6 (fallback drift), A2 (no mention lane).
