## Residual Review Findings

Run context: ce-code-review `mode:agent` on branch `fix/github-qualifier-strip` (head `42c5ab5bebcb3d4bd4d8bfc11f89b4df4bc1da9b`), plan `docs/plans/2026-08-07-001-fix-github-qualifier-collision-plan.md`, run id `20260807-231856-17902`. Findings not applied in LFG step 5; filed for durability.

### Filed (tracker: GitHub Issues)

- **P1** — `skills/last30days/scripts/lib/github.py:237` — Qualifier-only topic classified as ERROR poisons retry eligibility — [mvanhorn/last30days-skill#951](https://github.com/mvanhorn/last30days-skill/issues/951) (settled-conflict: report-only per KTD-1)
- **P2** — `skills/last30days/scripts/lib/github.py:186` — Quote-wrapped or paren-wrapped qualifiers bypass the strip — [mvanhorn/last30days-skill#952](https://github.com/mvanhorn/last30days-skill/issues/952)
- **P2** — `skills/last30days/scripts/lib/github.py:231` — Empty or noise-plus-qualifier topics flip to hard ERROR — [mvanhorn/last30days-skill#953](https://github.com/mvanhorn/last30days-skill/issues/953) (settled-conflict: report-only per KTD-1)
- **P3** — `skills/last30days/scripts/lib/github.py:229` — Repeated qualifier-only subqueries spam logs and error detail — [mvanhorn/last30days-skill#954](https://github.com/mvanhorn/last30days-skill/issues/954)

### Settled-conflict findings (report-only, not filed as apply requests)

- **P1** — `skills/last30days/scripts/lib/github.py:237` — Qualifier-only topic classified as ERROR poisons retry eligibility — conflicts with KTD-1 (session-settled plan decision: qualifier-only topics return the error envelope). Downstream ERROR/attempted classification blocks `_retry_thin_sources`; filed as #951 for durability, not for application.
- **P2** — `skills/last30days/scripts/lib/github.py:231` — Empty or noise-plus-qualifier topics flip to hard ERROR — conflicts with KTD-1/R3 (error envelope for qualifier-only/empty topics). Filed as #953 for durability, not for application.

### No sink / failed

None.

### Proceeded-and-flagged settled-decision conflicts (from ce-work step 2)

None — ce-work returned no `settled_decision_conflicts`.

### Residual risks carried from the review

- Error-envelope `context["core"]` is unstripped in the qualifier-only path vs stripped in the success path; no current consumer is affected.
- GitHub 422 behavior for unbalanced quotes is external API behavior, not exercised in tests.
- Planner emission of comma-glued, quoted, or wrapped qualifier shapes is LLM behavior; exposure is unquantifiable from code.
