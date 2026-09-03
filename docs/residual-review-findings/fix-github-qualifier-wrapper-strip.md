# Residual Review Findings

Source: LFG pipeline review of the #952 fix (branch `fix/github-qualifier-wrapper-strip`, commits a2b7785, 59f8937, 58b8bd5). Review run `20260809-160411-d2ac968f` (ce-code-review, roster: correctness, project-standards, testing, adversarial).

## Filed

- **P3 — skills/last30days/scripts/lib/github.py:201 — Stray wrapper residue from unbalanced wrapper collapse.** Mixed/nested wrapper shapes that strip a qualifier but leave a lone opener/closer (e.g. `(created:>2025-03-20` → `(`, cross-type nesting `(("created:>2025-03-20"))` → residue) survive into the query. Cosmetic only — the qualifier itself never leaks, so the #949 collision class stays dead. Suggested fix (a final lone-wrapper-char pass) was not applied because blindly removing all `)`/`]` could mangle legitimate topic text with unbalanced parens. Filed as https://github.com/mvanhorn/last30days-skill/issues/969.

## Applied (not residual)

- P1 — missing-closer / space-in-quote wrapper shapes leaking the raw qualifier into the query (regression vs first commit) — fixed in 58b8bd5 (boundary accepts wrapper openers).
- P2 — wrapped qualifiers with comma/semicolon glue (e.g. `(created:>2025-03-20,robotics)`) surviving the strip — fixed in 58b8bd5; glued terms preserved, qualifier stripped.
- Adversarial residual risk notes (empty-pair removal on whole topic, lenient mismatched-pair handling, fixpoint termination) — reviewed, no action required.
- Testing gaps (deeper nesting, mixed wrapper content, empty-pair-stays branch) — covered by structural guarantees; P1/P2 shapes now have regression tests.
