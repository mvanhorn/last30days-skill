# Changelog fragments

Feature and fix PRs add a fragment here. **Do not edit `CHANGELOG.md` or bump version manifests** — the release workflow does that.

You do **not** need the towncrier CLI to contribute. Fragments are ordinary Markdown files; towncrier runs only when a release is prepared. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## Create a fragment

```bash
# Prefer the PR or issue number when you know it:
#   changelog.d/<number>.<type>.md
# Orphan (no linked issue/PR yet):
#   changelog.d/+.<type>.md   or   changelog.d/+short-slug.<type>.md
```

### Types (Keep a Changelog)

| Suffix | Section |
|--------|---------|
| `security` | Security |
| `removed` | Removed |
| `deprecated` | Deprecated |
| `added` | Added |
| `changed` | Changed |
| `fixed` | Fixed |

### Content

One or a few sentences of what shipped — behavior, docs, or install impact someone would care about in release notes. Link issues in the fragment body if useful; towncrier also links the number from the filename.

```markdown
General reports no longer promote unanchored fallback entity misses into synthesis.
```

### Skip

Pure chores (typos in comments, CI pin bumps with nothing for release notes) can omit a fragment and check **Skip changelog** in the PR template, or add the `skip-changelog` label.
