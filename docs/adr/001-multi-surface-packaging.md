# ADR 001: Multi-surface packaging

Date: 2026-05-10  
Status: Accepted

## Decision

last30days supports multiple agent surfaces from one canonical skill tree. The canonical runtime lives under `skills/last30days/`, and `skills/last30days/scripts/sync.sh` deploys that skill to the local discovery paths used by Claude Code plugin cache, Agents/Codex-style skill directories, Hermes, and OpenClaw variants when present.

## Context

Each host discovers skills and plugins differently. There is no single shared standard for Claude Code plugins, claude.ai skills, Codex/OpenAI-style skills, Hermes skills, and OpenClaw skills. Keeping separate hand-maintained copies would cause drift in runtime scripts, skill instructions, fixtures, and host-specific variants.

The repo already encodes this strategy in `skills/last30days/scripts/sync.sh`. The same constraint also explains why `skills/last30days/scripts/lib/__init__.py` must remain a bare package marker: eager imports can break host environments that import the package for discovery before optional runtime dependencies are available.

## Consequences

- `skills/last30days/SKILL.md` and `skills/last30days/scripts/` remain the canonical source for the public skill runtime.
- Changes under `skills/last30days/scripts/lib/` should be followed by `bash skills/last30days/scripts/sync.sh` during local validation so installed host copies stay current.
- Host-specific variants may exist, but they should be treated as projections of the canonical runtime rather than independent implementations.
- `skills/last30days/scripts/lib/__init__.py` must stay a bare package marker. Do not add eager imports there.
- Contributors should update this ADR if the repo adopts a single packaging standard or removes a host surface.

## Links

- `skills/last30days/scripts/sync.sh`
- `skills/last30days/SKILL.md`
- `skills/last30days/scripts/lib/__init__.py`
- `CHANGELOG.md`
