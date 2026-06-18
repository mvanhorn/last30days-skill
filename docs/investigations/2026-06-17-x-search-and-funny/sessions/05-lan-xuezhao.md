# Session 05 — `/last30days Lan Xuezhao`

**What this session proved (cleanest):** the from:-AND-bug AND the total absence of a mention lane — directly motivating the "FROM + TO/ABOUT, with weight" requirement.

## Key evidence
- Engine X column = finance/VC keyword collisions (`@TheValueist`, `@arnaudmercier`, `@pierskicks`) + one `@hardmaru` "Thanks, Lan!" reply. None authored by @xuezhao.
- Verified mechanism: `--x-handle=xuezhao` → `from:xuezhao lan xuezhao since:...` (topic AND) → ~0. The unfiltered `from:xuezhao since:` branch is unreachable because topic is always truthy (→ A1).
- Structural gap: *"the engine only runs from: (authored-by) and keyword searches. There is no @xuezhao / to:xuezhao mention query anywhere in the pipeline"* (→ A2).
- Manual unfiltered `from:xuezhao` pulled 12 real posts (DeepSeek take, "new AI stack is global, sovereign and embedded", SpaceX grind) — genuinely interesting, all missed by the engine.
- Mentions were rich too (practitioners asking about her transcription/Hermes-dashboard rig) — exactly the "TO/ABOUT the person, with weight" lane the user now wants.
- Engagement counts came back 0 on the manual cookie-search field — note for weighting the mention/from lanes (resolve metrics per-tweet).

## Bugs surfaced → inventory IDs
A1 (from-AND), A2 (no mention lane), A3 (silent log), plus the FROM+ABOUT-with-weight requirement.
