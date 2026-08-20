# Contributing

Thanks for helping with last30days. Most PRs here are opened by coding agents following [`AGENTS.md`](AGENTS.md); this file is the short path for humans and agents alike.

## Setup

Python **3.12+**. From the repo root:

```bash
uv sync --group dev
uv run pytest
```

That installs pytest/coverage and **towncrier** into the project env. You do **not** need a global towncrier install for normal contributions.

## Day-to-day PRs (no towncrier CLI)

1. Make your change and add/update tests.
2. If it should show up in the next release notes, add a fragment:
   ```bash
   # Prefer the PR or issue number when you know it:
   #   changelog.d/<number>.<type>.md
   # Orphan (no number yet):
   #   changelog.d/+short-slug.<type>.md
   ```
   Types: `added`, `changed`, `fixed`, `removed`, `deprecated`, `security`.  
   Details: [`changelog.d/README.md`](changelog.d/README.md).
3. Fill out [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) — Summary (“what does this PR do”), Testing, Changelog, Agent disclosure, Relationship.
4. Do **not** edit `CHANGELOG.md` and do **not** bump version strings in `pyproject.toml`, `SKILL.md`, plugin/marketplace JSON, or `uv.lock`. CI enforces that.

Chores with nothing for the release notes: check Skip changelog in the template and add the `skip-changelog` label.

Fragments are plain Markdown files. **towncrier is only used when cutting a release** (locally via `uv run` or in GitHub Actions) — contributors never run it for a feature PR.

## Releases (maintainers)

Prefer **Actions → Prepare release** (patch / minor / major). That opens a lockstep version PR (towncrier builds `CHANGELOG.md`, bumps every plugin/marketplace surface). Merging to `main` tags `vX.Y.Z` and the existing Release workflow publishes artifacts.

Local equivalent (after `uv sync --group dev`):

```bash
uv run python .github/scripts/prepare_release.py --bump patch   # or --version X.Y.Z
```

More detail: `AGENTS.md` § Changelog and releases, and `docs/solutions/workflow-issues/towncrier-lockstep-release.md`.

## Tests

```bash
uv run pytest
uv run pytest tests/test_dedupe_v3.py -k some_case
uv run pytest --cov
```

## Security

Never commit real API keys, cookies, tokens, or `.env` contents. Use dummy values in tests and fixtures.
